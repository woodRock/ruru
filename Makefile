# Local equivalents of the CI jobs. `make check` runs all three.
.PHONY: venv install check lint types test test-fast

PY := .venv/bin/python

venv:
	python3.11 -m venv .venv
	$(PY) -m pip install -U pip
	$(PY) -m pip install -e ".[dev,tui]"

install:
	$(PY) -m pip install -e ".[dev,tui]"

check: lint types test

lint:
	$(PY) -m ruff check .

types:
	$(PY) -m mypy

test:
	$(PY) -m coverage run -m pytest && $(PY) -m coverage report

test-fast:
	$(PY) -m pytest -m "not slow"
