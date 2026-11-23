"""Timing for the desktop character's nonverbal activities, in seconds."""
import random


# These gestures are desktop interpretations. Spoken lines are selected separately
# from the game's recordings. A quiet activity can finish without a subtitle.
HOP_AND_LAND = (("hop", .44, .48), ("land", .64, .74))


def short_hop():
    """The two cues for the same jump shown by the beach routine and preview menu."""
    return [(name, random.uniform(low, high)) for name, low, high in HOP_AND_LAND]


ROUTINES = {
    "morning": (("wake", 2, 3), ("stretch", 3, 5), ("step", 2, 3.5), ("bow", 1.5, 2.5)),
    "class": (("read", 7, 12), ("turn_page", 1.8, 3), ("read", 5, 10), ("nod", 1.6, 2.5)),
    "science": (("inspect", 2, 4), ("read", 6, 10), ("count", 3, 6), ("think", 4, 7)),
    "testing": (("inspect", 2, 4), ("think", 4, 7), ("count", 3, 5), ("proud", 3, 5)),
    "retry": (("peek", 1.5, 2.4), ("think", 3, 5), ("beckon", 2, 3), ("wait", 6, 10)),
    "passed": (("surprise", 1.5, 2.2), ("cheer", 2.4, 4), ("proud", 3, 5)),
    "notes": (("look_down", 1.5, 2), ("write", 7.6, 8.2), ("think", 1.6, 2.4), ("settle", 1.3, 1.8)),
    "cook": (("knead", 3.5, 6), ("stir", 4, 8), ("taste", 2.5, 4), ("nod", 2, 3)),
    "crab": (("look_around", 3, 5), ("tiptoe", 2.5, 4), ("inspect", 3, 5),
             *HOP_AND_LAND, ("look_down", 2.3, 3.5)),
    "hum": (("listen", 3, 5), ("sway", 3, 6), ("hum", 6, 10), ("settle", 2, 4)),
    "watch": (("step", 2.2, 3.5), ("watch", 7, 13), ("listen", 4, 6), ("settle", 4, 8)),
    "lamps": (("turn", 2, 3.5), ("watch", 5, 10), ("think", 4, 7)),
    "evening_log": (("turn_page", 4.6, 5), ("write", 7.6, 8.2), ("look_down", 1.5, 2), ("settle", 1.3, 1.8)),
    "rest": (("step", 2, 3.6), ("yawn", 2.5, 4), ("doze", 8, 12), ("rest", 12, 20)),
}


def motions_for(activity, *, reacting, demo):
    steps = [(gesture, random.uniform(low, high)) for gesture, low, high in ROUTINES[activity]]
    if activity=="class" and reacting:
        steps=[("inspect" if gesture=="turn_page" else gesture,seconds) for gesture,seconds in steps]
    if reacting and steps[0][1] > 3.5:
        steps.insert(0, ("peek", random.uniform(1.6, 2.4)))
    if demo:
        # Preserve the original writing sequence and the timing shared by the
        # ascent, weight transfer and landing. A preview must not hold her in midair.
        steps = [(gesture, duration if (activity in ("notes","evening_log") and gesture in ("write","turn_page"))
                  or (activity=="crab" and gesture in ("hop","land"))
                  else min(5.0, max(1.35, duration * 0.66))) for gesture, duration in steps]
    return steps


def quote_key(clip):
    return "".join(ch for ch in clip["zh"].casefold() if ch.isalnum())
