"""Read the transparent working fingers and sleeve of a precomputed nib-lift exit."""
from functools import lru_cache
from pathlib import Path
import json

from PIL import Image

HERE = Path(__file__).resolve().parent
HELD = HERE.parents[1]
HAND = HELD / "action-hand/write"
ARMS = HELD / "writing-posture/arms"


@lru_cache(maxsize=8)
def table(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=8)
def atlas(path):
    with Image.open(path) as image:
        return image.convert("RGBA")


def existing_piece(component, index, posture):
    """Return a native d1/p1 crop and its unpadded actor origin."""
    hand = table(HAND / "performance.json")
    chest = table(ARMS / "manifest.json")["stages"]["write"][index]
    if component == "sleeve" and posture == "chest":
        source = chest["workSleeve"]
        return atlas(ARMS / source["file"]).crop(source["rect"]), tuple(source["xy"])
    src = hand["stages"]["write"][index][component]
    path = HAND / hand["atlases"][component][src["image"]]
    x, y = src["xy"]
    if posture == "chest":
        dx, dy = chest["delta"]
        x, y = x+dx, y+dy
    return atlas(path).crop(src["rect"]), (x, y)


def parts(start, sample, posture):
    """Return far/near/sleeve (RGBA, xy), the book-local pen record and actor anchors.

    Use 'hip' or 'chest'. Both consume the same captured inkTile. The loose nib
    lies between far and near fingers and the sleeve is above the near fingers.
    """
    if posture not in ("hip", "chest"):
        raise ValueError("Expected hip or chest")
    doc = table(HERE / "manifest.json")
    row = doc["starts"][str(start)]["frames"][sample]
    result = {"pen":row["pen"], "anchors":row["anchors"][posture]}
    for component in ("far","near","sleeve"):
        if "sourceWriteFrame" in row:
            result[component] = existing_piece(component, row["sourceWriteFrame"], posture)
            continue
        name = posture+"Sleeve" if component == "sleeve" else component
        item = row["art"][name]
        x,y = item["xy"]
        if component != "sleeve" and posture == "chest":
            dx,dy = row["anchors"]["chest"]["bookShiftFromHipPx"]
            x,y = x+int(dx),y+int(dy)
        result[component] = atlas(HERE / item["file"]).crop(item["rect"]), (x,y)
    return result
