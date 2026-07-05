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
const aboutLicenseUrl = 'https://polyformproject.org/licenses/noncommercial/1.0.0';

function aboutDateTimeText() {
  if (bootstrap.versionCommitTime) return bootstrap.versionCommitTime;
  return localizedDateTimeFormat(Date.now() / 1000, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function openAboutLinkedIn() {
  window.open(aboutLinkedInUrl, '_blank', 'noopener,noreferrer');
}

function aboutCommitShaText() {
  return bootstrap.versionCommit || bootstrap.commitSha || bootstrap.commit_sha || bootstrap.gitSha || bootstrap.git_sha || '';
}

function topbarVersionTitle() {
  const sha = aboutCommitShaText();
  const commitCount = Number(bootstrap.versionCommitCount);
  const lines = [];
  if (sha) lines.push(t('menu.help.about.sha', {sha}));
  if (bootstrap.versionCommitTime) lines.push(t('menu.help.lastCommit', {time: bootstrap.versionCommitTime}));
  if (Number.isFinite(commitCount) && commitCount > 0) lines.push(t('menu.help.about.commits', {count: commitCount}));
  return lines.join('\n') || t('menu.help.about.shaUnknown');
}

function topbarServerStartedAtMs() {
  const explicitMs = Number(bootstrap.serverStartedAtMs);
  if (Number.isFinite(explicitMs) && explicitMs > 0) return explicitMs;
  const seconds = Number(bootstrap.serverStartedAt);
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 0;
}

function topbarDurationText(seconds) {
  const remaining = Math.max(0, Math.floor(Number(seconds) || 0));
  if (remaining < 1) return t('duration.underOneSecond');
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
    parts.push(tPlural(`duration.${name}`, value));
    rest -= value * size;
    if (parts.length >= 2) break;
  }
  return parts.join(', ');
}

function topbarServerUptimeTitle() {
  const startedAtMs = topbarServerStartedAtMs();
  if (!startedAtMs) return t('server.uptimeUnknown');
  return t('server.uptimeRunning', {duration: topbarDurationText((Date.now() - startedAtMs) / 1000)});
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
  return `<span class="about-brand-yo">${esc(t('brand.marker'))}</span><span class="about-brand-lo">${esc(t('brand.wordmark.lo'))}</span><span class="about-brand-m">m</span><span class="about-brand-u">u</span><span class="about-brand-x">x</span>`;
}

function showAboutModal() {
  const modal = document.getElementById('modal');
  const title = document.getElementById('modalTitle');
  const body = document.getElementById('modalBody');
  if (!modal || !title || !body) return;
  title.textContent = t('menu.help.about');
  relocalizeModalChrome({content: false});
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
    <div class="about-links"><a class="about-author" href="${esc(aboutLinkedInUrl)}" target="_blank" rel="noopener noreferrer">${esc(t('menu.help.about.author'))}</a><span> - </span><a class="about-author about-github" href="${esc(aboutProjectUrl)}" target="_blank" rel="noopener noreferrer">${esc(t('menu.help.about.github'))}</a></div>
    <div class="about-license"><a class="about-author about-license-link" href="${esc(aboutLicenseUrl)}" target="_blank" rel="noopener noreferrer">${esc(t('menu.help.about.license'))}</a></div>
  </div>`;
  modal.classList.add(CLS.open);
  scheduleSharePopupLayerPublish();
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
  return tabMenuDetailText(item, transcriptMetadataState.payload.sessions?.[item]);
}

function commandPaletteTabDetail(item) {
  const detail = menuTabDetail(item);
  const pr = displayPullRequest(transcriptMetadataState.payload.sessions?.[item]);
  if (!pr?.number) return detail;
  const bareNumber = `#${pr.number}`;
  const visibleNumber = `PR ${bareNumber}`;
  const rest = detail
    .replace(visibleNumber, '')
    .replace(bareNumber, '')
    .replace(/\s*·\s*·\s*/g, ' · ')
    .replace(/^\s*·\s*|\s*·\s*$/g, '')
    .trim();
  return rest ? `${visibleNumber} · ${rest}` : visibleNumber;
}

function menuTabRowHtml(item, options = {}) {
  const type = tabTypeForItem(item);
  if (type?.rowHtml) return type.rowHtml(item, options);
  const info = transcriptMetadataState.payload.sessions?.[item];
  const auto = autoApproveStates.get(item)?.enabled === true;
  const state = sessionState(item, info);
  const pr = displayPullRequest(info);
  const desc = sessionTabDescription(item, info);
  const detailHtml = desc ? `<span class="session-button-dir tab-inline-detail">${esc(desc)}</span>` : '';
  return `<span class="pane-tab-core">${sessionTabLeadingActivityHtml(item, info, auto, {enabledOnly: false, toggle: options.toggleYolo === true && !readOnlyMode, state})}<span class="session-button-prefix">${sessionNumberNameHtml(item)}</span>
    <span class="session-button-text">${state ? sessionStateHtml(state) : ''}${defaultBranchBadgeHtml(item, info)}${pullRequestCompactBadgesHtml(item, pr)}${detailHtml}</span></span>`;
}

function menuTabCommand(item, options = {}) {
  const slot = slotForSession(item);
  const visible = itemIsActivePaneTab(item);
  const active = item === currentActiveMenuItem();
  const detail = options.detail || (visible ? menuTabDetail(item) : (itemIsBackgroundPaneTab(item) ? t('menu.tabs.minimizedDetail') : t('menu.tabs.inactiveDetail')));
  return menuCommand(itemLabel(item), () => {
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
  const renameLabel = hasSession ? t('menu.tmux.rename.for', {session}) : t('common.renameTmuxSession');
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
      statusEl.textContent = t('status.themeChanged', {theme: globalThemeLabel(next)});
    })
    .catch(error => {
      statusErr(localizedHtml('status.themeSaveFailed', {error: userMessageText(error, t('common.requestFailed'))}));
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
  const source = structuredMessageText(yoloRulesPayload, 'source', t('common.unknown'));
  const count = Number(yoloRulesPayload.rule_count || 0);
  const countText = tPlural('menu.yolo.ruleCount', count);
  const dryRun = yoloRulesPayload.dry_run ? t('menu.yolo.dryRunSuffix') : '';
  return yoloRulesPayload.error
    ? t('common.errorDetail', {error: userMessageText(yoloRulesPayload, yoloRulesPayload.error)})
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
    const errorText = userMessageText(payload, payload.error || '');
    statusEl.innerHTML = payload.error
      ? `<span class="err">${localizedHtml('status.yoloReloadFailed', {error: errorText})}</span>`
      : `<span class="ok">${localizedHtml('status.yoloReloaded')}</span>`;
    showToast(t('brand.yoloRules'), errorText || yoloRuleStatusDetail(), {level});
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
  const label = t('menu.tmux.yo.on');
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

function newTmuxSessionLabel(agent) {
  return t('menu.tmux.newSession', {name: agentName(agent)});
}

function newTmuxSessionIcon(agent) {
  return agent === 'term' ? appMenuUiIcon('shell') : agentIcon(agent);
}

function newTmuxSessionItems() {
  return ['claude', 'codex', 'term'].map(agent => {
    const available = availableAgents.has(agent);
    const capped = visibleSessions.length >= maxSessionTabs;
    // #39: an installed agent that is not logged in is greyed with its login command, instead of
    // silently starting a session that the CLI will reject for auth.
    const loggedOut = available && !agentLoggedIn(agent);
    // When launchable, the detail shows the params actually passed (the --dangerously-* flags in YOLO
    // mode) so you can see what command will run.
    return menuCommand(newTmuxSessionLabel(agent), () => createNextSession(agent), {
      iconHtml: newTmuxSessionIcon(agent),
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

function linearSearchFields(linear) {
  if (!Array.isArray(linear)) return [];
  return linear.flatMap(item => {
    if (!item) return [];
    if (typeof item === 'string') return [item];
    return [item.identifier, item.title, item.state, item.url];
  }).filter(Boolean);
}

function pullRequestSearchFields(pr) {
  if (!pr) return [];
  return [
    pr.title,
    pr.description,
    pr.url,
    pr.state,
    pr.status_label,
    pr.review_decision,
    pr.author_login,
    pr.number ? 'PR' : '',
    ...prNumberSearchForms(pr.number),
    ...linearSearchFields(pr.linear_ids),
  ].filter(Boolean);
}

function finderSearchAliases(item) {
  if (!isFileExplorerItem(item)) return [];
  return ['Finder', 'File Explorer', t('finder.label.finder'), t('finder.label.explorer')];
}

function tabSearchFields(item) {
  const info = transcriptMetadataState.payload.sessions?.[item] || {};
  const filePath = fileItemPath(item) || '';
  // Also index the touched repos' branch/PR/Linear metadata (the same data YO!info shows), so a
  // session is findable by any related branch even when it is not the currently checked-out branch.
  const primaryRoot = info.project?.git?.root || info.project?.git?.cwd || '';
  const branchSources = [
    ...(info.project?.git?.other_branches?.branches || []),
    ...(Array.isArray(info.project?.repos)
      ? info.project.repos
        .filter(repo => (repo?.root || repo?.cwd || '') !== primaryRoot)
        .flatMap(repo => repo?.other_branches?.branches || [])
      : []),
  ];
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
    ...finderSearchAliases(item),
    ...pullRequestSearchFields(pr),
    ...linearSearchFields(info.linear),
    ...linearSearchFields(info.project?.linear),
    ...branchSources.flatMap(branch => [
      branch.name,
      branch.subject,
      ...pullRequestSearchFields(branch.pull_request),
      ...linearSearchFields(branch.linear_ids),
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
    newTmuxSessionItems(),
    filteredOpenItems.map(item => menuTabCommand(item, {toggleYolo: true})),
    backgroundTabMenuItems(),
    inactiveTabMenuItems()
  );
  const resultItems = groupedItems.length ? groupedItems : [menuCommand(t('menu.tabs.noMatch'), null, {disabled: true})];
  return resultItems;
}

function fileMenuVirtualCommand(item, detail) {
  return menuCommand(itemLabel(item), () => selectSession(item, {userInitiated: true}), {
    checked: itemInLayout(item),
    detail,
    iconHtml: tabTypeIconHtml(item, {menu: true}),
    targetItem: item,
  });
}

function fileMenuPanelCommands() {
  return FILE_MENU_PANEL_DEFINITIONS.map(({itemId}) => {
    if (itemId === fileExplorerItemId) {
      return menuCommand(fileExplorerLabel(), () => toggleFinderPane(), {
        checked: itemInLayout(fileExplorerItemId),
        detail: appShortcutText('B'),
        iconHtml: tabTypeIconHtml(fileExplorerItemId, {menu: true}),
        targetItem: fileExplorerItemId,
      });
    }
    return fileMenuVirtualCommand(itemId, tabTypeForItem(itemId)?.detail?.() || '');
  });
}

function appMenuTree() {
  const activeTmux = currentSessionActionTarget();
  const shareSessions = shareSessionsFromLayout();
  const shareCanOpen = shareSessions.length > 0 || Boolean(activeTmux);
  const shareMenuActive = shareViewMode || shareHasActiveShare();
  const openItems = orderedPaneItems(activePaneItems());
  const yoloCount = yoloWorkingSessions().length;
  return [
    {
      id: 'file',
      label: t('menu.file'),
      items: menuGroups(
        [
          menuCommand(t('common.openFile'), openFileQuickOpen, {
            detail: appShortcutText('P'),
            iconHtml: appMenuUiIcon('document'),
          }),
          ...fileMenuPanelCommands().filter(command => !shareViewMode || command.targetItem !== chatItemId),
          menuCommand(t('menu.file.share'), () => showShareModal(), {
            disabled: readOnlyMode || (!shareHasActiveShare() && !shareCanOpen),
            detail: shareMenuActive || shareCanOpen ? t('share.menu.sharing') : t('share.noSession'),
            iconHtml: appMenuUiIcon('share', shareMenuActive),
          }),
          menuCommand(t('common.preferences'), () => selectSession(prefsItemId, {userInitiated: true}), {
            checked: itemInLayout(prefsItemId),
            detail: compactHomePath(settingsConfigPath()),
            iconHtml: tabTypeIconHtml(prefsItemId, {menu: true}),
            targetItem: prefsItemId,
          }),
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
        menuSubmenu(t('menu.view.alert'), notificationDeliveryItems(), {
          iconHtml: appMenuUiIcon('notify', notificationDeliveryEnabled()),
          keepOpen: true,
        }),
        menuCommand(t('common.refresh'), refreshAll, {
          iconHtml: appMenuUiIcon('refresh'),
        }),
        menuSubmenu(t('menu.view.theme'), [
          menuCommand(t('common.theme.system'), () => setGlobalThemeMode('system'), {checked: normalizeGlobalThemeMode() === 'system', detail: t('menu.view.theme.system.detail', {mode: t('common.theme.' + resolvedGlobalThemeMode())})}),
          menuCommand(t('common.theme.dark'), () => setGlobalThemeMode('dark'), {checked: normalizeGlobalThemeMode() === 'dark'}),
          menuCommand(t('common.theme.light'), () => setGlobalThemeMode('light'), {checked: normalizeGlobalThemeMode() === 'light'}),
        ]),
        ...(narrowSingleColumnMode() ? [] : [menuSubmenu(t('menu.view.layout'), availableLayoutModes().map(layoutMenuCommand))]),
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
        [fileMenuVirtualCommand(infoItemId, t('menu.file.info.detail'))],
        tmuxSessionViewCommands(activeTmux),
        [
          ...tmuxSessionActionCommands(activeTmux, {includeYolo: false}),
          menuCommand(t('menu.tmux.resume'), null, {
            disabled: true,
            detail: t('menu.common.comingSoon'),
          }),
        ],
        [
          menuSubmenu(t('brand.yolo'), tmuxYoloMenuItems()),
        ]
      ),
    },
    {
      id: 'tabs',
      label: t('common.tabsLabel'),
      badgeText: String(yoloCount),
      badgeTitle: tPlural('menu.tabs.yoloBadge', yoloCount),
      items: tabMenuItems(openItems),
    },
    {
      id: 'help',
      label: t('menu.help'),
      items: menuGroups(
        [menuCommand(t('common.commandPalette'), openCommandPalette, {
          detail: appShortcutText('P', {shift: true}),
        })],
        [
          menuCommand(t('brand.version', {version: bootstrap.version || ''}).trim(), null, {
            disabled: true,
            detail: bootstrap.versionCommitTime ? t('menu.help.lastCommit', {time: bootstrap.versionCommitTime}) : '',
          }),
          menuCommand(t('common.keyboardShortcuts'), openKeyboardShortcutsOverlay, {
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

let topbarNavigationCompact = null;
let topbarNavigationFitFrame = 0;
let topbarNavigationFitObserver = null;

// Pure geometry helper retained for responsive test coverage. It deliberately does not choose the
// rendered menu state: that decision must not depend on the current compact/full DOM shape.
function topbarFullMenuFitsAvailableSpace(areaWidth, fullMenuWidth, otherWidths = [], gap = 0) {
  const reservedWidth = otherWidths.reduce((sum, width) => sum + Math.max(0, Number(width) || 0), 0)
    + (Math.max(0, Number(gap) || 0) * otherWidths.length);
  return fullMenuWidth > 0 && fullMenuWidth <= Math.max(0, areaWidth - reservedWidth);
}

function topbarFullNavigationFits() {
  return null;
}

function setTopbarNavigationCompactClass(compact) {
  for (const target of [document.body, appRootElement()].filter(Boolean)) {
    target.classList?.toggle('app-topbar-menu-compact', compact);
  }
}

function topbarNavigationShouldBeCompact() {
  // A measured row depends on whether it currently renders the five roots or one Menus root.
  // At the phone fallback that created a compact/full render loop, detaching the button between
  // pointerdown and click. The breakpoint is already after all lower-priority chrome yields, so it
  // is the stable single owner of this final compact transition.
  return compactTopbarForViewport();
}

function scheduleTopbarNavigationFitCheck() {
  if (topbarNavigationFitFrame) return;
  topbarNavigationFitFrame = requestAnimationFrame(() => {
    topbarNavigationFitFrame = 0;
    syncTopbarNavigationPresentation();
  });
}

function installTopbarNavigationFitObserver() {
  if (topbarNavigationFitObserver || !sessionButtons || typeof ResizeObserver !== 'function') return;
  topbarNavigationFitObserver = new ResizeObserver(() => scheduleTopbarNavigationFitCheck());
  topbarNavigationFitObserver.observe(sessionButtons);
}

function topbarMenuTree() {
  const menus = appMenuTree();
  const compact = topbarNavigationShouldBeCompact();
  topbarNavigationCompact = compact;
  setTopbarNavigationCompactClass(compact);
  if (!compact) return menus;
  return [{
    id: 'application-menu',
    // Translator context: this is a compact application-navigation control (File/View/tmux/Tabs/Help),
    // never a restaurant/food menu. Keep the local software-UI term short enough for the top bar.
    label: t('menu.compact.label'),
    nestedRoot: true,
    items: menus.map(menu => menuSubmenu(menu.label, menu.items)),
  }];
}

function syncTopbarNavigationPresentation() {
  const compact = topbarNavigationShouldBeCompact();
  setTopbarNavigationCompactClass(compact);
  if (topbarNavigationCompact === null || topbarNavigationCompact === compact) return false;
  topbarNavigationCompact = compact;
  closeAppMenus();
  renderSessionButtons({force: true});
  return true;
}

function appMenuIsOpen() {
  return Boolean(sessionButtons?.querySelector('.app-menu.open'));
}

function topbarControlIsActive() {
  const active = document.activeElement;
  return Boolean(active && sessionButtons?.contains(active) && active.matches?.('select, input, .topbar-language, .app-menu-button'));
}

function flushPendingSessionButtonsRender() {
  if (!pendingSessionButtonsRender || topbarControlIsActive()) return;
  pendingSessionButtonsRender = false;
  renderSessionButtons();
}

function renderSessionButtons(options = {}) {
  const perf = clientPerfStart('renderSessionButtons');
  try {
    return renderSessionButtonsMeasured(options);
  } finally {
    clientPerfEnd(perf, {nodes: sessionButtons?.childElementCount || 0});
  }
}

function renderSessionButtonsMeasured(options = {}) {
  if (!sessionButtons) return;
  // An SSE/locale/status repaint can otherwise detach a touch target after pointerdown but before
  // the browser emits click. An open menu is an explicit interaction, so defer every repaint until
  // it closes; the existing pending render path preserves the newest requested chrome state.
  if (appMenuIsOpen()) {
    if (options.force) pendingSessionButtonsRender = true;
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
  sessionButtons.appendChild(createTopbarCenterTools());
  updateEditorNavButtons();   // sync ←/→ disabled state to the global editorNav stack after (re)assembly
  // Topbar right group: Language | Activity (activity pinned far-right). #257: the theme switcher was
  // removed as redundant — theme is set via View -> Theme and the Preferences Global color theme.
  sessionButtons.appendChild(createTopbarRightTools());
  updateTopbarOwnerStatus();
  updateTopbarActivityStatus();
  scheduleTopbarMetricsUpdate();
  installTopbarNavigationFitObserver();
  scheduleTopbarNavigationFitCheck();
  if (openAppMenuId) requestAnimationFrame(() => scheduleSharePopupLayerPublish({immediate: true}));
}

// #52: the wordmark is server-rendered (YO/LO/m/u/x); localize the YO/LO glyphs client-side so a
// Chinese locale shows 優樂mux / 优乐mux (優/优 boxed-green, 樂/乐 green) and updates on a language switch
// without a reload. The m/u/x colorful spans and the version are untouched.
function renderBrandWordmark() {
  for (const yo of document.querySelectorAll('.brand-title .brand-yolo')) yo.textContent = t('brand.marker');
  for (const lo of document.querySelectorAll('.brand-title .brand-lo')) lo.textContent = t('brand.wordmark.lo');
  for (const brand of document.querySelectorAll('.brand-title')) {
    brand.setAttribute('aria-label', t('brand.version', {version: bootstrap.version || ''}));
  }
  updateBrandTitles();
}

function renderTopbarStaticChrome() {
  sessionButtons?.setAttribute('aria-label', t('app.menusAria'));
  if (latencyMeter) latencyMeter.title = t('app.latencyTitle');
  if (logoutButton) {
    const label = t('menu.file.logout');
    logoutButton.textContent = label;
    logoutButton.title = label;
    logoutButton.setAttribute('aria-label', label);
  }
}

function createAppMenuBar() {
  const bar = document.createElement('nav');
  bar.className = 'app-menu-bar';
  bar.setAttribute('aria-label', t('app.menusAria'));
  bar.setAttribute('role', 'menubar');
  for (const menu of topbarMenuTree()) bar.appendChild(createAppMenu(menu));
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

function createTopbarCenterTools() {
  const group = document.createElement('div');
  group.className = 'topbar-center-tools';
  group.append(createTopbarNav(), createTopbarSearch());
  return group;
}

function createTopbarRightTools() {
  const group = document.createElement('div');
  group.className = 'topbar-right-tools';
  group.append(createTopbarLanguageSwitcher(), createTopbarOwnerStatus(), createTopbarActivityStatus());
  return group;
}

// Phase 1: a top-right language switcher (entry point #2). It writes the SAME general.language
// setting as the Preferences picker and applies the locale optimistically (no settings-poll round-trip).
// A native <select> cannot be mirrored to YO!share viewers, so this uses the app-menu popup owner.
function createTopbarLanguageSwitcher() {
  const pref = String(initialSetting('general.language', 'system'));
  const choices = i18nLocaleChoices();
  const active = choices.find(choice => choice.value === pref) || choices[0] || {value: 'system', label: t('pref.general.language.system')};
  const wrapper = createAppMenu({
    id: 'language',
    label: active.label,
    items: choices.map(choice => ({
      label: choice.label,
      detail: '',
      checked: choice.value === pref,
      disabled: shareViewMode,
      action: () => {
        const value = choice.value;
        applyLocale(resolveLocalePref(value));
        scheduleShareAppearancePublish();
        if (readOnlyMode) return;
        saveSettingsPatch(settingPatch('general.language', value))
          .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
      },
    })),
  });
  wrapper.classList.add('topbar-language-menu');
  const button = wrapper.querySelector(':scope > .app-menu-button');
  if (button) {
    button.classList.add('topbar-language');
    button.title = t('common.language');
    button.setAttribute('aria-label', t('common.language'));
    button.addEventListener('blur', flushPendingSessionButtonsRender);
  }
  wrapper.querySelector(':scope > .app-menu-popover')?.classList.add('topbar-language-popover');
  return wrapper;
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
  wrapper.className = `app-menu${menu.nestedRoot ? ' app-menu--nested-root' : ''}`;
  wrapper.dataset.appMenu = menu.id;
  const popover = document.createElement('div');
  popover.className = 'app-menu-popover';
  popover.setAttribute('role', 'menu');
  popover.setAttribute('aria-label', menu.label);
  for (const item of menu.items) popover.appendChild(createAppMenuItem(item));
  fitAppMenuPopover(popover);
  const activateMenu = event => {
    event.preventDefault();
    event.stopPropagation();
    if (wrapper.classList.contains(CLS.open)) {
      const openMs = Date.now() - openAppMenuOpenedAt;
      if (openAppMenuPinned && openMs >= menuClickCloseGraceMs) closeAppMenus();
      else openAppMenu(wrapper, {focusFirst: false, pinned: true});
      return;
    }
    openAppMenu(wrapper, {focusFirst: false, pinned: true});
  };
  const button = makeButton({
    className: 'app-menu-button',
    role: 'menuitem',
    html: `${esc(menu.label)}${menu.badgeText ? `<span class="app-menu-button-badge" title="${esc(menu.badgeTitle || '')}">${esc(menu.badgeText)}</span>` : ''}`,
    attributes: {'aria-haspopup': 'true', 'aria-expanded': openAppMenuId === menu.id ? 'true' : 'false'},
    onClick: event => {
      if (button.dataset.pointerActionHandled === '1') {
        delete button.dataset.pointerActionHandled;
        return;
      }
      activateMenu(event);
    },
    events: {
      keydown: event => handleAppMenuButtonKeydown(event, wrapper),
      pointerdown: event => {
        if (event.button !== 0) return;
        // Open before a browser turns the tap into click, so an async chrome repaint cannot make
        // the compact Menus target disappear under the user's finger.
        button.dataset.pointerActionHandled = '1';
        activateMenu(event);
      },
    },
  });
  wrapper.append(button, popover);
  button.addEventListener('blur', flushPendingSessionButtonsRender);
  if (openAppMenuId === menu.id) wrapper.classList.add(CLS.open);
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
  void path;
  void value;
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
        statusEl.textContent = t('status.settingSaved', {path: item.path});
      })
      .catch(error => {
        statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))}));
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
  wrapper.className = 'app-menu-submenu-wrap';
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
    wrapper.classList.add(CLS.open);
    button.setAttribute('aria-expanded', 'true');
  });
  button.addEventListener('keydown', event => {
    if (event.key === 'ArrowRight') {
      event.preventDefault();
      wrapper.classList.add(CLS.open);
      button.setAttribute('aria-expanded', 'true');
      focusFirstAppMenuCommand(submenu);
    }
  });
  button.setAttribute('aria-haspopup', 'true');
  button.setAttribute('aria-expanded', 'false');
  wrapper.append(button, submenu);
  bindAppMenuCommandMirrorActive(button, wrapper);
  return wrapper;
}

function appMenuCommandMirrorActiveStillApplies(button, owner = button) {
  const active = document.activeElement;
  return Boolean(
    button?.matches?.(':hover')
      || owner?.matches?.(':hover')
      || (active && (button?.contains?.(active) || owner?.contains?.(active)))
  );
}

function setAppMenuCommandMirrorActive(button, active) {
  if (!button) return;
  button.classList.toggle('share-mirror-active', active === true);
  scheduleSharePopupLayerPublish({immediate: true});
}

function bindAppMenuCommandMirrorActive(button, owner = button) {
  if (!button) return;
  const bindKey = owner === button ? 'shareMirrorActiveBound' : 'shareMirrorOwnerActiveBound';
  if (button.dataset[bindKey] === 'true') return;
  button.dataset[bindKey] = 'true';
  const activate = () => setAppMenuCommandMirrorActive(button, true);
  const deactivate = () => {
    requestAnimationFrame(() => {
      if (!appMenuCommandMirrorActiveStillApplies(button, owner)) setAppMenuCommandMirrorActive(button, false);
    });
  };
  const targets = owner === button ? [button] : [owner];
  for (const target of targets.filter(Boolean)) {
    target.addEventListener('pointerenter', activate);
    target.addEventListener('focusin', activate);
    target.addEventListener('pointerleave', deactivate);
    target.addEventListener('focusout', deactivate);
  }
}

function createAppMenuCommand(item, options = {}) {
  const ariaLabel = item.ariaLabel || [item.label, item.detail].filter(Boolean).join(' - ');
  const richHtml = item.html ? stripTitleAttrs(item.html) : '';
  const iconHtml = item.iconHtml ? stripTitleAttrs(item.iconHtml) : '';
  const contentHtml = richHtml
    ? `<span class="app-menu-rich">${richHtml}</span>`
    : `<span class="app-menu-line">${iconHtml ? `<span class="app-menu-icon">${iconHtml}</span>` : ''}<span class="app-menu-label">${esc(item.label)}</span></span>`;
  const detailHtml = item.detail ? `<span class="app-menu-detail" title="${esc(item.detail)}">${esc(item.detail)}</span>` : '';
  const button = makeButton({
    className: ['app-menu-command', item.className || '', options.asSubmenu ? 'has-submenu' : ''].filter(Boolean).join(' '),
    role: item.checked !== undefined ? 'menuitemcheckbox' : 'menuitem',
    checked: item.checked,
    disabled: item.disabled,
    ariaLabel,
    dataset: item.checked !== undefined ? {checked: item.checked ? 'true' : 'false'} : undefined,
    html: `<span class="app-menu-check" aria-hidden="true"></span><span class="app-menu-content">${contentHtml}${detailHtml}</span>${options.asSubmenu ? '<span class="app-menu-submenu-arrow" aria-hidden="true">&gt;</span>' : ''}`,
  });
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
  bindAppMenuCommandMirrorActive(button);
  return button;
}

function runAppMenuCommand(item) {
  if (item.disabled || typeof item.action !== 'function') return;
  const keepOpen = item.keepOpen === true;
  if (!keepOpen) closeAppMenus();
  try {
    Promise.resolve(item.action())
      .then(() => {
        if (keepOpen && item.renderMenu !== false) renderSessionButtons({force: true});
      })
      .catch(error => {
        statusErr(localizedHtml('status.menuCommandFailed', {error}));
        if (keepOpen && item.renderMenu !== false) renderSessionButtons({force: true});
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
    canOpen: event => autoFocusCanFollowCursor(event) || appMenuIsOpen(),
    showDelay: () => (appMenuIsOpen() ? 0 : menuHoverOpenDelayMs),
    hideDelay: () => menuHoverCloseDelayMs,
    stillActive: () => wrapper.matches?.(':hover'),
    onOpen: () => {
      const menuId = wrapper.dataset.appMenu || '';
      const currentWrapper = document.querySelector(`.app-menu[data-app-menu="${cssEscape(menuId)}"]`) || wrapper;
      if (!currentWrapper.classList.contains(CLS.open)) openAppMenu(currentWrapper, {focusFirst: false, pinned: false});
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
  wrapper.classList.add(CLS.open);
  if (!wrapper.classList.contains('app-menu--nested-root')) {
    wrapper.querySelectorAll('.app-menu-submenu-wrap').forEach(submenu => {
      submenu.classList.add(CLS.open);
      submenu.querySelector(':scope > .app-menu-command')?.setAttribute('aria-expanded', 'true');
    });
  }
  openAppMenuId = wrapper.dataset.appMenu || null;
  openAppMenuPinned = options.pinned === true;
  openAppMenuOpenedAt = Date.now();
  wrapper.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'true');
  scheduleSharePopupLayerPublish({immediate: true});
  scheduleShareTopologySnapshot('popup-open');
  if (options.focusFirst) requestAnimationFrame(() => focusFirstAppMenuCommand(wrapper));
}

function closeAppMenus(keepOpen = null) {
  for (const menu of document.querySelectorAll('.app-menu.open')) {
    if (menu === keepOpen) continue;
    menu.classList.remove(CLS.open);
    menu.querySelector('.app-menu-button')?.setAttribute('aria-expanded', 'false');
  }
  document.querySelectorAll('.app-menu-command.share-mirror-active').forEach(command => {
    if (!keepOpen?.contains?.(command)) command.classList.remove('share-mirror-active');
  });
  for (const submenu of document.querySelectorAll('.app-menu-submenu-wrap.open')) {
    if (keepOpen?.contains(submenu)) continue;
    submenu.classList.remove(CLS.open);
    submenu.querySelector(':scope > .app-menu-command')?.setAttribute('aria-expanded', 'false');
  }
  openAppMenuId = keepOpen?.dataset?.appMenu || null;
  if (!openAppMenuId) {
    openAppMenuPinned = false;
    openAppMenuOpenedAt = 0;
  }
  scheduleSharePopupLayerPublish({immediate: true});
  scheduleShareTopologySnapshot('popup-close');
}
