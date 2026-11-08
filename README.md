# Signerate - Sign Generator

Signerate is a small Flask app for generating 3D printable signs (3MF or STEP) with CadQuery.
Signs are organised in **Sets**: a Set is a list of sign texts plus the dimensions and style they
share. Everything autosaves; there is no Save button.

## Workflow

**Sign Sets (`/`)** lists every Set, most recently updated first, with a preview in its own style,
its size, the number of signs and how many are new. Click a card to open it. "Duplicate as new
Set..." and "+ New Set" start a two-step wizard:

1. Pick a starting point: blank, or an existing Set (optionally copying its signs; copied signs
   count as new).
2. Give it a unique name and adjust dimensions and style with a live preview. Values are copied,
   the Sets are independent afterwards.

Deleting a Set moves its file to `sets/_trash/`.

**Editor (`/set/<name>`)**

- Type a sign into the top field and press Enter: it is added at the top of the list. Pasting
  several lines adds one sign per line.
- Each sign shows `NEW` or `exported <date>`. "Mark as new" puts an exported sign back into the
  new pile. Editing the text of an exported sign makes it new again, since it needs reprinting.
  Deleting shows an "Undo" toast.
- "Dimensions & Style" opens a side panel (width, height, thicknesses, margin, protrusion, colours,
  font, alignment) and shows the Set font size and the sign that limits it.
- The export bar always shows "New only (n)" (the default every time the page loads) and
  "All (n)". The server reads the saved Set, builds the file and only then marks the exported
  signs, so a failed export marks nothing. The download starts automatically; "Download again"
  stays available until another export replaces the file.
- If dimensions or style differ from the last export, a banner offers "Mark all as new".
- Renaming the Set (click the name) renames its file.

### Font size rule

While nothing has been exported, the Set font size is the best fit across **all** its signs. At the
first export it is frozen to that value (over all signs, not only the exported ones), so a later
"new only" export matches the signs printed earlier. A frozen Set renders each sign at
`min(frozen size, its own best fit)`; a sign that would not fit at the frozen size is shown with a
`shrunk` badge. "Mark all as new" clears every export mark and unfreezes the size. Preview and
export use the same sizing function (`fonts.plan_sizes`).

## Data

One file per Set: `sets/<name>.json` (newest sign first).

```json
{
  "schema": 2, "name": "Sortierer", "created_at": "...", "updated_at": "...",
  "settings": { "width": 63, "height": 11, "min_margin": 0.2, "base_thickness": 0.4,
                "text_thickness": 0.2, "text_protrusion": 0, "bg_color": "#000000",
                "text_color": "#ffffff", "font_path": "/usr/share/fonts/...ttf",
                "horizontal_align": "center" },
  "font_size": null,
  "exported_settings": null,
  "signs": [ { "id": "hex", "text": "Projekt", "created_at": "...", "exported_at": null } ],
  "exports": [ { "at": "...", "format": "3mf", "scope": "new", "count": 3 } ]
}
```

- `font_size` is `null` until the first export, then the frozen size.
- `exported_settings` is the settings snapshot of the last export (drives the "changed" banner).
- Set names: letters (umlauts included), digits, space, `_` and `-`, up to 60 characters. `_trash`
  and `_backup_v1` are reserved.
- `settings.json` in the project root is only a **read-only default** for new Sets; nothing writes it.
- Files are written atomically (temp file and `os.replace`) under a lock.

### Migration

At startup, every `sets/*.json` that is not yet `schema: 2` is converted once (idempotent). The
original is first copied unchanged to `sets/_backup_v1/`. Old `texts` (oldest first) become `signs`
(newest first). If the old file had `versions`, all signs count as exported on the date of the last
version, the font size is frozen and `exported_settings` is set; without `versions` all signs are
new. If `sets/` is empty, a "Workshop" Set is seeded.

## HTTP API

| Route | Purpose |
|---|---|
| `GET /api/sets`, `POST /api/sets` | summaries; create `{name, from?, copy_signs?, settings?}` |
| `GET/PUT/DELETE /api/sets/<name>` | read; autosave `{settings?, signs?}`; move to trash |
| `POST /api/sets/<name>/rename` | `{name}` |
| `POST /api/sets/<name>/renew-all` | every sign new, font size unfrozen |
| `POST /api/sets/<name>/signs/<id>/renew` | one sign new |
| `POST /api/preview` | `{settings, texts, font_size?}` returns per-sign size and `shrunk` |
| `POST /api/sets/<name>/export` | `{scope: "new"\|"all", format: "3mf"\|"step"}` |
| `GET /api/progress`, `/api/last-export`, `/api/download?id=...` | export progress and download |

Errors are JSON `{"error": "..."}` with a 4xx/5xx status. Export filenames look like
`<Set>_New|All_<n>-Signs_<YYYY-MM-DD_HH-MM-SS>.<ext>`.

## Docker Deployment

```bash
docker compose up -d --build
```

- **Port**: 15000 on the host. Memory limit 1024 MB. Gunicorn runs a single worker with 4 threads.
- **Volumes**: `./sets` (Set files), `./settings.json` (default values for new Sets), the
  templates and static folders, and the system fonts (read-only).

## Local development

```bash
pip install -r requirements.txt
python app.py            # port 15000
```

Tests (no CadQuery needed, geometry is faked; Pillow and the Liberation fonts are):

```bash
pip install pytest && python -m pytest tests -q
```

## NGINX integration

Behind the Infraviored NGINX proxy, authentication is handled via the `auth_type_dynamic` map.
Access typically requires `?auth=SECRET` in the URL or Basic Auth credentials.
