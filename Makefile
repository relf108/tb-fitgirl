.PHONY: qa fmt test check

qa:
	ruff check src tests
	ruff format --check src tests

fmt:
	ruff check --fix src tests
	ruff format src tests

test:
	pytest -q

check: qa test
