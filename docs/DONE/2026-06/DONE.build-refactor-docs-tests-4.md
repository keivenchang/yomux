# Build, Refactor, Docs, and Tests

## DOIT.42 test-suite cleanup imported
- Imported the `doit-42-test-cleanup` worktree as a focused test-suite cleanup on top of the active-color mainline: Python source-grep tests now use behavioral or inspect-based assertions where appropriate, Selenium browser setup is module-scoped with cleanup, shared config/git/auth fixtures live in `tests/conftest.py` and `tests/_git_helpers.py`, real retry sleeps were removed from the auto-approve worker tests, tmux-utils tests moved to `test_tmux_utils.py`, active-accent browser assertions are relationship-based, pane-tab width checks are parametrized, the removed strip-hover token guard is a cheap CSS test, `web.py` has direct escaping/bootstrap coverage, and the TODO diff-overview browser test now waits for its async CodeMirror metrics under xdist load. Finished the remaining small checklist items by deriving JS pixel expectations from harness geometry, loosening a full session-files payload equality to field assertions, parametrizing visible-agent-working detector cases, adding GitHub/Linear request-building coverage, adding behavioral layout-param/deep-link edge coverage, and exercising real `do_GET`/`do_POST` server route dispatch. The DOIT.42 retrospective rejected the broad JS source-grep deletion, broad Selenium-to-vm migration, and full `node:test` migration as harmful or too risky for this pass after inspection. Verification beyond the standard gate: focused affected pytest files.

---

Completed 2026-06-06. Extracted from the 2026-06-06 daily log.
