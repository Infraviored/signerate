import json
import os
import re

import pytest

import fonts
import sets_store
from sets_store import ApiError

FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
pytestmark = pytest.mark.skipif(not os.path.exists(FONT), reason="Liberation Sans Bold not installed")

SHORT, LONG = "Hi", "A very long sign text that cannot possibly fit"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(sets_store, "SETS_DIR", tmp_path / "sets")
    monkeypatch.setattr(sets_store, "SETTINGS_FILE", tmp_path / "no-settings.json")
    monkeypatch.setattr(fonts, "find_arial_path", lambda: FONT)
    (tmp_path / "sets").mkdir()
    return tmp_path / "sets"


class FakeGenerator:
    """Stands in for the CadQuery geometry: records what it was asked to build."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def __call__(self, texts, sizes, settings, path, fmt, progress):
        if self.fail:
            raise RuntimeError("boom")
        self.calls.append({"texts": list(texts), "sizes": list(sizes), "fmt": fmt})
        with open(path, "wb") as f:
            f.write(b"fake")
        return path


def make_set(name="T", texts=(), exported=(), settings=None):
    """texts are given newest first (as stored)."""
    sets_store.create_set({"name": name, "settings": settings or {"font_path": FONT}})
    data = sets_store.read_set(name)
    data["signs"] = [
        {"id": sets_store.new_id(), "text": t, "created_at": sets_store.now_iso(),
         "exported_at": sets_store.now_iso() if t in exported else None}
        for t in texts
    ]
    sets_store._write(sets_store._path(name), data)
    return sets_store.read_set(name)


def export(name, scope, gen, tmp_path, fmt="3mf"):
    return sets_store.run_export(name, scope, fmt, tmp_path / "out.bin", gen)


# --- names -------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Sortierer", "Über Größe", "a_b-c 1", "Ünï"])
def test_valid_names(name):
    assert sets_store.validate_name(name) == name


@pytest.mark.parametrize("name", [
    "../x", "a/b", "a\\b", ".hidden", "..", "_trash", "_TRASH", "_backup_v1", "", "   ",
    "x" * 61, "a.json", "tab\tname", None, 5,
])
def test_invalid_names(name):
    with pytest.raises(ApiError) as e:
        sets_store.validate_name(name)
    assert e.value.status == 400


def test_name_checked_on_every_path(store):
    for call in (
        lambda: sets_store.read_set("../x"),
        lambda: sets_store.update_set("_trash", {}),
        lambda: sets_store.delete_set(".x"),
        lambda: sets_store.create_set({"name": "a/b"}),
    ):
        with pytest.raises(ApiError) as e:
            call()
        assert e.value.status == 400


def test_duplicate_and_rename_collisions(store):
    make_set("One")
    make_set("Two")
    with pytest.raises(ApiError) as e:
        sets_store.create_set({"name": "one"})  # case-insensitive
    assert e.value.status == 409
    with pytest.raises(ApiError) as e:
        sets_store.rename_set("Two", "One")
    assert e.value.status == 409
    sets_store.rename_set("Two", "Three")
    assert sets_store.list_names() == ["One", "Three"]
    assert sets_store.read_set("Three")["name"] == "Three"


# --- migration ---------------------------------------------------------------

V1_SETTINGS = {"width": 63, "height": 11, "min_margin": 0.2, "base_thickness": 0.4,
               "text_thickness": 0.2, "bg_color": "#000000", "text_color": "#ffffff",
               "font_path": FONT, "export_format": "3mf"}


def write_v1(store, name, **extra):
    raw = {"name": name, "texts": ["a", "b", "c", "b"], "settings": dict(V1_SETTINGS), **extra}
    (store / f"{name}.json").write_text(json.dumps(raw), encoding="utf-8")
    return raw


def test_migration_with_versions(store):
    versions = [{"id": "v1", "date": "2026-03-01", "texts": ["a", "b"]},
                {"id": "v2", "date": "2026-03-18", "texts": ["a", "b", "c", "b"]}]
    write_v1(store, "Old", versions=versions)
    original = (store / "Old.json").read_bytes()

    sets_store.init_sets()
    data = json.loads((store / "Old.json").read_text(encoding="utf-8"))

    assert data["schema"] == 2
    assert [s["text"] for s in data["signs"]] == ["b", "c", "b", "a"]  # reversed: newest first
    assert len({s["id"] for s in data["signs"]}) == 4
    assert all(s["exported_at"].startswith("2026-03-18") for s in data["signs"])
    created = {s["text"]: s["created_at"][:10] for s in data["signs"]}
    assert created == {"a": "2026-03-01", "b": "2026-03-01", "c": "2026-03-18"}
    assert data["exported_settings"] == data["settings"]
    expected = fonts.calculate_optimal_font_size(["a", "b", "c"], FONT, 63 - 0.4, 11 - 0.4)[0]
    assert data["font_size"] == expected
    # missing settings come from defaults
    assert data["settings"]["horizontal_align"] == "center"
    assert data["settings"]["text_protrusion"] == 0
    assert "export_format" not in data["settings"]
    assert (store / "_backup_v1" / "Old.json").read_bytes() == original


def test_migration_without_versions(store):
    write_v1(store, "Workshop")
    sets_store.init_sets()
    data = sets_store.read_set("Workshop")
    assert [s["text"] for s in data["signs"]] == ["b", "c", "b", "a"]
    assert all(s["exported_at"] is None for s in data["signs"])
    assert data["font_size"] is None and data["exported_settings"] is None


def test_migration_missing_font_falls_back_to_null(store):
    raw = write_v1(store, "NoFont", versions=[{"id": "v1", "date": "2026-03-01", "texts": ["a"]}])
    raw["settings"]["font_path"] = "/nonexistent/font.ttf"
    (store / "NoFont.json").write_text(json.dumps(raw), encoding="utf-8")
    sets_store.init_sets()
    data = sets_store.read_set("NoFont")
    assert data["font_size"] is None
    assert data["signs"][0]["exported_at"] is not None


def test_migration_is_idempotent(store):
    write_v1(store, "Old", versions=[{"id": "v1", "date": "2026-03-01", "texts": ["a", "b", "c"]}])
    sets_store.init_sets()
    first = (store / "Old.json").read_bytes()
    backup = (store / "_backup_v1" / "Old.json").read_bytes()
    sets_store.init_sets()
    assert (store / "Old.json").read_bytes() == first
    assert (store / "_backup_v1" / "Old.json").read_bytes() == backup
    assert sets_store.list_names() == ["Old"]  # _backup_v1 is not a Set


def test_seed_when_empty(store):
    sets_store.init_sets()
    data = sets_store.read_set("Workshop")
    assert data["schema"] == 2
    assert [s["text"] for s in data["signs"]] == ["Screws", "Nuts"]
    assert all(s["exported_at"] is None for s in data["signs"])


# --- export selection, freeze rule, shrunk ----------------------------------

def test_export_new_vs_all_in_list_order(store, tmp_path):
    make_set("T", texts=["d", "c", "b", "a"], exported={"c", "a"})
    gen = FakeGenerator()
    res = export("T", "new", gen, tmp_path)
    assert gen.calls[0]["texts"] == ["d", "b"]
    assert res["count"] == 2
    assert re.fullmatch(r"T-\w+\+1-[\d.]+x[\d.]+mm\.3mf", res["filename"])

    data = sets_store.read_set("T")
    assert all(s["exported_at"] for s in data["signs"])
    assert data["exports"][-1]["scope"] == "new" and data["exports"][-1]["count"] == 2

    with pytest.raises(ApiError) as e:
        export("T", "new", gen, tmp_path)
    assert e.value.status == 400 and "Nothing new" in e.value.message

    res = export("T", "all", gen, tmp_path, fmt="step")
    assert gen.calls[-1]["texts"] == ["d", "c", "b", "a"]
    assert re.fullmatch(r"T-\w+\+3-[\d.]+x[\d.]+mm\.step", res["filename"])


def test_font_size_is_frozen_over_all_signs_at_first_export(store, tmp_path):
    # Only SHORT is new; the optimum must still consider LONG.
    make_set("T", texts=[SHORT, LONG], exported={LONG})
    gen = FakeGenerator()
    export("T", "new", gen, tmp_path)
    data = sets_store.read_set("T")
    s = data["settings"]
    optimum_all = fonts.calculate_optimal_font_size(
        [SHORT, LONG], FONT, s["width"] - 2 * s["min_margin"], s["height"] - 2 * s["min_margin"])[0]
    only_short = fonts.calculate_optimal_font_size(
        [SHORT], FONT, s["width"] - 2 * s["min_margin"], s["height"] - 2 * s["min_margin"])[0]
    assert optimum_all < only_short
    assert data["font_size"] == optimum_all
    assert gen.calls[0]["sizes"] == [optimum_all]
    assert data["exported_settings"] == data["settings"]


def test_frozen_size_survives_later_signs_and_renew_all_resets(store, tmp_path):
    make_set("T", texts=[SHORT])
    gen = FakeGenerator()
    export("T", "new", gen, tmp_path)
    frozen = sets_store.read_set("T")["font_size"]

    data = sets_store.read_set("T")
    signs = [{"id": sets_store.new_id(), "text": LONG}] + [{"id": s["id"], "text": s["text"]} for s in data["signs"]]
    sets_store.update_set("T", {"signs": signs})
    export("T", "new", gen, tmp_path)
    call = gen.calls[-1]
    assert call["texts"] == [LONG]
    assert call["sizes"][0] < frozen          # shrunk to fit
    assert sets_store.read_set("T")["font_size"] == frozen

    data = sets_store.renew_all("T")
    assert data["font_size"] is None and data["exported_settings"] is None
    assert all(s["exported_at"] is None for s in data["signs"])


def test_shrunk_flag(store):
    result = sets_store.preview({"font_path": FONT}, [SHORT, LONG, ""], frozen=5.0)
    flags = [s["shrunk"] for s in result["signs"]]
    assert flags == [False, True, False]
    assert result["font_size"] == 5.0 and result["locked"] is True
    assert result["signs"][1]["font_size"] < 5.0
    assert result["signs"][0]["font_size"] == 5.0

    unfrozen = sets_store.preview({"font_path": FONT}, [SHORT, LONG])
    assert not any(s["shrunk"] for s in unfrozen["signs"])
    assert unfrozen["limiting_text"] == LONG
    assert {s["font_size"] for s in unfrozen["signs"]} == {unfrozen["font_size"]}


def test_failed_generation_marks_nothing(store, tmp_path):
    make_set("T", texts=["b", "a"])
    before = (store / "T.json").read_bytes()
    with pytest.raises(RuntimeError):
        export("T", "new", FakeGenerator(fail=True), tmp_path)
    assert (store / "T.json").read_bytes() == before
    data = sets_store.read_set("T")
    assert data["font_size"] is None and data["exports"] == []


def test_editing_exported_text_makes_it_new(store, tmp_path):
    data = make_set("T", texts=["b", "a"], exported={"a"})
    ids = {s["text"]: s["id"] for s in data["signs"]}
    out = sets_store.update_set("T", {"signs": [
        {"id": ids["b"], "text": "b"}, {"id": ids["a"], "text": "a changed"}]})
    assert all(s["exported_at"] is None for s in out["signs"])

    out = sets_store.update_set("T", {"signs": [{"id": ids["b"], "text": "b"}]})
    assert [s["text"] for s in out["signs"]] == ["b"]


def test_update_validation(store):
    make_set("T", texts=["a"])
    for bad in ({"signs": [{"text": ""}]}, {"signs": "x"}, {"settings": {"width": 0}},
                {"settings": {"bg_color": "red"}}, {"settings": {"min_margin": 99}},
                {"signs": [{"id": "abcdef0123456789", "text": "x"}, {"id": "abcdef0123456789", "text": "y"}]}):
        with pytest.raises(ApiError) as e:
            sets_store.update_set("T", bad)
        assert e.value.status == 400


def test_create_from_existing(store):
    make_set("Src", texts=["b", "a"], exported={"a"}, settings={"font_path": FONT, "width": 80})
    blank = sets_store.create_set({"name": "Blank", "from": "Src"})
    assert blank["settings"]["width"] == 80 and blank["signs"] == []
    copy = sets_store.create_set({"name": "Copy", "from": "Src", "copy_signs": True, "settings": {"height": 20}})
    assert [s["text"] for s in copy["signs"]] == ["b", "a"]
    assert all(s["exported_at"] is None for s in copy["signs"])
    assert copy["settings"]["height"] == 20 and copy["settings"]["width"] == 80
    assert sets_store.read_set("Src")["settings"]["height"] != 20  # independent


def test_delete_moves_to_trash(store):
    make_set("Gone")
    sets_store.delete_set("Gone")
    assert sets_store.list_names() == []
    assert len(list((store / "_trash").glob("Gone_*.json"))) == 1


# --- HTTP layer --------------------------------------------------------------

def test_http_export_and_download(store, monkeypatch, tmp_path):
    import app as app_module
    monkeypatch.setattr(app_module, "EXPORT_DIR", str(tmp_path / "exports"))
    gen = FakeGenerator()
    monkeypatch.setattr(app_module, "generate_signs", gen)
    make_set("Web", texts=["b", "a"])
    client = app_module.app.test_client()

    bad = client.post("/api/sets/Web/export", json={"scope": "nope"})
    assert bad.status_code == 400

    ok = client.post("/api/sets/Web/export", json={"scope": "new", "format": "3mf"})
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["count"] == 2

    assert client.get("/api/download").status_code == 404                 # no token
    assert client.get("/api/download?id=wrong").status_code == 404
    dl = client.get("/api/download?id=" + body["token"])
    assert dl.status_code == 200 and dl.data == b"fake"
    assert body["filename"] in dl.headers["Content-Disposition"]

    again = client.post("/api/sets/Web/export", json={"scope": "new"})
    assert again.status_code == 400 and "Nothing new" in again.get_json()["error"]

    assert client.get("/api/sets/..%2Fx").status_code in (400, 404)
    assert client.put("/api/sets/Web", data="{bad", content_type="application/json").status_code == 400


def test_export_filename():
    st = {"width": 43.0, "height": 19.5}
    assert sets_store.export_filename("Klingel", "Florian\nSchneider", 1, st, "3mf") == "Klingel-Florian-43x19.5mm.3mf"
    assert sets_store.export_filename("My Set", "Bits, Bohrer", 3, st, "step") == "My_Set-Bits+2-43x19.5mm.step"
    assert sets_store.export_filename("S", "...", 1, st, "3mf") == "S-Sign-43x19.5mm.3mf"


# --- export by selection ids -------------------------------------------------

def test_export_with_ids_marks_only_those(store, tmp_path):
    data = make_set("T", texts=["d", "c", "b", "a"], exported={"c"})
    ids = {s["text"]: s["id"] for s in data["signs"]}
    gen = FakeGenerator()
    res = sets_store.run_export("T", "new", "3mf", tmp_path / "out.bin", gen, ids=[ids["a"], ids["c"]])
    assert res["count"] == 2
    assert gen.calls[0]["texts"] == ["c", "a"]  # list order, not request order
    after = {s["text"]: s for s in sets_store.read_set("T")["signs"]}
    assert after["a"]["exported_at"] and after["c"]["exported_at"]
    assert after["d"]["exported_at"] is None and after["b"]["exported_at"] is None
    assert sets_store.read_set("T")["exports"][-1]["scope"] == "selected"


def test_export_with_unknown_or_empty_ids_is_rejected(store, tmp_path):
    data = make_set("T", texts=["b", "a"])
    good = data["signs"][0]["id"]
    for bad in ([], ["nope"], [good, "nope"], "x", [1]):
        with pytest.raises(ApiError) as e:
            sets_store.run_export("T", "new", "3mf", tmp_path / "out.bin", FakeGenerator(), ids=bad)
        assert e.value.status == 400
    assert all(s["exported_at"] is None for s in sets_store.read_set("T")["signs"])


# --- stale-tab protection ----------------------------------------------------

def _payload(data, base=..., texts=None):
    body = {"signs": [{"id": s["id"], "text": s["text"]} for s in data["signs"]]}
    if texts is not None:
        body["signs"] = texts
    if base is not ...:
        body["base_updated_at"] = base
    return body


def test_put_with_stale_base_is_409_and_writes_nothing(store):
    data = make_set("T", texts=["b", "a"])
    stale = data["updated_at"]
    sets_store.update_set("T", _payload(data, base=stale, texts=[{"text": "c"}] + _payload(data)["signs"]))
    with pytest.raises(ApiError) as e:
        sets_store.update_set("T", _payload(data, base=stale, texts=[]))  # would delete everything
    assert e.value.status == 409
    assert [s["text"] for s in sets_store.read_set("T")["signs"]] == ["c", "b", "a"]


def test_put_with_matching_or_missing_base_is_ok(store):
    data = make_set("T", texts=["a"])
    out = sets_store.update_set("T", _payload(data, base=data["updated_at"]))
    assert out["updated_at"] != data["updated_at"]
    sets_store.update_set("T", _payload(data))  # no base: allowed


def test_two_quick_puts_with_refreshed_base_are_ok(store):
    data = make_set("T", texts=["a"])
    first = sets_store.update_set("T", _payload(data, base=data["updated_at"]))
    second = sets_store.update_set("T", _payload(first, base=first["updated_at"]))
    third = sets_store.update_set("T", _payload(second, base=second["updated_at"]))
    assert len({data["updated_at"], first["updated_at"], second["updated_at"], third["updated_at"]}) == 4


def test_every_write_changes_updated_at(store, tmp_path):
    data = make_set("T", texts=["a"], exported={"a"})
    seen = [data["updated_at"]]
    seen.append(sets_store.renew_sign("T", data["signs"][0]["id"])["updated_at"])
    seen.append(sets_store.renew_all("T")["updated_at"])
    seen.append(sets_store.run_export("T", "all", "3mf", tmp_path / "o.bin", FakeGenerator())["set"]["updated_at"])
    seen.append(sets_store.rename_set("T", "U")["updated_at"])
    assert len(set(seen)) == len(seen)
