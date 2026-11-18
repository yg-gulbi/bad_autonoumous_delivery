"""ROS map geometry and trinary occupancy-grid processing, independent of Flask and ROS."""
from collections import Counter
from dataclasses import dataclass
from math import ceil, cos, floor, radians, sin
import re

import yaml

WALL, FREE, UNKNOWN = 0, 254, 205
BIN_M = 0.05
TOKEN = re.compile(rb"(?:\s+|\#[^\r\n]*)*([^\s#]+)")


def map_point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return a*x + c*y + e, b*x + d*y + f


def unmap_point(matrix, x, y):
    a, b, c, d, e, f = matrix
    det = a*d - b*c
    return (d*(x-e) - c*(y-f))/det, (a*(y-f) - b*(x-e))/det


def read_pgm(raw):
    offset = 0

    def token():
        nonlocal offset
        match = TOKEN.match(raw, offset)
        if not match:
            raise ValueError("PGM 헤더 또는 픽셀이 부족합니다.")
        offset = match.end()
        return match[1]

    mode = token()
    width, height, maximum = int(token()), int(token()), int(token())
    if mode not in (b"P2", b"P5") or min(width, height, maximum) < 1 or maximum > 65535:
        raise ValueError("P2 또는 P5 지도가 필요합니다.")
    count = width * height
    if mode == b"P2":
        values = [int(token()) for _ in range(count)]
    else:
        delimiter = re.match(rb"\r\n|\s", raw[offset:offset+2])
        if not delimiter:
            raise ValueError("PGM 헤더 구분자가 필요합니다.")
        offset += delimiter.end()
        step = 2 if maximum > 255 else 1
        payload = raw[offset:offset + step*count]
        if len(payload) != step*count:
            raise ValueError("PGM 픽셀이 부족합니다.")
        values = payload if step == 1 else [int.from_bytes(payload[i:i+2], "big") for i in range(0, len(payload), 2)]
    return width, height, maximum, values


@dataclass
class Grid:
    width: int
    height: int
    cells: bytes
    resolution: float
    origin: list

    @classmethod
    def read(cls, path):
        cfg = yaml.safe_load(path.read_text())
        width, height, maximum, values = read_pgm((path.parent / cfg["image"]).read_bytes())
        free = cfg.get("free_thresh", 0.196)
        occupied = cfg.get("occupied_thresh", 0.65)
        lookup = bytearray(maximum+1)
        for value in range(maximum+1):
            prob = value/maximum if cfg.get("negate", 0) else (maximum-value)/maximum
            lookup[value] = WALL if prob > occupied else FREE if prob < free else UNKNOWN
        return cls(width, height, bytes(lookup[v] for v in values),
                   float(cfg.get("resolution", 0.05)), cfg.get("origin", [0, 0, 0]))

    def pixel_to_world(self):
        x, y, yaw = self.origin
        c, s = cos(yaw)*self.resolution, sin(yaw)*self.resolution
        return c, s, s, -c, x-s*self.height, y+c*self.height

    def wall_centers(self):
        matrix = self.pixel_to_world()
        return [map_point(matrix, i % self.width + .5, i // self.width + .5)
                for i, v in enumerate(self.cells) if v == WALL]


def deskew_angle(points, origin):
    if not points:
        return 0.0
    coords = [((x-origin[0])/BIN_M, (y-origin[1])/BIN_M) for x, y in points]

    def score(tenths):
        angle = radians(tenths/10)
        c, s = cos(angle), sin(angle)
        cols, rows = Counter(), Counter()
        for x, y in coords:
            cols[floor(c*x - s*y + .5)] += 1
            rows[floor(s*x + c*y + .5)] += 1
        return sum(v*v for v in cols.values()) + sum(v*v for v in rows.values()), -abs(tenths)

    return max(range(-150, 151), key=score)/10


def world_matrix(angle, rotation, resolution):
    # Rotate in map coordinates (positive is counterclockwise), then invert screen Y.
    degrees = {"none": 0, "ccw90": 90, "cw90": -90, "180": 180}[str(rotation)] + angle
    c, s = cos(radians(degrees))/resolution, sin(radians(degrees))/resolution
    return [c, -s, -s, -c, 0, 0]


def neighbors(index, width, height, diagonal):
    x, y = index % width, index // width
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == dy == 0 or (not diagonal and dx and dy):
                continue
            if 0 <= x+dx < width and 0 <= y+dy < height:
                yield index + dx + dy*width


def replace_components(cells, width, height, value, replacement, *, below=None, diagonal=False):
    visited = bytearray(len(cells))
    for i, v in enumerate(cells):
        if v != value or visited[i]:
            continue
        group = [i]
        visited[i] = 1
        border = False
        for idx in group:
            border |= idx % width in (0, width-1) or idx // width in (0, height-1)
            for adj in neighbors(idx, width, height, diagonal):
                if not visited[adj] and cells[adj] == value:
                    visited[adj] = 1
                    group.append(adj)
        if (below is None and not border) or (below is not None and len(group) < below):
            for idx in group:
                cells[idx] = replacement


def clean_grid(cells, width, height):
    replace_components(cells, width, height, WALL, FREE, below=4, diagonal=True)
    walls = {i for i, v in enumerate(cells) if v == WALL}
    dilated = walls.copy()
    for i in walls:
        dilated.update(neighbors(i, width, height, True))
    for i in dilated - walls:
        if all(n in dilated for n in neighbors(i, width, height, True)):
            cells[i] = WALL
    replace_components(cells, width, height, FREE, UNKNOWN, below=20)
    replace_components(cells, width, height, UNKNOWN, FREE)


def render_grid(grid, matrix, padding_m):
    """Nearest-neighbor resample, clean, then crop. Matrix maps world to delivered PGM pixels."""
    src = grid.pixel_to_world()
    corners = [map_point(matrix, *map_point(src, x, y))
               for x in (0, grid.width) for y in (0, grid.height)]
    # Pad the working grid for morphology, even where raw wall pixels touch an edge.
    left = floor(min(p[0] for p in corners))-2
    top = floor(min(p[1] for p in corners))-2
    width = ceil(max(p[0] for p in corners))-left+2
    height = ceil(max(p[1] for p in corners))-top+2
    cells = bytearray([UNKNOWN]) * (width*height)
    for y in range(height):
        for x in range(width):
            point = unmap_point(matrix, left+x+.5, top+y+.5)
            col, row = map(floor, unmap_point(src, *point))
            if 0 <= col < grid.width and 0 <= row < grid.height:
                cells[y*width+x] = grid.cells[row*grid.width+col]
    clean_grid(cells, width, height)
    known = [i for i, v in enumerate(cells) if v != UNKNOWN]
    pad = ceil(padding_m/grid.resolution)
    if known:
        x0 = min(i % width for i in known)-pad
        x1 = max(i % width for i in known)+pad+1
        y0, y1 = known[0]//width-pad, known[-1]//width+pad+1
    else:
        x0, x1, y0, y1 = 0, width, 0, height
    cropped = bytearray([UNKNOWN]) * ((x1-x0)*(y1-y0))
    start, stop = max(0, x0), min(width, x1)
    for y in range(max(0, y0), min(height, y1)):
        dest = (y-y0)*(x1-x0) + start-x0
        cropped[dest:dest+stop-start] = cells[y*width+start:y*width+stop]
    matrix[4:6] = [-left-x0, -top-y0]
    header = f"P5\n{x1-x0} {y1-y0}\n255\n".encode()
    return x1-x0, y1-y0, header + cropped
