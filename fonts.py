"""Font discovery and text fitting. Pure Pillow, no CadQuery import, so the
Set store, the preview endpoint and the tests stay fast."""
import glob
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import ImageFont

# A sign is flagged "shrunk" when its own best fit is smaller than the frozen Set size.
SHRUNK_EPSILON = 1e-6
# Distance between baselines of a multi-line sign, as a multiple of the font size.
LINE_PITCH = 1.2


def find_system_fonts() -> List[dict]:
    if os.name == "nt":
        search_paths = [r"C:\Windows\Fonts"]
    else:
        search_paths = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"),
            os.path.expanduser("~/.local/share/fonts"),
            "/Library/Fonts",
            "/System/Library/Fonts",
            os.path.expanduser("~/Library/Fonts"),
        ]

    fonts = []
    seen_paths = set()

    for base_path in search_paths:
        if not os.path.exists(base_path):
            continue
        for ext in ("*.ttf", "*.TTF", "*.otf", "*.OTF"):
            pattern = os.path.join(base_path, "**", ext)
            for path in glob.glob(pattern, recursive=True):
                norm = os.path.normpath(path)
                if norm in seen_paths:
                    continue
                seen_paths.add(norm)
                fonts.append({
                    "name": Path(path).stem,
                    "path": norm.replace("\\", "/"),  # forward slashes for JS safety
                })

    fonts.sort(key=lambda x: x["name"].lower())
    return fonts


def find_arial_path() -> str:
    """Path of the Arial font if found, otherwise the first system font."""
    fonts = find_system_fonts()
    for f in fonts:
        if "arial" in f["name"].lower():
            return f["path"]
    return fonts[0]["path"] if fonts else ""


@lru_cache(maxsize=32)
def _pil_font(font_path: str, size: int):
    return ImageFont.truetype(font_path, size=size)


def _text_dims(text: str, font, ref_size: float, pilot_size: int = 100) -> Tuple[float, float]:
    """Width and height of a (possibly multi-line) text at `ref_size`."""
    # getbbox returns (left, top, right, bottom); it knows nothing about line breaks
    lines = [l.strip() for l in text.strip().split("\n")]
    boxes = [font.getbbox(l) for l in lines if l]
    raw_w = max(b[2] - b[0] for b in boxes)
    raw_h = max(b[3] - b[1] for b in boxes) + (len(lines) - 1) * LINE_PITCH * pilot_size
    return raw_w * (ref_size / pilot_size), raw_h * (ref_size / pilot_size)


def text_anchor(text: str, size: float, font_path: str, fit, align: str) -> Optional[float]:
    """x (plate-centred mm) of the edge a text is aligned to, from outlines.Fit.anchor.
    The ONE place for it: the preview and the export both call this.
    None when there is no fit (callers then use the plain rectangle rule)."""
    if fit is None or not text.strip() or not size:
        return None
    if align not in ("left", "right"):
        return 0.0
    w, h = _text_dims(text, _pil_font(font_path, 100), size)
    return fit.anchor(w, h, align)


def calculate_optimal_font_size(
    texts: List[str],
    font_path: str,
    available_width: float,
    available_height: float,
    ref_size: float = 20.0,
    fit=None,
) -> Tuple[float, str]:
    """
    Calculates font size and returns which text was the width-limiting factor.
    If the height is the limiting factor it affects all signs equally, so a
    'limiting_text' is only reported when it is limited by width (too long).
    """
    max_scale = float("inf")
    limiting_text = ""

    try:
        # A large pixel size gives good precision
        pilot_size = 100
        font = _pil_font(font_path, pilot_size)
    except Exception as e:
        print(f"DEBUG: Pillow font load failed: {e}")
        return _calculate_legacy(texts, font_path, available_width, available_height, ref_size)

    for text in texts:
        text_strip = text.strip()
        if not text_strip:
            continue

        text_w, text_h = _text_dims(text_strip, font, ref_size, pilot_size)
        if text_w <= 0 or text_h <= 0:
            continue

        scale_w = available_width / text_w
        scale_h = available_height / text_h
        # Small safety factor (0.975) to account for tessellation variations
        best = min(scale_w, scale_h)
        if fit is not None and not fit.is_rect:
            # A non-rectangular contour: the largest block that keeps its distance from it.
            # Never larger than the plain rectangle allows.
            best = min(best, fit.max_scale(text_w, text_h))
        local_scale = best * 0.975

        if local_scale < max_scale:
            max_scale = local_scale
            # Only report a bottleneck if width constrained this sign
            # AND this sign is the new global minimum.
            limiting_text = text_strip if scale_w < scale_h else ""

    if max_scale == float("inf"):
        return ref_size, ""

    return round(max_scale * ref_size, 3), limiting_text


def _calculate_legacy(texts, font_path, available_width, available_height, ref_size):
    """Fallback using CadQuery for metrics (SLOW)"""
    import cadquery as cq

    max_scale = float("inf")
    limiting_text = ""
    for text in texts:
        text_strip = text.strip()
        if not text_strip:
            continue
        sample = cq.Workplane("XY").text(text_strip, fontsize=ref_size, distance=0.1, fontPath=font_path)
        bb = sample.val().BoundingBox()
        text_w, text_h = bb.xmax - bb.xmin, bb.ymax - bb.ymin
        sw = available_width / text_w
        sh = available_height / text_h
        scale = min(sw, sh)
        if scale < max_scale:
            max_scale = scale
            limiting_text = text_strip
    return round(max_scale * ref_size, 3), limiting_text


def plan_sizes(
    texts: List[str],
    font_path: str,
    available_width: float,
    available_height: float,
    frozen: Optional[float] = None,
    fit=None,
    align: str = "center",
) -> dict:
    """The ONE sizing function used by preview and by generation.

    While `frozen` is None the Set size is the optimum across ALL texts.
    Once frozen, every sign renders at min(frozen, its own best fit) and is
    flagged `shrunk` when its own best fit is smaller than the frozen size.

    Returns {set_size, optimum, limiting_text, sizes, shrunk}; sizes/shrunk are
    parallel to `texts` (blank texts get the Set size and are never shrunk).
    set_size / optimum are None when there is nothing to measure and nothing frozen.

    `fit` (outlines.Fit) makes the text keep its distance from a non-rectangular contour;
    with `fit` the result also holds `anchors`, the x of the aligned text edge per sign
    (see text_anchor). Without `fit` (or for a rectangle) the sizes are the plain ones.
    """
    stripped = [t.strip() for t in texts]
    measurable = [t for t in stripped if t]

    optimum, limiting = (None, "")
    if measurable:
        optimum, limiting = calculate_optimal_font_size(
            measurable, font_path, available_width, available_height, fit=fit
        )

    set_size = frozen if frozen else optimum

    own_cache = {}
    sizes, shrunk = [], []
    for t in stripped:
        if not t or set_size is None:
            sizes.append(set_size)
            shrunk.append(False)
            continue
        if t not in own_cache:
            own_cache[t] = calculate_optimal_font_size([t], font_path, available_width, available_height, fit=fit)[0]
        own = own_cache[t]
        sizes.append(min(set_size, own))
        shrunk.append(bool(frozen) and own < frozen - SHRUNK_EPSILON)

    return {
        "set_size": set_size,
        "optimum": optimum,
        "limiting_text": limiting,
        "sizes": sizes,
        "shrunk": shrunk,
        "anchors": [text_anchor(t, sz, font_path, fit, align) if sz else None
                    for t, sz in zip(stripped, sizes)],
    }
