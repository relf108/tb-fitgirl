import pytest
import respx
from httpx import Response

from tb_fitgirl.downloader import Downloader, safe_relpath
from tb_fitgirl.models import Torrent, TorrentFile
from tb_fitgirl.torbox import MAIN_API, TorboxClient, TorboxError

TORRENT_RAW = {
    "id": 42,
    "hash": "abcd",
    "name": "PRAGMATA [FitGirl Repack]",
    "size": 100,
    "download_state": "cached",
    "download_present": True,
    "download_finished": True,
    "progress": 1,
    "files": [
        {"id": 0, "name": "PRAGMATA/setup.exe", "size": 11, "short_name": "setup.exe"},
        {"id": 1, "name": "PRAGMATA/data/archive.bin", "size": 5, "short_name": "archive.bin"},
    ],
}


def test_safe_relpath():
    assert safe_relpath("Game/setup.exe").as_posix() == "Game/setup.exe"
    assert safe_relpath("/abs/path.bin").as_posix() == "abs/path.bin"
    assert safe_relpath("a/../../etc/passwd").as_posix() == "a/etc/passwd"
    with pytest.raises(ValueError):
        safe_relpath("..")


def _mock_mylist(raw: dict) -> None:
    respx.get(f"{MAIN_API}/torrents/mylist").mock(
        return_value=Response(200, json={"success": True, "data": [raw]})
    )


def _mock_link_and_content(file_id: int, content: bytes) -> None:
    url = f"https://cdn.example.com/f{file_id}"
    respx.get(f"{MAIN_API}/torrents/requestdl", params={"file_id": str(file_id)}).mock(
        return_value=Response(200, json={"success": True, "data": url})
    )
    respx.get(url).mock(
        return_value=Response(200, content=content, headers={"Content-Length": str(len(content))})
    )


@respx.mock
def test_download_torrent(tmp_path):
    _mock_mylist(TORRENT_RAW)
    _mock_link_and_content(0, b"exe-content!")  # 12 != declared 11, size only used for skip
    _mock_link_and_content(1, b"data!")

    with TorboxClient(api_key="k") as tb, Downloader(tb, tmp_path) as dl:
        torrent = dl.wait_ready(42)
        assert torrent.name.startswith("PRAGMATA")
        progress_calls = []
        paths = dl.download_torrent(
            torrent, on_progress=lambda f, d, t: progress_calls.append((f.id, d, t))
        )

    assert (tmp_path / "PRAGMATA/setup.exe").read_bytes() == b"exe-content!"
    assert (tmp_path / "PRAGMATA/data/archive.bin").read_bytes() == b"data!"
    assert len(paths) == 2
    assert progress_calls  # progress reported
    assert not list(tmp_path.rglob("*.part"))  # no leftover temp files


@respx.mock
def test_download_skips_complete_file(tmp_path):
    target = tmp_path / "PRAGMATA/data/archive.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"12345")  # matches declared size 5

    file = TorrentFile(id=1, name="PRAGMATA/data/archive.bin", size=5)
    torrent = Torrent(id=42, hash="abcd", name="x", files=[file])
    with TorboxClient(api_key="k") as tb, Downloader(tb, tmp_path) as dl:
        path = dl.download_file(torrent, file)  # no HTTP mocked: would fail if hit
    assert path == target
    assert target.read_bytes() == b"12345"


@respx.mock
def test_wait_ready_timeout(tmp_path):
    raw = dict(TORRENT_RAW, download_present=False, download_state="downloading")
    _mock_mylist(raw)
    with TorboxClient(api_key="k") as tb, Downloader(tb, tmp_path) as dl:
        with pytest.raises(TorboxError, match="Timed out"):
            dl.wait_ready(42, timeout=0.01, interval=0.01)


@respx.mock
def test_request_download_link(tmp_path):
    route = respx.get(f"{MAIN_API}/torrents/requestdl").mock(
        return_value=Response(200, json={"success": True, "data": "https://cdn.example.com/x"})
    )
    with TorboxClient(api_key="secret-key") as tb:
        link = tb.request_download_link(42, file_id=7)
    assert link == "https://cdn.example.com/x"
    params = route.calls[0].request.url.params
    assert params["token"] == "secret-key"
    assert params["torrent_id"] == "42"
    assert params["file_id"] == "7"


@respx.mock
def test_my_list_parses():
    _mock_mylist(TORRENT_RAW)
    with TorboxClient(api_key="k") as tb:
        torrents = tb.my_list()
    assert len(torrents) == 1
    t = torrents[0]
    assert t.id == 42 and t.ready and len(t.files) == 2
    assert t.files[0].short_name == "setup.exe"
