let grid = null;
let statusEl = null;
let containersEl = null;
let source = null;
let paused = false;
let lastPayload = null;

function tmuxWallBootstrapPayload() {
  if (globalThis.tmuxWallBootstrap && typeof globalThis.tmuxWallBootstrap === 'object') {
    return globalThis.tmuxWallBootstrap;
  }
  const node = typeof document !== 'undefined' ? document.getElementById('tmux-wall-bootstrap') : null;
  if (!node?.textContent) return {};
  try {
    return JSON.parse(node.textContent);
  } catch (_) {
    return {};
  }
}

const tmuxWallBootstrap = tmuxWallBootstrapPayload();
const tmuxWallCatalog = tmuxWallBootstrap.catalog && typeof tmuxWallBootstrap.catalog === 'object'
  ? tmuxWallBootstrap.catalog
  : {};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function wallText(key, params = {}) {
  const template = String(tmuxWallCatalog[String(key || '')] || key || '');
  return template.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
  ));
}

function messageFieldText(value, field = 'message', fallback = '') {
  const sourceValue = value && typeof value === 'object' ? value : {};
  const name = String(field || 'message');
  const key = String(sourceValue[`${name}_key`] || '');
  const params = sourceValue[`${name}_params`];
  if (key && Object.prototype.hasOwnProperty.call(tmuxWallCatalog, key)) {
    return wallText(key, params && typeof params === 'object' ? params : {});
  }
  return String(sourceValue[name] || fallback || '');
}

function paneTitle(slot) {
  if (!slot.target) return wallText('tmuxWall.pane.empty');
  const p = slot.pane || {};
  const title = p.title ? ` - ${p.title}` : '';
  return slot.target + title;
}

function paneMeta(slot) {
  const p = slot.pane || {};
  const bits = [];
  if (p.command) bits.push(p.command);
  if (p.current_path) bits.push(p.current_path.replace(/^\/home\/[^/]+\//, '~/'));
  return bits.join(' | ');
}

function paneBadgeItems(slot) {
  const display = slot.display || {};
  const badges = [];
  const attention = messageFieldText(
    display.attention_label_key ? display : slot,
    'attention_label',
    display.attention_label || slot.attention_label || '',
  );
  if (attention) {
    badges.push({text: attention, kind: display.attention_kind || slot.attention_kind || 'attention'});
  }
  const reason = messageFieldText(slot, 'reason_label');
  if (reason && !['idle', 'done'].includes(String(slot.reason_code || '')) && reason !== attention) {
    badges.push({text: reason, kind: slot.reason_code || 'attention'});
  }
  const agent = slot.agent_kind || '';
  if (agent) badges.push({text: agent, kind: 'agent'});
  return badges;
}

function paneBadges(slot) {
  return paneBadgeItems(slot)
    .map(badge => `<span class="pane-badge ${esc(badge.kind)}">${esc(badge.text)}</span>`)
    .join('');
}

function paneErrorText(slot) {
  return messageFieldText(slot, 'error');
}

function renderPane(slot) {
  const errorText = paneErrorText(slot);
  const text = errorText && !slot.text ? errorText : slot.text;
  const div = document.createElement('article');
  div.className = 'pane';
  div.innerHTML = `
    <div class="pane-head">
      <div class="pane-head-main">
        <div class="pane-title" title="${esc(paneTitle(slot))}">${esc(paneTitle(slot))}</div>
        <div class="pane-meta" title="${esc(paneMeta(slot))}">${esc(paneMeta(slot))}</div>
      </div>
      <div class="pane-badges">${paneBadges(slot)}</div>
    </div>
    <pre class="term ${errorText ? 'err' : ''}"${slot.error ? ` title="${esc(slot.error)}"` : ''}>${esc(text)}</pre>`;
  return div;
}

function renderContainers(payload) {
  containersEl.innerHTML = '';
  const containers = payload.containers || [];
  if (!containers.length) {
    const diagnostic = String(payload.container_error || '');
    const message = diagnostic
      ? messageFieldText(payload, 'container_error')
      : wallText('tmuxWall.containers.none');
    containersEl.innerHTML = `<tr><td colspan="8" class="${diagnostic ? 'err' : ''}"${diagnostic ? ` title="${esc(diagnostic)}"` : ''}>${esc(message)}</td></tr>`;
    return;
  }
  for (const c of containers) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${esc(c.repo)}</td><td>${esc(c.backend)}</td><td>${esc(c.user)}</td><td>${esc(c.container_id)}</td><td>${esc(c.git_head)}</td><td>${esc(c.project_sha)}</td><td>${esc(c.branch)}</td><td class="path" title="${esc(c.host_path)}">${esc(c.host_path)}</td>`;
    containersEl.appendChild(tr);
  }
}

function render(payload) {
  lastPayload = payload;
  statusEl.textContent = payload.server_time || wallText('tmuxWall.status.live');
  if (payload.tmux_error) {
    statusEl.innerHTML = `<span class="err" title="${esc(payload.tmux_error)}">${esc(messageFieldText(payload, 'tmux_error'))}</span>`;
  }
  grid.replaceChildren(...(payload.slots || []).map(renderPane));
  renderContainers(payload);
}

function connect() {
  if (source) source.close();
  source = new EventSource('/events');
  source.onopen = () => { statusEl.textContent = wallText('tmuxWall.status.connected'); };
  source.onmessage = event => {
    if (!paused) render(JSON.parse(event.data));
  };
  source.onerror = () => {
    statusEl.innerHTML = `<span class="err">${esc(wallText('tmuxWall.status.disconnectedRetrying'))}</span>`;
  };
}

function initializeTmuxWall() {
  grid = document.getElementById('grid');
  statusEl = document.getElementById('status');
  containersEl = document.getElementById('containers');
  const pauseButton = document.getElementById('pauseBtn');
  const refreshButton = document.getElementById('refreshBtn');
  const summaryButton = document.getElementById('summaryBtn');
  if (!grid || !statusEl || !containersEl || !pauseButton || !refreshButton || !summaryButton) return false;

  pauseButton.onclick = () => {
    paused = !paused;
    pauseButton.textContent = wallText(paused ? 'tmuxWall.action.resume' : 'tmuxWall.action.pause');
    if (!paused && lastPayload) render(lastPayload);
  };
  refreshButton.onclick = async () => {
    const response = await fetch('/api/snapshot');
    render(await response.json());
  };
  summaryButton.onclick = () => {
    window.open('/api/summary-input?lines=1200', '_blank');
  };
  connect();
  return true;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    messageFieldText,
    paneBadgeItems,
    paneBadges,
    paneErrorText,
    paneMeta,
    paneTitle,
    wallText,
  };
}

if (typeof document !== 'undefined') initializeTmuxWall();
