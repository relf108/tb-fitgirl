from tb_fitgirl.desktop import steam_rungameid, write_desktop_entry


def test_steam_rungameid():
    appid = 0x80000000 | 123
    assert steam_rungameid(appid) == (appid << 32) | 0x02000000


def test_write_desktop_entry(tmp_path):
    appid = 3341980783
    path = write_desktop_entry("DELTARUNE", appid, applications_dir=tmp_path)
    assert path.exists()
    assert path.name == "tb-fitgirl-DELTARUNE.desktop"

    content = path.read_text()
    assert "[Desktop Entry]" in content
    assert "Name=DELTARUNE" in content
    assert f"Exec=steam steam://rungameid/{steam_rungameid(appid)}" in content
    assert "Type=Application" in content
    assert "Categories=Game;" in content
    assert content.endswith("\n")
    assert path.stat().st_mode & 0o111  # executable


def test_write_desktop_entry_sanitises_name(tmp_path):
    path = write_desktop_entry("DOOM: The/Dark Ages", 1, applications_dir=tmp_path)
    # No path separators or colons leak into the filename.
    assert "/" not in path.name
    assert ":" not in path.name
    assert path.read_text().splitlines()[1] == "Name=DOOM: The/Dark Ages"


def test_write_desktop_entry_custom_icon(tmp_path):
    path = write_desktop_entry("Game", 1, applications_dir=tmp_path, icon="/path/to/icon.png")
    assert "Icon=/path/to/icon.png" in path.read_text()
