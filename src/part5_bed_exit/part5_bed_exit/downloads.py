"""Official-source downloads with bounded size, atomic writes and provenance."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

import requests

SLP_ARCHIVE="https://coe.northeastern.edu/Research/AClab/SLP/SLP2022.zip"
FALLVISION_API="https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/75QPKK"
MODEL_URLS={
    "yolo11n.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt",
    "yolo11n-seg.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-seg.pt",
    "yolo11n-pose.pt":"https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt",
    "pose_landmarker_full.task":"https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task",
}


def sha256(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def save_json(path,data):
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


def _digest(path,algorithm):
    h=hashlib.new(algorithm.lower().replace("-",""))
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            h.update(block)
    return h.hexdigest()


def download_models(model_dir):
    return [download_file(url,Path(model_dir)/name,max_bytes=50*1024**2)
            for name,url in MODEL_URLS.items()]


class HTTPRangeReader(io.RawIOBase):
    """Seekable remote ZIP reader; never silently downloads the entire ZIP."""
    def __init__(self,url,block_size=256*1024,max_transfer=50*1024**2):
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

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self,offset,whence=0):
        p=offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        if p<0:
            raise ValueError("negative seek")
        self.position=p
        return p

    def read(self,size=-1):
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


def fallvision_inventory(output_dir):
    headers={}
    if os.getenv("DATAVERSE_TOKEN"):
        headers["X-Dataverse-key"]=os.environ["DATAVERSE_TOKEN"]
    r=requests.get(FALLVISION_API,headers=headers,timeout=45)
    r.raise_for_status()
    if "json" not in r.headers.get("Content-Type",""):
        raise RuntimeError("Dataverse returned an anti-bot/login page; use Files tab manually")
    version=r.json()["data"]["latestVersion"]
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
    """Choose exact unrestricted original RGB MP4 IDs after reviewing inventory."""
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
        path=download_file(f"https://dataverse.harvard.edu/api/access/datafile/{f['id']}",
                           Path(output_dir)/"raw"/f"{f['id']}_{name}",max_bytes,
                           checksum.get("value"),checksum.get("type","sha256"),headers)
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
