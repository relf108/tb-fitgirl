from pathlib import Path

import pytest

from tb_fitgirl import steam


@pytest.fixture
def steam_root(tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    (root / "userdata" / "12345678" / "config").mkdir(parents=True)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{root}"\n\t}}\n}}\n'
    )
    return root


def test_library_paths(steam_root):
    assert steam.library_paths(steam_root) == [steam_root]


def test_common_dir_created(steam_root):
    common = steam.common_dir(steam_root)
    assert common == steam_root / "steamapps" / "common"
    assert common.is_dir()


def test_shortcuts_vdf_picks_user(steam_root):
    assert steam.shortcuts_vdf(steam_root) == (
        steam_root / "userdata" / "12345678" / "config" / "shortcuts.vdf"
    )


def test_add_shortcut_roundtrip(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    exe = Path("/games/DELTARUNE/DELTARUNE.exe")

    appid = steam.add_shortcut("DELTARUNE", exe, vdf_path=vdf)
    assert appid & 0x80000000

    data = steam.load_shortcuts(vdf)
    entry = data["shortcuts"]["0"]
    assert entry["AppName"] == "DELTARUNE"
    assert entry["Exe"] == f'"{exe}"'
    assert entry["StartDir"] == '"/games/DELTARUNE"'
    assert entry["appid"] == appid
    assert entry["tags"] == {}


def test_add_shortcut_icon(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    exe = Path("/games/DELTARUNE/DELTARUNE.exe")

    steam.add_shortcut("DELTARUNE", exe, icon=Path("/icons/391540.jpg"), vdf_path=vdf)
    entry = steam.load_shortcuts(vdf)["shortcuts"]["0"]
    assert entry["icon"] == "/icons/391540.jpg"

    # Default stays an empty string, not "None".
    steam.add_shortcut("OTHER", Path("/games/OTHER/o.exe"), vdf_path=vdf)
    assert steam.load_shortcuts(vdf)["shortcuts"]["1"]["icon"] == ""


def test_add_shortcut_idempotent(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    exe = Path("/games/DELTARUNE/DELTARUNE.exe")
    a1 = steam.add_shortcut("DELTARUNE", exe, vdf_path=vdf)
    a2 = steam.add_shortcut("DELTARUNE", exe, vdf_path=vdf)
    assert a1 == a2
    assert list(steam.load_shortcuts(vdf)["shortcuts"]) == ["0"]


def test_add_shortcut_appends_and_backs_up(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    steam.add_shortcut("Game A", Path("/g/a.exe"), vdf_path=vdf)
    steam.add_shortcut("Game B", Path("/g/b.exe"), vdf_path=vdf)
    data = steam.load_shortcuts(vdf)
    assert [e["AppName"] for e in data["shortcuts"].values()] == ["Game A", "Game B"]
    assert vdf.with_suffix(".vdf.bak").is_file()


def test_grid_dir(steam_root):
    assert steam.grid_dir(steam_root) == (steam_root / "userdata" / "12345678" / "config" / "grid")


def test_set_grid_art_writes_and_replaces_stale(tmp_path):
    grid = tmp_path / "grid"
    art = tmp_path / "header.jpg"
    art.write_bytes(b"jpegbytes")

    target = steam.set_grid_art(999, art, grid=grid)
    assert target == grid / "999.jpg"
    assert target.read_bytes() == b"jpegbytes"

    # Switching image format replaces the old extension's file.
    png = tmp_path / "header.png"
    png.write_bytes(b"pngbytes")
    target2 = steam.set_grid_art(999, png, grid=grid)
    assert target2 == grid / "999.png"
    assert not (grid / "999.jpg").exists()


def test_remove_grid_art(tmp_path):
    grid = tmp_path / "grid"
    grid.mkdir()
    (grid / "999.jpg").write_bytes(b"x")
    (grid / "999p.png").write_bytes(b"x")
    (grid / "999_hero.png").write_bytes(b"x")
    (grid / "1000.jpg").write_bytes(b"other game")

    assert steam.remove_grid_art(999, grid=grid) is True
    assert list(grid.iterdir()) == [grid / "1000.jpg"]
    assert steam.remove_grid_art(999, grid=grid) is False
    assert steam.remove_grid_art(1, grid=tmp_path / "missing") is False


def test_remove_shortcut(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    steam.add_shortcut("Game A", Path("/g/a.exe"), vdf_path=vdf)
    b_appid = steam.add_shortcut("Game B", Path("/g/b.exe"), vdf_path=vdf)

    removed = steam.remove_shortcut("Game B", vdf_path=vdf)
    assert removed == b_appid

    remaining = steam.load_shortcuts(vdf)["shortcuts"]
    assert [e["AppName"] for e in remaining.values()] == ["Game A"]
    assert list(remaining) == ["0"]  # re-indexed
    assert vdf.with_suffix(".vdf.bak").is_file()


def test_remove_shortcut_missing(steam_root):
    vdf = steam.shortcuts_vdf(steam_root)
    steam.add_shortcut("Game A", Path("/g/a.exe"), vdf_path=vdf)
    assert steam.remove_shortcut("Nope", vdf_path=vdf) is None


def test_remove_shortcut_no_file(tmp_path):
    assert steam.remove_shortcut("X", vdf_path=tmp_path / "none.vdf") is None


def test_steam_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(steam, "STEAM_ROOTS", (str(tmp_path / "nope"),))
    with pytest.raises(steam.SteamNotFound):
        steam.steam_root()


def _make_proton(root, name, *, custom=False):
    base = (root / "compatibilitytools.d" if custom else root / "steamapps" / "common") / name
    base.mkdir(parents=True, exist_ok=True)
    (base / "proton").write_text("#!/usr/bin/env python3\n")
    return base / "proton"


def test_proton_installs_discovers_both_locations(steam_root):
    _make_proton(steam_root, "Proton 11.0")
    _make_proton(steam_root, "proton-cachyos-11.0", custom=True)
    installs = steam.proton_installs(steam_root)
    assert set(installs) == {"Proton 11.0", "proton-cachyos-11.0"}


def test_newest_proton_prefers_official(steam_root):
    _make_proton(steam_root, "proton-cachyos-11.0", custom=True)
    official = _make_proton(steam_root, "Proton 11.0")
    _make_proton(steam_root, "Proton 10.0")
    assert steam.newest_proton(steam_root) == official


def test_newest_proton_falls_back_to_custom(steam_root):
    ge = _make_proton(steam_root, "proton-cachyos-11.0", custom=True)
    assert steam.newest_proton(steam_root) == ge


def test_find_proton_by_substring(steam_root):
    _make_proton(steam_root, "Proton 11.0")
    ge = _make_proton(steam_root, "proton-cachyos-11.0", custom=True)
    assert steam.find_proton("cachyos", steam_root) == ge


def test_find_proton_missing(steam_root):
    _make_proton(steam_root, "Proton 11.0")
    with pytest.raises(steam.SteamNotFound, match="No Proton matching 'ge'"):
        steam.find_proton("ge", steam_root)


def test_newest_proton_none(steam_root):
    with pytest.raises(steam.SteamNotFound):
        steam.newest_proton(steam_root)
