# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Gate the source-to-test contracts for user-visible pending states."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.terminal_state_guard import assert_terminal_transition
from tests.terminal_state_guard import TERMINAL_STATE_CONTRACTS


REPO_ROOT = Path(__file__).resolve().parents[1]


def _proof_function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        ),
        None,
    )


def _terminal_assertion_contract_ids(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    contract_ids = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        called = node.func.id if isinstance(node.func, ast.Name) else ""
        if called != "assert_terminal_transition":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        contract_id = keywords.get("contract_id")
        if not isinstance(contract_id, ast.Constant) or not isinstance(contract_id.value, str):
            continue
        if {"pending_observed", "terminal_observed"} <= set(keywords):
            contract_ids.add(contract_id.value)
    return contract_ids


def test_terminal_state_contract_catalog_has_exit_proofs():
    contract_ids = [contract.contract_id for contract in TERMINAL_STATE_CONTRACTS]
    assert len(contract_ids) == len(set(contract_ids)), "terminal-state contract IDs must be unique"
    failures = []
    for contract in TERMINAL_STATE_CONTRACTS:
        owner = REPO_ROOT / contract.owner_path
        proof = REPO_ROOT / contract.proof_path
        if not owner.is_file():
            failures.append(f"{contract.contract_id}: missing owner {contract.owner_path}")
            continue
        if contract.owner_token not in owner.read_text(encoding="utf-8"):
            failures.append(
                f"{contract.contract_id}: owner token is absent from {contract.owner_path}"
            )
        if not proof.is_file():
            failures.append(f"{contract.contract_id}: missing proof {contract.proof_path}")
            continue
        function = _proof_function(proof, contract.proof_test)
        if function is None:
            failures.append(
                f"{contract.contract_id}: missing proof test {contract.proof_path}::{contract.proof_test}"
            )
            continue
        if contract.contract_id not in _terminal_assertion_contract_ids(function):
            failures.append(
                f"{contract.contract_id}: proof must call assert_terminal_transition with pending_observed and terminal_observed"
            )
    assert failures == []


def test_terminal_state_assertion_rejects_pending_without_terminal():
    with pytest.raises(
        AssertionError,
        match="differ-refreshing-elsewhere: pending state never reached a terminal state",
    ):
        assert_terminal_transition(
            contract_id="differ-refreshing-elsewhere",
            pending_observed=True,
            terminal_observed=False,
            evidence={"refreshing_elsewhere": True, "loading": True},
        )
