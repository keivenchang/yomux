// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Terminal mobile-accessory action declarations used by the following orchestration partial.

const terminalRuntimeFacades = new Map();

function registerTerminalRuntimeFacade(name, facade) {
  const key = String(name || '');
  if (!key || !facade || typeof facade !== 'object' || terminalRuntimeFacades.has(key)) return false;
  terminalRuntimeFacades.set(key, Object.freeze({...facade}));
  return true;
}

function terminalRuntimeFacade(name) {
  return terminalRuntimeFacades.get(String(name || '')) || null;
}

const terminalMobileAccessoryActionFamilies = Object.freeze({
  primary: Object.freeze(['tmux-prefix', 'upload', 'backspace', 'more']),
  side: Object.freeze(['tab', 'shift', 'ctrl']),
  dpad: Object.freeze(['copy', 'command-v', 'arrow-up', 'tmux-scroll-up', 'arrow-left', 'enter', 'arrow-right', 'alt', 'arrow-down', 'tmux-scroll-down']),
});
registerTerminalRuntimeFacade('mobile-accessory-actions', terminalMobileAccessoryActionFamilies);

let clientEventTransportLifecycleScope = null;
function currentClientEventTransportLifecycleScope() {
  if (!clientEventTransportLifecycleScope || clientEventTransportLifecycleScope.disposed()) clientEventTransportLifecycleScope = createLifecycleScope();
  return clientEventTransportLifecycleScope;
}

function paneFrameControlsHtml(session, options = {}) {
  const role = paneRoleForSlot(options.slot || slotForItem(session));
  const controlOptions = role.controls === 'minimize-only'
    ? {...options, actions: false, details: false, popout: false, expand: false, close: false, minimize: true}
    : options;
  const disabled = controlOptions.disabled === true;
  const unavailableLabel = controlOptions.unavailableLabel || itemLabel(session);
  const unavailableTitle = t('tab.unavailableFor', {name: unavailableLabel});
  const controls = [];
  const add = spec => controls.push(toolbarButtonHtml({
    className: ['tab', spec.className, spec.platformKind ? platformWindowControlClass(spec.platformKind) : '', spec.active ? 'active' : ''].filter(Boolean).join(' '),
    dataset: disabled ? {} : spec.dataset,
    disabled,
    hidden: spec.hidden === true,
    title: disabled ? unavailableTitle : spec.title,
    ariaLabel: spec.label,
    pressed: spec.pressed,
    html: spec.html,
  }));
  const includeActions = controlOptions.actions ?? isTmuxSession(session);
  const includeDetails = controlOptions.details === true;
  const includeMinimize = controlOptions.minimize !== false
    && (!narrowSingleColumnMode() || narrowPaneFrameActionTargetsTab(session));
  const includeExpand = controlOptions.expand !== false;
  const includePopout = controlOptions.popout === true;
  if (includeActions) {
    add({className: 'pane-actions', dataset: {paneActions: session}, title: t('common.sessionActions'), label: t('common.sessionActions'), html: '<span class="pane-actions-dots" aria-hidden="true">...</span>'});
  }
  if (includeDetails) {
    const detailsLabel = t('pane.details.hide');
    add({className: 'panel-detail-toggle pane-detail-toggle', platformKind: 'minimize', dataset: {detailToggle: session}, title: detailsLabel, label: detailsLabel, pressed: true, active: true});
  }
  if (includePopout) {
    add({className: 'pane-popout', dataset: {panePopout: session}, title: t('tab.popout'), label: t('tab.popout')});
  }
  if (includeExpand) {
    add({className: 'pane-expand', platformKind: 'zoom', dataset: {paneExpand: session}, title: t('pane.expand'), label: t('pane.expand'), hidden: !canPaneExpand(session)});
  }
  if (includeMinimize) {
    add({className: 'pane-minimize', platformKind: 'minimize', dataset: {paneMinimize: session}, title: t('pane.minimize'), label: t('pane.minimize')});
  }
  if (controlOptions.close) {
    const closeLabel = controlOptions.closeLabel || t('pane.closeTab');
    const closeTitle = controlOptions.closeTitle || closeLabel;
    add({className: ['pane-close', controlOptions.closeClass || ''].filter(Boolean).join(' '), platformKind: 'close', dataset: {paneClose: session}, title: closeTitle, label: closeLabel});
  }
  return controls.join('');
}

function paneFrameControlsGroupHtml(session, options = {}) {
  const groupClass = options.groupClass ? ` ${options.groupClass}` : '';
  return `<div class="tabs pane-frame-controls${groupClass}" role="tablist">${paneFrameControlsHtml(session, options)}</div>`;
}

function panelControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
  const disabledAttrs = label => disabled ? ` type="button" disabled title="${esc(t('tab.unavailableFor', {name: unavailableLabel}))}" aria-label="${esc(label)}"` : '';
  const readonlyAttrs = label => ` type="button" disabled title="${esc(t('tab.adminRequiredFor', {name: label}))}" aria-label="${esc(label)}"`;
  const tabAttrs = (name, label = '') => {
    if (disabled) return disabledAttrs(label || name);
    if (readOnlyMode && name === 'summary') return readonlyAttrs(t('brand.tab.summary'));
    const labelAttrs = label ? ` title="${esc(label)}" aria-label="${esc(label)}"` : '';
    return ` type="button" data-tab="${esc(session)}" data-tab-name="${name}"${labelAttrs}`;
  };
  const info = transcriptMetadataState.payload.sessions?.[session];
  const terminalTitle = terminalTabTitle(session, info);
  const terminalAttrs = disabled ? disabledAttrs(terminalTitle) : `${tabAttrs('terminal')} title="${esc(terminalTitle)}" aria-label="${esc(terminalTitle)}"`;
  const terminalLabel = disabled ? t('tab.terminal.short') : terminalTabLabel(session, info);
  const isFiles = typeof isFileSurfaceItem === 'function' ? isFileSurfaceItem(session) : isFileExplorerItem(session);
  // Term is pressed ONLY when the terminal view is the active one — computed from the live view, not
  // hardcoded, so a panel re-render (Dockview header refresh) doesn't re-press it after the user
  // switched to transcript / YO!summary / events. activateTab also toggles it on click.
  const terminalActive = panelActiveTabName(session) === 'terminal';
  const terminalButtonHtml = `<button class="tab${terminalActive ? ' active' : ''} terminal-tab" ${terminalAttrs}>${esc(terminalLabel)}</button>`;
  const frameHtml = isFiles
    ? paneFrameControlsHtml(session, {
      disabled,
      actions: false,
      minimize: false,
      expand: false,
      close: true,
      closeTitle: t('finder.close', {name: fileExplorerLabel()}),
      closeLabel: t('finder.close', {name: fileExplorerLabel()}),
    })
    : paneFrameControlsHtml(session, {
      disabled,
      actions: isTmuxSession(session),
      details: true,
      // In a one-column touch layout, X and minus remove only this selected tab. Showing them here
      // makes the ordinary pane controls useful without offering a blank-the-last-pane action.
      close: narrowPaneFrameActionTargetsTab(session),
    });
  return `<div class="tabs ${disabled ? 'disabled-panel-controls' : ''}" role="tablist">
          ${terminalButtonHtml}
          ${frameHtml}
        </div>`;
}

function virtualPanelControlsHtml(session, options = {}) {
  return `<div class="tabs virtual-panel-controls" role="tablist">
          ${paneFrameControlsHtml(session, {actions: false, close: false, ...options})}
        </div>`;
}

// A pane has exactly one frame-control owner. Dockview renders it in the common outer group header;
// the fallback layout renders it inside panelFrameHtml(). Keeping that choice here prevents every
// virtual panel from growing a second, independently hidden control row.
function virtualPanelInnerControlsHtml(session, options = {}) {
  return dockviewLayoutEnabled() ? '' : virtualPanelControlsHtml(session, options);
}

function relocalizeVirtualPanelChrome(panel, label = '') {
  if (!panel) return false;
  panel.querySelectorAll('.pane-tabs[role="tablist"]').forEach(tablist => tablist.setAttribute('aria-label', t('common.tabsLabel')));
  panel.querySelectorAll('[data-pane-minimize]').forEach(button => {
    button.title = t('pane.minimize');
    button.setAttribute('aria-label', t('pane.minimize'));
  });
  panel.querySelectorAll('[data-pane-expand]').forEach(button => {
    button.title = t('pane.expand');
    button.setAttribute('aria-label', t('pane.expand'));
  });
  const labelNode = panel.querySelector('.panel-session-label .session-button-dir');
  if (labelNode && label) labelNode.textContent = label;
  if (typeof syncPanelDetailsToggleState === 'function') syncPanelDetailsToggleState(panel);
  return true;
}

function panelActiveTabName(session) {
  const activePane = document.getElementById(panelDomId(session))?.querySelector('.tab-pane.active');
  const id = activePane?.id || '';
  if (id === `transcript-pane-${session}`) return 'transcript';
  if (id === `summary-pane-${session}`) return 'summary';
  if (id === `events-pane-${session}`) return 'events';
  return 'terminal';
}
