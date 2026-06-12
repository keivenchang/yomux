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

function menuNumberSetting(path, label, options = {}) {
  return {type: 'number-setting', path, label, ...options};
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

function layoutMenuCommand(mode) {
  const normalized = normalizeLayoutMode(mode);
  const detail = normalized === 'single'
    ? t('menu.view.layout.single.detail', {name: fileExplorerLabel()})
    : normalized === 'split'
      ? t('menu.view.layout.split.detail', {name: fileExplorerLabel()})
      : '';
  return menuCommand(t(`menu.view.layout.${normalized}`), () => applyLayoutMode(normalized), {detail});
}

const aboutLinkedInUrl = 'https://www.linkedin.com/in/keiven/';
const aboutProjectUrl = 'https://github.com/keivenchang/yolomux';

function aboutDateTimeText() {
  if (bootstrap.versionCommitTime) return bootstrap.versionCommitTime;
  try {
    return new Date().toLocaleString([], {dateStyle: 'medium', timeStyle: 'medium'});
  } catch (_) {
    return new Date().toString();
  }
}

function openAboutLinkedIn() {
  window.open(aboutLinkedInUrl, '_blank', 'noopener,noreferrer');
}

function aboutCommitShaText() {
  return bootstrap.versionCommit || bootstrap.commitSha || bootstrap.commit_sha || bootstrap.gitSha || bootstrap.git_sha || '';
}

function topbarVersionTitle() {
  const sha = aboutCommitShaText();
  const lines = [];
  if (sha) lines.push(`SHA: ${sha}`);
  if (bootstrap.versionCommitTime) lines.push(`Last commit: ${bootstrap.versionCommitTime}`);
  return lines.join('\n') || 'SHA unknown';
}

function topbarServerStartedAtMs() {
  const explicitMs = Number(bootstrap.serverStartedAtMs);
  if (Number.isFinite(explicitMs) && explicitMs > 0) return explicitMs;
  const seconds = Number(bootstrap.serverStartedAt);
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 0;
}

function topbarDurationText(seconds) {
  const remaining = Math.max(0, Math.floor(Number(seconds) || 0));
  if (remaining < 1) return 'under 1 second';
  const units = [
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
  ];
  const parts = [];
  let rest = remaining;
  for (const [name, size] of units) {
    const value = Math.floor(rest / size);
    if (!value) continue;
    parts.push(`${value} ${name}${value === 1 ? '' : 's'}`);
    rest -= value * size;
    if (parts.length >= 2) break;
  }
  return parts.join(', ');
}

function topbarServerUptimeTitle() {
  const startedAtMs = topbarServerStartedAtMs();
  if (!startedAtMs) return 'Server uptime unknown';
  return `Server running for ${topbarDurationText((Date.now() - startedAtMs) / 1000)}`;
}

function updateBrandTitles() {
  for (const brand of document.querySelectorAll('.brand-title')) {
    brand.title = topbarServerUptimeTitle();
    brand.onpointerenter = updateBrandTitles;
  }
  for (const version of document.querySelectorAll('.brand-title .brand-version')) {
    version.title = topbarVersionTitle();
    version.onpointerenter = updateBrandTitles;
  }
}

function aboutBrandHtml() {
  return `<span class="about-brand-yo" style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}">${esc(t('brand.wordmark.yo'))}</span><span class="about-brand-lo">${esc(t('brand.wordmark.lo'))}</span><span class="about-brand-m">m</span><span class="about-brand-u">u</span><span class="about-brand-x">x</span>`;
}

function showAboutModal() {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  const close = document.getElementById('closeModal');
  if (!modal || !title || !body) return;
  title.textContent = t('menu.help.about');
  if (close) {
    close.title = t('common.close');
    close.setAttribute('aria-label', t('common.close'));
  }
  modal.classList.add('about-open');
  const version = bootstrap.version || '';
  const sha = aboutCommitShaText();
  body.innerHTML = `<div class="about-modal">
    <div class="about-brand-row">${aboutBrandHtml()}${version ? `<span class="about-version">${esc(version)}</span>` : ''}</div>
    <dl class="about-details">
      <div><dt>${esc(t('menu.help.about.datetime'))}</dt><dd>${esc(aboutDateTimeText())}</dd></div>
      <div><dt>SHA</dt><dd>${esc(sha || t('common.unknown'))}</dd></div>
      <div><dt>${esc(t('menu.help.about.version'))}</dt><dd>${esc(version || t('common.unknown'))}</dd></div>
    </dl>
    <div class="about-links"><a class="about-author" href="${esc(aboutLinkedInUrl)}" target="_blank" rel="noopener noreferrer">${esc(t('menu.help.about.author'))}</a><span> - </span><a class="about-author about-github" href="${esc(aboutProjectUrl)}" target="_blank" rel="noopener noreferrer">${esc(t('menu.help.about.github'))}</a><span> (to YOLOmux)</span></div>
  </div>`;
  modal.classList.add('open');
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
  const detail = tabMenuDetailText(item, transcriptMeta.sessions?.[item]);
  // A non-numeric tmux session name (e.g. "PA exploration") is shown in the tab label only as its
  // assigned number (e.g. "9"), so the real name is otherwise invisible. Surface it in the menu/search
  // detail so a result like label "9" reads "9 · PA exploration · main · …" and is identifiable.
  if (numericSessionName(item) === null) return detail ? `${item} · ${detail}` : String(item);
  return detail;
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
  const detail = options.detail || (visible ? menuTabDetail(item) : (itemIsBackgroundPaneTab(item) ? t('menu.tabs.minimizedDetail') : t('menu.tabs.inactiveDetail')));
  return menuCommand(itemLabel(item), () => {
    if (item === infoItemId) return openInfoSubTab('info');
    if (slot && visible && !options.openAsPane) return activatePaneTab(slot, item, {userInitiated: true});
    return selectSession(item, {userInitiated: true});
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
  const includeKill = options.includeKill !== false;
  const readonlyDetail = t('menu.common.adminOnly');
  const focusDetail = hasSession ? menuTabDetail(session) : t('menu.tmux.focusSessionFirst');
  const visibleDetail = readOnlyMode ? readonlyDetail : (hasSession ? '' : t('menu.tmux.noTabFocused'));
  const yoloLabel = hasSession
    ? t(autoHere ? 'menu.tmux.yolo.disableFor' : 'menu.tmux.yolo.enableFor', {session})
    : t(autoHere ? 'menu.tmux.yolo.disable' : 'menu.tmux.yolo.enable');
  const renameLabel = hasSession ? t('menu.tmux.rename.for', {session}) : t('menu.tmux.rename');
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
      ariaLabel: [yoloLabel, hasSession ? focusDetail : t('menu.tmux.focusSessionFirst')].filter(Boolean).join(' - '),
    }));
  commands.push(
    menuCommand(renameLabel, renameAction, {
      disabled: readOnlyMode || !hasSession,
      detail: visibleDetail,
      ariaLabel: [renameLabel, focusDetail].filter(Boolean).join(' - '),
    })
  );
  if (includeKill) commands.push(tmuxSessionKillCommand(session));
  return commands;
}

function tmuxSessionKillCommand(session) {
  const hasSession = isTmuxSession(session);
  const readonlyDetail = t('menu.common.adminOnly');
  const focusDetail = hasSession ? menuTabDetail(session) : t('menu.tmux.focusSessionFirst');
  const visibleDetail = readOnlyMode ? readonlyDetail : (hasSession ? '' : t('menu.tmux.noTabFocused'));
  const label = hasSession ? t('menu.tmux.kill.for', {session}) : t('menu.tmux.kill');
  return menuCommand(label, () => killTmuxSession(session), {
    disabled: readOnlyMode || !hasSession,
    detail: visibleDetail,
    ariaLabel: [label, focusDetail].filter(Boolean).join(' - '),
  });
}

function yoloRulePath() {
  return yoloRulesPayload.path
    || nestedSetting(clientSettings, 'yolo.rule_file_path', nestedSetting(clientSettingsDefaults, 'yolo.rule_file_path', '~/.config/yolomux/yolo-rules.yaml'));
}

function settingsConfigPath() {
  return clientSettingsPayload.path || clientSettingsPayload.display_path || '~/.config/yolomux/settings.yaml';
}

// Map the global theme mode to the terminal_theme setting value for the lockstep View -> Theme toggle.
function terminalThemeSettingForGlobalMode(mode) {
  if (mode === 'system') return 'follow-app';
  if (mode === 'light') return 'light';
  return 'dark';
}

// Apply a resolved global theme mode live and persist it — shared by the one-click Theme submenu
// (setGlobalThemeMode) and the View cycle shortcut (cycleGlobalThemeSetting). #258: APPLY live (the menu
// used to only save the patch, so body.theme-* never flipped). #261: do NOT pin appearance.terminal_theme
// — the terminal keeps its own setting (default follow-app/System) and follows the app on its own.
function applyAndSaveGlobalTheme(next) {
  globalThemeMode = next;
  applyGlobalThemeMode({updateEditor: true, updateTerminals: true});
  renderSessionButtons();  // rebuild the menu bar so the View -> Theme active marker tracks the new mode
  return saveSettingsPatch(settingPatch('appearance.theme', next))
    .then(() => {
      statusEl.textContent = `theme: ${globalThemeLabel(next)}`;
    })
    .catch(error => {
      statusErr(localizedHtml('status.themeSaveFailed', {error}));
      refreshSettings({force: true});
    });
}

// One-click from the Theme submenu: any target (system/dark/light) applies in one click, both directions.
function setGlobalThemeMode(mode) {
  return applyAndSaveGlobalTheme(normalizeGlobalThemeMode(mode));
}

function cycleGlobalThemeSetting() {
  return applyAndSaveGlobalTheme(nextGlobalThemeMode());
}

function yoloRuleStatusDetail() {
  const source = yoloRulesPayload.source || 'unknown';
  const count = Number(yoloRulesPayload.rule_count || 0);
  const countText = tPlural('menu.yolo.ruleCount', count);
  const dryRun = yoloRulesPayload.dry_run ? t('menu.yolo.dryRunSuffix') : '';
  return yoloRulesPayload.error
    ? t('menu.yolo.errorDetail', {error: yoloRulesPayload.error})
    : t('menu.yolo.statusDetail', {source, rules: countText, dryRun});
}

async function openYoloRuleFile() {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.yoloReadOnlyCreateRules'));
    return;
  }
  try {
    const payload = await apiFetchJson('/api/yolo-rules/open', {method: 'POST'});
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    await openFileInEditor(payload.path || yoloRulePath(), {name: basenameOf(payload.path || yoloRulePath())});
    statusEl.textContent = t('status.openedYoloRule');
  } catch (error) {
    statusErr(localizedHtml('status.yoloOpenRuleFailed', {error}));
  }
}

async function reloadYoloRules() {
  try {
    const payload = await apiFetchJson('/api/yolo-rules/reload', {method: 'POST'});
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    const level = payload.error ? 'error' : '';
    statusEl.innerHTML = payload.error
      ? `<span class="err">${localizedHtml('status.yoloReloadFailed', {error: payload.error})}</span>`
      : `<span class="ok">${localizedHtml('status.yoloReloaded')}</span>`;
    showToast(t('status.yoloToastTitle'), payload.error || yoloRuleStatusDetail(), {level});
  } catch (error) {
    statusErr(localizedHtml('status.yoloReloadRequestFailed', {error}));
  }
}

async function refreshYoloRulesStatus(options = {}) {
  try {
    const payload = await apiFetchJson('/api/yolo-rules');
    yoloRulesPayload = payload;
    renderPreferencesPanels();
    return payload;
  } catch (error) {
    if (!options.silent) statusErr(localizedHtml('status.yoloStatusFailed', {error}));
    return null;
  }
}

function tmuxYoloMenuItems() {
  return [
    menuNumberSetting('appearance.yolo_rotate_ms', t('pref.appearance.yolo_rotate_ms.label'), {
      min: 0,
      max: 60000,
      step: 250,
      suffix: 'ms',
      fallback: 20000,
      detail: t('pref.appearance.yolo_rotate_ms.help'),
    }),
    menuCommand(t('menu.yolo.openRuleFile'), openYoloRuleFile, {
      disabled: readOnlyMode,
      detail: compactHomePath(yoloRulePath()),
    }),
    menuCommand(t('menu.yolo.reloadRules'), reloadYoloRules, {
      detail: yoloRuleStatusDetail(),
    }),
  ];
}

function tmuxCurrentYoloCommand(session) {
  const hasSession = isTmuxSession(session);
  const payload = hasSession ? autoApproveStates.get(session) : null;
  const enabled = hasSession ? autoApproveEnabledHere(payload) : false;
  const elsewhere = hasSession ? autoApproveEnabledElsewhere(payload) : false;
  const label = hasSession ? t(enabled ? 'menu.tmux.yo.on' : elsewhere ? 'menu.tmux.yo.elsewhere' : 'menu.tmux.yo.off') : t('menu.tmux.yo.none');
  return menuCommand(label, async () => {
    if (!hasSession) return;
    await toggleAutoApprove(session);
    renderSessionButtons({force: true});
    renderPaneTabStrips();
  }, {
    disabled: readOnlyMode || !hasSession,
    detail: hasSession ? t('menu.tmux.sessionLabel', {session}) : t('menu.tmux.focusTabFirst'),
    iconHtml: hasSession ? yoloMarkerHtml(session, enabled, {enabledOnly: false, yoloWorking: sessionYoloIsWorking(session)}) : '',
    keepOpen: true,
    ariaLabel: hasSession ? t(enabled ? 'menu.tmux.yolo.disableFor' : 'menu.tmux.yolo.enableFor', {session}) : t('menu.tmux.focusTabFirst'),
  });
}

function tmuxSessionViewCommands(session) {
  const hasSession = isTmuxSession(session);
  const active = hasSession && activeSessions.includes(session);
  const focusDetail = hasSession ? menuTabDetail(session) : t('menu.tmux.focusSessionFirst');
  const disabledDetail = hasSession ? t('menu.tmux.openInPaneFirst') : t('menu.tmux.noTabFocused');
  const viewLabel = sessionLabel(session);
  const transcriptName = t('menu.tmux.transcript', {session: viewLabel});
  const summaryName = t('menu.tmux.aiTranscript', {session: viewLabel});
  const eventLogName = t('menu.tmux.eventLog', {session: viewLabel});
  return [
    menuCommand(transcriptName, () => {
      if (active) activateTab(session, 'transcript');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      checked: active && panelActiveTabName(session) === 'transcript',
      ariaLabel: [transcriptName, focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand(summaryName, () => {
      if (active) activateTab(session, 'summary');
    }, {
      disabled: readOnlyMode || !active,
      detail: readOnlyMode ? t('menu.common.adminOnly') : (active ? '' : disabledDetail),
      checked: active && panelActiveTabName(session) === 'summary',
      ariaLabel: [summaryName, focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand(eventLogName, () => {
      if (active) activateTab(session, 'events');
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      checked: active && panelActiveTabName(session) === 'events',
      ariaLabel: [eventLogName, focusDetail].filter(Boolean).join(' - '),
    }),
    menuCommand(t('menu.tmux.paneDetails'), () => {
      if (!active) return;
      const panel = document.getElementById(panelDomId(session));
      if (panel) setPanelDetailsCollapsed(panel, !panel.classList.contains('details-collapsed'));
    }, {
      disabled: !active,
      detail: active ? '' : disabledDetail,
      ariaLabel: [t('menu.tmux.paneDetails'), focusDetail].filter(Boolean).join(' - '),
    }),
  ];
}

// The flags/params YOLOmux launches an agent with, shown as the new-session item's detail (e.g.
// "--dangerously-skip-permissions"). Strips the binary name from the launch command; for a plain Term
// it's just the shell ("bash"). Empty when the command carries no extra flags.
function agentLaunchParams(agent) {
  const command = String(agentLaunchCommands[agent] || '').trim();
  if (!command) return '';
  const space = command.indexOf(' ');
  return space >= 0 ? command.slice(space + 1).trim() : command;
}

function agentUnavailableDetail(agent) {
  if (agentUnavailableReason(agent) === 'not-on-path') return t('menu.tmux.agentUnavailablePath');
  return t('menu.tmux.agentUnavailable', {name: agentName(agent)});
}

function newTmuxSessionItems() {
  return ['claude', 'codex', 'term'].map(agent => {
    const available = availableAgents.has(agent);
    const capped = visibleSessions.length >= maxSessionTabs;
    // #39: an installed agent that is not logged in is greyed with its login command, instead of
    // silently starting a session that the CLI will reject for auth.
    const loggedOut = available && !agentLoggedIn(agent);
    // Drop the "+" prefix (label is just the agent name); when launchable, the detail shows the params
    // actually passed (the --dangerously-* flags in YOLO mode) so you can see what command will run.
    return menuCommand(agentName(agent), () => createNextSession(agent), {
      iconHtml: agentIcon(agent),
      disabled: readOnlyMode || !available || loggedOut || capped,
      detail: readOnlyMode
        ? t('menu.common.adminOnly')
        : (!available
          ? agentUnavailableDetail(agent)
          : (loggedOut
            ? t('menu.tmux.runLogin', {command: agentLoginCommand(agent)})
            : (capped ? t('menu.tmux.limitReached') : agentLaunchParams(agent)))),
    });
  });
}

function tabCommandsForItems(items, options) {
  return items.map(item => menuTabCommand(item, options));
}

// a PR number in any of the forms a user types (#N, PR#N, PR N, N).
function prNumberSearchForms(number) {
  if (!number) return [];
  return [`#${number}`, `PR#${number}`, `PR ${number}`, String(number)];
}

function finderSearchAliases(item) {
  if (!isFileExplorerItem(item)) return [];
  return ['Finder', 'File Explorer', t('finder.label.finder'), t('finder.label.explorer')];
}

function tabSearchFields(item) {
  const info = transcriptMeta.sessions?.[item] || {};
  const filePath = fileItemPath(item) || '';
  const pr = displayPullRequest(info);
  // also index the repo's OTHER-branch PRs/branches/Linear IDs (the same data YO!info shows),
  // so a session is findable by ANY PR (e.g. #10289 on a non-current branch), branch name, or Linear ID
  // — not just its current-branch PR. Already in the metadata payload, so no extra fetch.
  const otherBranches = info.project?.git?.other_branches?.branches || [];
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
    ...finderSearchAliases(item),
    pr?.title,
    pr?.url,
    pr?.number ? 'PR' : '',
    ...prNumberSearchForms(pr?.number),
    ...(Array.isArray(info.linear) ? info.linear : []),
    ...otherBranches.flatMap(branch => [
      branch.name,
      branch.pull_request?.title,
      ...prNumberSearchForms(branch.pull_request?.number),
      ...(Array.isArray(branch.linear_ids) ? branch.linear_ids : []),
    ]),
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
    detail: t('menu.tabs.minimized'),
    openAsPane: true,
    toggleYolo: true,
  });
}

function inactiveTabMenuItems() {
  return tabCommandsForItems(filterTabItemsForSearch(inactiveTabItems()), {
    checked: false,
    detail: t('menu.tabs.notInPane'),
    openAsPane: true,
    toggleYolo: true,
  });
}

// P0 menu-bar: the Tabs navigator's order. 'attention' floats needs-* sessions to the top (the "Needs
// me" view) using the shared state priority; 'name' sorts by label; 'default' keeps the
// tmux/editors/other grouping. Stable (preserves the incoming order within equal keys).
function tabMenuSortPriority(item) {
  if (!isTmuxSession(item)) return 50;   // non-session tabs (editors/Finder/Prefs) sit after sessions
  const priority = sessionState(item)?.priority;
  return Number.isFinite(priority) ? priority : 50;
}

function sortTabItemsForMenu(items) {
  if (tabsMenuSortMode === 'attention') {
    return items
      .map((item, index) => ({item, index}))
      .sort((a, b) => tabMenuSortPriority(a.item) - tabMenuSortPriority(b.item) || a.index - b.index)
      .map(entry => entry.item);
  }
  if (tabsMenuSortMode === 'name') {
    return items
      .map((item, index) => ({item, index}))
      .sort((a, b) => String(itemLabel(a.item)).localeCompare(String(itemLabel(b.item))) || a.index - b.index)
      .map(entry => entry.item);
  }
  return items;
}

function setTabsMenuSortMode(mode) {
  tabsMenuSortMode = tabsMenuSortModes.includes(mode) ? mode : 'default';
  storageSet('yolomux.tabsMenuSort.v1', tabsMenuSortMode);
  renderSessionButtons({force: true});
}

function tabMenuItems(openItems = orderedPaneItems(activePaneItems())) {
  const query = tabsMenuSearchText.trim();
  const filteredOpenItems = sortTabItemsForMenu(filterTabItemsForSearch(openItems, query));
  const groupedItems = menuGroups(
    filteredOpenItems.map(item => menuTabCommand(item, {toggleYolo: true})),
    backgroundTabMenuItems(),
    inactiveTabMenuItems()
  );
  const resultItems = groupedItems.length ? groupedItems : [menuCommand(t('menu.tabs.noMatch'), null, {disabled: true})];
  return resultItems;
}

function fileMenuVirtualCommand(item, detail) {
  return menuCommand(itemLabel(item), () => (item === infoItemId ? openInfoSubTab('info') : selectSession(item, {userInitiated: true})), {
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
  const debugMenuItems = debugModeEnabled ? [
    fileMenuVirtualCommand(debugPaneItemId, t('menu.file.debug.detail')),
  ] : [];
  return [
    {
      id: 'file',
      label: t('menu.file'),
      items: menuGroups(
        [
          menuCommand(fileExplorerLabel(), () => toggleFinderPane(), {
            checked: itemInLayout(fileExplorerItemId),
            detail: t('menu.file.browseFiles'),
            iconHtml: tabTypeIconHtml(fileExplorerItemId, {menu: true}),
            targetItem: fileExplorerItemId,
          }),
          fileMenuVirtualCommand(infoItemId, t('menu.file.info.detail')),
          // #40: YO!agent is now a sub-tab of the merged YO!info pane — this entry opens that pane on it.
          menuCommand(yoagentTabLabel(), () => openInfoSubTab('yoagent'), {
            checked: itemInLayout(infoItemId) && infoPanelSubTab === 'yoagent',
            detail: t('menu.file.yoagent.detail'),
            iconHtml: appMenuUiIcon('yoagent'),
          }),
          menuCommand(t('menu.file.openFile'), openFileQuickOpen, {
            detail: appShortcutText('P'),
            iconHtml: appMenuUiIcon('document'),
          }),
          menuCommand(t('menu.file.preferences'), () => selectSession(prefsItemId, {userInitiated: true}), {
            checked: itemInLayout(prefsItemId),
            detail: compactHomePath(settingsConfigPath()),
            iconHtml: tabTypeIconHtml(prefsItemId, {menu: true}),
            targetItem: prefsItemId,
          }),
          ...debugMenuItems,
        ],
        [
          menuCommand(t('menu.file.logout'), logOut, {
            detail: t('menu.file.logout.detail'),
            iconHtml: appMenuUiIcon('logout'),
          }),
        ]
      ),
    },
    {
      id: 'view',
      label: t('menu.view'),
      items: [
        menuCommand(tabMetaVisible ? t('menu.view.tabMeta.hide') : t('menu.view.tabMeta.show'), toggleTabMetadata, {
          checked: tabMetaVisible,
          detail: t('menu.view.tabMeta.detail'),
          iconHtml: appMenuUiIcon('tab-meta', tabMetaVisible),
        }),
        menuCommand(t('menu.view.alert'), toggleNotifications, {
          checked: notificationsEnabled,
          disabled: readOnlyMode,
          detail: readOnlyMode ? t('menu.view.alert.adminDetail') : '',
          iconHtml: appMenuUiIcon('notify', notificationsEnabled),
        }),
        menuCommand(t('menu.view.refresh'), refreshAll, {
          iconHtml: appMenuUiIcon('refresh'),
        }),
        menuSubmenu(t('menu.view.theme'), [
          menuCommand(t('menu.view.theme.system'), () => setGlobalThemeMode('system'), {checked: normalizeGlobalThemeMode() === 'system', detail: t('menu.view.theme.system.detail', {mode: t('menu.view.theme.' + resolvedGlobalThemeMode())})}),
          menuCommand(t('menu.view.theme.dark'), () => setGlobalThemeMode('dark'), {checked: normalizeGlobalThemeMode() === 'dark'}),
          menuCommand(t('menu.view.theme.light'), () => setGlobalThemeMode('light'), {checked: normalizeGlobalThemeMode() === 'light'}),
        ]),
        menuSubmenu(t('menu.view.layout'), layoutModeValues.map(layoutMenuCommand)),
        menuSubmenu(t('menu.view.sortTabs'), [
          menuCommand(t('menu.view.sortTabs.default'), () => setTabsMenuSortMode('default'), {checked: tabsMenuSortMode === 'default', detail: t('menu.view.sortTabs.default.detail')}),
          menuCommand(t('menu.view.sortTabs.attention'), () => setTabsMenuSortMode('attention'), {checked: tabsMenuSortMode === 'attention', detail: t('menu.view.sortTabs.attention.detail')}),
          menuCommand(t('menu.view.sortTabs.name'), () => setTabsMenuSortMode('name'), {checked: tabsMenuSortMode === 'name'}),
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
          menuCommand(t('menu.tmux.resume'), null, {
            disabled: true,
            detail: t('menu.common.comingSoon'),
          }),
        ],
        [
          menuSubmenu(t('menu.tmux.yoloSubmenu'), tmuxYoloMenuItems()),
        ]
      ),
    },
    {
      id: 'tabs',
      label: t('menu.tabs'),
      badgeText: yoloCount ? String(yoloCount) : '',
      badgeTitle: yoloCount ? tPlural('menu.tabs.yoloBadge', yoloCount) : '',
      items: tabMenuItems(openItems),
    },
    {
      id: 'help',
      label: t('menu.help'),
      items: menuGroups(
        [menuCommand(t('menu.help.commandPalette'), openCommandPalette, {
          detail: appShortcutText('P', {shift: true}),
        })],
        [
          menuCommand(`YOLOmux ${bootstrap.version || ''}`.trim(), null, {
            disabled: true,
            detail: bootstrap.versionCommitTime ? t('menu.help.lastCommit', {time: bootstrap.versionCommitTime}) : '',
          }),
          menuCommand(t('menu.help.shortcuts'), openKeyboardShortcutsOverlay, {
            detail: '?',
          }),
          menuCommand(t('menu.help.openReadme'), openProjectReadme, {
            disabled: !projectReadmePath(),
            detail: t('menu.help.localReadme'),
          }),
        ],
        [
          menuCommand(t('menu.help.about'), showAboutModal),
        ]
      ),
    },
  ];
}

function appMenuIsOpen() {
  return Boolean(sessionButtons?.querySelector('.app-menu.open'));
}

function topbarControlIsActive() {
  const active = document.activeElement;
  return Boolean(active && sessionButtons?.contains(active) && active.matches?.('select, input'));
}

function flushPendingSessionButtonsRender() {
  if (!pendingSessionButtonsRender || topbarControlIsActive()) return;
  pendingSessionButtonsRender = false;
  renderSessionButtons();
}

function renderSessionButtons(options = {}) {
  if (!sessionButtons) return;
  if (!options.force && appMenuIsOpen()) {
    scheduleTopbarMetricsUpdate();
    return;
  }
  if (!options.force && topbarControlIsActive()) {
    pendingSessionButtonsRender = true;
    scheduleTopbarMetricsUpdate();
    return;
  }
  pendingSessionButtonsRender = false;
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
    sessionButtons.classList.add(CLS.dragOver);
  };
  sessionButtons.ondragleave = event => {
    if (!sessionButtons.contains(event.relatedTarget)) sessionButtons.classList.remove(CLS.dragOver);
  };
  sessionButtons.ondrop = event => {
    const payload = dragPayload(event);
    sessionButtons.classList.remove(CLS.dragOver);
    if (!payload?.session) return;
    event.preventDefault();
    event.stopPropagation();
    removeSessionFromLayout(payload.session);
  };
  sessionButtons.classList.remove(CLS.dragOver);
  sessionButtons.appendChild(createAppMenuBar());
  sessionButtons.appendChild(createTopbarNav());
  sessionButtons.appendChild(createTopbarSearch());
  updateEditorNavButtons();   // sync ←/→ disabled state to the global editorNav stack after (re)assembly
  // Topbar right group: Language | Activity (activity pinned far-right). #257: the theme switcher was
  // removed as redundant — theme is set via View -> Theme and the Preferences Global color theme.
  sessionButtons.appendChild(createTopbarLanguageSwitcher());
  sessionButtons.appendChild(createTopbarActivityStatus());
  updateTopbarActivityStatus();
  scheduleTopbarMetricsUpdate();
}

// #52: the wordmark is server-rendered (YO/LO/m/u/x); localize the YO/LO glyphs client-side so a
// Chinese locale shows 優樂mux / 优乐mux (優/优 boxed-green, 樂/乐 green) and updates on a language switch
// without a reload. The m/u/x colorful spans and the version are untouched.
function renderBrandWordmark() {
  for (const yo of document.querySelectorAll('.brand-title .brand-yolo')) yo.textContent = t('brand.wordmark.yo');
  for (const lo of document.querySelectorAll('.brand-title .brand-lo')) lo.textContent = t('brand.wordmark.lo');
  updateBrandTitles();
}

function createAppMenuBar() {
  const bar = document.createElement('nav');
  bar.className = 'app-menu-bar';
  bar.setAttribute('aria-label', t('menu.bar.aria'));
  bar.setAttribute('role', 'menubar');
  for (const menu of appMenuTree()) bar.appendChild(createAppMenu(menu));
  return bar;
}

// the editor back/forward history control lives in the GLOBAL topbar (left of the search
// box), not per editor pane — it's one file-history control for the whole window, like a browser's.
// Buttons are always visible; updateEditorNavButtons() toggles their disabled state from editorNav.
function createTopbarNav() {
  const group = document.createElement('div');
  group.className = 'topbar-nav';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', t('editor.nav.aria'));
  const back = makeButton({
    id: 'topbarNavBack',
    className: 'topbar-nav-button',
    title: t('editor.nav.back'),
    ariaLabel: t('editor.nav.back'),
    disabled: true,
    label: '←',
    onClick: event => { event.preventDefault(); editorNavBack(); },
  });
  const forward = makeButton({
    id: 'topbarNavForward',
    className: 'topbar-nav-button',
    title: t('editor.nav.forward'),
    ariaLabel: t('editor.nav.forward'),
    disabled: true,
    label: '→',
    onClick: event => { event.preventDefault(); editorNavForward(); },
  });
  group.append(back, forward);
  return group;
}

// Universal search affordance in the topbar's empty middle gap: a launcher that
// opens the existing unified palette (files/tabs/settings by default, `>` for
// commands) — it reuses openFileQuickOpen, it does not fork the palette logic.
function createTopbarSearch() {
  const isMac = /Mac|iP(hone|ad|od)/.test(navigator.platform || navigator.userAgent || '');
  const mod = isMac ? 'Cmd' : 'Ctrl';
  const button = makeButton({
    id: 'topbarSearch',
    className: 'topbar-search',
    ariaLabel: t('topbar.search.aria'),
    title: t('topbar.search.title', {mod}),
  });
  const icon = document.createElement('span');
  icon.className = 'topbar-search-icon';
  icon.setAttribute('aria-hidden', 'true');
  icon.textContent = '⌕';
  const label = document.createElement('span');
  label.className = 'topbar-search-label';
  label.textContent = t('topbar.search.label');
  const hint = document.createElement('kbd');
  hint.className = 'topbar-search-hint';
  hint.textContent = isMac ? '⌘P' : 'Ctrl+P';
  button.append(icon, label, hint);
  button.addEventListener('click', () => openFileQuickOpen());
  return button;
}

// Phase 1: a top-right language switcher (entry point #2). It writes the SAME general.language
// setting as the Preferences picker and applies the locale optimistically (no settings-poll round-trip).
// 'system' resolves against navigator.language; the <select> shows the raw pref so 'system' reads as Auto.
function createTopbarLanguageSwitcher() {
  const pref = String(initialSetting('general.language', 'system'));
  const select = document.createElement('select');
  select.className = 'topbar-language';
  select.setAttribute('aria-label', t('language.switcher'));
  select.title = t('language.switcher');
  for (const choice of i18nLocaleChoices()) {
    const option = document.createElement('option');
    option.value = choice.value;
    option.textContent = choice.label;
    if (choice.value === pref) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener('change', () => {
    const value = select.value;
    applyLocale(resolveLocalePref(value));
    if (readOnlyMode) return;
    saveSettingsPatch(settingPatch('general.language', value))
      .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
  });
  select.addEventListener('blur', flushPendingSessionButtonsRender);
  return select;
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
  // C14: a standalone submenu popover (measured on its own, with no .open parent) matches the base
  // `.app-submenu-popover { display:none }` rule and would measure 0 — force it visible for measurement.
  if (clone.classList?.contains('app-submenu-popover')) clone.style.display = 'block';
  clone.querySelectorAll('.app-submenu-popover').forEach(submenu => {
    submenu.style.display = 'block';
    submenu.style.width = 'max-content';
    submenu.style.minWidth = '0';
    submenu.style.maxWidth = 'none';
  });
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
  // C14: the command label + detail spans were OMITTED above, so their truncation CSS (overflow:hidden,
  // nowrap, min-width:0 / max-width:42ch) collapsed them in max-content sizing and the menu was measured
  // to the LABELS — ellipsizing the longer detail sub-lines. Un-clip them so each command's full one-line
  // width is counted. Keep the detail's 42ch cap so a genuinely longer detail still ellipsizes by design.
  clone.querySelectorAll('.app-menu-label').forEach(node => {
    node.style.maxWidth = 'none';
    node.style.minWidth = 'auto';
    node.style.overflow = 'visible';
    node.style.textOverflow = 'clip';
    node.style.whiteSpace = 'nowrap';
  });
  clone.querySelectorAll('.app-menu-detail').forEach(node => {
    node.style.minWidth = 'auto';
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
  // C14: a cold first measurement taken before web fonts settle sizes with fallback-font metrics and can
  // come out too narrow. Re-fit ONCE when fonts are ready (guarded so it schedules a single time) so the
  // very first menu open is corrected without waiting for a reopen.
  if (document.fonts?.ready && !popover._appMenuFontsRefit) {
    popover._appMenuFontsRefit = true;
    document.fonts.ready.then(() => { if (popover.isConnected) fitAppMenuPopover(popover); }).catch(() => {});
  }
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
  if (item.type === 'number-setting') return createAppMenuNumberSetting(item);
  if (item.type === 'submenu') return createAppSubmenu(item);
  return createAppMenuCommand(item);
}

function clampAppMenuNumberSetting(item, rawValue) {
  const fallback = Number.isFinite(Number(item.fallback)) ? Number(item.fallback) : 0;
  let value = Number(rawValue);
  if (!Number.isFinite(value)) value = fallback;
  if (Number.isFinite(Number(item.min))) value = Math.max(Number(item.min), value);
  if (Number.isFinite(Number(item.max))) value = Math.min(Number(item.max), value);
  return Number.isInteger(value) ? Math.round(value) : value;
}

function applyAppMenuNumberSettingPreview(path, value) {
  if (path === 'appearance.yolo_rotate_ms') {
    yoloRotateMs = Math.max(0, Number(value) || 0);
    applyCssSettings();
  }
}

function createAppMenuNumberSetting(item) {
  const row = document.createElement('label');
  row.className = ['app-menu-setting-row', 'app-menu-number-setting', item.className || ''].filter(Boolean).join(' ');
  row.setAttribute('role', 'none');
  row.dataset.settingPath = item.path || '';
  const label = document.createElement('span');
  label.className = 'app-menu-setting-label';
  label.textContent = item.label || item.path || '';
  const control = document.createElement('span');
  control.className = 'app-menu-setting-control';
  const input = document.createElement('input');
  input.type = 'number';
  input.inputMode = 'decimal';
  input.dataset.settingPath = item.path || '';
  input.min = String(item.min ?? '');
  input.max = String(item.max ?? '');
  input.step = String(item.step || 1);
  input.value = String(clampAppMenuNumberSetting(item, numberSetting(item.path, item.fallback ?? 0)));
  input.disabled = readOnlyMode || item.disabled === true;
  if (item.detail) input.title = item.detail;
  input.setAttribute('aria-label', item.label || item.path || '');
  input.addEventListener('click', event => event.stopPropagation());
  input.addEventListener('keydown', event => event.stopPropagation());
  input.addEventListener('input', event => {
    event.stopPropagation();
    const next = clampAppMenuNumberSetting(item, input.value);
    applyAppMenuNumberSettingPreview(item.path, next);
  });
  input.addEventListener('change', event => {
    event.preventDefault();
    event.stopPropagation();
    const next = clampAppMenuNumberSetting(item, input.value);
    input.value = String(next);
    applyAppMenuNumberSettingPreview(item.path, next);
    saveSettingsPatch(settingPatch(item.path, next))
      .then(() => {
        renderPreferencesPanels();
        statusEl.textContent = `saved ${item.path}`;
      })
      .catch(error => {
        statusErr(localizedHtml('status.settingsSaveFailed', {error}));
        refreshSettings({force: true});
      });
  });
  control.appendChild(input);
  if (item.suffix) {
    const suffix = document.createElement('span');
    suffix.className = 'app-menu-setting-suffix';
    suffix.textContent = item.suffix;
    control.appendChild(suffix);
  }
  row.append(label, control);
  return row;
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
  const detailHtml = item.detail ? `<span class="app-menu-detail" title="${esc(item.detail)}">${esc(item.detail)}</span>` : '';
  button.innerHTML = `<span class="app-menu-check" aria-hidden="true"></span><span class="app-menu-content">${contentHtml}${detailHtml}</span>${options.asSubmenu ? '<span class="app-menu-submenu-arrow" aria-hidden="true">&gt;</span>' : ''}`;
  if (!options.asSubmenu) {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const autoTarget = event.target.closest('[data-auto-session]');
      if (autoTarget && button.contains(autoTarget)) {
        if (readOnlyMode) {
          statusEl.textContent = t('status.yoloAdminRequired');
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
        statusErr(localizedHtml('status.menuCommandFailed', {error}));
        if (keepOpen) renderSessionButtons({force: true});
      });
  } catch (error) {
    statusErr(localizedHtml('status.menuCommandFailed', {error}));
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
