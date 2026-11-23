"""Rebuild the surviving strokes on the original rigid back page during cover closure."""

import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.optimize import least_squares
from skimage.feature import SIFT, match_descriptors
from skimage.measure import ransac
from skimage.transform import AffineTransform


OUT = Path(__file__).resolve().parent
BOOK = OUT.parent.parent / "book"
PEN = OUT.parent.parent / "pen-write"
ANGLES = (180, 169, 153, 132, 111, 97, 83, 70, 52, 32, 14, 0)
CELL = (80, 60)


def features(image):
    sift = SIFT()
    sift.detect_and_extract(np.asarray(image.convert("L")))
    return sift.keypoints[:, ::-1], sift.descriptors


def difference(filled, sparse):
    return np.asarray(filled.convert("L"), dtype=float) - np.asarray(sparse.convert("L"), dtype=float)


def register(source, target):
    ref_points, ref_desc, src_diff = source
    kpt, desc = features(target["filled"])
    pairs = match_descriptors(ref_desc, desc, max_ratio=.82)
    p, q = ref_points[pairs[:, 0]], kpt[pairs[:, 1]]
    start, keep = ransac((p, q), AffineTransform, min_samples=3,
                         residual_threshold=1.6, max_trials=2000, rng=42)
    assert keep.sum() >= 8
    # Refine against the additional lines of the original filled page. Common
    # art (including the moving cover) cancels in this difference image.
    target_diff = difference(target["filled"], target["sparse"])
    visible = np.asarray(target["blank"])[:, :, 0] > 155
    y, x = np.nonzero((distance_transform_edt(visible) > 3) & (abs(target_diff) >= 6))
    src = start.inverse(np.column_stack((x, y)))
    use = (src[:, 0] > 196) & (src[:, 0] < 320) & (src[:, 1] > 108) & (src[:, 1] < 252)
    x, y = x[use], y[use]
    src = src[use]
    assert len(x) >= 24
    basis = np.array([(x - 255) / 64., (y - 174) / 64., np.ones_like(x)])
    expected = target_diff[y, x]

    def residual(coeff):
        query = src + (coeff.reshape(2, 3) @ basis).T
        return map_coordinates(src_diff, query.T[::-1], order=1) - expected

    adjusted = least_squares(residual, np.zeros(6), loss="soft_l1", f_scale=4.)
    offset = adjusted.x.reshape(2, 3)
    shift = np.array([[1./64, 0., -255./64], [0., 1./64, -174./64], [0., 0., 1.]])
    m = np.linalg.inv(start.params)
    m[:2] += offset @ shift
    fwd = np.linalg.inv(m)
    errors = np.linalg.norm(q[keep] - AffineTransform(matrix=fwd)(p[keep]), axis=1)
    assert np.median(errors) < 1.1
    return fwd, round(float(np.median(errors)), 3)


def build():
    book = json.loads((BOOK / "book.json").read_text("utf-8"))
    ink = json.loads((PEN / "write.json").read_text("utf-8"))["ink"]
    atlas = Image.open(PEN / ink["atlas"]).convert("RGBA")
    cols, count = ink["columns"], ink["tile_count"]
    w, h = ink["tile_size"]
    original_tiles = []
    for k in range(count):
        x, y = k % cols * w, k // cols * h
        original_tiles.append(atlas.crop((x, y, x + w, y + h)))
    left, top = ink["book_rect"][:2]
    rows = []
    output = Image.new("RGBA", (count * CELL[0], 8 * CELL[1]))
    for i, angle in enumerate(ANGLES):
        names = {k: book["closing_frames_by_state"]["open_" + k][i] for k in ("blank", "sparse", "filled")}
        images = {k: Image.open(BOOK / fn).convert("RGBA") for k, fn in names.items()}
        row = {"closingIndex": i, "openingIndex": 11-i, "angleDegrees": angle,
               "bookFiles": names, "atlasRow": None}
        rows.append(row)
        if angle < 52:
            continue
        if i == 0:
            points, descriptors = features(images["filled"])
            use = ((points[:, 0] > 195) & (points[:, 0] < 335) &
                   (points[:, 1] >= 78) & (points[:, 1] < 275))
            source = (points[use], descriptors[use], difference(images["filled"], images["sparse"]))
            fwd, error = np.eye(3), 0.
        else:
            fwd, error = register(source, images)
        row["originalTextMedianRegistrationErrorPx"] = error
        row["openToBookAffine"] = fwd[:2].round(7).tolist()
        visible = np.asarray(images["blank"])[:, :, 0].astype(float)
        # In this patch the back page is warm light paper, and the rotating
        # leather is dark. Only 70 and 52 degrees enter the stroke region.
        # The transition at their true sampled leather edge is antialiased.
        matte = np.clip((visible - 47.) / (215. - 47.), 0., 1.)
        inv = np.linalg.inv(fwd)
        inv[0, 2] -= left
        inv[1, 2] -= top
        patches = []
        for tile in original_tiles:
            alpha = tile.getchannel("A").transform(images["blank"].size, Image.Transform.AFFINE,
                                                   tuple(inv[:2].ravel()), Image.Resampling.BICUBIC)
            if angle <= 70:
                a = np.asarray(alpha).astype(float)
                # Inside the cover the leather has its own texture. Only
                # the one-pixel edge beside the exposed paper can blend.
                exposed = visible >= 150
                m = np.where(distance_transform_edt(~exposed) > 1.43, 0.,
                             np.where(distance_transform_edt(exposed) > 1.43, 1., matte))
                alpha = Image.fromarray(np.uint8(np.rint(a * m)))
            patches.append(alpha)
        bbox = patches[-1].getbbox()
        if angle == 52:
            assert bbox is None, f"Cover should hide all ink at 52 degrees, got {bbox}"
            continue
        assert bbox and bbox[2] - bbox[0] <= CELL[0] and bbox[3] - bbox[1] <= CELL[1]
        x0, y0, x1, y1 = bbox
        row.update(atlasRow=i, bookRect=[x0, y0, x1, y1])
        for k, patch in enumerate(patches):
            plane = Image.new("RGBA", (x1-x0, y1-y0), (55, 45, 41, 0))
            plane.putalpha(patch.crop(bbox))
            output.paste(plane, (k*CELL[0], i*CELL[1]))
            if i == 0:
                placed = Image.new("L", (432, 348))
                placed.paste(plane.getchannel("A"), (x0, y0))
                orig = Image.new("L", (432, 348))
                orig.paste(original_tiles[k].getchannel("A"), (left, top))
                assert np.array_equal(np.asarray(placed), np.asarray(orig))
    output.save(OUT / "ink.png", optimize=True)
    manifest = {
        "schemaVersion": 1, "canvasPx": book["canvas_px"], "pivotPx": book["pivot_px"],
        "atlas": "ink.png", "tileCount": count, "cellSize": list(CELL), "frames": rows,
        "bookManifest": "../../book/book.json", "sourceInk": "../../pen-write/write.json",
        "drawing": "Freeze ink_tile (0..20) and the source page. For closingIndex j select the underlying blank/sparse/filled frame from bookManifest. If atlasRow is an integer, copy [ink_tile*80, atlasRow*60, width, height] to bookRect. Rect is [left,top,right,bottom), width=right-left, height=bottom-top. Draw onto the 432x348 book before the front supporting fingers and before any pocket mask. Null atlasRow means fully occluded. Ink tile 0 is empty. All PNG paths relative to their own manifest.",
        "registration": "Original right-page writing in the filled vs sparse book tiles provides common landmarks, fitted by a subpixel affine. The exposed right page and the leather occluder are sampled from the original blank book tile. The angle labels are the earlier desk artwork's authored values. At 180 degrees ink alpha is identical to the pen-write atlas for every tile.",
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    build()
