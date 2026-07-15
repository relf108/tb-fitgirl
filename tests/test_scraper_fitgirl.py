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


POST_URL = "https://fitgirl-repacks.site/elden-ring/"

POST_HTML_TITLED = f"""
<html><body>
<h1 class="entry-title">Elden Ring</h1>
<ul><li><a href="{MAGNET}">magnet</a></li></ul>
</body></html>
"""


@respx.mock
def test_find_repack_from_post_url_skips_search():
    # Only the post page is mocked: any search request would hit an unmocked
    # route and raise, proving the URL path bypasses search entirely.
    respx.get(POST_URL).mock(return_value=Response(200, text=POST_HTML_TITLED))
    with get_scraper() as fg:
        repack = fg.find_repack(POST_URL)
    assert repack is not None
    assert repack.url == POST_URL
    assert repack.title == "Elden Ring"  # filled in from the page
    assert repack.primary_magnet == MAGNET


@pytest.mark.parametrize(
    "url",
    [
        "https://fitgirl-repacks.site/",
        "https://fitgirl-repacks.site/category/action/",
        "https://fitgirl-repacks.site/page/2/",
        "https://example.com/elden-ring/",
        "not a url at all",
        "magnet:?xt=urn:btih:abc",
    ],
)
def test_repack_from_url_rejects_non_posts(url):
    with get_scraper() as fg:
        assert fg.repack_from_url(url) is None


@respx.mock
def test_find_repack_relaxes_over_specific_title():
    exact = "Elden Ring \u2013 v1.10 + 4 DLCs [FitGirl Repack]"
    # Exact title returns nothing; the relaxed "Elden Ring" query hits.
    # search() folds the en-dash to ASCII, so mock the folded exact query.
    respx.get(BASE_URL, params={"s": "Elden Ring - v1.10 + 4 DLCs [FitGirl Repack]"}).mock(
        return_value=Response(200, text="<html><body></body></html>")
    )
    respx.get(BASE_URL, params={"s": "Elden Ring"}).mock(
        return_value=Response(200, text=SEARCH_HTML)
    )
    respx.get("https://fitgirl-repacks.site/elden-ring/").mock(
        return_value=Response(200, text=POST_HTML)
    )
    with get_scraper() as fg:
        repack = fg.find_repack(exact)
    assert repack is not None
    assert repack.primary_magnet is not None


def test_relax_query_strips_noise():
    with get_scraper() as fg:
        assert fg.relax_query("Elden Ring \u2013 v1.0 + 4 DLCs [FitGirl]") == "Elden Ring"
        assert fg.relax_query("Pragmata + 2 DLCs") == "Pragmata"
        # No relaxation possible: retrying the same query would be pointless.
        assert fg.relax_query("Elden Ring") is None


def test_relax_query_keeps_commas_and_tight_plus():
    # Commas and unspaced "+" are part of real game names, not noise; only a
    # dash (or other separator) after them should truncate.
    with get_scraper() as fg:
        title = "Warhammer 40,000: Space Marine 2 \u2013 v1.0 + 3 DLCs"
        assert fg.relax_query(title) == "Warhammer 40,000: Space Marine 2"
        assert fg.relax_query("Warhammer 40,000: Space Marine 2") is None
        assert fg.relax_query("Superliminal+ \u2013 Deluxe Edition") == "Superliminal+"


def test_relax_query_none_when_only_punctuation_differs():
    # search() already folds typographic punctuation, so a "relaxation" that
    # only differs typographically would repeat the identical failed request.
    with get_scraper() as fg:
        assert fg.relax_query("Sid Meier\u2019s Civilization VII") is None


def test_relax_query_folds_typographic_punctuation():
    # Curly apostrophe in the surviving game name is folded to ASCII so the
    # relaxed query matches the WordPress search.
    with get_scraper() as fg:
        title = "Sid Meier\u2019s Civilization VII \u2013 v1.3 + 34 DLCs"
        assert fg.relax_query(title) == "Sid Meier's Civilization VII"


@respx.mock
def test_search_folds_typographic_punctuation():
    # The bug: a scraped title carries curly quotes/dashes, but WordPress
    # search only matches ASCII. search() must fold before querying.
    route = respx.get(BASE_URL, params={"s": "Sid Meier's Civilization VII"}).mock(
        return_value=Response(200, text=SEARCH_HTML)
    )
    with get_scraper() as fg:
        fg.search("Sid Meier\u2019s Civilization VII")
    assert route.called


@respx.mock
def test_find_repack_curly_title_resolves():
    # End-to-end: the exact curly post title (what a GUI feeds back from a
    # search result) must reach the post and yield its magnet. The exact
    # folded query finds nothing; the relaxed, folded game name does.
    curly = "Elden Ring\u2019s \u2013 v1.0 + 4 DLCs"
    respx.get(BASE_URL, params={"s": "Elden Ring's - v1.0 + 4 DLCs"}).mock(
        return_value=Response(200, text="<html><body></body></html>")
    )
    respx.get(BASE_URL, params={"s": "Elden Ring's"}).mock(
        return_value=Response(200, text=SEARCH_HTML)
    )
    respx.get("https://fitgirl-repacks.site/elden-ring/").mock(
        return_value=Response(200, text=POST_HTML)
    )
    with get_scraper() as fg:
        repack = fg.find_repack(curly)
    assert repack is not None
    assert repack.primary_magnet is not None
