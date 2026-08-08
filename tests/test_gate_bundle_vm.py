# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Non-browser regression boxes driven through the shared real-bundle VM."""

import pytest

from tests.gate_harness import gate_bundle_vm  # noqa: F401

pytestmark = pytest.mark.node_vm


def test_bundle_vm_uses_product_js_debug_failure_classifier(gate_bundle_vm):
    result = gate_bundle_vm.execute(
        """
        api.recordJsDebugEventForTest('stats_history', {level: 'warning', message: 'vm-stats-warning'});
        api.recordJsDebugEventForTest('stats_history', {level: 'error', message: 'vm-stats-error'});
        api.recordJsDebugEventForTest('stats_history', {level: 'info', message: 'vm-stats-info'});
        api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/fs/info?path=%2Fapi%2Fauto-approve', status: 404, ok: false});
        api.recordJsDebugEventForTest('api', {method: 'POST', url: '/api/event', status: 400, ok: false});
        api.recordJsDebugEventForTest('api', {method: 'GET', url: '/api/stats-snapshot', status: 424, ok: false});
        return api.jsDebugFailureEventsForTest().map(event => ({
          type: event.type,
          level: event.level ?? null,
          message: event.message ?? null,
          method: event.method ?? null,
          url: event.url ?? null,
          status: event.status ?? null,
        }));
        """,
    )
    assert result.operation_error is None, result
    expected = (
        {
            "type": "stats_history",
            "level": "warning",
            "message": "vm-stats-warning",
            "method": None,
            "url": None,
            "status": None,
        },
        {
            "type": "stats_history",
            "level": "error",
            "message": "vm-stats-error",
            "method": None,
            "url": None,
            "status": None,
        },
        {
            "type": "api",
            "level": None,
            "message": None,
            "method": "GET",
            "url": "/api/fs/info?path=%2Fapi%2Fauto-approve",
            "status": 404,
        },
        {
            "type": "api",
            "level": None,
            "message": None,
            "method": "POST",
            "url": "/api/event",
            "status": 400,
        },
        {
            "type": "api",
            "level": None,
            "message": None,
            "method": "GET",
            "url": "/api/stats-snapshot",
            "status": 424,
        },
    )
    assert tuple(result.value) == expected, result
    assert tuple(
        {
            key: event.get(key)
            for key in ("type", "level", "message", "method", "url", "status")
        }
        for event in result.js_debug_errors
    ) == expected, result

def test_n6_branch_popout_orders_branches_newest_first(gate_bundle_vm):
    """The rendered branch pop-out puts the newest transcript branch before older branch rows."""
    result = gate_bundle_vm.execute(
        """
        api.setTranscriptSessionOrderForTest(['1']);
        api.setTranscriptInfoForTest('1', {
          selected_pane: {current_path: '/fixture/repo'},
          project: {
            git: {
              root: '/fixture/repo',
              branch: 'main',
              other_branches: {
                branches: [
                  {name: 'oldest-branch', updated: 'last week', updated_ts: 100},
                  {name: 'newest-branch', updated: 'now', updated_ts: 300},
                  {name: 'middle-branch', updated: 'yesterday', updated_ts: 200},
                ],
              },
            },
          },
        });
        const info = api.transcriptInfoForTest('1');
        const html = api.sessionPopoverHtml('1', info, '', false);
        return {
          branchRows: api.infoBranchRows().map(row => ({branch: row.branch, updatedTs: row.updatedTs})),
          html,
        };
        """,
    )
    assert result.operation_error is None, result
    rendered_rows = [row for row in result.value["branchRows"] if row["branch"] != "main"]
    assert rendered_rows == [
        {"branch": "newest-branch", "updatedTs": 300},
        {"branch": "middle-branch", "updatedTs": 200},
        {"branch": "oldest-branch", "updatedTs": 100},
    ], result
    html = result.value["html"]
    assert html.index("newest-branch") < html.index("middle-branch") < html.index("oldest-branch"), html
