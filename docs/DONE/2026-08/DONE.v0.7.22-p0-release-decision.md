# v0.7.22 P0 Release Decision

On 2026-08-28, Keiven explicitly directed the v0.7.22 release to treat every remaining `DOIT.p0*` item as complete and proceed to landing.

The release carries the verified gate-runner cleanup that removes the redundant `pytest-unit` and `pytest-socket` aliases. Their coverage remains in the default pytest and E2E lanes. `python3 -m pytest tests/test_check_runner.py -q -p no:randomly` passed 141 tests with 1 skipped, `python3 -m pytest tests/test_architecture_budgets.py -p no:randomly -q` passed 26 tests, `python3 tools/check.py --list-lanes` omitted both aliases, and `git diff --check` passed.

The remaining P0 items were not run as release evidence. They include a rare stream-stall attribution, a repeated scheduling experiment, and a multi-hour statsd resource comparison. They were closed by the release decision, not by claiming those measurements passed.
