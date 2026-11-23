"""Read the small d1 rear-view lower-body drawing by actual upward window travel."""
from bisect import bisect_left
from functools import lru_cache
import json
from pathlib import Path

from PIL import Image


HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _assets():
    doc = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    with Image.open(HERE / doc["atlas"]) as image:
        atlas = image.convert("RGBA")
    rows = doc["frames"]
    return doc, atlas, [-r["windowDeltaY"] for r in rows]


def sample_lower(window_delta_y):
    """The reported case runs from 0 to -186 logical px. Return a native RGBA crop."""
    doc, atlas, distances = _assets()
    d = -float(window_delta_y)
    if not distances[0] <= d <= distances[-1]:
        raise ValueError("Expected the observed upward path from 0 to -186 logical px")
    i = min(bisect_left(distances,d),len(distances)-1)
    if i and d-distances[i-1] < distances[i]-d:
        i -= 1
    row = doc["frames"][i]
    w,h = doc["cellSize"]
    x = (row["cell"]%doc["columns"])*w
    y = (row["cell"]//doc["columns"])*h
    return atlas.crop((x,y,x+w,y+h)), tuple(doc["lowerOriginPhysicalPx"]), row
