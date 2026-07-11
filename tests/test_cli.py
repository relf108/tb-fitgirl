import httpx
import pytest
import respx
from httpx import Response

from tb_fitgirl.cli import build_parser, main
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


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


@respx.mock
def test_search_shows_cache_status(monkeypatch, capsys):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")
    _mock_scrape("pragmata")
    _mock_checkcached(cached=True)

    assert main(["search", "pragmata"]) == 0
    out = capsys.readouterr().out
    assert "PRAGMATA" in out
    assert "yes" in out
    assert "40.0 GB" in out


@respx.mock
def test_search_no_results(monkeypatch, capsys):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")
    respx.get(BASE_URL, params={"s": "zzz"}).mock(
        return_value=Response(200, text="<html><body></body></html>")
    )
    assert main(["search", "zzz"]) == 1
    assert "No repacks" in capsys.readouterr().out


@respx.mock
def test_cache_by_title_adds(monkeypatch, capsys):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")
    _mock_scrape("pragmata")
    _mock_checkcached(cached=True)
    respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(200, json={"success": True, "data": {"torrent_id": 1, "hash": HASH}})
    )

    assert main(["cache", "pragmata"]) == 0
    out = capsys.readouterr().out
    assert "Cache status: cached" in out
    assert "Added to TorBox" in out


@respx.mock
def test_cache_only_if_cached_refuses(monkeypatch, capsys):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")
    _mock_checkcached(cached=False)

    assert main(["cache", MAGNET, "--only-if-cached"]) == 1
    out = capsys.readouterr().out
    assert "Cache status: not cached" in out
    assert "not adding" in out


@respx.mock
def test_main_reports_scraper_http_error(monkeypatch, capsys):
    monkeypatch.setenv("TORBOX_API_KEY", "test-key")
    respx.get(BASE_URL, params={"s": "pragmata"}).mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    assert main(["search", "pragmata"]) == 1
    assert "HTTP request failed" in capsys.readouterr().err


def test_main_reports_missing_key(monkeypatch, capsys):
    from tb_fitgirl.models import Repack

    monkeypatch.delenv("TORBOX_API_KEY", raising=False)
    repack = Repack(title="Game", url="https://x", magnets=[MAGNET])
    monkeypatch.setattr(
        "tb_fitgirl.cli._scrape_with_magnets", lambda source, title, limit: [repack]
    )
    assert main(["search", "elden ring"]) == 1
    assert "No API key" in capsys.readouterr().err
