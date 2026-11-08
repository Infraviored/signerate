import os
import sys
import tempfile
import threading
import traceback
import uuid

from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

import fonts
import nextcloud
import outlines
import sets_store
from sets_store import ApiError

app = Flask(__name__)

EXPORT_DIR = os.path.join(tempfile.gettempdir(), "signerate-exports")
EXPORT_LOCK = threading.Lock()  # one export at a time (PROGRESS and LAST_EXPORT are global)
PROGRESS = {"current": 0, "total": 0, "active": False}
LAST_EXPORT = None  # {token, path, mimetype, filename, set, count}

MIMETYPES = {"3mf": "model/3mf", "step": "application/step"}


def generate_signs(texts, font_sizes, settings, path, fmt, progress):
    """Geometry entry point; imported lazily so CadQuery loads only when exporting."""
    from generator import FrameError, generate_signs as _generate
    try:
        return _generate(texts, font_sizes, settings, path, export_type=fmt, progress_callback=progress)
    except FrameError as e:
        raise ApiError(str(e))


def log_error(e):
    print(f"[ERROR] {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.stderr.flush()


def json_body():
    data = request.get_json(silent=True, force=True)
    if not isinstance(data, dict):
        raise ApiError("Request body must be a JSON object.")
    return data


@app.errorhandler(ApiError)
def handle_api_error(e):
    return jsonify({"error": e.message}), e.status


@app.errorhandler(Exception)
def handle_unexpected(e):
    if isinstance(e, HTTPException):
        if request.path.startswith("/api/"):
            return jsonify({"error": e.description}), e.code
        return e
    log_error(e)
    return jsonify({"error": f"Server error: {e}"}), 500


# --- Pages -------------------------------------------------------------------

@app.route("/")
def page_overview():
    return render_template("index.html")


@app.route("/set/<name>")
def page_editor(name):
    try:
        data = sets_store.read_set(name)
    except ApiError:
        abort(404)
    return render_template("editor.html", name=data["name"])


# --- Fonts -------------------------------------------------------------------

@app.route("/api/fonts")
def api_fonts():
    return jsonify(fonts.find_system_fonts())


@app.route("/api/font")
def api_serve_font():
    """Serves a font file from the local system."""
    path = request.args.get("path")
    if not path or not os.path.exists(path):
        return "Font not found", 404
    return send_file(path)


@app.route("/api/defaults")
def api_defaults():
    return jsonify(sets_store.default_settings())


@app.route("/api/shapes")
def api_shapes():
    """Shape catalog for the picker: ids, names, parameters and a thumbnail path each."""
    return jsonify(outlines.catalog())


# --- Sets --------------------------------------------------------------------

@app.route("/api/sets", methods=["GET"])
def api_list_sets():
    return jsonify(sets_store.list_summaries())


@app.route("/api/sets", methods=["POST"])
def api_create_set():
    return jsonify(sets_store.present(sets_store.create_set(json_body()))), 201


@app.route("/api/sets/<name>", methods=["GET"])
def api_get_set(name):
    return jsonify(sets_store.present(sets_store.read_set(name)))


@app.route("/api/sets/<name>", methods=["PUT"])
def api_update_set(name):
    return jsonify(sets_store.present(sets_store.update_set(name, json_body())))


@app.route("/api/sets/<name>", methods=["DELETE"])
def api_delete_set(name):
    sets_store.delete_set(name)
    return jsonify({"ok": True})


@app.route("/api/sets/<name>/rename", methods=["POST"])
def api_rename_set(name):
    data = sets_store.rename_set(name, json_body().get("name"))
    if LAST_EXPORT and LAST_EXPORT["set"] == name:
        LAST_EXPORT["set"] = data["name"]
    return jsonify(sets_store.present(data))


@app.route("/api/sets/<name>/renew-all", methods=["POST"])
def api_renew_all(name):
    return jsonify(sets_store.present(sets_store.renew_all(name)))


@app.route("/api/sets/<name>/signs/<sign_id>/renew", methods=["POST"])
def api_renew_sign(name, sign_id):
    return jsonify(sets_store.present(sets_store.renew_sign(name, sign_id)))


# --- Preview -----------------------------------------------------------------

@app.route("/api/preview", methods=["POST"])
def api_preview():
    data = json_body()
    return jsonify(sets_store.preview(
        data.get("settings"), data.get("texts", []), data.get("font_size")
    ))


# --- Export ------------------------------------------------------------------

@app.route("/api/sets/<name>/export", methods=["POST"])
def api_export(name):
    global LAST_EXPORT
    data = json_body()
    scope = data.get("scope", "new")
    fmt = str(data.get("format", "3mf")).lower()
    destination = data.get("destination", "download")
    if destination not in ("download", "nextcloud"):
        raise ApiError("destination must be 'download' or 'nextcloud'.")
    if destination == "nextcloud" and not nextcloud.is_configured():
        raise ApiError("Nextcloud is not configured on this server.")
    if not EXPORT_LOCK.acquire(blocking=False):
        raise ApiError("Another export is running. Try again in a moment.", 409)
    path = None
    try:
        os.makedirs(EXPORT_DIR, exist_ok=True)
        path = os.path.join(EXPORT_DIR, f"export-{uuid.uuid4().hex}.{fmt if fmt in MIMETYPES else 'bin'}")

        def on_progress(curr, total):
            PROGRESS.update(current=curr + 1, total=total, active=True)

        PROGRESS.update(current=0, total=0, active=True)
        def to_nextcloud(file_path, filename):
            try:
                return nextcloud.upload(file_path, filename)
            except nextcloud.UploadError as e:
                raise ApiError(str(e), 502)

        deliver = to_nextcloud if destination == "nextcloud" else None
        result = sets_store.run_export(name, scope, fmt, path, generate_signs, on_progress, deliver)
        PROGRESS.update(current=result["count"], total=result["count"])

        old = LAST_EXPORT
        LAST_EXPORT = {
            "token": uuid.uuid4().hex,
            "path": path,
            "mimetype": MIMETYPES[fmt],
            "filename": result["filename"],
            "set": result["set"]["name"],
            "count": result["count"],
        }
        if old and old["path"] != path and os.path.exists(old["path"]):
            os.remove(old["path"])
        path = None  # keep the file
        return jsonify({
            "ok": True,
            "count": result["count"],
            "filename": result["filename"],
            "token": LAST_EXPORT["token"],
            "set": result["set"],
        })
    except ApiError:
        raise
    except Exception as e:
        log_error(e)
        raise ApiError(f"Generation failed: {e}", 500)
    finally:
        PROGRESS["active"] = False
        if path and os.path.exists(path):
            os.remove(path)  # failed export: no partial file left behind
        EXPORT_LOCK.release()


@app.route("/api/export-targets")
def api_export_targets():
    return jsonify({"nextcloud": nextcloud.is_configured(), "folder": nextcloud.folder()})


@app.route("/api/progress")
def api_progress():
    return jsonify(PROGRESS)


@app.route("/api/last-export")
def api_last_export():
    if not LAST_EXPORT or not os.path.exists(LAST_EXPORT["path"]):
        return jsonify(None)
    return jsonify({k: LAST_EXPORT[k] for k in ("token", "filename", "set", "count")})


@app.route("/api/download")
def api_download():
    """Serves only the file of the last export, under the name the server chose."""
    last = LAST_EXPORT
    if not last or not os.path.exists(last["path"]):
        raise ApiError("No export available to download.", 404)
    if request.args.get("id") != last["token"]:
        raise ApiError("This export has been replaced by a newer one.", 404)
    return send_file(
        last["path"],
        as_attachment=True,
        download_name=last["filename"],
        mimetype=last["mimetype"],
    )


sets_store.init_sets()

if __name__ == "__main__":
    app.run(debug=True, port=15000)
