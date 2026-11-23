"""Register the original d1 supporting finger around the bottom of the binding."""
import math
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation, label


HERE = Path(__file__).resolve().parent
HELD = HERE.parents[1]
SIZE = (154, 123)


def rgba(path):
    with Image.open(path) as im:
        return im.convert("RGBA")


def front():
    source = rgba(HELD / "grip-back.png")
    pixels = np.array(source)
    y, x = np.indices((SIZE[1], SIZE[0]))
    # The lowest finger is separated from the nearer fingers by an ink contour.
    # The broad end enters the rear palm and lies under the bottom of the binding.
    upper = np.interp(x, [25, 40, 56, 73, 84, 90], [84, 78, 75, 72, 71, 70])
    alpha = pixels[:,:,3] * np.clip(y - upper, 0, 1) * (x >= 25)
    labels, _ = label(alpha > 3)
    biggest = np.bincount(labels.ravel())[1:].argmax() + 1
    alpha *= binary_dilation(labels == biggest)
    source.putalpha(Image.fromarray(np.rint(alpha).astype("uint8")))
    degrees, scale = 137, .73
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    inverse = np.array(((c, s), (-s, c))) / scale
    offset = np.array((77, 81)) - inverse @ np.array((35, 90))
    affine = (*inverse[0], offset[0], *inverse[1], offset[1])
    return source.convert("RGBa").transform(SIZE, Image.Transform.AFFINE, affine,
                                          Image.Resampling.BICUBIC).convert("RGBA")


def main():
    back = Image.new("RGBA", SIZE)
    back.alpha_composite(rgba(HELD / "writing-posture/arms/support-contact-back.png"), (1, 55))
    back.alpha_composite(rgba(HELD / "grip-back.png"))
    back.save(HERE / "support-back.png", optimize=True)
    front().save(HERE / "support-front.png", optimize=True)


if __name__ == "__main__":
    main()
