// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const shareHostConnections = new Map();
let shareHostPollTimer = null;
// Identifies this browser tab to the server when reporting the files it has open.
let shareLocalEditorClient = '';
let shareManagerRequest = 0;
let shareManagerRendered = 0;
const sharedEditorPresentationDelayMs = 250;
const shareOwnerDigests = new WeakMap();

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

const shareParticipantOffsets = {35: 180, 155: 60, 275: 240, 95: 120, 335: 300, 215: 30};

function shareParticipantOffset({name, hue}) {
  return shareParticipantOffsets[shareParticipantHue(name, hue)] ?? 180;
}

function shareParticipantStyle(peer) {
  return peer.owner ? '--share-participant-color: var(--active-accent)' :
    `--share-participant-offset: ${shareParticipantOffset(peer)}`;
}

function paintShareCaretLabel(label, caret, scroller) {
  const participantColor = getComputedStyle(caret).color;
  const preview = caret.closest('.file-editor-panel')?.querySelector('.file-editor-preview-pane-panel');
  const backgrounds = [scroller, caret.closest('.cm-editor'), preview].filter(Boolean)
    .map(surface => getComputedStyle(surface).backgroundColor);
  for (let element = caret._htmlCaretElement; element; element = element.parentElement) {
    const background = element.ownerDocument.defaultView.getComputedStyle(element).backgroundColor;
    if (background !== 'rgba(0, 0, 0, 0)') {
      backgrounds.push(background);
      break;
    }
  }
  let fill = participantColor;
  if (backgrounds.includes(fill)) {
    const opposite = getComputedStyle(label).color === 'rgb(0, 0, 0)' ? 'rgb(255, 255, 255)' : 'rgb(0, 0, 0)';
    fill = [opposite, 'rgb(118, 118, 118)'].find(color => !backgrounds.includes(color)) || opposite;
  }
  label.style.backgroundColor = fill;
}

function shareCaretOnLastVisualLine(caret, halfLine) {
  if (caret._htmlCaretElement) {
    const doc = caret._htmlCaretElement.ownerDocument;
    const frame = doc.defaultView.frameElement;
    const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, node =>
      node.data.trim() && previewSourceAnchorIsRendered(node.parentElement) &&
        doc.defaultView.getComputedStyle(node.parentElement).visibility === 'visible'
        ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP);
    const first = walker.nextNode();
    walker.currentNode = doc.body;
    const last = walker.lastChild();
    const start = first && htmlSplitTextNodeCaretRect(first, 0);
    const end = last && htmlSplitTextNodeCaretRect(last, last.length);
    if (start && end && frame && Math.abs(start.top - end.top) >= halfLine) {
      return Math.abs(caret.getBoundingClientRect().top - frame.getBoundingClientRect().top - end.top) < halfLine;
    }
    return false;
  }
  if (caret.classList.contains('share-rich-caret')) {
    const view = caret.closest('.file-editor-panel')?._pmView;
    if (!view) return false;
    try {
      const end = view.coordsAtPos(view.state.doc.content.size - 1);
      const first = view.coordsAtPos(1);
      return Math.abs(first.top - end.top) >= halfLine &&
        Math.abs(view.coordsAtPos(view.posAtDOM(caret, 0)).top - end.top) < halfLine;
    } catch (_) {
      return false;
    }
  }
  const view = window.YOmuxCodeMirror?.EditorView.findFromDOM(caret.closest('.cm-editor'));
  const doc = view?.state.doc;
  if (!doc || doc.lines < 2) return false;
  const offset = view.posAtDOM(caret);
  return doc.lineAt(offset).number === doc.lines &&
    Math.abs(view.coordsAtPos(offset).top - view.coordsAtPos(doc.length).top) < halfLine;
}

function installShareCaretLabels() {
  const editorGrid = document.getElementById('grid');
  if (!editorGrid) return;
  const labels = new Map();
  let scrollers = new Set();
  let frame = 0;
  const markerSelector = '.share-remote-caret, .share-rich-caret';
  const scrollerSelector = '.cm-scroller, .file-editor-preview-pane-panel';
  function render() {
    frame = 0;
    const carets = new Set(editorGrid.querySelectorAll(markerSelector));
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
      const scroller = caret.closest(scrollerSelector);
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
        caret.style.setProperty('--share-participant-offset', shareParticipantOffset({name}));
      }
      paintShareCaretLabel(label, caret, scroller);
      const rightRoom = bounds.right - anchor.left - gap;
      const leftRoom = anchor.left - gap - bounds.left;
      label.style.setProperty('--share-caret-label-room', `${Math.max(0, rightRoom, leftRoom)}px`);
      const {width, height} = label.getBoundingClientRect();
      const left = width <= rightRoom ? anchor.left + gap : anchor.left - gap - width;
      const halfLine = (parseFloat(caretStyle.lineHeight) || fontSize) / 2;
      const caretCenter = anchor.height ? anchor.top + anchor.height / 2 : anchor.top - .4 * fontSize;
      const lastVisualLine = shareCaretOnLastVisualLine(caret, halfLine);
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
    && (node.matches(markerSelector) || node.querySelector(markerSelector));
  const mutations = new MutationObserver(records => {
    if (records.some(record => scrollers.has(record.target.closest?.(scrollerSelector)) ||
      Array.from(record.addedNodes).some(containsCaret) || Array.from(record.removedNodes).some(containsCaret))) schedule();
  });
  mutations.observe(editorGrid, {childList: true, subtree: true});
  mutations.observe(document.body, {attributes: true, attributeFilter: ['class', 'style']});
  const reposition = () => { if (scrollers.size) schedule(); };
  editorGrid.addEventListener('scroll', reposition, {capture: true, passive: true});
  window.addEventListener('resize', reposition, {passive: true});
  schedule();
}

function shareHostFileState(path, synchronizedPanel = null) {
  const state = fileState.get(path);
  if (!state) return;
  const pending = fileEditorPanelsForPath(path).filter(panel => panel._shareConnection?.pending() > 0);
  const owner = pending.includes(state.contentOwnerPanel) ? state.contentOwnerPanel : pending[0];
  if (owner && owner !== synchronizedPanel) state.content = owner._cmView.state.doc.toString();
  state.dirty = pending.length > 0 || state.content !== state.original;
}

function shareHostRenderPreview(panel, path) {
  const text = fileEditorPanelState(panel).content;
  const pane = fileEditorPanelPreviewPane(panel);
  if (panel._pmView) {
    syncProseMirrorPanelSource(panel, path, fileEditorPanelState(panel));
  } else if (pane && !scheduleCodeMirrorSplitPreview(panel, path)) {
    const draft = panel._shareConnection?.pending() ? panel._cmView.state.doc.toString() : text;
    renderFileEditorPreviewSurface(panel, pane, path, draft, {context: fileEditorPanelMode(panel)});
  }
  renderLinkedFilePreviewPanels(panel, path, text);
}

function applyShareEditorSaveState(state, body, text) {
  state.content = text;
  state._shareDocumentSession = body.session;
  state._shareDocumentVersion = body.version;
  state._sharePaused = body.paused;
  if (body.saved && state.original !== text) {
    state.original = text;
    state.lastSaveStatus = {content: text, key: 'editor.saved', params: {size: formatFileSize(new Blob([text]).size)}};
  }
}

function shareHostLocalChanged(panel, path) {
  const state = fileEditorPanelState(panel);
  if (!state || !panel._cmView) return;
  const previous = state.content;
  state.content = panel._cmView.state.doc.toString();
  state.contentOwnerPanel = panel;
  shareHostFileState(path, panel);
  scheduleSharedEditorPresentation(panel, path, state, previous !== state.content, false);
}

function markSharedEditorPending(panel, state) {
  if (!panel._shareConnection?.pending()) return;
  state.contentOwnerPanel = panel;
  if (state.dirty) return;
  state.dirty = true;
  const save = panel.querySelector('.file-editor-save-panel');
  if (save) save.disabled = false;
}

function scheduleSharedEditorPresentation(panel, path, state, preview = false, restart = true) {
  const previewQueued = panel._sharePreviewPending;
  if (preview) panel._sharePreviewPending = true;
  if (panel._sharePresentationTimer != null && (!restart || (previewQueued && fileEditorPanelPreviewPane(panel)))) return;
  clearTimeout(panel._sharePresentationTimer);
  const view = panel._cmView;
  const cooldown = panel._sharePreviewFinishedAt + sharedEditorPresentationDelayMs - performance.now();
  const delay = panel._sharePreviewPending && cooldown > 0 ? cooldown : sharedEditorPresentationDelayMs;
  panel._sharePresentationTimer = setTimeout(() => {
    panel._sharePresentationTimer = null;
    const renderPreview = panel._sharePreviewPending;
    if (panel._cmView !== view || panel.isConnected === false || !state._shareActive ||
        fileEditorPanelState(panel) !== state) {
      panel._sharePreviewPending = false;
      return;
    }
    if (view?.composing) {
      scheduleSharedEditorPresentation(panel, path, state);
      return;
    }
    panel._sharePreviewPending = false;
    if (renderPreview) {
      panel._cmSyncing = true;
      try {
        shareHostRenderPreview(panel, path);
      } finally {
        panel._sharePreviewFinishedAt = performance.now();
        panel._cmSyncing = false;
      }
    }
    updateSharedEditorStatus(panel, path, state);
    captureCodeMirrorPanelViewState(panel, path);
  }, delay);
}

function updateSharedEditorStatus(panel, path, state) {
  updateFileEditorPanelChrome(panel, path);
  const status = openFileStatus(state);
  setFileEditorPanelStatus(panel, status.message, status.level);
}

function reflectSharedEditorState(panel, path, state, body, text) {
  const previous = state.content;
  const wasDirty = state.dirty;
  const paused = state._sharePaused;
  const pdfChanged = body.pdf_revision !== undefined &&
    (state._sharePDFRevision !== body.pdf_revision || state._sharePDFStale !== body.pdf_stale);
  if (body.pdf_revision !== undefined) {
    state._sharePDFRevision = body.pdf_revision;
    state._sharePDFStale = body.pdf_stale;
  }
  applyShareEditorSaveState(state, body, text);
  shareHostFileState(path, panel);
  const preview = previous !== state.content || pdfChanged || wasDirty !== state.dirty;
  if (preview || wasDirty !== state.dirty || paused !== state._sharePaused) {
    scheduleSharedEditorPresentation(panel, path, state, preview, false);
  }
}

function disconnectShareEditor(panel) {
  if (panel._shareConnection) traceHtmlViewEdit(panel, 'live sync left', {pending: panel._shareConnection.pending()});
  if (panel._shareConnection?.pending()) shareHostLocalChanged(panel, panel._cmPath);
  clearTimeout(panel._sharePresentationTimer);
  panel._sharePresentationTimer = null;
  panel._sharePreviewPending = false;
  delete panel._sharePreviewFinishedAt;
  panel._shareConnection?.stop();
  panel._shareConnection = null;
  delete panel._htmlSharePeers;
  delete panel._htmlShareSelection;
  scheduleSplitEditorCaret(panel);
  if (panel._pmView) {
    paintSharedProseMirrorPresence(panel, []);
    refreshSharedProseMirrorAccess(panel);
  }
  shareHostConnections.delete(panel);
}

function disconnectShareEditorsForPath(path) {
  for (const [panel, entry] of shareHostConnections) {
    if (entry.path === path) disconnectShareEditor(panel);
  }
}

function shareHostEndpoint(path) {
  // The page identity makes this browser tab its own participant, so other tabs see its caret.
  shareLocalEditorClient ||= window.YOmuxShare.randomID();
  return `/api/share/document?path=${encodeURIComponent(path)}&page=${encodeURIComponent(shareLocalEditorClient)}`;
}

function registerShareEditorConnection(panel, path, connection) {
  traceHtmlViewEdit(panel, 'live sync joined', {path});
  panel._shareConnection = connection;
  shareHostConnections.set(panel, {path, connection});
  if (panel._pmView) refreshSharedProseMirrorAccess(panel);
  if (previewKindForPath(path) === 'html') syncHtmlViewEditStatus(fileEditorPanelPreviewPane(panel));
}

function rememberSharedHtmlSelection(panel, frame) {
  const guide = frame._htmlSplitViewGuide?.();
  if (guide) {
    panel._shareCaretSurface = 'view';
    panel._htmlShareSelection = {doc: panel._cmView.state.doc, anchor: guide.head === guide.from ? guide.to : guide.from, head: guide.head};
  }
  return guide;
}

function sharedHtmlSelection(panel) {
  const view = panel._cmView;
  if (!view || view.hasFocus) return null;
  const frame = htmlEditorPreviewFrame(panel);
  const guide = frame && rememberSharedHtmlSelection(panel, frame);
  if (panel._shareCaretSurface !== 'view' || !['split', 'preview'].includes(fileEditorPanelMode(panel))) return null;
  const saved = panel._htmlShareSelection;
  const {anchor, head} = saved?.doc === view.state.doc ? saved : view.state.selection.main;
  return {anchor, head, surface: 'view', focused: Boolean(guide)};
}

function sharedHtmlSourcePresence(peer) {
  return {...peer, follower: peer.surface === 'view', showSelection: true};
}

function updateSharedHtmlPresence(panel, peers) {
  panel._htmlSharePeers = {doc: panel._cmView?.state.doc, peers: peers.map(peer => ({
    ...peer, follower: peer.surface !== 'view',
  }))};
  scheduleSplitEditorCaret(panel);
}

function mapSharedHtmlPresence(panel, transaction) {
  const cached = panel._htmlSharePeers;
  if (!panel._shareConnection || !cached || cached.doc !== transaction.startState.doc) return;
  cached.doc = transaction.newDoc;
  cached.peers = cached.peers.map(peer => ({
    ...peer, anchor: transaction.changes.mapPos(peer.anchor), head: transaction.changes.mapPos(peer.head),
  }));
}

function shareCollaborationPresence(panel) {
  const richVisible = () => panel._pmView?.dom?.isConnected && !panel._pmError;
  const html = previewKindForPath(panel._cmPath) === 'html';
  const projectCaret = (peer, surface) => ({
    ...peer, follower: (peer.surface || 'text') !== surface,
  });
  return {
    replacement: codeMirrorTextReplacement,
    inputSurface: panel,
    previewInputSurface: html ? () => htmlEditorPreviewFrame(panel)?._htmlViewEditInputDocument : null,
    selection() {
      if (html) return sharedHtmlSelection(panel);
      const rich = panel._pmView;
      if (richVisible() && (rich.hasFocus() || fileEditorPanelMode(panel) === 'preview' ||
        (!panel._cmView?.hasFocus && panel._shareCaretSurface === 'view'))) {
        return sharedProseMirrorSelection(panel);
      }
      return null;
    },
    sourcePresence(peer) {
      if (html) return sharedHtmlSourcePresence(peer);
      return prosemirrorSupportedSource(fileEditorPanelPath(panel), fileEditorPanelState(panel))
        ? projectCaret(peer, 'text') : peer;
    },
    onPresence(peers) {
      if (html) updateSharedHtmlPresence(panel, peers);
      if (panel._pmView) paintSharedProseMirrorPresence(panel, richVisible() ? peers.map(peer => projectCaret(peer, 'view')) : []);
    },
  };
}

async function shareAttachHostPanel(panel, path) {
  if (typeof bootstrap !== 'undefined' && bootstrap.guestWorkspace) {
    return shareAttachGuestPanel(panel, path);
  }
  if (!window.YOmuxShare || !panel?._cmView || panel._shareConnection || panel._shareConnecting) return;
  const state = fileEditorPanelState(panel);
  if (state?.historical || !state?._shareActive) return;
  panel._shareConnecting = true;
  try {
    const view = panel._cmView, generation = state._shareGeneration;
    const alive = () => panel._cmView === view;
    const current = () => alive() && state._shareActive && state._shareGeneration === generation;
    // A draft based on the saved text joins and replays its edits instead of waiting for a save.
    const draftBase = state.dirty ? state.original.replace(/\r\n?/g, '\n') : undefined;
    // A refused join, such as a draft that no longer matches the saved text, leaves this editor
    // working and unshared. Callers open editors and poll other files, so it must not throw to them.
    let connection;
    try {
      connection = await window.YOmuxShare.connect(view, shareHostEndpoint(path), {
        alive, current, draftBase, ...shareCollaborationPresence(panel),
        onState(body, text) {
          if (current()) reflectSharedEditorState(panel, path, state, body, text);
        },
        onError(error) { if (current()) setFileEditorPanelStatus(panel, error.message, 'error'); },
        onEnd() {
          if (!current()) return;
          disconnectShareEditor(panel);
          // Live sync between this owner's own tabs ends quietly when the other tab closes.
          if (!state._shareLocalOnly) setFileEditorPanelStatus(panel, 'Sharing ended. Pending local edits remain in this editor.', 'warn');
        },
      });
    } catch (error) {
      if (current()) setFileEditorPanelStatus(panel, error.message, 'warn');
      return;
    }
    if (!current()) connection?.stop();
    else if (connection) registerShareEditorConnection(panel, path, connection);
  } finally { panel._shareConnecting = false; }
}

async function publishShareOwnerCarets(body) {
  if (!crypto.subtle) return;
  const updates = [];
  const views = new Map();
  for (const grant of body.shares) for (const resource of grant.resources) {
    if (resource.kind !== 'file' || resource.writable || !resource.can_write) continue;
    const path = body.resource_paths[resource.id];
    if (!path || fileState.get(path)?.dirty) continue;
    if (!views.has(path)) {
      const visible = fileEditorPanelsForPath(path).filter(panel => panel._cmView &&
        (panel._cmView.dom.getClientRects().length || htmlEditorPreviewFrame(panel)?.getClientRects().length))
        .map(panel => {
          const rich = sharedHtmlSelection(panel);
          const view = panel._cmView;
          return {doc: view.state.doc, selection: rich || view.state.selection.main, focused: rich?.focused || view.hasFocus};
        });
      views.set(path, visible.find(view => view.focused) || visible[0]);
    }
    const view = views.get(path);
    if (!view) continue;
    const {doc, selection} = view;
    let digest = shareOwnerDigests.get(doc);
    if (!digest) {
      digest = crypto.subtle.digest('SHA-256', new TextEncoder().encode(doc.toString())).then(
        hash => Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join(''),
      );
      shareOwnerDigests.set(doc, digest);
    }
    updates.push({grant: grant.id, resource: resource.id, digest, anchor: selection.anchor, head: selection.head, surface: selection.surface});
  }
  if (updates.length) {
    await window.YOmuxShare.post('/api/share/presence', await Promise.all(updates.slice(0, 64).map(async update => ({
      ...update, digest: await update.digest,
    }))));
  }
}

async function shareRefreshHostEditors() {
  if (typeof bootstrap !== 'undefined' && bootstrap.guestWorkspace) return;
  if (readOnlyMode || !window.YOmuxShare) return;
  try {
    // A failed open-file report only delays live sync. Share state below must still refresh.
    await reportShareLocalEditing().catch(error => recordJsDebugEvent('editor', {
      category: 'editor', ok: false, message: `Live sync report failed: ${error.message}`,
    }));
    const version = ++shareManagerRequest;
    const body = await window.YOmuxShare.request('/api/share');
    if (version < shareManagerRendered) return;
    updateShareManager(body, version);
    const paths = new Set(body.documents), localPaths = new Set(body.local_documents);
    for (const [path, state] of fileState) {
      const active = paths.has(path);
      state._shareLocalOnly = localPaths.has(path);
      const pdf = body.tex_pdfs?.[path];
      if (pdf?.revision !== state._shareTeX?.revision || pdf?.stale !== state._shareTeX?.stale) {
        state._shareTeX = pdf;
        renderOpenFilePath(path);
      }
      const generation = body.document_generations[path];
      if (active && state._shareGeneration && state._shareGeneration !== generation) {
        shareHostFileState(path);
        disconnectShareEditorsForPath(path);
        state._shareActive = false;
      }
      state._shareGeneration=generation;
      if (active && !state._shareActive) {
        syncOpenFileContentFromPanels(path, state.contentOwnerPanel);
        state._shareActive = true;
        clearFileAutosaveTimer(path);
        renderOpenFilePath(path);
      } else if (!active && state._shareActive) {
        state._shareActive = false;
        disconnectShareEditorsForPath(path);
        if (!state.dirty) await reloadOpenFileFromDisk(path, {force:true});
        else setFileEditorPanelStatus(state.contentOwnerPanel, 'Sharing ended. Save or resolve the retained local draft.', 'warn');
      }
      if (active) for (const panel of fileEditorPanelsForPath(path)) await shareAttachHostPanel(panel, path);
    }
    await publishShareOwnerCarets(body);
  } catch(error) { console.error('YO!share host synchronization failed', error); }
  finally { shareHostPollTimer = setTimeout(shareRefreshHostEditors, 1501); }
}

// Tell the server which writable files this tab has open. Two tabs with the same file
// collaborate through one server document, so edits appear in both without a disk reload.
async function reportShareLocalEditing() {
  shareLocalEditorClient ||= window.YOmuxShare.randomID();
  const files = [];
  for (const [path, state] of fileState) {
    if (files.length >= 64) break;
    if (state.kind !== 'text' || state.historical || state._shareUnavailable || !fileEditorStateWritable(state)) continue;
    // Diff mode has no live editor to collaborate through.
    if (!fileEditorPanelsForPath(path).some(panel => panel._cmView && fileEditorPanelMode(panel) !== 'diff')) continue;
    files.push({path, clean: !state.dirty});
  }
  const result = await window.YOmuxShare.post('/api/share/editing', {client: shareLocalEditorClient, files});
  for (const [path, code] of Object.entries(result.failures || {})) {
    recordJsDebugEvent('editor', {category: 'editor', ok: false, message: `Live sync unavailable for ${path}: ${code}`});
  }
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

async function copyShareInvitation(link, status, pendingCopy) {
  try {
    if (pendingCopy && await pendingCopy) showCopyFeedback();
    else await copyTextWithFeedback(link, {rethrow: true});
  } catch (error) {
    status.textContent = t('common.copyFailed', {error: error.message});
  }
}

function shareInviteCopyButton(link, guest, status) {
  const copy = shareManagerButton(`Copy URL to share w/ ${guest}`, () => copyShareInvitation(link, status));
  copy.classList.add('share-invite-copy');
  copy.insertAdjacentHTML('afterbegin', fileContextMenuIcon('copy'));
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
    const path = paths[resource.id];
    add({id: resource.id, kind: resource.kind, path, pane: resource.pane,
      writable: resource.writable}, path || resource.label, resource.can_write, true);
  }
  for (const item of layoutItems) {
    const path = fileItemPath(item);
    if (path) {
      const state = fileEditorStateForItem(path, item);
      if (state?.externalMissing || state?.loading || state?.historical || !['text', 'image', 'pdf', 'audio', 'video'].includes(state?.kind)) continue;
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

async function shareMarkdownImagePaths(path) {
  const {content} = await fetchFileReadPayload(path);
  const fragment = sanitizeMarkdownPreviewHtml(markdownPreviewHtml(content));
  const prefix = dirnameOf(path).replace(/\/$/, '') + '/';
  const paths = new Set();
  for (const image of fragment.querySelectorAll('img[src]')) {
    const source = image.getAttribute('src');
    if (safeDecodeURIComponent(splitMarkdownResourceUrl(source).path).startsWith('/')) continue;
    const target = markdownPreviewImageTarget(source, path);
    if (target?.path.startsWith(prefix) && previewKindForPath(target.path) === 'image') {
      paths.add(target.path.slice(prefix.length));
    }
  }
  return [...paths].slice(0, 64);
}

let shareReadControlId = 0;

function shareTabPicker({grant = null, paths = {}, onChange = null, guestField = null} = {}) {
  const root = document.createElement('div'); root.className = 'share-admin-picker';
  const {table, rows} = shareTable(['R', 'W', '']);
  table.tHead.rows[0].cells[0].setAttribute('aria-label', t('sharing.readable'));
  table.tHead.rows[0].cells[1].setAttribute('aria-label', t('sharing.writable'));
  table.classList.add('share-permission-table'); root.appendChild(table);
  let choices = [];
  let confirmingTerminal = false;
  let terminalWriteConfirmed = grant?.terminal_write_confirmed === true;
  function updateDescription({resource, write, access}) {
    const guest = guestField?.value.trim() || grant?.guest;
    access.textContent = write.checked && guest
      ? ' ' + t(resource.kind === 'terminal' ? 'sharing.executableBy' : 'sharing.writableTo', {guest})
      : '';
  }
  function addChoice(choice, previous) {
    const selection = document.createElement('label');
    selection.className = 'share-permission-description';
    const selected = document.createElement('input'); selected.type = 'checkbox';
    selected.id = 'share-read-' + ++shareReadControlId;
    selection.htmlFor = selected.id;
    selected.checked = previous?.selected.checked ?? choice.selected;
    selected.setAttribute('aria-label', t('sharing.resource', {label: choice.label}));
    const name = document.createElement('span'); name.textContent = choice.label;
    const access = document.createElement('span');
    selection.append(name, access);
    const write = document.createElement('input'); write.type = 'checkbox';
    write.checked = selected.checked && choice.canWrite && (previous?.write.checked ?? choice.resource.writable);
    write.disabled = !choice.canWrite;
    write.setAttribute('aria-label', t('sharing.permission', {label: `${t('sharing.writable')}: ${choice.label}`}));
    write.dataset.shareResource = choice.label;
    const row = shareTableRow(rows, [selected, write, selection]);
    const entry = {...choice, selected, write, row, access};
    updateDescription(entry);
    selected.addEventListener('change', () => {
      if (!selected.checked) write.checked = false;
      updateDescription(entry);
      onChange?.();
    });
    write.addEventListener('change', async () => {
      if (choice.resource.kind === 'terminal' && write.checked && !terminalWriteConfirmed) {
        write.checked = false;
        if (confirmingTerminal) return;
        confirmingTerminal = true;
        try {
          const action = await showFileEditorDecisionDialog({
            className: 'share-terminal-write-confirmation',
            title: t('sharing.confirmTerminalTitle'),
            message: t('sharing.confirmTerminalMessage'),
            messageVariant: 'danger',
            actions: [
              {id: 'cancel', label: t('common.cancel')},
              {id: 'allow', label: t('sharing.confirmTerminalAllow'), variant: 'danger'},
            ],
          });
          if (action !== 'allow' || !write.isConnected || write.disabled ||
              !choices.some(entry => entry.write === write)) return;
          terminalWriteConfirmed = true;
          write.checked = true;
        } finally {
          confirmingTerminal = false;
        }
      }
      if (write.checked) selected.checked = true;
      updateDescription(entry);
      onChange?.();
    });
    return entry;
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
  guestField?.addEventListener('input', () => choices.forEach(updateDescription));
  return {root, refresh, get choices() { return choices; }, get terminalWriteConfirmed() { return terminalWriteConfirmed; }, async resources() {
    return Promise.all(choices.filter(choice => choice.selected.checked).map(async choice => {
      const resource = {...choice.resource, writable: choice.canWrite && choice.write.checked};
      if (resource.kind === 'file' && resource.writable && (!resource.id || !choice.resource.writable)) {
        resource.saved_content = shareHostSavedContent(resource.path);
      }
      if (!resource.id && resource.kind === 'file' && previewKindForPath(resource.path) === 'markdown') {
        resource.image_paths = await shareMarkdownImagePaths(resource.path);
      }
      return resource;
    }));
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
  const visible = itemIsActivePaneTab(shareItemId);
  if (visible && !state.wasVisible) {
    const paths = visibleFileEditorWatchFiles();
    if (paths.length) void refreshOpenFilesIfChanged({paths, fresh: true})
      .catch(error => console.warn('YO!share file check failed', error));
  }
  state.wasVisible = visible;
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
      const connected = document.createElement('span'); connected.dataset.shareConnectedLabel = '';
      indicator.appendChild(connected);
      const countdown = document.createElement('span'); countdown.dataset.expiry = '';
      const stop = shareStopButton(state, grant.id); stop.classList.add('share-summary-stop');
      summary.append(name, count, indicator, countdown, stop); row.appendChild(summary);
      row.addEventListener('toggle', () => {
        if (!row.open) return;
        row.querySelector('.share-manager-detail')._refreshSharePicker();
        if (!state.links.has(grant.id)) void refreshShareManager();
      });
      const detail = document.createElement('section'); detail.className = 'share-manager-detail'; row.appendChild(detail);
      state.rows.set(grant.id, row); state.list.appendChild(row);
    }
    row.querySelector('[data-share-name]').textContent = grant.guest;
    const peers = state.peers[grant.id] || [];
    const connectedPeers = new Set(peers.filter(peer => Date.now() - Date.parse(peer.last_seen) <= 5000));
    row.querySelector('[data-share-count]').textContent = t('sharing.guestCount', {claimed: connectedPeers.size, limit: grant.limit});
    row.querySelector('.share-connected-indicator').hidden = connectedPeers.size === 0;
    row.querySelector('[data-share-connected-label]').textContent = t('sharing.connected');
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
        invite.append(input, shareInviteCopyButton(link, grant.guest, state.status));
        summary.appendChild(invite);
      }
    }
    const detail = row.querySelector('.share-manager-detail');
    const detailKey = JSON.stringify([grant.revision, grant.resources]);
    if (detail.dataset.key !== detailKey) {
      const focused = detail.contains(document.activeElement) ? document.activeElement.getAttribute('aria-label') : null;
      detail.dataset.key = detailKey; detail.replaceChildren(); buildShareManagerDetails(state, grant, detail);
      if (focused) [...detail.querySelectorAll('[aria-label]')].find(node => node.getAttribute('aria-label') === focused)?.focus();
    } else if (row.open) {
      detail._refreshSharePicker();
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
  const picker = shareTabPicker({grant, paths: state.paths, onChange: applyPicker});
  let applying = false;
  detail._refreshSharePicker = () => { if (!applying) picker.refresh(); };
  async function applyPicker() {
    if (applying) return;
    applying = true;
    const controls = [...detail.querySelectorAll('input:not(:disabled)')];
    try {
      controls.forEach(input => { input.disabled = true; });
      const resources = await picker.resources();
      await api.request('/api/share?id=' + encodeURIComponent(grant.id), {method: 'PATCH',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({revision: grant.revision, resources,
          terminal_write_confirmed: picker.terminalWriteConfirmed})});
      state.status.textContent = '';
      void publishNewSharedTeX(state, resources);
      await refreshShareManager();
    } catch (error) {
      state.status.textContent = error.message;
      detail.dataset.key = '';
      renderShareManager(state);
      await refreshShareManager();
    }
    finally {
      applying = false;
      controls.forEach(input => { if (input.isConnected) input.disabled = false; });
    }
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
    if (path && previewKindForPath(path) === 'tex') {
      actions.appendChild(shareManagerButton('Publish PDF: ' + resource.label, () => publishSharedTeX(state, [path])));
    }
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
    const text = JSON.stringify(incoming.map(message => [message.name, message.text, shareParticipantStyle(message)]));
    if (text === messages) return;
    const atEnd = log.scrollHeight - log.scrollTop - log.clientHeight <= 2;
    const fragment = document.createDocumentFragment();
    for (const message of incoming) {
      const row = document.createElement('div'); row.className = 'share-chat-message';
      const author = document.createElement('span'); author.className = 'share-chat-author';
      author.textContent = message.name + ': ';
      author.setAttribute('style', shareParticipantStyle(message));
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

function publishNewSharedTeX(state, resources) {
  const paths = resources.filter(resource => !resource.id && resource.kind === 'file' && previewKindForPath(resource.path) === 'tex')
    .map(resource => resource.path);
  if (paths.length) return publishSharedTeX(state, paths, {savedOnly: true});
}

async function publishSharedTeX(state, paths, {savedOnly = false} = {}) {
  const failures = [];
  for (const path of paths) {
    state.status.textContent = t('preview.tex.compiling');
    try {
      const file = fileState.get(path);
      if (!file || file.kind !== 'text') throw new Error('Open the source file before publishing.');
      if (!savedOnly) syncOpenFileContentFromPanels(path, file.contentOwnerPanel);
      const content = savedOnly ? file.original : file.content;
      const response = await fetch('/api/tex/preview', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path, content, publish: true})});
      if (!response.ok || !response.headers.get('Content-Type')?.includes('application/pdf')) {
        const failure = await response.json();
        throw new Error(failure.error || t(failure.code === 'compiler_missing' ? 'preview.tex.missing' : 'preview.tex.failed'));
      }
    } catch (error) {
      failures.push(`${basenameOf(path)}: ${error.message}`);
    }
  }
  state.status.textContent = failures.length ? failures.join(' · ') : t('sharing.published');
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
  const picker = state.createPicker = shareTabPicker({guestField: fields.guest}); body.appendChild(picker.root);
  const result = document.createElement('div'); result.className = 'share-create-error'; result.setAttribute('role', 'status');
  const actions = document.createElement('div'); actions.className = 'file-editor-dialog-actions';
  const createButton = shareManagerButton(t('sharing.createShare'), async () => {
    createButton.disabled = true;
    try {
      if (!picker.choices.some(choice => choice.selected.checked)) throw new Error('Select at least one tab to share.');
      if (![fields.minutes, fields.limit].every(field => field.reportValidity())) return;
      const invitation = {guest: fields.guest.value, minutes: Number(fields.minutes.value),
        limit: Number(fields.limit.value), terminal_write_confirmed: picker.terminalWriteConfirmed,
        appearance: {theme: resolvedGlobalThemeMode(), accent: initialSetting('appearance.active_color', 'green')}};
      const creation = picker.resources().then(async resources => ({
        response: await api.post('/api/share', {...invitation, resources}),
        resources,
      }));
      const pendingCopy = beginDeferredTextCopy(() => creation.then(({response}) => response.url));
      const {response, resources} = await creation;
      state.links.set(response.share.id, response.url);
      Object.assign(state.paths, response.resource_paths);
      state.status.textContent = '';
      publishShareManagerGrant(state, response.share);
      close();
      body.remove();
      state.createPicker = null;
      void copyShareInvitation(response.url, state.status, pendingCopy);
      void publishNewSharedTeX(state, resources);
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
  actions.appendChild(createButton); body.append(actions, result);
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
  // A closed or reloaded page has nothing open. Without this its old report would pair it with
  // its own replacement page for live sync until the report expires.
  window.addEventListener('pagehide', () => {
    if (!shareLocalEditorClient) return;
    void fetch('/api/share/editing', {method: 'POST', keepalive: true, credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({client: shareLocalEditorClient, files: []})})
      .catch(error => recordJsDebugEvent('editor', {category: 'editor', ok: false, message: `Live sync close report failed: ${error.message}`}));
  });
}
