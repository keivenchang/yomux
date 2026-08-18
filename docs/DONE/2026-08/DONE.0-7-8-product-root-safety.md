# 2026-08-16 product-root safety

YOLOmux now resolves every configured product path through `tools.instance_isolation.resolved_product_path()` before startup can import, lock, stop a listener, or write. Relative configured roots, relative `HOME`, `/`, exact `$HOME`, and paths inside the shared checkout fail with the responsible variable and value; rooted mode removes ambient XDG bases and `CODEX_HOME` unless an explicit contained override is supplied.

The pre-fix private-server reproduction created fourteen entries beneath its chosen cwd, including `state/tls/self-signed.key`. The focused `tests/test_product_root_safety.py` matrix passed 144 tests, including every root family and two-cwd identity. Restarted port 7771 PID 3464988 used `/tmp/y1776734304/p7771`, returned the expected unauthenticated 401 from `/api/ping`, left the retained fourteen-entry home inventory byte-identical, and did not recreate `~/state` or `~/runtime`.

At composed code HEAD `1406566f9`, all nine functional lanes passed in `/tmp/yolomux-check-runs/check-1786882610409235988-596763.json`. Exact-SHA certification then passed all seven units on clean `26651ec37` in `/tmp/yolomux-certification/cert-1786884102710445168-1423261`. The recovered `/tmp/home-cleanup-20260815/` tree and retained `~/config`, `~/cache`, and `~/codex` evidence were not deleted; cleanup still requires explicit authorization.
