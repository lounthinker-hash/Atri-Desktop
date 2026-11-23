"""Rebuild dimension-specific lossless pose plates directly from the installed original layers."""
from pathlib import Path
import json
import struct
import sys
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import desktop_expression
from desktop_expression import ExpressionRenderer, _FACE_GROUPS, _group
import desktop_kick_surface as kick

directory = ROOT / "assets/motion"
renderer = ExpressionRenderer(ROOT, standing_shoes=True)
sprites = json.loads((ROOT / "assets/sprites/manifest.json").read_text(encoding="utf8"))
poses = {}
for sprite in sprites:
    if sprite.get("dress") == "d1" and sprite.get("origin") == "scene":
        poses.setdefault(sprite["pose"], sprite)

def store(pack, key, image):
    pack.writestr(key, struct.pack("<II", *image.size) + image.tobytes())

if __name__ == "__main__":
    # Regeneration must read the source artwork even if a previous archive exists.
    desktop_expression.read_plate = kick.read_plate = lambda *args: None
    dest = directory / "pose-plates.next"
    for f in kick._DATA.glob("*.npz"):
        with np.load(f, allow_pickle=False) as source:
            fields = {k: source[k] for k in source}
        record = np.empty(1, dtype=[(k, a.dtype, a.shape) for k,a in fields.items()])
        for k, a in fields.items(): record[k][0] = a
        np.save(f.with_suffix(".npy"), record, allow_pickle=False)
    with ZipFile(dest, "w", ZIP_DEFLATED, compresslevel=3) as pack:
        for tall in sorted({round(h*dpi) for h in (280,390,520) for dpi in (1.,1.25,1.5,1.75,2.)}):
            for pose, sprite in poses.items():
                height = round(tall*.922) if pose=="p3" else tall
                rows = renderer.layers[renderer.poses[pose]]
                ids = tuple((1708 if pose=="p3" else 695) if _group(rows[i])=="足" else i for i in sprite["layerIds"] if _group(rows[i]) not in _FACE_GROUPS)
                base = renderer._body(pose, ids, height)
                store(pack, f"body/{pose}/{height}/" + ".".join(map(str,ids)),base)
                if pose == "p4":
                    store(pack, f"rear/{base.width}x{height}", renderer._shod_back(base.size))
                else:
                    for i, m in enumerate(kick._masks(pose, base.size)):
                        store(pack, f"kick/{pose}/{base.width}x{height}/mask{i}",m)
                    if pose in ("p1","p2"):
                        store(pack,f"kick/{pose}/{base.width}x{height}/behind",kick._behind(pose,base.size))
    dest.replace(directory / "pose-plates.zip")
