// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const shareHostConnections = new Map();
let shareHostPollTimer = null;
let shareManagerRequest = 0;
let shareManagerRendered = 0;

const legacyShareGuestHues = new Map();
const legacyShareGuestPalette = [35, 155, 275, 95, 335];
let refreshLegacyShareCaretColors;

function rememberLegacyShareGuest(name) {
  if (!legacyShareGuestHues.has(name)) {
    legacyShareGuestHues.set(name, legacyShareGuestPalette[legacyShareGuestHues.size % legacyShareGuestPalette.length]);
    refreshLegacyShareCaretColors?.();
  }
}

function shareParticipantHue(name, hue) {
  return Number.isInteger(hue) ? hue : legacyShareGuestHues.get(name) ?? 215;
}

function paintShareCaretLabel(label, caret, scroller) {
  const participantColor = getComputedStyle(caret).color;
  const preview = caret.closest('.file-editor-panel')?.querySelector('.file-editor-preview-pane-panel');
  const backgrounds = [scroller, caret.closest('.cm-editor'), preview].filter(Boolean)
    .map(surface => getComputedStyle(surface).backgroundColor);
  const black = 'rgb(0, 0, 0)';
  const white = 'rgb(255, 255, 255)';
  const inkFor = fill => svgColorContrastRatio(black, fill) >= svgColorContrastRatio(white, fill) ? black : white;
  const fill = backgrounds.includes(participantColor)
    ? [inkFor(participantColor), black, white, 'rgb(118, 118, 118)'].find(color => !backgrounds.includes(color))
    : participantColor;
  label.style.backgroundColor = fill;
  label.style.color = inkFor(fill);
}

function installShareCaretLabels() {
  const editorGrid = document.getElementById('grid');
  if (!editorGrid) return;
  const labels = new Map();
  let scrollers = new Set();
  let frame = 0;
  function render() {
    frame = 0;
    const carets = new Set(editorGrid.querySelectorAll('.share-remote-caret'));
    for (const [caret, label] of labels) {
      if (carets.has(caret)) continue;
      label.remove();
      labels.delete(caret);
    }
    const currentScrollers = new Set();
    const edgeGap = carets.size ? popoverEdgeGapPx() : 0;
    const gap = carets.size ? rootCssLengthPx('--space-6') : 0;
    const bounds = carets.size ? viewportBounds(edgeGap) : null;
    const occupied = [];
    for (const caret of carets) {
      const name = caret.dataset.shareCaretName || caret.title;
      const scroller = caret.closest('.cm-scroller');
      if (!name || !scroller) continue;
      currentScrollers.add(scroller);
      const anchor = caret.getBoundingClientRect();
      const viewport = scroller.getBoundingClientRect();
      const caretStyle = getComputedStyle(caret);
      const fontSize = parseFloat(caretStyle.fontSize);
      const visible = caret.getClientRects().length && anchor.left >= viewport.left && anchor.left <= viewport.right
        && anchor.top >= viewport.top && anchor.top - fontSize <= viewport.bottom;
      let label = labels.get(caret);
      if (!visible) {
        if (label) label.hidden = true;
        continue;
      }
      if (!label) {
        label = document.createElement('span');
        label.className = 'share-caret-label';
        label.textContent = name;
        if (!caret.dataset.shareCaretName) {
          caret.dataset.shareCaretName = name;
          caret.dataset.shareLegacyColor = '';
          caret.removeAttribute('title');
          caret.style.removeProperty('color');
        }
        appOverlayRootElement().appendChild(label);
        labels.set(caret, label);
      }
      label.hidden = false;
      if (caret.hasAttribute('data-share-legacy-color')) {
        caret.style.setProperty('--share-participant-hue', shareParticipantHue(name));
      }
      paintShareCaretLabel(label, caret, scroller);
      const rightRoom = bounds.right - anchor.left - gap;
      const leftRoom = anchor.left - gap - bounds.left;
      label.style.setProperty('--share-caret-label-room', `${Math.max(0, rightRoom, leftRoom)}px`);
      const {width, height} = label.getBoundingClientRect();
      const left = width <= rightRoom ? anchor.left + gap : anchor.left - gap - width;
      const halfLine = (parseFloat(caretStyle.lineHeight) || fontSize) / 2;
      const caretCenter = anchor.top - .4 * fontSize;
      const view = window.YOmuxCodeMirror?.EditorView.findFromDOM(caret.closest('.cm-editor'));
      const doc = view?.state.doc;
      const offset = doc?.lines > 1 ? view.posAtDOM(caret) : null;
      const end = offset !== null && doc.lineAt(offset).number === doc.lines
        ? view.coordsAtPos(doc.length) : null;
      const row = end && view.coordsAtPos(offset);
      const lastVisualLine = row && Math.abs(row.top - end.top) < halfLine;
      const roomBelow = caretCenter + halfLine + height / 2 <= Math.min(viewport.bottom, bounds.bottom);
      const top = caretCenter + (lastVisualLine || !roomBelow ? -halfLine : halfLine) - height / 2;
      const position = clampToViewport(left, top, width, height, {edgeGap});
      for (const other of occupied) {
        if (position.left < other.right && position.left + width > other.left
            && position.top < other.bottom && position.top + height > other.top) {
          position.top = clampToViewport(position.left, other.bottom + gap, width, height, {edgeGap}).top;
        }
      }
      occupied.push({left: position.left, right: position.left + width, top: position.top, bottom: position.top + height});
      label.style.left = `${position.left}px`;
      label.style.top = `${position.top}px`;
    }
    for (const scroller of scrollers) if (!currentScrollers.has(scroller)) resize.unobserve(scroller);
    for (const scroller of currentScrollers) if (!scrollers.has(scroller)) resize.observe(scroller);
    scrollers = currentScrollers;
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(render); };
  refreshLegacyShareCaretColors = schedule;
  const resize = new ResizeObserver(schedule);
  const containsCaret = node => node.nodeType === Node.ELEMENT_NODE
    && (node.matches('.share-remote-caret') || node.querySelector('.share-remote-caret'));
  const mutations = new MutationObserver(records => {
    if (scrollers.size || records.some(record => Array.from(record.addedNodes).some(containsCaret))) schedule();
  });
  mutations.observe(editorGrid, {childList: true, subtree: true});
  mutations.observe(document.body, {attributes: true, attributeFilter: ['class', 'style']});
  const reposition = () => { if (scrollers.size) schedule(); };
  editorGrid.addEventListener('scroll', reposition, {capture: true, passive: true});
  window.addEventListener('resize', reposition, {passive: true});
  schedule();
}

function shareHostFileState(path) {
  const state = fileState.get(path);
  if (!state) return;
  const pending = fileEditorPanelsForPath(path).filter(panel => panel._shareConnection?.pending() > 0);
  const owner = pending.includes(state.contentOwnerPanel) ? state.contentOwnerPanel : pending[0];
  if (owner) state.content = owner._cmView.state.doc.toString();
  state.dirty = pending.length > 0 || state.content !== state.original;
}

function shareHostRenderPreview(panel, path) {
  const text = fileEditorPanelState(panel).content;
  renderFileEditorPreviewSurface(panel, panel.querySelector('.file-editor-preview-pane-panel'), path, text);
  renderLinkedFilePreviewPanels(panel, path, text);
}

function shareHostLocalChanged(panel, path) {
  const state = fileEditorPanelState(panel);
  if (!state || !panel._cmView) return;
  state.content = panel._cmView.state.doc.toString();
  state.contentOwnerPanel = panel;
  shareHostFileState(path);
  updateFileEditorPanelChrome(panel, path);
  shareHostRenderPreview(panel, path);
}

function disconnectShareEditor(panel) {
  panel._shareConnection?.stop();
  panel._shareConnection = null;
  shareHostConnections.delete(panel);
}

function disconnectShareEditorsForPath(path) {
  for (const [panel, entry] of shareHostConnections) {
    if (entry.path === path) disconnectShareEditor(panel);
  }
}

function shareHostEndpoint(path) {
  return `/api/share/document?path=${encodeURIComponent(path)}`;
}

async function shareAttachHostPanel(panel, path) {
  if (typeof bootstrap !== 'undefined' && bootstrap.guestWorkspace) return shareAttachGuestPanel(panel, path);
  if (!window.YOmuxShare || !panel?._cmView || panel._shareConnection || panel._shareConnecting) return;
  const state = fileEditorPanelState(panel);
  if (state?.historical || !state?._shareActive) return;
  panel._shareConnecting = true;
  try {
    const view = panel._cmView, generation = state._shareGeneration;
    const alive = () => panel._cmView === view;
    const current = () => alive() && state._shareActive && state._shareGeneration === generation;
    const connection = await window.YOmuxShare.connect(view, shareHostEndpoint(path), {
      alive, current,
      onState(body, text) {
        if (!current()) return;
        state.content = text;
        if (body.saved) { state.original = text; state.dirty = false; }
        shareHostFileState(path);
        state._sharePaused = body.paused;
        panel._cmSyncing = true;
        try { shareHostRenderPreview(panel,path); } finally { panel._cmSyncing = false; }
        updateFileEditorPanelChrome(panel, path);
        setFileEditorPanelStatus(panel, body.paused ? `Sharing: saving paused (${body.paused}). Resolve in YO!share.` : 'Shared editing', body.paused ? 'error' : 'ok');
      },
      onError(error) { if (current()) setFileEditorPanelStatus(panel, error.message, 'error'); },
      onEnd() { if (current()) setFileEditorPanelStatus(panel, 'Sharing ended. Pending local edits remain in this editor.', 'warn'); },
    });
    if (!current()) connection?.stop();
    else { panel._shareConnection = connection; shareHostConnections.set(panel, {path, connection}); }
  } finally { panel._shareConnecting = false; }
}

async function shareRefreshHostEditors() {
  if (typeof bootstrap !== 'undefined' && bootstrap.guestWorkspace) return;
  if (readOnlyMode || !window.YOmuxShare) return;
  try {
    const version = ++shareManagerRequest;
    const body = await window.YOmuxShare.request('/api/share');
    if (version < shareManagerRendered) return;
    updateShareManager(body, version);
    const paths = new Set(body.documents);
    for (const [path, state] of fileState) {
      const active = paths.has(path);
      const generation = body.document_generations[path];
      if (active && state._shareGeneration && state._shareGeneration !== generation) {
        shareHostFileState(path);
        disconnectShareEditorsForPath(path);
        state._shareActive = false;
      }
      state._shareGeneration=generation;
      if (active && !state._shareActive) {
        syncOpenFileContentFromPanels(path, state.contentOwnerPanel);
        if (state.dirty) {
          setFileEditorPanelStatus(state.contentOwnerPanel, 'Save the host draft before joining shared editing.', 'error');
          continue;
        }
        state._shareActive = true;
        clearFileAutosaveTimer(path);
        for (const panel of fileEditorPanelsForPath(path)) {
          destroyProseMirrorPanel(panel);
          delete panel._pmRequired;
          const item = fileEditorPanelItem(panel);
          setFileEditorViewMode(path, 'edit', item);
        }
      } else if (!active && state._shareActive) {
        state._shareActive = false;
        disconnectShareEditorsForPath(path);
        if (!state.dirty) await reloadOpenFileFromDisk(path, {force:true});
        else setFileEditorPanelStatus(state.contentOwnerPanel, 'Sharing ended. Save or resolve the retained local draft.', 'warn');
      }
      if (active) for (const panel of fileEditorPanelsForPath(path)) await shareAttachHostPanel(panel, path);
    }
  } catch(error) { console.error('YO!share host synchronization failed', error); }
  finally { shareHostPollTimer = setTimeout(shareRefreshHostEditors, 1501); }
}

function shareHostSavedContent(path) {
  const state = fileState.get(path);
  if (!state) throw new Error(`Open and save ${path} before enabling write access.`);
  syncOpenFileContentFromPanels(path, state.contentOwnerPanel);
  if (state.dirty) throw new Error(`Save ${path} before enabling write access.`);
  return state.content;
}

function shareManagerButton(label, onClick) {
  return makeButton({label, ariaLabel: label, onClick, className: 'file-editor-dialog-action'});
}

function shareInviteCopyButton(link, guest, status) {
  const copy = shareManagerButton(`Copy URL to share w/ ${guest}`, async () => {
    try { await navigator.clipboard.writeText(link); } catch (error) { status.textContent = error.message; }
  });
  copy.classList.add('file-editor-primary-action');
  return copy;
}

function shareStopButton(state, id) {
  const stop = shareManagerButton('Stop Sharing', async event => {
    event.preventDefault();
    try {
      await window.YOmuxShare.request('/api/share?id=' + encodeURIComponent(id), {method: 'DELETE'});
      await refreshShareManager();
    } catch (error) { state.status.textContent = error.message; }
  });
  stop.classList.add('share-stop-action');
  return stop;
}

function createShareManager() {
  const panel = createFramedPanel({
    item: shareItemId, className: 'panel share-manager',
    frame: {controlsHtml: virtualPanelInnerControlsHtml(shareItemId), bodyClass: 'share-manager-body',
      bodyHtml: `<section class="share-manager-active" hidden><h2>Active shares</h2><div class="share-manager-list"></div></section>
        <details class="share-create-disclosure"><summary>${esc(t('sharing.newShare'))}</summary></details>
        <p role="status"></p>
        <details class="share-manager-recovery" hidden><summary>Recovery drafts</summary><div></div></details>`},
  });
  const root = panel.querySelector('.share-manager-body');
  const state = panel._shareManager = {root, grants: [], hasLoadedShares: false, paths: {}, peers: {}, links: new Map(), rows: new Map(),
    create: root.querySelector('.share-create-disclosure'),
    status: root.querySelector('[role=status]'), list: root.querySelector('.share-manager-list'),
    recovery: root.querySelector('.share-manager-recovery')};
  const {create} = state;
  create.addEventListener('toggle', () => {
    if (!create.open) return;
    if (state.createPicker) state.createPicker.refresh();
    else buildShareCreateForm(state, create);
  });
  state.recovery.addEventListener('toggle', () => { if (state.recovery.open) refreshShareRecovery(state); });
  void refreshShareRecovery(state);
  return panel;
}

async function refreshShareManager() {
  const state = panelNodes.get(shareItemId)?._shareManager;
  if (!state || !window.YOmuxShare || readOnlyMode) return;
  const version = ++shareManagerRequest;
  try { updateShareManager(await window.YOmuxShare.request('/api/share?invites=1'), version); }
  catch (error) { if (version >= shareManagerRendered) state.status.textContent = error.message; }
}

function shareTable(columns) {
  const table = document.createElement('table');
  table.className = 'share-admin-table';
  const heading = table.createTHead().insertRow();
  for (const label of columns) {
    const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; heading.appendChild(cell);
  }
  return {table, rows: table.createTBody()};
}

function shareTableRow(rows, cells) {
  const row = rows.insertRow();
  for (const value of cells) row.insertCell().append(value);
  return row;
}

function shareTimeLeft(expires) {
  const seconds = Math.max(0, Math.ceil((Date.parse(expires) - Date.now()) / 1000));
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function sharePickerCandidates(grant = null, paths = {}) {
  const candidates = new Map();
  function add(resource, label, canWrite, selected = false) {
    const key = resource.kind === 'file' ? 'file:' + resource.path : 'terminal:' + resource.pane;
    if (!candidates.has(key)) candidates.set(key, {key, resource, label, canWrite, selected});
  }
  for (const resource of grant?.resources || []) {
    add({id: resource.id, kind: resource.kind, path: paths[resource.id], pane: resource.pane,
      writable: resource.writable}, resource.label, resource.can_write, true);
  }
  for (const item of layoutItems) {
    const path = fileItemPath(item);
    if (path) {
      const state = fileEditorStateForItem(path, item);
      if (state?.historical || !['text', 'image', 'pdf', 'audio', 'video'].includes(state?.kind)) continue;
      add({kind: 'file', path, writable: false}, path, state.kind === 'text' && fileEditorStateWritable(state), !grant);
    } else if (isTmuxSession(item)) {
      const target = transcriptMetadataState.payload?.sessions?.[item]?.selected_pane;
      const pane = [terminalContextMenuPaneTargetForContainer(terminals.get(item)?.container),
        terminalContextMenuPaneTargetForSession(item), target?.pane_id].find(value => /^%\d+$/.test(value));
      if (pane) add({kind: 'terminal', pane, writable: false}, `${item} ${pane}`, true);
    }
  }
  const group = choice => choice.resource.kind === 'terminal' ? 2 : choice.canWrite ? 0 : 1;
  return [...candidates.values()].sort((a, b) => group(a) - group(b) || a.label.localeCompare(b.label));
}

function shareTabPicker(grant = null, paths = {}) {
  const root = document.createElement('div'); root.className = 'share-admin-picker';
  const {table, rows} = shareTable([t('common.tabsLabel'), 'W']);
  table.classList.add('share-permission-table'); root.appendChild(table);
  let choices = [];
  function addChoice(choice, previous) {
    const selection = document.createElement('label');
    const selected = document.createElement('input'); selected.type = 'checkbox';
    selected.checked = previous?.selected.checked ?? choice.selected;
    selected.setAttribute('aria-label', t('sharing.resource', {label: choice.label}));
    const name = document.createElement('span'); name.textContent = choice.label; selection.append(selected, name);
    const write = document.createElement('input'); write.type = 'checkbox';
    write.checked = choice.canWrite && (previous?.write.checked ?? choice.resource.writable);
    write.setAttribute('aria-label', t('sharing.permission', {label: 'W: ' + choice.label}));
    write.dataset.shareResource = choice.label;
    const row = shareTableRow(rows, [selection, write]);
    write.disabled = !choice.canWrite || !selected.checked;
    selected.addEventListener('change', () => { write.disabled = !choice.canWrite || !selected.checked; });
    return {...choice, selected, write, row};
  }
  function refresh() {
    const previous = new Map(choices.map(choice => [choice.key, choice]));
    choices = sharePickerCandidates(grant, paths).map(choice => {
      const entry = previous.get(choice.key);
      previous.delete(choice.key);
      if (entry?.label === choice.label && entry.canWrite === choice.canWrite) return entry;
      entry?.row.remove();
      return addChoice(choice, entry);
    });
    for (const choice of previous.values()) choice.row.remove();
    choices.forEach(({row}, index) => {
      if (rows.children[index] !== row) rows.insertBefore(row, rows.children[index] || null);
    });
  }
  refresh();
  return {root, refresh, get choices() { return choices; }, resources() {
    return choices.filter(choice => choice.selected.checked).map(choice => {
      const resource = {...choice.resource, writable: choice.canWrite && choice.write.checked};
      if (resource.kind === 'file' && resource.writable && (!resource.id || !choice.resource.writable)) {
        resource.saved_content = shareHostSavedContent(resource.path);
      }
      return resource;
    });
  }};
}

function updateShareManager(body, version) {
  if (version < shareManagerRendered) return;
  shareManagerRendered = version;
  for (const peers of Object.values(body.peers || {})) {
    for (const peer of peers) if (!Number.isInteger(peer.hue)) rememberLegacyShareGuest(peer.name);
  }
  void refreshHostShareChats(body.shares, version);
  const state = panelNodes.get(shareItemId)?._shareManager;
  if (!state) return;
  const becameEmpty = body.shares.length === 0 && (!state.hasLoadedShares || state.grants.length > 0);
  state.hasLoadedShares = true;
  if (body.invite_urls) state.links = new Map(Object.entries(body.invite_urls));
  state.grants = body.shares; state.paths = body.resource_paths; state.peers = body.peers;
  renderShareManager(state);
  if (becameEmpty) state.create.open = true;
  void refreshShareRecovery(state);
}

function publishShareManagerGrant(state, grant) {
  shareManagerRendered = ++shareManagerRequest;
  state.grants = [...state.grants.filter(item => item.id !== grant.id), grant];
  renderShareManager(state);
}

function renderShareManager(state) {
  if (state.create.open) state.createPicker?.refresh();
  const active = new Set(state.grants.map(grant => grant.id));
  state.root.querySelector('.share-manager-active').hidden = active.size === 0;
  for (const [id, row] of state.rows) if (!active.has(id)) { row.remove(); state.rows.delete(id); state.links.delete(id); }
  for (const grant of state.grants) {
    let row = state.rows.get(grant.id);
    if (!row) {
      row = document.createElement('details'); row.className = 'share-manager-disclosure'; row.dataset.shareId = grant.id;
      const summary = document.createElement('summary');
      const name = document.createElement('span'); name.dataset.shareName = '';
      const count = document.createElement('span'); count.dataset.shareCount = '';
      const indicator = document.createElement('span');
      indicator.className = 'share-connected-indicator';
      indicator.innerHTML = agentWindowStatusDotHtmlForTone(STATE_KEY.working, {surface: 'subwindow', pulse: true});
      const countdown = document.createElement('span'); countdown.dataset.expiry = '';
      const stop = shareStopButton(state, grant.id); stop.classList.add('share-summary-stop');
      summary.append(name, count, indicator, countdown, stop); row.appendChild(summary);
      row.addEventListener('toggle', () => {
        if (row.open && !state.links.has(grant.id)) void refreshShareManager();
      });
      const detail = document.createElement('section'); detail.className = 'share-manager-detail'; row.appendChild(detail);
      state.rows.set(grant.id, row); state.list.appendChild(row);
    }
    row.querySelector('[data-share-name]').textContent = grant.guest;
    const peers = state.peers[grant.id] || [];
    const connectedPeers = new Set(peers.filter(peer => Date.now() - Date.parse(peer.last_seen) <= 5000));
    row.querySelector('[data-share-count]').textContent = t('sharing.guestCount', {claimed: connectedPeers.size, limit: grant.limit});
    row.querySelector('.share-connected-indicator').hidden = connectedPeers.size === 0;
    row.querySelector('[data-expiry]').textContent = shareTimeLeft(grant.expires);
    const summary = row.querySelector('summary'), link = state.links.get(grant.id);
    const previousInvite = summary.querySelector('.share-invite');
    if (previousInvite?.dataset.url !== link) {
      previousInvite?.remove();
      if (link) {
        const invite = document.createElement('span'); invite.className = 'share-invite share-manager-actions';
        invite.dataset.url = link;
        const input = document.createElement('input'); input.type = 'url'; input.readOnly = true; input.value = link;
        input.setAttribute('aria-label', t('sharing.url'));
        invite.append(shareInviteCopyButton(link, grant.guest, state.status), input);
        summary.appendChild(invite);
      }
    }
    const detail = row.querySelector('.share-manager-detail');
    const detailKey = JSON.stringify([grant.revision, grant.resources]);
    if (detail.dataset.key !== detailKey) {
      const focused = detail.contains(document.activeElement) ? document.activeElement.getAttribute('aria-label') : null;
      detail.dataset.key = detailKey; detail.replaceChildren(); buildShareManagerDetails(state, grant, detail);
      if (focused) [...detail.querySelectorAll('[aria-label]')].find(node => node.getAttribute('aria-label') === focused)?.focus();
    }
    detail.querySelector('input[name=limit]').min = String(Math.max(1, grant.claimed));
    const guests = detail.querySelector('[data-share-peers]');
    const peerKey = JSON.stringify(peers.map(peer => [peer.name, peer.ip, peer.browser, peer.browser_version, connectedPeers.has(peer)]));
    if (guests.dataset.key !== peerKey) {
      guests.dataset.key = peerKey;
      const {table, rows} = shareTable(['Guest', 'IP address', 'Browser', 'Version']);
      for (const peer of peers) {
        const address = document.createElement('span'); address.className = 'share-peer-address'; address.textContent = peer.ip;
        if (connectedPeers.has(peer)) {
          const connected = shareManagerButton(t('sharing.connected'), () => {}); connected.disabled = true; connected.classList.add('share-peer-connected');
          connected.textContent = t('sharing.connected'); connected.setAttribute('aria-label', t('sharing.connected'));
          address.appendChild(connected);
        }
        shareTableRow(rows, [peer.name, address, peer.browser || 'Unknown', peer.browser_version || 'Unknown']);
      }
      guests.replaceChildren(peers.length ? table : 'No guests have joined.');
    }
  }
}

function relocalizeShareManager(panel) {
  const summary = panel.querySelector('.share-create-disclosure > summary');
  if (summary) summary.textContent = t('sharing.newShare');
  const state = panel?._shareManager;
  if (!state?.peers) return;
  for (const row of state.rows.values()) row.querySelector('.share-manager-detail').dataset.key = '';
  renderShareManager(state);
}

function buildShareManagerDetails(state, grant, detail) {
  const api = window.YOmuxShare, actions = document.createElement('div'); actions.className = 'share-manager-actions';
  const stop = shareStopButton(state, grant.id);
  const guests = document.createElement('div'); guests.dataset.sharePeers = '';
  const picker = shareTabPicker(grant, state.paths);
  const applyPicker = async () => {
    const controls = [...detail.querySelectorAll('input:not(:disabled)')];
    try {
      const resources = picker.resources();
      const newlyWritableTerminal = resources.some(resource => resource.kind === 'terminal' && resource.writable &&
        !grant.resources.some(previous => previous.id === resource.id && previous.writable));
      controls.forEach(input => { input.disabled = true; });
      await api.request('/api/share?id=' + encodeURIComponent(grant.id), {method: 'PATCH',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: grant.revision, resources,
          terminal_write_confirmed: newlyWritableTerminal})});
      state.status.textContent = ''; await refreshShareManager();
    } catch (error) {
      state.status.textContent = error.message;
      detail.dataset.key = '';
      renderShareManager(state);
      await refreshShareManager();
    }
    finally { controls.forEach(input => { if (input.isConnected) input.disabled = false; }); }
  };
  for (const choice of picker.choices) {
    choice.selected.addEventListener('change', () => {
      choice.write.disabled = !choice.selected.checked || !choice.canWrite;
      void applyPicker();
    });
    choice.write.addEventListener('change', applyPicker);
  }
  const maxMinutes = bootstrap.shareMaxMinutes;
  const expiryMinutes = Math.max(1, Math.min(maxMinutes, Math.ceil((Date.parse(grant.expires) - Date.now()) / 60000)));
  const settingsStatus = document.createElement('p');
  settingsStatus.setAttribute('role', 'status');
  const settings = [
    ['minutes', 'Expires in (minutes)', t('sharing.expiryMinutes'), expiryMinutes, 1, maxMinutes],
    ['limit', 'Guest limit', 'Guest limit', grant.limit, Math.max(1, grant.claimed), 1024],
  ].map(([name, label, accessibleLabel, value, min, max]) => {
    const row = document.createElement('label'); row.className = 'share-expiry-control';
    const input = document.createElement('input'); input.type = 'number'; input.name = name;
    input.min = String(min); input.max = String(max); input.step = '1'; input.required = true; input.value = String(value);
    input.setAttribute('aria-label', accessibleLabel);
    input.addEventListener('change', async () => {
      if (!input.reportValidity()) return;
      const controls = [...detail.querySelectorAll('input:not(:disabled)')];
      controls.forEach(control => { control.disabled = true; });
      try {
        const updated = await api.request('/api/share?id=' + encodeURIComponent(grant.id), {method: 'PATCH',
          headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: grant.revision, [name]: Number(input.value)})});
        publishShareManagerGrant(state, updated);
        void refreshShareManager();
      } catch (error) {
        input.value = String(value);
        settingsStatus.textContent = error.message === 'guest_limit_below_claimed'
          ? 'Guest limit cannot be lower than the number of claimed slots.' : error.message;
        void refreshShareManager();
      } finally { controls.filter(control => control.isConnected).forEach(control => { control.disabled = false; }); }
    });
    row.append(label, input);
    return row;
  });
  const topActions = document.createElement('div'); topActions.className = 'share-manager-actions share-detail-controls';
  topActions.append(...settings, stop, settingsStatus);
  for (const resource of grant.resources) {
    const path = state.paths[resource.id];
    if (path && /\.(tex|ltx)$/i.test(path)) actions.appendChild(shareManagerButton('Publish PDF: ' + resource.label, () => publishSharedTeX(state, path)));
  }
  detail.append(topActions, guests, picker.root, actions);
}

const hostShareConversations = new Map();
let hostShareChatRoot = null;
let hostShareChatSelected = null;

function createShareSidebar() {
  const sidebar = document.createElement('section');
  sidebar.className = 'share-sidebar-conversations';
  const handle = document.createElement('div');
  handle.className = 'share-sidebar-resizer';
  handle.tabIndex = 0;
  handle.title = t('pane.resize');
  handle.setAttribute('role', 'separator');
  handle.setAttribute('aria-orientation', 'horizontal');
  handle.setAttribute('aria-label', handle.title);
  handle.setAttribute('aria-valuemin', '25');
  handle.setAttribute('aria-valuemax', '75');
  handle.setAttribute('aria-valuenow', '40');
  const resize = percent => {
    const size = Math.min(75, Math.max(25, Math.round(percent * 10) / 10));
    sidebar.style.setProperty('--share-sidebar-size', size + '%');
    handle.setAttribute('aria-valuenow', String(size));
  };
  bindFileExplorerRowResizer(handle, sidebar, (event, grabOffset) => {
    const pane = sidebar.parentElement.getBoundingClientRect();
    if (pane.height > 0) resize((pane.bottom - event.clientY + grabOffset) * 100 / pane.height);
  });
  handle.addEventListener('keydown', event => {
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
    event.preventDefault();
    event.stopPropagation();
    resize(Number(handle.getAttribute('aria-valuenow')) + (event.key === 'ArrowUp' ? 5 : -5));
  });
  sidebar.appendChild(handle);
  return sidebar;
}

function shareSidebarChat(container, {id, label, endpoint, keepOpenWhileActive = false}) {
  const standalone = container.classList.contains('file-explorer-pane');
  const sidebar = standalone ? container.appendChild(createShareSidebar()) : container;
  const root = document.createElement('details'); root.className = 'share-sidebar-chat'; root.open = true;
  root.dataset.shareChat = id; root.setAttribute('aria-label', 'YO!Chat');
  const summary = document.createElement('summary'); summary.textContent = 'YO!Chat';
  const status = document.createElement('span'); status.className = 'share-chat-status'; summary.appendChild(status);
  const log = document.createElement('div'); log.setAttribute('role', 'log');
  const form = document.createElement('form'), input = document.createElement('textarea'); input.maxLength = 4096;
  input.rows = 2; input.className = 'share-chat-input';
  input.setAttribute('aria-label', t('sharing.messageTo', {guest: label}));
  const send = shareManagerButton('Send', () => {}); send.type = 'submit';
  form.append(input, send); root.append(summary, log, form); sidebar.appendChild(root);
  let live = true, sending = false, messages = '';
  if (keepOpenWhileActive) {
    summary.setAttribute('aria-disabled', 'true');
    summary.tabIndex = -1;
    summary.addEventListener('click', event => event.preventDefault());
  }
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault(); form.requestSubmit();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || !live || sending) return;
    sending = send.disabled = true;
    try {
      const body = await window.YOmuxShare.post(endpoint, {text});
      if (live && input.value.trim() === text) input.value = '';
      update(body.messages);
    } catch (error) { if (live) status.textContent = error.message; }
    finally { sending = false; send.disabled = !live; }
  });
  function update(incoming) {
    if (!live || !incoming) return;
    status.textContent = '';
    const text = JSON.stringify(incoming.map(message => [message.name, message.text, shareParticipantHue(message.name, message.hue)]));
    if (text === messages) return;
    const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight <= 2;
    const fragment = document.createDocumentFragment();
    for (const message of incoming) {
      const row = document.createElement('div'); row.className = 'share-chat-message';
      const author = document.createElement('span'); author.className = 'share-chat-author';
      author.textContent = message.name + ': ';
      author.style.setProperty('--share-participant-hue', shareParticipantHue(message.name, message.hue));
      row.append(author, document.createTextNode(message.text)); fragment.appendChild(row);
    }
    log.replaceChildren(fragment); messages = text; if (atEnd) log.scrollTop = log.scrollHeight;
  }
  return {root, update, close() { live = false; (standalone ? sidebar : root).remove(); }};
}

function removeHostShareChat(entry) {
  entry.chat.close();
  hostShareConversations.delete(entry.id);
}

async function refreshHostShareChats(grants, version) {
  const requests = [];
  const active = new Set(grants.map(grant => grant.id));
  for (const entry of hostShareConversations.values()) {
    if (!active.has(entry.id)) removeHostShareChat(entry);
  }
  for (const grant of grants) {
    let entry = hostShareConversations.get(grant.id);
    if (!entry) {
      if (!hostShareChatRoot) {
        hostShareChatRoot = createShareSidebar();
        const select = document.createElement('select'); select.setAttribute('aria-label', t('sharing.conversation'));
        select.addEventListener('change', () => { hostShareChatSelected = select.value; renderHostShareChats(); });
        hostShareChatRoot.appendChild(select);
      }
      const endpoint = '/api/share/chat?id=' + encodeURIComponent(grant.id);
      entry = {id: grant.id, label: grant.guest, endpoint,
        chat: shareSidebarChat(hostShareChatRoot, {id: grant.id, label: grant.guest, endpoint})};
      hostShareConversations.set(grant.id, entry); hostShareChatSelected = grant.id;
    }
    if (entry.loading) continue;
    entry.loading = true;
    requests.push((async () => {
      try {
        const body = await window.YOmuxShare.request(entry.endpoint);
        entry.chat.update(body.messages);
      } catch (error) {
        if (error.status !== 410) console.error('YO!Chat refresh failed', error);
        else if (version === shareManagerRendered && hostShareConversations.get(grant.id) === entry) {
          removeHostShareChat(entry);
          renderHostShareChats();
        }
      } finally { entry.loading = false; }
    })());
  }
  renderHostShareChats();
  await Promise.all(requests);
}

function renderHostShareChats() {
  if (!hostShareChatRoot) return;
  if (!hostShareConversations.size) {
    hostShareChatRoot.remove();
    hostShareChatRoot = null;
    hostShareChatSelected = null;
    return;
  }
  if (!hostShareConversations.has(hostShareChatSelected)) hostShareChatSelected = hostShareConversations.keys().next().value;
  const container = [...document.querySelectorAll('.file-explorer-panel .file-explorer-pane')]
    .find(node => node.getClientRects().length > 0);
  if (container && hostShareChatRoot.parentElement !== container) container.appendChild(hostShareChatRoot);
  const select = hostShareChatRoot.querySelector('select');
  const key = JSON.stringify([...hostShareConversations.values()].map(entry => [entry.id, entry.label]));
  if (select.dataset.key !== key) {
    select.dataset.key = key;
    select.replaceChildren(...[...hostShareConversations.values()].map(entry => {
      const option = document.createElement('option'); option.value = entry.id; option.textContent = entry.label; return option;
    }));
  }
  select.hidden = hostShareConversations.size < 2; select.value = hostShareChatSelected;
  for (const entry of hostShareConversations.values()) entry.chat.root.hidden = entry.id !== hostShareChatSelected;
}

async function publishSharedTeX(state, path) {
  try {
    const file = fileState.get(path);
    if (!file) throw new Error('Open the source file before publishing.');
    syncOpenFileContentFromPanels(path, file.contentOwnerPanel);
    const response = await fetch('/api/tex/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path, content: file.content, publish: true})});
    if (!response.ok || !response.headers.get('Content-Type')?.includes('application/pdf')) throw new Error((await response.json()).error || 'TeX compilation failed');
    state.status.textContent = t('sharing.published');
  } catch (error) { state.status.textContent = error.message; }
}

async function refreshShareRecovery(state) {
  if (state.loadingRecovery) return;
  state.loadingRecovery = true;
  const api = window.YOmuxShare;
  try {
    const body = await api.request('/api/share/recovery');
    const key = JSON.stringify(body.drafts);
    if (state.recoveryKey === key) return;
    state.recoveryKey = key;
    const container = state.recovery.querySelector('div');
    state.recovery.hidden = body.drafts.length === 0;
    if (!body.drafts.length) container.replaceChildren();
    else container.replaceChildren(...body.drafts.map(draft => {
      const row = document.createElement('details');
      const summary = document.createElement('summary');
      const source = document.createElement('pre');
      summary.textContent = draft.path;
      source.textContent = draft.text;
      row.append(summary, source, shareManagerButton('Replace disk content with shared draft', async () => {
        if (!window.confirm(t('sharing.confirmRestore', {path: draft.path}))) return;
        try {
          await api.post('/api/share/recovery', {path: draft.path, action: 'restore'});
          await refreshShareRecovery(state);
        } catch (error) { state.status.textContent = error.message; }
      }));
      return row;
    }));
  } catch (error) { state.status.textContent = error.message; }
  finally { state.loadingRecovery = false; }
}

function buildShareCreateForm(state, disclosure) {
  const api = window.YOmuxShare;
  if (!api || readOnlyMode) return;
  const anchor = disclosure.querySelector('summary');
  const body = document.createElement('div'); body.className = 'share-admin share-admin-body';
  const description = document.createElement('p');
  description.textContent = t('sharing.createHelp');
  body.appendChild(description);
  const fields = {};
  for (const [key, label, value, type] of [['guest', 'Guest name', api.guestName(), 'text'],
    ['minutes', 'Duration (minutes)', 30, 'number'], ['limit', 'Guest limit', 1, 'number']]) {
    const row = document.createElement('label'); row.textContent = label;
    const input = document.createElement('input'); input.type = type; input.value = String(value); input.name = key;
    if (type === 'number') { input.min = '1'; input.step = '1'; if (key === 'minutes') input.max = String(bootstrap.shareMaxMinutes); }
    row.appendChild(input); body.appendChild(row); fields[key] = input;
  }
  const picker = state.createPicker = shareTabPicker(); body.appendChild(picker.root);
  const result = document.createElement('div'); result.className = 'share-create-error'; result.setAttribute('role', 'status');
  const actions = document.createElement('div'); actions.className = 'file-editor-dialog-actions';
  const createButton = shareManagerButton(t('sharing.createShare'), async () => {
    createButton.disabled = true;
    try {
      const resources = picker.resources();
      if (!resources.length) throw new Error('Select at least one tab to share.');
      if (![fields.minutes, fields.limit].every(field => field.reportValidity())) return;
      const response = await api.post('/api/share', {guest: fields.guest.value, minutes: Number(fields.minutes.value),
        limit: Number(fields.limit.value), resources,
        terminal_write_confirmed: resources.some(resource => resource.kind === 'terminal' && resource.writable),
        appearance: {theme: resolvedGlobalThemeMode(), accent: initialSetting('appearance.active_color', 'green')}});
      state.links.set(response.share.id, response.url);
      Object.assign(state.paths, response.resource_paths);
      state.status.textContent = '';
      publishShareManagerGrant(state, response.share);
      close();
      body.remove();
      state.createPicker = null;
      await refreshShareManager();
    } catch (error) {
      result.textContent = error.message === 'guest_name_in_use'
        ? 'That guest name is already in use. Choose a different name.'
        : error.message;
      result.scrollIntoView({block: 'nearest'});
    }
    finally { createButton.disabled = false; }
  });
  fields.minutes.required = fields.limit.required = true;
  fields.limit.max = '1024';
  createButton.classList.add('file-editor-primary-action'); actions.appendChild(createButton); body.append(actions, result);
  function close() {
    disclosure.open = false;
    anchor.focus();
  }
  body.addEventListener('keydown', event => { if (event.key === 'Escape') { event.stopPropagation(); close(); } });
  disclosure.appendChild(body);
}

if (typeof window !== 'undefined' && typeof document !== 'undefined' && window.YOmuxShare) {
  installShareCaretLabels();
  shareHostPollTimer = setTimeout(shareRefreshHostEditors, 1501);
}
