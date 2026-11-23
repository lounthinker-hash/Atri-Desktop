"""Read-only RGBA renderer for a d1 diary interruption. No desktop window is created.

The local layer is the painter input. Full-body methods are for offline artwork checks.
All native anchors are relative to the unpadded 666 x 1706 d1 actor.
"""
from functools import lru_cache
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops


HERE = Path(__file__).resolve().parent
HELD = HERE.parents[1]
POSTURE = HELD / "writing-posture"
BOUNDS = (-110, 478, 675, 1130)
NATIVE_HEIGHT = 1706
PAD_X = 112
PADDED_SIZE = (890, NATIVE_HEIGHT)
DISPLAY_SIZE = (203, 390)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rounded(point):
    return tuple(np.floor(np.asarray(point) + .5).astype(int))


@lru_cache(maxsize=1)
def _lift():
    return _module("atri_diary_interrupt_lift", HERE.parent / "lift/read.py")


class Renderer:
    def __init__(self):
        self.arms = _module("atri_interrupt_arm_sources", POSTURE / "arms/composite.py")
        self.head = _module("atri_interrupt_head_sources", POSTURE / "head/compose.py")
        self.postures = _read(POSTURE / "manifest.json")
        self.actor = self.arms.art(POSTURE / self.postures["files"]["sourceActor"])
        self.ink = self.arms.PROPS["ink"]
        self.spine_book = np.asarray(self.arms.PROPS["spine"])
        self.book_scale = self.arms.SCALE

    def _drawing(self, posture, stage, index, fixed_ink, start):
        if posture not in ("hip", "chest"):
            raise ValueError("posture must be hip or chest")
        is_lift = stage == "lift"
        source_stage, source_index = ("write", start) if is_lift else (stage, index)
        row = self.arms.PERF["stages"][source_stage][source_index]
        pose = self.arms.pose_metadata(source_stage, source_index)
        frame = self.arms.PROPS["frames"][row["prop_frame"]]
        if is_lift and fixed_ink is None:
            fixed_ink = frame["ink_tile"]
        parts = None
        if is_lift:
            parts = _lift().parts(start, index, posture)
            frame = parts["pen"]
        tile = frame["ink_tile"] if fixed_ink is None else fixed_ink
        if not isinstance(tile, int) or not 0 <= tile < self.ink["tile_count"]:
            raise ValueError("fixed_ink must be an exported cumulative ink tile")
        return source_stage, source_index, row, pose, frame, tile, parts

    def _layers(self, posture, stage, index, page, fixed_ink, height, start,
                split=False):
        """Paint onto one accumulator in the same order as DiaryPerformance.layer."""
        c = self.arms
        src, n, row, pose, pen_frame, tile, lift = self._drawing(
            posture, stage, index, fixed_ink, start)
        scale = height / NATIVE_HEIGHT
        base = np.asarray(BOUNDS[:2])
        def xy(point):
            return _rounded((np.asarray(point) - base) * scale)
        size = xy(BOUNDS[2:])
        fixed = Image.new("RGBA", size)
        def place(dst, im, origin, factor=1.):
            pos = xy(origin)
            end = xy(np.asarray(origin) + np.asarray(im.size) * factor)
            dims = end[0]-pos[0], end[1]-pos[1]
            if min(dims) > 0:
                dst.alpha_composite(im.resize(dims, Image.Resampling.LANCZOS), pos)
        def part(dst, data):
            if data is not None:
                place(dst, *data)
        chest = posture == "chest"
        offset = np.asarray(pose["delta"] if chest else (0, 0))
        t = pose["weight"] if chest else 0.
        book_origin = np.array((34., 991.)) + offset - self.spine_book * self.book_scale
        def book(dst, im, local=(0, 0)):
            place(dst, im, book_origin + np.asarray(local) * self.book_scale, self.book_scale)
        hand_at = np.asarray(c.HELD_INFO["hand_rect_actor_px"][:2]) + offset
        contact_at = np.asarray((1, 962)) + offset
        weight = t*t*(3-2*t)
        if chest:
            part(fixed, c.atlas_part(pose, "supportSleeve"))
        if weight:
            place(fixed, c.fade(c.art(c.HERE/"support-contact-back.png"), weight), contact_at)
        place(fixed, c.art(HELD/"grip-back.png"), hand_at)
        pages = c.PROPS["original_stowed_pages" if pen_frame["stowed"] else "pages"]
        book(fixed, c.art(c.PEN / pages[page]))
        w, h = self.ink["tile_size"]
        ix, iy = (tile % self.ink["columns"]) * w, (tile // self.ink["columns"]) * h
        ink = c.art(c.PEN / self.ink["atlas"]).crop((ix, iy, ix+w, iy+h))
        book(fixed, ink, self.ink["book_rect"][:2])
        if weight:
            place(fixed, c.fade(c.art(c.HERE/"support-contact-front.png"), weight), contact_at)
        place(fixed, c.support_front(t), hand_at)
        work = Image.new("RGBA", size) if split else fixed
        def finger(key):
            if lift is not None:
                part(work, lift[key])
            else:
                piece = c.sprite_piece(key, src, n)
                if piece is not None:
                    place(work, piece[0], np.asarray(piece[1]) + offset)
        finger("far")
        if not pen_frame["stowed"]:
            pen = c.PROPS["pen"]
            radians = math.radians(pen_frame["rotation_cw"])
            co, sn = math.cos(radians), math.sin(radians)
            f = self.book_scale * scale
            tip_x, tip_y = ((book_origin + self.book_scale*np.asarray(pen_frame["tip"]))-base) * scale
            x, y = pen["tip"]
            inverse = (co/f, sn/f, x-(tip_x*co+tip_y*sn)/f,
                       -sn/f, co/f, y+(tip_x*sn-tip_y*co)/f)
            img = c.art(c.PEN / pen["file"]).convert("RGBa")
            work.alpha_composite(img.transform(size, Image.Transform.AFFINE, inverse,
                                               Image.Resampling.BICUBIC).convert("RGBA"))
            if pen_frame["loop_in_front"]:
                book(work, c.art(c.PEN/c.PROPS["loop_front"]))
        finger("near")
        if lift is not None:
            part(work, lift["sleeve"])
        elif chest:
            part(work, c.atlas_part(pose, "workSleeve"))
        else:
            part(work, c.sprite_piece("sleeve", src, n))
        return (fixed, work) if split else fixed

    def local_layer(self, posture, stage, index, page="blank", fixed_ink=None,
                    height=390, start=None):
        """Registered transparent local RGBA at BOUNDS, with source-resolution component sampling."""
        return self._layers(posture, stage, index, page, fixed_ink, height, start)

    def repair_native(self, posture, stage, index, start=None):
        """Return (L erase, RGBA repair) registered to BOUNDS before local_layer.

        These are local pixels from the original sleeves and dress. Apply them
        once to the live, unpadded actor before the overlay and body warp.
        """
        src, n = ("write", start) if stage == "lift" else (stage, index)
        c = self.arms
        size = BOUNDS[2]-BOUNDS[0], BOUNDS[3]-BOUNDS[1]
        erase = Image.new("L", size)
        patch = Image.new("RGBA", size)
        def local(point):
            return point[0]-BOUNDS[0], point[1]-BOUNDS[1]
        if posture == "chest":
            e, p = c.prepare_actor(src, n)
            at = local(c.BOUNDS)
            erase.paste(e, at)
            patch.paste(p, at)
        elif posture == "hip":
            hand = c.HELD_INFO
            erase.paste(c.mask(HELD / hand["layers"]["rest_hand_erase"]["file"]),
                        local(hand["hand_rect_actor_px"]))
            if not c.PERF["stages"][src][n]["original_rest"]:
                repair = c.TURN_INFO["rest_patch"]
                at = local(repair["actor_rect_px"])
                erase.paste(c.mask(c.TURN / repair["erase"]), at)
                patch.paste(c.art(c.TURN / repair["restore"]), at)
        else:
            raise ValueError("posture must be hip or chest")
        return erase, patch

    def _clean(self, posture, stage, index, start):
        src, n = ("write", start) if stage == "lift" else (stage, index)
        c = self.arms
        actor = self.actor.copy()
        if posture == "chest":
            erase, repair = c.prepare_actor(src, n)
            pasted = Image.new("RGBA", PADDED_SIZE)
            pasted.paste(actor, (PAD_X, 0))
            left, top, right, bottom = c.BOUNDS
            box = left+PAD_X, top, right+PAD_X, bottom
            cropped = pasted.crop(box)
            cropped.putalpha(ImageChops.multiply(cropped.getchannel("A"), ImageChops.invert(erase)))
            cropped.alpha_composite(repair)
            pasted.paste(cropped, box[:2])
            actor = pasted.crop((PAD_X, 0, PAD_X+actor.width, actor.height))
            sample = self.postures["clips"][src]["frames"][n]
            if stage == "lift":
                progress = min(1., max(0., index / 10.))
                ratio = progress * progress * (3. - 2. * progress)
                initial = sample["eyeOffsetActorPx"]
                last = self.postures["clips"]["write"]["frames"][75]["eyeOffsetActorPx"]
                eye = [(1-ratio)*a + ratio*b for a,b in zip(initial,last)]
            else:
                eye = sample.get("eyeOffsetActorPx", (0, 0))
            actor = self.head.apply_head(actor, sample["headFrame"], eye)
        else:
            mask = c.mask(HELD / c.HELD_INFO["layers"]["rest_hand_erase"]["file"])
            actor.paste((0,0,0,0), tuple(c.HELD_INFO["hand_rect_actor_px"][:2]), mask)
            if not c.PERF["stages"][src][n]["original_rest"]:
                repair = c.TURN_INFO["rest_patch"]
                pt = tuple(repair["actor_rect_px"][:2])
                actor.paste((0,0,0,0), pt, c.mask(c.TURN / repair["erase"]))
                actor.alpha_composite(c.art(c.TURN / repair["restore"]), pt)
        out = Image.new("RGBA", PADDED_SIZE)
        out.paste(actor, (PAD_X,0))
        return out

    def prepare_native(self, posture, stage, index, start=None):
        """Padded base with rest-arm repairs applied, used for the art previews."""
        return self._clean(posture, stage, index, start)

    def native_frame(self, posture, stage, index, page="blank", fixed_ink=None, start=None):
        out = self._clean(posture, stage, index, start)
        local = self.local_layer(posture, stage, index, page, fixed_ink, NATIVE_HEIGHT, start)
        out.alpha_composite(local, (PAD_X+BOUNDS[0], BOUNDS[1]))
        return out

    def frame390(self, posture, stage, index, page="blank", fixed_ink=None, start=None):
        """Full transparent inspection sprite, actor 390 high, dimensions 203 x 390."""
        source = self.native_frame(posture, stage, index, page, fixed_ink, start)
        return source.convert("RGBa").resize(DISPLAY_SIZE, Image.Resampling.LANCZOS).convert("RGBA")

    def split_native(self, posture, stage, index, page="blank", fixed_ink=None, start=None):
        """Return padded background and separate working hand/pen/sleeve in native coordinates."""
        bg = self._clean(posture, stage, index, start)
        fixed, moving = self._layers(posture, stage, index, page, fixed_ink, NATIVE_HEIGHT, start, True)
        origin = PAD_X+BOUNDS[0], BOUNDS[1]
        bg.alpha_composite(fixed, origin)
        fg = Image.new("RGBA", PADDED_SIZE)
        fg.paste(moving, origin)
        return bg, fg
