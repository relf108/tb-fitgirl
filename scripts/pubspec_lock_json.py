"""Convert gui/pubspec.lock (YAML) to gui/pubspec.lock.json.

The JSON copy lets the Nix flake read the lock file with
builtins.fromJSON, avoiding import-from-derivation. Regenerate after
`flutter pub upgrade` via `make update-pubspec-json`.

Only the subset of YAML emitted by `dart pub` is supported: nested
mappings with scalar string values, optionally double-quoted.
"""

from __future__ import annotations

import json
from pathlib import Path

LOCK = Path("gui/pubspec.lock")
LOCK_JSON = Path("gui/pubspec.lock.json")


def parse(lines: list[str]) -> dict:
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for line in lines:
        s = line.lstrip()
        if not s or s.startswith("#") or ":" not in s:
            continue
        indent = len(line) - len(s)
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        key, _, value = s.partition(":")
        key = key.strip().strip('"')
        value = value.strip().strip('"')
        if value:
            parent[key] = value
        else:
            parent[key] = {}
            stack.append((indent, parent[key]))
    return root


def main() -> None:
    data = parse(LOCK.read_text().splitlines(keepends=True))
    LOCK_JSON.write_text(json.dumps(data, indent=2))
    print(f"{LOCK_JSON} updated")


if __name__ == "__main__":
    main()
