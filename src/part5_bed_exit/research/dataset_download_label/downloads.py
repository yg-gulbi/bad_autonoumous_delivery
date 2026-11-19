"""연구 데이터와 모델을 공식 출처에서 안전하게 내려받는 공통 보조 모듈.

용량 제한, 체크섬, 부분 파일, 재개 다운로드, 출처 기록과 안전한 ZIP 해제를 담당한다.
데이터셋 공급자별 목록 조회·선택 함수도 포함하며 운영 노드는 이 파일을 사용하지 않는다.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

import requests

SLP_ARCHIVE="https://coe.northeastern.edu/Research/AClab/SLP/SLP2022.zip"
SLP_MICCAI19_ARCHIVE="https://coe.northeastern.edu/Research/AClab/SLP/SLP_MICCAI19.zip"
BLANKETSET_PUBLIC_RESOURCES={
    "movement_sequences.png": (
        "https://rdm.inesctec.pt/dataset/36363e81-6d41-4195-b475-9181b53e3696/"
        "resource/392d37a6-b6b3-41a9-bef3-971a40708260/download/movementsequences.png"
    ),
    "README.md": (
        "https://rdm.inesctec.pt/dataset/36363e81-6d41-4195-b475-9181b53e3696/"
        "resource/ffc0f314-16b2-40d5-a34b-fa91a80fcb36/download/readme.md"
    ),
    "blanketset_table.csv": (
        "https://rdm.inesctec.pt/dataset/36363e81-6d41-4195-b475-9181b53e3696/"
        "resource/03939c8e-6372-4bcd-bf96-e83aeb2c2fd0/download/blanketsettable.csv"
    ),
}
MANNEQUIN_RGB_URL=(
    "https://coe.northeastern.edu/Research/AClab/pose/colorManneNumbered.zip"
)
BLANKETGEN2_API="https://zenodo.org/api/records/23260194"
FALLVISION_API=(
    "https://dataverse.harvard.edu/api/datasets/export"
    "?exporter=dataverse_json&persistentId=doi:10.7910/DVN/75QPKK"
)
HF_REPO="xyz1901901/patientcare"
HF_API="https://huggingface.co/api/datasets"
ROBOFLOW_PREVIEW_URLS=(
    "https://source.roboflow.com/8WwkR5B0rZUWRmkeXpaFrnEVKFg2/03Fbb7vK4CXsAkZqUllT/thumb.jpg",
    "https://source.roboflow.com/8WwkR5B0rZUWRmkeXpaFrnEVKFg2/0480OxCgDrVJj48lcdUR/thumb.jpg",
    "https://source.roboflow.com/8WwkR5B0rZUWRmkeXpaFrnEVKFg2/07YeQYxnul6POLYEorHI/thumb.jpg",
    "https://source.roboflow.com/8WwkR5B0rZUWRmkeXpaFrnEVKFg2/0AL78HkdOetnQH9uKrSE/thumb.jpg",
)
MODEL_URLS={
    "yolo11n.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
    "yolo11n-seg.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-seg.pt",
    "yolo11n-pose.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt",
    "pose_landmarker_full.task":"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
}


def sha256(path):
    """파일을 1 MiB씩 읽어 SHA-256 해시 문자열을 계산한다."""
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def save_json(path,data):
    """부모 디렉터리를 만들고 NaN을 허용하지 않는 UTF-8 JSON을 저장한다."""
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")


def download_file(url,destination,max_bytes=100*1024**2,checksum=None,checksum_type="sha256",headers=None):
    """Reuse only a receipt-verified file. Fail on HTML/auth/oversize responses.

    A local SHA256 records identity; upstream checksum, when supplied, verifies
    against the provider. Never prints tokens or signed download URLs.
    """
    if urlparse(url).scheme!="https":
        raise ValueError("HTTPS required")
    dest=Path(destination)
    dest.parent.mkdir(parents=True,exist_ok=True)
    receipt=dest.with_name(dest.name+".source.json")
    if dest.is_file() and receipt.is_file():
        old=json.loads(receipt.read_text())
        if sha256(dest)==old.get("sha256") and (not checksum or _digest(dest,checksum_type)==checksum.lower()):
            return dest
    part=dest.with_name(dest.name+".part")
    try:
        with requests.get(url,stream=True,timeout=(15,90),headers=headers) as r:
            r.raise_for_status()
            if "text/html" in r.headers.get("Content-Type",""):
                raise RuntimeError("Server returned HTML/login page, not data")
            length=int(r.headers.get("Content-Length",0))
            if length>max_bytes:
                raise ValueError(f"Download exceeds {max_bytes} byte limit")
            written=0
            with part.open("wb") as f:
                for block in r.iter_content(1024*1024):
                    written+=len(block)
                    if written>max_bytes:
                        raise ValueError("Download exceeded size limit")
                    f.write(block)
            if not written or (length and length!=written):
                raise IOError("Incomplete/empty download")
        if checksum and _digest(part,checksum_type)!=checksum.lower():
            raise IOError("Provider checksum mismatch")
        part.replace(dest)
        # Strip query strings, which may contain access tokens.
        public_url=url.split("?")[0]
        save_json(receipt,{"url":public_url,"downloaded_unix_s":time.time(),
                           "bytes":written,"sha256":sha256(dest),
                           "provider_checksum":checksum,"provider_checksum_type":checksum_type if checksum else None})
        return dest
    finally:
        part.unlink(missing_ok=True)


def download_file_resumable(url,destination,max_bytes,headers=None,checksum=None,checksum_type="sha256"):
    """Download a large public file with HTTP Range resume and a receipt."""
    if urlparse(url).scheme!="https":
        raise ValueError("HTTPS required")
    dest=Path(destination)
    dest.parent.mkdir(parents=True,exist_ok=True)
    receipt=dest.with_name(dest.name+".source.json")
    if dest.is_file() and receipt.is_file():
        old=json.loads(receipt.read_text(encoding="utf-8"))
        provider_ok=not checksum or _digest(dest,checksum_type)==checksum.lower()
        if dest.stat().st_size==old.get("bytes") and sha256(dest)==old.get("sha256") and provider_ok:
            return dest

    part=dest.with_name(dest.name+".part")
    offset=part.stat().st_size if part.is_file() else 0
    request_headers=dict(headers or {})
    if offset:
        request_headers["Range"]=f"bytes={offset}-"
    with requests.get(url,stream=True,timeout=(20,120),headers=request_headers) as r:
        r.raise_for_status()
        if "text/html" in r.headers.get("Content-Type",""):
            raise RuntimeError("Server returned HTML/login page, not data")
        if offset and r.status_code!=206:
            offset=0
        length=int(r.headers.get("Content-Length",0))
        expected=offset+length if length else None
        if expected and expected>max_bytes:
            raise ValueError(f"Download exceeds {max_bytes} byte limit")
        mode="ab" if offset else "wb"
        written=offset
        with part.open(mode) as f:
            for block in r.iter_content(1024*1024):
                if not block:
                    continue
                written+=len(block)
                if written>max_bytes:
                    raise ValueError("Download exceeded size limit")
                f.write(block)
    if not written or (expected and written!=expected):
        raise IOError("Incomplete/empty download")
    if checksum and _digest(part,checksum_type)!=checksum.lower():
        raise IOError("Provider checksum mismatch")
    part.replace(dest)
    save_json(receipt,{"url":url.split("?")[0],"downloaded_unix_s":time.time(),
                       "bytes":written,"sha256":sha256(dest),
                       "provider_checksum":checksum,
                       "provider_checksum_type":checksum_type if checksum else None})
    return dest


def _digest(path,algorithm):
    """지정한 해시 알고리즘으로 파일 다이제스트를 계산한다."""
    h=hashlib.new(algorithm.lower().replace("-",""))
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def download_models(model_dir):
    """운영 네 메서드의 공식 사전학습 가중치를 모델 디렉터리에 내려받는다."""
    return [download_file(url,Path(model_dir)/name,max_bytes=50*1024**2)
            for name,url in MODEL_URLS.items()]


class HTTPRangeReader(io.RawIOBase):
    """전체 ZIP을 받지 않고 필요한 바이트 범위만 읽는 원격 탐색 가능 스트림."""

    def __init__(self,url,block_size=256*1024,max_transfer=50*1024**2):
        """원격 서버의 Range 지원과 파일 크기를 확인하고 블록 캐시를 준비한다."""
        super().__init__()
        self.url,self.block_size,self.max_transfer=url,block_size,max_transfer
        self.session=requests.Session()
        self.cache={}
        self.position=0
        self.transferred=0
        with self.session.get(url,headers={"Range":"bytes=0-0","Accept-Encoding":"identity"},
                              stream=True,timeout=45) as r:
            if r.status_code!=206:
                self.session.close()
                raise RuntimeError("Server does not support HTTP Range; use the full-archive cell")
            match=re.fullmatch(r"bytes 0-0/(\d+)",r.headers.get("Content-Range",""))
            if not match:
                raise IOError("Invalid Content-Range")
            self.size=int(match.group(1))

    def readable(self):
        """읽기 가능한 스트림임을 표준 IO 계층에 알린다."""
        return True

    def seekable(self):
        """바이트 위치 이동이 가능한 스트림임을 표준 IO 계층에 알린다."""
        return True

    def tell(self):
        """현재 원격 파일의 읽기 위치를 반환한다."""
        return self.position

    def seek(self,offset,whence=0):
        """시작·현재·끝 기준 오프셋으로 읽기 위치를 이동한다."""
        p=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        if p<0:
            raise ValueError("negative seek")
        self.position=p
        return p

    def read(self,size=-1):
        """필요한 원격 블록만 Range 요청해 현재 위치부터 바이트를 읽는다."""
        end=self.size if size<0 else min(self.position+size,self.size)
        parts=[]
        while self.position<end:
            index=self.position//self.block_size
            start=index*self.block_size
            if index not in self.cache:
                stop=min(start+self.block_size,self.size)-1
                if self.transferred+stop-start+1>self.max_transfer:
                    raise ValueError("Remote ZIP sample transfer limit exceeded")
                with self.session.get(self.url,headers={"Range":f"bytes={start}-{stop}","Accept-Encoding":"identity"},
                                      stream=True,timeout=60) as r:
                    if r.status_code!=206 or r.headers.get("Content-Range")!=f"bytes {start}-{stop}/{self.size}":
                        raise IOError("Server ignored or changed Range response")
                    data=r.raw.read(stop-start+2)
                    if len(data)!=stop-start+1:
                        raise IOError("Incomplete range response")
                self.cache[index]=data
                self.transferred+=len(data)
            data=self.cache[index]
            count=min(end-self.position,len(data)-(self.position-start))
            if count<=0:
                raise IOError("Invalid remote ZIP position")
            parts.append(data[self.position-start:self.position-start+count])
            self.position+=count
        return b"".join(parts)

    def close(self):
        """HTTP 세션을 닫고 상위 스트림의 종료 처리를 수행한다."""
        if hasattr(self,"session"):
            self.session.close()
        super().close()


def download_slp_samples(output_dir,count_per_cover=2):
    """Select RGB images under each cover condition, preserving ZIP paths.

    Sample-only: no invented event labels, no artificial image-to-video time axis.
    """
    if count_per_cover<1:
        raise ValueError("count_per_cover must be positive")
    output_dir=Path(output_dir)
    previous=output_dir/"download_manifest.json"
    if previous.is_file():
        old=json.loads(previous.read_text())
        files=old.get("files",[])
        if (len(files)==3*count_per_cover
                and all(r.get("archive_url")==SLP_ARCHIVE for r in files)
                and all((output_dir/"raw"/r["member"]).is_file()
                        and sha256(output_dir/"raw"/r["member"])==r["sha256"] for r in files)):
            return files
    records=[]
    with HTTPRangeReader(SLP_ARCHIVE) as remote,zipfile.ZipFile(remote) as z:
        names=sorted(z.namelist())
        save_json(output_dir/"archive_file_list.json",names)
        groups={"uncover":[],"cover1":[],"cover2":[]}
        for name in names:
            p=PurePosixPath(name)
            parts=[s.lower() for s in p.parts]
            if "rgb" not in parts or p.suffix.lower() not in (".png",".jpg",".jpeg"):
                continue
            for cover in groups:
                if cover in parts:
                    groups[cover].append(name)
        if not all(groups.values()):
            raise RuntimeError("Archive RGB/cover paths changed; inspect archive_file_list.json")
        for cover,names_in_group in groups.items():
            for name in names_in_group[:count_per_cover]:
                p=PurePosixPath(name)
                if p.is_absolute() or ".." in p.parts:
                    raise ValueError("Unsafe ZIP member path")
                info=z.getinfo(name)
                if info.file_size>10*1024**2:
                    raise ValueError("Unexpectedly large image")
                password=os.environ.get("SLP_ZIP_PASSWORD")
                if info.flag_bits & 1 and not password:
                    raise RuntimeError("SLP2022 RGB members are encrypted. Obtain access from ACLab, "
                                       "set SLP_ZIP_PASSWORD outside the notebook, then rerun.")
                data=z.read(info,pwd=None if password is None else password.encode())
                dest=output_dir/"raw"/Path(*p.parts)
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(data)
                records.append({"dataset":"SLP2022","archive_url":SLP_ARCHIVE,
                                "member":name,"cover":cover,"bytes":len(data),
                                "sha256":sha256(dest),"label_type":"pose_static_no_exit_events"})
        transferred=remote.transferred
    save_json(output_dir/"download_manifest.json",{"files":records,"network_bytes":transferred,
              "license":"SLP non-commercial research/teaching; see official README",
              "source":"https://github.com/ostadabbas/SLP-Dataset-and-Code"})
    return records


def download_slp_miccai19_public_rgb(output_dir):
    """Extract the only password-free SLP RGB subset with HTTP Range.

    The MICCAI19 archive exposes 45 thick-blanket RGB frames for simLab
    subject 00004.  Its pose labels and every other useful member remain
    encrypted, so this subset is for unlabeled qualitative testing only.
    """
    output_dir=Path(output_dir)
    manifest_path=output_dir/"download_manifest.json"
    if manifest_path.is_file():
        old=json.loads(manifest_path.read_text(encoding="utf-8"))
        files=old.get("files",[])
        if (len(files)==45
                and all(r.get("archive_url")==SLP_MICCAI19_ARCHIVE for r in files)
                and all((output_dir/"raw"/r["member"]).is_file()
                        and sha256(output_dir/"raw"/r["member"])==r["sha256"] for r in files)):
            return files

    records=[]
    with HTTPRangeReader(SLP_MICCAI19_ARCHIVE,max_transfer=128*1024**2) as remote,zipfile.ZipFile(remote) as z:
        members=[]
        for info in z.infolist():
            p=PurePosixPath(info.filename)
            if (not info.is_dir() and not info.flag_bits&1 and p.suffix.lower()==".png"
                    and p.parts[:5]==("SLP","simLab","00004","RGB","cover2")):
                members.append(info)
        members=sorted(members,key=lambda info: info.filename)
        if len(members)!=45:
            raise RuntimeError(f"Expected 45 public MICCAI19 RGB frames, found {len(members)}")
        for info in members:
            p=PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts:
                raise ValueError("Unsafe ZIP member path")
            if info.file_size>10*1024**2:
                raise ValueError("Unexpectedly large image")
            data=z.read(info)
            dest=output_dir/"raw"/Path(*p.parts)
            dest.parent.mkdir(parents=True,exist_ok=True)
            part=dest.with_name(dest.name+".part")
            part.write_bytes(data)
            part.replace(dest)
            records.append({"dataset":"SLP_MICCAI19_public_unlabeled",
                            "archive_url":SLP_MICCAI19_ARCHIVE,
                            "member":info.filename,"cover":"cover2",
                            "bytes":len(data),"sha256":sha256(dest),
                            "label_type":"none_pose_labels_encrypted"})
        transferred=remote.transferred
    save_json(manifest_path,{"files":records,"network_bytes":transferred,
              "license":"SLP non-commercial research/teaching; see official README",
              "limitations":["single subject","cover2 only","pose labels encrypted",
                             "no bed-exit event labels"],
              "source":"https://coe.northeastern.edu/Research/AClab/SLP/"})
    return records


def download_blanketset_public_metadata(output_dir):
    """Download public BlanketSet documentation, never restricted videos."""
    output_dir=Path(output_dir)
    paths=[]
    for name,url in BLANKETSET_PUBLIC_RESOURCES.items():
        last_error=None
        for attempt in range(5):
            try:
                paths.append(download_file(url,output_dir/name,max_bytes=20*1024**2))
                break
            except requests.RequestException as exc:
                last_error=exc
                if attempt<4:
                    time.sleep(2*(attempt+1))
        else:
            raise last_error
    save_json(output_dir/"public_manifest.json",{
        "dataset":"BlanketSet",
        "source":"https://rdm.inesctec.pt/dataset/nis-2022-004",
        "files":[path.name for path in paths],
        "video_access":"restricted_due_to_legal_considerations",
        "pose_labels":"not_provided",
    })
    return paths


def download_mannequin_rgb_in_bed(output_dir):
    """Download the public RGB mannequin in-bed pose set and LSP14 labels."""
    output_dir=Path(output_dir)
    archive=download_file(
        MANNEQUIN_RGB_URL,output_dir/"raw"/"colorManneNumbered.zip",
        max_bytes=50*1024**2)
    extracted=output_dir/"extracted"
    extract_zip(archive,extracted,max_bytes=2*1024**3)
    images=sorted(extracted.rglob("*.jpg"))
    labels=sorted(extracted.rglob("joints_gt.mat"))
    if len(images)!=312 or len(labels)!=1:
        raise RuntimeError(
            f"Expected 312 RGB images and one joints_gt.mat, found {len(images)} and {len(labels)}")
    save_json(output_dir/"download_manifest.json",{
        "dataset":"Mannequin RGB In-Bed Pose",
        "source":MANNEQUIN_RGB_URL,
        "archive_sha256":sha256(archive),
        "rgb_images":len(images),
        "pose_labels":"LSP14 joints_gt.mat",
        "blanket":"none",
    })
    return images


def blanketgen2_inventory(output_dir):
    """Record the official Zenodo files without downloading large archives."""
    r=requests.get(BLANKETGEN2_API,timeout=(15,90),headers={"Accept":"application/json"})
    r.raise_for_status()
    payload=r.json()
    files=[]
    for item in payload.get("files",[]):
        files.append({"name":item["key"],"bytes":item["size"],
                      "checksum":item.get("checksum"),
                      "url":item["links"]["self"]})
    if not any(item["name"]=="videos.zip" for item in files):
        raise RuntimeError("BlanketGen2 Zenodo record does not contain videos.zip")
    save_json(Path(output_dir)/"inventory.json",{
        "record":23260194,"doi":"10.25747/44jy-3v61",
        "license":"CC BY 4.0","files":files,
        "note":"Original Fit3D RGB frames and pose labels are not included."})
    return files


def download_blanketgen2_fit3d(output_dir,include_videos=False):
    """Download public BlanketGen2 materials; full mode adds 29.8 GB overlays."""
    output_dir=Path(output_dir)
    files=blanketgen2_inventory(output_dir)
    wanted={"BlanketGen2-Fit3D_Supplementary_Materials.zip"}
    if include_videos:
        wanted.add("videos.zip")
    selected=[item for item in files if item["name"] in wanted]
    if {item["name"] for item in selected}!=wanted:
        raise RuntimeError("Required BlanketGen2 files are missing from the Zenodo record")
    required=sum(item["bytes"] for item in selected)
    ensure_free_space(output_dir,required)
    paths=[]
    for item in selected:
        algorithm,value=(item["checksum"] or "md5:").split(":",1)
        paths.append(download_file_resumable(
            item["url"],output_dir/"raw"/item["name"],
            max_bytes=item["bytes"],checksum=value or None,checksum_type=algorithm))
    save_json(output_dir/"download_manifest.json",{
        "dataset":"BlanketGen2-Fit3D","doi":"10.25747/44jy-3v61",
        "files":[path.name for path in paths],"bytes":required,
        "contains_original_rgb":False,"contains_pose_labels":False,
        "usage":"Composite transparent blanket layers with separately licensed Fit3D frames.",
    })
    return paths


def download_slp_archive(output_dir,max_bytes=18*1024**3):
    """Download the official SLP2022 archive without extracting encrypted data."""
    return download_file_resumable(
        SLP_ARCHIVE,Path(output_dir)/"SLP2022.zip",max_bytes=max_bytes)


def fallvision_inventory(output_dir):
    """FallVision Dataverse의 공개 파일 메타데이터를 조회·저장한다."""
    headers={}
    if os.getenv("DATAVERSE_TOKEN"):
        headers["X-Dataverse-key"]=os.environ["DATAVERSE_TOKEN"]
    r=requests.get(FALLVISION_API,headers=headers,timeout=45)
    r.raise_for_status()
    if "json" not in r.headers.get("Content-Type",""):
        raise RuntimeError("Dataverse returned an anti-bot/login page; use Files tab manually")
    payload=r.json()
    # The regular dataset endpoint is sometimes blocked by Harvard's anti-bot
    # layer.  The official Dataverse JSON export exposes the same released file
    # metadata without downloading the multi-gigabyte archives.
    version=payload.get("datasetVersion") or payload["data"]["latestVersion"]
    files=[]
    for f in version["files"]:
        d=f["dataFile"]
        files.append({"id":d["id"],"name":d["filename"],"directory":f.get("directoryLabel",""),
                      "bytes":d["filesize"],"restricted":f.get("restricted",False),
                      "checksum":d.get("checksum"),"description":d.get("description",''),
                      "categories":f.get("categories",[])})
    save_json(Path(output_dir)/"inventory.json",{"doi":"10.7910/DVN/75QPKK",
              "license":version.get("license"),"termsOfUse":version.get("termsOfUse"),"files":files})
    return files


def download_fallvision_files(inventory,file_ids,output_dir,max_bytes=300*1024**2):
    """Download exact unrestricted FallVision archive IDs after inventory review."""
    selected=[f for f in inventory if f["id"] in file_ids]
    if len(selected)!=len(set(file_ids)) or not selected:
        raise ValueError("Select existing file IDs from the inventory")
    if any(f["restricted"] for f in selected):
        raise ValueError("Restricted file: obtain provider access first")
    if sum(f["bytes"] for f in selected)>max_bytes:
        raise ValueError("Selected files exceed budget")
    headers={"X-Dataverse-key":os.environ["DATAVERSE_TOKEN"]} if os.getenv("DATAVERSE_TOKEN") else {}
    paths=[]
    for f in selected:
        # Do not trust filenames supplied by a remote catalog as local paths.
        name=Path(f["name"]).name
        checksum=f.get("checksum") or {}
        path=download_file_resumable(
            f"https://dataverse.harvard.edu/api/access/datafile/{f['id']}",
            Path(output_dir)/"raw"/f"{f['id']}_{name}",max_bytes,headers,
            checksum.get("value"),checksum.get("type","sha256"))
        paths.append(path)
    return paths


def download_roboflow(output_dir):
    """Optional static-image dataset. API key stays in the environment."""
    from roboflow import Roboflow
    key=os.getenv("ROBOFLOW_API_KEY")
    if not key:
        raise RuntimeError("Set ROBOFLOW_API_KEY outside the notebook, or use the official Download UI")
    return Roboflow(api_key=key).workspace("helppai").project("bed-exit").version(4).download(
        "yolov8",location=str(output_dir),overwrite=False)


def download_roboflow_previews(output_dir):
    """Download the four public thumbnails linked by the official project page."""
    output_dir=Path(output_dir)
    paths=[]
    for index,url in enumerate(ROBOFLOW_PREVIEW_URLS,1):
        paths.append(download_file(url,output_dir/f"official_preview_{index:02d}.jpg",
                                   max_bytes=10*1024**2))
    save_json(output_dir/"preview_manifest.json",{
        "project":"https://universe.roboflow.com/helppai/bed-exit",
        "files":[str(path.name) for path in paths],
        "note":"Official project-page thumbnails only; annotations and all 848 images require export access.",
    })
    return paths


def huggingface_inventory(output_dir,repo=HF_REPO,revision="main"):
    """List every file in a public Hugging Face dataset repository."""
    headers={"Accept":"application/json"}
    if os.getenv("HF_TOKEN"):
        headers["Authorization"]=f"Bearer {os.environ['HF_TOKEN']}"
    url=f"{HF_API}/{repo}/tree/{quote(revision,safe='')}"
    params={"recursive":"true","expand":"true","limit":1000}
    files=[]
    while url:
        r=requests.get(url,params=params,headers=headers,timeout=(15,90))
        r.raise_for_status()
        if "json" not in r.headers.get("Content-Type",""):
            raise RuntimeError("Hugging Face returned a login/HTML page")
        for item in r.json():
            if item.get("type")!="file":
                continue
            size=item.get("size")
            if not size and item.get("lfs"):
                size=item["lfs"].get("size")
            files.append({"path":item["path"],"bytes":size,"oid":item.get("oid")})
        link=r.headers.get("Link","")
        match=re.search(r'<([^>]+)>;\s*rel="next"',link)
        url=match.group(1) if match else None
        params=None
    save_json(Path(output_dir)/"inventory.json",{
        "repository":repo,"revision":revision,"files":files,
        "known_total_bytes":sum(f["bytes"] or 0 for f in files)})
    return files


def download_huggingface_dataset(output_dir,patterns=None,max_total_bytes=10*1024**3,
                                 max_files=None,repo=HF_REPO,revision="main"):
    """Download selected or all files from a Hugging Face dataset repository."""
    output_dir=Path(output_dir)
    files=huggingface_inventory(output_dir,repo,revision)
    if patterns:
        files=[f for f in files if any(fnmatch(f["path"],p) for p in patterns)]
    if max_files is not None:
        files=files[:max_files]
    unknown=[f["path"] for f in files if f["bytes"] is None]
    if unknown:
        raise RuntimeError(f"Cannot preflight {len(unknown)} files with unknown sizes")
    total=sum(f["bytes"] for f in files)
    if total>max_total_bytes:
        raise ValueError(f"Selected Hugging Face files require {total} bytes; limit is {max_total_bytes}")
    free=shutil.disk_usage(output_dir.parent if output_dir.parent.exists() else Path.cwd()).free
    if total>max(0,free-10*1024**3):
        raise OSError(f"Need {total} bytes while preserving a 10 GiB free-space reserve")
    headers={}
    if os.getenv("HF_TOKEN"):
        headers["Authorization"]=f"Bearer {os.environ['HF_TOKEN']}"
    downloaded=[]
    for item in files:
        rel=PurePosixPath(item["path"])
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe repository path")
        url=(f"https://huggingface.co/datasets/{repo}/resolve/"
             f"{quote(revision,safe='')}/{quote(item['path'],safe='/')}?download=true")
        destination=output_dir/"raw"/Path(*rel.parts)
        downloaded.append(download_file_resumable(
            url,destination,max_bytes=max(item["bytes"],1),headers=headers))
    save_json(output_dir/"download_manifest.json",{
        "repository":repo,"revision":revision,"files":[str(p.relative_to(output_dir)) for p in downloaded],
        "bytes":total,"license_note":"Synthetic subset is CC BY 4.0; real clips retain source terms."})
    return downloaded


def ensure_free_space(path,required_bytes,reserve_bytes=10*1024**3):
    """Fail before a bulk download if it would consume the safety reserve."""
    path=Path(path)
    path.mkdir(parents=True,exist_ok=True)
    free=shutil.disk_usage(path).free
    if required_bytes>max(0,free-reserve_bytes):
        raise OSError(
            f"Need {required_bytes} bytes, but only {free} bytes are free and "
            f"{reserve_bytes} bytes must remain")
    return free


def extract_zip(archive, output_dir, max_bytes=1024**3):
    """Extract provider archives within a byte budget, excluding unsafe paths."""
    root=Path(output_dir).resolve()
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if sum(i.file_size for i in entries)>max_bytes:
            raise ValueError('Expanded archive exceeds size budget')
        for info in entries:
            target=(root/info.filename).resolve()
            if not target.is_relative_to(root) or (info.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe ZIP member path or symlink')
        root.mkdir(parents=True,exist_ok=True)
        z.extractall(root)
    return sorted(root.rglob('*.mp4'))
