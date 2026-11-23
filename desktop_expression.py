"""Compose Atri's desktop expressions from the original game's transparent layers."""

from functools import lru_cache
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from desktop_pose_assets import PoseWarmup, read_plate


# Each value selects the same numbered brow, eye and mouth from the game's
# f1–fl face aliases. Mood names describe the visible art on tatr01 and tatr02.
MOODS = {
    "source": None,
    "neutral": "1",
    "relaxed": "2",
    "thoughtful": "g",
    "focused": "5",
    "curious": "8",
    "happy": "3",
    "proud": "b",
    "sleepy": "h",
    "attentive": "9",
    "worried": "7",
    "content": "l",
    "excited": "a",
    "shy": "2",
}
_FACE_GROUPS = ("頬", "口", "目", "眉")
_SPEECH_FACES = {
    "writing": {"眉":"1","目":"1","口":"1"},
    "humming": {"眉":"2","目":"g","口":"l"},
    "goodnight": {"眉":"2","目":"h","口":"2"},
    "reassuring": {"眉":"7","目":"2","口":"2"},
}
_CLOSED_EYES = {"3", "6", "c", "g", "l"}
_SMILING_MOUTHS = {"2", "3", "a", "b", "l"}
_RESAMPLE = Image.Resampling.LANCZOS


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _group(layer):
    return layer["key"].split("/", 1)[0]


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _bounds(layer):
    x, y = layer["left"], layer["top"]
    return x, y, x + layer["width"], y + layer["height"]


def _mix_parts(a, b, amount):
    """Move both alpha bounds to the interpolated box before mixing their ink."""
    if amount <= 0 or a is b:
        return a
    if amount >= 1:
        return b
    box = tuple(round(x + (y - x) * amount) for x, y in zip(a[0], b[0]))
    size = max(1, box[2] - box[0]), max(1, box[3] - box[1])
    # Premultiplied colors prevent colored or dark halos at the edge of a lid.
    x = a[1].convert("RGBa").resize(size, _RESAMPLE)
    y = b[1].convert("RGBa").resize(size, _RESAMPLE)
    return box, Image.blend(x, y, amount).convert("RGBA")


class ExpressionRenderer:
    """Render a Pillow RGBA image at a requested physical-pixel height.

    A given pose and height always use the same registration and canvas size,
    including across mouth and blink updates. Pass an item from sprites/manifest.
    Unrecognized or nonlayered portraits return None so the caller can use its art.
    """

    def __init__(self, root, *, standing_shoes=False):
        self.directory = Path(root) / "assets" / "sprites"
        self.standing_shoes = standing_shoes
        catalog = _read_json(self.directory / "layers.json")
        sprites = _read_json(self.directory / "manifest.json")
        self.warmup = PoseWarmup(self, sprites) if standing_shoes else None
        self.poses = catalog["poses"]
        self.layers = {name: {r["layer_id"]: r for r in data["layers"]}
                       for name, data in catalog["sets"].items()}
        self.face_ids = {}
        for name, rows in self.layers.items():
            for layer_id, row in rows.items():
                group = _group(row)
                if group in _FACE_GROUPS:
                    self.face_ids[name, row["key"].rsplit("/", 1)[-1]] = layer_id
        # Reproduce the crop used by the exported sprites, independent of which
        # dress, shoe or facial layer happens to be visible in this frame.
        self.crops = {}
        for pose, name in self.poses.items():
            ids = {i for sprite in sprites if sprite["pose"] == pose for i in sprite["layerIds"]}
            self.crops[pose] = _union([_bounds(self.layers[name][i]) for i in ids])

    @lru_cache(maxsize=240)
    def _part(self, pose, layer_id, height):
        name = self.poses[pose]
        layer = self.layers[name][layer_id]
        crop = self.crops[pose]
        scale = height / (crop[3] - crop[1])
        x0, y0, x1, y1 = _bounds(layer)
        with Image.open(self.directory / layer["file"]) as file:
            image = file.convert("RGBA")
        if _group(layer) == "口":
            # Some mouth layers contain nearly invisible marks far above the
            # face. Keep the lip warp anchored to the original mouth region.
            reference = self.layers[name][self.face_ids[name, "口1"]]
            x, y, r, b = _bounds(reference)
            region = max(x0, x - 48), max(y0, y - 18), min(x1, r + 48), min(y1, b + 30)
            image = image.crop((region[0] - x0, region[1] - y0,
                                region[2] - x0, region[3] - y0))
            x0, y0, x1, y1 = region
        left, top = round((x0 - crop[0]) * scale), round((y0 - crop[1]) * scale)
        right, bottom = round((x1 - crop[0]) * scale), round((y1 - crop[1]) * scale)
        if layer["opacity"] != 255:
            image.putalpha(image.getchannel("A").point(lambda a: a * layer["opacity"] // 255))
        image = image.resize((max(1, right - left), max(1, bottom - top)), _RESAMPLE)
        # The transparent margin differs between eye and mouth shapes.
        # Interpolate the bounds of the visible drawing, not the stored rectangle.
        box = image.getchannel("A").getbbox()
        if box:
            left, top = left + box[0], top + box[1]
            image = image.crop(box)
        return (left, top, left + image.width, top + image.height), image

    @lru_cache(maxsize=12)
    def _body(self, pose, ids, height):
        key = f"body/{pose}/{height}/" + ".".join(map(str, ids))
        prepared = read_plate(self.directory.parent / "motion", key)
        if prepared is not None:
            return prepared
        crop = self.crops[pose]
        width = round((crop[2] - crop[0]) * height / (crop[3] - crop[1]))
        canvas = Image.new("RGBA", (max(1, width), height))
        for layer_id in ids:
            box, layer = self._part(pose, layer_id, height)
            canvas.alpha_composite(layer, box[:2])
        return canvas

    @lru_cache(maxsize=1)
    def _shod_back_master(self):
        """Register the original brown loafer's counter and sole to the two rear ankles."""
        import numpy as np
        from scipy.interpolate import PchipInterpolator
        from scipy.ndimage import map_coordinates

        rear_layer = self.layers[self.poses["p4"]][231]
        shoe_layer = self.layers[self.poses["p3"]][1708]
        with Image.open(self.directory / rear_layer["file"]) as source:
            rear = source.convert("RGBA")
        with Image.open(self.directory / shoe_layer["file"]) as source:
            leather = np.asarray(source.convert("RGBA"))
        # Knots follow the upper and outsole, in the 665 x 1499 back drawing.
        # The viewer-left heel is raised. The right loafer carries her weight.
        outlines = (
            (True, [(159,1399,1399),(164,1388,1414),(175,1384,1425),(187,1384,1424),
                    (198,1388,1437),(211,1394,1456),(225,1403,1477),(240,1422,1485),
                    (255,1445,1485),(273,1454,1479),(282,1458,1474),(284,1465,1465)]),
            (False, [(309,1455,1455),(313,1447,1463),(328,1441,1469),(346,1437,1474),
                     (360,1433,1481),(373,1424,1490),(387,1417,1496),(400,1416,1497),
                     (413,1417,1493),(419,1425,1483),(420,1454,1454)]),
        )
        for mirrored, outline in outlines:
            knots, rim, sole = np.array(outline).T
            x = np.arange(knots[0], knots[-1]+1)
            y = np.arange(rim.min()-2, sole.max()+2)
            top = PchipInterpolator(knots, rim)(x)
            bottom = PchipInterpolator(knots, sole)(x)
            z = (y[:,None]-top) / np.maximum(1., bottom-top)
            along = (x-x[0]) / (x[-1]-x[0])
            if mirrored:
                along = 1.-along
            welt = .79-.14*np.abs(along-.5)
            upper = np.clip(z/welt, 0., 1.)
            quarter_x = np.broadcast_to(584+31*along, z.shape)
            source_knots = [584,588,592,600,610,615]
            quarter_top = np.interp(quarter_x, source_knots, [383,389,393,394,398,402])
            quarter_base = np.interp(quarter_x, source_knots, [399,403,410,416,426,426])
            quarter_y = quarter_top*(1.-upper) + quarter_base*upper
            tread_x = 475+149*along
            tread_top = np.interp(tread_x, [475,480,500,520,540,560,580,600,620,624],
                                  [351,347,350,355,362,370,384,403,427,417])
            last = np.where(leather[:,:,3] > 160, np.arange(leather.shape[0])[:,None], 0).max(axis=0)
            tread_base = np.interp(tread_x, np.arange(len(last)), last)-1.
            depth = np.clip((z-welt)/(1.-welt), 0., 1.)
            sample_y = np.where(z < welt, quarter_y, tread_top*(1.-depth)+tread_base*depth)
            sample_x = np.where(z < welt, quarter_x, tread_x)
            ink = np.stack([map_coordinates(leather[:,:,c], [sample_y,sample_x], order=1)
                            for c in range(3)], axis=-1)
            # At the rim and welt, retain a narrow dark edge like the original seam.
            rim_light = .72+.28*np.clip((y[:,None]-top)/3., 0., 1.)
            welt_light = 1.-.22*np.exp(-((z-welt)/.027)**2)
            rgb = np.rint(ink*(rim_light*welt_light)[:,:,None]).astype(np.uint8)
            cover = np.minimum(np.clip(y[:,None]-top+.5,0.,1.),
                               np.clip(bottom-y[:,None]+.5,0.,1.))
            shoe = Image.fromarray(np.dstack((rgb, np.rint(cover*255).astype(np.uint8))))
            # Original skin under the toe is hidden by the shoe, including its transparent margins.
            alpha = np.array(rear.getchannel("A"))
            for col in range(x[0]-16, x[-1]+13):
                cut = int(np.interp(col, x, top))+3
                alpha[cut:, col] = 0
            rear.putalpha(Image.fromarray(alpha))
            rear.alpha_composite(shoe, (int(x[0]),int(y[0])))
        return rear

    @lru_cache(maxsize=12)
    def _shod_back(self, size):
        prepared = read_plate(self.directory.parent / "motion", f"rear/{size[0]}x{size[1]}")
        if prepared is not None:
            return prepared
        return self._shod_back_master().resize(size, _RESAMPLE)

    def _face_part(self, pose, group, code, height):
        return self._part(pose, self.face_ids[self.poses[pose], group + code], height)

    @lru_cache(maxsize=48)
    def _eyelids(self, pose, code, height):
        opened = self._face_part(pose, "目", code, height)
        closed = self._face_part(pose, "目", "6", height)
        box = _union((opened[0], closed[0]))
        size = box[2] - box[0], box[3] - box[1]
        layers = []
        for bounds, layer in (opened, closed):
            aligned = Image.new("RGBA", size)
            aligned.paste(layer, (bounds[0] - box[0], bounds[1] - box[1]))
            layers.append(aligned)

        # Trace the dark lash of each shut eye. A distinct closure height for
        # each column preserves the tilt and curvature of the side-view eyes.
        pixels = layers[1].load()
        anchors = []
        for x in range(size[0]):
            weights = []
            for y in range(size[1]):
                red, green, blue, alpha = pixels[x, y]
                luminance = (red + 2 * green + blue) / 4
                weights.append((alpha * max(0, 150 - luminance)) ** 2)
            mass = sum(weights)
            if mass > 10000:
                middle = sum(y * weight for y, weight in enumerate(weights)) / mass
                anchors.append((x, middle))
        if not anchors:
            anchors = [(0, size[1] / 2)]
        # Continue the curve across the nose gap and transparent corner pixels.
        curve = []
        segment = 0
        for x in range(size[0] + 1):
            while segment + 1 < len(anchors) and anchors[segment + 1][0] <= x:
                segment += 1
            left, a = anchors[segment]
            right, b = anchors[min(segment + 1, len(anchors) - 1)]
            mix = max(0, x - left) / max(1, right - left)
            curve.append(a + (b - a) * mix)
        return box, layers[0], layers[1], curve

    def _blink_part(self, pose, code, height, blink):
        if blink >= 0.75:
            return self._face_part(pose, "目", "6", height)
        box, opened, closed, curve = self._eyelids(pose, code, height)
        w, h = opened.size
        # Collapse the original iris toward the lash rather than overlaying the
        # lash on a still-open iris. The surrounding skin is on the body layer.
        aperture = max(0.045, (1 - blink / 0.75) ** 1.12)
        # More than one row of iris can collapse into one destination row.
        # Supersample vertically so the shrinking lash remains a solid stroke.
        oversample = 4
        mesh = []
        for x in range(0, w, 2):
            stop = min(x + 2, w)
            y0, y1 = curve[x], curve[stop]
            mesh.append(((x, 0, stop, h * oversample),
                         (x, y0 - y0 / aperture, x, y0 + (h - y0) / aperture,
                          stop, y1 + (h - y1) / aperture, stop, y1 - y1 / aperture)))
        warped = opened.transform((w, h * oversample), Image.Transform.MESH, mesh,
                                  Image.Resampling.BICUBIC).resize((w, h), _RESAMPLE)
        if blink > 0.60:
            # Only a narrow slit remains. Confine it to the lash before
            # handing off to the artist's original closed-eye drawing.
            strength = (blink - 0.60) / 0.15
            alpha = warped.getchannel("A")
            band = closed.getchannel("A").filter(ImageFilter.MaxFilter(3))
            warped.putalpha(Image.blend(alpha, ImageChops.multiply(alpha, band), strength))
            warped = Image.blend(warped.convert("RGBa"), closed.convert("RGBa"),
                                 strength).convert("RGBA")
        return box, warped

    @lru_cache(maxsize=24)
    def _lip(self, pose, code):
        # Each mouth drawing also contains a nose mark. The wide smile has a
        # cheek mark. Register just the lip contour before reducing the face
        # to desktop size; at 280 px the shut lip is barely a pixel across.
        box, art = self._face_part(pose, "口", code, 1600)
        reference, neutral = self._face_part(pose, "口", "1", 1600)
        center = (reference[0]+neutral.width/2, reference[3]-3)
        mask = art.getchannel("A").point(lambda a: 255 if a > 16 else 0)
        components = []
        while mask.getbbox():
            left, top, right, _ = mask.getbbox()
            x = next(x for x in range(left, right) if mask.getpixel((x, top)))
            ImageDraw.floodfill(mask, (x, top), 128)
            component = mask.point(lambda a: 255 if a == 128 else 0)
            l,t,r,b = component.getbbox()
            distance = (box[0]+(l+r)/2-center[0])**2 + (box[1]+(t+b)/2-center[1])**2
            components.append((distance,(l,t,r,b)))
            mask.paste(0,mask=component)
        l,t,r,b = min(components)[1]
        pad = 2
        region = max(0,l-pad),max(0,t-pad),min(art.width,r+pad),min(art.height,b+pad)
        reg = tuple(n+box[i%2] for i,n in enumerate(region))
        return reg,art.crop(region)

    @lru_cache(maxsize=384)
    def _speaking_mouth(self, pose, smiling, aperture, height):
        # Keep the nose and lip origin fixed. Subpixel changes in the corners
        # and aperture are rasterized together, in the requested pixel grid.
        rest = "2" if smiling else "1"
        keys = ((0, rest), (.54, "a"), (1, "3")) if smiling else (
            (0, rest), (.22, "9"), (.58, "8"), (1, "4"))
        value = aperture/80
        for (lo,a),(hi,b) in zip(keys, keys[1:]):
            if value <= hi:
                lip_box,lip = _mix_parts(self._lip(pose,a),self._lip(pose,b),(value-lo)/(hi-lo))
                break
        base,ink = self._face_part(pose,"口",rest,1600)
        old,_ = self._lip(pose,rest)
        ink = ink.copy()
        ink.paste((0,0,0,0),tuple(n-base[i%2] for i,n in enumerate(old)))
        region = _union((base,lip_box))
        scale = height/1600
        target = (math.floor(region[0]*scale)-1,math.floor(region[1]*scale)-1,
                  math.ceil(region[2]*scale)+1,math.ceil(region[3]*scale)+1)
        # Register on the final pixel centers before the single reduction.
        master = Image.new("RGBA",(region[2]-region[0],region[3]-region[1]))
        master.alpha_composite(ink,(base[0]-region[0],base[1]-region[1]))
        master.alpha_composite(lip,(lip_box[0]-region[0],lip_box[1]-region[1]))
        size = target[2]-target[0],target[3]-target[1]
        sampling = tuple(n/scale-region[i%2] for i,n in enumerate(target))
        padding = math.ceil(2/scale)+2
        canvas = Image.new("RGBA",(master.width+padding*2,master.height+padding*2))
        canvas.paste(master,(padding,padding))
        return target,canvas.resize(size,_RESAMPLE,tuple(n+padding for n in sampling))

    def with_scene_face(self, white, face):
        """Transfer only the annotated facial layers to the chosen white body."""
        name=self.poses[white["pose"]]
        labels={token[:1]:token for token in (face or "").split(":") if token[:1] in _FACE_GROUPS}
        if not all((name,labels.get(g)) in self.face_ids for g in ("眉","目","口")):
            return None
        parts=[i for i in white["layerIds"] if _group(self.layers[name][i]) not in _FACE_GROUPS]
        parts.extend(self.face_ids[name,labels[g]] for g in _FACE_GROUPS if (name,labels.get(g)) in self.face_ids)
        return dict(white,layerIds=parts,face=face)

    @lru_cache(maxsize=240)
    def _gaze(self,pose,code,height,horizontal,down,lid_amount):
        import numpy as np
        box,eye=(self._blink_part(pose,code,height,.13*lid_amount/8) if lid_amount
                 else self._face_part(pose,"目",code,height))
        rgba=eye.convert("RGBa")
        out=rgba.copy()
        middle=rgba.width//2
        for x1,x2 in ((0,middle),(middle,rgba.width)):
            half=rgba.crop((x1,0,x2,rgba.height))
            bbox=half.getchannel("a").getbbox()
            if not bbox:continue
            l,t,r,b=bbox
            local=half.crop(bbox)
            w,h=local.size
            # Keep both corners and lash outline fixed while the irises track the page.
            yy,xx=np.mgrid[:h+1,:w+1]
            weight=np.sin(np.pi*xx/w)*np.sin(np.pi*yy/h)
            sx=xx-weight*w*.21*horizontal/8
            sy=yy-weight*h*.28*down/8
            mesh=[]
            for y in range(0,h,2):
                for x in range(0,w,2):
                    X,Y=min(w,x+2),min(h,y+2)
                    mesh.append(((x,y,X,Y),tuple(v for j,i in ((y,x),(Y,x),(Y,X),(y,X))
                                                    for v in (sx[j,i],sy[j,i]))))
            out.paste(local.transform(local.size,Image.Transform.MESH,mesh,Image.Resampling.BICUBIC),(x1+l,t))
        return box,out.convert("RGBA")

    def render(self, sprite, mood="source", blink=0.0, mouth=None, height=390, emphasis=0.,paper_focus=1.,gaze=None):
        """Compose a frame. Blink runs open-to-closed, mouth runs shut-to-open.

        With mouth=None the original expression's mouth stays in place. With
        mood="source" the exact selected scene's brows, eyes, mouth and blush stay
        in place until animated. The returned image is owned by the caller.
        """
        if not sprite or sprite.get("pose") not in self.poses or not sprite.get("layerIds"):
            return None
        pose = sprite["pose"]
        height = max(1, int(round(height)))
        if self.warmup is not None and pose == "p1":
            self.warmup.request(height)
        base = self.poses[pose]
        rows = self.layers[base]
        ids = tuple(i for i in sprite["layerIds"] if _group(rows[i]) not in _FACE_GROUPS)
        if self.standing_shoes and pose in ("p1","p2","p3"):
            # The two game drawings assign different alias numbers to the
            # same brown shoes. Keep the pair Atri is wearing through a hop.
            shoe = 1708 if pose=="p3" else 695
            ids = tuple(shoe if _group(rows[i])=="足" else i for i in ids)
        image = self._body(pose, ids, height).copy()
        if pose == "p4":
            if self.standing_shoes:
                # The desktop variant replaces only the two ankles and feet.
                box = (round(image.width*.20), round(height*.915), round(image.width*.66), height)
                image.paste(self._shod_back(image.size).crop(box), box[:2])
            return image
        parts = {}
        for layer_id in sprite["layerIds"]:
            layer = rows[layer_id]
            group = _group(layer)
            if group in _FACE_GROUPS:
                parts[group] = layer["key"].rsplit("/", 1)[-1][1:]
        code = MOODS.get(mood)
        if code is not None:
            parts.update(dict.fromkeys(("眉", "目", "口"), code))
        parts.update(_SPEECH_FACES.get(mood,{}))
        if mood == "shy" and (base, "頬1") in self.face_ids:
            parts["頬"] = "1"
        blink = min(1.0, max(0.0, float(blink)))
        for group in _FACE_GROUPS:
            if group not in parts:
                continue
            current = parts[group]
            if mood=="writing" and group=="目" and blink<.15:
                amount=round(max(0.,min(1.,paper_focus))*8)
                x,y=gaze if gaze is not None else (-amount,amount)
                down=self._gaze(pose,"1",height,x,y,amount)
                part=down if blink==0 else _mix_parts(down,self._blink_part(pose,current,height,blink),blink/.15)
            elif gaze and any(gaze) and group=="目" and current not in _CLOSED_EYES and blink<.2:
                eye=self._gaze(pose,current,height,*gaze,0)
                part=eye if blink==0 else _mix_parts(eye,self._blink_part(pose,current,height,blink),blink/.2)
            elif group == "目" and blink > 0 and current not in _CLOSED_EYES:
                part = self._blink_part(pose, current, height, blink)
            elif group == "口" and mouth is not None:
                value = min(1.0, max(0.0, float(mouth)))
                part = self._speaking_mouth(pose,current in _SMILING_MOUTHS,round(value*80),height)
            else:
                part = self._face_part(pose, group, current, height)
            if group=="眉" and emphasis>.005:
                box,ink=part
                travel=min(1.,emphasis)*height*.003
                margin=math.ceil(travel)+1
                expanded=Image.new("RGBA",(ink.width,ink.height+2*margin))
                expanded.paste(ink,(0,margin))
                ink=expanded.transform(expanded.size,Image.Transform.AFFINE,
                                       (1,0,0,0,1,travel),Image.Resampling.BILINEAR)
                image.alpha_composite(ink,(box[0],box[1]-margin))
            else:
                image.alpha_composite(part[1], part[0][:2])
        return image
