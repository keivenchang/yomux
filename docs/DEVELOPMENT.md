# Development

This guide is for contributors. User setup is in the [README](../README.md); process and storage design is in [Backend architecture](specs/BACKEND_ARCHITECTURE.md).

Go runs the product. Python is used for the repository gate, developer controllers, mocks, and fixture builders; it is not needed to install or run YOmux.

## Setup and build

| Command | What it does |
| --- | --- |
| `make setup` | Builds the native server and browser assets, then installs `yomux` and `yolomux` below `PREFIX` (default `~/.local`). The installed commands run the binary and assets in `PREFIX/lib/yomux`, and `install-source.json` records the Git checkout used for self-update. |
| `make build` | Builds the native server (`tools/build_go.sh`) and publishes browser assets in this checkout. Run it after frontend source changes. |
| `make dev` | Installs the Python developer and test tools (`pip install -e ".[dev]"`), then runs `make build`. |
| `tools/build_go.sh` | Builds Go and the native asset generator with the platform-selected SDK. |

Versions are pinned: Bazel 9.2.0 in `.bazelversion`; Go 1.27.1, rules_go, Gazelle, rules_shell, and platforms in `MODULE.bazel`. The same SDK selection applies on Linux and macOS (macOS needs it for the Mach-O UUIDs that local-network binaries require).

Browser dependencies are pinned too. `assets/runtime/browser-assets.json` pins browser-library archives, fonts, Unicode/CLDR inputs, and editor input and output hashes; `assets/runtime/pdfjs-assets.json` pins PDF.js. Setup downloads each file, checks its checksum, and only then publishes it. Node.js and npm build the editors from their lockfiles only when no verified editor cache exists. Content-addressed caches allow offline repair and install. Setup and build may use the network; startup and tests never download anything. Downloaded and generated files stay out of Git.

### Linux inotify limits

Linux hosts that run the full gate need higher inotify limits. Product startup only warns about low limits (in the server log and browser, with measured values and a command that raises only the low ones until reboot); the gate refuses to run without them and also requires a measured number of free inotify instances. The values come from `cmd/yomux-go/runtime_limits.json`. To make them permanent:

```bash
printf '%s\n' fs.inotify.max_user_instances=1024 fs.inotify.max_user_watches=1048576 fs.inotify.max_queued_events=65536 | sudo tee /etc/sysctl.d/90-yomux-inotify.conf
sudo sysctl --system
```

### Frontend source and generated files

- Browser source lives in `static_src/`; authored locale catalogs live in `static_src/locales/`.
- Generated and ignored: `static/yomux.js`, `static/yomux.css`, `static/emoji-data.js`, `static/locales/`, editor bundles, and dependencies under `static/vendor/` and `static/fonts/`. The tracked xterm vendor files ship with the runtime; `make xterm` checks them against the pinned npm packages.
- The emoji catalog is generated from pinned Unicode/CLDR inputs into `node_modules/.yomux/emoji_data.js`.
- `make setup`, `make dev`, and `tools/check.py` regenerate missing or stale files. `python3 tools/static_build.py --check` checks them without writing.
- The old `/static/yolomux.js` and `/static/yolomux.css` URLs serve the same bundles.
- Focused browser and Node tests need prepared dependencies and bundles; on a fresh checkout run `make build` first. Tests never download a missing dependency; rerun setup instead.

The Bazel frontend action and self-update use the native `tools/staticgen` generator. Its output directory must be private and outside any Git worktree, and it rejects symlinked output paths. Asset order, pseudo-locale rules, and locale metadata live in shared JSON inputs. The native operator publishes checkout assets under the shared checkout writer declaration. The retained Python builder (`tools/static_build.py`) runs developer architecture and localization checks under the same declaration; watch mode reloads changes to the shared inputs.

At managed startup, the native `startup-assets` step regenerates application assets from already prepared inputs in a source checkout, or verifies the packaged assets of an installation without using the recorded checkout. Missing or corrupt dependencies fail with setup instructions instead of downloading. After readiness the launcher checks that the served JavaScript and CSS have the right status and content type.

`tests/fixtures/static_assets_contract.json` holds byte hashes produced by the original Python builder at a recorded source commit. After an intentional frontend change, generate the Python and native outputs in separate private directories, compare every file byte for byte, and only then refresh the hashes from the Python output. Record the source commit and `git hash-object tools/static_build.py`. Never copy hashes from a failing Go test into the fixture.

Asset publication, installation-source checks, and runtime version metadata read Git through a shared environment that disables global and system Git configuration and repository redirection. Network update commands keep their credential configuration.

## The gate

Run the full gate before landing a change:

```bash
python3 tools/check.py
```

It runs independent lanes in parallel. Useful options:

| Option | Use |
| --- | --- |
| `--list-lanes` | Print lane names. |
| `--lane NAME` | Run one lane; repeatable. `bazel-go` is the complete Go suite. |
| `--serial`, `--serial-lanes` | Run lanes one at a time, for readable failures. |
| `--execution auto\|docker\|native\|qualified-native` | Choose where tests run (see below). |
| `--cpu-percent 1-100` | Lower the CPU budget (also `YOMUX_CHECK_CPU_PERCENT`). |
| `--certify`, `--certification-only` | Add or run only the exclusive latency certification phase. |

The default gate is functional validation, not latency certification; a normal green run reports `NOT CERTIFIED`. Differ history records cold and prepared timings during the functional run, but its 250 ms ceilings, host qualification, and the renderer-pressure sensitivity check (fixed 1.15 busy-to-quiet ratio) run only in the certification phase. Latency certification is a Docker workflow.

Do not run the full gate and then `tools/run_bazel_tests.sh` on the same unchanged tree; the gate already runs the Go suite once.

`required_browser` marks browser regressions that must run in the browser lane; missing browser dependencies fail those required cases. Tests and fixtures that launch the Go runtime call the shared locked binary builder when needed; selecting Python-only tests does not require Go.

Tests use fixture-owned state and ports. They never use the operator server on port 7220, and port 7221 is reserved for manual Go previews. A fixed live port is a manual smoke target only and cannot replace the gate.

### Where tests run

- `auto` (default): Docker on Linux, native on macOS. On Linux, if Docker is unreachable or cannot provide isolation, the run stops with a warning; there is no automatic fallback.
- `docker`: fails admission instead of falling back.
- `native`: uses host binaries from `PATH` (tmux, Git, Python, Chrome, agent clients). Results are diagnostics only and do not establish Docker isolation or latency certification. It skips its Chrome E2E target when Chrome/Chromium or ChromeDriver is missing. On macOS it selects the macOS Go SDK.
- `qualified-native`: the full parallel functional gate without Docker, from a prepared bundle. See [Qualified native gate](#qualified-native-gate).

`YOMUX_CHECK_CONTAINER=0` controls host orchestration; it does not select native Bazel execution.

### CPU budget

Linux budgets pytest workers and Bazel jobs against 100% of schedulable capacity by default; `--cpu-percent` and `YOMUX_CHECK_CPU_PERCENT` can lower that budget. Browser and E2E workers each reserve two CPUs for Chromium and their fixture server. Pytest containers receive hard CPU quotas from the worker allocation: one CPU per non-browser worker and two per browser or E2E worker, with a 10 ms quota period to reduce throttling pauses. Worker counts and parallel lanes do not change, and each pool is capped at its reserved CPU share. Below six schedulable CPUs, or when explicit worker or job overrides exceed the budget, minimum one-worker pools can overcommit it; the Bazel container keeps its configured CPU limit. For example, 32 logical but 24 schedulable Linux CPUs produce 6/4/2 pytest workers and 6 Bazel jobs.

### macOS

- `python3 tools/check.py` selects native execution (`--execution native` is the same). Do not start Docker Desktop for a check.
- Bazel checks need JDK 25 or newer: `brew install openjdk@25`, or set `JAVA_HOME`. The runner passes it with `--server_javabase` because Homebrew's Bazel wrapper can overwrite `JAVA_HOME`. Older or missing explicit JDKs fail admission. (Bazel 9.2 uses [virtual threads for disk-cache work](https://raw.githubusercontent.com/bazelbuild/bazel/9.2.0/src/main/java/com/google/devtools/build/lib/remote/disk/DiskCacheClient.java), which Java 21 can pin while holding monitors; [JDK 24 fixed that](https://docs.oracle.com/en/java/javase/25/migrate/significant-changes-jdk-24.html), and JDK 25 is the LTS baseline.) Keep cached inputs and test concurrency enabled.
- Prepare the locked xterm fixture once: `python3 tools/native_bundle_acquire.py --prepare-xterm ~/.cache/yomux/native-fixtures ~/.cache/yomux/native-fixture-acquisition --document-lock-helper ~/dev/ai-config/agents/skills/doc-edit-lock/scripts/doc_lock.py`. Set `YOMUX_XTERM_PACKAGE_ROOT` to use another prepared `node_modules` directory.
- The native Go lane needs Claude, Codex, and OpenCode on `PATH`. Prepare the locked clients with `python3 tools/native_bundle_acquire.py --prepare-agents ~/.cache/yomux/native-fixtures --document-lock-helper ~/dev/ai-config/agents/skills/doc-edit-lock/scripts/doc_lock.py` and add the printed directories to `PATH`. Preparation is separate from the gate and refuses frozen bundles.
- Browser discovery is offline: `CHROME_BIN` and `CHROMEDRIVER_BIN`, then `PATH`, then installed app bundles and a compatible driver already in the Selenium cache. Invalid explicit overrides fail. A missing browser is recorded as an omitted `requires_chrome` target and does not count as browser coverage.

### Required browser checks

- HTML ViewEdit: the default browser lane edits `examples/11-html.html` in Chromium: clicking rendered text, typing, and saving in View and Split, plus touchscreen taps after and during loading. Skipping any case fails the gate. The plan is in [HTML ViewEdit](specs/HTML_VIEWEDIT.md).
- Native browser checks use layout-independent logical key events, explicit device metrics for narrow viewports, and wait for the active editor and completed render. They keep content, identity, geometry, and transition assertions.
- Terminal URLs: the default browser lane launches the installed real Codex and Claude clients in private tmux fixtures against the local fake provider. It uses five URLs at 75 columns, replays the rows with and without OSC-8 metadata, and checks real clipboard reads and Chromium `Page.windowOpen` destinations from both ends of every row. Its Chromium has external DNS blocked so redirects cannot hide a truncated URL. Codex fixtures disable daemon auto-start so the real TUI owns its engine and thread.
- Agent status: the same launches hold a provider request open across two status scans and require the agent to stay "working" in metadata, persisted status, and the rendered indicator, then become idle or cooling down after the response. Codex also streams a held commentary response with no working row; it must stay working until the turn completes. Go status tests read the real prompt corpus and animation-to-completion sequences.
- Missing clients or skipped samples fail the step. Docker and qualified-native runs use pinned clients; native diagnostics use installed ones.

Run the URL sample directly:

```bash
YOMUX_BROWSER_BLOCK_EXTERNAL_DNS=1 YOMUX_URL_WIDTHS=75 YOMUX_GO_BINARY=<built-native-binary> python3 -m pytest -q tests/test_browser_real_agent_urls.py -m real_agent_urls
```

`YOMUX_URL_RANDOM=1 YOMUX_URL_WIDTHS=40,64,75,80,120` runs the seeded 100-URL matrix (the gate always uses 75 columns). `YOMUX_URL_EVIDENCE` picks the evidence directory. `YOMUX_URL_NEGATIVE_CONTROL=1` restores the original grouping function and disables the adapter; the two-space case must then fail through the real menu action. Go regular and race tests separately drive the real PTY/WebSocket bridge across every byte split of OSC-8 and Unicode examples and across the 32 KiB read boundary; hyperlink interpretation stays in the shared browser resolver.

### Focused tests

- `python3 -m pytest <path-or-test>` runs one Python test.
- `node tests/layout_url.test.js` runs the focused Node and browser-source shards.
- `python3 tools/check.py --lane bazel-go` runs the complete Go suite.
- The six strict xfails in `tests/test_gate_sweep_p.py`, `tests/test_gate_contracts_q.py`, and `tests/test_gate_tmux_recovery.py` are placeholders for deferred session-recovery coverage. They do not test recovery or show a known defect, and no queue currently owns that work. The old SubsystemSpec controls and three-card Daemons expectations are out of scope; the current roster tests own that presentation.
- Installed-launcher audits are opt-in: set `YOMUX_DEV_START_SH` to the exact launcher and run `python3 -m pytest tests/test_launcher_row_wiring.py`. An explicitly missing target fails. The gate always tests this repository's own boot, environment, nohup, and startup-lock contracts.
- Python controllers may start native Go fixture servers and build fixed input data. The removed Python server is not a test oracle, and there is no flag to bring it back.

### Bazel test wrapper

`tools/run_bazel_tests.sh` runs the `bazel-go` lane (`//:go_tests`) directly:

```bash
tools/run_bazel_tests.sh --jobs "$(getconf _NPROCESSORS_ONLN)"
```

Options: `--jobs N` (default: online CPU count), `--memory-mb N`, `--target LABEL`, `--execution auto|native|qualified-native|docker`, `--cache=on|off`, `--reuse-test-results`, `--rerun-tests`, and Bazel arguments after `--`. Options that weaken the execution contract are rejected.

`//:go_tests` covers Go unit and race suites for the server, capabilities, Finder, stats, fake LLM, and scheduler; Go binary, API, and Bazel action-environment contracts; Docker runtime contracts; Chromium Finder, editor, Differ, and YO!stats end-to-end tests; and agent-status integration. The command-server package runs as eight normal and eight race shards with the medium timeout class. The stats restart check keeps the last good snapshot from the current server generation across pending or retryable responses, uses a 15-second publication deadline, and keeps error bodies as failure evidence.

Docker mode:

- Starts `docker/run-tests.sh` on the host, which mounts the source read-only and runs Bazel's hermetic Linux sandbox in a network-disabled, non-root container with a hard memory limit, and reuses passing test results by default. `--rerun-tests` forces execution; `--cache=off` also disables the shared disk action cache. `--reuse-test-results` is kept for compatibility and conflicts with `--rerun-tests`.
- The test cache key includes the image ID, kernel and platform, pinned seccomp and AppArmor policies, and sandbox configuration. Admission rechecks the image, mounts, credentials, network, and sandbox before every run, including cache hits.
- Each test gets a private HOME, temporary files, PTY devices, PID and network namespaces, and Bazel-declared runfiles. Git metadata and undeclared checkout files are not visible.
- Image preparation checks the tracked registry pin and buildifier module graph before downloading, and may use the network; test execution stays offline. Gate reports include structured execution records, including admission failures and omitted native targets.

Native mode uses the host `PATH` for tmux, Git, Python, browsers, and agent clients, so results are outside the hermetic guarantee and test-result reuse is opt-in. Bazel must match `.bazelversion`. `--memory-mb` only sets Bazel's scheduling capacity (`--local_resources`), not a memory limit. Native runs use private writable state, and a user-owned mode-0700 output root only with `--cache=on`; compiled actions use the user-owned disk cache.

On AppArmor Linux hosts, install the dedicated policy once with `python3 tools/bazel_execution_contract.py --install-apparmor` (uses non-interactive sudo). Regular runs only validate it: the compiled source must match the kernel's policy digest in enforce mode, or admission is refused. The profile keeps Docker's filesystem restrictions and allows the nested namespace mounts and pivot-root that Bazel needs; outer capabilities stay dropped. Repeat the install after a reboot or a policy change.

### Qualified native gate

`--execution qualified-native` runs the parallel functional gate without Docker, from a prepared bundle. Prepare the bundle first; the gate never downloads anything. On macOS the bundle must be outside the operator's HOME. Secondary scratch storage is optional; `/tmp` works.

```bash
# macOS preparation (network allowed). Needs arm64 and matching Python, Node, Go, Bazel, JDK, Command Line Tools, and cached Chrome/ChromeDriver.
python3 tools/native_bundle_acquire.py --output /tmp/yomux-native-bundle

# Linux preparation from the comparison image's full sha256 ID (Docker is used only here).
python3 tools/native_bundle_acquire.py --output /tmp/yomux-native-bundle --linux-image "$YOMUX_COMPARISON_IMAGE"

# Offline execution.
export YOMUX_NATIVE_BUNDLE=/tmp/yomux-native-bundle/payload
python3 tools/check.py --execution qualified-native
tools/run_bazel_tests.sh --execution qualified-native

# Focused pytest through the same runner.
YOMUX_BAZEL_EXECUTION=qualified-native python3 -m pytest tests/test_native_bundle_contract.py
```

A host without Docker can receive a prepared bundle, or freeze an existing layout with `tools/native_bundle_prepare.py --layout <layout> --output <bundle-directory>`.

What the bundle and runner guarantee:

- The manifest locks file bytes, modes, links, executable paths, and Python package versions. Each gate session verifies it, captures the source and Git context, and admits tools, offline Go dependencies, and the full dependency graph before any result-bearing test. Final acceptance requires worker cleanup and a full bundle re-verification.
- Each lane reuses one private worker and Bazel server, with a fresh uncached sandbox probe before cached results count. Output leases stay held until processes retire; a failed retirement quarantines the slot.
- Required browsers and agent clients cannot be omitted.
- `BUILD.bazel` owns the coverage inventory (the Go suite plus the Python, Node, static, and integration steps); Git revision and dirty-state checks stay in orchestration. Result identities include the bundle, platform, SDK, and execution policy; Docker and qualified-native results use separate caches.
- Linux: requires the dedicated AppArmor policy, hermetic `linux-sandbox`, private devpts, namespaces, and dropped capabilities; the runtime filesystem comes from the bundle; each entrypoint owns a child subreaper so detached descendants cannot escape cleanup.
- macOS: stock Bazel with processwrapper staging. Each test command and all its descendants run under one Seatbelt policy that denies external networking, reads and writes in the operator's home, launchd jobs, LaunchServices launches, Apple Events, and writes outside fixture state, temporary state, declared outputs, and required devices. Setup, repository rules, and non-test build actions are not sandboxed; execution records show this as `test-process-tree` with `setup_os_sandboxed=false` and `non_test_actions_os_sandboxed=false`. Known limits: undeclared host reads outside blocked paths, host localhost access, and host process visibility; services reachable over localhost or Unix sockets run outside the policy. The allocation journal and launchd coalition supervisor run outside the sandbox, and owned descendants must retire before slots are released.

## Diagnostic tools

### Stats crash and restart check

```bash
python3 tools/verify_go_stats_restart.py --binary <built-Go-binary> --output /tmp/yomux-stats-restart.json
```

It uses private client roots, tmux, config, state, databases, and listeners. It replays a 64-file, roughly 64 MiB backlog with 180 one-second live appends, kills only its own Go process at 90 seconds, reconnects authenticated SSE, and checks tokens, deduplication, checkpoints, and coarse-product convergence. It is a fixture check, not a production benchmark.

### Mac Repo Diff cache qualification

On macOS, the Git adapter uses one service-owned cache of immutable object and loose-ref trees. Each request keeps HEAD, index, packed refs, shallow metadata, and sanitized config private. The cache checks descriptor identity and complete inventory stamps (including ctime) before reuse, holds leases until the namespace closes, and builds each inventory once even under concurrent requests. A warm history request must not rebuild the object or ref snapshots. Capacity covers both tree types across repositories, and reused entries still count against namespace limits.

Do not remove, bypass, make request-local, or replace this cache or its reuse assertions unless the replacement is confirmed on a real Mac. This applies to people and AI agents, including refactors of Git history, stats, Differ, service construction, fingerprinting, clone options, or namespace cleanup. A Linux gate does not run the Darwin tests, a shared return path does not prove clones are not duplicated, and HEAD-only or TTL-only cache keys miss ref moves, object changes, and namespace replacement.

To qualify a change on macOS:

1. Run `tools/run_bazel_tests.sh --target //internal/finder:finder_test -- --test_filter=TestDarwin --test_output=all`, and the same with `finder_race_test`. Require zero new cached-tree builds across repeated history loads, one clone builder for concurrent identical requests, prompt waiter cancellation, correct cold and warm entry budgets, and intact leased generations after mutation. Keep coverage for same-size writes with restored mtime, source symlinks, loose-ref updates, packed-ref transitions, absent refs, linked worktrees, and source swap-and-restore during Git execution. Do not weaken these assertions.
2. Rebuild and restart the development server. Repeat authenticated requests for a real repository: `/api/fs/git-history?path=<repo>&limit=50&worktree=0`, `worktree=only`, and the history statistics stream. Record the first and at least three warm requests (status, commit count) and the rendered Repo Diff in real Chromium. Compare the same workload before and after; do not claim Mac performance from a synthetic response or a Linux measurement.

Reference measurements: on 2026-10-14, yo8880 history took 7.17 s cold and 1.53 to 1.80 s warm, against 22 ms for direct Git; profiling showed repeated object-file opens and uncached loose-ref cloning (the object cache from v0.10.2 existed, but loose refs never used it). After the fix, on base `e8e1e824590962ee5e333ed782877762c2007bc0`, 26 Darwin race tests passed, a negative control without build coalescing failed with two clone builders, and three warm requests returned 50 commits in 0.838, 0.664, and 0.664 s (working-tree status 0.607 s). Chromium rendered 31 history rows and 38 commit counts with a matching served bundle; the first cold load still took 6.136 s because the cache does not survive restart. The broader native Finder target had 230 passes and 33 failures, all 33 also failing on the unchanged base `4d33cbc967ef13772f1339f58189816a70952c9e`. These results do not establish a green gate or release readiness.

### Descriptor history prototype

An opt-in command compares the snapshot history reader with go-git v5.19.3 in a separate helper process. Normal Repo Diff requests keep using the snapshot reader.

```bash
bazel-bin/cmd/yomux-go/yomux-go_/yomux-go --diagnose-history-prototype --root ~/dev/yomux --repo ~/dev/yomux/yo8881 --limit 50
```

- `--root` (required) authorizes `--repo` through Finder. `--limit` is 1 to 200, `--offset` selects a page, and `--head` fails if HEAD changed.
- The JSON report has one initial and three repeat requests per reader, with elapsed milliseconds (including helper startup), HEAD, refs, commit metadata, pagination, errors, and differences. OS cache state is not controlled. Each read has a 30-second deadline; cancellation kills and reaps the helper, with no retries.
- It reads whole-repository metadata only: no diffs, statistics, worktree status, or signatures. Directory descriptors pin the Git, common, and object directories, every path component opens relative to them, and symlinks are rejected. Config includes and executable settings are ignored. The shared repository-extension allowlist applies; SHA-256, reftable, shallow boundaries, grafts, replacement refs, alternates, and decoration exclusions return explicit errors.
- It fails qualification when go-git's committer-time order differs from Git's `--topo-order`, or when HEAD, refs, pagination, or metadata differ. The equal-timestamp merge fixture shows an ordering difference, and a Mac checkout showed an extra `refs/.DS_Store` ref from go-git, so the prototype does not qualify as a replacement. Using it for Repo Diff would need new qualification evidence.

## Local servers

- After a backend change, rebuild and restart the development server before calling it ready. `./boot.sh <port>` and `yomux-dev-start` use the Go launcher. Installed `yomux` and `yolomux` need `--port`, `YOMUX_PORT`, or `YOMUX_DEFAULT_PORT`.
- Port 7220 is the operator runtime and 7221 is for manual Go previews. Automated tests use fixture-owned ports. Restart production only when explicitly asked.
- To keep an existing HTTPS identity during a cutover: `./boot.sh --force --no-dev --port <port> --cert <certificate.pem> --key <private-key.pem>`. Both files must be readable before the launcher touches logs or listeners.
- Direct launches honor `YOMUX_CONFIG_DIR`, `YOMUX_STATE_DIR`, `YOMUX_CACHE_DIR`, and `YOMUX_START_LOCK_DIR`; use the same roots for authenticated probes.
- Startup never waits on CPU, memory, or load average. `--ignore-load` and `YOMUX_START_LOAD_WAIT_SECONDS` are accepted and ignored. Root validation, instance ownership, startup locking, and listener readiness are still enforced.
- New configuration uses `YOMUX_*`. Legacy `YOLOMUX_*` names still work with a deprecation warning that prints no values; the `YOMUX_*` value wins, even when empty (empty keeps the default). The shell adapter is `tools/product_environment.sh`, and installed launcher copies must match it. Generated compatibility aliases carry fingerprints so child processes can tell them from new legacy input.
- The Linux workspace is `~/dev/yomux`, with shared Git metadata in `~/dev/yomux/yomux.repo` and worktrees below it.
- NFS-mounted source checkouts are fine, but tests, browser profiles, sockets, caches, logs, and evidence must use host-local temporary storage. Never point two hosts at one mutable runtime or database root.

macOS: managed servers run under `nohup`, with stdin from `/dev/null` and output appended to the per-port log. Manual restarts and the boot-time `local.yolomux.autostart.<port>` LaunchAgent use `boot.sh`, which clears inherited tmux settings and applies the captured root plan. Servers run outside tmux; tmux only hosts agents and session discovery. Verified replacement also retires a legacy per-port server tmux session. Set `AbandonProcessGroup=true` in the LaunchAgent so launchd keeps the detached server after `boot.sh` exits. Agent runners that clean up descendants should restart through the loaded job with `launchctl kickstart gui/$(id -u)/local.yolomux.autostart.<port>`, then verify the listener again.

### Verifying a restart

Check the listener, `/healthz`, `/api/capabilities`, the process checkout, and the exact browser path you changed. A source test does not prove which bundle a running server serves; confirm the served bundle matches the build.

For Tabber reordering changes, reproduce in Chromium with the pointer held over a row while recency changes. Assert unchanged row geometry, continued status and date updates, and the same click target in Normal and Advanced. Cover pane whitespace, open popovers, immediate re-entry, new and exited sessions, explicit sort, and disposal. Check relative date defaults, saved Date/None and URL migration, and later choices across reloads.

## Landing and releases

- Local `cps` rebases onto local `main` and fast-forwards the integration checkout; it does not bump the version or push. `cps origin` rebases onto `origin/main`, includes the version bump in the work commit, fast-forwards local `main`, and pushes after the merge succeeds. Both require the gate first. Stage explicit files; never `git add -A` for a release. `cps` does not restart servers.
- `version.yaml` is the product version. A release bump raises its quoted `major.minor.patch`. The Go build generates a version constant from it, and Python package metadata reads it too. `.bazelversion`, the Go SDK, and `go.mod` are toolchain pins. The gate validates the manifest, the generated Go version, and the release commit and tag.
- A release commit subject is `YOmux <version> <highlights>`, a short summary of what the release adds or fixes, and its body lists the features and fixes. Its annotated tag is `v<version>` with message `YOmux <version>` and must point to that commit. Tag only after the commit passes the gate, then push `main` and the tag together.

### Self-update

- Go checks the source checkout's `origin/main` at startup and hourly, unless `updates.notify_level` is `none`. It reads `version.yaml` from the fetched commit and applies the configured major/minor/patch threshold. `/api/update-status` returns the cached result, check time, and error; browsers query it on startup.
- Before offering Update & restart, the checker builds the pinned Go binary and static bundle in one reusable staging workspace and verifies the binary's version and launch support. Readiness (checking, ready, or failed) is published through status and events; a failed build keeps the notice but hides the upgrade action. Preparation has a 15-minute deadline, keeps the latest private build log, and stops only its own Bazel server. Admins can retry preparation (at most one manual retry per 30 seconds); a retry never installs anything.
- Update & restart reuses the verified artifacts, rechecks checkout and installation identity and the full static file inventory, fast-forwards, and relaunches the port through `boot.sh`. It rejects missing, modified, symlinked, or unexpected files, refuses dirty or diverged checkouts, and copies all browser assets without downloading. Installed commands find the source through `install-source.json`; a missing source fails the update but not startup. The installation prefix is refreshed before restart.
- Progress is at `/api/self-update/status`. The update completes only when a new instance reports the target version. Browsers then reload, waiting while there are unsaved edits. Failures after checkout activation show a managed recovery command; there is no automatic rollback.

Before tagging a release that changes self-update, run on native macOS from a clean checkout of the candidate:

```bash
python3 tools/verify_self_update_upgrade.py --candidate-ref <exact-candidate-SHA> --output <new-private-directory>
```

It selects the newest reachable release tag strictly below the candidate version, or the explicit `--base-tag vMAJOR.MINOR.PATCH`, and starts that published server against a disposable origin and runs real Bazel compilation, asset setup, staticgen, and managed restart, for both a linked checkout and an installed prefix. It requires preflight readiness, the matching completed job, the exact candidate SHA, a new PID reporting the target version, identical prepared and served bundles, and a browser reload that waits for unsaved edits. For the affected 0.11.0 base, its negative control restores the original archive writer, expects Git exit 128, and proves the old checkout and listener survive. Keep `result.json` and the private command logs with the candidate SHA. `--snapshot` requires the source checkout to match the selected base and tests local tracked changes through a disposable fixture commit one patch above that base; it does not certify a release. Fixtures use private roots and ports and never depend on a live server or Docker.

## Where things are documented

- [README](../README.md): user setup and features.
- [Go backend status](../GO_MIGRATION.md): routes, capabilities, and migration status.
- [Backend architecture](specs/BACKEND_ARCHITECTURE.md): process, storage, and filesystem boundaries.
- [UI contract](specs/GUI.md): visual and interaction rules; browser tests own the exact assertions.
- [YO!agent](YOAGENT.md): skills, context, and send rules.
- Git tags: exact release contents.
