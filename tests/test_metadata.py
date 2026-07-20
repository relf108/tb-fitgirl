from pathlib import Path

import httpx
import respx
from httpx import Response

from tb_fitgirl import metadata as metadata_mod
from tb_fitgirl.metadata import (
    CDN_HEADER_URL,
    STEAM_GRID_DB_ICONS_URL,
    STEAMGRIDDB_KEY_FILE,
    STORE_SEARCH_URL,
    StoreMatch,
    best_match,
    fetch_artwork,
    find_icon,
    steam_grid_db_search,
    store_search,
)


def _no_sgdb_key(monkeypatch, tmp_path: Path) -> None:
    """Ensure neither env nor config file supplies a SteamGridDB key."""
    monkeypatch.delenv("STEAMGRIDDB_API_KEY", raising=False)
    monkeypatch.setattr(metadata_mod, "CONFIG_DIR", str(tmp_path / "empty-cfg"))


@respx.mock
def test_store_search_parses_items():
    route = respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(
            200,
            json={
                "items": [
                    {"id": 391540, "name": "DELTARUNE", "tiny_image": "https://cdn/tiny.jpg"},
                    {"id": "bad"},
                    "not a dict",
                    {"id": 0, "name": "zero id skipped"},
                ]
            },
        )
    )
    matches = store_search("deltarune")
    assert route.calls[0].request.url.params["term"] == "deltarune"
    assert matches == [
        StoreMatch(appid=391540, name="DELTARUNE", tiny_image="https://cdn/tiny.jpg")
    ]


def test_best_match_exact_and_prefix():
    matches = [
        StoreMatch(1, "DELTARUNE Soundtrack"),
        StoreMatch(2, "DELTARUNE"),
    ]
    exact = best_match("Deltarune!", matches)
    assert exact is not None and exact.appid == 2  # normalised exact wins
    prefix = best_match("deltarune sound", matches)
    assert prefix is not None and prefix.appid == 1  # prefix
    # Term is a prefix of the store name (edition suffixes).
    edition = best_match("deltarune", [StoreMatch(3, "DELTARUNE: Chapter 1")])
    assert edition is not None and edition.appid == 3


def test_best_match_conservative():
    assert best_match("PRAGMATA", [StoreMatch(1, "Totally Different")]) is None
    assert best_match("", [StoreMatch(1, "X")]) is None
    assert best_match("anything", []) is None


@respx.mock
def test_fetch_artwork_downloads_and_caches(tmp_path):
    route = respx.get(CDN_HEADER_URL.format(appid=391540)).mock(
        return_value=Response(200, content=b"jpegbytes")
    )
    path = fetch_artwork(391540, icons_dir=tmp_path)
    assert path == tmp_path / "391540.jpg"
    assert path.read_bytes() == b"jpegbytes"

    fetch_artwork(391540, icons_dir=tmp_path)  # second call: cached
    assert route.call_count == 1


@respx.mock
def test_find_icon_falls_back_to_header_without_key(tmp_path, monkeypatch):
    _no_sgdb_key(monkeypatch, tmp_path)
    respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(200, json={"items": [{"id": 391540, "name": "DELTARUNE"}]})
    )
    respx.get(CDN_HEADER_URL.format(appid=391540)).mock(
        return_value=Response(200, content=b"jpegbytes")
    )
    assert find_icon("DELTARUNE", icons_dir=tmp_path) == tmp_path / "391540.jpg"


@respx.mock
def test_find_icon_prefers_steamgriddb(tmp_path, monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "sgdb-key")
    respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(200, json={"items": [{"id": 391540, "name": "DELTARUNE"}]})
    )
    respx.get(STEAM_GRID_DB_ICONS_URL.format(appid=391540)).mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": [{"url": "https://cdn.steamgriddb.com/icon/deltarune.png"}],
            },
        )
    )
    respx.get("https://cdn.steamgriddb.com/icon/deltarune.png").mock(
        return_value=Response(200, content=b"pngbytes")
    )
    path = find_icon("DELTARUNE", icons_dir=tmp_path)
    assert path is not None
    assert path == tmp_path / "391540.icon.png"
    assert path.read_bytes() == b"pngbytes"


@respx.mock
def test_find_icon_never_raises(tmp_path, monkeypatch):
    _no_sgdb_key(monkeypatch, tmp_path)
    respx.get(STORE_SEARCH_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert find_icon("DELTARUNE", icons_dir=tmp_path) is None

    respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(200, json={"items": [{"id": 391540, "name": "DELTARUNE"}]})
    )
    respx.get(CDN_HEADER_URL.format(appid=391540)).mock(return_value=Response(404, content=b""))
    assert find_icon("DELTARUNE", icons_dir=tmp_path) is None


def test_steam_grid_db_search_no_key(tmp_path, monkeypatch):
    _no_sgdb_key(monkeypatch, tmp_path)
    assert steam_grid_db_search(391540) is None


@respx.mock
def test_steam_grid_db_search_returns_top_url(monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "test-sgdb-key")
    route = respx.get(STEAM_GRID_DB_ICONS_URL.format(appid=391540)).mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": [
                    {"id": 1, "url": "https://cdn.steamgriddb.com/icon/a.png", "thumb": "t.png"},
                    {"id": 2, "url": "https://cdn.steamgriddb.com/icon/b.png"},
                ],
            },
        )
    )
    assert steam_grid_db_search(391540) == "https://cdn.steamgriddb.com/icon/a.png"
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-sgdb-key"


@respx.mock
def test_steam_grid_db_search_reads_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("STEAMGRIDDB_API_KEY", raising=False)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / STEAMGRIDDB_KEY_FILE).write_text("file-key\n", encoding="utf-8")
    monkeypatch.setattr(metadata_mod, "CONFIG_DIR", str(cfg))
    route = respx.get(STEAM_GRID_DB_ICONS_URL.format(appid=1)).mock(
        return_value=Response(
            200,
            json={"success": True, "data": [{"url": "https://cdn.example/i.png"}]},
        )
    )
    assert steam_grid_db_search(1) == "https://cdn.example/i.png"
    assert route.calls[0].request.headers["Authorization"] == "Bearer file-key"


@respx.mock
def test_steam_grid_db_search_empty_data(monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "k")
    respx.get(STEAM_GRID_DB_ICONS_URL.format(appid=1)).mock(
        return_value=Response(200, json={"success": True, "data": []})
    )
    assert steam_grid_db_search(1) is None


@respx.mock
def test_steam_grid_db_search_explicit_key_overrides_env(monkeypatch):
    monkeypatch.setenv("STEAMGRIDDB_API_KEY", "env-key")
    route = respx.get(STEAM_GRID_DB_ICONS_URL.format(appid=9)).mock(
        return_value=Response(
            200,
            json={"success": True, "data": [{"url": "https://cdn.example/i.png"}]},
        )
    )
    assert steam_grid_db_search(9, api_key="arg-key") == "https://cdn.example/i.png"
    assert route.calls[0].request.headers["Authorization"] == "Bearer arg-key"
