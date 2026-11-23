"""Foot-registered transitions between ATRI's front poses and the side-on kick."""

from functools import lru_cache
from collections import OrderedDict
import math

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from scipy.spatial import Delaunay
from scipy.ndimage import binary_dilation, distance_transform_cdt, label

# (x, y) in thousandths of the original, uncropped expression canvas.
# Each row follows the same painted detail through the three original drawings.
_POINTS = (
    np.array(
        [
            #                p1          p2          p3
            [(521, 86), (535, 88), (491, 8)],  # raised hair strand
            [(510, 136), (508, 136), (572, 52)],  # crown
            [(337, 174), (353, 173), (480, 94)],  # left ribbon
            [(722, 174), (676, 173), (659, 71)],  # right ribbon
            [(378, 240), (387, 238), (496, 168)],  # left temple
            [(461, 232), (436, 232), (538, 169)],  # left eye
            [(581, 231), (559, 232), (601, 153)],  # right eye
            [(666, 234), (648, 233), (652, 135)],  # right temple
            [(519, 283), (508, 281), (570, 204)],  # chin
            [(415, 303), (420, 302), (528, 239)],  # left collar
            [(624, 302), (599, 303), (642, 211)],  # right collar
            [(513, 338), (483, 338), (566, 269)],  # sailor knot
            [(472, 405), (458, 403), (520, 325)],  # necktie tip
            [(348, 325), (350, 291), (495, 256)],  # left shoulder
            [(705, 326), (683, 292), (690, 219)],  # right shoulder
            [(220, 440), (209, 200), (425, 310)],  # outer left elbow
            [(838, 438), (804, 194), (789, 281)],  # outer right elbow
            [(139, 528), (149, 119), (374, 329)],  # left cuff
            [(879, 534), (845, 117), (910, 285)],  # right cuff
            [(51, 559), (76, 69), (344, 298)],  # left wrist
            [(946, 560), (903, 67), (961, 251)],  # right wrist
            [(16, 562), (47, 28), (332, 281)],  # left fingertips
            [(982, 567), (954, 26), (977, 208)],  # right fingertips
            [(270, 461), (269, 454), (337, 422)],  # left locks
            [(817, 467), (784, 458), (804, 408)],  # right locks
            [(250, 522), (202, 539), (258, 486)],  # left hair tips
            [(905, 534), (834, 548), (812, 509)],  # right hair tips
            [(249, 585), (252, 585), (258, 504)],  # left skirt side
            [(825, 582), (793, 580), (777, 533)],  # right skirt side
            [(183, 640), (153, 635), (215, 534)],  # skirt edges
            [(880, 640), (848, 641), (838, 585)],
            [(369, 687), (365, 677), (377, 551)],  # left thigh at hem
            [(667, 683), (640, 676), (600, 597)],  # right thigh at hem
            [(482, 687), (477, 681), (478, 579)],  # mid hem
            [(406, 751), (395, 752), (310, 629)],  # left knee
            [(695, 761), (664, 764), (577, 702)],  # right knee
            [(424, 825), (415, 825), (223, 678)],  # left calf
            [(754, 823), (716, 824), (559, 808)],  # right calf
            [(471, 931), (454, 932), (116, 730)],  # left ankle
            [(501, 963), (476, 959), (91, 758)],  # left heel
            [(535, 989), (504, 985), (16, 702)],  # left toe
            [(760, 900), (717, 900), (495, 927)],  # right ankle
            [(755, 928), (713, 925), (484, 975)],  # right heel
            [(708, 953), (674, 955), (400, 932)],  # right toe
        ],
        dtype=np.float64,
    )
    / 1000
)
_INDEX = {"p1": 0, "p2": 1, "p3": 2}


def _placement(size, pose):
    w, h = size
    standard_height = h / (0.922 if pose == "p3" else 1)
    dx = {"p1": 0, "p2": 6, "p3": 11}[pose] * standard_height / 390
    dy = (3 * standard_height / 390) if pose == "p3" else 0
    return int(round(dx - w / 2)), int(round(dy - h))


@lru_cache(maxsize=96)
def _geometry(size_a, pose_a, size_b, pose_b, step, prior_origin=None, prior_points=None):
    a = np.array(prior_origin if prior_origin is not None else _placement(size_a, pose_a))
    b = np.array(_placement(size_b, pose_b))
    corner = np.minimum(a - (0 if prior_origin is not None else 4), b - 4)
    far = np.maximum(a + size_a + (0 if prior_origin is not None else 4), b + size_b + 4)
    w, h = (far - corner).astype(int)
    local_a = np.array(prior_points) if prior_points is not None else _POINTS[:, _INDEX[pose_a]]
    pa = local_a * size_a + a - corner
    pb = _POINTS[:, _INDEX[pose_b]] * size_b + b - corner
    # Boundary knots only constrain pixels that remain well outside the figure.
    boundary = np.array(
        [(0, 0), (w / 2, 0), (w, 0), (w, h / 2), (w, h), (w / 2, h), (0, h), (0, h / 2)]
    )
    pa = np.vstack((pa, boundary))
    pb = np.vstack((pb, boundary))
    mid = pa + (pb - pa) * (step / 48)
    t = step / 48
    # Corners surrounding each hand and shoe rotate with the bone. Straight
    # interpolation of opposed toe directions would squash a shoe edge-on.
    pa_extra = []
    pb_extra = []
    mid_extra = []
    extremities = []
    for root, tip, radius in ((19, 21, 0.025), (20, 22, 0.025), (38, 40, 0.028), (41, 43, 0.028)):
        va = pa[tip] - pa[root]
        vb = pb[tip] - pb[root]
        a_angle = math.atan2(va[1], va[0])
        b_angle = math.atan2(vb[1], vb[0])
        angle = a_angle + t * math.atan2(math.sin(b_angle - a_angle), math.cos(b_angle - a_angle))

        def orient(theta):
            c = math.cos(theta)
            s = math.sin(theta)
            return np.array([[c, s], [-s, c]])

        ba, bb, bm = orient(a_angle), orient(b_angle), orient(angle)
        la = np.linalg.norm(va)
        lb = np.linalg.norm(vb)
        lm = la + (lb - la) * t
        ha = radius * size_a[1]
        hb = radius * size_b[1]
        hm = (1 - t) * ha + t * hb
        mid[tip] = mid[root] + np.array([lm, 0.0]) @ bm
        if root in (38, 41):
            # The heel belongs to the rotating shoe. Linear heel motion and a
            # rotating toe give opposite directions half way through a turn.
            qa = (pa[root + 1] - pa[root]) @ ba.T
            qb = (pb[root + 1] - pb[root]) @ bb.T
            mid[root + 1] = mid[root] + (qa * (1 - t) + qb * t) @ bm
        extremities.append((root, lm, hm, bm, (la, ha, ba), (lb, hb, bb)))
        for along, across in (
            (-0.20, -0.68),
            (-0.20, 0.68),
            (0.65, -0.78),
            (0.65, 0.78),
            (1.25, -0.40),
            (1.25, 0.40),
        ):
            pa_extra.append(pa[root] + np.array([along * la, across * ha]) @ ba)
            pb_extra.append(pb[root] + np.array([along * lb, across * hb]) @ bb)
            mid_extra.append(mid[root] + np.array([along * lm, across * hm]) @ bm)
    pa = np.vstack((pa, pa_extra))
    pb = np.vstack((pb, pb_extra))
    mid = np.vstack((mid, mid_extra))
    triangles = Delaunay(mid)
    # PIL inverse quad mesh. Its resolution follows the rendered size instead of source DPI.
    cell_width = max(4., h / 86)
    xs = np.unique(np.linspace(0, w, max(9, math.ceil(w / cell_width) + 1)).round().astype(int))
    ys = np.unique(np.linspace(0, h, max(12, math.ceil(h / cell_width) + 1)).round().astype(int))
    xx, yy = np.meshgrid(xs, ys)
    xy = np.column_stack((xx.ravel(), yy.ravel()))
    tri = triangles.find_simplex(xy, tol=1e-7)
    transform = triangles.transform[tri]
    bary = np.einsum("ijk,ik->ij", transform[:, :2], xy - transform[:, 2])
    weights = np.column_stack((bary, 1 - bary.sum(axis=1)))
    vertices = triangles.simplices[tri]
    def mesh(source, origin, side):
        mapped = np.einsum("ij,ijk->ik", weights, source[vertices]) + corner - origin
        for start, end, radius in ((31,34,.032), (32,35,.032), (34,36,.028), (35,37,.028),
                                   (36,38,.022), (37,41,.022)):
            # Guide the ink across the sleeve and shin around their own bones.
            # Global triangulation alone can link one ankle to the other leg.
            dst = mid[end] - mid[start]
            src = source[end] - source[start]
            dst_length = np.linalg.norm(dst)
            src_length = np.linalg.norm(src)
            if min(dst_length, src_length) < 1:
                continue
            tangent = dst / dst_length
            normal = np.array([-tangent[1], tangent[0]])
            st = src / src_length
            sn = np.array([-st[1], st[0]])
            offset = xy - mid[start]
            along = offset @ tangent
            across = offset @ normal
            width = radius * h
            d = np.hypot(np.maximum.reduce([-along, along - dst_length, np.zeros_like(along)]), across)
            weight = np.clip((1.55 - d / width) / .72, 0, 1)[:, None]
            weight *= weight * (3 - 2 * weight)
            band = (source[start] + np.outer(along * src_length / dst_length, st)
                    + np.outer(across * (size_a, size_b)[side][1] / h, sn) + corner - origin)
            mapped += weight * (band - mapped)
        for root, length, thickness, basis, source_a, source_b in extremities:
            target_length, target_thickness, target_basis = (source_a, source_b)[side]
            local = (xy - mid[root]) @ basis.T
            along = local[:, 0]
            # Keep the palm, heel and toes in one local coordinate system.
            # A short cuff/ankle overlap hands the deformation back to the sleeve or calf.
            end_distance = np.maximum(-.32 * length - along, along - 1.16 * length)
            radius = np.hypot(np.maximum(end_distance, 0), local[:, 1]) / thickness
            strength = np.clip((1.55 - radius) / .60, 0, 1)[:, None]
            strength *= strength * (3 - 2 * strength)
            coords = local * (target_length / length, target_thickness / thickness)
            rigid = coords @ target_basis + source[root] + corner - origin
            mapped += strength * (rigid - mapped)
        return xs, ys, mapped.reshape(*xx.shape, 2).astype(np.float32)

    registered = tuple(map(tuple, mid[: len(_POINTS)] / np.array([w, h])))
    return (int(w), int(h)), tuple(map(int, corner)), mesh(pa, a, 0), mesh(pb, b, 1), registered


def _distance_field(alpha, size):
    # The distance field controls shape only; keep the ink at render resolution.
    mask = np.asarray(alpha.resize(size, Image.Resampling.BOX)) >= 96
    return distance_transform_cdt(mask).astype(np.float32) - distance_transform_cdt(~mask)


def _edge_colors(image, alpha):
    """Extend real painted color to antialias pixels that lack reliable RGB."""
    rgb = np.asarray(image.convert("RGBA"))[:, :, :3].copy()
    flimsy = (alpha > 0) & (alpha < 98)
    if flimsy.any():
        blurred = np.asarray(image.filter(ImageFilter.BoxBlur(1.35)))
        coverage = blurred[:, :, 3].astype(np.float32)
        sample = flimsy & (coverage > 15)
        rgb[sample] = np.minimum(255, blurred[sample, :3].astype(np.float32) * (255 / coverage[sample, None]))
    return rgb


def _trim_foot_fragments(image):
    # An ankle is continuous in all three original drawings. Remove isolated
    # lower-leg specks created where the exterior triangles sweep over a shoe.
    alpha = np.asarray(image.getchannel("A"))
    regions, count = label(alpha > 20)
    if count < 2:
        return image
    areas = np.bincount(regions.ravel())
    areas[0] = 0
    connected = binary_dilation(regions == areas.argmax(), iterations=2)
    bottom = round(image.height * .65)
    result = alpha.copy()
    result[bottom:] *= connected[bottom:]
    image.putalpha(Image.fromarray(result))
    return image


_LOOSE_PARTS = ((19, 21, .034), (20, 22, .034), (38, 40, .023), (41, 43, .023))


def _basis(vector):
    length = max(1., np.linalg.norm(vector))
    x, y = vector / length
    return np.array([[x, -y], [y, x]]), length


@lru_cache(maxsize=36)
def _part_masks(size, registration, height, separate_hands):
    points = np.asarray(registration) * size
    inv = Image.new("L", size, 255)
    parts = []
    for root, tip, radius in _LOOSE_PARTS:
        if root in (19, 20) and not separate_hands:
            continue
        base, end = points[root], points[tip]
        basis, length = _basis(end - base)
        width = radius * height
        pad = math.ceil(width * 1.45)
        lo = np.floor(np.minimum(base, end) - pad).astype(int).clip(0, size)
        hi = np.ceil(np.maximum(base, end) + pad).astype(int).clip(0, size)
        x, y, right, bottom = *lo, *hi
        yy, xx = np.mgrid[y:bottom, x:right]
        delta = np.stack((xx - base[0], yy - base[1]), axis=-1) @ basis
        axial = np.maximum(np.maximum(-length * .15 - delta[:, :, 0], delta[:, :, 0] - length), 0)
        dist = np.hypot(axial, delta[:, :, 1])
        coverage = np.clip((1.14 * width - dist) / max(1., .18 * width), 0, 1)
        mask = Image.fromarray(np.rint(coverage * 255).astype(np.uint8))
        box = tuple(map(int, (x, y, right, bottom)))
        inv.paste(ImageChops.darker(inv.crop(box), ImageChops.invert(mask)), box[:2])
        parts.append((root, tip, box, mask, base, basis, length))
    return inv, parts


def _warp_material(image, mesh, size, registration, target_points, height, separate_hands):
    """Transform hands and shoes once, through a local affine map of their bone."""
    inv, parts = _part_masks(image.size, registration, round(height), separate_hands)
    transparent = Image.new("RGBa", image.size)
    ink = Image.composite(image, transparent, inv)
    from desktop_motion import raster_grid
    body = raster_grid(ink, *mesh).convert("RGBA")
    for root, tip, box, mask, src_root, src_basis, src_length in parts:
        origin, end = target_points[root], target_points[tip]
        basis, length = _basis(end - origin)
        matrix = src_basis @ np.diag([src_length / length, 1.]) @ basis.T
        patch = image.crop(box)
        patch = Image.composite(patch, Image.new("RGBa", patch.size), mask)
        corners = np.array([(box[0], box[1]), (box[2], box[1]),
                            (box[2], box[3]), (box[0], box[3])])
        projected = (corners - src_root) @ np.linalg.inv(matrix).T + origin
        low = np.floor(projected.min(axis=0) - 2).astype(int).clip(0, size)
        high = np.ceil(projected.max(axis=0) + 2).astype(int).clip(0, size)
        if np.any(high <= low):
            continue
        offset = src_root - box[:2] + matrix @ (low - origin)
        coeff = np.column_stack((matrix, offset)).ravel()
        piece = patch.transform(tuple(high - low), Image.Transform.AFFINE, coeff,
                                Image.Resampling.BICUBIC).convert("RGBA")
        body.alpha_composite(piece, tuple(low))
    return body.convert("RGBa")


def _registered_ink(a, b, amount):
    v = amount * amount * (3 - 2 * amount)
    # Let the face turn before the shoes leave the floor. A simultaneous full
    # image mix would soften every facial and clothing line at the same instant.
    y = np.linspace(0, 1, a.height, dtype=np.float32)[:, None]
    # Treat the blouse, both cuffs and the front skirt as one drawing. A
    # horizontal seam through a lowered sleeve can create a second blue cuff.
    # Only below the hem do the feet follow a little later.
    leg = np.clip((y - .64) / .20, 0, 1)
    leg = leg * leg * (3 - 2 * leg)
    local = np.clip((v - (.27 + .24 * leg)) / .28, 0, 1)
    local = local * local * (3 - 2 * local)
    local = np.where(local < .05, 0., np.where(local > .90, 1., local)).astype(np.float32)
    mixed = np.flatnonzero((local[:, 0] > 0) & (local[:, 0] < 1))
    result = a.convert("RGBA")
    upper = np.flatnonzero(local[:, 0] >= 1)
    if upper.size:
        box = 0, 0, a.width, int(upper[-1]) + 1
        result.paste(b.crop(box).convert("RGBA"), (0, 0))
    if not mixed.size:
        return result
    first, last = int(mixed[0]), int(mixed[-1]) + 1
    band = 0, first, a.width, last
    a, b, local = a.crop(band), b.crop(band), local[first:last]
    aa, bb = a.getchannel("a"), b.getchannel("a")
    opacity_a = np.asarray(aa, dtype=np.float32)
    opacity_b = np.asarray(bb, dtype=np.float32)
    field = (1 - local) * _distance_field(aa, a.size) + local * _distance_field(bb, a.size)
    opacity = np.clip(field * 214 + 127.5, 0, 255)
    opacity = np.minimum(opacity, np.maximum(opacity_a, opacity_b))
    opacity = np.where(local <= 0, opacity_a, np.where(local >= 1, opacity_b, opacity))
    # Use actual endpoint color wherever the silhouette is opaque. The RGB
    # under a transparent pixel can contain zero or arbitrary original bytes.
    # Sharpen the material handoff so the two different sets of skirt folds
    # occupy a narrow travelling seam.
    ink = np.clip((local - .42) / .16, 0, 1)
    ink = ink * ink * (3 - 2 * ink)
    wa = (1 - ink) * opacity_a
    wb = ink * opacity_b
    wa = np.where((wb + wa < 2) & (opacity_a >= opacity_b), opacity_a, wa)
    wb = np.where((wb + wa < 2) & (opacity_b > opacity_a), opacity_b, wb)
    rgb_a = _edge_colors(a, opacity_a)
    rgb_b = _edge_colors(b, opacity_b)
    rgb = (rgb_a * wa[:, :, None] + rgb_b * wb[:, :, None]) / np.maximum(1, wa + wb)[:, :, None]
    result.paste(Image.fromarray(np.dstack((rgb, opacity)).clip(0, 255).astype(np.uint8)), (0, first))
    return result


# Conversion and reduction are expensive at high DPI. Preserve frozen endpoint
# pixels for the short lifetime of the handoff (strong refs prevent id reuse).
_RASTERS = OrderedDict()
_REGISTRATION = "atri_pose_registration"


def _pixels(image, scale):
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    key = (id(image), size)
    if key not in _RASTERS:
        premultiplied = image.convert("RGBa")
        if size != image.size:
            premultiplied = premultiplied.resize(size, Image.Resampling.BILINEAR)
        _RASTERS[key] = (image, premultiplied)
        if len(_RASTERS) > 5:
            _RASTERS.popitem(last=False)
    _RASTERS.move_to_end(key)
    return _RASTERS[key][1]


def bridge_pose(start, pose_start, end, pose_end, progress, *, pixel_center=None):
    """Return (RGBA image, (left, top)) in physical pixels relative to the floor.

    Pass uncropped original expression canvases at their actual display pixel
    heights (p3 uses .922). For an interrupted movement pass the *last returned
    PIL image* as start. Its in-memory registration retains its exact position
    and limb positions, even though its canvas is wider than the original pose.
    """
    if pose_start not in _INDEX or pose_end not in _INDEX:
        raise ValueError("Unsupported pose pair")
    t = min(1.0, max(0.0, float(progress)))
    prior = start.info.get(_REGISTRATION)
    if t <= 0 or t >= 1:
        image, name = (start,pose_start) if t <= 0 else (end,pose_end)
        if t<=0 and prior:
            return start,prior[0]
        if pixel_center is None:
            return image,_placement(image.size,name)
        nominal = image.height
        if name=="p3":
            nominal = prior[2] if prior else (end.height if pose_start=="p3" else start.height)
        dx={"p2":6,"p3":11}.get(name,0)*nominal/390
        dy=(3 if name=="p3" else 0)*nominal/390
        return image,(-image.width/2+dx,-image.height+dy)
    if "p3" in (pose_start, pose_end) or "atri_kick_surface" in start.info:
        from desktop_kick_surface import render
        transition = render(start, pose_start, end, pose_end, t,
                            {name: _POINTS[:, index] for name, index in _INDEX.items()},
                            pixel_center=0. if pixel_center is None else pixel_center)
        if transition is not None:
            return transition
    # Bound the intermediate rendering on high-DPI displays. The two actual
    # end frames remain at native resolution. Offset is restored to physical pixels.
    nominal = prior[2] if prior else start.height
    nominal = max(nominal, end.height)
    t = t * t * (3 - 2 * t)
    blend = min(1.0, max(0.0, (t - 0.32) / 0.36))
    # Keep fingers, eyes and footwear at native sampling during the one-source
    # parts. Only the short two-source overlap needs a smaller working buffer.
    scale = min(1.0, 500 / nominal) if 0 < blend < 1 else 1.0
    step = min(48, max(0, round(t * 48)))
    if step == 0:
        return start, prior[0] if prior else _placement(start.size, pose_start)
    if step == 48:
        return end, _placement(end.size, pose_end)
    a, b = _pixels(start, scale), _pixels(end, scale)
    old_origin = tuple(round(v * scale) for v in prior[0]) if prior else None
    old_points = prior[1] if prior else None
    size, origin, mesh_a, mesh_b, points = _geometry(
        a.size, pose_start, b.size, pose_end, step, old_origin, old_points
    )

    target_points = np.asarray(points) * size
    reg_a = old_points or tuple(map(tuple, _POINTS[:, _INDEX[pose_start]]))
    reg_b = tuple(map(tuple, _POINTS[:, _INDEX[pose_end]]))

    def warp_a():
        return _warp_material(a, mesh_a, size, reg_a, target_points, nominal * scale, pose_start == "p3")

    def warp_b():
        return _warp_material(b, mesh_b, size, reg_b, target_points, end.height * scale, pose_end == "p3")

    blend = min(1.0, max(0.0, (t - 0.32) / 0.36))
    color_phase = blend * blend * (3 - 2 * blend)
    if color_phase <= .29:
        raster = warp_a()
    elif color_phase >= .785:
        raster = warp_b()
    else:
        raster = _registered_ink(warp_a(), warp_b(), blend).convert(
            "RGBa"
        )
    if scale < 1:
        full_size = tuple(round(v / scale) for v in raster.size)
        raster = raster.resize(full_size, Image.Resampling.BILINEAR)
        origin = tuple(round(v / scale) for v in origin)
    result = _trim_foot_fragments(raster.convert("RGBA"))
    result.info[_REGISTRATION] = (origin, points, nominal)
    return result, origin
