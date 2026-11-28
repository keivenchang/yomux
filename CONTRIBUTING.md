# Contributing

YOmux accepts contributions only under the [contributor terms](CONTRIBUTOR_TERMS.md). By opening a pull request, sending a patch, or otherwise submitting a contribution, you confirm that you wrote it or have the right to submit it under those terms.

- Do not submit code, assets, generated output, or documentation copied from another project unless its license is compatible with YOmux's PolyForm Noncommercial license and the commercial sublicensing grant in the contributor terms.
- Do not remove or rewrite third-party copyright or license notices. Vendored dependency notices must stay with the files or bundles they cover.

Run the gate before asking for review:

```bash
python3 tools/check.py
```

The gate already includes the Go suite (the `bazel-go` lane). For focused Go work, run `python3 tools/check.py --lane bazel-go` or `tools/run_bazel_tests.sh` directly, but do not repeat the Go suite on the same unchanged tree after a full gate. For a quick local build, use `bazel --bazelrc=/dev/null build --jobs=auto //cmd/yomux-go:yomux-go`. Start a server for manual review with `./boot.sh <port>` only after the gate passes, and never point automated tests at a fixed operator port.

Setup, test options, and release steps are in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
