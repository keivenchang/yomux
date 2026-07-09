// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// One global YO!chat timeline. SQLite and the authenticated API remain authoritative.

const chatBrowserInstanceId = sessionScopedId('yolomux.chat.browserInstance');
const chatReaderId = chatBrowserInstanceId;
const chatIntroductionGreetingKeys = Object.freeze([
  'chat.intro.greeting.hiThere',
  'chat.intro.greeting.hello',
  'chat.intro.greeting.heyThere',
]);
const chatRecentEmojiStorageKey = 'yolomux.chat.recentEmoji';
const chatRecentEmojiLimit = 24;
const chatMessageMaxBytes = 8 * 1024;
// Network-backed refreshes deliberately avoid round boundaries so independently opened
// clients do not herd onto the same server tick.
const chatTypingRefreshMs = 3001;
const chatRelativeTimeRefreshMs = 5003;
const chatRelativeTimeLimitSeconds = 4 * 60 * 60;
const chatNotificationMaxAgeSeconds = 8 * 60 * 60;
const chatTailThresholdPx = 32;
const chatAuthorToneCount = 10;
const chatSelfAuthorTone = 2;
const chatMediaMaxUrlLength = 2048;
const chatMediaMaxPerMessage = 4;
const chatExternalUrlPattern = /\bhttps?:\/\/[^\s<>"')\]}]+/gi;
const chatImageExtensions = new Set(['avif', 'gif', 'jpeg', 'jpg', 'png', 'webp']);
const chatVideoExtensions = new Set(['m4v', 'mov', 'mp4', 'ogv', 'webm']);
const chatEmojiCategories = Object.freeze([
  ['recent', '★'], ['smileys-emotion', '😀'], ['people-body', '👋'], ['animals-nature', '🐻'],
  ['food-drink', '🍎'], ['travel-places', '🚗'], ['activities', '⚽'], ['objects', '💡'],
  ['symbols', '❤️'], ['flags', '🏳️'],
]);
const chatState = {
  loaded: false,
  loadingRequest: null,
  messages: new Map(),
  pending: new Map(),
  unread: new Map(),
  typing: [],
  revision: 0,
  readUpToId: 0,
  newerCursor: '',
  olderCursor: '',
  hasMore: false,
  olderRequested: false,
  followTail: true,
  draft: '',
  typingActive: false,
  typingTimer: null,
  typingExpiryTimer: null,
  requestGeneration: 0,
  olderGeneration: 0,
  contextGeneration: 0,
  requestController: null,
  searchGeneration: 0,
  searchOpen: false,
  searchVisible: false,
  searchResults: [],
  searchCursor: '',
  searchSnapshot: null,
  acknowledgedTone: '',
  acknowledgementStartedAt: 0,
  acknowledgementTimer: null,
  notifiedIds: new Set(),
  emojiCatalogPromise: null,
  emojiOpen: false,
  emojiCategory: 'recent',
  searchQuery: '',
  olderObserver: null,
  olderObserverTarget: null,
  statusSignature: null,
  timelineSignature: '',
  lastAnnouncement: '',
  renderedAnnouncement: '',
  clientIp: '',
};
const chatEmojiOverlayController = createDismissableOverlayController({
  trapFocus: true,
  onOpen: () => {
    chatState.emojiOpen = true;
    document.querySelector(`#${cssEscape(panelDomId(chatItemId))} [data-chat-emoji-button]`)?.setAttribute('aria-expanded', 'true');
  },
  onClose: () => {
    chatState.emojiOpen = false;
    document.querySelector(`#${cssEscape(panelDomId(chatItemId))} [data-chat-emoji-button]`)?.setAttribute('aria-expanded', 'false');
  },
});

function chatApiPost(path, payload, options = {}) {
  const keepalive = options.keepalive === true;
  return apiFetchJson(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload || {}),
    keepalive,
  });
}

function chatRequestOptions() {
  if (!chatState.requestController || chatState.requestController.signal.aborted) {
    chatState.requestController = new AbortController();
  }
  return {signal: chatState.requestController.signal};
}

function beginChatLoadingRequest(generationKey) {
  if (chatState.loadingRequest) return null;
  const request = {generationKey, generation: ++chatState[generationKey]};
  chatState.loadingRequest = request;
  return request;
}

function finishChatLoadingRequest(request) {
  if (!request || chatState.loadingRequest !== request) return false;
  chatState.loadingRequest = null;
  if (chatState.olderRequested) void loadOlderChatMessages();
  return true;
}

function chatPreciseRelativeTimeFormat(secondsAgo) {
  const seconds = Math.max(0, Number(secondsAgo) || 0);
  let value;
  let unit;
  let style;
  if (seconds < 1) {
    value = 0;
    unit = 'minute';
    style = 'long';
  } else if (seconds < 60) {
    value = Math.ceil(seconds / 5) * 5;
    unit = 'second';
    style = 'short';
  } else if (seconds <= 60 * 60) {
    value = Math.ceil(seconds / 6) / 10;
    unit = 'minute';
    style = 'short';
  } else if (seconds <= 24 * 60 * 60) {
    value = Math.ceil(seconds / 360) / 10;
    unit = 'hour';
    style = 'long';
  } else {
    value = Math.ceil(seconds / 8640) / 10;
    unit = 'day';
    style = 'long';
  }
  try {
    return new Intl.RelativeTimeFormat(i18nActiveLocale, {numeric: 'always', style})
      .format(-value, unit)
      .replace(/(\p{L})\./gu, '$1');
  } catch (_) {
    return relativeTimeFormat(seconds);
  }
}

function chatMessageTimestamp(timestampSeconds, nowSeconds = Date.now() / 1000) {
  const ageSeconds = Math.max(0, Number(nowSeconds) - Number(timestampSeconds || 0));
  const relative = chatPreciseRelativeTimeFormat(ageSeconds);
  if (ageSeconds < chatRelativeTimeLimitSeconds) return relative;
  const exact = localizedExactDateTimeFormat(timestampSeconds).replace(/ ([AP]M)$/u, '$1');
  return `${exact} ${relative}`;
}

function chatNotificationTimestamp(timestampSeconds, nowSeconds = Date.now() / 1000) {
  const ageSeconds = Math.max(0, Number(nowSeconds) - Number(timestampSeconds || 0));
  const exact = localizedExactDateTimeFormat(timestampSeconds).replace(/ ([AP]M)$/u, '$1');
  return `${exact} ${chatPreciseRelativeTimeFormat(ageSeconds)}`;
}

function chatMessageNotificationEligible(message, nowSeconds = Date.now() / 1000) {
  const timestamp = Number(message?.created_at_utc);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return false;
  return Math.max(0, Number(nowSeconds) - timestamp) <= chatNotificationMaxAgeSeconds;
}

function chatOrderedMessages() {
  return [...chatState.messages.values(), ...chatState.pending.values()].sort((left, right) => {
    const leftId = Number(left.id) || Number.MAX_SAFE_INTEGER;
    const rightId = Number(right.id) || Number.MAX_SAFE_INTEGER;
    if (leftId !== rightId) return leftId - rightId;
    return Number(left.created_at_utc || 0) - Number(right.created_at_utc || 0);
  });
}

function chatMediaUrlMatches(body) {
  const text = String(body || '');
  const matches = [];
  chatExternalUrlPattern.lastIndex = 0;
  let match;
  while ((match = chatExternalUrlPattern.exec(text))) {
    const rawMatch = match[0];
    const rawUrl = rawMatch.replace(/[.,;:!?]+$/, '');
    const url = normalizedExternalHttpUrl(rawUrl, {decodeHtmlAmpersands: true, maxLength: chatMediaMaxUrlLength});
    matches.push({start: match.index, end: match.index + rawMatch.length, rawUrl, trailing: rawMatch.slice(rawUrl.length), url});
  }
  return matches;
}

function chatMediaKind(url) {
  const normalized = normalizedExternalHttpUrl(url, {maxLength: chatMediaMaxUrlLength});
  if (!normalized) return '';
  const parsed = new URL(normalized);
  const extension = parsed.pathname.split('.').at(-1)?.toLowerCase() || '';
  if (chatVideoExtensions.has(extension)) return 'video';
  if (chatImageExtensions.has(extension)) return 'image';
  return /(?:^|[\/_-])(?:image|images|img|photo|picture|thumb|thumbnail|media)(?:$|[\/_-])/i.test(parsed.pathname)
    ? 'image'
    : '';
}

function chatLinkedTextHtml(body) {
  const text = String(body || '');
  const matches = chatMediaUrlMatches(text);
  if (!matches.length) return esc(text);
  const parts = [];
  let cursor = 0;
  for (const match of matches) {
    if (match.start > cursor) parts.push(esc(text.slice(cursor, match.start)));
    parts.push(match.url
      ? `<a class="yochat-external-link" href="${esc(match.url)}" target="_blank" rel="noopener noreferrer">${esc(match.rawUrl)}</a>`
      : esc(match.rawUrl));
    if (match.trailing) parts.push(esc(match.trailing));
    cursor = match.end;
  }
  if (cursor < text.length) parts.push(esc(text.slice(cursor)));
  return parts.join('');
}

function chatMediaPreviewsHtml(body) {
  const seen = new Set();
  const items = [];
  for (const match of chatMediaUrlMatches(body)) {
    const kind = chatMediaKind(match.url);
    if (!kind || seen.has(match.url)) continue;
    seen.add(match.url);
    const media = kind === 'video'
      ? `<video src="${esc(match.url)}" muted loop playsinline preload="metadata"></video>`
      : `<img src="${esc(match.url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`;
    items.push(`<button type="button" class="yochat-media-thumbnail" data-chat-media-url="${esc(match.url)}" data-chat-media-kind="${esc(kind)}" title="${esc(t('contextmenu.openNewTab'))}" aria-label="${esc(`${t('common.open')}: ${chatMediaLabel(match.url)}`)}">${media}</button>`);
    if (items.length >= chatMediaMaxPerMessage) break;
  }
  return items.length ? `<div class="yochat-media-strip">${items.join('')}</div>` : '';
}

function chatMessageBodyHtml(message) {
  const markdown = chatMessageIsYoagent(message);
  const body = String(message.body || '');
  const bodyHtml = markdown ? esc(body) : `${chatLinkedTextHtml(body)}${chatMediaPreviewsHtml(body)}`;
  return `<div class="conversation-message-body yoagent-message-body${markdown ? ' markdown-body' : ''}"${markdown ? ' data-yoagent-markdown' : ''}>${bodyHtml}</div>`;
}

function chatAuthorIdentity(message) {
  return String(message?.username || '');
}

function chatMessageIsYoagent(message) {
  return chatAuthorIdentity(message) === 'YO!agent';
}

function chatAuthorHash(identity) {
  let hash = 2166136261;
  for (const character of String(identity || '')) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function chatAuthorToneAssignments(messages) {
  const authors = [...new Set((messages || []).map(chatAuthorIdentity).filter(identity => identity && identity !== 'YO!agent'))].sort();
  const used = new Set();
  const assignments = new Map();
  const selfAuthor = String(authUsername || '');
  if (selfAuthor && authors.includes(selfAuthor)) {
    assignments.set(selfAuthor, chatSelfAuthorTone);
    used.add(chatSelfAuthorTone);
  }
  for (const author of authors) {
    if (author === selfAuthor) continue;
    let tone = chatAuthorHash(author) % chatAuthorToneCount;
    for (let offset = 0; offset < chatAuthorToneCount && used.has(tone); offset += 1) {
      tone = (tone + 1) % chatAuthorToneCount;
    }
    assignments.set(author, tone);
    used.add(tone);
  }
  return assignments;
}

function chatMessageMetadataHtml(message, timestamp, options = {}) {
  const ip = String(message.sender_ip || '').trim();
  const parts = [];
  if (options.self === true) parts.push(`<span class="yochat-message-self">${esc(t('chat.message.myself'))}</span>`);
  const timeHtml = timestamp
    ? `<time class="conversation-message-time yoagent-message-time" data-chat-created-at="${esc(message.created_at_utc)}" datetime="${esc(new Date(Number(message.created_at_utc) * 1000).toISOString())}">${esc(timestamp)}</time>`
    : '';
  if (timeHtml) parts.push(timeHtml);
  if (ip) parts.push(`<span class="yochat-message-ip">${esc(ip)}</span>`);
  if (!parts.length) return '';
  return `<span class="yochat-message-metadata">(${parts.join('<span aria-hidden="true">, </span>')})</span>`;
}

function chatMessageHtml(message, authorTones = new Map()) {
  const self = message.pending === true || String(message.username || '') === String(authUsername || '');
  const state = message.failed ? `<button type="button" class="chat-message-retry" data-action="chat-retry" data-chat-retry="${esc(message.client_message_uuid)}">${esc(t('common.retry'))}</button>`
    : message.pending ? `<span class="chat-message-pending">${esc(t('yoagent.action.state.sending'))}</span>` : '';
  const timestamp = chatMessageTimestamp(message.created_at_utc);
  const agentMessage = chatMessageIsYoagent(message);
  const tone = authorTones.get(chatAuthorIdentity(message));
  const toneClass = Number.isInteger(tone) ? ` yochat-author-tone-${tone}` : '';
  return conversationMessageShellHtml({
    self,
    className: `yoagent-message yochat-message${agentMessage ? ' yochat-agent-message' : toneClass}${message.pending ? ' pending' : ''}${message.failed ? ' failed' : ''}`,
    author: message.username,
    timestampHtml: chatMessageMetadataHtml(message, timestamp, {self}),
    bodyHtml: chatMessageBodyHtml(message),
    extrasHtml: state,
    attributes: `data-chat-message-id="${esc(message.id)}"`,
  });
}

function chatTypingParticipantNames() {
  return [...new Set(chatState.typing.map(item => String(item.username || '')).filter(Boolean))];
}

function chatTypingText() {
  const names = chatTypingParticipantNames();
  if (!names.length) return '';
  if (names.length === 1) return t('chat.typing.one', {name: names[0]});
  if (names.length === 2) return t('chat.typing.two', {first: names[0], second: names[1]});
  return t('chat.typing.many', {names: chatTypingNamesText(names)});
}

function chatTypingNamesText(names) {
  const participants = (Array.isArray(names) ? names : []).map(String).filter(Boolean);
  if (String(i18nActiveLocale || '').toLowerCase().startsWith('en')) {
    return `${participants.slice(0, -1).join(', ')}, & ${participants.at(-1)}`;
  }
  try {
    return new Intl.ListFormat(i18nActiveLocale, {style: 'long', type: 'conjunction'}).format(participants);
  } catch (_) {
    return participants.join(', ');
  }
}

function chatStatusTones() {
  const tones = [];
  if (chatState.unread.size) {
    tones.push([...chatState.unread.values()].some(message => message.is_question) ? 'attention' : 'cooldown');
  }
  if (chatTypingParticipantNames().length) tones.push(STATE_KEY.working);
  return tones;
}

function chatStatusMarkerHtml() {
  const tones = chatStatusTones();
  const typing = tones.includes(STATE_KEY.working);
  if (chatState.acknowledgedTone) {
    const elapsed = Math.max(0, Date.now() - chatState.acknowledgementStartedAt);
    const acknowledged = agentWindowStatusDotHtml({
      state: chatState.acknowledgedTone,
      icon: '●',
      label: t('chat.status.acknowledged'),
      acknowledging: true,
      acknowledgementDurationMs: agentStatusPulsePeriodMs,
      acknowledgementElapsedMs: elapsed,
    }, {label: t('chat.status.acknowledged')});
    const green = typing ? agentWindowStatusDotHtmlForTone(STATE_KEY.working, {label: t('chat.status.typing'), pulse: true}) : '';
    return `<span class="session-agent-activity-marker chat-status-marker">${acknowledged}${green}</span>`;
  }
  if (!tones.length) return '';
  const primary = tones[0];
  const label = primary === 'attention' ? t('chat.status.question') : primary === 'cooldown' ? t('chat.status.unread') : t('chat.status.typing');
  const item = {
    state: primary,
    icon: '●',
    label,
    aggregateTones: tones,
    allAggregateTones: tones,
    pulseActive: true,
    transitionPulseActive: true,
  };
  return `<span class="session-agent-activity-marker chat-status-marker">${agentWindowStatusDotHtml(item, {label})}</span>`;
}

function renderChatStatus() {
  const signature = chatStatusMarkerHtml();
  if (signature === chatState.statusSignature) return false;
  chatState.statusSignature = signature;
  if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
  if (typeof syncTabberTreeLayoutState === 'function') syncTabberTreeLayoutState();
  return true;
}

function renderChatPanel(options = {}) {
  const panel = document.getElementById(panelDomId(chatItemId));
  if (!panel) return;
  const timeline = panel.querySelector('[data-chat-timeline]');
  if (!timeline) return;
  const previousScrollTop = timeline.scrollTop;
  const previousBottom = timeline.scrollHeight - timeline.scrollTop;
  const wasAtTail = chatTimelineIsNearBottom(timeline);
  const shouldFollowTail = options.prepend !== true && (options.scrollBottom === true || chatState.followTail || wasAtTail);
  const messages = chatOrderedMessages();
  const authorTones = chatAuthorToneAssignments([...messages, {username: authUsername}]);
  const composer = panel.querySelector('[data-chat-form]');
  const composerTone = authorTones.get(String(authUsername || ''));
  for (let index = 0; index < chatAuthorToneCount; index += 1) composer?.classList.remove(`yochat-author-tone-${index}`);
  if (Number.isInteger(composerTone)) composer?.classList.add(`yochat-author-tone-${composerTone}`);
  const timelineSignature = JSON.stringify(messages.map(message => [message.id, message.client_message_uuid, message.pending === true, message.failed === true, message.username, message.sender_ip, message.created_at_utc, message.body]));
  if (timelineSignature !== chatState.timelineSignature) {
    const timelineBody = `${chatIntroductionHtml()}${messages.map(message => chatMessageHtml(message, authorTones)).join('')}`;
    timeline.innerHTML = `<div class="chat-history-sentry" data-chat-history-sentry aria-hidden="true"></div>${timelineBody}`;
    chatState.timelineSignature = timelineSignature;
    if (options.prepend === true) {
      chatState.followTail = false;
      timeline.scrollTop = Math.max(0, timeline.scrollHeight - previousBottom);
    } else if (shouldFollowTail) {
      chatState.followTail = true;
      timeline.scrollTop = timeline.scrollHeight;
    } else {
      timeline.scrollTop = previousScrollTop;
    }
  }
  renderConversationMessageMarkdown(panel);
  bindChatMediaPreviews(panel);
  refreshChatRelativeTimes(panel);
  const typingNode = panel.querySelector('[data-chat-typing]');
  if (typingNode) {
    const text = chatTypingText();
    if (typingNode.dataset.chatTypingText !== text) {
      typingNode.dataset.chatTypingText = text;
      typingNode.innerHTML = text ? textWithMovingEllipsisHtml(text, 'chat-typing-dots') : '';
    }
    typingNode.hidden = !text;
  }
  if (shouldFollowTail) timeline.scrollTop = timeline.scrollHeight;
  const liveNode = panel.querySelector('[data-chat-live]');
  if (liveNode && chatState.lastAnnouncement !== chatState.renderedAnnouncement) {
    liveNode.textContent = chatState.lastAnnouncement;
    chatState.renderedAnnouncement = chatState.lastAnnouncement;
  }
  const searchBar = panel.querySelector('[data-chat-search-bar]');
  if (searchBar) searchBar.hidden = !chatState.searchVisible;
  syncChatTailState(panel, timeline);
  renderChatSearchResults(panel);
  syncChatHistoryObserver(panel);
  renderChatStatus();
}

function chatTimelineIsNearBottom(timeline) {
  if (!timeline) return true;
  return timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight <= chatTailThresholdPx;
}

function syncChatTailState(panel, timeline = panel?.querySelector('[data-chat-timeline]')) {
  const atTail = chatTimelineIsNearBottom(timeline);
  chatState.followTail = atTail;
  const newMessages = panel?.querySelector('[data-chat-new-messages]');
  if (newMessages) newMessages.hidden = chatState.unread.size === 0 || atTail;
  return atTail;
}

function chatScrollTimelineToBottom(panel = document.getElementById(panelDomId(chatItemId))) {
  const timeline = panel?.querySelector('[data-chat-timeline]');
  if (!timeline) return false;
  timeline.scrollTop = timeline.scrollHeight;
  syncChatTailState(panel, timeline);
  requestAnimationFrame(() => syncChatTailState(panel, timeline));
  return true;
}

function refreshChatRelativeTimes(panel = document.getElementById(panelDomId(chatItemId)), nowSeconds = Date.now() / 1000) {
  if (!panel) return 0;
  let updated = 0;
  panel.querySelectorAll('[data-chat-created-at]').forEach(node => {
    const next = chatMessageTimestamp(Number(node.dataset.chatCreatedAt), nowSeconds);
    if (node.textContent === next) return;
    node.textContent = next;
    updated += 1;
  });
  return updated;
}

function chatMediaItemFor(url) {
  const normalized = normalizedExternalHttpUrl(url, {maxLength: chatMediaMaxUrlLength});
  return normalized ? `${chatMediaItemPrefix}${encodeURIComponent(normalized)}` : '';
}

function chatMediaUrlForItem(item) {
  const text = String(item || '');
  if (!text.startsWith(chatMediaItemPrefix)) return '';
  return normalizedExternalHttpUrl(safeDecodeURIComponent(text.slice(chatMediaItemPrefix.length)), {maxLength: chatMediaMaxUrlLength});
}

function chatMediaLabel(url) {
  const normalized = normalizedExternalHttpUrl(url, {maxLength: chatMediaMaxUrlLength});
  if (!normalized) return t('popover.kind.image');
  const parsed = new URL(normalized);
  const basename = safeDecodeURIComponent(parsed.pathname.split('/').filter(Boolean).at(-1) || '');
  return basename || parsed.hostname || t('popover.kind.image');
}

function chatMediaElementHtml(url, options = {}) {
  const normalized = normalizedExternalHttpUrl(url, {maxLength: chatMediaMaxUrlLength});
  if (!normalized) return '';
  if (chatMediaKind(normalized) === 'video') {
    return `<video src="${esc(normalized)}"${options.controls === true ? ' controls' : ''} muted loop playsinline preload="metadata"></video>`;
  }
  return `<img src="${esc(normalized)}" alt="${esc(chatMediaLabel(normalized))}" referrerpolicy="no-referrer">`;
}

function openChatMediaTab(url) {
  const item = resolveLayoutItem(chatMediaItemFor(url));
  if (!item) return false;
  selectSession(item, {userInitiated: true});
  return true;
}

function chatMediaActionItems(url) {
  const normalized = normalizedExternalHttpUrl(url, {maxLength: chatMediaMaxUrlLength});
  if (!normalized) return [];
  return [
    {id: 'tab', label: t('contextmenu.openNewTab'), run: () => openChatMediaTab(normalized)},
    {id: 'browser', label: t('contextmenu.openUrl'), run: () => window.open(normalized, '_blank', 'noopener,noreferrer')},
    {id: 'copy', label: t('contextmenu.copyUrl'), run: () => copyTextToClipboard(normalized)},
    {id: 'download', label: t('common.download'), run: () => triggerExternalUrlDownload(normalized)},
  ];
}

function runChatMediaAction(url, actionId) {
  const action = chatMediaActionItems(url).find(item => item.id === String(actionId || ''));
  if (!action) return false;
  action.run();
  return true;
}

function showChatMediaActions(anchor, x = null, y = null) {
  const url = normalizedExternalHttpUrl(anchor?.dataset?.chatMediaUrl, {maxLength: chatMediaMaxUrlLength});
  if (!url) return false;
  closeContextMenus();
  closeOtherSessionPopovers(null);
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu link-context-menu yochat-media-context-menu';
  menu.setAttribute('role', 'menu');
  for (const action of chatMediaActionItems(url)) {
    appendContextMenuButton(menu, action.label, action.run, closeLinkContextMenu);
  }
  linkContextMenu.open(menu, Number.isFinite(x) ? x : rect.left, Number.isFinite(y) ? y : rect.bottom);
  return true;
}

function bindChatMediaPreviews(panel = document.getElementById(panelDomId(chatItemId))) {
  if (!panel) return 0;
  const thumbnails = panel.querySelectorAll('[data-chat-media-url]');
  thumbnails.forEach(thumbnail => {
    const url = normalizedExternalHttpUrl(thumbnail.dataset.chatMediaUrl, {maxLength: chatMediaMaxUrlLength});
    if (!url) return;
    bindFileImagePreview(thumbnail, url, {name: chatMediaLabel(url)}, {
      sourceUrl: url,
      mediaKind: thumbnail.dataset.chatMediaKind,
      className: 'yochat-media-preview-popover',
    });
  });
  return thumbnails.length;
}

function chatMediaPanelBodyHtml(url) {
  return `<div class="yochat-media-panel-content">
    <div class="yochat-media-panel-stage">${chatMediaElementHtml(url, {controls: true})}</div>
    <div class="yochat-media-panel-actions">${chatMediaActionItems(url).slice(1).map(action => `<button type="button" data-chat-media-action="${esc(action.id)}">${esc(action.label)}</button>`).join('')}</div>
    <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>
  </div>`;
}

function createChatMediaPanel(item) {
  const url = chatMediaUrlForItem(item);
  const panel = document.createElement('article');
  panel.className = 'panel info-panel chat-media-panel';
  panel.id = panelDomId(item);
  panel.dataset.chatMediaUrl = url;
  panel.innerHTML = panelFrameHtml({
    item,
    controlsHtml: virtualPanelInnerControlsHtml(item),
    bodyClass: 'info-pane yochat-media-pane',
    bodyHtml: chatMediaPanelBodyHtml(url),
    toastStack: false,
  });
  bindPanelShell(panel, item);
  installLinkContextMenu(panel);
  panel.addEventListener('click', event => {
    const action = event.target.closest('[data-chat-media-action]');
    if (action) runChatMediaAction(url, action.dataset.chatMediaAction);
  });
  return panel;
}

function relocalizeChatMediaPanel(panel, item) {
  if (!panel) return false;
  const url = chatMediaUrlForItem(item);
  relocalizeVirtualPanelChrome(panel, chatMediaLabel(url));
  const body = panel.querySelector('.yochat-media-pane');
  if (body) body.innerHTML = chatMediaPanelBodyHtml(url);
  return true;
}

function chatIntroductionHtml() {
  const greeting = t(chatIntroductionGreetingKey());
  return conversationMessageShellHtml({
    self: false,
    className: 'yoagent-message yochat-message yochat-introduction',
    author: yoagentTabLabel(),
    bodyHtml: `<div class="conversation-message-body yoagent-message-body markdown-body" data-yoagent-markdown>${esc(`${greeting} ${t('chat.intro.chat')}\n\n${t('chat.intro.yoagent')}`)}</div>`,
  });
}

function chatIntroductionGreetingKey(browserInstanceId = chatBrowserInstanceId) {
  return chatIntroductionGreetingKeys[chatAuthorHash(browserInstanceId) % chatIntroductionGreetingKeys.length];
}

function replaceChatTyping(typing) {
  chatState.typing = (Array.isArray(typing) ? typing : []).filter(item => Number(item?.expires_at_utc) > Date.now() / 1000);
  if (chatState.typingExpiryTimer) clearTimeout(chatState.typingExpiryTimer);
  chatState.typingExpiryTimer = null;
  const earliest = Math.min(...chatState.typing.map(item => Number(item.expires_at_utc)).filter(Number.isFinite));
  if (!Number.isFinite(earliest)) return;
  chatState.typingExpiryTimer = setTimeout(() => {
    chatState.typingExpiryTimer = null;
    replaceChatTyping(chatState.typing);
    renderChatPanel();
    loadChatBootstrap({incoming: false});
  }, Math.max(0, (earliest * 1000) - Date.now()) + 20);
}

function chatMergeMessages(messages, options = {}) {
  let changed = false;
  for (const raw of Array.isArray(messages) ? messages : []) {
    const message = raw && typeof raw === 'object' ? {...raw} : null;
    if (!message || !Number.isFinite(Number(message.id))) continue;
    const id = Number(message.id);
    message.id = id;
    if (!chatState.messages.has(id)) changed = true;
    chatState.messages.set(id, message);
    chatState.pending.delete(String(message.client_message_uuid || ''));
    if (String(message.username || '') === String(authUsername || '') && message.sender_ip) {
      chatState.clientIp = String(message.sender_ip);
    }
    chatState.revision = Math.max(chatState.revision, id);
    const fromThisInstance = message.sender_instance_id === chatBrowserInstanceId;
    if (options.incoming === true && fromThisInstance && id > chatState.readUpToId) chatAdvanceReadCursor(id);
    if (options.incoming === true && !fromThisInstance) {
      if (chatState.acknowledgementTimer) {
        clearTimeout(chatState.acknowledgementTimer);
        chatState.acknowledgementTimer = null;
      }
      chatState.acknowledgedTone = '';
      chatState.unread.set(id, message);
      chatState.lastAnnouncement = chatNotificationBody(message);
      maybeNotifyChatMessage(message);
    }
  }
  return changed;
}

async function loadChatBootstrap(options = {}) {
  if (shareViewMode || !authUsername || chatState.loadingRequest) return false;
  const request = beginChatLoadingRequest('requestGeneration');
  try {
    const params = new URLSearchParams({reader_id: chatReaderId, browser_instance_id: chatBrowserInstanceId});
    const payload = await apiFetchJson(`/api/chat/bootstrap?${params}`, chatRequestOptions());
    if (request.generation !== chatState.requestGeneration) return false;
    const wasLoaded = chatState.loaded;
    chatState.loaded = true;
    chatState.revision = Math.max(chatState.revision, Number(payload.revision) || 0);
    chatState.readUpToId = Math.max(chatState.readUpToId, Number(payload.read_up_to_id) || 0);
    chatState.newerCursor = String(payload.newer_cursor || chatState.newerCursor);
    chatState.olderCursor = String(payload.older_cursor || '');
    chatState.hasMore = payload.has_more_older === true;
    chatState.clientIp = String(payload.client_ip || chatState.clientIp || '');
    replaceChatTyping(payload.typing);
    chatMergeMessages(payload.messages, {incoming: wasLoaded || options.incoming === true});
    renderChatPanel({scrollBottom: !wasLoaded});
    return true;
  } catch (error) {
    statusErr(localizedHtml('common.requestFailed'));
    return false;
  } finally {
    finishChatLoadingRequest(request);
  }
}

async function loadOlderChatMessages() {
  if (!chatState.olderCursor || !chatState.hasMore) {
    chatState.olderRequested = false;
    return false;
  }
  if (chatState.loadingRequest) return false;
  chatState.olderRequested = false;
  const request = beginChatLoadingRequest('olderGeneration');
  try {
    const params = new URLSearchParams({before: chatState.olderCursor, limit: '50'});
    const payload = await apiFetchJson(`/api/chat/page?${params}`, chatRequestOptions());
    if (request.generation !== chatState.olderGeneration) return false;
    chatMergeMessages(payload.messages);
    chatState.olderCursor = String(payload.older_cursor || '');
    chatState.hasMore = payload.has_more === true;
    renderChatPanel({prepend: true});
    return true;
  } catch (error) {
    statusErr(localizedHtml('common.requestFailed'));
    return false;
  } finally {
    finishChatLoadingRequest(request);
  }
}

async function loadChatDelta() {
  if (!chatState.loaded) return loadChatBootstrap({incoming: true});
  const generation = ++chatState.requestGeneration;
  const params = new URLSearchParams({after: chatState.newerCursor, limit: '200'});
  try {
    const payload = await apiFetchJson(`/api/chat/delta?${params}`, chatRequestOptions());
    if (generation !== chatState.requestGeneration) return false;
    chatState.newerCursor = String(payload.newer_cursor || chatState.newerCursor);
    chatMergeMessages(payload.messages, {incoming: true});
    renderChatPanel();
    return true;
  } catch (error) {
    console.warn('YO!chat delta refresh failed', error);
    return false;
  }
}

function chatPanelIsEngaged() {
  return notificationTargetIsFocused(chatItemId);
}

function chatNotificationSnippet(body, limit = 80) {
  const segments = typeof Intl?.Segmenter === 'function'
    ? [...new Intl.Segmenter(i18nActiveLocale, {granularity: 'grapheme'}).segment(String(body || ''))].map(item => item.segment)
    : Array.from(String(body || ''));
  return `${segments.slice(0, limit).join('')}${segments.length > limit ? '…' : ''}`;
}

function chatNotificationBody(message) {
  return t('chat.notification.body', {
    username: message?.username,
    snippet: chatNotificationSnippet(message?.body),
  });
}

function chatNotificationLines(message, nowSeconds = Date.now() / 1000) {
  return [chatNotificationBody(message), chatNotificationTimestamp(message?.created_at_utc, nowSeconds)];
}

function openChatNotification(messageId) {
  selectSession(chatItemId, {userInitiated: true}).then(() => openChatMessageContext(messageId));
}

function maybeNotifyChatMessage(message) {
  const id = Number(message?.id) || 0;
  if (!id || !chatMessageNotificationEligible(message) || chatState.notifiedIds.has(id) || message.sender_instance_id === chatBrowserInstanceId || chatPanelIsEngaged()) return false;
  chatState.notifiedIds.add(id);
  const lines = chatNotificationLines(message);
  const body = lines.join('\n');
  const onClick = () => openChatNotification(id);
  emitNotification('chatMessage', {
    title: chatTabLabel(), lines, body, systemBody: body, onClick,
    coalesceKey: `chat:${id}`, systemTag: `yolomux:chat:${id}`,
  });
  return true;
}

async function chatAdvanceReadCursor(messageId) {
  const newest = Number(messageId) || 0;
  if (!newest || newest <= chatState.readUpToId) return false;
  const previous = chatState.readUpToId;
  chatState.readUpToId = newest;
  try {
    const payload = await chatApiPost('/api/chat/read', {reader_id: chatReaderId, message_id: newest});
    chatState.readUpToId = Math.max(chatState.readUpToId, Number(payload.read_up_to_id) || newest);
    return true;
  } catch (error) {
    if (chatState.readUpToId === newest) chatState.readUpToId = previous;
    console.warn('YO!chat read acknowledgement failed', error);
    return false;
  }
}

async function chatAcknowledge() {
  if (!chatState.unread.size) return false;
  const newest = Math.max(...chatState.unread.keys());
  const tone = [...chatState.unread.values()].some(message => message.is_question) ? 'attention' : 'cooldown';
  chatState.unread.clear();
  chatState.acknowledgedTone = tone;
  chatState.acknowledgementStartedAt = Date.now();
  if (chatState.acknowledgementTimer) clearTimeout(chatState.acknowledgementTimer);
  chatState.acknowledgementTimer = setTimeout(() => {
    chatState.acknowledgedTone = '';
    chatState.acknowledgementTimer = null;
    renderChatStatus();
  }, agentStatusPulsePeriodMs);
  renderChatStatus();
  return chatAdvanceReadCursor(newest);
}

function setChatTyping(active, options = {}) {
  const next = active === true;
  if (chatState.typingTimer) {
    clearTimeout(chatState.typingTimer);
    chatState.typingTimer = null;
  }
  if (next === chatState.typingActive && !options.heartbeat) return;
  chatState.typingActive = next;
  chatApiPost('/api/chat/typing', {browser_instance_id: chatBrowserInstanceId, typing: next}, {keepalive: options.keepalive}).catch(() => {});
  if (next) {
    chatState.typingTimer = setTimeout(() => setChatTyping(true, {heartbeat: true}), chatTypingRefreshMs);
  }
}

function chatYoagentQuery(body) {
  const match = String(body || '').trim().match(/^\/yo\s+([\s\S]+)$/);
  return match?.[1]?.trim() || '';
}

async function requestChatYoagent(sourceMessage) {
  if (!chatYoagentQuery(sourceMessage?.body)) return false;
  try {
    const payload = await chatApiPost('/api/chat/yoagent', {
      browser_instance_id: chatBrowserInstanceId,
      message_id: sourceMessage.id,
    });
    chatMergeMessages([payload.message]);
    renderChatPanel({scrollBottom: chatState.followTail});
    await chatAdvanceReadCursor(payload.message?.id);
    return true;
  } catch (error) {
    statusErr(localizedHtml('common.requestFailed'));
    return false;
  }
}

async function sendChatPending(message) {
  message.pending = true;
  message.failed = false;
  chatState.pending.set(message.client_message_uuid, message);
  renderChatPanel({scrollBottom: true});
  try {
    const payload = await chatApiPost('/api/chat/send', {
      browser_instance_id: chatBrowserInstanceId,
      client_message_uuid: message.client_message_uuid,
      body: message.body,
    });
    chatState.pending.delete(message.client_message_uuid);
    chatMergeMessages([payload.message]);
    chatState.revision = Math.max(chatState.revision, Number(payload.revision) || 0);
    renderChatPanel({scrollBottom: true});
    await chatAdvanceReadCursor(payload.message?.id);
    await loadChatBootstrap();
    if (chatYoagentQuery(payload.message?.body)) requestChatYoagent(payload.message);
    return true;
  } catch (error) {
    message.failed = true;
    message.pending = false;
    chatState.pending.set(message.client_message_uuid, message);
    renderChatPanel();
    return false;
  }
}

function submitChatDraft() {
  const body = String(chatState.draft || '');
  if (!body) return false;
  if (new TextEncoder().encode(body).length > chatMessageMaxBytes) {
    statusErr(localizedHtml('chat.error.tooLarge'));
    return false;
  }
  const uuid = randomShareViewerId();
  const message = {
    id: `pending:${uuid}`,
    created_at_utc: Date.now() / 1000,
    username: authUsername,
    sender_ip: chatState.clientIp,
    sender_instance_id: chatBrowserInstanceId,
    client_message_uuid: uuid,
    body,
    is_question: false,
  };
  chatState.draft = '';
  const input = document.querySelector(`#${cssEscape(panelDomId(chatItemId))} [data-chat-input]`);
  if (input) input.value = '';
  setChatTyping(false);
  sendChatPending(message);
  return true;
}

function loadChatEmojiCatalog() {
  if (Array.isArray(globalThis.YOLOMUX_EMOJI_DATA)) return Promise.resolve(globalThis.YOLOMUX_EMOJI_DATA);
  if (chatState.emojiCatalogPromise) return chatState.emojiCatalogPromise;
  chatState.emojiCatalogPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/static/emoji-data.js';
    script.onload = () => resolve(Array.isArray(globalThis.YOLOMUX_EMOJI_DATA) ? globalThis.YOLOMUX_EMOJI_DATA : []);
    script.onerror = () => reject(new Error(t('common.requestFailed')));
    document.head.appendChild(script);
  });
  return chatState.emojiCatalogPromise;
}

function chatRecentEmoji() {
  try {
    const stored = JSON.parse(storageGet(chatRecentEmojiStorageKey, '[]'));
    return Array.isArray(stored) ? stored.map(String).slice(0, chatRecentEmojiLimit) : [];
  } catch (_) {
    return [];
  }
}

function rememberChatEmoji(glyph) {
  const recent = [String(glyph), ...chatRecentEmoji().filter(item => item !== glyph)].slice(0, chatRecentEmojiLimit);
  storageSet(chatRecentEmojiStorageKey, JSON.stringify(recent));
}

function chatEmojiSearchText(item) {
  const localized = item.names?.[i18nActiveLocale] || item.names?.[i18nFallbackLocale] || '';
  const keywords = item.keywords?.[i18nActiveLocale] || item.keywords?.[i18nFallbackLocale] || [];
  return [item.emoji, localized, ...keywords].join(' ').toLocaleLowerCase(i18nActiveLocale);
}

function renderChatEmojiGrid(panel, catalog, query = '') {
  const grid = panel?.querySelector('[data-chat-emoji-grid]');
  if (!grid) return;
  const needle = String(query || '').trim().toLocaleLowerCase(i18nActiveLocale);
  const recent = chatRecentEmoji();
  const ordered = [...recent.map(glyph => catalog.find(item => item.emoji === glyph)).filter(Boolean), ...catalog.filter(item => !recent.includes(item.emoji))];
  const filtered = ordered.filter(item => {
    if (needle) return chatEmojiSearchText(item).includes(needle);
    if (chatState.emojiCategory === 'recent') return recent.includes(item.emoji);
    return item.category === chatState.emojiCategory || (chatState.emojiCategory === 'people-body' && item.category === 'component');
  }).slice(0, 240);
  grid.innerHTML = filtered.map((item, index) => `<button type="button" role="gridcell" tabindex="${index === 0 ? '0' : '-1'}" data-chat-emoji="${esc(item.emoji)}" aria-label="${esc(item.names?.[i18nActiveLocale] || item.names?.en || item.emoji)}">${esc(item.emoji)}</button>`).join('')
    || `<div class="chat-emoji-empty">${esc(t('searchHistory.emptyResults'))}</div>`;
}

async function openChatEmojiPicker() {
  const panel = document.getElementById(panelDomId(chatItemId));
  const picker = panel?.querySelector('[data-chat-emoji-picker]');
  if (!picker) return false;
  const catalog = await loadChatEmojiCatalog();
  if (!chatRecentEmoji().length && chatState.emojiCategory === 'recent') chatState.emojiCategory = 'smileys-emotion';
  chatEmojiOverlayController.open(picker, {trigger: panel.querySelector('[data-chat-emoji-button]'), closeOnBlur: false});
  const search = picker.querySelector('[data-chat-emoji-search]');
  renderChatEmojiGrid(panel, catalog, search?.value || '');
  search?.focus();
  return true;
}

function closeChatEmojiPicker(options = {}) {
  chatEmojiOverlayController.close({returnFocus: options.returnFocus !== false});
}

function insertChatEmoji(glyph) {
  const input = document.querySelector(`#${cssEscape(panelDomId(chatItemId))} [data-chat-input]`);
  conversationInsertAtSelection(input, glyph);
  rememberChatEmoji(glyph);
}

function chatEmojiGridMove(grid, current, key) {
  const cells = [...grid.querySelectorAll('[data-chat-emoji]')];
  const index = Math.max(0, cells.indexOf(current));
  const columns = Math.max(1, Math.round(grid.clientWidth / Math.max(1, current.getBoundingClientRect().width)));
  const inlineDirection = getComputedStyle(grid).direction === 'rtl' ? -1 : 1;
  const delta = {ArrowLeft: -inlineDirection, ArrowRight: inlineDirection, ArrowUp: -columns, ArrowDown: columns}[key] || 0;
  const target = cells[Math.max(0, Math.min(cells.length - 1, index + delta))];
  if (!target) return false;
  cells.forEach(cell => { cell.tabIndex = cell === target ? 0 : -1; });
  target.focus();
  return true;
}

function chatEmojiCategoriesHtml() {
  return chatEmojiCategories.map(([category, glyph]) => {
    const label = category === 'recent' ? t('palette.group.recent') : t(`chat.emoji.category.${category}`);
    return `<button type="button" data-action="chat-emoji-category" data-chat-emoji-category="${esc(category)}" class="${category === chatState.emojiCategory ? 'active' : ''}" aria-pressed="${category === chatState.emojiCategory ? 'true' : 'false'}" title="${esc(label)}" aria-label="${esc(label)}">${esc(glyph)}</button>`;
  }).join('');
}

function chatSearchResultsHtml() {
  if (!chatState.searchOpen) return '';
  if (!chatState.searchResults.length) return `<div class="chat-search-empty">${esc(t('searchHistory.emptyResults'))}</div>`;
  return chatState.searchResults.map(context => {
    const message = context.target || {};
    const body = chatHighlightedSnippetHtml(message.body, chatState.searchQuery);
    const before = (context.before || []).at(-1);
    const after = (context.after || [])[0];
    const surrounding = [before, after].filter(Boolean).map(item => `<span>${esc(chatNotificationSnippet(item.body, 80))}</span>`).join('');
    return `<button type="button" class="chat-search-result" data-action="chat-search-result" data-chat-search-result="${esc(message.id)}">
      <span class="chat-search-result-head"><strong>${esc(message.username || '')}</strong><time data-chat-created-at="${esc(message.created_at_utc)}">${esc(chatMessageTimestamp(message.created_at_utc))}</time></span>
      <span>${body}</span><span class="chat-search-context">${surrounding}</span>
    </button>`;
  }).join('');
}

function chatHighlightedSnippetHtml(body, query) {
  const text = chatNotificationSnippet(body, 160);
  const needle = String(query || '');
  if (!needle) return esc(text);
  const index = text.toLocaleLowerCase(i18nActiveLocale).indexOf(needle.toLocaleLowerCase(i18nActiveLocale));
  if (index < 0) return esc(text);
  return `${esc(text.slice(0, index))}<mark>${esc(text.slice(index, index + needle.length))}</mark>${esc(text.slice(index + needle.length))}`;
}

function renderChatSearchResults(panel = document.getElementById(panelDomId(chatItemId))) {
  const results = panel?.querySelector('[data-chat-search-results]');
  if (!results) return;
  results.hidden = !chatState.searchOpen;
  results.innerHTML = chatSearchResultsHtml();
  results.parentElement?.classList.toggle('search-open', chatState.searchOpen);
}

function openChatSearch(panel = document.getElementById(panelDomId(chatItemId))) {
  if (!panel) return false;
  chatState.searchVisible = true;
  renderChatPanel();
  return focusPanelSearchInput(panel, '[data-chat-search]', {panelSelector: '.chat-panel', select: true});
}

async function searchChatHistory(query, options = {}) {
  const text = String(query || '').trim();
  chatState.searchOpen = true;
  if (!text) {
    chatState.searchResults = [];
    renderChatPanel();
    return false;
  }
  const generation = ++chatState.searchGeneration;
  chatState.searchQuery = text;
  const params = new URLSearchParams({query: text, limit: '20'});
  if (options.next === true && chatState.searchCursor) params.set('cursor', chatState.searchCursor);
  try {
    const payload = await apiFetchJson(`/api/chat/search?${params}`, chatRequestOptions());
    if (generation !== chatState.searchGeneration) return false;
    chatState.searchResults = options.next === true ? [...chatState.searchResults, ...(payload.hits || [])] : (payload.hits || []);
    chatState.searchCursor = String(payload.next_cursor || '');
    renderChatPanel();
    return true;
  } catch (error) {
    statusErr(localizedHtml('common.searchFailed'));
    return false;
  }
}

async function openChatMessageContext(messageId) {
  const panel = document.getElementById(panelDomId(chatItemId));
  const timeline = panel?.querySelector('[data-chat-timeline]');
  if (!chatState.searchSnapshot && timeline) chatState.searchSnapshot = {messages: new Map(chatState.messages), scrollTop: timeline.scrollTop};
  try {
    const generation = ++chatState.contextGeneration;
    const payload = await apiFetchJson(`/api/chat/context?${new URLSearchParams({message_id: String(messageId), before: '3', after: '3'})}`, chatRequestOptions());
    if (generation !== chatState.contextGeneration) return false;
    chatState.messages = new Map([...(payload.before || []), payload.target, ...(payload.after || [])].filter(Boolean).map(message => [Number(message.id), message]));
    chatState.followTail = false;
    renderChatPanel();
    panel?.querySelector(`[data-chat-message-id="${cssEscape(String(messageId))}"]`)?.scrollIntoView?.({block: 'center'});
    return true;
  } catch (error) {
    statusErr(localizedHtml('common.requestFailed'));
    return false;
  }
}

function closeChatSearch() {
  const panel = document.getElementById(panelDomId(chatItemId));
  const snapshot = resetChatSearchState();
  if (snapshot) {
    chatState.messages = snapshot.messages;
    renderChatPanel();
    const timeline = panel?.querySelector('[data-chat-timeline]');
    if (timeline) {
      timeline.scrollTop = snapshot.scrollTop;
      chatState.followTail = chatTimelineIsNearBottom(timeline);
    }
  } else {
    renderChatPanel();
  }
}

function resetChatSearchState() {
  const snapshot = chatState.searchSnapshot;
  chatState.searchOpen = false;
  chatState.searchVisible = false;
  chatState.searchResults = [];
  chatState.searchCursor = '';
  chatState.searchQuery = '';
  chatState.searchGeneration += 1;
  chatState.searchSnapshot = null;
  return snapshot;
}

function bindChatPanel(panel) {
  bindActionDispatcher(panel, {
    'chat-emoji-category': (_event, target) => {
      chatState.emojiCategory = target.dataset.chatEmojiCategory || 'recent';
      panel.querySelector('[data-chat-emoji-categories]').innerHTML = chatEmojiCategoriesHtml();
      loadChatEmojiCatalog().then(catalog => renderChatEmojiGrid(panel, catalog, panel.querySelector('[data-chat-emoji-search]')?.value || ''));
    },
    'chat-emoji-picker-open': () => openChatEmojiPicker(),
    'chat-emoji-picker-close': () => closeChatEmojiPicker(),
    'chat-new-messages': () => chatScrollTimelineToBottom(panel),
    'chat-search-close': () => closeChatSearch(),
    'chat-search-result': (_event, target) => openChatMessageContext(target.dataset.chatSearchResult),
    'chat-retry': (_event, target) => {
      const pending = chatState.pending.get(target.dataset.chatRetry || '');
      if (pending) sendChatPending(pending);
    },
  });
  panel.addEventListener('submit', event => {
    if (event.target.matches('[data-chat-form]')) {
      event.preventDefault();
      submitChatDraft();
    } else if (event.target.matches('[data-chat-search-form]')) {
      event.preventDefault();
      searchChatHistory(event.target.querySelector('[data-chat-search]')?.value || '');
    }
  });
  panel.addEventListener('input', event => {
    if (event.target.matches('[data-chat-input]')) {
      chatState.draft = event.target.value || '';
      autosizeChatComposer(panel);
      setChatTyping(Boolean(chatState.draft));
      chatAcknowledge();
    } else if (event.target.matches('[data-chat-emoji-search]')) {
      loadChatEmojiCatalog().then(catalog => renderChatEmojiGrid(panel, catalog, event.target.value || ''));
    }
  });
  panel.addEventListener('keydown', event => {
    if (event.target.matches('[data-chat-input]') && event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submitChatDraft();
      return;
    }
    const cell = event.target.closest('[data-chat-emoji]');
    if (cell && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      chatEmojiGridMove(cell.closest('[role="grid"]'), cell, event.key);
    } else if (cell && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      insertChatEmoji(cell.dataset.chatEmoji || '');
    }
  });
  panel.addEventListener('click', () => chatAcknowledge());
  panel.addEventListener('click', event => {
    const media = event.target.closest('[data-chat-media-url]');
    if (media) {
      event.preventDefault();
      showChatMediaActions(media, event.clientX, event.clientY);
    }
    const emoji = event.target.closest('[data-chat-emoji]');
    if (emoji) insertChatEmoji(emoji.dataset.chatEmoji || '');
  });
  panel.addEventListener('contextmenu', event => {
    const media = event.target.closest('[data-chat-media-url]');
    if (!media) return;
    event.preventDefault();
    event.stopPropagation();
    showChatMediaActions(media, event.clientX, event.clientY);
  });
  panel.addEventListener('focusout', event => {
    if (event.target.matches('[data-chat-input]')) setChatTyping(false);
  });
  panel.addEventListener('wheel', event => {
    const timeline = event.target.closest('[data-chat-timeline]');
    if (timeline && event.deltaY < 0 && timeline.scrollTop <= 2) {
      chatState.olderRequested = true;
      loadOlderChatMessages();
    }
  }, {passive: true});
  panel.addEventListener('scroll', event => {
    const timeline = event.target.closest?.('[data-chat-timeline]');
    if (!timeline) return;
    syncChatTailState(panel, timeline);
    if (timeline.scrollTop > 80 || timeline.scrollHeight <= timeline.clientHeight) return;
    chatState.olderRequested = true;
    loadOlderChatMessages();
  }, {capture: true, passive: true});
}

function autosizeChatComposer(panel = document.getElementById(panelDomId(chatItemId))) {
  const pane = panel?.querySelector('.chat-pane');
  const input = panel?.querySelector('[data-chat-input]');
  const composer = panel?.querySelector('[data-chat-form]');
  const controls = panel?.querySelector('.conversation-composer-controls');
  if (!pane || !input || !composer) return 0;
  const style = getComputedStyle(composer);
  const inputStyle = getComputedStyle(input);
  const fixedHeight = (controls?.offsetHeight || 0)
    + (Number.parseFloat(style.paddingTop) || 0)
    + (Number.parseFloat(style.paddingBottom) || 0)
    + (Number.parseFloat(style.rowGap || style.gap) || 0)
    + (Number.parseFloat(style.borderTopWidth) || 0)
    + (Number.parseFloat(style.borderBottomWidth) || 0);
  const minimumHeight = Number.parseFloat(inputStyle.minHeight) || 0;
  return conversationAutosizeTextarea(input, Math.max(minimumHeight, (pane.clientHeight / 2) - fixedHeight));
}

function installChatComposerResizeObserver(panel) {
  if (!panel) return false;
  panel?._chatComposerResizeObserver?.disconnect?.();
  panel._chatComposerResizeObserver = null;
  const pane = panel?.querySelector('.chat-pane');
  if (!pane || typeof ResizeObserver !== 'function') return false;
  panel._chatComposerResizeObserver = new ResizeObserver(() => {
    autosizeChatComposer(panel);
    if (chatState.followTail) chatScrollTimelineToBottom(panel);
    else syncChatTailState(panel);
  });
  panel._chatComposerResizeObserver.observe(pane);
  return true;
}

function syncChatHistoryObserver(panel = document.getElementById(panelDomId(chatItemId))) {
  const target = panel?.querySelector('[data-chat-history-sentry]');
  if (chatState.olderObserverTarget === target && chatState.olderObserver) return true;
  chatState.olderObserver?.disconnect?.();
  chatState.olderObserver = null;
  chatState.olderObserverTarget = null;
  if (!target || typeof IntersectionObserver !== 'function') return false;
  chatState.olderObserver = new IntersectionObserver(entries => {
    if (!chatState.olderRequested || !entries.some(entry => entry.isIntersecting)) return;
    loadOlderChatMessages();
  }, {root: panel.querySelector('[data-chat-timeline]'), threshold: 0});
  chatState.olderObserver.observe(target);
  chatState.olderObserverTarget = target;
  return true;
}

function createChatPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel info-panel chat-panel';
  panel.id = panelDomId(chatItemId);
  panel.innerHTML = panelFrameHtml({
    item: chatItemId,
    controlsHtml: virtualPanelInnerControlsHtml(chatItemId),
    afterHeadHtml: `<div class="info-actions-bar chat-actions-bar" data-chat-search-bar hidden>
      <form data-chat-search-form role="search"><input type="search" class="search-history-input" data-chat-search placeholder="${esc(t('chat.search.placeholder'))}" aria-label="${esc(t('common.search'))}"></form>
      <button type="button" data-action="chat-search-close" data-chat-search-close title="${esc(t('common.close'))}" aria-label="${esc(t('common.close'))}">×</button>
    </div>`,
    bodyClass: 'info-pane chat-pane',
    bodyHtml: `<div class="chat-history-search-split">
        <div class="chat-search-results" data-chat-search-results hidden></div>
        <div class="chat-timeline" data-chat-timeline role="log" aria-live="off" aria-label="${esc(t('chat.timeline.label'))}"></div>
      </div>
      <div class="a11y-only" data-chat-live role="status" aria-live="polite" aria-atomic="true"></div>
      <button type="button" class="chat-new-messages" data-action="chat-new-messages" data-chat-new-messages hidden>${esc(t('chat.newMessages'))}</button>
      <div class="chat-typing" data-chat-typing role="status" aria-live="polite" hidden></div>
      ${conversationComposerHtml({formClassName: 'yochat-composer', formAttributes: 'data-chat-form', inputHtml: `<textarea class="conversation-composer-input yochat-input" data-chat-input rows="2" placeholder="${esc(t('common.message'))}" aria-label="${esc(t('common.message'))}"></textarea>`, leadingControlsHtml: `<button type="button" class="chat-emoji-button" data-action="chat-emoji-picker-open" data-chat-emoji-button aria-haspopup="dialog" aria-expanded="false" title="${esc(t('chat.emoji.open'))}" aria-label="${esc(t('chat.emoji.open'))}">☺</button>`, sendHtml: conversationSendButtonHtml({className: 'yoagent-chat-send', title: t('yoagent.action.send')})})}
      <div class="chat-emoji-picker" data-chat-emoji-picker role="dialog" aria-modal="true" aria-label="${esc(t('chat.emoji.label'))}" hidden>
        <div class="chat-emoji-head"><input type="search" class="search-history-input" data-chat-emoji-search placeholder="${esc(t('common.search'))}" aria-label="${esc(t('common.search'))}"><button type="button" data-action="chat-emoji-picker-close" data-chat-emoji-close aria-label="${esc(t('common.close'))}">×</button></div>
        <nav class="chat-emoji-categories" data-chat-emoji-categories aria-label="${esc(t('chat.emoji.categories'))}">${chatEmojiCategoriesHtml()}</nav>
        <div class="chat-emoji-grid" data-chat-emoji-grid role="grid" aria-label="${esc(t('chat.emoji.grid'))}"></div>
      </div>`,
  });
  bindPanelShell(panel, chatItemId);
  bindChatPanel(panel);
  return panel;
}

function mountChatPanel() {
  if (shareViewMode) return false;
  chatRequestOptions();
  syncChatHistoryObserver();
  renderChatPanel();
  const installComposer = () => {
    const panel = document.getElementById(panelDomId(chatItemId));
    if (!panel) return false;
    autosizeChatComposer(panel);
    return installChatComposerResizeObserver(panel);
  };
  if (!installComposer()) requestAnimationFrame(installComposer);
  resetRuntimeInterval('chat-relative-times', () => refreshChatRelativeTimes(), chatRelativeTimeRefreshMs);
  loadChatDelta();
  return true;
}

function relocalizeChatPanel(panel = document.getElementById(panelDomId(chatItemId))) {
  if (!panel) return false;
  relocalizeVirtualPanelChrome(panel, chatTabLabel());
  const search = panel.querySelector('[data-chat-search]');
  if (search) {
    search.placeholder = t('chat.search.placeholder');
    search.setAttribute('aria-label', t('common.search'));
  }
  const input = panel.querySelector('[data-chat-input]');
  if (input) {
    input.placeholder = t('common.message');
    input.setAttribute('aria-label', t('common.message'));
  }
  chatState.timelineSignature = '';
  renderChatPanel();
  return true;
}

function handleChatInvalidation(type) {
  if (type === 'chat_typing_changed') return loadChatBootstrap({incoming: false});
  return loadChatDelta();
}

function clearChatLifecycle(options = {}) {
  if (chatState.typingTimer) clearTimeout(chatState.typingTimer);
  chatState.typingTimer = null;
  if (chatState.typingExpiryTimer) clearTimeout(chatState.typingExpiryTimer);
  chatState.typingExpiryTimer = null;
  if (chatState.typingActive) setChatTyping(false, {keepalive: options.keepalive === true});
  closeChatEmojiPicker({returnFocus: false});
  if (options.destroy === true) {
    const panel = document.getElementById(panelDomId(chatItemId));
    panel?._chatComposerResizeObserver?.disconnect?.();
    if (panel) panel._chatComposerResizeObserver = null;
    chatState.requestGeneration += 1;
    chatState.olderGeneration += 1;
    chatState.contextGeneration += 1;
    chatState.searchGeneration += 1;
    chatState.loadingRequest = null;
    chatState.requestController?.abort?.();
    chatState.requestController = null;
    chatState.olderObserver?.disconnect?.();
    chatState.olderObserver = null;
    chatState.olderObserverTarget = null;
    chatState.olderRequested = false;
    chatState.followTail = true;
    chatState.timelineSignature = '';
    clearRuntimeInterval('chat-relative-times');
    resetChatSearchState();
  }
}

function syncChatActiveLifecycle() {
  if (!itemIsActivePaneTab(chatItemId) && chatState.typingActive) setChatTyping(false, {keepalive: true});
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') clearChatLifecycle({keepalive: true});
  else if (chatState.loaded) loadChatDelta();
});
window.addEventListener('pagehide', () => clearChatLifecycle({keepalive: true}));
