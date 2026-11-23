"""Play the white sailor dress's shoulder-led arm raise in either direction.

Input images are unpadded ExpressionRenderer frames. Offsets are physical
pixels relative to the character's bottom center, with X right and Y down.
The wide intermediate canvas leaves room for the outstretched fingertips.
"""

from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image


_DIRECTORY = Path(__file__).resolve().parent / "assets" / "motion" / "reach"
_HEIGHT = 1706
_CROP_LEFT = {"p1": 354, "p2": 353}
_WIDE_LEFT = -15
_WIDE_WIDTH = 1450
_UPPER_HEIGHT = 1152
_FACE_BOX = (590, 594, 816, 787)
_CROP_TOP = 285
_LAST_FRAME = 64
_CACHE_BUDGET = 32 * 1024 * 1024
_plates = OrderedDict()
_plate_bytes = 0


@lru_cache(maxsize=1)
def _prepared_archive():
    try:
        return ZipFile(_DIRECTORY / "desktop-sizes.zip")
    except (OSError, BadZipFile):
        return None


def _placement(frame, pose):
    shift = 6 * frame.height / 390 if pose == "p2" else 0
    return shift - frame.width / 2, -frame.height


def _plate(step, size):
    global _plate_bytes
    key = step, size
    cached = _plates.get(key)
    if cached is not None:
        _plates.move_to_end(key)
        return cached
    archive = _prepared_archive()
    plate = None
    if archive is not None:
        for height in (390, 585):
            prepared = round(_WIDE_WIDTH*height/_HEIGHT), round(_UPPER_HEIGHT*height/_HEIGHT)
            if prepared[0] < size[0]:
                continue
            try:
                plate = Image.frombytes("RGBA", prepared, archive.read(f"{height}/{step:02}"))
            except (OSError, KeyError, BadZipFile, ValueError):
                break
            if prepared != size:
                plate = plate.resize(size, Image.Resampling.LANCZOS)
            break
    if plate is None:
        with Image.open(_DIRECTORY / f"{step:02}.webp") as source:
            plate = source.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    _plates[key] = plate
    _plate_bytes += 4 * plate.width * plate.height
    while _plate_bytes > _CACHE_BUDGET and len(_plates) > 1:
        _, old = _plates.popitem(last=False)
        _plate_bytes -= 4 * old.width * old.height
    return plate


@lru_cache(maxsize=12)
def _face_mask(size):
    with Image.open(_DIRECTORY / "face-mask.png") as source:
        return source.convert("L").resize(size, Image.Resampling.LANCZOS)


def _restore_expression(canvas, frame, pose, scale):
    # This mask covers the union of the original cheek, brow, eye and mouth
    # layers, including the outside of the lash during an intermediate blink.
    # Hair and neckline outside the mask stay on the moving upper-body plate.
    x, y, right, bottom = _FACE_BOX
    source_left = _CROP_LEFT[pose]
    source_box = (round((x - source_left) * scale), round((y - _CROP_TOP) * scale),
                  round((right - source_left) * scale), round((bottom - _CROP_TOP) * scale))
    region = frame.crop(source_box)
    if region.width and region.height:
        location = round((x - _WIDE_LEFT) * scale), source_box[1]
        canvas.paste(region, location, _face_mask(region.size))


def reach_pose(start_image, pose_start, end_image, pose_end, progress):
    """Return (RGBA, (left, top)) for p1 down <-> p2 raised.

    Supply the game's white sailor dress at both ends. Endpoints return the
    original live frames without resampling. The middle takes the facial
    expression and shoes from the live destination frame, so lip and eyelid
    animation remain responsive. The arms move on the same arc both ways.
    """
    if {pose_start, pose_end} != {"p1", "p2"}:
        raise ValueError("reach_pose needs one p1 and one p2 frame")
    if start_image.height != end_image.height:
        raise ValueError("both reach poses must have the same physical height")
    if progress <= 0:
        return start_image, _placement(start_image, pose_start)
    if progress >= 1:
        return end_image, _placement(end_image, pose_end)
    amount = float(progress) if pose_start == "p1" else 1 - float(progress)
    eased = amount * amount * (3 - 2 * amount)
    step = round(_LAST_FRAME * eased)
    height = end_image.height
    scale = height / _HEIGHT
    width = max(1, round(_WIDE_WIDTH * scale))
    upper_height = max(1, round(_UPPER_HEIGHT * scale))
    canvas = Image.new("RGBA", (width, height))
    canvas.paste(_plate(step, (width, upper_height)), (0, 0))
    left = round((_CROP_LEFT[pose_end] - _WIDE_LEFT) * scale)
    canvas.paste(end_image.crop((0, upper_height, end_image.width, height)), (left, upper_height))
    _restore_expression(canvas, end_image, pose_end, scale)
    # Both original crops use the same native tatr01 skeleton. Subpixel
    # correction accounts for the user's p2 anchor of +6 at height 390.
    placement_a = _placement(start_image, pose_start)[0] - (_CROP_LEFT[pose_start] - _WIDE_LEFT) * scale
    placement_b = _placement(end_image, pose_end)[0] - (_CROP_LEFT[pose_end] - _WIDE_LEFT) * scale
    return canvas, (placement_a + (placement_b - placement_a) * progress, -height)
