# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Shared source and fixture adapters for cross-layer contract tests."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Mapping, Sequence

from yolomux_lib import session_files
from yolomux_lib.pricing_catalog import PricingCatalog
from yolomux_lib.stats_current import materializer, pricing
from yolomux_lib.stats_current.storage import DATABASE_FILENAME, Store, UsageAtom
from yolomux_lib.stats_current.usage import usage_atom_from_source


REPO_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_USAGE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "current_model_usage"
CURRENT_STATS_OWNER_PATH = Path("static_src/js/yolomux/84_stats_current.js")
CURRENT_STATS_OWNER_FUNCTION = "fetchSnapshot"
LEGACY_STATS_DELEGATE_FUNCTION = "pollJsDebugStatsSample"


@dataclass(frozen=True, slots=True)
class JavaScriptEndpointUse:
    path: Path
    function: str
    line: int
    function_source: str
    endpoint_offset: int


@dataclass(frozen=True, slots=True)
class ObservedPricingRow:
    provider: str
    model: str
    dimension: str
    tokens: int


@dataclass(frozen=True, slots=True)
class QueuedHttpProducer:
    path: Path
    function: str


@dataclass(frozen=True, slots=True)
class _JavaScriptFunctionSpan:
    name: str
    start: int
    body_start: int
    end: int


_JAVASCRIPT_FUNCTION = re.compile(
    r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)
_EXACT_STATS_DELEGATION = re.compile(
    r"if\s*\(\s*jsDebugGraphExactResolutionEnabled\s*\)\s*\{"
    r"[\s\S]*?syncJsDebugCurrentStatsClient\s*\([^)]*\)\s*;"
    r"[\s\S]*?\breturn\s*;[\s\S]*?\}"
)


def _javascript_regex_starts(source: str, offset: int) -> bool:
    prefix = source[:offset].rstrip()
    if not prefix or prefix[-1] in "([{=,:;!?&|+-*%^~<>":
        return True
    return re.search(
        r"\b(?:return|throw|case|delete|typeof|void|new|in|of|yield|await)$",
        prefix,
    ) is not None


def _javascript_matching_brace(source: str, opening: int) -> int:
    depth = 0
    state = "code"
    index = opening
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "line-comment":
            if character == "\n":
                state = "code"
        elif state == "block-comment":
            if character == "*" and following == "/":
                state = "code"
                index += 1
        elif state in {"single-quote", "double-quote", "template"}:
            closing = {"single-quote": "'", "double-quote": '"', "template": "`"}[state]
            if character == "\\":
                index += 1
            elif character == closing:
                state = "code"
        elif state == "regex":
            if character == "\\":
                index += 1
            elif character == "[":
                state = "regex-class"
            elif character == "/":
                state = "code"
        elif state == "regex-class":
            if character == "\\":
                index += 1
            elif character == "]":
                state = "regex"
        elif character == "/" and following == "/":
            state = "line-comment"
            index += 1
        elif character == "/" and following == "*":
            state = "block-comment"
            index += 1
        elif character == "/" and _javascript_regex_starts(source, index):
            state = "regex"
        elif character == "'":
            state = "single-quote"
        elif character == '"':
            state = "double-quote"
        elif character == "`":
            state = "template"
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError(f"JavaScript function body at offset {opening} is not closed")


def _javascript_function_spans(source: str) -> tuple[_JavaScriptFunctionSpan, ...]:
    spans = []
    for match in _JAVASCRIPT_FUNCTION.finditer(source):
        body_match = re.search(r"\)\s*\{", source[match.end():])
        if body_match is None:
            continue
        body_start = match.end() + body_match.end() - 1
        try:
            body_end = _javascript_matching_brace(source, body_start)
        except ValueError:
            continue
        spans.append(_JavaScriptFunctionSpan(
            match.group(1), match.start(), body_start, body_end,
        ))
    return tuple(spans)


def discover_javascript_endpoint_uses(
    sources: Mapping[Path, str],
    endpoint: str,
) -> tuple[JavaScriptEndpointUse, ...]:
    uses = []
    for path, source in sorted(sources.items()):
        if endpoint not in source:
            continue
        spans = _javascript_function_spans(source)
        offset = source.find(endpoint)
        while offset >= 0:
            containing = [span for span in spans if span.body_start < offset < span.end]
            owner = min(containing, key=lambda span: span.end - span.start) if containing else None
            function_source = source[owner.start:owner.end] if owner is not None else source
            endpoint_offset = offset - (owner.start if owner is not None else 0)
            uses.append(JavaScriptEndpointUse(
                path,
                owner.name if owner is not None else "<top-level>",
                source.count("\n", 0, offset) + 1,
                function_source,
                endpoint_offset,
            ))
            offset = source.find(endpoint, offset + len(endpoint))
    return tuple(uses)


def classify_stats_snapshot_use(use: JavaScriptEndpointUse) -> str:
    if use.path == CURRENT_STATS_OWNER_PATH and use.function == CURRENT_STATS_OWNER_FUNCTION:
        return "current-owner"
    if re.search(r"\b(?:fetch|EventSource)\s*\(", use.function_source) is None:
        return "diagnostic"
    prefix = use.function_source[:use.endpoint_offset]
    if (
        use.function == LEGACY_STATS_DELEGATE_FUNCTION
        and _EXACT_STATS_DELEGATION.search(prefix) is not None
    ):
        return "legacy-delegated"
    return "bypass"


def observed_fixture_atoms() -> tuple[UsageAtom, ...]:
    atoms = []
    for path in sorted(OBSERVED_USAGE_FIXTURES.glob("*.jsonl")):
        parser = (
            session_files.iter_claude_transcript_usage_atoms
            if path.name.endswith(".claude.jsonl")
            else session_files.iter_codex_transcript_usage_atoms
        )
        atoms.extend(usage_atom_from_source(item) for item in parser(path))
    return tuple(atoms)


def _pricing_dimension(atom: UsageAtom) -> str:
    if atom.cache_role == "read":
        return "cache_read"
    if atom.cache_role == "write_5m":
        return "cache_write_5m"
    if atom.cache_role == "write_1h":
        return "cache_write_1h"
    if atom.direction in {"input", "output"}:
        return atom.direction
    return "other"


def observed_pricing_rows(atoms: Sequence[UsageAtom]) -> tuple[ObservedPricingRow, ...]:
    totals: dict[tuple[str, str, str], int] = {}
    for atom in atoms:
        quantity = atom.payload["quantity"]
        if not isinstance(quantity, (int, float)) or int(quantity) != quantity:
            raise AssertionError(f"observed usage quantity must be an integer: {quantity!r}")
        key = (
            str(atom.payload["provider"]),
            str(atom.payload["model"]),
            _pricing_dimension(atom),
        )
        totals[key] = totals.get(key, 0) + int(quantity)
    return tuple(
        ObservedPricingRow(provider, model, dimension, tokens)
        for (provider, model, dimension), tokens in sorted(totals.items())
    )


def build_observed_cost_report(atoms: Sequence[UsageAtom], tmp_path: Path) -> dict[str, object]:
    if not atoms:
        raise AssertionError("observed transcript fixtures emitted no usage atoms")
    with Store.open(tmp_path / DATABASE_FILENAME) as store:
        store.append_batch(usage_atoms=atoms)
        snapshot = store.read_snapshot()
    observed_until = max(atom.observed_at for atom in atoms) + 1
    generation = materializer.build_generation(
        snapshot,
        source_generation=snapshot.schema.source_generation,
        cache_generation=1,
        generated_at=observed_until,
        observed_until=observed_until,
        price_resolver=pricing.UsagePriceProjector(PricingCatalog(tmp_path / "pricing")),
    )
    return materializer.build_cost_report(
        materializer.slice_generation(generation, 86_400, 300),
    )


def render_cost_model_table(report: Mapping[str, object]) -> str:
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static_src/js/yolomux/85_debug_panel.js', 'utf8');
const sourceFunction = (name, nextName) => {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf(`\nfunction ${nextName}(`, start);
  if (start < 0 || end < 0) throw new Error(`missing ${name}`);
  return source.slice(start, end);
};
const report = JSON.parse(fs.readFileSync(0, 'utf8'));
const context = {
  result: '',
  report,
  esc: value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
  t: key => key,
  agentIcon: kind => kind,
  debugGraphAgentDisplayLabel: value => String(value),
  debugGraphCostModelLabel: row => String(row?.model || 'unknown'),
  debugGraphCostUsageColumnLabel: key => key,
  debugGraphCostUsageColumnHeaderAttrs: () => '',
  normalizedExternalHttpUrl: value => String(value || ''),
  debugGraphTokenNumberText: value => String(value),
  debugGraphTokensText: value => `${value} tokens`,
  debugGraphCostAggregateRows: () => [],
};
const helpers = [
  sourceFunction('jsDebugCurrentCostDimensionRows', 'jsDebugCurrentCostSummary'),
  sourceFunction('jsDebugCurrentCostSummary', 'jsDebugCurrentModelComponent'),
  sourceFunction('debugGraphCostMicroUsd', 'debugGraphCostApiListMicroUsd'),
  sourceFunction('debugGraphCostApiListMicroUsd', 'debugGraphCostUsdText'),
  sourceFunction('debugGraphCostUsdText', 'debugGraphCostRangeUsdText'),
  sourceFunction('debugGraphCostModelAgentKind', 'debugGraphCostComponentRateText'),
  sourceFunction('debugGraphCostComponentRateText', 'debugGraphCostSourceLabel'),
].join('\n');
vm.runInNewContext(`
  const debugGraphCostInteger = value => Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
  const debugGraphCostOptionalInteger = value => value === null || value === undefined ? null : debugGraphCostInteger(value);
  const debugGraphCostText = (_key, fallback) => fallback;
  const debugGraphCostRowRangeUsdText = row => debugGraphCostUsdText(row?.micro_usd);
  ${helpers}
  const summary = jsDebugCurrentCostSummary(report);
  result = debugGraphCostModelUsageChartHtml(summary.models, summary.components, {report: true, summary});
`, context);
process.stdout.write(context.result);
"""
    completed = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(report),
        cwd=os.fspath(REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _function_has_queued_accepted_transition(function: ast.AST) -> bool:
    has_queued = any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower() == "queued"
        for node in ast.walk(function)
    )
    if not has_queued:
        return False
    for statement in ast.walk(function):
        if not isinstance(statement, ast.Return) or statement.value is None:
            continue
        returned = tuple(ast.walk(statement.value))
        has_accepted = any(
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "HTTPStatus"
            and node.attr == "ACCEPTED"
            for node in returned
        )
        if has_accepted:
            return True
    return False


def discover_queued_http_producers(
    sources: Mapping[Path, str],
) -> tuple[QueuedHttpProducer, ...]:
    producers = []
    for path, source in sorted(sources.items()):
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _function_has_queued_accepted_transition(node):
                producers.append(QueuedHttpProducer(path, node.name))
    return tuple(producers)
