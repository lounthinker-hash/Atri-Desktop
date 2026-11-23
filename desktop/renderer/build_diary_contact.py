"""Build the lossless desktop nib-lift cache from the published original-art renderer."""
import argparse
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from zipfile import ZIP_DEFLATED,ZIP_STORED,ZipFile
import numpy as np


HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from desktop_props import lift_fingerprint

ASSETS=HERE.parents[1]/"assets/props/held-diary/write-interrupt"
HEIGHTS=(280,390,420,520,585,780)
PAGES=("blank","sparse","filled")


def install(temporary,target,fingerprint):
    try:
        os.replace(temporary,target)
        return target
    except PermissionError:
        if os.name!="nt" or not target.exists():raise
        # An existing companion may still have the original archive open.
        # Give new processes the complete replacement without disturbing it.
        alternate=target.parent/fingerprint/target.name
        alternate.parent.mkdir(exist_ok=True)
        if alternate.exists():
            with ZipFile(alternate) as existing:
                if existing.read("source.sha256").decode()==fingerprint:
                    Path(temporary).unlink()
                    return alternate
        os.replace(temporary,alternate)
        return alternate


def build(repack=False):
    spec=importlib.util.spec_from_file_location("atri_diary_art_builder",ASSETS/"playback.py")
    art=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(art)
    spec=importlib.util.spec_from_file_location("atri_corrected_diary_support",ASSETS.parent/"small-review/book/source.py")
    support=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    data=art.lift_data()
    metadata={key:{"inkTile":v["inkTile"],"stroke":v["stroke"],"frames":[
              {"sample":f["sample"],"anchors":{"hip":f["anchors"]["hip"]}} for f in v["frames"]]}
              for key,v in data.items()}
    target=HERE/"diary-contact.zip"
    source_hash=lift_fingerprint(ASSETS.parent)
    tmp=tempfile.NamedTemporaryFile(dir=HERE,suffix=".partial",delete=False)
    tmp.close()
    previous=ZipFile(target) if repack else None
    try:
        if previous and (previous.read("source.sha256").decode()!=source_hash or "lift-meta.json" in previous.namelist()):
            raise ValueError("Only a flat cache matching the current artwork can be repacked.")
        with ZipFile(tmp.name,"w",compression=ZIP_STORED) as outer:
            outer.writestr("source.sha256",source_hash)
            outer.writestr("lift-meta.json",json.dumps(metadata,separators=(",",":")).encode())
            for key in data:
                start=int(key)
                for height in HEIGHTS:
                    stream=BytesIO()
                    with ZipFile(stream,"w",compression=ZIP_DEFLATED,compresslevel=4) as inner:
                        for sample in range(1,10):
                            for page in PAGES:
                                name=f"{sample}/{page}"
                                if previous:
                                    encoded=previous.read(f"{height}/{start}/{name}")
                                else:
                                    ctx=art.capture("write",17+start,page)
                                    raw=support.local_layer(ctx.posture,"lift",sample,page,ctx.ink_tile,height,start).tobytes()
                                    if page=="blank":
                                        encoded=base=raw
                                    else:
                                        encoded=np.bitwise_xor(np.frombuffer(base,np.uint8),np.frombuffer(raw,np.uint8)).tobytes()
                                inner.writestr(name,encoded)
                    outer.writestr(f"{height}/{start}",stream.getvalue())
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise
    finally:
        if previous:previous.close()
    return install(tmp.name,target,source_hash)


if __name__=="__main__":
    options=argparse.ArgumentParser()
    options.add_argument("--repack-flat",action="store_true")
    print(build(options.parse_args().repack_flat))
