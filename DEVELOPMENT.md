# YOLOmux — Development

Conventions, architecture, and code layout for contributors and AI agents are in [`AGENTS.md`](AGENTS.md). This file covers the build and test workflow.

## Setup

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt   # adds pytest-xdist for parallel test runs
```

## Static build

The interactive frontend is edited in ordered partials:

- `static_src/js/yolomux/` — JS source files, loaded in filename order
- `static_src/css/yolomux/` — CSS source files, loaded in filename order

After changing any source file, rebuild the served single-file assets:

```bash
python3 tools/static_build.py
```

This writes `static/yolomux.js` and `static/yolomux.css`. Run with `--check` before committing to verify generated assets match source:

```bash
python3 tools/static_build.py --check
```

The pre-commit check (`python3 -m pytest tests -n 4 -q`) runs this automatically.

## Dev vs production servers

Two instances run side-by-side:

| Instance | Directory | Port | Purpose |
|----------|-----------|------|---------|
| dev | `~/yolomux.dev/` | 7778 | Active development |
| prod | `~/yolomux/` | 7777 | Stable copy, synced from dev after verification |

See [`AGENTS.md`](AGENTS.md) for the commit → push → sync workflow.

## Running the dev server

```bash
python3 yolomux.py --port 7778 --self-signed
```

Or use the restart scripts documented in `AGENTS.md` (systemd-run based, harness-safe).

## Tests

```bash
python3 -m pytest tests -n 4 -q          # full suite, 4 workers
python3 -m pytest tests/layout_url.test.js  # JS unit tests (run via Node)
node tests/layout_url.test.js            # run JS tests directly
```

Pre-commit checks (run automatically before each commit):

```bash
python3 -m py_compile yolomux_lib/*.py   # syntax check
python3 -m pytest tests -n 4 -q         # test suite
python3 tools/static_build.py --check   # generated asset drift check
node --check static/yolomux.js          # JS syntax check
node tests/layout_url.test.js           # JS unit tests
```

## xterm.js assets

YOLOmux serves xterm.js from a local install when available. It checks `YOLOMUX_XTERM_ROOTS` first, then `static/xterm`, then common Cursor, VS Code, and Windsurf server installs under the home directory. If `/static/xterm.js` or `/static/xterm.css` is missing, the browser falls back to jsDelivr.

## Localization

The UI ships in 13 languages. Locale files are in `static_src/` (search for `t('` call sites). When adding a new user-facing string, add the key to all 13 locale files: English, Traditional and Simplified Chinese, Spanish, Japanese, German, French, Brazilian Portuguese, Russian, Korean, Hindi, Arabic, and Hebrew, plus `en-XA` (pseudo-locale for QA).
