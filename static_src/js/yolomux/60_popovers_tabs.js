function toggleHiddenFiles() {
  setFileExplorerManualRootMode();
  fileExplorerShowHidden = !fileExplorerShowHidden;
  storageSet(fileExplorerHiddenStorageKey, fileExplorerShowHidden ? '1' : '0');
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  if (fileExplorerRoot) refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
}

async function expandDirectoryRow(row, fullPath, options = {}) {
  const entries = await fetchDirectory(fullPath);
  if (!entries) return;
  if (options.manual === true) {
    forgetFileExplorerSyncManualCollapse(fullPath);
    resetFileExplorerAppliedSyncPlan();
  }
  fileExplorerExpanded.add(fullPath);
  row.classList.add('expanded');
  row.setAttribute('aria-expanded', 'true');
  row.querySelector('.file-tree-icon').textContent = '▾';
  const existingChildren = childContainerForRow(row, fullPath);
  const children = existingChildren || createFileTreeChildContainer(fullPath);
  const depth = parseInt(row.style.paddingLeft, 10);
  const nextDepth = Math.round((depth - 8) / 14) + 1;
  renderTreeChildren(children, fullPath, entries, nextDepth);
  if (!existingChildren) row.insertAdjacentElement('afterend', children);
  rememberFileExplorerSyncExpandedState();
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function collapseDirectoryRow(row, fullPath, options = {}) {
  if (options.manual === true) {
    rememberFileExplorerSyncManualCollapse(fullPath);
    resetFileExplorerAppliedSyncPlan();
  }
  fileExplorerExpanded.delete(fullPath);
  row.classList.remove('expanded');
  row.setAttribute('aria-expanded', 'false');
  row.querySelector('.file-tree-icon').textContent = '▸';
  Array.from(row.parentElement?.children || [])
    .filter(node => node.classList?.contains('file-tree-children') && node.dataset?.parent === fullPath)
    .forEach(node => node.remove());
  rememberFileExplorerSyncExpandedState();
  if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

if (fileExplorerClose) fileExplorerClose.addEventListener('click', () => toggleFileExplorer());
if (fileExplorerPathCopy) fileExplorerPathCopy.addEventListener('click', copyCurrentFileExplorerPath);
bindFileExplorerPathInput(fileExplorerPath);
bindFileExplorerHeaderActions(fileExplorer);
if (fileExplorerRootModeButton) {
  fileExplorerRootModeButton.addEventListener('click', toggleFileExplorerRootMode);
}
renderFileExplorerRootModeControls();
if (fileExplorerHiddenToggle) {
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  fileExplorerHiddenToggle.addEventListener('click', toggleHiddenFiles);
}

function updateSessionButtonStates() {
  // Top navigation is menu-based now; per-session state lives in pane tabs
  // and menu rows, which are rebuilt by their normal render paths.
  // Keep the top-bar cross-session activity line live as states change (poll-driven).
  updateTopbarActivityStatus();
}

function bindFilePopoverActions(container) {
  container.querySelectorAll('[data-copy-popover-path]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyFilePath(button.dataset.copyPopoverPath || '', 'path');
    });
  });
}

function clearTimer(timer) {
  if (timer) clearTimeout(timer);
  return null;
}

function numericOption(value, fallback) {
  const resolved = typeof value === 'function' ? value() : value;
  const number = Number(resolved);
  return Number.isFinite(number) ? number : fallback;
}

function viewportBounds(edgeGap = popoverEdgeGapPx()) {
  const width = Math.max(0, window.innerWidth || document.documentElement?.clientWidth || 0);
  const height = Math.max(0, window.innerHeight || document.documentElement?.clientHeight || 0);
  return {
    left: edgeGap,
    top: edgeGap,
    right: Math.max(edgeGap, width - edgeGap),
    bottom: Math.max(edgeGap, height - edgeGap),
  };
}

function clampToViewport(left, top, width, height, options = {}) {
  const bounds = viewportBounds(options.edgeGap ?? popoverEdgeGapPx());
  const minTop = Math.max(bounds.top, Number(options.minTop || 0));
  const maxLeft = Math.max(bounds.left, bounds.right - Math.max(0, width || 0));
  const maxTop = Math.max(minTop, bounds.bottom - Math.max(0, height || 0));
  return {
    left: Math.min(Math.max(bounds.left, left), maxLeft),
    top: Math.min(Math.max(minTop, top), maxTop),
  };
}

function stopPopoverEvent(event) {
  event.stopPropagation();
}

function closeOtherSessionPopovers(current, options = {}) {
  const force = options.force === true;
  for (const other of document.querySelectorAll('.pane-tab.popover-open, .panel-popover-zone.popover-open')) {
    if (other !== current) {
      const popover = other.querySelector?.(':scope > .session-popover, :scope > .panel-detail-popover')
        || other.__yolomuxDetachedPopover;
      if (current === null && !force && popoverStillActive(other, popover)) continue;
      other.classList.remove('popover-open');
      popover?.classList?.remove('popover-open');
      other.__yolomuxDetachedPopover?.classList?.remove('popover-open');
      delete other.dataset.popoverHoverState;
    }
  }
}

function popoverLifecycleActive(anchor, popover) {
  const state = anchor?.dataset?.popoverHoverState || '';
  return Boolean(
    state === 'open'
      || state === 'pending'
      || state === 'closing'
      || anchor?.classList?.contains?.('popover-open')
      || anchor?.matches?.(':hover')
      || popover?.matches?.(':hover')
  );
}

function popoverStillActive(anchor, popover) {
  const focused = document.activeElement;
  return Boolean(
    anchor?.matches?.(':hover')
      || popover?.matches?.(':hover')
      || (focused && (anchor.contains(focused) || popover?.contains(focused)))
  );
}

function bindPopoverHover(anchor, popover, handlers) {
  const queueOpen = handlers.queueOpen || handlers.keepOpen;
  const keepOpen = handlers.keepOpen || queueOpen;
  const closeSoon = handlers.closeSoon;
  const closeIfOutside = event => {
    const next = event?.relatedTarget;
    if (next && (anchor.contains(next) || popover?.contains(next))) return;
    closeSoon(event);
  };

  anchor.addEventListener('pointerenter', queueOpen);
  anchor.addEventListener('pointerleave', closeIfOutside);
  anchor.addEventListener('focusin', queueOpen);
  anchor.addEventListener('focusout', closeIfOutside);
  if (!popover) return;
  popover.addEventListener('pointerenter', keepOpen);
  popover.addEventListener('pointerleave', closeIfOutside);
  popover.addEventListener('click', stopPopoverEvent);
  popover.addEventListener('dragstart', stopPopoverEvent);
  popover.querySelectorAll('a').forEach(link => {
    link.addEventListener('pointerenter', keepOpen);
    link.addEventListener('click', stopPopoverEvent);
  });
}

function createHoverPopover(options) {
  const anchor = options.anchor;
  if (!anchor) return null;
  const stateClass = options.stateClass === undefined ? 'popover-open' : options.stateClass;
  let showTimer = null;
  let hideTimer = null;
  const popover = () => (typeof options.popover === 'function' ? options.popover() : options.popover);
  const canOpen = event => (typeof options.canOpen === 'function' ? options.canOpen(event) !== false : true);
  const stillActive = event => (typeof options.stillActive === 'function'
    ? options.stillActive(event) !== false
    : popoverStillActive(anchor, popover()));
  const markState = state => {
    if (!anchor.dataset) return;
    if (state) anchor.dataset.popoverHoverState = state;
    else delete anchor.dataset.popoverHoverState;
  };
  const cancelTimers = () => {
    showTimer = clearTimer(showTimer);
    hideTimer = clearTimer(hideTimer);
  };
  const closeNow = event => {
    cancelTimers();
    if (stateClass) anchor.classList.remove(stateClass);
    markState('');
    options.onClose?.(event);
  };
  const openNow = event => {
    cancelTimers();
    markState('');
    if (anchor.isConnected === false || !canOpen(event)) return;
    if (event && !stillActive(event)) return;
    if (stateClass && anchor.classList.contains(stateClass) && stillActive(event)) {
      options.position?.(event);
      options.onOpen?.(event);
      markState('open');
      return;
    }
    options.position?.(event);
    options.closeOthers?.();
    options.onOpen?.(event);
    if (stateClass) anchor.classList.add(stateClass);
    markState('open');
    const activePopover = popover();
    if (activePopover && activePopover.dataset.hoverPopoverBound !== 'true') {
      bindPopoverHover(anchor, activePopover, {queueOpen, keepOpen: openNow, closeSoon});
      activePopover.dataset.hoverPopoverBound = 'true';
    }
  };
  function queueOpen(event) {
    hideTimer = clearTimer(hideTimer);
    markState('pending');
    if (anchor.isConnected === false || !canOpen(event)) {
      markState('');
      return;
    }
    if (stateClass && anchor.classList.contains(stateClass)) return;
    showTimer = clearTimer(showTimer);
    options.onQueue?.(event);
    const delay = numericOption(options.showDelay, popoverShowDelayMs);
    showTimer = setTimeout(() => openNow(event), Math.max(0, delay));
  }
  function closeSoon(event) {
    showTimer = clearTimer(showTimer);
    hideTimer = clearTimer(hideTimer);
    markState('closing');
    const delay = numericOption(options.hideDelay, popoverHideDelayMs);
    hideTimer = setTimeout(() => {
      if (anchor.isConnected === false) {
        markState('');
        hideTimer = null;
        if (stateClass) anchor.classList.remove(stateClass);
        options.onClose?.(event);
        return;
      }
      if (stillActive(event)) {
        markState('open');
        hideTimer = null;
        return;
      }
      if (stateClass) anchor.classList.remove(stateClass);
      markState('');
      hideTimer = null;
      options.onClose?.(event);
    }, Math.max(0, delay));
  }
  const initialPopover = popover();
  bindPopoverHover(anchor, initialPopover, {queueOpen, keepOpen: openNow, closeSoon});
  if (initialPopover) initialPopover.dataset.hoverPopoverBound = 'true';
  return {queueOpen, openNow, closeSoon, closeNow, cancelTimers};
}

function cssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, '\\$&');
}

// Tab buttons are also drag handles, so activation waits until pointer release proves it was a click.
function bindTabActivation(node, activate, options = {}) {
  let pointerCandidate = false;
  let pointerActivated = false;
  let dragged = false;
  let startX = 0;
  let startY = 0;
  const stop = event => {
    if (options.stopPropagation) event.stopPropagation();
  };
  const ignored = event => options.ignore?.(event) === true;
  const resetPointer = () => {
    pointerCandidate = false;
    dragged = false;
  };

  node.addEventListener('pointerdown', event => {
    if (event.button !== 0 || ignored(event)) return;
    pointerCandidate = true;
    pointerActivated = false;
    dragged = false;
    startX = event.clientX;
    startY = event.clientY;
  });
  node.addEventListener('pointerup', event => {
    if (!pointerCandidate || event.button !== 0 || ignored(event)) {
      resetPointer();
      return;
    }
    const moved = Math.abs(event.clientX - startX) > 4 || Math.abs(event.clientY - startY) > 4;
    const wasDragged = dragged;
    resetPointer();
    if (wasDragged || moved) return;
    event.preventDefault();
    stop(event);
    pointerActivated = true;
    activate(event);
  });
  node.addEventListener('click', event => {
    if (ignored(event)) return;
    if (pointerActivated) {
      pointerActivated = false;
      event.preventDefault();
      stop(event);
      return;
    }
    event.preventDefault();
    stop(event);
    activate(event);
  });
  node.addEventListener('dragstart', () => {
    dragged = true;
    pointerCandidate = false;
    pointerActivated = false;
  });
  node.addEventListener('dragend', resetPointer);
}

// Tabs, headers, and popovers all use these helpers so badge precedence stays consistent.
function metaJoin(parts) {
  return parts.filter(Boolean).join('<span class="meta-sep"> · </span>');
}

function sessionNumberNameHtml(session) {
  const label = sessionLabel(session);
  const name = String(session);
  const nameHtml = name && name !== label ? `<span class="session-button-name">${esc(name)}</span>` : '';
  return `<span class="session-button-number">${esc(label)}</span>${nameHtml}`;
}

function yoloMarkerHtml(session, auto, options = {}) {
  const payload = options.payload || autoApproveStates.get(session);
  const locked = !auto && (options.locked === true || (options.locked !== false && autoApproveEnabledElsewhere(payload)));
  if (!auto && !locked && options.enabledOnly !== false) return '';
  const classes = ['session-yolo-marker'];
  if (auto) classes.push('active');
  else if (locked) classes.push('locked');
  else classes.push('inactive');
  if (options.yoloWorking) classes.push('working');
  if (readOnlyMode) classes.push('readonly');
  const yoloAttr = ` data-yolo-session="${esc(session)}"`;
  const toggleAttr = options.toggle && !readOnlyMode ? ` data-auto-session="${esc(session)}"` : '';
  const rotationStyle = options.yoloWorking ? ` style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}"` : '';
  const stateText = auto ? t('yolo.state.onHere') : (locked ? t('yolo.state.onElsewhere') : t('yolo.state.off'));
  const title = options.toggle && readOnlyMode
    ? t('yolo.titleReadonly', {state: stateText, session: sessionLabel(session)})
    : (options.toggle ? t('yolo.titleForSession', {state: stateText, session: sessionLabel(session)}) : t('yolo.title', {state: stateText}));
  return `<span class="${esc(classes.join(' '))}"${yoloAttr}${toggleAttr}${rotationStyle} title="${esc(title)}">${esc(t('brand.marker'))}</span>`;
}

function pullRequestCompactBadgesHtml(session, pr) {
  const numberHtml = pullRequestNumberIndicatorHtml(session, pr);
  const statusHtml = pullRequestStatusIndicatorHtml(session, pr);
  const ciHtml = pullRequestCiIndicatorHtml(session, pr);
  const reviewHtml = pullRequestApprovalIndicatorHtml(session, pr);
  return [numberHtml, statusHtml, ciHtml, reviewHtml].filter(Boolean).join('');
}

function applySessionStateClasses(node, state) {
  node.classList.toggle('needs-attention', state?.attention === true);
  node.classList.toggle('needs-input', state?.key === 'needs-input');
  node.classList.toggle('needs-exec', state?.key === 'needs-approval');
  node.classList.toggle('needs-blocked', state?.key === 'blocked');
  syncAttentionAnimation(node, state?.attention === true);
}

function panelHeaderStateHtml(state) {
  return state ? sessionStateHtml(state) : '';
}

function currentBranchSubject(git) {
  const branches = git?.other_branches?.branches || [];
  const current = branches.find(branch => branch.current);
  return current?.subject || '';
}

function isDefaultBranch(git) {
  return ['main', 'master'].includes(String(git?.branch || ''));
}

function gitHeadSubject(git) {
  return String(git?.head || '').replace(/^[0-9a-f]{7,40}\s+/, '');
}

function pullRequestNumberFromSubject(subject) {
  const match = String(subject || '').match(/\(#(\d+)\)\s*$/);
  return match ? Number(match[1]) : null;
}

function subjectWithoutPullRequestNumber(subject) {
  return String(subject || '').replace(/\s*\(#\d+\)\s*$/, '').trim();
}

function githubPullRequestUrlFromGit(git, number) {
  const repoUrl = git?.github_repo?.url;
  return repoUrl && number ? `${repoUrl}/pull/${number}` : '';
}

function defaultBranchHeadPullRequest(info) {
  const project = info?.project || {};
  const git = project.git;
  if (!isDefaultBranch(git)) return null;
  const subject = gitHeadSubject(git);
  const number = pullRequestNumberFromSubject(subject);
  if (!number) return null;
  const existing = project.pull_request?.number === number ? project.pull_request : {};
  const title = subjectWithoutPullRequestNumber(existing.title || subject);
  const description = subjectWithoutPullRequestNumber(existing.description || subject);
  return {
    ...existing,
    number,
    title,
    description,
    url: existing.url || githubPullRequestUrlFromGit(git, number),
    checks: existing.checks || {state: 'unknown'},
    // A (#NNNN) in the default branch's HEAD merge commit means that PR is, by definition, merged
    // (it is in main's history) — so label it MERGED even though we only inferred it from the subject.
    status_label: existing.status_label || 'merged',
    source_only: true,
  };
}

function displayPullRequest(info) {
  return defaultBranchHeadPullRequest(info) || info?.project?.pull_request || null;
}

function metadataBadgeKey(session, badge) {
  return `${session}:${badge}`;
}

function metadataBadgePulseClass(session, badge) {
  if (!session) return '';
  const until = metadataBadgePulseUntil.get(metadataBadgeKey(session, badge));
  if (!until || until <= Date.now()) return '';
  return ' metadata-pulse';
}

function metadataBadgeClasses(session, badge, classes) {
  return `${classes}${metadataBadgePulseClass(session, badge)}`;
}

function updateMetadataBadgePulses(meta) {
  const now = Date.now();
  for (const [key, until] of metadataBadgePulseUntil.entries()) {
    if (until <= now) metadataBadgePulseUntil.delete(key);
  }
  for (const [session, info] of Object.entries(meta?.sessions || {})) {
    const pulses = info?.metadata_badge_pulse_remaining_ms || {};
    for (const badge of ['main', 'pr', 'status', 'ci']) {
      const remaining = Number(pulses[badge] || 0);
      if (remaining > 0) {
        metadataBadgePulseUntil.set(metadataBadgeKey(session, badge), now + remaining);
      }
    }
  }
}

function defaultBranchBadgeHtml(session, info) {
  if (!isDefaultBranch(info?.project?.git)) return '';
  return `<span class="${metadataBadgeClasses(session, 'main', 'ci-indicator tab-symbol branch-indicator')}">MAIN</span>`;
}

function sessionWorkDescription(session, info, limit = 96) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const title = pr.title || pr.description || '';
    const prefix = pullRequestLinkLabel(pr);
    return shortText(title ? `${prefix}: ${title}` : prefix, limit);
  }
  const linear = project.linear || [];
  const issue = linear.find(item => item.title);
  if (issue) return shortText(`${issue.identifier}: ${issue.title}`, limit);
  const subject = currentBranchSubject(git);
  if (subject) return shortText(subject, limit);
  if (git?.branch) return shortText(shortBranch(git.branch), limit);
  return shortText(projectDirName(session, info), limit);
}

function sessionTabDescription(session, info) {
  const pr = displayPullRequest(info);
  if (pr?.number) {
    const title = pr.title || pr.description || '';
    if (title) return shortText(title, 72);
  }
  return sessionWorkDescription(session, info, 72);
}

function tabMenuDetailText(item, info = transcriptMeta.sessions?.[item]) {
  if (isInfoItem(item)) return t('tab.info.detail');
  const project = info?.project || {};
  const git = project.git;
  const parts = [];
  if (git?.branch) parts.push(git.branch);
  const path = panelFullPath(item, info);
  if (path) parts.push(compactHomePath(path));
  const pr = displayPullRequest(info);
  const linear = (project.linear || []).map(issue => issue.identifier).filter(Boolean).join(', ');
  if (linear) parts.push(linear);
  if (pr?.number) {
    parts.push(pullRequestLinkLabel(pr));
  }
  const desc = sessionWorkDescription(item, info, 180);
  if (desc && !parts.includes(desc)) parts.push(desc);
  return parts.join(' · ') || itemLabel(item);
}

function projectDirName(session, info) {
  if (!info) return t('common.loading');
  const {gitRoot, gitCwd, selectedPath} = sessionTranscriptInfo(session);
  const path = gitRoot || gitCwd || selectedPath;
  return pathBasename(path) || 'no path';
}

function pathBasename(path) {
  const text = String(path || '').replace(/\/+$/, '');
  if (!text) return '';
  const parts = text.split('/');
  return parts[parts.length - 1] || '';
}

function filePopoverHtml(item) {
  const path = fileItemPath(item);
  const state = openFiles.get(path) || {};
  const rows = filePopoverRows(path, state);
  return `<div class="session-popover file-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(basenameOf(path))}</div>
      </div>
    </div>
    ${rows.join('')}
  </div>`;
}

function filePopoverRows(path, state = {}) {
  const kind = state.kind === 'image' ? t('popover.kind.image') : state.kind === 'text' ? t('popover.kind.text') : state.kind || t('popover.kind.file');
  const status = state.dirty ? t('filetab.modified') : state.loading ? t('common.loading') : state.error ? String(state.error) : kind;
  const rows = [
    popoverRow(t('popover.path'), filePopoverPathHtml(path)),
  ];
  if (status && status !== kind) rows.push(popoverPairRow(t('popover.type'), esc(kind), t('popover.status'), esc(status)));
  else rows.push(popoverRow(t('popover.type'), esc(kind)));
  if (Number.isFinite(state.size)) rows.push(popoverRow(t('popover.size'), formatFileSize(state.size)));
  return rows;
}

function pathCopyButtonHtml(path, options = {}) {
  const className = ['path-copy-button', options.className || ''].filter(Boolean).join(' ');
  const dataAttr = options.dataAttr || 'data-copy-path';
  const title = options.title || 'Copy path';
  return `<button type="button" class="${esc(className)}" ${dataAttr}="${esc(path)}" title="${esc(title)}" aria-label="${esc(options.ariaLabel || title)}"></button>`;
}

function filePopoverPathHtml(path) {
  return `<span class="popover-copy-value">${esc(path)}</span>${pathCopyButtonHtml(path, {className: 'popover-copy-button', dataAttr: 'data-copy-popover-path'})}`;
}

function sessionPopoverSubtitleHtml(session, info, fallback = '') {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  const chips = [];
  if (isDefaultBranch(git)) chips.push(defaultBranchBadgeHtml(session, info));
  if (pr?.number) chips.push(pullRequestNumberIndicatorHtml(session, pr));
  const text = pr?.number
    ? shortText(pr.title || pr.description || '', 220)
    : String(fallback || '');
  const textHtml = text ? `<span class="popover-subtitle-text">${esc(text)}</span>` : '';
  return `<div class="popover-subtitle">${chips.join('')}${textHtml}</div>`;
}

function sessionBranchValueHtml(session, info) {
  const git = info?.project?.git;
  if (!git?.branch) return '';
  const branchHtml = isDefaultBranch(git)
    ? defaultBranchBadgeHtml(session, info)
    : branchLinkHtml(git, git.branch);
  return `${branchHtml}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`;
}

function pullRequestPopoverRowHtml(session, pr) {
  const prParts = [pullRequestNumberIndicatorHtml(session, pr), pullRequestAuthorHtml(pr)].filter(Boolean);
  const checks = pullRequestChecksHtml(pr);
  if (checks) prParts.push(checks);
  const review = pullRequestReviewInlineHtml(pr);
  if (review) prParts.push(review);
  return metaJoin(prParts);
}

function gitStatusHasFacts(git) {
  return Number.isFinite(git?.dirty_count) || Number.isFinite(git?.ahead) || Number.isFinite(git?.behind);
}

function popoverActivityText(session, git) {
  const text = String(sessionActivitySummary(session)?.local || '').trim();
  if (!text) return '';
  return gitStatusHasFacts(git)
    ? text.replace(/\s*Status check:\s*[^.]+\.?\s*$/i, '').trim()
    : text;
}

function gitHeadValueHtml(git) {
  const head = String(git?.head || '').trim();
  const match = head.match(/^([0-9a-f]{7,40})\b/i);
  if (match) return esc(match[1]);
  return esc(shortText(subjectWithoutPullRequestNumber(gitHeadSubject(git)), 120));
}

function sessionPopoverHtml(session, info, agentKind, autoEnabled, state = sessionState(session, info)) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  const linear = project.linear || [];
  const pane = info?.selected_pane;
  const description = sessionWorkDescription(session, info, 220);
  const title = `${sessionLabel(session)} · ${projectDirName(session, info)}`;
  const subtitle = description || git?.branch || pane?.current_path || t('git.noCheckout');
  const subtitleHtml = sessionPopoverSubtitleHtml(session, info, subtitle);
  const rows = [];
  const stateValue = `${sessionStateHtml(state)} <span class="meta-muted">${esc(state.reason)}</span>`;
  const autoPayload = autoApproveStates.get(session);
  const autoElsewhere = autoApproveEnabledElsewhere(autoPayload);
  const autoText = autoEnabled ? t('yolo.on') : (autoElsewhere ? t('yolo.elsewhere') : '');
  const agentValue = agentKind ? `${agentName(agentKind)}${autoText ? ` · ${autoText}` : ''}` : (autoText || t('agent.notDetected'));
  const displayPath = panelFullPath(session, info) || pane?.current_path || t('common.notAvailable');
  rows.push(popoverPairRow(t('popover.state'), stateValue, t('popover.agent'), agentValue));
  const activityText = popoverActivityText(session, git);
  if (activityText) rows.push(popoverRow(yoagentTabLabel(), esc(activityText)));
  rows.push(popoverRow(t('popover.path'), displayPath));
  if (git?.branch) rows.push(popoverRow(t('popover.branch'), sessionBranchValueHtml(session, info)));
  let linearValue = '';
  let linearDesc = '';
  if (linear.length) {
    linearValue = linearInlineHtml(linear);
    linearDesc = linearDescriptionsInlineHtml(linear);
    if (linearValue) rows.push(popoverRow('Linear', linearValue));
    if (linearDesc) rows.push(popoverRow(t('popover.details'), linearDesc));
  }
  if (pr?.number) {
    rows.push(popoverRow('PR', pullRequestPopoverRowHtml(session, pr)));
  }
  const subject = currentBranchSubject(git);
  if (subject && !pr?.number) rows.push(popoverRow(t('popover.desc'), `<div class="popover-desc">${esc(subject)}</div>`));
  if (git?.root && git.root !== displayPath) rows.push(popoverRow(t('popover.repo'), git.root));
  // S7: name a linked worktree vs its parent repo so the focused path isn't mistaken for the main checkout.
  if (git?.worktree) rows.push(popoverRow(t('popover.worktree'), `${esc(git.worktree.name || git.worktree.path)} — worktree of ${esc(git.worktree.parent_root)}`));
  if (git?.head) rows.push(popoverRow('HEAD', gitHeadValueHtml(git)));
  if (gitStatusHasFacts(git)) rows.push(popoverRow(t('popover.git'), gitStatusText(git)));
  return `<div class="session-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(title)}</div>
        ${subtitleHtml}
      </div>
    </div>
    ${rows.join('')}
    ${otherBranchesHtml(session, info)}
  </div>`;
}

function popoverRow(label, valueHtml) {
  return `<div class="popover-row"><div class="popover-label">${esc(label)}</div><div class="popover-value">${stripTitleAttrs(valueHtml)}</div></div>`;
}

function popoverPairRow(leftLabel, leftValueHtml, rightLabel, rightValueHtml) {
  return `<div class="popover-row compact">
    <div class="popover-label">${esc(leftLabel)}</div><div class="popover-value">${stripTitleAttrs(leftValueHtml)}</div>
    <div class="popover-label">${esc(rightLabel)}</div><div class="popover-value">${stripTitleAttrs(rightValueHtml)}</div>
  </div>`;
}

function stripTitleAttrs(html) {
  return String(html || '').replace(/\s+title="[^"]*"/g, '');
}

function linearInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    const label = issue.identifier || '';
    if (!label) continue;
    const link = linkHtml(issue.url, label, issue.title || '');
    if (!link) continue;
    const state = issue.state ? `<span class="meta-muted"> ${esc(issue.state)}</span>` : '';
    parts.push(`${link}${state}`);
  }
  return metaJoin(parts);
}

function linearDescriptionsInlineHtml(issues) {
  const parts = [];
  for (const issue of issues || []) {
    if (!issue?.title) continue;
    const prefix = issue.identifier ? `${issue.identifier} ` : '';
    parts.push(`${prefix}${issue.title}`);
  }
  return parts.length ? esc(shortText(parts.join(' · '), 180)) : '';
}

function gitStatusText(git) {
  const parts = [];
  if (Number.isFinite(git.dirty_count)) parts.push(t('git.dirty', {count: git.dirty_count}));
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(t('git.ahead', {count: git.ahead}));
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(t('git.behind', {count: git.behind}));
  return esc(parts.length ? parts.join(' · ') : t('git.clean'));
}

function branchLinkHtml(git, branchName) {
  return esc(branchName || '');
}

function linearIssueHtml(issue) {
  const label = `${issue.identifier}${issue.state ? ` ${issue.state}` : ''}`;
  return linkHtml(issue.url, label, issue.title || '');
}

function linearIssueLinkHtml(identifier) {
  if (!identifier) return '';
  return linkHtml(`https://linear.app/nv/issue/${encodeURIComponent(identifier)}`, identifier, identifier);
}

function pullRequestLinkForBranch(git, branch) {
  const pr = branch?.pull_request;
  const repoUrl = git?.github_repo?.url;
  if (!pr?.number) return '';
  const url = pr.url || (repoUrl ? `${repoUrl}/pull/${pr.number}` : '');
  return linkHtml(url, pullRequestLinkLabel(pr), pr.title || pr.description || branch.subject || '', pullRequestStatusClass(pr));
}

function pullRequestNumberChipLinkHtml(session, pr) {
  if (!pr?.number) return '';
  const chip = pullRequestNumberIndicatorHtml(session, pr);
  if (!pr.url) return chip;
  const title = pr.title || pr.description || '';
  const titleAttr = title ? ` title="${esc(title)}"` : '';
  return `<a href="${esc(pr.url)}" target="_blank" rel="noreferrer noopener" draggable="false"${titleAttr} class="popover-chip-link">${chip}</a>`;
}

function pullRequestForBranch(git, branch, info) {
  const pr = branch?.current ? displayPullRequest(info) || branch.pull_request : branch?.pull_request;
  if (!pr?.number) return null;
  return {
    ...pr,
    url: pr.url || githubPullRequestUrlFromGit(git, pr.number),
  };
}

function branchListBranchHtml(session, git, branch) {
  const branchName = branch?.name || '';
  if (isDefaultBranch({branch: branchName})) {
    const classes = branch?.current
      ? metadataBadgeClasses(session, 'main', 'ci-indicator tab-symbol branch-indicator')
      : 'ci-indicator tab-symbol branch-indicator';
    return `<span class="${esc(classes)}">MAIN</span>`;
  }
  return branchLinkHtml(git, branchName);
}

function normalizeBranchSubjectText(value) {
  return subjectWithoutPullRequestNumber(value).replace(/\s+/g, ' ').trim().toLowerCase();
}

function branchListSubjectHtml(branch, pr) {
  const subject = subjectWithoutPullRequestNumber(branch?.subject || '');
  if (!subject) return '';
  if (branch?.current) {
    const currentTitles = [pr?.title, pr?.description].map(normalizeBranchSubjectText).filter(Boolean);
    if (currentTitles.includes(normalizeBranchSubjectText(subject))) return '';
  }
  return `<div class="branch-subject">${esc(shortText(subject, 240))}</div>`;
}

function branchPullRequestMetaHtml(session, pr) {
  if (!pr?.number) return '';
  const parts = [pullRequestNumberChipLinkHtml(session, pr)];
  const status = pullRequestInlineStatusDisplay(pr);
  if (status) parts.push(`<span class="meta-pr-status ${esc(pullRequestStatusClass(pr))}">${esc(status)}</span>`);
  return metaJoin(parts);
}

function pullRequestTextForBranch(pr, fallback = '') {
  if (!pr?.number) return '';
  return [pullRequestLinkLabel(pr), pr.title || pr.description || fallback].filter(Boolean).join(' ');
}

function branchUpdatedText(branch) {
  const ts = Number(branch?.updated_ts || 0);
  if (Number.isFinite(ts) && ts > 0) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    return relativeTimeFormat(seconds);
  }
  return branch?.updated || '';
}

function otherBranchesHtml(session, info) {
  const git = info?.project?.git;
  const inventory = git?.other_branches || {};
  const branches = inventory.branches || [];
  if (!branches.length) {
    return `<div class="branch-list"><div class="branch-list-title">${esc(t('branch.all'))}</div><div class="meta-muted">${esc(t('branch.none'))}</div></div>`;
  }
  const items = branches.map(branch => {
    const pr = pullRequestForBranch(git, branch, info);
    const branchLink = branchListBranchHtml(session, git, branch);
    const prLink = branchPullRequestMetaHtml(session, pr);
    const linearLinks = (branch.linear_ids || []).map(linearIssueLinkHtml).filter(Boolean).join(' ');
    const meta = [prLink, linearLinks, esc(branchUpdatedText(branch))].filter(Boolean).join(' ');
    return `<div class="branch-item">
      <div class="branch-name">${branchLink}</div>
      <div class="branch-meta">${meta}</div>
      ${branchListSubjectHtml(branch, pr)}
    </div>`;
  }).join('');
  const hidden = Number(inventory.hidden_count || 0) > 0
    ? `<div class="meta-muted">${esc(t('branch.more', {count: inventory.hidden_count}))}</div>`
    : '';
  return `<div class="branch-list"><div class="branch-list-title">${esc(t('branch.all'))}</div>${items}${hidden}</div>`;
}

function dragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-session') || '';
  if (!raw && dragPaneSlot) return null;
  if (!raw && dragSession) return {session: dragSession, sourceSlot: dragSourceSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isLayoutItem(parsed.session) ? parsed : null;
  } catch (_) {
    return isLayoutItem(raw) ? {session: raw, sourceSlot: null} : null;
  }
}

function paneDragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-pane') || '';
  if (!raw && dragPaneSlot) return {slot: dragPaneSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return layoutSlotKeys().includes(parsed.slot) ? parsed : null;
  } catch (_) {
    return layoutSlotKeys().includes(raw) ? {slot: raw} : null;
  }
}

function normalizeFileDragPayload(parsed) {
  if (!parsed?.path && !Array.isArray(parsed?.paths)) return null;
  const paths = Array.isArray(parsed.paths) ? parsed.paths.filter(Boolean) : [parsed.path].filter(Boolean);
  return paths.length ? {...parsed, path: parsed.path || paths[0], paths} : null;
}

function parseFileDragPayload(raw) {
  if (!raw) return null;
  try {
    return normalizeFileDragPayload(JSON.parse(raw));
  } catch (_) {
    return null;
  }
}

function hasYolomuxFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes('application/x-yolomux-file');
}

function fileDragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-file') || '';
  return parseFileDragPayload(raw) || (hasYolomuxFileDrag(event) ? dragFilePayloadState : null);
}

async function openDraggedFilesInEditor(payload, options = {}) {
  const paths = Array.from(new Set((payload?.paths || [payload?.path]).filter(Boolean)));
  if (!paths.length) return;
  let opened = 0;
  for (const [index, path] of paths.entries()) {
    try {
      const info = await fetchFilePathInfo(path);
      if (info.kind !== 'file') continue;
      await openFileInEditor(path, info, {
        forceNewTab: true,
        targetSlot: options.targetSlot || null,
        targetZone: options.targetZone || 'middle',
        targetIndex: options.targetIndex == null ? null : Number(options.targetIndex) + index,
      });
      // Unify with double-click: a dragged CHANGED (tracked, has-diff) file opens in the SAME diff
      // view (ensureCodeMirrorDiffPanel) as openChangedFileInDiff, not a plain edit-mode editor.
      // Unchanged/untracked files have no diff available and stay in edit.
      await refreshOpenFileDiff(path, {silent: true});
      const draggedState = openFiles.get(path);
      if (draggedState && openFileDiffAvailable(draggedState)) {
        setFileEditorViewMode(path, 'diff', fileEditorItemFor(path));
        for (const draggedPanel of fileEditorPanelsForPath(path)) {
          renderFileEditorPanel(draggedPanel, draggedPanel.dataset.layoutItem || fileEditorItemFor(path));
        }
      }
      // #260: a drag-drop open is a FRESH open at the current disk state, not a "changed on disk"
      // conflict. If the just-opened file is NOT dirty, clear any external-change flags so it never
      // pops a spurious reload prompt (this matches double-click's openChangedFileInDiff, which opens
      // with a clean baseline). A dirty file keeps its conflict state so real unsaved-edit warnings stay.
      if (draggedState && !draggedState.dirty) {
        delete draggedState.externalChanged;
        delete draggedState.externalMissing;
        delete draggedState.externalError;
        delete draggedState.externalChangeEditPrompted;
        renderOpenFilePath(path);
      }
      opened += 1;
    } catch (error) {
      showFileOpenError(path, error);
    }
  }
  if (opened) statusOk(`opened ${esc(opened === 1 ? basenameOf(paths[0]) : `${opened} files`)}`);
}

function terminalCurrentPath(session) {
  const info = transcriptMeta.sessions?.[session];
  return terminalDisplayPane(info)?.current_path || info?.selected_pane?.current_path || '';
}

function pathRelativeToDirectory(path, directory) {
  const fullPath = String(path || '');
  const rawBase = String(directory || '');
  const base = rawBase === '/' ? '/' : rawBase.replace(/\/+$/, '');
  if (!fullPath || !base || !fullPath.startsWith('/')) return fullPath;
  if (fullPath === base) return '.';
  if (base === '/') return fullPath.slice(1);
  if (!fullPath.startsWith(`${base}/`)) return fullPath;
  return fullPath.slice(base.length + 1);
}

function terminalFileReference(session, path) {
  return pathRelativeToDirectory(path, terminalCurrentPath(session));
}

function terminalFileReferences(session, payload) {
  const paths = Array.isArray(payload?.paths) ? payload.paths : [payload?.path].filter(Boolean);
  return paths.map(path => terminalFileReference(session, path));
}

function transparentNativeDragImage() {
  if (transparentDragImage) return transparentDragImage;
  const node = document.createElement('div');
  node.className = 'transparent-drag-image';
  node.style.position = 'fixed';
  node.style.left = '-10000px';
  node.style.top = '-10000px';
  node.style.width = '1px';
  node.style.height = '1px';
  node.style.opacity = '0';
  node.style.pointerEvents = 'none';
  document.body.appendChild(node);
  transparentDragImage = node;
  return node;
}

function moveCustomDragPreview(event) {
  if (!customDragPreview || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return;
  customDragPreview.style.left = `${Math.round(event.clientX - customDragPreviewOffset.x)}px`;
  customDragPreview.style.top = `${Math.round(event.clientY - customDragPreviewOffset.y)}px`;
}

function bindCustomDragPreviewListeners() {
  document.addEventListener?.('dragover', moveCustomDragPreview, true);
  document.addEventListener?.('drag', moveCustomDragPreview, true);
  document.addEventListener?.('drop', stopCustomDragPreview, true);
  document.addEventListener?.('dragend', stopCustomDragPreview, true);
}

function unbindCustomDragPreviewListeners() {
  document.removeEventListener?.('dragover', moveCustomDragPreview, true);
  document.removeEventListener?.('drag', moveCustomDragPreview, true);
  document.removeEventListener?.('drop', stopCustomDragPreview, true);
  document.removeEventListener?.('dragend', stopCustomDragPreview, true);
}

function stopCustomDragPreview() {
  unbindCustomDragPreviewListeners();
  customDragPreview?.remove();
  customDragPreview = null;
  closeFileImagePreview();
}

function paneDragPreviewMetrics(slot, event) {
  const rect = layoutSlotScreenRect(slot);
  const fallbackWidth = 360;
  const fallbackHeight = 220;
  const sourceWidth = Math.max(1, Number(rect?.width) || fallbackWidth);
  const sourceHeight = Math.max(1, Number(rect?.height) || fallbackHeight);
  const viewportWidth = Math.max(320, Number(window.innerWidth) || 1200);
  const viewportHeight = Math.max(240, Number(window.innerHeight) || 800);
  const maxWidth = Math.min(720, Math.max(220, viewportWidth * 0.64));
  const maxHeight = Math.min(420, Math.max(160, viewportHeight * 0.58));
  const scale = Math.min(1, maxWidth / sourceWidth, maxHeight / sourceHeight);
  const width = Math.max(180, Math.round(sourceWidth * scale));
  const height = Math.max(120, Math.round(sourceHeight * scale));
  const sourceOffsetX = rect ? (Number(event?.clientX) || rect.left) - rect.left : width * 0.12;
  const sourceOffsetY = rect ? (Number(event?.clientY) || rect.top) - rect.top : 18;
  return {
    width,
    height,
    offsetX: Math.max(16, Math.min(width - 16, Math.round(sourceOffsetX * scale))),
    offsetY: Math.max(16, Math.min(height - 16, Math.round(sourceOffsetY * scale))),
  };
}

function paneDragPreviewHtml(slot) {
  const tabs = paneTabs(slot);
  const active = activeItemForSide(slot) || tabs[0] || slot;
  const title = itemLabel(active);
  const count = tabs.length;
  const extra = tabs
    .filter(item => item !== active)
    .slice(0, 3)
    .map(item => `<span>${esc(itemLabel(item))}</span>`)
    .join('');
  return `
    <div class="pane-drag-image-frame">
      <div class="pane-drag-image-title">${esc(title)}</div>
      <div class="pane-drag-image-meta">${esc(count === 1 ? '1 tab' : `${count} tabs`)}</div>
      ${extra ? `<div class="pane-drag-image-tabs">${extra}</div>` : ''}
    </div>`;
}

function startPaneDragPreview(event, slot, options = {}) {
  stopCustomDragPreview();
  const metrics = paneDragPreviewMetrics(slot, event);
  const preview = document.createElement('div');
  preview.className = 'pane-drag-image drag-image';
  preview.dataset.dragSlot = slot;
  preview.innerHTML = paneDragPreviewHtml(slot);
  preview.style.position = 'fixed';
  preview.style.pointerEvents = 'none';
  preview.style.zIndex = '99999';
  preview.style.width = `${metrics.width}px`;
  preview.style.height = `${metrics.height}px`;
  document.body.appendChild(preview);
  customDragPreview = preview;
  customDragPreviewOffset = {x: metrics.offsetX, y: metrics.offsetY};
  moveCustomDragPreview(event);
  if (options.nativeDrag === true) {
    bindCustomDragPreviewListeners();
    preview.getBoundingClientRect();
    event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
  }
}

// #47: tab drags use the native drag image (see startSessionDrag) — the clone-follow tab preview is
// gone. The custom-preview machinery below is retained only for the rich FILE drag preview.
function startFileDragPreview(event, paths, entry) {
  stopCustomDragPreview();
  const normalizedPaths = Array.from(new Set((paths || []).filter(Boolean)));
  const firstPath = normalizedPaths[0] || '';
  const preview = document.createElement('div');
  preview.className = 'file-drag-image drag-image';
  const title = normalizedPaths.length === 1 ? basenameOf(firstPath) : `${normalizedPaths.length} items`;
  const pathRows = normalizedPaths.slice(0, 4)
    .map(path => `<div class="file-drag-path">${esc(path)}</div>`)
    .join('');
  const more = normalizedPaths.length > 4 ? `<div class="file-drag-more">+ ${normalizedPaths.length - 4} more</div>` : '';
  preview.innerHTML = `
    <div class="file-drag-main">
      ${fileDragPreviewMedia(firstPath, entry)}
      <div class="file-drag-copy">
        <div class="file-drag-title">${esc(title)}</div>
        ${pathRows}${more}
      </div>
    </div>`;
  preview.style.position = 'fixed';
  preview.style.pointerEvents = 'none';
  preview.style.zIndex = '99999';
  document.body.appendChild(preview);
  customDragPreview = preview;
  customDragPreviewOffset = {x: -14, y: -14};
  moveCustomDragPreview(event);
  bindCustomDragPreviewListeners();
  preview.getBoundingClientRect();
  event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
}

function fileDragPreviewMedia(path, entry) {
  const kind = entry?.kind || 'file';
  if (kind === 'file' && IMAGE_EXTENSIONS.has(fileExtensionOf(path))) {
    return `<img class="file-drag-thumb" src="${rawFileUrl(path)}" alt="">`;
  }
  const icon = kind === 'dir' ? '▸' : '📄';
  return `<span class="file-drag-thumb file-drag-icon" aria-hidden="true">${icon}</span>`;
}

// S14: OPT-IN tab-drag timing to diagnose the ~500ms first-drag delay without guessing. Off by
// default (no permanent user-visible perf log). Enable by setting storage key 'yolomux.debugDragTiming' to
// '1' (via storageSet in the console), drag a tab, then read the per-bucket console.table at drop. Marks
// the buckets the DOIT calls out: pointerdown -> dragstart/startSessionDrag (begin/end) -> first dragover
// -> first dragMeasureStrip / paneTabDropPlacement. dragTimingMarkOnce dedups the repeating measure calls.
let dragTimingMarks = null;
const dragTimingSeen = new Set();
function dragTimingEnabled() {
  return storageGet('yolomux.debugDragTiming') === '1';
}
function dragTimingReset() {
  dragTimingSeen.clear();
  dragTimingMarks = dragTimingEnabled() ? [] : null;
}
function dragTimingMark(label) {
  if (dragTimingMarks) dragTimingMarks.push({label, t: performance.now()});
}
function dragTimingMarkOnce(label) {
  if (dragTimingMarks && !dragTimingSeen.has(label)) { dragTimingSeen.add(label); dragTimingMark(label); }
}
function dragTimingReport() {
  if (dragTimingMarks && dragTimingMarks.length >= 2) {
    const first = dragTimingMarks[0].t;
    const rows = dragTimingMarks.map((mark, i) => ({
      mark: mark.label,
      deltaMs: i ? Number((mark.t - dragTimingMarks[i - 1].t).toFixed(1)) : 0,
      sinceStartMs: Number((mark.t - first).toFixed(1)),
    }));
    console.table(rows);
    showDragTimingOverlay(rows);   // copyable on-page readout — no DevTools needed
  }
  dragTimingMarks = null;
  dragTimingSeen.clear();
}

// S14: render the last drag's timing into a fixed, click-to-select-all box so it can be
// copy-pasted (or screenshotted) back without opening DevTools. Created lazily; only the flag-gated
// dragTimingReport calls it, so it never appears in normal use.
function showDragTimingOverlay(rows) {
  let el = document.getElementById('drag-timing-overlay');
  if (!el) {
    el = document.createElement('pre');
    el.id = 'drag-timing-overlay';
    el.className = 'drag-timing-overlay';
    el.title = 'drag timing (flag: yolomux.debugDragTiming) — click to select, then copy';
    el.addEventListener('click', () => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    });
    document.body.appendChild(el);
  }
  const width = Math.max(...rows.map(row => row.mark.length));
  el.textContent = ['drag timing (ms) — click to select, copy, paste back:',
    ...rows.map(row => `${row.mark.padEnd(width)}  +${String(row.deltaMs).padStart(7)}  total ${String(row.sinceStartMs).padStart(7)}`)].join('\n');
}

function startSessionDrag(event, session, sourceSlot = null) {
  dragTimingMark('startSessionDrag:begin');
  dragSession = session;
  dragSourceSlot = sourceSlot;
  dragPaneSlot = null;
  const payload = JSON.stringify({session, sourceSlot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-session', payload);
  event.dataTransfer.setData('text/plain', session);
  // #47: use the NATIVE drag image — a one-time compositor snapshot of the tab itself — instead of the
  // JS clone-follow preview. That removes the per-move reposition, the two document capture listeners,
  // and the animated heavyweight clone that caused the "won't budge" first drag and the per-move jank.
  // C12 F2: take the grab offset from event.offsetX/offsetY (already on the event) instead of
  // getBoundingClientRect(), which forced a synchronous layout reflow inside the handler — coldest on the
  // first drag after load — before the browser could start the drag.
  const source = event.currentTarget;
  if (source && event.dataTransfer?.setDragImage) {
    const offsetX = Math.max(0, Number(event.offsetX) || 0);
    const offsetY = Math.max(0, Number(event.offsetY) || 0);
    event.dataTransfer.setDragImage(source, offsetX, offsetY);
  }
  resetDragTabRectCache();
  dragTimingMark('startSessionDrag:end');
}

function startPaneDrag(event, sourceSlot) {
  const slot = layoutSlotKeys().includes(sourceSlot) ? sourceSlot : null;
  if (!slot || slotIsFileExplorerPane(slot)) {
    event.preventDefault?.();
    return;
  }
  const active = activeItemForSide(slot);
  if (!active) {
    event.preventDefault?.();
    return;
  }
  dragTimingMark('startPaneDrag');
  dragSession = active;
  dragSourceSlot = slot;
  dragPaneSlot = slot;
  const payload = JSON.stringify({slot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-pane', payload);
  event.dataTransfer.setData('text/plain', paneTabs(slot).map(itemLabel).join('\n'));
  startPaneDragPreview(event, slot, {nativeDrag: true});
}

function endSessionDrag(event) {
  dragSession = null;
  dragSourceSlot = null;
  dragPaneSlot = null;
  resetDragTabRectCache();
  stopCustomDragPreview();
  sessionButtons.classList.remove(CLS.dragOver);
  clearDropPreview();
  // flush any tab/preferences re-renders that were deferred during the drag.
  if (pendingTabStripRender) { pendingTabStripRender = false; renderPaneTabStrips(); }
  if (pendingPreferencesRender) { pendingPreferencesRender = false; renderPreferencesPanels(); }
  // flush through the shared layout render scheduler so same-shape drops keep the cheap path.
  flushPendingLayoutRender();
  dragTimingReport();
}
