import httpx
import respx
from httpx import Response

from tb_fitgirl.metadata import (
    CDN_HEADER_URL,
    STORE_SEARCH_URL,
    StoreMatch,
    best_match,
    fetch_artwork,
    find_icon,
    store_search,
)


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
def test_find_icon_happy_path(tmp_path):
    respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(200, json={"items": [{"id": 391540, "name": "DELTARUNE"}]})
    )
    respx.get(CDN_HEADER_URL.format(appid=391540)).mock(
        return_value=Response(200, content=b"jpegbytes")
    )
    assert find_icon("DELTARUNE", icons_dir=tmp_path) == tmp_path / "391540.jpg"


@respx.mock
def test_find_icon_never_raises(tmp_path):
    respx.get(STORE_SEARCH_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert find_icon("DELTARUNE", icons_dir=tmp_path) is None

    respx.get(STORE_SEARCH_URL).mock(
        return_value=Response(200, json={"items": [{"id": 391540, "name": "DELTARUNE"}]})
    )
    respx.get(CDN_HEADER_URL.format(appid=391540)).mock(return_value=Response(404, content=b""))
    assert find_icon("DELTARUNE", icons_dir=tmp_path) is None
