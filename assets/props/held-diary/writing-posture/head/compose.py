"""Register the lowered writing portrait to an unpadded, native d1/p1 sprite."""
import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=20)
def _image(file):
    with Image.open(HERE / file) as src:
        return src.copy()


@lru_cache(maxsize=1)
def _live_face_mask():
    # The outgoing mouth and eyelids relax while the first few head samples turn.
    # The oval is in crop coordinates, 100 pixels above the actor's y coordinate.
    mask = Image.new("L", (666, 472))
    ImageDraw.Draw(mask).ellipse((267, 255, 428, 379), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(7))


def _move_iris(patch, center, radius, offset):
    cx, cy = center
    rx, ry = radius
    x0, y0 = math.floor(cx-rx), math.floor(cy-ry)
    view = patch.crop((x0, y0, math.ceil(cx+rx)+1, math.ceil(cy+ry)+1))
    def sample(x, y):
        r = ((x+x0-cx)/rx)**2 + ((y+y0-cy)/ry)**2
        w = max(0., 1-r)**1.5
        return x-offset[0]*w, y-offset[1]*w
    # The eye rim and the lashes remain fixed. Only the interior follows the nib.
    mesh = []
    for y in range(0, view.height, 2):
        for x in range(0, view.width, 2):
            u, v = min(view.width,x+2), min(view.height,y+2)
            corners = sample(x,y) + sample(x,v) + sample(u,v) + sample(u,y)
            mesh.append(((x,y,u,v),corners))
    patch.paste(view.transform(view.size, Image.Transform.MESH, mesh,
                               Image.Resampling.BICUBIC), (x0, y0))


def apply_head(actor, pose_index, eye_offset=(0., 0.)):
    """Return a new RGBA sprite. A zero index preserves the incoming expression."""
    meta = _catalog()
    if actor.size != tuple(meta["canvas"]) or actor.mode != "RGBA":
        raise ValueError("Expected native 666 × 1706 unpadded RGBA d1/p1 artwork")
    if not 0 <= pose_index < len(meta["frames"]):
        raise ValueError("Head index outside the writing-pose manifest")
    result = actor.copy()
    if pose_index == 0:
        return result
    row = meta["frames"][pose_index]
    x, y, w, h = row["rectXYWH"]
    original = actor.crop((x, y, x+w, y+h)).convert("RGBa")
    replacement = _image(row["file"]).convert("RGBa")
    matte = _image(meta["mask"])
    live = row["retainLiveFace"]
    if live:
        matte = ImageChops.subtract(matte, _live_face_mask().point(lambda n: round(live*n)))
    patch = Image.composite(replacement, original, matte).convert("RGBA")
    if pose_index == 16 and any(eye_offset):
        for pupil, radius in zip(row["pupilsActorPx"], ((13, 8), (20, 10))):
            _move_iris(patch, (pupil[0]-x, pupil[1]-y), radius, eye_offset)
    result.paste(patch, (x, y))
    return result
