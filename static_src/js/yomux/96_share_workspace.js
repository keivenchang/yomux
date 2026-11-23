// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const shareWorkspace = {
  base: '', active: false, resources: new Map(), drafts: new Map(), layoutCommitted: false,
  chat: null, timer: null, countdown: null, restoreView: null,
};

const shareWorkspaceViewModes = new Set(['edit', 'preview', 'split']);
const shareWorkspaceScrollKeys = {editor: 'edit', preview: 'preview'};
const shareWorkspaceReloadKey = 'yomux.share.reloadView';

function readShareWorkspaceView() {
  const reload = safeJsonParse(sessionStorageGet(shareWorkspaceReloadKey), null);
  sessionStorageRemove(shareWorkspaceReloadKey);
  const isReload = globalThis.performance?.getEntriesByType?.('navigation')[0]?.type === 'reload';
  const current = location.pathname + location.search;
  const params = new URLSearchParams(isReload && reload?.url === current ? reload.query : location.search);
  if (!params.has('tab')) return null;
  const mode = params.get('view');
  const scroll = new Map();
  for (const [source, key] of Object.entries(shareWorkspaceScrollKeys)) {
    const value = params.get(key) || '';
    if (/^\d{1,8},-?\d{1,8}$/.test(value)) {
      const [top, left] = value.split(',').map(Number);
      scroll.set(source, {top, left});
    }
  }
  return {id: params.get('tab'), mode: shareWorkspaceViewModes.has(mode) ? mode : '', scroll};
}

function updateShareWorkspaceURL({unloading = false} = {}) {
  if (!shareWorkspace.active || !shareWorkspace.layoutCommitted) return;
  const item = tabberNormalSelections[0];
  const path = item && fileItemPath(item);
  const resource = path && shareWorkspaceResource(path);
  const id = resource?.id || '';
  const mode = resource && resource.kind !== 'terminal' ? editorViewModeFor(path, item) : '';
  const pending = shareWorkspace.restoreView;
  if (pending?.id === id && pending.mode === mode) return;
  shareWorkspace.restoreView = null;

  const params = new URLSearchParams(location.search);
  for (const key of ['active', 'sessions', 'layout', 'tabs', 'finder', 'state', 'view', 'edit', 'preview']) params.delete(key);
  params.set('tab', id);
  if (shareWorkspaceViewModes.has(mode)) params.set('view', mode);
  const panel = panelNodes.get(item);
  for (const [source, key] of Object.entries(shareWorkspaceScrollKeys)) {
    if (mode !== 'split' && mode !== key) continue;
    const element = fileEditorSourceElement(panel, source);
    if (!element?.getClientRects().length) continue;
    const {top, left} = ownedElementScrollPosition(element);
    params.set(key, [Math.round(top), Math.round(left)].join(','));
  }
  const current = location.pathname + location.search;
  const query = params.toString();
  const url = location.pathname + '?' + query;
  if (url !== current) {
    // Reload may have chosen its URL before the debounced scroll update could publish.
    if (unloading) sessionStorageSet(shareWorkspaceReloadKey, JSON.stringify({url: current, query}));
    else history.replaceState(null, '', url);
  }
  return url;
}

function restoreShareWorkspaceScroll(item, view) {
  const deadline = performance.now() + 3000;
  const owners = new Map();
  let settledFrames = 0;
  const restore = () => {
    if (!shareWorkspace.active || view !== shareWorkspace.restoreView) return;
    const panel = panelNodes.get(item);
    let complete = true;
    for (const [source, coordinates] of view.scroll) {
      const element = fileEditorSourceElement(panel, source);
      if (!element?.getClientRects().length) {
        complete = false;
        continue;
      }
      const position = ownedElementScrollPosition(element);
      if (Math.abs(position.top - coordinates.top) < 0.5 && Math.abs(position.left - coordinates.left) < 0.5) continue;
      if (!owners.has(element)) owners.set(element, createInputOwnedDeferredElementScrollOwner(element));
      if (!writeDeferredElementScrollIfOwned(owners.get(element), element, coordinates, 'share-url-restore')) continue;
      if (view.mode === 'split') rememberFileEditorReflectedScroll(panel, source, source, Number(panel._splitScrollGeneration || 0));
      if (source === 'editor') panel._cmView?.requestMeasure();
      complete = false;
    }
    settledFrames = complete ? settledFrames + 1 : 0;
    if (settledFrames < 2 && performance.now() < deadline) {
      requestAnimationFrame(restore);
      return;
    }
    shareWorkspace.restoreView = null;
    updateShareWorkspaceURL();
  };
  requestAnimationFrame(restore);
}

function shareWorkspacePath(resource) {
  return `/shared/${resource.id}/${resource.label.replaceAll('/', '_')}${resource.kind === 'terminal' ? '.terminal' : ''}`;
}

function shareWorkspaceResource(path) {
  return [...shareWorkspace.resources.values()].find(resource => shareWorkspacePath(resource) === path);
}

function shareWorkspaceRawURL(path, params = {}) {
  if (!shareWorkspace.active) return 'about:blank';
  const resource = shareWorkspaceResource(path);
  if (resource) return shareWorkspace.base + resource.id + (params.download ? '?download=1' : '');
  // Markdown assets are capabilities explicitly associated with the source grant.
  for (const source of shareWorkspace.resources.values()) {
    const directory = dirnameOf(shareWorkspacePath(source));
    for (const [relative, id] of Object.entries(source.assets || {})) {
      if (joinAndNormalize(directory, relative) === path && shareWorkspace.resources.has(id)) return shareWorkspace.base + id;
    }
  }
  return 'about:blank';
}

function shareWorkspaceSnapshot(resource, options = {}) {
  if (resource.writable) resource._snapshotClient ||= window.YOmuxShare.randomID();
  return window.YOmuxShare.request(shareWorkspace.base + resource.id +
    '?permission=' + resource.permission_revision +
    (resource.writable ? '&initial=1&client=' + encodeURIComponent(resource._snapshotClient) : ''), options);
}

async function shareWorkspaceFetch(url, options = {}) {
  const request = new URL(String(url), location.origin);
  const path = request.searchParams.get('path');
  const resource = shareWorkspaceResource(path);
  const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
  if (request.origin === location.origin && request.pathname.startsWith('/static/')) return fetch(url, options);
  if (shareWorkspace.active && request.origin === location.origin && request.pathname.startsWith(shareWorkspace.base)
      && shareWorkspace.resources.has(request.pathname.slice(shareWorkspace.base.length))
      && (!options.method || options.method === 'GET')) return fetch(url, {...options, credentials: 'same-origin'});
  if (shareWorkspace.active && resource && (!options.method || options.method === 'GET')) {
    if (request.pathname === '/api/fs/raw') return fetch(shareWorkspaceRawURL(path), {...options, credentials: 'same-origin'});
    if (request.pathname === '/api/fs/read' || request.pathname === '/api/fs/info') {
      const body = await shareWorkspaceSnapshot(resource, options);
      return json({content: body.text, size: new TextEncoder().encode(body.text || '').length, writable: resource.writable,
        read_only: !resource.writable, git: null, mtime: 0});
    }
  }
  if (request.pathname === '/api/activity') return json({sessions: {}, windows: {}, agents: []});
  return json({error: 'This action is outside the shared tabs.', error_code: 'share_scope'}, 403);
}

function shareWorkspaceMenus() {
  return [{id: 'view', label: t('menu.view'), items: [
    menuSubmenu(t('menu.view.theme'), ['dark', 'light'].map(theme => menuCommand(t('common.theme.' + theme),
      () => setGlobalThemeMode(theme), {checked: resolvedGlobalThemeMode() === theme, keepOpen: true}))),
  ]}, {id: 'tabs', label: t('common.tabsLabel'), items: tabMenuItems()},
  {id: 'help', label: t('menu.help'), items: [aboutMenuCommand()]}];
}

function saveShareWorkspaceSettings(patch) {
  if (patch.appearance?.theme && ['dark', 'light'].includes(patch.appearance.theme)) {
    storageSet('yomux.share.theme.' + shareWorkspace.base, patch.appearance.theme);
  }
  applySettingsPayload({settings: mergeSettingObjects(clientSettings, patch)}, {force: true});
  return Promise.resolve();
}

function applyShareWorkspaceAppearance(appearance) {
  const savedTheme = storageGet('yomux.share.theme.' + shareWorkspace.base);
  applySettingsPayload({settings: {
    appearance: {theme: savedTheme || appearance?.theme || (appearance ? 'dark' : 'system'),
      active_color: appearance?.accent || 'green', sync_font_sizes: false},
    general: {default_tabber_mode: 'normal'},
  }}, {initial: true, force: true});
}

function retainShareWorkspaceDraft(path) {
  const state = fileState.get(path);
  const panels = fileEditorPanelsForPath(path);
  const pending = panels.find(panel => panel._shareConnection?.pending() > 0);
  if (pending || state?.dirty) {
    shareWorkspace.drafts.set(path, pending ? pending._cmView.state.doc.toString() : state.content);
    renderShareWorkspaceDrafts();
  }
  for (const panel of panels) {
    disconnectShareEditor(panel);
  }
}

function renderShareWorkspaceDrafts() {
  let drawer = document.getElementById('share-retained-drafts');
  if (!drawer) {
    drawer = document.createElement('details');
    drawer.id = 'share-retained-drafts';
    const title = document.createElement('summary'); title.textContent = t('sharing.unsentDrafts');
    drawer.appendChild(title); document.getElementById('appRoot').appendChild(drawer);
  }
  drawer.open = true;
  for (const [path, text] of shareWorkspace.drafts) {
    let field = [...drawer.querySelectorAll('textarea')].find(node => node.dataset.path === path);
    if (!field) {
      const label = document.createElement('label'); label.textContent = basenameOf(path);
      field = document.createElement('textarea'); field.readOnly = true; field.dataset.path = path;
      field.setAttribute('aria-label', t('sharing.unsentDraft', {label: basenameOf(path)})); label.appendChild(field); drawer.appendChild(label);
    }
    field.value = text;
  }
}

async function shareAttachGuestPanel(panel, path) {
  const resource = shareWorkspaceResource(path);
  if (!shareWorkspace.active || !resource?.writable || !panel._cmView || panel._shareConnection || panel._shareConnecting) return;
  panel._shareConnecting = true;
  const view = panel._cmView, revision = resource.permission_revision;
  const current = () => shareWorkspace.active && panel._cmView === view && shareWorkspaceResource(path)?.permission_revision === revision;
  try {
    const connection = await window.YOmuxShare.connect(view, shareWorkspace.base + resource.id + '?permission=' + revision, {
      current, alive: () => panel._cmView === view,
      onState(body, text) {
        if (!current()) return;
        const state = fileState.get(path);
        state.content = text;
        state._sharePDFAvailable = body.pdf_available;
        state._sharePDFRevision = body.pdf_revision;
        state._sharePDFStale = body.pdf_stale;
        if (body.saved) state.original = text;
        shareHostFileState(path);
        shareHostRenderPreview(panel, path);
        updateFileEditorPanelChrome(panel, path);
        setFileEditorPanelStatus(panel, body.paused ? 'Saving paused: ' + body.paused : body.saved ? 'Saved' : 'Saving', body.paused ? 'error' : 'ok');
      },
      onError(error) { if (current()) setFileEditorPanelStatus(panel, error.message, 'error'); },
      onEnd() {
        if (!current()) return;
        retainShareWorkspaceDraft(path);
        fileState.get(path).writable = false;
        refreshFileEditorWriteAccess(panel, fileState.get(path));
      },
    });
    if (!current()) connection?.stop();
    else if (connection) { panel._shareConnection = connection; shareHostConnections.set(panel, {path, connection}); }
  } finally {
    panel._shareConnecting = false;
  }
}

function shareTerminalControlInput(event) {
  if (!event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return null;
  const key = event.key.toLowerCase();
  // Keep the browser's paste shortcut on platforms that use Ctrl+V.
  if (key === 'v' && !isMacPlatform()) return null;
  if (!event.shiftKey && /^[a-z]$/.test(key)) return String.fromCharCode(key.charCodeAt(0) - 96);
  const codes = {' ': 0, '@': 0, '2': 0, '[': 27, '3': 27, '\\': 28, '4': 28,
    ']': 29, '5': 29, '^': 30, '6': 30, '_': 31, '7': 31, '?': 127, '8': 127};
  return Object.hasOwn(codes, key) ? String.fromCharCode(codes[key]) : null;
}

function createShareTerminalPanel(item) {
  const path = fileItemPath(item), resource = shareWorkspaceResource(path);
  const panel = createFramedPanel({item, className: 'panel', frame: {
    controlsHtml: virtualPanelInnerControlsHtml(item), bodyClass: 'share-terminal-body terminal-pane',
    bodyHtml: '<pre class="share-screen" tabindex="0"><span class="share-screen-text"></span><span class="share-screen-cursor" aria-hidden="true" hidden></span></pre><p class="share-terminal-status" role="status" hidden></p>',
  }});
  panel._guestTerminal = true;
  panel.dataset.shareWritable = String(resource.writable);
  const screen = panel.querySelector('.share-screen'); screen.setAttribute('aria-label', resource.label);
  const text = screen.querySelector('.share-screen-text'), cursor = screen.querySelector('.share-screen-cursor');
  const status = panel.querySelector('.share-terminal-status');
  const currentGrant = () => shareWorkspace.active && shareWorkspace.resources.get(resource.id);
  let followInput = true;
  function cursorScrollOffset() {
    const view = screen.getBoundingClientRect();
    const caret = cursor.getBoundingClientRect();
    const offset = (start, end, origin, size) => start < origin ? start - origin : Math.max(0, end - origin - size);
    return {left: offset(caret.left, caret.right, view.left + screen.clientLeft, screen.clientWidth),
      top: offset(caret.top, caret.bottom, view.top + screen.clientTop, screen.clientHeight)};
  }
  function revealCursor() {
    const {left, top} = cursorScrollOffset();
    if (left || top) screen.scrollBy({left, top, behavior: 'instant'});
  }
  screen.addEventListener('focus', () => {
    setFocusedPanelItem(item);
    followInput = true;
    if (currentGrant()?.writable && !cursor.hidden) revealCursor();
  });
  panel._paintShareTerminalSnapshot = (body, grant) => {
    const focused = grant.writable && document.activeElement === screen;
    const offset = focused && !cursor.hidden && cursorScrollOffset();
    const follow = followInput || (offset && !offset.left && !offset.top);
    if (text.textContent !== body.text) text.textContent = body.text;
    cursor.hidden = !body.cursor;
    if (body.cursor) {
      cursor.style.setProperty('--share-cursor-column', String(body.cursor.column));
      cursor.style.setProperty('--share-cursor-row', String(body.cursor.row));
      if (focused && follow) {
        revealCursor();
        followInput = false;
      }
    }
    status.hidden = true;
  };
  let input = Promise.resolve();
  function send(body) {
    const grant = currentGrant();
    if (!grant?.writable) return;
    input = input.then(async () => {
      if (currentGrant() !== grant) return;
      await window.YOmuxShare.post(shareWorkspace.base + grant.id + '?permission=' + grant.permission_revision, body);
      followInput = true;
    }).catch(error => showLayoutStatus(error.message));
  }
  const keys = {Enter: 'Enter', Escape: 'Escape', Tab: 'Tab', Backspace: 'BSpace', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right', Home: 'Home', End: 'End', PageUp: 'PPage', PageDown: 'NPage'};
  screen.addEventListener('keydown', event => {
    if (!currentGrant()?.writable || event.metaKey || event.altKey) return;
    const control = shareTerminalControlInput(event);
    if (control !== null) {
      event.preventDefault();
      event.stopPropagation();
      send({text: control});
      return;
    }
    const key = keys[event.key];
    if (event.ctrlKey && !key) return;
    if (key || event.key.length === 1) { event.preventDefault(); send(key ? {key} : {text: event.key}); }
  });
  screen.addEventListener('paste', event => { if (currentGrant()?.writable) { event.preventDefault(); send({text: event.clipboardData.getData('text/plain')}); } });
  return panel;
}

async function refreshShareTerminalScreens() {
  await Promise.all([...shareWorkspace.resources.values()].filter(resource => resource.kind === 'terminal').map(async resource => {
    const item = fileEditorItemFor(shareWorkspacePath(resource)), panel = panelNodes.get(item);
    if (!panel?.isConnected || !panel.getClientRects().length) return;
    const current = () => shareWorkspace.active && shareWorkspace.resources.get(resource.id) === resource && panelNodes.get(item) === panel;
    try {
      const body = await shareWorkspaceSnapshot(resource);
      if (!current()) return;
      panel._paintShareTerminalSnapshot(body, resource);
    } catch (error) {
      if (!current()) return;
      const status = panel.querySelector('.share-terminal-status');
      status.textContent = error.message;
      status.hidden = false;
    }
  }));
}

function renderSharePublishedPDF(container, path) {
  const state = fileState.get(path), resource = shareWorkspaceResource(path);
  const signature = JSON.stringify([state?._sharePDFRevision, state?._sharePDFStale, state?._sharePDFAvailable]);
  if (container._sharePDFSignature === signature) return false;
  container._sharePDFSignature = signature;
  const message = document.createElement('p');
  message.textContent = !state?._sharePDFAvailable ? 'The host has not published a PDF.' : state._sharePDFStale ? 'Published PDF is stale. Waiting for the host to republish.' : 'Host-published PDF';
  container.replaceChildren(message);
  if (resource && state?._sharePDFAvailable) {
    const frame = document.createElement('iframe'); frame.title = t('sharing.publishedPDF');
    frame.src = shareWorkspace.base + resource.id + '?pdf=1&revision=' + state._sharePDFRevision;
    frame.style.cssText = 'width:100%;flex:1;min-height:20em;border:0'; container.appendChild(frame);
  }
  return true;
}

function shareWorkspaceKind(resource) {
  const ext = fileExtensionOf(shareWorkspacePath(resource));
  if (resource.kind === 'terminal') return 'text';
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp'].includes(ext)) return 'image';
  if (['.pdf', '.mp3', '.mp4', '.ogg', '.wav', '.webm'].includes(ext)) return 'media';
  return 'text';
}

async function syncShareWorkspaceResources(resources) {
  const previous = shareWorkspace.resources;
  const staged = [];
  // A membership read can become stale while its resource reads are in flight.
  // Do not publish half a membership or empty editor state from that old revision.
  for (const descriptor of resources) {
    const resource = {...descriptor, _snapshotClient: previous.get(descriptor.id)?._snapshotClient};
    let kind = shareWorkspaceKind(resource);
    let body = null;
    if (resource.kind !== 'terminal' && kind === 'text') {
      try {
        body = await shareWorkspaceSnapshot(resource);
      } catch (error) {
        if (error.status !== 409 || error.message !== 'file_unavailable') throw error;
        kind = 'error';
      }
    }
    if (!shareWorkspace.active) return;
    staged.push({resource, kind, body});
  }
  const next = new Map(staged.map(({resource}) => [resource.id,
    previous.get(resource.id)?.permission_revision === resource.permission_revision ? Object.assign(previous.get(resource.id), resource) : resource]));
  const changed = JSON.stringify([...previous.values()].map(resource => [resource.id, resource.permission_revision])) !==
    JSON.stringify([...next.values()].map(resource => [resource.id, resource.permission_revision]));
  const selected = tabberNormalSelections?.[0];
  for (const resource of previous.values()) {
    if (next.get(resource.id) === resource) continue;
    const path = shareWorkspacePath(resource);
    retainShareWorkspaceDraft(path);
    if (!next.has(resource.id)) {
      storeTabberNormalSelections((tabberNormalSelections || []).filter(item => item !== fileEditorItemFor(path)), {syncLayout: false});
      await removeOpenFile(path, {confirmDirty: false, render: false});
      if (!shareWorkspace.active) return;
    }
  }
  shareWorkspace.resources = next;
  for (const {resource: descriptor, kind, body} of staged) {
    const resource = next.get(descriptor.id);
    const path = shareWorkspacePath(resource), item = fileEditorItemFor(path);
    let state = fileState.get(path);
    if (kind === 'error') {
      if (!state?._shareUnavailable) {
        retainShareWorkspaceDraft(path);
        setFileState(path, {...fileErrorState('This shared file is no longer available.'), _shareUnavailable: true,
          _shareActive: true, editorTabItems: new Set([item])});
        renderOpenFilePath(path);
      }
      continue;
    }
    if (!state || state._shareUnavailable || !previous.has(resource.id)) {
      const content = resource.kind !== 'terminal' && kind === 'text' ? body.text : '';
      state = setFileState(path, {kind, content, original: content, dirty: false, writable: false, _shareActive: true,
        _guestTerminal: resource.kind === 'terminal', editorTabItems: new Set([item])});
    }
    state.writable = resource.writable;
    state._shareActive = true;
    if (resource.kind !== 'terminal' && state.kind === 'text') {
      if (!resource.writable) {
        state.content = state.original = body.text;
        state.dirty = false;
        for (const panel of fileEditorPanelsForPath(path)) {
          if (panel._cmView && panel._cmView.state.doc.toString() !== body.text) {
            panel._cmSyncing = true;
            try { panel._cmView.dispatch({changes: {from: 0, to: panel._cmView.state.doc.length, insert: body.text}}); }
            finally { panel._cmSyncing = false; }
          }
        }
      }
      state._sharePDFAvailable = body.pdf_available;
      state._sharePDFRevision = body.pdf_revision;
      state._sharePDFStale = body.pdf_stale;
    }
    if (resource.kind === 'terminal') {
      const panel = panelNodes.get(item);
      if (panel) panel.dataset.shareWritable = String(resource.writable);
    } else {
      for (const panel of fileEditorPanelsForPath(path)) refreshFileEditorWriteAccess(panel, state);
    }
    if (state.kind === 'text' && resource.kind !== 'terminal') {
      if (changed) for (const panel of fileEditorPanelsForPath(path)) {
        const preview = panel.querySelector('.file-editor-preview-pane-panel');
        if (preview) preview._previewText = null;
      }
      renderOpenFilePath(path);
    }
  }
  syncFileLayoutItems();
  if (changed || !shareWorkspace.layoutCommitted) {
    const items = [...next.values()].map(resource => fileEditorItemFor(shareWorkspacePath(resource)));
    const view = !shareWorkspace.layoutCommitted ? shareWorkspace.restoreView : null;
    const savedResource = next.get(view?.id);
    const savedPath = savedResource && shareWorkspacePath(savedResource);
    const savedItem = savedPath && fileEditorItemFor(savedPath);
    if (view?.mode && savedResource && savedResource.kind !== 'terminal') {
      setFileEditorViewMode(savedPath, view.mode, savedItem);
      const editorScroll = view.scroll.get('editor');
      if (editorScroll && view.mode !== 'preview') {
        layoutUrlRememberEditorViewState({path: savedPath, item: savedItem}, editorScroll.top, editorScroll.left);
      }
    }
    const selection = savedItem ? [savedItem] : view?.id === '' ? [] : items.includes(selected) ? [selected] : items.slice(0, 1);
    storeTabberNormalSelections(selection, {syncLayout: false});
    applyLayoutSlots(defaultNormalTabberLayoutSlots(), {focusSession: tabberNormalSelections[0] || tabberItemId, prune: true});
    refreshTabberPanels();
    shareWorkspace.layoutCommitted = true;
    if (savedItem && view.scroll.size && view.mode === editorViewModeFor(savedPath, savedItem)) {
      restoreShareWorkspaceScroll(savedItem, view);
    } else if (view) {
      shareWorkspace.restoreView = null;
    }
    updateShareWorkspaceURL();
  }
  for (const resource of next.values()) if (resource.writable && resource.kind !== 'terminal') {
    const path = shareWorkspacePath(resource);
    if (fileState.get(path)?._shareUnavailable) continue;
    for (const panel of fileEditorPanelsForPath(path)) {
      void shareAttachGuestPanel(panel, path).catch(error => setFileEditorPanelStatus(panel, error.message, 'error'));
    }
  }
}

function createShareWorkspaceCountdown(expires) {
  const indicator = document.createElement('span');
  indicator.className = 'topbar-status-surface share-expiry-countdown';
  indicator.setAttribute('role', 'timer');
  document.querySelector('.topbar .actions').prepend(indicator);
  const paint = () => {
    const text = t('sharing.timeLeft', {time: shareTimeLeft(expires)});
    if (indicator.textContent !== text) indicator.textContent = text;
  };
  paint();
  const timer = setInterval(paint, 1000);
  return {
    update(deadline) {
      expires = deadline;
      paint();
    },
    end() {
      clearInterval(timer);
      indicator.textContent = t('sharing.ended');
    },
  };
}

async function endShareWorkspace(message) {
  shareWorkspace.active = false;
  clearTimeout(shareWorkspace.timer);
  shareWorkspace.countdown?.end();
  const paths = [...shareWorkspace.resources.values()].map(shareWorkspacePath);
  for (const path of paths) retainShareWorkspaceDraft(path);
  shareWorkspace.resources.clear();
  shareWorkspace.chat?.close();
  shareWorkspace.chat = null;
  closeSessionContextMenu();
  closeFileContextMenu();
  showShareAccessNotice(message);
  await Promise.all(paths.map(path => removeOpenFile(path, {confirmDirty: false, render: false})));
  storeTabberNormalSelections([], {syncLayout: false});
  applyLayoutSlots(defaultNormalTabberLayoutSlots(), {focusSession: tabberItemId, prune: true});
  refreshTabberPanels();
}

function showShareAccessNotice(message) {
  const messages = {
    share_ended: 'This share link has expired or ended. Ask the owner to open File → YO!share → New Share, select the tabs, then click Create New Share and send you the new link.',
    join_required: 'Open the invitation link to enter this share.',
    invalid_join: 'This invitation link is invalid.',
    guest_limit_reached: 'This share has reached its guest limit.',
  };
  document.querySelector('.topbar').hidden = true;
  renderSessionButtons();
  const notice = document.getElementById('shareAccessNotice');
  notice.textContent = messages[message] || message;
  notice.hidden = false;
  if (notice.closest('dialog')) return;

  const dialog = document.createElement('dialog');
  dialog.className = 'share-access-dialog';
  dialog.setAttribute('closedby', 'none');
  dialog.setAttribute('aria-label', t('sharing.title'));
  dialog.setAttribute('aria-describedby', notice.id);
  dialog.addEventListener('cancel', event => event.preventDefault());
  const brand = document.querySelector('.topbar .brand-title').cloneNode(true);
  brand.querySelectorAll('.brand-version, button').forEach(node => node.remove());
  brand.removeAttribute('title');
  brand.setAttribute('aria-label', 'YOmux');
  const card = document.createElement('div');
  card.className = 'share-access-card';
  notice.tabIndex = -1;
  notice.setAttribute('autofocus', '');
  notice.before(dialog);
  card.append(notice);
  const drafts = document.getElementById('share-retained-drafts');
  if (drafts) card.append(drafts);
  dialog.append(brand, card);
  dialog.showModal();
}

async function bootShareWorkspace() {
  shareWorkspace.base = location.pathname.replace(/\/$/, '') + '/';
  shareWorkspace.restoreView = readShareWorkspaceView();
  const appearanceKey = 'yomux.share.appearance.' + shareWorkspace.base;
  installNativeAppViewportOwner(); syncNativeAppViewport({force: true});
  installPreviewScrollOwnershipCapture(); installTouchContextMenuOwner();
  installGlobalThemeMediaListener();
  const secret = location.hash.slice(1);
  try {
    const exchange = () => window.YOmuxShare.post(shareWorkspace.base + 'join', {secret});
    const body = secret ? await (navigator.locks ? navigator.locks.request(shareWorkspace.base + 'join', exchange) : exchange()) : await window.YOmuxShare.request(shareWorkspace.base + 'state');
    if (secret) history.replaceState(null, '', location.pathname + location.search);
    shareWorkspace.active = true;
    const appearance = body.share.appearance || {};
    storageSet(appearanceKey, JSON.stringify(appearance));
    applyShareWorkspaceAppearance(appearance);
    setTabberMode('normal', {save: false, refresh: false});
    storeTabberNormalSelections([], {syncLayout: false});
    tabberActivityState.loaded = true;
    await applyLocale(resolveLocalePref('en'));
    syncAppViewportBreakpointClasses(); applyFileExplorerStaticLabels();
    document.body.classList.add('share-workspace-session');
    for (const id of ['notifyToggle', 'refreshMeta', 'logoutButton', 'latencyMeter']) document.getElementById(id).hidden = true;
    document.title = ['YOmux', body.owner, body.name].filter(Boolean).join(' - ');
    document.addEventListener('scroll', event => {
      if (event.target.matches?.('.cm-scroller, .file-editor-preview-pane-panel')) scheduleLayoutUrlStateRefresh();
    }, {capture: true, passive: true});
    window.addEventListener('pagehide', () => updateShareWorkspaceURL({unloading: true}));
    shareWorkspace.countdown = createShareWorkspaceCountdown(body.share.expires);
    document.querySelector('.topbar').hidden = false;
    paintInitialAppShell();
    const sidebar = panelNodes.get(tabberItemId)?.querySelector('.file-explorer-pane');
    if (sidebar) shareWorkspace.chat = shareSidebarChat(sidebar, {
      id: body.share.id, label: body.share.guest, endpoint: shareWorkspace.base + 'chat', keepOpenWhileActive: true,
    });
    let legacyGuestSlots = 0;
    async function poll(initialState = null) {
      if (!shareWorkspace.active) return;
      try {
        const state = initialState || await window.YOmuxShare.request(shareWorkspace.base + 'state');
        if (!shareWorkspace.active) return;
        if (!Number.isInteger(state.hue)) {
          while (legacyGuestSlots < state.share.claimed) {
            legacyGuestSlots++;
            rememberLegacyShareGuest(state.share.guest + (legacyGuestSlots === 1 ? '' : ' ' + legacyGuestSlots));
          }
        }
        shareWorkspace.countdown.update(state.share.expires);
        try {
          await syncShareWorkspaceResources(state.share.resources);
        } finally {
          if (shareWorkspace.active) await refreshShareTerminalScreens();
        }
        if (!shareWorkspace.active) return;
        const chat = await window.YOmuxShare.request(shareWorkspace.base + 'chat');
        shareWorkspace.chat?.update(chat.messages);
      } catch (error) {
        if ([401, 403, 410].includes(error.status)) await endShareWorkspace(error.message);
        else if (![404, 409].includes(error.status)) showLayoutStatus(error.message);
      } finally { if (shareWorkspace.active) shareWorkspace.timer = setTimeout(poll, 1003); }
    }
    await poll(body);
  } catch (error) {
    if (shareWorkspace.active) await endShareWorkspace(error.message);
    else {
      if (error.appearance) storageSet(appearanceKey, JSON.stringify(error.appearance));
      applyShareWorkspaceAppearance(error.appearance || readStoredJson(appearanceKey));
      showShareAccessNotice(error.message);
    }
  }
}
