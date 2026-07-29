# Changelog

## [1.0.1] — 2026-07-29

### Added
- Default app icon (launcher, AppImage, Nix package, window chrome)

## [1.0.0] — 2026-07-22

First stable release.

### Added
- CLI: search, cache, download, install, steam-add, uninstall
- JSON-lines bridge for GUI control + cancel (process group)
- Flutter Linux GUI: API key (keyring), search, one-click install with progress, library + uninstall
- AppImage bundle (GUI + frozen CLI/bridge, no host Python)
- Nix flake packages (CLI/bridge + GUI)
- Steam + `.desktop` launcher entries; SteamGridDB icons
- Multi-exe launcher selection

### Notes
- Linux only for the GUI path; needs Steam + Proton
- TorBox API key required (`TORBOX_API_KEY` or GUI keyring)
