PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.PHONY: install test lint architecture check run

install:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

architecture:
	$(PYTHON) -m pytest tests/test_architecture.py -q

check: lint architecture test

run:
	$(PYTHON) -m uvicorn taskhub_v2.api.app:create_app --factory --host 0.0.0.0 --port 8200
