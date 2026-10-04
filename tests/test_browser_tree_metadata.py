# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import pytest
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.browser_helpers.browser_layout import load_live_runtime_boot_fixture

pytestmark = [pytest.mark.browser, pytest.mark.socket, pytest.mark.boot]


def test_tree_metadata_alignment_follows_live_column_visibility(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script(
        "return typeof updateFileTreeRowContents === 'function'"
    ))
    records = browser.execute_script(
        """
        const container = document.createElement('div');
        container.style.cssText = 'position:fixed;left:20px;top:20px;width:700px';
        document.body.append(container);
        const wasLight = document.body.classList.contains('theme-light');
        const records = [];
        try {
          for (const light of [false, true]) {
            document.body.classList.toggle('theme-light', light);
            for (const compact of [false, true]) {
              const row = document.createElement('div');
              row.className = `file-tree-row${compact ? ' compact' : ''}`;
              container.append(row);
              // Reuse the same row so visibility changes exercise retained columns.
              for (let mask = 15; mask >= 0; mask--) {
                updateFileTreeRowContents(row, 'F', 'file.json', {
                  agentHtml: '<span>A</span>',
                  diffParts: mask & 1 ? [{kind: 'add', text: '+12'}] : [],
                  dirCountText: mask & 2 ? '3' : '',
                  gitStatus: mask & 4 ? 'M' : '',
                  dateText: mask & 8 ? '2 minutes ago' : '',
                });
                const measure = preserved => {
                  const columns = ['diff', 'dir-count', 'git-status', 'date']
                    .map(name => row.querySelector(`:scope > .file-tree-${name}`))
                    .filter(node => !node.hidden);
                  records.push({light, compact, mask, preserved,
                    margins: columns.map(node => parseFloat(getComputedStyle(node).marginInlineStart)),
                    gaps: columns.slice(1).map((node, index) =>
                      node.getBoundingClientRect().left - columns[index].getBoundingClientRect().right),
                    gap: parseFloat(getComputedStyle(row).columnGap)});
                };
                measure(false);
                updateFileTreeRowContents(row, 'F', 'file.json', {
                  agentHtml: '<span>A</span>',
                  gitStatus: mask & 4 ? 'M' : '',
                  preserveDiff: true, preserveDirCount: true, preserveDate: true,
                });
                measure(true);
              }
              row.remove();
            }
          }
          return records;
        } finally {
          container.remove();
          document.body.classList.toggle('theme-light', wasLight);
        }
        """
    )
    assert len(records) == 128
    for record in records:
        margins = record["margins"]
        if margins:
            assert margins[0] > 20, record
            assert all(abs(margin) <= 0.5 for margin in margins[1:]), record
        assert all(abs(gap - record["gap"]) <= 0.5 for gap in record["gaps"]), record


def test_disclosure_refresh_is_idempotent_and_repairs_nested_glyph(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script(
        "return typeof setDisclosureTriangleElement === 'function'"
    ))
    result = browser.execute_script("""
      const element = document.createElement('span');
      document.body.append(element);
      const observer = new MutationObserver(() => {});
      try {
        setDisclosureTriangleElement(element, true);
        observer.observe(element, {attributes: true, childList: true, subtree: true, characterData: true});
        setDisclosureTriangleElement(element, true);
        const repeated = observer.takeRecords().length;
        element.innerHTML = `<b>${disclosureTriangleGlyph(true)}</b>`;
        observer.takeRecords();
        setDisclosureTriangleElement(element, true);
        return {repeated, repaired: element.childNodes.length === 1 && element.firstChild.nodeType === Node.TEXT_NODE};
      } finally {
        observer.disconnect();
        element.remove();
      }
    """)
    assert result == {"repeated": 0, "repaired": True}


def test_expansion_during_metadata_publication_uses_current_payload(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script(
        "return typeof scheduleFileTreeMetadataRefresh === 'function'"
    ))
    result = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      const previous = fileExplorerFinderSessionFilesState.payload;
      const container = document.createElement('div');
      document.body.append(container);
      const original = updateFileTreeGitStatusRows;
      const cleanup = () => {
        updateFileTreeGitStatusRows = original;
        fileExplorerFinderSessionFilesState.payload = previous;
        container.remove();
        original();
      };
      try {
        fileExplorerFinderSessionFilesState.payload = {
          session: '1', loaded: true, repos: [],
          files: [{abs_path: '/metadata-new/child.txt', path: 'child.txt', repo: '/metadata-new', status: 'M', added: 7, removed: 2}],
        };
        renderTreeChildren(container, '/metadata-new', [{name: 'existing.txt', kind: 'file'}], 0);
        let row = null;
        let immediate = '';
        updateFileTreeGitStatusRows = (...args) => {
          original(...args);
          if (row) return;
          // The row is added after the batch captured its row list.
          renderTreeChildren(container, '/metadata-new', [{name: 'child.txt', kind: 'file'}], 0);
          row = container.querySelector('.file-tree-row');
          immediate = row.querySelector('.file-tree-diff').textContent;
        };
        scheduleFileTreeMetadataRefresh();
        const finish = () => {
          if (fileTreeMetadataRefreshState.timer) { setTimeout(finish, 0); return; }
          let result;
          try {
            result = {immediate, settled: row.querySelector('.file-tree-diff').textContent};
          } catch (error) {
            result = {error: String(error)};
          } finally {
            cleanup();
          }
          done(result);
        };
        finish();
      } catch (error) {
        try { done({error: String(error)}); } finally { cleanup(); }
      }
    """)
    assert "+7" in result["immediate"] and "-2" in result["immediate"], result
    assert result["settled"] == result["immediate"]


def test_starred_tree_row_metadata_refresh_preserves_child_order(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script(
        "return typeof updateFileTreeSyncTargetMarker === 'function'"
    ))
    result = browser.execute_script("""
      const row = document.createElement('div');
      row.className = 'file-tree-row kind-dir';
      row.style.cssText = 'position:fixed;left:20px;top:20px;width:700px';
      document.body.append(row);
      const options = {iconClass: 'file-icon-dir ui-disclosure-triangle', disclosureExpanded: true,
        dirCountText: '3', diffParts: [{kind: 'add', text: '+7'}], gitStatus: 'M'};
      const refresh = () => {
        updateFileTreeRowContents(row, disclosureTriangleGlyph(true), 'source', options);
        updateFileTreeSyncTargetMarker(row, true, 'Sync target');
      };
      const geometry = () => [...row.children].map(node => {
        const rect = node.getBoundingClientRect();
        return {className: node.className, left: rect.left, width: rect.width, height: rect.height};
      });
      const observer = new MutationObserver(() => {});
      try {
        refresh();
        const before = geometry();
        observer.observe(row, {childList: true, subtree: true});
        refresh();
        return {moves: observer.takeRecords().length, before, after: geometry()};
      } finally {
        observer.disconnect();
        row.remove();
      }
    """)
    assert result["moves"] == 0, result
    assert result["before"] == result["after"]


def test_tree_metadata_refresh_preserves_real_agent_svg_and_escaped_labels(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path)
    WebDriverWait(browser, 8).until(lambda driver: driver.execute_script(
        "return typeof changeFileAgentsHtml === 'function'"
    ))
    result = browser.execute_script("""
      const row = document.createElement('div');
      row.className = 'file-tree-row kind-file';
      document.body.append(row);
      const observer = new MutationObserver(() => {});
      try {
        const options = {
          agentHtml: changeFileAgentsHtml({agents: ['codex', 'claude']}),
          nameHtml: '<span title="a&#39;b">a&#39;b</span>',
          diffParts: [{kind: 'add', text: "+1'"}],
          dateHtml: '<span title="today&#39;s date">today</span>',
        };
        updateFileTreeRowContents(row, 'F', "a'b", options);
        const before = [...row.querySelectorAll('*')];
        const agent = row.querySelector('.file-tree-agent');
        const serializedDiffers = agent.innerHTML !== options.agentHtml;
        const realSvgCount = agent.querySelectorAll('svg').length;
        observer.observe(row, {childList: true, subtree: true, characterData: true});
        updateFileTreeRowContents(row, 'F', "a'b", options);
        const unchanged = observer.takeRecords().map(record => ({type: record.type, target: record.target.className}));
        const sameNodes = before.every((node, index) => node === row.querySelectorAll('*')[index]);
        agent.textContent = 'external modification';
        observer.takeRecords();
        updateFileTreeRowContents(row, 'F', "a'b", options);
        const repaired = agent.querySelectorAll('svg').length === realSvgCount;
        updateFileTreeRowContents(row, 'F', "a'b", {...options, agentHtml: changeFileAgentsHtml({agents: ['claude']})});
        const changed = agent.querySelectorAll('.agent-icon.claude').length === 1 && !agent.querySelector('.agent-icon.codex');
        return {serializedDiffers, realSvgCount, unchanged, sameNodes, repaired, changed};
      } finally {
        observer.disconnect();
        row.remove();
      }
    """)
    assert result["realSvgCount"] == 2 and result["serializedDiffers"], result
    assert result["unchanged"] == [] and result["sameNodes"], result
    assert result["repaired"] and result["changed"], result
