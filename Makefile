# SafeChat Guardian - common tasks. On Windows without `make`, run the commands shown in README.md.
ifeq ($(OS),Windows_NT)
  PY ?= .venv/Scripts/python
else
  PY ?= .venv/bin/python
endif
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: install seed train eval run run-lan test lint format android-test clean

install:            ## create the venv and install backend + dev dependencies
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"

seed:               ## generate the deterministic synthetic corpus (data/synthetic/)
	$(PY) scripts/generate_synthetic.py

train:              ## train the TF-IDF + logistic-regression baseline (models/baseline.joblib)
	$(PY) scripts/train_baseline.py

eval:               ## evaluate all engines on the test split, write reports/latest.md
	$(PY) scripts/evaluate.py

run:                ## dashboard + API on localhost only
	$(PY) -m uvicorn app.main:app --host $(HOST) --port $(PORT)

run-lan:            ## listen on the LAN so a phone on the same Wi-Fi can connect (set a password!)
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT)

test:               ## backend tests + lint
	$(PY) -m pytest
	$(PY) -m ruff check .

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format:
	$(PY) -m ruff format .

android-test:       ## Android unit tests + debug APK (needs JDK 17 and the Android SDK)
	cd android && ./gradlew test assembleDebug

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ data/guardian.db*
