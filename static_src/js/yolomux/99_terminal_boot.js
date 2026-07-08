
function paneFrameControlsHtml(session, options = {}) {
  const disabled = options.disabled === true;
  const unavailableLabel = options.unavailableLabel || itemLabel(session);
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
  const includeActions = options.actions ?? isTmuxSession(session);
  const includeDetails = options.details === true;
  const includeMinimize = options.minimize !== false && (!narrowSingleColumnMode() || narrowPaneFrameActionTargetsTab(session));
  const includeExpand = options.expand !== false;
  const includePopout = options.popout === true;
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
  if (options.close) {
    const closeLabel = options.closeLabel || t('pane.closeTab');
    const closeTitle = options.closeTitle || closeLabel;
    add({className: ['pane-close', options.closeClass || ''].filter(Boolean).join(' '), platformKind: 'close', dataset: {paneClose: session}, title: closeTitle, label: closeLabel});
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
  const isFiles = isFileExplorerItem(session);
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

const tmuxStatusModes = new Map();

// Mobile terminal apps conventionally supplement, rather than replace, the OS keyboard with a
// movable smart-key palette. Keep its state per session and pass every byte through the normal
// xterm transport so shares, tmux-window tracking, attention acknowledgement, and metrics stay unified.
const terminalMobileAccessoryStates = new Map();
const terminalMobileAccessoryKeyPresses = new Map();
const terminalMobileAccessorySuppressedClicks = new Map();
let terminalMobileAccessoryDismissalInstalled = false;
let terminalMobileAccessoryResizeInstalled = false;
// Clipboard reads must start while a touch still has browser user activation.  The shared
// terminal context menu opens after the long-press delay, when Safari may reject a new read.
// Keep only availability (never clipboard contents) so that menu can truthfully offer Paste.
const terminalClipboardProbeTtlMs = 10_000;
let terminalClipboardProbe = {available: false, expiresAt: 0, promise: null};

function terminalClipboardPasteAvailable() {
  return terminalClipboardProbe.available === true && Date.now() < terminalClipboardProbe.expiresAt;
}

function primeTerminalClipboardAvailability() {
  if (terminalClipboardProbe.promise) return terminalClipboardProbe.promise;
  const clipboard = globalThis.navigator?.clipboard;
  if (!clipboard) return Promise.resolve(false);
  const probe = (async () => {
    if (typeof clipboard.read === 'function') {
      try {
        const items = await clipboard.read();
        return (items || []).some(item => (item.types || []).some(type => String(type).startsWith('image/') || type === 'text/plain'));
      } catch (_) {
        // Some Safari builds expose read() but grant text-only readText() permission instead.
      }
    }
    if (typeof clipboard.readText === 'function') {
      try {
        return Boolean(await clipboard.readText());
      } catch (_) {
        // A denied probe simply omits Paste from this long-press menu.
      }
    }
    return false;
  })()
    .then(available => {
      terminalClipboardProbe = {available, expiresAt: Date.now() + terminalClipboardProbeTtlMs, promise: null};
      return available;
    });
  terminalClipboardProbe = {...terminalClipboardProbe, promise: probe};
  return probe;
}

const terminalMobileAccessoryKeyDefs = Object.freeze([
  {action: 'escape', label: 'Esc', ariaLabel: 'Esc', data: '\x1b'},
  {action: 'ctrl', label: 'Ctrl', ariaLabel: 'Ctrl', modifier: 'ctrl'},
  {action: 'alt', label: 'Alt', ariaLabel: 'Alt', modifier: 'alt'},
  {action: 'interrupt', label: '^C', ariaLabel: 'Ctrl-C', data: '\x03', className: 'mobile-terminal-key--interrupt'},
  {action: 'tmux-prefix', label: '^B', ariaLabel: 'Ctrl-B (tmux prefix)', data: '\x02'},
  {action: 'tab', label: 'Tab', ariaLabel: 'Tab', data: '\t'},
  {action: 'copy', labelKey: 'common.copy', ariaLabelKey: 'common.copy'},
  {action: 'command-v', label: '⌘V', ariaLabel: 'Command-V'},
  {action: 'tmux-scroll-up', label: 'Pg↑', ariaLabel: 'Scroll tmux up'},
  {action: 'tmux-scroll-down', label: 'Pg↓', ariaLabel: 'Scroll tmux down'},
  {action: 'backspace', label: '⌫', ariaLabel: 'Backspace', data: '\x7f'},
  {action: 'arrow-left', label: '←', ariaLabel: 'Left arrow'},
  {action: 'arrow-down', label: '↓', ariaLabel: 'Down arrow'},
  {action: 'arrow-up', label: '↑', ariaLabel: 'Up arrow'},
  {action: 'arrow-right', label: '→', ariaLabel: 'Right arrow'},
  {action: 'enter', label: '↵', ariaLabel: 'Enter', data: '\r'},
  {action: 'more', label: '⋯', ariaLabel: 'More terminal keys', more: true},
]);
const terminalMobileAccessoryMoreKeyDefs = Object.freeze([
  // These mirror browser/app actions that a phone has no physical Command key for. They are
  // actions rather than terminal bytes: Cmd-P opens quick-open; Paste stays visible in the first row.
  {action: 'command-p', label: '⌘P', ariaLabel: 'Command-P'},
  {action: 'home', label: 'Home', ariaLabel: 'Home'},
  {action: 'end', label: 'End', ariaLabel: 'End'},
  {action: 'delete', label: 'Del', ariaLabel: 'Delete', data: '\x1b[3~'},
  {action: 'shift-tab', label: '⇧↹', ariaLabel: 'Shift-Tab', data: '\x1b[Z'},
  {action: 'ctrl-d', label: '^D', ariaLabel: 'Ctrl-D', data: '\x04'},
  {action: 'ctrl-z', label: '^Z', ariaLabel: 'Ctrl-Z', data: '\x1a'},
  {action: 'ctrl-l', label: '^L', ariaLabel: 'Ctrl-L', data: '\x0c'},
  {action: 'ctrl-r', label: '^R', ariaLabel: 'Ctrl-R', data: '\x12'},
]);
const terminalMobileAccessoryPrimaryActions = Object.freeze(['escape', 'ctrl', 'interrupt', 'tab', 'tmux-prefix', 'more']);
// The surrounding command keys form one compact five-column navigation pad: clipboard controls
// live on the left, direct tmux scrolling on the right, and arrows retain their physical D-pad.
const terminalMobileAccessoryDpadActions = Object.freeze(['copy', 'arrow-up', 'tmux-scroll-up', 'arrow-left', 'enter', 'arrow-right', 'command-v', 'arrow-down', 'tmux-scroll-down']);

function terminalMobileAccessoryState(session, options = {}) {
  const key = String(session || '');
  if (!key) return null;
  let state = terminalMobileAccessoryStates.get(key) || null;
  if (!state && options.create === true) {
    state = {ctrl: false, alt: false, more: false, open: false, x: null, y: null, drag: null, launcherPress: null, suppressLauncherClick: false};
    terminalMobileAccessoryStates.set(key, state);
  }
  return state;
}

function terminalMobileAccessoryEnabled() {
  return browserUsesCoarsePointer();
}

function terminalMobileAccessoryCursorData(session, action) {
  const suffix = {home: 'H', end: 'F', 'arrow-up': 'A', 'arrow-down': 'B', 'arrow-right': 'C', 'arrow-left': 'D'}[action];
  if (!suffix) return '';
  // xterm exposes the active cursor-key mode. Respect it so a TUI receives the same sequence as a
  // physical arrow key instead of assuming shell/readline's normal CSI mode.
  return terminals.get(session)?.term?.modes?.applicationCursorKeys === true ? `\x1bO${suffix}` : `\x1b[${suffix}`;
}

function terminalMobileAccessoryData(session, action) {
  const definition = terminalMobileAccessoryDefinition(action);
  if (!definition) return '';
  return definition.data || terminalMobileAccessoryCursorData(session, action);
}

function terminalMobileAccessoryButtonHtml(session, definition, state, extraClass = '') {
  const active = definition.modifier ? state?.[definition.modifier] === true : false;
  // Copy reads local terminal selection only, so it stays available to read-only share viewers;
  // every other palette key can change the terminal and keeps the existing write gate.
  const disabled = readOnlyMode && !shareWriteMode && definition.action !== 'copy' ? ' disabled' : '';
  const expanded = definition.more ? ` aria-expanded="${state?.more === true ? 'true' : 'false'}"` : '';
  const label = definition.labelKey ? t(definition.labelKey) : definition.label;
  const ariaLabel = definition.ariaLabelKey ? t(definition.ariaLabelKey) : definition.ariaLabel;
  return `<button type="button" class="mobile-terminal-key${definition.className ? ` ${definition.className}` : ''}${active ? ' active' : ''}${extraClass ? ` ${extraClass}` : ''}" data-terminal-mobile-key="${esc(definition.action)}" data-terminal-mobile-session="${esc(session)}" aria-label="${esc(ariaLabel)}"${definition.modifier ? ` aria-pressed="${active ? 'true' : 'false'}"` : ''}${expanded}${disabled}>${esc(label)}</button>`;
}

function terminalMobileAccessoryDefinition(action) {
  return [...terminalMobileAccessoryKeyDefs, ...terminalMobileAccessoryMoreKeyDefs].find(item => item.action === action) || null;
}

function terminalMobileAccessoryPositionStyle(state) {
  const x = Number.isFinite(state?.x) ? Math.max(0, Math.round(state.x)) : null;
  const y = Number.isFinite(state?.y) ? Math.max(0, Math.round(state.y)) : null;
  if (x === null && y === null) return '';
  // A moved absolute overlay must release its original end edges. Leaving `bottom` set alongside
  // the drag-set `top` makes CSS stretch the palette to the terminal bottom, producing a giant
  // empty box and preventing a useful upward move.
  return ` style="${x === null ? '' : `inset-inline-start:${x}px;inset-inline-end:auto;`}${y === null ? '' : `inset-block-start:${y}px;inset-block-end:auto;transform:none;`}"`;
}

function terminalMobileAccessoryHtml(session) {
  if (!isTmuxSession(session) || !terminalMobileAccessoryEnabled()) return '';
  const state = terminalMobileAccessoryState(session, {create: true});
  const key = action => terminalMobileAccessoryButtonHtml(session, terminalMobileAccessoryDefinition(action), state, `mobile-terminal-key--${action}`);
  const primaryKeys = terminalMobileAccessoryPrimaryActions.filter(action => action !== 'more').map(key).join('');
  // The overflow button remains the rightmost control in both palette states. Keeping the return
  // target in one physical corner avoids a touch user hunting for it after the content switches.
  const moreKeys = [...terminalMobileAccessoryMoreKeyDefs.map(definition => terminalMobileAccessoryButtonHtml(session, definition, state)), key('more')].join('');
  return `<button type="button" class="mobile-terminal-key-launcher" data-terminal-mobile-accessory="${esc(session)}" data-terminal-mobile-toggle="${esc(session)}" aria-label="${esc(t('common.keyboardShortcuts'))}" aria-expanded="${state.open ? 'true' : 'false'}">⌨</button>
    <div class="mobile-terminal-keybar" data-terminal-mobile-keybar="${esc(session)}" role="toolbar" aria-label="${esc(t('common.keyboardShortcuts'))}"${terminalMobileAccessoryPositionStyle(state)}${state.open ? '' : ' hidden'}>
      <button type="button" class="mobile-terminal-key-drag" data-terminal-mobile-drag="${esc(session)}" aria-label="${esc(t('common.keyboardShortcuts'))}">⠿</button>
      <div class="mobile-terminal-keyrow-shell"><div class="mobile-terminal-keyrow mobile-terminal-keyrow--primary">${primaryKeys}</div>${key('more')}</div>
      <div class="mobile-terminal-key-dpad">${terminalMobileAccessoryDpadActions.map(key).join('')}</div>
      <div class="mobile-terminal-keyrow mobile-terminal-keyrow--more"${state.more ? '' : ' hidden'}>${moreKeys}</div>
    </div>`;
}

function syncTerminalMobileAccessoryState(session) {
  const state = terminalMobileAccessoryState(session);
  const bar = document.querySelector(`[data-terminal-mobile-keybar="${cssEscape(session)}"]`);
  const launcher = document.querySelector(`[data-terminal-mobile-toggle="${cssEscape(session)}"]`);
  if (!state || !bar) return false;
  for (const modifier of ['ctrl', 'alt']) {
    const button = bar.querySelector(`[data-terminal-mobile-key="${modifier}"]`);
    if (!button) continue;
    const active = state[modifier] === true;
    button.classList.toggle(CLS.active, active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  }
  const more = bar.querySelector('.mobile-terminal-keyrow--more');
  const moreButtons = bar.querySelectorAll('[data-terminal-mobile-key="more"]');
  bar.hidden = state.open !== true;
  bar.classList.toggle('mobile-terminal-keybar--more', state.more === true);
  if (launcher) launcher.setAttribute('aria-expanded', state.open === true ? 'true' : 'false');
  const positionedInline = Number.isFinite(state.x);
  const positionedBlock = Number.isFinite(state.y);
  bar.style.insetInlineStart = positionedInline ? `${Math.max(0, Math.round(state.x))}px` : '';
  bar.style.insetInlineEnd = positionedInline ? 'auto' : '';
  bar.style.insetBlockStart = positionedBlock ? `${Math.max(0, Math.round(state.y))}px` : '';
  bar.style.insetBlockEnd = positionedBlock ? 'auto' : '';
  bar.style.transform = positionedBlock ? 'none' : '';
  if (more) more.hidden = state.more !== true;
  for (const moreButton of moreButtons) moreButton.setAttribute('aria-expanded', state.more === true ? 'true' : 'false');
  scheduleFit(session);
  return true;
}

function toggleTerminalMobileAccessoryState(session, key) {
  const state = terminalMobileAccessoryState(session, {create: true});
  if (!state || !['ctrl', 'alt', 'more', 'open'].includes(key)) return false;
  if (key === 'open' && state.open !== true) dismissTerminalMobileAccessories(session);
  state[key] = !state[key];
  syncTerminalMobileAccessoryState(session);
  return state[key];
}

function dismissTerminalMobileAccessory(session) {
  const state = terminalMobileAccessoryState(session);
  if (!state || state.open !== true) return false;
  state.open = false;
  state.more = false;
  state.ctrl = false;
  state.alt = false;
  state.drag = null;
  syncTerminalMobileAccessoryState(session);
  return true;
}

function dismissTerminalMobileAccessories(exceptSession = '') {
  let dismissed = false;
  for (const session of terminalMobileAccessoryStates.keys()) {
    if (session === String(exceptSession || '')) continue;
    dismissed = dismissTerminalMobileAccessory(session) || dismissed;
  }
  return dismissed;
}

function installTerminalMobileAccessoryDismissal() {
  if (terminalMobileAccessoryDismissalInstalled) return;
  terminalMobileAccessoryDismissalInstalled = true;
  // The key palette is an on-demand aid, not persistent pane chrome. A touch outside its own
  // launcher/palette means the user has resumed another terminal or app action, so close it.
  document.addEventListener('pointerdown', event => {
    const target = event.target;
    if (target?.closest?.('[data-terminal-mobile-keybar], [data-terminal-mobile-toggle]')) return;
    dismissTerminalMobileAccessories();
  }, {capture: true, passive: true});
}

function installTerminalMobileAccessoryResizeSync() {
  if (terminalMobileAccessoryResizeInstalled) return;
  terminalMobileAccessoryResizeInstalled = true;
  window.addEventListener('resize', () => {
    for (const [session, state] of terminalMobileAccessoryStates) {
      if (!Number.isFinite(state.x) && !Number.isFinite(state.y)) continue;
      const bar = document.querySelector(`[data-terminal-mobile-keybar="${cssEscape(session)}"]`);
      const pane = bar?.closest?.('.tab-pane');
      if (!bar || !pane) continue;
      state.x = Math.max(0, Math.min(pane.clientWidth - bar.offsetWidth, state.x));
      state.y = Math.max(0, Math.min(pane.clientHeight - bar.offsetHeight, state.y));
      syncTerminalMobileAccessoryState(session);
    }
  }, {passive: true});
}

const terminalMobileAccessoryLongPressMs = 450;
const terminalMobileAccessoryRepeatDelayMs = mobileTerminalKeyRepeatDelayMs;
const terminalMobileAccessoryRepeatIntervalMs = mobileTerminalKeyRepeatIntervalMs;

function terminalMobileAccessoryRepeats(action) {
  return ['arrow-up', 'arrow-down', 'arrow-left', 'arrow-right', 'tmux-scroll-up', 'tmux-scroll-down'].includes(action);
}

function beginTerminalMobileAccessoryKeyPress(session, action, event, button) {
  if (!terminalMobileAccessoryRepeats(action) || event.button > 0) return false;
  const key = String(session || '');
  const existing = terminalMobileAccessoryKeyPresses.get(key);
  if (existing) endTerminalMobileAccessoryKeyPress(key, event, button);
  const press = {action, pointerId: event.pointerId, delayTimer: null, repeatTimer: null};
  terminalMobileAccessoryKeyPresses.set(key, press);
  button?.setPointerCapture?.(event.pointerId);
  // Send the first key on touch-down, then keep repeating it while held, as a hardware arrow key
  // does. Preventing default keeps xterm's hidden input focused and the palette open.
  sendTerminalMobileAccessoryInput(key, action);
  press.delayTimer = window.setTimeout(() => {
    if (terminalMobileAccessoryKeyPresses.get(key) !== press) return;
    const repeat = () => {
      if (terminalMobileAccessoryKeyPresses.get(key) !== press) return;
      sendTerminalMobileAccessoryInput(key, action);
      press.repeatTimer = window.setTimeout(repeat, terminalMobileAccessoryRepeatIntervalMs);
    };
    press.repeatTimer = window.setTimeout(repeat, terminalMobileAccessoryRepeatIntervalMs);
  }, terminalMobileAccessoryRepeatDelayMs);
  event.preventDefault();
  event.stopPropagation();
  return true;
}

function endTerminalMobileAccessoryKeyPress(session, event, button) {
  const key = String(session || '');
  const press = terminalMobileAccessoryKeyPresses.get(key);
  if (!press || press.pointerId !== event.pointerId) return false;
  window.clearTimeout(press.delayTimer);
  window.clearTimeout(press.repeatTimer);
  terminalMobileAccessoryKeyPresses.delete(key);
  terminalMobileAccessorySuppressedClicks.set(key, press.action);
  button?.releasePointerCapture?.(event.pointerId);
  event.preventDefault();
  event.stopPropagation();
  return true;
}

function consumeTerminalMobileAccessoryKeyClick(session, action) {
  const key = String(session || '');
  if (terminalMobileAccessorySuppressedClicks.get(key) !== action) return false;
  terminalMobileAccessorySuppressedClicks.delete(key);
  return true;
}

function beginTerminalMobileAccessoryLauncherPress(session, event) {
  const state = terminalMobileAccessoryState(session, {create: true});
  if (!state || event.button > 0 || event.pointerType === 'mouse') return false;
  dismissTerminalMobileAccessories(session);
  const press = {pointerId: event.pointerId, longPressed: false, timer: null};
  state.launcherPress = press;
  press.timer = window.setTimeout(() => {
    if (terminalMobileAccessoryState(session)?.launcherPress !== press) return;
    press.longPressed = true;
    state.open = true;
    state.more = true;
    syncTerminalMobileAccessoryState(session);
  }, terminalMobileAccessoryLongPressMs);
  return true;
}

function endTerminalMobileAccessoryLauncherPress(session, event) {
  const state = terminalMobileAccessoryState(session);
  const press = state?.launcherPress;
  if (!state || !press || press.pointerId !== event.pointerId) return false;
  window.clearTimeout(press.timer);
  state.launcherPress = null;
  if (!press.longPressed) return false;
  state.suppressLauncherClick = true;
  event.preventDefault();
  event.stopPropagation();
  return true;
}

function consumeTerminalMobileAccessoryLauncherClick(session) {
  const state = terminalMobileAccessoryState(session);
  if (!state?.suppressLauncherClick) return false;
  state.suppressLauncherClick = false;
  return true;
}

function beginTerminalMobileAccessoryDrag(session, event, handle) {
  const state = terminalMobileAccessoryState(session, {create: true});
  const bar = handle?.closest?.('[data-terminal-mobile-keybar]');
  if (!state || !bar || event.button > 0) return false;
  const rect = bar.getBoundingClientRect();
  state.drag = {pointerId: event.pointerId, offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top};
  handle.setPointerCapture?.(event.pointerId);
  event.preventDefault();
  event.stopPropagation();
  return true;
}

function moveTerminalMobileAccessoryDrag(session, event, handle) {
  const state = terminalMobileAccessoryState(session);
  const drag = state?.drag;
  const bar = handle?.closest?.('[data-terminal-mobile-keybar]');
  const pane = bar?.closest?.('.tab-pane');
  if (!drag || drag.pointerId !== event.pointerId || !bar || !pane) return false;
  const paneRect = pane.getBoundingClientRect();
  state.x = Math.max(0, Math.min(paneRect.width - bar.offsetWidth, event.clientX - paneRect.left - drag.offsetX));
  state.y = Math.max(0, Math.min(paneRect.height - bar.offsetHeight, event.clientY - paneRect.top - drag.offsetY));
  syncTerminalMobileAccessoryState(session);
  event.preventDefault();
  return true;
}

function endTerminalMobileAccessoryDrag(session, event, handle) {
  const state = terminalMobileAccessoryState(session);
  if (!state?.drag || state.drag.pointerId !== event.pointerId) return false;
  handle.releasePointerCapture?.(event.pointerId);
  state.drag = null;
  event.preventDefault();
  return true;
}

function terminalDataWithMobileAccessoryModifiers(session, data) {
  const state = terminalMobileAccessoryState(session);
  const text = String(data || '');
  if (!state || (!state.ctrl && !state.alt) || !text) return text;
  const ctrl = state.ctrl === true;
  const alt = state.alt === true;
  state.ctrl = false;
  state.alt = false;
  syncTerminalMobileAccessoryState(session);
  let value = text;
  if (ctrl && text.length === 1) {
    const code = text.charCodeAt(0);
    if (code === 32) value = '\x00';
    else if (code === 63) value = '\x7f';
    else if (code >= 64 && code <= 95) value = String.fromCharCode(code & 0x1f);
    else if (code >= 97 && code <= 122) value = String.fromCharCode(code - 96);
  }
  return alt ? `\x1b${value}` : value;
}

async function pasteTerminalMobileAccessoryClipboard(session) {
  const clipboard = globalThis.navigator?.clipboard;
  if (!clipboard) {
    statusErr(t('common.clipboardUnavailable'));
    return false;
  }
  try {
    let text = '';
    if (typeof clipboard.read === 'function') {
      const items = await clipboard.read();
      const imageFiles = [];
      for (const item of items || []) {
        for (const type of item.types || []) {
          const blob = await item.getType(type);
          if (String(type).startsWith('image/')) imageFiles.push(new File([blob], pastedImageFilename('', type), {type}));
          else if (type === 'text/plain' && !text) text = await blob.text();
        }
      }
      if (imageFiles.length) {
        if (!beginPasteUpload(session)) return false;
        uploadFiles(session, imageFiles, {source: 'paste'}).finally(() => {
          pasteUploadInFlight = false;
        });
        return true;
      }
    }
    if (!text && typeof clipboard.readText === 'function') text = await clipboard.readText();
    if (!text) return false;
    noteTerminalExplicitInput(session);
    const sent = handleTerminalData(session, text, {mobileAccessory: true, bypassMobileAccessoryModifiers: true});
    // Palette keys already travel over the terminal socket; asking xterm to focus here summons
    // iPadOS's software keyboard after every arrow press.
    if (!sent) statusErr(terminalNotConnectedHtml(session));
    return sent;
  } catch (_error) {
    // Safari can reject a clipboard read after user activation expires; explain the failure instead
    // of pretending the terminal received text.
    statusErr(t('common.clipboardUnavailable'));
    return false;
  }
}

function sendTerminalMobileAccessoryInput(session, action) {
  if (action === 'copy') {
    const term = terminals.get(session)?.term || null;
    const container = document.getElementById(terminalDomId(session));
    void copyTerminalSelection(session, term, {}, container);
    return true;
  }
  if (action === 'command-p') {
    openFileQuickOpen();
    return true;
  }
  if (action === 'command-v') {
    void pasteTerminalMobileAccessoryClipboard(session);
    return true;
  }
  if (action === 'tmux-scroll-up' || action === 'tmux-scroll-down') {
    const item = terminals.get(session);
    const term = item?.term;
    const pageLines = Math.max(1, Math.floor((Number(term?.rows) || 24) * terminalWheelPageFraction));
    const signedLines = action === 'tmux-scroll-up' ? -pageLines : pageLines;
    if (!readOnlyMode && item?.socket?.readyState === WebSocket.OPEN) {
      queueTmuxScroll(item, signedLines);
      return true;
    }
    if (term) {
      queueLocalTerminalScroll(term, signedLines);
      return true;
    }
    statusErr(terminalNotConnectedHtml(session));
    return false;
  }
  if (action === 'ctrl' || action === 'alt' || action === 'more' || action === 'open') {
    toggleTerminalMobileAccessoryState(session, action);
    return true;
  }
  const data = terminalMobileAccessoryData(session, action);
  if (!data) return false;
  noteTerminalExplicitInput(session);
  const sent = handleTerminalData(session, data, {mobileAccessory: true, bypassMobileAccessoryModifiers: true});
  if (!sent) statusErr(terminalNotConnectedHtml(session));
  return sent;
}

function tmuxStatusModeForSession(session) {
  return tmuxStatusModes.get(String(session || '')) || 'none';
}

function tmuxStatusToggleHtml(session) {
  const mode = tmuxStatusModeForSession(session);
  const label = t(`pref.appearance.tmux_status_bar.${mode === 'none' ? 'off' : mode}`);
  const disabled = readOnlyMode ? ' disabled' : '';
  return `<button type="button" class="tab tmux-status-toggle tmux-status-toggle--${mode}" data-tmux-status-toggle="${esc(session)}" title="${esc(t('pref.appearance.tmux_status_bar.label'))}: ${esc(label)}" aria-label="${esc(t('pref.appearance.tmux_status_bar.label'))}: ${esc(label)}"${disabled}>${mode === 'top' ? '↑' : mode === 'bottom' ? '↓' : '·'}</button>`;
}

async function refreshTmuxStatusMode(session) {
  if (!isTmuxSession(session)) return;
  const payload = await apiFetchJson(`/api/tmux-status?session=${encodeURIComponent(session)}`, {cache: 'no-store'});
  const mode = ['top', 'bottom', 'none'].includes(payload?.status) ? payload.status : 'none';
  tmuxStatusModes.set(String(session), mode);
  const button = document.querySelector(`[data-tmux-status-toggle="${cssEscape(session)}"]`);
  if (button) button.outerHTML = tmuxStatusToggleHtml(session);
}

async function cycleTmuxStatusMode(session) {
  if (readOnlyMode) return;
  const payload = await apiFetchJson(`/api/tmux-status?session=${encodeURIComponent(session)}`, {method: 'POST'});
  const mode = ['top', 'bottom', 'none'].includes(payload?.status) ? payload.status : 'none';
  tmuxStatusModes.set(String(session), mode);
  const button = document.querySelector(`[data-tmux-status-toggle="${cssEscape(session)}"]`);
  if (button) button.outerHTML = tmuxStatusToggleHtml(session);
}

function createPanel(session) {
  const panel = document.createElement('article');
  panel.className = 'panel';
  panel.id = panelDomId(session);
  panel.innerHTML = panelFrameHtml({
    item: session,
    controlsHtml: panelControlsHtml(session),
    afterHeadHtml: `<div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-popover-zone panel-popover-zone">
          <div id="panel-tab-${session}" class="panel-session-label">${panelHeaderStateHtml(sessionState(session, transcriptMetadataState.payload.sessions?.[session]))}</div>
          <div id="meta-${session}" class="pane-info-bar-meta meta">${esc(t('pane.findingBranch'))}</div>
          ${sessionPopoverHtml(session, transcriptMetadataState.payload.sessions?.[session], sessionAgentKind(session), autoApproveStates.get(session)?.enabled === true, sessionState(session, transcriptMetadataState.payload.sessions?.[session]))}
        </div>
        ${isTmuxSession(session) ? tmuxWindowBarHtml(session, transcriptMetadataState.payload.sessions?.[session], {infoBar: true}) : ''}
        ${isTmuxSession(session) ? `<div id="meta-controls-${session}" class="pane-info-bar-controls"></div>` : ''}
        ${isTmuxSession(session) ? tmuxStatusToggleHtml(session) : ''}
      </div>`,
    bodyClass: 'tab-pane active',
    bodyAttributes: `id="terminal-pane-${esc(session)}"`,
    bodyHtml: `<div id="term-${session}" class="terminal"></div>${terminalMobileAccessoryHtml(session)}`,
    toastContentHtml: `<div id="upload-${session}" class="upload-result toast" hidden></div>`,
    afterBodyHtml: `<div id="transcript-pane-${session}" class="tab-pane">
        <div class="transcript">
          <div class="transcript-head">${esc(t('common.transcript'))}</div>
          <div id="transcript-path-${session}" class="transcript-path-row">${esc(t('pane.findingTranscript'))}</div>
          <div id="transcript-${session}" class="transcript-preview">${esc(t('pane.findingTranscript'))}</div>
        </div>
      </div>
      <div id="summary-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">${esc(t('menu.tmux.aiTranscript', {session: sessionLabel(session)}))}</div>
          <div id="summary-context-${session}" class="summary-context" data-locale-text-key="summary.loadingContext">${esc(t('summary.loadingContext'))}</div>
          <div id="summary-${session}" class="summary-preview markdown-body" data-locale-text-key="summary.emptyPrompt">${esc(t('summary.emptyPrompt'))}</div>
        </div>
      </div>
      <div id="events-pane-${session}" class="tab-pane">
        <div class="summary">
          <div class="transcript-head">${esc(t('events.title'))}</div>
          <div id="events-${session}" class="event-list" data-locale-text-key="events.loading">${esc(t('events.loading'))}</div>
        </div>
      </div>`,
  });
  bindPanelShell(panel, session);
  bindPanelControls(panel, session);
  return panel;
}

function setMetadataRefreshButtonLoading(button, loading, idleLabel, idleTitle) {
  if (!button) return;
  button.classList.toggle('loading', loading);
  button.disabled = loading;
  button.setAttribute('aria-busy', loading ? 'true' : 'false');
  button.textContent = idleLabel;
  button.title = loading ? t('info.loadingRepo') : idleTitle;
  button.setAttribute('aria-label', loading ? t('info.loadingRepo') : idleTitle);
}

function syncTranscriptMetaLoadingUi() {
  document.getElementById(panelDomId(infoItemId))?.querySelectorAll('[data-info-refresh]').forEach(button => {
    setMetadataRefreshButtonLoading(button, transcriptMetadataState.loading, t('common.refresh'), t('common.refresh'));
  });
  const metaRefreshButton = refreshMeta;
  if (metaRefreshButton) {
    metaRefreshButton.classList.toggle('loading', transcriptMetadataState.loading);
    metaRefreshButton.disabled = transcriptMetadataState.loading;
    metaRefreshButton.setAttribute('aria-busy', transcriptMetadataState.loading ? 'true' : 'false');
    refreshMetaButtonChrome();
  }
  document.getElementById(panelDomId(infoItemId))?.classList.toggle('metadata-loading', transcriptMetadataState.loading);
}

function infoMetadataLoadingHtml() {
  return `<div class="info-empty info-loading" role="status" aria-live="polite">
    <span class="info-loading-spinner" aria-hidden="true"></span>
    <span>${esc(t('info.loadingRepo'))}</span>
  </div>`;
}

function backgroundServerPortText(record) {
  const port = Number(record?.port);
  return Number.isFinite(port) && port > 0 ? `:${Math.trunc(port)}` : '';
}

function backgroundServerLabel(record, fallback = '') {
  const source = record && typeof record === 'object' ? record : {};
  const host = String(source.hostname || fallback || serverHostname || '').trim();
  const endpoint = host ? `${host}${backgroundServerPortText(source)}` : '';
  const root = compactHomePath(source.project_root || '');
  const pid = Number(source.pid);
  return [
    endpoint,
    root,
    Number.isFinite(pid) && pid > 0 ? t('backgroundOwner.pid', {pid: Math.trunc(pid)}) : '',
  ].filter(Boolean).join(' · ') || t('backgroundOwner.thisServer');
}

function backgroundOwnerRoleSummary(roleName, payload = backgroundOwnerStatusState.payload, options = {}) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const roles = data.roles && typeof data.roles === 'object' ? data.roles : {};
  const role = roles[roleName] && typeof roles[roleName] === 'object' ? roles[roleName] : {};
  const ownsRole = role.owner === true;
  const current = data.generation && typeof data.generation === 'object' ? data.generation : {};
  const owner = data.current_owner && typeof data.current_owner === 'object' ? data.current_owner : null;
  return {
    ownsRole,
    mode: ownsRole ? (options.ownerMode || 'leader') : (options.followerMode || 'follower'),
    state: ownsRole ? 'leader' : 'follower',
    currentLabel: backgroundServerLabel(current),
    ownerLabel: owner ? backgroundServerLabel(owner) : '',
    status: String(role.status || data.status || ''),
    error: String(data.last_error || ''),
  };
}

function backgroundOwnerSearchIndexSummary(payload = backgroundOwnerStatusState.payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const searchIndex = data.search_index && typeof data.search_index === 'object' ? data.search_index : {};
  const summary = backgroundOwnerRoleSummary('search-index', payload);
  const ownsIndex = searchIndex.owner === true || summary.ownsRole === true;
  const current = searchIndex.current_server && typeof searchIndex.current_server === 'object' ? searchIndex.current_server : data.generation;
  const owner = searchIndex.owner_server && typeof searchIndex.owner_server === 'object' ? searchIndex.owner_server : data.current_owner;
  return {
    ...summary,
    ownsIndex,
    ownsRole: ownsIndex,
    mode: ownsIndex ? 'leader' : 'follower',
    state: ownsIndex ? 'leader' : 'follower',
    currentLabel: backgroundServerLabel(current),
    ownerLabel: owner && typeof owner === 'object' ? backgroundServerLabel(owner) : '',
    status: String(searchIndex.status || summary.status || data.status || ''),
  };
}

function backgroundOwnerStatsSummary(payload = backgroundOwnerStatusState.payload) {
  return backgroundOwnerRoleSummary('stats-sampler', payload);
}

function backgroundOwnerSessionFilesSummary(payload = backgroundOwnerStatusState.payload) {
  return backgroundOwnerRoleSummary('session-files', payload);
}

function applyBackgroundOwnerStatusPayload(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  backgroundOwnerStatusState.guard.invalidate();
  backgroundOwnerStatusState.payload = payload;
  backgroundOwnerStatusState.updatedAt = Date.now();
  backgroundOwnerStatusState.error = '';
  backgroundOwnerStatusState.loading = false;
  if (options.render !== false) renderInfoPanel();
  if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
  return true;
}

const startupSnapshotFreshnessMs = 5_000;

function backgroundOwnerStatusIsFresh() {
  return Boolean(backgroundOwnerStatusState.payload)
    && Date.now() - Number(backgroundOwnerStatusState.updatedAt || 0) < startupSnapshotFreshnessMs;
}

async function refreshBackgroundOwnerStatus(options = {}) {
  if (shareViewMode) return false;
  // Every consumer observes the same current snapshot. A reconnect may require a new request
  // after this settles, but must not discard and duplicate the request boot already owns.
  if (backgroundOwnerStatusState.request) return backgroundOwnerStatusState.request;
  if (options.preferFresh === true && backgroundOwnerStatusIsFresh()) return true;
  const requestIsCurrent = backgroundOwnerStatusState.guard.begin();
  backgroundOwnerStatusState.loading = !backgroundOwnerStatusState.payload;
  backgroundOwnerStatusState.error = '';
  if (options.render !== false) renderInfoPanel();
  if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
  const request = (async () => {
    try {
      const payload = await apiFetchJson('/api/background/status', {cache: 'no-store'});
      if (!requestIsCurrent()) return false;
      return applyBackgroundOwnerStatusPayload(payload, options);
    } catch (error) {
      if (!requestIsCurrent()) return false;
      backgroundOwnerStatusState.error = userMessageSnapshot(error);
      backgroundOwnerStatusState.loading = false;
      if (options.render !== false) renderInfoPanel();
      if (typeof updateTopbarOwnerStatus === 'function') updateTopbarOwnerStatus();
      return false;
    } finally {
      if (backgroundOwnerStatusState.request === request) backgroundOwnerStatusState.request = null;
    }
  })();
  backgroundOwnerStatusState.request = request;
  return request;
}

// client-side mirror of the backend parse_pull_request_ref — normalize a watched-PR entry
// ("owner/repo#N", "owner/repo/N", or a github.com PR URL) to the canonical "owner/repo#N", else ''.
// Used to dedupe and to match a stored entry (which may be a URL) against a PR's canonical ref.
function normalizeWatchedPrRef(entry) {
  const text = String(entry || '').trim();
  if (!text) return '';
  const seg = '[A-Za-z0-9._-]+';
  if (/github\.com/i.test(text) && /:\/\//.test(text)) {
    const match = text.match(new RegExp(`github\\.com/(${seg})/(${seg})/(?:pull|pulls)/(\\d+)`, 'i'));
    if (match) return `${match[1]}/${match[2]}#${Number(match[3])}`;
    return '';
  }
  const short = text.match(new RegExp(`^(${seg})/(${seg})(?:#|/(?:pull/)?)(\\d+)$`));
  if (short && Number(short[3]) > 0) return `${short[1]}/${short[2]}#${Number(short[3])}`;
  return '';
}

async function refreshWatchedPrs() {
  try {
    const data = await apiFetchJson('/api/watched-prs');
    applyWatchedPrsPayload(data);
  } catch (_error) {}
}

function applyWatchedPrsPayload(data) {
  if (!data || typeof data !== 'object') return false;
  watchedPrsData = {
    watched_prs: Array.isArray(data.watched_prs) ? data.watched_prs : [],
    truncated: Number(data.truncated) || 0,
    invalid: Array.isArray(data.invalid) ? data.invalid : [],
  };
  notifyWatchedPrTransitions(watchedPrsData.watched_prs);
  return true;
}

function yoagentChatScrollOwner(node = document.getElementById('yoagent-content')) {
  return node?.querySelector?.('.yoagent-chat-history') || node || null;
}

function scrollYoagentChatToBottom(node = document.getElementById('yoagent-content')) {
  const owner = yoagentChatScrollOwner(node);
  if (owner) owner.scrollTop = owner.scrollHeight;
  yoagentScrollbackLocked = false;
}

function yoagentChatHistoryIsNearBottom(owner, threshold = 48) {
  if (!owner) return true;
  return owner.scrollHeight - owner.clientHeight - owner.scrollTop <= threshold;
}

function yoagentChatScrollState(node = document.getElementById('yoagent-content')) {
  const owner = yoagentChatScrollOwner(node);
  return {
    nearBottom: yoagentChatHistoryIsNearBottom(owner),
    ownerTop: owner ? owner.scrollTop : 0,
  };
}

function restoreYoagentChatScrollState(node, state) {
  if (!node || !state) return;
  const owner = yoagentChatScrollOwner(node);
  if (owner) owner.scrollTop = state.ownerTop || 0;
  yoagentScrollbackLocked = state.nearBottom === false;
}

function installYoagentChatScrollTracker(node = document.getElementById('yoagent-content')) {
  const history = yoagentChatScrollOwner(node);
  if (!history || history.dataset.yoagentScrollTracker === 'true') return;
  history.dataset.yoagentScrollTracker = 'true';
  history.addEventListener('scroll', () => {
    yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom(history);
  }, {passive: true});
  const form = node?.querySelector?.('[data-yoagent-chat-form]');
  if (!form || form.dataset.yoagentWheelForward === 'true') return;
  form.dataset.yoagentWheelForward = 'true';
  form.addEventListener('wheel', event => {
    const maxTop = history.scrollHeight - history.clientHeight;
    const delta = Number(event.deltaY || 0);
    if (maxTop <= 0 || !delta) return;
    const nextTop = Math.max(0, Math.min(maxTop, history.scrollTop + delta));
    if (nextTop === history.scrollTop) return;
    event.preventDefault();
    history.scrollTop = nextTop;
    yoagentScrollbackLocked = !yoagentChatHistoryIsNearBottom(history);
  }, {passive: false});
}

function yoagentOpenMessageDetailsState(node = document.getElementById('yoagent-content')) {
  const openKeys = new Set();
  (node?.querySelectorAll?.('.yoagent-message-details[open][data-yoagent-message-details-key]') || []).forEach(details => {
    const key = details.dataset?.yoagentMessageDetailsKey || '';
    if (key) openKeys.add(key);
  });
  return openKeys;
}

function restoreYoagentOpenMessageDetailsState(node, openKeys) {
  if (!node || !openKeys?.size) return;
  (node.querySelectorAll?.('.yoagent-message-details[data-yoagent-message-details-key]') || []).forEach(details => {
    const key = details.dataset?.yoagentMessageDetailsKey || '';
    if (key && openKeys.has(key)) details.open = true;
  });
}

function yoagentShouldScrollBottom(options, scrollState) {
  if (options.scrollBottom === true) return true;
  if (options.scrollBottom === false) return false;
  if (yoagentScrollbackLocked) return false;
  return scrollState?.nearBottom !== false;
}

function focusYoagentChatInput(node = document.getElementById('yoagent-content')) {
  const input = node?.querySelector?.('[data-yoagent-chat-input]');
  if (!input || input.disabled) return;
  input.focus({preventScroll: true});
  const end = input.value.length;
  try { input.setSelectionRange(end, end); } catch (_) {}
}

function yoagentChatInputIsFocused(node = document.getElementById('yoagent-content')) {
  const input = node?.querySelector?.('[data-yoagent-chat-input]');
  return Boolean(input && document.activeElement === input);
}

function restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd) {
  if (!inputFocused) return false;
  const nextInput = node?.querySelector?.('[data-yoagent-chat-input]');
  if (!nextInput || nextInput.disabled) return false;
  nextInput.focus({preventScroll: true});
  if (selectionStart !== null && selectionEnd !== null) {
    try { nextInput.setSelectionRange(selectionStart, selectionEnd); } catch (_) {}
  }
  return true;
}

function refreshYoagentSummaryRegions(node = document.getElementById('yoagent-content')) {
  if (!node) return false;
  const chat = node.querySelector('.yoagent-chat');
  if (!chat) return false;
  const openDetails = yoagentOpenMessageDetailsState(node);
  chat.outerHTML = yoagentChatHtml();
  renderConversationMessageMarkdown(node);
  restoreYoagentOpenMessageDetailsState(node, openDetails);
  installYoagentChatScrollTracker(node);
  return true;
}

function yoagentBusyUiIsMounted(node = document.getElementById('yoagent-content')) {
  return Boolean(yoagentChatState.busy && node?.querySelector?.('.yoagent-chat-status'));
}

// Downgrade block-level headings (#/##/### …) to inline bold so an embedded agent heading renders as
// emphasis inside a compact card instead of a giant h1/h2 that balloons its height. Inline emphasis,
// code, lists, and links are left intact for marked.js to render.
// the LLM backends emit "loose" markdown (blank lines between list items, double blank
// lines between sections) which marked.js renders with big gaps. Tighten ONLY the yoagent inputs
// (not the shared file-editor preview): collapse 2+ blank lines to one, and drop blank lines between
// adjacent list items so a loose list renders as tightly as a tight one.
function yoagentTightMarkdown(text) {
  return String(text || '')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^([ \t]*(?:[-*+]|\d+\.)[ \t].*)\n(?:[ \t]*\n)+(?=[ \t]*(?:[-*+]|\d+\.)[ \t])/gm, '$1\n');
}

function yoagentInlineMarkdown(text) {
  const downgraded = String(text || '').replace(/^[ \t]*#{1,6}[ \t]+(.*?)[ \t]*#*$/gm, (match, title) => (title ? `**${title}**` : ''));
  return yoagentTightMarkdown(downgraded);
}

function yoagentSessionFromHref(href) {
  try {
    const url = new URL(String(href || ''), window.location.href);
    return url.searchParams.get('yoagent-session') || '';
  } catch (_) {
    return '';
  }
}

function handleYoagentSessionLinkClick(event) {
  const anchor = event.target?.closest?.('a[href]');
  if (!anchor) return;
  const session = yoagentSessionFromHref(anchor.getAttribute('href') || '');
  if (!session) return;
  event.preventDefault();
  selectSession(session, {userInitiated: true});
}

function linkYoagentSessionCodeReferences(container) {
  if (!container) return;
  (container.querySelectorAll?.('code') || []).forEach(code => {
    if (code.closest('a')) return;
    const session = (code.textContent || '').trim();
    if (!session || !sessions.includes(session)) return;
    const previousText = code.previousSibling?.nodeType === Node.TEXT_NODE ? code.previousSibling.textContent || '' : '';
    if (!/(^|\b)(tmux\s+)?session\s*$/i.test(previousText)) return;
    const link = document.createElement('a');
    link.href = `?yoagent-session=${encodeURIComponent(session)}`;
    link.className = 'yoagent-session-link';
    link.title = t('yoagent.openSession', {session});
    code.replaceWith(link);
    link.appendChild(code);
  });
}

function installYoagentSessionLinks(container) {
  if (!container) return;
  linkYoagentSessionCodeReferences(container);
  if (container.dataset.yoagentSessionLinksBound !== 'true') {
    container.dataset.yoagentSessionLinksBound = 'true';
    container.addEventListener('click', handleYoagentSessionLinkClick);
  }
}

function renderConversationMessageMarkdown(node = document.getElementById('yoagent-content')) {
  // Render assistant chat replies through the Markdown pipeline so bold titles, code, lists, and links
  // display formatted. Without marked.js the escaped-text fallback stays.
  if (!node || typeof window.marked === 'undefined') return;
  (node.querySelectorAll?.('.yoagent-global [data-yoagent-global-markdown]') || []).forEach(body => {
    renderMarkdownPreviewInto(body, yoagentTightMarkdown(body.textContent || ''));
    installYoagentSessionLinks(body);
    body.removeAttribute('data-yoagent-global-markdown');
  });
  (node.querySelectorAll?.('.yoagent-message.assistant [data-yoagent-markdown]') || []).forEach(body => {
    renderMarkdownPreviewInto(body, yoagentTightMarkdown(body.textContent || ''));
    installYoagentSessionLinks(body);
    body.removeAttribute('data-yoagent-markdown');
  });
}

function renderYoagentPanel(options = {}) {
  const node = document.getElementById('yoagent-content');
  if (!node) return;
  const scrollState = yoagentChatScrollState(node);
  const openDetails = yoagentOpenMessageDetailsState(node);
  const shouldScrollBottom = yoagentShouldScrollBottom(options, scrollState);
  const input = node.querySelector('[data-yoagent-chat-input]');
  const inputFocused = input && document.activeElement === input;
  const selectionStart = inputFocused ? input.selectionStart : null;
  const selectionEnd = inputFocused ? input.selectionEnd : null;
  if (input && options.preserveDraft !== false) yoagentChatState.draft = input.value || '';
  if (yoagentBusyUiIsMounted(node) && options.allowBusyRebuild !== true) {
    if (refreshYoagentSummaryRegions(node)) {
      if (shouldScrollBottom) scrollYoagentChatToBottom(node);
      else restoreYoagentChatScrollState(node, scrollState);
      restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    }
    return;
  }
  if (options.summaryOnly && refreshYoagentSummaryRegions(node)) {
    if (shouldScrollBottom) scrollYoagentChatToBottom(node);
    else restoreYoagentChatScrollState(node, scrollState);
    restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
    return;
  }
  node.innerHTML = yoagentChatHtml();
  renderConversationMessageMarkdown(node);
  restoreYoagentOpenMessageDetailsState(node, openDetails);
  installYoagentChatScrollTracker(node);
  if (shouldScrollBottom) {
    requestAnimationFrame(() => scrollYoagentChatToBottom(node));
    setTimeout(() => scrollYoagentChatToBottom(node), 0);
  } else {
    restoreYoagentChatScrollState(node, scrollState);
  }
  if (options.focusInput) {
    requestAnimationFrame(() => focusYoagentChatInput(node));
    return;
  }
  if (!inputFocused) return;
  restoreYoagentChatInputFocus(node, inputFocused, selectionStart, selectionEnd);
}

function infoBranchRows() {
  if (shareViewMode && Array.isArray(shareInfoBranchRowsOverride)) return shareInfoBranchRowsOverride.slice();
  return rawInfoBranchRows();
}

const infoGroupingStorageKey = 'yolomux.info.grouping.v1';
const infoLegacyGroupingStorageKey = 'yolomux.info2.grouping.v1';
const infoSortStorageKey = 'yolomux.info.sort.v1';
const infoLegacySortStorageKey = 'yolomux.info2.sort.v1';
const infoDefaultGrouping = Object.freeze(['tab', 'path', 'tmux-window']);
const infoDefaultSort = Object.freeze({key: 'date', dir: 'desc'});
const infoSearchMaxLength = 240;
const infoSortDefs = Object.freeze([
  {key: 'name', dir: 'asc', value: 'name:asc', labelKey: 'finder.sort.az'},
  {key: 'name', dir: 'desc', value: 'name:desc', labelKey: 'finder.sort.za'},
  {key: 'date', dir: 'desc', value: 'date:desc', labelKey: 'common.sort.recent'},
  {key: 'date', dir: 'asc', value: 'date:asc', labelKey: 'finder.sort.oldest'},
]);
const infoDimensionDefs = Object.freeze([
  {key: 'tab', labelKey: 'info.dimension.tab'},
  {key: 'tmux-window', labelKey: 'info.field.tmuxSubWindow'},
  {key: 'ai', labelKey: 'info.dimension.ai'},
  {key: 'path', labelKey: 'common.pathLabel'},
  {key: 'branch', labelKey: 'common.branchLabel'},
  {key: 'linear', labelKey: 'info.field.linear'},
  {key: 'pr', labelKey: 'common.pullRequestShort'},
]);
const infoPresetDefs = Object.freeze([
  {key: 'tab-tmux-window', labelKey: 'info.preset.tabTmuxWindow.label', titleKey: 'info.preset.tabTmuxWindow.title', grouping: ['tab', 'tmux-window']},
  {key: 'tab-path', labelKey: 'info.preset.tabPath.label', titleKey: 'info.preset.tabPath.title', grouping: ['tab', 'path', 'tmux-window']},
  {key: 'path-branch', labelKey: 'info.preset.pathBranch.label', titleKey: 'info.preset.pathBranch.title', grouping: ['path', 'branch']},
  {key: 'linear-pr', labelKey: 'info.preset.linearPr.label', titleKey: 'info.preset.linearPr.title', grouping: ['linear', 'pr']},
  {key: 'pr-branch', labelKey: 'info.preset.prBranch.label', titleKey: 'info.preset.prBranch.title', grouping: ['pr', 'branch']},
]);
let infoGrouping = readStoredInfoGrouping();
let infoSort = readStoredInfoSort();
let infoSearch = '';
const infoCollapsedGroupKeys = new Set();

function localizedInfoDefinition(definition) {
  return {
    ...definition,
    ...(definition.labelKey ? {label: t(definition.labelKey)} : {}),
    ...(definition.titleKey ? {title: t(definition.titleKey)} : {}),
  };
}

function infoGroupDimensions() {
  return infoDimensionDefs.map(localizedInfoDefinition);
}

function infoGroupDimensionAllowedAtLevel(key, level, grouping = []) {
  const dimension = String(key || '').trim();
  const index = Number(level);
  if (!dimension || !Number.isInteger(index) || index < 0 || index > 3) return false;
  if (dimension === 'ai' && index === 0) return false;
  if (dimension === 'tmux-window') {
    const parent = Array.isArray(grouping) ? String(grouping[0] || '') : '';
    return index >= 1 && parent === 'tab';
  }
  return true;
}

function infoGroupDimensionsForLevel(level = 0, grouping = infoGrouping) {
  const index = Number(level);
  const normalizedIndex = Number.isInteger(index) ? Math.max(0, Math.min(3, index)) : 0;
  const activeGrouping = Array.isArray(grouping) ? grouping.slice() : normalizeInfoGrouping(grouping);
  return infoDimensionDefs
    .filter(dimension => infoGroupDimensionAllowedAtLevel(dimension.key, normalizedIndex, activeGrouping))
    .map(localizedInfoDefinition);
}

function infoSortFields() {
  return infoSortDefs.map(localizedInfoDefinition);
}

function infoGroupingPresets() {
  return infoPresetDefs.map(preset => ({...localizedInfoDefinition(preset), grouping: preset.grouping.slice()}));
}

function normalizeInfoGrouping(value, options = {}) {
  const valid = new Set(infoDimensionDefs.map(dimension => dimension.key));
  const input = Array.isArray(value) ? value : String(value || '').split(/[,\s|>]+/);
  const candidates = [];
  for (const item of input) {
    const key = String(item || '').trim();
    if (!key || !valid.has(key) || candidates.includes(key)) continue;
    candidates.push(key);
  }
  const migrated = options.migrateLegacyPresets ? normalizeInfoLegacyPresetGrouping(candidates) : candidates;
  const result = [];
  for (const key of Array.isArray(migrated) ? migrated : []) {
    if (!infoGroupDimensionAllowedAtLevel(key, result.length, result)) continue;
    result.push(key);
    if (result.length >= 4) break;
  }
  return (result.length ? result : infoDefaultGrouping).slice();
}

function normalizeInfoLegacyPresetGrouping(grouping) {
  const key = (Array.isArray(grouping) ? grouping : []).join('|');
  if (key === 'tab|ai|path|branch') return ['tab', 'path', 'tmux-window'];
  if (key === 'path|branch|tab|ai') return ['path', 'branch'];
  if (key === 'branch|path|tab|ai') return ['path', 'branch'];
  if (key === 'ai|tab|path|branch') return ['linear', 'pr'];
  return Array.isArray(grouping) ? grouping.slice() : infoDefaultGrouping.slice();
}

function readStoredInfoGrouping() {
  const raw = storageGet(infoGroupingStorageKey, '') || storageGet(infoLegacyGroupingStorageKey, '');
  if (!raw) return infoDefaultGrouping.slice();
  try {
    const parsed = JSON.parse(raw);
    return normalizeInfoGrouping(parsed, {migrateLegacyPresets: true});
  } catch (_) {
    return normalizeInfoGrouping(raw, {migrateLegacyPresets: true});
  }
}

function writeInfoGrouping(value) {
  infoGrouping = normalizeInfoGrouping(value);
  storageSet(infoGroupingStorageKey, JSON.stringify(infoGrouping));
  return infoGrouping.slice();
}

function currentInfoGrouping() {
  return infoGrouping.slice();
}

function normalizeInfoSort(value) {
  let raw = value;
  if (typeof raw === 'string') {
    try {
      raw = raw.trim().startsWith('{') ? JSON.parse(raw) : raw;
    } catch (_) {
      raw = value;
    }
  }
  if (typeof raw === 'string') {
    const text = raw.trim();
    if (['date-desc', 'date:desc', 'new', 'recent'].includes(text)) raw = {key: 'date', dir: 'desc'};
    else if (['date-asc', 'date:asc', 'old', 'oldest'].includes(text)) raw = {key: 'date', dir: 'asc'};
    else if (['name-asc', 'name:asc', 'az', 'a-z'].includes(text)) raw = {key: 'name', dir: 'asc'};
    else if (['name-desc', 'name:desc', 'za', 'z-a'].includes(text)) raw = {key: 'name', dir: 'desc'};
    else {
      const [key, dir] = text.split(/[:|,]/);
      raw = {key, dir};
    }
  }
  const rawKey = String(raw?.key || '').trim();
  const key = rawKey === 'date' ? 'date' : (rawKey ? 'name' : infoDefaultSort.key);
  const dir = String(raw?.dir || '').trim() === 'asc' ? 'asc' : 'desc';
  return {key, dir};
}

function readStoredInfoSort() {
  return normalizeInfoSort(storageGet(infoSortStorageKey, '') || storageGet(infoLegacySortStorageKey, '') || JSON.stringify(infoDefaultSort));
}

function writeInfoSort(value) {
  infoSort = normalizeInfoSort(value);
  storageSet(infoSortStorageKey, JSON.stringify(infoSort));
  return {...infoSort};
}

function currentInfoSort() {
  return {...infoSort};
}

function normalizeInfoSearch(value) {
  return String(value || '').slice(0, infoSearchMaxLength);
}

function currentInfoSearch() {
  return infoSearch;
}

function infoTreeGroupIdentity(group = {}) {
  const key = group.key ?? group.label ?? group.title ?? '';
  return [String(group.dimension || ''), String(key)];
}

function infoTreeGroupCollapseKey(group = {}, ancestorGroupIdentities = []) {
  const identities = Array.isArray(ancestorGroupIdentities) ? ancestorGroupIdentities.slice() : [];
  identities.push(infoTreeGroupIdentity(group));
  return encodeURIComponent(JSON.stringify(identities));
}

function setInfoTreeGroupCollapsed(key, collapsed) {
  const groupKey = String(key || '');
  if (!groupKey) return false;
  const wasCollapsed = infoCollapsedGroupKeys.has(groupKey);
  if (collapsed) infoCollapsedGroupKeys.add(groupKey);
  else infoCollapsedGroupKeys.delete(groupKey);
  return infoCollapsedGroupKeys.has(groupKey) !== wasCollapsed;
}

function pruneInfoTreeCollapsedGroups(activeKeys) {
  if (!(activeKeys instanceof Set) || !infoCollapsedGroupKeys.size) return;
  [...infoCollapsedGroupKeys].forEach(key => {
    if (!activeKeys.has(key)) infoCollapsedGroupKeys.delete(key);
  });
}

function refreshInfoGroupingControls() {
  document.querySelectorAll('.info-tree-actions-bar').forEach(bar => {
    const actions = bar.querySelector('.info-subtab-actions');
    bar.innerHTML = `${typeof infoGroupingControlsHtml === 'function' ? infoGroupingControlsHtml() : ''}${actions ? actions.outerHTML : ''}`;
  });
}

function setInfoGrouping(value) {
  const previous = infoGrouping.join(',');
  writeInfoGrouping(value);
  refreshInfoGroupingControls();
  renderInfoPanel();
  if (infoGrouping.join(',') !== previous) scheduleShareUiStatePublish();
}

function setInfoSort(value, options = {}) {
  const previous = `${infoSort.key}:${infoSort.dir}`;
  writeInfoSort(value);
  refreshInfoGroupingControls();
  renderInfoPanel();
  if (`${infoSort.key}:${infoSort.dir}` !== previous && options.publish !== false) scheduleShareUiStatePublish();
  return {...infoSort};
}

function setInfoSortMode(value, options = {}) {
  return setInfoSort(value, options);
}

function setInfoSearch(value, options = {}) {
  const previous = infoSearch;
  infoSearch = normalizeInfoSearch(value);
  if (options.refreshControls === true) refreshInfoGroupingControls();
  if (options.render !== false) renderInfoPanel();
  if (infoSearch !== previous && options.publish !== false) scheduleShareUiStatePublish();
  return infoSearch;
}

function setInfoGroupingPreset(key) {
  const preset = infoPresetDefs.find(item => item.key === key);
  if (preset) setInfoGrouping(preset.grouping);
}

function setInfoGroupingLevel(level, value) {
  const index = Number(level);
  if (!Number.isInteger(index) || index < 0 || index > 3) return;
  const next = infoGrouping.slice();
  const key = String(value || '').trim();
  next[index] = key;
  setInfoGrouping(next);
}

function infoRecordAiKind(record = {}) {
  const direct = String(record?.aiAgentKey || record?.aiKind || '').trim();
  if (direct && direct !== '__no_ai__' && direct !== 'no-ai') return direct;
  const label = String(record?.aiAgentLabel || record?.aiLabel || '').trim();
  if (!label || /^no\s+ai$/i.test(label)) return '';
  if (label.includes(':')) return label.split(':').pop().trim();
  return label;
}

function infoRecordAiAgentLabel(record = {}) {
  return infoAgentKindLabel(infoRecordAiKind(record));
}

function infoRecordTmuxWindowIndex(record = {}) {
  return String(record?.aiWindowIndex ?? record?.aiWindow ?? '').trim();
}

function infoRecordTmuxWindowLabel(record = {}) {
  if (!infoRecordHasAi(record)) return t('info.missing.tmuxSubWindow');
  return String(record?.tmuxWindowLabel || record?.aiLabel || '').trim() || t('info.field.tmuxSubWindow');
}

function infoRecordTmuxWindowKey(record = {}) {
  const explicit = String(record?.tmuxWindowKey || '').trim();
  if (explicit) return explicit;
  if (!infoRecordHasAi(record)) return '__no_tmux_window__';
  const index = infoRecordTmuxWindowIndex(record);
  return `${record?.tabSession || record?.tabKey || 'no-tab'}:${index || infoRecordTmuxWindowLabel(record)}:${infoRecordTmuxWindowLabel(record)}`;
}

function infoDimensionValue(record, dimension) {
  const fallback = {key: 'none', label: t('info.group.none'), title: ''};
  if (!record || !dimension) return fallback;
  if (dimension === 'tab') return {key: record.tabKey, label: record.tabLabel, title: record.tabTitle, sortValue: infoRecordNumericSortValue(record, 'tab')};
  if (dimension === 'tmux-window') return {key: infoRecordTmuxWindowKey(record), label: infoRecordTmuxWindowLabel(record), title: record.tmuxWindowTitle || record.aiTitle || infoRecordTmuxWindowLabel(record), sortValue: infoRecordNumericSortValue(record, 'tmux-window')};
  if (dimension === 'ai') return {key: infoRecordAiKind(record) || '__no_ai__', label: infoRecordAiAgentLabel(record), title: record.aiAgentTitle || infoRecordAiAgentLabel(record)};
  if (dimension === 'path') return {key: record.pathKey, label: record.pathTitle || record.pathLabel, title: record.pathTitle};
  if (dimension === 'branch') return {key: record.branchKey, label: record.branchLabel, title: record.branchTitle};
  if (dimension === 'pr') return {key: record.prKey, label: record.prTitle || record.prLabel, title: record.prTitle, sortValue: infoRecordNumericSortValue(record, 'pr')};
  if (dimension === 'linear') return {key: record.linearKey, label: record.linearTitle || record.linearLabel, title: record.linearTitle, sortValue: infoRecordNumericSortValue(record, 'linear')};
  return fallback;
}

function infoFirstIntegerFromValue(value) {
  const match = String(value || '').match(/\d+/);
  return match ? Number(match[0]) : NaN;
}

function infoPrNumberFromValue(value) {
  const direct = Number(value);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const text = String(value || '');
  const hashMatch = text.match(/#\s*(\d+)/);
  if (hashMatch) return Number(hashMatch[1]);
  const urlMatch = text.match(/\/pull\/(\d+)(?:\D|$)/);
  return urlMatch ? Number(urlMatch[1]) : NaN;
}

function infoRowPrNumber(row = {}) {
  for (const value of [row.prNumber, row.prLabel, row.prTitle, row.prSort, row.prUrl]) {
    const number = infoPrNumberFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoRelationshipRecords(rows = infoBranchRows()) {
  const records = [];
  const noTab = {
    session: '',
    label: t('info.missing.tabAndAi'),
    title: t('info.missing.tabOrAiForBranch'),
    kind: '',
    window: '',
    tabLabel: t('info.missing.tab'),
    aiLabel: t('info.missing.ai'),
  };
  for (const row of Array.isArray(rows) ? rows : []) {
    const directTabAgents = Array.isArray(row?.tabAgents) && row.tabAgents.length ? row.tabAgents : [];
    const pathTabAgents = Array.isArray(row?.pathTabAgents) && row.pathTabAgents.length ? row.pathTabAgents : [];
    const tabAgents = directTabAgents.length ? directTabAgents : (pathTabAgents.length ? pathTabAgents : [noTab]);
    for (const agent of tabAgents) {
      const session = String(agent?.session || '');
      const tabLabel = String(agent?.tabLabel || (session && typeof sessionLabel === 'function' ? sessionLabel(session) : session) || t('info.missing.tab'));
      const aiLabel = String(agent?.aiLabel || infoTabAgentAiLabel(agent));
      const aiKind = String(agent?.kind || '');
      const tmuxWindowIndex = String(agent?.windowIndex ?? agent?.window_index ?? agent?.window ?? '');
      const tmuxWindowKey = tmuxWindowIndex
        ? `${session || 'no-tab'}:${tmuxWindowIndex}:${aiLabel}`
        : '__no_tmux_window__';
      const tmuxWindowLabel = tmuxWindowKey === '__no_tmux_window__' ? t('info.missing.tmuxSubWindow') : aiLabel;
      const path = String(row?.path || '');
      const branch = String(row?.branch || '');
      const linearLabel = Array.isArray(row?.linearItems) && row.linearItems.length
        ? row.linearItems.map(item => item.identifier || item.url || '').filter(Boolean).join(', ')
        : String(row?.linearTitle || '').trim();
      const prKeyLabel = String(row?.prLabel || row?.prSort || row?.prTitle || '').trim();
      const prDisplayLabel = String(row?.prDescriptionTitle || row?.prTitle || row?.prSort || row?.prLabel || '').trim();
      const prNumber = infoRowPrNumber(row);
      const prCompactLabel = Number.isFinite(prNumber) ? `#${prNumber}` : prKeyLabel;
      records.push({
        id: [path, branch, session || 'no-tab', aiKind || 'no-ai', tmuxWindowIndex, prCompactLabel || prKeyLabel, linearLabel].join('\n'),
        tabKey: session || '__no_tab__',
        tabSession: session,
        tabLabel,
        tabTitle: String(agent?.title || tabLabel),
        aiKey: `${agent?.kind || 'no-ai'}:${agent?.window || ''}:${aiLabel}`,
        aiKind,
        aiAgentKey: aiKind || '__no_ai__',
        aiAgentLabel: infoAgentKindLabel(aiKind),
        aiAgentTitle: infoAgentKindLabel(aiKind),
        aiWindow: String(agent?.window || ''),
        aiWindowIndex: String(agent?.windowIndex ?? agent?.window_index ?? agent?.window ?? ''),
        aiState: String(agent?.state || ''),
        aiPane: String(agent?.pane || ''),
        aiPaneTarget: String(agent?.pane_target || ''),
        aiCurrent: agent?.current === true,
        aiWindowActive: agent?.window_active === true,
        aiPid: tmuxWindowProcessPid(agent),
        aiWorkingStoppedTs: Number.isFinite(Number(agent?.working_stopped_ts)) ? Number(agent.working_stopped_ts) : 0,
        aiIdleSince: Number.isFinite(Number(agent?.idle_since)) ? Number(agent.idle_since) : 0,
        aiLastActiveTs: Number.isFinite(Number(agent?.last_active_ts)) ? Number(agent.last_active_ts) : 0,
        aiLabel,
        aiTitle: String(agent?.title || aiLabel),
        tmuxWindowKey,
        tmuxWindowLabel,
        tmuxWindowTitle: String(agent?.title || tmuxWindowLabel),
        pathKey: infoNormalizedPath(path) || '__no_path__',
        pathLabel: String(row?.pathLabel || compactHomePath(path) || t('info.missing.path')),
        pathTitle: String(row?.pathTitle || path || t('info.missing.path')),
        pathActivityTs: Number.isFinite(row?.pathActivityTs) ? row.pathActivityTs : 0,
        pathActivitySource: String(row?.pathActivitySource || ''),
        branchKey: branch || '__no_branch__',
        branchLabel: branch || t('info.missing.branch'),
        branchTitle: branch || t('info.missing.branch'),
        branchHtml: row?.branchHtml || esc(branch || t('info.missing.branch')),
        prKey: prCompactLabel || prKeyLabel || '__no_pr__',
        prLabel: prCompactLabel || prKeyLabel || prDisplayLabel || t('info.missing.pr'),
        prTitle: prDisplayLabel || prCompactLabel || t('info.missing.pr'),
        prNumber: Number.isFinite(prNumber) ? prNumber : null,
        prUrl: String(row?.prUrl || ''),
        prClass: String(row?.prClass || ''),
        prLifecycleText: String(row?.prLifecycleText || ''),
        prLifecycleClass: String(row?.prLifecycleClass || ''),
        prCiText: String(row?.prCiText || ''),
        prCiClass: String(row?.prCiClass || ''),
        prHtml: infoPrCellHtml(row) || '',
        linearKey: linearLabel || '__no_linear__',
        linearLabel: linearLabel || t('info.missing.linear'),
        linearTitle: String(row?.linearTitle || linearLabel || t('info.missing.linear')),
        linearHtml: infoLinearCellHtml(row) || '',
        linearItems: Array.isArray(row?.linearItems) ? row.linearItems.slice(0, 20) : [],
        desc: String(row?.desc || ''),
        updated: String(row?.updatedText || row?.updated || ''),
        updatedTitle: String(row?.updatedTitle || row?.updated || ''),
        updatedTs: Number.isFinite(row?.updatedTs) ? row.updatedTs : 0,
        updatedSource: String(row?.updatedSource || ''),
      });
    }
  }
  return infoSortedRecords(records, infoSort);
}

function infoCompareLabels(left, right, direction = 1) {
  const leftMissing = infoRecordMissingValue(left);
  const rightMissing = infoRecordMissingValue(right);
  if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
  return String(left || '').localeCompare(String(right || ''), undefined, {sensitivity: 'base'}) * direction;
}

function infoCompareNumbers(left, right, direction = 1) {
  const leftNumber = Number(left);
  const rightNumber = Number(right);
  const leftHasNumber = Number.isFinite(leftNumber);
  const rightHasNumber = Number.isFinite(rightNumber);
  if (leftHasNumber && rightHasNumber && leftNumber !== rightNumber) return (leftNumber - rightNumber) * direction;
  if (leftHasNumber !== rightHasNumber) return leftHasNumber ? -1 : 1;
  return 0;
}

function infoRecordPrNumber(record = {}) {
  for (const value of [record.prNumber, record.prLabel, record.prTitle, record.prKey]) {
    const number = infoPrNumberFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoFirstRecordNumber(record = {}, values = []) {
  for (const value of values) {
    const number = infoFirstIntegerFromValue(value);
    if (Number.isFinite(number)) return number;
  }
  return NaN;
}

function infoRecordLinearNumber(record = {}) {
  const items = Array.isArray(record.linearItems) ? record.linearItems : [];
  for (const item of items) {
    const number = infoFirstRecordNumber(item, [item?.identifier, item?.title, item?.url]);
    if (Number.isFinite(number)) return number;
  }
  return infoFirstRecordNumber(record, [record.linearKey, record.linearLabel, record.linearTitle]);
}

function infoRecordNumericSortValue(record = {}, dimension = '') {
  if (dimension === 'pr') return infoRecordPrNumber(record);
  if (dimension === 'linear') return infoRecordLinearNumber(record);
  if (dimension === 'tab') return infoFirstRecordNumber(record, [record.tabKey, record.tabLabel, record.tabTitle, record.tabSession]);
  if (dimension === 'tmux-window') return infoFirstRecordNumber(record, [infoRecordTmuxWindowIndex(record), infoRecordTmuxWindowLabel(record), record.aiLabel, record.aiKey, record.aiTitle]);
  return NaN;
}

function infoCompareNumberThenLabel(leftNumber, rightNumber, leftLabel, rightLabel, direction = 1) {
  const leftMissing = infoRecordMissingValue(leftLabel);
  const rightMissing = infoRecordMissingValue(rightLabel);
  if (leftMissing !== rightMissing) return leftMissing ? 1 : -1;
  const leftHasNumber = Number.isFinite(Number(leftNumber));
  const rightHasNumber = Number.isFinite(Number(rightNumber));
  if (leftHasNumber && rightHasNumber && Number(leftNumber) !== Number(rightNumber)) return (Number(leftNumber) - Number(rightNumber)) * direction;
  if (leftHasNumber !== rightHasNumber) return leftHasNumber ? -1 : 1;
  return infoCompareLabels(leftLabel, rightLabel, direction);
}

function infoCompareRecordNumberThenLabel(left = {}, right = {}, dimension = '', direction = 1) {
  return infoCompareNumberThenLabel(
    infoRecordNumericSortValue(left, dimension),
    infoRecordNumericSortValue(right, dimension),
    infoRecordLabel(left, dimension),
    infoRecordLabel(right, dimension),
    direction,
  );
}

function infoRecordLabel(record = {}, dimension = '') {
  if (dimension === 'tab') return record.tabLabel;
  if (dimension === 'tmux-window') return infoRecordTmuxWindowLabel(record);
  if (dimension === 'ai') return infoRecordAiAgentLabel(record);
  if (dimension === 'linear') return record.linearLabel || record.linearTitle;
  if (dimension === 'pr') return record.prLabel || record.prTitle;
  if (dimension === 'path') return record.pathLabel;
  if (dimension === 'branch') return record.branchLabel;
  return '';
}

function infoSearchField(kind, ...values) {
  const text = values.map(singleLineText).filter(Boolean).join(' ');
  return text ? {kind, text} : null;
}

function infoSearchFields(kind, ...values) {
  const seen = new Set();
  return values
    .map(singleLineText)
    .filter(Boolean)
    .filter(value => {
      const key = value.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .map(text => ({kind, text}));
}

function infoPrSearchText(record = {}) {
  if (String(record?.prKey || '') === '__no_pr__') return '';
  const label = String(record?.prLabel || '').trim();
  const title = String(record?.prTitle || '').trim();
  const numberText = Number.isFinite(record?.prNumber)
    ? `#${record.prNumber}`
    : (!infoRecordMissingValue(label) ? label : '');
  const description = title && numberText && title.startsWith(numberText)
    ? title.slice(numberText.length).trim()
    : title;
  return [numberText, description, record.prLifecycleText, record.prCiText].filter(Boolean).join(' ');
}

function infoLinearSearchFields(record = {}) {
  if (String(record?.linearKey || '') === '__no_linear__') return [];
  const items = Array.isArray(record?.linearItems) ? record.linearItems : [];
  const fields = items
    .map(item => infoSearchField('linear', item?.identifier, item?.title, item?.state))
    .filter(Boolean);
  const label = String(record?.linearLabel || '').trim();
  const title = String(record?.linearTitle || '').trim();
  const description = title && label && title.startsWith(label) ? title.slice(label.length).trim() : title;
  const fallback = infoSearchField('linear', label, description);
  if (fallback) fields.push(fallback);
  return fields;
}

function infoRecordSearchFields(record = {}) {
  return [
    ...(infoRecordHasTab(record) ? infoSearchFields('tab', sessionLabel(record.tabSession), record.tabLabel, record.tabSession) : []),
    ...(infoRecordHasAi(record) ? infoSearchFields('tmux-window', infoRecordTmuxWindowLabel(record)) : []),
    ...(infoRecordHasAi(record) ? infoSearchFields('ai', infoRecordAiAgentLabel(record), infoRecordAiKind(record)) : []),
    !infoRecordMissingValue(record?.pathLabel) && String(record?.pathKey || '') !== '__no_path__'
      ? infoSearchField('path', record.pathTitle || record.pathLabel)
      : null,
    !infoRecordMissingValue(record?.branchLabel) && String(record?.branchKey || '') !== '__no_branch__'
      ? infoSearchField('branch', record.branchTitle || record.branchLabel)
      : null,
    infoSearchField('pr', infoPrSearchText(record)),
    ...infoLinearSearchFields(record),
    !infoRecordMissingValue(record?.updated) ? infoSearchField('updated', record.updated) : null,
  ].filter(Boolean);
}

function infoSearchFieldMatches(field = {}, query = infoSearch) {
  const text = String(query || '').trim();
  return Boolean(text && Number.isFinite(fuzzySearchScore(text, [field.text])));
}

function infoRecordSearchKindMatches(record = {}, kind = '', query = infoSearch) {
  const text = String(query || '').trim();
  if (!text || !kind) return false;
  return infoRecordSearchFields(record).some(field => field.kind === kind && infoSearchFieldMatches(field, text));
}

function infoRecordMatchesSearch(record = {}, query = infoSearch) {
  const text = String(query || '').trim();
  if (!text) return true;
  return infoRecordSearchFields(record).some(field => infoSearchFieldMatches(field, text));
}

function infoFilteredRecords(records = [], query = infoSearch) {
  return (Array.isArray(records) ? records : []).filter(record => infoRecordMatchesSearch(record, query));
}

function infoSearchHighlightHtml(value, query = infoSearch) {
  return fuzzyHighlightHtml(query, value, {markClass: 'info-tree-search-match'});
}

function infoRecordSearchValueHtml(record = {}, kind = '', value = '', query = infoSearch) {
  return infoRecordSearchKindMatches(record, kind, query)
    ? infoSearchHighlightHtml(value, query)
    : esc(value);
}

function infoGroupSearchKindMatches(group = {}, query = infoSearch) {
  const dimension = String(group?.dimension || '');
  if (!dimension) return false;
  return (Array.isArray(group.records) ? group.records : []).some(record => infoRecordSearchKindMatches(record, dimension, query));
}

function infoGroupSearchValueHtml(group = {}, value = '', query = infoSearch) {
  return infoGroupSearchKindMatches(group, query)
    ? infoSearchHighlightHtml(value, query)
    : esc(value);
}

function infoHighlightedLinkHtml(url, label, title = '', className = '', highlight = false) {
  const labelHtml = highlight ? infoSearchHighlightHtml(label) : esc(label);
  if (!url) return `<span>${labelHtml}</span>`;
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const classAttr = className ? ` class="${esc(className)}"` : '';
  return `<a href="${esc(url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr}${classAttr}>${labelHtml}</a>`;
}

function compareInfoRecords(left, right, sort = infoSort, options = {}) {
  const normalizedSort = normalizeInfoSort(sort);
  const direction = normalizedSort.dir === 'desc' ? -1 : 1;
  let result = 0;
  if (normalizedSort.key === 'date') {
    result = infoCompareNumbers(left?.updatedTs, right?.updatedTs, direction);
  } else {
    result = infoCompareLabels(left?.pathLabel, right?.pathLabel, direction)
      || infoCompareLabels(left?.branchLabel, right?.branchLabel, direction)
      || infoCompareRecordNumberThenLabel(left, right, 'tab', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'ai', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'linear', direction)
      || infoCompareRecordNumberThenLabel(left, right, 'pr', direction);
  }
  if (result || options.fallback === false) return result;
  return (right?.updatedTs || 0) - (left?.updatedTs || 0)
    || infoCompareLabels(left?.pathLabel, right?.pathLabel)
    || infoCompareLabels(left?.branchLabel, right?.branchLabel)
    || infoCompareLabels(left?.tabLabel, right?.tabLabel)
    || infoCompareLabels(left?.aiLabel, right?.aiLabel)
    || infoCompareLabels(left?.prLabel, right?.prLabel);
}

function infoSortedRecords(records = [], sort = infoSort) {
  return (Array.isArray(records) ? records : []).slice().sort((left, right) => compareInfoRecords(left, right, sort));
}

function infoGroupRepresentativeRecord(group, sort = infoSort) {
  return infoSortedRecords(group?.records || [], sort)[0] || {};
}

function infoGroupTree(records = infoRelationshipRecords(), grouping = infoGrouping, sort = infoSort) {
  const levels = normalizeInfoGrouping(grouping);
  const build = (items, depth) => {
    const dimension = levels[depth];
    const sortedItems = infoSortedRecords(items, sort);
    if (!dimension) return {type: 'leaf-list', records: sortedItems};
    const groups = new Map();
    for (const record of sortedItems) {
      const value = infoDimensionValue(record, dimension);
      const key = String(value.key || value.label || 'none');
      if (!groups.has(key)) groups.set(key, {type: 'group', dimension, key, label: String(value.label || t('info.group.none')), title: String(value.title || value.label || ''), sortValue: value.sortValue, count: 0, records: [], children: []});
      const group = groups.get(key);
      if (!Number.isFinite(Number(group.sortValue)) && Number.isFinite(Number(value.sortValue))) group.sortValue = value.sortValue;
      group.count += 1;
      group.records.push(record);
    }
    const children = [...groups.values()]
      .sort((left, right) => compareInfoGroups(left, right, sort))
      .map(group => {
        const childTree = build(group.records, depth + 1);
        return {...group, children: childTree.children || childTree.records || []};
      });
    return {type: 'tree', dimension, children};
  };
  return build(records, 0);
}

function compareInfoGroups(left, right, sort = infoSort) {
  const normalizedSort = normalizeInfoSort(sort);
  const direction = normalizedSort.dir === 'desc' ? -1 : 1;
  if (normalizedSort.key === 'date') {
    const selectedResult = compareInfoRecords(infoGroupRepresentativeRecord(left, sort), infoGroupRepresentativeRecord(right, sort), sort, {fallback: false});
    if (selectedResult) return selectedResult;
  } else if (left?.dimension === right?.dimension && ['tab', 'tmux-window', 'linear', 'pr'].includes(left?.dimension)) {
    return infoCompareNumberThenLabel(left.sortValue, right.sortValue, left?.label, right?.label, direction)
      || (right?.count || 0) - (left?.count || 0);
  }
  return infoCompareLabels(left?.label, right?.label, normalizedSort.key === 'date' ? 1 : direction)
    || (right?.count || 0) - (left?.count || 0);
}

function infoTreeItemClasses(baseClass, options = {}) {
  return [
    baseClass,
    'info-tree-item',
    options.first ? 'info-tree-item-first' : '',
    options.last ? 'info-tree-item-last' : '',
  ].filter(Boolean).join(' ');
}

function infoRecordMissingValue(value) {
  const text = String(value || '').trim();
  return !text || /^no\s+(?:path|pr|linear|tab|ai|branch|tmux\s+sub-window)$/i.test(text);
}

function infoRecordHasTab(record) {
  return !infoRecordMissingValue(record?.tabLabel) && String(record?.tabKey || '') !== '__no_tab__' && Boolean(record?.tabSession);
}

function infoRecordHasAi(record) {
  return !infoRecordMissingValue(record?.aiLabel)
    && !String(record?.aiKey || '').startsWith('no-ai:')
    && Boolean(record?.tabSession)
    && String(record?.aiWindow || '') !== '';
}

function infoTabIsShown(record = {}) {
  const session = String(record?.tabSession || record?.tabKey || '').trim();
  return Boolean(session && typeof itemIsActivePaneTab === 'function' && itemIsActivePaneTab(session));
}

function infoRecordTabValueHtml(record = {}, options = {}) {
  if (!infoRecordHasTab(record)) return '';
  const label = String(options.label || record?.tabLabel || record?.tabSession || '').trim();
  if (!label) return '';
  const active = infoTabIsShown(record);
  const title = String(options.title || record?.tabTitle || label);
  const attrs = [`data-info-tab-state="${active ? 'active' : 'inactive'}"`];
  if (options.action !== false) attrs.push(`data-info-open-tab="${esc(record.tabSession)}"`);
  const sessionText = sessionLabel(record.tabSession);
  const sessionLabelHtml = infoRecordSearchKindMatches(record, 'tab')
    ? infoSearchHighlightHtml(sessionText)
    : undefined;
  return tmuxPaneTabTokenHtml(record.tabSession, {
    tag: options.action === false ? 'span' : 'button',
    classes: ['info-tree-tab-token', options.action === false ? 'info-tree-tab-token-static' : 'info-tree-tab-token-action'],
    active,
    title,
    attrs,
    sessionLabelHtml,
    leadingHtml: options.leadingHtml,
  });
}

function infoStatusBadgeHtml(record, text, className, options = {}) {
  const label = String(text || '').trim();
  if (!label) return '';
  const labelHtml = options.highlight ? infoSearchHighlightHtml(label) : esc(label);
  return pullRequestStatusBadgeHtml(record?.tabSession, label, className, {labelHtml});
}

function infoRecordPrStatusHtml(record) {
  const parts = [];
  const highlight = infoRecordSearchKindMatches(record, 'pr');
  if (record?.prLifecycleText) parts.push(infoStatusBadgeHtml(record, record.prLifecycleText, record.prLifecycleClass, {highlight}));
  if (record?.prCiText) parts.push(infoStatusBadgeHtml(record, record.prCiText, record.prCiClass, {highlight}));
  return parts.filter(Boolean).join(' ');
}

function infoRecordPrDescHtml(record) {
  if (String(record?.prKey || '') === '__no_pr__') return '';
  const text = String(record?.prTitle || record?.prLabel || '').trim();
  if (infoRecordMissingValue(text)) return '';
  const label = String(record?.prLabel || '').trim();
  const numberText = Number.isFinite(record?.prNumber)
    ? `#${record.prNumber}`
    : (!infoRecordMissingValue(label) ? label : '');
  const highlight = infoRecordSearchKindMatches(record, 'pr');
  if (!numberText) return infoHighlightedLinkHtml(record?.prUrl || '', text, record?.prUrl || record?.prTitle || text, record?.prClass || '', highlight);
  const description = text.startsWith(numberText) ? text.slice(numberText.length).trim() : (text === numberText ? '' : text);
  return [
    infoHighlightedLinkHtml(record?.prUrl || '', numberText, record?.prUrl || record?.prTitle || numberText, record?.prClass || '', highlight),
    description ? (highlight ? infoSearchHighlightHtml(description) : esc(description)) : '',
    infoRecordPrStatusHtml(record),
  ]
    .filter(Boolean)
    .join(' ');
}

function infoLinearItemDescHtml(item = {}, options = {}) {
  const identifier = String(item?.identifier || '').trim();
  const title = String(item?.title || '').trim();
  const url = String(item?.url || '').trim();
  if (!identifier && !title) return '';
  const href = url || linearIssueUrl(identifier);
  const highlight = options.highlight === true;
  const identifierHtml = identifier ? infoHighlightedLinkHtml(href, identifier, href || title || identifier, '', highlight) : '';
  const description = title && title !== identifier ? (highlight ? infoSearchHighlightHtml(title) : esc(title)) : '';
  return [identifierHtml, description].filter(Boolean).join(' ');
}

function infoRecordLinearDescHtml(record) {
  if (String(record?.linearKey || '') === '__no_linear__') return '';
  const highlight = infoRecordSearchKindMatches(record, 'linear');
  const items = Array.isArray(record?.linearItems) ? record.linearItems : [];
  const withDescriptions = items
    .map(item => ({
      identifier: String(item?.identifier || '').trim(),
      title: String(item?.title || '').trim(),
      url: String(item?.url || '').trim(),
    }))
    .filter(item => item.identifier || item.title);
  if (withDescriptions.length) {
    return withDescriptions.map(item => infoLinearItemDescHtml(item, {highlight})).filter(Boolean).join(' ');
  }
  const title = String(record?.linearTitle || '').trim();
  const label = String(record?.linearLabel || '').trim();
  if (infoRecordMissingValue(title) && infoRecordMissingValue(label)) return '';
  if (!infoRecordMissingValue(label)) {
    const description = title && title !== label
      ? (title.startsWith(label) ? title.slice(label.length).trim() : title)
      : '';
    const href = linearIssueUrl(label);
    return [
      infoHighlightedLinkHtml(href, label, href || title || label, '', highlight),
      description ? (highlight ? infoSearchHighlightHtml(description) : esc(description)) : '',
    ].filter(Boolean).join(' ');
  }
  return highlight ? infoSearchHighlightHtml(title) : esc(title);
}

function infoFieldLabel(kind) {
  const labels = {
    path: 'common.field.path',
    branch: 'info.field.gitBranch',
    pr: 'info.field.githubPr',
    linear: 'info.field.linear',
    tab: 'info.field.tabTmuxSession',
    ai: 'info.field.tmuxSubWindow',
    'tmux-window': 'info.field.tmuxSubWindow',
    updated: 'common.updated',
  };
  return t(labels[kind] || kind);
}

function infoRecordFieldHtml(kind, html, title = '') {
  if (!html) return '';
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  const label = infoFieldLabel(kind);
  return `<div class="info-tree-field info-tree-field-${esc(kind)}"${titleAttr}>
      <span class="info-tree-field-label">${esc(label)}:</span>
      <span class="info-tree-field-value">${html}</span>
    </div>`;
}

function infoRecordAgentPayload(record) {
  const agent = {
    kind: record.aiKind,
    state: record.aiState,
    window: record.aiWindow,
    window_index: record.aiWindowIndex || record.aiWindow,
    pane: record.aiPane,
    pane_target: record.aiPaneTarget,
    current: record.aiCurrent === true,
    window_active: record.aiWindowActive === true,
    working_stopped_ts: record.aiWorkingStoppedTs,
    idle_since: record.aiIdleSince,
    last_active_ts: record.aiLastActiveTs,
    pid: record.aiPid,
  };
  return agent;
}

function infoRecordCanonicalAgent(record) {
  const session = String(record?.tabSession || '').trim();
  const windowIndex = infoRecordTmuxWindowIndex(record);
  if (!session || !windowIndex || typeof agentWindowStatusForSessionWindow !== 'function') return null;
  const info = transcriptMetadataState.payload.sessions?.[session] || null;
  return agentWindowStatusForSessionWindow(session, windowIndex, info, autoApproveStates.get(session));
}

function infoRecordDisplayAgent(record) {
  return infoRecordCanonicalAgent(record) || {...infoRecordAgentPayload(record), state: ''};
}

function infoRecordAgentActivityItem(record) {
  if (!infoRecordHasAi(record)) return null;
  const agent = infoRecordCanonicalAgent(record);
  if (!agent) return null;
  return agentWindowActivityIconForStatusItem(agent, record.aiKind, record.tabSession, {
    current: false,
    window_active: false,
    scheduleRefresh: false,
  });
}

function infoTabGroupStatusItem(group = {}) {
  return infoTabGroupStatusRecord(group)?.item || null;
}

function infoTabGroupStatusRecord(group = {}) {
  if (group.dimension !== 'tab') return null;
  let best = null;
  for (const record of Array.isArray(group.records) ? group.records : []) {
    const item = infoRecordAgentActivityItem(record);
    const rank = agentWindowStatusItemVisualRank(item);
    if (!item || rank >= 9) continue;
    if (!best || rank < best.rank) best = {record, item, rank};
  }
  return best;
}

function infoTabGroupLeadingActivityHtml(group = {}) {
  const status = infoTabGroupStatusRecord(group);
  if (!status?.record || typeof agentWindowActivityIconHtmlForStatus !== 'function') return undefined;
  const record = status.record;
  const session = String(record?.tabSession || '').trim();
  if (!session) return undefined;
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const summary = sessionAgentWindowStatusSummary(session, info, autoApproveStates.get(session));
  const payload = autoApproveStates.get(session);
  const auto = payload?.enabled === true;
  const yoloHtml = yoloMarkerHtml(session, auto, {enabledOnly: false, toggle: !readOnlyMode, yoloWorking: false, payload});
  const agent = summary?.agent || infoRecordDisplayAgent(record);
  const activityHtml = summary?.item
    ? agentWindowActivityIconHtml(agent.kind, agent.state, agentWindowIdleSeconds(agent), {
      ...agentWindowActivityOptionsForStatus(agent, session),
      item: summary.item,
      statusOnly: true,
    })
    : agentWindowActivityIconHtmlForStatus(agent, record.aiKind, session, {statusOnly: true});
  return activityHtml ? `${yoloHtml}<span class="session-agent-activity-marker info-tree-tab-group-status">${activityHtml}</span>` : undefined;
}

function infoAgentAttentionHtml(record) {
  if (!infoRecordHasAi(record) || typeof agentWindowIsAttentionState !== 'function' || !agentWindowIsAttentionState(record.aiState)) return '';
  return '';
}

function infoRecordAiWindowButtonHtml(record, options = {}) {
  if (!infoRecordHasAi(record) || typeof tmuxWindowButtonHtml !== 'function') return '';
  const label = String(record?.aiLabel || '').trim();
  if (!label) return '';
  const labelHtml = infoRecordSearchValueHtml(record, 'tmux-window', label);
  const agent = infoRecordDisplayAgent(record);
  const active = agent.current === true || agent.window_active === true;
  const title = String(options.title || record.aiTitle || label);
  const attrs = options.action === false
    ? []
    : [
        `data-info-open-ai-tab="${esc(record.tabSession)}"`,
        `data-info-open-ai-window="${esc(record.aiWindow)}"`,
      ];
  return tmuxWindowButtonHtml({
    tag: options.action === false ? 'span' : 'button',
    classes: ['info-tree-ai-window-button'],
    session: record.tabSession,
    visibleName: label,
    labelHtml,
    numberLabel: record.aiWindowIndex || record.aiWindow || label,
    active,
    agentStatus: agent,
    agentKey: agent.kind || record.aiKind,
    title,
    attrs,
    ariaPressed: options.action !== false,
  });
}

function infoRecordAiRecencyHtml(record) {
  const agent = infoRecordDisplayAgent(record);
  const lastActive = Number(agent.idle_since || agent.last_active_ts || 0);
  if (!Number.isFinite(lastActive) || lastActive <= 0) return '';
  const text = typeof sessionPopoverAgentRecencyText === 'function'
    ? sessionPopoverAgentRecencyText(agent)
    : sessionFileRelativeTimeText(lastActive);
  return text ? `<span class="info-tree-ai-recency info-tree-trailing-meta">${esc(text)}</span>` : '';
}

function infoRecordAiPidHtml(record) {
  const pidText = tmuxWindowPidText(record?.aiPid);
  return pidText ? `<span class="info-tree-ai-pid">${esc(pidText)}</span>` : '';
}

function infoRecordAiValueHtml(record, options = {}) {
  if (!infoRecordHasAi(record)) return '';
  const buttonHtml = infoRecordAiWindowButtonHtml(record, options);
  if (!buttonHtml) return '';
  const status = infoAgentAttentionHtml(record);
  const pid = infoRecordAiPidHtml(record);
  const recency = infoRecordAiRecencyHtml(record);
  return `<span class="info-tree-ai-value tmux-window-bar info-tree-ai-window-token" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info">${buttonHtml}${status}${pid}${recency}</span>`;
}

function infoRecordUpdatedMetaHtml(record) {
  if (infoRecordMissingValue(record?.updated)) return '';
  const source = record?.updatedSource === 'git-commit' ? t('info.meta.gitCommit') : '';
  const text = [source, record.updated].filter(Boolean).join(' ');
  const title = [source, String(record?.updatedTitle || record.updated)].filter(Boolean).join(': ');
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  return `<span class="info-tree-meta-updated info-tree-trailing-meta"${titleAttr}>${infoRecordSearchValueHtml(record, 'updated', text)}</span>`;
}

function infoRecordPathActivityMetaHtml(record) {
  const timestamp = Number(record?.pathActivityTs || 0);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return '';
  const text = relativeTimeFormat(Math.max(0, Math.floor(Date.now() / 1000) - timestamp));
  const title = t('info.meta.latestPathActivity', {time: text});
  return `<span class="info-tree-meta-updated info-tree-meta-path-activity info-tree-trailing-meta" title="${esc(title)}">${esc(text)}</span>`;
}

function infoRecordMainChipsHtml(record, options = {}) {
  const hiddenDimensions = new Set(Array.isArray(options.hiddenDimensions) ? options.hiddenDimensions : []);
  const fields = [];
  const pathVisible = !hiddenDimensions.has('path') && !infoRecordMissingValue(record?.pathLabel) && String(record?.pathKey || '') !== '__no_path__';
  const branchVisible = !hiddenDimensions.has('branch') && !infoRecordMissingValue(record?.branchLabel) && String(record?.branchKey || '') !== '__no_branch__';
  const updatedMeta = infoRecordUpdatedMetaHtml(record);
  const linearDesc = infoRecordLinearDescHtml(record);
  if (!hiddenDimensions.has('linear') && linearDesc) fields.push(infoRecordFieldHtml('linear', linearDesc, record.linearTitle));
  const prDesc = infoRecordPrDescHtml(record);
  if (!hiddenDimensions.has('pr') && prDesc) fields.push(infoRecordFieldHtml('pr', prDesc, record.prTitle));
  if (!hiddenDimensions.has('tab') && infoRecordHasTab(record)) {
    fields.push(infoRecordFieldHtml('tab', infoRecordTabValueHtml(record), record.tabTitle));
  }
  if (!hiddenDimensions.has('tmux-window') && infoRecordHasAi(record)) {
    fields.push(infoRecordFieldHtml('ai', infoRecordAiValueHtml(record), record.aiTitle));
  }
  if (pathVisible) {
    const pathText = String(record?.pathTitle || record?.pathLabel || '').trim();
    fields.push(infoRecordFieldHtml('path', `<button type="button" class="info-tree-action-link info-tree-action-link-path" data-info-open-path="${esc(record.pathKey || pathText)}" title="${esc(pathText)}">${infoRecordSearchValueHtml(record, 'path', pathText)}</button>${infoRecordPathActivityMetaHtml(record)}`, record.pathTitle));
  }
  if (branchVisible) {
    const branchText = String(record?.branchTitle || record?.branchLabel || '').trim();
    fields.push(infoRecordFieldHtml('branch', `<span class="info-tree-value-text">${infoRecordSearchValueHtml(record, 'branch', branchText)}</span>${updatedMeta}`, record.branchTitle));
  }
  return fields.join('');
}

function infoRecordHtml(record, options = {}) {
  return `<div class="${esc(infoTreeItemClasses('info-tree-record', options))}" data-info-record="${esc(record.id)}">
    <div class="info-tree-record-main">${infoRecordMainChipsHtml(record, options)}</div>
  </div>`;
}

function infoTreeHiddenDimensions(ancestors, dimension) {
  return Array.from(new Set([...(Array.isArray(ancestors) ? ancestors : []), dimension].filter(Boolean)));
}

const infoDimensionCountKeys = Object.freeze({
  tab: 'common.tabs',
  'tmux-window': 'info.count.tmuxWindow',
  ai: 'info.count.ai',
  path: 'common.pathCount',
  branch: 'info.count.branch',
  pr: 'info.count.pr',
  linear: 'info.count.linear',
});

function infoDimensionCountText(key, count) {
  return tPlural(infoDimensionCountKeys[key] || 'info.count.item', count);
}

function infoGroupLabelHtml(group = {}) {
  const label = String(group.label || '');
  if (group.dimension === 'path' && !infoRecordMissingValue(label) && String(group.key || '') !== '__no_path__') {
    const path = String(group.key || group.title || label);
    return `<span class="info-tree-group-label info-tree-group-label-path"><button type="button" class="info-tree-group-label-action" data-info-open-path="${esc(path)}" title="${esc(group.title || path)}">${infoGroupSearchValueHtml(group, label)}</button></span>`;
  }
  const representative = infoGroupRepresentativeRecord(group);
  if (group.dimension === 'tmux-window') {
    const html = infoRecordAiValueHtml(representative, {action: false});
    if (html) return `<span class="info-tree-group-label info-tree-group-label-ai">${html}</span>`;
  }
  if (group.dimension === 'pr') {
    const html = infoRecordPrDescHtml(representative);
    return `<span class="info-tree-group-label info-tree-group-label-pr">${html || esc(t('info.group.none'))}</span>`;
  }
  if (group.dimension === 'linear') {
    const html = infoRecordLinearDescHtml(representative);
    return `<span class="info-tree-group-label info-tree-group-label-linear">${html || esc(t('info.group.none'))}</span>`;
  }
  if (group.dimension === 'tab') {
    const tabHtml = infoRecordTabValueHtml(representative, {
      action: false,
      label,
      title: group.title || label,
      leadingHtml: infoTabGroupLeadingActivityHtml(group),
    });
    return tabHtml || `<span class="info-tree-group-label">${infoGroupSearchValueHtml(group, label)}</span>`;
  }
  return `<span class="info-tree-group-label">${infoGroupSearchValueHtml(group, label)}</span>`;
}

function infoGroupChildCountHtml(group = {}) {
  const directChildGroups = Array.isArray(group.children) ? group.children.filter(child => child?.type === 'group') : [];
  if (directChildGroups.length <= 1) return '';
  const dimension = directChildGroups[0]?.dimension || '';
  return `<span class="info-tree-group-child-count">(${esc(infoDimensionCountText(dimension, directChildGroups.length))})</span>`;
}

function infoGroupDimensionLabel(key) {
  if (key === 'tab' || key === 'tmux-window' || key === 'branch' || key === 'pr') return `${infoFieldLabel(key)}:`;
  return `${infoDimensionLabel(key)}:`;
}

function infoTreeChildrenHtml(children, depth = 0, ancestorDimensions = [], ancestorGroupIdentities = [], activeGroupKeys = null) {
  if (!Array.isArray(children) || !children.length) return '';
  return children.map((child, index) => {
    const treeItemOptions = {first: index === 0, last: index === children.length - 1};
    if (child?.type !== 'group') return infoRecordHtml(child, {...treeItemOptions, hiddenDimensions: ancestorDimensions});
    const hiddenDimensions = infoTreeHiddenDimensions(ancestorDimensions, child.dimension);
    const groupKey = infoTreeGroupCollapseKey(child, ancestorGroupIdentities);
    const childGroupIdentities = [...ancestorGroupIdentities, infoTreeGroupIdentity(child)];
    if (activeGroupKeys instanceof Set) activeGroupKeys.add(groupKey);
    const nested = child.children?.length && child.children[0]?.type === 'group'
      ? infoTreeChildrenHtml(child.children, depth + 1, hiddenDimensions, childGroupIdentities, activeGroupKeys)
      : (child.children || []).map((record, recordIndex, records) => infoRecordHtml(record, {
        first: recordIndex === 0,
        last: recordIndex === records.length - 1,
        hiddenDimensions,
      })).join('');
    const childCount = infoGroupChildCountHtml(child);
    const trailingMeta = child.dimension === 'path' ? infoRecordPathActivityMetaHtml(infoGroupRepresentativeRecord(child)) : '';
    const openAttr = infoCollapsedGroupKeys.has(groupKey) ? '' : ' open';
    return `<details class="${esc(infoTreeItemClasses('info-tree-group', treeItemOptions))}" data-info-dimension="${esc(child.dimension)}" data-info-depth="${depth}" data-info-group-key="${esc(groupKey)}"${openAttr}>
      <summary title="${esc(child.title)}">
        <span class="info-tree-group-dimension">${esc(infoGroupDimensionLabel(child.dimension))}</span>
        <span class="info-tree-group-label-line">${infoGroupLabelHtml(child)}${childCount}${trailingMeta}</span>
      </summary>
      <div class="info-tree-group-children">${nested}</div>
    </details>`;
  }).join('');
}

function infoDimensionLabel(key) {
  return infoGroupDimensions().find(dimension => dimension.key === key)?.label || key;
}

function infoTreeHtml(records = infoRelationshipRecords(), grouping = infoGrouping, sort = infoSort) {
  const normalizedSort = normalizeInfoSort(sort);
  const tree = infoGroupTree(records, grouping, normalizedSort);
  const activeGroupKeys = new Set();
  const childrenHtml = infoTreeChildrenHtml(tree.children || [], 0, [], [], activeGroupKeys);
  pruneInfoTreeCollapsedGroups(activeGroupKeys);
  return `<div class="info-tree" data-info-grouping="${esc(normalizeInfoGrouping(grouping).join(','))}" data-info-sort="${esc(`${normalizedSort.key}:${normalizedSort.dir}`)}" data-info-search="${esc(infoSearch.trim())}">${childrenHtml}</div>`;
}

function infoPanelRenderVisible() {
  return activePaneItems().includes(infoItemId);
}

function infoPanelRenderSignature() {
  return JSON.stringify({
    loading: transcriptMetadataState.loading,
    loaded: transcriptMetadataState.loaded,
    error: transcriptMetadataState.error,
    search: infoSearch,
    grouping: infoGrouping,
    sort: infoSort,
    meta: transcriptMetadataState.payload,
    agentStatus: sessions.map(session => [session, agentWindowStatusVisualSignature(autoApproveStates.get(session) || {})]),
  });
}

function renderInfoPanel(options = {}) {
  const node = document.getElementById('info-content');
  if (!node) return;
  if (options.force !== true && !infoPanelRenderVisible()) {
    recordClientPerfCounter('renderInfoPanel', 0, {skipped: 1});
    return;
  }
  let renderedNodes = 0;
  const perf = clientPerfStart('renderInfoPanel');
  try {
    return renderInfoPanelMeasured(node, options);
  } finally {
    renderedNodes = node.querySelectorAll?.('.info-tree-record, .info-tree-group')?.length || 0;
    clientPerfEnd(perf, {nodes: renderedNodes});
  }
}

function renderInfoPanelMeasured(node, options = {}) {
  const syncInfoContent = () => {
    if (typeof syncInfoTreeScrolledState === 'function') syncInfoTreeScrolledState(node.closest('.info-tree-panel'));
    if (typeof refreshPanePopouts === 'function') refreshPanePopouts(infoItemId);
  };
  const renderInfoContent = html => {
    node.innerHTML = html;
    syncInfoContent();
  };
  syncTranscriptMetaLoadingUi();
  const signature = infoPanelRenderSignature();
  if (options.force !== true && signature === infoPanelRenderCache.signature && infoPanelRenderCache.html) {
    const hasContent = Boolean(node.children?.length || String(node.innerHTML || '').trim());
    if (!hasContent) renderInfoContent(infoPanelRenderCache.html);
    else syncInfoContent();
    return;
  }
  const commitInfoContent = html => {
    infoPanelRenderCache.signature = signature;
    infoPanelRenderCache.html = html;
    renderInfoContent(html);
  };
  const allRecords = infoRelationshipRecords();
  const records = infoFilteredRecords(allRecords, infoSearch);
  if (!records.length) {
    if (allRecords.length && infoSearch.trim()) {
      commitInfoContent(`<div class="info-empty info-tree-empty">${localizedHtml('info.search.noMatches', {query: infoSearch.trim()})}</div>`);
      return;
    }
    if (transcriptMetadataState.loading) {
      commitInfoContent(infoMetadataLoadingHtml());
      return;
    }
    if (transcriptMetadataState.error) {
      commitInfoContent(`<div class="info-empty info-error">${esc(t('info.loadFailed'))} ${esc(transcriptMetadataLoadErrorText())}</div>`);
      return;
    }
    if (!transcriptMetadataState.loaded) {
      commitInfoContent(infoMetadataLoadingHtml());
      return;
    }
    commitInfoContent(`<div class="info-empty">${esc(t('info.empty'))}</div>`);
    return;
  }
  commitInfoContent(infoTreeHtml(records, infoGrouping, infoSort));
}

function infoPrCellHtml(row) {
  if (row?.prLabel) return linkHtml(row.prUrl || '', row.prLabel, row.prTitle || '', row.prClass || '');
  return row?.prHtml || '';
}

function infoLinearCellHtml(row) {
  if (Array.isArray(row?.linearItems)) {
    return row.linearItems.map(item => {
      if (item?.url) return linearIssueHtml(item);
      return linearIssueLinkHtml(item?.identifier || '');
    }).filter(Boolean).join(' ');
  }
  return row?.linearHtml || '';
}

function infoPathLabel(git) {
  const path = infoGitRoot(git);
  const label = compactHomePath(path);
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return label;
  return worktreeDisplayText({name: label, parent_root: compactHomePath(parent)});
}

function infoPathTitle(git) {
  const path = infoGitRoot(git);
  const parent = git?.worktree?.parent_root || '';
  if (!parent) return path;
  return worktreeDisplayText({name: path, parent_root: parent});
}

function infoGitRoot(git) {
  return String(git?.worktree?.path || git?.root || git?.cwd || '');
}

function infoNormalizedPath(value) {
  const text = String(value || '').trim();
  return typeof normalizeDirectoryPath === 'function' ? normalizeDirectoryPath(text) : text.replace(/\/+$/, '') || text;
}

function infoGitPathKey(git) {
  return infoNormalizedPath(infoGitRoot(git));
}

function infoPathIsWithin(path, root) {
  const normalizedPath = infoNormalizedPath(path);
  const normalizedRoot = infoNormalizedPath(root);
  return Boolean(normalizedPath && normalizedRoot && (normalizedPath === normalizedRoot || normalizedPath.startsWith(`${normalizedRoot}/`)));
}

function infoBranchSourcesForSession(session, info) {
  const project = info?.project || {};
  const primaryGit = project.git;
  const primaryKey = infoGitPathKey(primaryGit);
  const sources = [];
  const seenSources = new Set();
  const addSource = (git, primary) => {
    const sourceKey = infoGitPathKey(git);
    const branches = git?.other_branches?.branches;
    if (!sourceKey || !Array.isArray(branches) || !branches.length || seenSources.has(sourceKey)) return;
    seenSources.add(sourceKey);
    sources.push({session, info, project, git, primary: primary === true});
  };
  addSource(primaryGit, true);
  for (const repo of Array.isArray(project.repos) ? project.repos : []) {
    addSource(repo, Boolean(primaryKey && infoGitPathKey(repo) === primaryKey));
  }
  for (const row of Array.isArray(info?.window_metadata) ? info.window_metadata : []) {
    addSource(row?.git, Boolean(primaryKey && infoGitPathKey(row?.git) === primaryKey));
  }
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(session, info, autoApproveStates.get(session))
    : [];
  for (const agent of agents) {
    addSource(agent?.git, Boolean(primaryKey && infoGitPathKey(agent?.git) === primaryKey));
    for (const entry of typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : []) {
      addSource(entry?.git, Boolean(primaryKey && infoGitPathKey(entry?.git) === primaryKey));
    }
  }
  return sources;
}

function infoBranchSourcesForIndexedRepos() {
  const repos = Array.isArray(transcriptMetadataState.payload?.indexed_repos) ? transcriptMetadataState.payload.indexed_repos : [];
  return repos
    .filter(git => infoGitPathKey(git) && Array.isArray(git?.other_branches?.branches) && git.other_branches.branches.length)
    .map(git => ({session: '', info: {}, project: {}, git, primary: false, indexed: true}));
}

function infoBranchOwnedBySource(git, branch) {
  const branchName = String(branch?.name || '');
  const currentBranch = String(git?.branch || '');
  return branch?.current === true && Boolean(branchName) && (!currentBranch || currentBranch === branchName);
}

function infoBranchGitMatches(git, branchName, options = {}) {
  const gitBranch = String(git?.branch || '');
  if (options.requireBranch === true) return Boolean(branchName && gitBranch && gitBranch === branchName);
  return !branchName || !gitBranch || gitBranch === branchName;
}

function infoAgentWindowMatchesBranchGit(agent, sourceRoot, branchName, options = {}) {
  const pathEntries = typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : [];
  const gitCandidates = [
    agent?.git,
    ...(pathEntries.map(entry => entry?.git)),
  ].filter(git => git && typeof git === 'object');
  let sawMatchingRoot = false;
  for (const git of gitCandidates) {
    if (infoGitPathKey(git) !== infoNormalizedPath(sourceRoot)) continue;
    sawMatchingRoot = true;
    if (infoBranchGitMatches(git, branchName, options)) return true;
  }
  if (sawMatchingRoot) return false;
  if (options.requireBranch === true) return false;
  const paths = [
    agent?.path,
    ...(Array.isArray(agent?.paths) ? agent.paths : []),
    ...(pathEntries.map(entry => entry?.path)),
  ].filter(Boolean);
  return paths.some(path => infoPathIsWithin(path, sourceRoot));
}

function infoAgentWindowMatchesPathRoot(agent, sourceRoot) {
  const pathEntries = typeof agentWindowPathEntries === 'function' ? agentWindowPathEntries(agent) : [];
  const gitCandidates = [
    agent?.git,
    ...(pathEntries.map(entry => entry?.git)),
  ].filter(git => git && typeof git === 'object');
  if (gitCandidates.some(git => infoGitPathKey(git) === infoNormalizedPath(sourceRoot))) return true;
  const paths = [
    agent?.path,
    ...(Array.isArray(agent?.paths) ? agent.paths : []),
    ...(pathEntries.map(entry => entry?.path)),
  ].filter(Boolean);
  return paths.some(path => infoPathIsWithin(path, sourceRoot));
}

function infoSourceWindowRowsForBranch(source, branch, options = {}) {
  if (!source.session) return [];
  const root = infoGitPathKey(source.git);
  const branchName = String(branch?.name || source.git?.branch || '');
  return (Array.isArray(source.info?.window_metadata) ? source.info.window_metadata : [])
    .filter(row => {
      const git = row?.git || {};
      return infoGitPathKey(git) === root
        && infoBranchGitMatches(git, branchName, options);
    });
}

function infoTabAgentLabel(session, agent = null) {
  const tabLabel = typeof sessionLabel === 'function' ? sessionLabel(session) : String(session || '');
  if (!agent) return t('info.tabAgent.withoutAi', {tab: tabLabel});
  return `${tabLabel} / ${infoTabAgentAiLabel(agent)}`;
}

function infoTabAgentAiLabel(agent = null) {
  if (!agent) return t('info.missing.ai');
  if (agent.aiLabel) return String(agent.aiLabel);
  if (agent.label && String(agent.label).includes(' / ')) return String(agent.label).split(' / ').slice(1).join(' / ') || 'AI';
  const agentLabel = typeof agentWindowCanonicalLabel === 'function'
    ? agentWindowCanonicalLabel(agent.window_index ?? agent.window, agent.kind, agent.window_label || agent.label || agent.kind)
    : String(agent.label || agent.kind || 'AI');
  return agentLabel || 'AI';
}

function infoAgentKindLabel(value) {
  const kind = String(value || '').trim();
  return kind || t('info.missing.ai');
}

function infoTabAgentEntry(session, agent = null) {
  const tabLabel = typeof sessionLabel === 'function' ? sessionLabel(session) : String(session || '');
  const aiLabel = infoTabAgentAiLabel(agent);
  const aiKind = String(agent?.kind || '');
  const label = infoTabAgentLabel(session, agent);
  const title = agent
    ? `${label}${agent.state ? ` · ${agent.state}` : ''}${agent.path ? ` · ${agent.path}` : ''}`
    : `${label} · ${t('info.missing.tmuxSubWindow')}`;
  return {
    session: String(session || ''),
    label,
    title,
    tabLabel,
    aiLabel,
    kind: aiKind,
    aiAgentLabel: infoAgentKindLabel(aiKind),
    window: String(agent?.window_index ?? agent?.window ?? ''),
    windowIndex: String(agent?.window_index ?? agent?.window ?? ''),
    state: String(agent?.state || ''),
    pane: String(agent?.pane || ''),
    pane_target: String(agent?.pane_target || ''),
    current: agentWindowPayloadCurrent(agent) === true,
    window_active: agent?.window_active === true,
    pid: tmuxWindowProcessPid(agent),
    working_stopped_ts: Number.isFinite(Number(agent?.working_stopped_ts)) ? Number(agent.working_stopped_ts) : 0,
    idle_since: Number.isFinite(Number(agent?.idle_since)) ? Number(agent.idle_since) : 0,
    last_active_ts: Number.isFinite(Number(agent?.last_active_ts)) ? Number(agent.last_active_ts) : 0,
  };
}

function infoBranchTabAgentsForSource(source, branch) {
  if (!source.session) return [];
  const owned = infoBranchOwnedBySource(source.git, branch);
  const root = infoGitRoot(source.git);
  const branchName = String(branch?.name || source.git?.branch || '');
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(source.session, source.info, autoApproveStates.get(source.session))
    : [];
  const matchingAgents = agents.filter(agent => infoAgentWindowMatchesBranchGit(agent, root, branchName, {requireBranch: !owned}));
  if (matchingAgents.length) return matchingAgents.map(agent => infoTabAgentEntry(source.session, agent));
  const matchingWindows = infoSourceWindowRowsForBranch(source, branch, {requireBranch: !owned});
  if (!owned) {
    if (matchingWindows.length) return [infoTabAgentEntry(source.session, null)];
    return [];
  }
  if (source.primary === true && agents.length) return agents.map(agent => infoTabAgentEntry(source.session, agent));
  if (matchingWindows.length || source.primary === true || !agents.length) return [infoTabAgentEntry(source.session, null)];
  return [];
}

function infoPathTabAgentsForSource(source) {
  if (!source.session) return [];
  const root = infoGitRoot(source.git);
  if (!root) return [];
  const agents = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(source.session, source.info, autoApproveStates.get(source.session))
    : [];
  const matchingAgents = agents.filter(agent => infoAgentWindowMatchesPathRoot(agent, root));
  if (matchingAgents.length) return matchingAgents.map(agent => infoTabAgentEntry(source.session, agent));
  return [infoTabAgentEntry(source.session, null)];
}

function mergedInfoTabAgents(...groups) {
  const seen = new Set();
  const result = [];
  for (const group of groups) {
    for (const item of Array.isArray(group) ? group : []) {
      const label = String(item?.label || '');
      const session = String(item?.session || '');
      const key = `${session}\n${label}`;
      if (!label || seen.has(key)) continue;
      seen.add(key);
      result.push({
        session,
        label,
        title: String(item?.title || label),
        tabLabel: String(item?.tabLabel || (session && typeof sessionLabel === 'function' ? sessionLabel(session) : session) || ''),
        aiLabel: String(item?.aiLabel || infoTabAgentAiLabel(item)),
        kind: String(item?.kind || ''),
        window: String(item?.window || ''),
        windowIndex: String(item?.windowIndex ?? item?.window_index ?? item?.window ?? ''),
        state: String(item?.state || ''),
        pane: String(item?.pane || ''),
        pane_target: String(item?.pane_target || ''),
        current: item?.current === true,
        window_active: item?.window_active === true,
        working_stopped_ts: Number.isFinite(Number(item?.working_stopped_ts)) ? Number(item.working_stopped_ts) : 0,
        idle_since: Number.isFinite(Number(item?.idle_since)) ? Number(item.idle_since) : 0,
        last_active_ts: Number.isFinite(Number(item?.last_active_ts)) ? Number(item.last_active_ts) : 0,
      });
    }
  }
  return result;
}

function infoTabAgentsText(items) {
  return (Array.isArray(items) ? items : []).map(item => item?.label || '').filter(Boolean).join(', ');
}

function rowWithInfoTabAgents(row, tabAgents) {
  const merged = mergedInfoTabAgents(tabAgents);
  const pathAgents = mergedInfoTabAgents(row?.pathTabAgents);
  const text = infoTabAgentsText(merged);
  return {
    ...row,
    tabAgents: merged,
    tabAgentsTitle: merged.map(item => item.title || item.label).filter(Boolean).join('\n'),
    pathTabAgents: pathAgents,
    pathTabAgentsTitle: pathAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    session: text,
    current: merged.length > 0,
  };
}

function infoPathActivityForSource(source = {}) {
  const root = infoGitRoot(source.git);
  const repos = Array.isArray(source?.project?.repos) ? source.project.repos : [];
  const repo = repos.find(item => infoNormalizedPath(item?.root) === infoNormalizedPath(root));
  const timestamp = Number(repo?.activity_ts ?? source?.git?.activity_ts ?? 0);
  return {
    timestamp: Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0,
    source: String(repo?.activity_source || source?.git?.activity_source || ''),
  };
}

function infoBranchRowForSource(source, branch, ownsSession) {
  const {session, info, project, git, primary} = source;
  const useCurrentProjectMetadata = ownsSession && primary;
  const currentPr = useCurrentProjectMetadata ? displayPullRequest(info) : null;
  const currentLinear = useCurrentProjectMetadata ? project.linear || [] : [];
  const branchLinear = Array.isArray(branch.linear) ? branch.linear : [];
  const branchLinearIds = Array.isArray(branch.linear_ids) ? branch.linear_ids : [];
  const prLinearIds = Array.isArray(branch.pull_request?.linear_ids) ? branch.pull_request.linear_ids : [];
  const linearSourceItems = currentLinear.length ? currentLinear : branchLinear;
  const fallbackLinearIds = Array.from(new Set([...branchLinearIds, ...prLinearIds].map(item => String(item || '').trim()).filter(Boolean)));
  const linearIds = linearSourceItems.length
    ? linearSourceItems.map(issue => issue.identifier).filter(Boolean)
    : fallbackLinearIds;
  const linearHtml = linearSourceItems.length
    ? linearSourceItems.map(issue => linearIssueHtml(issue)).join(' ')
    : linearIds.map(linearIssueLinkHtml).filter(Boolean).join(' ');
  const prHtml = currentPr?.number ? pullRequestLinkHtml(currentPr) : pullRequestLinkForBranch(git, branch);
  const prValue = currentPr?.number ? currentPr : branch.pull_request;
  const prTitle = pullRequestTextForBranch(prValue, branch.subject || '');
  const prNumber = infoPrNumberFromValue(prValue?.number);
  const prDescriptionTitle = prValue?.number
    ? [`#${prValue.number}`, prValue.title || prValue.description || branch.subject || ''].filter(Boolean).join(' ')
    : '';
  const repoUrl = git?.github_repo?.url || '';
  const prUrl = prValue?.url || (prValue?.number && repoUrl ? `${repoUrl}/pull/${prValue.number}` : '');
  const prLabel = prValue?.number ? pullRequestLinkLabel(prValue) : '';
  const prClass = prValue?.number ? pullRequestStatusClass(prValue) : '';
  const prLifecycle = prValue?.number ? pullRequestLifecycleStatus(prValue) : null;
  const prLifecycleText = prLifecycle?.text || '';
  const prLifecycleClass = prLifecycle?.className || '';
  const prCiStatus = prValue?.number ? pullRequestCiStatus(prValue) : null;
  const prCiText = prCiStatus?.text || '';
  const prCiClass = prCiStatus?.className || '';
  const linearTitle = linearSourceItems.length
    ? linearSourceItems.map(issue => [issue.identifier, issue.state, issue.title].filter(Boolean).join(' ')).filter(Boolean).join(' · ')
    : linearIds.join(' ');
  const linearItems = linearSourceItems.length
    ? linearSourceItems.map(issue => ({
      identifier: String(issue?.identifier || ''),
      state: String(issue?.state || ''),
      title: String(issue?.title || ''),
      url: String(issue?.url || ''),
    })).filter(issue => issue.identifier || issue.url)
    : linearIds.map(identifier => ({identifier: String(identifier || '')})).filter(issue => issue.identifier);
  const desc = shortText(
    currentPr?.title
      || currentPr?.description
      || currentLinear.find(issue => issue.title)?.title
      || branch.subject
      || '',
    180,
  );
  const pathActivity = infoPathActivityForSource(source);
  return {
    session: '',
    path: infoGitRoot(git),
    pathLabel: infoPathLabel(git),
    pathTitle: infoPathTitle(git),
    pathActivityTs: pathActivity.timestamp,
    pathActivitySource: pathActivity.source,
    branch: branch.name || '',
    branchHtml: branchLinkHtml(git, branch.name),
    desc,
    updated: branch.updated || '',
    updatedText: branchUpdatedText(branch),
    updatedTitle: branch.updated || branchUpdatedText(branch),
    updatedTs: Number.isFinite(branch.updated_ts) ? branch.updated_ts : 0,
    updatedSource: 'git-commit',
    prHtml: prHtml || '',
    prTitle,
    prDescriptionTitle,
    prUrl,
    prLabel,
    prNumber: Number.isFinite(prNumber) ? prNumber : null,
    prClass,
    prLifecycleText,
    prLifecycleClass,
    prCiText,
    prCiClass,
    prSort: prTitle || (prValue?.number ? String(prValue.number) : ''),
    linearHtml,
    linearItems,
    linearTitle,
    current: false,
    sourcePrimary: primary,
    tabAgents: infoBranchTabAgentsForSource(source, branch),
    pathTabAgents: infoPathTabAgentsForSource(source),
  };
}

function preferInfoBranchMetadataRow(existing, next) {
  if (!existing) return next;
  if (next.tabAgents?.length && !existing.tabAgents?.length) return next;
  if (next.tabAgents?.length && next.sourcePrimary && !existing.sourcePrimary) return next;
  return existing;
}

function mergeInfoBranchRow(existing, next) {
  if (!existing) return rowWithInfoTabAgents(next, next.tabAgents);
  const preferred = preferInfoBranchMetadataRow(existing, next);
  const mergedAgents = mergedInfoTabAgents(existing.tabAgents, next.tabAgents);
  const mergedPathAgents = mergedInfoTabAgents(existing.pathTabAgents, next.pathTabAgents);
  return rowWithInfoTabAgents({...preferred, pathTabAgents: mergedPathAgents}, mergedAgents);
}

function rawInfoBranchRows() {
  const rowsByKey = new Map();
  const infoSessions = Array.isArray(transcriptMetadataState.payload?.session_order) ? transcriptMetadataState.payload.session_order : sessions;
  for (const session of infoSessions) {
    const info = transcriptMetadataState.payload.sessions?.[session];
    for (const source of infoBranchSourcesForSession(session, info)) {
      for (const branch of source.git?.other_branches?.branches || []) {
        const key = `${infoGitPathKey(source.git)}\n${branch.name || ''}`;
        const row = infoBranchRowForSource(source, branch, infoBranchOwnedBySource(source.git, branch));
        rowsByKey.set(key, mergeInfoBranchRow(rowsByKey.get(key), row));
      }
    }
  }
  for (const source of infoBranchSourcesForIndexedRepos()) {
    for (const branch of source.git?.other_branches?.branches || []) {
      const key = `${infoGitPathKey(source.git)}\n${branch.name || ''}`;
      const row = infoBranchRowForSource(source, branch, false);
      rowsByKey.set(key, mergeInfoBranchRow(rowsByKey.get(key), row));
    }
  }
  return [...rowsByKey.values()];
}

function shareInfoString(value, limit = 500) {
  return String(value || '').slice(0, limit);
}

function shareInfoTabAgentsSnapshot(items) {
  return Array.isArray(items)
    ? items.slice(0, 20).map(item => ({
      session: shareInfoString(item?.session, 80),
      label: shareInfoString(item?.label, 200),
      title: shareInfoString(item?.title, 500),
      tabLabel: shareInfoString(item?.tabLabel, 120),
      aiLabel: shareInfoString(item?.aiLabel, 120),
      kind: shareInfoString(item?.kind, 40),
      window: shareInfoString(item?.window, 40),
    })).filter(item => item.label)
    : [];
}

function shareInfoRowSnapshot(row = {}) {
  const tabAgents = shareInfoTabAgentsSnapshot(row.tabAgents);
  const pathTabAgents = shareInfoTabAgentsSnapshot(row.pathTabAgents);
  const tabAgentText = tabAgents.length ? infoTabAgentsText(tabAgents) : shareInfoString(row.session, 200);
  return {
    session: tabAgentText,
    tabAgents,
    tabAgentsTitle: tabAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    pathTabAgents,
    pathTabAgentsTitle: pathTabAgents.map(item => item.title || item.label).filter(Boolean).join('\n'),
    path: shareInfoString(row.path, 1000),
    pathLabel: shareInfoString(row.pathLabel, 1000),
    pathTitle: shareInfoString(row.pathTitle, 1000),
    pathActivityTs: Number.isFinite(row.pathActivityTs) ? row.pathActivityTs : 0,
    pathActivitySource: shareInfoString(row.pathActivitySource, 100),
    branch: shareInfoString(row.branch, 500),
    desc: shareInfoString(row.desc, 1000),
    updated: shareInfoString(row.updated, 200),
    updatedText: shareInfoString(row.updatedText, 200),
    updatedTitle: shareInfoString(row.updatedTitle, 500),
    updatedTs: Number.isFinite(row.updatedTs) ? row.updatedTs : 0,
    updatedSource: shareInfoString(row.updatedSource, 100),
    prTitle: shareInfoString(row.prTitle, 1000),
    prUrl: shareInfoString(row.prUrl, 1000),
    prLabel: shareInfoString(row.prLabel, 100),
    prNumber: Number.isFinite(infoRowPrNumber(row)) ? infoRowPrNumber(row) : null,
    prClass: shareInfoString(row.prClass, 100),
    prSort: shareInfoString(row.prSort, 1000),
    linearTitle: shareInfoString(row.linearTitle, 1000),
    linearItems: Array.isArray(row.linearItems)
      ? row.linearItems.slice(0, 20).map(item => ({
        identifier: shareInfoString(item?.identifier, 120),
        state: shareInfoString(item?.state, 120),
        title: shareInfoString(item?.title, 500),
        url: shareInfoString(item?.url, 1000),
      })).filter(item => item.identifier || item.url)
      : [],
    current: row.current === true,
  };
}

function cleanShareInfoRows(value) {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 1000).map(shareInfoRowSnapshot);
}

function shareInfoStateSnapshot(options = {}) {
  const snapshot = {
    grouping: currentInfoGrouping(),
    sort: currentInfoSort(),
    search: currentInfoSearch(),
  };
  if (options.includeRows !== false) snapshot.branchRows = infoBranchRows().map(shareInfoRowSnapshot);
  return snapshot;
}

function applyShareInfoState(info = {}) {
  if (!info || typeof info !== 'object') return;
  if ('grouping' in info || 'infoGrouping' in info || 'info2Grouping' in info) {
    infoGrouping = normalizeInfoGrouping(info.grouping || info.infoGrouping || info.info2Grouping);
  }
  if ('sort' in info || 'infoSort' in info || 'info2Sort' in info) {
    infoSort = normalizeInfoSort(info.sort || info.infoSort || info.info2Sort);
  }
  if ('search' in info || 'infoSearch' in info || 'info2Search' in info) {
    setInfoSearch(info.search ?? info.infoSearch ?? info.info2Search, {publish: false, render: false});
  }
  if ('branchRows' in info) shareInfoBranchRowsOverride = cleanShareInfoRows(info.branchRows);
  refreshInfoGroupingControls();
  renderInfoPanel({force: true});
  restoreShareScrollTargetByKey('info');
}

function bindPanelControls(panel, session) {
  delegate(panel, 'pointerdown', '[data-terminal-mobile-toggle]', (event, button) => {
    beginTerminalMobileAccessoryLauncherPress(button.dataset.terminalMobileToggle || session, event);
  });
  delegate(panel, 'pointerup', '[data-terminal-mobile-toggle]', (event, button) => {
    endTerminalMobileAccessoryLauncherPress(button.dataset.terminalMobileToggle || session, event);
  });
  delegate(panel, 'pointercancel', '[data-terminal-mobile-toggle]', (event, button) => {
    endTerminalMobileAccessoryLauncherPress(button.dataset.terminalMobileToggle || session, event);
  });
  delegate(panel, 'contextmenu', '[data-terminal-mobile-toggle]', event => {
    event.preventDefault();
  });
  delegate(panel, 'pointerdown', '[data-terminal-mobile-drag]', (event, handle) => {
    beginTerminalMobileAccessoryDrag(handle.dataset.terminalMobileDrag || session, event, handle);
  });
  delegate(panel, 'pointermove', '[data-terminal-mobile-drag]', (event, handle) => {
    moveTerminalMobileAccessoryDrag(handle.dataset.terminalMobileDrag || session, event, handle);
  });
  delegate(panel, 'pointerup', '[data-terminal-mobile-drag]', (event, handle) => {
    endTerminalMobileAccessoryDrag(handle.dataset.terminalMobileDrag || session, event, handle);
  });
  delegate(panel, 'pointercancel', '[data-terminal-mobile-drag]', (event, handle) => {
    endTerminalMobileAccessoryDrag(handle.dataset.terminalMobileDrag || session, event, handle);
  });
  delegate(panel, 'click', '[data-terminal-mobile-toggle]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const targetSession = button.dataset.terminalMobileToggle || session;
    if (consumeTerminalMobileAccessoryLauncherClick(targetSession)) return;
    sendTerminalMobileAccessoryInput(targetSession, 'open');
  });
  delegate(panel, 'pointerdown', '[data-terminal-mobile-key]', (event, button) => {
    // Do not blur xterm/close the software keyboard before the click sends its byte.
    if (beginTerminalMobileAccessoryKeyPress(button.dataset.terminalMobileSession || session, button.dataset.terminalMobileKey, event, button)) return;
    event.preventDefault();
    event.stopPropagation();
  });
  delegate(panel, 'pointerup', '[data-terminal-mobile-key]', (event, button) => {
    endTerminalMobileAccessoryKeyPress(button.dataset.terminalMobileSession || session, event, button);
  });
  delegate(panel, 'pointercancel', '[data-terminal-mobile-key]', (event, button) => {
    endTerminalMobileAccessoryKeyPress(button.dataset.terminalMobileSession || session, event, button);
  });
  delegate(panel, 'click', '[data-terminal-mobile-key]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const targetSession = button.dataset.terminalMobileSession || session;
    const action = button.dataset.terminalMobileKey;
    if (consumeTerminalMobileAccessoryKeyClick(targetSession, action)) return;
    sendTerminalMobileAccessoryInput(targetSession, action);
  });
  delegate(panel, 'click', '[data-tmux-status-toggle]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    void cycleTmuxStatusMode(button.dataset.tmuxStatusToggle).catch(error => statusErr(userMessageText(error, t('common.requestFailed'))));
  });
  delegate(panel, 'click', '[data-tab]', (_event, button) => {
    const currentName = button.dataset.tabName;
    const nextName = currentName !== 'terminal' && button.classList.contains(CLS.active) ? 'terminal' : currentName;
    activateTab(button.dataset.tab, nextName, {userInitiated: true});
  });
  delegate(panel, 'click', '[data-window-dir], [data-window-index]', (event, button) => {
    if (button.dataset.pointerActionHandled === '1') {
      delete button.dataset.pointerActionHandled;
      return;
    }
    handleWindowStepButtonClick(event);
  });
  delegate(panel, 'click', '[data-pane-close]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    closePaneFrameItem(button.dataset.paneClose);
  });
  delegate(panel, 'click', '[data-pane-minimize]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    minimizePaneFromLayout(button.dataset.paneMinimize);
  });
  delegate(panel, 'click', '[data-pane-expand]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    expandPaneFromLayout(button.dataset.paneExpand);
  });
  delegate(panel, 'click', '[data-pane-actions]', (event, button) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = button.getBoundingClientRect();
    showSessionContextMenu(button.dataset.paneActions || session, rect.left, rect.bottom + 4);
  });
  if (isTmuxSession(session)) {
    panel.querySelector('.panel-head')?.addEventListener('contextmenu', event => {
      if (event.target.closest('button, input')) return;
      event.preventDefault();
      event.stopPropagation();
      showSessionContextMenu(session, event.clientX, event.clientY);
    });
  }
  delegate(panel, 'click', '[data-context]', () => showContext(session));
  panel.addEventListener('click', event => {
    const target = event.target.closest('[data-auto-session]');
    if (!target || !panel.contains(target)) return;
    event.preventDefault();
    event.stopPropagation();
    toggleAutoApprove(target.dataset.autoSession || session);
  });
  delegate(panel, 'click', repoSelectorControlSelector, (event, button) => activateRepoSelectorControl(event, button, session));
  panel.querySelector('.meta')?.addEventListener('dragstart', event => event.stopPropagation());
  bindFileUpload(panel, session);
}

function hasFileDrag(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  return types.includes('Files') || Boolean(event.dataTransfer?.files?.length);
}

function hasUploadableDrag(event) {
  // External file drops (any type) OR an image exposed as rich data with no File entry — both must be
  // claimed so a dragged image never leaks to the terminal-backed agent as a rich [Image #N] attachment.
  return hasFileDrag(event) || dataTransferHasImagePayload(event?.dataTransfer);
}

function bindFileUpload(panel, session) {
  if (readOnlyMode) return;
  setFileUploadDropLabel(panel);
  panel.addEventListener('dragenter', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragover', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    panel.classList.add(CLS.fileDragOver);
  });
  panel.addEventListener('dragleave', event => {
    if (!hasUploadableDrag(event)) return;
    if (panel.contains(event.relatedTarget)) return;
    panel.classList.remove(CLS.fileDragOver);
  });
  panel.addEventListener('drop', event => {
    if (!hasUploadableDrag(event)) return;
    event.preventDefault();
    event.stopPropagation();
    panel.classList.remove(CLS.fileDragOver);
    // DOIT.57: remember the drop point so the post-upload suggestion overlay can anchor there.
    // Prefer the plain File list; fall back to images extracted from rich data (text/html <img>,
    // image MIME) so a dragged image exposed without a File still uploads instead of leaking.
    const dropped = event.dataTransfer?.files?.length ? event.dataTransfer.files : dataTransferImageFiles(event.dataTransfer);
    uploadFiles(session, dropped, {suggestAt: {x: event.clientX, y: event.clientY}});
  });
}

function setFileUploadDropLabel(panel) {
  if (panel) panel.dataset.fileDropLabel = t('drop.uploadOverlay');
}

function relocalizeFileUploadDropLabels() {
  document.querySelectorAll?.('.panel[data-file-drop-label]').forEach(panel => {
    setFileUploadDropLabel(panel);
  });
}

function insertFileDragPayloadIntoTerminal(session, payload) {
  const references = terminalFileReferences(session, payload);
  if (!references.length) return;
  const inserted = insertIntoTerminal(session, `${references.map(shellQuote).join(' ')} `);
  const label = references.length === 1 ? references[0] : tPlural('common.pathCount', references.length);
  statusEl.innerHTML = inserted
    ? `<span class="ok">${localizedHtml('status.insertedInto', {name: label, session: sessionLabel(session)})}</span>`
    : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
}

function bindClipboardPaste() {
  if (readOnlyMode) return;
  if (clipboardPasteBound) return;
  clipboardPasteBound = true;
  document.addEventListener('paste', event => {
    if (!dataTransferHasImagePayload(event.clipboardData)) return;
    const editorTarget = markdownEditorPasteTarget(event);
    // Image-bearing paste: ALWAYS claim it (preventDefault + stopPropagation) so the raw image can never
    // reach a CodeMirror editor or terminal-backed agent as a rich [Image #N] attachment.
    // Then upload ALL pasted images and insert only textual references for the focused surface.
    event.preventDefault();
    event.stopPropagation();
    if (editorTarget) {
      const files = dataTransferImageFiles(event.clipboardData);
      if (!files.length) {
        statusErr(localizedHtml('status.selectPaneForImagePaste'));
        return;
      }
      if (!beginPasteUpload(`editor:${editorTarget.path}`)) return;
      uploadEditorFiles(editorTarget, files).finally(() => {
        pasteUploadInFlight = false;
      });
      return;
    }
    const session = pasteTargetSession(event);
    if (!session) {
      statusErr(localizedHtml('status.selectPaneForImagePaste'));
      return;
    }
    const files = dataTransferImageFiles(event.clipboardData);
    if (!files.length) {
      // Claimed (so nothing leaks to the agent) but the image was exposed only as un-extractable rich
      // data (e.g. a remote <img> URL with no File and no data: URL).
      statusErr(localizedHtml('status.selectPaneForImagePaste'));
      return;
    }
    if (!beginPasteUpload(session)) return;
    uploadFiles(session, files, {source: 'paste'}).finally(() => {
      pasteUploadInFlight = false;
    });
  }, {capture: true});
}

function markdownEditorPasteTarget(event) {
  const eventPanel = event.target?.closest?.('.file-editor-panel') || null;
  const focusedPanel = !eventPanel && !focusedTerminal && isFileEditorItem(focusedPanelItem) ? panelNodes.get(focusedPanelItem) || null : null;
  const panel = eventPanel || focusedPanel;
  const view = panel?._cmView || null;
  if (!panel || !view || panel._cmMode === 'diff') return null;
  const path = fileEditorPanelPath(panel) || fileItemPath(fileEditorPanelItem(panel) || focusedPanelItem);
  if (!path || previewRendererForPath(path)?.id !== 'markdown') return null;
  return {panel, view, path};
}

// ONE shared image-payload contract for BOTH paste (clipboardData) and drop (dataTransfer). A browser may
// expose an image as a File, OR as rich data (a text/html <img>, an image MIME type) with NO File. ALL of
// these must be detectable so the handlers can CLAIM the event and never let a raw image reach the
// terminal-backed agent as a rich [Image #N] attachment. See AGENTS.md (rich-data drag/paste note).
function dataTransferHasImagePayload(dt) {
  if (!dt) return false;
  if (Array.from(dt.items || []).some(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))) return true;
  if (dt.files && Array.from(dt.files).some(file => String(file.type || '').startsWith('image/'))) return true;
  const types = Array.from(dt.types || []);
  if (types.some(type => String(type).startsWith('image/'))) return true;
  if (types.includes('text/html') && /<img\b/i.test(typeof dt.getData === 'function' ? (dt.getData('text/html') || '') : '')) return true;
  return false;
}

// Extract EVERY image in the payload as a renamed upload File, so multi-image prompts are deterministic
// (N images -> N uploaded path references, never one text ref + one attachment). Handles File items, a
// plain File list, and data: URL <img> sources embedded in text/html (browser image copies).
function dataTransferImageFiles(dt) {
  if (!dt) return [];
  const files = [];
  for (const item of Array.from(dt.items || [])) {
    if (item.kind !== 'file' || !String(item.type || '').startsWith('image/')) continue;
    const file = item.getAsFile?.();
    if (!file) continue;
    const type = file.type || item.type || 'image/png';
    files.push(new File([file], pastedImageFilename(file.name, type), {type}));
  }
  if (!files.length && dt.files) {
    for (const file of Array.from(dt.files)) {
      if (!String(file.type || '').startsWith('image/')) continue;
      const type = file.type || 'image/png';
      files.push(new File([file], pastedImageFilename(file.name, type), {type}));
    }
  }
  if (!files.length && typeof dt.getData === 'function') {
    const html = dt.getData('text/html') || '';
    const re = /<img\b[^>]*\bsrc\s*=\s*["']?(data:image\/[^"'\s>]+)/gi;
    let match;
    while ((match = re.exec(html))) {
      const file = dataUrlToImageFile(match[1]);
      if (file) files.push(file);
    }
  }
  return files;
}

function dataUrlToImageFile(dataUrl) {
  const match = /^data:(image\/[a-z0-9.+-]+);base64,(.*)$/i.exec(String(dataUrl || ''));
  if (!match) return null;
  const type = match[1];
  try {
    const binary = atob(match[2]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return new File([bytes], pastedImageFilename('', type), {type});
  } catch (_) {
    return null;
  }
}

function beginPasteUpload(session) {
  const now = Date.now();
  if (pasteUploadInFlight) return false;
  const existing = readStoredJson(pasteLockStorageKey, null);
  if (existing?.expiresAt && existing.expiresAt > now) return false;
  storageSet(pasteLockStorageKey, JSON.stringify({session, expiresAt: now + 1500}));
  pasteUploadInFlight = true;
  return true;
}

function pasteTargetSession(event) {
  const panel = event.target?.closest?.('.panel');
  const panelSession = panel?.id?.startsWith('panel-') ? panel.id.slice('panel-'.length) : '';
  if (sessions.includes(panelSession) && activeSessions.includes(panelSession)) return panelSession;
  if (focusedTerminal && activeSessions.includes(focusedTerminal)) return focusedTerminal;
  if (focusedPanelItem && sessions.includes(focusedPanelItem) && activeSessions.includes(focusedPanelItem)) return focusedPanelItem;
  if (lastFocusedTmuxSession && activeSessions.includes(lastFocusedTmuxSession)) return lastFocusedTmuxSession;
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  return activeTmuxSessions.length === 1 ? activeTmuxSessions[0] : null;
}

function nextPasteFilename(mimeType) {
  const stamp = pacificDateStamp();
  const suffix = imageSuffix(mimeType);
  const key = `${stamp}:${suffix}`;
  const next = nextPasteCounter(key);
  return `${stamp}-${String(next).padStart(3, '0')}${suffix}`;
}

function pastedImageFilename(originalName, mimeType) {
  const suffix = imageSuffixFromFilename(originalName) || imageSuffix(mimeType);
  const imageNumber = imageNumberFromFilename(originalName);
  if (Number.isFinite(imageNumber)) {
    return `${pacificDateStamp()}-${String(imageNumber).padStart(3, '0')}${suffix}`;
  }
  return nextPasteFilename(mimeType);
}

function nextPasteCounter(key) {
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key)) + 1;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
  return next;
}

function readPasteCounters() {
  const counters = readStoredJson(pasteCountersStorageKey, {});
  return counters && typeof counters === 'object' ? counters : {};
}

function writePasteCounters(counters) {
  storageSet(pasteCountersStorageKey, JSON.stringify(counters));
}

function pasteCounterValue(counters, key) {
  return Number(counters?.[key]) || 0;
}

function imageNumberFromFilename(filename) {
  const name = pathBasename(filename || '').replace(/\.[A-Za-z0-9]{1,8}$/, '');
  const match = name.match(/(?:^|[^A-Za-z])image[^0-9]*(\d+)(?:[^0-9]|$)/i);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function pasteUploadIndexFromPath(path) {
  const match = pathBasename(path || '').match(/^\d{8}-(\d{3})(?:\.[A-Za-z0-9]{1,8})$/);
  if (!match) return null;
  const value = Number(match[1]);
  return Number.isInteger(value) && value > 0 ? value : null;
}

function imageSuffixFromFilename(filename) {
  const match = String(filename || '').match(/(\.[A-Za-z0-9]{1,8})$/);
  if (!match) return '';
  const suffix = match[1].toLowerCase();
  return ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'].includes(suffix) ? (suffix === '.jpeg' ? '.jpg' : suffix) : '';
}

function pacificDateStamp() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/Los_Angeles',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}${values.month}${values.day}`;
}

function imageSuffix(mimeType) {
  const value = String(mimeType || '').toLowerCase();
  if (value.includes('jpeg') || value.includes('jpg')) return '.jpg';
  if (value.includes('gif')) return '.gif';
  if (value.includes('webp')) return '.webp';
  if (value.includes('bmp')) return '.bmp';
  return '.png';
}

async function uploadFiles(session, fileList, options = {}) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyUploadFiles'));
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const totalBytes = files.reduce((total, file) => total + (Number(file?.size) || 0), 0);
  if (uploadMaxBytes > 0 && totalBytes > uploadMaxBytes) {
    statusErr(localizedHtml('status.uploadTooLarge', {selected: formatFileSize(totalBytes), limit: formatFileSize(uploadMaxBytes)}));
    showUploadRsyncRecommendation({session, sizeBytes: totalBytes});
    return;
  }
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const payload = await apiFetchJson(`/api/upload?session=${encodeURIComponent(session)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    const paths = (payload.files || []).map(file => file.path).filter(Boolean);
    if (options.source === 'paste') syncPasteCountersFromPayload(payload);
    activateTab(session, 'terminal');
    const dropPayload = {path: paths[0], paths, kind: 'file'};
    const pathInserted = options.source === 'paste' || terminalDropShouldInsertPathFirst(session, dropPayload);
    const inserted = pathInserted
      ? (options.source === 'paste'
          ? insertPasteUploadReferences(session, payload.files || [], {silent: true})
          : insertUploadPaths(session, paths, {silent: true}))
      : false;
    const uploadResult = emitNotification('uploadResult', {session, uploadPayload: payload, inserted}).inApp;
    if (paths.length && boolSetting('uploads.show_suggestions', true)) {
      const timeoutMs = uploadResult?.expiresAt ? uploadResult.expiresAt - Date.now() : toastDurationMs;
      const shown = showTerminalDropSuggestions(session, dropPayload, options.suggestAt?.x, options.suggestAt?.y, {pathInserted, timeoutMs});
      if (!shown && !pathInserted) insertUploadPaths(session, paths, {silent: true});
    } else if (!pathInserted) {
      insertUploadPaths(session, paths, {silent: true});
    }
    refreshTerminalAfterUpload(session);
    refreshOpenEventLogs();
    refreshTranscripts({force: true});
  } catch (error) {
    showFileTransferError(error, {session, fallback: t('status.uploadFailed', {error: userMessageText(error, t('common.requestFailed'))})});
  }
}

async function uploadEditorFiles(editorTarget, fileList) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyUploadFiles'));
    return;
  }
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const totalBytes = files.reduce((total, file) => total + (Number(file?.size) || 0), 0);
  if (uploadMaxBytes > 0 && totalBytes > uploadMaxBytes) {
    statusErr(localizedHtml('status.uploadTooLarge', {selected: formatFileSize(totalBytes), limit: formatFileSize(uploadMaxBytes)}));
    showUploadRsyncRecommendation({item: focusedPanelItem, sizeBytes: totalBytes});
    return;
  }
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file, file.name || 'upload.bin');
  }
  try {
    const payload = await apiFetchJson(`/api/upload?editor_path=${encodeURIComponent(editorTarget.path)}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });
    syncPasteCountersFromPayload(payload);
    insertEditorPasteUploadReferences(editorTarget, payload.files || []);
  } catch (error) {
    showFileTransferError(error, {item: focusedPanelItem, fallback: t('status.uploadFailed', {error: userMessageText(error, t('common.requestFailed'))})});
  }
}

function refreshTerminalAfterUpload(session) {
  if (!isTmuxSession(session)) return;
  scheduleFit(session);
  refreshTerminal(session);
  requestAnimationFrame(() => {
    scheduleFit(session);
    refreshTerminal(session);
    requestAnimationFrame(() => refreshTerminal(session));
  });
}

function insertUploadPaths(session, paths, options = {}) {
  if (!paths.length) return false;
  const inserted = insertIntoTerminal(session, `${paths.map(shellQuote).join(' ')} `);
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">${localizedHtml('status.insertedUploadPath', {session: sessionLabel(session)})}</span>`
      : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
  }
  return inserted;
}

function insertPasteUploadReferences(session, files, options = {}) {
  const references = pasteUploadReferences(files);
  if (!references.length) return insertUploadPaths(session, files.map(file => file.path).filter(Boolean), options);
  const inserted = insertIntoTerminal(session, references.join(' '));
  if (!options.silent) {
    statusEl.innerHTML = inserted
      ? `<span class="ok">${localizedHtml('status.insertedPastedImage', {session: sessionLabel(session)})}</span>`
      : `<span class="err">${terminalNotConnectedHtml(session)}</span>`;
  }
  return inserted;
}

function pasteUploadReferences(files) {
  return (files || []).map((file, index) => {
    const path = file.path || '';
    if (!path) return '';
    const number = pasteUploadIndexFromPath(path) || index + 1;
    return `[Image #${number}] ${shellQuote(path)}`;
  }).filter(Boolean);
}

function insertEditorPasteUploadReferences(editorTarget, files) {
  const references = markdownImageUploadReferences(files);
  if (!references.length) return false;
  const view = editorTarget?.view;
  if (!view?.state?.doc || typeof view.dispatch !== 'function') return false;
  const selection = view.state.selection?.main || {};
  const docLength = Number(view.state.doc.length) || 0;
  const from = Math.max(0, Math.min(docLength, Number.isFinite(selection.from) ? selection.from : docLength));
  const to = Math.max(from, Math.min(docLength, Number.isFinite(selection.to) ? selection.to : from));
  const insert = references.join('\n');
  view.dispatch({
    changes: {from, to, insert},
    selection: {anchor: from + insert.length},
  });
  view.focus?.();
  return true;
}

function markdownImageUploadReferences(files) {
  return (files || []).map(file => {
    const path = file.relative_path || pathBasename(file.path || '') || file.saved_name || '';
    if (!path) return '';
    return `![image](${markdownLinkTarget(path)})`;
  }).filter(Boolean);
}

function markdownLinkTarget(path) {
  return String(path || '').split('/').map(part => encodeURIComponent(part)).join('/');
}

function syncPasteCountersFromPayload(payload) {
  const files = payload?.files || [];
  for (const file of files) syncPasteCounterFromPath(file.path || file.saved_name || '');
}

function syncPasteCounterFromPath(path) {
  const index = pasteUploadIndexFromPath(path);
  if (!Number.isFinite(index)) return;
  const suffix = imageSuffixFromFilename(path) || imageSuffix('');
  const stampMatch = pathBasename(path).match(/^(\d{8})-/);
  const stamp = stampMatch?.[1] || pacificDateStamp();
  const key = `${stamp}:${suffix}`;
  const localValue = pasteCounters.get(key) || 0;
  const counters = readPasteCounters();
  const next = Math.max(localValue, pasteCounterValue(counters, key), index);
  if (next <= localValue) return;
  counters[key] = next;
  writePasteCounters(counters);
  pasteCounters.set(key, next);
}

function insertIntoTerminal(session, text) {
  if (readOnlyMode && !shareWriteMode) {
    statusErr(localizedHtml('status.readOnlyTypeTerminals'));
    return false;
  }
  const item = terminals.get(session);
  if (!item) return false;
  const filtered = stripTerminalQueryResponses(text);
  if (!filtered) return false;
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true});
  if (shareReplayShellActive && shareWriteMode) {
    const sent = shareSendTerminalInputIntent(session, filtered);
    if (sent && autoFocusEnabled) item.term?.focus?.();
    return sent;
  }
  if (item.socket?.readyState !== WebSocket.OPEN) return false;
  const sendPerf = clientPerfStart('wsSend');
  item.socket.send(JSON.stringify({type: 'input', data: filtered}));
  clientPerfEnd(sendPerf, {bytes: utf8ByteLength(filtered)});
  item.lastInputSentAt = performanceNow();
  if (autoFocusEnabled) item.term?.focus?.();
  return true;
}

function noteTerminalExplicitInput(session) {
  const item = terminals.get(session);
  if (item) {
    item.lastExplicitInputMark = clientPerfMark(`terminal-keydown:${session}`);
    item.lastExplicitInputAt = performanceNow();
  }
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {userInitiated: true, syncFinder: false});
}

function terminalDataWithoutPassiveReports(data) {
  return String(data || '')
    .replace(/\x1b\[[IO]/g, '')
    .replace(/\x1b\[<\d+(?:;\d+){2}[mM]/g, '')
    .replace(/\x1b\[M[\s\S]{3}/g, '');
}

function terminalDataShouldAcknowledgeAttention(data) {
  return terminalDataWithoutPassiveReports(data).length > 0;
}

function acknowledgeTerminalAttentionFromTransportInput(session, data, options = {}) {
  if (options.acknowledgeAttention === false) return false;
  if (!terminalDataShouldAcknowledgeAttention(data)) return false;
  if (typeof acknowledgeTerminalAttentionFromUserAction !== 'function') return false;
  return acknowledgeTerminalAttentionFromUserAction(session, null, options.attentionOptions || {});
}

const terminalTmuxInputStates = new Map();
const tmuxWindowReadbackDelayMs = tmuxWindowReadbackMs;
const tmuxWindowReadbackRetryDelayMs = tmuxWindowReadbackRetryMs;
const tmuxWindowReadbackMaxAttempts = 6;
const terminalTmuxWindowRepeatMs = 900;

function terminalTmuxInputState(session, options = {}) {
  const key = String(session || '');
  if (!key) return null;
  let state = terminalTmuxInputStates.get(key) || null;
  if (!state && options.create === true) {
    state = {prefixPending: false, repeatUntilMs: 0};
    terminalTmuxInputStates.set(key, state);
  }
  return state;
}

function pruneTerminalTmuxInputState(session, now = Date.now()) {
  const key = String(session || '');
  const state = terminalTmuxInputState(key);
  if (!state) return null;
  if (Number(state.repeatUntilMs || 0) <= now) state.repeatUntilMs = 0;
  if (state.prefixPending !== true && !state.repeatUntilMs) {
    terminalTmuxInputStates.delete(key);
    return null;
  }
  return state;
}

function updateTerminalTmuxInputState(session, updates = {}) {
  const state = terminalTmuxInputState(session, {create: true});
  if (!state) return null;
  if (updates.prefixPending !== undefined) state.prefixPending = updates.prefixPending === true;
  if (updates.repeatUntilMs !== undefined) state.repeatUntilMs = Math.max(0, Number(updates.repeatUntilMs) || 0);
  return pruneTerminalTmuxInputState(session);
}

function pruneTerminalTmuxInputStates(now = Date.now()) {
  for (const session of terminalTmuxInputStates.keys()) pruneTerminalTmuxInputState(session, now);
}

const terminalTmuxWindowShortcutDefs = Object.freeze({
  n: {labelKey: 'terminal.window.next', repeatable: true},
  p: {labelKey: 'terminal.window.previous', repeatable: true},
  l: {labelKey: 'terminal.window.last', requireChanged: true, prefixOnly: true},
  w: {labelKey: 'terminal.window.chooser', requireChanged: true, prefixOnly: true},
  "'": {labelKey: 'terminal.window.prompt', requireChanged: true, prefixOnly: true},
  f: {labelKey: 'terminal.window.find', requireChanged: true, prefixOnly: true},
});

function terminalTmuxWindowShortcut(key, options = {}) {
  const value = String(key || '');
  const definition = terminalTmuxWindowShortcutDefs[value];
  if (definition && (!definition.prefixOnly || options.includePrefixOnly === true)) {
    return {
      label: t(definition.labelKey),
      ...(definition.repeatable ? {repeatable: true} : {}),
      ...(definition.requireChanged ? {requireChanged: true} : {}),
    };
  }
  if (options.includeNumbers === true && /^[0-9]$/.test(value)) {
    return {label: t('terminal.window.title', {name: value}), windowIndex: value};
  }
  return null;
}

function terminalTmuxPrefixWindowShortcut(key) {
  return terminalTmuxWindowShortcut(key, {includePrefixOnly: true, includeNumbers: true});
}

function terminalTmuxAltWindowShortcut(key) {
  return terminalTmuxWindowShortcut(key);
}

function tmuxWindowSignalReadbackUrl(session) {
  const params = new URLSearchParams();
  params.set('force', '1');
  const target = String(session || '').trim();
  if (target) params.set('session', target);
  return `/api/tmux-signals?${params.toString()}`;
}

function tmuxSignalPayloadData(payload) {
  return payload?.data && typeof payload.data === 'object' ? payload.data : payload;
}

function activeTmuxSignalWindowForSession(session, payload = tmuxSignalState) {
  const sessionText = String(session || '').trim();
  if (!sessionText) return null;
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  return windows.find(windowRecord => tmuxSignalWindowSession(windowRecord) === sessionText && windowRecord?.active === true) || null;
}

function confirmTmuxWindowActiveOverridesFromRawSignals(payload = {}) {
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
    const override = tmuxWindowActiveIndexOverride(session);
    if (!session || activeIndex === null || override === undefined || override === tmuxWindowPendingActiveIndex) continue;
    if (activeIndex === tmuxWindowIndexKey(override)) confirmTmuxWindowActiveIndexOverride(session, activeIndex);
  }
}

function reconcileTmuxWindowDirectTargetGuardsFromRawSignals(payload = {}) {
  if (typeof tmuxWindowDirectTargetGuard !== 'function') return;
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
    const guard = tmuxWindowDirectTargetGuard(session);
    if (!session || activeIndex === null || !guard) continue;
    if (activeIndex === tmuxWindowIndexKey(guard.index)) {
      confirmTmuxWindowDirectTargetGuard(session, activeIndex, {sequence: guard.sequence});
      continue;
    }
  }
}

function transcriptPaneMatchesSignalPane(pane, signalPane) {
  if (!pane || !signalPane) return false;
  const paneTarget = String(pane.target || pane.pane_id || '').trim();
  const signalTarget = String(signalPane.target || signalPane.pane_id || '').trim();
  if (paneTarget && signalTarget && paneTarget === signalTarget) return true;
  const paneWindow = tmuxWindowIndexKey(pane.window ?? pane.window_index);
  const signalWindow = tmuxWindowIndexKey(signalPane.window_index);
  const paneIndex = String(pane.pane ?? pane.pane_index ?? '').trim();
  const signalIndex = String(signalPane.pane_index ?? '').trim();
  return paneWindow !== null && paneWindow === signalWindow && paneIndex && signalIndex && paneIndex === signalIndex;
}

function mergeTranscriptPaneWithSignalPane(pane, signalPane, activeIndex) {
  const windowIndex = tmuxWindowIndexKey(pane?.window ?? pane?.window_index);
  const next = {...pane, window_active: windowIndex !== null && windowIndex === activeIndex};
  if (!signalPane) return next;
  if (signalPane.current_path) next.current_path = normalizeDirectoryPath(signalPane.current_path);
  if (signalPane.current_command) next.command = signalPane.current_command;
  if (signalPane.pane_id) next.pane_id = signalPane.pane_id;
  if (signalPane.target) next.target = signalPane.target;
  if (signalPane.pane_index !== undefined) next.pane = String(signalPane.pane_index);
  next.active = signalPane.active === true;
  return next;
}

function applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord, options = {}) {
  const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
  const info = transcriptMetadataState.payload.sessions?.[session];
  if (activeIndex === null || !info || !Array.isArray(info.panes)) return false;
  const signalPanes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
  let selectedPane = info.selected_pane || null;
  const panes = info.panes.map(pane => {
    const signalPane = signalPanes.find(item => transcriptPaneMatchesSignalPane(pane, item)) || null;
    const next = mergeTranscriptPaneWithSignalPane(pane, signalPane, activeIndex);
    if (next.window_active && (next.active || !selectedPane || tmuxWindowIndexKey(selectedPane.window ?? selectedPane.window_index) !== activeIndex)) {
      selectedPane = next;
    }
    return next;
  });
  if (!panes.some(pane => pane.window_active) && signalPanes.length) {
    const signalPane = signalPanes.find(item => item.active === true) || signalPanes[0];
    const synthesized = mergeTranscriptPaneWithSignalPane({
      window: windowRecord.window_index,
      window_name: windowRecord.window_name || '',
      pane: signalPane.pane_index ?? '',
      pane_id: signalPane.pane_id || signalPane.target || '',
      target: signalPane.target || signalPane.pane_id || '',
      current_path: signalPane.current_path || '',
      command: signalPane.current_command || '',
      active: signalPane.active === true,
    }, signalPane, activeIndex);
    panes.push(synthesized);
    selectedPane = synthesized;
  }
  const nextInfo = {...info, selected_pane: selectedPane, panes};
  setTranscriptMetadataPayload({
    ...transcriptMetadataState.payload,
    sessions: {...(transcriptMetadataState.payload.sessions || {}), [session]: nextInfo},
  });
  if (options.render !== false) {
    updatePanelHeader(session, nextInfo);
    renderInfoPanel();
    if (typeof refreshTabberPanelsForTmuxWindowChange === 'function') refreshTabberPanelsForTmuxWindowChange();
  }
  return true;
}

function applyTmuxSignalActiveWindowsToTranscriptInfo(payload = {}) {
  const windows = Array.isArray(payload?.windows) ? payload.windows : [];
  let changed = false;
  const seen = new Set();
  for (const windowRecord of windows) {
    if (windowRecord?.active !== true) continue;
    const session = tmuxSignalWindowSession(windowRecord);
    if (!session || seen.has(session)) continue;
    seen.add(session);
    changed = applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord, {render: false}) || changed;
  }
  if (changed) {
    for (const session of seen) updatePanelHeader(session, transcriptMetadataState.payload.sessions?.[session]);
    renderInfoPanel();
    if (typeof refreshTabberPanelsForTmuxWindowChange === 'function') refreshTabberPanelsForTmuxWindowChange();
  }
  return changed;
}

async function refreshTmuxWindowActiveFromSignals(session, options = {}) {
  const payload = await apiFetchJson(tmuxWindowSignalReadbackUrl(session), {cache: 'no-store'});
  const rawData = tmuxSignalPayloadData(payload);
  if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return true;
  const expected = tmuxWindowIndexKey(options.expectedIndex);
  const rawWindowRecord = expected !== null ? activeTmuxSignalWindowForSession(session, rawData) : null;
  const data = applyTmuxSignalsPayload(payload) || payload;
  const windowRecord = expected !== null ? rawWindowRecord : activeTmuxSignalWindowForSession(session, data);
  const activeIndex = tmuxWindowIndexKey(windowRecord?.window_index);
  if (activeIndex === null) return false;
  if (expected !== null && activeIndex !== expected) {
    const override = tmuxWindowActiveIndexOverride(session);
    updateTmuxWindowBarActiveButtons(session, override === tmuxWindowPendingActiveIndex ? null : override);
    return false;
  }
  const previous = tmuxWindowIndexKey(options.previousIndex);
  const retryingChangedWindow = options.requireChanged === true && previous !== null && activeIndex === previous && options.acceptUnchanged !== true;
  if (retryingChangedWindow) {
    updateTmuxWindowBarActiveButtons(session, null);
    return false;
  }
  applyTmuxSignalActiveWindowToTranscriptInfo(session, windowRecord);
  confirmTmuxWindowActiveIndexOverride(session, activeIndex, {sequence: options.sequence});
  return true;
}

function scheduleTmuxWindowReadback(session, options = {}) {
  const delayMs = Number.isFinite(options.delayMs) ? Math.max(0, options.delayMs) : tmuxWindowReadbackDelayMs;
  const attempt = Number.isFinite(options.attempt) ? Number(options.attempt) : 0;
  const run = () => {
    if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return Promise.resolve(true);
    const acceptUnchanged = options.requireChanged === true && attempt + 1 >= tmuxWindowReadbackMaxAttempts;
    const readback = refreshTmuxWindowActiveFromSignals(session, {...options, acceptUnchanged});
    return Promise.resolve(readback).then(confirmed => {
      if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return;
      if (!confirmed && attempt + 1 < tmuxWindowReadbackMaxAttempts) {
        scheduleTmuxWindowReadback(session, {...options, delayMs: tmuxWindowReadbackRetryDelayMs, attempt: attempt + 1});
      }
    }).catch(error => {
      console.warn('tmux sub-window signal readback failed', error);
      if (!tmuxWindowSwitchSequenceMatches(session, options.sequence)) return;
      if (attempt + 1 < tmuxWindowReadbackMaxAttempts) {
        scheduleTmuxWindowReadback(session, {...options, delayMs: tmuxWindowReadbackRetryDelayMs, attempt: attempt + 1});
      } else {
        const info = transcriptMetadataState.payload.sessions?.[session];
        reconcileTmuxWindowActiveIndexOverride(session, info, {expectedIndex: options.expectedIndex, sequence: options.sequence});
      }
    });
  };
  if (delayMs <= 0) return run();
  return new Promise(resolve => {
    setTimeout(() => resolve(run()), delayMs);
  });
}

function noteTerminalTmuxWindowSwitch(session, shortcut) {
  if (!shortcut) return false;
  const directIndex = tmuxWindowNumber(shortcut.windowIndex);
  const previousIndex = tmuxWindowInfoActiveIndex(transcriptMetadataState.payload.sessions?.[session]);
  const sequence = directIndex !== null
    ? setTmuxWindowActiveIndexOverride(session, directIndex)
    : setTmuxWindowActiveIndexPending(session);
  updateTerminalTmuxInputState(session, {repeatUntilMs: shortcut.repeatable ? Date.now() + terminalTmuxWindowRepeatMs : 0});
  statusOk(`${esc(shortcut.label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusTerminalFromUserAction(session, 75);
  const requireChanged = shortcut.requireChanged === true || directIndex === null;
  scheduleTmuxWindowReadback(session, directIndex !== null
    ? {clearActiveIndexOverride: true, expectedIndex: directIndex, sequence}
    : {requireChanged: requireChanged && previousIndex !== null, previousIndex, sequence});
  return true;
}

function observeTerminalTmuxPrefixWindowSwitches(session, data) {
  const text = String(data || '');
  if (!text) return false;
  let pending = terminalTmuxInputState(session)?.prefixPending === true;
  let mirrored = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (char === '\x1b' && index + 1 < text.length) {
      const altShortcut = terminalTmuxAltWindowShortcut(text[index + 1]);
      if (altShortcut) {
        mirrored = noteTerminalTmuxWindowSwitch(session, altShortcut) || mirrored;
        index += 1;
        continue;
      }
    }
    const repeatUntil = Number(terminalTmuxInputState(session)?.repeatUntilMs || 0);
    const repeatActive = repeatUntil > Date.now();
    const repeatShortcut = repeatActive ? terminalTmuxPrefixWindowShortcut(char) : null;
    if (!pending && repeatShortcut && repeatShortcut.repeatable) {
      mirrored = noteTerminalTmuxWindowSwitch(session, repeatShortcut) || mirrored;
      continue;
    }
    if (pending) {
      mirrored = noteTerminalTmuxWindowSwitch(session, terminalTmuxPrefixWindowShortcut(char)) || mirrored;
      pending = false;
      continue;
    }
    if (char === '\x02') pending = true;
  }
  updateTerminalTmuxInputState(session, {prefixPending: pending});
  pruneTerminalTmuxInputStates();
  return mirrored;
}

function handleTerminalData(session, data, options = {}) {
  const perf = clientPerfStart('term.onData');
  try {
    return handleTerminalDataMeasured(session, data, options);
  } finally {
    clientPerfEnd(perf, {bytes: utf8ByteLength(data)});
  }
}

function handleTerminalDataMeasured(session, data, options = {}) {
  if (readOnlyMode && !shareWriteMode) return false;
  const filtered = options.bypassMobileAccessoryModifiers === true
    ? stripTerminalQueryResponses(data)
    : terminalDataWithMobileAccessoryModifiers(session, stripTerminalQueryResponses(data));
  if (!filtered) return false;
  // Physical/mobile key input can arrive without another pointer event. Retire a touch-opened
  // tab detail here as well, while keeping passive terminal protocol reports from dismissing it.
  if (terminalDataShouldAcknowledgeAttention(filtered) && typeof closeOtherSessionPopovers === 'function') {
    closeOtherSessionPopovers(null, {force: true});
  }
  const current = terminals.get(session);
  if (current?.lastExplicitInputMark) {
    clientPerfMeasureSinceMark('keydownToTermData', current.lastExplicitInputMark, {bytes: utf8ByteLength(filtered)});
    current.lastExplicitInputMark = '';
  }
  if (shareReplayShellActive && shareWriteMode) {
    acknowledgeTerminalAttentionFromTransportInput(session, filtered, options);
    shareSendTerminalInputIntent(session, filtered);
    return true;
  }
  const socket = current?.socket;
  if (socket?.readyState !== WebSocket.OPEN) return false;
  observeTerminalTmuxPrefixWindowSwitches(session, filtered);
  acknowledgeTerminalAttentionFromTransportInput(session, filtered, options);
  const sendPerf = clientPerfStart('wsSend');
  socket.send(JSON.stringify({type: 'input', data: filtered}));
  clientPerfEnd(sendPerf, {bytes: utf8ByteLength(filtered)});
  current.lastInputSentAt = performanceNow();
  return true;
}

function shellQuote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'";
}

function uploadResultRecord(session, options = {}) {
  const key = String(session || '');
  if (!key) return null;
  let record = uploadResultRecords.get(key) || null;
  if (!record && options.create === true) {
    record = {entries: [], cleanupTimer: null};
    uploadResultRecords.set(key, record);
  }
  return record;
}

function clearUploadResultCleanup(session, record = uploadResultRecord(session)) {
  if (!record || record.cleanupTimer === null) return false;
  clearTimeout(record.cleanupTimer);
  record.cleanupTimer = null;
  return true;
}

function deleteUploadResultRecord(session) {
  const key = String(session || '');
  const record = uploadResultRecord(key);
  if (!record) return false;
  clearUploadResultCleanup(key, record);
  return uploadResultRecords.delete(key);
}

function moveUploadResultRecord(oldSession, newSession) {
  const oldKey = String(oldSession || '');
  const newKey = String(newSession || '');
  const record = uploadResultRecord(oldKey);
  if (!record || !newKey || oldKey === newKey) return false;
  clearUploadResultCleanup(oldKey, record);
  if (!uploadResultRecords.has(newKey)) uploadResultRecords.set(newKey, record);
  uploadResultRecords.delete(oldKey);
  return true;
}

function deliverUploadResultNotification(session, payload = {}, inserted = false) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return null;
  const files = payload.files || [];
  const paths = files.map(file => file.path).filter(Boolean);
  const label = files.length === 1 ? (files[0].saved_name || files[0].name || t('popover.kind.file')) : t('files.count', {count: files.length});
  const target = payload.target_dir || '';
  const uploadResultKey = inserted ? 'upload.resultInserted' : 'upload.resultTerminalDisconnected';
  const expiresAt = Date.now() + toastDurationMs;
  const newEntries = files.length
    ? files.map(file => {
      const name = file.saved_name || file.name || t('popover.kind.file');
      const destination = pathBasename(file.path || target) || target;
      return {
        id: ++uploadResultSequence,
        text: t(uploadResultKey, {name, destination}),
        path: file.path || '',
        expiresAt,
      };
    })
    : [{
      id: ++uploadResultSequence,
      text: t(uploadResultKey, {name: label, destination: pathBasename(target) || target}),
      path: target,
      expiresAt,
    }];
  const record = uploadResultRecord(session, {create: true});
  const active = [...record.entries.filter(entry => entry.expiresAt > Date.now()), ...newEntries].slice(-8);
  record.entries = active;
  renderUploadResult(session);
  return {expiresAt};
}

function ensureUploadResultShell(session, node) {
  return ensureToastShell(node, {
    title: t('upload.resultTitle', {session: sessionLabel(session)}),
    closeLabel: t('upload.hideStatus'),
    keepLabel: t('upload.keepStatus'),
    onKeep: () => keepUploadResult(session),
    onClose: () => hideUploadResult(session),
  });
}

function keepUploadResult(session) {
  const record = uploadResultRecord(session);
  if (!record) return;
  for (const entry of record.entries) entry.expiresAt = Number.POSITIVE_INFINITY;
  clearUploadResultCleanup(session, record);
}

function scheduleUploadResultCleanup(session, record, now) {
  clearUploadResultCleanup(session, record);
  const delays = record.entries
    .map(entry => entry.expiresAt - now)
    .filter(Number.isFinite);
  if (!delays.length) return;
  const delay = Math.max(1, Math.min(...delays));
  const timer = window.setTimeout(() => {
    const current = uploadResultRecord(session);
    if (current !== record || record.cleanupTimer !== timer) return;
    record.cleanupTimer = null;
    renderUploadResult(session);
  }, delay);
  record.cleanupTimer = timer;
}

function renderUploadResult(session) {
  const node = document.getElementById(`upload-${session}`);
  if (!node) return;
  const now = Date.now();
  const record = uploadResultRecord(session);
  const active = (record?.entries || []).filter(entry => entry.expiresAt > now).slice(-8);
  if (record) record.entries = active;
  if (!active.length) {
    node.hidden = true;
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    deleteUploadResultRecord(session);
    return;
  }
  const textNode = ensureUploadResultShell(session, node);
  if (!textNode) return;
  const paths = active.map(entry => entry.path).filter(Boolean);
  node.hidden = false;
  textNode.title = paths.join('\n');
  renderToastLines(textNode, active.map(entry => ({
    text: entry.text,
    countdownMs: entry.expiresAt - now,
  })));
  scheduleUploadResultCleanup(session, record, now);
}

function hideUploadResult(session) {
  deleteUploadResultRecord(session);
  const node = document.getElementById(`upload-${session}`);
  if (node) {
    const titleNode = node.querySelector('.toast-title');
    if (titleNode) titleNode.textContent = '';
    const textNode = node.querySelector('.toast-body');
    if (textNode) textNode.replaceChildren();
    node.hidden = true;
  }
}

function updatePanelSlot(panel, session, slot) {
  panel.dataset.slot = slot;
  panel.dataset.layoutItem = session;
  const head = panel.querySelector('.panel-head');
  if (head) head.dataset.dragSlot = slot;
  if (isFileEditorItem(session)) renderFileEditorPanel(panel, session, {updateActiveFile: !dockviewLayoutActive(), captureViewState: false});
  updatePaneExpandButton(panel, session);
  if (!hideDockviewInnerPaneTabs(panel)) updatePaneTabStrip(panel, slot);
}

function updatePaneExpandButton(panel, session) {
  const button = panel.querySelector('[data-pane-expand]');
  if (button) button.hidden = !canPaneExpand(session);
}

function syncPanelVisibility(previousActive = []) {
  const visible = new Set(activeSessions);
  for (const session of sessions) {
    if (!visible.has(session)) {
      stopTranscriptStream(session);
      stopSummaryStream(session);
      if (focusedTerminal === session) focusedTerminal = null;
    }
    updateTypingIndicator(session);
  }
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`terminal-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) scheduleFit(session);
  }
  if (typeof syncChatActiveLifecycle === 'function') syncChatActiveLifecycle();
}

function activateTab(session, name, options = {}) {
  setFocusedPanelItem(session, {userInitiated: options.userInitiated === true});
  if (name !== 'transcript') stopTranscriptStream(session);
  if (name !== 'summary') stopSummaryStream(session);
  document.querySelectorAll(`[data-tab="${session}"]`).forEach(button => {
    button.classList.toggle(CLS.active, button.dataset.tabName === name);
  });
  document.querySelectorAll(`[data-panel-tab-overflow="${session}"]`).forEach(button => {
    button.classList.toggle(CLS.active, ['transcript', 'summary', 'events'].includes(name));
  });
  for (const tabName of ['terminal', 'transcript', 'summary', 'events']) {
    const pane = document.getElementById(`${tabName}-pane-${session}`);
    if (pane) pane.classList.toggle(CLS.active, tabName === name);
  }
  updateTypingIndicator(session);
  if (name === 'terminal') {
    scheduleFit(session);
    setTimeout(() => refreshTerminal(session), terminalRefreshAfterTabSelectMs);
    scheduleTerminalBlankScreenRefresh(session, {reason: 'terminal-tab'});
    if (options.userInitiated) focusTerminalFromUserAction(session);
    else focusTerminalWhenAutoFocus(session, 25);
  } else {
    clearFocusedTerminal(session);
  }
  if (name === 'transcript') {
    startTranscriptStream(session, {scrollBottom: true});
  }
  if (name === 'summary') startSummaryStream(session);
  if (name === 'events') refreshEventLog(session);
  if (!shareViewMode && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function tmuxWindow(session, key, label) {
  if (readOnlyMode) {
    statusErr(localizedHtml('terminal.connection.readonlyTmuxWindow'));
    return;
  }
  // Callers acknowledge the exact source/target window before switching. Refocusing afterward
  // must not acknowledge a second, unrelated child selected by the parent-session summary.
  const focusAfterAcknowledgedSwitch = () => focusTerminalFromUserAction(session, 75, {
    acknowledgeAgentWindow: false,
    acknowledgePromptAttention: false,
  });
  const directIndex = tmuxWindowNumber(key?.windowIndex);
  if (directIndex !== null) {
    const previousInfo = transcriptMetadataState.payload.sessions?.[session] || null;
    const sequence = setTmuxWindowActiveIndexOverride(session, directIndex);
    statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
    scheduleFit(session);
    focusAfterAcknowledgedSwitch();
    apiFetchJson(`/api/tmux-window?session=${encodeURIComponent(session)}&window=${encodeURIComponent(String(directIndex))}`, {method: 'POST'})
      .then(() => {
        if (!tmuxWindowSwitchSequenceMatches(session, sequence)) return;
        scheduleTmuxWindowReadback(session, {delayMs: 0, clearActiveIndexOverride: true, expectedIndex: directIndex, sequence});
      })
      .catch(error => {
        if (!clearTmuxWindowActiveIndexOverride(session, {sequence, clearDirectTarget: true})) return;
        if (previousInfo) {
          setTranscriptMetadataPayload({
            ...transcriptMetadataState.payload,
            sessions: {...(transcriptMetadataState.payload.sessions || {}), [session]: previousInfo},
          });
          updatePanelHeader(session, previousInfo);
          renderInfoPanel();
        } else {
          reconcileTmuxWindowActiveIndexOverride(session, transcriptMetadataState.payload.sessions?.[session], {sequence});
        }
        statusErr(esc(userMessageText(error, t('terminal.window.failed', {error: error.message || error}))));
      });
    return;
  }
  const item = terminals.get(session);
  if (!item || item.socket?.readyState !== WebSocket.OPEN) {
    statusErr(terminalNotConnectedHtml(session));
    return;
  }
  const previousIndex = tmuxWindowInfoActiveIndex(transcriptMetadataState.payload.sessions?.[session]);
  const sequence = setTmuxWindowActiveIndexPending(session);
  fitTerminal(session);
  item.socket.send(JSON.stringify({type: 'input', data: String.fromCharCode(2) + key}));
  statusOk(`${esc(label)}: ${esc(sessionLabel(session))}`);
  scheduleFit(session);
  focusAfterAcknowledgedSwitch();
  scheduleTmuxWindowReadback(session, {requireChanged: previousIndex !== null, previousIndex, sequence});
}

async function ensureTerminalRunning(session) {
  const key = String(session || '');
  const existing = terminalStartupPromises.get(key);
  if (existing) return existing;
  const promise = (async () => {
    const item = terminals.get(session);
    const readyState = item?.socket?.readyState;
    const container = document.getElementById(terminalDomId(session));
    const boundToCurrentContainer = Boolean(item?.term && container?.isConnected && item.container === container);
    if (item && boundToCurrentContainer && readyState !== undefined && readyState !== WebSocket.CLOSING && readyState !== WebSocket.CLOSED) return;
    if (readOnlyMode) {
      startTerminal(session);
      return;
    }
    const knownFromTranscriptPayload = Boolean(transcriptMetadataState.loaded && transcriptMetadataState.payload.sessions?.[session]);
    const ensured = knownFromTranscriptPayload || await ensureSession(session);
    if (!ensured) {
      const container = document.getElementById(terminalDomId(session));
      if (container) container.innerHTML = `<pre class="terminal-error">${localizedHtml('terminal.connection.sessionUnavailableRetry', {session: sessionLabel(session)})}</pre>`;
      return;
    }
    startTerminal(session);
  })();
  terminalStartupPromises.set(key, promise);
  try {
    return await promise;
  } finally {
    if (terminalStartupPromises.get(key) === promise) terminalStartupPromises.delete(key);
  }
}

function connectTerminalSocket(session, item) {
  if (!item?.term || !item?.container) return;
  if (item.socket && item.socket.readyState !== WebSocket.CLOSED && item.socket.readyState !== WebSocket.CLOSING) return;
  const socket = new WebSocket(wsUrl(session));
  socket.binaryType = 'arraybuffer';
  item.socket = socket;
  item.manualClose = false;
  socket.onopen = () => {
    clearTerminalRemovalLatency('session', session);
    item.terminalOutputSeen = false;
    item.reconnectAttempt = 0;
    dismissTerminalConnectionToasts(session);
    if (terminalIsVisible(session, item.container)) {
      scheduleFit(session);
      scheduleTerminalBlankScreenRefresh(session, {reason: 'socket-open'});
      if (!shareViewMode) scheduleRemoteResize(session, shareRemoteResizeAfterSocketOpenMs);
    }
    void refreshTmuxStatusMode(session).catch(error => console.warn('tmux status read failed', error));
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
  socket.onmessage = event => {
    if (terminals.get(session) !== item || !item.term) return;
    try {
      const dataBytes = event.data instanceof ArrayBuffer ? event.data.byteLength : utf8ByteLength(event.data);
      const inputSentAt = Number(item.lastInputSentAt || 0);
      if (inputSentAt > 0) {
        recordClientPerfCounter('echoToTermWrite', performanceNow() - inputSentAt, {bytes: dataBytes});
        item.lastInputSentAt = 0;
      }
      const writePerf = clientPerfStart('xtermWrite');
      if (shareViewMode) {
        handleShareViewSocketMessage(session, item, event.data);
      } else if (event.data instanceof ArrayBuffer) {
        item.term.write(new Uint8Array(event.data));
      } else {
        item.term.write(String(event.data));
      }
      clientPerfEnd(writePerf, {bytes: dataBytes});
      const firstOutput = item.terminalOutputSeen !== true;
      item.terminalOutputSeen = true;
      item.fileUnderlineController?.schedule?.({reason: 'output'});
      if (firstOutput) scheduleTerminalBlankScreenRefresh(session, {reason: 'first-output'});
      scheduleTerminalAttentionHighlight(session);
    } catch (_) {
      if (terminals.get(session) === item) closeTerminalItem(session, item);
    }
  };
  socket.onclose = event => {
    if (item.manualClose || terminals.get(session) !== item) return;
    postEvent(session, 'terminal_disconnected', `terminal disconnected from ${session}`, {});
    clearFocusedTerminal(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
    // Confirm once before reconnecting: a dead tmux session is pruned without the old reconnect
    // backoff loop, while a live session still reconnects after a transient close.
    confirmSessionGoneOrReconnect(session, item, event);
  };
  socket.onerror = () => {
    updateTypingIndicator(session);
    updateStatus();
    refreshTrackedSessionChrome(session);
  };
}

function shareSocketMessage(data) {
  if (typeof data !== 'string') return null;
  try {
    return JSON.parse(data);
  } catch (_) {
    return null;
  }
}

function shareTerminalBytesFromMessage(session, message) {
  if (!message || message.ch !== 'term' || message.pane !== session || typeof message.data !== 'string') {
    return null;
  }
  const raw = atob(message.data);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

function handleShareViewSocketMessage(session, item, data) {
  const message = shareSocketMessage(data);
  if (!message) return;
  if (message.ch === 'ui') {
    applyShareUiMessage(message);
    return;
  }
  if (message.ch === 'ptr') {
    renderSharePointerGhost(message.payload && typeof message.payload === 'object' ? message.payload : message);
    return;
  }
  const bytes = shareTerminalBytesFromMessage(session, message);
  if (bytes) {
    item.shareTerminalBytesReceived = true;
    item.shareTerminalLastByteAt = Date.now();
    item.shareTerminalByteCount = Math.max(0, Math.round(Number(item.shareTerminalByteCount) || 0)) + bytes.length;
    item.term.write(bytes);
  }
}

function bindTerminalContainerForSession(session, term, container) {
  if (!session || !term || !container) return;
  if (container.dataset?.terminalHandlersBound === session) return;
  if (container.dataset) container.dataset.terminalHandlersBound = session;
  installTerminalMobileAccessoryDismissal();
  installTerminalMobileAccessoryResizeSync();
  installTerminalContextMenu(session, term, container);
  installTerminalCopyShortcut(session, term, container);
  installTerminalFileDrop(session, container);
  enableTerminalScroll(session, term, container);
  observeTerminalResize(session, container);
  container.addEventListener('focusin', () => {
    setFocusedTerminal(session);
  });
  container.addEventListener('focusout', () => {
    clearFocusedTerminal(session);
  });
  container.addEventListener('copy', event => {
    copyTerminalSelectionToClipboardEvent(session, term, event, container);
  }, {capture: true});
  container.addEventListener('keydown', () => {
    dismissTerminalMobileAccessory(session);
    noteTerminalExplicitInput(session);
  }, {capture: true});
  container.addEventListener('paste', () => {
    dismissTerminalMobileAccessory(session);
    noteTerminalExplicitInput(session);
  }, {capture: true});
  container.addEventListener('beforeinput', () => {
    dismissTerminalMobileAccessory(session);
    noteTerminalExplicitInput(session);
  }, {capture: true});
}

function terminalUnicode11AddonCtor() {
  return window.Unicode11Addon?.Unicode11Addon || null;
}

function applyTerminalUnicode11Addon(term) {
  const Unicode11AddonCtor = terminalUnicode11AddonCtor();
  if (!Unicode11AddonCtor || typeof term?.loadAddon !== 'function' || !term?.unicode) return false;
  try {
    const addon = new Unicode11AddonCtor();
    term.loadAddon(addon);
    term.unicode.activeVersion = '11';
    return term.unicode.activeVersion === '11';
  } catch (error) {
    console.warn('xterm Unicode 11 width addon failed', error);
    return false;
  }
}

function startTerminal(session) {
  const existing = terminals.get(session);
  const reconnectAttempt = existing?.reconnectAttempt || 0;
  const container = document.getElementById(terminalDomId(session));
  if (!container) return;
  if (existing?.term && existing.container === container) {
    connectTerminalSocket(session, existing);
    return;
  }
  if (existing) {
    closeTerminalItem(session, existing);
    terminals.delete(session);
  }
  const TerminalCtor = window.Terminal?.Terminal || window.Terminal;
  if (!TerminalCtor) {
    container.innerHTML = `<pre class="terminal-error">${esc(t('terminal.xtermLoadFailed'))}</pre>`;
    statusErr(localizedHtml('status.xtermUnavailable'));
    return;
  }
  container.innerHTML = '';
  const size = shareHostTerminalSize(session) || estimateTerminalSize(container);
  const baseTheme = terminalThemeForGlobalTheme();
  const term = new TerminalCtor({
    cols: size.cols,
    rows: size.rows,
    cursorBlink: typeof terminalCursorBlinkEnabled === 'function' ? terminalCursorBlinkEnabled() : true,
    convertEol: false,
    fontFamily: terminalFontFamily,
    fontSize: terminalFontSize,
    letterSpacing: 0,
    lineHeight: 1.0,
    scrollback: terminalScrollback,
    disableStdin: readOnlyMode && !shareWriteMode,
    theme: terminalThemeForSession(session, baseTheme),
    minimumContrastRatio: terminalMinimumContrastRatio(),
    // Unicode11Addon uses xterm's unicode width service; this local xterm build gates it behind proposed API opt-in.
    allowProposedApi: true,
    // Alt-screen TUIs (claude, vim, less) enable mouse reporting, which makes xterm send drags to the app
    // instead of selecting text — so Ctrl-C/Cmd-C has nothing to copy. Option-click (Mac) forces a text
    // selection anyway; on Linux/Windows hold Shift while dragging (xterm's built-in bypass).
    macOptionClickForcesSelection: true,
  });
  applyTerminalUnicode11Addon(term);
  term.open(container);
  // match the container bg to the terminal theme so every pane shares one white.
  applyTerminalContainerTheme(container, baseTheme);
  installTerminalLinkProvider(session, term);
  installTerminalOsc52Bridge(session, term);   // Claude/tmux OSC 52 clipboard escapes -> browser clipboard
  const openedSize = shareHostTerminalSize(session) || estimateTerminalSize(container, term);
  if (term.cols !== openedSize.cols || term.rows !== openedSize.rows) {
    term.resize(openedSize.cols, openedSize.rows);
  }
  const item = {
    term,
    socket: null,
    container,
    manualClose: false,
    reconnectAttempt,
    reconnectTimer: null,
    resizeTimer: null,
    scrollTimer: null,
    pendingScrollLines: 0,
    shareTerminalBytesReceived: false,
    shareTerminalLastByteAt: 0,
    shareTerminalByteCount: 0,
    shareTerminalLastResetAt: 0,
    shareTerminalSkippedResetCount: 0,
    blankScreenRefreshTimer: 0,
    blankScreenRefreshAttempts: 0,
    attentionHighlightFrame: 0,
    terminalOutputSeen: false,
    fileUnderlineController: null,
  };
  terminals.set(session, item);
  item.fileUnderlineController = installTerminalFileReferenceUnderlines(session, term, container);
  bindTerminalContainerForSession(session, term, container);
  term.onFocus?.(() => {
    setFocusedTerminal(session);
  });
  term.onBlur?.(() => {
    clearFocusedTerminal(session);
  });
  // xterm can emit focus and mouse-tracking bytes from hover. Keep Differ commits on DOM
  // keydown/paste/beforeinput and pane pointerdown, not on the terminal transport stream.
  term.onData(data => handleTerminalData(session, data));
  if (focusedTerminal === session && terminalPaneIsActive(session)) focusTerminalDom(session);
  connectTerminalSocket(session, item);
}

function updateTypingIndicator(session) {
  const item = terminals.get(session);
  const container = item?.container || document.getElementById(terminalDomId(session));
  const pane = document.getElementById(`terminal-pane-${session}`);
  const panel = document.getElementById(panelDomId(session));
  const ready = Boolean(
    item?.socket?.readyState === WebSocket.OPEN
    && focusedTerminal === session
    && pane?.classList.contains(CLS.active)
  );
  container?.classList.toggle('typing-ready', ready);
  panel?.classList.toggle('typing-ready-pane', ready);
  panel?.classList.toggle('yolo-ready-pane', ready && autoApproveStates.get(session)?.enabled === true);
}

function updateStatus() {
  if (activeSessions.length === 0) {
    statusEl.textContent = t('terminal.status.noSessionSelected');
    statusEl.removeAttribute('title');
    return;
  }
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  if (!activeTmuxSessions.length) {
    statusEl.textContent = t('terminal.status.viewShown', {view: infoTabLabel()});
    statusEl.removeAttribute('title');
    return;
  }
  let open = 0;
  for (const session of activeTmuxSessions) {
    const item = terminals.get(session);
    if (item?.socket?.readyState === WebSocket.OPEN) open += 1;
  }
  const total = activeTmuxSessions.length;
  statusEl.textContent = open === total ? '' : t('terminal.connection.connShort', {open, total});
  statusEl.title = open === total ? '' : t('terminal.connection.socketsTitle', {open, total});
}

async function toggleAutoApprove(session) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.yoloReadOnlyChange'));
    return;
  }
  const state = autoApproveStates.get(session) || {};
  const current = state.enabled === true;
  await setAutoApprove(session, !current);
}

async function setAutoApprove(session, enabled) {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.yoloReadOnlyChange'));
    return;
  }
  try {
    const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}&enabled=${enabled ? '1' : '0'}`, {method: 'POST'});
    autoApproveStates.set(session, payload);
    updateDocumentTitle();
    updateSessionButtonStates();
    renderInfoPanel();
    renderAutoApproveButton(session, payload);
    scheduleTerminalAttentionHighlight(session);
    scheduleShareUiStatePublish();
    statusEl.innerHTML = payload.enabled
      ? `<span class="ok">${localizedHtml('status.yoloEnabledFor', {session: sessionLabel(session)})}</span>`
      : `<span class="ok">${localizedHtml('status.yoloDisabledFor', {session: sessionLabel(session)})}</span>`;
  } catch (error) {
    const payload = error?.payload || {};
    if (error?.status) {
      if (payload?.target || payload?.session) {
        autoApproveStates.set(session, payload);
        updateDocumentTitle();
        updateSessionButtonStates();
        renderAutoApproveButton(session, payload);
        scheduleTerminalAttentionHighlight(session);
        scheduleShareUiStatePublish();
      }
      statusErr(esc(userMessageText(error, t('status.yoloApprovalFailedDefault'))));
      return;
    }
    statusErr(localizedHtml('status.yoloRequestFailed', {error}));
  }
}

async function refreshAutoStatuses(options = {}) {
  const result = await loadAutoStatuses({...options, render: false});
  renderAutoApproveStatusSurfaces(result);
  bindClipboardPaste();
  refreshOpenEventLogs();
}

function autoApproveSnapshotIsFresh() {
  return loadAutoStatuses.lastResult !== null
    && Date.now() - Number(loadAutoStatuses.updatedAt || 0) < startupSnapshotFreshnessMs;
}

function loadAutoStatuses(options = {}) {
  if (loadAutoStatuses.request) return loadAutoStatuses.request;
  if (options.force !== true && options.preferFresh === true && autoApproveSnapshotIsFresh()) return Promise.resolve(loadAutoStatuses.lastResult);
  const request = (async () => {
  let result = null;
  try {
    const payload = await apiFetchJson('/api/auto-approve');
    result = applyAutoApprovePayload(payload, options);
    loadAutoStatuses.lastResult = result;
    loadAutoStatuses.updatedAt = Date.now();
  } catch (_) {
    for (const session of activeSessions.filter(isTmuxSession)) {
      try {
        const payload = await apiFetchJson(`/api/auto-approve?session=${encodeURIComponent(session)}`);
        autoApproveStates.set(session, payload);
      } catch (_) {}
    }
    result = {applied: false, sessionsChanged: false, previousActive: activeSessions.slice()};
  }
  if (options.render !== false && !result?.rendered) renderAutoApproveStatusSurfaces(result);
  return result;
  })();
  const settledRequest = request.finally(() => {
    if (loadAutoStatuses.request === settledRequest) loadAutoStatuses.request = null;
  });
  loadAutoStatuses.request = settledRequest;
  return settledRequest;
}
loadAutoStatuses.request = null;
loadAutoStatuses.lastResult = null;
loadAutoStatuses.updatedAt = 0;

function renderAutoApproveStatusSurfaces(result = {}) {
  const perf = clientPerfStart('autoStatusRender');
  try {
    if (result?.sessionsChanged) renderPanels(result.previousActive || activeSessions.slice());
    else if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
    updateDocumentTitle();
  // Re-toggle the YO markers' working class from the fresh states on the SAME poll the title updates,
  // so a finished/idle pane's marker stops spinning instead of lingering (the transcript poll path
  // updated the title but never re-synced the markers).
    renderAutoApproveButtons();
    updateSessionButtonStates();
    if (typeof syncSessionTabLeadingActivityChrome === 'function') syncSessionTabLeadingActivityChrome();
    refreshActivePanelHeaders();
    trackSessionStateChanges();
    syncTerminalAttentionHighlights();
    scheduleShareUiStatePublish();
    if (result && typeof result === 'object') result.rendered = true;
  } finally {
    clientPerfEnd(perf, {sessions: sessions.length});
  }
}

function applyAutoApprovePayload(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const previousActive = activeSessions.slice();
  const sessionsChanged = Array.isArray(payload.session_order) ? updateSessionList(payload.session_order) : false;
  if (payload.rules) {
    yoloRulesPayload = payload.rules;
    renderPreferencesPanels();
  }
  for (const session of sessions) {
    const state = payload.sessions?.[session] || {target: session, enabled: false, last_action: 'off'};
    autoApproveStates.set(session, state);
    reconcileTmuxWindowMetadataFromAgentWindows(session, state);
  }
  const result = {applied: true, sessionsChanged, previousActive, workingAgentTransitionNotifications: 0};
  if (options.render !== false) renderAutoApproveStatusSurfaces(result);
  // Deliver after the status render. Rendering may rebuild a pane and its toast stack; inserting a
  // transition toast before that pass makes it disappear immediately even though classification ran.
  if (typeof reconcileWorkingAgentTransitionNotifications === 'function') {
    for (const session of sessions) {
      const state = autoApproveStates.get(session) || {target: session, enabled: false, last_action: 'off'};
      result.workingAgentTransitionNotifications += reconcileWorkingAgentTransitionNotifications(session, state);
    }
  }
  return result;
}

function reconcileTmuxWindowMetadataFromAgentWindows(session, payload = {}) {
  const info = transcriptMetadataState.payload.sessions?.[session];
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const agentWindows = typeof agentWindowPayloadRows === 'function'
    ? agentWindowPayloadRows(payload.agent_windows)
    : [];
  if (!info || !agentWindows.length) return false;
  const paneWindows = new Set(panes.map(pane => tmuxWindowIndexKey(pane?.window)).filter(index => index !== null));
  const missing = agentWindows.filter(agent => {
    const index = tmuxWindowIndexKey(agent?.window_index ?? agent?.window);
    return index !== null && !paneWindows.has(index);
  });
  if (!missing.length) return false;
  const reconciledPanes = [...panes, ...missing.map(agent => {
    const window = tmuxWindowIndexKey(agent.window_index ?? agent.window);
    const name = String(agent.window_name || agent.kind || 'window').trim();
    const active = agent.current === true || agent.window_active === true;
    return {
      window,
      window_name: name,
      process_label: name,
      target: agent.pane_target || '',
      pane_id: agent.pane_target || '',
      active,
      window_active: active,
      pid: agent.pid || null,
      process_label_pid: agent.pid || null,
    };
  })].sort((left, right) => Number(left.window) - Number(right.window));
  setTranscriptMetadataPayload({
    ...transcriptMetadataState.payload,
    sessions: {
      ...(transcriptMetadataState.payload.sessions || {}),
      [session]: {
        ...info,
        panes: reconciledPanes,
        selected_pane: reconciledPanes.find(pane => pane.window_active === true) || info.selected_pane,
      },
    },
  });
  return true;
}

function autoApproveOwnerLabel(payload) {
  const owner = payload?.lock_owner || {};
  const pid = owner.pid ? `pid ${owner.pid}` : '';
  const root = owner.project_root || '';
  return [pid, root].filter(Boolean).join(' ') || structuredMessageText(payload, 'last_action', t('yolo.ownerFallback'));
}

function renderAutoApproveButtons() {
  for (const session of sessions) {
    const state = autoApproveStates.get(session) || {target: session, enabled: false, last_action: 'off'};
    renderAutoApproveButton(session, state);
  }
}

function renderAutoApproveButton(session, payload) {
  const buttons = document.querySelectorAll(`[data-yolo-session="${cssEscape(session)}"]`);
  const enabled = payload?.enabled === true;
  const locked = payload?.locked === true && !enabled;
  const working = sessionYoloIsWorking(session, payload);
  for (const button of buttons) {
    syncPressedButton(button, enabled);
    button.classList.toggle('inactive', !enabled && !locked);
    button.classList.toggle('locked', locked);
    button.classList.toggle('working', working);
    button.closest('.pane-tab')?.classList.remove('is-working');
    button.textContent = t('brand.marker');
    const actionText = structuredMessageText(payload, 'last_action');
    const action = actionText ? t('yolo.actionSuffix', {action: actionText}) : '';
    const readonly = readOnlyMode ? t('yolo.readonlySuffix') : '';
    const buttonLabel = enabled
      ? t('yolo.buttonOnForSession', {session: sessionLabel(session), action, readonly})
      : locked
        ? t('yolo.buttonOwnedBy', {owner: autoApproveOwnerLabel(payload)})
      : t('yolo.buttonOffForSession', {session: sessionLabel(session), readonly});
    button.setAttribute('aria-label', buttonLabel);
    if (button.closest('.tabber-session-tab')) button.removeAttribute('title');
    else button.title = buttonLabel;
  }
  updatePanelHeader(session, transcriptMetadataState.payload.sessions?.[session]);
  updateTypingIndicator(session);
}

function startSummaryStream(session) {
  stopSummaryStream(session);
  const node = document.getElementById(summaryDomId(session));
  if (!node) return;
  delete node.dataset.localeTextKey;
  if (readOnlyMode) {
    node.textContent = t('transcript.adminRequired');
    statusErr(`${esc(t('transcript.adminStatus'))}`);
    return;
  }
  // Accumulate the raw streamed text and render it through the markdown pipeline
  // (coalesced to one render per frame) so the panel shows formatted markdown,
  // not raw `##`/`**`/backticks. The leading `[codex]` status lines render as
  // plain paragraphs, then the model's markdown summary renders properly.
  let raw = `${t('summary.stream.starting')}\n\n`;
  let renderScheduled = false;
  const renderSummary = () => {
    renderScheduled = false;
    renderMarkdownPreviewInto(node, raw);
    node.scrollTop = node.scrollHeight;
  };
  const appendSummary = text => {
    raw += text;
    if (!renderScheduled) {
      renderScheduled = true;
      requestAnimationFrame(renderSummary);
    }
  };
  renderSummary();
  const source = new EventSource(`/api/summary-stream?session=${encodeURIComponent(session)}&lookback=${60 * 60}`);
  summaryStreams.set(session, source);
  source.addEventListener('meta', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    const fallback = t(payload.fallback ? 'summary.stream.source.recentTail' : 'summary.stream.source.lastHour');
    const projectCount = Array.isArray(payload.projects) ? payload.projects.length : 0;
    appendSummary(`${t('summary.stream.summarizing', {source: fallback, focus: payload.focus_root || session})}\n`);
    if (payload.summary_model) {
      appendSummary(`${t('summary.stream.model', {
        model: payload.summary_model,
        effort: payload.summary_effort || t('summary.stream.effortDefault'),
      })}\n`);
    }
    appendSummary(`${tPlural('summary.stream.projectInventory', projectCount)}\n\n`);
  });
  source.addEventListener('log', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.text) appendSummary(`${t('summary.stream.log', {text: payload.text})}\n`);
  });
  source.addEventListener('delta', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.text) appendSummary(payload.text);
  });
  source.addEventListener('summary_error', event => {
    // A bad frame must still tear the stream down (this is the error path); guard the read but always stop
    // — an unguarded JSON.parse throw here would leak the EventSource.
    const payload = safeJsonParse(event.data, null);
    appendSummary(`\n${t('summary.stream.error', {error: userMessageText(payload, t('summary.stream.failed'))})}\n`);
    stopSummaryStream(session);
  });
  source.addEventListener('done', event => {
    const payload = safeJsonParse(event.data, null);
    if (payload?.return_code && payload.return_code !== 0) {
      appendSummary(`\n${t('summary.stream.exited', {code: payload.return_code})}\n`);
    }
    stopSummaryStream(session);
  });
  source.onerror = () => {
    if (summaryStreams.get(session) !== source) return;
    appendSummary(`\n${t('terminal.summary.streamDisconnected')}\n`);
    stopSummaryStream(session);
  };
}

function stopSummaryStream(session) {
  const source = summaryStreams.get(session);
  if (!source) return;
  source.close();
  summaryStreams.delete(session);
}

function reloadIsSafe() {
  // Don't yank the page out from under unsaved work or active typing.
  for (const file of openFiles.values()) {
    if (file?.dirty) return false;
  }
  const active = document.activeElement;
  if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) return false;
  return true;
}

let selfUpdateAvailableTarget = '';
const selfUpdateReloadState = {
  pending: false,
  target: '',
  attempts: 0,
  timer: null,
  deferredToastShown: false,
};
const selfUpdateReloadPollMs = 1501;
const selfUpdateReloadMaxAttempts = 120;

function dismissToastNode(node) {
  if (!node) return;
  const alertId = Number(node.dataset?.alertId || 0);
  if (alertId) removeAttentionAlert(alertId);
  else node.remove?.();
}

function dismissUpdateAvailableToasts(ownerToast = null) {
  const toasts = new Set();
  if (ownerToast) toasts.add(ownerToast);
  for (const node of document.querySelectorAll?.('.toast-update') || []) {
    toasts.add(node);
  }
  for (const node of toasts) dismissToastNode(node);
}

function hideUpdateBadge() {
  const badge = document.querySelector('[data-update-badge]');
  if (!badge) return;
  badge.hidden = true;
  delete badge.dataset.updateTarget;
  renderUpdateBadgeChrome();
}

function renderUpdateBadgeChrome() {
  const badge = document.querySelector('[data-update-badge]');
  if (!badge) return;
  const target = String(badge.dataset.updateTarget || selfUpdateAvailableTarget || '').trim();
  badge.textContent = t('update.badgeLabel');
  badge.setAttribute('aria-label', t('update.badgeAria'));
  badge.title = badge.hidden
    ? t('update.badgeTitle')
    : t('update.badgeAvailable', {target: target ? ` (${target})` : ''});
}

function markSelfUpdateReloadPending(target = '') {
  selfUpdateReloadState.pending = true;
  selfUpdateReloadState.target = String(target || selfUpdateReloadState.target || '').trim();
  selfUpdateReloadState.attempts = 0;
  selfUpdateReloadState.deferredToastShown = false;
  if (selfUpdateReloadState.target) serverVersionReloadHandled = selfUpdateReloadState.target;
  document.getElementById('serverUpdateBanner')?.remove();
}

function selfUpdateOwnsServerVersion(serverVersion) {
  if (!selfUpdateReloadState.pending) return false;
  if (!selfUpdateReloadState.target || serverVersion === selfUpdateReloadState.target) {
    if (serverVersion) serverVersionReloadHandled = serverVersion;
    return true;
  }
  return false;
}

function selfUpdateReloadDeferredReason() {
  for (const file of openFiles.values()) {
    if (file?.dirty) return t('update.defer.unsavedEdits');
  }
  const active = document.activeElement;
  if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable)) {
    return t('update.defer.activeTyping');
  }
  return t('update.defer.currentActivity');
}

function showSelfUpdateReloadDeferredToast() {
  if (selfUpdateReloadState.deferredToastShown) return;
  selfUpdateReloadState.deferredToastShown = true;
  emitNotification('update', {
    title: t('update.softwareTitle'),
    lines: [t('update.reloadDeferred', {reason: selfUpdateReloadDeferredReason()})],
    className: 'attention-alert toast toast-update', coalesceKey: 'self-update-reload-deferred',
  });
}

function maybeReloadAfterSelfUpdate() {
  if (!selfUpdateReloadState.pending) return false;
  if (reloadIsSafe()) {
    location.reload();
    return true;
  }
  showSelfUpdateReloadDeferredToast();
  scheduleSelfUpdateReloadPoll();
  return false;
}

function scheduleSelfUpdateReloadPoll(delayMs = selfUpdateReloadPollMs) {
  if (!selfUpdateReloadState.pending) return;
  if (selfUpdateReloadState.timer) clearTimeout(selfUpdateReloadState.timer);
  selfUpdateReloadState.timer = window.setTimeout(() => {
    selfUpdateReloadState.timer = null;
    pollSelfUpdateReload();
  }, delayMs);
}

async function pollSelfUpdateReload() {
  if (!selfUpdateReloadState.pending) return false;
  selfUpdateReloadState.attempts += 1;
  try {
    await apiFetchJson(`/api/ping?selfUpdate=${Date.now()}`, {cache: 'no-store'});
    return maybeReloadAfterSelfUpdate();
  } catch (_error) {
    // The old process may already be gone. Keep polling until the replacement server answers.
  }
  if (selfUpdateReloadState.attempts >= selfUpdateReloadMaxAttempts) {
    selfUpdateReloadState.pending = false;
    emitNotification('update', {title: t('update.softwareTitle'), lines: [t('update.restartTimeout')], coalesceKey: 'self-update-timeout'});
    return false;
  }
  scheduleSelfUpdateReloadPoll();
  return false;
}

function startSelfUpdateReloadPolling(target = '') {
  markSelfUpdateReloadPending(target);
  scheduleSelfUpdateReloadPoll(0);
}

function showServerUpdateBanner(version) {
  let banner = document.getElementById('serverUpdateBanner');
  if (banner && banner.parentElement) {
    banner.dataset.version = version;
    return;
  }
  banner = [...(document.body?.children || [])].find(node => node?.id === 'serverUpdateBanner') || null;
  if (banner) {
    banner.dataset.version = version;
    return;
  }
  banner = document.createElement('div');
  banner.id = 'serverUpdateBanner';
  banner.className = 'server-update-banner';
  banner.dataset.version = version;
  const msg = document.createElement('span');
  msg.className = 'server-update-banner-msg';
  msg.textContent = t('update.available');
  const reload = makeButton({className: 'server-update-banner-reload', label: t('common.reload'), onClick: () => location.reload()});
  const dismiss = makeButton({
    className: 'server-update-banner-dismiss',
    label: t('update.dismiss'),
    ariaLabel: t('update.dismiss'),
    onClick: () => banner.remove(),
  });
  const actions = document.createElement('div');
  actions.className = 'toast-control-row server-update-banner-actions';
  actions.append(reload, dismiss);
  banner.append(msg, actions);
  document.body.appendChild(banner);
}

function maybeHandleServerVersionChange(serverVersion, serverClientRevision = '') {
  // The boot version/revision only update on page load; this lets a long-lived
  // client learn that the running server no longer matches the bundle that booted this tab.
  const normalizedServerVersion = String(serverVersion || '');
  const bootVersion = String(bootstrap.version || '');
  const versionChanged = normalizedServerVersion && normalizedServerVersion !== bootVersion;
  const versionReloadAllowed = versionChanged && updateNotificationAllowsVersion(bootVersion, normalizedServerVersion);
  const bootClientRevision = String(bootstrap.clientRevision || '');
  const normalizedClientRevision = String(serverClientRevision || '');
  const reloadNotificationsEnabled = normalizeUpdateNotificationLevel(updateNotificationLevelSetting()) !== 'none';
  const clientRevisionChanged = reloadNotificationsEnabled && normalizedClientRevision && bootClientRevision && normalizedClientRevision !== bootClientRevision;
  if (!versionReloadAllowed && !clientRevisionChanged) return;
  if (versionReloadAllowed && selfUpdateOwnsServerVersion(normalizedServerVersion)) return;
  const reloadKey = versionReloadAllowed ? `version:${normalizedServerVersion}` : `client:${normalizedClientRevision}`;
  if (serverVersionReloadHandled === reloadKey) return;
  serverVersionReloadHandled = reloadKey;
  if (boolSetting('general.reload_on_update_auto', false) && reloadIsSafe()) {
    location.reload();
    return;
  }
  showServerUpdateBanner(versionReloadAllowed ? normalizedServerVersion : reloadKey);
}

async function applySessionMetadataPayload(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const requestIsCurrent = typeof options.requestIsCurrent === 'function' ? options.requestIsCurrent : () => true;
  if (!requestIsCurrent()) return false;
  setTranscriptMetadataPayload(transcriptPayloadWithTmuxWindowOverrides(payload), {invalidateRequest: options.source !== 'request'});
  // Metadata can arrive after the more-frequent auto-approve poll. Keep every agent window that
  // poll already proved exists, so a late or missed tmux window event cannot make buttons vanish
  // until the next poll repairs the client model.
  for (const session of Object.keys(transcriptMetadataState.payload.sessions || {})) {
    reconcileTmuxWindowMetadataFromAgentWindows(session, autoApproveStates.get(session));
  }
  transcriptMetadataState.loaded = true;
  transcriptMetadataState.error = null;
  if (typeof warmTabberDataOnLaunch === 'function') warmTabberDataOnLaunch();
  maybeHandleServerVersionChange(transcriptMetadataState.payload.server_version, transcriptMetadataState.payload.client_revision);
  applyAgentAvailabilityPayload(transcriptMetadataState.payload);
  updateMetadataBadgePulses(transcriptMetadataState.payload);
  const previousActive = activeSessions.slice();
  const sessionsChanged = updateSessionList(transcriptMetadataState.payload.session_order || []);
  if (options.refreshAuto !== false) {
    await loadAutoStatuses();
  }
  if (!requestIsCurrent()) return false;
  transcriptMetadataState.loading = false;
  if (sessionsChanged) renderPanels(previousActive);
  renderSessionButtons();
  renderInfoPanel();
  renderYoagentPanel();
  if (options.refreshActivity !== false) refreshActivitySummary({silent: true});
  for (const session of activeSessions.filter(isTmuxSession)) {
    const preview = document.getElementById(transcriptDomId(session));
    const info = transcriptMetadataState.payload.sessions?.[session];
    const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
    updatePanelHeader(session, info);
    renderSummaryContext(session, info, agent);
    if (!preview) continue;
    if (agent?.transcript) {
      updateTranscriptPathRow(session, agent.transcript);
      if (options.refreshContext === false) continue;
      if (typeof transcriptPreviewPaneIsActive === 'function' && !transcriptPreviewPaneIsActive(session)) continue;
      clearTranscriptContextLoadError(preview);
      preview.textContent = `${t('transcript.meta', {sessionId: agent.session_id || '', status: agent.status || ''})}\n\n${t('transcript.loadingRecentContext')}`;
      refreshTranscriptPreview(session, preview, {preserveScroll: false});
    } else {
      relocalizeTranscriptPanelStatus(session);
    }
  }
  renderPaneTabStrips();
  scheduleFileExplorerActiveTabSync();
  if (!shareViewMode && typeof syncServerWatchRoots === 'function') syncServerWatchRoots({renew: true});
  if (!shareViewMode) scheduleShareUiStatePublish();
  trackSessionStateChanges();
  refreshOpenEventLogs();
  return true;
}

function applyTranscriptsPayload(payload, options = {}) {
  return applySessionMetadataPayload(payload, options);
}

async function fetchAndApplySessionMetadata(fetchPayload, applyPayload) {
  let payload;
  try {
    payload = await fetchPayload();
  } catch (error) {
    return {ok: false, stage: 'fetch', error};
  }
  try {
    await applyPayload(payload);
  } catch (error) {
    return {ok: false, stage: 'apply', error};
  }
  return {ok: true, payload};
}

function transcriptMetadataLoadErrorSnapshot(error, stage = 'fetch') {
  const normalizedStage = stage === 'apply' ? 'apply' : 'fetch';
  const fallback = normalizedStage === 'fetch'
    ? {key: 'transcript.lookupFailed', params: {}, fallback: ''}
    : {key: '', params: {}, fallback: String(error?.message || error || '')};
  return {...userMessageSnapshot(error, fallback), stage: normalizedStage};
}

async function refreshSessionMetadata(options = {}) {
  if (transcriptMetadataState.request) return transcriptMetadataState.request;
  const requestIsCurrent = transcriptMetadataState.guard.begin();
  transcriptMetadataState.loading = true;
  transcriptMetadataState.error = null;
  syncTranscriptMetaLoadingUi();
  renderInfoPanel();
  const request = (async () => {
    try {
      const params = new URLSearchParams();
      if (options.force === true) params.set('force', '1');
      const suffix = params.toString();
      const result = await fetchAndApplySessionMetadata(
        () => apiFetchJson(`/api/session-metadata${suffix ? `?${suffix}` : ''}`),
        payload => applySessionMetadataPayload(payload, {
          refreshAuto: options.refreshAuto !== false,
          refreshContext: true,
          refreshActivity: options.refreshActivity !== false,
          source: 'request',
          requestIsCurrent,
        }),
      );
      if (!result.ok && requestIsCurrent()) {
        transcriptMetadataState.error = transcriptMetadataLoadErrorSnapshot(result.error, result.stage);
        console.error(`session metadata ${result.stage} failed`, result.error);
        for (const session of activeSessions.filter(isTmuxSession)) {
          renderTranscriptMetadataLoadError(session);
        }
      }
    } finally {
      if (transcriptMetadataState.request === request) {
        transcriptMetadataState.loading = false;
        transcriptMetadataState.request = null;
        syncTranscriptMetaLoadingUi();
        renderInfoPanel();
      }
    }
  })();
  transcriptMetadataState.request = request;
  return request;
}

async function refreshTranscripts(options = {}) {
  return refreshSessionMetadata(options);
}

let paneInfoBarResizeObserver = null;
const PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS = 3;
const PANE_INFO_BAR_SCROLL_END_HOLD_SECONDS = 2;

function paneInfoBarScrollDurationSeconds(distancePx) {
  const distance = Math.max(0, Number(distancePx) || 0);
  return Math.min(90, Math.max(12, distance / 22));
}

function paneInfoBarScrollTiming(distancePx) {
  const travelSeconds = paneInfoBarScrollDurationSeconds(distancePx);
  const totalSeconds = PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS + travelSeconds + PANE_INFO_BAR_SCROLL_END_HOLD_SECONDS;
  const startPercent = (PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS / totalSeconds) * 100;
  const endPercent = ((PANE_INFO_BAR_SCROLL_START_HOLD_SECONDS + travelSeconds) / totalSeconds) * 100;
  return {
    totalSeconds,
    timing: `linear(0 0%, 0 ${startPercent.toFixed(2)}%, 1 ${endPercent.toFixed(2)}%, 1 100%)`,
  };
}

function setStylePropertyIfChanged(style, name, value) {
  if (!style) return;
  const next = String(value);
  if (style.getPropertyValue?.(name) === next) return;
  style.setProperty?.(name, next);
}

function removeStylePropertyIfPresent(style, name) {
  if (!style?.getPropertyValue?.(name)) return;
  style.removeProperty?.(name);
}

function paneInfoBarMetaNodes(root = document) {
  if (!root) return [];
  const nodes = [];
  if (root.matches?.('.pane-info-bar-meta')) nodes.push(root);
  if (typeof root.querySelectorAll === 'function') nodes.push(...root.querySelectorAll('.pane-info-bar-meta'));
  return [...new Set(nodes)];
}

function observePaneInfoBarResizeTarget(target) {
  if (!target || !paneInfoBarResizeObserver) return;
  if (target._paneInfoBarResizeObserved === true) return;
  target._paneInfoBarResizeObserved = true;
  paneInfoBarResizeObserver.observe(target);
}

function ensurePaneInfoBarResizeObserver(meta, viewport = null, text = null) {
  if (!meta || typeof window === 'undefined' || typeof window.ResizeObserver !== 'function') return;
  if (!paneInfoBarResizeObserver) {
    paneInfoBarResizeObserver = new ResizeObserver(entries => {
      for (const entry of entries || []) schedulePaneInfoBarMetaOverflowSync(entry?.target?.closest?.('.pane-info-bar') || entry?.target || document);
    });
  }
  observePaneInfoBarResizeTarget(meta);
  const bar = meta.closest?.('.pane-info-bar');
  observePaneInfoBarResizeTarget(bar);
  observePaneInfoBarResizeTarget(viewport);
  observePaneInfoBarResizeTarget(text);
}

function syncPaneInfoBarMetaOverflow(root = document) {
  for (const meta of paneInfoBarMetaNodes(root)) {
    const viewport = meta.querySelector?.('.pane-info-bar-scroll-viewport');
    const text = viewport?.querySelector?.('.pane-info-bar-scroll-text');
    ensurePaneInfoBarResizeObserver(meta, viewport, text);
    if (!viewport || !text) {
      meta.classList?.remove?.('pane-info-bar-meta-overflow');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-distance');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-offset');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-duration');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-timing');
      continue;
    }
    const viewportWidth = Number(viewport.clientWidth || viewport.getBoundingClientRect?.().width || 0);
    const textWidth = Number(text.scrollWidth || text.getBoundingClientRect?.().width || 0);
    const distance = Math.max(0, Math.ceil(textWidth - viewportWidth));
    const overflowing = distance > 1;
    meta.classList?.toggle?.('pane-info-bar-meta-overflow', overflowing);
    if (overflowing) {
      const previousDistance = Number.parseFloat(meta.style?.getPropertyValue?.('--pane-info-bar-scroll-distance') || '');
      const scrollDistance = Number.isFinite(previousDistance) && previousDistance > 0 && Math.abs(previousDistance - distance) <= 4
        ? previousDistance
        : distance;
      const scrollTiming = paneInfoBarScrollTiming(scrollDistance);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-distance', `${scrollDistance}px`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-offset', `${-scrollDistance}px`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-duration', `${scrollTiming.totalSeconds.toFixed(2)}s`);
      setStylePropertyIfChanged(meta.style, '--pane-info-bar-scroll-timing', scrollTiming.timing);
    } else {
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-distance');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-offset');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-duration');
      removeStylePropertyIfPresent(meta.style, '--pane-info-bar-scroll-timing');
    }
  }
}

function schedulePaneInfoBarMetaOverflowSync(root = document) {
  const run = () => syncPaneInfoBarMetaOverflow(root);
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => {
    run();
    requestAnimationFrame(run);
  });
  else setTimeout(run, 0);
}

function updatePanelInfoBarMeta(session, info) {
  const meta = document.getElementById(`meta-${session}`);
  if (!meta) return;
  const controls = document.getElementById(`meta-controls-${session}`);
  const {controlsHtml, metadataHtml} = paneInfoBarMetaParts(session, info);
  const html = stripTitleAttrs(metadataHtml);
  if (controls && controls.innerHTML !== controlsHtml) controls.innerHTML = controlsHtml;
  const changed = meta.innerHTML !== html;
  if (changed) meta.innerHTML = html;
  meta.removeAttribute('title');
  if (changed) schedulePaneInfoBarMetaOverflowSync(meta);
}

function updatePanelHeader(session, info) {
  const tab = document.getElementById(paneTabDomId(session));
  const panel = document.getElementById(panelDomId(session));
  const auto = autoApproveStates.get(session)?.enabled === true;
  const state = sessionState(session, info);
  updatePanelControlLabels(session, info);
  syncAttentionAnimation(panel, state.attention === true);
  if (tab) {
    tab.className = ['panel-session-label', auto ? 'auto' : '', state.attention ? STATE_CLASS.needsAttention : ''].filter(Boolean).join(' ');
    syncAttentionAnimation(tab, state.attention === true);
    tab.innerHTML = panelHeaderStateHtml(state);
    tab.removeAttribute('title');
  }
  scheduleAgentWindowActivityAnimationSync(panel || document);
  updatePanelInfoBarMeta(session, info);
  const popover = panel?.querySelector(':scope .panel-popover-zone > .session-popover');
  if (popover) {
    const agentKind = sessionAgentKind(session);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = sessionPopoverHtml(session, info, agentKind, auto, state);
    popover.replaceWith(wrapper.firstElementChild);
  }
  panel?.classList.toggle(STATE_CLASS.needsInputPane, state.key === STATE_KEY.needsInput && state.attention === true);
  panel?.classList.toggle(STATE_CLASS.needsExecPane, state.key === STATE_KEY.needsApproval && state.attention === true);
  panel?.classList.toggle(STATE_CLASS.needsBlockedPane, state.key === STATE_KEY.blocked);
}

function refreshSessionChrome(session) {
  updateSessionButtonStates();
  updatePanelHeader(session, transcriptMetadataState.payload.sessions?.[session]);
}

function refreshTrackedSessionChrome(session) {
  refreshSessionChrome(session);
  trackSessionStateChanges();
}

function refreshActivePanelHeaders() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    relocalizeTerminalPanelChrome(session);
  }
}

function renderSummaryContext(session, info, agent) {
  const node = document.getElementById(`summary-context-${session}`);
  if (!node) return;
  delete node.dataset.localeTextKey;
  node.innerHTML = summaryContextHtml(session, info, agent);
}

function relocalizeTerminalPanelChrome(session) {
  const panel = document.getElementById(panelDomId(session));
  if (!panel) return false;
  relocalizeVirtualPanelChrome(panel);
  const terminalTab = panel.querySelector('.terminal-tab');
  if (terminalTab) {
    const info = transcriptMetadataState.payload.sessions?.[session];
    const title = terminalTabTitle(session, info);
    terminalTab.textContent = terminalTabLabel(session, info);
    terminalTab.title = title;
    terminalTab.setAttribute('aria-label', title);
  }
  panel.querySelectorAll('.pane-actions').forEach(button => {
    button.title = t('common.sessionActions');
    button.setAttribute('aria-label', t('common.sessionActions'));
  });
  updatePanelHeader(session, transcriptMetadataState.payload.sessions?.[session]);
  panel.querySelectorAll('[data-locale-text-key]').forEach(node => {
    node.textContent = t(node.dataset.localeTextKey);
  });
  const statusToggle = panel.querySelector(`[data-tmux-status-toggle="${cssEscape(session)}"]`);
  if (statusToggle) statusToggle.outerHTML = tmuxStatusToggleHtml(session);
  const transcriptHead = panel.querySelector(`#transcript-pane-${cssEscape(session)} .transcript-head`);
  if (transcriptHead) transcriptHead.textContent = t('common.transcript');
  const summaryHead = panel.querySelector(`#summary-pane-${cssEscape(session)} .transcript-head`);
  if (summaryHead) summaryHead.textContent = t('menu.tmux.aiTranscript', {session: sessionLabel(session)});
  const eventsHead = panel.querySelector(`#events-pane-${cssEscape(session)} .transcript-head`);
  if (eventsHead) eventsHead.textContent = t('events.title');
  relocalizeTranscriptPanelStatus(session);
  return true;
}

function transcriptPathRowHtml(path, fallback = '') {
  if (!path) return `<span class="transcript-path-missing">${esc(fallback || t('transcript.noPath'))}</span>`;
  return `<span class="transcript-path-label">${esc(t('common.field.path'))}</span><span class="transcript-path-value">${esc(path)}</span>${pathCopyButtonHtml(path, {className: 'transcript-path-copy', title: t('common.copyTranscriptPath')})}`;
}

function updateTranscriptPathRow(session, path, fallback = '') {
  const row = document.getElementById(`transcript-path-${session}`);
  if (!row) return;
  row.innerHTML = transcriptPathRowHtml(path, fallback);
}

function transcriptMetadataLoadErrorText() {
  const fallback = transcriptMetadataState.error?.stage === 'apply'
    ? t('common.requestFailed')
    : t('transcript.lookupFailed');
  return userMessageText(transcriptMetadataState.error, fallback);
}

function transcriptMetadataLoadErrorLabel() {
  return transcriptMetadataState.error?.stage === 'apply'
    ? transcriptMetadataLoadErrorText()
    : t('transcript.lookupFailed');
}

function transcriptAgentErrorText(agent) {
  return structuredMessageText(agent, 'error', String(agent?.error || t('transcript.noAgentFound')));
}

function transcriptContextLoadErrorText(error) {
  return t('transcript.contextLoadFailed', {error: userMessageText(error, t('common.requestFailed'))});
}

function clearTranscriptContextLoadError(preview) {
  if (!preview) return;
  delete preview._transcriptContextLoadError;
  preview.querySelector?.('.transcript-context-error')?.remove?.();
}

function renderTranscriptContextLoadError(preview) {
  if (!preview) return;
  const error = preview._transcriptContextLoadError;
  const existing = preview.querySelector?.('.transcript-context-error');
  if (!error) {
    existing?.remove?.();
    return;
  }
  const html = `<div class="transcript-item system transcript-context-error"><div class="transcript-text">${esc(transcriptContextLoadErrorText(error))}</div></div>`;
  if (existing) existing.outerHTML = html;
  else preview.insertAdjacentHTML?.('beforeend', html);
}

function renderTranscriptMetadataLoadError(session) {
  const meta = document.getElementById(`meta-${session}`);
  const preview = document.getElementById(transcriptDomId(session));
  const error = transcriptMetadataLoadErrorText();
  const label = transcriptMetadataLoadErrorLabel();
  if (meta) meta.innerHTML = `<span class="err">${esc(label)}</span>`;
  updateTranscriptPathRow(session, '', label);
  if (preview) {
    clearTranscriptContextLoadError(preview);
    preview.textContent = transcriptMetadataState.error?.stage === 'apply'
      ? error
      : t('transcript.lookupFailedWithError', {error});
  }
}

function relocalizeTranscriptPanelStatus(session) {
  const preview = document.getElementById(transcriptDomId(session));
  if (!preview) return;
  if (transcriptMetadataState.error) {
    renderTranscriptMetadataLoadError(session);
    return;
  }
  const info = transcriptMetadataState.payload.sessions?.[session];
  const agent = info?.agents?.find(item => item.transcript) || info?.agents?.[0];
  if (agent?.transcript) {
    renderTranscriptContextLoadError(preview);
    return;
  }
  clearTranscriptContextLoadError(preview);
  const message = agent?.error ? transcriptAgentErrorText(agent) : t('transcript.noAgentFound');
  updateTranscriptPathRow(session, '', message);
  preview.textContent = message;
}

async function refreshTranscriptPreview(session, preview, options = {}) {
  try {
    const payload = await apiFetchJson(`/api/context-items?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
    if (!applyContextItemsPayloadFromPush(payload, options)) {
      preview.textContent = JSON.stringify(payload, null, 2);
    }
  } catch (error) {
    preview._transcriptContextLoadError = userMessageSnapshot(error, {key: 'common.requestFailed', params: {}, fallback: ''});
    renderTranscriptContextLoadError(preview);
  }
}

function applyContextItemsPayloadFromPush(payload = {}, options = {}) {
  if (!payload || !payload.items) return false;
  const session = payload.session || options.session || '';
  const preview = options.preview || (session ? document.getElementById(transcriptDomId(session)) : null);
  if (!preview) return false;
  updateTranscriptPathRow(session, payload.path);
  renderTranscriptItems(preview, payload.path, payload.items, options);
  return true;
}

function startTranscriptStream(session, options = {}) {
  stopTranscriptStream(session);
  const preview = document.getElementById(transcriptDomId(session));
  if (!preview) return;
  const url = `/api/context-stream?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`;
  const source = new EventSource(url);
  transcriptStreams.set(session, source);
  source.addEventListener('reset', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    updateTranscriptPathRow(session, payload.path);
    renderTranscriptItems(preview, payload.path, payload.items || [], {scrollBottom: options.scrollBottom === true});
  });
  source.addEventListener('items', event => {
    const payload = safeJsonParse(event.data, null);
    if (!payload) return;
    appendTranscriptItems(preview, payload.items || []);
  });
  source.addEventListener('ping', () => {});
  source.onerror = () => {
    stopTranscriptStream(session);
    const pane = document.getElementById(`transcript-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) {
      statusErr(localizedHtml('terminal.transcript.streamDisconnected', {session: sessionLabel(session)}));
      setTimeout(() => {
        if (document.getElementById(`transcript-pane-${session}`)?.classList.contains(CLS.active)) {
          startTranscriptStream(session, {scrollBottom: false});
        }
      }, 1500);
    }
  };
}

function stopTranscriptStream(session) {
  const source = transcriptStreams.get(session);
  if (source) {
    source.close();
    transcriptStreams.delete(session);
  }
}

function renderTranscriptItems(container, path, items, options = {}) {
  const shouldScrollBottom = options.scrollBottom === true;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  const oldTop = container.scrollTop;
  const oldHeight = container.scrollHeight;
  const blocks = items.map(item => transcriptItemHtml(item));
  delete container._transcriptContextLoadError;
  container.innerHTML = blocks.join('');
  if (shouldScrollBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  } else if (options.preserveScroll) {
    if (wasNearBottom) {
      container.scrollTop = container.scrollHeight;
    } else {
      container.scrollTop = Math.max(0, oldTop + container.scrollHeight - oldHeight);
    }
  } else {
    container.scrollTop = container.scrollHeight;
  }
}

function appendTranscriptItems(container, items) {
  if (!items.length) return;
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 32;
  container.insertAdjacentHTML('beforeend', items.map(item => transcriptItemHtml(item)).join(''));
  const rendered = Array.from(container.querySelectorAll('.transcript-item:not(.system)'));
  const extra = rendered.length - transcriptPreviewMessages;
  for (const item of rendered.slice(0, Math.max(0, extra))) item.remove();
  if (wasNearBottom) {
    requestAnimationFrame(() => {
      container.scrollTop = container.scrollHeight;
    });
  }
}

function transcriptItemHtml(item) {
  const role = normalizeRole(item.role);
  const roleKeys = {
    assistant: 'transcript.role.assistant',
    user: 'transcript.role.user',
    tool_use: 'transcript.role.toolUse',
    tool_result: 'transcript.role.toolResult',
    summary: 'transcript.role.summary',
    system: 'transcript.role.system',
  };
  const roleLabel = t(roleKeys[role] || 'common.message');
  const meta = [item.timestamp, item.cwd].map(value => String(value || '')).filter(Boolean).join(', ');
  const header = meta ? t('transcript.itemHeader', {role: roleLabel, meta}) : roleLabel;
  return `<div class="transcript-item ${role}">
    <div class="transcript-role">${esc(header)}</div>
    <div class="transcript-text">${esc(item.text || '')}</div>
  </div>`;
}

function eventItemHtml(event) {
  const details = event.details && typeof event.details === 'object' ? event.details : {};
  const message = structuredMessageText(event, 'message');
  const detailText = Object.entries(details)
    .filter(([, value]) => value != null && value !== '')
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : value}`)
    .join(' · ');
  const title = detailText ? `${message}\n${detailText}` : message;
  return `<div class="event-item" title="${esc(title)}">
    <span class="event-time">${esc(formatEventTime(event.time))}</span>
    <span class="event-type">${esc(event.type || t('common.eventLabel'))}</span>
    <span class="event-message">${esc(message)}${detailText ? ` · ${esc(detailText)}` : ''}</span>
  </div>`;
}

function formatEventTime(value) {
  const date = new Date(value || 0);
  if (Number.isNaN(date.getTime())) return '';
  return localizedDateTimeFormat(date.getTime() / 1000, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

async function refreshEventLog(session) {
  const node = document.getElementById(`events-${session}`);
  if (!node) return;
  delete node.dataset.localeTextKey;
  try {
    const payload = await apiFetchJson(`/api/events?session=${encodeURIComponent(session)}&limit=120`);
    const events = Array.isArray(payload.events) ? payload.events : [];
    node.innerHTML = events.length
      ? events.slice().reverse().map(eventItemHtml).join('')
      : `<div class="event-empty">${esc(t('events.empty'))}</div>`;
  } catch (error) {
    if (error?.status) {
      node.innerHTML = `<div class="event-empty">${esc(userMessageText(error.payload, t('events.loadFailed')))}</div>`;
      return;
    }
    node.innerHTML = `<div class="event-empty">${localizedHtml('events.loadFailedWithError', {error})}</div>`;
  }
}

function refreshOpenEventLogs() {
  for (const session of activeSessions.filter(isTmuxSession)) {
    const pane = document.getElementById(`events-pane-${session}`);
    if (pane?.classList.contains(CLS.active)) refreshEventLog(session);
  }
}

function postEvent(session, type, message, details = {}) {
  apiFetch('/api/event', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({session, type, message, details}),
  }).then(() => {
    refreshOpenEventLogs();
  }).catch(() => {});
}

function normalizeRole(role) {
  const value = String(role || 'message').toLowerCase();
  if (value.includes('tool_use')) return 'tool_use';
  if (value.includes('tool_result')) return 'tool_result';
  if (value.includes('assistant')) return 'assistant';
  if (value.includes('user')) return 'user';
  if (value.includes('summary')) return 'summary';
  if (value.includes('system')) return 'system';
  return 'system';
}

function renderLatency(latestMs) {
  const samples = latencySamples.slice(-latencySamplesMax);
  if (samples.length === 0) {
    latencyLine.setAttribute('points', '');
  } else {
    const maxMs = Math.max(100, ...samples);
    const width = 44;
    const height = 18;
    const points = samples.map((value, index) => {
      const x = samples.length === 1 ? width : (index / (samples.length - 1)) * width;
      const y = height - 1 - (Math.min(value, maxMs) / maxMs) * (height - 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    latencyLine.setAttribute('points', points.join(' '));
  }

  latencyMeter.classList.remove('good', 'warn', 'bad');
  if (latestMs == null) {
    latencyMeter.classList.add('bad');
    latencyNumber.textContent = '-- ms';
    return;
  }
  latencyNumber.textContent = `${latestMs} ms`;
  if (latestMs <= 80) {
    latencyMeter.classList.add('good');
  } else if (latestMs <= 200) {
    latencyMeter.classList.add('warn');
  } else {
    latencyMeter.classList.add('bad');
  }
}

async function updateLatency() {
  if (document.visibilityState === 'hidden') return null;
  const startedAt = performance.now();
  try {
    await apiFetchJson(`/api/ping?t=${Date.now()}`, {cache: 'no-store'});
    const elapsedMs = Math.max(1, Math.round(performance.now() - startedAt));
    latencySamples = [...latencySamples, elapsedMs].slice(-latencySamplesMax);
    renderLatency(elapsedMs);
  } catch (_) {
    renderLatency(null);
  }
}

function refreshAll() {
  resyncVisibleTerminalRemoteSizes('refresh');
  refreshVisibleTerminalScreens('manual-refresh');
  refreshTranscripts({force: true});
  refreshBackgroundOwnerStatus({force: true});
  refreshAutoStatuses();
  refreshWatchedFilesystem({full: true});
}

function scheduleReconnectResync(reason = '') {
  if (clientEventTransportState.resyncTimer) clearTimeout(clientEventTransportState.resyncTimer);
  clientEventTransportState.resyncTimer = setTimeout(() => {
    clientEventTransportState.resyncTimer = null;
    refreshAll();
  }, reconnectResyncDebounceMs);
}

function resyncVisibleTerminalRemoteSizes(reason = '') {
  void reason;
  for (const session of activeSessions.filter(isTmuxSession)) {
    scheduleFit(session);
    forceRemoteResize(session);
  }
}

function installReconnectResyncHandlers() {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
      scheduleReconnectResync('visible');
      resyncVisibleTerminalRemoteSizes('visible');
      updateLatency();
      if (fileExplorerMode === 'tabber' && typeof fetchTabberActivity === 'function') fetchTabberActivity();
      if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots({immediate: true});
      return;
    }
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots({deactivate: true, immediate: true});
    if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
  });
  window.addEventListener('online', () => {
    scheduleReconnectResync('online');
    resyncVisibleTerminalRemoteSizes('online');
  });
}

let initialAppShellPainted = false;

function paintInitialAppShell() {
  if (initialAppShellPainted) return;
  renderSessionButtons();
  renderPanels([], {prune: false});
  seedVisualActivePaneItem(activeSessions);
  updatePanelInactiveOverlays();
  initialAppShellPainted = true;
}

async function boot() {
  installNativeAppViewportOwner();
  installTouchContextMenuOwner();
  syncNativeAppViewport({force: true});
  applySettingsPayload(clientSettingsPayload, {initial: true, force: true});
  installReconnectResyncHandlers();
  if (shareViewMode) {
    applyShareViewBodyClasses();
    const bootstrapUiState = shareBootstrap?.uiState && typeof shareBootstrap.uiState === 'object' ? shareBootstrap.uiState : {};
    applyShareViewportState(bootstrapUiState.viewport || shareBootstrap?.viewport || {});
    applyShareAppearanceState(bootstrapUiState.appearance || shareBootstrap?.appearance || {});
    applyShareMirrorTransform();
  }
  waitForYolomuxFontsReady({timeoutMs: 0}).catch(() => {});
  syncAppViewportBreakpointClasses();
  // i18n: AWAIT the active locale catalog (all-static-fetch) before the first render so menus,
  // tabs, and the wordmark paint in the right language from the start — no flash of raw t() keys (the
  // menu bar renders synchronously at boot, before any later re-render could fix it). A 'system' pref is
  // resolved client-side against navigator.language (the server can't see the browser locale).
  await applyLocale(resolveLocalePref(initialSetting('general.language', 'system')));
  installGlobalThemeMediaListener();
  if (installShareReplayShell()) {
    installDevAutoReload();
    return;
  }
  applyFileExplorerStaticLabels();
  renderTransportWarning();
  renderTabMetaToggle();
  bindTopbarMetrics();
  syncInitialLayoutUrl();
  statusEl.textContent = t('status.yoloLoading');
  let initialAutoStatusesPromise = Promise.resolve(false);
  if (!shareViewMode) {
    loadNotificationDelivery();
    refreshBackgroundOwnerStatus({render: false}).catch(error => {
      console.warn('initial background-owner status refresh failed', error);
      return false;
    });
    initialAutoStatusesPromise = loadAutoStatuses().catch(error => {
      console.warn('initial auto-status refresh failed', error);
      return false;
    });
  }
  bindClipboardPaste();
  paintInitialAppShell();
  scheduleDeferredSettingsMetadataRefresh();
  if (!shareViewMode) {
    await refreshTranscripts({refreshAuto: false});
  } else {
    setTranscriptMetadataPayload({session_order: sessions.slice(), sessions: Object.fromEntries(sessions.map(session => [session, {target: session}]))});
    transcriptMetadataState.loaded = true;
    await refreshTranscripts({refreshAuto: false, refreshActivity: false});
  }
  installYolomuxFontMetricRefresh();
  updatePanelInactiveOverlays();
  if (shareViewMode) {
    const bootstrapUiState = shareBootstrap?.uiState && typeof shareBootstrap.uiState === 'object' ? shareBootstrap.uiState : {};
    await applyShareUiState({
      ...bootstrapUiState,
      finder: bootstrapUiState.finder || shareBootstrap?.finder || {},
    });
  }
  if (!shareViewMode && clientPushCanSupplyData() && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
  await Promise.all(activeSessions.filter(isTmuxSession).map(session => ensureTerminalRunning(session)));
  if (!shareViewMode && typeof startJsDebugStatsPolling === 'function') startJsDebugStatsPolling();
  if (!shareViewMode && typeof primeJsDebugStatsBeforeLongLivedStreams === 'function') {
    await primeJsDebugStatsBeforeLongLivedStreams();
  }
  if (!shareViewMode) installClientEventStream();
  if (!shareViewMode) {
    initialAutoStatusesPromise.then(() => {
      renderAutoApproveButtons();
      updateSessionButtonStates();
      refreshActivePanelHeaders();
      trackSessionStateChanges();
    });
  }
  if (!shareViewMode) refreshWatchedPrs();
  renderAutoApproveButtons();
  updateLatency();
  installRuntimeIntervals();
  scheduleStartupHelperTip();
  installShareViewerBanner();
  installSharePointerPublisher();
  installShareScrollPublisher();
  installShareGeometryDigestLoop();
  installSharePopupLayerPublisher();
  installShareReplayMutationPublisher();
  startShareStatusRefresh();
  installDevAutoReload();
  document.querySelector('[data-update-badge]')?.addEventListener('click', triggerSelfUpdate);
  checkForUpdateOnce();
}

function clientEventEnvelope(event) {
  try {
    const parsed = JSON.parse(event?.data || '{}');
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (_error) {
    return {};
  }
}

function clientEventPayloadFromEnvelope(envelope) {
  return envelope && typeof envelope === 'object' && envelope.payload && typeof envelope.payload === 'object'
    ? envelope.payload
    : envelope;
}

function recordSseDebugEvent(eventType, envelope = {}, rawEvent = null) {
  if (!jsDebugCollectionEnabled) return;
  const payload = clientEventPayloadFromEnvelope(envelope);
  const rawData = rawEvent?.data || '';
  const dataBytes = utf8ByteLength(rawData);
  const dataLines = String(rawData || '').split(/\r?\n/);
  const frameBytes = utf8ByteLength(`event: ${eventType}\n`)
    + dataLines.reduce((total, line) => total + utf8ByteLength(`data: ${line}\n`), 0)
    + 1;
  const serverTimeMs = Number(envelope?.time) * 1000;
  const receiveLatencyMs = Number.isFinite(serverTimeMs)
    ? Math.max(0, Number((Date.now() - serverTimeMs).toFixed(1)))
    : undefined;
  recordJsDebugEvent('sse', {
    eventType,
    serverEventId: Number(envelope?.id || 0) || undefined,
    trigger: payload?.trigger || '',
    cache: payload?.cache || '',
    computeMs: Number.isFinite(Number(payload?.compute_ms)) ? Number(payload.compute_ms) : undefined,
    receiveLatencyMs,
    bytes: dataBytes,
    frameBytes,
    changeSummary: payload?.change_summary && typeof payload.change_summary === 'object' ? payload.change_summary : null,
    listingSummary: payload?.listing_summary && typeof payload.listing_summary === 'object' ? payload.listing_summary : null,
    phaseTimings: payload?.timings && typeof payload.timings === 'object' ? payload.timings : null,
    key: payload?.session || payload?.locale || payload?.request?.session || '',
  });
}

function updateDryRunEnabled() {
  return typeof urlFlagEnabled === 'function' && urlFlagEnabled('updateDryRun');
}

function updateActionButton(label, onClick) {
  const button = makeButton({
    className: 'toast-action',
    label,
    onClick: event => {
      event.stopPropagation();
      onClick(event, button.closest('.toast'));
    },
  });
  return button;
}

async function triggerSelfUpdate(_event = null, ownerToast = null) {
  const dry = updateDryRunEnabled();
  const confirmed = window.confirm(dry
    ? t('update.confirmDryRun')
    : t('update.confirmInstall'));
  if (!confirmed) return;
  const target = String(ownerToast?.dataset?.updateTarget || selfUpdateAvailableTarget || '').trim();
  dismissUpdateAvailableToasts(ownerToast);
  hideUpdateBadge();
  try {
    const data = await apiFetchJson(`/api/self-update${dry ? '?dryrun=1' : ''}`, {method: 'POST'});
    const title = data.ok ? (data.restarting ? t('update.installing') : t('update.softwareTitle')) : t('update.failed');
    emitNotification('update', {title, lines: [userMessageText(data, t(data.ok ? 'state.done' : 'update.seeServerLogs'))], coalesceKey: 'self-update-result'});
    if (data.ok && data.restarting) {
      startSelfUpdateReloadPolling(data.target || data.version || target);
    }
  } catch (error) {
    emitNotification('update', {title: t('update.failed'), lines: [userMessageText(error, t('update.seeServerLogs'))], coalesceKey: 'self-update-result'});
  }
}

// Non-intrusive "a newer version exists" cue: unhide the topbar badge and show one dismissible toast
// with an "Update Now" action (admin-only; the endpoint rejects readonly).
function applyUpdateAvailable(status) {
  if (!status || !status.available) return;
  if (status.notify === false) return;
  const target = String(status.target || '').trim();
  selfUpdateAvailableTarget = target;
  const badge = document.querySelector('[data-update-badge]');
  if (badge) {
    badge.hidden = false;
    if (target) badge.dataset.updateTarget = target;
    else delete badge.dataset.updateTarget;
    renderUpdateBadgeChrome();
  }
  const node = emitNotification('update', {
    title: t('update.availableTitle'),
    lines: [t('update.availableBody', {target: status.target ? ` (${status.target})` : ''})],
    actions: [updateActionButton(t('update.now'), triggerSelfUpdate)],
    countdownMs: 4 * 60 * 60 * 1000,  // keep the update cue up for 4 hours, not the default ~10s
    className: 'attention-alert toast toast-update',  // solid (opaque) background, not the translucent default
    coalesceKey: 'update-available',
  }).inApp;
  if (node && target) node.dataset.updateTarget = target;
}

async function checkForUpdateOnce() {
  try {
    const status = await apiFetchJson(`/api/update-status${updateDryRunEnabled() ? '?dryrun=1' : ''}`);
    if (status && status.available) applyUpdateAvailable(status);
  } catch (_error) { /* offline / transient — the hourly push will retry */ }
}

function yoagentJobNotificationTitle(notification = {}) {
  return structuredMessageText(notification, 'title', t('brand.tab.agent'));
}

function yoagentJobNotificationBody(notification = {}) {
  return structuredMessageText(notification, 'body', '').trim();
}

function maybeNotifyYoagentJob(notification = {}) {
  const title = yoagentJobNotificationTitle(notification);
  const body = yoagentJobNotificationBody(notification);
  if (!body || !notificationDeliveryEnabled()) return;
  const session = String(notification.session || '').trim();
  const tag = `yoagent-job:${session || 'global'}:${body}`;
  try {
    emitNotification('yoagentJob', {
      session, title, body, systemTitle: hostNotificationTitle(title),
      systemTag: tag, renotify: true, coalesceKey: tag,
    });
  } catch (error) {
    postEvent(session || null, 'yoagent_job_notification_error', `notification failed: ${error}`, {});
  }
}

function tmuxSignalsPayloadWithWindowOverrides(data) {
  if (!data || typeof data !== 'object' || !Array.isArray(data.windows)) return data;
  const overrides = new Map();
  for (const [session, override] of tmuxWindowActiveIndexOverrideEntries()) {
    if (override === tmuxWindowPendingActiveIndex) continue;
    const indexKey = tmuxWindowIndexKey(override);
    if (indexKey !== null) overrides.set(String(session), indexKey);
  }
  if (typeof tmuxWindowDirectTargetGuardEntries === 'function') {
    for (const [session, guard] of tmuxWindowDirectTargetGuardEntries()) {
      if (overrides.has(session)) continue;
      const guardIndex = tmuxWindowIndexKey(guard?.index);
      if (guardIndex !== null) overrides.set(String(session), guardIndex);
    }
  }
  if (!overrides.size) return data;
  let changed = false;
  const windows = data.windows.map(windowRecord => {
    const session = tmuxSignalWindowSession(windowRecord);
    const override = overrides.get(session);
    if (override === undefined) return windowRecord;
    const active = override === tmuxWindowIndexKey(windowRecord?.window_index);
    if (windowRecord?.active === active) return windowRecord;
    changed = true;
    return {...windowRecord, active};
  });
  return changed ? {...data, windows} : data;
}

function tmuxSignalsPayloadWithPatch(data) {
  if (!data || typeof data !== 'object' || data.patch !== true) return data;
  if (!tmuxSignalState || typeof tmuxSignalState !== 'object' || !Array.isArray(tmuxSignalState.windows)) return data;
  const nextByKey = new Map(tmuxSignalState.windows.map(windowRecord => [tmuxSignalWindowKey(windowRecord), windowRecord]).filter(([key]) => key));
  for (const key of data.removed_window_keys || []) {
    nextByKey.delete(String(key || ''));
  }
  for (const windowRecord of data.windows || []) {
    const key = tmuxSignalWindowKey(windowRecord);
    if (key) nextByKey.set(key, windowRecord);
  }
  return {
    ...tmuxSignalState,
    ...data,
    patch: false,
    windows: Array.from(nextByKey.values()),
  };
}

function recordTmuxSignalRemovedWindowLatencies(data) {
  if (!data || typeof data !== 'object') return;
  const removedWindowEventAt = Number(data.removed_window_event_at);
  const removedWindowEventType = String(data.removed_window_event_type || '');
  for (const key of data.removed_window_keys || []) {
    const windowKey = String(key || '');
    if (!windowKey) continue;
    completeTerminalRemovalLatencyFromEpochSeconds('window', windowKey, removedWindowEventAt, {
      origin: removedWindowEventType || 'tmux-signal',
      eventType: removedWindowEventType,
      reason: data.patch === true ? 'tmux-signal-patch' : 'tmux-signal-snapshot',
    });
  }
}

function applyTmuxSignalsPayload(payload = {}) {
  const rawData = tmuxSignalsPayloadWithPatch(tmuxSignalPayloadData(payload));
  const data = tmuxSignalsPayloadWithWindowOverrides(rawData);
  if (!data || typeof data !== 'object') return null;
  recordTmuxSignalRemovedWindowLatencies(data);
  tmuxSignalState = data;
  applyTmuxSignalActiveWindowsToTranscriptInfo(data);
  confirmTmuxWindowActiveOverridesFromRawSignals(rawData);
  reconcileTmuxWindowDirectTargetGuardsFromRawSignals(rawData);
  return data;
}

function clientPushEventSessionKey(payload = {}) {
  return String(payload.session || payload.request?.session || payload.data?.session || payload.data?.target || '');
}

function clientPushEventCoalesceKey(type, payload = {}) {
  const key = String(type || 'event');
  const session = clientPushEventSessionKey(payload);
  if (session) return `${key}:${session}`;
  return key;
}

function queueClientPushEvent(type, payload = {}) {
  const key = clientPushEventCoalesceKey(type, payload);
  clientEventTransportState.queue.set(key, {type, payload});
  // Chrome pauses requestAnimationFrame in background tabs. Status events still have to update
  // notification state there, otherwise a complete green->red/yellow transition can be missed
  // before the user returns to YOLOmux.
  if (document.visibilityState === 'hidden') {
    if (clientEventTransportState.frame) cancelAnimationFrame(clientEventTransportState.frame);
    clientEventTransportState.frame = 0;
    flushQueuedClientPushEvents();
    return;
  }
  if (clientEventTransportState.frame) return;
  clientEventTransportState.frame = requestAnimationFrame(() => {
    clientEventTransportState.frame = 0;
    flushQueuedClientPushEvents();
  });
}

function flushQueuedClientPushEvents() {
  const events = Array.from(clientEventTransportState.queue.values());
  clientEventTransportState.queue.clear();
  recordClientPerfCounter('sseEvent', 0, {nodes: events.length});
  for (const event of events) handleClientPushEventNow(event.type, event.payload);
}

function handleClientPushEvent(type, payload = {}) {
  queueClientPushEvent(type, payload);
}

function handleClientPushEventNow(type, payload = {}) {
  if (type === 'update_available') {
    applyUpdateAvailable(payload && payload.available !== undefined ? payload : (payload.data || {}));
    return;
  }
  if (type === 'settings_changed') {
    if (payload.data && typeof payload.data === 'object') {
      applySettingsPayload(payload.data, {force: true});
    }
    return;
  }
  if (type === 'auto_approve_changed') {
    if (payload.refresh) {
      refreshAutoStatuses().catch(() => {});
      return;
    }
    if (payload.data) applyAutoApprovePayload(payload.data);
    return;
  }
  if (type === 'attention_acks_changed') {
    applyAttentionAcknowledgementResponse(payload);
    return;
  }
  if (type === 'background_owner_changed') {
    if (!applyBackgroundOwnerStatusPayload(payload)) {
      refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('background-owner status refresh failed', error));
    }
    return;
  }
  if (type === 'background_refresh_done') {
    if (payload.role === 'search-index') {
      refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('search-index status refresh failed', error));
      if (commandPaletteState.node && !commandPaletteState.node.hidden && commandPaletteEffectiveMode() === 'files') {
        refreshFileQuickOpenCandidates(commandPaletteState.query).catch(error => console.warn('search-index quick-open refresh failed', error));
      }
    }
    if (payload.role === 'session-files') {
      const session = String(payload.session || '');
      if (!session || session === fileExplorerSessionFilesTargetSession()) {
        fetchSessionFiles({silent: true}).catch(error => console.warn('session-files refresh failed', error));
      }
    }
    return;
  }
  if (type === 'tmux_signals_changed') {
    applyTmuxSignalsPayload(payload);
    if (typeof updatePanelWindowStepButtons === 'function' && typeof activePaneItems === 'function') {
      for (const session of activePaneItems()) {
        if (typeof isTmuxSession === 'function' && !isTmuxSession(session)) continue;
        updatePanelWindowStepButtons(session, transcriptMetadataState.payload.sessions?.[session]);
      }
    }
    return;
  }
  if (type === 'watched_prs_changed') {
    if (payload.data) applyWatchedPrsPayload(payload.data);
    return;
  }
  if (type === 'transcripts_changed') {
    if (payload.data) {
      applyTranscriptsPayload(payload.data, {refreshAuto: false, refreshContext: false, refreshActivity: false});
    } else {
      refreshTranscripts({refreshAuto: false, refreshActivity: false}).catch(error => console.warn('client-events transcript refresh failed', error));
    }
    return;
  }
  if (type === 'context_items_ready') {
    if (payload.data) applyContextItemsPayloadFromPush(payload.data, {session: payload.session, preserveScroll: true});
    return;
  }
  if (type === 'activity_summary_ready') {
    if (payload.data) applyActivitySummaryPayloadFromPush(payload.data);
    return;
  }
  if (type === 'yoagent_conversation_changed') {
    loadYoagentConversation({force: true, render: yoagentPanelIsActive(), scrollBottom: 'auto'}).catch(error => console.warn('YO!agent conversation refresh failed', error));
    return;
  }
  if (type === 'yoagent_stream_delta') {
    if (typeof applyYoagentStreamPayload === 'function' && applyYoagentStreamPayload(payload)) {
      renderYoagentPanel({preserveDraft: true, scrollBottom: 'auto'});
    }
    return;
  }
  if (type === 'yoagent_jobs_changed') {
    if (typeof loadYoagentJobs === 'function') {
      loadYoagentJobs({force: true, silent: true, render: yoagentPanelIsActive(), scrollBottom: false}).catch(error => console.warn('YO!agent jobs refresh failed', error));
    }
    maybeNotifyYoagentJob(payload.notification || {});
    return;
  }
  if (type === 'yoagent_skills_changed') {
    refreshActivitySummary({force: true, render: yoagentPanelIsActive()}).catch(error => console.warn('YO!agent skills refresh failed', error));
    return;
  }
  if (type === 'chat_messages_changed' || type === 'chat_typing_changed') {
    if (typeof handleChatInvalidation === 'function') {
      handleChatInvalidation(type, payload);
    }
    return;
  }
  if (type === 'session_files_ready') {
    if (payload.data && typeof applySessionFilesPayloadFromPush === 'function') {
      applySessionFilesPayloadFromPush(payload.data, payload.request || {});
    }
    return;
  }
  if (type === 'files_changed') {
    if (typeof refreshOpenFilesFromPush === 'function') {
      refreshOpenFilesFromPush(payload).catch(error => console.warn('client file push refresh failed', error));
    }
    return;
  }
  if (type === 'fs_changed') {
    if (typeof refreshFileExplorerFromPush === 'function') {
      refreshFileExplorerFromPush(payload).catch(error => console.warn('client fs push refresh failed', error));
    }
  }
}

function clientEventDemandDescriptor() {
  const visible = document.visibilityState !== 'hidden';
  const activeItems = visible && typeof activePaneItems === 'function' ? activePaneItems() : [];
  const channels = new Set();
  const notificationAttention = typeof notificationDeliveryEnabled === 'function' && notificationDeliveryEnabled('system');
  const notificationChat = typeof notificationDeliveryEnabled === 'function'
    && (notificationDeliveryEnabled('inApp') || notificationDeliveryEnabled('system'));
  if (visible) {
    channels.add('core');
    channels.add('status');
    const finderActive = activeItems.includes(fileExplorerItemId);
    const fileEditorActive = activeItems.some(item => isFileEditorItem(item));
    if (finderActive && fileExplorerMode === 'tabber') channels.add('activity');
    if ((finderActive && fileExplorerMode !== 'tabber') || fileEditorActive) channels.add('files');
    if (activeItems.includes(infoItemId)) {
      channels.add('activity');
      channels.add('transcripts');
    }
    if (activeItems.includes(yoagentItemId)) {
      channels.add('activity');
      channels.add('transcripts');
      channels.add('yoagent');
    }
    if (activeItems.includes(chatItemId) || notificationChat) channels.add('chat');
    if (activeItems.some(item => isTmuxSession(item) && typeof transcriptPreviewPaneIsActive === 'function' && transcriptPreviewPaneIsActive(item))) {
      channels.add('transcripts');
    }
  } else if (notificationAttention) {
    channels.add('attention');
    channels.add('chat');
  }
  return {
    visibility: visible ? 'visible' : 'hidden',
    active_panes: activeItems.slice().sort(),
    active_subtabs: {
      finder: finderActiveMode(),
      yoagent: activeItems.includes(yoagentItemId),
      chat: activeItems.includes(chatItemId),
    },
    channels: Array.from(channels).sort(),
    notification_attention: notificationAttention,
  };
}

function finderActiveMode() {
  return itemIsActivePaneTab(fileExplorerItemId) ? normalizeFileExplorerMode(fileExplorerMode) : '';
}

function clientEventDemandSignature(descriptor) {
  return JSON.stringify(descriptor);
}

function closeClientEventStream() {
  const source = clientEventTransportState.source;
  clientEventTransportState.source = null;
  clientEventTransportState.connected = false;
  source?.close?.();
}

function openClientEventStream(descriptor) {
  if (typeof EventSource === 'undefined' || !descriptor.channels.length) return null;
  const params = new URLSearchParams({
    channels: descriptor.channels.join(','),
    client_id: String(shareClientId || ''),
  });
  let source;
  try {
    source = new EventSource(`/api/client-events?${params.toString()}`);
  } catch (_error) {
    return null;
  }
  clientEventTransportState.source = source;
  const channels = new Set(descriptor.channels);
  source.addEventListener('ready', event => {
    if (clientEventTransportState.source !== source) return;
    clientEventTransportState.connected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    recordSseDebugEvent('ready', clientEventEnvelope(event), event);
    if (channels.has('files') && typeof syncServerWatchRoots === 'function') syncServerWatchRoots({immediate: true});
    if (channels.has('status') || channels.has('attention')) refreshAutoStatuses({force: true}).catch(error => console.warn('client-events ready auto-status refresh failed', error));
    if (channels.has('core')) refreshBackgroundOwnerStatus({preferFresh: true}).catch(error => console.warn('client-events ready background-owner refresh failed', error));
    if (channels.has('chat') && typeof loadChatBootstrap === 'function') loadChatBootstrap({incoming: true});
  });
  source.addEventListener('ping', event => {
    if (clientEventTransportState.source !== source) return;
    clientEventTransportState.connected = true;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
    recordSseDebugEvent('ping', clientEventEnvelope(event), event);
  });
  source.onerror = () => {
    if (clientEventTransportState.source !== source) return;
    clientEventTransportState.connected = false;
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
  };
  for (const type of ['settings_changed', 'attention_acks_changed', 'auto_approve_changed', 'background_owner_changed', 'background_refresh_done', 'tmux_signals_changed', 'watched_prs_changed', 'files_changed', 'fs_changed', 'session_files_ready', 'transcripts_changed', 'context_items_ready', 'activity_summary_ready', 'update_available', 'yoagent_conversation_changed', 'yoagent_jobs_changed', 'yoagent_skills_changed', 'yoagent_stream_delta', 'chat_messages_changed', 'chat_typing_changed']) {
    source.addEventListener(type, event => {
      if (clientEventTransportState.source !== source) return;
      clientEventTransportState.connected = true;
      if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(true);
      const envelope = clientEventEnvelope(event);
      recordSseDebugEvent(type, envelope, event);
      handleClientPushEvent(type, clientEventPayloadFromEnvelope(envelope));
    });
  }
  return source;
}

function applyClientEventDemand() {
  clientEventTransportState.demandTimer = null;
  if (!clientEventTransportState.enabled) return false;
  const descriptor = clientEventDemandDescriptor();
  const signature = clientEventDemandSignature(descriptor);
  if (signature === clientEventTransportState.demandSignature && clientEventTransportState.source) return false;
  clientEventTransportState.demand = descriptor;
  clientEventTransportState.demandSignature = signature;
  closeClientEventStream();
  if (!descriptor.channels.length) {
    if (typeof recordJsDebugClientEventsConnectionState === 'function') recordJsDebugClientEventsConnectionState(false);
    return true;
  }
  openClientEventStream(descriptor);
  return true;
}

function syncClientEventDemand(options = {}) {
  if (!clientEventTransportState.enabled) return false;
  if (clientEventTransportState.demandTimer) clearTimeout(clientEventTransportState.demandTimer);
  if (options.immediate === true) return applyClientEventDemand();
  clientEventTransportState.demandTimer = setTimeout(applyClientEventDemand, clientEventDemandDebounceMs);
  return true;
}

function installClientEventStream() {
  clientEventTransportState.enabled = true;
  return syncClientEventDemand({immediate: true});
}

// Dev-velocity #1b: in --dev mode, reload the page when the static bundle changes (ends the recurring
// "is the bundle stale?" misdiagnoses). Listens to the server's /api/dev-reload SSE 'reload' event;
// no-op outside dev mode. The EventSource auto-reconnects across the backend re-exec (#1c).
function installDevAutoReload() {
  if (!devMode || typeof EventSource === 'undefined') return;
  let source;
  try {
    const revision = encodeURIComponent(String(bootstrap.devBundleRevision || ''));
    source = new EventSource(`/api/dev-reload?bundle_revision=${revision}`);
  } catch (_error) {
    return;
  }
  source.addEventListener('ready', event => {
    // A client reconnects after a server restart, which means it misses the old process's
    // `reload` event. The fresh server's revision makes that stale bundle observable at once.
    const serverRevision = String(safeJsonParse(event.data, {})?.signature || '');
    const bootRevision = String(bootstrap.devBundleRevision || '');
    if (serverRevision && bootRevision && serverRevision !== bootRevision) location.reload();
  });
  source.addEventListener('reload', () => {
    statusOk(localizedHtml('status.devBundleReloading'));
    location.reload();
  });
}

async function showContext(session) {
  const modal = document.getElementById('modal');
  const body = document.getElementById('modalBody');
  modal.classList.remove('about-open', 'share-open');
  body.innerHTML = '';
  modal.dataset.modalKind = 'context';
  modal.dataset.modalSession = session;
  body.dataset.localeTextKey = 'common.loading';
  modal.classList.add(CLS.open);
  relocalizeModalChrome();
  const payload = await apiFetchJson(`/api/context?session=${encodeURIComponent(session)}&messages=${transcriptPreviewMessages}`);
  delete body.dataset.localeTextKey;
  if (payload.text) {
    body.textContent = `${payload.path}\n\n${payload.text}`;
  } else {
    body.textContent = JSON.stringify(payload, null, 2);
  }
  scheduleSharePopupLayerPublish();
}

function relocalizeModalChrome(options = {}) {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  const close = document.getElementById('closeModal');
  const closeLabel = t('common.close');
  if (close) {
    close.title = closeLabel;
    close.setAttribute('aria-label', closeLabel);
  }
  if (!modal || options.content === false || !modal.classList.contains(CLS.open)) return Boolean(modal);
  if (modal.classList.contains('about-open')) {
    showAboutModal();
    return true;
  }
  if (modal.classList.contains('share-open')) return relocalizeShareModal();
  if (modal.dataset.modalKind !== 'context') return true;
  if (title) title.textContent = t('transcript.tailTitle', {session: sessionLabel(modal.dataset.modalSession || '')});
  if (body?.dataset.localeTextKey) body.textContent = t(body.dataset.localeTextKey);
  return true;
}

function globalShortcutTargetAllowsAppAction(target) {
  const nodes = [
    typeof Element !== 'undefined' && target instanceof Element ? target : null,
    document.activeElement,
  ].filter(Boolean);
  if (!nodes.length) return true;
  const blocked = ['.xterm', '.terminal-pane', '.cm-editor', 'input', 'textarea', 'select', '[contenteditable="true"]'];
  return !nodes.some(node => blocked.some(selector => node.closest?.(selector)));
}

function globalShortcutTargetAllowsPlatformAction(target) {
  return isMacPlatform() || globalShortcutTargetAllowsAppAction(target);
}

function globalShortcutTargetIsTerminalSurface(target) {
  const node = typeof Element !== 'undefined' && target instanceof Element ? target : document.activeElement;
  return Boolean(node?.closest?.('.xterm') || node?.closest?.('.terminal-pane'));
}

function globalShortcutTargetAllowsFinderShortcut(target) {
  if (globalShortcutTargetAllowsAppAction(target)) return true;
  return isMacPlatform() && globalShortcutTargetIsTerminalSurface(target);
}

function globalShortcutShouldToggleFinder(event, key = String(event?.key || '').toLowerCase(), mod = appModifier(event)) {
  return Boolean(mod && key === 'b' && globalShortcutTargetAllowsFinderShortcut(event?.target));
}

function clearPendingGlobalShortcutChord() {
  pendingGlobalShortcutChord = null;
  if (pendingGlobalShortcutChordTimer) {
    clearTimeout(pendingGlobalShortcutChordTimer);
    pendingGlobalShortcutChordTimer = null;
  }
}

function startPinTabShortcutChord() {
  clearPendingGlobalShortcutChord();
  pendingGlobalShortcutChord = 'pin-tab';
  pendingGlobalShortcutChordTimer = setTimeout(clearPendingGlobalShortcutChord, globalShortcutChordTimeoutMs);
  statusEl.textContent = t('shortcuts.pinTabPrompt', {keys: `${appShortcutText('K', {shift: true})} Enter`});
}

function handlePendingGlobalShortcutChord(event, key) {
  if (!pendingGlobalShortcutChord) return false;
  if (pendingGlobalShortcutChord === 'pin-tab' && key === 'enter') {
    event.preventDefault();
    event.stopPropagation();
    clearPendingGlobalShortcutChord();
    toggleActiveTabPinned();
    return true;
  }
  if (event.key === 'Escape') {
    clearPendingGlobalShortcutChord();
    return false;
  }
  clearPendingGlobalShortcutChord();
  return false;
}

function itemCanCloseWithAppShortcut(item) {
  return isFileEditorItem(item) || isImageViewerItem(item);
}

function toggleFileExplorerShortcut() {
  if (itemInLayout(fileExplorerItemId)) {
    fileExplorerShortcutRestoreSlots = cloneLayoutSlots();
    rememberFileExplorerOpenIntent(false);
    applyLayoutSlots(layoutWithoutItem(fileExplorerItemId, {
      preservePlaceholders: false,
    }), {
      preserveMissingFileExplorer: true,
      message: fileExplorerHiddenStatusMessage(),
    });
    return;
  }
  if (fileExplorerShortcutRestoreSlots && itemInLayout(fileExplorerItemId, fileExplorerShortcutRestoreSlots)) {
    rememberFileExplorerOpenIntent(true);
    applyLayoutSlots(fileExplorerShortcutRestoreSlots, {
      prune: false,
      message: t('layout.status.restored', {item: fileExplorerLabel()}),
    });
    fileExplorerShortcutRestoreSlots = null;
    return;
  }
  rememberFileExplorerOpenIntent(true);
  selectSession(fileExplorerItemId);
}

function handleFocusedTerminalCopyShortcut(event) {
  if (!globalShortcutTargetIsTerminalSurface(event.target) && !globalShortcutTargetAllowsAppAction(event.target)) return false;
  const session = focusedTerminal;
  if (!session) return false;
  const item = terminals.get(session);
  if (!item?.term) return false;
  if (!handleTerminalTmuxWindowShortcutKeydown(session, event) && !handleTerminalCopyShortcutKeydown(session, item.term, item.container, event)) return false;
  event.stopImmediatePropagation?.();
  event.stopPropagation?.();
  return true;
}

if (refreshMeta) {
  refreshMetaButtonChrome();
  refreshMeta.onclick = refreshAll;
}
if (tabMetaToggle) {
  tabMetaToggle.onclick = toggleTabMetadata;
  // Restore the `#` tab-metadata toggle to the top-right cluster, just left of Notify.
  notifyToggle?.parentElement?.insertBefore(tabMetaToggle, notifyToggle);
}
if (logoutButton) logoutButton.onclick = () => { window.location.href = '/logout'; };
document.getElementById('closeModal').onclick = () => {
  const modal = document.getElementById('modal');
  modal.classList.remove(CLS.open, 'about-open', 'share-open');
  scheduleSharePopupLayerPublish({immediate: true});
};
function promptAttentionClearElement(target) {
  return target?.closest?.('[data-prompt-attention-clear]');
}

function handlePromptAttentionClearEvent(event) {
  const node = promptAttentionClearElement(event.target);
  if (!node) return false;
  event.preventDefault();
  event.stopPropagation();
  clearPromptAttentionForSession(node.dataset.session || '', {delayMs: agentWindowActivityAcknowledgeDelayMs});
  return true;
}

document.addEventListener('click', handlePromptAttentionClearEvent);
document.addEventListener('keydown', event => {
  if (!['Enter', ' '].includes(event.key)) return;
  handlePromptAttentionClearEvent(event);
});
document.addEventListener('pointerdown', event => {
  if (event.target?.closest?.('.app-menu')) return;
  closeAppMenus();
}, true);
topbar?.addEventListener('pointerenter', () => {
  closeOtherSessionPopovers(null, {force: true});
  closeFileImagePreview();
});

function focusedPanelSearchTarget(event, item) {
  const direct = event.target?.closest?.('[data-layout-item]');
  if (direct?.dataset?.layoutItem === item && direct.offsetParent !== null) return direct;
  const registered = panelNodes.get(item);
  if (registered?.offsetParent !== null) return registered;
  return Array.from(document.querySelectorAll('[data-layout-item]'))
    .find(panel => panel.dataset.layoutItem === item && panel.offsetParent !== null) || null;
}

function handleFocusedPanelSearchShortcut(event, {mod = appModifier(event), key = String(event.key || '').toLowerCase()} = {}) {
  if (!mod || event.shiftKey || key !== 'f') return false;
  const item = focusedPanelItem;
  const focusSearch = tabTypeForItem(item)?.focusSearch;
  if (typeof focusSearch !== 'function') return false;
  const panel = focusedPanelSearchTarget(event, item);
  if (!panel) return false;
  // The tab-type registry owns which panels have an app find control. This single dispatcher keeps
  // Cmd/Ctrl-F aligned across those panels while leaving native Find intact elsewhere.
  event.preventDefault();
  event.stopPropagation();
  Promise.resolve(focusSearch(item, panel)).catch(error => console.warn('panel search shortcut failed', error));
  return true;
}

function handleGlobalShortcutKeydown(event) {
  if (handleFocusedTerminalCopyShortcut(event)) return;
  // C10: the Finder tree claims Command-Delete (Mac) / Delete (PC) to delete the selected file(s) before
  // the global Mod+Delete tab-close fallback can fire.
  if (handleFileExplorerDeleteShortcut(event)) return;
  // File Explorer / Finder-style keyboard traversal of the Finder/Differ selection (Arrow + Shift+Arrow,
  // Home/End, Mod+A) — claimed before the global shortcuts so arrows move the file selection when the
  // Finder/Differ is the active surface.
  if (handleFileExplorerArrowNav(event)) return;
  const mod = appModifier(event);
  const key = String(event.key || '').toLowerCase();
  if (handleFocusedPanelSearchShortcut(event, {mod, key})) return;
  const platformActionAllowed = globalShortcutTargetAllowsPlatformAction(event.target);
  if (handlePendingGlobalShortcutChord(event, key)) return;
  const paneTabShortcutDirection = terminalTmuxWindowShortcutDirection(event);
  if (paneTabShortcutDirection && globalShortcutTargetAllowsAppAction(event.target)) {
    event.preventDefault();
    event.stopPropagation();
    selectAdjacentPaneTab(paneTabShortcutDirection, {userInitiated: true});
    return;
  }
  // editor back/forward history via the keyboard — Mod+Alt+[ / Mod+Alt+]. (appModifier() is
  // false when Alt is held, so test the platform modifier directly.) Matched by event.code so a layout
  // where Alt remaps the bracket char still works; plain Mod+[ / Mod+] stay with CodeMirror (indent).
  const platformMod = isMacPlatform() ? (event.metaKey === true && event.ctrlKey !== true) : (event.ctrlKey === true && event.metaKey !== true);
  if (platformMod && event.altKey && (event.code === 'BracketLeft' || event.code === 'BracketRight')) {
    event.preventDefault();
    event.stopPropagation();
    if (event.code === 'BracketLeft') editorNavBack();
    else editorNavForward();
    return;
  }
  if (platformMod && event.altKey && event.code === 'KeyB') {
    event.preventDefault();
    event.stopPropagation();
    openYoagentRightPane();
    return;
  }
  if (mod && key === 'w') {
    event.preventDefault();
    event.stopPropagation();
    const item = currentActiveMenuItem();
    if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);
    return;
  }
  if (mod && key === 'p' && platformActionAllowed) {
    event.preventDefault();
    if (event.shiftKey) openCommandPalette();
    else openFileQuickOpen();
    return;
  }
  if (mod && platformActionAllowed) {
    if (key === 'k') {
      event.preventDefault();
      event.stopPropagation();
      if (event.shiftKey) startPinTabShortcutChord();
      else showShareModal();
      return;
    }
    if ((key === 'backspace' || key === 'delete') && globalShortcutTargetAllowsAppAction(event.target)) {
      event.preventDefault();
      const item = currentActiveMenuItem();
      if (itemCanCloseWithAppShortcut(item)) removeSessionFromLayout(item);
      return;
    }
    if (globalShortcutShouldToggleFinder(event, key, mod)) {
      event.preventDefault();
      toggleFileExplorerShortcut();
      return;
    }
    if (event.key === ',') {
      event.preventDefault();
      selectSession(prefsItemId);
      return;
    }
  }
  if (!mod && globalShortcutTargetAllowsAppAction(event.target) && (event.key === '?' || (event.key === '/' && event.shiftKey))) {
    event.preventDefault();
    openKeyboardShortcutsOverlay();
    return;
  }
  if (event.key === 'Escape') {
    closeKeyboardShortcutsOverlay();
    closeAppMenus();
  }
}
installShareReadonlyInteractionBlocker();
installTerminalResizeAuthorityHandlers();
window.addEventListener('keydown', handleGlobalShortcutKeydown, true);
window.addEventListener(APP_VIEWPORT_CHANGE_EVENT, () => {
  // Safari can publish the new viewport before its topbar flex geometry has settled. The shared
  // fit check runs on the next frame (and the ResizeObserver covers a later width update), so the
  // full/compact menu decision is based only on current space, never the previous presentation.
  scheduleTopbarNavigationFitCheck();
  scheduleResponsiveLayoutPrune();
  scheduleAllTabStripOverflowChecks();
  if (typeof dockviewScheduleLayoutToHost === 'function') dockviewScheduleLayoutToHost();
  applyShareMirrorTransform();
  scheduleShareViewportPublish();
  for (const session of activeSessions.filter(isTmuxSession)) scheduleFit(session);
});

boot();
