"""Animate the white sailor dress from its front artwork into its airborne side view.

The painted figure is divided into eight corresponding surfaces. Sleeves and legs
bend around their own centers, palms turn with the wrists, and the back of the
pleated skirt emerges from beneath the front hem. Both playback directions use
the same physical intermediate pose and the caller's actual expression and shoes.
"""

from collections import OrderedDict
import ctypes
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from desktop_pose_assets import read_plate

_ROOT = Path(__file__).resolve().parent
_DATA = _ROOT / "assets" / "motion" / "kick"
_STATE = "atri_kick_surface"
_REG = "atri_pose_registration"
_BODY, _LEFT_SLEEVE, _RIGHT_SLEEVE, _LEFT_LEG, _RIGHT_LEG, _PLEATS, _LEFT_HAND, _RIGHT_HAND = range(8)
_ORDER = (_PLEATS, _LEFT_LEG, _RIGHT_LEG, _LEFT_SLEEVE, _RIGHT_SLEEVE, _LEFT_HAND, _RIGHT_HAND, _BODY)
_LIMBS = {_LEFT_SLEEVE, _RIGHT_SLEEVE, _LEFT_LEG, _RIGHT_LEG}
_SLEEVES = {_LEFT_SLEEVE, _RIGHT_SLEEVE}
_HANDS = {_LEFT_HAND, _RIGHT_HAND}
_RASTER = ctypes.CDLL(str(_ROOT / "desktop" / "renderer" / "kick_sampler.dll"))
_PTR, _INT = ctypes.c_void_p, ctypes.c_int
_RASTER.surface.argtypes = [
    _PTR,
    _INT,
    _INT,
    _PTR,
    _INT,
    _INT,
    _PTR,
    _INT,
    _INT,
    _PTR,
    _PTR,
    _PTR,
    _PTR,
    _INT,
    _INT,
]
_RASTER.surface.restype = None
_TEXTURES = OrderedDict()


def _ease(value):
    t = np.clip(value, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _vector(angle):
    return np.stack((np.cos(angle), np.sin(angle)), axis=-1)


def _turn(a, b, t):
    difference = b - a
    return a + t * (difference - np.rint(np.median(difference) / (2 * np.pi)) * (2 * np.pi))


def _arrays(filename):
    packed = (_DATA / filename).with_suffix(".npy")
    if packed.is_file():
        table = np.load(packed, mmap_mode="r", allow_pickle=False)
        return {key: table[key][0] for key in table.dtype.names}
    with np.load(_DATA / filename, allow_pickle=False) as archive:
        return {key: archive[key] for key in archive}


@lru_cache(maxsize=3)
def _source(pose):
    return _arrays(pose + "-s.npz")


@lru_cache(maxsize=2)
def _body_pair(pose):
    return _arrays(pose + "-body.npz")


@lru_cache(maxsize=12)
def _masks(pose, size):
    prefix = f"kick/{pose}/{size[0]}x{size[1]}/mask"
    prepared = [read_plate(_DATA.parent, prefix + str(i), "L") for i in range(8)]
    if all(mask is not None for mask in prepared):
        return prepared
    data = _source(pose)
    masks = [
        Image.fromarray(data[f"mask{i}"]).resize(size, Image.Resampling.BILINEAR) for i in range(8)
    ]
    # Below the lowest hem all visible ink belongs to the independently
    # registered legs. Exclude antialias remnants left in the body matte.
    masks[_BODY].paste(0, (0, round(0.70 * size[1]), size[0], size[1]))
    return masks


@lru_cache(maxsize=8)
def _behind(pose, size):
    prepared = read_plate(_DATA.parent, f"kick/{pose}/{size[0]}x{size[1]}/behind")
    if prepared is not None:
        return prepared
    with Image.open(_DATA / (pose + "-behind.png")) as picture:
        return picture.convert("RGBA").resize(size, Image.Resampling.LANCZOS)


def _textures(image, pose):
    key = id(image), pose
    if key in _TEXTURES:
        _TEXTURES.move_to_end(key)
        return _TEXTURES[key][1]
    result = _paint_channels(pose, image.size, image.tobytes())
    _TEXTURES[key] = image, result
    if len(_TEXTURES) > 6:
        _TEXTURES.popitem(last=False)
    return result


@lru_cache(maxsize=8)
def _paint_channels(pose, size, rgba):
    # ExpressionRenderer returns a fresh frame even when the iris and mouth have
    # not moved. Cache by the exact bytes as well as object identity.
    paint = Image.frombytes("RGBA", size, rgba).convert("RGBa")
    blank = Image.new("RGBa", size)
    result = []
    for index, mask in enumerate(_masks(pose, size)):
        ink = Image.composite(paint, blank, mask)
        if index == _BODY and pose != "p3":
            head = _behind(pose, size).copy()
            head.alpha_composite(ink.convert("RGBA"))
            ink = head.convert("RGBa")
        result.append(ink.tobytes())
    return result


def _place(size, pose):
    w, h = size
    unit = h / (0.922 if pose == "p3" else 1.0) / 390.0
    return np.array(
        [
            round(-w / 2 + {"p1": 0, "p2": 6, "p3": 11}[pose] * unit),
            round(-h + (3 * unit if pose == "p3" else 0.0)),
        ]
    )


def _f64(array):
    return np.ascontiguousarray(array, dtype=np.float64)


@lru_cache(maxsize=16)
def _geometry(pose, size_a, size_b):
    ra, rb = _source(pose), _source("p3")
    body = _body_pair(pose)
    a, b = _place(size_a, pose), _place(size_b, "p3")
    scale_a, scale_b = size_a[1] / ra["size"][1], size_b[1] / rb["size"][1]
    floor = np.floor(np.minimum(a, b) - [55 * scale_a, 20 * scale_a]).astype(int)
    far = np.ceil(np.maximum(a + size_a, b + size_b) + [35 * scale_a, 26 * scale_a]).astype(int)
    surfaces = {}
    for i in _ORDER:
        if i == _PLEATS:
            pa, pb = body["pleatA"] * scale_a, body["pleatB"] * scale_b
        elif i == _BODY:
            pa, pb = body["a"] * scale_a, body["b"] * scale_b
        else:
            pa, pb = ra[f"pos{i}"] * scale_a, rb[f"pos{i}"] * scale_b
        item = {"pa": _f64(pa), "pb": _f64(pb)}
        if i in _LIMBS:
            ca, cb = ra[f"center{i}"] * scale_a, rb[f"center{i}"] * scale_b
            na, nb = ra[f"normal{i}"], rb[f"normal{i}"]
            da, db = np.diff(ca, axis=0), np.diff(cb, axis=0)
            item.update(
                ca=ca,
                cb=cb,
                width_a=ra[f"width{i}"] * scale_a,
                width_b=rb[f"width{i}"] * scale_b,
                length_a=np.linalg.norm(da, axis=1),
                length_b=np.linalg.norm(db, axis=1),
                na=na,
                nb=nb,
                aa=np.unwrap(np.arctan2(da[:, 1], da[:, 0])),
                ab=np.unwrap(np.arctan2(db[:, 1], db[:, 0])),
                angle_a=np.unwrap(np.arctan2(na[:, 1], na[:, 0])),
                angle_b=np.unwrap(np.arctan2(nb[:, 1], nb[:, 0])),
            )
            if i in _SLEEVES:
                wa = ra[f"wrist{i+5}"] * scale_a
                wb = rb[f"wrist{i+5}"] * scale_b
                nearest = int(
                    np.argmin(
                        np.linalg.norm(ca - wa, axis=1) / scale_a
                        + np.linalg.norm(cb - wb, axis=1) / scale_b
                    )
                )
                tna, tnb = np.array([-na[nearest, 1], na[nearest, 0]]), np.array(
                    [-nb[nearest, 1], nb[nearest, 0]]
                )
                item.update(
                    wrist_index=nearest,
                    aw=np.array([(wa - ca[nearest]) @ na[nearest], (wa - ca[nearest]) @ tna]),
                    bw=np.array([(wb - cb[nearest]) @ nb[nearest], (wb - cb[nearest]) @ tnb]),
                )
        elif i in _HANDS:
            item.update(
                angle_a=ra[f"dir{i}"],
                angle_b=rb[f"dir{i}"],
                radius_a=np.linalg.norm(pa - ra[f"palm{i}"] * scale_a, axis=2),
                radius_b=np.linalg.norm(pb - rb[f"palm{i}"] * scale_b, axis=2),
                palm_a=np.linalg.norm(ra[f"palm{i}"] - ra[f"wrist{i}"]) * scale_a,
                palm_b=np.linalg.norm(rb[f"palm{i}"] - rb[f"wrist{i}"]) * scale_b,
                rays=np.linspace(-np.pi, np.pi, pa.shape[1]),
            )
        surfaces[i] = item
    return a, b, floor, tuple(far - floor), scale_a, scale_b, surfaces


def _limb(item, a, b, t):
    center_start = (1 - t) * (item["ca"][0] + a) + t * (item["cb"][0] + b)
    angle = _turn(item["aa"], item["ab"], t)
    length = (1 - t) * item["length_a"] + t * item["length_b"]
    center = center_start + np.vstack(([0, 0], np.cumsum(_vector(angle) * length[:, None], axis=0)))
    normal = _vector(_turn(item["angle_a"], item["angle_b"], t))
    width = (1 - t) * item["width_a"] + t * item["width_b"]
    return center[:, None, :] + normal[:, None, :] * width[:, :, None], center, normal


def _landing_leg(item, index, a, b, t, scale):
    """Plant the incoming shoe before the pelvis has finished following it.

    The front drawing rests on the viewer-left shoe. In the side drawing the
    other shoe hangs lowest. An evenly rotating leg puts its toe on the floor
    *before* it has travelled beneath the hip. Register the sole and ankle as
    one piece, letting the knee take up the remaining pelvis travel.
    """
    points, center, normal = _limb(item, a, b, t)
    left = index == _LEFT_LEG
    if left:
        phase = float(_ease((t - .20) / .80))
    else:
        # Keep the side-view support under her while the leading leg extends
        # towards the floor. Only once that foot has arrived may the trailing
        # heel roll up and take its final standing position.
        phase = float(_ease(t / .17))
    sole, _, _ = _limb(item, a, b, phase)
    # Lift before swinging and set down only after lateral travel. All toes and
    # the counter follow the same arc, without bending the shoe at the ankle.
    if left:
        sole[:, :, 1] -= 30.0 * scale * np.sin(np.pi * phase)**1.35
    else:
        # The airborne endpoint is registered three display pixels below the
        # standing root. Bring its support to the common floor during weight
        # transfer, instead of planting below it and springing back above it.
        sole[:, :, 1] -= 5.0 * scale * float(_ease(t / .17)) * (1 - float(_ease((t-.58)/.42)))
    station = np.linspace(0., 1., len(points))[:, None, None]
    weight = _ease((station - .21) / .51)
    return points + (sole - points) * weight, center, normal


def _raster(size, textures, sizes, destination, a, b, phase):
    low = np.maximum(0, np.floor(destination.min(axis=(0, 1)) - 1).astype(int))
    high = np.minimum(size, np.ceil(destination.max(axis=(0, 1)) + 1).astype(int))
    w, h = np.maximum(1, high - low)
    layer = np.zeros((h, w, 4), dtype=np.uint8)
    destination = _f64(destination-low)
    a, b = _f64(a), _f64(b)
    phase = _f64(np.broadcast_to(phase, destination.shape[:2]))
    _RASTER.surface(
        layer.ctypes.data,
        w,
        h,
        textures[0],
        *sizes[0],
        textures[1],
        *sizes[1],
        destination.ctypes.data,
        a.ctypes.data,
        b.ctypes.data,
        phase.ctypes.data,
        *phase.shape,
    )
    return Image.fromarray(layer), tuple(map(int, low))


_JOINTS = (
    ((123, 192), (268, 154)),
    ((140, 190), (301, 143)),
    ((99, 393), (191, 308)),
    ((162, 390), (281, 304)),
)


def _under_joint(output, index, material, sizes, pa, pb, points, a, b, sa, sb, origin, phase, t):
    # The source drawings omit the fabric under the arm and the skin under the skirt.
    # A strip from within the original sleeve/thigh sits behind that moving joint.
    if t <= 0.002 or t >= 0.998:
        return
    station = int((0.24 if index != _RIGHT_SLEEVE else 0.26) * len(points))
    aa, bb = _JOINTS[index - 1]
    anchor = (1 - t) * (np.asarray(aa) * sa + a) + t * (np.asarray(bb) * sb + b)
    col = np.linspace(0.01, 0.99, 17)
    border = (col * (points.shape[1] - 1)).astype(int)
    interior = (np.linspace(0.32, 0.68, 17) * (points.shape[1] - 1)).astype(int)
    joint = points[station, border]
    d = joint.mean(axis=0) - anchor
    length = np.linalg.norm(d)
    if length < 2:
        return
    normal = np.array([d[1], -d[0]]) / length
    if normal @ (joint[-1] - joint[0]) < 0:
        normal = -normal
    width = 42 if index in _SLEEVES else 22
    base = anchor + (col - 0.5)[:, None] * width * (sa + sb) * normal
    along = np.linspace(0, 1, 12)[:, None, None]
    dest = (1 - along) * base[None, :, :] + along * joint[None, :, :]
    sample = np.linspace(station + 5, station, 12).astype(int)
    cover, position = _raster(
        output.size,
        material,
        sizes,
        dest - origin,
        pa[sample][:, interior],
        pb[sample][:, interior],
        np.broadcast_to(phase, points.shape[:2])[sample][:, border],
    )
    fade = min(1.0, t / 0.12, (1 - t) / 0.12)
    if fade < 1:
        cover.putalpha(cover.getchannel("A").point(lambda v: round(v * fade)))
    output.alpha_composite(cover, position)


def _frame(front, pose, side, u, pixel_center):
    a, b, floor, size, sa, sb, surfaces = _geometry(pose, front.size, side.size)
    t = float(_ease(u))
    # Qt places a 1:1 image on the nearest device pixel. An odd-width side
    # drawing and an even-width front drawing can therefore be one column apart
    # from the integer atlas origin. Move its ink through subpixels in the
    # surface sampler, keeping the padded Qt rectangle on one fixed grid.
    unit = front.height / 390.0
    ideal = np.array([-front.width / 2 + (6*unit if pose=="p2" else 0), -side.width / 2 + 11*unit])
    shifts = np.floor(pixel_center + ideal + .5) - np.floor(pixel_center+.5) - (a[0],b[0])
    shift = (1-t)*shifts[0]+t*shifts[1]
    origin = floor - (shift,0)
    first, second = _textures(front, pose), _textures(side, "p3")
    output = Image.new("RGBA", size)
    wrists = {}
    for j in _ORDER:
        it = surfaces[j]
        pa, pb = it["pa"], it["pb"]
        pair = (first[j], second[j])
        if j in _LIMBS:
            if j in _SLEEVES:
                points, center, normal = _limb(it, a, b, t)
            else:
                points, center, normal = _landing_leg(it, j, a, b, t, sa)
            s = np.linspace(0.0, 1.0, len(center))[:, None]
            if j in _SLEEVES:
                q = it["wrist_index"]
                oc = (1 - t) * it["aw"] + t * it["bw"]
                tangent = np.array([-normal[q, 1], normal[q, 0]])
                wrists[j] = center[q] + normal[q] * oc[0] + tangent * oc[1]
                peak = 0.24 + 0.46 * s
            else:
                peak = 0.40 + 0.29 * s
            phase = _ease((t - peak + 0.14) / 0.28)
            _under_joint(
                output, j, pair, (front.size, side.size), pa, pb, points, a, b, sa, sb, origin, phase, t
            )
        elif j in _HANDS:
            angle = _turn(it["angle_a"], it["angle_b"], t)
            reach = (1 - t) * it["palm_a"] + t * it["palm_b"]
            # Slight overlap keeps the palm tucked into the wide blue cuff.
            reach -= 5 * sa * 4 * t * (1 - t)
            palm = wrists[j - 5] + reach * _vector(angle)
            radius = (1 - t) * it["radius_a"] + t * it["radius_b"]
            points = palm + radius[:, :, None] * _vector(angle + it["rays"])[None, :, :]
            phase = _ease((t - 0.43) / 0.34)
        else:
            points = (1 - t) * (pa + a) + t * (pb + b)
            if j == _PLEATS:
                phase = 1.0
            else:
                s = np.clip((pa[:, :, 1] / front.height - 0.14) / 0.52, 0, 1)
                peak = 0.25 + 0.46 * s + 0.025 * np.clip(pa[:, :, 0] / front.width - 0.5, -1, 1)
                phase = _ease((t - peak + 0.115) / 0.23)
        layer, position = _raster(size, pair, (front.size, side.size), points - origin, pa, pb, phase)
        output.alpha_composite(layer, position)
    # Restore fine line ends and the one-pixel silhouette sampled differently by
    # the closed atlas. Movement already starts at the original pose during this handoff.
    edge = min(u, 1 - u)
    if edge < 0.10:
        source, at = (front, a) if u < 0.5 else (side, b)
        exact = Image.new("RGBA", size)
        exact.paste(source, tuple(np.rint(at - origin).astype(int)))
        output = Image.blend(
            exact.convert("RGBa"), output.convert("RGBa"), float(_ease(edge / 0.10))
        ).convert("RGBA")
    return output, tuple(map(int, floor)), a, b, t, shift


def render(start, pose_start, end, pose_end, progress, registration, pixel_center=0.):
    """Registered physical-pixel frame, or None when an unrelated snapshot needs the generic bridge."""
    history = start.info.get(_STATE)
    if history:
        pose, front, side, u0 = history
        if pose_end == pose:
            front = end
            u1 = 0.0
        elif pose_end == "p3":
            side = end
            u1 = 1.0
        else:
            return None
        u = u0 + (u1 - u0) * progress
    else:
        if start.info.get(_REG) or {pose_start, pose_end} not in ({"p1", "p3"}, {"p2", "p3"}):
            return None
        if pose_start == "p3":
            pose, front, side, u = pose_end, end, start, 1 - progress
        else:
            pose, front, side, u = pose_start, start, end, progress
    frame, origin, a, b, t, shift = _frame(front, pose, side, u, pixel_center)
    front_points, side_points = registration[pose], registration["p3"]
    marks = (
        (1 - t) * (front_points * front.size + a) + t * (side_points * side.size + b) - origin + (shift,0)
    ) / frame.size
    frame.info[_REG] = (origin, tuple(map(tuple, marks)), front.height)
    frame.info[_STATE] = (pose, front, side, u)
    return frame, origin
