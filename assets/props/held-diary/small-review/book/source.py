"""Read the support layers or render an updated transparent writing/lift plate."""
from functools import lru_cache
import importlib.util
from pathlib import Path

from PIL import Image


HERE = Path(__file__).resolve().parent
HELD = HERE.parents[1]
BOUNDS = (-110, 478, 675, 1130)


@lru_cache(maxsize=2)
def support(side):
    if side not in ("front", "back"):
        raise ValueError("Expected front or back")
    with Image.open(HERE / ("support-" + side + ".png")) as image:
        return image.convert("RGBA")


class SupportArt:
    """Use the new underside and near digit once while preserving all moving parts."""
    def __init__(self, source):
        self.source = source
        self.replace = {HELD / "grip-back.png": support("back"),
                        HELD / "grip-front.png": support("front")}
        # The underside retouch is already registered into our rear plate.
        # Chest's old foreground touch-up is replaced by the new near finger.
        for name in ("support-contact-back.png", "support-contact-front.png"):
            path = HELD / "writing-posture/arms" / name
            self.replace[path] = Image.new("RGBA", source.art(path).size)

    def __getattr__(self, name):
        return getattr(self.source, name)

    def art(self, path):
        if path in self.replace:
            return self.replace[path]
        return self.source.art(path)

    def support_front(self, weight):
        return support("front")


@lru_cache(maxsize=1)
def renderer():
    path = HELD / "write-interrupt/render/source.py"
    spec = importlib.util.spec_from_file_location("atri_small_d1_diary_layer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = module.Renderer()
    instance.arms = SupportArt(instance.arms)
    return instance


def local_layer(posture, stage, index, page="blank", fixed_ink=None, height=420, start=None):
    """RGBA before the book/actor transform. height is the actor's physical height."""
    return renderer().local_layer(posture, stage, index, page, fixed_ink, height, start)
