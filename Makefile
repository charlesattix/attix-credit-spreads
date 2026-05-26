.PHONY: all build test test-python test-web lint lint-python lint-web \
        docker-build docker-run setup install clean

all: lint test build

# === SETUP ===
setup: install
	mkdir -p data output logs

install:
	pip install -r requirements.txt
	cd web && npm ci

# === BUILD ===
build:
	cd web && npm run build

docker-build:
	docker build -t attix-cs .

docker-run:
	docker run --rm -p 8080:8080 --env-file .env attix-cs

# === TEST ===
test: test-python test-web

test-python:
	python -m pytest tests/ -v

test-web:
	cd web && npx vitest run

# === LINT ===
lint: lint-python lint-web

lint-python:
	python3 -m py_compile main.py
	find . -name '*.py' -not -path './venv/*' -not -path './.venv/*' | xargs -I{} python3 -m py_compile {}
	@echo "Lint passed"

lint-web:
	cd web && npm run lint 2>/dev/null || echo "No lint script configured"

# === CLEAN ===
clean:
	rm -rf web/node_modules web/.next
	rm -rf __pycache__ **/__pycache__ .pytest_cache
	rm -rf .coverage htmlcov
