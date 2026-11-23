"""Read the d1 contact-to-rest artwork using the last painted diary frame."""
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
from typing import NamedTuple


HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def catalog():
    return json.loads((HERE / "manifest.json").read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=1)
def lift_data():
    path = HERE / catalog()["files"]["lift"]
    return json.loads(path.read_text(encoding="utf-8-sig"))["starts"]


@lru_cache(maxsize=1)
def renderer():
    path = HERE / catalog()["files"]["renderer"]
    spec = importlib.util.spec_from_file_location("atri_diary_release_art", path)
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    return source.Renderer()


class Capture(NamedTuple):
    start: int
    page: str
    posture: str
    ink_tile: int


def capture(clip, index, page, posture="hip"):
    """Return None outside a paper-contact pose. index is the painted clip index."""
    offsets = catalog()["writeStartByClip"]
    if clip not in offsets:
        return None
    start = index - offsets[clip]
    row = lift_data().get(str(start))
    if row is None:
        return None
    page = page.removeprefix("open_")
    if page not in ("blank", "sparse", "filled") or posture not in ("hip", "chest"):
        raise ValueError("Expected a held page and its already painted writing posture")
    return Capture(start, page, posture, row["inkTile"])


def source_frame(sample):
    """Resolve an output sample to a stage and its zero-based local art index."""
    for section in catalog()["timeline"]:
        first, last = section["outputInclusive"]
        if first <= sample <= last:
            return section["stage"], section["sourceFirst"] + sample - first
    raise IndexError("The contact-to-rest exposure sheet contains samples 0 through 53")


def local_layer(ctx, sample, height=390):
    """Transparent PIL RGBA for the existing book/shoulder transform and erase mask."""
    stage, index = source_frame(sample)
    return renderer().local_layer(ctx.posture, stage, index, page=ctx.page,
                                  fixed_ink=ctx.ink_tile, height=height, start=ctx.start)


def is_original_rest(sample):
    """The original relaxed right arm is visible only after the working layers finish."""
    return source_frame(sample) == ("return", 16)


def repair_native(ctx, sample):
    """Return the erase mask and repair art for one base actor, before body deformation."""
    stage, index = source_frame(sample)
    return renderer().repair_native(ctx.posture, stage, index, start=ctx.start)


@lru_cache(maxsize=1)
def _closing_art():
    from PIL import Image
    path = HERE / catalog()["files"]["closing"]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    with Image.open(path.parent / data["atlas"]) as source:
        return data, source.convert("RGBA")


def closing_ink(ctx, opening_index):
    """Return (RGBA, book xy) for the current original cover drawing, or None if hidden."""
    data, atlas = _closing_art()
    if not 0 <= opening_index < len(data["frames"]):
        raise IndexError("Expected the original opening index 0 (closed) to 11 (open)")
    row = data["frames"][-1-opening_index]
    if row["atlasRow"] is None or ctx.ink_tile == 0:
        return None
    x, y, right, bottom = row["bookRect"]
    w, h = data["cellSize"]
    sx, sy = ctx.ink_tile*w, row["atlasRow"]*h
    return atlas.crop((sx, sy, sx + right-x, sy + bottom-y)), (x, y)
