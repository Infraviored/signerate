import os
import sys
import traceback
from flask import Flask, render_template, request, jsonify, send_file
from generator import (
    load_settings, save_settings, find_system_fonts,
    calculate_optimal_font_size, generate_signs, generate_preview_svg
)

app = Flask(__name__)

# Track the last generated file to serve it correctly in /api/download
LAST_GENERATED = {
    "path": "signs.3mf",
    "mimetype": "model/3mf"
}

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
    settings.update({k: v for k, v in data.get("settings", {}).items() if k in settings})

    font_path = settings.get("font_path", "")
    if not font_path or not os.path.exists(font_path):
        return jsonify({"error": "No valid font selected."}), 400

    clean = [t for t in texts if t.strip()]
    if not clean:
        return jsonify({"error": "Please enter at least one text."}), 400

    available_w = settings["width"]  - 2 * settings["min_margin"]
    available_h = settings["height"] - 2 * settings["min_margin"]

    try:
        font_size = calculate_optimal_font_size(clean, font_path, available_w, available_h)
        svg = generate_preview_svg(texts, settings)
        
        return jsonify({
            "font_size":   round(font_size, 2),
            "sign_count":  len(clean),
            "available_w": round(available_w, 2),
            "available_h": round(available_h, 2),
            "svg": svg
        })
    except Exception as e:
        log_error(e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data     = request.get_json(force=True)
    texts    = data.get("texts", [])
    fmt      = data.get("format", "3mf").lower() # "3mf" or "step"
    settings = load_settings()
    settings.update({k: v for k, v in data.get("settings", {}).items() if k in settings})

    font_path = settings.get("font_path", "")
    if not font_path or not os.path.exists(font_path):
        return jsonify({"error": "No valid font selected."}), 400
    if not any(t.strip() for t in texts):
        return jsonify({"error": "Please enter at least one text."}), 400

    output_file = f"signs.{fmt}"
    mimetype = "model/3mf" if fmt == "3mf" else "application/step"

    try:
        font_size, path = generate_signs(texts, settings, output_file, export_type=fmt)
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
