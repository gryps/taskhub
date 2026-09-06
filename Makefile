.PHONY: install test lint run

install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	python3 -m ruff check .

run:
	python3 -m uvicorn taskhub_v2.api.app:create_app --factory --host 0.0.0.0 --port 8200
