import pytest
import respx
from httpx import Response

from tb_fitgirl.scrapers import DEFAULT_SCRAPER, SCRAPERS, get_scraper
from tb_fitgirl.scrapers.base import Scraper
from tb_fitgirl.scrapers.fitgirl import BASE_URL, FitgirlScraper

SEARCH_HTML = """
<html><body>
<article>
  <h1 class="entry-title"><a href="https://fitgirl-repacks.site/elden-ring/">Elden Ring</a></h1>
</article>
<article>
  <h1 class="entry-title"><a href="https://fitgirl-repacks.site/upcoming/">Upcoming repacks</a></h1>
</article>
</body></html>
"""

MAGNET = "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12&dn=Elden.Ring"

POST_HTML = f"""
<html><body>
<h1 class="entry-title">Elden Ring</h1>
<ul>
  <li><a href="{MAGNET}">magnet</a></li>
  <li><a href="{MAGNET}">dupe magnet</a></li>
  <li><a href="https://example.com/other">not magnet</a></li>
</ul>
</body></html>
"""


def test_registry():
    assert DEFAULT_SCRAPER == "fitgirl"
    scraper = get_scraper("fitgirl")
    assert isinstance(scraper, FitgirlScraper)
    assert isinstance(scraper, Scraper)
    assert "fitgirl" in SCRAPERS


def test_registry_unknown_name():
    with pytest.raises(ValueError, match="Unknown scraper 'dodi'"):
        get_scraper("dodi")


@respx.mock
def test_search():
    respx.get(BASE_URL, params={"s": "elden ring"}).mock(
        return_value=Response(200, text=SEARCH_HTML)
    )
    with get_scraper() as fg:
        results = fg.search("elden ring")
    assert [r.title for r in results] == ["Elden Ring", "Upcoming repacks"]
    assert results[0].url == "https://fitgirl-repacks.site/elden-ring/"
    assert results[0].source == "fitgirl"


@respx.mock
def test_fetch_magnets_dedupes():
    respx.get(BASE_URL, params={"s": "elden ring"}).mock(
        return_value=Response(200, text=SEARCH_HTML)
    )
    respx.get("https://fitgirl-repacks.site/elden-ring/").mock(
        return_value=Response(200, text=POST_HTML)
    )
    with get_scraper() as fg:
        repack = fg.find_repack("elden ring")
    assert repack is not None
    assert repack.primary_magnet is not None
    assert len(repack.magnets) == 1
    assert repack.primary_magnet.startswith("magnet:?xt=urn:btih:abcdef")


@respx.mock
def test_search_no_results():
    respx.get(BASE_URL, params={"s": "zzz"}).mock(
        return_value=Response(200, text="<html><body><p>Nothing</p></body></html>")
    )
    with get_scraper() as fg:
        assert fg.search("zzz") == []
