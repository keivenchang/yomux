// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// YO!agent panel rendering, conversation controls, and activity summary refresh split from 80_panes_preferences.js.

function sessionActivitySummary(session) {
  return activitySummaryPayload?.sessions?.[session] || null;
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
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: 'America/Los_Angeles',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
    }).format(date);
  } catch (_) {
    return date.toLocaleString();
  }
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

function yoagentAuxiliaryLineIsTool(line) {
  return /^(tool start|tool output|tool done|approval requested):?/i.test(String(line || '').trim());
}

function yoagentAuxiliaryLineIsDiagnostic(line) {
  return /^(?:thinking done|usage:|[-*]\s*(?:backend|response time|model CLI time|Codex transport|Codex timing|prompt size|model session|fallback reason|stream phase|raw model thinking|activity context changed|auxiliary stream truncated)\b)/i.test(String(line || '').trim());
}

function yoagentAuxiliaryLineIsThinking(line) {
  return /^(?:thinking|thinking summary|redacted thinking):?/i.test(String(line || '').trim());
}

const YOAGENT_THINKING_PREVIEW_WORDS = 50;

function yoagentAuxiliaryPreviewThinkingLines(text) {
  const result = [];
  let currentKind = '';
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    const line = String(rawLine || '').trim();
    if (!line) continue;
    if (yoagentAuxiliaryLineIsTool(line)) {
      currentKind = 'tool';
      continue;
    }
    if (yoagentAuxiliaryLineIsDiagnostic(line)) {
      currentKind = 'diagnostic';
      continue;
    }
    if (yoagentAuxiliaryLineIsThinking(line)) {
      currentKind = 'thinking';
      result.push(line);
      continue;
    }
    if (currentKind === 'tool' || currentKind === 'diagnostic') continue;
    result.push(line);
  }
  return result;
}

function yoagentLastLines(lines, count = 1) {
  return (Array.isArray(lines) ? lines : []).slice(-Math.max(1, count)).join('\n');
}

function yoagentThinkingPreviewText(text) {
  const normalized = String(text || '').replace(/\\n/g, '\n').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  const words = normalized.split(/\s+/).filter(Boolean);
  if (words.length <= YOAGENT_THINKING_PREVIEW_WORDS) return normalized;
  return `… ${words.slice(-YOAGENT_THINKING_PREVIEW_WORDS).join(' ')}`;
}

function yoagentToolLineHtml(line) {
  const text = String(line || '').replace(/\\n/g, '\n');
  const commandMatch = text.match(/^((?:tool start:\s*(?:command|bash|shell)|approval requested):\s*)(.*)$/i);
  if (commandMatch) {
    return `${esc(commandMatch[1])}<span class="yoagent-tc-command">${esc(commandMatch[2])}</span>`;
  }
  const shellMatch = text.match(/^(\$\s+)(.*)$/);
  if (shellMatch) {
    return `${esc(shellMatch[1])}<span class="yoagent-tc-command">${esc(shellMatch[2])}</span>`;
  }
  return esc(text);
}

function yoagentToolLinesHtml(lines) {
  return (Array.isArray(lines) ? lines : []).map(yoagentToolLineHtml).join('\n');
}

function yoagentStreamItems(message) {
  return (Array.isArray(message?.streamItems) ? message.streamItems : [])
    .map(item => {
      const text = String(item?.text || '');
      return {
        kind: String(item?.kind || '').trim(),
        text,
      };
    })
    .filter(item => ['assistant', 'thinking', 'tool'].includes(item.kind) && item.text.trim());
}

function yoagentPreviewLine(text) {
  return String(text || '').replace(/\\n/g, '\n').split(/\r?\n/).map(line => line.trim()).filter(Boolean)[0] || '';
}

function yoagentStreamAuxiliaryItemHtml(item, key, index) {
  const kind = String(item?.kind || '');
  const text = String(item?.text || '');
  if (!text || (kind !== 'thinking' && kind !== 'tool')) return '';
  const tool = kind === 'tool';
  const label = tool ? t('yoagent.toolCall.label') : 'thinking';
  const preview = tool ? yoagentPreviewLine(text) : yoagentThinkingPreviewText(text);
  const classes = tool
    ? 'yoagent-message-details yoagent-toolcall-details has-auxiliary yoagent-stream-detail'
    : 'yoagent-message-details has-auxiliary yoagent-stream-detail';
  const streamClasses = tool
    ? 'yoagent-auxiliary-stream yoagent-toolcall-stream'
    : 'yoagent-auxiliary-stream';
  const body = tool ? yoagentToolLinesHtml([text]) : esc(text);
  return `<details class="${classes}" data-yoagent-message-details-key="${esc(`${key}|stream|${index}`)}">
    <summary><span>${esc(`${label}…`)}</span>${preview ? `<span class="yoagent-details-preview">${esc(preview)}</span>` : ''}</summary>
    <pre class="${streamClasses}">${body}</pre>
  </details>`;
}

function yoagentMessageStreamItemsHtml(message, key = '') {
  const items = yoagentStreamItems(message);
  if (!items.length) return '';
  return `<div class="yoagent-message-stream">${items.map((item, index) => {
    if (item.kind === 'assistant') {
      return `<div class="yoagent-message-body markdown-body yoagent-stream-assistant" data-yoagent-markdown>${esc(item.text)}</div>`;
    }
    return yoagentStreamAuxiliaryItemHtml(item, key, index);
  }).join('')}</div>`;
}

function yoagentMessageDetailsHtml(message, key = '') {
  const text = String(message?.details || '').trim();
  const auxiliaryLines = yoagentAuxiliaryLines(message);
  const toolLines = auxiliaryLines.filter(yoagentAuxiliaryLineIsTool);
  const thinkingLines = auxiliaryLines.filter(line => !yoagentAuxiliaryLineIsTool(line) && !yoagentAuxiliaryLineIsDiagnostic(line));
  const auxiliaryText = thinkingLines.join('\n');
  const toolText = toolLines.join('\n');
  const auxiliaryPreviewLines = yoagentAuxiliaryPreviewThinkingLines(message?.auxiliaryPreview || '');
  const auxiliaryPreview = yoagentThinkingPreviewText(auxiliaryPreviewLines.join('\n') || thinkingLines.join('\n'));
  const toolPreview = yoagentPreviewLine(yoagentLastLines(toolLines, 1));
  const hasAuxiliary = Boolean(auxiliaryText || auxiliaryPreview);
  const hasTool = Boolean(toolText || toolPreview);
  const truncated = Boolean(message?.auxiliaryTruncated);
  if (!text && !hasAuxiliary && !hasTool) return '';
  const preview = auxiliaryPreview
    ? `<span class="yoagent-details-preview">${esc(auxiliaryPreview)}</span>`
    : '';
  const auxiliaryBlock = auxiliaryText
    ? `<pre class="yoagent-auxiliary-stream">${esc(auxiliaryText)}</pre>`
    : '';
  const truncationNote = truncated
    ? `<div class="yoagent-details-note">auxiliary stream truncated to latest entries</div>`
    : '';
  const detailsBlock = text
    ? `<pre class="yoagent-safe-details">${esc(text)}</pre>`
    : '';
  const thinkingDetails = (text || hasAuxiliary)
    ? `<details class="yoagent-message-details${hasAuxiliary ? ' has-auxiliary' : ''}" data-yoagent-message-details-key="${esc(key)}">
    <summary><span>${esc(`${t('popover.details')}…`)}</span>${preview}</summary>
    ${auxiliaryBlock}${truncationNote}${detailsBlock}
  </details>`
    : '';
  const toolDetails = hasTool
    ? `<details class="yoagent-message-details yoagent-toolcall-details has-auxiliary" data-yoagent-message-details-key="${esc(`${key}|tc`)}">
    <summary><span>${esc(t('yoagent.toolCall.label'))}</span>${toolPreview ? `<span class="yoagent-details-preview">${esc(toolPreview)}</span>` : ''}</summary>
    <pre class="yoagent-auxiliary-stream yoagent-toolcall-stream">${yoagentToolLinesHtml(toolLines.length ? toolLines : [toolPreview])}</pre>
  </details>`
    : '';
  return `${thinkingDetails}${toolDetails}`;
}

function relativeActivityGeneratedText(payload = activitySummaryPayload) {
  const ts = Number(payload?.generated_ts || 0) || Date.parse(payload?.generated_at || '') / 1000;
  if (!Number.isFinite(ts) || ts <= 0) return {text: t('yoagent.notLoaded'), title: ''};
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  // Phase 3: render the relative time with Intl.RelativeTimeFormat(activeLocale) for native
  // locale phrasing, wrapped by the localized "last updated {rel}" string.
  const text = seconds < 60
    ? t('yoagent.updated.justNow')
    : t('yoagent.updated.wrap', {rel: relativeTimeFormat(seconds)});
  let title = payload?.generated_at || '';
  try {
    title = new Intl.DateTimeFormat(undefined, {
      timeZone: 'America/Los_Angeles',
      dateStyle: 'medium',
      timeStyle: 'medium',
      timeZoneName: 'short',
    }).format(new Date(ts * 1000));
  } catch (_) {}
  return {text, title};
}

function globalActivitySummaryHtml() {
  const summary = activitySummaryPayload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = summary.headline || lines[0] || '';
  const detailLines = lines.filter(line => line && line !== headline && !/^Session\s+\S+:/i.test(String(line)));
  const generated = relativeActivityGeneratedText();
  const refreshBar = activitySummaryRefreshing ? `<div class="yoagent-refresh-progress" aria-label="${esc(t('yoagent.refreshing'))}"></div>` : '';
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
  if (!(yoagentStreamingMessages instanceof Map)) return [];
  return [...yoagentStreamingMessages.values()]
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
  const content = String(message?.content || (message?.aborted ? t('yoagent.stopped') : (streaming ? t('yoagent.thinking') : '')));
  if (agentResult) {
    const parts = yoagentAgentResultParts(content);
    const heading = parts.heading
      ? `<div class="yoagent-agent-result-heading markdown-body" data-yoagent-markdown>${esc(parts.heading)}</div>`
      : '';
    const output = parts.output
      ? `<div class="yoagent-agent-result-output markdown-body" data-yoagent-markdown>${esc(parts.output)}</div>`
      : '';
    return `<div class="yoagent-message-body yoagent-agent-result-body">${heading}${output}</div>`;
  }
  const bodyClass = roleClass === 'assistant' ? 'yoagent-message-body markdown-body' : 'yoagent-message-body';
  const markdownAttr = roleClass === 'assistant' ? ' data-yoagent-markdown' : '';
  return `<div class="${bodyClass}"${markdownAttr}>${esc(content)}</div>`;
}

function yoagentChatMessagesHtml() {
  const messages = [...(Array.isArray(yoagentMessages) ? yoagentMessages : []), ...yoagentStreamingMessagesList()];
  const startupInfo = yoagentStartupInfoVisible ? yoagentStartupInfoHtml() : '';
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
    return `<div class="${messageClass}">
      <div class="yoagent-message-role"><span>${esc(role)}</span>${yoagentMessageTimestampHtml(message.createdAt, roleClass === 'assistant' ? message : null)}</div>
      ${streamItemsHtml || yoagentMessageBodyHtml(message, roleClass, agentResult, streaming)}
      ${stoppedState}
      ${roleClass === 'assistant' && !streamItemsHtml ? yoagentMessageDetailsHtml(message, detailsKey) : ''}
      ${roleClass === 'assistant' ? yoagentActionCardsHtml(message.actions) : ''}
    </div>`;
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
  const canSend = status === 'ready' && action.id && !readOnlyMode && !yoagentBusy;
  const button = canSend
    ? `<button type="button" class="yoagent-action-send" data-yoagent-action-send="${esc(action.id)}">${esc(t('yoagent.action.send'))}</button>`
    : `<span class="yoagent-action-state">${esc(yoagentActionStatusText(action))}</span>`;
  const rows = [
    [t('yoagent.action.row.session'), target.session || action.session || ''],
    [t('yoagent.action.row.agent'), [target.agent_kind, target.agent_model].filter(Boolean).join(' ')],
    [t('yoagent.action.row.transport'), transportLabel],
    [t('yoagent.action.row.pane'), target.pane_target || ''],
    [t('yoagent.action.row.path'), target.cwd || ''],
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
  if (status === 'sent') return t('yoagent.action.state.sent');
  if (String(action?.status_text || '') === 'sending') return t('yoagent.action.state.sending');
  return action?.status_text || status;
}

function yoagentStartupActivityPayload() {
  return yoagentStartupActivitySummaryPayload || activitySummaryPayload;
}

function cloneYoagentActivitySummaryPayload(payload) {
  return JSON.parse(JSON.stringify(payload || {sessions: {}, global: {lines: []}, session_order: []}));
}

function captureYoagentStartupActivitySummarySnapshot(options = {}) {
  if (options.replace !== true && yoagentStartupActivitySummaryPayload) return yoagentStartupActivitySummaryPayload;
  yoagentStartupActivitySummaryPayload = activitySummaryPayload && typeof activitySummaryPayload === 'object'
    ? cloneYoagentActivitySummaryPayload(activitySummaryPayload)
    : {sessions: {}, global: {lines: []}, session_order: []};
  return yoagentStartupActivitySummaryPayload;
}

function resetYoagentStartupActivitySummarySnapshot() {
  yoagentStartupActivitySummaryPayload = null;
}

function yoagentIntroMessageText(payload = yoagentStartupActivityPayload()) {
  const summary = payload?.global || {};
  const lines = Array.isArray(summary.lines) ? summary.lines : [];
  const headline = String(summary.headline || lines[0] || '').trim();
  if (!headline) return '';
  const details = lines
    .filter(line => line && line !== headline && !/^Session\s+\S+:/i.test(String(line)))
    .slice(0, 2)
    .map(line => `- ${String(line).trim()}`);
  return ["Here's what I see right now:", "", headline, ...details].filter(Boolean).join('\n');
}

function yoagentIntroMessageHtml() {
  const payload = yoagentStartupActivityPayload();
  const text = yoagentIntroMessageText(payload);
  if (!text || !yoagentChatEnabled()) return '';
  return `<div class="yoagent-message assistant yoagent-intro-message">
    <div class="yoagent-message-role"><span>${esc(yoagentTabLabel())}</span>${yoagentMessageTimestampHtml(payload?.generated_at)}</div>
    <div class="yoagent-message-body markdown-body" data-yoagent-markdown>${esc(text)}</div>
  </div>`;
}

function yoagentStartupInfoHtml() {
  return `${yoagentIntroMessageHtml()}${yoagentRecentAgentsMessageHtml()}`;
}

function showYoagentStartupInfoOnce() {
  if (yoagentStartupInfoShown) return false;
  captureYoagentStartupActivitySummarySnapshot();
  yoagentStartupInfoShown = true;
  yoagentStartupInfoVisible = true;
  return true;
}

function showYoagentStartupInfoForLatestActivity() {
  resetYoagentStartupActivitySummarySnapshot();
  yoagentStartupInfoShown = false;
  return showYoagentStartupInfoOnce();
}

function hideYoagentStartupInfo() {
  yoagentStartupInfoVisible = false;
}

function yoagentNoticeHtml() {
  if (!yoagentNotice?.reason) return '';
  const backend = yoagentNotice.backend ? `<span class="yoagent-chat-notice-backend">${esc(yoagentNotice.backend)}</span> ` : '';
  return `<div class="yoagent-chat-notice">${backend}${esc(yoagentNotice.reason)}</div>`;
}

function yoagentPendingWaitsHtml() {
  const waits = Array.isArray(yoagentPendingWaits) ? yoagentPendingWaits : [];
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
      ? `<button type="button" class="yoagent-waiting-clear" data-yoagent-wait-clear="${esc(id)}" title="${esc(t('yoagent.clear'))}" aria-label="${esc(t('yoagent.clear'))}">${esc(t('yoagent.clear'))}</button>`
      : '';
    return `<li class="yoagent-waiting-item" title="${esc(transcript)}">
      <span class="session-yolo-marker active working yoagent-waiting-spinner" style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}" aria-hidden="true">${esc(t('brand.marker'))}</span>
      <span class="yoagent-waiting-label">${esc(label)}</span>
      ${age ? `<span class="yoagent-waiting-age">${esc(age)}</span>` : ''}
      ${clearButton}
    </li>`;
  }).join('');
  return `<div class="yoagent-waiting-queue" aria-live="polite" aria-label="${esc(title)}">
    <div class="yoagent-waiting-title">${esc(title)}</div>
    <ul class="yoagent-waiting-list">${rows}</ul>
  </div>`;
}

function applyYoagentJobsPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  yoagentJobs = Array.isArray(payload.jobs) ? payload.jobs : [];
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

function yoagentJobStatusText(job) {
  const status = String(job?.status || '');
  if (status === 'pending_confirmation') return t('yoagent.jobs.status.pendingConfirmation');
  if (status === 'timed_out') return t('yoagent.jobs.status.timedOut');
  if (status) return status.replace(/_/g, ' ');
  return t('yoagent.jobs.status.unknown');
}

function yoagentJobRowsHtml() {
  const jobs = Array.isArray(yoagentJobs) ? yoagentJobs : [];
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
    const error = String(job?.error || job?.result?.error || '');
    const meta = [
      type,
      target ? t('yoagent.jobs.target', {target}) : '',
      blocker ? t('yoagent.jobs.blockedBy', {blocker}) : '',
      error,
    ].filter(Boolean).join(' · ');
    const controls = [
      canConfirm ? `<button type="button" class="yoagent-job-confirm" data-yoagent-job-confirm="${esc(id)}">${esc(t('yoagent.jobs.confirm'))}</button>` : '',
      canCancel ? `<button type="button" class="yoagent-job-cancel" data-yoagent-job-cancel="${esc(id)}">${esc(t('yoagent.jobs.cancel'))}</button>` : '',
    ].filter(Boolean).join('');
    return `<li class="yoagent-job-item yoagent-job-${esc(status || 'unknown')}" data-yoagent-job-row="${esc(id)}">
      <div class="yoagent-job-main">
        <span class="yoagent-job-status">${esc(yoagentJobStatusText(job))}</span>
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
    <div class="yoagent-jobs-title">${esc(t('yoagent.jobs.title'))}</div>
    <ul class="yoagent-jobs-items">${rows}</ul>
  </div>`;
}

function yoagentChatQueueHtml() {
  const items = Array.isArray(yoagentChatQueue) ? yoagentChatQueue : [];
  if (!items.length) return '';
  const rows = items.map((item, index) => {
    const id = String(item?.id || '');
    const text = String(item?.text || '');
    const label = text.length > 180 ? `${text.slice(0, 177)}...` : text;
    return `<li class="yoagent-chat-queue-item" data-yoagent-chat-queue-row="${esc(id)}">
      <span class="yoagent-chat-queue-index">${esc(String(index + 1))}</span>
      <span class="yoagent-chat-queue-text">${esc(label)}</span>
      <button type="button" class="yoagent-chat-queue-cancel" data-yoagent-queued-cancel="${esc(id)}" title="${esc(t('yoagent.queue.cancel'))}" aria-label="${esc(t('yoagent.queue.cancel'))}">${esc(t('yoagent.queue.cancel'))}</button>
    </li>`;
  }).join('');
  return `<div class="yoagent-chat-queue" aria-live="polite" aria-label="${esc(t('yoagent.queue.title'))}">
    <div class="yoagent-chat-queue-title">${esc(t('yoagent.queue.title'))}</div>
    <ul class="yoagent-chat-queue-items">${rows}</ul>
  </div>`;
}

function applyYoagentConversationPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  if (!Object.prototype.hasOwnProperty.call(payload, 'messages')) return false;
  const messages = Array.isArray(payload.messages) ? payload.messages : [];
  const hadPendingWaits = Array.isArray(yoagentPendingWaits) && yoagentPendingWaits.length > 0;
  yoagentPendingWaits = Array.isArray(payload.pending_waits) ? payload.pending_waits : [];
  if (messages.length && yoagentStreamingMessages instanceof Map) yoagentStreamingMessages.clear();
  yoagentMessages = messages;
  yoagentConversationPath = String(payload.transcript_path || '');
  yoagentConversationDisplayPath = String(payload.transcript_display_path || yoagentConversationPath);
  yoagentConversationLoaded = true;
  if (hadPendingWaits && !yoagentPendingWaits.length && yoagentChatQueue.length) {
    if (typeof queueMicrotask === 'function') queueMicrotask(() => drainYoagentChatQueue());
    else Promise.resolve().then(() => drainYoagentChatQueue());
  }
  return true;
}

function applyYoagentStreamPayload(payload = {}) {
  if (!payload || typeof payload !== 'object') return false;
  const streamId = String(payload.stream_id || '').trim();
  if (!streamId) return false;
  if (!(yoagentStreamingMessages instanceof Map)) yoagentStreamingMessages = new Map();
  const createdAt = String(payload.created_at || new Date().toISOString());
  const content = String(payload.content || '');
  const phase = String(payload.phase || '');
  const hiddenThinking = Boolean(payload.hidden_thinking_removed);
  const previous = yoagentStreamingMessages.get(streamId) || {};
  const auxiliaryLines = Array.isArray(payload.auxiliary_lines)
    ? payload.auxiliary_lines.map(line => String(line || '')).filter(Boolean)
    : (Array.isArray(previous.auxiliaryLines) ? previous.auxiliaryLines : []);
  const streamItems = Array.isArray(payload.stream_items)
    ? yoagentStreamItems({streamItems: payload.stream_items})
    : yoagentStreamItems(previous);
  const auxiliaryActive = Boolean(payload.hidden_work_active || payload.tool_active);
  const thinkingLines = auxiliaryLines.filter(line => !yoagentAuxiliaryLineIsTool(line) && !yoagentAuxiliaryLineIsDiagnostic(line));
  const auxiliaryPreviewLines = yoagentAuxiliaryPreviewThinkingLines(payload.auxiliary_preview || '');
  const auxiliaryPreview = yoagentThinkingPreviewText(
    auxiliaryPreviewLines.join('\n')
    || (thinkingLines.length ? thinkingLines.join('\n') : String(previous.auxiliaryPreview || '')),
  );
  const detailLines = [];
  if (payload.backend) detailLines.push(`- backend: \`${payload.backend}\``);
  if (phase) detailLines.push(`- stream phase: \`${phase}\``);
  if (hiddenThinking) detailLines.push('- raw model thinking was hidden; YOLOmux shows safe diagnostics instead of chain-of-thought');
  if (payload.auxiliary_truncated) detailLines.push('- auxiliary stream truncated to latest entries');
  yoagentStreamingMessages.set(streamId, {
    role: 'assistant',
    content: content || previous.content || '',
    createdAt: previous.createdAt || createdAt,
    details: detailLines.join('\n') || previous.details || '',
    auxiliaryLines,
    auxiliaryText: auxiliaryLines.join('\n') || previous.auxiliaryText || '',
    auxiliaryPreview,
    streamItems,
    auxiliaryActive,
    auxiliaryDone: Boolean(payload.auxiliary_done) || previous.auxiliaryDone || false,
    auxiliaryTruncated: Boolean(payload.auxiliary_truncated) || previous.auxiliaryTruncated || false,
    aborted: Boolean(payload.aborted) || previous.aborted || false,
    streaming: payload.done !== true,
  });
  yoagentPrewarming = payload.done === true ? false : yoagentPrewarming;
  return true;
}

function resetYoagentComposerHistory() {
  yoagentHistoryCursor = null;
  yoagentHistoryDraft = '';
}

function yoagentUserMessageHistory() {
  return (Array.isArray(yoagentMessages) ? yoagentMessages : [])
    .filter(message => message?.role === 'user')
    .map(message => String(message.content || '').trim())
    .filter(Boolean);
}

function setYoagentChatInputValue(input, value) {
  if (!input) return;
  const nextValue = String(value || '');
  input.value = nextValue;
  yoagentDraft = nextValue;
  const end = nextValue.length;
  try { input.setSelectionRange(end, end); } catch (_) {}
}

function yoagentNavigateChatHistory(input, direction) {
  if (!input || input.disabled) return false;
  const history = yoagentUserMessageHistory();
  const latest = yoagentHistoryCursor === null;
  if (direction === 'up') {
    if (!history.length) return false;
    if (latest) {
      yoagentHistoryDraft = input.value || yoagentDraft || '';
      yoagentHistoryCursor = history.length - 1;
    } else {
      yoagentHistoryCursor = Math.max(0, Math.min(history.length - 1, Number(yoagentHistoryCursor) - 1));
    }
    setYoagentChatInputValue(input, history[yoagentHistoryCursor] || '');
    return true;
  }
  if (direction === 'down') {
    if (latest) return false;
    const next = Math.min(history.length, Number(yoagentHistoryCursor) + 1);
    if (next >= history.length) {
      yoagentHistoryCursor = null;
      setYoagentChatInputValue(input, yoagentHistoryDraft);
      yoagentHistoryDraft = '';
    } else {
      yoagentHistoryCursor = next;
      setYoagentChatInputValue(input, history[yoagentHistoryCursor] || '');
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
  const path = yoagentConversationPath || '';
  const display = yoagentConversationDisplayPath || path;
  if (!path && !yoagentConversationLoading && !yoagentConversationLoaded) return '';
  const value = path
    ? `<code class="yoagent-transcript-value" title="${esc(path)}">${esc(display)}</code>${pathCopyButtonHtml(path, {className: 'yoagent-transcript-copy', title: t('yoagent.transcript.copy')})}`
    : `<span class="yoagent-transcript-loading">${esc(t('yoagent.transcript.loading'))}</span>`;
  return `<div class="yoagent-transcript-path">
    <span class="yoagent-transcript-label">${esc(t('yoagent.transcript.label'))}</span>
    ${value}
  </div>`;
}

function yoagentRecentAgentActivityText(agent) {
  const signal = yoagentRecentAgentSignal(agent);
  if (signal.pane?.dead === true) return tmuxSignalDeadText(signal.pane);
  const activityTs = tmuxSignalWindowActivityTs(signal.window);
  if (activityTs > 0) {
    const seconds = Math.max(0, Math.round(Date.now() / 1000 - activityTs));
    return `tmux ${compactRelativeTimeFormat(seconds)}`;
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
  if (windowRecord?.bell_flag === true) badges.push({kind: 'bell', text: 'bell'});
  if (windowRecord?.silence_flag === true) badges.push({kind: 'silence', text: 'silent'});
  const presenceText = tmuxSignalWindowPresenceText(windowRecord);
  if (presenceText) badges.push({kind: 'presence', text: presenceText});
  if (windowRecord?.zoomed === true) badges.push({kind: 'zoom', text: 'zoom'});
  for (const label of tmuxSignalPaneModeLabels(pane)) badges.push({kind: 'mode', text: label});
  if (!badges.length) return '';
  return `<span class="yoagent-recent-agent-signals">${badges.map(badge => `<span class="yoagent-recent-agent-signal signal-${esc(badge.kind)}">${esc(badge.text)}</span>`).join('')}</span>`;
}

function yoagentRecentAgentRestartHtml(agent, signal) {
  if (readOnlyMode || signal?.pane?.dead !== true) return '';
  const kind = String(agent?.agent_kind || tmuxSignalPaneCommand(signal.pane) || '').toLowerCase();
  if (!tmuxSignalAgentCommands.has(kind)) return '';
  return `<button type="button" class="yoagent-recent-agent-restart" data-yolomux-agent-restart="${esc(kind)}" title="Create a new ${esc(agentLabel(kind))} session">Restart</button>`;
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
      agent.cwd ? `cwd: ${agent.cwd}` : '',
      pathText ? `paths: ${pathText}` : '',
      agent.transcript ? `transcript: ${agent.transcript}` : '',
      signal.window?.activity_ts ? `tmux activity: ${new Date(Number(signal.window.activity_ts) * 1000).toISOString()}` : '',
      tmuxSignalWindowClientNames(signal.window).length ? `tmux viewers: ${tmuxSignalWindowClientNames(signal.window).join(', ')}` : '',
      signal.window?.layout ? `tmux layout: ${signal.window.layout}` : '',
      signal.window?.visible_layout ? `tmux visible layout: ${signal.window.visible_layout}` : '',
      signal.window?.bell_flag ? 'tmux bell alert' : '',
      signal.window?.silence_flag ? 'tmux silence alert' : '',
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
    <span class="yoagent-recent-agents-label">${esc(t('yoagent.recentAgents.label'))}</span>
    <ul class="yoagent-recent-agents-list">${rows}</ul>
  </div>`;
}

function yoagentRecentAgentsMessageHtml() {
  const payload = yoagentStartupActivityPayload();
  const html = yoagentRecentAgentsHtml(payload);
  if (!html || !yoagentChatEnabled()) return '';
  return `<div class="yoagent-message assistant yoagent-recent-agents-message">
    <div class="yoagent-message-role"><span>${esc(yoagentTabLabel())}</span>${yoagentMessageTimestampHtml(payload?.generated_at)}</div>
    ${html}
  </div>`;
}

async function loadYoagentConversation(options = {}) {
  if (readOnlyMode || yoagentConversationLoading) return;
  if (yoagentConversationLoaded && options.force !== true) return;
  yoagentConversationLoading = true;
  if (options.render !== false) renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    const payload = await apiFetchJson('/api/yoagent/conversation', {cache: 'no-store'});
    applyYoagentConversationPayload(payload);
  } catch (error) {
    if (!options.silent) statusErr(localizedHtml('yoagent.conversationLoadFailed', {error}));
  } finally {
    yoagentConversationLoading = false;
    if (options.render !== false) renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom ?? false});
  }
}

async function loadYoagentJobs(options = {}) {
  if (yoagentJobsLoading) return false;
  yoagentJobsLoading = true;
  try {
    const payload = await apiFetchJson('/api/yoagent/jobs', {cache: 'no-store'});
    applyYoagentJobsPayload(payload);
    if (options.render !== false && infoPanelSubTab === 'yoagent') renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom || false});
    return true;
  } catch (error) {
    if (!options.silent) console.warn('YO!agent jobs refresh failed', error);
    return false;
  } finally {
    yoagentJobsLoading = false;
  }
}

function yoagentBackendLabel(value) {
  const key = String(value || '').toLowerCase();
  if (key === 'auto') return t('yoagent.backend.auto');
  if (key === 'deterministic') return t('yoagent.backend.none');
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return value || t('yoagent.backend.none');
}

function yoagentBackendKey() {
  return String(initialSetting('yoagent.backend', 'auto') || 'auto').trim().toLowerCase();
}

const YOAGENT_CHAT_BACKENDS = ['codex', 'claude'];

function yoagentBackendUsable(agent) {
  return YOAGENT_CHAT_BACKENDS.includes(agent) && availableAgents.has(agent) && agentLoggedIn(agent);
}

function yoagentAvailableBackendOptions() {
  return YOAGENT_CHAT_BACKENDS.filter(yoagentBackendUsable);
}

// #41: mirror the server's auto-resolution (codex -> claude -> deterministic) using the cached agent
// login status, so the chat input enables/disables to match what the backend will actually run.
function yoagentResolvedBackend() {
  const key = yoagentBackendKey();
  if (YOAGENT_CHAT_BACKENDS.includes(key)) return yoagentBackendUsable(key) ? key : 'deterministic';
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
  const effectiveChoices = choices.length ? choices : [{value: '', label: noneLabel || t('yoagent.backend.none')}];
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
    noneLabel: t('yoagent.backend.none'),
    dot: true,
  });
  const modelPath = backend ? `yoagent.${backend}_model` : 'yoagent.backend';
  const modelChoices = backend ? yoagentModelPreferenceChoicesForBackend(backend) : [];
  const modelHtml = yoagentComposerSelectHtml({
    kind: 'model',
    title: backend === 'claude' ? t('pref.yoagent.claude_model.label') : backend === 'codex' ? t('pref.yoagent.codex_model.label') : t('yoagent.backend.none'),
    path: modelPath,
    value: yoagentChoiceValue(modelPath, modelChoices),
    choices: modelChoices,
    disabled,
    noneLabel: t('yoagent.backend.none'),
  });
  const effortPath = backend ? `yoagent.${backend}_effort` : 'yoagent.backend';
  const effortChoices = backend ? yoagentEffortPreferenceChoicesForBackend(backend) : [];
  const effortHtml = yoagentComposerSelectHtml({
    kind: 'effort',
    title: backend === 'claude' ? t('pref.yoagent.claude_effort.label') : backend === 'codex' ? t('pref.yoagent.codex_effort.label') : t('yoagent.backend.none'),
    path: effortPath,
    value: yoagentChoiceValue(effortPath, effortChoices),
    choices: effortChoices,
    disabled,
    noneLabel: t('yoagent.backend.none'),
  });
  return backendHtml + modelHtml + effortHtml;
}

function yoagentChatHtml() {
  const chatEnabled = yoagentChatEnabled();
  const disabled = !chatEnabled ? ' disabled' : '';
  const backendDisabled = yoagentBusy || readOnlyMode ? ' disabled' : '';
  const placeholder = t('yoagent.chatPlaceholder');
  const isThinking = yoagentBusy || yoagentPrewarming;
  const startupInfo = yoagentStartupInfoVisible ? yoagentStartupInfoHtml() : '';
  const hasConversation = Boolean(yoagentMessages.length || yoagentChatQueue.length || yoagentPendingWaits.length || yoagentJobs.length || yoagentNotice || isThinking || yoagentError || startupInfo || !chatEnabled);
  const thinkingHtml = textWithMovingEllipsisHtml(t('yoagent.thinking'), 'yoagent-thinking-dots');
  const busy = isThinking
    ? `<div class="yoagent-chat-status"><span class="session-yolo-marker active working yoagent-chat-spinner" style="--yolo-rotate-delay: ${esc(yoloRotationDelay())}" aria-hidden="true">${esc(t('brand.marker'))}</span><span class="yoagent-thinking">${thinkingHtml}</span></div>`
    : '';
  const retry = yoagentError && yoagentDraft && yoagentChatEnabled() && !yoagentBusy
    ? `<button type="button" class="yoagent-chat-retry" data-yoagent-retry>${esc(t('yoagent.retry'))}</button>`
    : '';
  const error = yoagentError ? `<div class="yoagent-chat-error"><span>${esc(yoagentError)}</span>${retry}</div>` : '';
  const chatDisabled = !chatEnabled ? `<div class="yoagent-chat-disabled">${esc(t('yoagent.chatDisabled'))}</div>` : '';
  const clearDisabled = yoagentBusy || readOnlyMode || (!yoagentMessages.length && !yoagentNotice && !yoagentError) ? ' disabled' : '';
  const submitButton = yoagentBusy
    ? `<button type="button" class="yoagent-chat-stop" data-yoagent-chat-cancel title="${esc(t('yoagent.stop'))}" aria-label="${esc(t('yoagent.stop'))}">×</button>`
    : `<button type="submit" class="yoagent-chat-send"${disabled} title="${esc(t('yoagent.ask'))}" aria-label="${esc(t('yoagent.ask'))}">
          <svg class="yoagent-chat-send-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M12 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>`;
  const form = `<form class="yoagent-chat-form" data-yoagent-chat-form>
      <input type="text" class="yoagent-chat-input" data-yoagent-chat-input value="${esc(yoagentDraft)}" placeholder="${esc(placeholder)}"${disabled}>
      <div class="yoagent-chat-controls">
        ${yoagentComposerControlsHtml(backendDisabled)}
        <span class="yoagent-chat-controls-spacer"></span>
        <button type="button" class="yoagent-chat-clear" data-yoagent-clear${clearDisabled}>${esc(t('yoagent.clear'))}</button>
        ${submitButton}
      </div>
    </form>`;
  return `<section class="yoagent-chat ${hasConversation ? 'has-history' : 'empty'}" aria-label="${esc(t('yoagent.chatAria', {name: yoagentTabLabel()}))}">
    ${yoagentTranscriptPathHtml()}
    <div class="yoagent-chat-history">${yoagentNoticeHtml()}${yoagentChatMessagesHtml()}${yoagentChatQueueHtml()}${yoagentPendingWaitsHtml()}${yoagentJobsHtml()}${busy}${error}${chatDisabled}</div>
    ${form}
  </section>`;
}

function yoagentChatNetworkError(error) {
  const text = String(error?.message || error || '');
  return error instanceof TypeError || /failed to fetch|networkerror|load failed|fetch failed/i.test(text);
}

function yoagentChatErrorMessage(error) {
  if (yoagentChatNetworkError(error)) {
    return t('yoagent.networkError');
  }
  return t('yoagent.chatFailed', {error: error?.message || error});
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
  yoagentChatQueue.push({id: `queued-${++yoagentChatQueueSerial}`, text: value, createdAt: new Date().toISOString()});
  yoagentDraft = '';
  resetYoagentComposerHistory();
  renderYoagentPanel({preserveDraft: false, scrollBottom: true, allowBusyRebuild: true, focusInput: true});
  return true;
}

function cancelQueuedYoagentChatMessage(queueId) {
  const id = String(queueId || '');
  if (!id) return false;
  const previousLength = yoagentChatQueue.length;
  yoagentChatQueue = yoagentChatQueue.filter(item => String(item?.id || '') !== id);
  const changed = yoagentChatQueue.length !== previousLength;
  if (changed) renderYoagentPanel({preserveDraft: true, scrollBottom: false, allowBusyRebuild: true});
  return changed;
}

function finishYoagentActiveRequest(requestId, options = {}) {
  if (!yoagentActiveChatRequest || yoagentActiveChatRequest.id !== requestId) return false;
  yoagentActiveChatRequest = null;
  yoagentBusy = false;
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

function drainYoagentChatQueue() {
  if (yoagentBusy || yoagentActiveChatRequest || yoagentPendingWaits.length || !yoagentChatEnabled() || !yoagentChatQueue.length) return false;
  const next = yoagentChatQueue.shift();
  if (!next) return false;
  startYoagentChatRequest(next.text, {fromQueue: true});
  return true;
}

function cancelActiveYoagentChatRequest() {
  const active = yoagentActiveChatRequest;
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
  yoagentMessages = [];
  yoagentPendingWaits = [];
  yoagentChatQueue = [];
  if (yoagentActiveChatRequest) cancelActiveYoagentChatRequest();
  yoagentBusy = false;
  yoagentPrewarming = false;
  yoagentPrewarmStarted = false;
  yoagentStartupLlmRequested = false;
  if (yoagentStreamingMessages instanceof Map) yoagentStreamingMessages.clear();
  yoagentError = '';
  yoagentDraft = '';
  resetYoagentComposerHistory();
  yoagentNotice = null;
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
      const next = yoagentJobs.map(job => String(job?.id || job?.job_id || '') === id ? payload.job : job);
      if (!next.some(job => String(job?.id || job?.job_id || '') === id)) next.unshift(payload.job);
      yoagentJobs = next;
    }
    await loadYoagentJobs({silent: true, render: false});
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  } catch (error) {
    yoagentError = yoagentChatErrorMessage(error);
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
    yoagentError = yoagentChatErrorMessage(error);
    renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  }
}

async function startYoagentChatRequest(rawText, options = {}) {
  const text = String(rawText || '').trim();
  if (!text || yoagentBusy || yoagentActiveChatRequest || !yoagentChatEnabled()) return;
  resetYoagentComposerHistory();
  installYoagentFocusTracker();
  const requestId = yoagentNewChatRequestId();
  const streamId = requestId;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  yoagentActiveChatRequest = {id: requestId, streamId, controller, text, cancelled: false};
  const focusSerial = yoagentFocusSerial;
  const shouldRestoreFocus = yoagentChatInputIsFocused() && yoagentDocumentHasFocus();
  yoagentMessages.push({role: 'user', content: text, createdAt: new Date().toISOString()});
  yoagentDraft = '';
  yoagentBusy = true;
  yoagentPrewarming = false;
  yoagentError = '';
  yoagentNotice = null;
  if (yoagentStreamingMessages instanceof Map) yoagentStreamingMessages.clear();
  startYoagentLocalThinkingStream(streamId);
  renderYoagentPanel({preserveDraft: false, scrollBottom: true, allowBusyRebuild: true});
  try {
    const payload = await apiFetchJson('/api/yoagent/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text, history: yoagentMessages.slice(-11, -1), locale: i18nActiveLocaleId(), request_id: requestId, stream_id: streamId}),
      signal: controller?.signal,
    });
    if (yoagentActiveChatRequest?.id !== requestId) return;
    if (payload.cancelled) {
      applyYoagentStreamPayload({stream_id: streamId, phase: 'stopped', done: true, aborted: true, auxiliary_done: true});
      return;
    }
    if (payload.fallback && payload.fallback_reason) {
      yoagentNotice = {backend: yoagentBackendLabel(payload.backend_used || payload.backend), reason: payload.fallback_reason};
    }
    if (!applyYoagentConversationPayload(payload.conversation || {})) {
      yoagentMessages.push({
        role: 'assistant',
        content: payload.answer || t('yoagent.noAnswer'),
        actions: Array.isArray(payload.actions) ? payload.actions : [],
        details: payload.details || '',
        createdAt: payload.answered_at || new Date().toISOString(),
      });
    }
    statusEl.textContent = t('yoagent.statusAnswered', {backend: yoagentBackendLabel(payload.backend_used || payload.backend)});
  } catch (error) {
    if (yoagentActiveChatRequest?.id !== requestId || yoagentChatAbortError(error)) return;
    if (yoagentChatNetworkError(error)) yoagentDraft = text;
    yoagentError = yoagentChatErrorMessage(error);
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
  if (!text || !yoagentChatEnabled()) return;
  if (yoagentBusy || yoagentActiveChatRequest || yoagentPendingWaits.length) {
    enqueueYoagentChatMessage(text);
    return;
  }
  return startYoagentChatRequest(text);
}

function updateYoagentActionPreview(previewId, patch) {
  let changed = false;
  for (const message of yoagentMessages) {
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
  if (!previewId || readOnlyMode || yoagentBusy) return;
  hideYoagentStartupInfo();
  yoagentBusy = true;
  updateYoagentActionPreview(previewId, {status_text: 'sending'});
  renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    const payload = await apiFetchJson('/api/yoagent/actions/execute-send', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({preview_id: previewId}),
    });
    applyYoagentConversationPayload(payload.conversation || {});
    updateYoagentActionPreview(previewId, {status: 'sent', status_text: 'sent'});
    const answer = payload.answer
      ? t('yoagent.action.sentWithAnswer', {session: payload.session, transport: payload.transport, answer: payload.answer})
      : t('yoagent.action.sent', {session: payload.session, transport: payload.transport});
    if (!payload.conversation) yoagentMessages.push({role: 'assistant', content: answer, createdAt: new Date().toISOString()});
    statusEl.textContent = t('yoagent.statusActionSent', {session: payload.session});
  } catch (error) {
    updateYoagentActionPreview(previewId, {status: 'error', status_text: error?.message || String(error)});
    yoagentError = yoagentChatErrorMessage(error);
  } finally {
    yoagentBusy = false;
    renderYoagentPanel({preserveDraft: true, scrollBottom: true});
  }
}

async function prewarmYoagent(options = {}) {
  if (yoagentPrewarmStarted || readOnlyMode || !yoagentChatEnabled()) return;
  const shouldRequestStartupAnswer = !yoagentStartupLlmRequested && !yoagentMessages.length && !yoagentConversationLoaded;
  if (shouldRequestStartupAnswer) yoagentStartupLlmRequested = true;
  yoagentPrewarmStarted = true;
  yoagentPrewarming = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false});
  try {
    const payload = await apiFetchJson('/api/yoagent/prewarm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({locale: i18nActiveLocaleId(), visible: shouldRequestStartupAnswer}),
    });
    if (payload?.fallback && payload.fallback_reason) {
      yoagentNotice = {backend: yoagentBackendLabel(payload.backend_used || payload.backend), reason: payload.fallback_reason};
    }
    if (payload?.conversation) applyYoagentConversationPayload(payload.conversation || {});
  } catch (error) {
    if (shouldRequestStartupAnswer) {
      yoagentError = yoagentChatErrorMessage(error);
    }
    // Non-visible process warm-up is opportunistic; visible chat requests handle real errors.
  } finally {
    yoagentPrewarming = false;
    renderYoagentPanel({preserveDraft: true, scrollBottom: options.scrollBottom === true});
  }
}

function activitySummaryIsVisible() {
  return infoPanelSubTab === 'yoagent' && itemIsActivePaneTab(infoItemId);
}

async function refreshActivitySummary(options = {}) {
  if (options.silent === true) {
    if (typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
    return;
  }
  if (activitySummaryRefreshing && options.force !== true) return;
  const requestIsCurrent = activitySummaryGuard.begin();
  activitySummaryRefreshing = true;
  renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
  try {
    const params = new URLSearchParams();
    if (options.force) params.set('force', '1');
    params.set('locale', i18nActiveLocaleId());
    params.set('scope', 'all');
    params.set('hours', String(infoSessionFileLookbackHours));
    const payload = await apiFetchJson(`/api/activity-summary?${params.toString()}`, {cache: 'no-store'});
    if (!requestIsCurrent()) return;
    applyActivitySummaryPayloadFromPush(payload, {refreshStartupSnapshot: true, render: true});
  } catch (error) {
    if (!requestIsCurrent()) return;
    activitySummaryPayload = {
      ...activitySummaryPayload,
      errors: [String(error)],
      global: {lines: [`activity summary unavailable: ${String(error)}`]},
    };
    if (!options.silent) statusErr(localizedHtml('status.activitySummaryFailed', {error}));
  } finally {
    if (requestIsCurrent()) {
      activitySummaryRefreshing = false;
      renderInfoPanel();
      renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
      if (infoPanelSubTab === 'yoagent') prewarmYoagent();
    }
  }
}

function applyActivitySummaryPayloadFromPush(payload = {}, options = {}) {
  if (!payload || typeof payload !== 'object') return false;
  if (payload.session_file_hours != null) {
    infoSessionFileLookbackHours = writeStoredInfoLookbackHours(payload.session_file_hours);
  }
  activitySummaryPayload = payload;
  activitySummaryLastRefreshTs = Date.now();
  activitySummaryRefreshing = false;
  clearInfoSessionDrawerCache();
  if (options.refreshStartupSnapshot === true) captureYoagentStartupActivitySummarySnapshot({replace: true});
  if (options.render === true || options.refreshStartupSnapshot === true) {
    renderInfoPanel();
    renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
    if (infoPanelSubTab === 'yoagent') prewarmYoagent();
  }
  return true;
}
