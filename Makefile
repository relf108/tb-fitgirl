.PHONY: qa fmt test check gui-create gui-run gui-build gui-test update-pubspec-json

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

# Keep gui/pubspec.lock.json in sync with gui/pubspec.lock after `flutter pub upgrade`.
# The JSON copy lets the Nix flake read the lock without import-from-derivation.
update-pubspec-json:
	python3 -c "
import json, sys
lines = open('gui/pubspec.lock').readlines()
def parse(lines):
    root = {}; stack = [(-1, root)]
    for line in lines:
        s = line.lstrip()
        if not s or s.startswith('#'): continue
        ind = len(line.rstrip('\n')) - len(s)
        while len(stack) > 1 and stack[-1][0] >= ind: stack.pop()
        parent = stack[-1][1]
        if ':' in s:
            k, _, v = s.partition(':'); k = k.strip().strip('\"'); v = v.strip().strip('\"')
            parent[k] = v if v else {}
            if not v: stack.append((ind, parent[k]))
    return root
json.dump(parse(lines), open('gui/pubspec.lock.json', 'w'), indent=2)
print('gui/pubspec.lock.json updated')
"
