import pytest

import nextcloud
import sets_store
from test_sets import FakeGenerator, export, make_set, store  # noqa: F401


@pytest.fixture
def configured(monkeypatch):
    for key, value in {"URL": "https://c.example", "USER": "u", "PASSWORD": "p", "FOLDER": "/A B/C/"}.items():
        monkeypatch.setenv(f"NEXTCLOUD_{key}", value)


def test_not_configured_without_every_value(configured, monkeypatch):
    assert nextcloud.is_configured()
    monkeypatch.setenv("NEXTCLOUD_PASSWORD", " ")
    assert not nextcloud.is_configured()


def test_upload_numbers_a_taken_name(configured, monkeypatch, tmp_path):
    taken = 2
    urls = []

    def fake_put(url, path, auth):
        urls.append(url)
        return len(urls) > taken

    monkeypatch.setattr(nextcloud, "_put", fake_put)
    assert nextcloud.upload(str(tmp_path / "f"), "Set-Hi-80x30mm.3mf") == "Set-Hi-80x30mm-3.3mf"
    assert urls[0] == "https://c.example/remote.php/dav/files/u/A%20B/C/Set-Hi-80x30mm.3mf"


def test_upload_gives_up_when_every_name_is_taken(configured, monkeypatch, tmp_path):
    monkeypatch.setattr(nextcloud, "_put", lambda *a: False)
    with pytest.raises(nextcloud.UploadError):
        nextcloud.upload(str(tmp_path / "f"), "x.3mf")


def test_deliver_gets_the_file_and_may_rename_it(store, tmp_path):  # noqa: F811
    make_set("T", ["Hi"])
    seen = []

    def deliver(path, filename):
        seen.append((path, filename))
        return "renamed.3mf"

    result = sets_store.run_export("T", "new", "3mf", tmp_path / "out.bin", FakeGenerator(), None, deliver)
    assert seen == [(str(tmp_path / "out.bin"), seen[0][1])]
    assert seen[0][1].startswith("T-Hi-")
    assert result["filename"] == "renamed.3mf"
    assert sets_store.read_set("T")["signs"][0]["exported_at"]


def test_failed_delivery_marks_nothing(store, tmp_path):  # noqa: F811
    make_set("T", ["Hi"])

    def deliver(path, filename):
        raise sets_store.ApiError("no upload", 502)

    with pytest.raises(sets_store.ApiError):
        sets_store.run_export("T", "new", "3mf", tmp_path / "out.bin", FakeGenerator(), None, deliver)
    data = sets_store.read_set("T")
    assert data["signs"][0]["exported_at"] is None
    assert data["exports"] == []


def test_export_without_deliver_still_works(store, tmp_path):  # noqa: F811
    make_set("T", ["Hi"])
    assert export("T", "new", FakeGenerator(), tmp_path)["filename"].startswith("T-Hi-")
