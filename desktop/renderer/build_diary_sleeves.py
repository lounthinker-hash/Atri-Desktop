"""Pack exact native wrist and sleeve exposures for the desktop's cold frames."""
import hashlib
import json
from pathlib import Path
import struct
import zipfile
from PIL import Image

ASSETS=Path(__file__).resolve().parents[2]/"assets"


def build():
    base=ASSETS/"props/held-diary/action-hand/write"
    catalog=json.loads((base/"performance.json").read_bytes())
    frames=[frame for stage in catalog["stages"].values() for frame in stage]
    target=ASSETS/"motion/diary-sleeves.zip"
    staging=target.with_suffix(".building")
    with zipfile.ZipFile(staging,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as bundle:
        for index,name in enumerate(catalog["atlases"]["sleeve"][:2]):
            path=base/name
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            rects={tuple(f["sleeve"]["rect"]) for f in frames if f.get("sleeve") and f["sleeve"]["image"]==index}
            with Image.open(path) as source:
                sheet=source.convert("RGBA")
            for rect in sorted(rects):
                tile=sheet.crop(rect)
                key=digest+"/"+"-".join(map(str,rect))
                bundle.writestr(key,struct.pack("<II",*tile.size)+tile.tobytes())
    staging.replace(target)


if __name__=="__main__":
    build()
