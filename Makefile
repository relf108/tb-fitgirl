.PHONY: qa fmt test check gui-create gui-run gui-build gui-test update-pubspec-json appimage

qa:
	ruff check src tests
	ruff format --check src tests

fmt:
	ruff check --fix src tests
	ruff format src tests

test:
	pytest -q

check: qa test

# One-off: generate the Flutter Linux runner boilerplate (not committed).
# pub get resolves Flutter's SDK-pinned deps from your configured registry.
gui-create:
	cd gui && flutter create --platforms=linux --project-name tbfg_gui --no-pub . \
		&& flutter pub get

gui-run: gui-create
	cd gui && flutter run -d linux

gui-build: gui-create
	cd gui && flutter build linux

gui-test: gui-create
	cd gui && flutter analyze && flutter test

# Single-file AppImage: Flutter GUI + frozen Python CLI/bridge. Output under dist/.
# Needs flutter, python3 (venv+pip), and network (pip + appimagetool) on first run.
appimage:
	bash scripts/build-appimage.sh

# Keep gui/pubspec.lock.json in sync with gui/pubspec.lock after `flutter pub upgrade`.
# The JSON copy lets the Nix flake read the lock without import-from-derivation.
update-pubspec-json:
	python3 scripts/pubspec_lock_json.py
