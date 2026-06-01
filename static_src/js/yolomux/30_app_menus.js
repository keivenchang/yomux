function menuCommand(label, action, options = {}) {
  const command = {type: 'command', label, action, ...options};
  if (command.keepOpen === undefined && Object.prototype.hasOwnProperty.call(options, 'checked')) {
    command.keepOpen = true;
  }
  return command;
}

function menuSubmenu(label, items, options = {}) {
  return {type: 'submenu', label, items, ...options};
}

function menuSeparator() {
  return {type: 'separator'};
}

function menuGroups(...groups) {
  const items = [];
  for (const group of groups) {
    const commands = (Array.isArray(group) ? group : [group]).filter(Boolean);
    if (!commands.length) continue;
    if (items.length) items.push(menuSeparator());
    items.push(...commands);
  }
  return items;
}

function currentActiveMenuItem() {
  if (focusedPanelItem && itemIsActivePaneTab(focusedPanelItem)) return focusedPanelItem;
  if (focusedTerminal && itemIsActivePaneTab(focusedTerminal)) return focusedTerminal;
  return activePaneItems()[0] || null;
}

function currentSessionActionTarget() {
  const current = currentActiveMenuItem();
  if (isTmuxSession(current)) return current;
  if (isTmuxSession(lastFocusedTmuxSession) && activeSessions.includes(lastFocusedTmuxSession)) return lastFocusedTmuxSession;
  const activeTmuxSessions = activeSessions.filter(isTmuxSession);
  return activeTmuxSessions.length === 1 ? activeTmuxSessions[0] : null;
}

function orderedPaneItems(items = activePaneItems()) {
  const unique = [];
  for (const group of [
    items.filter(isTmuxSession),
    items.filter(isFileEditorItem),
    items.filter(item => !isTmuxSession(item) && !isFileEditorItem(item)),
  ]) {
    for (const item of group) {
      if (!unique.includes(item)) unique.push(item);
    }
  }
  return unique;
}

function menuTabDetail(item) {
  const type = tabTypeForItem(item);
  if (type?.detail) return type.detail(item);
  return tabMenuDetailText(item, transcriptMeta.sessions?.[item]);
}

function changesTabDetail() {
  const count = sessionFilesPayload.files?.length || 0;
  const session = sessionFilesPayload.session || sessionFilesTargetSession();
  if (sessionFilesLoading) return 'loading AI-changed files';
  if (count) return `${count} changed file${count === 1 ? '' : 's'}${session ? ` for ${sessionLabel(session)}` : ''}`;
  return session ? `no AI-changed files loaded for ${sessionLabel(session)}` : 'AI-changed files';
}

function menuTabRowHtml(item, options = {}) {
  const type = tabTypeForItem(item);
  if (type?.rowHtml) return type.rowHtml(item, options);
  const info = transcriptMeta.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true;
  const state = sessionState(item, info);
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(item, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="pane-tab-core">${yoloMarkerHtml(item, auto, {enabledOnly: false, toggle: options.toggleYolo === true && !readOnlyMode, yoloWorking: sessionYoloIsWorking(item)})}<span class="session-button-prefix">${sessionNumberNameHtml(item)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(item, info)}${pullRequestCompactBadgesHtml(item, pr)}${detailHtml}</span></span>`;
}

function menuTabCommand(item, options = {}) {
  const slot = slotForSession(item);
  const visible = itemIsActivePaneTab(item);
  const active = item === currentActiveMenuItem();
  const detail = options.detail || (visible ? menuTabDetail(item) : (itemIsBackgroundPaneTab(item) ? 'Minimized: in a pane, not shown' : 'Inactive: not in a pane'));
  return menuCommand(itemLabel(item), () => {
    if (slot && visible && !options.openAsPane) return activatePaneTab(slot, item);
    return selectSession(item);
  }, {
    checked: options.checked ?? active,
    detail: '',
    ariaLabel: [itemLabel(item), detail].filter(Boolean).join(' - '),
    html: stripTitleAttrs(menuTabRowHtml(item, {...options, menu: true})),
    className: 'app-menu-tab-command',
    targetItem: item,
  });
}

function tmuxSessionActionCommands(session, options = {}) {
  const hasSession = isTmuxSession(session);
  const autoPayload = hasSession ? autoApproveStates.get(session) : null;
  const autoHere = hasSession ? autoApproveEnabledHere(autoPayload) : false;
  const includeYolo = options.includeYolo !== false;
  const readonlyDetail = 'Admin only';
  const focusDetail = hasSession ? menuTabDetail(session) : 'Focus a tmux session first';
  const visibleDetail = readOnlyMode ? readonlyDetail : (hasSession ? '' : 'No tmux tab focused');
  const yoloLabel = `${autoHere ? 'Disable' : 'Enable'} YOLO for Tmux Session${hasSession ? ` '${session}'` : ''}`;
  const renameLabel = hasSession ? `Rename tmux session '${session}'` : 'Rename tmux session';
  const renameAction = options.renameAction || (() => renameTmuxSession(session));
  const commands = [];
  if (includeYolo) commands.push(menuCommand(yoloLabel, async () => {
      if (!hasSession) return;
      await toggleAutoApprove(session);
      renderSessionButtons();
      renderPaneTabStrips();
    }, {
      checked: autoHere,
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      iconHtml: hasSession ? yoloMarkerHtml(session, autoHere, {enabledOnly: false, yoloWorking: sessionYoloIsWorking(session)}) : '',
      ariaLabel: [yoloLabel, hasSession ? focusDetail : 'Focus a tmux session first'].filter(Boolean).join(' - '),
    }));
  commands.push(
    menuCommand(renameLabel, renameAction, {
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: [renameLabel, focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Kill session', () => killTmuxSession(session), {
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: ['Kill session', focusDetail].filter(Boolean).join(' - '),
    }),
  );
  return commands;
}

function yoloRulePath() {
  return yoloRulesPayload.path
    || nestedSetting(clientSettings, 'yolo.rule_file_path', nestedSetting(clientSettingsDefaults, 'yolo.rule_file_path', '~/.config/yolomux/yolo-rules.yaml'));
}

function settingsConfigPath() {
  return clientSettingsPayload.path || clientSettingsPayload.display_path || '~/.config/yolomux/settings.yaml';
}

function cycleGlobalThemeSetting() {
  const next = nextGlobalThemeMode();
  saveSettingsPatch(settingPatch('appearance.theme', next))
    .then(() => {
      statusEl.textContent = `theme: ${globalThemeLabel(next)}`;
    })
    .catch(error => {
      statusEl.innerHTML = `<span class="err">theme save failed: ${esc(error)}</span>`;
      refreshSettings({force: true});
    });
}

function yoloRuleStatusDetail() {
  const source = yoloRulesPayload.source || 'unknown';
  const count = Number(yoloRulesPayload.rule_count || 0);
  const countText = `${count} rule${count === 1 ? '' : 's'}`;
  const dryRun = yoloRulesPayload.dry_run ? ', dry run' : '';
  return yoloRulesPayload.error
    ? `error: ${yoloRulesPayload.error}`
    : `${source}, ${countText}${dryRun}`;
}

async function openYoloRuleFile() {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot create YOLO rule files</span>';
    return;
  }
  try {
    const response = await apiFetch('/api/yolo-rules/open', {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    await openFileInEditor(payload.path || yoloRulePath(), {name: basenameOf(payload.path || yoloRulePath())});
    statusEl.textContent = 'opened YOLO rule file';
  } catch (error) {
    statusEl.innerHTML = `<span class="err">open YOLO rule file failed: ${esc(error)}</span>`;
  }
}

async function reloadYoloRules() {
  try {
    const response = await apiFetch('/api/yolo-rules/reload', {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    const level = payload.error ? 'error' : '';
    statusEl.innerHTML = payload.error
      ? `<span class="err">YOLO rule reload failed: ${esc(payload.error)}</span>`
      : `<span class="ok">reloaded YOLO rules</span>`;
    showToast('YOLO rules', payload.error || yoloRuleStatusDetail(), {level});
  } catch (error) {
    statusEl.innerHTML = `<span class="err">reload YOLO rules failed: ${esc(error)}</span>`;
  }
}

async function refreshYoloRulesStatus(options = {}) {
  try {
    const response = await apiFetch('/api/yolo-rules');
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    return payload;
  } catch (error) {
    if (!options.silent) statusEl.innerHTML = `<span class="err">YOLO rule status failed: ${esc(error)}</span>`;
    return null;
  }
}

function tmuxYoloMenuItems() {
  return [
    menuCommand('Open rule file', openYoloRuleFile, {
      disabled: readOnlyMode,
      detail: compactHomePath(yoloRulePath()),
    }),
    menuCommand('Reload rules', reloadYoloRules, {
      detail: yoloRuleStatusDetail(),
    }),
  ];
}

function tmuxCurrentYoloCommand(session) {
  const hasSession = isTmuxSession(session);
  const payload = hasSession ? autoApproveStates.get(session) : null;
  const enabled = hasSession ? autoApproveEnabledHere(payload) : false;
  const elsewhere = hasSession ? autoApproveEnabledElsewhere(payload) : false;
  const label = hasSession ? `YO ${enabled ? 'on' : elsewhere ? 'elsewhere' : 'off'}` : 'YO';
  return menuCommand(label, async () => {
    if (!hasSession) return;
    await toggleAutoApprove(session);
    renderSessionButtons({force: true});
    renderPaneTabStrips();
  }, {
    disabled: readOnlyMode || !hasSession,
    detail: hasSession ? `Tmux Session '${session}'` : 'Focus a tmux tab first',
    iconHtml: hasSession ? yoloMarkerHtml(session, enabled, {enabledOnly: false, yoloWorking: sessionYoloIsWorking(session)}) : '',
    keepOpen: true,
    ariaLabel: hasSession ? `${enabled ? 'Disable' : 'Enable'} YOLO for Tmux Session '${session}'` : 'Focus a tmux tab first',
  });
}

function tmuxSessionViewCommands(session) {
  const hasSession = isTmuxSession(session);
  const active = hasSession && activeSessions.includes(session);
  const focusDetail = hasSession ? menuTabDetail(session) : 'Focus a tmux session first';
  const disabledDetail = hasSession ? 'Open the tab in a pane first' : 'No tmux tab focused';
  return [
    menuCommand('Transcript', () => {
      if (active) activateTab(session, 'transcript');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      checked: active && panelActiveTabName(session) === 'transcript',
      ariaLabel: ['Transcript', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('AI summary', () => {
      if (active) activateTab(session, 'summary');
    }, {
      disabled: readOnlyMode || !active,
      detail: readOnlyMode ? 'Admin only' : (active ? '' : disabledDetail),
      checked: active && panelActiveTabName(session) === 'summary',
      ariaLabel: ['AI summary', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Event log', () => {
      if (active) activateTab(session, 'events');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      checked: active && panelActiveTabName(session) === 'events',
      ariaLabel: ['Event log', focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand('Pane details', () => {
      if (!active) return;
      const panel = document.getElementById(`panel-${session}`);
      if (panel) setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      ariaLabel: ['Pane details', focusDetail].filter(Boolean).join(' - '),
    }),
  ];
}

function newTmuxSessionItems() {
  return ['claude', 'codex', 'term'].map(agent => {
    const available = availableAgents.has(agent);
    const capped = visibleSessions.length >= maxSessionTabs;
    return menuCommand(`+ ${agentName(agent)}`, () => createNextSession(agent), {
      iconHtml: agentIcon(agent),
      disabled: readOnlyMode || !available || capped,
      detail: readOnlyMode
        ? 'Admin only'
        : (!available ? `${agentName(agent)} unavailable` : (capped ? 'Limit reached' : '')),
    });
  });
}

function tabCommandsForItems(items, options) {
  return items.map(item => menuTabCommand(item, options));
}

function tabSearchFields(item) {
  const info = transcriptMeta.sessions?.[item] || {};
  const filePath = fileItemPath(item) || '';
  const pr = displayPullRequest(info);
  return [
    item,
    itemLabel(item),
    sessionLabel(item),
    menuTabDetail(item),
    filePath,
    compactHomePath(filePath),
    info.cwd,
    info.branch,
    info.status,
    info.description,
    info.goal,
    pr?.title,
    pr?.url,
    pr?.number ? 'PR' : '',
    pr?.number ? `PR ${pr.number}` : '',
    pr?.number ? `PR#${pr.number}` : '',
    pr?.number ? `#${pr.number}` : '',
    pr?.number ? String(pr.number) : '',
    ...(Array.isArray(info.linear) ? info.linear : []),
  ].filter(Boolean);
}

function tabSearchScore(item, query = tabsMenuSearchText) {
  if (!String(query || '').trim()) return 0;
  return fuzzySearchScore(query, tabSearchFields(item));
}

function filterTabItemsForSearch(items, query = tabsMenuSearchText) {
  if (!String(query || '').trim()) return items;
  return items
    .map((item, index) => ({item, index, score: tabSearchScore(item, query)}))
    .filter(entry => Number.isFinite(entry.score))
    .sort((left, right) => right.score - left.score || left.index - right.index)
    .map(entry => entry.item);
}

function backgroundTabMenuItems() {
  return tabCommandsForItems(filterTabItemsForSearch(backgroundTabItems()), {
    checked: false,
    detail: 'Minimized',
    openAsPane: true,
    toggleYolo: true,
  });
}

function inactiveTabMenuItems() {
  return tabCommandsForItems(filterTabItemsForSearch(inactiveTabItems()), {
    checked: false,
    detail: 'Not in a pane',
    openAsPane: true,
    toggleYolo: true,
  });
}

function tabMenuItems(openItems = orderedPaneItems(activePaneItems())) {
  const query = tabsMenuSearchText.trim();
  const filteredOpenItems = filterTabItemsForSearch(openItems, query);
  const groupedItems = menuGroups(
    filteredOpenItems.map(item => menuTabCommand(item, {toggleYolo: true})),
    backgroundTabMenuItems(),
    inactiveTabMenuItems()
  );
  const resultItems = groupedItems.length ? groupedItems : [menuCommand('No matching tabs', null, {disabled: true})];
  return resultItems;
}

function fileMenuVirtualCommand(item, detail) {
  return menuCommand(itemLabel(item), () => selectSession(item), {
    checked: itemInLayout(item),
    detail,
    iconHtml: tabTypeIconHtml(item, {menu: true}),
    targetItem: item,
  });
}

function appMenuTree() {
  const activeTmux = currentSessionActionTarget();
  const openItems = orderedPaneItems(activePaneItems());
  const yoloCount = yoloEnabledSessions().length;
  return [
    {
      id: 'file',
      label: 'File',
      items: menuGroups(
        [
          menuCommand(fileExplorerLabel(), () => selectSession(fileExplorerItemId), {
            checked: itemInLayout(fileExplorerItemId),
            detail: 'Browse files',
            iconHtml: tabTypeIconHtml(fileExplorerItemId, {menu: true}),
            targetItem: fileExplorerItemId,
          }),
          fileMenuVirtualCommand(infoItemId, 'Open branch, PR, CI, and repo metadata'),
          fileMenuVirtualCommand(yoagentItemId, 'Open the AI agent activity summary'),
          menuCommand('Open file', openFileQuickOpen, {
            detail: appShortcutText('P'),
            iconHtml: appMenuUiIcon('document'),
          }),
          menuCommand('Preferences', () => selectSession(prefsItemId), {
            checked: itemInLayout(prefsItemId),
            detail: compactHomePath(settingsConfigPath()),
            iconHtml: tabTypeIconHtml(prefsItemId, {menu: true}),
            targetItem: prefsItemId,
          }),
        ],
        [
          menuCommand('Log out', logOut, {
            detail: 'End this browser session',
            iconHtml: appMenuUiIcon('logout'),
          }),
        ]
      ),
    },
    {
      id: 'view',
      label: 'View',
      items: [
        menuCommand(tabMetaVisible ? 'Hide tab metadata' : 'Show tab metadata', toggleTabMetadata, {
          checked: tabMetaVisible,
          detail: 'Branch, state, PR, cwd',
          iconHtml: appMenuUiIcon('tab-meta', tabMetaVisible),
        }),
        menuCommand('Alert', toggleNotifications, {
          checked: notificationsEnabled,
          disabled: readOnlyMode,
          detail: readOnlyMode ? 'Requires admin access' : '',
          iconHtml: appMenuUiIcon('notify', notificationsEnabled),
        }),
        menuCommand('Refresh', refreshAll, {
          iconHtml: appMenuUiIcon('refresh'),
        }),
        menuCommand(`Theme: ${globalThemeLabel()}`, cycleGlobalThemeSetting, {
          detail: `Switch to ${globalThemeLabel(nextGlobalThemeMode())}`,
        }),
        menuSubmenu('Layout', [
          menuCommand('Single pane', setLayoutToSinglePane, {detail: 'Consolidate visible non-Finder tabs'}),
          menuCommand('Split', setLayoutToSplitPanes, {detail: 'Split visible non-Finder tabs left/right'}),
          menuCommand('Grid', null, {disabled: true, detail: 'Drag panes for now'}),
          menuCommand('Wall', null, {disabled: true}),
        ]),
      ],
    },
    {
      id: 'tmux',
      label: 'tmux',
      items: menuGroups(
        [tmuxCurrentYoloCommand(activeTmux)],
        newTmuxSessionItems(),
        tmuxSessionViewCommands(activeTmux),
        [
          ...tmuxSessionActionCommands(activeTmux, {includeYolo: false}),
          menuCommand('Resume session', null, {
            disabled: true,
            detail: 'Coming soon',
          }),
        ],
        [
          menuSubmenu('YOLO', tmuxYoloMenuItems()),
        ]
      ),
    },
    {
      id: 'tabs',
      label: 'Tabs',
      badgeText: yoloCount ? String(yoloCount) : '',
      badgeTitle: yoloCount ? `${yoloCount} tmux session${yoloCount === 1 ? '' : 's'} with YOLO enabled` : '',
      items: tabMenuItems(openItems),
    },
    {
      id: 'help',
      label: 'Help',
      items: menuGroups(
        [menuCommand('Command palette', openCommandPalette, {
          detail: appShortcutText('P', {shift: true}),
        })],
        [
          menuCommand(`YOLOmux ${bootstrap.version || ''}`.trim(), null, {
            disabled: true,
            detail: bootstrap.versionCommitTime ? `Last commit: ${bootstrap.versionCommitTime}` : '',
          }),
          menuCommand('Keyboard shortcuts', openKeyboardShortcutsOverlay, {
            detail: '?',
          }),
          menuCommand('Open README', openProjectReadme, {
            disabled: !projectReadmePath(),
            detail: 'Local README',
          }),
        ]
      ),
    },
  ];
}

function appMenuIsOpen() {
  return Boolean(sessionButtons?.querySelector('.app-menu.open'));
}

function renderSessionButtons(options = {}) {
  if (!sessionButtons) return;
  if (!options.force && appMenuIsOpen()) {
    scheduleTopbarMetricsUpdate();
    return;
  }
  const openMenu = sessionButtons.querySelector('.app-menu.open');
  if (openMenu) {
    openAppMenuId = openMenu.dataset.appMenu || null;
  } else {
    openAppMenuId = null;
    openAppMenuPinned = false;
    openAppMenuOpenedAt = 0;
  }
  sessionButtons.innerHTML = '';
  sessionButtons.ondragover = event => {
    const payload = dragPayload(event);
    if (!payload?.session || !itemInLayout(payload.session)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    clearDropPreview();
    sessionButtons.classList.add('drag-over');
  };
  sessionButtons.ondragleave = event => {
    if (!sessionButtons.contains(event.relatedTarget)) sessionButtons.classList.remove('drag-over');
  };
  sessionButtons.ondrop = event => {
    const payload = dragPayload(event);
    sessionButtons.classList.remove('drag-over');
    if (!payload?.session) return;
    event.preventDefault();
    event.stopPropagation();
    removeSessionFromLayout(payload.session);
  };
  sessionButtons.classList.remove('drag-over');
  sessionButtons.appendChild(createAppMenuBar());
  scheduleTopbarMetricsUpdate();
}

function createAppMenuBar() {
  const bar = document.createElement('nav');
  bar.className = 'app-menu-bar';
  bar.setAttribute('aria-label', 'Application menu');
  bar.setAttribute('role', 'menubar');
  for (const menu of appMenuTree()) bar.appendChild(createAppMenu(menu));
  return bar;
}

function appMenuAnchorInlineSize(popover) {
  const anchor = popover?.parentElement?.querySelector?.(':scope > .app-menu-button, :scope > .app-menu-command');
  return Math.ceil(anchor?.getBoundingClientRect?.().width || 0);
}

function measureAppMenuContentWidth(popover) {
  if (!popover?.cloneNode || !document.body) return null;
  const clone = popover.cloneNode(true);
  clone.style.position = 'fixed';
  clone.style.insetInlineStart = '0';
  clone.style.insetBlockStart = '0';
  clone.style.transform = 'translateX(-100%)';
  clone.style.visibility = 'hidden';
  clone.style.pointerEvents = 'none';
  clone.style.opacity = '0';
  clone.style.width = 'max-content';
  clone.style.minWidth = '0';
  clone.style.maxWidth = 'none';
  clone.style.maxHeight = 'none';
  clone.style.removeProperty('--app-menu-fit-width');
  clone.style.removeProperty('--app-menu-fit-offset');
  clone.querySelectorAll('.app-menu-command').forEach(command => {
    command.style.width = 'max-content';
    command.style.minWidth = '0';
    command.style.maxWidth = 'none';
  });
  clone.querySelectorAll('.app-menu-rich, .pane-tab-core, .session-button-text, .session-button-name, .session-button-dir, .session-button-detail, .tab-inline-detail, .pane-tab-info-label').forEach(node => {
    node.style.maxWidth = 'none';
    node.style.overflow = 'visible';
    node.style.textOverflow = 'clip';
    node.style.whiteSpace = 'nowrap';
  });
  document.body.appendChild(clone);
  const width = Math.ceil(clone.getBoundingClientRect().width || clone.scrollWidth || 0);
  clone.remove();
  return width || null;
}

function fitAppMenuPopover(popover) {
  if (!popover) return;
  popover.style.setProperty('--app-menu-fit-offset', '0px');
  popover.style.removeProperty('--app-menu-fit-width');
  const measured = measureAppMenuContentWidth(popover);
  const anchorWidth = appMenuAnchorInlineSize(popover);
  const desiredWidth = Math.max(anchorWidth, measured || 0);
  if (desiredWidth > 0) popover.style.setProperty('--app-menu-fit-width', `${desiredWidth}px`);

  const rect = popover.getBoundingClientRect();
  const viewportRight = viewportBounds(popoverEdgeGapPx()).right;
  if (!rect.width || !viewportRight) return;
  const overflow = Math.max(0, rect.right - viewportRight);
  if (!overflow) return;
  const maxShift = Math.max(0, rect.width - anchorWidth);
  popover.style.setProperty('--app-menu-fit-offset', `${-Math.min(overflow, maxShift)}px`);
}

function createAppMenu(menu) {
  const wrapper = document.createElement('div');
  wrapper.className = 'app-menu';
  wrapper.dataset.appMenu = menu.id;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'app-menu-button';
  button.setAttribute('aria-haspopup', 'true');
  button.setAttribute('aria-expanded', openAppMenuId === menu.id ? 'true' : 'false');
  button.setAttribute('role', 'menuitem');
  button.innerHTML = `${esc(menu.label)}${menu.badgeText ? `<span class="app-menu-button-badge" title="${esc(menu.badgeTitle || '')}">${esc(menu.badgeText)}</span>` : ''}`;
  const popover = document.createElement('div');
  popover.className = 'app-menu-popover';
  popover.setAttribute('role', 'menu');
  popover.setAttribute('aria-label', menu.label);
  for (const item of menu.items) popover.appendChild(createAppMenuItem(item));
  fitAppMenuPopover(popover);
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (wrapper.classList.contains('open')) {
      const openMs = Date.now() - openAppMenuOpenedAt;
      if (openAppMenuPinned && openMs >= menuClickCloseGraceMs) closeAppMenus();
      else openAppMenu(wrapper, {focusFirst: false, pinned: true});
      return;
    }
    openAppMenu(wrapper, {focusFirst: false, pinned: true});
  });
  button.addEventListener('keydown', event => handleAppMenuButtonKeydown(event, wrapper));
  wrapper.append(button, popover);
  if (openAppMenuId === menu.id) wrapper.classList.add('open');
  bindAppMenuHover(wrapper);
  return wrapper;
}

function createAppMenuItem(item) {
  if (item.type === 'separator') {
    const node = document.createElement('div');
    node.className = 'app-menu-separator';
    node.role = 'separator';
    return node;
  }
  if (item.type === 'section') {
    const node = document.createElement('div');
    node.className = 'app-menu-section';
    node.textContent = item.label;
    return node;
  }
  if (item.type === 'submenu') return createAppSubmenu(item);
  return createAppMenuCommand(item);
}

function createAppSubmenu(item) {
  const wrapper = document.createElement('div');
  wrapper.className = 'app-menu-submenu-wrap open';
  const button = createAppMenuCommand({
    label: item.label,
    disabled: item.disabled,
    detail: item.detail,
    className: 'app-menu-submenu-button',
  }, {asSubmenu: true});
  const submenu = document.createElement('div');
  submenu.className = 'app-submenu-popover';
  submenu.setAttribute('role', 'menu');
  submenu.setAttribute('aria-label', item.label);
  for (const child of item.items || []) submenu.appendChild(createAppMenuItem(child));
  fitAppMenuPopover(submenu);
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (button.disabled) return;
    wrapper.classList.add('open');
    button.setAttribute('aria-expanded', 'true');
  });
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      wrapper.classList.add('open');
      button.setAttribute('aria-expanded', 'true');
      focusFirstAppMenuCommand(submenu);
    }
  });
  button.setAttribute('aria-haspopup', 'true');
  button.setAttribute('aria-expanded', 'true');
  wrapper.append(button, submenu);
  return wrapper;
}

function createAppMenuCommand(item, options = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = ['app-menu-command', item.className || '', options.asSubmenu ? 'has-submenu' : ''].filter(Boolean).join(' ');
  button.setAttribute('role', item.checked !== undefined ? 'menuitemcheckbox' : 'menuitem');
  if (item.checked !== undefined) {
    button.dataset.checked = item.checked ? 'true' : 'false';
    button.setAttribute('aria-checked', item.checked ? 'true' : 'false');
  }
  if (item.disabled) button.disabled = true;
  const ariaLabel = item.ariaLabel || [item.label, item.detail].filter(Boolean).join(' - ');
  if (ariaLabel) button.setAttribute('aria-label', ariaLabel);
  const richHtml = item.html ? stripTitleAttrs(item.html) : '';
  const iconHtml = item.iconHtml ? stripTitleAttrs(item.iconHtml) : '';
  const contentHtml = richHtml
    ? `<span class="app-menu-rich">${richHtml}</span>`
    : `<span class="app-menu-line">${iconHtml ? `<span class="app-menu-icon">${iconHtml}</span>` : ''}<span class="app-menu-label">${esc(item.label)}</span></span>`;
  const detailHtml = item.detail ? `<span class="app-menu-detail">${esc(item.detail)}</span>` : '';
  button.innerHTML = `<span class="app-menu-check" aria-hidden="true"></span><span class="app-menu-content">${contentHtml}${detailHtml}</span>${options.asSubmenu ? '<span class="app-menu-submenu-arrow" aria-hidden="true">&gt;</span>' : ''}`;
  if (!options.asSubmenu) {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const autoTarget = event.target.closest('[data-auto-session]');
      if (autoTarget && button.contains(autoTarget)) {
        if (readOnlyMode) {
          statusEl.textContent = 'YOLO changes require admin access';
          return;
        }
        toggleAutoApprove(autoTarget.dataset.autoSession).then(() => {
          renderSessionButtons({force: true});
          renderPaneTabStrips();
        });
        return;
      }
      runAppMenuCommand(item);
    });
  }
  button.addEventListener('keydown', event => handleAppMenuCommandKeydown(event, button, item, options));
  return button;
}

function runAppMenuCommand(item) {
  if (item.disabled || typeof item.action !== 'function') return;
  const keepOpen = item.keepOpen === true;
  if (!keepOpen) closeAppMenus();
  try {
    Promise.resolve(item.action())
      .then(() => {
        if (keepOpen) renderSessionButtons({force: true});
      })
      .catch(error => {
        statusEl.innerHTML = `<span class="err">menu command failed: ${esc(error)}</span>`;
        if (keepOpen) renderSessionButtons({force: true});
      });
  } catch (error) {
    statusEl.innerHTML = `<span class="err">menu command failed: ${esc(error)}</span>`;
    if (keepOpen) renderSessionButtons({force: true});
  }
}

function appMenuCommands(container) {
  const scope = container.classList?.contains('app-menu')
    ? container.querySelector(':scope > .app-menu-popover')
    : container;
  if (!scope) return [];
  return Array.from(scope.querySelectorAll('.app-menu-command'))
    .filter(button => !button.disabled && button.closest('.app-menu-popover, .app-submenu-popover') === scope);
}

function focusFirstAppMenuCommand(container) {
  appMenuCommands(container)[0]?.focus();
}

function focusAdjacentAppMenuCommand(button, direction) {
  const popover = button.closest('.app-menu-popover, .app-submenu-popover');
  if (!popover) return;
  const commands = appMenuCommands(popover);
  const index = commands.indexOf(button);
  if (!commands.length || index < 0) return;
  const next = commands[(index + direction + commands.length) % commands.length];
  next.focus();
}

function focusAdjacentTopMenu(wrapper, direction) {
  const menus = Array.from(sessionButtons.querySelectorAll('.app-menu'));
  const index = menus.indexOf(wrapper);
  if (index < 0 || !menus.length) return;
  const next = menus[(index + direction + menus.length) % menus.length];
  openAppMenu(next, {focusFirst: false});
  next.querySelector('.app-menu-button')?.focus();
}

function handleAppMenuButtonKeydown(event, wrapper) {
  if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    openAppMenu(wrapper, {focusFirst: true, pinned: true});
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    focusAdjacentTopMenu(wrapper, 1);
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    focusAdjacentTopMenu(wrapper, -1);
  } else if (event.key === 'Escape') {
    closeAppMenus();
  }
}

function handleAppMenuCommandKeydown(event, button, item, options = {}) {
  if (event.key === 'ArrowDown') {
    event.preventDefault();
    focusAdjacentAppMenuCommand(button, 1);
  } else if (event.key === 'ArrowUp') {
    event.preventDefault();
    focusAdjacentAppMenuCommand(button, -1);
  } else if (event.key === 'Escape') {
    event.preventDefault();
    closeAppMenus();
    button.closest('.app-menu')?.querySelector('.app-menu-button')?.focus();
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    const submenu = button.closest('.app-submenu-popover');
    if (submenu) {
      submenu.closest('.app-menu-submenu-wrap')?.querySelector(':scope > .app-menu-command')?.focus();
      return;
    }
    const wrapper = button.closest('.app-menu');
    if (wrapper) focusAdjacentTopMenu(wrapper, -1);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    const submenu = button.closest('.app-menu-submenu-wrap')?.querySelector(':scope > .app-submenu-popover');
    if (submenu && options.asSubmenu) {
      focusFirstAppMenuCommand(submenu);
      return;
    }
    const wrapper = button.closest('.app-menu');
    if (wrapper) focusAdjacentTopMenu(wrapper, 1);
  } else if (!options.asSubmenu && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault();
    runAppMenuCommand(item);
  }
}

function bindAppMenuHover(wrapper) {
  createHoverPopover({
    anchor: wrapper,
    popover: () => wrapper.querySelector(':scope > .app-menu-popover'),
    stateClass: '',
    canOpen: () => autoFocusEnabled || appMenuIsOpen(),
    showDelay: () => (appMenuIsOpen() ? 0 : menuHoverOpenDelayMs),
    hideDelay: () => menuHoverCloseDelayMs,
    stillActive: () => wrapper.matches?.(':hover'),
    onOpen: () => {
      const menuId = wrapper.dataset.appMenu || '';
      const currentWrapper = document.querySelector(`.app-menu[data-app-menu="${cssEscape(menuId)}"]`) || wrapper;
      if (!currentWrapper.classList.contains('open')) openAppMenu(currentWrapper, {focusFirst: false, pinned: false});
    },
    onClose: () => {
      const menuId = wrapper.dataset.appMenu || '';
      if (!wrapper.matches?.(':hover') && openAppMenuId === menuId) closeAppMenus();
    },
  });
}

function openAppMenu(wrapper, options = {}) {
  if (!wrapper) return;
  closeContextMenus();
  closeOtherSessionPopovers(null, {force: true});
  closeFileImagePreview();
  closeAppMenus(wrapper);
  fitAppMenuPopover(wrapper.querySelector(':scope > .app-menu-popover'));
  wrapper.querySelectorAll(':scope > .app-menu-popover .app-submenu-popover').forEach(fitAppMenuPopover);
  wrapper.classList.add('open');
  wrapper.querySelectorAll('.app-menu-submenu-wrap').forEach(submenu => {
    submenu.classList.add('open');
    submenu.querySelector(':scope > .app-menu-command')?.setAttribute('aria-expanded', 'true');
  });
  openAppMenuId = wrapper.dataset.appMenu || null;
  openAppMenuPinned = options.pinned === true;
  openAppMenuOpenedAt = Date.now();
  wrapper.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'true');
  if (options.focusFirst) requestAnimationFrame(() => focusFirstAppMenuCommand(wrapper));
}

function closeAppMenus(keepOpen = null) {
  for (const menu of document.querySelectorAll('.app-menu.open')) {
    if (menu === keepOpen) continue;
    menu.classList.remove('open');
    menu.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'false');
  }
  for (const submenu of document.querySelectorAll('.app-menu-submenu-wrap.open')) {
    if (keepOpen?.contains(submenu)) continue;
    submenu.classList.remove('open');
    submenu.querySelector(':scope > .app-menu-command')?.setAttribute('aria-expanded', 'false');
  }
  openAppMenuId = keepOpen?.dataset?.appMenu || null;
  if (!openAppMenuId) {
    openAppMenuPinned = false;
    openAppMenuOpenedAt = 0;
  }
}
