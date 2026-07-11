from tb_fitgirl.models import CacheStatus, Repack, human_size, magnet_hash

MAGNET = "magnet:?xt=urn:btih:ABCDEF1234567890ABCDEF1234567890ABCDEF12&dn=Some.Game"


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(52 * 1024**3) == "52.0 GB"


def test_magnet_hash():
    assert magnet_hash(MAGNET) == "abcdef1234567890abcdef1234567890abcdef12"
    assert magnet_hash("magnet:?dn=no-hash") is None
    assert magnet_hash("not a magnet") is None


def test_magnet_hash_base32():
    # base32 of bytes.fromhex("abcdef1234567890abcdef1234567890abcdef12")
    b32 = "VPG66ERUKZ4JBK6N54JDIVTYSCV433YS".lower()
    assert magnet_hash(f"magnet:?xt=urn:btih:{b32}&dn=x") == (
        "abcdef1234567890abcdef1234567890abcdef12"
    )


def test_magnet_hash_rejects_invalid():
    assert magnet_hash("magnet:?xt=urn:btih:nothexatall&dn=x") is None
    assert magnet_hash("magnet:?xt=urn:btih:abcdef&dn=x") is None  # too short


def test_repack_hashes_dedupe():
    repack = Repack(
        title="Game",
        url="https://example.com",
        magnets=[MAGNET, MAGNET.lower(), "magnet:?dn=no-hash"],
    )
    assert repack.hashes == ["abcdef1234567890abcdef1234567890abcdef12"]
    assert repack.primary_magnet == MAGNET


def test_cache_status_size():
    assert CacheStatus(hash="aa", cached=True, size=1024).size_human == "1.0 KB"
