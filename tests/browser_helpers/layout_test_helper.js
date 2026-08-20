const assert = require('assert');
const fs = require('fs');
const UI_PINS = JSON.parse(fs.readFileSync('tests/fixtures/ui_pins.json', 'utf8'));  // shared color pins (see test_ui_pins.py)
const vm = require('vm');

// Node harness contract: this is a pure-logic VM, not a browser. It has no CSS engine or real layout;
// getComputedStyle only supplies direction and default element rects are synthetic. ResizeObserver,
// MutationObserver, scrolling metrics, and boot-section wiring are intentionally absent. Tests that need
// computed paint, observer delivery, scroll geometry, or rendered placement belong in Selenium.
const BUNDLE_SOURCE = fs.readFileSync('static/yolomux.js', 'utf8');
const EN_CATALOG = Object.freeze(JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')));
const BUNDLE_BOOT_START = BUNDLE_SOURCE.indexOf("if (refreshMeta) {");
assert.ok(BUNDLE_BOOT_START > 0, 'could not find browser boot section');
let bundleScript = null;
const FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST = 'yolomux.fileExplorerOpen.v1';

// Historical renderer fixtures describe the retired compact `project` payload. Keep that format
// at the test boundary only: production receives and selects the normalized graph exclusively.
function canonicalWorkGraphFixture(session, info = {}) {
  if (!info || typeof info !== 'object') return info;
  if (info.work_graph || !info.project || typeof info.project !== 'object') return info;
  const project = info.project;
  const repoInputs = [project.git, ...(Array.isArray(project.repos) ? project.repos : [])].filter(repo => repo?.root);
  if (!repoInputs.length) return {...info, work_graph: {version: 1, generation: 1, tmux_sessions: {}, tmux_windows: {}, tmux_panes: {}, runtime_actors: {}, path_observations: {}, git_worktrees: {}, local_repositories: {}, hosted_repositories: {}, local_branches: {}, pull_requests: {}, linear_issues: {}, worktree_branch_activity: {}}};
  const graph = {version: 1, generation: 1, tmux_sessions: {}, tmux_windows: {}, tmux_panes: {}, runtime_actors: {}, path_observations: {}, git_worktrees: {}, local_repositories: {}, hosted_repositories: {}, local_branches: {}, pull_requests: {}, linear_issues: {}, worktree_branch_activity: {}};
  const sessionId = `tmux-session:${session}`;
  graph.tmux_sessions[sessionId] = {id: sessionId, name: String(session), tmux_window_ids: [], tmux_pane_ids: [], runtime_actor_ids: [], path_observation_ids: []};
  const worktreeIds = new Map();
  const branchActivityByWorktreeAndBranch = new Map();
  const ensureIssue = issue => {
    const identifier = String(issue?.identifier || issue || '').trim();
    if (!identifier) return '';
    const id = `linear:${identifier}`;
    graph.linear_issues[id] ||= {id, identifier, title: issue?.title || '', url: issue?.url || '', state: issue?.state || ''};
    return id;
  };
  for (const repo of repoInputs) {
    const root = String(repo?.worktree?.path || repo?.root || '').replace(/\/+$/, '');
    if (!root || worktreeIds.has(root)) continue;
    const localId = `local:${root}`;
    const worktreeId = `worktree:${root}`;
    worktreeIds.set(root, worktreeId);
    const sourceBranches = Array.isArray(repo.other_branches?.branches) ? [...repo.other_branches.branches] : [];
    const currentName = String(repo.branch || sourceBranches.find(branch => branch?.current)?.name || '');
    if (currentName && !sourceBranches.some(branch => String(branch?.name || '') === currentName)) {
      sourceBranches.unshift({name: currentName, current: true});
    }
    const hosted = repo.github_repo || null;
    const hostedId = hosted?.url ? `hosted:${hosted.url}` : '';
    if (hostedId) graph.hosted_repositories[hostedId] ||= {id: hostedId, url: hosted.url, pull_request_ids: []};
    const branchIds = [];
    for (const source of sourceBranches.length ? sourceBranches : (currentName ? [{name: currentName, current: true}] : [])) {
      const name = String(source?.name || '');
      if (!name) continue;
      const branchId = `branch:${localId}:${name}`;
      const prs = [...(Array.isArray(source.pull_requests) ? source.pull_requests : [source.pull_request].filter(Boolean))];
      if (source.current && root === String(project.git?.root || '').replace(/\/+$/, '') && project.pull_request) prs.push(project.pull_request);
      const prIds = [];
      for (const value of prs) {
        const number = Number(value?.number);
        if (!Number.isFinite(number)) continue;
        const prId = `pr:${hostedId || localId}:${number}`;
        const linearIds = (value.linear || value.linear_ids || []).map(ensureIssue).filter(Boolean);
        graph.pull_requests[prId] ||= {id: prId, hosted_repository_id: hostedId || null, number, title: value.title || value.description || '', description: value.description || '', url: value.url || '', state: value.state, status_label: value.status_label || '', draft: value.draft === true, merged: value.merged === true, checks: value.checks, review_decision: value.review_decision || '', linear_issue_ids: linearIds, local_branch_ids: []};
        if (!graph.pull_requests[prId].local_branch_ids.includes(branchId)) graph.pull_requests[prId].local_branch_ids.push(branchId);
        if (hostedId && !graph.hosted_repositories[hostedId].pull_request_ids.includes(prId)) graph.hosted_repositories[hostedId].pull_request_ids.push(prId);
        prIds.push(prId);
      }
      const currentProjectLinear = source.current && root === String(project.git?.root || '').replace(/\/+$/, '')
        ? (project.linear || [])
        : [];
      const linearIds = [...(source.linear || source.linear_ids || []), ...prs.flatMap(value => value?.linear || value?.linear_ids || []), ...currentProjectLinear]
        .map(ensureIssue)
        .filter(Boolean);
      graph.local_branches[branchId] = {id: branchId, local_repository_id: localId, name, current: source.current === true || name === currentName, updated: source.updated, updated_ts: source.updated_ts, subject: source.subject, pull_request_ids: [...new Set(prIds)], linear_issue_ids: [...new Set(linearIds)]};
      branchIds.push(branchId);
    }
    const currentBranchId = branchIds.find(id => graph.local_branches[id].current) || branchIds[0] || null;
    graph.local_repositories[localId] = {id: localId, common_git_dir: String(repo.worktree?.parent_root || root) + '/.git', git_worktree_ids: [worktreeId], local_branch_ids: branchIds, hosted_repository_id: hostedId || null};
    graph.git_worktrees[worktreeId] = {id: worktreeId, root, git_dir: root + '/.git', kind: repo.worktree?.parent_root ? 'linked' : 'primary', parent_root: repo.worktree?.parent_root || '', local_repository_id: localId, hosted_repository_id: hostedId || null, current_branch_id: currentBranchId, branch_activity_ids: [], path_observation_ids: [], git: {root, branch: currentName, head: repo.head || '', upstream: repo.upstream || '', ahead: repo.ahead, behind: repo.behind, dirty_count: repo.dirty_count || 0, github_repo: hosted, worktree: repo.worktree}};
  }
  const paneRows = Array.isArray(info.panes) && info.panes.length ? info.panes : [info.selected_pane || {}];
  // Fixtures that predate WorkGraph used `agents` or `agent_windows` for the same RuntimeActor
  // concept. Convert either explicitly; do not infer one actor from unrelated pane display rows.
  const fixtureActors = Array.isArray(info.agents) && info.agents.length
    ? info.agents
    : (Array.isArray(info.agent_windows) && info.agent_windows.length ? info.agent_windows : [{kind: '', fixture: true}]);
  const actorRows = fixtureActors.map(actor => ({
    ...actor,
    path: actor?.path || '',
  }));
  actorRows.forEach((actor, index) => {
    const pane = paneRows.find(row => String(row?.target || '') === String(actor?.pane_target || '')) || paneRows[index] || paneRows[0] || {};
    const windowIndex = String(actor.window_index ?? actor.window ?? pane.window ?? index);
    const paneIndex = String(actor.pane ?? pane.pane ?? '0');
    const windowId = `tmux-window:${session}:${windowIndex}`;
    const target = String(actor.pane_target || pane.target || `%${session}-${index}`);
    const existingPaneId = `tmux-pane:${session}:${windowIndex}.${paneIndex}`;
    const paneId = graph.tmux_panes[existingPaneId] && graph.tmux_panes[existingPaneId].target !== target
      ? `${existingPaneId}:${target.replace(/[^a-zA-Z0-9_.-]/g, '_')}`
      : existingPaneId;
    const actorId = `actor:${session}:${index}`;
    graph.tmux_windows[windowId] ||= {id: windowId, tmux_session_id: sessionId, index: windowIndex, name: actor.window_name || actor.kind || '', tmux_pane_ids: []};
    if (!graph.tmux_windows[windowId].tmux_pane_ids.includes(paneId)) graph.tmux_windows[windowId].tmux_pane_ids.push(paneId);
    const explicitGit = actor.git?.root ? actor.git : null;
    const path = String(actor.path || explicitGit?.root || pane.current_path || project.git?.cwd || project.git?.root || '').replace(/\/+$/, '');
    const root = String(explicitGit?.root || [...worktreeIds.keys()].find(candidate => path === candidate || path.startsWith(`${candidate}/`)) || project.git?.root || '').replace(/\/+$/, '');
    const worktreeId = worktreeIds.get(root) || [...worktreeIds.values()][0] || null;
    graph.tmux_panes[paneId] = {id: paneId, tmux_window_id: windowId, target, index: paneIndex, current_path: path, active: pane.active === true || index === 0, window_active: pane.window_active === true || index === 0, runtime_actor_ids: [actorId], path_observation_ids: []};
    graph.runtime_actors[actorId] = {id: actorId, tmux_pane_id: paneId, kind: actor.fixture ? '' : (actor.kind || 'shell'), cwd: path, status: actor.state || '', path_observation_ids: []};
    if (worktreeId) {
      const observationId = `observation:${session}:${index}`;
      graph.path_observations[observationId] = {id: observationId, tmux_pane_id: paneId, runtime_actor_id: actorId, git_worktree_id: worktreeId, path, source: 'fixture', priority: 0, last_observed_at: index + 1};
      graph.tmux_panes[paneId].path_observation_ids.push(observationId);
      graph.runtime_actors[actorId].path_observation_ids.push(observationId);
      graph.git_worktrees[worktreeId].path_observation_ids.push(observationId);
      graph.tmux_sessions[sessionId].path_observation_ids.push(observationId);
      const worktree = graph.git_worktrees[worktreeId];
      const branchId = String(explicitGit?.branch || '')
        ? `branch:${worktree.local_repository_id}:${String(explicitGit.branch)}`
        : worktree.current_branch_id;
      if (branchId && graph.local_branches[branchId]) {
        const key = `${worktreeId}\n${branchId}`;
        const activityId = `activity:${worktreeId}:${branchId}`;
        if (!branchActivityByWorktreeAndBranch.has(key)) {
          graph.worktree_branch_activity[activityId] = {id: activityId, git_worktree_id: worktreeId, local_branch_id: branchId, branch_name_snapshot: graph.local_branches[branchId].name, current: branchId === worktree.current_branch_id, first_observed_at: index + 1, last_observed_at: index + 1, path_observation_ids: []};
          worktree.branch_activity_ids.push(activityId);
          branchActivityByWorktreeAndBranch.set(key, activityId);
        }
        const activity = graph.worktree_branch_activity[branchActivityByWorktreeAndBranch.get(key)];
        if (activity && !activity.path_observation_ids.includes(observationId)) activity.path_observation_ids.push(observationId);
      }
    }
    if (!graph.tmux_sessions[sessionId].tmux_window_ids.includes(windowId)) graph.tmux_sessions[sessionId].tmux_window_ids.push(windowId);
    if (!graph.tmux_sessions[sessionId].tmux_pane_ids.includes(paneId)) graph.tmux_sessions[sessionId].tmux_pane_ids.push(paneId);
    graph.tmux_sessions[sessionId].runtime_actor_ids.push(actorId);
  });
  return {...info, work_graph: graph, project: undefined, window_metadata: (info.window_metadata || []).map(({git, ...row}) => row)};
}

function serverDefaultSettings(...keys) {
  // Numeric defaults come from the ONE owner, yolomux_lib/workspace/settings.py
  // DEFAULT_SETTINGS; the fixture inherits instead of keeping a drifting copy.
  const source = fs.readFileSync(require('path').join(__dirname, '..', '..', 'yolomux_lib', 'workspace', 'settings.py'), 'utf8');
  return Object.fromEntries(keys.map(key => {
    const match = source.match(new RegExp(`"${key}": ([0-9.]+),`));
    if (!match) throw new Error(`layout_test_helper: default "${key}" not found in yolomux_lib/workspace/settings.py DEFAULT_SETTINGS`);
    return [key, Number(match[1])];
  }));
}

function serverFilesystemBatchLimits() {
  // The /api/fs/batch bounds come from the ONE owner, yolomux_lib/filesystem/__init__.py, the same
  // way the real boot payload does. A literal here would be a copy free to drift from the constant
  // the server actually refuses above, which is the whole reason the bundle stopped carrying one.
  const source = fs.readFileSync(require('path').join(__dirname, '..', '..', 'yolomux_lib', 'filesystem', '__init__.py'), 'utf8');
  const maxRequestsMatch = source.match(/^MAX_BATCH_REQUESTS = (\d+)$/m);
  if (!maxRequestsMatch) throw new Error('layout_test_helper: MAX_BATCH_REQUESTS not found in yolomux_lib/filesystem/__init__.py');
  const maxRequests = Number(maxRequestsMatch[1]);
  const triggerMatch = source.match(/^BATCH_TRIGGER_COUNT_LIMIT = (MAX_BATCH_REQUESTS|\d+)$/m);
  if (!triggerMatch) throw new Error('layout_test_helper: BATCH_TRIGGER_COUNT_LIMIT not found in yolomux_lib/filesystem/__init__.py');
  const triggerCountLimit = triggerMatch[1] === 'MAX_BATCH_REQUESTS' ? maxRequests : Number(triggerMatch[1]);
  return {maxRequests, triggerCountLimit};
}

const SERVER_FILESYSTEM_BATCH_LIMITS = Object.freeze(serverFilesystemBatchLimits());

const DEFAULT_TEST_SETTINGS = Object.freeze({
  appearance: Object.freeze({}),
  editor: Object.freeze({
    trim_trailing_whitespace_on_save: false,
    ensure_final_newline_on_save: false,
  }),
  file_explorer: Object.freeze({
    index_refresh_seconds: 120,
    new_entry_highlight_ms: 60000,
  }),
  notifications: Object.freeze({
    toast_duration_ms: 10000,
  }),
  // Performance defaults are READ from the single owner (yolomux_lib/workspace/settings.py
  // DEFAULT_SETTINGS) instead of hand-copied here: this block previously drifted in
  // lockstep-edit fashion whenever a default changed. A missing key throws so a
  // renamed setting fails loudly rather than silently diverging.
  performance: Object.freeze(serverDefaultSettings(
    'latency_refresh_ms',
    'event_log_refresh_ms',
    'tabber_activity_refresh_ms',
    'agent_status_pulse_period_ms',
    'workflow_transition_glow_seconds',
    'popover_show_delay_ms',
    'popover_hide_delay_ms',
    'remote_resize_delay_ms',
  )),
});

class TestClassList {
  constructor() {
    this.names = new Set();
  }

  add(...names) {
    names.forEach(name => this.names.add(name));
  }

  remove(...names) {
    names.forEach(name => this.names.delete(name));
  }

  toggle(name, force) {
    if (force === true) {
      this.names.add(name);
      return true;
    }
    if (force === false) {
      this.names.delete(name);
      return false;
    }
    if (this.names.has(name)) {
      this.names.delete(name);
      return false;
    }
    this.names.add(name);
    return true;
  }

  contains(name) {
    return this.names.has(name);
  }
}

class TestStyle {
  constructor() {
    this.properties = new Map();
  }

  setProperty(name, value) {
    this.properties.set(name, value);
  }

  removeProperty(name) {
    this.properties.delete(name);
  }

  getPropertyValue(name) {
    return this.properties.get(name) || '';
  }
}

class TestWebSocket {
  constructor(url) {
    this.url = String(url || '');
    this.readyState = TestWebSocket.OPEN;
    this.sent = [];
    TestWebSocket.instances.push(this);
    setImmediate(() => this.onopen?.({target: this}));
  }

  send(message) {
    this.sent.push(message);
  }

  close() {
    this.readyState = TestWebSocket.CLOSED;
    this.onclose?.({target: this});
  }
}
TestWebSocket.instances = [];
TestWebSocket.OPEN = 1;
TestWebSocket.CLOSING = 2;
TestWebSocket.CLOSED = 3;

function testDatasetKeyForAttribute(name) {
  return String(name || '').replace(/-([a-z])/g, (_match, char) => char.toUpperCase());
}

function testHtmlAttributeValue(attrs, name) {
  const match = String(attrs || '').match(new RegExp(`\\b${name}="([^"]*)"`));
  return match ? match[1] : '';
}

function populateTestDatasetFromHtmlAttrs(node, attrs) {
  String(attrs || '').replace(/\bdata-([A-Za-z0-9_-]+)="([^"]*)"/g, (_match, name, value) => {
    node.dataset[testDatasetKeyForAttribute(name)] = value;
    return '';
  });
}

function testElementFromTmuxWindowBarHtml(html) {
  const source = String(html || '');
  const barMatch = source.match(/^<div\b([^>]*)>([\s\S]*)<\/div>$/);
  if (!barMatch || !/\btmux-window-bar\b/.test(barMatch[1])) return null;
  const [, barAttrs, body] = barMatch;
  const bar = new TestElement('', 'div');
  bar.className = testHtmlAttributeValue(barAttrs, 'class') || 'tmux-window-bar';
  populateTestDatasetFromHtmlAttrs(bar, barAttrs);
  bar._innerHTML = body;
  for (const buttonMatch of body.matchAll(/<button\b([^>]*)>/g)) {
    const attrs = buttonMatch[1] || '';
    const button = new TestElement('', 'button');
    button.className = testHtmlAttributeValue(attrs, 'class');
    populateTestDatasetFromHtmlAttrs(button, attrs);
    const pressed = testHtmlAttributeValue(attrs, 'aria-pressed');
    if (pressed) button.setAttribute('aria-pressed', pressed);
    bar.appendChild(button);
  }
  return bar;
}

class TestElement {
  constructor(id = '', tagName = 'div') {
    this.id = id;
    this.localName = tagName;
    this.tagName = String(tagName || 'div').toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this._innerHTML = '';
    this.textContent = '';
    this.removed = false;
    this.parentElement = null;
    this.scrollTop = 0;
    this.clientHeight = 200;
    this.isConnected = true;
    this.rect = {width: 1200, height: 800, left: 0, top: 0, right: 1200, bottom: 800};
    this.style = new TestStyle();
    this.classList = new TestClassList();
    this.listeners = new Map();
  }

  get className() { return Array.from(this.classList.names).join(' '); }
  set className(value) {
    this.classList = new TestClassList();
    String(value || '').split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name));
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    this.children.forEach(node => {
      node.parentElement = null;
    });
    this.children = [];
    const tmuxWindowBar = testElementFromTmuxWindowBarHtml(this._innerHTML);
    if (tmuxWindowBar) this.appendChild(tmuxWindowBar);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    const items = this.listeners.get(type) || [];
    this.listeners.set(type, items.filter(item => item !== listener));
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  prepend(...nodes) {
    for (const node of nodes.reverse()) {
      this.insertBefore(node, this.children[0] || null);
    }
  }
  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  insertBefore(node, before) {
    const existingIndex = this.children.indexOf(node);
    if (existingIndex >= 0) this.children.splice(existingIndex, 1);
    const index = before ? this.children.indexOf(before) : -1;
    node.parentElement = this;
    if (index >= 0) this.children.splice(index, 0, node);
    else this.children.push(node);
    return node;
  }
  insertAdjacentElement(position, node) {
    if (position !== 'afterend' || !this.parentElement) return this.appendChild(node);
    const siblings = this.parentElement.children;
    const existingIndex = siblings.indexOf(node);
    if (existingIndex >= 0) siblings.splice(existingIndex, 1);
    const index = siblings.indexOf(this);
    node.parentElement = this.parentElement;
    siblings.splice(index + 1, 0, node);
    return node;
  }
  after(...nodes) {
    if (!this.parentElement) return;
    let before = this.nextElementSibling;
    for (const node of nodes) {
      this.parentElement.insertBefore(node, before || null);
      before = node.nextElementSibling;
    }
  }
  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }
  get firstElementChild() {
    return this.children[0] || null;
  }
  get nextElementSibling() {
    const siblings = this.parentElement?.children || [];
    const index = siblings.indexOf(this);
    return index >= 0 ? siblings[index + 1] || null : null;
  }
  cloneNode() {
    const clone = new TestElement(`${this.id}-clone`, this.localName);
    clone.dataset = {...this.dataset};
    clone.attributes = {...this.attributes};
    clone.innerHTML = this.innerHTML;
    clone.textContent = this.textContent;
    clone.rect = {...this.rect};
    return clone;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some(child => child?.contains?.(node));
  }
  getBoundingClientRect() { return this.rect; }
  insertAdjacentHTML(position, html) {
    if (position !== 'beforeend') throw new Error(`TestElement.insertAdjacentHTML does not support ${position}`);
    this._innerHTML += String(html || '');
  }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    const descendantMatch = selector.match(/^(.+?)\s+(.+)$/);
    if (descendantMatch) {
      const [, ancestorSelector, nodeSelector] = descendantMatch;
      if (!this.matches(nodeSelector)) return false;
      let ancestor = this.parentElement;
      while (ancestor) {
        if (ancestor.matches?.(ancestorSelector)) return true;
        ancestor = ancestor.parentElement;
      }
      return false;
    }
    if (/^[A-Za-z][A-Za-z0-9-]*$/.test(selector)) return selector === String(this.localName || '').toLowerCase();
    if (selector === ':hover') return this.hovered === true;
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    const dataPaneTabMatch = selector.match(/^\.pane-tab\[data-pane-tab="([^"]+)"\]$/);
    if (dataPaneTabMatch) {
      return this.classList.contains('pane-tab') && this.dataset.paneTab === dataPaneTabMatch[1];
    }
    if (selector === '[data-window-dir]') return this.dataset.windowDir !== undefined;
    if (selector === '[data-window-index]') return this.dataset.windowIndex !== undefined;
    if (selector === '[data-detail-toggle]') return this.dataset.detailToggle !== undefined;
    const dataDetailToggleMatch = selector.match(/^\[data-detail-toggle="([^"]+)"\]$/);
    if (dataDetailToggleMatch) return this.dataset.detailToggle === dataDetailToggleMatch[1];
    const classDataMatch = selector.match(/^\.([A-Za-z0-9_-]+)\[data-([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (classDataMatch) {
      const [, className, attrName, attrValue] = classDataMatch;
      const key = testDatasetKeyForAttribute(attrName);
      return this.classList.contains(className) && this.dataset[key] !== undefined && (attrValue === undefined || this.dataset[key] === attrValue);
    }
    const dataMatch = selector.match(/^\[data-([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (dataMatch) {
      const [, attrName, attrValue] = dataMatch;
      const key = testDatasetKeyForAttribute(attrName);
      return this.dataset[key] !== undefined && (attrValue === undefined || this.dataset[key] === attrValue);
    }
    const attributeMatch = selector.match(/^\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (attributeMatch) {
      const [, attributeName, attributeValue] = attributeMatch;
      return this.attributes[attributeName] !== undefined && (attributeValue === undefined || this.attributes[attributeName] === attributeValue);
    }
    const tagDataMatch = selector.match(/^([A-Za-z][A-Za-z0-9-]*)\[data-([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (tagDataMatch) {
      const [, tagName, attrName, attrValue] = tagDataMatch;
      const key = testDatasetKeyForAttribute(attrName);
      return this.localName === tagName && this.dataset[key] !== undefined && (attrValue === undefined || this.dataset[key] === attrValue);
    }
    const tagAttributeMatch = selector.match(/^([A-Za-z][A-Za-z0-9-]*)\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (tagAttributeMatch) {
      const [, tagName, attributeName, attributeValue] = tagAttributeMatch;
      return this.localName === tagName
        && this.attributes[attributeName] !== undefined
        && (attributeValue === undefined || this.attributes[attributeName] === attributeValue);
    }
    if (selector === 'textarea[data-setting-path]') return this.localName === 'textarea' && this.dataset.settingPath !== undefined;
    if (selector === 'input[type="text"][data-setting-path]') return this.localName === 'input' && this.attributes.type === 'text' && this.dataset.settingPath !== undefined;
    if (selector === '[role="tree"]') return this.attributes.role === 'tree';
    if (selector === '.file-explorer-tree-panel') return this.classList.contains('file-explorer-tree-panel');
    if (selector === '.file-tree-row[data-path]') return this.classList.contains('file-tree-row') && Boolean(this.dataset.path);
    const fileTreePathMatch = selector.match(/^\.file-tree-row\[data-path="([^"]+)"\]$/);
    if (fileTreePathMatch) return this.classList.contains('file-tree-row') && this.dataset.path === fileTreePathMatch[1];
    if (/^\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$/.test(selector)) {
      return selector.slice(1).split('.').every(className => this.classList.contains(className));
    }
    if (/^\.[A-Za-z0-9_-]+$/.test(selector)) return this.classList.contains(selector.slice(1));
    throw new Error(`TestElement.matches does not support selector: ${selector}`);
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      if (className.includes('[')) {
        return this.children.find(child => child.matches(`.${className}`)) || null;
      }
      return this.children.find(child => child.classList?.contains(className)) || null;
    }
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      const scopedSelector = className.includes('[') ? `.${className}` : null;
      return this.children.filter(child => (
        scopedSelector ? child.matches(scopedSelector) : child.classList?.contains(className)
      ));
    }
    const matches = [];
    const visit = node => {
      for (const child of node.children || []) {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  remove() {
    this.removed = true;
    if (this.parentElement) {
      const index = this.parentElement.children.indexOf(this);
      if (index >= 0) this.parentElement.children.splice(index, 1);
    }
    this.parentElement = null;
  }
  scrollIntoView() {}
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.focused = true; }
  replaceChildren(...nodes) {
    this.children.forEach(node => {
      node.parentElement = null;
    });
    this.children = [];
    nodes.forEach(node => this.appendChild(node));
  }
  replaceWith(node) {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    if (index < 0) return;
    if (node.parentElement) {
      const previousSiblings = node.parentElement.children;
      const previousIndex = previousSiblings.indexOf(node);
      if (previousIndex >= 0) previousSiblings.splice(previousIndex, 1);
    }
    node.parentElement = this.parentElement;
    siblings.splice(index, 1, node);
    this.parentElement = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

class TestFile {
  constructor(parts, name, options = {}) {
    this.parts = parts;
    this.name = name;
    this.type = options.type || '';
    this.size = parts.reduce((total, part) => total + (Number(part?.size) || String(part || '').length), 0);
  }
}

class TestFormData {
  constructor() {
    this.fields = [];
  }

  append(name, value, filename = undefined) {
    this.fields.push({name, value, filename});
  }
}

class TestEventSource {
  constructor(url) {
    this.url = String(url || '');
    this.readyState = 1;
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter(item => item !== listener));
  }

  close() {
    this.closeCount = Number(this.closeCount || 0) + 1;
    this.readyState = 2;
  }
}

class TestWheelEvent {
  constructor(type, options = {}) {
    this.type = type;
    Object.assign(this, options);
  }
}

class TestCustomEvent {
  constructor(type, options = {}) {
    this.type = String(type || '');
    this.detail = options.detail;
  }
}

function assertNoStandalonePrBadge(html, label) {
  assert.equal(html.includes('>PR<'), false, `${label} avoids redundant PR text`);
  assert.equal(/class="[^"]*pr-indicator[^"]*"[^>]*>PR</.test(html), false, `${label} never renders a standalone PR text pill`);
  assert.equal(html.includes('pr-indicator'), false, `${label} suppresses the separate PR number badge`);
}

function assertSingleCiBadge(html, label) {
  assert.ok(html.includes('>CI</span>'), `${label} renders a CI badge`);
  assert.equal((html.match(/>CI<\/span>/g) || []).length, 1, `${label} renders exactly one CI badge`);
}

function sourceBetween(text, startMarker, endMarker) {
  const start = text.indexOf(startMarker);
  const end = text.indexOf(endMarker, start + String(startMarker).length);
  assert.ok(start >= 0 && end > start, `missing source range ${startMarker} -> ${endMarker}`);
  return text.slice(start, end);
}

function makeCatalogT(catalog) {
  return (key, params = {}) => String(catalog[String(key)] || key).replace(/\{(\w+)\}/g, (_match, name) => params[name] ?? '');
}

function deferredFetch() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return {promise, resolve, reject};
}

async function apiTransportRetirementScenario() {
  const outcome = promise => promise.then(() => 'resolved', error => String(error?.message || error));
  const api = loadYolomux();
  api.setStatsCurrentNamespaceForTest(null);
  const request = deferredFetch();
  api.setFetchForTest(() => request.promise);
  const pending = api.apiFetchJsonForTest('/api/auto-approve');
  api.windowListenersForTest('beforeunload')[0]({type: 'beforeunload'});
  request.reject(new TypeError('Failed to fetch'));
  const retiredError = await outcome(pending);
  const retirement = api.jsDebugEventsForTest().find(event => event.type === 'api' && event.endpoint === '/api/auto-approve');
  const retired = {error: retiredError, outcome: retirement.deliveryOutcome, reason: retirement.reason, failures: api.jsDebugFailureEventsForTest().length, backendFailures: api.backendHealthStateForTest().consecutiveFailures, consoleErrors: api.vmConsoleErrorsForTest().length};

  const lateApi = loadYolomux();
  lateApi.windowListenersForTest('beforeunload')[0]({type: 'beforeunload'});
  const lateRequest = deferredFetch();
  lateApi.setFetchForTest(() => lateRequest.promise);
  const latePending = lateApi.apiFetchJsonForTest('/api/late-retirement');
  lateRequest.reject(new TypeError('Failed to fetch'));
  const lateError = await outcome(latePending);
  const lateEvent = lateApi.jsDebugEventsForTest().find(event => event.endpoint === '/api/late-retirement');
  const lateRetired = {error: lateError, outcome: lateEvent.deliveryOutcome, reason: lateEvent.reason, failures: lateApi.jsDebugFailureEventsForTest().length, backendFailures: lateApi.backendHealthStateForTest().consecutiveFailures, consoleErrors: lateApi.vmConsoleErrorsForTest().length};

  const resumedApi = loadYolomux();
  resumedApi.windowListenersForTest('beforeunload')[0]({type: 'beforeunload'});
  const resumedRequest = deferredFetch();
  resumedApi.setFetchForTest(() => resumedRequest.promise);
  const resumedPending = resumedApi.apiFetchJsonForTest('/api/resumed-live');
  resumedApi.windowListenersForTest('pageshow')[0]({type: 'pageshow'});
  resumedRequest.reject(new TypeError('Failed to fetch'));
  const resumedError = await outcome(resumedPending);
  const resumedEvent = resumedApi.jsDebugEventsForTest().find(event => event.endpoint === '/api/resumed-live');
  const resumedLive = {error: resumedError, outcome: resumedEvent.deliveryOutcome, failures: resumedApi.jsDebugFailureEventsForTest().length, backendFailures: resumedApi.backendHealthStateForTest().consecutiveFailures, consoleErrors: resumedApi.vmConsoleErrorsForTest().length};

  api.windowListenersForTest('pageshow')[0]({type: 'pageshow'});
  api.setStatsCurrentNamespaceForTest({
    pageTransportLifecycle: new Proxy({}, {get() { throw new Error('public namespace used as runtime owner'); }}),
  });
  const first = deferredFetch();
  const second = deferredFetch();
  api.setFetchForTest(url => String(url).endsWith('/first') ? first.promise : second.promise);
  const firstPending = api.apiFetchJsonForTest('/api/first');
  const secondPending = api.apiFetchJsonForTest('/api/second');
  api.windowListenersForTest('beforeunload')[0]({type: 'beforeunload'});
  first.resolve(jsonResponse({ok: true}));
  await firstPending;
  second.reject(new TypeError('Failed to fetch'));
  const racedError = await outcome(secondPending);
  const raced = {error: racedError, outcome: api.jsDebugEventsForTest().find(event => event.endpoint === '/api/second').deliveryOutcome, failures: api.jsDebugFailureEventsForTest().length};

  const control = loadYolomux();
  const failure = deferredFetch();
  control.setFetchForTest(() => failure.promise);
  const failurePending = control.apiFetchJsonForTest('/api/auto-approve');
  failure.reject(new TypeError('Failed to fetch'));
  const failureError = await outcome(failurePending);
  const [recorded] = control.jsDebugFailureEventsForTest();
  const live = {error: failureError, type: recorded.type, endpoint: recorded.endpoint, outcome: recorded.deliveryOutcome, backendFailures: control.backendHealthStateForTest().consecutiveFailures};
  return {retired, lateRetired, resumedLive, raced, live};
}

function settingsOverride(settings = {}, defaults = DEFAULT_TEST_SETTINGS) {
  return {defaults, settings, mtime_ns: 1};
}

function loadYolomux(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64', accessRole = 'admin', options = {}) {
  const bootstrapOverrides = options.bootstrapOverrides || Object.fromEntries(Object.entries(options).filter(([key]) => !['sessionStorage', 'localStorage', 'fireTimeoutDelays', 'locationPort', 'locationHash', 'coarsePointer', 'hoverCapable', 'viewport', 'performance', 'PerformanceObserver'].includes(key)));
  const fireTimeoutDelays = new Set((Array.isArray(options.fireTimeoutDelays) ? options.fireTimeoutDelays : [])
    .map(Number)
    .filter(delay => Number.isFinite(delay) && delay >= 0));
  const coarsePointer = options.coarsePointer === true;
  const hoverCapable = options.hoverCapable === undefined ? !coarsePointer : options.hoverCapable === true;
  const viewport = options.viewport || {};
  const viewportWidth = Number.isFinite(Number(viewport.width)) ? Number(viewport.width) : 1200;
  const viewportHeight = Number.isFinite(Number(viewport.height)) ? Number(viewport.height) : 800;

  const bootstrapPayload = {
    sessions,
    availableAgents: [],
    agentLaunchCommands: {
      claude: {normal: 'claude', full_access: 'claude --dangerously-skip-permissions'},
      codex: {normal: 'codex', full_access: 'codex --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust'},
      term: {normal: 'bash', full_access: 'bash'},
    },
    terminalCommands: ['bash', 'tsh', 'zsh'],
    dangerouslyYolo: true,
    accessRole,
    homePath: '/home/test',
    repoRoot: '/home/test/yolomux.dev',
    maxSessionTabs: 99,
    // The real server always states the bounds /api/fs/batch refuses above (yolomux_lib/web.py),
    // and the bundle splits its posts at maxRequests. Seed the production values so every suite
    // exercises the configuration a browser actually boots into; the tests that care about the
    // split or about an unstated bound override this key explicitly.
    filesystemBatchLimits: {...SERVER_FILESYSTEM_BATCH_LIMITS},
    serverHostname: 'test-host',
    cpuTopology: {logical_cpus: 8, physical_cores: 4},
    localeRegistry: {
      fallback: 'en',
      pseudo: 'en-XA',
      systemPreference: 'system',
      systemLocale: 'en',
      locales: [
        ['en', 'English', 'ltr'], ['zh-Hant', '繁體中文', 'ltr'], ['zh-Hans', '简体中文', 'ltr'], ['ja', '日本語', 'ltr'], ['ko', '한국어', 'ltr'], ['es', 'Español', 'ltr'], ['de', 'Deutsch', 'ltr'], ['fr', 'Français', 'ltr'], ['it', 'Italiano', 'ltr'], ['pt-BR', 'Português (BR)', 'ltr'], ['pl', 'Polski', 'ltr'], ['nl', 'Nederlands', 'ltr'], ['he', 'עברית', 'rtl'], ['ar', 'العربية', 'rtl'], ['ru', 'Русский', 'ltr'], ['hi', 'हिन्दी', 'ltr'], ['vi', 'Tiếng Việt', 'ltr'], ['th', 'ไทย', 'ltr'], ['tr', 'Türkçe', 'ltr'],
      ].map(([id, endonym, direction]) => ({id, endonym, direction})),
    },
    // Seed the en catalog the way production inlines bootstrap.strings, so localized labels (the brand
    // tab labels infoTabLabel()/yoagentTabLabel() etc.) resolve synchronously at first render under en.
    strings: {en: EN_CATALOG},
    settingsPayload: {defaults: DEFAULT_TEST_SETTINGS, settings: {}, mtime_ns: 1},
    ...bootstrapOverrides,
  };
  const bootstrap = JSON.stringify(bootstrapPayload);
  const elements = new Map();
  const documentListeners = new Map();
  const windowListeners = new Map();
  const storage = new Map(Object.entries(options.localStorage || {}).map(([key, value]) => [String(key), String(value)]));
  const sessionStorageMap = new Map(Object.entries(options.sessionStorage || {}).map(([key, value]) => [String(key), String(value)]));
  const localStorage = {
    getItem(key) { return storage.has(String(key)) ? storage.get(String(key)) : null; },
    setItem(key, value) { storage.set(String(key), String(value)); },
    removeItem(key) { storage.delete(String(key)); },
  };
  const sessionStorage = {
    getItem(key) { return sessionStorageMap.has(String(key)) ? sessionStorageMap.get(String(key)) : null; },
    setItem(key, value) { sessionStorageMap.set(String(key), String(value)); },
    removeItem(key) { sessionStorageMap.delete(String(key)); },
  };
  const locationPort = String(options.locationPort || '7770');
  const location = {
    search,
    pathname: '/',
    hash: String(options.locationHash || ''),
    protocol,
    hostname: 'localhost',
    port: locationPort,
    host: `localhost:${locationPort}`,
    assign(url) { context.__locationAssignments.push(String(url)); },
    reload() { context.__reloadCount = (context.__reloadCount || 0) + 1; },
  };
  const immediateTimeoutHandles = new Set();
  const testSetTimeout = options.setTimeout || ((callback, ms) => {
    const delay = Number(ms);
    if ((delay === 8 || fireTimeoutDelays.has(delay)) && typeof callback === 'function') {
      const handle = setImmediate(() => {
        immediateTimeoutHandles.delete(handle);
        callback();
      });
      immediateTimeoutHandles.add(handle);
      return handle;
    }
    return 0;
  });
  const testClearTimeout = options.clearTimeout || (handle => {
    if (!immediateTimeoutHandles.delete(handle)) return;
    clearImmediate(handle);
  });
  const testSetInterval = options.setInterval || (() => {});
  const testClearInterval = options.clearInterval || (() => {});
  // Product code deliberately logs recoverable transport failures. Capture those VM logs so green
  // node runs stay readable and the test which triggers a failure can assert its diagnostic.
  const vmConsoleErrors = [];
  const vmConsole = {
    log() {},
    info() {},
    debug() {},
    warn(...args) { vmConsoleErrors.push(args.map(String).join(' ')); },
    error(...args) { vmConsoleErrors.push(args.map(String).join(' ')); },
  };
  const notification = {permission: 'denied'};
  const element = id => {
    if (!elements.has(id)) elements.set(id, new TestElement(id));
    const node = elements.get(id);
    if (id === 'yolomux-bootstrap') node.textContent = bootstrap;
    return node;
  };
  const context = {
    console: vmConsole,
    EventSource: TestEventSource,
    WheelEvent: TestWheelEvent,
    CustomEvent: class TestCustomEvent {
      constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
    },
    File: TestFile,
    FormData: TestFormData,
    URL,
    URLSearchParams,
    WebSocket: TestWebSocket,
    __testWebSocketInstances: TestWebSocket.instances,
    AbortController,
    CustomEvent: TestCustomEvent,
    clearInterval: testClearInterval,
    clearTimeout: testClearTimeout,
    document: {
      __listeners: documentListeners,
      addEventListener(type, listener) {
        if (!documentListeners.has(type)) documentListeners.set(type, []);
        documentListeners.get(type).push(listener);
      },
      body: element('body'),
      createElement: tag => new TestElement('', tag),
      createTextNode(text) {
        const node = new TestElement('', '#text');
        node.textContent = String(text ?? '');
        return node;
      },
      documentElement: element('html'),
      execCommand(command) {
        if (String(command || '').toLowerCase() !== 'copy') return false;
        let prevented = false;
        const event = {
          clipboardData: {
            setData(type, value) {
              if (type === 'text/plain') context.__clipboardText = String(value);
            },
          },
          preventDefault() { prevented = true; },
          stopPropagation() {},
          stopImmediatePropagation() {},
        };
        for (const listener of [...(documentListeners.get('copy') || [])]) listener(event);
        return prevented;
      },
      getElementById: element,
      querySelector: () => null,
      querySelectorAll: () => [],
      removeEventListener(type, listener) {
        const listeners = documentListeners.get(type) || [];
        documentListeners.set(type, listeners.filter(item => item !== listener));
      },
      activeElement: null,
    },
    fetch() { return Promise.reject(new Error('fetch disabled in layout URL tests')); },
    getComputedStyle: () => ({direction: 'ltr'}),
    history: {
      replaceState(_state, _title, url) {
        context.__lastUrl = url;
      },
    },
    location,
    navigator: {
      platform: navigatorPlatform,
      userAgent: navigatorPlatform,
      maxTouchPoints: coarsePointer ? 5 : 0,
      clipboard: {
        writeText(text) {
          context.__clipboardText = String(text ?? '');
          return Promise.resolve();
        },
      },
    },
    Notification: notification,
    performance: options.performance || {now: () => 0},
    PerformanceObserver: options.PerformanceObserver,
    ResizeObserver: options.ResizeObserver,
    requestAnimationFrame: options.requestAnimationFrame || (callback => callback()),
    cancelAnimationFrame: options.cancelAnimationFrame || (() => {}),
    setInterval: testSetInterval,
    // The bundle schedules the batched /api/fs/batch directory-listing flush via
    // setTimeout(flushFileExplorerFsBatch, fileExplorerFsBatchDelayMs) — and 8ms is UNIQUE to that flush
    // (verified: no other bundle timer uses an 8ms delay). All other bundle timers (polls, debounces,
    // periodic publishers) must stay no-ops here. So fire ONLY the 8ms flush, on a real setImmediate, so any
    // code that `await`s a directory listing settles instead of hanging forever (which used to leave the
    // trailing suite IIFE unsettled and silently exit 0 — see the suite watchdog + AGENTS.md note).
    // Synchronous back-to-back enqueues still batch into one request (no await between them = no yield),
    // so batching/coalescing tests are unaffected.
    setTimeout: testSetTimeout,
    window: {
      __listeners: windowListeners,
      Notification: notification,
      ResizeObserver: options.ResizeObserver,
      addEventListener(type, listener) {
        if (!windowListeners.has(type)) windowListeners.set(type, []);
        windowListeners.get(type).push(listener);
      },
      dispatchEvent(event) {
        for (const listener of [...(windowListeners.get(event?.type) || [])]) listener(event);
        return true;
      },
      clearTimeout: testClearTimeout,
      confirm: () => true,
      dispatchEvent(event) {
        for (const listener of windowListeners.get(String(event?.type || '')) || []) listener(event);
        return true;
      },
      EventSource: TestEventSource,
      innerHeight: viewportHeight,
      innerWidth: viewportWidth,
      location,
      localStorage,
      open(url, name, features) {
        const record = {
          url: String(url || ''),
          name: String(name || ''),
          features: String(features || ''),
        };
        const documentStub = {
          readyState: 'complete',
          body: element(`popout-body-${context.__openedWindows.length}`),
          title: '',
          open() { this.html = ''; },
          write(html) { this.html = String(html || ''); },
          close() {},
          querySelector() { return null; },
        };
        const popoutWindow = {
          closed: false,
          document: documentStub,
          location: {pathname: record.url.split('?')[0] || ''},
          addEventListener() {},
          close() { this.closed = true; },
          focus() { record.focused = true; },
        };
        record.window = popoutWindow;
        context.__openedWindows.push(record);
        return popoutWindow;
      },
      sessionStorage,
      removeEventListener(type, listener) {
        const listeners = windowListeners.get(type) || [];
        windowListeners.set(type, listeners.filter(item => item !== listener));
      },
      matchMedia(query) {
        const mediaQuery = String(query || '');
        return {matches: (coarsePointer && mediaQuery.includes('pointer: coarse')) || (hoverCapable && mediaQuery.includes('any-hover: hover')), addEventListener() {}, addListener() {}};
      },
      setTimeout: testSetTimeout,
    },
    localStorage,
    sessionStorage,
    __locationAssignments: [],
    __clipboardText: '',
    // the OSC 52 clipboard bridge decodes base64 UTF-8; expose the host implementations.
    atob,
    btoa,
    TextEncoder,
    TextDecoder,
    Uint8Array,
    // Host ArrayBuffer so binary terminal-socket frames built by tests pass the bundle's
    // `data instanceof ArrayBuffer` routing (Uint8Array/TextEncoder above are host classes too).
    ArrayBuffer,
  };
  context.globalThis = context;
  context.__openedWindows = [];
  vm.createContext(context);
  (bundleScript ||= new vm.Script(`${BUNDLE_SOURCE.slice(0, BUNDLE_BOOT_START)}
globalThis.__layoutTestApi = {
  safeDecodeURIComponent,
  utf8ByteLength,
  performanceNow,
  domDataAttributeName,
  singleLineText,
  activeItemForSide,
  agentErrorIsBlocking,
  appModifier,
  appMenuTree,
  openAppMenuForTest: openAppMenu,
  topbarMenuTreeForTest: topbarMenuTree,
  topbarFullNavigationFitsForTest: topbarFullNavigationFits,
  topbarFullMenuFitsAvailableSpaceForTest: topbarFullMenuFitsAvailableSpace,
  topbarNavigationShouldBeCompactForTest: topbarNavigationShouldBeCompact,
  compactTopbarForViewportForTest: compactTopbarForViewport,
  appShortcutText,
  aboutBrandHtml,
  showAboutModal,
  startupHelperCatalog,
  readStartupHelperIndex,
  writeStartupHelperIndex,
  showStartupHelperTip,
  t,
  tPlural,
  yoagentInlineMarkdown,
  yoagentTightMarkdown,
  joinAndNormalize,
  i18nNormalizeLocale,
  resolveLocalePref,
  i18nLocaleChoices,
  i18nIsRtl,
  relativeTimeFormat,
  compactRelativeTimeFormat,
  sessionFileLookbackDefaultHoursForTest: sessionFileLookbackDefaultHours,
  sessionFileLookbackHourValuesForTest: sessionFileLookbackHourValues,
  sessionFileLookbackLabelForTest: sessionFileLookbackLabel,
  sessionFileLookbackOptionsForTest: sessionFileLookbackOptions,
  normalizeSessionFileLookbackHoursForTest: normalizeSessionFileLookbackHours,
  backgroundOwnerSearchIndexSummaryForTest: backgroundOwnerSearchIndexSummary,
  backgroundOwnerStatsSummaryForTest: backgroundOwnerStatsSummary,
  backgroundOwnerSessionFilesSummaryForTest: backgroundOwnerSessionFilesSummary,
  backgroundOwnerOwnsAllRolesForTest: backgroundOwnerOwnsAllRoles,
  backgroundOwnerCurrentOwnerLiveForTest: backgroundOwnerCurrentOwnerLive,
  topbarOwnerStatusHtmlForTest: topbarOwnerStatusHtml,
  topbarOwnerStatusTitleForTest: topbarOwnerStatusTitle,
  createTopbarOwnerStatusForTest: createTopbarOwnerStatus,
  showBackgroundOwnerContextMenuForTest: showBackgroundOwnerContextMenu,
  setBackgroundOwnerStatusPayloadForTest(payload) {
    backgroundOwnerStatusState.resource?.replace(payload, 'test');
    backgroundOwnerStatusState.payload = payload;
    backgroundOwnerStatusState.updatedAt = Date.now();
    backgroundOwnerStatusState.loading = false;
    backgroundOwnerStatusState.error = '';
  },
  refreshBackgroundOwnerStatusForTest: refreshBackgroundOwnerStatus,
  loadAutoStatusesForTest: loadAutoStatuses,
  autoStatusRequestActiveForTest() { return loadAutoStatuses.request !== null; },
  applyBackgroundOwnerStatusPayloadForTest: applyBackgroundOwnerStatusPayload,
  backgroundOwnerStatusStateForTest() {
    return {
      payload: backgroundOwnerStatusState.payload,
      loading: backgroundOwnerStatusState.loading,
      error: backgroundOwnerStatusState.error,
      request: backgroundOwnerStatusState.request,
      updatedAt: backgroundOwnerStatusState.updatedAt,
    };
  },
  i18nActiveLocaleId,
  i18nSetCatalogForTest,
  applyLocaleForTest: applyLocale,
  transcriptItemHtmlForTest: transcriptItemHtml,
  transcriptAgentErrorTextForTest: transcriptAgentErrorText,
  transcriptContextLoadErrorTextForTest: transcriptContextLoadErrorText,
  transcriptMetadataLoadErrorTextForTest: transcriptMetadataLoadErrorText,
  transcriptMetadataLoadErrorLabelForTest: transcriptMetadataLoadErrorLabel,
  transcriptMetadataLoadErrorSnapshotForTest: transcriptMetadataLoadErrorSnapshot,
  fetchAndApplySessionMetadataForTest: fetchAndApplySessionMetadata,
  setTranscriptMetadataLoadErrorForTest(value) { transcriptMetadataState.error = value; },
  refreshSessionMetadataForTest: refreshSessionMetadata,
  applySessionMetadataPayloadForTest: applySessionMetadataPayload,
  transcriptMetadataStateForTest() {
    return {
      payload: transcriptMetadataState.payload,
      loading: transcriptMetadataState.loading,
      loaded: transcriptMetadataState.loaded,
      error: transcriptMetadataState.error,
      request: transcriptMetadataState.request,
      epoch: transcriptMetadataState.epoch,
      previousEpoch: transcriptMetadataState.previousEpoch,
      generation: transcriptMetadataState.generation,
      pendingGeneration: transcriptMetadataState.pendingGeneration,
      lastApply: transcriptMetadataState.lastApply,
    };
  },
  settleForcedSessionMetadataForTest: settleForcedSessionMetadata,
  adoptServerEpochForTest: adoptServerEpoch,
  refreshTmuxSessionMutationStateForTest: refreshTmuxSessionMutationState,
  metadataConvergenceStatusForTest() { return statusEl.dataset.metadataConvergence || ''; },
  repoComparisonErrorHtmlForTest: repoComparisonErrorHtml,
  setActiveLocaleForTest(locale) { i18nActiveLocale = locale; },
  updateNotificationAllowsVersionForTest: updateNotificationAllowsVersion,
  normalizeUpdateNotificationLevelForTest: normalizeUpdateNotificationLevel,
  createAppMenuCommand,
  backgroundTabItems,
  canPaneExpand,
  codeMirrorHtmlSemanticEmphasisExtension,
  codeMirrorApiIsUsable,
  codeMirrorLanguageExtension,
  codeMirrorHighlightExtension,
  codeMirrorMarkdownCodeLanguages,
  createEditableCodeMirrorState,
  codeMirrorPlainEditableExtensions,
  normalizeFileEditorSaveContentForTest: normalizeFileEditorSaveContent,
  fileEditorTextMetricsForTest: fileEditorTextMetrics,
  fileEditorCountStatusTextForTest: fileEditorCountStatusText,
  fileEditorImageVersionForTest: rawFileMediaVersion,
  updateCodeMirrorCursorStatusForTest: updateCodeMirrorCursorStatus,
  codeMirrorSearchMatches,
  codeMirrorSearchMatchSummary,
  codeMirrorSearchVisibleMatchIndex,
  codeMirrorPhraseValues,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  editorNav,
  editorNavBackForTest: editorNavBack,
  recordEditorNav,
  cursorStyleFileReference,
  fileEditorPaneTabHtml,
  paneTabAriaLabelForTest: paneTabAriaLabel,
  fileQuickOpenItem,
  fileQuickOpenItems,
  movingEllipsisHtml,
  stripTrailingEllipsisText,
  fileQuickOpenExtraRootsForSearchQuery,
  fileQuickOpenRootMatchesPathAlias,
  fileQuickOpenRootForFile,
  fileQuickOpenRootForSearch,
  fileQuickOpenRootsForSearch,
  fileQuickOpenSearchPayloadResultForTest: fileQuickOpenSearchPayloadResult,
  fileQuickOpenTargetSlot,
  fileQuickOpenSearchText,
  fileQuickOpenScopeLabel,
  fileIndexStatusFromPayloadForTest: fileIndexStatusFromPayload,
  fileIndexFreshnessFromPayloadForTest: fileIndexFreshnessFromPayload,
  fileIndexFreshnessMessageForTest: fileIndexFreshnessMessage,
  fileQuickOpenWorstFreshnessForTest: fileQuickOpenWorstFreshness,
  applyFileIndexStatusPayloadForTest: applyFileIndexStatusPayload,
  fileExplorerIndexBadgeTitleForTest: fileExplorerIndexBadgeTitle,
  fileExplorerIndexStatusForTest(root) { return fileExplorerIndexStatus.get(normalizeStoredFileExplorerIndexedDir(root)) || ''; },
  showFileIndexPartialCoverageWarningForTest: showFileIndexPartialCoverageWarning,
  clearFileIndexPartialWarningsForTest() { fileIndexPartialWarningRoots.clear(); },
  seedFileIndexPartialWarningRootForTest(root) { fileIndexPartialWarningRoots.add(normalizeStoredFileExplorerIndexedDir(root)); },
  fileIndexPartialWarningRootWarnedForTest(root) { return fileIndexPartialWarningRoots.has(normalizeStoredFileExplorerIndexedDir(root)); },
  fileExplorerDirectoryIsIndexed,
  fileExplorerIndexBadgeText,
  gitStatusRowClass,
  fileEditorGitActionControlsVisible,
  fileStateCanRenderDiffView,
  diffModeShouldFallBackToEdit,
  setFileExplorerIndexedDirsForTest(paths) { setFileExplorerIndexedDirs(paths); },
  setFileExplorerIndexExcludePathsForTest(paths) {
    fileExplorerIndexExcludePaths = new Set((paths || []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean));
  },
  setFileExplorerIndexStatusForTest(root, status) { fileExplorerIndexStatus.set(normalizeStoredFileExplorerIndexedDir(root), status); },
  createTopbarSearch,
  createTopbarNav,
  normalizeWatchedPrRef,
  watchedPrStatusSnapshot,
  watchedPrTransitionKeys,
  shouldNotifyTransitionKey,
  codeMirrorDiffLayout,
  buildDiffOverviewGradientForTest: buildDiffOverviewGradient,
  buildDiffOverviewGradientFromBandsForTest: buildDiffOverviewGradientFromBands,
  diffOverviewRowsFromCodeMirrorChunksForTest: diffOverviewRowsFromCodeMirrorChunks,
  diffOverviewRowsFromCodeMirrorRenderedWeightsForTest: diffOverviewRowsFromCodeMirrorRenderedWeights,
  diffOverviewScrollLooksCurrentOnlyForTest: diffOverviewScrollLooksCurrentOnly,
  diffOverviewCodeMirrorChunksForTest: diffOverviewCodeMirrorChunks,
  diffOverviewRemovedLineCountForTest: diffOverviewRemovedLineCount,
  updateCodeMirrorDiffOverviewForTest: updateCodeMirrorDiffOverview,
  setDiffExpandUnchangedForTest(value) { diffExpandUnchanged = value === true; },
  fileExplorerChangesPanelHtml,
  bindChangesPanelForTest: bindChangesPanel,
  fileTreeExpandCollapseAllButtonsHtml,
  fileExplorerDirectoryPathsForRootForTest: fileExplorerDirectoryPathsForRoot,
  setAllFileTreeDirectoriesExpandedForTest: setAllFileTreeDirectoriesExpanded,
  fileExplorerSessionFilesTargetSessionForTest: fileExplorerSessionFilesTargetSession,
  fileExplorerFinderTargetSessionForTest: fileExplorerFinderTargetSession,
  switchFileExplorerFinderSessionForTest: switchFileExplorerFinderSession,
  sessionFilesCacheKeyForTest: sessionFilesCacheKey,
  clientSessionFilesWatchRequestsForTest: clientSessionFilesWatchRequests,
  setGlobalDiffRefsForTest(fromRef, toRef) {
    diffRefFrom = String(fromRef || 'HEAD');
    diffRefTo = String(toRef || 'current');
  },
  setRepoDiffRefsForTest: setRepoDiffRefs,
  sessionFilesCacheSizesForTest() {
    return {finder: fileExplorerFinderSessionFilesCache.size, differ: fileExplorerSessionFilesCache.size};
  },
  noteFileExplorerChangesSessionInteractionForTest: noteFileExplorerChangesSessionInteraction,
  setFileExplorerChangesSelectedSessionForTest(value) { fileExplorerChangesSelectedSession = String(value || ''); },
  setFileExplorerFinderSelectedSessionForTest(value) { fileExplorerFinderSelectedSession = String(value || ''); },
  changesGroupsSnapshotHtmlForTest: changesGroupsSnapshotHtml,
  fetchSessionFilesForTest: fetchSessionFiles,
  fileExplorerSessionFilesStateForTest() { return {...fileExplorerSessionFilesState}; },
  fileExplorerFinderSessionFilesStateForTest() { return {...fileExplorerFinderSessionFilesState}; },
  sessionFilesRequestForDestinationForTest: sessionFilesRequestForDestination,
  sessionFilesPayloadSignatureForPayloadForTest: sessionFilesPayloadSignatureForPayload,
  fileExplorerChangesCollapseToggleHtml,
  fileExplorerChangesAllReposCollapsedForTest: fileExplorerChangesAllReposCollapsed,
  toggleAllFileExplorerChangesForTest: toggleAllFileExplorerChanges,
  projectMetaHtml,
  paneInfoBarMetaPartsForTest: paneInfoBarMetaParts,
  paneInfoBarMetaHtml,
  cycleSessionRepoDisplayForTest: cycleSessionRepoDisplay,
  diffRefControlsHtml,
  diffRefResetButtonHtml,
  diffRefSelectOptionsHtml,
  diffRefPopoverItems,
  ensureDiffRefPopoverForTest: ensureDiffRefPopover,
  hideDiffRefPopoverForTest: hideDiffRefPopover,
  handleDiffRefPopoverKeydownForTest: handleDiffRefPopoverKeydown,
  setDiffRefPopoverStateForTest(input, items = [], activeIndex = -1) {
    diffRefPopoverState.input = input;
    diffRefPopoverState.items = items;
    diffRefPopoverState.activeIndex = activeIndex;
    if (diffRefPopoverState.node) diffRefPopoverState.node.hidden = false;
  },
  diffRefPopoverStateForTest() {
    return {
      hasNode: Boolean(diffRefPopoverState.node),
      input: diffRefPopoverState.input,
      itemCount: diffRefPopoverState.items.length,
      activeIndex: diffRefPopoverState.activeIndex,
      listenersInstalled: diffRefPopoverState.listenersInstalled,
      hidden: diffRefPopoverState.node?.hidden !== false,
    };
  },
  diffRefCompactDisplayForTest: diffRefCompactDisplay,
  diffRefPopoverSubjectPartsForTest: diffRefPopoverSubjectParts,
  diffRefParams,
  diffRefFromSuggestions,
  diffRefToSuggestions,
  fileDiffRefHistoryItems,
  fileRepoForPath,
  setDiffRefsByRepoForTest(repo, refs) {
    if (!repo) return;
    if (!refs) delete diffRefsByRepo[repo];
    else diffRefsByRepo[repo] = {from: refs.from, to: refs.to};
  },
  globalActivitySummaryHtml,
  yoagentChatHtml,
  yoagentResolvedBackendForTest: yoagentResolvedBackend,
  yoagentAvailableBackendOptionsForTest: yoagentAvailableBackendOptions,
  yoagentChatEnabledForTest: yoagentChatEnabled,
  setYoagentDraftForTest(value) { yoagentChatState.draft = String(value || ''); },
  setYoagentBusyForTest(value) { yoagentChatState.busy = Boolean(value); },
  setYoagentActiveChatRequestForTest(value) { yoagentChatState.activeRequest = value; },
  yoagentActiveChatRequestForTest() { return yoagentChatState.activeRequest; },
  yoagentChatQueueForTest() { return yoagentChatState.queue.slice(); },
  yoagentChatStateForTest() { return {...yoagentChatState, queue: yoagentChatState.queue.slice()}; },
  yoagentStartupStateForTest() { return {...yoagentStartupState}; },
  cancelQueuedYoagentChatMessageForTest: cancelQueuedYoagentChatMessage,
  cancelActiveYoagentChatRequestForTest: cancelActiveYoagentChatRequest,
  sendYoagentChatMessageForTest: sendYoagentChatMessage,
  executeYoagentActionSendForTest: executeYoagentActionSend,
  prewarmYoagentForTest: prewarmYoagent,
  clearYoagentConversationForTest: clearYoagentConversation,
  setYoagentErrorForTest(value) { yoagentChatState.error = value && typeof value === 'object' ? value : String(value || ''); },
  setYoagentNoticeForTest(value) { yoagentChatState.notice = value; },
  setYoagentMessagesForTest(value) { yoagentConversationState.resource?.invalidate({source: 'test'}); yoagentConversationState.messages = Array.isArray(value) ? value : []; resetYoagentComposerHistory(); },
  applyYoagentStreamPayloadForTest: applyYoagentStreamPayload,
  refreshActivitySummaryForTest: refreshActivitySummary,
  showYoagentStartupInfoOnceForTest: showYoagentStartupInfoOnce,
  showYoagentStartupInfoForLatestActivityForTest: showYoagentStartupInfoForLatestActivity,
  hideYoagentStartupInfoForTest: hideYoagentStartupInfo,
  applyActivitySummaryPayloadFromPushForTest: applyActivitySummaryPayloadFromPush,
  yoagentOpenMessageDetailsStateForTest: yoagentOpenMessageDetailsState,
  restoreYoagentOpenMessageDetailsStateForTest: restoreYoagentOpenMessageDetailsState,
  yoagentUserMessageHistoryForTest: yoagentUserMessageHistory,
  yoagentNavigateChatHistoryForTest: yoagentNavigateChatHistory,
  resetYoagentComposerHistoryForTest: resetYoagentComposerHistory,
  applyYoagentConversationPayloadForTest: applyYoagentConversationPayload,
  loadYoagentConversationForTest: loadYoagentConversation,
  yoagentConversationStateForTest() {
    return {
      messages: yoagentConversationState.messages,
      pendingWaits: yoagentConversationState.pendingWaits,
      loaded: yoagentConversationState.loaded,
      loading: yoagentConversationState.loading,
      path: yoagentConversationState.path,
      displayPath: yoagentConversationState.displayPath,
      streamingCount: yoagentConversationState.streamingMessages.size,
      request: yoagentConversationState.request,
    };
  },
  applyYoagentJobsPayloadForTest: applyYoagentJobsPayload,
  yoagentJobsHtmlForTest: yoagentJobsHtml,
  loadYoagentJobsForTest: loadYoagentJobs,
  yoagentJobsStateForTest() {
    return {items: yoagentJobsState.items, loading: yoagentJobsState.loading, request: yoagentJobsState.request};
  },
  confirmYoagentJobForTest: confirmYoagentJob,
  cancelYoagentJobForTest: cancelYoagentJob,
  clearYoagentPendingWaitForTest: clearYoagentPendingWait,
  sessionActivitySummary,
  fitAppMenuPopover,
  finderDirectoryForItem,
  finderTargetPathForItem,
  activeFinderDirectoryPath,
  activeFinderTargetPath,
  activeTmuxDirectoryPath,
  commonAncestorPath,
  cancelPendingFileExplorerActiveSync,
  fileExplorerSyncPlanForTest: fileExplorerSyncPlan,
  fileExplorerSyncTargetDirsForTest: fileExplorerSyncTargetDirs,
  sessionFilesAffectedDirsForTest: sessionFilesAffectedDirs,
  syncFileExplorerRootToPlanForTest: syncFileExplorerRootToPlan,
  fetchFileExplorerSyncListingsForTest: fetchFileExplorerSyncListings,
  fileExplorerSyncStateForTest() { return {...fileExplorerSyncState}; },
  setFileExplorerVisibleSyncTargetForTest: setFileExplorerVisibleSyncTarget,
  rememberFileExplorerSyncExpandedStateForTest: rememberFileExplorerSyncExpandedState,
  resetFileExplorerSyncManualCollapsesForTest: resetFileExplorerSyncManualCollapsesIfNeeded,
  rememberFileExplorerSyncManualCollapseForTest: rememberFileExplorerSyncManualCollapse,
  setFileExplorerSyncUserExpansionForTest: setFileExplorerSyncUserExpansion,
  fileExplorerSyncUserExpansionStateForTest: fileExplorerSyncUserExpansionEntries,
  fileExplorerSyncManualCollapsedPathsForTest() { return Array.from(fileExplorerSyncManualCollapsedPaths).sort(); },
  fileExplorerSyncTargetRecordForTest(targetKey, create = false, touch = false) {
    const record = fileExplorerSyncTargetRecord(targetKey, create, touch);
    return record ? {
      expandedPaths: [...record.expandedPaths],
      manualCollapsedPaths: [...record.manualCollapsedPaths].sort(),
      cursorPath: record.cursorPath || '',
      selectedPaths: [...(record.selectedPaths || [])].sort(),
      anchorPath: record.anchorPath || '',
    } : null;
  },
  restoreFileExplorerSyncCursorStateForTest: restoreFileExplorerSyncCursorState,
  touchFileExplorerSyncTargetRecordForTest(targetKey) { return Boolean(fileExplorerSyncTargetRecord(targetKey, true, true)); },
  fileExplorerSyncTargetRecordKeysForTest() { return [...fileExplorerSyncTargetRecords.keys()]; },
  fileExplorerMemoryCacheLimitForTest: fileExplorerMemoryCacheLimit,
  setFileExplorerSyncStateForTest(value = {}) {
    fileExplorerSyncState.inFlightSignature = String(value.inFlightSignature || '');
    fileExplorerSyncState.appliedPlanKey = String(value.appliedPlanKey || '');
    fileExplorerSyncState.generation = Number(value.generation || 0);
  },
  fileExplorerSessionHighlightClassForPath,
  fileExplorerSessionHighlightClassForTest(path, kind = 'dir', preferredItem = null) {
    return fileExplorerSessionHighlightClassForPath(path, kind, {sessionHighlightSets: fileExplorerSessionHighlightSets(preferredItem)});
  },
  fileExplorerSessionHighlightSetsForTest(preferredItem = null) {
    const sets = fileExplorerSessionHighlightSets(preferredItem);
    return {repoRoots: [...sets.repoRoots], touchedDirs: [...sets.touchedDirs], expandedDirs: [...sets.expandedDirs], syncTargetDirs: [...sets.syncTargetDirs]};
  },
  fileExplorerRootForOpen,
  fileExplorerRootModeValue,
  setFileExplorerRootMode,
  scheduleFileExplorerActiveTabSyncForTest: scheduleFileExplorerActiveTabSync,
  fileExplorerLabel,
  fileExplorerPanelCloseClass,
  fileEditorPanelCloseClass,
  fileIconFor,
  fileIconClassFor,
  fileExplorerNeedsLeftDock,
  openFileExplorerAtForTest: openFileExplorerAt,
  visibleFileEditorWatchFilesForTest: visibleFileEditorWatchFiles,
  backgroundFileEditorWatchFilesForTest: backgroundFileEditorWatchFiles,
  clientServerWatchStateForTest: clientServerWatchState,
  clientEventDemandDescriptorForTest: clientEventDemandDescriptor,
  setEventLogTabActiveForTest(session, active = true) {
    const pane = document.getElementById('events-pane-' + session);
    pane.classList.toggle(CLS.active, active === true);
    document.getElementById(panelDomId(session)).querySelector = selector => selector === '.tab-pane.active' && active === true ? pane : null;
  },
  setEventLogScrollTopForTest(session, value) { document.getElementById('events-' + session).scrollTop = Number(value) || 0; },
  eventLogScrollTopForTest(session) { return Number(document.getElementById('events-' + session).scrollTop) || 0; },
  refreshEventLogsFromPushForTest: refreshEventLogsFromPush,
  syncClientEventDemandForTest: syncClientEventDemand,
  applyClientEventDemandForTest: applyClientEventDemand,
  applyClientEventReadyEnvelopeForTest: applyClientEventReadyEnvelope,
  clientEventReadyGapResourcesForTest: clientEventReadyGapResources,
  repairClientEventResourcesForTest: repairClientEventResources,
  clientPushCanSupplyDataForTest: clientPushCanSupplyData,
  readOnlyModeForTest() { return readOnlyMode; },
  setClientEventsSourceForTest(value = {}) { clientEventTransportState.source = value; },
  setEventSourceConstructorForTest(value) { globalThis.EventSource = value; },
  addWindowEventListenerForTest(type, listener) { window.addEventListener(type, listener); },
  clientEventTransportStateForTest() {
    return {
      source: clientEventTransportState.source,
      replacementSource: clientEventTransportState.replacementSource,
      connected: clientEventTransportState.connected,
      reconnectPending: clientEventTransportState.reconnectPending,
      disconnectEpisode: clientEventTransportState.disconnectEpisode ? {
        id: clientEventTransportState.disconnectEpisode.id,
        reported: clientEventTransportState.disconnectEpisode.reported,
        hasSource: clientEventTransportState.disconnectEpisode.source !== null,
      } : null,
      enabled: clientEventTransportState.enabled,
      candidateEpisode: clientEventTransportState.candidateEpisode ? {
        source: clientEventTransportState.candidateEpisode.source,
        attempts: clientEventTransportState.candidateEpisode.attempts,
        demandSignature: clientEventTransportState.candidateEpisode.demandSignature,
      } : null,
      demand: clientEventTransportState.demand,
      demandSignature: clientEventTransportState.demandSignature,
      resourceEpoch: clientEventTransportState.resourceEpoch,
      resourceRevisions: Object.fromEntries(clientEventTransportState.resourceRevisions),
      queued: clientEventTransportState.queue.size,
      frame: clientEventTransportState.frame,
      demandTimer: clientEventTransportState.demandTimer,
      disconnectTimer: clientEventTransportState.disconnectTimer,
      resyncTimer: clientEventTransportState.resyncTimer,
    };
  },
  syncServerWatchRootsForTest: syncServerWatchRoots,
  syncServerWatchRootsNowForTest: syncServerWatchRootsNow,
  clientServerWatchStateForTest() { return clientServerWatchState(); },
  serverWatchRootsStateForTest() {
    return {
      signature: serverWatchRootsState.signature,
      inFlight: serverWatchRootsState.inFlight,
      registrationPending: serverWatchRootsState.registrationPending,
      registered: serverWatchRootsState.registered,
      syncedAt: serverWatchRootsState.syncedAt,
      baselinePending: serverWatchRootsState.watchDiffPromise !== null,
      timer: serverWatchRootsState.timer,
      timerDelay: serverWatchRootsState.timerDelay,
      pendingOptions: {...serverWatchRootsState.pendingOptions},
    };
  },
  setServerWatchRootsSyncedAtForTest(value) { serverWatchRootsState.syncedAt = Number(value) || 0; },
  fileExplorerPaneTabHtml,
  makeButtonForTest: makeButton,
  bindOnceForTest: bindOnce,
  bindScopedOnceForTest: bindScopedOnce,
  normalizeAppOwnedControlsForTest: normalizeAppOwnedControls,
  reconcilePanelBodyForTest: reconcilePanelBody,
  createHoverPopoverForTest: createHoverPopover,
  bindPanelPopoverForTest: bindPanelPopover,
  debugRuntimeFacadeForTest: debugRuntimeFacade,
  terminalRuntimeFacadeForTest: terminalRuntimeFacade,
  setStatsCurrentNamespaceForTest(value) { globalThis.YOLOmuxStatsCurrent = value; },
  bindPaneFrameControlsForTest: bindPaneFrameControls,
  bindSearchHistoryPanelForTest: bindSearchHistoryPanel,
  fetchDirectoryForTest: fetchDirectory,
  fileExplorerEntriesByWatchedDirectoryForTest: fileExplorerEntriesByWatchedDirectory,
  refreshFileExplorerFromPushForTest: refreshFileExplorerFromPush,
  refreshWatchedFilesystemForTest: refreshWatchedFilesystem,
  fileExplorerTreePaneIsVisibleForTest: fileExplorerTreePaneIsVisible,
  filesystemWatchTokenForTest() { return fileExplorerFilesystemWatchToken; },
  filesystemPushTokenForTest() { return fileExplorerFilesystemPushToken; },
  setFilesystemWatchTokenForTest(value) { fileExplorerFilesystemWatchToken = String(value || ''); },
  setFilesystemLastFullAtForTest(value) { fileExplorerFilesystemLastFullAt = Number(value) || 0; },
  currentFileExplorerListErrorForTest: currentFileExplorerListError,
  setFileExplorerPushRefreshDepthForTest(value) { fileExplorerPushRefreshDepth = Math.max(0, Number(value) || 0); },
  setFileExplorerLastListErrorForTest(path, error = 'failed') { setFileExplorerListError(path, error, 500); },
  fetchFilePathInfoForTest: fetchFilePathInfo,
  deleteFileTreePathForTest: deleteFileTreePath,
  flushFileExplorerFsBatchForTest: flushFileExplorerFsBatch,
  firstEmptyPane,
  filePopoverRows,
  fuzzySearchScore,
  fuzzyHighlightHtml,
  sessionScopedIdForTest: sessionScopedId,
  fuzzySubsequenceMatch,
  fuzzySubsequenceScore,
  lineDiffRows,
  fileConflictCompareHtml,
  childPathParts,
  writeOwnedElementScrollForTest: writeOwnedElementScroll,
  restoreElementScrollPositionForTest: restoreElementScrollPosition,
  restorePaneElementScrollStateForTest: restorePaneElementScrollState,
  applyPreviewZoomSurfaceForTest: applyPreviewZoomSurface,
  hydratePreviewZoomSurfaceForTest: hydratePreviewZoomSurface,
  renderFileEditorPreviewSurfaceForTest: renderFileEditorPreviewSurface,
  renderFileEditorImagePaneForTest: renderFileEditorImagePane,
  beginFilePreviewPopoutScrollInputForTest: beginFilePreviewPopoutScrollInput,
  endFilePreviewPopoutScrollInputForTest: endFilePreviewPopoutScrollInput,
  scheduleFilePreviewPopoutApplyWhenIdleForTest: scheduleFilePreviewPopoutApplyWhenIdle,
  installPreviewScrollOwnershipCaptureForTest: installPreviewScrollOwnershipCapture,
  previewScrollUserOwnershipGenerationForTest: previewScrollUserOwnershipGeneration,
  expirePreviewScrollOwnershipForTest() {
    previewScrollTraceState.active = null;
    previewScrollTraceState.last = null;
  },
  renderMermaidSourceIntoForTest: renderMermaidSourceInto,
  renderRawImagePreviewIntoForTest: renderRawImagePreviewInto,
  schedulePreviewDeferredWorkAfterUserScrollForTest: schedulePreviewDeferredWorkAfterUserScroll,
  setMermaidApiForTest(value) {
    window.mermaid = value;
    mermaidApiPromise = null;
  },
  recordTerminalMobileInputTraceForTest: recordTerminalMobileInputTrace,
  debugModeEnabledForTest() { return debugModeEnabled; },
  debugModeExplicitUrlEnabledForTest() { return debugModeExplicitUrlEnabled; },
  debugPaneItemId,
  yocostItemId,
  clearJsDebugEventsForTest: clearJsDebugEvents,
  jsDebugEventsForTest() { return jsDebugEvents.map(event => ({...event})); },
  jsDebugFailureEventsForTest: jsDebugFailureEvents,
  jsDebugCurrentObservationReceiptBarrierForTest: jsDebugCurrentObservationReceiptBarrier,
  jsDebugCurrentObservationStateForTest() {
    return {
      queue: jsDebugCurrentObservationState.queue.length,
      receipts: jsDebugCurrentObservationState.receipts.size,
      timerPending: jsDebugCurrentObservationState.timer !== null,
      livenessTimerPending: jsDebugCurrentObservationState.livenessTimer !== null,
    };
  },
  debugEventCountsForTest: debugEventCounts,
  debugMobileCaptureSnapshotForTest: debugMobileCaptureSnapshot,
  debugMobileCaptureTextForClipboardForTest: debugMobileCaptureTextForClipboard,
  debugGraphExactResolutionChoicesForTest: debugGraphExactResolutionChoices,
  jsDebugCurrentCoverageIntervalsForTest: jsDebugCurrentCoverageIntervals,
  clearJsDebugGraphDataForTest: clearJsDebugGraphData,
  debugGraphApplyServerRecordForTest: debugGraphApplyServerRecord,
  compactJsDebugGraphBucketsForTest: compactJsDebugGraphBuckets,
  jsDebugGraphBucketDurationsForTest() { return [...jsDebugGraphBuckets.values()].map(bucket => Number(bucket.durationMs) / 1000); },
  terminalRemovalLatencySummaryForTest: terminalRemovalLatencySummary,
  jsDebugTextForClipboardForTest: jsDebugTextForClipboard,
  recordSseDebugEventForTest: recordSseDebugEvent,
  recordClientPerfCounterForTest: recordClientPerfCounter,
  clientPerfSummaryForTest: clientPerfSummary,
  clearClientPerfCountersForTest: clearClientPerfCounters,
  recordJsDebugEventForTest: recordJsDebugEvent,
  jsDebugCurrentObservationFromEventForTest: jsDebugCurrentObservationFromEvent,
  jsDebugObservationBatchForEntriesForTest: jsDebugObservationBatchForEntries,
  reloadJourneyIdForTest() { return reloadClientJourneyId; },
  pageLoadProfileEventForTest: pageLoadProfileEvent,
  notePageLoadApiStartedForTest: notePageLoadApiStarted,
  notePageLoadApiCompletedForTest: notePageLoadApiCompleted,
  setPageLoadProfileStateForTest(values) { Object.assign(pageLoadProfileState, values); },
  newFinderUsableJourneyForTest: newFinderUsableJourney,
  scheduleFinderUsableObservationForTest: scheduleFinderUsableObservation,
  clientPerfInteractionEventForTest: clientPerfInteractionEvent,
  registerApiOperationReceiptForTest: registerApiOperationReceipt,
  applyApiOperationTerminalForTest: applyApiOperationTerminal,
  operationTerminalAckDelayMsForTest() { return operationTerminalAckDelayMs; },
  operationTerminalAckStateForTest() {
    return {
      queued: operationTerminalAckState.pending.size,
      timerPending: operationTerminalAckState.timer !== null,
      requestPending: operationTerminalAckState.request !== null,
    };
  },
  waitForApiOperationResultForTest: waitForApiOperationResult,
  apiFetchJsonForTest: apiFetchJson,
  fetchFilesystemOperationPayloadForTest: fetchFilesystemOperationPayload,
  apiOperationStateForTest() {
    return {
      records: apiOperationState.records.size,
      pending: apiOperationState.pending.size,
      terminal: apiOperationState.terminal.size,
      waiters: [...apiOperationState.waiters.values()].reduce((total, values) => total + values.size, 0),
      handlerInvocations: [...apiOperationState.records.values()].reduce((total, record) => total + record.handlerInvocations, 0),
    };
  },
  fixtureLifecycleOperationStateForTest() {
    return window.__yolomuxFixtureLifecycle.operationState();
  },
  apiOperationTerminalForTest(operationId) {
    return apiOperationState.terminal.get(String(operationId || '')) || null;
  },
  clientCanUseUnscopedHostRequestsForTest: clientCanUseUnscopedHostRequests,
  refreshTmuxStatusModeForTest: refreshTmuxStatusMode,
  jsDebugResourcePhaseTimingsForTest: jsDebugResourcePhaseTimings,
  applyApiRequestIdHeaderForTest: applyApiRequestIdHeader,
  inactiveTabItems,
  infoItemId,
  infoRelationshipRecords,
  infoPanelRenderSignatureForTest: infoPanelRenderSignature,
  infoGroupTree,
  infoDimensionCountTextForTest: infoDimensionCountText,
  infoGroupDimensions,
  infoGroupDimensionsForLevel,
  infoGroupingControlsHtmlForTest: infoGroupingControlsHtml,
  infoRecordHtmlForTest: infoRecordHtml,
  infoTreeHtmlForTest: infoTreeHtml,
  infoTreeGroupCollapseKeyForTest: infoTreeGroupCollapseKey,
  setInfoTreeGroupCollapsedForTest: setInfoTreeGroupCollapsed,
  infoCollapsedGroupKeysForTest() { return [...infoCollapsedGroupKeys]; },
  infoFilteredRecordsForTest: infoFilteredRecords,
  infoRecordSearchFieldsForTest: infoRecordSearchFields,
  infoSortFields,
  infoGroupingPresetsForTest: infoGroupingPresets,
  currentInfoGroupingForTest: currentInfoGrouping,
  setInfoGroupingForTest: setInfoGrouping,
  setInfoGroupingPresetForTest: setInfoGroupingPreset,
  currentInfoSortForTest: currentInfoSort,
  setInfoSortForTest: setInfoSort,
  currentInfoSearchForTest: currentInfoSearch,
  setInfoSearchForTest: setInfoSearch,
  searchHistoryItemId,
  searchHistoryPanelHtmlForTest: searchHistoryPanelHtml,
  searchHistoryResultsHtmlForTest: searchHistoryResultsHtml,
  runHistoryRowsHtmlForTest: runHistoryRowsHtml,
  runSearchHistoryQueryForTest: runSearchHistoryQuery,
  refreshRunHistoryDataForTest: refreshRunHistoryData,
  setSearchHistoryStateForTest(query = '', searchPayload = {query: '', results: []}, historyPayload = {runs: []}) {
    searchHistoryState.guard.invalidate();
    runHistoryState.guard.invalidate();
    searchHistoryState.query = String(query || '');
    searchHistoryState.payload = searchPayload || {query: '', results: []};
    runHistoryState.payload = historyPayload || {runs: []};
    searchHistoryState.loading = false;
    runHistoryState.loading = false;
    searchHistoryState.error = '';
    runHistoryState.error = '';
  },
  searchHistoryStateForTest() {
    return {...searchHistoryState};
  },
  runHistoryStateForTest() {
    return {...runHistoryState};
  },
  infoPanelSubTabForTest() { return infoPanelSubTab; },
  setInfoPanelSubTabForTest(value) { infoPanelSubTab = normalizedInfoSubTab(value); },
  infoSessionFileLookbackHoursForTest() { return infoSessionFileLookbackHours; },
  setInfoSessionFileLookbackHoursForTest: setInfoSessionFileLookbackHours,
  tabberLookbackControlHtmlForTest: tabberLookbackControlHtml,
  tabberSessionFileLookbackHoursForTest() { return tabberSessionFileLookbackHours; },
  setTabberSessionFileLookbackHoursForTest: setTabberSessionFileLookbackHours,
  tabMetaVisibleForTest() { return tabMetaVisible; },
  setTabMetaVisibleForTest(value) {
    tabMetaVisible = value === true;
    renderTabMetaToggle();
  },
  toggleTabMetadataForTest: toggleTabMetadata,
  itemInLayout,
  slotForItem,
  itemLabel,
  itemParam,
  resolveLayoutItem,
  gitDiffItemFor,
  gitDiffItemPath,
  historicalFileEditorItemFor,
  historicalFileEditorIdentity,
  itemIsBackgroundPaneTab,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  largestPaneSlotForFileEditor,
  layoutWithFileExplorerDockedLeft,
  layoutWithSidePaneItems,
  layoutWithDefaultLeftSidePane,
  layoutSlotsForTest() { return cloneLayoutSlots(layoutSlots); },
  dynamicVirtualLayoutItemsForTest() { return Array.from(dynamicVirtualLayoutItems); },
  setGitDiffTabStateForTest(item, values = {}) { return ensureGitDiffTabState(item, values); },
  gitDiffTabStateForTest(item) {
    const state = gitDiffTabState.get(item);
    return state ? {...state} : null;
  },
  gitDiffCommitRowForTest: gitDiffCommitRow,
  gitDiffCommitMessageForTest: gitDiffCommitMessage,
  gitDiffCommitFileTreeForTest: gitDiffCommitFilesTree,
  gitDiffHistoricalFileItemForTest: gitDiffHistoricalFileItem,
  gitDiffHistoricalComparisonKindForTest: gitDiffHistoricalComparisonKind,
  openGitDiffHistoricalFileForTest: openGitDiffHistoricalFile,
  createGitDiffPanelForTest: createGitDiffPanel,
  renderGitDiffPanelForTest: renderGitDiffPanel,
  relocalizeGitDiffPanelForTest: relocalizeGitDiffPanel,
  refreshGitDiffHistoryForTest: refreshGitDiffHistory,
  loadOlderGitDiffHistoryForTest: loadOlderGitDiffHistory,
  loadGitDiffCommitDetailForTest: loadGitDiffCommitDetail,
  setGitDiffCommitExpandedForTest: setGitDiffCommitExpanded,
  createLifecycleScopeForTest: createLifecycleScope,
  createLatestResourceForTest: createLatestResource,
  mobileSinglePaneModeForTest: mobileSinglePaneMode,
  narrowSingleColumnModeForTest: narrowSingleColumnMode,
  fileExplorerUsesNormalTabMovementForTest: fileExplorerUsesNormalTabMovement,
  tabletUsesDesktopLayoutForTest: tabletUsesDesktopLayout,
  phoneLikeMobileViewportForTest: phoneLikeMobileViewport,
  narrowTouchSingleColumnViewportForTest: narrowTouchSingleColumnViewport,
  mobileRecentTmuxItemsForTest: mobileRecentTmuxItems,
  mobileSinglePaneLayoutSlotsForTest: mobileSinglePaneLayoutSlots,
  compactCurrentLayoutSlotsForTest: compactCurrentLayoutSlots,
  availableLayoutModesForTest: availableLayoutModes,
  layoutWithReplacedItem,
  layoutWithoutItem,
  layoutWithItems,
  tabSplitCapabilities,
  tabDirectionalActionCapabilities,
  moveLayoutItemDirectional,
  swapLayoutItemDirectional,
  splitLayoutItemDirectional,
  tabCanFillWorkspace,
  tabWorkspaceIsFilled,
  toggleTabWorkspaceFill,
  applyLayoutMode,
  setLayoutToSinglePane,
  setLayoutToSplitPanes,
  setLayoutToGridPanes,
  setLayoutToWallPanes,
  layoutModeValues,
  layoutTabsParamValue,
  layoutTabStatesFromParam,
  layoutTreeKey,
  leafNode,
  yoagentItemId,
  chatItemId,
  finderItemId,
  differItemId,
  tabberItemId,
  fileExplorerItemId,
  virtualTabItems,
  conversationClampSelectionToGraphemesForTest: conversationClampSelectionToGraphemes,
  conversationInsertAtSelectionForTest: conversationInsertAtSelection,
  chatNotificationSnippetForTest: chatNotificationSnippet,
  chatNotificationTimestampForTest: chatNotificationTimestamp,
  chatMessageNotificationEligibleForTest: chatMessageNotificationEligible,
  chatNotificationLinesForTest: chatNotificationLines,
  chatMessageTimestampForTest: chatMessageTimestamp,
  chatPreciseRelativeTimeFormatForTest: chatPreciseRelativeTimeFormat,
  setDateTimeHourCycleForTest(value) { dateTimeHourCycle = normalizeDateTimeHourCycle(value); },
  chatEmojiSearchTextForTest: chatEmojiSearchText,
  chatRecentEmojiForTest: chatRecentEmoji,
  rememberChatEmojiForTest: rememberChatEmoji,
  chatMessageHtmlForTest: chatMessageHtml,
  chatMediaUrlMatchesForTest: chatMediaUrlMatches,
  chatMediaKindForTest: chatMediaKind,
  chatMediaItemForTest: chatMediaItemFor,
  chatMediaUrlForItemForTest: chatMediaUrlForItem,
  chatMediaActionItemsForTest: chatMediaActionItems,
  chatAuthorToneAssignmentsForTest: chatAuthorToneAssignments,
  chatIntroductionHtmlForTest: chatIntroductionHtml,
  chatIntroductionGreetingKeyForTest: chatIntroductionGreetingKey,
  chatYoagentQueryForTest: chatYoagentQuery,
  chatMessageRequestsYoagentForTest: chatMessageRequestsYoagent,
  openChatSearchForTest: openChatSearch,
  replaceChatTypingForTest: replaceChatTyping,
  chatRequestOptionsForTest: chatRequestOptions,
  clearChatLifecycleForTest: clearChatLifecycle,
  chatLifecycleStateForTest() {
    return {
      active: chatState.lifecycleScope?.current() === true,
      requestController: chatState.lifecycleScope?.value('request-controller') || null,
      typingTimer: chatState.lifecycleScope?.value('typing-heartbeat-timer') || null,
      typingExpiryTimer: chatState.lifecycleScope?.value('typing-expiry-timer') || null,
    };
  },
  conversationAutosizeTextareaForTest: conversationAutosizeTextarea,
  chatPanelIsEngagedForTest: chatPanelIsEngaged,
  chatStatusTonesForTest: chatStatusTones,
  chatTypingTextForTest: chatTypingText,
  chatStatusMarkerHtmlForTest: chatStatusMarkerHtml,
  chatMergeMessagesForTest: chatMergeMessages,
  loadChatBootstrapForTest: loadChatBootstrap,
  loadChatDeltaForTest: loadChatDelta,
  loadOlderChatMessagesForTest: loadOlderChatMessages,
  chatRequestStateForTest() {
    return {
      loaded: chatState.loaded,
      loading: chatState.loadingRequest !== null,
      loadingRequest: chatState.loadingRequest ? {...chatState.loadingRequest} : null,
      requestGeneration: chatState.requestGeneration,
      olderGeneration: chatState.olderGeneration,
      contextGeneration: chatState.contextGeneration,
      messageIds: [...chatState.messages.keys()],
    };
  },
  setChatStateForTest(value = {}) {
    if (value.loaded !== undefined) chatState.loaded = value.loaded === true;
    if (value.loading === false) chatState.loadingRequest = null;
    if (value.requestGeneration !== undefined) chatState.requestGeneration = Number(value.requestGeneration) || 0;
    if (value.olderGeneration !== undefined) chatState.olderGeneration = Number(value.olderGeneration) || 0;
    if (value.clientIp !== undefined) chatState.clientIp = String(value.clientIp || '');
    if (value.contextGeneration !== undefined) chatState.contextGeneration = Number(value.contextGeneration) || 0;
    if (value.olderCursor !== undefined) chatState.olderCursor = String(value.olderCursor || '');
    if (value.hasMore !== undefined) chatState.hasMore = value.hasMore === true;
    if (Array.isArray(value.messages)) chatState.messages = new Map(value.messages.map(message => [Number(message.id), message]));
    if (Array.isArray(value.typing)) chatState.typing = value.typing;
    if (Array.isArray(value.unread)) chatState.unread = new Map(value.unread.map(message => [Number(message.id), message]));
    if (value.acknowledgedTone !== undefined) chatState.acknowledgedTone = String(value.acknowledgedTone || '');
    if (value.acknowledgementStartedAt !== undefined) chatState.acknowledgementStartedAt = Number(value.acknowledgementStartedAt) || 0;
  },
  prefsItemId,
  menuTabCommand,
  activateTerminalDetailTabForTest: activateTab,
  activatePaneTab,
  paneTabTraversalPositionsForTest: paneTabTraversalPositions,
  adjacentPaneTabPosition,
  selectAdjacentPaneTab,
  currentSessionActionTarget,
  currentTmuxMenuTargetForTest: currentTmuxMenuTarget,
  explicitPaneFocusItemForTest: explicitPaneFocusItem,
  setExplicitPaneFocusItemForTest: setExplicitPaneFocusItem,
  setFocusedPanelItem,
  setFocusedTerminal,
  clearFocusedTerminal,
  handleFocusedTerminalCopyShortcutForTest: handleFocusedTerminalCopyShortcut,
  visualActivePaneItemForTest: visualActivePaneItem,
  codeMirrorWrapMarkerRowsForBlock,
  lastActivePaneItemForTest() { return lastActivePaneItem; },
  focusedPanelItemForTest() { return focusedPanelItem; },
  setAutoFocusEnabledForTest(value) { autoFocusEnabled = Boolean(value); },
  autoFocusEnabledForTest() { return autoFocusEnabled; },
  browserHasCursorHoverForTest: browserHasCursorHover,
  autoFocusCanFollowCursorForTest: autoFocusCanFollowCursor,
  notificationTargetIsFocusedForTest: notificationTargetIsFocused,
  selectSession,
  selectPanelOnHover,
  claimVisibleTerminalResizeAuthorityForTest: claimVisibleTerminalResizeAuthority,
  focusTerminalWhenAutoFocus,
  focusPanel,
  focusTerminalFromUserAction,
  noteTerminalExplicitInputForTest: noteTerminalExplicitInput,
  handleTerminalDataForTest: handleTerminalData,
  terminalMobileAccessoryHtmlForTest: terminalMobileAccessoryHtml,
  ensureFileUploadChooserForTest: ensureFileUploadChooser,
  openFileUploadChooserForSessionForTest: openFileUploadChooserForSession,
  openFileUploadChooserForEditorForTest: openFileUploadChooserForEditor,
  terminalMobileAccessoryDataForTest: terminalMobileAccessoryData,
  terminalDataWithMobileAccessoryModifiersForTest: terminalDataWithMobileAccessoryModifiers,
  terminalMobileAccessoryPalettePlacementForTest: terminalMobileAccessoryPalettePlacement,
  terminalMobileAccessoryLauncherDragPositionForTest: terminalMobileAccessoryLauncherDragPosition,
  terminalMobileAccessoryStateForTest(session) {
    const state = terminalMobileAccessoryState(session);
    return state ? {...state} : null;
  },
  toggleTerminalMobileAccessoryStateForTest: toggleTerminalMobileAccessoryState,
  dismissTerminalMobileAccessoryForTest: dismissTerminalMobileAccessory,
  beginTerminalMobileAccessoryLauncherPressForTest: beginTerminalMobileAccessoryLauncherPress,
  moveTerminalMobileAccessoryLauncherPressForTest: moveTerminalMobileAccessoryLauncherPress,
  endTerminalMobileAccessoryLauncherPressForTest: endTerminalMobileAccessoryLauncherPress,
  consumeTerminalMobileAccessoryLauncherClickForTest: consumeTerminalMobileAccessoryLauncherClick,
  sendTerminalMobileAccessoryInputForTest: sendTerminalMobileAccessoryInput,
  terminalMobileAccessoryRepeatsForTest: terminalMobileAccessoryRepeats,
  toggleFileExplorerShortcut,
  clearFileExplorerShortcutRestoreSlotsForTest() { fileExplorerShortcutRestoreSlots = null; },
  focusedTerminalForTest() { return focusedTerminal; },
  globalShortcutTargetAllowsAppAction,
  globalShortcutTargetAllowsFinderShortcut,
  globalShortcutShouldToggleFinderForTest: globalShortcutShouldToggleFinder,
  installTerminalCopyShortcutForTest: installTerminalCopyShortcut,
  handleTerminalTmuxHistoryNavigationKeydownForTest: handleTerminalTmuxHistoryNavigationKeydown,
  osc52ClipboardText,
  installTerminalOsc52BridgeForTest: installTerminalOsc52Bridge,
  rememberTerminalAppClipboardTextForTest: rememberTerminalAppClipboardText,
  terminalContextMenuSelectionForTest: terminalContextMenuSelection,
  terminalVisibleSelectionStateForTest: terminalVisibleSelectionState,
  clearTerminalVisibleSelectionForTest: clearTerminalVisibleSelection,
  apiFetchJsonQuietForTest: apiFetchJsonQuiet,
  apiFetchJsonForTest: apiFetchJson,
  locationAssignmentsForTest() { return [...globalThis.__locationAssignments]; },
  isApiPendingResponseForTest: isApiPendingResponse,
  setFetchForTest(fn) { globalThis.fetch = fn; },
  setClipboardForTest(clipboard, ClipboardItemType = class {}) {
    navigator.clipboard = clipboard;
    globalThis.ClipboardItem = ClipboardItemType;
  },
  setSaveBlobDownloadForTest(fn) { saveBlobDownload = fn; },
  setConfirmForTest(fn) { window.confirm = fn; },
  setShowToastForTest(fn) { showToast = fn; },
  notificationEventDefinitionsForTest() {
    return Object.fromEntries(Object.entries(notificationEventDefinitions).map(([key, value]) => [key, {...value}]));
  },
  emitNotificationForTest: emitNotification,
  copyImageFileToClipboardForTest: copyImageFileToClipboard,
  triggerFileDownloadForTest: triggerFileDownload,
  triggerFolderZipDownloadForTest: triggerFolderZipDownload,
  applyAndSaveGlobalThemeForTest: applyAndSaveGlobalTheme,
  reloadCountForTest() { return globalThis.__reloadCount || 0; },
  yoagentJobNotificationTitleForTest: yoagentJobNotificationTitle,
  yoagentJobNotificationBodyForTest: yoagentJobNotificationBody,
  applyUpdateAvailableForTest: applyUpdateAvailable,
  triggerSelfUpdateForTest: triggerSelfUpdate,
  maybeHandleServerVersionChangeForTest: maybeHandleServerVersionChange,
  startSelfUpdateReloadPollingForTest: startSelfUpdateReloadPolling,
  pollSelfUpdateReloadForTest: pollSelfUpdateReload,
  reloadIsSafeForTest: reloadIsSafe,
  selfUpdateReloadStateForTest() {
    return {
      pending: selfUpdateReloadState.pending,
      target: selfUpdateReloadState.target,
      attempts: selfUpdateReloadState.attempts,
      deferredToastShown: selfUpdateReloadState.deferredToastShown,
      serverVersionReloadHandled,
    };
  },
  clipboardTextForTest() { return globalThis.__clipboardText; },
  clearClipboardTextForTest() { globalThis.__clipboardText = ''; },
  setBrowserSelectionForTest(text, anchorNode = null, focusNode = anchorNode) {
    globalThis.__browserSelectionClearCount = 0;
    let value = String(text || '');
    const selection = {
      toString: () => value,
      anchorNode,
      focusNode,
      removeAllRanges() {
        value = '';
        globalThis.__browserSelectionClearCount += 1;
      },
    };
    globalThis.getSelection = () => selection;
    window.getSelection = globalThis.getSelection;
  },
  browserSelectionClearCountForTest() { return globalThis.__browserSelectionClearCount || 0; },
  clearBrowserSelectionForTest() {
    delete globalThis.getSelection;
    delete window.getSelection;
    globalThis.__browserSelectionClearCount = 0;
  },
  bindClipboardPasteForTest: bindClipboardPaste,
  dataTransferHasImagePayloadForTest: dataTransferHasImagePayload,
  dataTransferImageFilesForTest: dataTransferImageFiles,
  documentListenersForTest(type) { return [...(document.__listeners.get(type) || [])]; },
  setDocumentQuerySelectorForTest(fn) { document.querySelector = fn; },
  setDocumentQuerySelectorAllForTest(fn) { document.querySelectorAll = fn; },
  setDocumentVisibilityForTest(value) { Object.defineProperty(document, 'visibilityState', {value: String(value || 'visible'), configurable: true}); },
  setNotificationDeliveryForTest(value = {}) { notificationDelivery = {inApp: value.inApp === true, system: value.system === true}; },
  queueClientPushEventForTest: queueClientPushEvent,
  handleClientPushEventForTest: handleClientPushEvent,
  clientServerPushEventTypesForTest: () => [...clientServerPushEventTypes],
  backendHealthStateForTest: () => ({...backendHealthState, resources: backendHealthState.resources.map(item => ({...item}))}),
  backendHealthIndicatorModelForTest: backendHealthIndicatorModel,
  applyBackendHealthPayloadForTest: applyBackendHealthPayload,
  noteBackendHealthFailureForTest: noteBackendHealthFailure,
  noteBackendHealthSuccessForTest: noteBackendHealthSuccess,
  // The product resolves its host from the real topbar, which the node DOM stub does not build.
  // Tests pass their own topbar-right-tools host to the one renderer; there is no second one.
  syncBackendHealthIndicatorForTest: host => syncBackendHealthIndicator(host),
  openBackendHealthDetailsForTest: openBackendHealthDetails,
  debugSubTabForTest: () => debugRuntimeState.subTab,
  flushQueuedClientPushEventsForTest: flushQueuedClientPushEvents,
  scheduleReconnectResyncForTest: scheduleReconnectResync,
  installClientEventStreamForTest: installClientEventStream,
  disposeClientEventTransportForTest: disposeClientEventTransportLifecycle,
  commandPaletteItemScore,
  commandPaletteRankItems,
  commandPaletteCandidateItems,
  searchRankWeights,
  commandPaletteSearchQuery,
  commandPaletteCommandItems,
  commandPaletteItems,
  invokeCommandPaletteItemForTest(item, event = null) {
    commandPaletteState.items = item ? [item] : [];
    commandPaletteState.index = 0;
    return invokeCommandPaletteSelection(event);
  },
  dedupeFileSearchResults,
  setCommandPaletteStateForTest(mode, query) { commandPaletteMode = mode; commandPaletteState.query = query || ''; },
  commandPaletteStateForTest() { return {...commandPaletteState, items: commandPaletteState.items.slice()}; },
  commandPaletteMatches,
  openFileQuickOpen,
  testElementForId(id) { return document.getElementById(id); },
  registerTerminalForTest(session, term, socket = {readyState: WebSocket.OPEN}) {
    const item = {term, socket, container: document.getElementById('terminal-pane-' + session), sessionLifecycleToken: tmuxSessionLifecycleToken(session)};
    terminals.set(session, item);
    return item;
  },
  terminalItemForTest(session) { return terminals.get(session) || null; },
  closeTerminalItemForTest: closeTerminalItem,
  estimateTerminalSizeForTest: estimateTerminalSize,
  scheduleRemoteResizeForTest: scheduleRemoteResize,
  scheduleTerminalBlankScreenRefreshForTest: scheduleTerminalBlankScreenRefresh,
  queueTmuxScrollForTest: queueTmuxScroll,
  connectTerminalSocketForTest: connectTerminalSocket,
  startSummaryStreamForTest: startSummaryStream,
  stopSummaryStreamForTest: stopSummaryStream,
  summaryStreamForTest(session) { return summaryLifecycleScopes.get(session)?.value('stream') || null; },
  startTranscriptStreamForTest: startTranscriptStream,
  stopTranscriptStreamForTest: stopTranscriptStream,
  transcriptStreamForTest(session) { return transcriptLifecycleScopes.get(session)?.value('stream') || null; },
  terminalAttentionQuestionTextsForTest: terminalAttentionQuestionTexts,
  terminalAttentionQuestionRowForTest: terminalAttentionQuestionRow,
  syncTerminalAttentionHighlightForTest: syncTerminalAttentionHighlight,
  clearTerminalAttentionHighlightForTest: clearTerminalAttentionHighlight,
  transcriptInfoForTest(session) { return transcriptMetadataState.payload.sessions?.[session]; },
  applyTranscriptsPayloadForTest: applyTranscriptsPayload,
  applyTmuxSignalsPayloadForTest: applyTmuxSignalsPayload,
  syncAgentWindowActivityAnimationDelaysForTest: syncAgentWindowActivityAnimationDelays,
  restartAgentWindowActivityPulseAnimationsForTest: restartAgentWindowActivityPulseAnimations,
  scheduleTmuxWindowReadbackForTest: scheduleTmuxWindowReadback,
  setTmuxWindowActiveIndexOverrideForTest: setTmuxWindowActiveIndexOverride,
  setTmuxWindowActiveIndexPendingForTest: setTmuxWindowActiveIndexPending,
  tmuxWindowActiveIndexOverrideForTest: tmuxWindowActiveIndexOverride,
  tmuxWindowNavigationRecordForTest(session) {
    const record = tmuxWindowNavigationRecord(session);
    return record ? {
      activeIndexOverride: record.activeIndexOverride,
      sequence: record.sequence,
      directTargetGuard: record.directTargetGuard ? {...record.directTargetGuard} : null,
      switchLoading: record.switchLoading ? {...record.switchLoading} : null,
    } : null;
  },
  processTerminalSocketFrameForTest(session, data) {
    const item = terminals.get(session);
    return processTerminalSocketFrame(session, item, data);
  },
  stallTmuxWindowSwitchLoadingForTest: stallTmuxWindowSwitchLoading,
  retryTmuxWindowSwitchLoadingForTest: retryTmuxWindowSwitchLoading,
  cancelTmuxWindowSwitchLoadingForTest: cancelTmuxWindowSwitchLoading,
  expireTmuxWindowDirectTargetGuardForTest(session) {
    const record = tmuxWindowNavigationRecord(session);
    if (record?.directTargetGuard) record.directTargetGuard.guardUntilMs = 0;
    return tmuxWindowDirectTargetGuard(session);
  },
  activeTmuxSignalWindowForSessionForTest: activeTmuxSignalWindowForSession,
  seedSessionLifecycleStateForTest(session) {
    const record = sessionStatusRecord(session, true);
    record.state = {key: 'needs-input', reason: 'test attention', signature: 'needs-input:test attention'};
    record.notificationLastSent.set('state:needs-input:test attention', 123);
    record.workingAgentNotificationTones.set('0::codex', 'working');
    record.workingAgentTransitionNotificationPending.set('0::codex', {session, tone: 'attention', timer: 79});
    record.metadataBadgePulseUntil.set('pr', Date.now() + 5000);
    autoApproveStates.set(session, {enabled: true});
    paneViewState.set(session, {scrollTop: 9});
    pasteCounters.set(session, 4);
    sessionRepoDisplayRoot.set(session, '/repo/test');
    tabLastActivatedAt.set(session, 100);
    tmuxStatusModes.set(session, 'top');
    terminalAppClipboardText.set(session, {text: 'copied text', timestamp: Date.now()});
    tmuxWindowNavigationRecords.set(session, {activeIndexOverride: '1', sequence: 9, directTargetGuard: null});
    terminalTmuxInputStates.set(session, {prefixPending: true, repeatUntilMs: Date.now() + 900});
    altScreenWheelRemainder.set(session, 0.5);
    attentionAcknowledgementRecords.set(
      attentionAcknowledgementKey(['prompt', session, 'seed']),
      {recordedAt: null, timer: 84, pending: false},
    );
    agentWindowActivityRecords.set(session + ':0::codex', {
      activity: {state: 'working'},
      stoppedRefresh: {timer: 81, untilMs: Date.now() + 1000},
      transitionPulseRefresh: {timer: 82, untilMs: Date.now() + 1000},
      acknowledgedStoppedAt: 0,
      acknowledgementVisual: {timer: 83, untilMs: Date.now() + 1000},
    });
  },
  seedSessionToastForTest(session, kind = 'attention', timer = 71) {
    const id = ++attentionAlertSequence;
    const node = document.createElement('div');
    node.className = 'toast';
    node.dataset.alertId = String(id);
    node.dataset.toastKind = kind;
    node.dataset.toastSession = session;
    attentionAlerts.appendChild(node);
    toastRecords.set(id, {node, timer});
    return id;
  },
  toastStateForTest(id) {
    const record = toastRecords.get(id);
    return record ? {session: record.node.dataset.toastSession || '', timer: Boolean(record.timer)} : null;
  },
  seedSessionTeardownStateForTest(session) {
    const closed = {transcript: 0, summary: 0};
    const transcriptScope = createLifecycleScope({onDispose: () => transcriptLifecycleScopes.delete(session)});
    transcriptScope.ownStream('stream', {close() { closed.transcript += 1; }});
    transcriptLifecycleScopes.set(session, transcriptScope);
    const summaryScope = createLifecycleScope({onDispose: () => summaryLifecycleScopes.delete(session)});
    summaryScope.ownStream('stream', {close() { closed.summary += 1; }});
    summaryLifecycleScopes.set(session, summaryScope);
    this.seedSessionLifecycleStateForTest(session);
    uploadResultRecords.set(session, {entries: [{text: 'uploaded'}], cleanupTimer: 123});
    return closed;
  },
  showUploadResultForTest(session, payload, inserted) {
    return emitNotification('uploadResult', {session, uploadPayload: payload, inserted}).inApp;
  },
  renderUploadResultForTest: renderUploadResult,
  keepUploadResultForTest: keepUploadResult,
  hideUploadResultForTest: hideUploadResult,
  uploadResultRecordForTest(session) {
    const record = uploadResultRecord(session);
    return record ? {entries: record.entries, cleanupTimer: record.cleanupTimer} : null;
  },
  seedUploadResultRecordForTest(session, entries, cleanupTimer = null) {
    uploadResultRecords.set(session, {entries: Array.from(entries || []), cleanupTimer});
  },
  stopSessionUiForTest: stopSessionUi,
  sessionLifecycleStateForTest(session) {
    const record = sessionStatusRecord(session);
    return {
      statusRecord: Boolean(record),
      trackedState: record?.state?.key || '',
      notificationCount: record?.notificationLastSent.size || 0,
      toneCount: record?.workingAgentNotificationTones.size || 0,
      pendingTransitionCount: record?.workingAgentTransitionNotificationPending.size || 0,
      pendingTransitionSessions: Array.from(record?.workingAgentTransitionNotificationPending.values() || []).map(pending => pending.session).sort().join(','),
      pulseCount: record?.metadataBadgePulseUntil.size || 0,
      autoApprove: autoApproveStates.has(session),
      paneScrollTop: paneViewState.get(session)?.scrollTop ?? null,
      pasteCount: pasteCounters.get(session) ?? null,
      repoDisplayRoot: sessionRepoDisplayRoot.get(session) || '',
      lastActivatedAt: tabLastActivatedAt.get(session) ?? null,
      tmuxStatusMode: tmuxStatusModes.get(session) || '',
      clipboardText: terminalAppClipboardText.get(session)?.text || '',
      navigation: tmuxWindowNavigationRecords.has(session),
      terminalInput: terminalTmuxInputStates.has(session),
      wheelRemainder: altScreenWheelRemainder.has(session),
      agentActivityCount: Array.from(agentWindowActivityRecords.keys()).filter(key => key.startsWith(session + ':')).length,
      acknowledgementCount: Array.from(attentionAcknowledgementRecords.keys()).filter(key => attentionAcknowledgementKeySession(key) === session).length,
    };
  },
  sessionTeardownStateForTest(session) {
    const uploadRecord = uploadResultRecord(session);
    return {
      terminal: terminals.has(session),
      transcript: transcriptLifecycleScopes.has(session),
      summary: summaryLifecycleScopes.has(session),
      uploads: Boolean(uploadRecord),
      uploadTimer: uploadRecord?.cleanupTimer !== null && uploadRecord?.cleanupTimer !== undefined,
      ...this.sessionLifecycleStateForTest(session),
    };
  },
  tmuxSessionActionCommands,
  tmuxSessionViewCommands,
  tmuxSessionNameError,
  replaceTmuxSessionInClient,
  renameTmuxSessionForTest: renameTmuxSession,
  applyLayoutSlotsForTest: applyLayoutSlots,
  applyLayoutUrlEditorModeEntryForTest: applyLayoutUrlEditorModeEntry,
  activateNavItemForTest: activateNavItem,
  layoutMutationSnapshotForTest() { return runtimeState.layoutMutationSnapshot(); },
  beginLayoutMutationCompletionForTest: beginLayoutMutationCompletion,
  completeLayoutMutationGenerationForTest: completeLayoutMutationGeneration,
  waitForLayoutMutationCompletionForTest: waitForLayoutMutationCompletion,
  promiseWithDeadlineForTest: promiseWithDeadline,
  runTmuxSessionMutationForTest: runTmuxSessionMutation,
  createNextSessionForTest: createNextSession,
  confirmSessionGoneOrReconnectForTest: confirmSessionGoneOrReconnect,
  markPendingTmuxSessionForTest: markPendingTmuxSession,
  pendingTmuxSessionNamesForTest: pendingTmuxSessionNames,
  beginTmuxSessionLifecycleMutationForTest: beginTmuxSessionLifecycleMutation,
  commitTmuxSessionLifecycleMutationForTest: commitTmuxSessionLifecycleMutation,
  rollbackTmuxSessionLifecycleMutationForTest: rollbackTmuxSessionLifecycleMutation,
  waitForTmuxSessionLifecycleMutationLeasesForTest: waitForTmuxSessionLifecycleMutationLeases,
  tmuxSessionLifecycleAcquireRequestForTest: tmuxSessionLifecycleAcquireRequest,
  tmuxSessionLifecycleTokenForTest: tmuxSessionLifecycleToken,
  tmuxSessionLifecycleTokenIsCurrentForTest: tmuxSessionLifecycleTokenIsCurrent,
  tmuxSessionLifecycleRecordForTest(session) {
    const record = tmuxSessionLifecycleRecord(session, {create: false});
    return record ? {
      name: record.name,
      generation: record.generation,
      topologyEpoch: record.topologyEpoch,
      phase: record.phase,
      leases: record.requestLeases.size,
      sources: record.sources.size,
      timers: record.timers.size,
      consumerDetachments: record.consumerDetachments,
    } : null;
  },
  normalizedSessionOrder,
  fileDropCategory,
  dropSuggestionIndexFromKeyEvent,
  rememberDropActionForTest: rememberDropAction,
  dropSuggestionsFor,
  dropActionDisplayLabel,
  composeDropSuggestion,
  insertedDropActionText,
  runDropActionForTest: runDropAction,
  customDropActionFromLine,
  commandPaletteDropActionItems,
  normalizeLayoutSlots,
  compactLayoutSlots,
  layoutSlotsSignature,
  dockviewJsonFromLayoutSlots,
  layoutSlotsFromDockviewJson,
  queueDockviewLayoutAdoptionForTest(json) {
    const fromJsonCalls = [];
    dockviewLayoutState.host = null;
    dockviewLayoutState.api = {
      toJSON() { return json; },
      fromJSON(value) { fromJsonCalls.push(value); },
      clear() {},
    };
    dockviewLayoutState.fromJsonCallsForTest = fromJsonCalls;
    dockviewLayoutState.syncQueued = true;
    dockviewLayoutState.syncQueuedLayoutSignature = layoutSlotsExactSignature(layoutSlots);
  },
  dockviewFromJsonCallsForTest() { return [...(dockviewLayoutState.fromJsonCallsForTest || [])]; },
  adoptQueuedDockviewLayoutForTest: adoptDockviewLayout,
  adoptDockviewLayoutForTest(json, hostRect = null) {
    if (hostRect) {
      const host = document.createElement('div');
      host.id = 'dockviewHost';
      host.clientWidth = Number(hostRect.width) || 0;
      host.clientHeight = Number(hostRect.height) || 0;
      host.rect = {
        width: Number(hostRect.width) || 0,
        height: Number(hostRect.height) || 0,
        left: 0,
        top: 0,
        right: Number(hostRect.width) || 0,
        bottom: Number(hostRect.height) || 0,
      };
      dockviewLayoutState.host = host;
    } else {
      dockviewLayoutState.host = null;
    }
    dockviewLayoutState.api = {
      toJSON() { return json; },
      fromJSON() {},
      clear() {},
    };
    adoptDockviewLayout();
    return layoutSlots;
  },
  dockviewLayoutContentSignature,
  dockviewHostCanAdoptLayout,
  isPinnableTab,
  tabIsPinned,
  orderPaneTabs,
  pinnedTabIconHtml,
  paneTabDismissControlHtml,
  setTabPinned,
  toggleTabPinned,
  toggleActiveTabPinned,
  pinnedTabsForTest() { return [...pinnedTabItems]; },
  setPinnedTabsForTest(items) {
    pinnedTabItems = normalizePinnedTabItems(items || []);
    writeStoredPinnedTabs();
  },
  paneIsPlaceholder,
  panelControlsHtml,
  paneFrameControlsHtml,
  dockviewHeaderActionsHtml,
  syncPaneRoleDom,
  setPanelDetailsCollapsedForTest: setPanelDetailsCollapsed,
  platformWindowControlClass,
  positionPaneTabPopover,
  pathIsInsideDirectory,
  scrollFileTreeRowIntoView,
  bindPaneTabStrip,
  paneTabShouldPreserve,
  restorePaneTabPopover,
  syncPreservedPaneTab,
  paneTabPopoverItemToRestore,
  clearPaneTabDropPreview,
  showSessionContextMenu,
  showTabContextMenu,
  openedWindowsForTest() { return globalThis.__openedWindows.map(record => ({url: record.url, name: record.name, features: record.features, focused: record.focused === true})); },
  openedWindowClosedForTest(index = 0) { return globalThis.__openedWindows[index]?.window?.closed === true; },
  bodyChildren() { return document.body.children; },
  defaultLayoutSlots,
  layoutShapeSignature,
  dedentSelectionText,
  dropIntentForEvent,
  dropIntentAllowsSession,
  fileDropIntentAllowsPayload,
  pathDropIntentAllowsPayload,
  paneSwapAllowed,
  paneSwapIntentForEvent,
  paneSwapIntentAllowed,
  swapPaneSlots,
  directoryEntriesSignature,
  fileExplorerDirectoryRecordForTest(path) {
    const record = fileExplorerDirectoryRecord(path);
    return record ? {
      signature: record.signature,
      knownEntryNames: Array.from(record.knownEntryNames || []),
    } : null;
  },
  recordDirectorySignatureForTest: recordDirectorySignature,
  markNewDirectoryEntriesForTest: markNewDirectoryEntries,
  fileExplorerEntryIsNewForTest: fileExplorerEntryIsNew,
  invalidateFileExplorerFsCachesForTest: invalidateFileExplorerFsCaches,
  invalidateFileExplorerRootsForTest: invalidateFileExplorerRoots,
  fileExplorerFsResourceRecordsForTest() {
    return [...fileExplorerFsResourceRecords.entries()].map(([key, record]) => ({
      key,
      generation: record.generation,
      hasValue: record.value !== undefined,
      value: record.value,
      storedAt: record.storedAt,
      requestActive: Boolean(record.request),
      failureCount: record.failureCount,
      failureStatus: record.failureStatus,
      retryAt: record.retryAt,
    }));
  },
  editorModeLabel,
  editorWrapValue,
  editorViewModeFor,
  editorPreviewModeAvailable,
  setFileEditorViewMode,
  fileEditorDiffExpandUnchangedForItemForTest: fileEditorDiffExpandUnchangedForItem,
  setFileEditorDiffExpandUnchangedForItemForTest: setFileEditorDiffExpandUnchangedForItem,
  fileEditorBlameControlsVisible,
  updateFileEditorBlameButton,
  updateFileEditorDiffButton,
  updateFileEditorDiffExpandButton,
  codeMirrorConfigSignature,
  openFileDiffAvailable,
  localizedDateTimeFormat,
  sessionFileTimeText,
  sessionFileRelativeTimeText,
  sessionFileDisplayTimeText,
	  fileExplorerTreeDateModeLabel,
	  fileExplorerTreeDateModeButtonLabel,
	  fileExplorerTreeDateModeTitle,
	  fileExplorerViewSettingsForTest() { return JSON.parse(JSON.stringify(fileExplorerViewSettings)); },
	  fileExplorerTreeDateModeForViewForTest: fileExplorerTreeDateModeForView,
	  fileExplorerTreeSortModeForViewForTest: fileExplorerTreeSortModeForView,
	  setFileExplorerViewSettingForTest(view, key, value) { return setFileExplorerViewSetting(view, key, value, {refresh: false, publish: false}); },
	  nextFileExplorerTreeDateMode,
	  cycleFileExplorerTreeDateModeForTest() {
	    for (const view of ['finder', 'tabber', 'differ']) cycleFileExplorerTreeDateMode(view);
	  },
	  fileExplorerTreeDateModeForTest() { return fileExplorerTreeDateModeForView('finder'); },
	  setFileExplorerTreeDateModeForTest(value) {
	    for (const view of ['finder', 'tabber', 'differ']) setFileExplorerViewSetting(view, 'treeDateMode', value, {refresh: false, publish: false});
	  },
  fileTreeRecencyStateForMtimeForTest(mtime, nowMs) {
    const state = fileTreeRecencyStateForMtime(mtime, nowMs);
    return state ? {...state} : null;
  },
  setFileTreeRecencyNowForTest(value) {
    if (value == null) delete globalThis.__yolomuxFileTreeRecencyNowMs;
    else globalThis.__yolomuxFileTreeRecencyNowMs = Number(value);
  },
  dateTimeHourCycleForTest() { return dateTimeHourCycle; },
  setDateTimeHourCycleForTest(value) { dateTimeHourCycle = normalizeDateTimeHourCycle(value); },
  activeEditorSchemeForTest() { return activeEditorScheme(); },
  configuredEditorSchemeForMode,
  editorSchemeCssVariables,
  editorThemeLabel,
  editorPreviewThemeStateForTest: editorPreviewThemeState,
  applyEditorCursorStyle,
  setFileEditorThemeMode,
  setFileEditorPreviewDisplayMode,
  cycleEditorThemeMode,
  fileEditorThemeModeForTest() { return fileEditorThemeMode; },
  fileEditorPreviewDisplayModeForTest() { return fileEditorPreviewDisplayMode; },
  editorStateFieldsSnapshotForTest() {
    return {
      globalThemeMode,
      terminalThemeMode,
      themeMode: fileEditorThemeMode,
      previewDisplayMode: fileEditorPreviewDisplayMode,
      wrapEnabled: fileEditorWrapEnabled,
      lineNumbersEnabled: fileEditorLineNumbersEnabled,
      blameEnabled: fileEditorBlameEnabled,
      diffExpandUnchanged,
      previewFontSize: editorPreviewFontSize,
    };
  },
  fileEditorCursorStyleForTest() { return fileEditorCursorStyle; },
  setFileEditorCursorStyleForTest(value) { fileEditorCursorStyle = value; },
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  uiColorVisualPresetForTest: uiColorVisualPreset,
  applyGlobalThemeMode,
  globalThemeLabel,
  globalThemeIsDark,
  nextGlobalThemeMode,
  terminalThemeForGlobalTheme,
  terminalRenderThemeForGlobalTheme,
  terminalThemeForSession,
  terminalMinimumContrastRatio,
  setFileEditorCursorColorForTest(value) { fileEditorCursorColor = normalizeEditorCursorColor(value); },
  setFocusedPanelItemValueForTest(value) { focusedPanelItem = value; },
  globalThemeModeForTest() { return globalThemeMode; },
  setGlobalThemeModeForTest(value) { globalThemeMode = normalizeGlobalThemeMode(value); },
  resolvedGlobalThemeModeForTest: resolvedGlobalThemeMode,
  setSystemPrefersDarkForTest(value) {
    const previousMatchMedia = window.matchMedia;
    window.matchMedia = query => {
      const mediaQuery = String(query || '');
      const matches = mediaQuery.includes('prefers-color-scheme')
        ? value === true
        : previousMatchMedia?.(query)?.matches === true;
      return {matches, addEventListener() {}, addListener() {}};
    };
  },
  terminalThemeModeForTest() { return terminalThemeMode; },
  setTerminalThemeModeForTest(value) { terminalThemeMode = normalizeTerminalThemeMode(value); },
  expandPaneFromLayout,
  terminalThemeSettingForGlobalMode,
  sessionConfirmedGone,
  globalActivityCounts,
  globalActivityStatusLineHtml,
  openTabberActivityOverviewForTest: openTabberActivityOverview,
  clearPromptAttentionForSessionForTest(session, options = {}) { return clearPromptAttentionForSession(session, {...options, localOnly: options.localOnly !== false}); },
  attentionAcknowledgementKeyIsRecordedForTest: attentionAcknowledgementKeyIsRecorded,
  applyAttentionAcknowledgementResponseForTest: applyAttentionAcknowledgementResponse,
  acknowledgeAttentionKeysForTest: acknowledgeAttentionKeys,
  postAttentionAcknowledgementKeysForTest: postAttentionAcknowledgementKeys,
  setAttentionAcknowledgementPendingForTest(key) {
    const record = attentionAcknowledgementRecord(key, true);
    if (!record) return null;
    record.pending = true;
    return {...record};
  },
  clearSessionAttentionAcknowledgementRecordsForTest: clearSessionAttentionAcknowledgementRecords,
  attentionAcknowledgementRecordForTest(key) {
    const record = attentionAcknowledgementRecord(key);
    return record ? {...record} : null;
  },
  attentionAcknowledgementRecordCountForTest() { return attentionAcknowledgementRecords.size; },
  terminalDataShouldAcknowledgeAttentionForTest: terminalDataShouldAcknowledgeAttention,
  updateSessionButtonStatesForTest: updateSessionButtonStates,
  browserFaviconBadgeCount,
  browserFaviconBadgeLabel,
  tmuxSignalPaneForTarget,
  tmuxSignalAgentStateForSession,
  setTmuxSignalStateForTest(payload) { tmuxSignalState = payload; },
  tmuxSignalStateForTest() { return tmuxSignalState; },
  setAutoApproveStateForTest(session, state) { autoApproveStates.set(session, state); },
  autoApproveStateForTest(session) { return autoApproveStates.get(session); },
  applyAutoApprovePayloadForTest: applyAutoApprovePayload,
  setAgentAuthForTest(value) { agentAuth = value || {}; },
  maxTabsPerPane,
  paneCapacityCheckForInsert,
  tabsToEvictForCap,
  recordTabActivation,
  setTabLastActivatedForTest(item, ts) { tabLastActivatedAt.set(item, ts); },
  infoBranchRows,
  applyTmuxWindowActiveIndexToTranscriptInfoForTest: applyTmuxWindowActiveIndexToTranscriptInfo,
  fileContextMenuState,
  finderOpenInNewTabActionsForContext,
  fileExplorerIndexContextAction,
  fileEditorItemFor,
  fileEditorCopyItemFor,
  fileEditorDiffPreviewItemFor,
  fileEntryChanged,
  fileItemPath,
  filePanelItemsForPath,
  imageOpenUsesSharedViewer,
  imageViewerItemFor,
  markdownPreviewInputAllowed,
  PREVIEW_RENDERERS,
  previewRendererForPath,
  renderPreviewDescriptor,
  previewPathIsPreviewable,
  previewKindForPath,
  defaultFileEditorViewModeForPath,
  previewMediaKindForPath,
  previewMimeForPath,
  jsonLinesTablePreview,
  markdownPreviewHtml,
  markdownPreviewImageTarget,
  sanitizeStandaloneSvg,
  sanitizeStandaloneSvgString,
  standaloneSvgBlockedTagsForTest() { return Array.from(STANDALONE_SVG_BLOCKED_TAGS); },
  isMermaidFenceLanguage,
  keyboardShortcutsHtml,
  openFileEditorItems,
  pullRequestStatusLabel,
  pullRequestStatusDisplay,
  pullRequestStatusClass,
  pullRequestInlineStatusDisplay,
  pullRequestCiState,
  pullRequestCiStatus,
  pullRequestChecksHtml,
  pullRequestLinkLabel,
  pullRequestApprovalIndicatorHtml,
  pullRequestCompactBadgesHtml,
  pullRequestNumberIndicatorHtml,
  pullRequestReviewInlineHtml,
  sessionStateHtml,
  openFileStatus,
  fileErrorStateForTest: fileErrorState,
  missingFileStateForTest: missingFileState,
  tooLargeFileStateForTest: tooLargeFileState,
  fileErrorTextForTest: fileErrorText,
  fileInspectionErrorMessageForTest: fileInspectionErrorMessage,
  setOpenFileOwner,
  renderTransportWarning,
  captureFileEditorPanelViewStateForTest: captureFileEditorPanelViewState,
  restoreFileEditorPanelViewStateForTest: restoreFileEditorPanelViewState,
  fileEditorViewStateForTest(item) {
    const state = fileEditorViewState.get(item);
    return state ? {...state} : null;
  },
  pendingFileEditorLineTargetForTest(item) {
    return pendingFileEditorLineTargets.get(item) || null;
  },
  renderFileEditorPanelShouldCaptureViewStateForTest: renderFileEditorPanelShouldCaptureViewState,
  renderFileEditorPanel,
  renderLinkedFilePreviewPanelsForTest: renderLinkedFilePreviewPanels,
  refreshEditorPreviewsForTest: refreshEditorPreviews,
  setPanelNodeForTest(item, panel) { panelNodes.set(item, panel); },
  scheduleFileEditorSplitScrollSyncForTest: scheduleFileEditorSplitScrollSync,
  scheduleFileEditorPreviewLayoutSyncForTest: scheduleFileEditorPreviewLayoutSync,
  fileEditorPreviewScrollSyncSourceForTest: fileEditorPreviewScrollSyncSource,
  sourcePositionForPreviewScrollForTest: sourcePositionForPreviewScroll,
  editorScrollTopForSourcePositionForTest: editorScrollTopForSourcePosition,
  fileEditorScrollSyncBlockedForTest: fileEditorScrollSyncBlocked,
  setFileEditorPanelStatusForTest: setFileEditorPanelStatus,
  reloadOpenFileFromDiskForTest: reloadOpenFileFromDisk,
  refreshOpenFilesIfChangedForTest: refreshOpenFilesIfChanged,
  replaceOpenFileStateFromDiskForTest: replaceOpenFileStateFromDisk,
  loadOpenFileStateFromDiskForTest: loadOpenFileStateFromDisk,
  loadFileEditorStateForTest: loadFileEditorState,
  commandPaletteValidateFileTabPathsForTest: commandPaletteValidateFileTabPaths,
  fileEditorItemForTest: fileEditorItemFor,
  saveFileEditorForTest: saveFileEditor,
  refreshOpenFileDiffForTest: refreshOpenFileDiff,
  renderEditorPreviewPane,
  openFileIsMissing,
  terminalTabLabel,
  terminalTabTitle,
  terminalTabDisplayLabel,
  terminalTmuxPrefixWindowShortcutForTest: terminalTmuxPrefixWindowShortcut,
  terminalTmuxAltWindowShortcutForTest: terminalTmuxAltWindowShortcut,
  terminalTmuxInputStateForTest(session) {
    const state = terminalTmuxInputState(session);
    return state ? {...state} : null;
  },
  expireTerminalTmuxRepeatForTest(session) {
    const state = terminalTmuxInputState(session);
    if (state) state.repeatUntilMs = 0;
    return pruneTerminalTmuxInputState(session);
  },
  tmuxWindowForTest: tmuxWindow,
  activateTmuxWindowFromUserActionForTest(session, windowIndex, label = '', options = {}) {
    return activateTmuxWindowFromUserAction(session, windowIndex, label, {...options, localOnly: options.localOnly !== false});
  },
  registerFileEditorLayoutItemForTest: registerFileEditorLayoutItem,
  setOpenFileStateForTest(path, state) { setFileState(path, state); },
  setHistoricalFileStateForTest(item, state) { return setHistoricalFileState(item, state); },
  fileEditorStateForItemForTest: fileEditorStateForItem,
  openPathForPhysicalFileForTest: openPathForPhysicalFile,
  setFileOpenPromiseForTest: setFileOpenPromise,
  fileOpenPromiseForTest: fileOpenPromiseFor,
  currentFileStateForTest(path) {
    const state = fileStateFor(path);
    return state ? {...state} : null;
  },
  openFileStateForTest(path) {
    const state = fileStateFor(path);
    return state ? {...state} : null;
  },
  renderTreeChildrenForTest(container, parentPath, entries, depth = 0, entriesByDirPairs = [], options = {}) {
    renderTreeChildren(container, parentPath, entries, depth, {...options, entriesByDir: new Map(entriesByDirPairs)});
  },
  renderTabberTreeForTest: renderTabberTree,
  treeRowContractForTest(row) {
    return {
      dataset: Object.fromEntries(Object.entries(row?.dataset || {}).sort(([left], [right]) => left.localeCompare(right))),
      classes: Array.from(row?.classList?.names || row?.classList || []).sort(),
      ariaSelected: row?.getAttribute?.('aria-selected') || '',
      ariaExpanded: row?.getAttribute?.('aria-expanded') || '',
      ariaCurrent: row?.getAttribute?.('aria-current') || '',
      draggable: row?.draggable === true,
      columns: Array.from(row?.children || []).map(child => child.className),
    };
  },
  expandDirectoryRowForTest: expandDirectoryRow,
  collapseDirectoryRowForTest: collapseDirectoryRow,
  setFileExplorerRepoInfoForTest(path, repo) {
    fileExplorerRepoInfoCache.set(normalizeDirectoryPath(path), repo);
  },
  cacheFileExplorerRepoInfoForTest: cacheFileExplorerRepoInfo,
  repoInfoPopoverHtml,
  fileTreeRepoSyncMeta,
  fileTreeDisplayParts,
  setChangesFolderCollapsedForTest(keys) { changesFolderCollapsed = new Set((keys || []).map(String)); },
  changesFolderCollapsedForTest() { return Array.from(changesFolderCollapsed).sort(); },
  changesRepoCollapsedForTest() { return Array.from(changesRepoCollapsed).sort(); },
  rawFileUrl,
  rawFileMediaVersion,
  rawFileDownloadUrl,
  fetchRawFileBlobForTest: fetchRawFileBlob,
  installRawFileMediaSourceForTest: installRawFileMediaSource,
  releaseRawFileMediaSourceForTest: releaseRawFileMediaSource,
  releaseRawFileMediaSourcesForTest: releaseRawFileMediaSources,
  previewFileActionLinksForTest: previewFileActionLinks,
  zipFileDownloadUrl,
  downloadFilenameFromContentDisposition,
  markOpenFileDiffUnavailable,
  openChangedFileInDiffForTest: openChangedFileInDiff,
  openFileInEditorForTest: openFileInEditor,
  openFileInAdditionalEditorTabForTest: openFileInAdditionalEditorTab,
  openHistoricalFileInEditorForTest: openHistoricalFileInEditor,
  closeFileTabForTest: closeFileTab,
  historicalDiffRefControlsHtmlForTest: historicalDiffRefControlsHtml,
  openFilePreviewPopoutForTest: openFilePreviewPopout,
  setFilePreviewPopoutForTest(path, previewWindow) { filePreviewPopouts.set(path, {window: previewWindow}); },
  filePreviewPopoutForTest(path) { return filePreviewPopouts.get(path) || null; },
  closePopoutsForLayoutItemForTest: closePopoutsForLayoutItem,
  focusPreferencesSearch,
  renderPreferencesPanelsForTest: renderPreferencesPanels,
  renderPaneTabStrips,
  paneTabDisplayContext,
  fileTabParentDisambiguators,
  ensureFileTabStateForItem,
  setDragSessionForTest(session) { dragState.item = session; },
  dragStateForTest() { return {...dragState}; },
  scheduleJsDebugPanelRefreshForTest: scheduleJsDebugPanelRefresh,
  jsDebugRenderDragDeferredForTest() { return jsDebugRenderDragDeferred; },
  pendingTabStripRenderForTest() { return pendingTabStripRender; },
  renderSessionButtonsForTest: renderSessionButtons,
  sessionButtonsForTest() { return sessionButtons; },
  pendingSessionButtonsRenderForTest() { return pendingSessionButtonsRender; },
  setPendingSessionButtonsRenderForTest(value) { pendingSessionButtonsRender = Boolean(value); },
  setDocumentActiveElementForTest(element) { document.activeElement = element; },
  documentActiveElementForTest() { return document.activeElement; },
  topbarControlIsActiveForTest: topbarControlIsActive,
  flushPendingSessionButtonsRenderForTest: flushPendingSessionButtonsRender,
  pendingPreferencesRenderForTest() { return pendingPreferencesRender; },
  setPendingPreferencesRenderForTest(value) { pendingPreferencesRender = Boolean(value); },
  preferencesScrollActiveUntilForTest() { return preferencesScrollActiveUntil; },
  setPreferencesScrollActiveUntilForTest(value) { preferencesScrollActiveUntil = Number(value) || 0; },
  notePreferencesScrollActivityForTest: notePreferencesScrollActivity,
  renderPanels,
  pendingPanelsRenderForTest() { return Boolean(pendingLayoutRender); },
  pendingLayoutRenderForTest() { return pendingLayoutRender; },
  pendingLayoutRenderFrameForTest() { return pendingLayoutRenderFrame; },
  setPendingPanelsRenderForTest(value) {
    pendingLayoutRender = value ? {previousActive: [], prevShape: '', nextShape: '', options: {}, reason: 'test', forceFull: true} : null;
  },
  setClientSettingsPatchForTest(patch) {
    clientSettings = mergeSettingObjects(clientSettings, patch || {});
  },
  setClientSettingsPayloadPatchForTest(patch) {
    clientSettingsPayload = {...clientSettingsPayload, ...(patch || {})};
  },
  setWindowConfirmForTest(fn) {
    window.confirm = fn;
  },
  preferenceItemMatches,
  preferenceSectionMatches,
  quickOpenExclusionSettingPatchForTest: quickOpenExclusionSettingPatch,
  settingsLoadedAgeText,
  preferencesPanelHtmlForTest(query, collapsed = []) {
    preferencesSearchText = query || '';
    collapsedPreferenceSections = new Set(collapsed);
    preferencesResetConfirmVisible = false;
    return preferencesPanelHtml();
  },
  preferencesResetConfirmHtmlForTest() {
    preferencesResetConfirmVisible = true;
    return preferencesPanelHtml();
  },
  registerFileEditorLayoutItem,
  registerImageViewerLayoutItem,
  requestFileEditorPanelFocus,
  focusFileEditorPanelIfReady,
  openFileSurfacePane,
  minimizePaneFromLayout,
  removePaneFromLayout,
  canCloseEmptyPane,
  closeEmptyPaneFromLayout,
  removeSessionFromLayout,
  closePaneFrameItem,
  runtimeIntervalDelay,
  runtimeIntervalCatalogForTest: runtimeIntervalCatalogEntry,
  runtimeTimerClassCatalogForTest: () => runtimeTimerClassCatalog,
  runtimeIntervalDiagnosticsForTest: runtimeIntervalDiagnostics,
  resetRuntimeIntervalForTest: resetRuntimeInterval,
  clearRuntimeIntervalForTest: clearRuntimeInterval,
  runtimeIntervalActiveForTest: runtimeIntervalActive,
  runtimeIntervalStateForTest(name) {
    const state = runtimeIntervals.get(name);
    return state ? {active: state.active === true, delay: state.delay, timer: state.timer} : null;
  },
  sessionPopoverHtml,
  setFileQuickOpenCandidatesForTest(root, files) {
    fileQuickOpenState.root = root;
    fileQuickOpenState.candidates = files;
    fileQuickOpenState.loading = false;
    fileQuickOpenState.error = '';
    commandPaletteMode = 'files';
  },
  setFileQuickOpenIndexWarmingForTest(warming) {
    fileQuickOpenState.indexWarming = Boolean(warming);
    commandPaletteMode = 'files';
  },
  setFileQuickOpenFreshnessForTest(freshness) {
    fileQuickOpenState.freshness = freshness || null;
    commandPaletteMode = 'files';
  },
  commandPaletteStatusTextForTest: commandPaletteStatusText,
  commandPaletteFreshnessTextForTest: commandPaletteFreshnessText,
  renderCommandPaletteResultsForTest: renderCommandPaletteResults,
  setFileQuickOpenLoadingForTest(loading) {
    fileQuickOpenState.loading = Boolean(loading);
    fileQuickOpenState.error = '';
    commandPaletteMode = 'files';
  },
  setFileQuickOpenErrorForTest(error) {
    fileQuickOpenState.candidates = [];
    fileQuickOpenState.loading = false;
    fileQuickOpenState.error = error;
    commandPaletteMode = 'files';
  },
  fileQuickOpenStateForTest() { return {...fileQuickOpenState, candidates: fileQuickOpenState.candidates.slice()}; },
  installCommandPaletteFixtureForTest() {
    const node = document.createElement('div');
    node.className = 'app-modal-overlay command-palette';
    const dialog = document.createElement('div');
    dialog.className = 'command-palette-dialog';
    const input = document.createElement('input');
    input.className = 'command-palette-input';
    input.focus = () => {};
    const status = document.createElement('div');
    status.className = 'command-palette-status';
    const results = document.createElement('div');
    results.className = 'command-palette-results';
    dialog.appendChild(input);
    dialog.appendChild(status);
    dialog.appendChild(results);
    node.appendChild(dialog);
    commandPaletteState.node = node;
    return node;
  },
  openCommandPaletteForTest: openCommandPalette,
  closeCommandPaletteForTest: closeCommandPalette,
  scheduleFileQuickOpenSearchForTest: scheduleFileQuickOpenSearch,
  refreshFileQuickOpenCandidatesForTest: refreshFileQuickOpenCandidates,
  abortFileQuickOpenSearchForTest: abortFileQuickOpenSearch,
  sha256HexForTest: sha256HexOfString,
  fileSearchScopeIdForTest: fileSearchScopeId,
  mergeFileQuickOpenChangesForTest: mergeFileQuickOpenChanges,
  rankAndPruneFileQuickOpenCandidatesForTest: rankAndPruneFileQuickOpenCandidates,
  ingestFileQuickOpenDeltaForTest: ingestFileQuickOpenDeltaPayload,
  handleFileSearchProgressSignalForTest: handleFileSearchProgressSignal,
  fileQuickOpenDeltaRootsForTest() {
    return Array.from(fileQuickOpenState.deltaRoots.values()).map(entry => ({...entry}));
  },
  setCommandPaletteQueryForTest(value) {
    commandPaletteState.query = String(value || '');
    commandPaletteState.index = 0;
  },
  commandPaletteResultsHtmlForTest() {
    const query = commandPaletteSearchQuery();
    const rows = commandPaletteRankItems(commandPaletteItems(), query).slice(0, 60);
    return rows.length ? commandPaletteResultsHtml(rows, query) : '';
  },
  commandPaletteEmptyTextForTest: commandPaletteEmptyText,
  commandPaletteItemLabelHtmlForTest: commandPaletteItemLabelHtml,
  commandPaletteStatusHtmlForTest: commandPaletteStatusHtml,
  setTabsMenuSearchTextForTest(value) { tabsMenuSearchText = String(value || ''); },
  sessionState,
  slotForNewFileEditorTab,
  slotForNewTmuxSession,
  slotForSession,
  slotForTabActivation,
  focusedActivationSlotForTest: focusedActivationSlot,
  fileEditorActivationSlotForTest: fileEditorActivationSlot,
  simpleCodeSyntaxHtml,
  smallLayoutSlotCandidate,
  slotCanAutoPrune,
  splitPercentForPointer,
  layoutNodeMinWidth,
  minSplitPaneWidthPx,
  minSplitPaneHeightPx,
  layoutVisiblePaneCount,
  splitPercentForNewItem,
  sessionNotificationTitleForTest: sessionNotificationTitle,
  attentionToastLineForTest: attentionToastLine,
  handleDropDragOver,
  classifyLayoutDragForTest: classifyLayoutDrag,
  applyLayoutDragIntentForTest: applyLayoutDragIntent,
  installFilePathDropTarget,
  showPaneTabDropPreview,
  showDropPreview,
  clearDropPreview,
  shouldPreserveSourceSlotForSplit,
  startSessionDrag,
  startPaneDrag,
  beginFileDragForTest: beginFileDrag,
  cancelDragOperationStateForTest: cancelDragOperationState,
  paneDragPayload,
  endSessionDrag,
  startFileTreeDrag,
  stopCustomDragPreview,
  syncInitialLayoutUrl,
  tabMenuDetailText,
  sessionWorkGraphForTest: sessionWorkGraph,
  focusedRepositoryIdsForTmuxTargetForTest: focusedRepositoryIdsForTmuxTarget,
  branchIdsForGitWorktreeForTest: branchIdsForGitWorktree,
  pullRequestIdsForTmuxTargetForTest: pullRequestIdsForTmuxTarget,
  sessionWorkSummaryForTest: sessionWorkSummary,
  sessionTabDescriptionForTest: sessionTabDescription,
  sessionWorkDescriptionForTest: sessionWorkDescription,
  displayPullRequestForTest: displayPullRequest,
  tabSearchFields,
  tabSearchScore,
  TAB_TYPES,
  tabTypeForItem,
  tabTypeForParam,
  paneRoleGeneric,
  paneRoleSide,
  paneSideLeft,
  paneSideRight,
  panePlacementGenericOnly,
  panePlacementSideAllowed,
  panePlacementSideRequired,
  genericPaneRoleDefinition,
  sidePaneRoleDefinition,
  paneRoleDefinition,
  panePlacementForItem,
  paneRoleAllowsItem,
  paneRoleForState,
  paneRoleForSlot,
  sidePaneSlots,
  sidePaneSlotsForSide,
  sidePaneSlot,
  slotIsSidePane,
  sidePaneMinimumViewportWidthPx,
  sidePanesAvailable,
  sidePaneConstrainedMode,
  layoutHasSidePane,
  layoutSidePaneRootSplit,
  clampSidePaneWidthPercent,
  sidePaneWidthPercent,
  paneRoleAllowsItemTransfer,
  paneStateWithTabs,
  emptyPaneState,
  emptyPlaceholderPaneState,
  terminalWheelSignedLines,
  terminalWheelEventIsDiscrete,
  terminalTouchGestureDecision,
  terminalTouchSignedRows,
  terminalTouchAlternateCommand,
  terminalHasMouseTracking,
  routeTerminalScrollLinesForTest: routeTerminalScrollLines,
  sessionPaneIsAlternateScreen,
  terminalWrappedLineLinks,
  terminalWrappedLineReferences,
  terminalReferenceAtPosition,
  terminalReferenceProviderLinks,
  terminalFileReferenceUnderlineSegments,
  terminalFileReferenceUnderlineIsActiveForTest: terminalFileReferenceUnderlineIsActive,
  installTerminalFileReferenceUnderlines,
  terminalFileReferenceAbsolutePath,
  terminalFileReferenceTarget,
  terminalFileReferenceTargetCacheSizeForTest: () => terminalFileReferenceTargetCache.size,
  terminalFileReferenceTargetCacheHasForTest: (session, reference) => terminalFileReferenceTargetCache.has(terminalFileReferenceCacheKey(session, reference)),
  invalidateTerminalFileReferenceTargetsForTest: invalidateTerminalFileReferenceTargets,
  terminalPositionFromClientPoint,
  requestFileEditorLineTarget,
  applyPendingFileEditorLineTarget,
  showTerminalContextMenuForTest: showTerminalContextMenu,
  transcriptPathRowHtml,
  splitNode,
  splitSessionAtSlot,
  splitSessionAtLayoutBoundary,
  splitSessionAtGutter,
  updateActiveSessionParam,
  openYoagentRightPane,
  rightmostExistingPaneSlot,
  paneTabDropIndex,
  paneTabDropPlacement,
  dockviewTabPointerRootBoundaryIntentForTest: dockviewTabPointerRootBoundaryIntent,
  dockviewTabDropWouldNoop,
  dockviewTabEdgeReorderIntent,
  dockviewTabStripEndDropIntent,
  dockviewTabDropViolatesPinnedPartitionForTest: dockviewTabDropViolatesPinnedPartition,
  windowStepButtonFromEvent,
  tmuxWindowStepTargetForTest: tmuxWindowStepTarget,
  tmuxWindowRecords,
  tmuxWindowBarLabelMode,
  tmuxWindowButtonHtmlForTest: tmuxWindowButtonHtml,
  tmuxWindowBarHtml,
  updatePanelInfoBarMetaForTest: updatePanelInfoBarMeta,
  updatePanelWindowStepButtonsForTest: updatePanelWindowStepButtons,
  agentWindowVisibleTonesForTest() { return [...AGENT_WINDOW_VISIBLE_TONES]; },
  agentWindowAggregateTonesForTest() { return [...AGENT_WINDOW_AGGREGATE_TONES]; },
  agentWindowVisibleToneForTest: agentWindowVisibleTone,
  agentWindowStatusToneForItemForTest: agentWindowStatusToneForItem,
  agentWindowStatusItemVisualRankForTest: agentWindowStatusItemVisualRank,
  agentWindowActivityToneWrapperClassForTest: agentWindowActivityToneWrapperClass,
  agentWindowStatusSampleItemForTest: agentWindowStatusSampleItem,
  agentWindowStatusDotHtmlForToneForTest: agentWindowStatusDotHtmlForTone,
  agentWindowStatusDotHtmlForTest: agentWindowStatusDotHtml,
  agentWindowActivityStyleAttributeForTest: agentWindowActivityStyleAttribute,
  agentWindowActivityIconForTest: agentWindowActivityIcon,
  agentWindowActivityRecordForTest(key) {
    const record = agentWindowActivityRecord(key);
    if (!record) return null;
    const visual = record.acknowledgementVisual;
    return {
      activity: record.activity ? {...record.activity} : null,
      stoppedRefreshUntilMs: Number(record.stoppedRefresh?.untilMs || 0),
      transitionPulseRefreshUntilMs: Number(record.transitionPulseRefresh?.untilMs || 0),
      acknowledgedStoppedAt: Number(record.acknowledgedStoppedAt || 0),
      acknowledgementVisual: visual ? {
        startedAtMs: Number(visual.startedAtMs || 0),
        untilMs: Number(visual.untilMs || 0),
        durationMs: Number(visual.durationMs || 0),
        acknowledgementKey: String(visual.acknowledgementKey || ''),
        refreshed: visual.refreshed === true,
      } : null,
    };
  },
  topbarActivityCountBallHtmlForTest: topbarActivityCountBallHtml,
  keyboardLegendStatusSampleForTest: keyboardLegendStatusSample,
  preferencesStatusPulseExampleHtmlForTest: preferencesStatusPulseExampleHtml,
  setWorkflowTransitionGlowSecondsForTest(value) { workflowTransitionGlowSeconds = Math.max(0, Number(value) || 0); },
  acknowledgeAgentWindowActivityForTest(session, windowIndex = null, options = {}) { return acknowledgeAgentWindowActivity(session, windowIndex, {...options, localOnly: options.localOnly !== false}); },
  acknowledgeTerminalAttentionFromUserActionForTest(session, windowIndex = null, options = {}) { return acknowledgeTerminalAttentionFromUserAction(session, windowIndex, {...options, localOnly: options.localOnly !== false}); },
  acknowledgeAgentWindowStoppedTransitionForTest: acknowledgeAgentWindowStoppedTransition,
  agentWindowAcknowledgementVisualActiveForTest: agentWindowAcknowledgementVisualActive,
  agentWindowActivityIconHtmlForStatusForTest: agentWindowActivityIconHtmlForStatus,
  sessionAgentWindowStatusModelForTest: sessionAgentWindowStatusModel,
  sessionAgentWindowStatusPayloadsForTest: sessionAgentWindowStatusPayloads,
  sessionAgentWindowStatusSummaryForTest: sessionAgentWindowStatusSummary,
  infoTabGroupStatusRecordForTest: infoTabGroupStatusRecord,
  tmuxWindowCanonicalLabelForTest: tmuxWindowCanonicalLabel,
  buildTabberTreeForTest: buildTabberTree,
  tabMenuItems,
  sortTabItemsForMenu,
  tmuxPaneTabHtml,
  paneTabs,
  paneStateWithTabs,
  markdownSyntaxHtml,
  markdownTextWithSourceAnchors,
  markdownTaskLineEntries,
  markdownTextWithTaskLineToggled,
  bindMarkdownTaskCheckboxesForTest: bindMarkdownTaskCheckboxes,
  updateMarkdownTaskFromPreviewForTest: updateMarkdownTaskFromPreview,
  markdownPreviewBlockedTagsForTest() { return Array.from(MARKDOWN_PREVIEW_BLOCKED_TAGS); },
  moveSessionToSlot,
  dropItemCanBeDraggedForTest: dropItemCanBeDragged,
  openFileEditorPane,
  onFileTreeRowClick,
  pathRelativeToDirectory,
  replaceHtmlPreservingScroll,
  reconcilePanelBody,
  elementScrollAnchor,
  keyedScrollAnchor,
  pruneFileExplorerSelectionForRoot,
  selectFileTreePath,
  selectFileTreeRange,
  updateFileTreeSelectionFromClick,
  fileExplorerKeyIntent,
  sharedTreeKeyIntentForTest: sharedTreeKeyIntent,
  handleFileExplorerArrowNavForTest: handleFileExplorerArrowNav,
  activeTabberRowPathForTest: activeTabberRowPath,
  syncTabberTreeActiveSelectionForTest: syncTabberTreeActiveSelection,
  syncTabberTreeLayoutStateForTest: syncTabberTreeLayoutState,
  tabberTreeSelectionForTest() {
    return {
      paths: Array.from(tabberTreeSelectedPaths).sort(),
      lead: tabberTreeSelectionLead,
    };
  },
  sharedTreeControllerNamesForTest: sharedTreeInteractionControllerNames,
  currentSlots() { return layoutSlots; },
  fileExplorerSelectionForTest() {
    return {
      paths: Array.from(fileExplorerSelectedPaths).sort(),
      anchor: fileExplorerSelectionAnchor,
      manual: fileExplorerManualSelectionActive,
    };
  },
  fileExplorerSelectionLeadForTest() { return fileExplorerSelectionLead; },
  setFileExplorerSelectionForTest(paths, anchor = null) {
    fileExplorerSelectedPaths.clear();
    for (const path of paths || []) fileExplorerSelectedPaths.add(path);
    fileExplorerSelectionAnchor = anchor;
    setFileExplorerSelectionPin(false);
  },
  activeFileForTest() { return activeFile; },
  setFileExplorerExpandedForTest(paths) {
    fileExplorerExpanded.clear();
    for (const path of paths || []) fileExplorerExpanded.add(path);
  },
  fileExplorerExpandedForTest() { return Array.from(fileExplorerExpanded).sort(); },
  setFileExplorerRootForTest(path) { fileExplorerRoot = normalizeDirectoryPath(path); },
  fileExplorerRootForTest() { return fileExplorerRoot; },
  fileExplorerTreeForTest() { return fileExplorerTree; },
  fileExplorerPathDisplayForTest() { return fileExplorerPath?.value || fileExplorerPath?.textContent || ''; },
  setFileExplorerDirListingForTest(path, entries) {
    setFileExplorerFsResourceValue('list', path, entries);
  },
  fileExplorerFsResourceKeysForTest() { return [...fileExplorerFsResourceRecords.keys()]; },
  setAutoApproveStateForTest(session, payload) {
    autoApproveStates.set(session, payload);
  },
  setTranscriptInfoForTest(session, info) {
    transcriptMetadataState.payload.sessions = {...(transcriptMetadataState.payload.sessions || {}), [session]: info};
  },
  setTranscriptSessionOrderForTest(values) {
    transcriptMetadataState.payload.session_order = Array.isArray(values) ? values.map(value => String(value)) : [];
  },
  setActivitySummaryPayloadForTest(payload) {
    activitySummaryState.guard.invalidate();
    activitySummaryState.payload = payload;
    activitySummaryState.refreshing = false;
    yoagentStartupState.activityPayload = null;
  },
  activitySummaryEnabledForTest() { return activitySummaryEnabled; },
  activitySummaryStateForTest() {
    return {payload: activitySummaryState.payload, refreshing: activitySummaryState.refreshing};
  },
  yoagentRecentAgentsHtmlForTest: yoagentRecentAgentsHtml,
  applyServerMetadataPulsesForTest(session, pulses) {
    updateMetadataBadgePulses({sessions: {[session]: {metadata_badge_pulse_remaining_ms: pulses}}});
  },
  storageValueForTest(key) { return localStorage.getItem(key); },
  sessionStorageValueForTest(key) { return sessionStorage.getItem(key); },
  rememberFileExplorerOpenIntentForTest: rememberFileExplorerOpenIntent,
  fileExplorerClosedByUserForTest: fileExplorerClosedByUser,
  windowListenersForTest(type) { return [...(window.__listeners?.get?.(type) || [])]; },
  appRootForTest: appRootElement,
  appViewport,
  viewportDiagnosticsSnapshotForTest: viewportDiagnosticsSnapshot,
  viewportDiagnosticsTextForTest: viewportDiagnosticsText,
  renderViewportDiagnosticsForTest: renderViewportDiagnostics,
  nativeViewport,
  nativeUsableViewportHeightForTest: nativeUsableViewportHeight,
  syncNativeAppViewportForTest: syncNativeAppViewport,
  setNativeViewportForTest({width, height, visualHeight = null, scale = 1} = {}) {
    if (Number.isFinite(Number(width))) window.innerWidth = Number(width);
    if (Number.isFinite(Number(height))) window.innerHeight = Number(height);
    window.visualViewport = visualHeight == null ? null : {height: Number(visualHeight), scale: Number(scale)};
  },
  effectiveViewportWidthForTest: effectiveViewportWidth,
  setAppViewportOverrideForTest: setAppViewportOverride,
  appSpaceRect,
  appSpacePoint,
  visualPointFromAppSpace,
  fitTerminalForTest: fitTerminal,
  setSessionFilesPayloadForTest(payload) {
    setSessionFilesPayloadForDestination('differ', payload);
  },
  setSessionFilesPayloadForDestinationForTest(payload) {
    setSessionFilesPayloadForDestination('differ', payload);
  },
  setSessionFilesLoadingForTest(loading) {
    fileExplorerSessionFilesState.loading = Boolean(loading);
  },
  setFileExplorerSessionFilesPayloadForTest(payload) {
    setSessionFilesPayloadForDestination('differ', payload);
  },
  setFinderSessionFilesPayloadForTest(payload) {
    setSessionFilesPayloadForDestination('finder', payload);
  },
  sessionFilesPayloadForTest() {
    return fileExplorerSessionFilesState.payload;
  },
  finderSessionFilesPayloadForTest() {
    return fileExplorerFinderSessionFilesState.payload;
  },
  applyLayoutUrlStateSeedForTest: applyLayoutUrlStateSeed,
  layoutUrlStateSnapshotForTest: layoutUrlStateSnapshot,
  applyEditorStateFieldsForTest: applyEditorStateFields,
  applyPendingLayoutUrlStateForTest: applyPendingLayoutUrlState,
  scheduleLayoutUrlStateRefreshForTest: scheduleLayoutUrlStateRefresh,
  layoutUrlStateForTest() { return {...layoutUrlState}; },
  setSessionFilesCachePayloadForTest(session, payload) {
    fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {
      payload: {...payload, session},
      signature: sessionFilesPayloadSignatureForPayload(payload),
    });
  },
  setFinderSessionFilesCachePayloadForTest(session, payload) {
    fileExplorerFinderSessionFilesCache.set(sessionFilesCacheKey(session, 'finder'), {
      payload: {...payload, session},
      signature: sessionFilesPayloadSignatureForPayload(payload),
    });
  },
  applySessionFilesPayloadFromPushForTest: applySessionFilesPayloadFromPush,
  sessionFilesPanelIsLoadingForTest: sessionFilesPanelIsLoading,
  sessionFilesPushRequestMatchesCurrentForTest: sessionFilesPushRequestMatchesCurrent,
  sessionFilesRequestMatchesDestinationForTest: sessionFilesRequestMatchesDestination,
  changedFileOwnerSessionForPathForTest: changedFileOwnerSessionForPath,
  fileTreeChangedAncestorStatsForTest(payload) {
    return Array.from(fileTreeChangedAncestorStats(payload).entries()).map(([path, stats]) => [path, {...stats}]);
  },
  updateFileTreeGitStatusRowsForTest(rows) {
    updateFileTreeGitStatusRows(rows);
  },
  setFileExplorerModeForTest(mode) {
    fileExplorerMode = normalizeFileExplorerMode(mode);
  },
  fileExplorerModeForTest() { return fileExplorerMode; },
  readStoredFileExplorerModeForTest(value) {
    if (value == null) localStorage.removeItem(fileExplorerModeStorageKey);
    else localStorage.setItem(fileExplorerModeStorageKey, value);
    return readStoredFileExplorerMode();
  },
  buildTabberTree,
  renderTabberTree,
  fetchTabberSessionFilesForTest: fetchTabberSessionFiles,
  fetchTabberSessionFilesBatchForTest: fetchTabberSessionFilesBatch,
  tabberSessionForNumericKey,
  openTabberSessionForTest: openTabberSession,
  bindTabberPanelForTest: bindTabberPanel,
  fileExplorerChangesPanelStaticHtmlForTest: fileExplorerChangesPanelStaticHtml,
  fileExplorerTreeSortSelectHtmlForTest: fileExplorerTreeSortSelectHtml,
  fileExplorerModeSwitcherHtml,
  normalizeFileExplorerMode,
  finderRelativeCopyEntryForSelectionForTest: finderRelativeCopyEntryForSelection,
  fileTreeRelativeCopyEntriesForTest: fileTreeRelativeCopyEntries,
  repoRelativeCopyEntryForSelectionForTest: repoRelativeCopyEntryForSelection,
  fileTreeRelativeCopyModeForTest: fileTreeRelativeCopyMode,
  setTabberActivityForTest(payload) {
    tabberActivityPayload = payload;
    tabberActivityState.requestGeneration = 0;
    tabberActivityState.appliedGeneration = 0;
    tabberActivityState.loaded = true;
    tabberActivityState.request = null;
  },
  applyTabberActivityPayloadForTest: applyTabberActivityPayload,
  fetchTabberActivityForTest: fetchTabberActivity,
  handleClientPushEventNowForTest: handleClientPushEventNow,
  tabberActivityPayloadForTest() { return tabberActivityPayload; },
  tabberActivityStateForTest() { return {...tabberActivityState}; },
  setFileExplorerTreeSortModeForTest(mode) {
    for (const view of ['finder', 'tabber', 'differ']) setFileExplorerViewSetting(view, 'treeSortMode', mode, {refresh: false, publish: false});
  },
  setTabberSessionFilesForTest(session, files) {
    const state = tabberSessionFilesState(session);
    state.files = files;
    state.loaded = true;
    state.loading = false;
  },
  setTabberSessionFilesLoadingForTest(session) {
    const state = tabberSessionFilesState(session);
    state.files = [];
    state.loaded = false;
    state.loading = true;
  },
  setTabberCollapsedForTest(paths) {
    fileExplorerTabberCollapsed.clear();
    fileExplorerTabberExpanded.clear();
    for (const path of paths || []) fileExplorerTabberCollapsed.add(path);
  },
  setTabberPathExpandedForTest: setTabberPathExpanded,
  tabberRenderedRowsForTest(options = {}) {
    if (options.preserveCollapsed !== true) {
      fileExplorerTabberCollapsed.clear();
      fileExplorerTabberExpanded.clear();
      if (options.defaultCollapsed !== true) {
        const {entries, entriesByDir} = buildTabberTree();
        const expandAll = (list, parent) => {
          for (const entry of list || []) {
            if (entry.kind !== 'dir') continue;
            const path = parent === '/' ? '/' + entry.name : parent + '/' + entry.name;
            fileExplorerTabberExpanded.add(path);
            expandAll(entriesByDir.get(normalizeDirectoryPath(path)), path);
          }
        };
        expandAll(entries, '/');
      }
    }
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    return Array.from(el.querySelectorAll('.file-tree-row')).map(row => {
      const dateCell = row.querySelector('.file-tree-date') || {};
      const dateHtml = dateCell.innerHTML || '';
      const dateText = dateHtml ? dateHtml.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim() : (dateCell.textContent || '');
      return {
        type: row.dataset.tabberType || '',
        name: (row.querySelector('.file-tree-name') || {}).textContent || '',
        icon: (row.querySelector('.file-tree-icon') || {}).textContent || '',
        path: row.dataset.path || '',
        repoRoot: row.dataset.tabberRepoRoot || '',
        branch: row.dataset.tabberBranch || '',
        nameHtml: (row.querySelector('.file-tree-name') || {}).innerHTML || '',
        status: (row.querySelector('.file-tree-git-status') || {}).textContent || '',
        statusHidden: row.querySelector('.file-tree-git-status')?.hidden !== false,
        date: dateText,
        dateHtml,
        recency: row.dataset.recency || '',
        title: row.dataset.tabberTitle || row.getAttribute('title') || '',
        nativeTitle: row.getAttribute('title') || '',
        ariaCurrent: row.getAttribute('aria-current') || '',
        ariaExpanded: row.getAttribute('aria-expanded') || '',
        ariaSelected: row.getAttribute('aria-selected') || '',
        classes: Array.from(row.classList?.names || row.classList || []).sort(),
        dateClasses: Array.from(dateCell.classList?.names || dateCell.classList || []).sort(),
        dateAttentionDelay: dateCell.style?.getPropertyValue?.('--attention-animation-delay') || '',
        datasetKeys: Object.keys(row.dataset).sort(),
      };
    });
  },
  tabberRenderedNamesForTest() {
    fileExplorerTabberCollapsed.clear();
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    renderTabberTree(el);
    return Array.from(el.querySelectorAll('.file-tree-row')).map(row => {
      const name = row.querySelector('.file-tree-name');
      return name ? (name.textContent || '') : '';
    });
  },
  setSessionFilesSortModeForTest(mode) {
    setFileExplorerViewSetting('differ', 'treeSortMode', mode, {refresh: false, publish: false});
  },
  sortedSessionFiles,
  sessionYoloIsWorking,
  runningAgentCount,
  updateDocumentTitle,
  documentTitleForTest() { return document.title; },
  setDocumentTitleNowForTest(value) { window.__yolomuxDocumentTitleNowMs = Number(value); },
  modalClassForTest() { return document.getElementById('modal').className; },
  modalTitleForTest() { return document.getElementById('modalTitle').textContent; },
  modalBodyHtmlForTest() { return document.getElementById('modalBody').innerHTML; },
  statusTextForTest() { return statusEl.textContent; },
  statusHtmlForTest() { return statusEl.innerHTML; },
  statusClassForTest() { return statusEl.className; },
  statusKindForTest() { return statusEl.dataset.layoutStatusKind || ''; },
  serialize(slots) {
    return {
      tree: slots[layoutTreeKey],
      panes: Object.fromEntries(layoutSlotKeys(slots).map(slot => [
        slot,
        {
          tabs: paneTabs(slot, slots),
          active: activeItemForSide(slot, slots),
          ...(paneIsPlaceholder(slot, slots) ? {placeholder: true} : {}),
        },
      ])),
    };
  },
  setLayoutSlotsForTest(nextSlots) {
    layoutSlots = normalizeLayoutSlots(nextSlots);
    activeSessions = sessionsFromLayout();
    updateActiveSessionParam();
    return globalThis.__lastUrl;
  },
  syncInitialLayoutUrlForTest() {
    syncInitialLayoutUrl();
    return globalThis.__lastUrl;
  },
  lastUrlForTest() {
    return globalThis.__lastUrl || '';
  },
  setGridPreviewNodesForTest(nodes) {
    grid.querySelectorAll = () => nodes;
  },
  gridForTest() {
    return grid;
  },
  setAppMenuBarRectForTest(rect) {
    renderSessionButtons({force: true});
    const bars = sessionButtons.querySelectorAll('.app-menu-bar');
    for (const bar of bars) bar.rect = rect;
    const buttons = Array.from(sessionButtons.querySelectorAll('.app-menu'))
      .map(menu => menu.querySelector(':scope > .app-menu-button'))
      .filter(Boolean);
    const buttonWidth = buttons.length ? rect.width / buttons.length : 0;
    buttons.forEach((button, index) => {
      button.rect = {
        left: rect.left + (buttonWidth * index),
        right: rect.left + (buttonWidth * (index + 1)),
        top: rect.top,
        bottom: rect.bottom,
        width: buttonWidth,
        height: rect.height,
      };
    });
    return bars.length;
  },
  setAppMenuButtonRectForTest(menuId, rect) {
    renderSessionButtons({force: true});
    const bars = sessionButtons.querySelectorAll('.app-menu-bar');
    for (const bar of bars) {
      bar.rect = {left: 0, right: 500, top: 0, bottom: 32, width: 500, height: 32};
    }
    for (const button of Array.from(sessionButtons.querySelectorAll('.app-menu')).map(menu => menu.querySelector(':scope > .app-menu-button')).filter(Boolean)) {
      button.rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0};
    }
    const menu = Array.from(sessionButtons.querySelectorAll('.app-menu')).find(node => node.dataset.appMenu === menuId);
    const button = menu?.querySelector(':scope > .app-menu-button');
    if (button) button.rect = rect;
    return Boolean(button);
  },
  setLayoutColumnRectsForTest(rects) {
    grid.querySelector = selector => {
      const text = String(selector || '');
      const prefix = '.layout-column[data-slot="';
      const slot = text.startsWith(prefix) && text.endsWith('"]') ? text.slice(prefix.length, -2) : '';
      const rect = slot ? rects[slot] : null;
      if (!rect) return null;
      const node = document.createElement('div');
      node.rect = rect;
      return node;
    };
  },
  defaultLayoutForTest() {
    return globalThis.__layoutTestApi.serialize(defaultLayoutSlots());
  },
  customDragPreviewForTest() {
    return dragState.customPreview;
  },
  clampToViewport,
  createHoverPopoverForTest(options) {
    return createHoverPopover(options);
  },
  httpsWarningForTest() {
    return document.getElementById('httpsWarning');
  },
  bodyClassListForTest() {
    return document.body.classList;
  },
  syncAppViewportBreakpointClassesForTest: syncAppViewportBreakpointClasses,
  documentElementStyleForTest() {
    return document.documentElement.style;
  },
};`, {filename: 'yolomux-test-bundle.js'})).runInContext(context);
  context.document.__listeners = documentListeners;
  const api = context.__layoutTestApi;
  // Mirror production boot: preload the en catalog so t() resolves to English in tests (fetch is
  // disabled here, so the live applyLocale() never runs).
  try {
    api.i18nSetCatalogForTest('en', EN_CATALOG);
  } catch (_) {}
  const setTranscriptInfoForTest = api.setTranscriptInfoForTest;
  api.setTranscriptInfoForTest = (session, info) => setTranscriptInfoForTest(session, canonicalWorkGraphFixture(session, info));
  // Payload-level refresh tests bypass setTranscriptInfoForTest. Normalize their historical fixture
  // rows at the harness boundary too, so lightweight refresh keeps the previous canonical graph.
  for (const name of ['applyTranscriptsPayloadForTest', 'applySessionMetadataPayloadForTest']) {
    const applyPayload = api[name];
    if (typeof applyPayload !== 'function') continue;
    api[name] = (payload, ...args) => {
      if (!payload?.sessions || typeof payload.sessions !== 'object') return applyPayload(payload, ...args);
      const sessions = Object.fromEntries(Object.entries(payload.sessions).map(([session, info]) => [session, canonicalWorkGraphFixture(session, info)]));
      return applyPayload({...payload, sessions}, ...args);
    };
  }
  // Only wrap helpers whose second parameter is transcript metadata.  Tab/popover helpers take
  // different positional arguments, and coercing those values here hid real call-shape bugs.
  for (const name of ['projectMetaHtml', 'paneInfoBarMetaHtml', 'paneInfoBarMetaPartsForTest']) {
    const render = api[name];
    if (typeof render === 'function') api[name] = (session, info, ...args) => {
      const graphInfo = canonicalWorkGraphFixture(session, info);
      return render(session, graphInfo, ...args);
    };
  }
  const cycleSessionRepoDisplayForTest = api.cycleSessionRepoDisplayForTest;
  if (typeof cycleSessionRepoDisplayForTest === 'function') api.cycleSessionRepoDisplayForTest = (session, info, direction) => cycleSessionRepoDisplayForTest(session, canonicalWorkGraphFixture(session, info), direction);
  api.vmConsoleErrorsForTest = () => [...vmConsoleErrors];
  return api;
}

function fileExplorerClosedOptions() {
  return {sessionStorage: {[FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST]: '0'}};
}

function loadYolomuxWithFileExplorerClosed(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64', accessRole = 'admin') {
  return loadYolomux(search, sessions, protocol, navigatorPlatform, accessRole, fileExplorerClosedOptions());
}

function treeKeyEvent(key, target, options = {}) {
  return {
    key,
    target,
    altKey: options.altKey === true,
    ctrlKey: options.ctrlKey === true,
    metaKey: options.metaKey === true,
    shiftKey: options.shiftKey === true,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
    stopImmediatePropagation() { this.immediatePropagationStopped = true; },
  };
}

function tabElement(session, left, width, top = 0) {
  const tab = new TestElement(session);
  tab.dataset.paneTab = session;
  tab.rect = {left, right: left + width, top, bottom: top + 27, width, height: 27};
  return tab;
}

function tabStrip(tabs) {
  const strip = new TestElement('strip');
  strip.children = tabs;
  strip.rect = {left: 100, right: 406, top: 0, bottom: 28, width: 306, height: 28};
  strip.querySelectorAll = selector => {
    assert.equal(selector, '.pane-tab');
    return tabs;
  };
  return strip;
}

function dragEvent(clientX, session = '9') {
  return {
    clientX,
    clientY: 8,
    target: null,
    dataTransfer: {
      dropEffect: '',
      effectAllowed: '',
      dragImage: null,
      types: ['application/x-yolomux-session'],
      getData(type) {
        if (type === 'application/x-yolomux-session') return JSON.stringify({session, sourceSlot: 'right'});
        return '';
      },
      setData(type, value) {
        this[type] = value;
      },
      setDragImage(node, x, y) {
        this.dragImage = {node, x, y};
      },
    },
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
    stopImmediatePropagation() {
      this.propagationStopped = true;
    },
  };
}

function fileDragEvent(target, payload = {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, clientX = 20, clientY = 160) {
  const event = dragEvent(clientX);
  event.clientY = clientY;
  event.target = target;
  event.currentTarget = target;
  event.dataTransfer.types = ['application/x-yolomux-file', 'text/plain'];
  event.dataTransfer.getData = type => {
    if (type === 'application/x-yolomux-file') return JSON.stringify(payload);
    if (type === 'text/plain') return (payload.paths || [payload.path]).filter(Boolean).join('\n');
    return '';
  };
  return event;
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : String(status),
    json: async () => payload,
  };
}

function flushAsyncWork() {
  return new Promise(resolve => setImmediate(resolve));
}

function terminalLine(text, isWrapped = false) {
  return {
    isWrapped,
    translateToString() {
      return text;
    },
  };
}

function nestedSlots(api) {
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 62.5),
    37.5,
  );
  slots.left = api.paneStateWithTabs(['5', '6'], '6');
  slots.slot1 = api.paneStateWithTabs(['1'], '1');
  slots.slot2 = api.paneStateWithTabs(['3'], '3');
  return api.normalizeLayoutSlots(slots);
}

function parseUrl(url) {
  const query = url.split('?')[1] || '';
  return new URLSearchParams(query);
}

function canonical(value) {
  return JSON.parse(JSON.stringify(value));
}

function makeFileTree(paths) {
  const tree = new TestElement('file-tree');
  tree.setAttribute('role', 'tree');
  tree.classList.add('file-explorer-tree-panel');
  tree.rect = {left: 0, top: 0, right: 260, bottom: 160, width: 260, height: 160};
  tree.clientHeight = 160;
  const rows = paths.map((path, index) => {
    const row = new TestElement(`row-${index}`);
    row.classList.add('file-tree-row');
    row.dataset.path = path;
    row.dataset.kind = 'file';
    row.rect = {left: 0, top: index * 20, right: 260, bottom: index * 20 + 18, width: 260, height: 18};
    tree.appendChild(row);
    return row;
  });
  return {tree, rows};
}

let __testPass = 0;
let __testFail = 0;
function test(label, fn) {
  // Per-test isolation: a failing assertion is recorded and the rest of the suite still runs, so one
  // failure no longer hides every later check. A pass/fail summary + non-zero exit print at the end.
  try {
    const result = fn();
    if (result && typeof result.then === 'function') throw new Error('async fn passed to sync test(); use testAsync');
    __testPass++;
  } catch (error) {
    __testFail++;
    // Fail the process IMMEDIATELY on any failing test. The tail .finally also sets this, but it is
    // skipped whenever the trailing async block does not settle (an await that never resolves) \u2014 which
    // silently let red tests exit 0 and pass the cps gate. Setting it here does not depend on the tail.
    process.exitCode = 1;
    console.error(`\u2717 ${label}: ${(error && error.message) || error}`);
  }
}

async function testAsync(label, fn) {
  try {
    await fn();
    __testPass++;
  } catch (error) {
    __testFail++;
    process.exitCode = 1;
    console.error(`\u2717 ${label}: ${(error && error.message) || error}`);
  }
}

// Gate-integrity watchdog: a single hung `await` in the trailing suite block (e.g. an un-flushed batched
// fs listing) used to leave this IIFE unsettled, so its .finally never ran, the event loop drained, and
// node exited 0 with NO summary — silently masking every later test and passing the cps gate. This timer
// runs in the REAL node context (the harness setTimeout stub only affects vm-eval'd bundle code), so it
// both keeps the process alive to detect the hang AND fails loud instead of false-green. Cleared on settle.
const SUITE_WATCHDOG_MS = 60000;
let suiteWatchdog = null;

function startSuiteWatchdog() {
  if (suiteWatchdog) return;
  suiteWatchdog = setTimeout(() => {
    console.error(`\n✗ layout suite DID NOT SETTLE within ${SUITE_WATCHDOG_MS}ms — a trailing await is hung (commonly an un-flushed batched /api/fs/batch listing; drive flushFileExplorerFsBatchForTest). Failing instead of exiting 0.`);
    process.exitCode = 1;
    process.exit(1);
  }, SUITE_WATCHDOG_MS);
}

function finishSuite() {
  if (suiteWatchdog) clearTimeout(suiteWatchdog);
  suiteWatchdog = null;
  console.log(`\nlayout suite: ${__testPass} passed, ${__testFail} failed`);
  if (__testFail) process.exitCode = 1;
}

async function runSuites(suites) {
  startSuiteWatchdog();
  try {
    for (const runSuite of suites) await runSuite();
  } catch (error) {
    // Some historical suites use direct assertions in a trailing async block rather than testAsync.
    // Count that failure too, so the printed summary cannot claim a false all-green result.
    __testFail++;
    console.error(error);
    process.exitCode = 1;
  } finally {
    finishSuite();
  }
}

module.exports = {
  assert,
  fs,
  UI_PINS,
  vm,
  FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST,
  DEFAULT_TEST_SETTINGS,
  TestClassList,
  TestStyle,
  testDatasetKeyForAttribute,
  TestElement,
  TestFile,
  TestFormData,
  assertNoStandalonePrBadge,
  assertSingleCiBadge,
  sourceBetween,
  makeCatalogT,
  deferredFetch,
  apiTransportRetirementScenario,
  settingsOverride,
  loadYolomux,
  fileExplorerClosedOptions,
  loadYolomuxWithFileExplorerClosed,
  treeKeyEvent,
  tabElement,
  tabStrip,
  dragEvent,
  fileDragEvent,
  jsonResponse,
  flushAsyncWork,
  terminalLine,
  nestedSlots,
  parseUrl,
  canonical,
  makeFileTree,
  test,
  testAsync,
  runSuites,
  finishSuite,
};
