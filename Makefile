.PHONY: qa fmt test check gui-create gui-run gui-build gui-test

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
