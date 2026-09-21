PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: install test lint frontend-test frontend-build check run

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

frontend-test:
	npm --prefix taskhub-web test

frontend-build:
	npm --prefix taskhub-web run build

check: lint test frontend-test frontend-build

run:
	$(PYTHON) -m uvicorn taskhub_v2.api.app:create_app --factory --host 0.0.0.0 --port 8200
