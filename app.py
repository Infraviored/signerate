import os
import sys
import traceback
from flask import Flask, render_template, request, jsonify, send_file
from generator import (
    load_settings, save_settings, find_system_fonts, find_arial_path,
    calculate_optimal_font_size, generate_signs, generate_preview_svg
)
import json
import glob
from pathlib import Path

app = Flask(__name__)

# Track the last generated file to serve it correctly in /api/download
LAST_GENERATED = {
    "path": "signs.3mf",
    "mimetype": "model/3mf"
}
PROGRESS = {"current": 0, "total": 0, "active": False}

def log_info(msg):
    print(f"[INFO] {msg}")
    sys.stdout.flush()

def log_debug(msg, data=None):
    print(f"[DEBUG] {msg}")
    if data:
        import json
        try:
            print(json.dumps(data, indent=2))
        except:
            print(data)
    sys.stdout.flush()

def log_error(e):
    print("\n" + "!" * 80)
    print(f"!!! CRITICAL SERVER ERROR: {str(e)}")
    print("!" * 80)
    traceback.print_exc(file=sys.stderr)
    print("!" * 80 + "\n")
    sys.stderr.flush()
    sys.stdout.flush()

@app.route("/")
def index():
    return render_template("index.html",
                           settings=load_settings(),
                           fonts=find_system_fonts())


@app.route("/api/font")
def api_serve_font():
    """Serves a font file from the local system."""
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        return "Font not found", 404
    return send_file(path)


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(force=True)
    settings = load_settings()
    for key, value in data.items():
        if key in settings:
            settings[key] = value
    save_settings(settings)
    return jsonify({"ok": True})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    data     = request.get_json(force=True)
    texts    = data.get("texts", [])
    settings = load_settings()
    
    # Merge incoming settings
    incoming_settings = data.get("settings", {})
    for k, v in incoming_settings.items():
        if k in settings:
            settings[k] = v

    log_debug(f"Preview Request (signs={len(texts)})", {"texts": texts, "settings": incoming_settings})

    font_path = settings.get("font_path", "")
    # Sanitize Windows paths from JS
    if font_path:
        font_path = font_path.replace("/", "\\")
        settings["font_path"] = font_path

    if not font_path:
        log_info("Preview failed: No font_path key in settings.")
        return jsonify({"error": "No font selected in settings."}), 400
    
    if not os.path.exists(font_path):
        log_info(f"Preview failed: Font path does not exist: {font_path}")
        return jsonify({"error": f"Font file not found: {font_path}"}), 400

    clean = [t for t in texts if t.strip()]
    if not clean:
        return jsonify({"error": "Please enter at least one text."}), 400

    available_w = float(settings["width"])  - 2 * float(settings["min_margin"])
    available_h = float(settings["height"]) - 2 * float(settings["min_margin"])

    try:
        import time
        start_t = time.time()
        font_size, limiting_text = calculate_optimal_font_size(clean, font_path, available_w, available_h)
        svg = generate_preview_svg(texts, settings)
        duration = time.time() - start_t
        
        log_info(f"Preview generated in {duration:.2f}s (font_size={font_size:.2f})")
        
        return jsonify({
            "font_size":     round(font_size, 2),
            "limiting_text": limiting_text,
            "sign_count":    len(clean),
            "available_w":  round(available_w, 2),
            "available_h":  round(available_h, 2),
            "svg": svg
        })
    except Exception as e:
        log_error(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data     = request.get_json(force=True)
    texts    = data.get("texts", [])
    fmt      = data.get("format", "3mf").lower()
    settings = load_settings()
    
    incoming_settings = data.get("settings", {})
    for k, v in incoming_settings.items():
        if k in settings:
            settings[k] = v

    log_debug(f"Generate Request ({fmt})", {"settings": incoming_settings})

    font_path = settings.get("font_path", "")
    if font_path:
        font_path = font_path.replace("/", "\\")
        settings["font_path"] = font_path

    if not font_path or not os.path.exists(font_path):
        log_info(f"Generate failed: Invalid font path: {font_path}")
        return jsonify({"error": f"Invalid font path: {font_path}"}), 400
    if not any(t.strip() for t in texts):
        return jsonify({"error": "Please enter at least one text."}), 400

    output_file = f"signs.{fmt}"
    mimetype = "model/3mf" if fmt == "3mf" else "application/step"

    try:
        def on_progress(curr, total):
            PROGRESS["current"] = curr + 1
            PROGRESS["total"] = total
            PROGRESS["active"] = True

        PROGRESS["current"] = 0
        PROGRESS["total"] = len([t for t in texts if t.strip()])
        PROGRESS["active"] = True

        font_size, path = generate_signs(texts, settings, output_file, export_type=fmt, progress_callback=on_progress)
        
        PROGRESS["active"] = False
        LAST_GENERATED["path"] = path
        LAST_GENERATED["mimetype"] = mimetype
        
        return jsonify({
            "ok":         True,
            "font_size":  round(font_size, 2),
            "sign_count": len([t for t in texts if t.strip()]),
            "filename":   os.path.basename(path)
        })
    except Exception as e:
        log_error(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/progress")
def api_progress():
    return jsonify(PROGRESS)

@app.route("/api/download")
def api_download():
    path = LAST_GENERATED["path"]
    if not os.path.exists(path):
        return jsonify({"error": "No file generated yet."}), 404
        
    return send_file(
        os.path.abspath(path),
        as_attachment=True,
        download_name=os.path.basename(path),
        mimetype=LAST_GENERATED["mimetype"],
    )


# --- Set Management ---

SETS_DIR = Path("sets")

def init_sets():
    """Seeds the Workshop Signs set if no sets exist."""
    if not SETS_DIR.exists():
        SETS_DIR.mkdir()
    
    existing = list(SETS_DIR.glob("*.json"))
    if not existing:
        log_info("Seeding default 'Workshop' set...")
        default_set = {
            "name": "Workshop",
            "settings": {
                "width": 120,
                "height": 40,
                "min_margin": 5.0,
                "base_thickness": 2.0,
                "text_thickness": 0.8,
                "font_path": find_arial_path(),
                "bg_color": "#000000",
                "text_color": "#ffffff",
                "export_format": "3mf"
            },
            "texts": ["Screws", "Nuts"]
        }
        with open(SETS_DIR / "Workshop.json", "w", encoding="utf-8") as f:
            json.dump(default_set, f, indent=4, ensure_ascii=False)

@app.route("/api/sets", methods=["GET"])
def api_list_sets():
    sets = []
    for p in SETS_DIR.glob("*.json"):
        sets.append(p.stem)
    return jsonify(sorted(sets))

@app.route("/api/sets/<name>", methods=["GET"])
def api_get_set(name):
    path = SETS_DIR / f"{name}.json"
    if not path.exists():
        return jsonify({"error": "Set not found"}), 404
    with open(path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

@app.route("/api/sets", methods=["POST"])
def api_save_set():
    data = request.get_json(force=True)
    name = data.get("name", "Unnamed Set")
    # Sanitize just in case
    safe_name = "".join([c for c in name if c.isalnum() or c in (' ', '_', '-')]).strip()
    if not safe_name: safe_name = "Unnamed Set"
    
    path = SETS_DIR / f"{safe_name}.json"
    log_info(f"Saving set: {path}")
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    
    return jsonify({"ok": True, "name": safe_name})


if __name__ == "__main__":
    init_sets()
    app.run(debug=True, port=5000)
