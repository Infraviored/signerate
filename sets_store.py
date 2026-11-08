"""Sign Sets: one JSON file per Set in sets/<name>.json (schema 2).

Everything that touches those files lives here: name validation, atomic writes,
the lock around read-modify-write, migration from the old format and the export
bookkeeping. No CadQuery import; geometry is passed in as a callable.
"""
import json
import math
import os
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fonts

BASE_DIR = Path(__file__).resolve().parent
SETS_DIR = Path(os.environ.get("SIGNERATE_SETS_DIR") or BASE_DIR / "sets")
SETTINGS_FILE = BASE_DIR / "settings.json"  # read-only defaults for new Sets

SCHEMA = 2
TRASH_DIRNAME = "_trash"
BACKUP_DIRNAME = "_backup_v1"
RESERVED_NAMES = {TRASH_DIRNAME, BACKUP_DIRNAME}
MAX_NAME_LEN = 60
MAX_TEXT_LEN = 200
MAX_SIGNS = 2000

SETTING_KEYS = (
    "width", "height", "min_margin", "base_thickness", "text_thickness",
    "text_protrusion", "bg_color", "text_color", "font_path", "horizontal_align",
    "text_size", "frame_width",
)

BUILTIN_DEFAULTS = {
    "width": 63.0,
    "height": 11.0,
    "min_margin": 0.2,
    "base_thickness": 0.4,
    "text_thickness": 0.2,
    "text_protrusion": 0.0,
    "bg_color": "#000000",
    "text_color": "#ffffff",
    "font_path": "",
    "horizontal_align": "center",
    "text_size": None,  # None = fit the plate; a number = fixed size in mm
    "frame_width": 0.0,  # ring along the plate edge in the text colour; 0 = none
}

LOCK = threading.RLock()

_NAME_RE = re.compile(r"[\w -]+")
_ID_RE = re.compile(r"[0-9a-f]{8,32}")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def log(msg):
    print(f"[SETS] {msg}")
    sys.stdout.flush()


# --- Small helpers -----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:16]


def _parse_iso(value):
    """Returns the value if it is a valid ISO timestamp string, else None."""
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


# --- Names -------------------------------------------------------------------

def validate_name(name):
    """The ONE name check, used by every route. Returns the cleaned name."""
    if not isinstance(name, str):
        raise ApiError("Name must be text.")
    name = name.strip()
    if not name:
        raise ApiError("Name is required.")
    if len(name) > MAX_NAME_LEN:
        raise ApiError(f"Name is too long (max {MAX_NAME_LEN} characters).")
    if name.casefold() in RESERVED_NAMES:
        raise ApiError("This name is reserved.")
    if not _NAME_RE.fullmatch(name):
        raise ApiError("Use only letters, digits, spaces, '_' and '-'.")
    return name


def _path(name):
    return SETS_DIR / f"{validate_name(name)}.json"


def list_names():
    if not SETS_DIR.exists():
        return []
    return sorted(p.stem for p in SETS_DIR.glob("*.json"))


def _name_taken(name, except_name=None):
    folded = name.casefold()
    return any(
        n.casefold() == folded and n != except_name for n in list_names()
    )


# --- Settings ----------------------------------------------------------------

def default_settings():
    """Built-in defaults overlaid with the read-only settings.json."""
    out = dict(BUILTIN_DEFAULTS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k in SETTING_KEYS:
            if k in raw:
                out[k] = raw[k]
    except (OSError, ValueError):
        pass
    if not out.get("font_path"):
        out["font_path"] = fonts.find_arial_path()
    return out


def _number(value, key):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(f"{key} must be a number.")
    value = float(value)
    if not math.isfinite(value):
        raise ApiError(f"{key} must be a finite number.")
    return value


def validate_settings(raw, base):
    """Merges `raw` over `base` and validates the result. Unknown keys are ignored."""
    if not isinstance(raw, dict):
        raise ApiError("settings must be an object.")
    s = {k: base.get(k, BUILTIN_DEFAULTS[k]) for k in SETTING_KEYS}
    for k in SETTING_KEYS:
        if k in raw:
            s[k] = raw[k]

    for k in ("width", "height", "base_thickness", "text_thickness"):
        s[k] = _number(s[k], k)
        if s[k] <= 0 or s[k] > 2000:
            raise ApiError(f"{k} must be greater than 0 (and at most 2000).")
    s["min_margin"] = _number(s["min_margin"], "min_margin")
    if s["min_margin"] < 0:
        raise ApiError("min_margin must not be negative.")
    s["text_protrusion"] = _number(s["text_protrusion"], "text_protrusion")
    if abs(s["text_protrusion"]) > 100:
        raise ApiError("text_protrusion is out of range.")
    s["frame_width"] = _number(s["frame_width"], "frame_width")
    if s["frame_width"] < 0:
        raise ApiError("frame_width must not be negative.")
    for k in ("bg_color", "text_color"):
        if not isinstance(s[k], str) or not _COLOR_RE.fullmatch(s[k]):
            raise ApiError(f"{k} must be a color like #aabbcc.")
        s[k] = s[k].lower()
    if not isinstance(s["font_path"], str):
        raise ApiError("font_path must be text.")
    if s["horizontal_align"] not in ("left", "center", "right"):
        raise ApiError("horizontal_align must be left, center or right.")
    if s["text_size"] is not None:
        s["text_size"] = _number(s["text_size"], "text_size")
        if s["text_size"] <= 0 or s["text_size"] > 2000:
            raise ApiError("text_size must be greater than 0 (and at most 2000).")
    check_geometry(s)
    return s


def text_inset(s):
    """Distance from the plate edge to the area the text may use."""
    return float(s["min_margin"]) + float(s.get("frame_width") or 0)


def check_geometry(s):
    if 2 * text_inset(s) >= min(float(s["width"]), float(s["height"])):
        raise ApiError("Margin and frame are too large for the width/height.")


# --- Files -------------------------------------------------------------------

def _write(path, data):
    """Atomic write: temp file in the same directory, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_set(name):
    path = _path(name)
    with LOCK:
        if not path.exists():
            raise ApiError("Set not found.", 404)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except ValueError:
            raise ApiError("Set file is damaged.", 500)
        if not isinstance(raw, dict):
            raise ApiError("Set file is damaged.", 500)
        if raw.get("schema") != SCHEMA:
            raw = migrate_file(path, raw)
        return raw


# --- Migration ---------------------------------------------------------------

def _date_to_iso(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return f"{value}T12:00:00+00:00"
    return _parse_iso(value)


def _optimum_for(texts, settings):
    font_path = settings.get("font_path")
    if not texts or not font_path or not os.path.exists(font_path):
        return None
    try:
        aw = float(settings["width"]) - 2 * text_inset(settings)
        ah = float(settings["height"]) - 2 * text_inset(settings)
        return fonts.plan_sizes(texts, font_path, aw, ah)["optimum"]
    except Exception as e:  # unreadable font etc.
        log(f"Could not compute font size during migration: {e}")
        return None


def migrate_data(raw, name, mtime_iso):
    """Converts an old (texts/versions) Set into schema 2. Pure, no file access."""
    base = default_settings()
    old_settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    settings = {k: old_settings.get(k, base[k]) for k in SETTING_KEYS}

    versions = raw.get("versions") if isinstance(raw.get("versions"), list) else []
    first_date = {}
    for v in versions:
        when = _date_to_iso(v.get("date")) if isinstance(v, dict) else None
        if not when:
            continue
        for t in v.get("texts", []):
            if isinstance(t, str):
                first_date.setdefault(t.strip(), when)

    last_date = None
    if versions and isinstance(versions[-1], dict):
        last_date = _date_to_iso(versions[-1].get("date")) or mtime_iso

    old_texts = [t.strip() for t in raw.get("texts", []) if isinstance(t, str) and t.strip()]
    texts_new_first = list(reversed(old_texts))  # old files are oldest first

    signs = [
        {
            "id": new_id(),
            "text": t,
            "created_at": first_date.get(t, mtime_iso),
            "exported_at": last_date,
        }
        for t in texts_new_first
    ]

    font_size = None
    exported_settings = None
    if versions:
        font_size = _optimum_for(texts_new_first, settings)
        exported_settings = dict(settings)

    created = min((s["created_at"] for s in signs), default=mtime_iso)
    return {
        "schema": SCHEMA,
        "name": name,
        "created_at": min(created, mtime_iso),
        "updated_at": mtime_iso,
        "settings": settings,
        "font_size": font_size,
        "exported_settings": exported_settings,
        "signs": signs,
        "exports": [],
    }


def migrate_file(path, raw=None):
    """Backs the file up to sets/_backup_v1/, then rewrites it as schema 2."""
    path = Path(path)
    with LOCK:
        if raw is None:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        if raw.get("schema") == SCHEMA:
            return raw
        backup_dir = path.parent / BACKUP_DIRNAME
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / path.name
        if not backup.exists():
            shutil.copy2(path, backup)
        mtime_iso = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
        data = migrate_data(raw, path.stem, mtime_iso)
        _write(path, data)
        log(f"Migrated {path.name} to schema 2 ({len(data['signs'])} signs, backup in {BACKUP_DIRNAME}/)")
        return data


def migrate_all():
    with LOCK:
        for p in sorted(SETS_DIR.glob("*.json")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict) and raw.get("schema") != SCHEMA:
                    migrate_file(p, raw)
            except Exception as e:
                log(f"Skipping {p.name}: {e}")


def init_sets():
    """Creates sets/, migrates old files, seeds a Workshop Set if there are none."""
    SETS_DIR.mkdir(parents=True, exist_ok=True)
    migrate_all()
    if not list_names():
        log("Seeding default 'Workshop' Set...")
        now = now_iso()
        settings = default_settings()
        settings.update({
            "width": 120.0, "height": 40.0, "min_margin": 5.0,
            "base_thickness": 2.0, "text_thickness": 0.8,
        })
        # "Nuts" is the older entry, so it is stored last (newest first).
        signs = [
            {"id": new_id(), "text": t, "created_at": now, "exported_at": None}
            for t in ("Screws", "Nuts")
        ]
        _write(SETS_DIR / "Workshop.json", {
            "schema": SCHEMA, "name": "Workshop", "created_at": now, "updated_at": now,
            "settings": settings, "font_size": None, "exported_settings": None,
            "signs": signs, "exports": [],
        })


# --- Queries -----------------------------------------------------------------

def size_limit(settings, frozen):
    """The size signs are capped at: a user-fixed size wins over the frozen one."""
    return settings.get("text_size") or frozen or None


def set_font_size(data):
    """The Set's current font size: fixed size, frozen value, else the optimum across all signs."""
    limit = size_limit(data["settings"], data.get("font_size"))
    if limit:
        return limit
    return _optimum_for([s["text"] for s in data["signs"]], data["settings"])


def summarize(data):
    signs = data["signs"]
    return {
        "name": data["name"],
        "updated_at": data["updated_at"],
        "settings": data["settings"],
        "font_size": set_font_size(data),
        "sign_count": len(signs),
        "new_count": sum(1 for s in signs if not s.get("exported_at")),
        "preview_text": signs[0]["text"] if signs else data["name"],
        "preview_texts": [s["text"] for s in signs[:12]],
    }


def list_summaries():
    out = []
    with LOCK:
        for n in list_names():
            try:
                out.append(summarize(read_set(n)))
            except ApiError as e:
                log(f"Skipping {n}: {e.message}")
    out.sort(key=lambda s: s["updated_at"], reverse=True)
    return out


# --- Mutations ---------------------------------------------------------------

def _clean_text(value):
    if not isinstance(value, str):
        raise ApiError("Sign text must be text.")
    value = value.strip()
    if not value:
        raise ApiError("Sign text must not be empty.")
    if len(value) > MAX_TEXT_LEN:
        raise ApiError(f"Sign text is too long (max {MAX_TEXT_LEN} characters).")
    return value


def _merge_signs(existing, incoming):
    if not isinstance(incoming, list):
        raise ApiError("signs must be a list.")
    if len(incoming) > MAX_SIGNS:
        raise ApiError(f"Too many signs (max {MAX_SIGNS}).")
    known = {s["id"]: s for s in existing}
    seen = set()
    out = []
    for item in incoming:
        if not isinstance(item, dict):
            raise ApiError("Each sign must be an object.")
        text = _clean_text(item.get("text"))
        sid = item.get("id")
        if not isinstance(sid, str) or not _ID_RE.fullmatch(sid):
            sid = new_id()
        if sid in seen:
            raise ApiError("Duplicate sign id.")
        seen.add(sid)
        old = known.get(sid)
        if old:
            exported_at = old.get("exported_at")
            if exported_at and old["text"] != text:
                exported_at = None  # changed after printing: needs reprinting
            out.append({"id": sid, "text": text,
                        "created_at": old["created_at"], "exported_at": exported_at})
        else:
            # New sign, or one restored by "Undo": the client may carry its timestamps.
            out.append({"id": sid, "text": text,
                        "created_at": _parse_iso(item.get("created_at")) or now_iso(),
                        "exported_at": _parse_iso(item.get("exported_at"))})
    return out


def create_set(payload):
    name = validate_name(payload.get("name"))
    source_name = payload.get("from")
    with LOCK:
        if _name_taken(name):
            raise ApiError("A Set with this name already exists.", 409)
        base = default_settings()
        source = None
        if source_name:
            source = read_set(source_name)
            base = source["settings"]
        settings = validate_settings(payload.get("settings") or {}, base)
        now = now_iso()
        signs = []
        if source and payload.get("copy_signs"):
            signs = [
                {"id": new_id(), "text": s["text"], "created_at": now, "exported_at": None}
                for s in source["signs"]
            ]
        data = {
            "schema": SCHEMA, "name": name, "created_at": now, "updated_at": now,
            "settings": settings, "font_size": None, "exported_settings": None,
            "signs": signs, "exports": [],
        }
        _write(_path(name), data)
        return data


def update_set(name, payload):
    with LOCK:
        data = read_set(name)
        if "settings" in payload:
            data["settings"] = validate_settings(payload["settings"], data["settings"])
        if "signs" in payload:
            data["signs"] = _merge_signs(data["signs"], payload["signs"])
        data["updated_at"] = now_iso()
        _write(_path(name), data)
        return data


def rename_set(name, new_name):
    new_name = validate_name(new_name)
    with LOCK:
        data = read_set(name)
        if new_name == name:
            return data
        if _name_taken(new_name, except_name=name):
            raise ApiError("A Set with this name already exists.", 409)
        data["name"] = new_name
        data["updated_at"] = now_iso()
        _write(_path(new_name), data)
        _path(name).unlink()
        return data


def delete_set(name):
    with LOCK:
        path = _path(name)
        if not path.exists():
            raise ApiError("Set not found.", 404)
        trash = SETS_DIR / TRASH_DIRNAME
        trash.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        os.replace(path, trash / f"{path.stem}_{stamp}.json")


def renew_sign(name, sign_id):
    with LOCK:
        data = read_set(name)
        for s in data["signs"]:
            if s["id"] == sign_id:
                s["exported_at"] = None
                break
        else:
            raise ApiError("Sign not found.", 404)
        data["updated_at"] = now_iso()
        _write(_path(name), data)
        return data


def renew_all(name):
    """Every sign becomes new again and the font size is no longer frozen."""
    with LOCK:
        data = read_set(name)
        for s in data["signs"]:
            s["exported_at"] = None
        data["font_size"] = None
        data["exported_settings"] = None
        data["updated_at"] = now_iso()
        _write(_path(name), data)
        return data


# --- Preview -----------------------------------------------------------------

def preview(raw_settings, texts, frozen=None):
    settings = validate_settings(raw_settings or {}, default_settings())
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ApiError("texts must be a list of strings.")
    if len(texts) > MAX_SIGNS:
        raise ApiError(f"Too many signs (max {MAX_SIGNS}).")
    if frozen is not None:
        frozen = _number(frozen, "font_size")
        if frozen <= 0:
            frozen = None
    font_path = settings["font_path"]
    if not font_path:
        raise ApiError("No font selected.")
    if not os.path.exists(font_path):
        raise ApiError(f"Font file not found: {font_path}")
    aw = settings["width"] - 2 * text_inset(settings)
    ah = settings["height"] - 2 * text_inset(settings)
    frozen = size_limit(settings, frozen)
    plan = fonts.plan_sizes(texts, font_path, aw, ah, frozen)
    r = lambda v: None if v is None else round(v, 3)
    return {
        "font_size": r(plan["set_size"]),
        "optimum": r(plan["optimum"]),
        "limiting_text": plan["limiting_text"],
        "locked": frozen is not None,
        "signs": [
            {"text": t.strip(), "font_size": r(sz), "shrunk": sh}
            for t, sz, sh in zip(texts, plan["sizes"], plan["shrunk"])
        ],
    }


# --- Export ------------------------------------------------------------------

def export_filename(name, scope, count, fmt, when=None):
    when = when or datetime.now()
    safe = name.replace(" ", "_")
    kind = "New" if scope == "new" else "All"
    return f"{safe}_{kind}_{count}-Signs_{when.strftime('%Y-%m-%d_%H-%M-%S')}.{fmt}"


def run_export(name, scope, fmt, output_path, generate, progress=None):
    """Exports signs of a Set. `generate(texts, font_sizes, settings, path, fmt, progress)`
    builds the file. Only after it succeeded are the signs marked as exported."""
    if scope not in ("new", "all"):
        raise ApiError("scope must be 'new' or 'all'.")
    if fmt not in ("3mf", "step"):
        raise ApiError("format must be '3mf' or 'step'.")

    with LOCK:
        snap = read_set(name)
    settings = snap["settings"]
    check_geometry(settings)
    signs = snap["signs"]
    if not signs:
        raise ApiError("This Set has no signs yet.")
    selected = [(i, s) for i, s in enumerate(signs) if scope == "all" or not s.get("exported_at")]
    if not selected:
        raise ApiError("Nothing new to export. Choose 'All' or mark signs as new.")
    font_path = settings["font_path"]
    if not font_path or not os.path.exists(font_path):
        raise ApiError(f"Font file not found: {font_path or '(none selected)'}")

    aw = settings["width"] - 2 * text_inset(settings)
    ah = settings["height"] - 2 * text_inset(settings)
    # Sizes are planned over ALL signs so a partial export matches earlier prints.
    plan = fonts.plan_sizes(
        [s["text"] for s in signs], font_path, aw, ah, size_limit(settings, snap.get("font_size"))
    )

    texts = [s["text"] for _, s in selected]
    sizes = [plan["sizes"][i] for i, _ in selected]
    generate(texts, sizes, settings, str(output_path), fmt, progress)

    exported_ids = {s["id"]: s["text"] for _, s in selected}
    with LOCK:
        try:
            cur = read_set(name)
        except ApiError:
            raise ApiError("The Set was renamed or deleted during the export; nothing was marked.", 409)
        now = now_iso()
        for s in cur["signs"]:
            if exported_ids.get(s["id"]) == s["text"]:
                s["exported_at"] = now
        if not cur.get("font_size") and not settings.get("text_size"):
            cur["font_size"] = plan["set_size"]
        cur["exported_settings"] = dict(settings)
        cur["exports"].append({"at": now, "format": fmt, "scope": scope, "count": len(selected)})
        cur["updated_at"] = now
        _write(_path(name), cur)

    return {
        "count": len(selected),
        "filename": export_filename(name, scope, len(selected), fmt),
        "set": cur,
    }
