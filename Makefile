# Convenience targets.  Everything runs inside the project venv (.venv).
PY := .venv/bin/python
REEL := .venv/bin/reel

.PHONY: help setup test test-fast lint format typecheck check generated examples previews storyboards smoke clean serve ui-install ui-dev ui-build ui-test ui-check

help:
	@echo "setup        create .venv (uv, Python 3.12) and install reel with dev tools"
	@echo "test         full test suite (renders real frames; ~1-2 min)"
	@echo "test-fast    everything except the slower render/ffmpeg tests"
	@echo "lint         ruff check + format check"
	@echo "format       ruff format + autofix"
	@echo "typecheck    mypy"
	@echo "check        lint + typecheck + test"
	@echo "serve        Reel Studio, the web app, on 127.0.0.1:8765 (opens with an access token)"
	@echo "ui-install   install the web app's Node dependencies (only needed to change the web app)"
	@echo "ui-dev       web app with hot reload on :5173, talking to 'make serve' (run that first with --no-token)"
	@echo "ui-build     build the web app into src/reel/server/static (committed: reel serve needs no Node)"
	@echo "ui-test      web app unit and component tests"
	@echo "ui-check     web app type check + tests + production build"
	@echo "generated    regenerate schema/, templates manifest, docs/reference and the exact LLM prompts in docs/prompts"
	@echo "examples     lint the two example specs"
	@echo "previews     render both examples as 360x640 previews into out/"
	@echo "storyboards  HTML storyboards of both examples into out/"
	@echo "smoke        the whole path once: doctor, lint + preview both examples, build a video from a script (offline planner)"

setup:
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e ".[dev]"

test:
	$(PY) -m pytest

test-fast:
	$(PY) -m pytest -m "not render and not ffmpeg"

lint:
	.venv/bin/ruff check src tests examples
	.venv/bin/ruff format --check src tests examples

format:
	.venv/bin/ruff format src tests examples
	.venv/bin/ruff check --fix src tests examples

typecheck:
	$(PY) -m mypy src

check: lint typecheck test

# the committed files describe what ships: never the asset folders (REEL_ASSETS) of the machine that regenerates them
GEN := env -u REEL_ASSETS $(REEL)

generated:
	$(GEN) schema -o schema/scene_spec.schema.json
	$(GEN) manifest --builtin-only -o src/reel/templates/manifest.json
	$(GEN) reference -o docs/reference
	$(GEN) prompt --schema -o docs/prompts/full.txt
	$(GEN) prompt --compact --schema -o docs/prompts/compact.txt

examples:
	$(REEL) lint examples/story_50s.json
	$(REEL) lint examples/explainer_45s.json

previews:
	$(REEL) render examples/story_50s.json --preview -o out/story_50s_preview.mp4
	$(REEL) render examples/explainer_45s.json --preview -o out/explainer_45s_preview.mp4

storyboards:
	$(REEL) storyboard examples/story_50s.json -o out/story_storyboard.html
	$(REEL) storyboard examples/explainer_45s.json -o out/explainer_storyboard.html

smoke:
	$(REEL) doctor
	$(MAKE) examples
	$(MAKE) previews
	$(REEL) build examples/scripts/why_we_sleep.txt --no-llm --preview -o out/smoke_build.mp4

# ---------------------------------------------------------------- Reel Studio (the web app)
serve:
	$(REEL) serve --open

ui-install:
	cd ui && npm ci

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

ui-test:
	cd ui && npm test

ui-check:
	cd ui && npm run typecheck && npm test && npm run build

clean:
	rm -rf out .pytest_cache .mypy_cache .ruff_cache
