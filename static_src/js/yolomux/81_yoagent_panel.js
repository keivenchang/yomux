// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// YO!agent panel rendering, conversation controls, and activity summary refresh split from 80_panes_preferences.js.

function sessionActivitySummary(session) {
  return activitySummaryState.payload?.sessions?.[session] || null;
}

function activitySummaryMarkdownBlockHtml(text, className) {
  return `<div class="${esc(className)} markdown-body" data-yoagent-global-markdown>${esc(text)}</div>`;
}

function activitySummaryLinesHtml(lines, options = {}) {
  const items = Array.isArray(lines) ? lines.filter(Boolean) : [];
  if (!items.length) return options.empty ? `<div class="yoagent-empty">${esc(options.empty)}</div>` : '';
  return items.map(line => activitySummaryMarkdownBlockHtml(String(line), 'yoagent-line')).join('');
}

function yoagentTimestampText(value) {
  const date = value ? new Date(value) : new Date();
  if (!Number.isFinite(date.getTime())) return '';
  return localizedDateTimeFormat(date.getTime() / 1000, {
    timeZone: 'America/Los_Angeles', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  });
}

function yoagentMessageResponseMs(message) {
  const value = Number(message?.responseMs ?? message?.response_ms ?? 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function yoagentMessageLatencyHtml(message) {
  const responseMs = yoagentMessageResponseMs(message);
  if (!responseMs) return '';
  const seconds = responseMs / 1000;
  return `<span class="yoagent-message-latency">${esc(t('yoagent.responseLatency', {seconds: seconds.toFixed(1)}))}</span>`;
}

function yoagentMessageTimestampHtml(value, message = null) {
  const text = yoagentTimestampText(value);
  const latency = message ? yoagentMessageLatencyHtml(message) : '';
  return text ? `<span class="yoagent-message-time">${esc(text)}</span>${latency}` : latency;
}

function yoagentMessageDetailsKey(message, index) {
  return [
    index,
    message?.role || 'assistant',
    message?.kind || '',
    message?.session || '',
    message?.createdAt || '',
  ].map(part => String(part ?? '')).join('|');
}

function yoagentAuxiliaryLines(message) {
  const lines = Array.isArray(message?.auxiliaryLines)
    ? message.auxiliaryLines
    : String(message?.auxiliaryText || '').split(/\r?\n/);
  return lines.map(line => String(line || '').trim()).filter(Boolean);
}

const YOAGENT_THINKING_PREVIEW_WORDS = 50;
const YOAGENT_THINKING_LIVE_PREVIEW_WORDS = 160;

function yoagentAuxiliaryPreviewThinkingLines(text) {
  return String(text || '').split(/\r?\n/).map(line => String(line || '').trim()).filter(Boolean);
}

function yoagentThinkingPreviewText(text, wordLimit = YOAGENT_THINKING_PREVIEW_WORDS) {
  const normalized = String(text || '').replace(/\\n/g, '\n').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  const words = yoagentThinkingWords(normalized);
  const limit = Math.max(1, Number(wordLimit) || YOAGENT_THINKING_PREVIEW_WORDS);
  if (words.length <= limit) return normalized;
  return `… ${words.slice(-limit).join(' ')}`;
}

function yoagentThinkingWords(text) {
  const normalized = String(text || '').replace(/\\n/g, '\n').replace(/\s+/g, ' ').trim();
  return normalized ? normalized.split(/\s+/).filter(Boolean) : [];
}

function yoagentThinkingWordCount(text) {
  return yoagentThinkingWords(text).length;
}

function yoagentThinkingLabel(text, tokenCount = 0) {
  if (tokenCount > 0) return tPlural('yoagent.thinking.tokens', tokenCount);
  return tPlural('yoagent.thinking.words', yoagentThinkingWordCount(text));
}

function yoagentThinkingReadableText(text) {
  return String(text || '')
    .replace(/\\n/g, '\n')
    .replace(/\s+/g, ' ')
    .replace(/[()[\]{}~*.,:;!?\s-]+/g, ' ')
    .trim();
}

function yoagentThinkingIsTokenOnly(text, tokenCount = 0) {
  return Number(tokenCount) > 0 && !yoagentThinkingReadableText(text);
}

function yoagentThinkingTokenOnlyNoteHtml(text, tokenCount = 0) {
  return yoagentThinkingIsTokenOnly(text, tokenCount)
    ? `<div class="yoagent-details-note">${esc(t('yoagent.thinking.tokensOnlyNote'))}</div>`
    : '';
}

function yoagentThinkingDetailsPreview(text, options = {}) {
  if (!options.active || yoagentThinkingIsTokenOnly(text, options.tokenCount)) return '';
  return yoagentThinkingPreviewText(text, YOAGENT_THINKING_LIVE_PREVIEW_WORDS);
}

function yoagentThinkingPreviewActive(message) {
  if (message && Object.prototype.hasOwnProperty.call(message, 'thinkingActive')) {
    return Boolean(message.thinkingActive);
  }
  if (message && Object.prototype.hasOwnProperty.call(message, 'auxiliaryActive')) {
    return Boolean(message.auxiliaryActive);
  }
  return Boolean(message?.streaming);
}

function yoagentDetailsPreviewHtml(preview, className = '') {
  if (!preview) return '';
  const classes = ['yoagent-details-preview', className].filter(Boolean).join(' ');
  return `<span class="${esc(classes)}">${esc(preview)}</span>`;
}

function yoagentToolItemBodyHtml(item) {
  const text = String(item?.text || '').replace(/\\n/g, '\n');
  const command = String(item?.command || '');
  return command && text === command
    ? `<span class="yoagent-tc-command">${esc(command)}</span>`
    : esc(text);
}

function yoagentStreamItemLabel(item) {
  const source = item?.label && typeof item.label === 'object'
    ? item.label
    : {key: item?.labelKey, params: item?.labelParams, fallback: item?.fallback};
  return {
    key: String(source?.key || ''),
    params: source?.params && typeof source.params === 'object' ? {...source.params} : {},
    fallback: String(source?.fallback || ''),
  };
}

function yoagentStreamItems(message) {
  const items = (Array.isArray(message?.streamItems) ? message.streamItems : [])
    .map(item => {
      const text = String(item?.text || '');
      const sourceIndex = Number(item?.sourceIndex);
      const label = yoagentStreamItemLabel(item);
      return {
        kind: String(item?.kind || '').trim(),
        text,
        eventKind: String(item?.eventKind || ''),
        label,
        tokenCount: Math.max(0, Number(item?.tokenCount) || 0),
        toolName: String(item?.toolName || ''),
        command: String(item?.command || ''),
        path: String(item?.path || ''),
        sourceIndex: Number.isFinite(sourceIndex) ? sourceIndex : null,
      };
    })
    .filter(item => ['assistant', 'thinking', 'tool', 'diagnostic'].includes(item.kind)
      && (item.text.trim() || item.label.key || item.label.fallback))
    .map((item, index) => ({...item, sourceIndex: item.sourceIndex == null ? index : item.sourceIndex}));
  const coalesced = [];
  for (const item of items) {
    const previous = coalesced[coalesced.length - 1];
    if (previous && previous.kind === item.kind
      && JSON.stringify(previous.label) === JSON.stringify(item.label)
      && (item.kind === 'thinking' || item.kind === 'tool')) {
      previous.text = `${previous.text.replace(/\s+$/, '')}\n${item.text.replace(/^\s+/, '')}`;
      previous.tokenCount = Math.max(previous.tokenCount, item.tokenCount);
      continue;
    }
    coalesced.push({...item});
  }
  return coalesced;
}

function yoagentPreviewLine(text) {
  return String(text || '').replace(/\\n/g, '\n').split(/\r?\n/).map(line => line.trim()).filter(Boolean)[0] || '';
}

function yoagentStreamAuxiliaryItemHtml(item, key, index, options = {}) {
  const kind = String(item?.kind || '');
  const text = String(item?.text || '');
  if (!['thinking', 'tool', 'diagnostic'].includes(kind)) return '';
  const tool = kind === 'tool';
  const diagnostic = kind === 'diagnostic';
  const descriptorLabel = messageDescriptorText(item.label);
  const label = kind === 'thinking' ? yoagentThinkingLabel(text, item.tokenCount) : descriptorLabel;
  const preview = tool ? yoagentPreviewLine(text) : yoagentThinkingDetailsPreview(text, {...options, tokenCount: item.tokenCount});
  const previewHtml = yoagentDetailsPreviewHtml(preview, tool ? '' : 'yoagent-thinking-live-preview');
  const classes = tool
    ? 'yoagent-message-details yoagent-toolcall-details has-auxiliary yoagent-stream-detail'
    : 'yoagent-message-details has-auxiliary yoagent-stream-detail';
  const streamClasses = tool
    ? 'yoagent-auxiliary-stream yoagent-toolcall-stream'
    : 'yoagent-auxiliary-stream';
  const body = tool ? yoagentToolItemBodyHtml(item) : esc(text);
  const note = tool || diagnostic ? '' : yoagentThinkingTokenOnlyNoteHtml(text, item.tokenCount);
  const streamBody = text && (tool || diagnostic || !yoagentThinkingIsTokenOnly(text, item.tokenCount))
    ? `<pre class="${streamClasses}">${body}</pre>`
    : '';
  const streamIndex = Number.isFinite(Number(item?.sourceIndex)) ? Number(item.sourceIndex) : index;
  return `<details class="${classes}" data-yoagent-message-details-key="${esc(`${key}|stream|${streamIndex}`)}">
    <summary><span>${esc(`${label}…`)}</span>${previewHtml}</summary>
    ${note}${streamBody}
  </details>`;
}

function yoagentMessageStreamItemsHtml(message, key = '') {
  const items = yoagentStreamItems(message);
  if (!items.length) return '';
  const thinkingActive = yoagentThinkingPreviewActive(message);
  return `<div class="yoagent-message-stream">${items.map((item, index) => {
    if (item.kind === 'assistant') {
      return `<div class="conversation-message-body yoagent-message-body markdown-body yoagent-stream-assistant" data-yoagent-markdown>${esc(item.text)}</div>`;
    }
    return yoagentStreamAuxiliaryItemHtml(item, key, index, {active: thinkingActive});
  }).join('')}</div>`;
}

function yoagentMessageDetailsHtml(message, key = '') {
  const text = String(message?.details || '').trim();
  const auxiliaryLines = yoagentAuxiliaryLines(message);
  const auxiliaryText = auxiliaryLines.join('\n');
  const auxiliaryPreviewLines = yoagentAuxiliaryPreviewThinkingLines(message?.auxiliaryPreview || '');
  const auxiliarySourceText = auxiliaryPreviewLines.join('\n') || auxiliaryText;
  const auxiliaryBodyText = auxiliaryText || auxiliarySourceText;
  const thinkingActive = yoagentThinkingPreviewActive(message);
  const auxiliaryPreview = yoagentThinkingDetailsPreview(auxiliarySourceText, {active: thinkingActive});
  const hasAuxiliary = Boolean(auxiliaryBodyText || auxiliaryPreview);
  const truncated = Boolean(message?.auxiliaryTruncated);
  if (!text && !hasAuxiliary) return '';
  const preview = yoagentDetailsPreviewHtml(auxiliaryPreview, 'yoagent-thinking-live-preview');
  const auxiliaryBlock = auxiliaryBodyText && !yoagentThinkingIsTokenOnly(auxiliaryBodyText)
    ? `<pre class="yoagent-auxiliary-stream">${esc(auxiliaryBodyText)}</pre>`
    : '';
  const tokenOnlyNote = auxiliaryBodyText ? yoagentThinkingTokenOnlyNoteHtml(auxiliaryBodyText) : '';
  const truncationNote = truncated
    ? `<div class="yoagent-details-note">${esc(t('yoagent.details.auxiliaryTruncated'))}</div>`
    : '';
  const detailsBlock = text
    ? `<pre class="yoagent-safe-details">${esc(text)}</pre>`
    : '';
  const detailsLabel = hasAuxiliary
    ? yoagentThinkingLabel(auxiliaryBodyText || auxiliaryPreview)
    : t('common.details');
  const thinkingDetails = (text || hasAuxiliary)
    ? `<details class="yoagent-message-details${hasAuxiliary ? ' has-auxiliary' : ''}" data-yoagent-message-details-key="${esc(key)}">
    <summary><span>${esc(`${detailsLabel}…`)}</span>${preview}</summary>
    ${tokenOnlyNote}${auxiliaryBlock}${truncationNote}${detailsBlock}
  </details>`
    : '';
  return thinkingDetails;
}

function yoagentMessageDetailRowsHtml(message, key = '') {
  const rows = Array.isArray(message?.detailRows) ? message.detailRows : [];
  if (!rows.length) return '';
  return `<details class="yoagent-message-details" data-yoagent-message-details-key="${esc(`${key}|metadata`)}">
    <summary><span>${esc(`${t('common.details')}…`)}</span></summary>
    <div class="yoagent-safe-details">${rows.map(row => `<div>${esc(messageDescriptorText(row))}</div>`).join('')}</div>
  </details>`;
}

function relativeActivityGeneratedText(payload = activitySummaryState.payload) {
  const ts = Number(payload?.generated_ts || 0) || Date.parse(payload?.generated_at || '') / 1000;
  if (!Number.isFinite(ts) || ts <= 0) return {text: t('state.notLoaded'), title: ''};
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  // Phase 3: render the relative time with Intl.RelativeTimeFormat(activeLocale) for native
  // locale phrasing, wrapped by the localized "last updated {rel}" string.
  const text = seconds < 60
    ? t('yoagent.updated.justNow')
    : t('yoagent.updated.wrap', {rel: relativeTimeFormat(seconds)});
  let title = payload?.generated_at || '';
  title = localizedDateTimeFormat(ts, {
    timeZone: 'America/Los_Angeles', year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  }) || title;
  return {text, title};
}

function activitySummaryGlobalDetailLines(summary) {
  if (!summary || typeof summary !== 'object') return [];
  const lines = Array.isArray(summary.detail_lines) ? summary.detail_lines.filter(Boolean) : [];
  const messages = Array.isArray(summary.detail_messages)
    ? summary.detail_messages.map(message => messageDescriptorText(message)).filter(Boolean)
    : [];
  return [...lines, ...messages];
}

function globalActivitySummaryHtml() {
  const summary = activitySummaryState.payload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = summary.headline || lines[0] || '';
  const detailLines = activitySummaryGlobalDetailLines(summary);
  const generated = relativeActivityGeneratedText();
  const refreshBar = activitySummaryState.refreshing ? `<div class="yoagent-refresh-progress" aria-label="${esc(t('yoagent.refreshing'))}"></div>` : '';
  return `<section class="yoagent-global" aria-label="${esc(t('yoagent.globalAria', {name: yoagentTabLabel()}))}">
    <div class="yoagent-global-head">
      <span>${esc(yoagentTabLabel())}</span>
      <span class="yoagent-generated" title="${esc(generated.title)}">(${esc(generated.text)})</span>
    </div>
    ${refreshBar}
    ${headline ? activitySummaryMarkdownBlockHtml(headline, 'yoagent-headline') : activitySummaryLinesHtml([], {empty: t('yoagent.emptyGlobal')})}
    ${activitySummaryLinesHtml(detailLines)}
  </section>`;
}

function yoagentStreamingMessagesList() {
  if (!(yoagentConversationState.streamingMessages instanceof Map)) return [];
  return [...yoagentConversationState.streamingMessages.values()]
    .filter(message => message && (message.content || message.streaming || message.aborted || message.details || message.auxiliaryText || message.auxiliaryPreview))
    .sort((a, b) => String(a.createdAt || '').localeCompare(String(b.createdAt || '')));
}

function yoagentAgentResultParts(text) {
  const value = String(text || '');
  const match = value.match(/^([^\r\n]*)(?:\r?\n\s*\r?\n|\r?\n)([\s\S]*)$/);
  if (!match) return {heading: value.trim(), output: ''};
  return {heading: String(match[1] || '').trim(), output: String(match[2] || '').trim()};
}

function yoagentMessageBodyHtml(message, roleClass, agentResult, streaming) {
  const contentFallback = message?.content || (message?.aborted ? t('yoagent.stopped') : (streaming ? t('yoagent.thinking') : ''));
  const content = structuredMessageText(message, 'content', contentFallback);
  if (agentResult) {
    const parts = yoagentAgentResultParts(content);
    const heading = parts.heading
      ? `<div class="yoagent-agent-result-heading markdown-body" data-yoagent-markdown>${esc(parts.heading)}</div>`
      : '';
    const output = parts.output
      ? `<div class="yoagent-agent-result-output markdown-body" data-yoagent-markdown>${esc(parts.output)}</div>`
      : '';
    return `<div class="conversation-message-body yoagent-message-body yoagent-agent-result-body">${heading}${output}</div>`;
  }
  const bodyClass = roleClass === 'assistant' ? 'conversation-message-body yoagent-message-body markdown-body' : 'conversation-message-body yoagent-message-body';
  const markdownAttr = roleClass === 'assistant' ? ' data-yoagent-markdown' : '';
  return `<div class="${bodyClass}"${markdownAttr}>${esc(content)}</div>`;
}

function yoagentChatMessagesHtml() {
  const messages = [...(Array.isArray(yoagentConversationState.messages) ? yoagentConversationState.messages : []), ...yoagentStreamingMessagesList()];
  const startupInfo = yoagentStartupState.infoVisible ? yoagentStartupInfoHtml() : '';
  if (!messages.length) {
    if (startupInfo) return startupInfo;
    if (!yoagentChatEnabled()) {
      return `<div class="yoagent-chat-empty">${esc(t('yoagent.chatDisabled'))}</div>`;
    }
    return `<div class="yoagent-chat-empty">${esc(t('yoagent.chatEmpty', {name: yoagentTabLabel()}))}</div>`;
  }
  const messageHtml = messages.map((message, index) => {
    const role = message.role === 'user' ? t('yoagent.you') : yoagentTabLabel();
    const roleClass = message.role === 'user' ? 'user' : 'assistant';
    const agentResult = roleClass === 'assistant' && message?.kind === 'agent_result';
    const streaming = roleClass === 'assistant' && message?.streaming;
    const messageClass = `yoagent-message ${roleClass}${agentResult ? ' yoagent-agent-result' : ''}${streaming ? ' streaming' : ''}${message?.aborted ? ' stopped' : ''}`;
    const detailsKey = yoagentMessageDetailsKey(message, index);
    const streamItemsHtml = roleClass === 'assistant' ? yoagentMessageStreamItemsHtml(message, detailsKey) : '';
    const stoppedState = roleClass === 'assistant' && message?.aborted && String(message?.content || '').trim()
      ? `<div class="yoagent-message-state">${esc(t('yoagent.stopped'))}</div>`
      : '';
    return conversationMessageShellHtml({
      self: roleClass === 'user',
      className: messageClass,
      author: role,
      timestampHtml: yoagentMessageTimestampHtml(message.createdAt, roleClass === 'assistant' ? message : null),
      bodyHtml: streamItemsHtml || yoagentMessageBodyHtml(message, roleClass, agentResult, streaming),
      extrasHtml: `${stoppedState}${roleClass === 'assistant' && !streamItemsHtml ? yoagentMessageDetailsHtml(message, detailsKey) : ''}${roleClass === 'assistant' ? yoagentMessageDetailRowsHtml(message, detailsKey) : ''}${roleClass === 'assistant' ? yoagentActionCardsHtml(message.actions) : ''}`,
    });
  }).join('');
  return `${messageHtml}${startupInfo}`;
}

function yoagentActionCardsHtml(actions) {
  const items = Array.isArray(actions) ? actions : [];
  return items.map(yoagentActionCardHtml).join('');
}

function yoagentActionCardHtml(action) {
  if (!action || typeof action !== 'object') return '';
  const target = action.target && typeof action.target === 'object' ? action.target : {};
  const status = String(action.status || 'ready');
  const transport = String(target.transport || 'tmux-legacy');
  const transportLabel = yoagentActionTransportText(transport, target.transport_label);
  const canSend = status === 'ready' && action.id && !readOnlyMode && !yoagentChatState.busy;
  const button = canSend
    ? `<button type="button" class="yoagent-action-send" data-action="yoagent-action-send" data-yoagent-action-send="${esc(action.id)}">${esc(t('yoagent.action.send'))}</button>`
    : `<span class="yoagent-action-state">${esc(yoagentActionStatusText(action))}</span>`;
  const rows = [
    [t('common.sessionLabel'), target.session || action.session || ''],
    [t('yoagent.action.row.agent'), [target.agent_kind, target.agent_model].filter(Boolean).join(' ')],
    [t('yoagent.action.row.transport'), transportLabel],
    [t('yoagent.action.row.pane'), target.pane_target || ''],
    [t('common.pathLabel'), target.cwd || ''],
  ].filter(row => row[1]);
  const rowHtml = rows.map(([label, value]) => `<div class="yoagent-action-row"><span>${esc(label)}</span><code>${esc(value)}</code></div>`).join('');
  return `<div class="yoagent-action-card ${esc(status)}" data-yoagent-action-card="${esc(action.id || '')}">
    <div class="yoagent-action-head"><span>${esc(t('yoagent.action.preview'))}</span>${button}</div>
    <div class="yoagent-action-rows">${rowHtml}</div>
    <pre class="yoagent-action-text">${esc(action.text || '')}</pre>
  </div>`;
}

function yoagentActionTransportText(transport, fallback = '') {
  if (transport === 'tmux-legacy' || transport === 'pane-paste') return `${t('yoagent.action.transport.panePasteFallback')} (tmux-legacy)`;
  return transport === 'agent-native-resume'
    ? t('yoagent.action.transport.agentNativeResume')
    : (String(fallback || '') || transport);
}

function yoagentActionStatusText(action) {
  const status = String(action?.status || '');
  return structuredMessageText(action, 'status_text', status);
}

function yoagentActionStatusPatch(key, fallback, params = {}) {
  return structuredMessageSnapshot({status_text: fallback, status_text_key: key, status_text_params: params}, 'status_text');
}

function yoagentActionErrorStatusPatch(error) {
  const snapshot = userMessageSnapshot(error, error?.message || String(error || ''));
  return {
    status: 'error',
    status_text: snapshot.user_message.fallback,
    status_text_key: snapshot.user_message.key,
    status_text_params: snapshot.user_message.params,
    status_diagnostic: snapshot.error,
  };
}

function yoagentStartupActivityPayload() {
  return yoagentStartupState.activityPayload || activitySummaryState.payload;
}

function cloneYoagentActivitySummaryPayload(payload) {
  return JSON.parse(JSON.stringify(payload || {sessions: {}, global: {lines: []}, session_order: []}));
}

function captureYoagentStartupActivitySummarySnapshot(options = {}) {
  if (options.replace !== true && yoagentStartupState.activityPayload) return yoagentStartupState.activityPayload;
  yoagentStartupState.activityPayload = activitySummaryState.payload && typeof activitySummaryState.payload === 'object'
    ? cloneYoagentActivitySummaryPayload(activitySummaryState.payload)
    : {sessions: {}, global: {lines: []}, session_order: []};
  return yoagentStartupState.activityPayload;
}

function resetYoagentStartupActivitySummarySnapshot() {
  yoagentStartupState.activityPayload = null;
}

function yoagentIntroMessageText(payload = yoagentStartupActivityPayload()) {
  const summary = payload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = String(summary.headline || lines[0] || '').trim();
  if (!headline) return '';
  const details = activitySummaryGlobalDetailLines(summary)
    .slice(0, 2)
    .map(line => `- ${String(line).trim()}`);
  return [t('yoagent.intro.now'), '', headline, ...details].filter(Boolean).join('\n');
}

function yoagentIntroMessageHtml() {
  const payload = yoagentStartupActivityPayload();
  const text = yoagentIntroMessageText(payload);
  if (!text || !yoagentChatEnabled()) return '';
  return conversationMessageShellHtml({
    className: 'yoagent-message yoagent-intro-message',
    author: yoagentTabLabel(),
    timestampHtml: yoagentMessageTimestampHtml(payload?.generated_at),
    bodyHtml: `<div class="conversation-message-body yoagent-message-body markdown-body" data-yoagent-markdown>${esc(text)}</div>`,
  });
}

function yoagentStartupInfoHtml() {
  return `${yoagentIntroMessageHtml()}${yoagentRecentAgentsMessageHtml()}`;
}

function showYoagentStartupInfoOnce() {
  if (yoagentStartupState.infoShown) return false;
  captureYoagentStartupActivitySummarySnapshot();
  yoagentStartupState.infoShown = true;
  yoagentStartupState.infoVisible = true;
  return true;
}

function showYoagentStartupInfoForLatestActivity() {
  resetYoagentStartupActivitySummarySnapshot();
  yoagentStartupState.infoShown = false;
  return showYoagentStartupInfoOnce();
}

function hideYoagentStartupInfo() {
  yoagentStartupState.infoVisible = false;
}

function yoagentNoticeHtml() {
  if (!yoagentChatState.notice?.reason) return '';
  const backendText = yoagentChatState.notice.backend ? yoagentBackendLabel(yoagentChatState.notice.backend) : '';
  const backend = backendText ? `<span class="yoagent-chat-notice-backend">${esc(backendText)}</span> ` : '';
  const reason = typeof yoagentChatState.notice.reason === 'object'
    ? messageDescriptorText(yoagentChatState.notice.reason)
    : String(yoagentChatState.notice.reason || '');
  return `<div class="yoagent-chat-notice">${backend}${esc(reason)}</div>`;
}

function yoagentFallbackNotice(payload = {}) {
  return {
    backend: String(payload.backend_used || payload.backend || ''),
    reason: messageFieldDescriptor(payload, 'fallback_reason'),
  };
}

function yoagentPendingWaitsHtml() {
  const waits = Array.isArray(yoagentConversationState.pendingWaits) ? yoagentConversationState.pendingWaits : [];
  if (!waits.length) return '';
  const title = tPlural('yoagent.waiting.count', waits.length);
  const rows = waits.map(wait => {
    const session = String(wait?.session || '');
    const handoff = wait?.handoff && typeof wait.handoff === 'object' ? wait.handoff : null;
    const handoffTarget = String(handoff?.session || '');
    const handoffSource = String(handoff?.source_session || session);
    const sourceRegarding = String(handoff?.source_regarding || t('yoagent.waiting.currentRequest'));
    const targetRegarding = String(handoff?.target_regarding || t('yoagent.waiting.nextRequest'));
    const label = handoffTarget
      ? t('yoagent.waiting.handoff', {source: handoffSource, target: handoffTarget, sourceRegarding, targetRegarding})
      : (session ? t('yoagent.waiting.session', {session}) : String(wait?.label || t('yoagent.waiting.generic')));
    const startedTs = Number(wait?.started_ts || 0);
    const age = Number.isFinite(startedTs) && startedTs > 0
      ? compactRelativeTimeFormat(Math.max(0, Math.round(Date.now() / 1000 - startedTs)))
      : '';
    const transcript = String(wait?.transcript || '');
    const id = String(wait?.id || '');
    const clearButton = id && !readOnlyMode
      ? `<button type="button" class="yoagent-waiting-clear btn-base yoagent-compact-action" data-action="yoagent-wait-clear" data-yoagent-wait-clear="${esc(id)}" title="${esc(t('common.clear'))}" aria-label="${esc(t('common.clear'))}">${esc(t('common.clear'))}</button>`
      : '';
    return `<li class="yoagent-waiting-item yoagent-compact-item" title="${esc(transcript)}">
      <span class="yoagent-waiting-label yoagent-compact-label">${textWithMovingEllipsisHtml(label, 'yoagent-waiting-dots')}</span>
      ${age ? `<span class="yoagent-waiting-age">${esc(age)}</span>` : ''}
      ${clearButton}
    </li>`;
  }).join('');
  return `<div class="yoagent-waiting-queue" aria-live="polite" aria-label="${esc(title)}">
    <div class="yoagent-waiting-title yoagent-section-title">${esc(title)}</div>
    <ul class="yoagent-waiting-list yoagent-compact-list">${rows}</ul>
  </div>`;
}

function setYoagentJobs(items, options = {}) {
  if (options.invalidateRequest !== false) yoagentJobsState.guard.invalidate();
  yoagentJobsState.items = Array.isArray(items) ? items : [];
  return yoagentJobsState.items;
}

function applyYoagentJobsPayload(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  setYoagentJobs(payload.jobs, {invalidateRequest: options.source !== 'request'});
  return true;
}

function yoagentJobTargetText(job) {
  const target = job?.target && typeof job.target === 'object' ? job.target : {};
  if (Array.isArray(target.roster) && target.roster.length) return target.roster.map(item => String(item || '')).filter(Boolean).join(', ');
  return String(target.session || job?.session || '');
}

function yoagentJobActionText(job) {
  const action = job?.action && typeof job.action === 'object' ? job.action : {};
  return String(job?.public_text || job?.prompt_preview || action.text_preview || action.text || action.message || '').trim();
}

const YOAGENT_JOB_CLASSIFICATION_KEYS = Object.freeze({
  status: Object.freeze({
    queued: 'yoagent.jobs.status.queued',
    pending_confirmation: 'yoagent.jobs.status.pendingConfirmation',
    fired: 'yoagent.jobs.status.fired',
    failed: 'yoagent.jobs.status.failed',
    cancelled: 'yoagent.jobs.status.cancelled',
    timed_out: 'yoagent.jobs.status.timedOut',
  }),
  type: Object.freeze({
    notify_session_idle: 'yoagent.jobs.type.notifySessionIdle',
    notify_session_needs_input: 'yoagent.jobs.type.notifySessionNeedsInput',
    notify_session_blocked: 'yoagent.jobs.type.notifySessionBlocked',
    notify_session_done_after_working: 'yoagent.jobs.type.notifySessionDoneAfterWorking',
    notify_all_idle: 'yoagent.jobs.type.notifyAllIdle',
    wait_then_send: 'yoagent.jobs.type.waitThenSend',
    result_watch: 'yoagent.jobs.type.resultWatch',
  }),
});

function yoagentJobClassificationText(classification, value) {
  const kind = String(classification || '');
  const normalized = String(value || '');
  const key = YOAGENT_JOB_CLASSIFICATION_KEYS[kind]?.[normalized] || `yoagent.jobs.${kind}.unknown`;
  return t(key);
}

function yoagentJobRowsHtml() {
  const jobs = Array.isArray(yoagentJobsState.items) ? yoagentJobsState.items : [];
  if (!jobs.length) return '';
  return jobs.map(job => {
    const id = String(job?.id || job?.job_id || '');
    const status = String(job?.status || '');
    const type = String(job?.type || '');
    const target = yoagentJobTargetText(job);
    const actionText = yoagentJobActionText(job);
    const canConfirm = status === 'pending_confirmation' && id && !readOnlyMode;
    const canCancel = ['queued', 'pending_confirmation'].includes(status) && id && !readOnlyMode;
    const blocker = job?.last_observed_state?.blockers && Array.isArray(job.last_observed_state.blockers)
      ? job.last_observed_state.blockers.join(', ')
      : '';
    const error = userMessageText(job?.result?.send || job?.result || job, '');
    const meta = [
      type ? yoagentJobClassificationText('type', type) : '',
      target ? t('yoagent.jobs.target', {target}) : '',
      blocker ? t('yoagent.jobs.blockedBy', {blocker}) : '',
      error,
    ].filter(Boolean).join(' · ');
    const controls = [
      canConfirm ? `<button type="button" class="yoagent-job-confirm" data-action="yoagent-job-confirm" data-yoagent-job-confirm="${esc(id)}">${esc(t('yoagent.jobs.confirm'))}</button>` : '',
      canCancel ? `<button type="button" class="yoagent-job-cancel" data-action="yoagent-job-cancel" data-yoagent-job-cancel="${esc(id)}">${esc(t('common.cancel'))}</button>` : '',
    ].filter(Boolean).join('');
    return `<li class="yoagent-job-item yoagent-job-${esc(status || 'unknown')}" data-yoagent-job-row="${esc(id)}">
      <div class="yoagent-job-main">
        <span class="yoagent-job-status">${esc(yoagentJobClassificationText('status', status))}</span>
        <span class="yoagent-job-meta">${esc(meta)}</span>
      </div>
      ${actionText ? `<div class="yoagent-job-text">${esc(actionText)}</div>` : ''}
      ${controls ? `<div class="yoagent-job-controls">${controls}</div>` : ''}
    </li>`;
  }).join('');
}

function yoagentJobsHtml() {
  const rows = yoagentJobRowsHtml();
  if (!rows) return '';
  return `<div class="yoagent-jobs-list" aria-live="polite" aria-label="${esc(t('yoagent.jobs.title'))}">
    <div class="yoagent-jobs-title yoagent-section-title">${esc(t('yoagent.jobs.title'))}</div>
    <ul class="yoagent-jobs-items">${rows}</ul>
  </div>`;
}

function yoagentChatQueueHtml() {
  const items = Array.isArray(yoagentChatState.queue) ? yoagentChatState.queue : [];
  if (!items.length) return '';
  const rows = items.map((item, index) => {
    const id = String(item?.id || '');
    const text = String(item?.text || '');
    const label = text.length > 180 ? `${text.slice(0, 177)}...` : text;
    return `<li class="yoagent-chat-queue-item yoagent-compact-item" data-yoagent-chat-queue-row="${esc(id)}">
      <span class="yoagent-chat-queue-index">${esc(String(index + 1))}</span>
      <span class="yoagent-chat-queue-text yoagent-compact-label">${esc(label)}</span>
      <button type="button" class="yoagent-chat-queue-cancel btn-base yoagent-compact-action" data-action="yoagent-queued-cancel" data-yoagent-queued-cancel="${esc(id)}" title="${esc(t('common.cancel'))}" aria-label="${esc(t('common.cancel'))}">${esc(t('common.cancel'))}</button>
    </li>`;
  }).join('');
  return `<div class="yoagent-chat-queue" aria-live="polite" aria-label="${esc(t('yoagent.queue.title'))}">
    <div class="yoagent-chat-queue-title yoagent-section-title">${esc(t('yoagent.queue.title'))}</div>
    <ul class="yoagent-chat-queue-items yoagent-compact-list">${rows}</ul>
  </div>`;
}

function applyYoagentConversationPayload(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  if (!Object.prototype.hasOwnProperty.call(payload, 'messages')) return false;
  if (options.source !== 'request') yoagentConversationState.guard.invalidate();
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const hadPendingWaits = Array.isArray(yoagentConversationState.pendingWaits) && yoagentConversationState.pendingWaits.length > 0;
  yoagentConversationState.pendingWaits = Array.isArray(payload.pending_waits) ? payload.pending_waits : [];
  if (messages.length && yoagentConversationState.streamingMessages instanceof Map) yoagentConversationState.streamingMessages.clear();
  yoagentConversationState.messages = messages;
  yoagentConversationState.path = String(payload.transcript_path || '');
  yoagentConversationState.displayPath = String(payload.transcript_display_path || yoagentConversationState.path);
  yoagentConversationState.loaded = true;
  yoagentConversationState.loading = false;
  if (hadPendingWaits && !yoagentConversationState.pendingWaits.length && yoagentChatState.queue.length) {
    if (typeof queueMicrotask === 'function') queueMicrotask(() => drainYoagentChatQueue());
    else Promise.resolve().then(() => drainYoagentChatQueue());
  }
  return true;
}

function applyYoagentStreamPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const streamId = String(payload.stream_id || '').trim();
  if (!streamId) return false;
  yoagentConversationState.guard.invalidate();
  if (!(yoagentConversationState.streamingMessages instanceof Map)) yoagentConversationState.streamingMessages = new Map();
  const createdAt = String(payload.created_at || new Date().toISOString());
  const content = String(payload.content || '');
  const phase = String(payload.phase || '');
  const hiddenThinking = Boolean(payload.hidden_thinking_removed);
  const previous = yoagentConversationState.streamingMessages.get(streamId) || {};
  const auxiliaryLines = Array.isArray(payload.auxiliary_lines)
    ? payload.auxiliary_lines.map(line => String(line || '')).filter(Boolean)
    : (Array.isArray(previous.auxiliaryLines) ? previous.auxiliaryLines : []);
  const streamItems = Array.isArray(payload.stream_items)
    ? yoagentStreamItems({streamItems: payload.stream_items})
    : yoagentStreamItems(previous);
  const thinkingActive = Boolean(payload.hidden_work_active);
  const toolActive = Boolean(payload.tool_active);
  const auxiliaryActive = Boolean(thinkingActive || toolActive);
  const thinkingItems = streamItems.filter(item => item.kind === 'thinking');
  const auxiliaryPreviewLines = thinkingItems.map(item => item.text).filter(Boolean);
  const auxiliaryPreview = yoagentThinkingPreviewText(
    auxiliaryPreviewLines.join('\n')
    || String(previous.auxiliaryPreview || ''),
  );
  const detailRows = [];
  if (payload.backend) detailRows.push({key: 'yoagent.details.backend', params: {backend: payload.backend}, fallback: `backend: ${payload.backend}`});
  if (phase) detailRows.push({key: 'yoagent.details.streamPhase', params: {phase}, fallback: `stream phase: ${phase}`});
  if (hiddenThinking) detailRows.push({key: 'yoagent.details.hiddenThinking', params: {}, fallback: 'raw model thinking was hidden'});
  if (payload.auxiliary_truncated) detailRows.push({key: 'yoagent.details.auxiliaryTruncated', params: {}, fallback: 'auxiliary stream truncated'});
  yoagentConversationState.streamingMessages.set(streamId, {
    role: 'assistant',
    content: content || previous.content || '',
    createdAt: previous.createdAt || createdAt,
    detailRows: detailRows.length ? detailRows : (Array.isArray(previous.detailRows) ? previous.detailRows : []),
    auxiliaryLines,
    auxiliaryText: auxiliaryLines.join('\n') || previous.auxiliaryText || '',
    auxiliaryPreview,
    streamItems,
    thinkingActive,
    toolActive,
    auxiliaryActive,
    auxiliaryDone: Boolean(payload.auxiliary_done) || previous.auxiliaryDone || false,
    auxiliaryTruncated: Boolean(payload.auxiliary_truncated) || previous.auxiliaryTruncated || false,
    aborted: Boolean(payload.aborted) || previous.aborted || false,
    streaming: payload.done !== true,
  });
  yoagentStartupState.prewarming = payload.done === true ? false : yoagentStartupState.prewarming;
  return true;
}

function resetYoagentComposerHistory() {
  yoagentChatState.historyCursor = null;
  yoagentChatState.historyDraft = '';
}

function yoagentUserMessageHistory() {
  return (Array.isArray(yoagentConversationState.messages) ? yoagentConversationState.messages : [])
    .filter(message => message?.role === 'user')
    .map(message => String(message.content || '').trim())
    .filter(Boolean);
}

function setYoagentChatInputValue(input, value) {
  if (!input) return;
  const nextValue = String(value || '');
  input.value = nextValue;
  yoagentChatState.draft = nextValue;
  const end = nextValue.length;
  try { input.setSelectionRange(end, end); } catch (_) {}
}

function yoagentNavigateChatHistory(input, direction) {
  if (!input || input.disabled) return false;
  const history = yoagentUserMessageHistory();
  const latest = yoagentChatState.historyCursor === null;
  if (direction === 'up') {
    if (!history.length) return false;
    if (latest) {
      yoagentChatState.historyDraft = input.value || yoagentChatState.draft || '';
      yoagentChatState.historyCursor = history.length - 1;
    } else {
      yoagentChatState.historyCursor = Math.max(0, Math.min(history.length - 1, Number(yoagentChatState.historyCursor) - 1));
    }
    setYoagentChatInputValue(input, history[yoagentChatState.historyCursor] || '');
    return true;
  }
  if (direction === 'down') {
    if (latest) return false;
    const next = Math.min(history.length, Number(yoagentChatState.historyCursor) + 1);
    if (next >= history.length) {
      yoagentChatState.historyCursor = null;
      setYoagentChatInputValue(input, yoagentChatState.historyDraft);
      yoagentChatState.historyDraft = '';
    } else {
      yoagentChatState.historyCursor = next;
      setYoagentChatInputValue(input, history[yoagentChatState.historyCursor] || '');
    }
    return true;
  }
  return false;
}

function handleYoagentChatHistoryKeydown(event, input) {
  if (!input || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
  const direction = event.key === 'ArrowUp' ? 'up' : (event.key === 'ArrowDown' ? 'down' : '');
  if (!direction || !yoagentNavigateChatHistory(input, direction)) return false;
  event.preventDefault();
  event.stopPropagation();
  return true;
}

function yoagentTranscriptPathHtml() {
  if (readOnlyMode) return '';
  const path = yoagentConversationState.path || '';
  const display = yoagentConversationState.displayPath || path;
  if (!path && !yoagentConversationState.loading && !yoagentConversationState.loaded) return '';
  const value = path
    ? `<code class="yoagent-transcript-value" title="${esc(path)}">${esc(display)}</code>${pathCopyButtonHtml(path, {className: 'yoagent-transcript-copy', title: t('common.copyTranscriptPath')})}`
    : `<span class="yoagent-transcript-loading">${esc(t('yoagent.transcript.loading'))}</span>`;
  return `<div class="yoagent-transcript-path">
    <span class="yoagent-transcript-label">${esc(t('common.transcript'))}</span>
    ${value}
  </div>`;
}

function yoagentRecentAgentActivityText(agent) {
  const signal = yoagentRecentAgentSignal(agent);
  if (signal.pane?.dead === true) return tmuxSignalDeadText(signal.pane);
  const activityTs = tmuxSignalWindowActivityTs(signal.window);
  if (activityTs > 0) {
    const seconds = Math.max(0, Math.round(Date.now() / 1000 - activityTs));
    return t('yoagent.recent.tmuxActivity', {time: compactRelativeTimeFormat(seconds)});
  }
  if (agent?.running === true) return t('yoagent.agent.running');
  const ts = Number(agent?.last_used_ts || agent?.sort_ts || 0);
  if (!Number.isFinite(ts) || ts <= 0) return '';
  return compactRelativeTimeFormat(Math.max(0, Math.round(Date.now() / 1000 - ts)));
}

function yoagentRecentAgentSortTs(agent, signal = yoagentRecentAgentSignal(agent)) {
  const activityTs = tmuxSignalWindowActivityTs(signal.window);
  if (activityTs > 0) return activityTs;
  const sessionActivityTs = tmuxSignalSessionActivityTs(agent?.session);
  if (sessionActivityTs > 0) return sessionActivityTs;
  const ts = Number(agent?.last_used_ts || agent?.sort_ts || 0);
  return Number.isFinite(ts) ? ts : 0;
}

function yoagentRecentAgentSignal(agent) {
  const target = String(agent?.pane_target || '').trim();
  let pane = target ? tmuxSignalPaneForTarget(target) : null;
  if (!pane) {
    const session = String(agent?.session || '').trim();
    const windowText = String(agent?.window ?? '').trim();
    const paneText = String(agent?.pane ?? '').trim();
    pane = tmuxSignalPanes().find(candidate => (
      tmuxSignalPaneSession(candidate) === session
      && String(candidate?.window_index ?? '').trim() === windowText
      && (!paneText || String(candidate?.pane_index ?? '').trim() === paneText)
      && tmuxSignalPaneIsAgent(candidate)
    )) || null;
  }
  const windowRecord = pane ? tmuxSignalWindowForPane(pane) : tmuxSignalWindowForSessionIndex(agent?.session, agent?.window);
  return {pane, window: windowRecord};
}

function yoagentRecentAgentSignalBadgesHtml(signal) {
  const pane = signal?.pane || null;
  const windowRecord = signal?.window || null;
  const badges = [];
  const deadText = tmuxSignalDeadText(pane);
  if (deadText) badges.push({kind: 'dead', text: deadText});
  if (windowRecord?.bell_flag === true) badges.push({kind: 'bell', text: t('yoagent.recent.bell')});
  if (windowRecord?.silence_flag === true) badges.push({kind: 'silence', text: t('yoagent.recent.silent')});
  const presenceText = tmuxSignalWindowPresenceText(windowRecord);
  if (presenceText) badges.push({kind: 'presence', text: presenceText});
  if (windowRecord?.zoomed === true) badges.push({kind: 'zoom', text: t('yoagent.recent.zoom')});
  for (const label of tmuxSignalPaneModeLabels(pane)) badges.push({kind: 'mode', text: label});
  if (!badges.length) return '';
  return `<span class="yoagent-recent-agent-signals">${badges.map(badge => `<span class="yoagent-recent-agent-signal signal-${esc(badge.kind)}">${esc(badge.text)}</span>`).join('')}</span>`;
}

function yoagentRecentAgentRestartHtml(agent, signal) {
  if (readOnlyMode || signal?.pane?.dead !== true) return '';
  const kind = String(agent?.agent_kind || tmuxSignalPaneCommand(signal.pane) || '').toLowerCase();
  if (!tmuxSignalAgentCommands.has(kind)) return '';
  return `<button type="button" class="yoagent-recent-agent-restart" data-action="yoagent-agent-restart" data-yolomux-agent-restart="${esc(kind)}" title="${esc(t('yoagent.restart.title', {kind: agentLabel(kind)}))}">${esc(t('yoagent.restart'))}</button>`;
}

function yoagentRecentAgentPathText(agent, signal = yoagentRecentAgentSignal(agent)) {
  const rawSignalPath = String(signal?.pane?.current_path || '').trim();
  const signalPath = rawSignalPath ? normalizeDirectoryPath(rawSignalPath) : '';
  if (signalPath) return compactHomePath(signalPath);
  const paths = Array.isArray(agent?.recent_paths)
    ? agent.recent_paths
      .map(item => compactHomePath(item?.path || ''))
      .filter(Boolean)
    : [];
  if (paths.length) {
    const visible = paths.slice(0, 2);
    const extra = paths.length - visible.length;
    return `${visible.join(', ')}${extra > 0 ? ` +${extra}` : ''}`;
  }
  return agent?.cwd ? compactHomePath(agent.cwd) : '';
}

function yoagentRecentAgentsHtml(payload = yoagentStartupActivityPayload()) {
  const agents = Array.isArray(payload?.agents) ? payload.agents : [];
  const items = agents
    .filter(agent => agent && typeof agent === 'object' && agent.label)
    .map(agent => {
      const signal = yoagentRecentAgentSignal(agent);
      return {agent, signal, sortTs: yoagentRecentAgentSortTs(agent, signal)};
    })
    .sort((left, right) => right.sortTs - left.sortTs)
    .slice(0, 6);
  if (!items.length) return '';
  const rows = items.map(({agent, signal}) => {
    const kind = String(agent.agent_kind || '').toLowerCase();
    const activity = yoagentRecentAgentActivityText(agent);
    const windowText = String(agent.window_label || [agent.window, agent.window_name || kind].filter(Boolean).join(':') || kind || '').trim();
    const pathText = yoagentRecentAgentPathText(agent, signal);
    const signalBadges = yoagentRecentAgentSignalBadgesHtml(signal);
    const restart = yoagentRecentAgentRestartHtml(agent, signal);
    const idleClass = signal.window && !tmuxSignalWindowIsRecentlyActive(signal.window) ? ' tmux-idle' : '';
    const title = [
      agent.cwd ? t('yoagent.tooltip.cwd', {path: agent.cwd}) : '',
      pathText ? t('yoagent.tooltip.paths', {paths: pathText}) : '',
      agent.transcript ? t('yoagent.tooltip.transcript', {path: agent.transcript}) : '',
      signal.window?.activity_ts ? t('yoagent.tooltip.tmuxActivity', {time: new Date(Number(signal.window.activity_ts) * 1000).toISOString()}) : '',
      tmuxSignalWindowClientNames(signal.window).length ? t('yoagent.tooltip.tmuxViewers', {viewers: tmuxSignalWindowClientNames(signal.window).join(', ')}) : '',
      signal.window?.layout ? t('yoagent.tooltip.tmuxLayout', {layout: signal.window.layout}) : '',
      signal.window?.visible_layout ? t('yoagent.tooltip.tmuxVisibleLayout', {layout: signal.window.visible_layout}) : '',
      signal.window?.bell_flag ? t('state.reason.tmuxBellAlert') : '',
      signal.window?.silence_flag ? t('yoagent.tooltip.tmuxSilenceAlert') : '',
      signal.pane?.dead ? tmuxSignalDeadText(signal.pane) : '',
      tmuxSignalPaneModeLabels(signal.pane).join(', '),
      agent.state_text || '',
    ].filter(Boolean).join('\n');
    return `<li class="yoagent-recent-agent${idleClass}" data-agent-kind="${esc(kind)}" title="${esc(title)}">
      <span class="yoagent-recent-agent-line">
        ${kind ? agentIcon(kind, {label: agentLabel(kind)}) : ''}
        <span class="yoagent-recent-agent-session">${esc(t('yoagent.sessionLabel', {session: agent.session || ''}))}</span>
        <span class="yoagent-recent-agent-window">${esc(windowText)}</span>
        ${pathText ? `<span class="yoagent-recent-agent-paths">${esc(pathText)}</span>` : ''}
        ${activity ? `<span class="yoagent-recent-agent-activity">${esc(activity)}</span>` : ''}
        ${signalBadges}
        ${restart}
      </span>
    </li>`;
  }).join('');
  return `<div class="yoagent-recent-agents" aria-label="${esc(t('yoagent.recentAgents.label'))}">
    <span class="yoagent-recent-agents-label yoagent-section-title">${esc(t('yoagent.recentAgents.label'))}</span>
    <ul class="yoagent-recent-agents-list">${rows}</ul>
  </div>`;
}

function yoagentRecentAgentsMessageHtml() {
  const payload = yoagentStartupActivityPayload();
  const html = yoagentRecentAgentsHtml(payload);
  if (!html || !yoagentChatEnabled()) return '';
  return conversationMessageShellHtml({
    className: 'yoagent-message yoagent-recent-agents-message',
    author: yoagentTabLabel(),
    timestampHtml: yoagentMessageTimestampHtml(payload?.generated_at),
    bodyHtml: html,
  });
}

async function loadYoagentConversation(options = {}) {
  if (readOnlyMode) return false;
  if (yoagentConversationState.request && options.force !== true) return yoagentConversationState.request;
  if (yoagentConversationState.loaded && options.force !== true) return;
  const requestIsCurrent = yoagentConversationState.guard.begin();
  yoagentConversationState.loading = true;
  if (options.render !== false) renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom === true});
  const request = (async () => {
    try {
      const payload = await apiFetchJson('/api/yoagent/conversation', {cache: 'no-store'});
      if (!requestIsCurrent()) return false;
      return applyYoagentConversationPayload(payload, {source: 'request'});
    } catch (error) {
      if (requestIsCurrent() && !options.silent) statusErr(localizedHtml('yoagent.conversationLoadFailed', {error}));
      return false;
    } finally {
      if (yoagentConversationState.request === request) {
        yoagentConversationState.loading = false;
        yoagentConversationState.request = null;
        if (options.render !== false) renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom ?? false});
      }
    }
  })();
  yoagentConversationState.request = request;
  return request;
}

async function loadYoagentJobs(options = {}) {
  if (yoagentJobsState.request && options.force !== true) return yoagentJobsState.request;
  const requestIsCurrent = yoagentJobsState.guard.begin();
  yoagentJobsState.loading = true;
  const request = (async () => {
    try {
      const payload = await apiFetchJson('/api/yoagent/jobs', {cache: 'no-store'});
      if (!requestIsCurrent()) return false;
      applyYoagentJobsPayload(payload, {source: 'request'});
      if (options.render !== false && yoagentPanelIsActive()) renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom || false});
      return true;
    } catch (error) {
      if (requestIsCurrent() && !options.silent) console.warn('YO!agent jobs refresh failed', error);
      return false;
    } finally {
      if (yoagentJobsState.request === request) {
        yoagentJobsState.loading = false;
        yoagentJobsState.request = null;
      }
    }
  })();
  yoagentJobsState.request = request;
  return request;
}

let yoagentAgentAvailabilityRefreshPromise = null;

async function refreshYoagentAgentAvailability(options = {}) {
  if (yoagentAgentAvailabilityRefreshPromise) return yoagentAgentAvailabilityRefreshPromise;
  yoagentAgentAvailabilityRefreshPromise = (async () => {
    try {
      const params = new URLSearchParams();
      if (options.force === true) params.set('force', '1');
      const suffix = params.toString();
      const payload = await apiFetchJson(`/api/agent-auth${suffix ? `?${suffix}` : ''}`, {cache: 'no-store'});
      applyAgentAvailabilityPayload(payload);
      if (options.render !== false) renderYoagentPanel({preserveDraft: true, scrollBottom: false});
      return true;
    } catch (error) {
      if (!options.silent) console.warn('YO!agent backend availability refresh failed', error);
      return false;
    } finally {
      yoagentAgentAvailabilityRefreshPromise = null;
    }
  })();
  return yoagentAgentAvailabilityRefreshPromise;
}

function yoagentBackendLabel(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'auto') return t('yoagent.backend.auto');
  if (key === 'deterministic') return t('state.noAgent');
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return value || t('state.noAgent');
}

function yoagentBackendKey() {
  return String(initialSetting('yoagent.backend', 'auto') || 'auto').trim().toLowerCase();
}

const YOAGENT_CHAT_BACKENDS = ['codex', 'claude'];

function yoagentBackendInstalled(agent) {
  return YOAGENT_CHAT_BACKENDS.includes(agent) && availableAgents.has(agent);
}

function yoagentBackendUsable(agent) {
  return yoagentBackendInstalled(agent) && agentLoggedIn(agent);
}

function yoagentAvailableBackendOptions() {
  const current = yoagentBackendKey();
  return YOAGENT_CHAT_BACKENDS.filter(agent => yoagentBackendInstalled(agent) && (agentLoggedIn(agent) || agent === current));
}

// #41: mirror the server's auto-resolution (codex -> claude -> deterministic) using the cached agent
// login status, so the chat input enables/disables to match what the backend will actually run.
function yoagentResolvedBackend() {
  const key = yoagentBackendKey();
  if (YOAGENT_CHAT_BACKENDS.includes(key)) return yoagentBackendInstalled(key) ? key : 'deterministic';
  for (const agent of YOAGENT_CHAT_BACKENDS) {
    if (yoagentBackendUsable(agent)) return agent;
  }
  return 'deterministic';
}

function yoagentChatEnabled() {
  return YOAGENT_CHAT_BACKENDS.includes(yoagentResolvedBackend());
}

function yoagentComposerBackendKey() {
  const options = yoagentAvailableBackendOptions();
  const current = yoagentBackendKey();
  if (options.includes(current)) return current;
  const resolved = yoagentResolvedBackend();
  if (options.includes(resolved)) return resolved;
  return options[0] || '';
}

function yoagentChoiceValue(path, choices) {
  const current = String(initialSetting(path, '') || '').trim();
  if (choices.some(choice => choice.value === current)) return current;
  return choices[0]?.value || '';
}

function yoagentComposerSelectHtml({kind, title, path, value, choices, disabled, noneLabel, dot = false}) {
  const effectiveChoices = choices.length ? choices : [{value: '', label: noneLabel || t('state.noAgent')}];
  const disabledAttr = disabled || !choices.length ? ' disabled' : '';
  const options = effectiveChoices
    .map(choice => `<option value="${esc(choice.value)}"${choice.value === value ? ' selected' : ''}>${esc(choice.label)}</option>`)
    .join('');
  return `<label class="yoagent-composer-pill yoagent-composer-pill-${esc(kind)}" title="${esc(title)}">
    ${dot ? '<span class="yoagent-backend-pill-dot" aria-hidden="true"></span>' : ''}
    <select data-yoagent-${esc(kind)} data-yoagent-setting-path="${esc(path)}" aria-label="${esc(title)}"${disabledAttr}>${options}</select>
  </label>`;
}

function yoagentComposerControlsHtml(disabled) {
  const backendChoices = yoagentAvailableBackendOptions().map(value => ({value, label: yoagentBackendLabel(value)}));
  const backend = yoagentComposerBackendKey();
  const backendPath = 'yoagent.backend';
  const backendHtml = yoagentComposerSelectHtml({
    kind: 'backend',
    title: t('pref.yoagent.backend.label'),
    path: backendPath,
    value: backend,
    choices: backendChoices,
    disabled,
    noneLabel: t('state.noAgent'),
    dot: true,
  });
  const modelPath = backend ? `yoagent.${backend}_model` : 'yoagent.backend';
  const modelChoices = backend ? yoagentModelPreferenceChoicesForBackend(backend) : [];
  const modelHtml = yoagentComposerSelectHtml({
    kind: 'model',
    title: backend === 'claude' ? t('pref.yoagent.claude_model.label') : backend === 'codex' ? t('pref.yoagent.codex_model.label') : t('state.noAgent'),
    path: modelPath,
    value: yoagentChoiceValue(modelPath, modelChoices),
    choices: modelChoices,
    disabled,
    noneLabel: t('state.noAgent'),
  });
  const effortPath = backend ? `yoagent.${backend}_effort` : 'yoagent.backend';
  const effortChoices = backend ? yoagentEffortPreferenceChoicesForBackend(backend) : [];
  const effortHtml = yoagentComposerSelectHtml({
    kind: 'effort',
    title: backend === 'claude' ? t('pref.yoagent.claude_effort.label') : backend === 'codex' ? t('pref.yoagent.codex_effort.label') : t('state.noAgent'),
    path: effortPath,
    value: yoagentChoiceValue(effortPath, effortChoices),
    choices: effortChoices,
    disabled,
    noneLabel: t('state.noAgent'),
  });
  return backendHtml + modelHtml + effortHtml;
}

function yoagentChatHtml() {
  const chatEnabled = yoagentChatEnabled();
  const disabled = !chatEnabled ? ' disabled' : '';
  const backendDisabled = yoagentChatState.busy || readOnlyMode ? ' disabled' : '';
  const placeholder = t('yoagent.chatPlaceholder');
  const isThinking = yoagentChatState.busy || yoagentStartupState.prewarming;
  const startupInfo = yoagentStartupState.infoVisible ? yoagentStartupInfoHtml() : '';
  const hasConversation = Boolean(yoagentConversationState.messages.length || yoagentChatState.queue.length || yoagentConversationState.pendingWaits.length || yoagentJobsState.items.length || yoagentChatState.notice || isThinking || yoagentChatState.error || startupInfo || !chatEnabled);
  const thinkingHtml = textWithMovingEllipsisHtml(t('yoagent.thinking'), 'yoagent-thinking-dots');
  const busy = isThinking
    ? `<div class="yoagent-chat-status"><span class="yoagent-thinking">${thinkingHtml}</span></div>`
    : '';
  const retry = yoagentChatState.error && yoagentChatState.draft && yoagentChatEnabled() && !yoagentChatState.busy
    ? `<button type="button" class="yoagent-chat-retry" data-action="yoagent-retry" data-yoagent-retry>${esc(t('common.retry'))}</button>`
    : '';
  const error = yoagentChatState.error ? `<div class="yoagent-chat-error"><span>${esc(yoagentErrorText())}</span>${retry}</div>` : '';
  const chatDisabled = !chatEnabled ? `<div class="yoagent-chat-disabled">${esc(t('yoagent.chatDisabled'))}</div>` : '';
  const clearDisabled = yoagentChatState.busy || readOnlyMode || (!yoagentConversationState.messages.length && !yoagentChatState.notice && !yoagentChatState.error) ? ' disabled' : '';
  const submitButton = yoagentChatState.busy
    ? `<button type="button" class="yoagent-chat-stop" data-action="yoagent-chat-cancel" data-yoagent-chat-cancel title="${esc(t('yoagent.stop'))}" aria-label="${esc(t('yoagent.stop'))}">×</button>`
    : conversationSendButtonHtml({className: 'yoagent-chat-send', title: t('yoagent.ask'), disabled: !chatEnabled});
  const form = conversationComposerHtml({formClassName: 'yoagent-chat-form', formAttributes: 'data-yoagent-chat-form', inputHtml: `<input type="text" class="yoagent-chat-input conversation-composer-input" data-yoagent-chat-input value="${esc(yoagentChatState.draft)}" placeholder="${esc(placeholder)}"${disabled}>`, leadingControlsHtml: yoagentComposerControlsHtml(backendDisabled), trailingControlsHtml: `<button type="button" class="yoagent-chat-clear" data-action="yoagent-clear" data-yoagent-clear${clearDisabled}>${esc(t('common.clear'))}</button>`, sendHtml: submitButton});
  return `<section class="yoagent-chat ${hasConversation ? 'has-history' : 'empty'}" aria-label="${esc(t('yoagent.chatAria', {name: yoagentTabLabel()}))}">
    ${yoagentTranscriptPathHtml()}
    <div class="yoagent-chat-history">${yoagentNoticeHtml()}${yoagentChatMessagesHtml()}${yoagentChatQueueHtml()}${yoagentPendingWaitsHtml()}${yoagentJobsHtml()}${busy}${error}${chatDisabled}</div>
    ${form}
  </section>`;
}

function yoagentChatNetworkError(error) {
  return error instanceof TypeError;
}

function yoagentChatRequestTooLargeError(error) {
  return Number(error?.status || error?.payload?.status || 0) === 413;
}

function yoagentChatErrorState(error) {
  if (yoagentChatNetworkError(error)) {
    return {key: 'yoagent.networkError', params: {}, fallback: ''};
  }
  if (yoagentChatRequestTooLargeError(error)) {
    return {key: 'yoagent.chatTooLarge', params: {}, fallback: ''};
  }
  return userMessageSnapshot(error, error?.message || String(error || '')).user_message;
}

function yoagentErrorText() {
  if (yoagentChatState.error && typeof yoagentChatState.error === 'object') return messageDescriptorText(yoagentChatState.error);
  return String(yoagentChatState.error || '');
}

let yoagentFocusSerial = 0;
let yoagentFocusTrackerInstalled = false;

function yoagentDocumentHasFocus() {
  if (typeof document !== 'undefined' && document.visibilityState && document.visibilityState !== 'visible') return false;
  if (typeof document !== 'undefined' && typeof document.hasFocus === 'function') return document.hasFocus();
  return true;
}

function yoagentEventIsInsideComposer(event) {
  return Boolean(event?.target?.closest?.('[data-yoagent-chat-form]'));
}

function installYoagentFocusTracker() {
  if (yoagentFocusTrackerInstalled || typeof document === 'undefined') return;
  yoagentFocusTrackerInstalled = true;
  document.addEventListener('pointerdown', event => {
    if (!yoagentEventIsInsideComposer(event)) yoagentFocusSerial += 1;
  }, true);
  document.addEventListener('focusin', event => {
    if (!yoagentEventIsInsideComposer(event)) yoagentFocusSerial += 1;
  }, true);
  if (typeof window !== 'undefined') {
    window.addEventListener('blur', () => { yoagentFocusSerial += 1; });
  }
}

function yoagentNewChatRequestId() {
  const random = typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function'
    ? Array.from(crypto.getRandomValues(new Uint8Array(6))).map(value => value.toString(16).padStart(2, '0')).join('')
    : Math.random().toString(16).slice(2, 14);
  return `chat-${Date.now().toString(36)}-${random}`;
}

function yoagentChatAbortError(error) {
  return String(error?.name || '') === 'AbortError' || /abort/i.test(String(error?.message || error || ''));
}

function startYoagentLocalThinkingStream(streamId) {
  applyYoagentStreamPayload({
    stream_id: streamId,
    phase: 'thinking',
    hidden_work_active: true,
    auxiliary_lines: [t('yoagent.thinking')],
    created_at: new Date().toISOString(),
  });
}

function enqueueYoagentChatMessage(text) {
  const value = String(text || '').trim();
  if (!value) return false;
  yoagentChatState.queue.push({id: `queued-${++yoagentChatState.queueSerial}`, text: value, createdAt: new Date().toISOString()});
  yoagentChatState.draft = '';
  resetYoagentComposerHistory();
  renderYoagentPanel({preserveDraft: false, scrollBottom: true, allowBusyRebuild: true, focusInput: true});
  return true;
}

function cancelQueuedYoagentChatMessage(queueId) {
  const id = String(queueId || '');
  if (!id) return false;
  const previousLength = yoagentChatState.queue.length;
  yoagentChatState.queue = yoagentChatState.queue.filter(item => String(item?.id || '') !== id);
  const changed = yoagentChatState.queue.length !== previousLength;
  if (changed) renderYoagentPanel({preserveDraft: true, scrollBottom: false, allowBusyRebuild: true});
  return changed;
}

function finishYoagentBusyWork(options = {}) {
  yoagentChatState.busy = false;
  renderYoagentPanel({
    preserveDraft: options.preserveDraft !== false,
    scrollBottom: options.scrollBottom !== false,
    allowBusyRebuild: true,
    focusInput: options.focusInput === true,
  });
  if (typeof queueMicrotask === 'function') queueMicrotask(() => drainYoagentChatQueue());
  else Promise.resolve().then(() => drainYoagentChatQueue());
  return true;
}

function finishYoagentActiveRequest(requestId, options = {}) {
  if (!yoagentChatState.activeRequest || yoagentChatState.activeRequest.id !== requestId) return false;
  yoagentChatState.activeRequest = null;
  return finishYoagentBusyWork(options);
}

function drainYoagentChatQueue() {
  if (yoagentChatState.busy || yoagentChatState.activeRequest || yoagentConversationState.pendingWaits.length || !yoagentChatEnabled() || !yoagentChatState.queue.length) return false;
  const next = yoagentChatState.queue.shift();
  if (!next) return false;
  startYoagentChatRequest(next.text, {fromQueue: true});
  return true;
}

function cancelActiveYoagentChatRequest() {
  const active = yoagentChatState.activeRequest;
  if (!active || active.cancelled) return false;
  active.cancelled = true;
  try { active.controller?.abort(); } catch (_) {}
  applyYoagentStreamPayload({
    stream_id: active.streamId,
    phase: 'stopped',
    done: true,
    aborted: true,
    auxiliary_done: true,
  });
  finishYoagentActiveRequest(active.id, {scrollBottom: true, focusInput: true});
  apiFetchJson(`/api/yoagent/chat/${encodeURIComponent(active.id)}/cancel`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({request_id: active.id, stream_id: active.streamId}),
  }).catch(error => {
    if (!yoagentChatAbortError(error)) console.warn('YO!agent cancel failed', error);
  });
  return true;
}

async function clearYoagentConversation() {
  yoagentConversationState.guard.invalidate();
  yoagentConversationState.messages = [];
  yoagentConversationState.pendingWaits = [];
  yoagentChatState.queue = [];
  if (yoagentChatState.activeRequest) cancelActiveYoagentChatRequest();
  yoagentChatState.busy = false;
  yoagentStartupState.prewarming = false;
  yoagentStartupState.prewarmStarted = false;
  yoagentStartupState.llmRequested = false;
  if (yoagentConversationState.streamingMessages instanceof Map) yoagentConversationState.streamingMessages.clear();
  yoagentChatState.error = null;
  yoagentChatState.draft = '';
  resetYoagentComposerHistory();
  yoagentChatState.notice = null;
  showYoagentStartupInfoForLatestActivity();
  renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  try {
    const payload = await apiFetchJson('/api/yoagent/reset', {method: 'POST'});
    applyYoagentConversationPayload(payload.conversation || {});
    showYoagentStartupInfoForLatestActivity();
    renderYoagentPanel({preserveDraft: false, scrollBottom: true});
    statusEl.textContent = t('yoagent.statusCleared');
    await refreshActivitySummary({force: true, silent: true});
    showYoagentStartupInfoForLatestActivity();
    renderYoagentPanel({preserveDraft: false, scrollBottom: true});
  } catch (error) {
    statusErr(`${esc(t('yoagent.statusClearFailed', {error}))}`);
  }
}

async function updateYoagentJob(jobId, action) {
  const id = String(jobId || '');
  if (!id || readOnlyMode) return;
  try {
    const payload = await apiFetchJson(`/api/yoagent/jobs/${encodeURIComponent(id)}/${action}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
    });
    if (payload?.job) {
      const next = yoagentJobsState.items.map(job => String(job?.id || job?.job_id || '') === id ? payload.job : job);
      if (!next.some(job => String(job?.id || job?.job_id || '') === id)) next.unshift(payload.job);
      setYoagentJobs(next);
    }
    await loadYoagentJobs({silent: true, render: false});
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  } catch (error) {
    yoagentChatState.error = yoagentChatErrorState(error);
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  }
}

function confirmYoagentJob(jobId) {
  return updateYoagentJob(jobId, 'confirm');
}

function cancelYoagentJob(jobId) {
  return updateYoagentJob(jobId, 'cancel');
}

async function clearYoagentPendingWait(waitId) {
  const id = String(waitId || '');
  if (!id || readOnlyMode) return;
  try {
    const payload = await apiFetchJson(`/api/yoagent/waits/${encodeURIComponent(id)}/clear`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id}),
    });
    applyYoagentConversationPayload(payload.conversation || {});
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  } catch (error) {
    if (error?.payload?.conversation) applyYoagentConversationPayload(error.payload.conversation);
    yoagentChatState.error = yoagentChatErrorState(error);
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  }
}

async function startYoagentChatRequest(rawText, options = {}) {
  const text = String(rawText || '').trim();
  if (!text || yoagentChatState.busy || yoagentChatState.activeRequest) return;
  if (YOAGENT_CHAT_BACKENDS.includes(yoagentBackendKey())) {
    await refreshYoagentAgentAvailability({force: true, silent: true, render: false});
  }
  if (!yoagentChatEnabled()) {
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
    return;
  }
  resetYoagentComposerHistory();
  installYoagentFocusTracker();
  const requestId = yoagentNewChatRequestId();
  const streamId = requestId;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  yoagentConversationState.guard.invalidate();
  yoagentChatState.activeRequest = {id: requestId, streamId, controller, text, cancelled: false};
  const focusSerial = yoagentFocusSerial;
  const shouldRestoreFocus = yoagentChatInputIsFocused() && yoagentDocumentHasFocus();
  yoagentConversationState.messages.push({role: 'user', content: text, createdAt: new Date().toISOString()});
  yoagentChatState.draft = '';
  yoagentChatState.busy = true;
  yoagentStartupState.prewarming = false;
  yoagentChatState.error = null;
  yoagentChatState.notice = null;
  if (yoagentConversationState.streamingMessages instanceof Map) yoagentConversationState.streamingMessages.clear();
  startYoagentLocalThinkingStream(streamId);
  renderYoagentPanel({preserveDraft: false, scrollBottom: true, allowBusyRebuild: true});
  try {
    const payload = await apiFetchJson('/api/yoagent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, locale: i18nActiveLocaleId(), request_id: requestId, stream_id: streamId}),
      signal: controller?.signal,
    });
    if (yoagentChatState.activeRequest?.id !== requestId) return;
    if (payload.cancelled) {
      applyYoagentStreamPayload({stream_id: streamId, phase: 'stopped', done: true, aborted: true, auxiliary_done: true});
      return;
    }
    if (payload.fallback && payload.fallback_reason) {
      yoagentChatState.notice = yoagentFallbackNotice(payload);
    }
    if (!applyYoagentConversationPayload(payload.conversation || {})) {
      yoagentConversationState.messages.push({
        role: 'assistant',
        content: payload.answer || '',
        ...(!payload.answer ? structuredMessageSnapshot({content: '', content_key: 'yoagent.noAnswer', content_params: {}}, 'content') : {}),
        actions: Array.isArray(payload.actions) ? payload.actions : [],
        details: payload.details || '',
        createdAt: payload.answered_at || new Date().toISOString(),
      });
    }
    statusEl.textContent = t('yoagent.statusAnswered', {backend: yoagentBackendLabel(payload.backend_used || payload.backend)});
  } catch (error) {
    if (yoagentChatState.activeRequest?.id !== requestId || yoagentChatAbortError(error)) return;
    if (yoagentChatNetworkError(error)) yoagentChatState.draft = text;
    yoagentChatState.error = yoagentChatErrorState(error);
  } finally {
    finishYoagentActiveRequest(requestId, {
      preserveDraft: true,
      scrollBottom: true,
      focusInput: shouldRestoreFocus && focusSerial === yoagentFocusSerial && yoagentDocumentHasFocus(),
    });
  }
}

async function sendYoagentChatMessage(rawText) {
  const text = String(rawText || '').trim();
  if (!text) return;
  if (yoagentChatState.busy || yoagentChatState.activeRequest || yoagentConversationState.pendingWaits.length) {
    enqueueYoagentChatMessage(text);
    return;
  }
  return startYoagentChatRequest(text);
}

function updateYoagentActionPreview(previewId, patch) {
  let changed = false;
  for (const message of yoagentConversationState.messages) {
    if (!Array.isArray(message.actions)) continue;
    message.actions = message.actions.map(action => {
      if (action?.id !== previewId) return action;
      changed = true;
      return {...action, ...patch};
    });
  }
  return changed;
}

async function executeYoagentActionSend(previewId) {
  if (!previewId || readOnlyMode || yoagentChatState.busy) return;
  yoagentConversationState.guard.invalidate();
  hideYoagentStartupInfo();
  yoagentChatState.busy = true;
  updateYoagentActionPreview(previewId, yoagentActionStatusPatch('yoagent.action.state.sending', 'sending'));
  renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    const payload = await apiFetchJson('/api/yoagent/actions/execute-send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preview_id: previewId}),
    });
    applyYoagentConversationPayload(payload.conversation || {});
    updateYoagentActionPreview(previewId, {status: 'sent', ...yoagentActionStatusPatch('yoagent.action.state.sent', 'sent')});
    const answer = payload.answer
      ? t('yoagent.action.sentWithAnswer', {session: payload.session, transport: payload.transport, answer: payload.answer})
      : t('yoagent.action.sent', {session: payload.session, transport: payload.transport});
    if (!payload.conversation) yoagentConversationState.messages.push({role: 'assistant', content: answer, createdAt: new Date().toISOString()});
    statusEl.textContent = t('yoagent.statusActionSent', {session: payload.session});
  } catch (error) {
    updateYoagentActionPreview(previewId, yoagentActionErrorStatusPatch(error));
    yoagentChatState.error = yoagentChatErrorState(error);
  } finally {
    finishYoagentBusyWork({preserveDraft: true, scrollBottom: true});
  }
}

async function prewarmYoagent(options = {}) {
  if (yoagentStartupState.prewarmStarted || readOnlyMode || !yoagentChatEnabled()) return;
  const shouldRequestStartupAnswer = !yoagentStartupState.llmRequested && !yoagentConversationState.messages.length && !yoagentConversationState.loaded;
  if (shouldRequestStartupAnswer) yoagentStartupState.llmRequested = true;
  yoagentStartupState.prewarmStarted = true;
  yoagentStartupState.prewarming = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom === true});
  try {
    const payload = await apiFetchJson('/api/yoagent/prewarm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({locale: i18nActiveLocaleId(), visible: shouldRequestStartupAnswer}),
    });
    if (payload?.fallback && payload.fallback_reason) {
      yoagentChatState.notice = yoagentFallbackNotice(payload);
    }
    if (payload?.conversation) applyYoagentConversationPayload(payload.conversation || {});
  } catch (error) {
    if (shouldRequestStartupAnswer) {
      yoagentChatState.error = yoagentChatErrorState(error);
    }
    // Non-visible process warm-up is opportunistic; visible chat requests handle real errors.
  } finally {
    yoagentStartupState.prewarming = false;
    renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom === true});
  }
}

function yoagentPanelIsActive() {
  return itemIsActivePaneTab(yoagentItemId);
}

function activitySummaryIsVisible() {
  return yoagentPanelIsActive();
}

async function refreshActivitySummary(options = {}) {
  if (options.silent === true && options.localeChange !== true) {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    return;
  }
  if (activitySummaryState.refreshing && options.force !== true) return;
  const requestIsCurrent = activitySummaryState.guard.begin();
  activitySummaryState.refreshing = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
  try {
    const params = new URLSearchParams();
    if (options.force) params.set('force', '1');
    params.set('locale', i18nActiveLocaleId());
    params.set('scope', 'all');
    params.set('hours', String(infoSessionFileLookbackHours));
    const payload = await apiFetchJson(`/api/activity-summary?${params.toString()}`, {cache: 'no-store'});
    if (!requestIsCurrent()) return;
    applyActivitySummaryPayloadFromPush(payload, {refreshStartupSnapshot: true, render: true, source: 'request'});
  } catch (error) {
    if (!requestIsCurrent()) return;
    const errorDescriptor = userMessageSnapshot(error, error?.message || String(error || '')).user_message;
    activitySummaryState.payload = {
      ...activitySummaryState.payload,
      errors: [String(error)],
      global: {
        headline: '',
        lines: [],
        detail_lines: [],
        detail_messages: [{
          key: 'status.activitySummaryFailed',
          params: {error: errorDescriptor},
          fallback: '',
        }],
      },
    };
    if (!options.silent) statusErr(localizedHtml('status.activitySummaryFailed', {error}));
  } finally {
    if (requestIsCurrent()) {
      activitySummaryState.refreshing = false;
      renderInfoPanel();
      renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
      if (yoagentPanelIsActive()) prewarmYoagent();
    }
  }
}

function applyActivitySummaryPayloadFromPush(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  if (options.source !== 'request') activitySummaryState.guard.invalidate();
  if (payload.session_file_hours != null) {
    infoSessionFileLookbackHours = writeStoredInfoLookbackHours(payload.session_file_hours);
  }
  activitySummaryState.payload = payload;
  activitySummaryState.refreshing = false;
  if (options.refreshStartupSnapshot === true) captureYoagentStartupActivitySummarySnapshot({replace: true});
  if (options.render === true || options.refreshStartupSnapshot === true) {
    renderInfoPanel();
    renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
    if (yoagentPanelIsActive()) prewarmYoagent();
  }
  return true;
}
