"""Book and grip registration for the unpadded white d1/p1 writing artwork.

Coordinates returned by geometry() are native actor pixels. Its QTransform maps
them to logical widget pixels. install() is an adapter for the offscreen review.
"""
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QTransform


def writing_matrix(performance, rect, body, breath, attached_point, baseline):
    """Use exactly the two pins used by the current performance painter."""
    w, h = rect.width(), rect.height()
    canvas = np.asarray(performance.canvas)
    spine = np.asarray(performance.spine)
    shoulder = np.array((453., 563.))

    def at(point):
        return np.asarray(attached_point(np.asarray(point)/canvas, w, h, "p1", body, breath))

    pivot = at(spine)
    along = (at(spine + (100, 0))-pivot)/100
    along = baseline(along, h/canvas[1], body[4])
    down = (at(shoulder)-pivot-(shoulder[0]-spine[0])*along)/(shoulder[1]-spine[1])
    origin = np.array((rect.x(), rect.y())) + pivot - spine[0]*along - spine[1]*down
    return QTransform(*along, *down, *origin)


def geometry(props, size, actor_rect, body, breath, passive_transform, layers):
    """Keep the existing closed pose and arrive on the exact written-page basis.

    Both finger planes and the book share this map. The parent can paint their
    contents in the prescribed depth order. At fully open no angle switch is
    needed when the performance painter takes or returns ownership.
    """
    from desktop_motion import attached_point
    perf = props.performance
    u = max(0., min(1., props.opening))
    if u == 0:
        return passive_transform, layers
    weight = u*u*(3.-2.*u)
    spine = np.asarray(perf.spine)
    scale = actor_rect.height()/perf.canvas[1]
    if hasattr(props, "book_plane"):
        _, along, down, origin = props.book_plane(actor_rect, body, breath)
        active = QTransform(*along,*down,actor_rect.x()+origin[0],actor_rect.y()+origin[1])
    else:
        active = writing_matrix(perf, actor_rect, body, breath, attached_point, props.writing_baseline)
    sx, sy = spine
    old_x = np.array((passive_transform.m11(), passive_transform.m12()))*scale
    old_y = np.array((passive_transform.m21(), passive_transform.m22()))*scale
    new_x = np.array((active.m11(), active.m12()))
    new_y = np.array((active.m21(), active.m22()))
    target = np.array(active.map(float(sx), float(sy)))
    previous = np.array((passive_transform.dx(), passive_transform.dy()))
    along = old_x + weight*(new_x-old_x)
    down = old_y + weight*(new_y-old_y)
    pivot = previous + weight*(target-previous)
    matrix = QTransform(*along, *down, *(pivot-sx*along-sy*down))

    # Reverse the earlier small-camera rounding at the same rate as the plane
    # changes. Full opening then matches the canonical 432x348 book and both
    # native grip crops, including any ink already present on the page.
    hand = props.held["hand_rect_actor_px"]
    hand_rect = np.array((hand[0], hand[1], hand[2]-hand[0], hand[3]-hand[1]))
    book_origin = np.asarray(perf.book_origin, dtype=float)
    book_size = np.asarray(props.books["canvas_px"])*perf.book_scale
    tuck = 1.-props.book_presence**2*(3.-2.*props.book_presence)
    book_origin = book_origin + tuck*perf.canvas[1]*np.array((.105, -.043))
    target_rects = (hand_rect, np.r_[book_origin,book_size], hand_rect)
    mapped = []
    for (prior, image), desired in zip(layers, target_rects):
        old = np.array((sx+prior.x()/scale, sy+prior.y()/scale,
                        prior.width()/scale, prior.height()/scale))
        mapped.append((QRectF(*(old + weight*(desired-old))), image))
    return matrix, tuple(mapped)


def install(props):
    """Adapt a preview instance's held-book geometry, retaining its normal painter."""
    original = props.grip_geometry

    def registered(held, size, rect, body, breath):
        transform, layers = original(held, size, rect, body, breath)
        gesture = (props.last_action or ("",))[0]
        writing = (props.clip == "write" or gesture == "write" or
                   props.activity in ("notes", "preview:write") or
                   props.interrupt_capture is not None)
        if writing and abs(props.hand_angle) < 1.e-6:
            return geometry(props, size, rect, body, breath, transform, layers)
        return transform, layers

    props.grip_geometry = registered
