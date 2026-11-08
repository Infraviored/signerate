import math
import os

import pytest

import fonts
import outlines
import sets_store
from sets_store import ApiError

FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
needs_font = pytest.mark.skipif(not os.path.exists(FONT), reason="Liberation Sans Bold not installed")

PLATES = [(300, 120), (43, 19), (20, 10), (300, 40), (40, 200), (63, 11), (120, 40)]
ALL = list(outlines.SHAPE_IDS)


def _segments_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 * d2 < -1e-12 and d3 * d4 < -1e-12


def is_simple(pts):
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(a, b, pts[j], pts[(j + 1) % n]):
                return False
    return True


@pytest.mark.parametrize("shape", ALL)
@pytest.mark.parametrize("W,H", PLATES)
def test_polygon_closed_simple_and_bounded(shape, W, H):
    pts = outlines.polygon(shape, W, H)
    assert len(pts) >= 4
    assert pts[0] != pts[-1]  # closed implicitly, no repeated end point
    assert outlines._area(pts) > 0  # counter-clockwise
    assert is_simple(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert max(xs) - min(xs) == pytest.approx(W, abs=1e-4)
    assert max(ys) - min(ys) == pytest.approx(H, abs=1e-4)
    assert min(xs) == pytest.approx(-W / 2, abs=1e-4) and min(ys) == pytest.approx(-H / 2, abs=1e-4)


@pytest.mark.parametrize("shape", ALL)
@pytest.mark.parametrize("W,H", PLATES)
def test_auto_params_valid_and_inside_their_range(shape, W, H):
    rows = outlines.resolve(shape, W, H)
    assert bool(rows) == (shape != "rect")
    for r in rows:
        assert r["min"] <= r["auto"] <= r["max"] and r["value"] == r["auto"]
    assert outlines.svg_path(shape, W, H) in (None,) or outlines.svg_path(shape, W, H).startswith("M")


@pytest.mark.parametrize("shape", [s for s in ALL if s != "rect"])
@pytest.mark.parametrize("W,H", PLATES)
def test_extreme_params_stay_valid(shape, W, H):
    for pick in ("min", "max"):
        given = {r["key"]: r[pick] for r in outlines.resolve(shape, W, H)}
        # ranges of later parameters depend on earlier ones, so ask again with those applied
        try:
            rows = outlines.resolve(shape, W, H, given)
        except outlines.ShapeError:
            continue
        pts = outlines.polygon(shape, W, H, {r["key"]: r["value"] for r in rows})
        assert is_simple(pts)
        xs = [p[0] for p in pts]
        assert max(xs) - min(xs) == pytest.approx(W, abs=1e-4)


def test_rounded_chord_error_is_small():
    pts = outlines.polygon("rounded", 100, 50, {"radius": 10})
    # every vertex of the corner arcs lies on the circle, every edge midpoint within 0.02 + eps
    cx, cy = 50 - 10, 25 - 10
    worst = 0.0
    for i, (x, y) in enumerate(pts):
        nx, ny = pts[(i + 1) % len(pts)]
        mx, my = (x + nx) / 2, (y + ny) / 2
        if mx > cx and my > cy:
            worst = max(worst, abs(math.hypot(mx - cx, my - cy) - 10))
    assert 0 < worst <= 0.021


def test_catalog_has_twelve_plus_rect_and_params():
    cat = outlines.catalog()
    assert [c["id"] for c in cat] == ALL and len(cat) == 13
    for c in cat:
        assert (c["id"] == "rect") == (not c["params"])
        assert not c["name"].endswith("...") and "family" not in c["name"].lower()
        for p in c["params"]:
            assert not p["label"].endswith("...") and "family" not in p["label"].lower()


def test_param_validation():
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("rounded", 100, 40, {"radius": 21})  # > H/2
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("rounded", 100, 40, {"radius": -1})
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("rounded", 100, 40, {"radius": "5"})
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("rounded", 100, 40, {"radius": True})
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("rounded", 100, 40, {"bogus": 1})
    with pytest.raises(outlines.ShapeError):
        outlines.resolve("nope", 100, 40)
    assert outlines.normalize_params("rounded", 100, 40, {"radius": None}) == {}


# --- Fit ----------------------------------------------------------------------

@pytest.mark.parametrize("shape", [s for s in ALL if s != "rect"])
@pytest.mark.parametrize("W,H,inset", [(120, 40, 2.0), (43, 19, 1.2), (300, 120, 3.0)])
def test_max_scale_keeps_text_inside_contour(shape, W, H, inset):
    fit = outlines.make_fit(shape, W, H, {}, inset)
    tw, th = 5.0, 1.0
    s = fit.max_scale(tw, th)
    assert s > 0
    assert fit.text_fits(0.0, s * tw, s * th)
    # growing by a little breaks it, unless the plain rectangle is the limit
    rect_limit = min((W - 2 * inset) / tw, (H - 2 * inset) / th)
    if s < rect_limit * 0.999:
        assert not fit.text_fits(0.0, s * 1.01 * tw, s * 1.01 * th)
    assert s <= rect_limit + 1e-9


@pytest.mark.parametrize("shape", ALL)
@pytest.mark.parametrize("align", ["left", "right"])
def test_anchor_fits_and_is_furthest(shape, align):
    W, H, inset = 120.0, 40.0, 2.0
    fit = outlines.make_fit(shape, W, H, {}, inset)
    w, h = 30.0, 8.0
    a = fit.anchor(w, h, align)
    sgn = -1 if align == "left" else 1
    centre = a - sgn * w / 2
    assert fit.text_fits(centre, w, h)
    assert not fit.text_fits(centre + sgn * 0.05, w, h) or abs(a) >= W / 2 - inset - 1e-6
    if shape == "rect":
        assert a == sgn * (W / 2 - inset)


def test_anchor_respects_contour_not_bounding_box():
    fit = outlines.make_fit("ribbon", 120, 40, {}, 1.0)
    rect_edge = -(60 - 1.0)
    assert fit.anchor(20, 6, "left") > rect_edge + 5  # the notch pushes the text inwards


def test_has_room():
    assert outlines.make_fit("oval", 40, 10, {}, 1.0).has_room()
    assert not outlines.make_fit("oval", 40, 10, {}, 4.9).has_room()


# --- Sizes ----------------------------------------------------------------------

@needs_font
def test_rect_sizes_unchanged():
    texts = ["Hello", "A longer sign text", "x"]
    for W, H, m in [(63, 11, 0.2), (120, 40, 5), (43, 19, 1.2)]:
        plain = fonts.plan_sizes(texts, FONT, W - 2 * m, H - 2 * m)
        fit = outlines.make_fit("rect", W, H, {}, m)
        with_fit = fonts.plan_sizes(texts, FONT, W - 2 * m, H - 2 * m, fit=fit)
        for k in ("set_size", "optimum", "limiting_text", "sizes", "shrunk"):
            assert plain[k] == with_fit[k]
        assert plain["anchors"] == [None] * 3


@needs_font
@pytest.mark.parametrize("shape", [s for s in ALL if s != "rect"])
def test_sizes_never_exceed_rect_and_text_stays_inside(shape):
    W, H, m = 120.0, 40.0, 2.0
    texts = ["Hello", "Screws and nuts", "Hi\nthere"]
    plain = fonts.plan_sizes(texts, FONT, W - 2 * m, H - 2 * m)
    fit = outlines.make_fit(shape, W, H, {}, m)
    plan = fonts.plan_sizes(texts, FONT, W - 2 * m, H - 2 * m, fit=fit, align="left")
    assert plan["optimum"] <= plain["optimum"] + 1e-9
    for t, size, anchor in zip(texts, plan["sizes"], plan["anchors"]):
        w, h = fonts._text_dims(t, fonts._pil_font(FONT, 100), size)
        assert fit.text_fits(anchor + w / 2, w, h)
        centred = fonts.plan_sizes([t], FONT, W - 2 * m, H - 2 * m, fit=fit)["sizes"][0]
        assert fit.text_fits(0.0, *fonts._text_dims(t, fonts._pil_font(FONT, 100), centred))


@needs_font
def test_fixed_size_that_does_not_fit_shrinks_like_a_rectangle():
    fit = outlines.make_fit("ribbon", 120, 40, {}, 2.0)
    plan = fonts.plan_sizes(["Hello"], FONT, 116, 36, frozen=60.0, fit=fit)
    assert plan["sizes"][0] < 60.0 and plan["shrunk"] == [True]


# --- Store ----------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sets_store, "SETS_DIR", tmp_path / "sets")
    monkeypatch.setattr(sets_store, "SETTINGS_FILE", tmp_path / "none.json")
    monkeypatch.setattr(fonts, "find_arial_path", lambda: FONT)
    (tmp_path / "sets").mkdir()
    return tmp_path / "sets"


@needs_font
def test_default_shape_is_rect_and_old_sets_load(store):
    s = sets_store.validate_settings({}, sets_store.default_settings())
    assert s["shape"] == "rect" and s["shape_params"] == {}
    old = {k: v for k, v in s.items() if k not in ("shape", "shape_params")}
    assert sets_store.outline_for(old)["shape"] == "rect"
    assert sets_store.plan_for(old, ["Hi"])["sizes"][0] > 0


@needs_font
@pytest.mark.parametrize("raw", [
    {"shape": "nope"}, {"shape": 5}, {"shape": "rounded", "shape_params": {"radius": -1}},
    {"shape": "rounded", "shape_params": {"radius": "x"}}, {"shape": "rounded", "shape_params": {"foo": 1}},
    {"shape": "rect", "shape_params": {"radius": 1}}, {"shape": "rounded", "shape_params": []},
    {"shape": "rounded", "shape_params": {"radius": 1e9}}, {"shape": "rounded", "shape_params": {"radius": float("nan")}},
])
def test_invalid_shape_values_are_400(store, raw):
    with pytest.raises(ApiError) as e:
        sets_store.validate_settings(raw, sets_store.default_settings())
    assert e.value.status == 400 and e.value.message


@needs_font
def test_frame_too_wide_for_shape_is_400(store):
    with pytest.raises(ApiError) as e:
        sets_store.validate_settings({"width": 40, "height": 10, "shape": "oval", "min_margin": 0.5, "frame_width": 4.2},
                                     sets_store.default_settings())
    assert "too large" in e.value.message


@needs_font
def test_params_null_means_auto_and_survive_duplicate(store):
    sets_store.create_set({"name": "A", "settings": {"font_path": FONT, "shape": "rounded", "shape_params": {"radius": 3}}})
    assert sets_store.read_set("A")["settings"]["shape_params"] == {"radius": 3.0}
    sets_store.create_set({"name": "B", "from": "A", "settings": {}})
    b = sets_store.read_set("B")["settings"]
    assert b["shape"] == "rounded" and b["shape_params"] == {"radius": 3.0}
    sets_store.update_set("A", {"settings": {"shape_params": {"radius": None}}})
    assert sets_store.read_set("A")["settings"]["shape_params"] == {}


@needs_font
def test_preview_contains_outline_and_anchor(store):
    res = sets_store.preview({"shape": "hexagon", "width": 100, "height": 30, "horizontal_align": "right",
                              "min_margin": 1}, ["Hi"])
    assert res["outline"]["shape"] == "hexagon" and res["outline"]["path"].startswith("M")
    assert res["signs"][0]["x"] is not None and res["signs"][0]["x"] < 50
    assert sets_store.summarize(sets_store.create_set({"name": "S", "settings": {"font_path": FONT}}))["outline"]["path"] is None


@needs_font
def test_export_plans_with_shape(store, tmp_path):
    from tests.test_sets import FakeGenerator, make_set
    make_set("Sh", ["Hello"], settings={"font_path": FONT, "shape": "ribbon", "width": 100, "height": 30})
    plain = make_set("Pl", ["Hello"], settings={"font_path": FONT, "width": 100, "height": 30})
    gen = FakeGenerator()
    sets_store.run_export("Sh", "all", "3mf", tmp_path / "o.bin", gen)
    gen2 = FakeGenerator()
    sets_store.run_export("Pl", "all", "3mf", tmp_path / "o.bin", gen2)
    assert gen.calls[0]["sizes"][0] < gen2.calls[0]["sizes"][0]
