# Development

Go is the default server for managed/operator launches, including `boot.sh`, `make run`, and `yolo-dev-start`. The Python server is deprecated and available only as an explicit temporary fallback while the remaining Go capability gaps close; removal is planned around late October 2026. Go still uses Python to render the shared HTML shell, and Python remains required for contributor tooling. This page keeps the commands contributors need; code-level ownership rules live in `AGENTS.md`.

## Setup and checks

Install the Python runtime and development dependencies with `make dev`. Python 3.10 or newer is required. Runtime dependencies, including `watchfiles>=1.2.0`, and the `dev` extra (`mypy`, `pillow`, `pytest`, `pytest-timeout`, `pytest-xdist`, and `selenium`) are declared in `pyproject.toml`. The Go build uses the Bazel version pinned in `.bazelversion`; Docker is required for the isolated Go test lane.

Run the canonical gate before landing a change:

```bash
python3 tools/check.py
```

The runner lists available lanes with `python3 tools/check.py --list-lanes` and runs independent lanes in parallel by default. For focused work, use `python3 tools/check.py --lane <name>`. The `bazel-go` lane owns the complete Go suite; do not run the full gate and the Go wrapper back-to-back on the same tree. Linux makes 100% of that capacity available to pytest by default; `--cpu-percent` and `YOLOMUX_CHECK_CPU_PERCENT` can lower the share. For example, 32 logical but 24 schedulable Linux CPUs produce 12/8/4 non-browser/browser/e2e workers.

The default gate is development validation, not latency certification. Use `python3 tools/check.py --certify` for the explicit certification run or `python3 tools/check.py --certification-only` when certification is the only requested lane; a normal green gate reports `NOT CERTIFIED`.

Use `python3 -m pytest <path-or-test>` for a focused Python test. `node tests/layout_url.test.js` runs the focused Node/browser-source shards. Automated browser and Go tests use fixture-owned state and ports; they never use the operator server on port 7220.

Frontend source lives in `static_src/`. `static/yolomux.js` and `static/yolomux.css` are ignored build outputs and are not tracked by Git. `make setup`, `make dev`, and `tools/check.py` generate them when missing or stale; after frontend source changes, run `python3 tools/static_build.py`. Use `python3 tools/static_build.py --check` to verify the generated files without writing them. Focused browser and Node tests need the generated bundles; run `make dev` or the builder first on a fresh checkout.

Build Go with:

```bash
bazel --bazelrc=/dev/null build --jobs=auto //cmd/yolomux-go:yolomux_go
```

Run `python3 tools/check.py --lane bazel-go` for validation. `tools/run_bazel_tests.sh` is the direct equivalent when only that complete Go suite is needed.

## Local servers

After a change, restart the active development server before reporting it ready. `./boot.sh <port>` starts Go by default after the Bazel binary is built. The `yolo-dev-start` workflow also starts Go explicitly for configured numbered checkouts. Use `./boot.sh --deprecated-python <port>` only when validating the deprecated Python fallback. Do not use a raw `python3 yolomux.py` launch for the normal server; that direct legacy entrypoint can still select Python unless `--backend go` is passed.

Port 7220 is the operator runtime. Port 7221 is reserved for a manual Go preview. Automated tests must use fixture-owned ports. Restart production only when explicitly requested.

Verify the selected listener and health endpoint after restart. For Go, check `/healthz`, `/api/capabilities`, the process checkout, and the exact browser path affected by the change. A source test does not prove which bundle a running server serves.

NFS-mounted source checkouts are allowed, but tests, browser profiles, sockets, caches, logs, and evidence must use host-local temporary storage. Do not point two hosts at one mutable runtime or database root.

## Landing changes

Local `cps` rebases onto local `main` and fast-forwards the integration checkout; it does not bump a version or push. `cps origin` rebases onto `origin/main`, includes the version bump in the work commit, fast-forwards local `main`, then pushes after the merge succeeds. Both modes require the canonical gate first. Stage explicit files only; never use `git add -A` for a release.

`cps` does not restart servers. Restart and verify the active development server separately. Production restart requires an explicit request.

## Current owners

- `GO_MIGRATION.md` is the current Go route, capability, and migration-status guide.
- `specs/BACKEND_ARCHITECTURE.md` summarizes the Python and Go process models.
- `specs/GUI.md` holds durable visual and interaction rules; current browser tests own exact assertions.
- `YOAGENT.md` describes the Python YO!agent feature and its supported operations.
- `README.md` is the end-user quickstart; Git tags are the authority for exact release contents.
