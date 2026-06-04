const grid = document.getElementById('grid');
const statusEl = document.getElementById('status');
const containersEl = document.getElementById('containers');
let source = null;
let paused = false;
let lastPayload = null;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

function paneTitle(slot) {
  if (!slot.target) return 'empty';
  const p = slot.pane || {};
  const title = p.title ? ` - ${p.title}` : '';
  return slot.target + title;
}

function paneMeta(slot) {
  const p = slot.pane || {};
  const bits = [];
  if (p.command) bits.push(p.command);
  if (p.current_path) bits.push(p.current_path.replace(/^\/home\/keivenc\//, '~/'));
  return bits.join(' | ');
}

function renderPane(slot) {
  const text = slot.error && !slot.text ? slot.error : slot.text;
  const div = document.createElement('article');
  div.className = 'pane';
  div.innerHTML = `
    <div class="pane-head">
      <div class="pane-title" title="${esc(paneTitle(slot))}">${esc(paneTitle(slot))}</div>
      <div class="pane-meta" title="${esc(paneMeta(slot))}">${esc(paneMeta(slot))}</div>
    </div>
    <pre class="term ${slot.error ? 'err' : ''}">${esc(text)}</pre>`;
  return div;
}

function renderContainers(payload) {
  containersEl.innerHTML = '';
  const containers = payload.containers || [];
  if (!containers.length) {
    const msg = payload.container_error || 'No running Project containers found.';
    containersEl.innerHTML = `<tr><td colspan="8" class="err">${esc(msg)}</td></tr>`;
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
  statusEl.textContent = payload.server_time || 'live';
  if (payload.tmux_error) statusEl.innerHTML = `<span class="err">${esc(payload.tmux_error)}</span>`;
  grid.replaceChildren(...(payload.slots || []).map(renderPane));
  renderContainers(payload);
}

function connect() {
  if (source) source.close();
  source = new EventSource('/events');
  source.onopen = () => { statusEl.textContent = 'connected'; };
  source.onmessage = event => {
    if (!paused) render(JSON.parse(event.data));
  };
  source.onerror = () => {
    statusEl.innerHTML = '<span class="err">disconnected; retrying</span>';
  };
}

document.getElementById('pauseBtn').onclick = () => {
  paused = !paused;
  document.getElementById('pauseBtn').textContent = paused ? 'Resume' : 'Pause';
  if (!paused && lastPayload) render(lastPayload);
};

document.getElementById('refreshBtn').onclick = async () => {
  const response = await fetch('/api/snapshot');
  render(await response.json());
};

document.getElementById('summaryBtn').onclick = () => {
  window.open('/api/summary-input?lines=1200', '_blank');
};

connect();
