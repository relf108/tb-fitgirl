import io
import json

import pytest
import respx
from httpx import Response

from tb_fitgirl import bridge
from tb_fitgirl.scrapers.fitgirl import BASE_URL
from tb_fitgirl.torbox import MAIN_API

HASH = "abcdef1234567890abcdef1234567890abcdef12"
MAGNET = f"magnet:?xt=urn:btih:{HASH}&dn=Pragmata"

SEARCH_HTML = """
<html><body><article>
  <h1 class="entry-title"><a href="https://fitgirl-repacks.site/pragmata/">PRAGMATA</a></h1>
</article></body></html>
"""

POST_HTML = f"""
<html><body><ul><li><a href="{MAGNET}">magnet</a></li></ul></body></html>
"""


def _mock_scrape(query: str) -> None:
    respx.get(BASE_URL, params={"s": query}).mock(return_value=Response(200, text=SEARCH_HTML))
    respx.get("https://fitgirl-repacks.site/pragmata/").mock(
        return_value=Response(200, text=POST_HTML)
    )


def _mock_checkcached(cached: bool) -> None:
    data = {HASH: {"name": "PRAGMATA", "size": 40 * 1024**3, "hash": HASH}} if cached else {}
    respx.get(f"{MAIN_API}/torrents/checkcached").mock(
        return_value=Response(200, json={"success": True, "data": data})
    )


def run_bridge(requests, monkeypatch, capsys):
    """Feed JSON requests through bridge.main() and return the parsed events."""
    lines = "\n".join(json.dumps(r) for r in requests) + "\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(lines))
    assert bridge.main() == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line]


def events_of(events, kind):
    return [e for e in events if e["event"] == kind]


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")


def test_invalid_json_line(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json\n[1,2]\n"))
    assert bridge.main() == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["data"]["code"] for e in events] == ["BAD_REQUEST", "BAD_REQUEST"]


def test_unknown_op(monkeypatch, capsys):
    events = run_bridge([{"id": 1, "op": "bogus"}], monkeypatch, capsys)
    assert events[0]["event"] == "error"
    assert events[0]["data"]["code"] == "UNKNOWN_OP"
    assert events[0]["id"] == 1


def test_status(monkeypatch, capsys):
    from tb_fitgirl import steam

    monkeypatch.setattr(steam, "steam_running", lambda: True)
    events = run_bridge([{"id": 1, "op": "status"}], monkeypatch, capsys)
    result = events_of(events, "result")[0]
    assert result["data"]["steam_running"] is True
    assert "fitgirl" in result["data"]["sources"]


@respx.mock
def test_validate_key(monkeypatch, capsys):
    respx.get(f"{MAIN_API}/user/me").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {"email": "a@b.c", "plan": 2, "premium_expires_at": "2027-01-01"},
            },
        )
    )
    events = run_bridge(
        [{"id": 1, "op": "validate_key", "args": {"api_key": "abc"}}], monkeypatch, capsys
    )
    data = events_of(events, "result")[0]["data"]
    assert data == {
        "email": "a@b.c",
        "plan": 2,
        "plan_name": "Pro",
        "expiry": "2027-01-01",
    }


@respx.mock
def test_validate_key_bad(monkeypatch, capsys):
    respx.get(f"{MAIN_API}/user/me").mock(return_value=Response(403, json={}))
    events = run_bridge([{"id": 1, "op": "validate_key"}], monkeypatch, capsys)
    error = events_of(events, "error")[0]
    assert "Authentication" in error["data"]["message"]


@respx.mock
def test_search(monkeypatch, capsys):
    _mock_scrape("pragmata")
    _mock_checkcached(cached=True)
    events = run_bridge(
        [{"id": 1, "op": "search", "args": {"title": "pragmata"}}], monkeypatch, capsys
    )
    assert events_of(events, "progress")  # scrape/cache phases emitted
    repacks = events_of(events, "result")[0]["data"]["repacks"]
    assert len(repacks) == 1
    assert repacks[0]["title"] == "PRAGMATA"
    assert repacks[0]["cached"] is True
    assert repacks[0]["size_human"] == "40.0 GB"
    assert repacks[0]["magnet"] == MAGNET


@respx.mock
def test_search_no_results(monkeypatch, capsys):
    respx.get(BASE_URL, params={"s": "zzz"}).mock(
        return_value=Response(200, text="<html><body></body></html>")
    )
    events = run_bridge([{"id": 1, "op": "search", "args": {"title": "zzz"}}], monkeypatch, capsys)
    assert events_of(events, "result")[0]["data"]["repacks"] == []


@respx.mock
def test_cache_by_title(monkeypatch, capsys):
    _mock_scrape("pragmata")
    _mock_checkcached(cached=True)
    respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(200, json={"success": True, "data": {"torrent_id": 1, "hash": HASH}})
    )
    events = run_bridge(
        [{"id": 1, "op": "cache", "args": {"target": "pragmata"}}], monkeypatch, capsys
    )
    data = events_of(events, "result")[0]["data"]
    assert data["cached"] is True


@respx.mock
def test_download_scrapes_and_streams_progress(monkeypatch, capsys, tmp_path):
    _mock_scrape("pragmata")
    _mock_checkcached(cached=True)
    torrent_raw = {
        "id": 42,
        "hash": HASH,
        "name": "PRAGMATA [FitGirl Repack]",
        "size": 5,
        "download_state": "cached",
        "download_present": True,
        "files": [{"id": 0, "name": "PRAGMATA/setup.exe", "size": 5, "short_name": "setup.exe"}],
    }
    respx.get(f"{MAIN_API}/torrents/mylist").mock(
        side_effect=[
            Response(200, json={"success": True, "data": []}),
            Response(200, json={"success": True, "data": [torrent_raw]}),
        ]
    )
    respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(200, json={"success": True, "data": {"torrent_id": 42, "hash": HASH}})
    )
    respx.get(f"{MAIN_API}/torrents/requestdl").mock(
        return_value=Response(200, json={"success": True, "data": "https://cdn.example.com/f0"})
    )
    respx.get("https://cdn.example.com/f0").mock(return_value=Response(200, content=b"12345"))

    events = run_bridge(
        [{"id": 7, "op": "download", "args": {"target": "pragmata", "dest": str(tmp_path)}}],
        monkeypatch,
        capsys,
    )
    result = events_of(events, "result")[0]
    assert result["id"] == 7
    assert result["data"]["path"] == str(tmp_path / "PRAGMATA")
    assert (tmp_path / "PRAGMATA/setup.exe").read_bytes() == b"12345"

    progress = events_of(events, "progress")
    phases = {p["data"]["phase"] for p in progress}
    assert "scrape" in phases
    assert "download" in phases
    final = [p["data"] for p in progress if p["data"]["phase"] == "download" and p["data"]["total"]]
    assert final[-1]["done"] == final[-1]["total"] == 5


@respx.mock
def test_download_numeric_id_never_scrapes(monkeypatch, capsys):
    respx.get(f"{MAIN_API}/torrents/mylist").mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    events = run_bridge(
        [{"id": 1, "op": "download", "args": {"target": "12345"}}], monkeypatch, capsys
    )
    error = events_of(events, "error")[0]
    assert "12345 not found" in error["data"]["message"]


def _fake_steam_library(tmp_path, monkeypatch):
    from tb_fitgirl import steam

    common = tmp_path / "Steam" / "steamapps" / "common"
    common.mkdir(parents=True)
    monkeypatch.setattr(steam, "common_dir", lambda: common)
    monkeypatch.setattr(steam, "steam_running", lambda: False)
    return common


def test_uninstall(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    common = _fake_steam_library(tmp_path, monkeypatch)
    game = common / "DELTARUNE"
    game.mkdir()
    monkeypatch.setattr(steam, "remove_shortcut", lambda name: 123)
    monkeypatch.setattr("tb_fitgirl.bridge.remove_desktop_entry", lambda name: True)

    events = run_bridge(
        [{"id": 1, "op": "uninstall", "args": {"target": "deltarune"}}], monkeypatch, capsys
    )
    data = events_of(events, "result")[0]["data"]
    assert data["deleted"] is True
    assert data["removed_shortcut"] is True
    assert not game.exists()


def test_uninstall_keep_files(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    common = _fake_steam_library(tmp_path, monkeypatch)
    game = common / "DELTARUNE"
    game.mkdir()
    monkeypatch.setattr(steam, "remove_shortcut", lambda name: None)
    monkeypatch.setattr("tb_fitgirl.bridge.remove_desktop_entry", lambda name: False)

    events = run_bridge(
        [{"id": 1, "op": "uninstall", "args": {"target": "deltarune", "keep_files": True}}],
        monkeypatch,
        capsys,
    )
    data = events_of(events, "result")[0]["data"]
    assert data["deleted"] is False
    assert game.exists()


def test_uninstall_refuses_outside_steam(tmp_path, monkeypatch, capsys):
    _fake_steam_library(tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere" / "Game"
    outside.mkdir(parents=True)
    events = run_bridge(
        [{"id": 1, "op": "uninstall", "args": {"target": str(outside)}}], monkeypatch, capsys
    )
    assert "Refusing to delete" in events_of(events, "error")[0]["data"]["message"]
    assert outside.exists()


def test_steam_add(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    common = _fake_steam_library(tmp_path, monkeypatch)
    game = common / "DELTARUNE"
    game.mkdir()
    (game / "DELTARUNE.exe").write_bytes(b"MZ" + b"\0" * 100)
    monkeypatch.setattr(steam, "add_shortcut", lambda name, exe: 999)
    monkeypatch.setattr("tb_fitgirl.bridge.write_desktop_entry", lambda name, appid: game / "e")

    events = run_bridge(
        [{"id": 1, "op": "steam_add", "args": {"target": "deltarune"}}], monkeypatch, capsys
    )
    data = events_of(events, "result")[0]["data"]
    assert data["appid"] == 999
    assert data["name"] == "DELTARUNE"


def test_steam_add_refuses_when_steam_running(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    common = _fake_steam_library(tmp_path, monkeypatch)
    game = common / "DELTARUNE"
    game.mkdir()
    (game / "DELTARUNE.exe").write_bytes(b"MZ" + b"\0" * 100)
    monkeypatch.setattr(steam, "steam_running", lambda: True)

    events = run_bridge(
        [{"id": 1, "op": "steam_add", "args": {"target": "deltarune"}}], monkeypatch, capsys
    )
    assert "Steam is running" in events_of(events, "error")[0]["data"]["message"]


def _prep_install(tmp_path, monkeypatch, *, steam_running=False):
    """Fake repack + Steam library; stub the unpacker to create the game exe."""
    from types import SimpleNamespace

    from tb_fitgirl import steam

    _fake_steam_library(tmp_path, monkeypatch)
    monkeypatch.setattr(steam, "steam_running", lambda: steam_running)
    monkeypatch.setattr(steam, "newest_proton", lambda: tmp_path / "proton" / "proton")

    repack_dir = tmp_path / "downloads" / "PRAGMATA [FitGirl Repack]"
    repack_dir.mkdir(parents=True)
    repack = SimpleNamespace(game_name="PRAGMATA", bins=[], optional_bins=[], md5_file=None)
    monkeypatch.setattr("tb_fitgirl.bridge.find_repack", lambda path: repack)

    def fake_install(repack, target, *, on_progress=None, **kwargs):
        target.mkdir(parents=True, exist_ok=True)
        (target / "PRAGMATA.exe").write_bytes(b"MZ" + b"\0" * 100)
        if on_progress:
            on_progress(50, 100, 1.0, 50.0)
            on_progress(100, 100, 2.0, 0.0)

    monkeypatch.setattr("tb_fitgirl.bridge.install", fake_install)
    monkeypatch.setattr(steam, "add_shortcut", lambda name, exe: 777)
    monkeypatch.setattr(
        "tb_fitgirl.bridge.write_desktop_entry", lambda name, appid: tmp_path / "entry.desktop"
    )
    return repack_dir


def test_install_end_to_end(tmp_path, monkeypatch, capsys):
    repack_dir = _prep_install(tmp_path, monkeypatch)
    events = run_bridge(
        [
            {
                "id": 1,
                "op": "install",
                "args": {"target": "pragmata", "downloads": str(repack_dir.parent)},
            }
        ],
        monkeypatch,
        capsys,
    )
    data = events_of(events, "result")[0]["data"]
    assert data["name"] == "PRAGMATA"
    assert data["exe"].endswith("PRAGMATA.exe")
    assert data["steam_added"] is True
    assert data["appid"] == 777
    assert any("Proton version" in s for s in data["manual_steps"])
    phases = [p["data"]["phase"] for p in events_of(events, "progress")]
    assert "unpack" in phases
    assert "shortcut" in phases
    unpack = [p["data"] for p in events_of(events, "progress") if p["data"]["phase"] == "unpack"]
    assert unpack[-1]["done"] == unpack[-1]["total"] == 100


def test_install_steam_running_still_installs(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    repack_dir = _prep_install(tmp_path, monkeypatch)
    monkeypatch.setattr(steam, "steam_running", lambda: True)
    events = run_bridge(
        [
            {
                "id": 1,
                "op": "install",
                "args": {"target": "pragmata", "downloads": str(repack_dir.parent)},
            }
        ],
        monkeypatch,
        capsys,
    )
    data = events_of(events, "result")[0]["data"]
    assert data["steam_added"] is False
    assert data["reason"] == "steam_running"
    assert any("close it" in s.lower() for s in data["manual_steps"])


def test_install_matches_noisy_post_title(tmp_path, monkeypatch, capsys):
    """A full scraped post title finds the plainly-named downloaded dir."""
    repack_dir = _prep_install(tmp_path, monkeypatch)
    title = "PRAGMATA \u2013 v1.0.3 + 2 DLCs/Bonus Content [FitGirl Repack]"
    events = run_bridge(
        [
            {
                "id": 1,
                "op": "install",
                "args": {"target": title, "downloads": str(repack_dir.parent)},
            }
        ],
        monkeypatch,
        capsys,
    )
    data = events_of(events, "result")[0]["data"]
    assert data["name"] == "PRAGMATA"


def test_install_uses_downloaded_dir_when_title_never_matches(tmp_path, monkeypatch, capsys):
    """When nothing matches by title, the dir reported by the download wins."""
    repack_dir = _prep_install(tmp_path, monkeypatch)
    downloads = repack_dir.parent
    renamed = downloads / "Totally Different Torrent Name"
    repack_dir.rename(renamed)

    def fake_download(emit, args, dest):
        assert dest == downloads
        return renamed

    monkeypatch.setattr("tb_fitgirl.bridge._download", fake_download)
    events = run_bridge(
        [
            {
                "id": 1,
                "op": "install",
                "args": {"target": "unmatchable title", "downloads": str(downloads)},
            }
        ],
        monkeypatch,
        capsys,
    )
    data = events_of(events, "result")[0]["data"]
    assert data["name"] == "PRAGMATA"  # from the fake repack in _prep_install


def test_short_title():
    from tb_fitgirl.bridge import _short_title

    assert _short_title("Game \u2013 v1.0 + 2 DLCs [FitGirl Repack]") == "Game"
    assert _short_title("Game [FitGirl Repack]") == "Game"
    assert _short_title("Game: Edition (Build 123)") == "Game: Edition"
    assert _short_title("plain name") == "plain name"


def test_install_no_download_missing(tmp_path, monkeypatch, capsys):
    events = run_bridge(
        [
            {
                "id": 1,
                "op": "install",
                "args": {"target": "zzz", "downloads": str(tmp_path), "no_download": True},
            }
        ],
        monkeypatch,
        capsys,
    )
    assert "downloads disabled" in events_of(events, "error")[0]["data"]["message"]


def test_sequential_requests_keep_ids(tmp_path, monkeypatch, capsys):
    from tb_fitgirl import steam

    monkeypatch.setattr(steam, "steam_running", lambda: False)
    events = run_bridge(
        [{"id": 1, "op": "status"}, {"id": 2, "op": "bogus"}, {"id": 3, "op": "status"}],
        monkeypatch,
        capsys,
    )
    assert [(e["id"], e["event"]) for e in events] == [
        (1, "result"),
        (2, "error"),
        (3, "result"),
    ]
