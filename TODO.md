# TODO

## GUI (Flutter front-end)

v1 is implemented in `gui/` (Flutter, Linux desktop) on top of the
JSON-lines stdio bridge (`src/tb_fitgirl/bridge.py`):

- [x] API key handling: first-run prompt, validation via `GET /user/me`
      (plan/expiry shown), stored in the OS keyring via `secret-tool`,
      update/clear in settings.
- [x] Search: debounced text box, scrape off the UI thread (bridge
      subprocess), title/size/cache-status list.
- [x] One-click install: full chain (cache -> download -> verify -> Proton
      install -> Steam + launcher entry) with live download/unpack progress
      and a cancel button that kills the bridge's process tree. The two
      manual steps (close Steam first; set Proton version after) are
      surfaced in the confirm dialog and the results screen.

- [x] Library view of installed games (union of our Steam shortcuts and
      `tb-fitgirl-*.desktop` entries; regular Steam games can never appear)
      with per-game uninstall incl. optional file deletion.

### Nice-to-have (later)

- Auto-set the Proton version on the shortcut so the "set Proton in Steam"
  step disappears (write the compat-tool mapping in `config.vdf`; same
  "Steam must be closed" constraint as shortcuts).
- Per-repack component selection UI for `--gui` installs (currently we take
  all defaults silently).
- Fetch a real game icon for the Steam shortcut and .desktop entry.
- Windows/macOS runners for the GUI (bridge protocol is platform-neutral;
  keyring + process-group handling are Linux-specific today).

## Back-end follow-ups

- Optional: auto-set Proton version for the shortcut (see above).
- Consider a single `play`/`get` alias that is literally `install`
  (now that install auto-downloads, they're nearly the same).
- Consider refactoring the orchestration shared by `cli.py` and `bridge.py`
  (resolve/add/download chain) into a core module so neither reimplements
  the other.
