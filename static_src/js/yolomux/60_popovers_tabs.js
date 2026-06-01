function toggleHiddenFiles() {
  fileExplorerShowHidden = !fileExplorerShowHidden;
  try { window.localStorage?.setItem(fileExplorerHiddenStorageKey, fileExplorerShowHidden ? '1' : '0'); }
  catch (_) {}
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  if (fileExplorerRoot) refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
}

async function expandDirectoryRow(row, fullPath) {
  const entries = await fetchDirectory(fullPath);
  if (!entries) return;
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
}

function collapseDirectoryRow(row, fullPath) {
  fileExplorerExpanded.delete(fullPath);
  row.classList.remove('expanded');
  row.setAttribute('aria-expanded', 'false');
  row.querySelector('.file-tree-icon').textContent = '▸';
  const next = row.nextElementSibling;
  if (next && next.classList.contains('file-tree-children') && next.dataset.parent === fullPath) {
    next.remove();
  }
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
}

function bindFilePopoverActions(container) {
  container.querySelectorAll('[data-copy-popover-path]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyFilePath(button.dataset.copyPopoverPath || '', 'full');
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
      const popover = other.querySelector?.(':scope > .session-popover, :scope > .panel-detail-popover');
      if (current === null && !force && popoverStillActive(other, popover)) continue;
      other.classList.remove('popover-open');
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
  if (auto && options.yoloWorking) classes.push('working');
  if (readOnlyMode) classes.push('readonly');
  const yoloAttr = ` data-yolo-session="${esc(session)}"`;
  const toggleAttr = options.toggle && !readOnlyMode ? ` data-auto-session="${esc(session)}"` : '';
  const rotationStyle = auto && options.yoloWorking ? ` style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}"` : '';
  const stateText = auto ? 'on here' : (locked ? 'on elsewhere' : 'off');
  const title = options.toggle && readOnlyMode
    ? `YOLO ${stateText} for ${sessionLabel(session)}; readonly access`
    : (options.toggle ? `YOLO ${stateText} for ${sessionLabel(session)}` : `YOLO ${stateText}`);
  return `<span class="${esc(classes.join(' '))}"${yoloAttr}${toggleAttr}${rotationStyle} title="${esc(title)}">YO</span>`;
}

function pullRequestCompactBadgesHtml(session, pr) {
  const statusHtml = pullRequestStatusIndicatorHtml(session, pr);
  const ciHtml = pullRequestCiIndicatorHtml(session, pr);
  return [statusHtml, ciHtml].filter(Boolean).join('');
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
    status_label: '',
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
    const status = pullRequestStatusLabel(pr);
    const title = pr.title || pr.description || '';
    const prefix = `#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`;
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
  if (isInfoItem(item)) return 'all branches sorted by recent activity';
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
    const status = pullRequestStatusLabel(pr);
    parts.push(`#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`);
  }
  const desc = sessionWorkDescription(item, info, 180);
  if (desc && !parts.includes(desc)) parts.push(desc);
  return parts.join(' · ') || itemLabel(item);
}

function projectDirName(session, info) {
  if (!info) return 'loading';
  const project = info?.project || {};
  const git = project.git;
  const path = git?.root || git?.cwd || info?.selected_pane?.current_path || '';
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
  const kind = state.kind === 'image' ? 'image viewer' : state.kind === 'text' ? 'file editor' : state.kind || 'file';
  const status = state.dirty ? 'modified' : state.loading ? 'loading' : state.error ? String(state.error) : kind;
  const rows = [
    popoverRow('path', filePopoverPathHtml(path)),
  ];
  if (status && status !== kind) rows.push(popoverPairRow('type', esc(kind), 'status', esc(status)));
  else rows.push(popoverRow('type', esc(kind)));
  if (Number.isFinite(state.size)) rows.push(popoverRow('size', formatFileSize(state.size)));
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

function sessionPopoverHtml(session, info, agentKind, autoEnabled, state = sessionState(session, info)) {
  const project = info?.project || {};
  const git = project.git;
  const pr = displayPullRequest(info);
  const linear = project.linear || [];
  const pane = info?.selected_pane;
  const description = sessionWorkDescription(session, info, 220);
  const title = `${sessionLabel(session)} · ${projectDirName(session, info)}`;
  const subtitle = description || git?.branch || pane?.current_path || 'no checkout detected';
  const rows = [];
  const stateValue = `${sessionStateHtml(state)} <span class="meta-muted">${esc(state.reason)}</span>`;
  const autoPayload = autoApproveStates.get(session);
  const autoElsewhere = autoApproveEnabledElsewhere(autoPayload);
  const autoText = autoEnabled ? 'YOLO on' : (autoElsewhere ? 'YOLO elsewhere' : '');
  const agentValue = agentKind ? `${agentName(agentKind)}${autoText ? ` · ${autoText}` : ''}` : (autoText || 'not detected');
  const displayPath = panelFullPath(session, info) || pane?.current_path || 'not available';
  rows.push(popoverPairRow('state', stateValue, 'agent', agentValue));
  const activity = sessionActivitySummary(session);
  if (activity?.local) rows.push(popoverRow(yoagentTabLabel, esc(activity.local)));
  rows.push(popoverRow('path', displayPath));
  if (git?.branch) rows.push(popoverRow('branch', `${branchLinkHtml(git, git.branch)}${git.upstream ? `<span class="meta-muted"> -> ${esc(git.upstream)}</span>` : ''}`));
  if (Number.isFinite(git?.dirty_count) || Number.isFinite(git?.ahead) || Number.isFinite(git?.behind)) {
    rows.push(popoverRow('git', gitStatusText(git)));
  }
  let linearValue = '';
  let linearDesc = '';
  if (linear.length) {
    linearValue = linearInlineHtml(linear);
    linearDesc = linearDescriptionsInlineHtml(linear);
    if (linearValue) rows.push(popoverRow('Linear', linearValue));
    if (linearDesc) rows.push(popoverRow('details', linearDesc));
  }
  let prDesc = '';
  if (pr?.number) {
    const prParts = [pullRequestLinkHtml(pr), pullRequestAuthorHtml(pr)].filter(Boolean);
    const checks = pullRequestChecksHtml(pr);
    if (checks) prParts.push(checks);
    rows.push(popoverRow('PR', metaJoin(prParts)));
    prDesc = pullRequestDescriptionInlineHtml(pr);
  }
  if (prDesc) {
    rows.push(popoverRow('desc', prDesc));
  }
  const subject = currentBranchSubject(git);
  if (subject && !pr?.number) rows.push(popoverRow('desc', `<div class="popover-desc">${esc(subject)}</div>`));
  if (git?.root && git.root !== displayPath) rows.push(popoverRow('repo', git.root));
  if (git?.head) rows.push(popoverRow('HEAD', git.head));
  return `<div class="session-popover" role="tooltip">
    <div class="popover-head">
      <div>
        <div class="popover-title">${esc(title)}</div>
        <div class="popover-subtitle">${esc(subtitle)}</div>
      </div>
    </div>
    ${rows.join('')}
    ${otherBranchesHtml(git)}
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

function pullRequestDescriptionInlineHtml(pr) {
  const title = String(pr?.title || '').trim();
  const description = String(pr?.description || '').trim();
  const body = description && description !== title ? description.replace(/^#+\s*Overview:\s*/i, '').trim() : '';
  const text = [title, body].filter(Boolean).join(' · ');
  return text ? esc(shortText(text, 180)) : '';
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
  if (Number.isFinite(git.dirty_count)) parts.push(`${git.dirty_count} dirty`);
  if (Number.isFinite(git.ahead) && git.ahead > 0) parts.push(`${git.ahead} ahead`);
  if (Number.isFinite(git.behind) && git.behind > 0) parts.push(`${git.behind} behind`);
  return esc(parts.length ? parts.join(' · ') : 'clean');
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
  const status = pullRequestStatusDisplay(pr);
  const label = `#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`;
  return linkHtml(url, label, pr.title || pr.description || branch.subject || '', pullRequestStatusClass(pr));
}

function pullRequestTextForBranch(pr, fallback = '') {
  if (!pr?.number) return '';
  const status = pullRequestStatusDisplay(pr);
  return [`#${pr.number}${status && status !== 'unknown' ? ` ${status}` : ''}`, pr.title || pr.description || fallback].filter(Boolean).join(' ');
}

function otherBranchesHtml(git) {
  const inventory = git?.other_branches || {};
  const branches = inventory.branches || [];
  if (!branches.length) {
    return `<div class="branch-list"><div class="branch-list-title">All branches</div><div class="meta-muted">none found in this checkout</div></div>`;
  }
  const items = branches.map(branch => {
    const branchLink = branchLinkHtml(git, branch.name);
    const prLink = pullRequestLinkForBranch(git, branch);
    const linearLinks = (branch.linear_ids || []).map(linearIssueLinkHtml).filter(Boolean).join(' ');
    const meta = [prLink, linearLinks, esc(branch.updated || '')].filter(Boolean).join(' ');
    return `<div class="branch-item">
      <div class="branch-name">${branch.current ? '<span class="info-branch-current">current</span> ' : ''}${branchLink}</div>
      <div class="branch-meta">${meta}</div>
      <div class="branch-subject">${esc(shortText(branch.subject || '', 240))}</div>
    </div>`;
  }).join('');
  const hidden = Number(inventory.hidden_count || 0) > 0
    ? `<div class="meta-muted">+ ${inventory.hidden_count} more</div>`
    : '';
  return `<div class="branch-list"><div class="branch-list-title">All branches</div>${items}${hidden}</div>`;
}

function dragPayload(event) {
  const raw = event.dataTransfer?.getData('application/x-yolomux-session') || '';
  if (!raw && dragSession) return {session: dragSession, sourceSlot: dragSourceSlot};
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return isLayoutItem(parsed.session) ? parsed : null;
  } catch (_) {
    return isLayoutItem(raw) ? {session: raw, sourceSlot: null} : null;
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
      opened += 1;
    } catch (error) {
      showFileOpenError(path, error);
    }
  }
  if (opened) statusEl.innerHTML = `<span class="ok">opened ${esc(opened === 1 ? basenameOf(paths[0]) : `${opened} files`)}</span>`;
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

function startCustomDragPreview(event) {
  const source = event.currentTarget;
  if (!source || !source.cloneNode) return;
  stopCustomDragPreview();
  const rect = source.getBoundingClientRect();
  const clone = source.cloneNode(true);
  clone.classList?.remove('popover-open', 'dragging');
  clone.classList?.add('drag-image');
  clone.querySelectorAll?.('.session-popover').forEach(node => node.remove());
  clone.style.position = 'fixed';
  clone.style.width = `${Math.max(1, Math.round(rect.width || 1))}px`;
  clone.style.height = `${Math.max(1, Math.round(rect.height || 1))}px`;
  clone.style.opacity = '0.50';
  clone.style.pointerEvents = 'none';
  clone.style.zIndex = '99999';
  document.body.appendChild(clone);
  customDragPreview = clone;
  const offsetX = Math.max(0, Math.min(rect.width || 0, event.clientX - rect.left)) || Math.max(1, (rect.width || 1) / 2);
  const offsetY = Math.max(0, Math.min(rect.height || 0, event.clientY - rect.top)) || Math.max(1, (rect.height || 1) / 2);
  customDragPreviewOffset = {x: offsetX, y: offsetY};
  moveCustomDragPreview(event);
  bindCustomDragPreviewListeners();
  event.dataTransfer?.setDragImage?.(transparentNativeDragImage(), 0, 0);
}

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

function startSessionDrag(event, session, sourceSlot = null) {
  dragSession = session;
  dragSourceSlot = sourceSlot;
  const payload = JSON.stringify({session, sourceSlot});
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('application/x-yolomux-session', payload);
  event.dataTransfer.setData('text/plain', session);
  startCustomDragPreview(event);
}

function endSessionDrag(event) {
  dragSession = null;
  dragSourceSlot = null;
  stopCustomDragPreview();
  sessionButtons.classList.remove('drag-over');
  clearDropPreview();
}
