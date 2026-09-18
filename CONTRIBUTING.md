# Contributing

YOLOmux accepts contributions only under the contributor terms in [`CONTRIBUTOR_TERMS.md`](CONTRIBUTOR_TERMS.md).

By opening a pull request, sending a patch, or otherwise submitting a contribution, you confirm that you wrote the contribution or have the right to submit it under those terms.

Do not submit code, assets, generated output, or documentation copied from another project unless its license is compatible with YOLOmux's public PolyForm Noncommercial license and the commercial sublicensing grant in the contributor terms.

Do not remove or rewrite third-party copyright or license notices. Vendored dependency notices must stay attached to the files or bundles they cover.

Run the local gate before asking for review:

```bash
python3 tools/check.py
```

The default gate already includes the isolated `bazel-go` lane. For focused Go iteration, run that lane through `python3 tools/check.py --lane bazel-go` or invoke its wrapper directly. The wrapper starts a disposable Docker test environment, gives each invocation private Bazel/Go writable roots, disables test-runtime network access, and runs the Go unit, race, contract, Docker-runtime, Chromium Finder/editor/Differ, YO!stats browser, and agent-status targets in parallel up to the requested job count:

```bash
tools/run_bazel_tests.sh --jobs "$(getconf _NPROCESSORS_ONLN)"
```

Do not run both the full gate and the direct wrapper against the same frozen tree merely to repeat the same Go suite. Use `bazel --bazelrc=/dev/null build --jobs=auto //cmd/yolomux-go:yolomux_go` for a local preview build. Do not use a fixed operator port as an automated test target; Docker fixtures own their own ports and state. Start a Go server for final manual review only after the isolated gate passes, using `YOLOMUX_START_LOAD_WAIT_SECONDS=30 ./boot.sh --golang <port>`. The default Python launch remains `python3 yolomux.py --dang`.
