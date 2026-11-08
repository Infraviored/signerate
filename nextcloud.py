"""Uploads an export to Nextcloud over WebDAV. Configured through the environment
(NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASSWORD, NEXTCLOUD_FOLDER); see .env.example."""
import base64
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 180
MAX_NAME_TRIES = 200


class UploadError(Exception):
    pass


def _config():
    return {k: os.environ.get(f"NEXTCLOUD_{k.upper()}", "").strip() for k in ("url", "user", "password", "folder")}


def is_configured():
    return all(_config().values())


def folder():
    return _config()["folder"].strip("/")


def _put(url, path, auth):
    """True when stored; False when the name is already taken."""
    with open(path, "rb") as body:
        req = urllib.request.Request(url, data=body, method="PUT", headers={
            "Authorization": auth,
            "Content-Length": str(os.path.getsize(path)),
            "Content-Type": "application/octet-stream",
            "If-None-Match": "*",  # never overwrite
        })
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT):
                return True
        except urllib.error.HTTPError as e:
            if e.code == 412:
                return False
            if e.code in (401, 403):
                raise UploadError("Nextcloud refused the login. Check the configured password.")
            if e.code in (404, 409):
                raise UploadError(f'The Nextcloud folder "{folder()}" does not exist.')
            raise UploadError(f"Nextcloud answered with error {e.code}.")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise UploadError(f"Cannot reach Nextcloud: {getattr(e, 'reason', e)}")


def upload(path, filename):
    """Stores the file under `filename`, or `name-2.ext` etc. if taken. Returns the name used."""
    cfg = _config()
    token = base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    base = (
        f"{cfg['url'].rstrip('/')}/remote.php/dav/files/{urllib.parse.quote(cfg['user'])}"
        f"/{urllib.parse.quote(folder())}"
    )
    stem, ext = os.path.splitext(filename)
    for n in range(1, MAX_NAME_TRIES + 1):
        candidate = filename if n == 1 else f"{stem}-{n}{ext}"
        if _put(f"{base}/{urllib.parse.quote(candidate)}", path, f"Basic {token}"):
            return candidate
    raise UploadError("Too many files with this name in the Nextcloud folder.")
