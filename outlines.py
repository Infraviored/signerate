"""Outer contours of a sign. The ONLY place that defines shape geometry.

Pure Python, no CadQuery. For a plate of W x H mm and a shape id it provides
  - the catalog (ids, names, parameters),
  - resolved parameters (automatic values, ranges, validation),
  - a closed polygon in plate-centred mm (x right, y up), sampled to a chord
    error of about 0.02 mm, for the export and for text fitting,
  - an SVG path (x right, y down, origin top left, 0..W x 0..H) for the preview,
  - `Fit`, which answers "how big may a text block be, and where may it sit,
    so that it keeps its distance from the real contour".

Every automatic value derives from W and H and is valid for that plate.
Dimension-dependent ranges are resolved in dependency order (see the `_params_*`
functions), so a value that is valid for the plate stays valid together with the others.
"""
import math
from functools import lru_cache

CHORD_TOL = 0.02  # mm, maximum distance between polygon and true curve
EPS = 1e-9


class ShapeError(ValueError):
    """A shape or parameter value that cannot be used. The message is meant for people."""


# --- Sampling ----------------------------------------------------------------

def _dist_to_chord(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    return abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) / L


def _refine(f, ta, tb, pa, pb, tol, out, depth):
    tm = (ta + tb) / 2
    pm = f(tm)
    if depth < 16 and _dist_to_chord(pm, pa, pb) > tol:
        _refine(f, ta, tm, pa, pm, tol, out, depth + 1)
        _refine(f, tm, tb, pm, pb, tol, out, depth + 1)
    else:
        out.append(pb)


def _sample(f, t0, t1, pieces, tol=CHORD_TOL):
    """Points of the curve f(t), t in [t0, t1], without the start point."""
    ts = [t0 + (t1 - t0) * i / pieces for i in range(pieces + 1)]
    ps = [f(t) for t in ts]
    out = []
    for i in range(pieces):
        _refine(f, ts[i], ts[i + 1], ps[i], ps[i + 1], tol, out, 0)
    out[-1] = ps[-1]  # the exact end
    return out


# --- Path builder (design coordinates: x right, y DOWN, 0..W x 0..H) ---------

def _r(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class _Path:
    def __init__(self, x, y):
        self.start = (x, y)
        self.cur = (x, y)
        self.cmds = []  # SVG text pieces
        self.pts = [(x, y)]

    def line(self, x, y):
        if math.hypot(x - self.cur[0], y - self.cur[1]) < EPS:
            return self
        self.cmds.append(f"L{_r(x)},{_r(y)}")
        self.pts.append((x, y))
        self.cur = (x, y)
        return self

    def cubic(self, c1, c2, p):
        p0 = self.cur

        def f(t):
            u = 1 - t
            return (u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p[0],
                    u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p[1])

        self.cmds.append(f"C{_r(c1[0])},{_r(c1[1])} {_r(c2[0])},{_r(c2[1])} {_r(p[0])},{_r(p[1])}")
        self.pts.extend(_sample(f, 0.0, 1.0, 8))
        self.cur = p
        return self

    def arc(self, rx, ry, sweep, x, y):
        """Axis-aligned elliptical arc to (x, y), the smaller one (SVG large-arc = 0)."""
        x0, y0 = self.cur
        if rx < EPS or ry < EPS or math.hypot(x - x0, y - y0) < EPS:
            return self.line(x, y)
        x1p, y1p = (x0 - x) / 2, (y0 - y) / 2
        lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
        if lam > 1:
            k = math.sqrt(lam)
            rx, ry = rx * k, ry * k
        num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
        den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
        co = math.sqrt(max(0.0, num / den)) if den > 0 else 0.0
        sign = 1.0 if sweep == 1 else -1.0  # large-arc flag is 0, SVG spec F.6.5.2
        cxp = sign * co * rx * y1p / ry
        cyp = sign * co * -ry * x1p / rx
        cx, cy = cxp + (x0 + x) / 2, cyp + (y0 + y) / 2
        th1 = math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
        th2 = math.atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx)
        d = th2 - th1
        if sweep == 0 and d > 0:
            d -= 2 * math.pi
        elif sweep == 1 and d < 0:
            d += 2 * math.pi
        self.cmds.append(f"A{_r(rx)},{_r(ry)} 0 0 {sweep} {_r(x)},{_r(y)}")
        pieces = max(1, math.ceil(abs(d) / (math.pi / 6)))
        self.pts.extend(_sample(lambda t: (cx + rx * math.cos(t), cy + ry * math.sin(t)), th1, th1 + d, pieces))
        self.pts[-1] = (x, y)
        self.cur = (x, y)
        return self

    def poly(self, points):
        """A ready-made polyline (used where no SVG primitive exists)."""
        for x, y in points:
            self.line(x, y)
        return self

    def svg(self):
        return f"M{_r(self.start[0])},{_r(self.start[1])}" + "".join(self.cmds) + "Z"


# --- Parameters ---------------------------------------------------------------

class _Params:
    """Resolves parameter values in order; each later range may depend on earlier values."""

    def __init__(self, given):
        self.given = given or {}
        self.rows = []
        self.values = {}

    def get(self, key, label, unit, auto, lo, hi):
        hi = max(hi, lo)
        auto = min(max(auto, lo), hi)
        raw = self.given.get(key)
        if raw is None:
            value = auto
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ShapeError(f"{label} must be a number.")
            value = float(raw)
            if not math.isfinite(value):
                raise ShapeError(f"{label} must be a finite number.")
            if value < lo - 1e-9 or value > hi + 1e-9:
                suffix = f" {unit}" if unit else ""
                raise ShapeError(f"{label} must be between {_r(round(lo, 2))} and {_r(round(hi, 2))}{suffix} for this plate.")
            value = min(max(value, lo), hi)
        self.values[key] = value
        self.rows.append({"key": key, "label": label, "unit": unit,
                          "value": value, "auto": auto, "min": lo, "max": hi})
        return value


# --- Shapes ---------------------------------------------------------------------
# Reference drawing: plate 300 x 120, corner radius 22. Proportions below are taken from it.

def _params_none(W, H, p):
    pass


def _params_rounded(W, H, p):
    m = min(W, H)
    p.get("radius", "Corner radius", "mm", 0.18 * m, 0, m / 2)


def _build_rounded(W, H, v):
    r = v["radius"]
    return (_Path(r, 0).line(W - r, 0).arc(r, r, 1, W, r).line(W, H - r).arc(r, r, 1, W - r, H)
            .line(r, H).arc(r, r, 1, 0, H - r).line(0, r).arc(r, r, 1, r, 0))


def _build_punched(W, H, v):
    r = v["radius"]
    return (_Path(r, 0).line(W - r, 0).arc(r, r, 0, W, r).line(W, H - r).arc(r, r, 0, W - r, H)
            .line(r, H).arc(r, r, 0, 0, H - r).line(0, r).arc(r, r, 0, r, 0))


def _params_bracket(W, H, p):
    m = min(W, H)
    s = p.get("shoulder_inset", "Shoulder inset", "mm", 0.3 * m, min(0.05 * m, 0.45 * W), 0.45 * W)
    p.get("point_depth", "Point depth", "mm", 0.183 * m, min(0.05 * m, s), s)


def _build_bracket(W, H, v):
    s, d = v["shoulder_inset"], v["point_depth"]
    sy = H / 120.0
    mid = H / 2
    k = 30 * sy                       # where the shoulder hands over to the point
    fx = (s - d) * 6 / 14 if s > d else 0.0
    # right half top to bottom, then the mirrored left half bottom to top
    p = _Path(s, 0).line(W - s, 0)
    p.cubic((W - s, 20 * sy), (W - s + fx, 24 * sy), (W - d, k))
    p.cubic((W - d + d * 8 / 22, 36 * sy), (W - d + d * 14 / 22, 46 * sy), (W, mid))
    p.cubic((W - d + d * 14 / 22, H - 46 * sy), (W - d + d * 8 / 22, H - 36 * sy), (W - d, H - k))
    p.cubic((W - s + fx, H - 24 * sy), (W - s, H - 20 * sy), (W - s, H))
    p.line(s, H)
    p.cubic((s, H - 20 * sy), (s - fx, H - 24 * sy), (d, H - k))
    p.cubic((d - d * 8 / 22, H - 36 * sy), (d - d * 14 / 22, H - 46 * sy), (0, mid))
    p.cubic((d - d * 14 / 22, 46 * sy), (d - d * 8 / 22, 36 * sy), (d, k))
    p.cubic((s - fx, 24 * sy), (s, 20 * sy), (s, 0))
    return p


def _params_ornament(W, H, p):
    m = min(W, H)
    ch = p.get("crest_height", "Crest height", "mm", 0.083 * m, 0, 0.25 * H)
    r = p.get("radius", "Corner radius", "mm", 0.15 * m, 0, min(W / 2, (H - 2 * ch) / 2))
    p.get("crest_width", "Crest width", "mm", 0.733 * m, min(0.1 * m, W - 2 * r), W - 2 * r)


def _build_ornament(W, H, v):
    ch, r, cw = v["crest_height"], v["radius"], v["crest_width"]
    c = W / 2
    hw = cw / 2
    p = _Path(r, ch).line(c - hw, ch)
    p.cubic((c - hw / 2, ch), (c - hw * 0.364, 0), (c, 0))
    p.cubic((c + hw * 0.364, 0), (c + hw / 2, ch), (c + hw, ch))
    p.line(W - r, ch).arc(r, r, 1, W, ch + r).line(W, H - ch - r).arc(r, r, 1, W - r, H - ch)
    p.line(c + hw, H - ch)
    p.cubic((c + hw / 2, H - ch), (c + hw * 0.364, H), (c, H))
    p.cubic((c - hw * 0.364, H), (c - hw / 2, H - ch), (c - hw, H - ch))
    p.line(r, H - ch).arc(r, r, 1, 0, H - ch - r).line(0, ch + r).arc(r, r, 1, r, ch)
    return p


def _params_cut(W, H, p):
    m = min(W, H)
    p.get("size", "Cut size", "mm", 0.18 * m, 0, m / 2)


def _build_cut(W, H, v):
    c = v["size"]
    return (_Path(c, 0).line(W - c, 0).line(W, c).line(W, H - c).line(W - c, H)
            .line(c, H).line(0, H - c).line(0, c))


def _params_oval(W, H, p):
    p.get("squareness", "Squareness", "", 2.0, 2.0, 10.0)


def _build_oval(W, H, v):
    n = v["squareness"]
    a, b = W / 2, H / 2
    if abs(n - 2.0) < 1e-9:
        return _Path(0, b).arc(a, b, 1, W, b).arc(a, b, 1, 0, b)
    e = 2.0 / n

    def f(t):
        c, s = math.cos(t), math.sin(t)
        return (a + a * math.copysign(abs(c) ** e, c), b - b * math.copysign(abs(s) ** e, s))

    pts = _sample(f, 0.0, 2 * math.pi, 32)  # without the start point, which is f(0)
    return _Path(*f(0.0)).poly(pts[:-1])  # the last point is the start again


def _params_pill(W, H, p):
    p.get("end_length", "End length", "mm", H / 2, min(0.1 * H, W / 2), W / 2)


def _build_pill(W, H, v):
    L = v["end_length"]
    b = H / 2
    return (_Path(L, 0).line(W - L, 0).arc(L, b, 1, W, b).arc(L, b, 1, W - L, H)
            .line(L, H).arc(L, b, 1, 0, b).arc(L, b, 1, L, 0))


def _params_ticket(W, H, p):
    m = min(W, H)
    c = p.get("radius", "Corner radius", "mm", 0.0, 0, m / 2)
    p.get("notch_radius", "Notch radius", "mm", 0.133 * m, 0, min(H / 2 - c, W / 4))


def _build_ticket(W, H, v):
    n, c = v["notch_radius"], v["radius"]
    mid = H / 2
    return (_Path(c, 0).line(W - c, 0).arc(c, c, 1, W, c).line(W, mid - n).arc(n, n, 0, W, mid + n)
            .line(W, H - c).arc(c, c, 1, W - c, H).line(c, H).arc(c, c, 1, 0, H - c)
            .line(0, mid + n).arc(n, n, 0, 0, mid - n).line(0, c).arc(c, c, 1, c, 0))


def _params_arched(W, H, p):
    p.get("arch_height", "Arch height", "mm", 0.433 * H, 0.1 * H, H)


def _build_arched(W, H, v):
    a = v["arch_height"]
    return _Path(0, H).line(0, a).arc(W / 2, a, 1, W, a).line(W, H)


def _params_shield(W, H, p):
    s = p.get("shoulder_height", "Shoulder height", "mm", 0.433 * H, 0, 0.9 * H)
    p.get("fullness", "Curve fullness", "", 0.59, 0.1, 1.0)


def _build_shield(W, H, v):
    s, f = v["shoulder_height"], v["fullness"]
    y1 = s + f * (H - s)
    y2 = H - 0.176 * (H - s)
    return (_Path(0, 0).line(W, 0).line(W, s).cubic((W, y1), (0.68 * W, y2), (W / 2, H))
            .cubic((0.32 * W, y2), (0, y1), (0, s)))


def _params_ribbon(W, H, p):
    p.get("notch_depth", "Notch depth", "mm", 0.283 * min(W, H), 0, 0.45 * W)


def _build_ribbon(W, H, v):
    n = v["notch_depth"]
    return _Path(0, 0).line(W, 0).line(W - n, H / 2).line(W, H).line(0, H).line(n, H / 2)


def _params_hexagon(W, H, p):
    p.get("point_depth", "Point depth", "mm", 0.283 * min(W, H), 0, 0.45 * W)


def _build_hexagon(W, H, v):
    d = v["point_depth"]
    return (_Path(d, 0).line(W - d, 0).line(W, H / 2).line(W - d, H).line(d, H).line(0, H / 2))


def _build_rect(W, H, v):
    return _Path(0, 0).line(W, 0).line(W, H).line(0, H)


SHAPES = {
    "rect": ("Rectangle", _params_none, _build_rect),
    "rounded": ("Rounded corners", _params_rounded, _build_rounded),
    "punched": ("Punched corners", _params_rounded, _build_punched),
    "bracket": ("Bracket", _params_bracket, _build_bracket),
    "ornament": ("Ornament", _params_ornament, _build_ornament),
    "cut": ("Cut corners", _params_cut, _build_cut),
    "oval": ("Oval", _params_oval, _build_oval),
    "pill": ("Pill", _params_pill, _build_pill),
    "ticket": ("Ticket", _params_ticket, _build_ticket),
    "arched": ("Arched top", _params_arched, _build_arched),
    "shield": ("Shield", _params_shield, _build_shield),
    "ribbon": ("Ribbon", _params_ribbon, _build_ribbon),
    "hexagon": ("Hexagon label", _params_hexagon, _build_hexagon),
}
SHAPE_IDS = tuple(SHAPES)
DEFAULT_SHAPE = "rect"


def _check_shape(shape):
    if not isinstance(shape, str) or shape not in SHAPES:
        raise ShapeError("Unknown shape.")
    return shape


def resolve(shape, W, H, given=None):
    """List of {key, label, unit, value, auto, min, max} in display order. Raises
    ShapeError for unknown keys and for values outside the range valid for W x H."""
    _check_shape(shape)
    given = given or {}
    if not isinstance(given, dict):
        raise ShapeError("Shape parameters must be an object.")
    p = _Params(given)
    SHAPES[shape][1](W, H, p)
    known = {r["key"] for r in p.rows}
    for key in given:
        if key not in known:
            raise ShapeError("This shape has no parameter " + repr(str(key)) + ".")
    order = _display_order(shape)
    return sorted(p.rows, key=lambda r: order.index(r["key"]))


def _display_order(shape):
    # Resolution order differs from display order only for the ornament.
    return {"ornament": ["radius", "crest_height", "crest_width"]}.get(
        shape, [r["key"] for r in _rows_for(shape)])


@lru_cache(maxsize=None)
def _keys(shape):
    p = _Params({})
    SHAPES[shape][1](300.0, 120.0, p)
    return tuple((r["key"], r["label"], r["unit"]) for r in p.rows)


def _rows_for(shape):
    return [{"key": k} for k, _, _ in _keys(shape)]


def normalize_params(shape, W, H, raw):
    """Validated copy of `raw` holding only the parameters that are set (null = automatic)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ShapeError("Shape parameters must be an object.")
    given = {k: v for k, v in raw.items() if v is not None}
    resolve(shape, W, H, given)
    return {k: float(v) for k, v in given.items()}


def build(shape, W, H, params=None):
    rows = resolve(shape, W, H, params)
    return SHAPES[shape][2](W, H, {r["key"]: r["value"] for r in rows})


def svg_path(shape, W, H, params=None):
    """Path for the preview in a 0..W x 0..H viewBox; None for the plain rectangle."""
    if shape == "rect":
        return None
    return build(shape, W, H, params).svg()


def polygon(shape, W, H, params=None):
    """Closed polygon (no repeated end point), counter-clockwise, centred on the plate, y up."""
    pts = build(shape, W, H, params).pts
    out = []
    for x, y in pts:
        q = (x - W / 2, H / 2 - y)
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-7:
            out.append(q)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= 1e-7:
        out.pop()
    if _area(out) < 0:
        out.reverse()
    return out


def _area(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2


def describe(shape, W, H, params=None):
    """What the client needs to draw a plate and to fill the shape fields."""
    rows = resolve(shape, W, H, params)
    return {
        "shape": shape,
        "w": W,
        "h": H,
        "path": svg_path(shape, W, H, params),
        "params": {r["key"]: {"value": round(r["value"], 3), "auto": round(r["auto"], 3),
                              "min": round(r["min"], 3), "max": round(r["max"], 3)} for r in rows},
    }


@lru_cache(maxsize=1)
def catalog():
    """Shapes for the picker: id, name, parameters and a thumbnail path on a 300 x 120 plate."""
    out = []
    for sid, (name, _, _) in SHAPES.items():
        out.append({
            "id": sid,
            "name": name,
            "params": [{"key": k, "label": label, "unit": unit} for k, label, unit in _keys(sid)],
            "path": svg_path(sid, 300.0, 120.0),
        })
    return out


# --- Fit: where may text go? --------------------------------------------------------

class Fit:
    """Keeps a text block `inset` (margin + frame) away from the real contour.

    The block is tested as a rectangle grown by `inset` on every side; it fits when that
    rectangle lies inside the polygon. For a plain rectangle the numbers are the old
    closed-form ones (`is_rect`).
    """

    def __init__(self, shape, W, H, params, inset):
        self.shape, self.W, self.H, self.inset = shape, float(W), float(H), float(inset)
        self.is_rect = shape == "rect"
        self.poly = polygon(shape, W, H, params)
        n = len(self.poly)
        self.edges = []
        for i in range(n):
            ax, ay = self.poly[i]
            bx, by = self.poly[(i + 1) % n]
            self.edges.append((ax, ay, bx, by, min(ax, bx), max(ax, bx), min(ay, by), max(ay, by)))

    def _inside(self, px, py):
        inside = False
        for ax, ay, bx, by, *_ in self.edges:
            if (ay > py) != (by > py) and px < ax + (py - ay) * (bx - ax) / (by - ay):
                inside = not inside
        return inside

    def _hits(self, x0, y0, x1, y1):
        """True if the contour passes through the open rectangle."""
        for ax, ay, bx, by, minx, maxx, miny, maxy in self.edges:
            if maxx <= x0 or minx >= x1 or maxy <= y0 or miny >= y1:
                continue
            dx, dy = bx - ax, by - ay
            t0, t1 = 0.0, 1.0
            for p, q in ((-dx, ax - x0), (dx, x1 - ax), (-dy, ay - y0), (dy, y1 - ay)):
                if p == 0:
                    if q <= 0:
                        break
                else:
                    r = q / p
                    if p < 0:
                        if r > t0:
                            t0 = r
                    elif r < t1:
                        t1 = r
                    if t0 >= t1:
                        break
            else:
                if t1 - t0 > 1e-9:
                    return True
        return False

    def box_fits(self, x0, y0, x1, y1):
        """Does the rectangle (plate-centred mm) lie inside the contour?"""
        if x1 <= x0 or y1 <= y0:
            return False
        if not self._inside((x0 + x1) / 2, (y0 + y1) / 2):
            return False
        return not self._hits(x0, y0, x1, y1)

    def text_fits(self, cx, w, h):
        """A text block of w x h, horizontally centred on cx, vertically on the plate centre."""
        i = self.inset
        return self.box_fits(cx - w / 2 - i, -h / 2 - i, cx + w / 2 + i, h / 2 + i)

    def has_room(self, size=0.5):
        return self.text_fits(0.0, size, size)

    def max_scale(self, tw, th):
        """Largest s such that a centred text block of s*tw x s*th fits."""
        if tw <= 0 or th <= 0:
            return 0.0
        i = self.inset
        hi = min((self.W - 2 * i) / tw, (self.H - 2 * i) / th)
        if hi <= 0:
            return 0.0
        if self.is_rect or self.text_fits(0.0, hi * tw, hi * th):
            return hi
        lo = 0.0
        if not self.text_fits(0.0, 1e-6, 1e-6):
            return 0.0
        for _ in range(26):
            mid = (lo + hi) / 2
            if self.text_fits(0.0, mid * tw, mid * th):
                lo = mid
            else:
                hi = mid
        return lo

    def anchor(self, w, h, align):
        """x (plate-centred) of the edge the text is aligned to: its left edge for "left",
        its right edge for "right", 0 for "center". It is the furthest position, towards
        that side, where the block still keeps its distance from the contour."""
        if align not in ("left", "right"):
            return 0.0
        i = self.inset
        if self.is_rect:
            return -(self.W / 2 - i) if align == "left" else self.W / 2 - i
        sgn = -1.0 if align == "left" else 1.0
        # Position of the block centre, moved from the middle towards the side.
        far = sgn * (self.W / 2 - i - w / 2)   # block touches the bounding rectangle margin
        near = 0.0
        if self.text_fits(far, w, h):
            return far + sgn * w / 2
        if not self.text_fits(near, w, h):
            return sgn * w / 2
        lo, hi = near, far  # lo fits, hi does not
        for _ in range(30):
            mid = (lo + hi) / 2
            if self.text_fits(mid, w, h):
                lo = mid
            else:
                hi = mid
        return lo + sgn * w / 2


@lru_cache(maxsize=64)
def _fit(shape, W, H, params, inset):
    return Fit(shape, W, H, dict(params), inset)


def make_fit(shape, W, H, params, inset):
    """Cached Fit; `params` is the (validated) shape_params dict."""
    return _fit(shape, float(W), float(H), tuple(sorted((params or {}).items())), round(float(inset), 6))
