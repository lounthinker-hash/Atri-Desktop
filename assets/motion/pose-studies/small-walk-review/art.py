"""Registered white-dress lower art, using signed horizontal foot plants from the drawing."""
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
POSE = HERE.parent
ROOT = HERE.parents[3]
SIDES = ("left", "right")
SCALE = 2
TILE = (312, 498)
TOP = 290


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=2)
def rows(direction):
    name = "weight-step" if direction > 0 else "weight-step-left"
    return read(POSE / name / "frames.json")


@lru_cache(maxsize=1)
def sources():
    directory = ROOT / "assets/sprites"
    original = Image.new("RGBA", (666, 1706))
    index = read(directory / "layers.json")["sets"]["tatr01"]["layers"]
    for key in (694, 695):
        item = next(layer for layer in index if layer["layer_id"] == key)
        with Image.open(directory / item["file"]) as part:
            original.alpha_composite(part.convert("RGBA"), (item["left"]-354, item["top"]-285))
    plane = Image.new("RGBA", (312, 788))
    plane.paste(original.convert("RGBa").resize((305, 780), Image.Resampling.LANCZOS).convert("RGBA"))
    array = np.asarray(plane).copy()
    y, x = np.indices(array.shape[:2])
    rgb = array[:,:,:3].astype(int)
    # Separate the peach thigh below the painted white hem, column by column.
    skin = ((y >= 515) & (y < 536) & (rgb[:,:,0] - rgb[:,:,2] > 33) & (array[:,:,3]>96))
    cut = np.where(skin.any(axis=0), skin.argmax(axis=0), 535)
    lower = y >= cut[None,:]
    cloth = array.copy()
    cloth[lower] = 0
    split = np.interp(y[:,0]/780, [.65,.7,.78,.86,.94,1.], [.52,.53,.548,.581,.609,.63])[:,None]*305
    leg_images = {}
    for side,mask in zip(SIDES,(x<split,x>=split)):
        pixels = array.copy()
        pixels[~(lower & mask)] = 0
        # Extend the hidden top of the thigh under the original hem.
        # The opaque painted skirt covers this join during knee and hip movement.
        for column in range(pixels.shape[1]):
            start = cut[column]
            # The first anti-aliased skin row includes the old hem outline.
            # Sample inside the thigh so a second horizontal hem line does not
            # move out below the skirt when the supporting leg straightens.
            pixel = pixels[start+5,column].copy()
            if pixel[3] > 100:
                pixels[start-38:start,column] = pixel
                for j in range(5):
                    if pixels[start+j,column,3] > 120:
                        pixels[start+j,column,:3]=pixel[:3]
        leg_images[side] = Image.fromarray(pixels)
    return Image.fromarray(cloth), leg_images, plane


def anchors(direction, index, amplitude):
    row = rows(direction)[index]
    rest = rows(direction)[0]
    legs = {}
    for side in SIDES:
        item = row["legs"][side]
        origin = rest["legs"][side]
        target = {}
        for key in ("hip", "knee", "ankle"):
            a = np.asarray(item[key], dtype=float)
            b = np.asarray(origin[key], dtype=float)
            target[key] = [float(b[0] + (a[0]-b[0])*amplitude), float(a[1])]
        sole = np.asarray(item["sole"]["point"])
        rest_sole = np.asarray(origin["sole"]["point"])
        target["sole"] = [float(rest_sole[0]+(sole[0]-rest_sole[0])*amplitude),float(sole[1])]
        target["contact"] = bool(item["sole"]["contact"])
        legs[side] = target
    return legs


def _warp_leg(source, original, target):
    # Map the hip, knee and ankle independently. The whole shoe follows its ankle rigidly.
    # All mapping is in unpadded, 2x the original 390-unit registration.
    a = np.asarray([original[k] for k in ("hip","knee","ankle")])*2
    b = np.asarray([target[k] for k in ("hip","knee","ankle")])*2
    delta = b-a
    sy = [0, *a[:,1], 820.]
    dy = [delta[0,1], *b[:,1], 820.+delta[-1,1]]
    sx = [delta[0,0], *delta[:,0], delta[-1,0]]
    levels = np.unique(np.clip(np.r_[TOP, np.round(b[:,1]),TOP+TILE[1]], TOP,TOP+TILE[1])).astype(int)
    mesh = []
    for y0, y1 in zip(levels[:-1],levels[1:]):
        y = np.interp([y0,y1],dy,sy)
        shift = np.interp(y,sy,sx)
        x0, x1 = -shift
        quad = (x0,y[0],x1,y[1],x1+TILE[0],y[1],x0+TILE[0],y[0])
        mesh.append(((0,int(y0)-TOP,TILE[0],int(y1)-TOP),tuple(quad)))
    return source.convert("RGBa").transform(TILE,Image.Transform.MESH,mesh,
                                            Image.Resampling.BICUBIC).convert("RGBA")


@lru_cache(maxsize=150)
def lower(direction, index, amplitude):
    item = rows(direction)[index]
    if index in (0,47):
        # Return the untouched original lower layer at a seam shared by two steps.
        _, _, whole = sources()
        return whole.crop((0,TOP,TILE[0],TOP+TILE[1]))
    target = anchors(direction,index,amplitude)
    rest = rows(direction)[0]["legs"]
    cloth, surfaces, _ = sources()
    tile = Image.new("RGBA", TILE)
    for side in ("right","left"):
        tile.alpha_composite(_warp_leg(surfaces[side],rest[side],target[side]))
    dx,dy = item["upper_offset"]
    tile.alpha_composite(cloth, (round(2*dx*amplitude),round(2*dy)-TOP))
    return tile


def sample(total_dx, actual_dx, height=280):
    """Use the measured logical window offset. Return source pose and its subframe registration."""
    direction = 1 if total_dx>=0 else -1
    unit = 55.*height/390.
    count = max(1,round(abs(total_dx)/unit))
    amplitude = abs(total_dx)/(count*unit)
    scaled = min(float(count),max(0.,direction*actual_dx/max(abs(total_dx),1.e-8)*count))
    cycle = min(count-1, int(scaled))
    within = scaled-cycle
    values = np.array([abs(row["stage_travel_x"])/55. for row in rows(direction)])
    i = int(np.argmin(abs(values-within)))
    wanted = direction*count*unit*amplitude*(cycle+values[i])/count
    return direction,cycle,i,amplitude,wanted-actual_dx
