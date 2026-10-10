#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Derived pytest file catalogs for the local check lanes.

Pytest only imports files which can contribute to a lane. The catalog is
derived from test definitions and their static pytest markers so adding a test
file cannot silently leave it outside the default gate.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from tools.test_plan import automatic_test_markers
from tools.test_plan import PHASE_MARKER_PRECEDENCE
from tools.test_plan import phase_for_markers
from tools.test_plan import TEST_PHASE_NAMES
from tools.test_plan import TEST_PHASE_SPECS
from tools.test_plan import test_node_sort_key


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = REPO_ROOT / "tests"
DEPRECATED_PYTHON_MARKER = "deprecated_python_server"
# These are backend implementations, not the Python HTML renderer or contributor
# tooling. Shared fixture/ownership tests must state their role when using them.
LEGACY_BACKEND_MODULES = frozenset({
    "app", "server", "server_auth", "server_query", "server_logs", "server_static",
    "search", "file_index", "bfs_index", "search_indexer", "statusd", "statusd_client", "statusd_protocol",
    "stats_current", "activity", "activity_summary", "activity_summary_runtime",
    "observability", "workspace.session_files", "session_files", "workspace.metadata", "metadata",
    "batchd", "infra.batchd", "approvald", "approval.approvald", "auto_approve_worker",
    "watchd", "watchd_client", "client_events", "yoagent",
    "filesystem", "backend_health", "infra.state_services", "state_services", "http_routes",
    "login_rate_limit", "login_escalation", "control", "chat", "chat_service", "chat_store",
    "watchd_protocol", "tmux.sessions", "sessions", "cache", "websocket",
    "background_scheduler", "infra.background_scheduler", "infra.published_json_cache",
    "infra.refresh_outcome", "tmux_theme", "tmux.tmux_theme", "ptrace", "infra.ptrace",
})
LEGACY_BACKEND_HELPERS = frozenset({
    "gate_live_server", "gate_authenticated_live_server", "stateful_journey", "gate_browser_runtime",
    "e2e_browser", "authenticated_e2e_browser", "make_tmux_webterm_app",
    "start_isolated_browser_app", "start_browser_server", "start_fixture_runtime",
    "start_fixture_live_server", "TmuxWebtermApp", "TmuxWebtermHTTPServer", "FixtureHttpServer",
    "_start_current_stats", "_seed_realistic_stats", "commit_scan",
    "_fresh_statsd_arenas", "session_workdir", "numbered_session_workdir",
    "session_dev_workdir", "warn_unavailable_agent_commands_once",
})
BACKEND_TEST_ROLES = frozenset({DEPRECATED_PYTHON_MARKER, "shared_tooling", "go_backend"})


def unclassified_legacy_tests(path: Path, *, repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Find backend consumers lacking an explicit backend or shared-tooling role.

    Follow local helpers and fixtures, including inherited class fixtures. This is
    a classification guard, never an inferred exclusion: source markers own selection.
    """
    metadata = dict(test_metadata(path, repo_root=repo_root))
    if all(markers & BACKEND_TEST_ROLES for markers in metadata.values()):
        return ()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    legacy_names = set(LEGACY_BACKEND_HELPERS)
    for statement in ast.walk(tree):
        imports: list[tuple[str, str]] = []
        if isinstance(statement, ast.ImportFrom) and statement.module:
            imports = [(f"{statement.module}.{alias.name}", alias.asname or alias.name) for alias in statement.names]
        elif isinstance(statement, ast.Import):
            imports = [(alias.name, alias.asname or alias.name.split(".")[0]) for alias in statement.names]
        for module, local in imports:
            if module.startswith("yomux_lib."):
                owner = module.removeprefix("yomux_lib.")
                if any(owner == prefix or owner.startswith(prefix + ".") for prefix in LEGACY_BACKEND_MODULES):
                    legacy_names.add(local)
    functions: dict[tuple[str, ...], ast.FunctionDef | ast.AsyncFunctionDef] = {}
    bases: dict[tuple[str, ...], tuple[tuple[str, ...], ...]] = {}
    classes: dict[tuple[str, ...], ast.ClassDef] = {}

    def visit(body: list[ast.stmt], scope: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                owner = (*scope, node.name)
                classes[owner] = node
                bases[owner] = tuple((*scope, base.id) for base in node.bases if isinstance(base, ast.Name))
                visit(node.body, owner)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[(*scope, node.name)] = node

    visit(tree.body)

    def class_scopes(scope: tuple[str, ...], visited: frozenset[tuple[str, ...]] = frozenset()) -> set[tuple[str, ...]]:
        if scope in visited:
            return set()
        return {scope} | set().union(*(class_scopes(base, visited | {scope}) for base in bases.get(scope, ())), set())

    def resolve(name: str, scope: tuple[str, ...]) -> tuple[str, ...] | str:
        for owner in sorted(class_scopes(scope), key=len, reverse=True):
            if (*owner, name) in functions:
                return (*owner, name)
        return (name,) if (name,) in functions else name

    autouse: set[tuple[str, ...]] = set()
    for key, node in functions.items():
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "fixture":
                if any(keyword.arg == "autouse" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True for keyword in decorator.keywords):
                    # A marker-gated legacy autouse fixture intentionally stays idle
                    # for normal cases; it must not turn shared tooling into legacy tests.
                    guarded = any(isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                                  and value.func.attr == "get_closest_marker" and value.args
                                  and isinstance(value.args[0], ast.Constant) and value.args[0].value == DEPRECATED_PYTHON_MARKER
                                  for value in ast.walk(node))
                    if not guarded:
                        autouse.add(key)

    references: dict[tuple[str, ...] | str, set[tuple[str, ...] | str]] = {}
    module_fixtures: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            dependencies = {value.id for value in ast.walk(statement.value) if isinstance(value, ast.Name)}
            for value in ast.walk(statement.value):
                if isinstance(value, ast.Call) and _pytest_marker_name(value) == "usefixtures":
                    module_fixtures.update(argument.value for argument in value.args
                                           if isinstance(argument, ast.Constant) and isinstance(argument.value, str))
                if isinstance(value, ast.Call) and value.args and isinstance(value.args[0], ast.Constant):
                    module = value.args[0].value
                    if isinstance(module, str) and module.startswith("yomux_lib."):
                        owner = module.removeprefix("yomux_lib.")
                        if any(owner == prefix or owner.startswith(prefix + ".") for prefix in LEGACY_BACKEND_MODULES):
                            dependencies.add("TmuxWebtermApp")
            for target in targets:
                if isinstance(target, ast.Name):
                    references[target.id] = {resolve(name, ()) for name in dependencies}
    for key, node in functions.items():
        scope = key[:-1]
        names = {value.id for value in ast.walk(node) if isinstance(value, ast.Name)}
        names.update(argument.arg for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
        names.update(value.attr for value in ast.walk(node) if isinstance(value, ast.Attribute)
                     and ((isinstance(value.value, ast.Name) and value.value.id in {"self", "cls"})
                          or value.attr in LEGACY_BACKEND_HELPERS))
        for value in ast.walk(node):
            if not isinstance(value, ast.Call):
                continue
            if _pytest_marker_name(value) == "usefixtures":
                names.update(argument.value for argument in value.args if isinstance(argument, ast.Constant) and isinstance(argument.value, str))
            method = value.func.attr if isinstance(value.func, ast.Attribute) else value.func.id if isinstance(value.func, ast.Name) else ""
            if method == "getfixturevalue" and value.args and isinstance(value.args[0], ast.Constant):
                names.add(value.args[0].value)
            if method == "import_module" and value.args and isinstance(value.args[0], ast.Constant):
                module = value.args[0].value
                if isinstance(module, str) and module.startswith("yomux_lib."):
                    owner = module.removeprefix("yomux_lib.")
                    if any(owner == prefix or owner.startswith(prefix + ".") for prefix in LEGACY_BACKEND_MODULES):
                        names.add("TmuxWebtermApp")
        references[key] = {resolve(name, scope) for name in names if isinstance(name, str)}
        references[key].update(resolve(name, scope) for name in module_fixtures)
        references[key].update(fixture for fixture in autouse if fixture[:-1] in (class_scopes(scope) | {()}))
        # Class usefixtures markers apply to every method, just like autouse fixtures.
        for owner in class_scopes(scope):
            if owner in classes:
                klass = classes[owner]
                expressions = [*klass.decorator_list, *(statement.value for statement in klass.body
                                if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None)]
                for expression in expressions:
                    for decorator in ast.walk(expression):
                        if not isinstance(decorator, ast.Call) or _pytest_marker_name(decorator) != "usefixtures":
                            continue
                        references[key].update(resolve(argument.value, scope) for argument in decorator.args
                                               if isinstance(argument, ast.Constant) and isinstance(argument.value, str))
    consumers: set[tuple[str, ...] | str] = set(legacy_names)
    while True:
        new_consumers = {name for name, names in references.items() if names & consumers}
        if new_consumers <= consumers:
            break
        consumers.update(new_consumers)
    return tuple(nodeid for nodeid, markers in metadata.items()
                 if tuple(nodeid.split("::")[1:]) in consumers and not markers & BACKEND_TEST_ROLES)


def _pytest_marker_name(node: ast.AST) -> str | None:
    value = node.func if isinstance(node, ast.Call) else node
    if not isinstance(value, ast.Attribute):
        return None
    mark = value.value
    if (
        isinstance(mark, ast.Attribute)
        and mark.attr == "mark"
        and isinstance(mark.value, ast.Name)
        and mark.value.id == "pytest"
    ):
        return value.attr
    return None


def _pytest_markers(node: ast.AST) -> set[str]:
    """Return markers applied by this expression, excluding marker-valued arguments."""

    marker = _pytest_marker_name(node)
    if marker is not None:
        return {marker}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return set().union(*(_pytest_markers(element) for element in node.elts), set())
    return set()


def _assigned_pytest_markers(body: list[ast.stmt]) -> set[str]:
    markers: set[str] = set()
    for statement in body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            continue
        markers.update(_pytest_markers(statement.value))
    return markers


def _decorator_markers(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> set[str]:
    markers: set[str] = set()
    for decorator in node.decorator_list:
        markers.update(_pytest_markers(decorator))
    return markers


def test_metadata(path: Path, *, repo_root: Path = REPO_ROOT) -> tuple[tuple[str, frozenset[str]], ...]:
    """Return static node IDs and inherited markers without importing the module."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    automatic = set(automatic_test_markers(path))
    module_markers = _assigned_pytest_markers(tree.body)
    try:
        relative = path.relative_to(repo_root).as_posix()
    except ValueError:
        relative = path.resolve().as_posix()
    definitions: list[tuple[str, frozenset[str]]] = []

    def inherited_class_markers(statement: ast.ClassDef, peers: dict[str, ast.ClassDef], seen: frozenset[str] = frozenset()) -> set[str]:
        if statement.name in seen:
            return set()
        markers = _decorator_markers(statement) | _assigned_pytest_markers(statement.body)
        for base in statement.bases:
            if isinstance(base, ast.Name) and base.id in peers:
                markers.update(inherited_class_markers(peers[base.id], peers, seen | {statement.name}))
        return markers

    def visit(body: list[ast.stmt], inherited: set[str], parents: tuple[str, ...] = ()) -> None:
        peers = {statement.name: statement for statement in body if isinstance(statement, ast.ClassDef)}
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                class_markers = inherited | inherited_class_markers(statement, peers)
                visit(statement.body, class_markers, (*parents, statement.name))
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name.startswith("test_"):
                nodeid = "::".join((relative, *parents, statement.name))
                markers = frozenset(automatic | inherited | _decorator_markers(statement))
                definitions.append((nodeid, markers))

    visit(tree.body, module_markers)
    return tuple(definitions)


def wholly_deprecated(path: Path, *, repo_root: Path = REPO_ROOT) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if DEPRECATED_PYTHON_MARKER in _assigned_pytest_markers(tree.body):
        return True
    definitions = test_metadata(path, repo_root=repo_root)
    return bool(definitions) and all(DEPRECATED_PYTHON_MARKER in markers for _, markers in definitions)


def test_definitions(path: Path, *, repo_root: Path = REPO_ROOT, run_deprecated_python: bool = True) -> tuple[tuple[str, str], ...]:
    """Return node IDs and phases; inventories include deprecated cases unless filtered."""
    return tuple(
        (nodeid, phase_for_markers(set(markers)))
        for nodeid, markers in test_metadata(path, repo_root=repo_root)
        if run_deprecated_python or DEPRECATED_PYTHON_MARKER not in markers
    )


def file_phases(path: Path) -> set[str]:
    """Return every check lane phase containing a statically defined test in path."""

    return {phase for _nodeid, phase in test_definitions(path)}


def discover_pytest_phase_files(
    test_root: Path = TEST_ROOT,
    *,
    repo_root: Path = REPO_ROOT,
    run_deprecated_python: bool = False,
) -> dict[str, tuple[str, ...]]:
    """Derive phase file ownership from every Python test under test_root."""

    definitions: list[tuple[str, str, str]] = []
    for path in sorted(test_root.rglob("test_*.py")):
        relative = path.relative_to(repo_root).as_posix()
        definitions.extend(
            (nodeid, phase, relative)
            for nodeid, phase in test_definitions(path, repo_root=repo_root, run_deprecated_python=run_deprecated_python)
        )
    ordered = sorted(
        enumerate(definitions),
        key=lambda pair: test_node_sort_key(pair[1][0], pair[0]),
    )
    phase_files = {phase: [] for phase in TEST_PHASE_NAMES}
    for _index, (_nodeid, phase, relative) in ordered:
        if relative not in phase_files[phase]:
            phase_files[phase].append(relative)
    return {phase: tuple(paths) for phase, paths in phase_files.items()}


PYTEST_PHASE_FILES: Final[dict[str, tuple[str, ...]]] = discover_pytest_phase_files()
MAINTENANCE_PHASE_FILES: Final[dict[str, tuple[str, ...]]] = discover_pytest_phase_files(run_deprecated_python=True)
NONBROWSER_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["nonbrowser"]
BOOT_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["boot"]
BROWSER_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["browser"]
GOLDEN_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["golden"]
E2E_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["e2e"]
NODE_BRIDGE_FILES: Final[tuple[str, ...]] = PYTEST_PHASE_FILES["node_bridge"]
MOCK_TRANSCRIPT_FILES: Final[tuple[str, ...]] = ("tests/test_mock_transcripts.py",)
NODE_SHARD_LAUNCHER: Final[str] = "tests/layout_url.test.js"
# The one place a Node shard may be kept out of the gate. Every other `tests/*.test.js` is derived
# from disk below, so a new shard joins the gate by existing rather than by being remembered here.
# This list previously enumerated its 14 members by hand and silently omitted three whole shards,
# including cross-surface state assertions over quick-open, Finder, Differ, editor, terminal, and
# layout: the gate reported green without ever running them.
NODE_LAYOUT_EXCLUDED_FILES: Final[tuple[str, ...]] = ()


def discover_node_layout_files() -> tuple[str, ...]:
    """Return the Node shards the node-layout lane runs, derived from the shard files on disk."""

    excluded = {NODE_SHARD_LAUNCHER, *NODE_LAYOUT_EXCLUDED_FILES}
    return tuple(
        relative
        for relative in sorted(
            path.relative_to(REPO_ROOT).as_posix() for path in TEST_ROOT.glob("*.test.js")
        )
        if relative not in excluded
    )


NODE_LAYOUT_FILES: Final[tuple[str, ...]] = discover_node_layout_files()
# Non-shard JavaScript modules which directly own test scenarios invoked by a
# registered shard. Keeping them explicit makes architecture budgets recursive
# without mistaking generic DOM helpers for assertion owners.
NODE_TEST_HELPER_OWNERS: Final[tuple[str, ...]] = (
    "tests/browser_helpers/cross_surface_quick_open_suite.js",
    "tests/browser_helpers/editor_preview_info_graph_suite.js",
    "tests/browser_helpers/editor_preview_suite.js",
)

# Python helper modules which own cohesive assertion families while their thin
# collecting facades retain the historical pytest node IDs. These are explicit
# because pytest collection alone cannot discover a non-test-named owner.
PYTHON_TEST_HELPER_OWNERS: Final[tuple[str, ...]] = (
    "tests/helpers/fixture_http_server.py",
    "tests/helpers/login_throttle.py",
    "tests/subsystems/app_darwin_memory.py",
    "tests/subsystems/app_batchd_product.py",
    "tests/subsystems/browser_harness_lifecycle.py",
    "tests/subsystems/stats_24h_http.py",
)

def pytest_files(phase: str, *, run_deprecated_python: bool = False) -> list[str]:
    """Return the derived pytest targets for one canonical phase."""

    catalog = MAINTENANCE_PHASE_FILES if run_deprecated_python else PYTEST_PHASE_FILES
    return list(catalog[phase])
