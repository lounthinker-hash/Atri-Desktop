"""Progressive front-to-back turn of the original white-dress artwork."""

from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops
from scipy.ndimage import gaussian_filter1d

from desktop_motion import raster_grid

# Corresponding drawing heights: flyaway, crown, temples, chin, shoulder, elbow,
# hem, knee and ankle. Profiles are sampled at these registered heights so the
# front face meets the back hair instead of exposing the two different head heights.
_FRONT_Y = np.array(
    [0, 0.086, 0.136, 0.178, 0.230, 0.282, 0.31, 0.43, 0.56, 0.65, 0.68, 0.75, 0.87, 0.94, 1.0]
)
_BACK_Y = np.array(
    [0, 0.008, 0.057, 0.102, 0.164, 0.226, 0.27, 0.405, 0.56, 0.64, 0.674, 0.75, 0.87, 0.94, 1.0]
)


@lru_cache(maxsize=14)
def _profile(alpha_bytes, size):
    w, h = size
    alpha = np.frombuffer(alpha_bytes, dtype=np.uint8).reshape(h, w) > 18
    rows = np.flatnonzero(alpha.any(axis=1))
    if not len(rows):
        raise ValueError("Empty turning artwork")
    left = np.argmax(alpha[rows], axis=1)
    right = w - np.argmax(alpha[rows, ::-1], axis=1)
    y = np.arange(h) + 0.5
    center = np.interp(y, rows + 0.5, (left + right) * 0.5)
    radius = np.interp(y, rows + 0.5, (right - left) * 0.5)
    sigma = max(0.75, h * 0.011)
    # Allow thin flyaways just beyond the locally smoothed profile.
    return gaussian_filter1d(center, sigma), gaussian_filter1d(radius, sigma) + h * 0.005, rows[0]


def _ease(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _sample(profile, y):
    return np.interp(y, np.arange(len(profile)) + 0.5, profile)


@lru_cache(maxsize=14)
def _leg_profiles(alpha_bytes, size):
    """Track the two opaque legs below the standing dress, then the isolated shoes."""
    w, h = size
    begins = round(h * 0.681)
    mask = np.frombuffer(alpha_bytes, np.uint8).reshape(h - begins, w) > 28
    edges = np.diff(np.pad(mask.view(np.int8), ((0,0),(1,1))), axis=1)
    ys, starts = np.where(edges == 1)
    _, ends = np.where(edges == -1)
    limits = np.r_[0, np.cumsum(np.bincount(ys, minlength=h-begins))]
    parts = [[], []]
    last = np.array([0.44, 0.60]) * w
    for row,(low,high) in enumerate(zip(limits[:-1],limits[1:]),begins):
        groups = [[], []]
        for l,r in zip(starts[low:high],ends[low:high]):
            middle = (l+r)*.5
            which = int(abs(last[1]-middle) < abs(last[0]-middle))
            groups[which].append((l, r))
        for i, g in enumerate(groups):
            if g:
                l, r = g[0][0], g[-1][1]
                last[i] = (l + r) * 0.5
                parts[i].append((row + 0.5, last[i], (r - l) * 0.5 + 0.65))
    out = []
    y = np.arange(h) + 0.5
    for p in parts:
        if not p:
            return None
        rows, centers, radii = np.array(p).T
        out.append((np.interp(y, rows, centers), np.interp(y, rows, radii), rows[-1] + 0.5))
    return out


def _foot_profiles(image):
    top = round(image.height * .681)
    alpha = image.getchannel("A").crop((0, top, image.width, image.height))
    return _leg_profiles(alpha.tobytes(), image.size)


def _legs(front, back, u, w):
    """The support leg at front-left becomes back-right. The other foot steps around it."""
    h = front.height
    fg = _foot_profiles(front)
    bg = _foot_profiles(back)
    if fg is None or bg is None:
        return None
    top = round(h * 0.655)
    result = Image.new("RGBA", (w, h - top))
    t = _ease(u)
    arc = np.sin(np.pi * t)
    y = np.arange(top, h) + 0.5
    # The front view's originally raised foot passes behind the weight-bearing foot.
    for i in (1, 0):
        cf, rf, ef = fg[i]
        cb, rb, eb = bg[1 - i]
        floor = (1 - t) * ef + t * eb - (h * 0.015 * arc if i == 1 else 0)
        yf = top + (y - top) * (ef - top) / max(1, floor - top)
        yb = top + (y - top) * (eb - top) / max(1, floor - top)
        fx, bx = _sample(cf, yf), _sample(cb, yb)
        fw, bw = _sample(rf, yf), _sample(rb, yb)
        depth = np.clip((y / h - 0.655) / 0.28, 0.0, 1.0)
        center = (fx + round((w - front.width) / 2)) * (1 - t) + (
            bx + round((w - back.width) / 2)
        ) * t
        center += arc * h * (0.056 if i == 1 else -0.008) * (0.2 + 0.8 * depth)
        radius = np.maximum(1.0, (fw * (1 - t) + bw * t) * (1.0 - 0.08 * arc))
        lo = max(0, int(np.floor((center - radius).min())) - 2)
        hi = min(w, int(np.ceil((center + radius).max())) + 2)
        length = hi - lo
        xs = np.arange(0, length + 1, dtype=np.int32)
        ys = np.unique(np.r_[np.arange(0, h - top, 2), h - top]).astype(np.int32)
        j = np.minimum(ys, len(y) - 1)
        unit = (lo + xs[None, :] - center[j, None]) / radius[j, None]

        def part(image, xx, ww, yy):
            sample_y = yy[j, None] - 0.5 + (ys[:, None] >= len(y))
            coords = np.stack(
                np.broadcast_arrays(xx[j, None] + ww[j, None] * unit, sample_y), axis=-1
            )
            return raster_grid(image, xs, ys, coords).convert("RGBa")

        first = part(front, fx, fw, yf)
        second = part(back, bx, bw, yb)
        leg = Image.blend(first, second, t).convert("RGBA")
        x = lo + np.arange(length) + 0.5
        edge = np.clip(radius[:, None] + 0.35 - np.abs(x - center[:, None]), 0.0, 1.0)
        edge *= np.clip(floor + 0.25 - y, 0.0, 1.0)[:, None]
        mask = Image.fromarray(np.rint(edge * 255).astype(np.uint8))
        leg.putalpha(ImageChops.multiply(leg.getchannel("A"), mask))
        result.alpha_composite(leg, (lo, 0))
    return result, top


def _geometry(front, back, progress):
    h = front.height
    w = max(front.width, back.width) + 4
    fc, fr, ftop = _profile(front.getchannel("A").tobytes(), front.size)
    bc, br, btop = _profile(back.getchannel("A").tobytes(), back.size)
    y = np.arange(h, dtype=np.float64) + 0.5
    t = _ease(progress)
    fmarks, bmarks = _FRONT_Y[1:] * h, _BACK_Y[1:] * h
    fmarks[0], bmarks[0] = ftop, btop
    target = (1 - t) * fmarks + t * bmarks
    # Preserve the empty margin above the first strand. Stretching the rear
    # flyaway into the larger front margin creates a detached second hair tip.
    yf = np.where(y < target[0], ftop + y - target[0], np.interp(y, target, fmarks))
    yb = np.where(y < target[0], btop + y - target[0], np.interp(y, target, bmarks))
    # Head leads the shoulders and the weight settles through both feet last.
    angle = np.pi * _ease(progress + 0.34 * progress * (1 - progress) * (0.52 - y / h))
    ca, sa = np.cos(angle), np.sin(angle)
    mix = (1 - ca) * 0.5
    fx, fw = _sample(fc, yf), _sample(fr, yf)
    bx, bw = _sample(bc, yb), _sample(br, yb)
    center = (fx + round((w - front.width) / 2)) * (1 - mix) + (
        bx + round((w - back.width) / 2)
    ) * mix
    radius = fw * (1 - mix) + bw * mix
    depth = radius * (0.58 + 0.23 * np.exp(-(((y / h - 0.20) / 0.13) ** 2)))
    ax, az = radius * ca, depth * sa
    span = np.maximum(0.5, np.hypot(ax, az))
    return w, yf, yb, fx, fw, bx, bw, center, span, ax / span, sa


def turn_pose(start, previous, end, pose, progress):
    """Return an RGBA turn frame and its offset from the standing floor origin."""
    if {previous, pose} != {"p1", "p4"} or start.height != end.height:
        raise ValueError("Turn through the standing pose at a single display scale")
    if progress <= 0:
        return start, (-start.width / 2, -start.height)
    if progress >= 1:
        return end, (-end.width / 2, -end.height)
    front, back = (start, end) if previous == "p1" else (end, start)
    u = round(progress if previous == "p1" else 1.0 - progress, 12)
    h = front.height
    w, yf, yb, fx, fw, bx, bw, center, span, c, sa = _geometry(front, back, u)
    lower = _legs(front, back, u, w)
    upper_height = min(h, int(np.ceil(h * 0.682))) if lower is not None else h
    rows = slice(0, upper_height)
    pad = h * 0.026
    left = max(0, int(np.floor((center - span)[rows].min() - pad)))
    right = min(w, int(np.ceil((center + span)[rows].max() + pad)))
    width = right - left
    xs = np.unique(np.linspace(0, width, min(86, width + 1)).round().astype(np.int32))
    ys = np.unique(
        np.linspace(0, upper_height, min(104, upper_height + 1)).round().astype(np.int32)
    )
    y = np.clip(ys, 0, h - 1)
    nx = (left + xs[None, :] - center[y, None]) / span[y, None]
    bulge = np.maximum(0.0, 1.0 - nx * nx) * sa[y, None]
    sf = nx - 0.46 * bulge
    sb = nx + 0.43 * bulge
    xf = fx[y, None] + fw[y, None] * sf
    xb = bx[y, None] + bw[y, None] * sb
    f = raster_grid(
        front, xs, ys, np.stack(np.broadcast_arrays(xf, yf[y, None] - 0.5), axis=-1)
    ).convert("RGBa")
    b = raster_grid(
        back, xs, ys, np.stack(np.broadcast_arrays(xb, yb[y, None] - 0.5), axis=-1)
    ).convert("RGBa")
    x = left + np.arange(width, dtype=np.float32)[None, :] + 0.5
    # For the visible ellipse the front/back seam lies exactly at center-span*c.
    # The source x coordinates extrapolate monotonically into transparent margins.
    seam = (center - span * c)[rows].astype(np.float32)[:, None]
    feather = (0.65 + 0.004 * h * sa[rows]).astype(np.float32)[:, None]
    mix = 1.0 - _ease((x - seam + feather) / (2.0 * feather))
    if u < 0.19:
        mix *= float(_ease(u / 0.19))
    elif u > 0.81:
        mix = 1.0 - (1.0 - mix) * float(_ease((1.0 - u) / 0.19))
    mask = Image.fromarray(np.rint(255 * mix).astype(np.uint8))
    upper = Image.composite(b, f, mask).convert("RGBA")
    # The two drawings leave different spaces between a sleeve and the loose hair.
    # At the side view, allow the underlying side of the upper body to show through
    # a gap in the texture in front of it. Keep the outside hair strands transparent.
    core = _ease(
        (
            0.76
            - np.abs(x - center[rows, None].astype(np.float32))
            / span[rows, None].astype(np.float32)
        )
        / 0.26
    )
    core *= np.sin(np.pi * u) ** 2
    core *= _ease((np.arange(upper_height, dtype=np.float32) / h - 0.20) / 0.06)[:, None]
    hidden = Image.composite(f, b, mask).convert("RGBA")
    hidden.putalpha(
        ImageChops.multiply(
            hidden.getchannel("A"), Image.fromarray(np.rint(core * 255).astype(np.uint8))
        )
    )
    hidden.alpha_composite(upper)
    result = Image.new("RGBA", (w, h))
    result.paste(hidden, (left, 0))
    if lower is not None:
        legs, top = lower
        blend = _ease((np.arange(top, h) + 0.5 - h * 0.666) / (h * 0.012))
        leg_mask = Image.fromarray(
            np.broadcast_to(np.rint(255 * blend).astype(np.uint8)[:, None], legs.size[::-1]).copy()
        )
        result.paste(legs, (0, top), leg_mask)
    t = float(_ease(u))
    left_f = -front.width / 2 - round((w - front.width) / 2)
    left_b = -back.width / 2 - round((w - back.width) / 2)
    return result, (left_f + (left_b - left_f) * t, -h)
