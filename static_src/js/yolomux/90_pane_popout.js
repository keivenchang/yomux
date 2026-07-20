// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared detached pane popout host. Per-pane code supplies only a snapshot renderer.

const panePopouts = new Map();
const paneItemPopouts = panePopoutNamespaceMap('pane');

function panePopoutStorageKey(namespace, id) {
  return `${namespace}:${id}`;
}

function panePopoutNamespaceMap(namespace) {
  return {
    get(id) {
      return panePopouts.get(panePopoutStorageKey(namespace, id));
    },
    set(id, record) {
      const key = panePopoutStorageKey(namespace, id);
      panePopouts.set(key, {...record, key, namespace, id});
      return this;
    },
    has(id) {
      return panePopouts.has(panePopoutStorageKey(namespace, id));
    },
    delete(id) {
      return panePopouts.delete(panePopoutStorageKey(namespace, id));
    },
    keys() {
      return Array.from(panePopouts.values())
        .filter(record => record.namespace === namespace)
        .map(record => record.id)
        [Symbol.iterator]();
    },
  };
}

function closePanePopoutRecord(record) {
  if (!record) return false;
  panePopouts.delete(record.key);
  const popoutWindow = record.window;
  if (!popoutWindow || popoutWindow.closed) return false;
  try { popoutWindow.close?.(); } catch (_) {}
  return true;
}

function panePopoutDocument(popoutWindow) {
  try { return popoutWindow?.document || null; } catch (_) { return null; }
}

function panePopoutDefaultLabel() {
  return t('app.documentTitle');
}

function panePopoutDefaultTitle() {
  return t('pane.popout.title', {name: panePopoutDefaultLabel()});
}

function currentStylesheetHref(match = 'yolomux.css') {
  const link = Array.from(document.querySelectorAll('link[rel="stylesheet"]'))
    .find(item => String(item.getAttribute('href') || '').includes(match));
  return link ? link.href : '';
}

function panePopoutBodyClassName(extraClass = '') {
  const preserve = [
    ...THEME_BODY_CLASSES,
    ...EDITOR_THEME_BODY_CLASSES,
    EDITOR_PREVIEW_VANILLA_CLASS,
    'status-pulse-disabled',
  ];
  const classes = preserve.filter(name => document.body?.classList?.contains(name));
  classes.push('pane-popout-window');
  if (extraClass) classes.push(extraClass);
  return classes.join(' ');
}

function panePopoutVariableStyle() {
  const root = getComputedStyle(document.documentElement);
  const names = [];
  for (let index = 0; index < root.length; index += 1) {
    const name = root.item(index);
    if (name?.startsWith?.('--')) names.push(name);
  }
  return names
    .sort()
    .map(name => {
      const value = root.getPropertyValue(name).trim();
      return value ? `${name}: ${value}` : '';
    })
    .filter(Boolean)
    .join('; ');
}

function writePanePopoutDocument(popoutWindow, options = {}) {
  const doc = panePopoutDocument(popoutWindow);
  if (!doc) return false;
  const title = options.title || panePopoutDefaultTitle();
  const cssHref = options.cssHref || currentStylesheetHref('yolomux.css') || '/static/yolomux.css';
  const bodyClass = options.bodyClass || panePopoutBodyClassName();
  const bodyStyle = options.bodyStyle || panePopoutVariableStyle();
  const style = options.style || '';
  const bodyHtml = options.bodyHtml || '<main class="pane-popout-shell"><section data-pane-popout-root></section></main>';
  doc.open();
  doc.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(title)}</title>
  <link rel="stylesheet" href="${esc(cssHref)}">
  ${style ? `<style>${style}</style>` : ''}
</head>
<body class="${esc(bodyClass)}" style="${esc(bodyStyle)}">
${bodyHtml}
</body>
</html>`);
  doc.close();
  return true;
}

function writePanePopoutAfterNavigation(popoutWindow, expectedPathname, write) {
  let written = false;
  const run = () => {
    if (written || !popoutWindow || popoutWindow.closed) return;
    written = true;
    write();
    popoutWindow.focus?.();
  };
  try {
    if (popoutWindow.location?.pathname === expectedPathname && popoutWindow.document?.readyState === 'complete') {
      run();
      return;
    }
    popoutWindow.addEventListener?.('load', run, {once: true});
    window.setTimeout(run, 1000);
  } catch (_) {
    run();
  }
}

function paneCanPopout(item) {
  const type = tabTypeForItem(item);
  if (!type) return false;
  if (typeof type.canPopout === 'function') return type.canPopout(item) === true;
  return type.canPopout === true || typeof type.popoutRenderer === 'function' || typeof type.openPopout === 'function';
}

function panePopoutDisabledReason(item) {
  if (isTmuxSession(item)) return t('pane.popout.disabledTerminal');
  const type = tabTypeForItem(item);
  const reason = type?.popoutDisabledReason;
  if (typeof reason === 'function') return String(reason(item) || t('pane.popout.disabled'));
  if (reason) return String(reason);
  return t('pane.popout.disabled');
}

function panePopoutPanelSnapshot(item, options = {}) {
  if (options.refresh === 'info' && typeof renderInfoPanel === 'function') renderInfoPanel({force: true});
  if (options.refresh === 'debug' && typeof renderDebugPanels === 'function') renderDebugPanels({force: true});
  const panel = document.getElementById(panelDomId(item));
  if (!panel) return null;
  const clone = panel.cloneNode(true);
  clone.querySelector('.panel-head')?.remove();
  clone.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  clone.querySelectorAll('[autofocus]').forEach(node => node.removeAttribute('autofocus'));
  clone.querySelectorAll('input, textarea, select, button').forEach(node => {
    if (node.matches?.('input, textarea, select')) node.setAttribute('disabled', 'disabled');
    if (node.matches?.('button')) node.setAttribute('disabled', 'disabled');
  });
  const label = itemLabel(item);
  return {
    title: t('pane.popout.title', {name: label}),
    label,
    className: `pane-popout-snapshot ${clone.className || ''}`.trim(),
    html: clone.innerHTML,
  };
}

function panePopoutSnapshotForItem(item) {
  const type = tabTypeForItem(item);
  if (!type) return null;
  if (typeof type.popoutRenderer === 'function') return type.popoutRenderer(item);
  return panePopoutPanelSnapshot(item);
}

function panePopoutDocumentStyle() {
  return `
    html, body {
      min-height: 100%;
      margin: 0;
      overflow: auto;
    }
    body.pane-popout-window {
      display: block !important;
      height: auto;
      min-height: 100%;
      background: var(--bg, #0f131a);
      color: var(--text, #e5e7eb);
      font: 13px/1.35 var(--font, system-ui, sans-serif);
    }
    .pane-popout-shell {
      box-sizing: border-box;
      width: 100%;
      min-height: 100vh;
      padding: 48px 16px 20px;
    }
    .pane-popout-title {
      position: fixed;
      top: 0;
      inset-inline: 0;
      z-index: var(--z-topbar);
      box-sizing: border-box;
      min-height: 36px;
      padding: 9px 14px 8px;
      border-bottom: 1px solid var(--line, #2a3444);
      background: var(--panel, #151b24);
      color: var(--text, #e5e7eb);
      font-weight: 700;
      box-shadow: 0 1px 0 rgb(0 0 0 / 0.18);
    }
    .pane-popout-root,
    .pane-popout-snapshot {
      min-width: 0;
    }
    .pane-popout-window .panel {
      position: static !important;
      display: block !important;
      width: 100% !important;
      height: auto !important;
      min-height: 0 !important;
      overflow: visible !important;
      border: 0 !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    .pane-popout-window .panel-overlay-root,
    .pane-popout-window .info-pane,
    .pane-popout-window .preferences-body,
    .pane-popout-window .preferences-scroll {
      position: static !important;
      inset: auto !important;
      height: auto !important;
      max-height: none !important;
      min-height: 0 !important;
      overflow: visible !important;
    }
    .pane-popout-window .panel-toast-stack {
      display: none !important;
    }
    body.theme-light.pane-popout-window {
      background: var(--bg, #f8fafc);
      color: var(--text, #111827);
    }
    body.theme-light.pane-popout-window .pane-popout-title {
      background: var(--panel, #ffffff);
      color: var(--text, #111827);
      box-shadow: 0 1px 0 rgb(15 23 42 / 0.08);
    }
    @media (max-width: 640px) {
      .pane-popout-shell { padding: 46px 10px 16px; }
      .pane-popout-title { padding-inline: 10px; }
    }
  `;
}

function writePaneItemPopoutDocument(record, snapshot) {
  if (!record?.window || !snapshot) return false;
  return writePanePopoutDocument(record.window, {
    title: snapshot.title || panePopoutDefaultTitle(),
    bodyClass: panePopoutBodyClassName('pane-popout-item-window'),
    bodyStyle: panePopoutVariableStyle(),
    style: panePopoutDocumentStyle(),
    bodyHtml: `<main class="pane-popout-shell">
  <header class="pane-popout-title">${esc(snapshot.label || snapshot.title || panePopoutDefaultLabel())}</header>
  <section data-pane-popout-root class="pane-popout-root ${esc(snapshot.className || '')}">${snapshot.html || ''}</section>
</main>`,
  });
}

function updatePanePopout(item) {
  const record = paneItemPopouts.get(item);
  if (!record) return false;
  const popoutWindow = record.window;
  if (!popoutWindow || popoutWindow.closed) {
    paneItemPopouts.delete(item);
    return false;
  }
  const snapshot = panePopoutSnapshotForItem(item);
  if (!snapshot) return false;
  try {
    const doc = popoutWindow.document;
    const root = doc?.querySelector?.('[data-pane-popout-root]');
    if (!root) return writePaneItemPopoutDocument(record, snapshot);
    doc.title = snapshot.title || panePopoutDefaultTitle();
    doc.body.className = panePopoutBodyClassName('pane-popout-item-window');
    doc.body.setAttribute('style', panePopoutVariableStyle());
    const title = doc.querySelector('.pane-popout-title');
    if (title) title.textContent = snapshot.label || snapshot.title || panePopoutDefaultLabel();
    root.className = `pane-popout-root ${snapshot.className || ''}`.trim();
    root.innerHTML = snapshot.html || '';
    return true;
  } catch (_) {
    paneItemPopouts.delete(item);
    return false;
  }
}

function refreshPanePopouts(item = '') {
  let updated = false;
  for (const id of Array.from(paneItemPopouts.keys())) {
    if (item && id !== item) continue;
    updated = updatePanePopout(id) || updated;
  }
  return updated;
}

function closePanePopout(item) {
  return closePanePopoutRecord(paneItemPopouts.get(item));
}

function closePopoutsForLayoutItem(item) {
  let closed = closePanePopout(item);
  if (isFileEditorItem(item) && typeof closeFilePreviewPopout === 'function') {
    closed = closeFilePreviewPopout(fileItemPath(item)) || closed;
  }
  return closed;
}

function closeAllPanePopouts() {
  for (const record of Array.from(panePopouts.values())) closePanePopoutRecord(record);
}

window.addEventListener('beforeunload', closeAllPanePopouts);

function openPanePopout(item) {
  if (!paneCanPopout(item)) {
    console.info('[YOLOmux] pane popout unavailable', {item, reason: panePopoutDisabledReason(item)});
    return false;
  }
  const type = tabTypeForItem(item);
  if (typeof type?.openPopout === 'function') return type.openPopout(item);
  const snapshot = panePopoutSnapshotForItem(item);
  if (!snapshot) return false;
  const existing = paneItemPopouts.get(item)?.window;
  if (existing && !existing.closed) {
    updatePanePopout(item);
    existing.focus?.();
    return true;
  }
  const popoutWindow = window.open(`/pane-popout?item=${encodeURIComponent(item)}`, `yolomux-pane-${encodeURIComponent(item)}`, 'popup,width=980,height=900');
  if (!popoutWindow) {
    statusErr(localizedHtml('status.panePopoutBlocked'));
    return false;
  }
  try {
    paneItemPopouts.set(item, {window: popoutWindow});
    const record = paneItemPopouts.get(item);
    writePanePopoutAfterNavigation(popoutWindow, '/pane-popout', () => writePaneItemPopoutDocument(record, snapshot));
    return true;
  } catch (error) {
    paneItemPopouts.delete(item);
    try { popoutWindow.close(); } catch (_) {}
    statusErr(localizedHtml('status.panePopoutFailed', {error}));
    return false;
  }
}
