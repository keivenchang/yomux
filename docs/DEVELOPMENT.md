# Development

Go is the default server for managed/operator launches, including `boot.sh`, `make run`, and `yolo-dev-start`. The Python server is deprecated and available only as an explicit temporary fallback while the remaining Go capability gaps close; removal is planned around late October 2026. Go still uses Python to render the shared HTML shell, and Python remains required for contributor tooling. This page keeps the commands contributors need; code-level ownership rules live in `AGENTS.md`.

## Setup and checks

Install the Python runtime and development dependencies with `make dev`. Python 3.10 or newer is required. Runtime dependencies, including `watchfiles>=1.2.0`, and the `dev` extra (`mypy`, `pillow`, `pytest`, `pytest-timeout`, `pytest-xdist`, and `selenium`) are declared in `pyproject.toml`. `.bazelversion` owns the Bazel version; `MODULE.bazel` owns the Go SDK and Bazel rules, with a source check keeping the `go.mod` rules_go mirror aligned for Go runfiles imports. Docker is required for the certified isolated Go test lane; the runner also supports warned native execution on machines without Docker. Image preparation resolves and verifies the pinned offline dependency inputs before tests run without network access.

Run the canonical gate before landing a change:

```bash
python3 tools/check.py
```

The runner lists available lanes with `python3 tools/check.py --list-lanes` and runs independent lanes in parallel by default. For focused work, use `python3 tools/check.py --lane <name>`. The `bazel-go` lane owns the complete Go suite; do not run the full gate and the Go wrapper back-to-back on the same tree. `auto` uses Docker when its execution contract is available and otherwise selects native execution with a warning; pass `--execution native` to select it explicitly. Native mode uses host binaries found on `PATH`, and skips its Chrome E2E target when Chrome/Chromium or ChromeDriver is unavailable. On macOS it also selects the macOS Go SDK. Linux shares 100% of that capacity between pytest and Bazel by default; `--cpu-percent` and `YOMUX_CHECK_CPU_PERCENT` can lower the share. For example, 32 logical but 24 schedulable Linux CPUs produce 6/4/2 pytest workers and 10 Bazel jobs.

Go YO!stats supports host CPU and memory, process CPU and RSS, agent inventory, and daemon-process CPU and RSS on Linux and macOS. macOS collection uses Mach host counters and libproc process data; other operating systems report those measurements unavailable. New samples are collected at the existing product cadence, so older persisted gaps remain unavailable.

The default gate is development validation, not latency certification. Use `python3 tools/check.py --certify` for the explicit certification run or `python3 tools/check.py --certification-only` when certification is the only requested lane; a normal green gate reports `NOT CERTIFIED`.

Use `python3 -m pytest <path-or-test>` for a focused Python test. `node tests/layout_url.test.js` runs the focused Node/browser-source shards. Automated browser and Go tests use fixture-owned state and ports; they never use the operator server on port 7220.

Frontend source lives in `static_src/`. `static/yomux.js` and `static/yomux.css` are ignored build outputs and are not tracked by Git. `make setup`, `make dev`, and `tools/check.py` generate them when missing or stale; after frontend source changes, run `python3 tools/static_build.py`. Use `python3 tools/static_build.py --check` to verify the generated files without writing them. The old `/static/yolomux.js` and `/static/yolomux.css` URLs resolve to the same bundles during the transition. Focused browser and Node tests need the generated bundles; run `make dev` or the builder first on a fresh checkout.

Build Go with the platform-selected SDK:

```bash
tools/build_go.sh
```

The build and test wrappers use the same SDK selection: Go 1.27.1 on Linux and macOS, where its Mach-O UUIDs are required by local-network binaries. Bazel 9.2.0 is pinned in `.bazelversion`; `MODULE.bazel` owns the SDK, rules_go, Gazelle, rules_shell, and platforms versions. Run `python3 tools/check.py --lane bazel-go` for the complete Go suite. `tools/run_bazel_tests.sh` runs that lane directly and accepts `--jobs N`, `--memory-mb N`, `--target LABEL`, `--execution auto|native|docker`, `--cache=on|off`, `--reuse-test-results`, `--rerun-tests`, and trailing Bazel arguments after `--`. Docker mode uses Bazel’s hermetic Linux sandbox inside a network-disabled, non-root test container and reuses passing test results by default. `--rerun-tests` forces test execution; `--cache=off` disables the shared disk action cache and also forces test execution. `--reuse-test-results` remains accepted for compatibility and conflicts with `--rerun-tests`; test freshness options do not change action keys. Docker test cache identity includes the exact image ID, Linux kernel/platform, pinned seccomp policy, and sandbox configuration. Each test gets private HOME, temporary files, PTY devices, PID/network namespaces, and Bazel-declared runfiles; checkout Git metadata and undeclared checkout files are unavailable inside the test sandbox. Docker admission rechecks the image, mounts, credentials, network, and Linux sandbox before every invocation, including cache hits. `auto` selects native execution with a warning when Docker isolation cannot be admitted. `--execution docker` fails admission instead of falling back. Native mode uses the machine’s existing `PATH` for tmux, Git, Python, browser tools, agent clients, and other test executables; those binaries and host state are not pinned, so native results remain outside the Docker hermeticity guarantee and test-result reuse stays opt-in. Bazel itself must match `.bazelversion`, and Go uses the SDK selected by `MODULE.bazel`. Native `--memory-mb` sets Bazel scheduling capacity with `--local_resources`; it does not impose a process memory limit. Docker applies a hard container memory limit. Native invocations use private writable execution state by default and use a user-owned mode-0700 persistent output root only when test-result reuse is explicitly enabled with `--cache=on`; compiled actions use the user-owned disk cache. Trailing Bazel arguments may select tests and test filters; options that weaken the execution contract are rejected.

## Local servers

After a change, restart the active development server before reporting it ready. `./boot.sh <port>` starts Go by default after the Bazel binary is built. The `yomux-dev-start` workflow also starts Go explicitly for configured numbered checkouts. Use `./boot.sh --deprecated-python <port>` or `python3 yomux.py --backend python` only when validating the deprecated Python fallback. Direct `python3 yomux.py` starts Go by default.

To keep an existing HTTPS identity during a backend cutover, pass `./boot.sh --force --no-dev --port <port> --cert <certificate.pem> --key <private-key.pem>`. Both readable files are required before the launcher touches logs or listeners. Direct launches honor explicit `YOMUX_CONFIG_DIR`, `YOMUX_STATE_DIR`, `YOMUX_CACHE_DIR`, and `YOMUX_START_LOCK_DIR` values; use the same roots for authenticated probes.

Managed startup does not gate or wait on CPU usage, memory usage, or OS load averages. The legacy `--ignore-load` option and `YOMUX_START_LOAD_WAIT_SECONDS` setting are accepted only for compatibility and do not affect startup. Root validation, instance ownership, startup locking, and listener readiness checks remain enforced.

Port 7220 is the operator runtime. Port 7221 is reserved for a manual Go preview. Automated tests must use fixture-owned ports. Restart production only when explicitly requested.

Verify the selected listener and health endpoint after restart. For Go, check `/healthz`, `/api/capabilities`, the process checkout, and the exact browser path affected by the change. A source test does not prove which bundle a running server serves.

NFS-mounted source checkouts are allowed, but tests, browser profiles, sockets, caches, logs, and evidence must use host-local temporary storage. Do not point two hosts at one mutable runtime or database root.

## Landing changes

Local `cps` rebases onto local `main` and fast-forwards the integration checkout; it does not bump a version or push. `cps origin` rebases onto `origin/main`, includes the version bump in the work commit, fast-forwards local `main`, then pushes after the merge succeeds. Both modes require the canonical gate first. Stage explicit files only; never use `git add -A` for a release.

`yomux_lib/version.py` is the only product-version source. Python package metadata reads it directly, and Bazel generates the Go backend's version from it; `.bazelversion`, the Go SDK, and `go.mod` are toolchain pins, not product versions. The check runner verifies that the Docker Bazel pin matches `.bazelversion`, Docker's Linux Go SDK matches `MODULE.bazel` including its checksum, the macOS SDK selector matches its Bazel SDK, and `go.mod`'s language version matches the SDK's major/minor. A global release commit subject must be `Release YOmux <version>`, its annotated tag must be named `v<version>` with tag message `YOmux <version>`, and the tag must point to that release commit. The canonical gate checks the product source, Go version generation, and any release commit or tag at `HEAD`; create the tag only after the release commit passes that check, and push `main` and the tag atomically.

`cps` does not restart servers. Restart and verify the active development server separately. Production restart requires an explicit request.

## Current owners

- `GO_MIGRATION.md` is the current Go route, capability, and migration-status guide.
- `specs/BACKEND_ARCHITECTURE.md` summarizes the Python and Go process models.
- `specs/GUI.md` holds durable visual and interaction rules; current browser tests own exact assertions.
- `YOAGENT.md` describes the Python YO!agent feature and its supported operations.
- `README.md` is the end-user quickstart; Git tags are the authority for exact release contents.
