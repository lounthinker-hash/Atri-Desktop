"""Read a native 420px lower tile for either measured horizontal walk."""
from bisect import bisect_left
from functools import lru_cache
import json
from pathlib import Path
from PIL import Image

HERE=Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def tables():
    return json.loads((HERE/"frames.json").read_text(encoding="utf8"))


@lru_cache(maxsize=2)
def atlas(name):
    with Image.open(HERE/name) as source:
        return source.convert("RGBA")


def sample_lower(case, travel_x):
    """Absolute signed window travel since the start, in logical px; no desktop side effects."""
    data=tables()[case]
    total=data["dx"]
    count=data["stepCount"]
    single=abs(total)/count
    progress=min(abs(total),max(0.,travel_x*(1 if total>0 else -1)))
    cycle=min(count-1,int(progress/single))
    value=progress-single*cycle
    positions=[abs(f["rootTravelLogical"]) for f in data["rows"]]
    j=min(len(positions)-1,bisect_left(positions,value))
    if j>0 and value-positions[j-1]<=positions[j]-value:
        j-=1
    row=data["rows"][j]
    registered=cycle*total/count+row["rootTravelLogical"]
    return {
        "lower": atlas(data["atlas"]).crop(row["rect"]),
        "tileOriginPhysical": tuple(data["tileOriginPhysical"]),
        "upperOffsetPhysical": tuple(row["upperOffsetPhysical"]),
        "actorCorrectionLogical": registered-travel_x,
        "anchors390": row["anchors390"],
        "cycle":cycle,
        "frame":j
    }
