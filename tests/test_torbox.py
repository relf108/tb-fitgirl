import pytest
import respx
from httpx import Response

from tb_fitgirl.torbox import MAIN_API, TorboxClient, TorboxError


@pytest.fixture
def client():
    with TorboxClient(api_key="test-key") as c:
        yield c


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("TORBOX_API_KEY", raising=False)
    with pytest.raises(TorboxError, match="No API key"):
        TorboxClient()


@respx.mock
def test_me(client):
    respx.get(f"{MAIN_API}/user/me").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {"email": "a@b.c", "plan": 2, "premium_expires_at": "2027-01-01"},
            },
        )
    )
    data = client.me()
    assert data["email"] == "a@b.c"
    assert data["plan"] == 2


@respx.mock
def test_me_bad_key(client):
    respx.get(f"{MAIN_API}/user/me").mock(return_value=Response(403, json={}))
    with pytest.raises(TorboxError, match="Authentication"):
        client.me()


@respx.mock
def test_me_unexpected_payload(client):
    respx.get(f"{MAIN_API}/user/me").mock(return_value=Response(200, json={"data": []}))
    with pytest.raises(TorboxError, match="Unexpected"):
        client.me()


@respx.mock
def test_check_cached(client):
    route = respx.get(f"{MAIN_API}/torrents/checkcached").mock(
        return_value=Response(
            200,
            json={
                "success": True,
                "data": {"aaaa": {"name": "Game", "size": 52 * 1024**3, "hash": "aaaa"}},
            },
        )
    )
    statuses = client.check_cached(["AAAA", "bbbb"])
    params = route.calls[0].request.url.params
    assert params["hash"] == "aaaa,bbbb"
    assert params["format"] == "object"

    assert statuses["aaaa"].cached
    assert statuses["aaaa"].name == "Game"
    assert statuses["aaaa"].size_human == "52.0 GB"
    assert not statuses["bbbb"].cached


def test_check_cached_empty(client):
    assert client.check_cached([]) == {}


@respx.mock
def test_auth_failure(client):
    respx.get(f"{MAIN_API}/torrents/checkcached").mock(return_value=Response(403, json={}))
    with pytest.raises(TorboxError, match="Authentication"):
        client.check_cached(["aa"])


@respx.mock
def test_plan_gated_rate_limit(client):
    respx.get(f"{MAIN_API}/torrents/checkcached").mock(
        return_value=Response(429, json={"error": "Rate limit exceeded: 0 per 1 minute"})
    )
    with pytest.raises(TorboxError, match="not available on your plan"):
        client.check_cached(["aa"])


@respx.mock
def test_rate_limit_non_dict_payload(client):
    respx.get(f"{MAIN_API}/torrents/checkcached").mock(
        return_value=Response(429, json=["unexpected"])
    )
    with pytest.raises(TorboxError, match="Rate limited"):
        client.check_cached(["aa"])


@respx.mock
def test_ordinary_rate_limit(client):
    respx.get(f"{MAIN_API}/torrents/checkcached").mock(
        return_value=Response(429, json={"error": "Rate limit exceeded: 300 per 1 minute"})
    )
    with pytest.raises(TorboxError, match="Rate limited"):
        client.check_cached(["aa"])


@respx.mock
def test_create_torrent(client):
    route = respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(
            200,
            json={"success": True, "data": {"torrent_id": 7, "hash": "aa", "auth_id": "x"}},
        )
    )
    data = client.create_torrent("magnet:?xt=urn:btih:aa")
    assert data["torrent_id"] == 7
    body = route.calls[0].request.content
    assert b"magnet" in body
    assert b"add_only_if_cached" not in body


@respx.mock
def test_create_torrent_only_if_cached(client):
    route = respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(
            400,
            json={
                "success": False,
                "error": "DOWNLOAD_NOT_CACHED",
                "detail": "Torrent not found in cache.",
            },
        )
    )
    with pytest.raises(TorboxError, match="not found in cache") as excinfo:
        client.create_torrent("magnet:?xt=urn:btih:aa", only_if_cached=True)
    assert excinfo.value.code == "DOWNLOAD_NOT_CACHED"
    assert b"add_only_if_cached" in route.calls[0].request.content


@respx.mock
def test_api_error_detail_surfaces(client):
    respx.post(f"{MAIN_API}/torrents/createtorrent").mock(
        return_value=Response(400, json={"success": False, "detail": "Invalid magnet."})
    )
    with pytest.raises(TorboxError, match="Invalid magnet."):
        client.create_torrent("magnet:?xt=urn:btih:zz")
