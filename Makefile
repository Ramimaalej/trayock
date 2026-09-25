PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin
PIP    := $(BIN)/pip

.PHONY: help dev check test lint format dist install uninstall \
        desktop desktop-off autostart autostart-off udev clean

help: ## list available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

dev: $(BIN)/inputlock ## create the venv and install inputlock editable

$(BIN)/inputlock: pyproject.toml
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(PIP) install -e .
	touch $@

check: lint test ## run lint + tests

test: dev ## run the unit test suite
	$(BIN)/python -m unittest discover -s tests -v

lint: ## run ruff
	@if [ -x $(BIN)/ruff ]; then $(BIN)/ruff check .; else ruff check .; fi

format: ## autofix ruff findings
	@if [ -x $(BIN)/ruff ]; then $(BIN)/ruff check --fix .; else ruff check --fix .; fi

dist: dev ## build sdist + wheel into dist/
	@if [ -x $(BIN)/pyproject-build ]; then $(BIN)/pyproject-build; \
	else $(BIN)/python -m build; fi

install: dev ## install into the venv for real (non-editable)
	$(PIP) install .

uninstall: ## remove inputlock from the venv
	$(PIP) uninstall -y inputlock || true

desktop: dev ## install the app launcher into ~/.local/share/applications
	$(BIN)/inputlock --install-desktop

desktop-off: dev ## remove the app launcher
	$(BIN)/inputlock --remove-desktop

autostart: dev ## start inputlock automatically at login
	$(BIN)/inputlock --install-autostart

autostart-off: dev ## stop starting inputlock at login
	$(BIN)/inputlock --remove-autostart

udev: ## install the udev rule granting the 'input' group access (needs root)
	install -m 0644 packaging/99-inputlock.rules /etc/udev/rules.d/99-inputlock.rules
	udevadm control --reload
	udevadm trigger

clean: ## remove build artifacts
	rm -rf build dist src/*.egg-info
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
