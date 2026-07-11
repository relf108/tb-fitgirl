# TODO

## GUI (Flutter front-end)

Per goals.md, a graphical interface wrapping the Python back end. The CLI
already exposes the full pipeline (search / cache / download / install /
uninstall); the GUI should drive that same logic, not reimplement it.

### Must-have for v1

1. **API key handling**
   - Prompt for the TorBox API key on first run; store it securely
     (not plain-text in the repo). Prefer the OS keyring
     (libsecret / KWallet / macOS Keychain) over a dotfile.
   - Validate the key against `GET /v1/api/user/me` and show plan/expiry.
   - Let the user update/clear it in a settings screen.

2. **Search**
   - Text box → scrape the selected source (default fitgirl), list results
     with title, size, and TorBox cache status (cached / not cached).
   - Reuse `search`/scraper + `TorboxClient.check_cached`; don't duplicate.
   - Debounce input; run scrapes off the UI thread.

3. **One-click install**
   - Per result: an "Install" button that runs the whole chain
     (cache if needed → download → verify → Proton install → Steam +
     launcher entry), i.e. the existing `install` command end-to-end.
   - Live progress: download %, then unpack % (installer already reports
     `bytes / estimated_total / rate` via the `on_progress` callback).
   - Surface the two known manual steps clearly:
     - "Close Steam before installing" (shortcut write needs it closed).
     - "Set Proton version in Steam" after install (non-Steam shortcuts
       don't inherit a default) — link to Properties > Compatibility.

### Architecture notes

- **Back-end boundary**: wrap the CLI as a local process/IPC, or expose the
  Python functions via a thin JSON API (e.g. a local FastAPI/stdio bridge).
  The GUI should call `search` / `cache` / `download` / `install` /
  `uninstall` and stream their progress. Keep all TorBox/Proton logic in
  Python; Flutter is presentation only.
- **Progress streaming**: the installer/downloader progress callbacks need a
  channel to the UI (stdout JSON lines, or a socket). Define a small event
  schema: `{phase, done, total, rate, message}`.
- **Long-running installs**: Proton unpack can take many minutes and the
  installer window hangs at finalisation — the back end already handles this
  (`ready_when` stops at the game exe). GUI just needs a cancel button that
  kills the process tree.

### Nice-to-have (later)

- Library view of installed games (read Steam shortcuts + our launcher
  entries) with uninstall buttons.
- Auto-set the Proton version on the shortcut so the "set Proton in Steam"
  step disappears (write the compat-tool mapping in `config.vdf`; same
  "Steam must be closed" constraint as shortcuts).
- Per-repack component selection UI for `--gui` installs (currently we take
  all defaults silently).
- Fetch a real game icon for the Steam shortcut and .desktop entry.

## Back-end follow-ups

- Optional: auto-set Proton version for the shortcut (see above).
- Consider a single `play`/`get` alias that is literally `install`
  (now that install auto-downloads, they're nearly the same).
