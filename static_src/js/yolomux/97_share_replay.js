// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Share mirror protocol, DOM replay, pointer mirroring, scroll sync, and geometry diagnostics.
// Share mirror protocol owner. Replay code must add names here first, then reference these constants.
const shareMirrorFrameTypes = Object.freeze({
  uiState: 'ui-state',
  layout: 'layout',
  viewport: 'viewport',
  appearance: 'appearance',
  popupLayer: 'popup-layer',
  geometryDigest: 'geometry-digest',
  hostResize: 'host-resize',
  pointer: 'pointer',
  scroll: 'scroll',
  fileVersion: 'file-version',
  activeTab: 'active-tab',
  focus: 'focus',
  finderMode: 'finder-mode',
  menu: 'menu',
  shareStatus: 'share-status',
  domKeyframe: 'dom-keyframe',
  domDelta: 'dom-delta',
  domKeyframeRequest: 'dom-keyframe-request',
  domKeyframeAck: 'dom-keyframe-ack',
  domReplayError: 'dom-replay-error',
  terminalHostResize: 'terminal-host-resize',
  textWrapMetrics: 'text-wrap-metrics',
  inputIntent: 'input-intent',
});

const shareMirrorReplayFrameTypes = Object.freeze([
  shareMirrorFrameTypes.domKeyframe,
  shareMirrorFrameTypes.domDelta,
  shareMirrorFrameTypes.domKeyframeRequest,
  shareMirrorFrameTypes.domKeyframeAck,
  shareMirrorFrameTypes.domReplayError,
  shareMirrorFrameTypes.terminalHostResize,
]);

const shareMirrorProtocol = Object.freeze({
  version: 1,
  frames: shareMirrorFrameTypes,
  replayFrameTypes: shareMirrorReplayFrameTypes,
  sequencedFrameTypes: Object.freeze([
    shareMirrorFrameTypes.uiState,
    shareMirrorFrameTypes.layout,
    shareMirrorFrameTypes.viewport,
    shareMirrorFrameTypes.appearance,
    shareMirrorFrameTypes.popupLayer,
    shareMirrorFrameTypes.geometryDigest,
    shareMirrorFrameTypes.hostResize,
    ...shareMirrorReplayFrameTypes,
  ]),
  keyframeReasons: Object.freeze(['join', 'gap', 'digest', 'replay-error', 'backpressure', 'topology', 'manual-debug']),
  sequenceFields: Object.freeze(['epoch', 'sequence', 'baseSequence']),
  redaction: Object.freeze({
    policyVersion: 1,
    metadataFields: Object.freeze(['policyVersion', 'removedCount']),
  }),
  terminalPlaceholder: Object.freeze({
    dataset: 'shareTerminalPlaceholder',
    fields: Object.freeze(['placeholderId', 'session', 'rows', 'cols', 'terminalEpoch']),
  }),
  inputIntentTypes: Object.freeze({
    terminalInput: 'terminal-input',
    terminalPaste: 'terminal-paste',
    terminalScroll: 'terminal-scroll',
    tabActivate: 'tab-activate',
    menuCommand: 'menu-command',
    hostCommand: 'host-command',
  }),
  debugNames: Object.freeze({
    domKeyframe: 'DOM keyframe',
    domDelta: 'DOM delta',
    keyframeRequest: 'DOM keyframe request',
    replayError: 'DOM replay error',
    terminalPlaceholder: 'terminal placeholder',
  }),
});

// End share mirror protocol owner.

const shareMirrorSequencedFrameTypes = new Set(shareMirrorProtocol.sequencedFrameTypes);
const shareMirrorReplayFrameTypeSet = new Set(shareMirrorProtocol.replayFrameTypes);

function shareMirrorFrameTypeIsSequenced(type) {
  return shareMirrorSequencedFrameTypes.has(String(type || ''));
}

function shareMirrorFrameTypeIsReplay(type) {
  return shareMirrorReplayFrameTypeSet.has(String(type || ''));
}

function shareMirrorFrameTypeIsDomReplayContent(type) {
  const cleanType = String(type || '');
  return cleanType === shareMirrorProtocol.frames.domKeyframe || cleanType === shareMirrorProtocol.frames.domDelta;
}

function shareMirrorFrameReason(type, options = {}) {
  const reason = String(options.reason || type || 'update').trim();
  return reason || 'update';
}

function shareDomReplayFrameMetadata(type, options = {}, commit = true) {
  let epoch = Math.max(1, Math.floor(Number(shareReplayMirrorEpoch) || 1));
  let sequence = Math.max(0, Math.floor(Number(shareReplayMirrorSequence) || 0));
  if (type === shareMirrorProtocol.frames.domKeyframe || options.resetEpoch === true) {
    epoch += 1;
  }
  sequence += 1;
  if (commit) {
    shareReplayMirrorEpoch = epoch;
    shareReplayMirrorSequence = sequence;
  }
  return {
    epoch,
    sequence,
    reason: shareMirrorFrameReason(type, options),
  };
}

function shareSemanticFrameMetadata(type, options = {}, commit = true) {
  let epoch = Math.max(1, Math.floor(Number(shareMirrorEpoch) || 1));
  let sequence = Math.max(0, Math.floor(Number(shareMirrorSequence) || 0));
  if (type === shareMirrorProtocol.frames.uiState || options.resetEpoch === true) {
    epoch += 1;
  }
  sequence += 1;
  if (commit) {
    shareMirrorEpoch = epoch;
    shareMirrorSequence = sequence;
  }
  return {
    epoch,
    sequence,
    reason: shareMirrorFrameReason(type, options),
  };
}

function shareNextDomReplayFrameMetadata(type, options = {}) {
  return shareDomReplayFrameMetadata(type, options, true);
}

function shareNextMirrorFrameMetadata(type, options = {}) {
  if (shareMirrorFrameTypeIsDomReplayContent(type)) return shareNextDomReplayFrameMetadata(type, options);
  return shareSemanticFrameMetadata(type, options, true);
}

function sharePeekMirrorFrameMetadata(type, options = {}) {
  if (shareMirrorFrameTypeIsDomReplayContent(type)) return shareDomReplayFrameMetadata(type, options, false);
  return shareSemanticFrameMetadata(type, options, false);
}

function shareCommitBuiltUiMessage(message = {}) {
  if (!message || !shareMirrorFrameTypeIsSequenced(message.type)) return;
  const epoch = Math.max(1, Math.round(Number(message.epoch) || 1));
  const sequence = Math.max(0, Math.round(Number(message.sequence) || 0));
  if (shareMirrorFrameTypeIsDomReplayContent(message.type)) {
    shareReplayMirrorEpoch = Math.max(shareReplayMirrorEpoch, epoch);
    shareReplayMirrorSequence = Math.max(shareReplayMirrorSequence, sequence);
    return;
  }
  shareMirrorEpoch = Math.max(shareMirrorEpoch, epoch);
  shareMirrorSequence = Math.max(shareMirrorSequence, sequence);
}

function shareBuildUiMessage(type, payload = {}, options = {}) {
  const message = {type, payload, sender: shareClientId};
  if (shareMirrorFrameTypeIsReplay(type)) message.version = shareMirrorProtocol.version;
  if (shareMirrorFrameTypeIsSequenced(type)) {
    Object.assign(message, options.commitSequence === false
      ? sharePeekMirrorFrameMetadata(type, options)
      : shareNextMirrorFrameMetadata(type, options));
  }
  if (type === shareMirrorProtocol.frames.domDelta) {
    const explicitBase = Number(payload?.baseSequence ?? options.baseSequence);
    message.baseSequence = Number.isFinite(explicitBase) ? Math.max(0, Math.round(explicitBase)) : Math.max(0, Number(message.sequence || 0) - 1);
  }
  return message;
}

function shareSendInputIntent(payload = {}, options = {}) {
  if (!shareViewMode || !shareWriteMode || !shareToken || !payload || typeof payload !== 'object') return false;
  if (shareReplayShellActive) {
    return shareReplaySendUiMessage(shareBuildUiMessage(shareMirrorProtocol.frames.inputIntent, payload, options));
  }
  sharePublish(shareMirrorProtocol.frames.inputIntent, payload, options);
  return true;
}

function shareSendTerminalInputIntent(session, data) {
  const filtered = stripTerminalQueryResponses(data);
  if (!filtered) return false;
  return shareSendInputIntent({
    intent: shareMirrorProtocol.inputIntentTypes.terminalInput,
    session: String(session || ''),
    data: filtered,
  }, {reason: 'terminal-input'});
}

function applyShareInputIntent(payload = {}) {
  if (shareViewMode || !payload || typeof payload !== 'object') return false;
  const intent = String(payload.intent || '');
  if (intent === shareMirrorProtocol.inputIntentTypes.tabActivate) {
    const item = resolveLayoutItem(payload.item || payload.session || '');
    const slot = slotForItem(item);
    if (!slot) return false;
    activatePaneTab(slot, item, {userInitiated: false});
    return true;
  }
  if (intent === shareMirrorProtocol.inputIntentTypes.hostCommand && payload.command === 'request-keyframe') {
    return sharePublishDomKeyframe('manual-debug');
  }
  return false;
}

function shareReplayFeatureEnabled() {
  if (!shareViewMode && shareHasActiveShare()) return true;
  return shareReplayEnabled === true;
}

function shareHostConnectedViewerCount() {
  if (shareViewMode) return 0;
  return (activeShares || []).reduce((total, share) => {
    const count = Math.max(0, Math.floor(Number(share?.viewers) || 0));
    const details = Array.isArray(share?.viewerDetails) ? share.viewerDetails.length : 0;
    return total + Math.max(count, details);
  }, 0);
}

function shareHostHasConnectedViewers() {
  return shareHostConnectedViewerCount() > 0;
}

function shareReplayHostPerfCounter() {
  return {
    count: 0,
    totalMs: 0,
    maxMs: 0,
    lastMs: null,
    lastAt: 0,
    skippedNoViewers: 0,
    lastSkipAt: 0,
    lastViewerCount: 0,
    lastDetail: {},
  };
}

const shareReplayHostPerformance = {
  geometryDigest: shareReplayHostPerfCounter(),
  mutationRecords: shareReplayHostPerfCounter(),
  mutationFlush: shareReplayHostPerfCounter(),
  mutationObserver: {
    installed: 0,
    disconnected: 0,
    skippedNoViewers: 0,
    lastAt: 0,
    lastSkipAt: 0,
    active: false,
  },
};

function shareReplayHostPerfMs(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 10) / 10 : null;
}

function shareReplayDebugPerfEventsEnabled() {
  return typeof debugModeEnabled !== 'undefined'
    && debugModeEnabled === true
    && typeof recordJsDebugEvent === 'function';
}

function shareReplayRecordHostPerfEvent(kind = '', payload = {}, durationMs = null) {
  if (!shareReplayDebugPerfEventsEnabled()) return;
  if (durationMs !== null && durationMs < 8 && kind !== 'geometryDigest') return;
  recordJsDebugEvent('share-replay-perf', {
    kind,
    ...payload,
  });
}

function shareReplayRecordHostPerfSkip(kind = '', reason = 'no-viewers') {
  const counter = shareReplayHostPerformance[kind];
  if (!counter) return;
  counter.skippedNoViewers = Math.max(0, Math.round(Number(counter.skippedNoViewers) || 0)) + 1;
  counter.lastSkipAt = Date.now();
  counter.lastViewerCount = shareHostConnectedViewerCount();
  shareReplayRecordHostPerfEvent(kind, {
    reason,
    viewerCount: counter.lastViewerCount,
    skippedNoViewers: counter.skippedNoViewers,
  });
}

function shareReplayRecordHostPerf(kind = '', startedAt = 0, detail = {}) {
  const counter = shareReplayHostPerformance[kind];
  if (!counter) return;
  const durationMs = shareReplayHostPerfMs(performanceNow() - Number(startedAt || 0)) ?? 0;
  counter.count += 1;
  counter.totalMs = shareReplayHostPerfMs(Number(counter.totalMs || 0) + durationMs) ?? 0;
  counter.maxMs = Math.max(Number(counter.maxMs || 0), durationMs);
  counter.lastMs = durationMs;
  counter.lastAt = Date.now();
  counter.lastViewerCount = shareHostConnectedViewerCount();
  counter.lastDetail = {...detail};
  shareReplayRecordHostPerfEvent(kind, {
    durationMs,
    viewerCount: counter.lastViewerCount,
    ...detail,
  }, durationMs);
}

function shareReplayHostPerfSnapshot(counter = {}) {
  const count = Math.max(0, Math.round(Number(counter.count) || 0));
  const totalMs = shareReplayHostPerfMs(counter.totalMs) ?? 0;
  return {
    count,
    totalMs,
    avgMs: count ? shareReplayHostPerfMs(totalMs / count) : null,
    maxMs: shareReplayHostPerfMs(counter.maxMs) ?? 0,
    lastMs: counter.lastMs,
    lastAt: Math.max(0, Math.round(Number(counter.lastAt) || 0)),
    skippedNoViewers: Math.max(0, Math.round(Number(counter.skippedNoViewers) || 0)),
    lastSkipAt: Math.max(0, Math.round(Number(counter.lastSkipAt) || 0)),
    lastViewerCount: Math.max(0, Math.round(Number(counter.lastViewerCount) || 0)),
    lastDetail: {...(counter.lastDetail || {})},
  };
}

function shareReplayHostPerformanceDiagnostics() {
  return {
    viewerCount: shareHostConnectedViewerCount(),
    mutationObserver: {
      ...shareReplayHostPerformance.mutationObserver,
      active: Boolean(shareReplayMutationObserver),
    },
    geometryDigest: shareReplayHostPerfSnapshot(shareReplayHostPerformance.geometryDigest),
    mutationRecords: shareReplayHostPerfSnapshot(shareReplayHostPerformance.mutationRecords),
    mutationFlush: shareReplayHostPerfSnapshot(shareReplayHostPerformance.mutationFlush),
  };
}

function shareReplayHostNodeId(node) {
  if (!node || (typeof node !== 'object' && typeof node !== 'function') || (typeof Node !== 'undefined' && !(node instanceof Node) && !node.localName && !node.tagName && Number(node.nodeType) !== 3)) {
    return 0;
  }
  const existing = shareReplayHostNodeIds.get(node);
  if (existing) return existing;
  const next = Math.max(1, Math.round(Number(shareReplayHostNextNodeId) || 1));
  shareReplayHostNextNodeId = next + 1;
  shareReplayHostNodeIds.set(node, next);
  return next;
}

function shareReplayRememberSerializedNode(context, node) {
  if (!context || !node || (typeof node !== 'object' && typeof node !== 'function')) return;
  if (!Array.isArray(context.mirroredNodes)) context.mirroredNodes = [];
  context.mirroredNodes.push(node);
}

function shareReplayHostMirroredNodeSet(nodes = []) {
  const result = new WeakSet();
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node && (typeof node === 'object' || typeof node === 'function')) result.add(node);
  }
  return result;
}

function shareReplayHostMergeMirroredNodes(nodes = []) {
  for (const node of Array.isArray(nodes) ? nodes : []) {
    if (node && (typeof node === 'object' || typeof node === 'function')) shareReplayHostMirroredNodes.add(node);
  }
}

function shareReplayHostForgetMirroredSubtree(node) {
  if (!node || (typeof node !== 'object' && typeof node !== 'function')) return;
  shareReplayHostMirroredNodes.delete(node);
  for (const child of Array.from(node.childNodes || node.children || [])) shareReplayHostForgetMirroredSubtree(child);
}

function shareReplaySerializedNodeId(node, context) {
  if (context?.useStableNodeIds === true) return shareReplayHostNodeId(node);
  const next = Math.max(1, Math.round(Number(context.nextNodeId) || 1));
  context.nextNodeId = next + 1;
  return next;
}

function shareReplayDatasetAttributeName(key = '') {
  return `data-${String(key || '').replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)}`;
}

function shareReplayElementHasFlag(element, name) {
  const dataKey = name.replace(/^data-/, '').replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  return element?.hasAttribute?.(name) || element?.dataset?.[dataKey] !== undefined;
}

const shareReplayBlockedTags = new Set(['script', 'iframe', 'object', 'embed', 'style']);
const shareReplayAllowedHtmlTags = new Set([
  'a', 'abbr', 'article', 'aside', 'b', 'br', 'button', 'canvas', 'code', 'dd', 'del', 'details', 'div', 'dl', 'dt', 'em', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hr', 'i', 'img', 'input', 'kbd', 'label', 'legend', 'li', 'main', 'mark', 'menu', 'nav', 'ol', 'option', 'p', 'pre', 'progress', 's', 'samp', 'section', 'select', 'small', 'span', 'strong', 'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'textarea', 'tfoot', 'th', 'thead', 'time', 'tr', 'u', 'ul',
]);
const shareReplayAllowedSvgTags = new Set([
  'circle', 'defs', 'ellipse', 'g', 'line', 'path', 'polyline', 'rect', 'svg', 'text', 'title', 'tspan',
]);
const shareReplaySvgNamespace = 'http://www.w3.org/2000/svg';

function shareReplayRedactText(value) {
  let text = String(value ?? '');
  for (const secret of shareDebugSecretValues()) {
    text = text.split(secret).join('[redacted-share-token]');
  }
  return text
    .replace(/(?:https?:\/\/[^\s"'<>]+)?\/share\/[A-Za-z0-9_-]+(?:#[^\s"'<>]*)?/g, '[redacted-share-url]')
    .replace(/([?#&](?:t|token|share|shareToken|share_token)=)[^&#\s"']+/gi, '$1[redacted-share-token]')
    .replace(/\b((?:token|shareToken|share_token)=)[^&#\s"']+/gi, '$1[redacted-share-token]');
}

function shareReplayAttributeIsTokenBearing(name = '') {
  return /(?:^|[-_:])(?:token|sharetoken|share-token|share_token|secret|password)(?:$|[-_:])/i.test(String(name || ''));
}

function shareReplayUrlAttributeIsUnsafe(name = '', value = '') {
  const cleanName = String(name || '').toLowerCase();
  if (!['href', 'src', 'xlink:href', 'action', 'formaction'].includes(cleanName)) return false;
  const cleanValue = String(value || '').trim();
  if (!cleanValue) return false;
  if (/\/share\/[A-Za-z0-9_-]+/i.test(cleanValue) || /#t=/i.test(cleanValue)) return true;
  return /^(?:javascript|vbscript|data):/i.test(cleanValue);
}

function shareReplayElementRedactionAction(element) {
  const tag = String(element?.localName || element?.tagName || '').toLowerCase();
  if (shareReplayBlockedTags.has(tag)) return 'drop';
  if (shareReplayElementHasFlag(element, 'data-share-private') || shareReplayElementHasFlag(element, 'data-share-redact')) return 'drop';
  const type = String(element?.getAttribute?.('type') || element?.attributes?.type || '').toLowerCase();
  if ((tag === 'input' && type === 'password') || shareReplayElementHasFlag(element, 'data-share-secret')) return 'placeholder';
  return 'keep';
}

function shareReplayElementInlineStyleValue(element, property = '') {
  const style = element?.style;
  if (!style || !property) return '';
  if (typeof style.getPropertyValue === 'function') return String(style.getPropertyValue(property) || '');
  const camel = String(property).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
  return String(style[property] || style[camel] || '');
}

function shareReplayElementOrAncestorHidden(element) {
  let node = element;
  while (node) {
    if (node.hidden === true || node.hasAttribute?.('hidden')) return true;
    if (String(node.getAttribute?.('aria-hidden') || '').toLowerCase() === 'true') return true;
    if (String(shareReplayElementInlineStyleValue(node, 'display')).toLowerCase() === 'none') return true;
    if (String(shareReplayElementInlineStyleValue(node, 'visibility')).toLowerCase() === 'hidden') return true;
    node = node.parentElement;
  }
  return false;
}

function shareReplayElementHasVisibleBox(element) {
  if (!element) return false;
  const rect = typeof element.getBoundingClientRect === 'function' ? element.getBoundingClientRect() : null;
  const rectWidth = Number(rect?.width || 0);
  const rectHeight = Number(rect?.height || 0);
  const clientWidth = Number(element.clientWidth || 0);
  const clientHeight = Number(element.clientHeight || 0);
  const offsetWidth = Number(element.offsetWidth || 0);
  const offsetHeight = Number(element.offsetHeight || 0);
  return (rectWidth > 0 && rectHeight > 0) || (clientWidth > 0 && clientHeight > 0) || (offsetWidth > 0 && offsetHeight > 0);
}

function shareReplayElementIsTerminalContainer(element) {
  return Boolean(
    element?.classList?.contains?.('terminal')
    || element?.dataset?.shareTerminalSession
  );
}

function shareReplayTerminalSessionForElement(element) {
  const id = String(element?.id || '');
  if (element?.classList?.contains?.('terminal') && id.startsWith('term-')) return id.slice('term-'.length);
  if (element?.dataset?.shareTerminalSession) return String(element.dataset.shareTerminalSession || '');
  if (element?.dataset?.session && element?.classList?.contains?.('terminal')) return String(element.dataset.session || '');
  return '';
}

function shareScopedTerminalSessions() {
  const allowed = new Set();
  const add = value => {
    const session = String(value || '').trim();
    if (!session) return false;
    allowed.add(session);
    return true;
  };
  const addRecord = record => {
    if (!record || typeof record !== 'object') return;
    let added = false;
    if (Array.isArray(record.sessions)) {
      for (const session of record.sessions) added = add(session) || added;
    }
    if (!added) add(record.session);
  };
  if (shareBootstrap?.view) addRecord(shareBootstrap);
  for (const share of activeShares || []) addRecord(share);
  return allowed.size ? allowed : null;
}

function shareTerminalSessionAllowedForReplay(session) {
  const clean = String(session || '').trim();
  if (!clean) return false;
  const allowed = shareScopedTerminalSessions();
  return !allowed || allowed.has(clean);
}

function shareReplayTerminalElementIsVisible(element) {
  if (!element || element.isConnected === false) return false;
  if (element.closest?.('#panelPool')) return false;
  if (shareReplayElementOrAncestorHidden(element)) return false;
  return shareReplayElementHasVisibleBox(element);
}

function shareReplayTerminalPlaceholderForElement(element, nodeId) {
  const session = shareReplayTerminalSessionForElement(element);
  if (!session) return null;
  if (!shareTerminalSessionAllowedForReplay(session)) return null;
  if (!shareReplayTerminalElementIsVisible(element)) return null;
  const item = terminals.get(session);
  const hostSize = shareHostTerminalSize(session);
  const rows = Math.max(0, Math.round(Number(hostSize?.rows || item?.term?.rows || element?.dataset?.rows || 0)));
  const cols = Math.max(0, Math.round(Number(hostSize?.cols || item?.term?.cols || element?.dataset?.cols || 0)));
  const terminalEpoch = Math.max(0, Math.round(Number(element?.dataset?.terminalEpoch || shareReplayMirrorEpoch || 0)));
  const placeholderId = `term-ph-${session}`;
  return {
    node: {
      nodeId,
      tag: 'div',
      attrs: {
        class: 'share-terminal-placeholder',
        [shareReplayDatasetAttributeName(shareMirrorProtocol.terminalPlaceholder.dataset)]: session,
        'data-session': session,
        'data-rows': String(rows),
        'data-cols': String(cols),
      },
      children: [],
    },
    terminal: {placeholderId, session, rows, cols, terminalEpoch},
  };
}

function shareReplaySanitizeAttribute(name = '', value = '') {
  const cleanName = String(name || '').toLowerCase();
  if (!cleanName || cleanName.startsWith('on')) return null;
  if (cleanName === 'srcdoc') return null;
  if (shareReplayAttributeIsTokenBearing(cleanName)) return null;
  if (shareReplayUrlAttributeIsUnsafe(cleanName, value)) return null;
  return [String(name || '').trim(), shareReplayRedactText(value)];
}

function shareReplayAttributeNameIsSafe(name = '') {
  return /^[A-Za-z_:-][A-Za-z0-9_:\\.-]*$/.test(String(name || ''));
}

function shareReplayTagIsAllowed(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  return shareReplayAllowedHtmlTags.has(cleanTag) || shareReplayAllowedSvgTags.has(cleanTag);
}

function shareReplaySafeSerializedTag(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  if (shareReplayTagIsAllowed(cleanTag)) return cleanTag;
  return 'span';
}

function shareReplayCreateElement(tag = '') {
  const cleanTag = String(tag || '').toLowerCase();
  if (shareReplayBlockedTags.has(cleanTag)) {
    throw new Error(`unsupported replay tag: ${cleanTag || 'missing'}`);
  }
  const replayTag = shareReplaySafeSerializedTag(cleanTag);
  if (shareReplayAllowedSvgTags.has(cleanTag)) {
    return document.createElementNS(shareReplaySvgNamespace, replayTag);
  }
  return document.createElement(replayTag);
}

function shareReplayNormalizeNodeId(value) {
  const nodeId = Number(value);
  if (!Number.isSafeInteger(nodeId) || nodeId <= 0) throw new Error('invalid replay node id');
  return nodeId;
}

function shareReplayApplyAttributes(element, attrs = {}, options = {}) {
  if (!element || !attrs || typeof attrs !== 'object' || Array.isArray(attrs)) return;
  for (const [rawName, rawValue] of Object.entries(attrs)) {
    const sanitized = shareReplaySanitizeAttribute(rawName, rawValue);
    if (!sanitized) continue;
    const [name, value] = sanitized;
    const cleanName = String(name || '');
    if (!shareReplayAttributeNameIsSafe(cleanName)) continue;
    if (options.preserveRoot === true && cleanName === 'id') continue;
    if (options.preserveRoot === true && cleanName === 'class') continue;
    element.setAttribute(cleanName, value);
  }
}

function shareReplayResetMirrorRootAttributes(root, attrs = {}) {
  if (!root) return;
  const hostClass = String(attrs?.class || 'app-root').trim() || 'app-root';
  root.className = `${hostClass} share-replay-root`;
  root.id = 'appRoot';
  root.dataset.shareReplayRoot = 'true';
  root.dataset.shareReplayInert = 'true';
  root.setAttribute('role', 'presentation');
  root.setAttribute('aria-label', shareReplayMirrorLabel());
  root.setAttribute('tabindex', '-1');
  shareReplayApplyAttributes(root, attrs, {preserveRoot: true});
}

function shareReplayBuildNode(entry = {}, context) {
  if (!entry || typeof entry !== 'object') throw new Error('invalid replay node');
  const nodeId = shareReplayNormalizeNodeId(entry.nodeId);
  if (context.nodeMap.has(nodeId)) throw new Error(`duplicate replay node id: ${nodeId}`);
  if ('text' in entry && !entry.tag) {
    const node = document.createTextNode(shareReplayRedactText(entry.text));
    context.nodeMap.set(nodeId, node);
    return node;
  }
  const tag = String(entry.tag || '').toLowerCase();
  const element = shareReplayCreateElement(tag);
  if (element.dataset) element.dataset.shareReplayNodeId = String(nodeId);
  else element.setAttribute('data-share-replay-node-id', String(nodeId));
  shareReplayApplyAttributes(element, entry.attrs || {});
  context.nodeMap.set(nodeId, element);
  const children = Array.isArray(entry.children) ? entry.children : [];
  for (const child of children) {
    element.appendChild(shareReplayBuildNode(child, context));
  }
  if (!children.length && 'text' in entry) {
    element.appendChild(document.createTextNode(shareReplayRedactText(entry.text)));
  }
  return element;
}

function shareReplayNormalizeTerminalPlaceholder(entry = {}) {
  const placeholderId = String(entry?.placeholderId || '').slice(0, 200);
  const session = String(entry?.session || '').slice(0, 200);
  if (!placeholderId || !session) return null;
  return {
    placeholderId,
    session,
    rows: Math.max(0, Math.round(Number(entry.rows) || 0)),
    cols: Math.max(0, Math.round(Number(entry.cols) || 0)),
    terminalEpoch: Math.max(0, Math.round(Number(entry.terminalEpoch) || 0)),
  };
}

function shareReplayTerminalPlaceholderElement(entry = {}) {
  const session = String(entry?.session || '').trim();
  if (!session) return null;
  return document.querySelector?.(`[data-share-terminal-placeholder="${cssEscape(session)}"]`) || null;
}

function shareReplayPrepareTerminalPlaceholderElement(element, entry = {}) {
  if (!element || !entry?.session) return null;
  element.id = terminalDomId(entry.session);
  element.classList?.add('terminal', 'share-terminal-placeholder-bound');
  if (element.dataset) {
    element.dataset.session = entry.session;
    element.dataset.rows = String(entry.rows);
    element.dataset.cols = String(entry.cols);
    element.dataset.terminalEpoch = String(entry.terminalEpoch);
    element.dataset.shareTerminalPlaceholderId = entry.placeholderId;
  }
  element.setAttribute?.('role', 'presentation');
  element.setAttribute?.('aria-label', t('share.replay.sharedTerminalAria', {session: entry.session}));
  return element;
}

function shareReplayDetachTerminalPlaceholder(entry = {}) {
  const session = String(entry?.session || '');
  if (!session) return;
  const item = terminals.get(session);
  const termElement = item?.term?.element || item?.container?.querySelector?.('.xterm') || null;
  if (termElement?.parentElement) termElement.remove();
}

function shareReplayBindTerminalPlaceholder(entry = {}) {
  const element = shareReplayPrepareTerminalPlaceholderElement(shareReplayTerminalPlaceholderElement(entry), entry);
  if (!element) return false;
  updateShareHostTerminalSize(entry.session, entry.rows, entry.cols);
  const item = terminals.get(entry.session);
  if (item?.term) {
    const termElement = item.term.element || item.container?.querySelector?.('.xterm') || null;
    if (item.container !== element) {
      element.replaceChildren();
      if (termElement) element.appendChild(termElement);
      item.container = element;
    } else if (termElement && termElement.parentElement !== element) {
      element.appendChild(termElement);
    }
    item.placeholderId = entry.placeholderId;
    bindTerminalContainerForSession(entry.session, item.term, element);
    connectTerminalSocket(entry.session, item);
    scheduleFit(entry.session);
    return true;
  }
  startTerminal(entry.session);
  const created = terminals.get(entry.session);
  if (created) created.placeholderId = entry.placeholderId;
  return Boolean(created?.term && created.container === element);
}

function shareReplayApplyTerminalPlaceholders(terminals = []) {
  const next = new Map();
  for (const raw of Array.isArray(terminals) ? terminals : []) {
    const entry = shareReplayNormalizeTerminalPlaceholder(raw);
    if (!entry) continue;
    next.set(entry.placeholderId, entry);
  }
  for (const [placeholderId, entry] of shareReplayTerminalPlaceholders.entries()) {
    if (!next.has(placeholderId)) shareReplayDetachTerminalPlaceholder(entry);
  }
  shareReplayTerminalPlaceholders = next;
  for (const entry of shareReplayTerminalPlaceholders.values()) {
    shareReplayBindTerminalPlaceholder(entry);
  }
}

function bindShareReplayPaneTabPopovers(root = appRootElement()) {
  if (!shareReplayShellActive || shareReadOnlyReplayModeEnabled() || !root?.querySelectorAll || typeof createHoverPopover !== 'function') return 0;
  let bound = 0;
  const tabs = root.querySelectorAll('.pane-tab, .dockview-pane-tab, .tabber-session-tab, .panel-popover-zone');
  for (const tab of tabs) {
    if (!tab || tab.dataset?.shareReplayPopoverBound === 'true') continue;
    const popover = tab.querySelector?.(':scope > .session-popover');
    if (!popover) continue;
    if (tab.dataset) tab.dataset.shareReplayPopoverBound = 'true';
    const position = event => {
      if (typeof positionPaneTabPopover === 'function') positionPaneTabPopover(tab, popover, event);
    };
    createHoverPopover({
      anchor: tab,
      popover,
      showDelay: () => (document.querySelector('.pane-tab.popover-open, .dockview-pane-tab.popover-open, .tabber-session-tab.popover-open') ? tabPopoverFollowDelayMs : tabPopoverShowDelayMs),
      hideDelay: () => popoverHideDelayMs,
      canOpen: () => true,
      onQueue: position,
      onOpen: event => {
        popover.classList?.add('popover-open');
        position(event);
      },
      onClose: () => popover.classList?.remove('popover-open'),
      position,
      closeOthers: () => {
        if (typeof closeOtherSessionPopovers === 'function') closeOtherSessionPopovers(tab);
      },
    });
    bound += 1;
  }
  return bound;
}

function shareReplayApplyStaticKeyframe(payload = {}, message = {}) {
  if (!shareReplayShellActive || !payload || typeof payload !== 'object') return false;
  const rootEntry = payload.root && typeof payload.root === 'object' ? payload.root : null;
  if (!rootEntry) throw new Error('replay keyframe missing root');
  const context = {nodeMap: new Map()};
  const rootNodeId = shareReplayNormalizeNodeId(rootEntry.nodeId);
  context.nodeMap.set(rootNodeId, null);
  const children = Array.isArray(rootEntry.children) ? rootEntry.children : [];
  const fragment = document.createDocumentFragment();
  for (const child of children) fragment.appendChild(shareReplayBuildNode(child, context));
  const root = appRootElement();
  if (!root || root === document.body) throw new Error('replay mirror root missing');
  shareReplayResetMirrorRootAttributes(root, rootEntry.attrs || {});
  root.replaceChildren(fragment);
  root.dataset.shareReplayNodeId = String(rootNodeId);
  context.nodeMap.set(rootNodeId, root);
  shareReplayNodeMap = context.nodeMap;
  shareReplayApplyTerminalPlaceholders(payload.terminals || []);
  shareReplayApplyScrollEntries(payload.scroll || []);
  if (payload.viewport && typeof payload.viewport === 'object') applyShareViewportState(payload.viewport);
  bindShareReplayPaneTabPopovers(root);
  shareReplayCurrentEpoch = Math.max(0, Math.round(Number(message.epoch) || Number(payload.epoch) || shareReplayCurrentEpoch || 1));
  shareReplayLastSequence = Math.max(0, Math.round(Number(message.sequence) || Number(payload.sequence) || 0));
  shareReplayLastKeyframe = {payload, message};
  setShareReplayShellStatus('mirrored', {
    digest: payload.digest,
    sequence: message.sequence ?? payload.sequence,
  });
  return true;
}

function shareReplayCurrentDomDigest() {
  const root = appRootElement();
  if (!root) return '';
  return shareHashText(String(root.textContent || ''));
}

function shareReplayFrameNumberDetail(message = {}) {
  const payload = message?.payload && typeof message.payload === 'object' ? message.payload : {};
  const frameNumber = value => {
    const number = Number(value);
    return Number.isFinite(number) ? Math.round(number) : null;
  };
  return {
    epoch: frameNumber(message.epoch),
    sequence: frameNumber(message.sequence),
    baseSequence: frameNumber(message.baseSequence ?? payload.baseSequence),
    currentEpoch: Math.max(0, Math.round(Number(shareReplayCurrentEpoch) || 0)),
    lastSequence: Math.max(0, Math.round(Number(shareReplayLastSequence) || 0)),
  };
}

function shareReplayErrorDetail(reason = 'replay-error', detail = {}) {
  const cleanDetail = detail && typeof detail === 'object' ? detail : {};
  const currentEpoch = Math.max(0, Math.round(Number(cleanDetail.currentEpoch ?? shareReplayCurrentEpoch) || 0));
  const lastSequence = Math.max(0, Math.round(Number(cleanDetail.lastSequence ?? shareReplayLastSequence) || 0));
  const epoch = Number(cleanDetail.epoch);
  const sequence = Number(cleanDetail.sequence);
  const baseSequence = Number(cleanDetail.baseSequence);
  const expectedBaseSequence = Number(cleanDetail.expectedBaseSequence ?? lastSequence);
  const expectedSequence = Number(cleanDetail.expectedSequence ?? (lastSequence + 1));
  const error = cleanDetail.error instanceof Error ? cleanDetail.error.message : cleanDetail.error;
  const entry = {
    frameType: String(cleanDetail.frameType || cleanDetail.type || shareMirrorProtocol.frames.domDelta),
    reason: String(reason || cleanDetail.reason || 'replay-error'),
    error: String(error || cleanDetail.message || '').slice(0, 500),
    currentEpoch,
    lastSequence,
  };
  for (const [key, value] of Object.entries({
    epoch,
    sequence,
    baseSequence,
    expectedSequence,
    expectedBaseSequence,
    frameBytes: Number(cleanDetail.frameBytes),
  })) {
    if (Number.isFinite(value)) entry[key] = Math.round(value);
  }
  if (cleanDetail.digest) entry.digest = String(cleanDetail.digest || '');
  return shareRedactDiagnosticValue(entry);
}

function shareReplayRecordLastReplayError(reason = 'replay-error', detail = {}) {
  const cleanDetail = detail && typeof detail === 'object' ? detail : {};
  const shouldRecord = reason !== 'join' && reason !== 'manual-debug'
    && (cleanDetail.error || cleanDetail.reason || cleanDetail.frameType || cleanDetail.sequence || cleanDetail.baseSequence || cleanDetail.digest);
  if (!shouldRecord) return null;
  shareReplayLastReplayError = shareReplayErrorDetail(reason, cleanDetail);
  return shareReplayLastReplayError;
}

function shareReplayDeltaSequenceStatus(message = {}) {
  const detail = shareReplayFrameNumberDetail(message);
  const epoch = detail.epoch;
  const sequence = detail.sequence;
  const baseSequence = detail.baseSequence;
  const currentEpoch = detail.currentEpoch;
  const lastSequence = detail.lastSequence;
  if (!Number.isFinite(epoch) || !Number.isFinite(sequence) || !Number.isFinite(baseSequence)) {
    return {ok: false, reason: 'gap', error: 'missing replay sequence', ...detail};
  }
  if (!currentEpoch || epoch > currentEpoch) {
    return {ok: false, reason: 'gap', error: 'delta epoch has no keyframe base', ...detail};
  }
  if (epoch < currentEpoch || sequence <= lastSequence) {
    return {ok: false, reason: 'stale', stale: true, error: 'stale replay delta', ...detail};
  }
  if (baseSequence !== lastSequence || sequence !== lastSequence + 1) {
    return {ok: false, reason: 'gap', error: 'non-contiguous replay delta', ...detail};
  }
  return {ok: true, epoch, sequence, baseSequence};
}

function shareReplayDeltaCanApplyBestEffort(sequenceStatus = {}) {
  if (!sequenceStatus || sequenceStatus.ok || sequenceStatus.stale === true) return false;
  if (sequenceStatus.reason !== 'gap') return false;
  const epoch = Number(sequenceStatus.epoch);
  const sequence = Number(sequenceStatus.sequence);
  const currentEpoch = Number(sequenceStatus.currentEpoch);
  const lastSequence = Number(sequenceStatus.lastSequence);
  return Number.isFinite(epoch)
    && Number.isFinite(sequence)
    && Number.isFinite(currentEpoch)
    && Number.isFinite(lastSequence)
    && currentEpoch > 0
    && epoch === currentEpoch
    && sequence > lastSequence;
}

function shareReplayNodeForDelta(nodeId) {
  const id = Number(nodeId);
  if (!Number.isSafeInteger(id) || id <= 0) throw new Error('invalid replay delta node id');
  const node = shareReplayNodeMap.get(id);
  if (!node) throw new Error(`unknown replay node id: ${id}`);
  return node;
}

function shareReplayApplyDeltaMutation(entry = {}) {
  if (!entry || typeof entry !== 'object') return;
  const kind = String(entry.kind || '');
  const target = shareReplayNodeForDelta(entry.target);
  if (kind === 'characterData') {
    target.textContent = shareReplayRedactText(entry.text || '');
    return;
  }
  if (kind === 'attributes') {
    const name = String(entry.name || '');
    if (!shareReplayAttributeNameIsSafe(name)) return;
    if (entry.removed === true || entry.value === null || entry.value === undefined) {
      target.removeAttribute?.(name);
      return;
    }
    const sanitized = shareReplaySanitizeAttribute(name, entry.value);
    if (!sanitized || !shareReplayAttributeNameIsSafe(sanitized[0])) return;
    target.setAttribute?.(sanitized[0], sanitized[1]);
    return;
  }
  if (kind === 'childList') {
    for (const rawId of Array.isArray(entry.removed) ? entry.removed : []) {
      const removed = shareReplayNodeForDelta(rawId);
      removed.remove?.();
      shareReplayNodeMap.delete(Number(rawId));
    }
    for (const child of Array.isArray(entry.added) ? entry.added : []) {
      const node = shareReplayBuildNode(child, {nodeMap: shareReplayNodeMap});
      target.appendChild?.(node);
    }
  }
}

function shareReplayApplyDeltaTerminalPlaceholders(payload = {}) {
  const entries = Array.isArray(payload?.terminals)
    ? payload.terminals
    : Array.isArray(payload?.terminalPlaceholders)
      ? payload.terminalPlaceholders
      : [];
  if (!entries.length) return 0;
  shareReplayApplyTerminalPlaceholders(entries);
  return entries.length;
}

function shareReplayApplyScrollEntry(entry = {}) {
  if (!entry || typeof entry !== 'object') return;
  const element = shareReplayNodeForDelta(entry.nodeId ?? entry.target);
  if (!element || !('scrollTop' in element || 'scrollLeft' in element)) return;
  const top = Math.max(0, Math.round(Number(entry.top || 0)));
  const left = Math.max(0, Math.round(Number(entry.left || 0)));
  const previous = applyingShareRemoteScroll;
  applyingShareRemoteScroll = true;
  try {
    element.scrollTop = top;
    element.scrollLeft = left;
  } finally {
    requestAnimationFrame(() => { applyingShareRemoteScroll = previous; });
  }
}

function shareReplayApplyScrollEntries(scroll = []) {
  for (const entry of Array.isArray(scroll) ? scroll : []) shareReplayApplyScrollEntry(entry);
}

function shareReplayPointerPayload(payload = {}, sender = '') {
  if (!payload || typeof payload !== 'object') return null;
  const x = Number(payload.x);
  const y = Number(payload.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    scope: payload.scope || 'viewport',
    x,
    y,
    visible: payload.visible !== false,
    click: payload.click === true,
    sender: String(payload.sender || sender || 'host'),
  };
}

function shareReplayApplyPointer(payload = {}, message = {}) {
  const pointer = shareReplayPointerPayload(payload, message.sender || 'host');
  if (!pointer || pointer.visible === false) return;
  renderSharePointerGhost(pointer);
}

function shareReplayErrorIsUnknownNode(error) {
  return /unknown replay node id/i.test(String(error?.message || error || ''));
}

function shareReplaySkipUnknownNodeDelta(payload = {}, sequenceStatus = {}) {
  if (!payload || payload.digest) return false;
  const sequence = Number(sequenceStatus.sequence);
  const epoch = Number(sequenceStatus.epoch);
  if (!Number.isFinite(epoch) || !Number.isFinite(sequence)) return false;
  shareReplayStaleFrames += 1;
  shareReplayCurrentEpoch = epoch;
  shareReplayLastSequence = sequence;
  setShareReplayShellStatus('mirrored', {sequence});
  return true;
}

function applyShareReplayDelta(payload = {}, message = {}) {
  if (!shareReplayShellActive || !payload || typeof payload !== 'object') return false;
  shareReplayRecordFrameMetrics(shareMirrorProtocol.frames.domDelta, payload, message);
  const sequenceStatus = shareReplayDeltaSequenceStatus(message);
  if (!sequenceStatus.ok) {
    if (sequenceStatus.stale === true) {
      shareReplayStaleFrames += 1;
      return true;
    }
    shareReplayDroppedFrames += 1;
    shareReplayRequestKeyframe(sequenceStatus.reason, {
      ...sequenceStatus,
      frameType: message.type || shareMirrorProtocol.frames.domDelta,
      digest: payload.digest,
      frameBytes: shareReplayFrameByteLength({type: message.type || shareMirrorProtocol.frames.domDelta, payload, epoch: message.epoch, sequence: message.sequence}),
    });
    if (!shareReplayDeltaCanApplyBestEffort(sequenceStatus)) {
      setShareReplayShellStatus('viewer-behind', {sequence: message.sequence, digest: payload.digest});
      return true;
    }
  }
  try {
    for (const mutation of Array.isArray(payload.mutations) ? payload.mutations : []) {
      shareReplayApplyDeltaMutation(mutation);
    }
    shareReplayApplyDeltaTerminalPlaceholders(payload);
    shareReplayApplyScrollEntries(payload.scroll || []);
    if (payload.pointer && typeof payload.pointer === 'object') shareReplayApplyPointer(payload.pointer, message);
    const expectedDigest = String(payload.digest || '');
    const actualDigest = shareReplayCurrentDomDigest();
    if (expectedDigest && expectedDigest !== actualDigest) {
      throw new Error('replay DOM digest mismatch');
    }
    shareReplayCurrentEpoch = sequenceStatus.epoch;
    shareReplayLastSequence = sequenceStatus.sequence;
    setShareReplayShellStatus('mirrored', {
      sequence: sequenceStatus.sequence,
      digest: expectedDigest || actualDigest,
    });
    bindShareReplayPaneTabPopovers();
  } catch (error) {
    if (shareReplayErrorIsUnknownNode(error) && shareReplaySkipUnknownNodeDelta(payload, sequenceStatus)) {
      return true;
    }
    shareReplayDroppedFrames += 1;
    setShareReplayShellStatus('error', {sequence: message.sequence, digest: payload.digest});
    shareReplayRequestKeyframe('replay-error', {
      ...shareReplayFrameNumberDetail(message),
      frameType: message.type || shareMirrorProtocol.frames.domDelta,
      error,
      digest: payload.digest,
      frameBytes: shareReplayFrameByteLength({type: message.type || shareMirrorProtocol.frames.domDelta, payload, epoch: message.epoch, sequence: message.sequence}),
    });
  }
  return true;
}

function shareReplayMutationElement(node) {
  const value = Number(node?.nodeType);
  if (value === 1) return node;
  if (value === 3) return node?.parentElement || node?.parentNode || null;
  return node?.localName || node?.tagName ? node : null;
}

function shareReplayMutationNodeIsIgnored(node) {
  const element = shareReplayMutationElement(node);
  if (!element) return true;
  const selectors = [
    '.terminal',
    '.xterm',
    '.xterm-screen',
    '.xterm-viewport',
    '.xterm-cursor',
    '.xterm-helper-textarea',
    '[data-share-private]',
    '[data-share-redact]',
    '[data-share-volatile]',
    '[data-volatile]',
    '.file-explorer-panel',
    '.share-replay-volatile',
    '#latencyMeter',
    '#latencyNumber',
    '#latencyLine',
    '.latency-meter',
    '.share-pointer-ghost',
    '.share-ghost-cursor',
    '.share-click-ripple',
    '.share-viewer-banner',
  ];
  return selectors.some(selector => element.closest?.(selector));
}

function shareReplayHostMutationTargetIsMirrored(node) {
  const element = shareReplayMutationElement(node);
  if (!element) return false;
  const root = appRootElement();
  const disconnected = element.isConnected === false;
  const outsideRoot = typeof root?.contains === 'function' && !root.contains(element);
  if (!root || (element !== root && (disconnected || outsideRoot))) return false;
  if (Number(node?.nodeType) === 3) return shareReplayHostMirroredNodes.has(node);
  return shareReplayHostMirroredNodes.has(node) || shareReplayHostMirroredNodes.has(element);
}

function shareReplaySanitizeMutationAttribute(name = '', value = '') {
  const cleanName = String(name || '').trim();
  const lowerName = cleanName.toLowerCase();
  if (!cleanName || lowerName.startsWith('on') || lowerName === 'srcdoc' || shareReplayAttributeIsTokenBearing(lowerName)) return null;
  if (shareReplayUrlAttributeIsUnsafe(lowerName, value)) return [cleanName, null, true];
  const sanitized = shareReplaySanitizeAttribute(cleanName, value);
  if (!sanitized || !shareReplayAttributeNameIsSafe(sanitized[0])) return null;
  return [sanitized[0], sanitized[1], false];
}

function shareReplayMutationEntries(records = []) {
  const entries = [];
  const terminals = [];
  const mirroredAdditions = [];
  const mirroredRemovals = [];
  let batchNeedsKeyframe = false;
  for (const record of Array.from(records || [])) {
    if (batchNeedsKeyframe) continue;
    const type = String(record?.type || '');
    const target = record?.target || null;
    if (!target || shareReplayMutationNodeIsIgnored(target)) continue;
    if (!shareReplayHostMutationTargetIsMirrored(target)) continue;
    const targetId = shareReplayHostNodeId(target);
    if (!targetId) continue;
    if (type === 'characterData') {
      entries.push({
        kind: 'characterData',
        target: targetId,
        text: shareReplayRedactText(target.textContent || ''),
      });
      continue;
    }
    if (type === 'attributes') {
      const name = String(record.attributeName || '');
      const rawValue = target.getAttribute?.(name);
      const sanitized = shareReplaySanitizeMutationAttribute(name, rawValue ?? '');
      if (!sanitized) continue;
      entries.push({
        kind: 'attributes',
        target: targetId,
        name: sanitized[0],
        value: sanitized[2] ? null : sanitized[1],
        removed: sanitized[2] || rawValue === null || rawValue === undefined,
      });
      continue;
    }
    if (type === 'childList') {
      const added = [];
      let needsKeyframe = false;
      for (const node of Array.from(record.addedNodes || [])) {
        if (shareReplayMutationNodeIsIgnored(node)) {
          needsKeyframe = true;
          continue;
        }
        const context = {nextNodeId: 1, terminals: [], removedCount: 0, mirroredNodes: [], useStableNodeIds: true};
        const serialized = shareReplaySerializeNode(node, context);
        if (serialized) added.push(serialized);
        if (context.mirroredNodes.length) mirroredAdditions.push(...context.mirroredNodes);
        if (context.terminals.length) terminals.push(...context.terminals);
      }
      const removed = [];
      for (const node of Array.from(record.removedNodes || [])) {
        if (shareReplayMutationNodeIsIgnored(node)) {
          needsKeyframe = true;
          continue;
        }
        if (!shareReplayHostMirroredNodes.has(node)) continue;
        const nodeId = shareReplayHostNodeId(node);
        if (nodeId) {
          removed.push(nodeId);
          mirroredRemovals.push(node);
        }
      }
      if (needsKeyframe) {
        batchNeedsKeyframe = true;
        entries.splice(0, entries.length);
        mirroredAdditions.splice(0, mirroredAdditions.length);
        mirroredRemovals.splice(0, mirroredRemovals.length);
        scheduleShareTopologyDomKeyframe();
        continue;
      }
      if (added.length || removed.length) {
        entries.push({kind: 'childList', target: targetId, added, removed});
      }
    }
  }
  if (entries.length || terminals.length) {
    shareReplayHostMergeMirroredNodes(mirroredAdditions);
    for (const node of mirroredRemovals) shareReplayHostForgetMirroredSubtree(node);
  }
  Object.defineProperty(entries, 'terminals', {
    value: terminals.sort((a, b) => String(a.session || '').localeCompare(String(b.session || ''))),
    configurable: true,
  });
  return entries;
}

function shareReplayEnqueueMutationRecords(records = [], options = {}) {
  if (shareViewMode || !shareReplayFeatureEnabled() || shareReplayMutationPublisherPaused) return [];
  if (options.requireViewers === true && !shareHostHasConnectedViewers()) {
    shareReplayRecordHostPerfSkip('mutationRecords');
    shareReplayDrainMutationPublisher();
    return [];
  }
  const sourceRecords = Array.from(records || []);
  const startedAt = performanceNow();
  const entries = shareReplayMutationEntries(sourceRecords);
  const terminals = Array.isArray(entries.terminals) ? entries.terminals : [];
  shareReplayRecordHostPerf('mutationRecords', startedAt, {
    records: sourceRecords.length,
    entries: entries.length,
    terminals: terminals.length,
  });
  if (!entries.length && !terminals.length) return [];
  if (entries.length) shareReplayPendingMutations.push(...entries);
  if (terminals.length) shareReplayPendingTerminalPlaceholders.push(...terminals);
  if (!shareReplayDeltaFramePending) {
    shareReplayDeltaFramePending = true;
    requestAnimationFrame(shareReplayFlushMutationDeltas);
  }
  return entries;
}

function shareReplayDrainMutationPublisher() {
  if (shareReplayMutationObserver) {
    shareReplayMutationObserver.takeRecords?.();
    shareReplayMutationObserver.disconnect?.();
    shareReplayMutationObserver = null;
    shareReplayHostPerformance.mutationObserver.disconnected += 1;
    shareReplayHostPerformance.mutationObserver.active = false;
    shareReplayHostPerformance.mutationObserver.lastAt = Date.now();
  }
  shareReplayPendingMutations.splice(0, shareReplayPendingMutations.length);
  shareReplayPendingTerminalPlaceholders.splice(0, shareReplayPendingTerminalPlaceholders.length);
  shareReplayDeltaFramePending = false;
}

function shareReplayResumeMutationPublisher() {
  shareReplayMutationPublisherPaused = false;
  installShareReplayMutationPublisher();
}

function shareReplayResumeMutationPublisherAfterFrames(delayMs = 0) {
  if (shareReplayTopologyMutationPauseTimer) {
    clearTimeout(shareReplayTopologyMutationPauseTimer);
    shareReplayTopologyMutationPauseTimer = null;
  }
  const resume = () => shareReplayResumeMutationPublisher();
  const delay = Math.max(0, Math.round(Number(delayMs) || 0));
  const resumeAfterDelay = () => {
    if (delay > 0) setTimeout(resume, delay);
    else resume();
  };
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => requestAnimationFrame(resumeAfterDelay));
  } else {
    setTimeout(resumeAfterDelay, 32);
  }
}

function shareReplayPauseMutationPublisherForTopology() {
  shareReplayMutationPublisherPaused = true;
  shareReplayDrainMutationPublisher();
  if (shareReplayTopologyMutationPauseTimer) clearTimeout(shareReplayTopologyMutationPauseTimer);
  shareReplayTopologyMutationPauseTimer = setTimeout(() => {
    shareReplayTopologyMutationPauseTimer = null;
    if (!shareReplayHostKeyframeTimer && !shareReplayTopologyKeyframeTimer) shareReplayResumeMutationPublisher();
  }, shareReplayHostKeyframeMinIntervalMs + 1000);
}

function shareReplayPublishDeltaPayload(payload = {}, reason = 'mutation', options = {}) {
  if (shareViewMode || !shareReplayFeatureEnabled() || !payload || typeof payload !== 'object') return null;
  const cleanPayload = {
    ...payload,
    capturedAt: Date.now(),
  };
  const result = sharePublish(shareMirrorProtocol.frames.domDelta, cleanPayload, {reason, maxBytes: options.maxBytes});
  shareReplayLastDeltaBatch = result?.dropped
    ? {...cleanPayload, skipped: true, bytes: result.bytes}
    : cleanPayload;
  return shareReplayLastDeltaBatch;
}

function shareReplayFlushMutationDeltas() {
  shareReplayDeltaFramePending = false;
  const mutations = shareReplayPendingMutations.splice(0, shareReplayPendingMutations.length);
  const terminals = shareReplayPendingTerminalPlaceholders.splice(0, shareReplayPendingTerminalPlaceholders.length);
  if (!mutations.length && !terminals.length) return null;
  const startedAt = performanceNow();
  const payload = {
    mutations,
    count: mutations.length,
  };
  if (terminals.length) payload.terminals = terminals;
  const published = shareReplayPublishDeltaPayload(payload, 'mutation', {maxBytes: shareReplayHostDeltaMaxBytes});
  shareReplayRecordHostPerf('mutationFlush', startedAt, {
    mutations: mutations.length,
    terminals: terminals.length,
    bytes: Math.max(0, Math.round(Number(published?.bytes) || 0)),
    skipped: published?.skipped === true,
  });
  if (published?.skipped) {
    sharePublishDomKeyframe('backpressure');
    return null;
  }
  return published;
}

function installShareReplayMutationPublisher() {
  if (shareViewMode || !shareReplayFeatureEnabled() || shareReplayMutationPublisherPaused || typeof MutationObserver === 'undefined') return;
  if (!shareHostHasConnectedViewers()) {
    shareReplayHostPerformance.mutationObserver.skippedNoViewers += 1;
    shareReplayHostPerformance.mutationObserver.lastSkipAt = Date.now();
    shareReplayDrainMutationPublisher();
    return;
  }
  if (shareReplayMutationObserver) return;
  const root = appRootElement();
  if (!root || root === document.body) return;
  shareReplayHostNodeId(root);
  shareReplayMutationObserver = new MutationObserver(records => shareReplayEnqueueMutationRecords(records, {requireViewers: true}));
  shareReplayMutationObserver.observe(root, {
    attributes: true,
    characterData: true,
    childList: true,
    subtree: true,
  });
  shareReplayHostPerformance.mutationObserver.installed += 1;
  shareReplayHostPerformance.mutationObserver.active = true;
  shareReplayHostPerformance.mutationObserver.lastAt = Date.now();
}

function shareReplayResetMutationPublisherForKeyframe(reason = 'manual-debug') {
  shareReplayMutationPublisherPaused = true;
  shareReplayDrainMutationPublisher();
  const cleanReason = shareReplayKeyframeReason(reason);
  const quietMs = cleanReason === 'join' || cleanReason === 'topology' ? shareReplayPostTopologyKeyframeQuietMs : 0;
  shareReplayResumeMutationPublisherAfterFrames(quietMs);
}

function shareReplayElementAttributes(element) {
  const attrs = {};
  const add = (name, value) => {
    const attr = shareReplaySanitizeAttribute(name, value);
    if (!attr) return;
    attrs[attr[0]] = attr[1];
  };
  if (element?.id) add('id', element.id);
  if (element?.className) add('class', element.className);
  const rawAttrs = element?.attributes;
  if (rawAttrs && typeof rawAttrs.length === 'number') {
    for (const attr of Array.from(rawAttrs)) add(attr?.name, attr?.value);
  } else if (rawAttrs && typeof rawAttrs === 'object') {
    for (const [name, value] of Object.entries(rawAttrs)) add(name, value);
  }
  for (const [key, value] of Object.entries(element?.dataset || {})) add(shareReplayDatasetAttributeName(key), value);
  if (element?.classList?.contains('pane-tab-detached-popover') && element?.classList?.contains('popover-open')) {
    const rect = appSpaceRect(element);
    if (rect && Number.isFinite(rect.left) && Number.isFinite(rect.top) && Number.isFinite(rect.width) && Number.isFinite(rect.height)) {
      const fixedGeometry = [
        `left:${rect.left.toFixed(3)}px`,
        `top:${rect.top.toFixed(3)}px`,
        'right:auto',
        'bottom:auto',
        `width:${rect.width.toFixed(3)}px`,
        `height:${rect.height.toFixed(3)}px`,
      ];
      const style = String(attrs.style || '')
        .split(';')
        .map(part => part.trim())
        .filter(part => part && !/^(?:inset|left|top|right|bottom|width|height)\s*:/i.test(part));
      attrs.style = [...style, ...fixedGeometry].join(';');
    }
  }
  return Object.fromEntries(Object.keys(attrs).sort().map(key => [key, attrs[key]]));
}

function shareReplaySerializeNode(node, context) {
  if (!node) return null;
  const nodeType = Number(node.nodeType || (node.localName || node.tagName ? 1 : 0));
  if (nodeType === 3) {
    const text = shareReplayRedactText(node.textContent || '');
    if (!text) return null;
    shareReplayRememberSerializedNode(context, node);
    return {nodeId: shareReplaySerializedNodeId(node, context), text};
  }
  if (nodeType !== 1) return null;
  const redactionAction = shareReplayElementRedactionAction(node);
  if (redactionAction === 'drop') {
    context.removedCount += 1;
    return null;
  }
  const terminalContainer = shareReplayElementIsTerminalContainer(node);
  if (terminalContainer && (!shareReplayTerminalSessionForElement(node) || !shareReplayTerminalElementIsVisible(node))) {
    context.removedCount += 1;
    return null;
  }
  const nodeId = shareReplaySerializedNodeId(node, context);
  if (redactionAction === 'placeholder') {
    context.removedCount += 1;
    shareReplayRememberSerializedNode(context, node);
    return {
      nodeId,
      tag: String(node.localName || node.tagName || 'input').toLowerCase(),
      attrs: {'data-share-redacted': 'secret'},
      children: [],
    };
  }
  const terminalPlaceholder = terminalContainer ? shareReplayTerminalPlaceholderForElement(node, nodeId) : null;
  if (terminalPlaceholder) {
    context.terminals.push(terminalPlaceholder.terminal);
    shareReplayRememberSerializedNode(context, node);
    return terminalPlaceholder.node;
  }
  if (terminalContainer) {
    context.removedCount += 1;
    return null;
  }
  const children = [];
  const childNodes = Array.from(node.childNodes || node.children || []);
  for (const child of childNodes) {
    const serialized = shareReplaySerializeNode(child, context);
    if (serialized) children.push(serialized);
  }
  const tag = shareReplaySafeSerializedTag(node.localName || node.tagName || 'div');
  const entry = {nodeId, tag, attrs: shareReplayElementAttributes(node), children};
  const text = shareReplayRedactText(node.textContent || '');
  if (!children.length && text) entry.text = text;
  shareReplayRememberSerializedNode(context, node);
  return entry;
}

function shareReplayScrollContainerSelector() {
  return typeof paneScrollContainerSelector === 'string' && paneScrollContainerSelector
    ? paneScrollContainerSelector
    : '.preferences-scroll, .file-explorer-tree-panel, .file-explorer-changes-panel, .file-editor-preview-pane-panel, .file-editor-codemirror .cm-scroller, .file-editor-codemirror-panel .cm-scroller, #info-content';
}

function shareReplayScrollableElementAllowed(element) {
  if (!element || shareReplayMutationNodeIsIgnored(element)) return false;
  if (element.closest?.('.terminal, .xterm, .xterm-viewport')) return false;
  const scrollTop = Math.max(0, Math.round(Number(element.scrollTop || 0)));
  const scrollLeft = Math.max(0, Math.round(Number(element.scrollLeft || 0)));
  const scrollHeight = Math.round(Number(element.scrollHeight || 0));
  const scrollWidth = Math.round(Number(element.scrollWidth || 0));
  const clientHeight = Math.round(Number(element.clientHeight || 0));
  const clientWidth = Math.round(Number(element.clientWidth || 0));
  return Boolean(
    scrollTop
    || scrollLeft
    || scrollHeight > clientHeight + 1
    || scrollWidth > clientWidth + 1
    || element.dataset?.shareReplayScroll === 'true'
  );
}

function shareReplayScrollableElements(root = appRootElement()) {
  const queryRoot = root?.querySelectorAll ? root : document;
  const selector = shareReplayScrollContainerSelector();
  const nodes = Array.from(queryRoot.querySelectorAll?.(selector) || []);
  if (root?.matches?.(selector)) nodes.unshift(root);
  const seen = new Set();
  return nodes.filter(node => {
    if (seen.has(node) || !shareReplayScrollableElementAllowed(node)) return false;
    seen.add(node);
    return true;
  });
}

function shareReplayScrollEntryForElement(element) {
  if (!shareReplayScrollableElementAllowed(element)) return null;
  const nodeId = shareReplayHostNodeId(element);
  if (!nodeId) return null;
  const semantic = typeof shareScrollPayloadForElement === 'function' ? shareScrollPayloadForElement(element) : null;
  return {
    nodeId,
    target: semantic?.target || '',
    kind: semantic?.kind || '',
    top: Math.max(0, Math.round(Number(element.scrollTop || 0))),
    left: Math.max(0, Math.round(Number(element.scrollLeft || 0))),
  };
}

function shareReplayScrollSnapshot(root = appRootElement()) {
  return shareReplayScrollableElements(root).map(shareReplayScrollEntryForElement).filter(Boolean);
}

function shareReplayAssetFingerprint() {
  return {
    js: String(bootstrap.versionCommit || bootstrap.version || ''),
    css: String(bootstrap.version || ''),
    fonts: shareFontFingerprint(),
  };
}

function shareCreateDomKeyframePayload(reason = 'manual-debug') {
  if (!shareReplayFeatureEnabled()) return null;
  const context = {nextNodeId: 1, terminals: [], removedCount: 0, mirroredNodes: [], useStableNodeIds: true};
  const root = shareReplaySerializeNode(appRootElement(), context);
  if (!root) return null;
  shareReplayHostMirroredNodes = shareReplayHostMirroredNodeSet(context.mirroredNodes);
  const payload = {
    shareId: String(shareBootstrap?.id || activeShares[0]?.id || activeShares[0]?.token || ''),
    reason: shareMirrorFrameReason(shareMirrorProtocol.frames.domKeyframe, {reason}),
    createdAt: Date.now() / 1000,
    viewport: shareViewportSnapshot(),
    assets: shareReplayAssetFingerprint(),
    root,
    terminals: context.terminals.sort((a, b) => a.session.localeCompare(b.session)),
    scroll: shareReplayScrollSnapshot(),
    redaction: {
      policyVersion: shareMirrorProtocol.redaction.policyVersion,
      removedCount: context.removedCount,
    },
  };
  payload.digest = shareHashText(stableDigestJson({
    root: payload.root,
    terminals: payload.terminals,
    redaction: payload.redaction,
  }));
  return payload;
}

function shareCreateDomKeyframeMessage(reason = 'manual-debug') {
  const payload = shareCreateDomKeyframePayload(reason);
  return payload ? shareBuildUiMessage(shareMirrorProtocol.frames.domKeyframe, payload, {reason}) : null;
}

function shareReplayKeyframeReason(reason = 'manual-debug', fallback = 'manual-debug') {
  const clean = String(reason || '').trim();
  if (shareMirrorProtocol.keyframeReasons.includes(clean)) return clean;
  return fallback;
}

function shareReplayCoalesceKeyframeReason(currentReason = '', nextReason = '') {
  const current = shareReplayKeyframeReason(currentReason, '');
  const next = shareReplayKeyframeReason(nextReason, '');
  if (!current) return next || 'manual-debug';
  if (!next) return current;
  const priority = ['manual-debug', 'join', 'topology', 'backpressure', 'replay-error', 'digest', 'gap'];
  return priority.indexOf(next) >= 0 && priority.indexOf(next) < priority.indexOf(current) ? next : current;
}

function sharePublishDomKeyframeNow(reason = 'manual-debug') {
  if (shareReplayHostKeyframeTimer) {
    clearTimeout(shareReplayHostKeyframeTimer);
    shareReplayHostKeyframeTimer = null;
  }
  shareReplayHostKeyframePendingReason = '';
  if (shareViewMode || !shareHasActiveShare()) return false;
  const cleanReason = shareReplayKeyframeReason(reason);
  const now = Date.now();
  const cancelsScheduledTopology = cleanReason !== 'topology' && Boolean(shareReplayTopologyKeyframeTimer || shareReplayTopologyKeyframeQueuedAt);
  const followsRecentTopology = cleanReason !== 'topology'
    && shareReplayHostLastKeyframeReason === 'topology'
    && now - Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0)) < shareReplayPostTopologyKeyframeQuietMs;
  if (cleanReason !== 'topology') clearScheduledShareTopologyDomKeyframe();
  shareReplayResetMutationPublisherForKeyframe(cancelsScheduledTopology || followsRecentTopology ? 'topology' : cleanReason);
  const payload = shareCreateDomKeyframePayload(cleanReason);
  if (!payload) return false;
  sharePublish(shareMirrorProtocol.frames.domKeyframe, payload, {reason: cleanReason});
  shareReplayHostLastKeyframeAt = now;
  shareReplayHostLastKeyframeReason = cleanReason;
  return true;
}

function sharePublishDomKeyframe(reason = 'manual-debug') {
  const cleanReason = shareReplayKeyframeReason(reason);
  const now = Date.now();
  const lastAt = Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0));
  if (cleanReason === 'manual-debug' || cleanReason === 'topology' || cleanReason === 'join' || lastAt <= 0 || now - lastAt >= shareReplayHostKeyframeMinIntervalMs) {
    return sharePublishDomKeyframeNow(cleanReason);
  }
  shareReplayHostKeyframeSuppressedCount = Math.max(0, Math.round(Number(shareReplayHostKeyframeSuppressedCount) || 0)) + 1;
  shareReplayHostKeyframePendingReason = shareReplayCoalesceKeyframeReason(shareReplayHostKeyframePendingReason, cleanReason);
  if (!shareReplayHostKeyframeTimer) {
    const delayMs = Math.max(0, shareReplayHostKeyframeMinIntervalMs - (now - lastAt));
    shareReplayHostKeyframeTimer = setTimeout(() => {
      const pendingReason = shareReplayHostKeyframePendingReason || cleanReason;
      shareReplayHostKeyframeTimer = null;
      shareReplayHostKeyframePendingReason = '';
      sharePublishDomKeyframeNow(pendingReason);
    }, delayMs);
  }
  return true;
}

function shareMirrorFrameSequenceFamily(message = {}) {
  return shareMirrorFrameTypeIsDomReplayContent(message?.type) ? 'dom-replay' : 'semantic';
}

function shareMirrorSenderKey(message = {}) {
  const sender = String(message.sender || message.owner || 'host').trim() || 'host';
  const family = shareMirrorFrameSequenceFamily(message);
  return family === 'semantic' ? sender : `${sender}:${family}`;
}

function shareMirrorFrameNumbers(message = {}) {
  const epoch = Math.floor(Number(message.epoch));
  const sequence = Math.floor(Number(message.sequence));
  if (!Number.isFinite(epoch) || !Number.isFinite(sequence)) return null;
  return {epoch, sequence};
}

function shareDropStaleMirrorFrame(message = {}) {
  if (!message || !shareMirrorFrameTypeIsSequenced(message.type)) return false;
  const current = shareMirrorFrameNumbers(message);
  if (!current) return false;
  const sender = shareMirrorSenderKey(message);
  const previous = shareMirrorLastFrameBySender.get(sender);
  if (previous) {
    if (current.epoch < previous.epoch) return true;
    if (current.epoch === previous.epoch && current.sequence <= previous.sequence) return true;
  }
  shareMirrorLastFrameBySender.set(sender, current);
  return false;
}

function sharePublish(type, payload = {}, options = {}) {
  if (type === 'scroll' && !shareCanPublishScroll()) return {sent: false, queued: 0, bytes: 0};
  if (!shareCanPublishUi() || !type) return {sent: false, queued: 0, bytes: 0};
  const message = shareBuildUiMessage(type, payload, {...options, commitSequence: false});
  const serialized = JSON.stringify(message);
  const bytes = utf8ByteLength(serialized);
  const maxBytes = Math.max(0, Math.round(Number(options.maxBytes) || 0));
  if (maxBytes > 0 && bytes > maxBytes) {
    return {sent: false, queued: 0, dropped: true, bytes, message};
  }
  shareCommitBuiltUiMessage(message);
  const targets = shareViewMode ? [{token: shareToken}] : activeShares;
  let sent = 0;
  let queued = 0;
  for (const share of targets) {
    const token = share?.token || share;
    if (!token) continue;
    const socket = ensureShareHostSocket(token);
    if (!socket) continue;
    if (socket.readyState === WebSocket.OPEN) {
      socket.send(serialized);
      sent += 1;
      continue;
    }
    const shareHostQueue = shareHostQueues.get(token) || [];
    if (shareHostQueue.length < 32) {
      shareHostQueue.push(serialized);
      queued += 1;
    }
    shareHostQueues.set(token, shareHostQueue);
  }
  return {sent: sent > 0, sentCount: sent, queued, bytes, message};
}

function shareRedactSecretNode(node) {
  if (!node) return;
  if ('value' in node) node.setAttribute('value', '...');
  node.textContent = '...';
}

function shareSanitizePopupHtml(html = '') {
  const text = String(html || '');
  if (!text) return '';
  const template = document.createElement('template');
  if (!('content' in template)) return '';
  template.innerHTML = text;
  template.content.querySelectorAll('script, iframe, object, embed').forEach(node => node.remove());
  template.content.querySelectorAll('*').forEach(node => {
    for (const attr of Array.from(node.attributes || [])) {
      const sanitized = shareReplaySanitizeAttribute(attr.name, attr.value);
      if (!sanitized || sanitized[0] === 'id') {
        node.removeAttribute(attr.name);
      } else if (sanitized[1] !== String(attr.value ?? '')) {
        node.setAttribute(sanitized[0], sanitized[1]);
      }
    }
  });
  template.content.querySelectorAll('[data-share-secret]').forEach(shareRedactSecretNode);
  template.content.querySelectorAll('input[value*="/share/"], input[value*="#t="]').forEach(shareRedactSecretNode);
  const result = template.innerHTML || '';
  return result.length > 8192 ? '' : result;
}

function sharePopupLayerElements() {
  const selectors = [
    '.app-menu.open .app-menu-popover',
    '.terminal-context-menu',
    '.session-rename-backdrop',
    '.file-image-preview-popover',
    '.pane-tab-popover',
    '.pane-tab.popover-open > .session-popover',
    '.dockview-pane-tab.popover-open > .session-popover',
    '.tabber-session-tab.popover-open > .session-popover',
    '.panel-popover-zone.popover-open > .session-popover',
    '.pane-tab-detached-popover.popover-open',
    '.diff-ref-popover',
    '.diff-ref-suggestion-popover:not([hidden])',
    '#modal.open',
  ];
  const nodes = [];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (node && !nodes.includes(node)) nodes.push(node);
    }
  }
  return nodes.filter(node => {
    const rect = node.getBoundingClientRect?.();
    return rect && rect.width > 0 && rect.height > 0;
  });
}

function sharePopupLayerPayload() {
  const items = [];
  for (const node of sharePopupLayerElements()) {
    const html = shareSanitizePopupHtml(node.outerHTML || '');
    if (!html) continue;
    const rect = appSpaceRect(node);
    items.push({
      kind: node.classList?.contains('modal') ? 'modal' : 'popup',
      rect: {
        left: Math.round(rect.left),
        top: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      },
      html,
    });
  }
  sharePopupLayerSequence += 1;
  return {items, seq: sharePopupLayerSequence, owner: shareClientId};
}

function sharePublishPopupLayer() {
  if (!shareCanPublishUi()) return;
  sharePublish('popup-layer', sharePopupLayerPayload());
}

function scheduleSharePopupLayerPublish(options = {}) {
  if (!shareCanPublishUi()) return;
  if (sharePopupLayerPublishTimer) clearTimeout(sharePopupLayerPublishTimer);
  sharePopupLayerPublishTimer = null;
  if (options.immediate === true) {
    sharePublishPopupLayer();
    return;
  }
  sharePopupLayerPublishTimer = setTimeout(() => {
    sharePopupLayerPublishTimer = null;
    sharePublishPopupLayer();
  }, 60);
}

function ensureSharePopupLayer() {
  if (sharePopupLayerNode?.isConnected) return sharePopupLayerNode;
  const root = appRootElement();
  if (!root || root === document.body) return null;
  sharePopupLayerNode = document.createElement('div');
  sharePopupLayerNode.className = 'share-popup-mirror-layer';
  sharePopupLayerNode.setAttribute('aria-hidden', 'true');
  root.appendChild(sharePopupLayerNode);
  return sharePopupLayerNode;
}

function applySharePopupLayer(payload = {}, sender = '') {
  if (!shareViewMode || shareReadOnlyReplayModeEnabled()) return;
  const layer = ensureSharePopupLayer();
  if (!layer) return;
  const owner = String(payload.owner || sender || 'host');
  const seq = Number(payload.seq);
  if (Number.isFinite(seq)) {
    const previousSeq = Number(sharePopupLayerLastSeqBySender.get(owner) || 0);
    if (seq <= previousSeq) return;
    sharePopupLayerLastSeqBySender.set(owner, seq);
  }
  const items = Array.isArray(payload.items) ? payload.items : [];
  layer.replaceChildren();
  for (const item of items.slice(0, 8)) {
    const rect = item?.rect && typeof item.rect === 'object' ? item.rect : {};
    const html = shareSanitizePopupHtml(item?.html || '');
    if (!html) continue;
    const shell = document.createElement('div');
    shell.className = 'share-popup-mirror-item';
    shell.style.left = `${Math.round(Number(rect.left) || 0)}px`;
    shell.style.top = `${Math.round(Number(rect.top) || 0)}px`;
    shell.style.width = `${Math.max(0, Math.round(Number(rect.width) || 0))}px`;
    shell.style.height = `${Math.max(0, Math.round(Number(rect.height) || 0))}px`;
    shell.innerHTML = html;
    layer.appendChild(shell);
  }
}

function installSharePopupLayerPublisher() {
  if (shareViewMode || sharePopupLayerObserver || typeof MutationObserver === 'undefined' || !document.body) return;
  sharePopupLayerObserver = new MutationObserver(() => scheduleSharePopupLayerPublish());
  sharePopupLayerObserver.observe(document.body, {
    attributes: true,
    attributeFilter: ['aria-expanded', 'class', 'hidden', 'style'],
    childList: true,
    subtree: true,
  });
}

function sharePublishLayout() {
  sharePublish('layout', shareLayoutSeed());
}

function shareStringArray(value, limit = 1000) {
  if (!Array.isArray(value)) return [];
  const result = [];
  for (const item of value) {
    const text = String(item || '').trim();
    if (!text || result.includes(text)) continue;
    result.push(text);
    if (result.length >= limit) break;
  }
  return result;
}

function shareSetSnapshot(set, limit = 1000) {
  return Array.from(set || []).map(item => String(item || '')).filter(Boolean).slice(0, limit).sort();
}

function shareSetSignature(set, limit = 1000) {
  return shareSetSnapshot(set, limit).join('\n');
}

function shareAutoApproveStateSnapshot() {
  const states = {};
  for (const session of sessions.filter(isTmuxSession)) {
    const state = autoApproveStates.get(session);
    if (!state || typeof state !== 'object') continue;
    states[session] = {
      target: String(state.target || session),
      enabled: state.enabled === true,
      locked: state.locked === true,
      ...structuredMessageSnapshot(state, 'last_action'),
      last_screen_sig: String(state.last_screen_sig || ''),
      screen: state.screen && typeof state.screen === 'object'
        ? {
          key: String(state.screen.key || ''),
          text: String(state.screen.text || '').slice(0, 200),
        }
        : {},
      lock_owner: state.lock_owner && typeof state.lock_owner === 'object'
        ? {
          pid: state.lock_owner.pid || '',
          project_root: String(state.lock_owner.project_root || ''),
        }
        : {},
    };
  }
  return {sessions: states};
}

function shareScrollSnapshotElements() {
  const selectors = [
    '.preferences-scroll',
    '#info-content',
    '.file-explorer-tree-panel',
    '.file-explorer-changes-panel',
    '.file-editor-preview-pane-panel',
    '.file-editor-codemirror-panel .cm-scroller',
    '.terminal[id^="term-"] .xterm-viewport',
  ];
  const nodes = [];
  for (const selector of selectors) {
    for (const node of document.querySelectorAll(selector)) {
      if (!node || nodes.includes(node)) continue;
      const rect = node.getBoundingClientRect?.();
      if (rect && (rect.width > 0 || rect.height > 0)) nodes.push(node);
    }
  }
  return nodes;
}

function shareScrollStateSnapshot() {
  const byTarget = new Map();
  for (const element of shareScrollSnapshotElements()) {
    const payload = shareScrollPayloadForElement(element);
    if (!payload?.target) continue;
    byTarget.set(payload.target, payload);
  }
  return Array.from(byTarget.values()).slice(0, 100);
}

function shareReplaceSet(target, values, limit = 1000) {
  if (!target?.clear) return;
  target.clear();
  for (const item of shareStringArray(values, limit)) target.add(item);
}

function shareDiffRefsByRepoSnapshot() {
  const result = {};
  for (const [repo, refs] of Object.entries(diffRefsByRepo || {})) {
    const cleanRepo = String(repo || '').trim();
    if (!cleanRepo || !refs || typeof refs !== 'object') continue;
    result[cleanRepo] = {
      from: cleanDiffRef(refs.from, 'HEAD'),
      to: cleanDiffRef(refs.to, 'current'),
    };
  }
  return result;
}

function shareCleanDiffRefsByRepo(value) {
  const result = {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
  for (const [repo, refs] of Object.entries(value)) {
    const cleanRepo = String(repo || '').trim();
    if (!cleanRepo || !refs || typeof refs !== 'object') continue;
    result[cleanRepo] = {
      from: cleanDiffRef(refs.from, 'HEAD'),
      to: cleanDiffRef(refs.to, 'current'),
    };
  }
  return result;
}

function shareEditorModesSnapshot() {
  const modes = [];
  const seen = new Set();
  const addMode = (path, item = null, mode = '') => {
    const cleanPath = String(path || '').trim();
    const cleanItem = item && isFileEditorItem(item) ? item : '';
    const cleanMode = editorViewModes.has(mode) ? mode : editorViewModeFor(cleanPath, cleanItem || null);
    if (!cleanPath || !editorViewModes.has(cleanMode)) return;
    const key = `${cleanPath}\n${cleanItem || cleanPath}`;
    if (seen.has(key)) return;
    seen.add(key);
    const entry = {path: cleanPath, item: cleanItem, mode: cleanMode};
    const state = openFiles.get(cleanPath);
    const itemKey = cleanItem || fileEditorItemFor(cleanPath);
    const viewState = fileEditorViewState.get(itemKey);
    if (viewState) {
      entry.viewState = {
        top: Math.max(0, Math.round(Number(viewState.scrollTop || 0))),
        left: Math.max(0, Math.round(Number(viewState.scrollLeft || 0))),
        anchor: Math.max(0, Math.round(Number(viewState.anchor || 0))),
        head: Math.max(0, Math.round(Number(viewState.head ?? viewState.anchor ?? 0))),
        line: Math.max(0, Math.round(Number(viewState.line || 0))),
      };
    }
    if (cleanMode === 'diff') {
      const refs = diffRefParams(fileRepoForPath(cleanPath));
      entry.diffFromRef = cleanDiffRef(state?.diffPinnedFromRef || state?.diffFromRef || refs.from || 'HEAD', 'HEAD');
      entry.diffToRef = cleanDiffRef(state?.diffPinnedToRef || state?.diffToRef || refs.to || 'current', 'current');
      entry.diffExpandUnchanged = fileEditorDiffExpandUnchangedForItem(itemKey);
    }
    modes.push(entry);
  };
  for (const item of paneItems(layoutSlots)) {
    if (!isFileEditorItem(item)) continue;
    const path = fileItemPath(item);
    if (path) addMode(path, item, editorViewModeFor(path, item));
  }
  for (const [path, state] of openFiles.entries()) {
    const viewModes = state?.viewMode instanceof Map ? state.viewMode : fileEditorViewModesForPath(path);
    for (const [key, mode] of viewModes.entries()) {
      addMode(path, key === path ? '' : key, mode);
    }
  }
  return modes;
}

function shareRememberEditorViewState(payload = {}, top = 0, left = 0) {
  const path = String(payload.path || '').trim();
  const item = String(payload.item || '').trim();
  const key = item && isFileEditorItem(item) ? item : (path ? fileEditorItemFor(path) : '');
  if (!key) return;
  const previous = fileEditorViewState.get(key) || {};
  fileEditorViewState.set(key, {
    ...previous,
    scrollTop: Math.max(0, Math.round(Number(top || 0))),
    scrollLeft: Math.max(0, Math.round(Number(left || 0))),
    anchor: Math.max(0, Math.round(Number(payload.anchor ?? previous.anchor ?? 0))),
    head: Math.max(0, Math.round(Number(payload.head ?? payload.anchor ?? previous.head ?? previous.anchor ?? 0))),
    line: Math.max(0, Math.round(Number(payload.line ?? previous.line ?? 0))),
    scrollSnapshot: previous.scrollSnapshot || null,
  });
}

function shareApplyEditorModeEntry(entry = {}) {
  if (!entry || typeof entry !== 'object') return null;
  const path = String(entry.path || '').trim();
  const item = String(entry.item || '').trim();
  const mode = String(entry.mode || '').trim();
  if (!path || !editorViewModes.has(mode)) return null;
  const cleanItem = item && isFileEditorItem(item) ? item : '';
  if (cleanItem && !openFiles.has(path) && typeof registerFileEditorLayoutItem === 'function') {
    registerFileEditorLayoutItem(path, {item: cleanItem});
  }
  const key = cleanItem || path;
  fileEditorViewModesForPath(path, true).set(key, mode);
  const viewState = entry.viewState && typeof entry.viewState === 'object' ? entry.viewState : null;
  if (viewState) {
    shareRememberEditorViewState({
      path,
      item: cleanItem,
      anchor: viewState.anchor,
      head: viewState.head,
      line: viewState.line,
    }, viewState.top, viewState.left);
    const line = Math.floor(Number(viewState.line) || 0);
    if (line > 0 && typeof requestFileEditorLineTarget === 'function') requestFileEditorLineTarget(key, line);
  }
  if (mode === 'diff') {
    const state = openFiles.get(path) || (cleanItem ? ensureFileState(path) : null);
    if (state) {
      state.diffPinnedFromRef = cleanDiffRef(entry.diffFromRef || state.diffPinnedFromRef || state.diffFromRef || 'HEAD', 'HEAD');
      state.diffPinnedToRef = cleanDiffRef(entry.diffToRef || state.diffPinnedToRef || state.diffToRef || 'current', 'current');
    }
    if ('diffExpandUnchanged' in entry && cleanItem) {
      fileEditorDiffExpandOverrides.set(cleanItem, entry.diffExpandUnchanged === true);
    }
  }
  return {path, item: cleanItem, mode};
}

function shareTerminalDimensionsSnapshot() {
  return Array.from(terminals.entries()).map(([session, item]) => ({
    session: String(session || ''),
    rows: Math.max(0, Math.round(Number(item?.term?.rows) || 0)),
    cols: Math.max(0, Math.round(Number(item?.term?.cols) || 0)),
  })).filter(entry => entry.session && entry.rows > 0 && entry.cols > 0);
}

function shareFinderStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const finder = {
    ...shareFinderSeed(),
    showHidden: fileExplorerShowHidden === true,
    treeDateMode: normalizeFileExplorerTreeDateMode(fileExplorerTreeDateMode),
    treeSortMode: ['az', 'za', 'newest', 'oldest'].includes(fileExplorerTreeSortMode) ? fileExplorerTreeSortMode : 'az',
    sessionFilesSortMode: normalizeSessionFilesSortMode(sessionFilesSortMode),
    diffRefFrom: cleanDiffRef(diffRefFrom, 'HEAD'),
    diffRefTo: cleanDiffRef(diffRefTo, 'current'),
  };
  if (compact) return finder;
  return {
    ...finder,
    expanded: shareSetSnapshot(fileExplorerExpanded),
    selectedPaths: shareSetSnapshot(fileExplorerSelectedPaths),
    selectionAnchor: fileExplorerSelectionAnchor || '',
    selectionLead: fileExplorerSelectionLead || '',
    changesFolderCollapsed: shareSetSnapshot(changesFolderCollapsed),
    changesRepoCollapsed: shareSetSnapshot(changesRepoCollapsed),
    tabberCollapsed: shareSetSnapshot(fileExplorerTabberCollapsed),
    diffRefsByRepo: shareDiffRefsByRepoSnapshot(),
  };
}

function shareEditorStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const editor = {
    globalThemeMode: normalizeGlobalThemeMode(globalThemeMode),
    terminalThemeMode: normalizeTerminalThemeMode(terminalThemeMode),
    themeMode: normalizeEditorThemeMode(fileEditorThemeMode),
    previewDisplayMode: normalizeEditorPreviewDisplayMode(fileEditorPreviewDisplayMode),
    wrapEnabled: fileEditorWrapEnabled === true,
    lineNumbersEnabled: fileEditorLineNumbersEnabled === true,
    blameEnabled: fileEditorBlameEnabled === true,
    diffExpandUnchanged: diffExpandUnchanged === true,
    previewFontSize: clampEditorPreviewFontSize(editorPreviewFontSize),
  };
  if (!compact) editor.modes = shareEditorModesSnapshot();
  return editor;
}

function sharePreferencesStateSnapshot(options = {}) {
  const compact = options.compact === true;
  return {
    searchText: String(preferencesSearchText || '').slice(0, compact ? 200 : 2000),
    collapsedSections: shareSetSnapshot(collapsedPreferenceSections, compact ? 40 : 200),
    resetConfirmVisible: preferencesResetConfirmVisible === true,
  };
}

function shareBaseUiStateSnapshot(options = {}) {
  const compact = options.compact === true;
  const seed = shareLayoutSeed();
  return {
    layout: seed.layout,
    tabs: seed.tabs,
    viewport: shareViewportSnapshot(),
    appearance: shareAppearanceSnapshot(),
    terminalDims: shareTerminalDimensionsSnapshot(),
    chrome: {
      tabMetaVisible: tabMetaVisible !== false,
      infoSubTab: yoagentPanelIsActive() ? 'yoagent' : 'info',
    },
    autoApprove: shareAutoApproveStateSnapshot(),
    info: shareInfoStateSnapshot({includeRows: !compact}),
    finder: shareFinderStateSnapshot({compact}),
    editor: shareEditorStateSnapshot({compact}),
    preferences: sharePreferencesStateSnapshot({compact}),
  };
}

function shareCreateUiStateSnapshot() {
  return shareBaseUiStateSnapshot({compact: true});
}

function shareUiStateSnapshot() {
  return {
    ...shareBaseUiStateSnapshot({compact: false}),
    textWraps: shareWrappedTextDigestSnapshot(),
    scroll: shareScrollStateSnapshot(),
  };
}

function sharePublishUiState(options = {}) {
  if (!shareCanPublishUi()) return;
  sharePublish('ui-state', shareUiStateSnapshot(), options);
}

function scheduleShareUiStatePublish(options = {}) {
  if (!shareCanPublishUi()) return;
  if (shareUiStatePublishTimer) clearTimeout(shareUiStatePublishTimer);
  shareUiStatePublishTimer = setTimeout(() => {
    shareUiStatePublishTimer = null;
    sharePublishUiState(options);
  }, 80);
}

function scheduleShareTopologySnapshot(reason = 'topology') {
  const cleanReason = String(reason || 'topology').trim() || 'topology';
  scheduleShareUiStatePublish({reason: `topology:${cleanReason}`});
  scheduleShareTopologyDomKeyframe();
}

function clearScheduledShareTopologyDomKeyframe() {
  if (shareReplayTopologyKeyframeTimer) {
    clearTimeout(shareReplayTopologyKeyframeTimer);
    shareReplayTopologyKeyframeTimer = null;
  }
  shareReplayTopologyKeyframeQueuedAt = 0;
}

function sharePointerQuietDelayMs(now = (typeof performance !== 'undefined' && performance?.now ? performance.now() : 0)) {
  const lastPointerAt = Number(sharePointerLastPublishedAt);
  if (!Number.isFinite(lastPointerAt)) return 0;
  return Math.max(0, shareTopologyKeyframePointerQuietMs - (Number(now) - lastPointerAt));
}

function shareTopologyDomKeyframeDelayMs() {
  const now = Date.now();
  const queuedAt = Math.max(0, Math.round(Number(shareReplayTopologyKeyframeQueuedAt) || 0));
  const maxDelay = queuedAt > 0 ? Math.max(0, shareTopologyKeyframeMaxDeferralMs - (now - queuedAt)) : shareTopologyKeyframeMaxDeferralMs;
  const lastKeyframeAt = Math.max(0, Math.round(Number(shareReplayHostLastKeyframeAt) || 0));
  const keyframeFloorDelay = lastKeyframeAt > 0 ? Math.max(0, shareReplayHostKeyframeMinIntervalMs - (now - lastKeyframeAt)) : 0;
  return Math.min(maxDelay, Math.max(keyframeFloorDelay, sharePointerQuietDelayMs()));
}

function runScheduledShareTopologyDomKeyframe() {
  shareReplayTopologyKeyframeTimer = null;
  const delayMs = shareTopologyDomKeyframeDelayMs();
  if (delayMs > 0) {
    shareReplayTopologyKeyframeTimer = setTimeout(runScheduledShareTopologyDomKeyframe, delayMs);
    return;
  }
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const nextDelayMs = shareTopologyDomKeyframeDelayMs();
      if (nextDelayMs > 0) {
        if (!shareReplayTopologyKeyframeQueuedAt) shareReplayTopologyKeyframeQueuedAt = Date.now();
        shareReplayTopologyKeyframeTimer = setTimeout(runScheduledShareTopologyDomKeyframe, nextDelayMs);
        return;
      }
      shareReplayTopologyKeyframeQueuedAt = 0;
      sharePublishDomKeyframe('topology');
    });
  });
}

function scheduleShareTopologyDomKeyframe() {
  if (shareViewMode || !shareHasActiveShare() || !shareReplayFeatureEnabled()) return;
  shareReplayPauseMutationPublisherForTopology();
  if (!shareReplayTopologyKeyframeQueuedAt) shareReplayTopologyKeyframeQueuedAt = Date.now();
  if (shareReplayTopologyKeyframeTimer) return;
  shareReplayTopologyKeyframeTimer = setTimeout(runScheduledShareTopologyDomKeyframe, 0);
}

function scheduleShareViewportPublish() {
  if (shareViewMode || !shareCanPublishUi()) return;
  if (shareViewportPublishTimer) clearTimeout(shareViewportPublishTimer);
  shareViewportPublishTimer = setTimeout(() => {
    shareViewportPublishTimer = null;
    sharePublish('viewport', shareViewportSnapshot());
  }, 150);
}

function scheduleShareAppearancePublish(options = {}) {
  if (shareViewMode || !shareCanPublishUi()) return;
  if (shareAppearancePublishTimer) clearTimeout(shareAppearancePublishTimer);
  shareAppearancePublishTimer = setTimeout(() => {
    shareAppearancePublishTimer = null;
    sharePublish('appearance', shareAppearanceSnapshot());
  }, 80);
  if (options.topology !== false) scheduleShareTopologySnapshot(options.reason || 'appearance');
}

function applyShareViewportState(viewport = {}) {
  if (!shareViewMode || !viewport || typeof viewport !== 'object') return;
  const width = Number(viewport.width ?? viewport.w);
  const height = Number(viewport.height ?? viewport.h);
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return;
  setAppViewportOverride({width, height});
  applyShareMirrorTransform();
}

function applyShareAppearanceNumber(appearance, key, settingKey, min, max, fallback) {
  if (!(key in appearance)) return fallback;
  const value = Math.max(min, Math.min(max, Math.round(Number(appearance[key]) || fallback)));
  clientSettings = mergeSettingObjects(clientSettings, {appearance: {[settingKey]: value}});
  return value;
}

function applyShareAppearanceString(appearance, key, settingKey, allowed, fallback) {
  if (!(key in appearance)) return fallback;
  const value = String(appearance[key] || '');
  const normalized = allowed.includes(value) ? value : fallback;
  clientSettings = mergeSettingObjects(clientSettings, {appearance: {[settingKey]: normalized}});
  return normalized;
}

function applyShareAppearanceState(appearance = {}) {
  if (!shareViewMode || !appearance || typeof appearance !== 'object') return;
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    const languagePref = String(appearance.languagePref || '').trim();
    const locale = String(appearance.locale || '').trim();
    if (languagePref || locale) {
      const nextPref = languagePref || locale;
      clientSettings = mergeSettingObjects(clientSettings, {general: {language: nextPref}});
      const resolvedLocale = locale || resolveLocalePref(nextPref);
      if (resolvedLocale && resolvedLocale !== i18nActiveLocaleId()) void applyLocale(resolvedLocale);
    }
    const uiFontSize = applyShareAppearanceNumber(appearance, 'uiFontSize', 'ui_font_size', 6, 20, numberSetting('appearance.ui_font_size', 13));
    terminalFontSize = applyShareAppearanceNumber(appearance, 'terminalFontSize', 'terminal_font_size', 6, 28, terminalFontSize);
    editorFontSize = applyShareAppearanceNumber(appearance, 'editorFontSize', 'editor_font_size', 6, 28, editorFontSize);
    editorPreviewFontSize = applyShareAppearanceNumber(appearance, 'previewFontSize', 'preview_font_size', 6, 32, editorPreviewFontSize);
    fileExplorerFontSize = applyShareAppearanceNumber(appearance, 'fileExplorerFontSize', 'file_explorer_font_size', 6, 24, fileExplorerFontSize);
    applyShareAppearanceNumber(appearance, 'tabWidth', 'tab_width', 120, 420, numberSetting('appearance.tab_width', 180));
    applyShareAppearanceNumber(appearance, 'paneSpacing', 'pane_spacing', 0, 20, numberSetting('appearance.pane_spacing', 3));
    applyShareAppearanceNumber(appearance, 'paneRingOpacity', 'pane_ring_opacity', 5, 100, numberSetting('appearance.pane_ring_opacity', 75));
    applyShareAppearanceNumber(appearance, 'inactivePaneOpacity', 'inactive_pane_opacity', 0, 100, numberSetting('appearance.inactive_pane_opacity', 60));
    globalThemeMode = normalizeGlobalThemeMode(appearance.theme || globalThemeMode);
    shareResolvedGlobalThemeMode = normalizeResolvedGlobalThemeMode(appearance.resolvedTheme || '');
    terminalThemeMode = normalizeTerminalThemeMode(appearance.terminalTheme || terminalThemeMode);
    clientSettings = mergeSettingObjects(clientSettings, {appearance: {
      theme: globalThemeMode,
      terminal_theme: terminalThemeMode,
      active_color: applyShareAppearanceString(appearance, 'activeColor', 'active_color', ['green', 'blue', 'orange', 'yellow', 'purple', 'white'], initialSetting('appearance.active_color', 'green')),
      separator_color: applyShareAppearanceString(appearance, 'separatorColor', 'separator_color', ['theme', 'green', 'blue', 'orange', 'yellow', 'purple', 'white'], initialSetting('appearance.separator_color', 'theme')),
      ui_font_size: uiFontSize,
    }});
    applyCssSettings();
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true, refreshEditors: true});
    applyEditorThemeMode({refreshEditors: true});
    renderSessionButtons();
    renderPaneTabStrips();
  } finally {
    finishRemoteApply();
  }
}

function applyShareTerminalDimensionsState(value = []) {
  if (!shareViewMode || !Array.isArray(value)) return;
  for (const entry of value) {
    if (!entry || typeof entry !== 'object') continue;
    updateShareHostTerminalSize(entry.session, entry.rows, entry.cols);
  }
}

function applyShareEditorState(editor = {}) {
  if (!editor || typeof editor !== 'object') return;
  if ('globalThemeMode' in editor) globalThemeMode = normalizeGlobalThemeMode(editor.globalThemeMode);
  if ('terminalThemeMode' in editor) terminalThemeMode = normalizeTerminalThemeMode(editor.terminalThemeMode);
  if ('themeMode' in editor) fileEditorThemeMode = normalizeEditorThemeMode(editor.themeMode);
  if ('previewDisplayMode' in editor) fileEditorPreviewDisplayMode = normalizeEditorPreviewDisplayMode(editor.previewDisplayMode);
  if ('wrapEnabled' in editor) fileEditorWrapEnabled = editor.wrapEnabled === true;
  if ('lineNumbersEnabled' in editor) fileEditorLineNumbersEnabled = editor.lineNumbersEnabled === true;
  if ('blameEnabled' in editor) fileEditorBlameEnabled = editor.blameEnabled === true;
  if ('diffExpandUnchanged' in editor) diffExpandUnchanged = editor.diffExpandUnchanged === true;
  if ('previewFontSize' in editor) editorPreviewFontSize = clampEditorPreviewFontSize(editor.previewFontSize);
  const modes = Array.isArray(editor.modes) ? editor.modes : [];
  for (const entry of modes) shareApplyEditorModeEntry(entry);
  applyCssSettings();
  applyGlobalThemeMode({updateEditor: true, updateTerminals: true, refreshEditors: true});
  applyEditorThemeMode({refreshEditors: true});
  applyEditorWrapPreference();
  updateEditorPreviewFontControls();
  for (const panel of document.querySelectorAll('.file-editor-panel')) {
    const item = panel.dataset.layoutItem || fileEditorItemFor(panel.dataset.filePath || '');
    if (item) renderFileEditorPanel(panel, item, {updateActiveFile: false});
  }
  restoreShareScrollTargetsByPrefix('editor:');
  renderSessionButtons();
  renderPaneTabStrips();
}

function applyShareChromeState(chrome = {}) {
  if (!chrome || typeof chrome !== 'object') return;
  if ('tabMetaVisible' in chrome) {
    tabMetaVisible = chrome.tabMetaVisible !== false;
    renderTabMetaToggle();
  }
  if ('infoSubTab' in chrome) {
    infoPanelSubTab = normalizedInfoSubTab(chrome.infoSubTab);
    applyInfoSubTab();
    if (yoagentPanelIsActive()) {
      renderYoagentPanel({preserveDraft: true, scrollBottom: false, summaryOnly: true});
    }
  }
  renderSessionButtons();
  renderPaneTabStrips();
}

async function applyShareFinderState(finder = {}) {
  if (!finder || typeof finder !== 'object') return;
  const session = String(finder.session || '').trim();
  const previousRoot = normalizeDirectoryPath(fileExplorerRoot || '');
  const previousExpandedSignature = shareSetSignature(fileExplorerExpanded);
  const previousMode = normalizeFileExplorerMode(fileExplorerMode);
  if ('mode' in finder) fileExplorerMode = normalizeFileExplorerMode(finder.mode);
  const modeChanged = previousMode !== fileExplorerMode;
  if ('rootMode' in finder) fileExplorerRootMode = finder.rootMode === 'fixed' ? 'fixed' : 'sync';
  if ('showHidden' in finder) fileExplorerShowHidden = finder.showHidden === true;
  if (isTmuxSession(session)) {
    fileExplorerChangesSelectedSession = session;
    fileExplorerExplicitSyncSession = session;
  }
  if ('treeDateMode' in finder) fileExplorerTreeDateMode = normalizeFileExplorerTreeDateMode(finder.treeDateMode);
  if ('treeSortMode' in finder) fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(finder.treeSortMode) ? finder.treeSortMode : 'az';
  if ('sessionFilesSortMode' in finder) sessionFilesSortMode = normalizeSessionFilesSortMode(finder.sessionFilesSortMode);
  if ('diffRefFrom' in finder) diffRefFrom = cleanDiffRef(finder.diffRefFrom, diffRefFrom || 'HEAD');
  if ('diffRefTo' in finder) diffRefTo = cleanDiffRef(finder.diffRefTo, diffRefTo || 'current');
  if ('diffRefsByRepo' in finder) diffRefsByRepo = shareCleanDiffRefsByRepo(finder.diffRefsByRepo);
  if ('expanded' in finder) shareReplaceSet(fileExplorerExpanded, finder.expanded);
  if ('selectedPaths' in finder) shareReplaceSet(fileExplorerSelectedPaths, finder.selectedPaths);
  if ('selectionAnchor' in finder) fileExplorerSelectionAnchor = String(finder.selectionAnchor || '');
  if ('selectionLead' in finder) fileExplorerSelectionLead = String(finder.selectionLead || '');
  if ('changesFolderCollapsed' in finder) shareReplaceSet(changesFolderCollapsed, finder.changesFolderCollapsed);
  if ('changesRepoCollapsed' in finder) shareReplaceSet(changesRepoCollapsed, finder.changesRepoCollapsed);
  if ('tabberCollapsed' in finder) shareReplaceSet(fileExplorerTabberCollapsed, finder.tabberCollapsed);
  const expandedChanged = previousExpandedSignature !== shareSetSignature(fileExplorerExpanded);
  applyFileExplorerMode();
  // A semantic share frame can move Differ directly to Tabber while the root remains unchanged.
  // Rebuild the shared mode panel before awaiting any root work so the Tabber renderer has its own
  // shell instead of trying to hydrate the stale Differ DOM after the host state has moved on.
  if (modeChanged) renderFileExplorerChangesPanels({force: true});
  renderFileExplorerRootModeControls();
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  document.querySelectorAll('.file-explorer-hidden-toggle-panel').forEach(syncFileExplorerHiddenButton);
  syncFileExplorerTreeDateButtons();
  const root = String(finder.root || '').trim();
  const normalizedRoot = root ? normalizeDirectoryPath(expandUserPath(root)) : '';
  if (!itemInLayout(fileExplorerItemId)) {
    if (normalizedRoot) fileExplorerRoot = normalizedRoot;
    renderPaneTabStrips();
    return;
  }
  if (normalizedRoot) {
    fileExplorerRoot = normalizedRoot;
    const hasRenderedTreeRows = fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'));
    if (previousRoot !== normalizedRoot || !hasRenderedTreeRows) {
      await openFileExplorerAt(normalizedRoot, {preserveExpanded: true, preserveScroll: true});
    } else {
      setFileExplorerPathDisplay(fileExplorerRoot);
      const shouldRefreshTree = expandedChanged || 'showHidden' in finder || 'treeDateMode' in finder || 'treeSortMode' in finder;
      if (shouldRefreshTree) {
        const refreshed = await refreshFileExplorerTreesInPlace({
          root: fileExplorerRoot,
          preserveExpanded: false,
          preserveScroll: true,
        });
        if (!refreshed && !fileExplorerTreeContainers().some(container => container.querySelector?.('.file-tree-row[data-path]'))) {
          await openFileExplorerAt(normalizedRoot, {preserveExpanded: true, preserveScroll: true});
        }
      }
    }
  } else if (fileExplorerRoot) {
    await refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  }
  if (fileExplorerMode === 'tabber') {
    refreshTabberPanels();
    fetchTabberActivity();
  } else {
    renderFileExplorerChangesPanels({force: true});
    if (fileExplorerChangesSelectedSession) {
      fetchSessionFiles({destination: 'finder', session: fileExplorerChangesSelectedSession, silent: true, force: false, background: true});
    }
  }
  restoreShareScrollTargetByKey(`finder:${normalizeFileExplorerMode(fileExplorerMode)}`);
  updateFileExplorerCurrentFileHighlight();
  renderPaneTabStrips();
}

function applySharePreferencesState(preferences = {}) {
  if (!preferences || typeof preferences !== 'object') return;
  if ('searchText' in preferences) preferencesSearchText = String(preferences.searchText || '');
  if (Array.isArray(preferences.collapsedSections)) {
    setCollapsedPreferenceSections(shareStringArray(preferences.collapsedSections, 200), {sections: preferenceSections()});
  }
  if ('resetConfirmVisible' in preferences) preferencesResetConfirmVisible = preferences.resetConfirmVisible === true;
  renderPreferencesPanels({force: true});
  restoreShareScrollTargetByKey('preferences');
}

function applyShareAutoApproveState(autoApprove = {}) {
  if (!autoApprove || typeof autoApprove !== 'object') return;
  const payload = {sessions: autoApprove.sessions && typeof autoApprove.sessions === 'object' ? autoApprove.sessions : {}};
  applyAutoApprovePayload(payload);
  renderSessionButtons();
  renderPaneTabStrips();
}

async function applyShareUiState(payload = {}) {
  if (!shareSemanticMirrorApplyAllowed() || !payload || typeof payload !== 'object') return;
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    applyShareViewportState(payload.viewport || {});
    applyShareAppearanceState(payload.appearance || {});
    applyShareTerminalDimensionsState(payload.terminalDims || []);
    if (payload.layout || payload.tabs) {
      const next = layoutFromParam(payload.layout || '', payload.tabs || '', {preserveMissingFileExplorer: true});
      if (next) applyLayoutSlots(next, {prune: false, preserveMissingFileExplorer: true});
    }
    applyShareChromeState(payload.chrome || {});
    applyShareAutoApproveState(payload.autoApprove || {});
    applyShareInfoState(payload.info || {});
    applyShareEditorState(payload.editor || {});
    applySharePreferencesState(payload.preferences || {});
    await applyShareFinderState(payload.finder || {});
    applyShareTextWrapMetrics(payload.textWraps || []);
    applyShareScrollSnapshot(payload.scroll || []);
  } finally {
    finishRemoteApply();
  }
}

const sharePointerPublishIntervalMs = 50;

function sharePointerPayloadForPoint(clientX, clientY, options = {}) {
  const point = appSpacePoint(clientX, clientY);
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) return null;
  return {
    scope: 'viewport',
    x: Math.round(point.x * 10) / 10,
    y: Math.round(point.y * 10) / 10,
    ...(options.click ? {click: true} : {}),
  };
}

function sharePointerPayloadForEvent(event, options = {}) {
  return sharePointerPayloadForPoint(event?.clientX, event?.clientY, options);
}

function sharePointFromPointerPayload(payload = {}) {
  if (payload.scope && payload.scope !== 'viewport') return null;
  const x = Number(payload.x);
  const y = Number(payload.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const point = visualPointFromAppSpace(x, y);
  return {
    x: point.x,
    y: point.y,
    scope: 'viewport',
  };
}

function sharePointerSenderKey(sender = '') {
  const clean = String(sender || '').trim();
  return clean || 'host';
}

function sharePointerSenderColor(sender = '') {
  const palette = ['#e53935', '#00897b', '#3949ab', '#f4511e', '#8e24aa', '#0277bd', '#6d8f00', '#d81b60'];
  const text = sharePointerSenderKey(sender);
  let hash = 0;
  for (let index = 0; index < text.length; index += 1) hash = ((hash * 31) + text.charCodeAt(index)) >>> 0;
  return palette[hash % palette.length];
}

function ensureSharePointerGhost(sender = '') {
  const key = sharePointerSenderKey(sender);
  const existing = sharePointerGhosts.get(key);
  if (existing?.isConnected) return existing;
  const ghost = document.createElement('div');
  ghost.className = 'share-ghost-cursor';
  ghost.dataset.shareSender = key;
  ghost.setAttribute('aria-hidden', 'true');
  ghost.style.setProperty('--share-cursor-color', sharePointerSenderColor(key));
  document.body.appendChild(ghost);
  sharePointerGhosts.set(key, ghost);
  sharePointerGhost = ghost;
  return ghost;
}

function renderShareClickRipple(x, y, sender = '') {
  const ripple = document.createElement('div');
  ripple.className = 'share-click-ripple';
  ripple.setAttribute('aria-hidden', 'true');
  ripple.style.left = `${Math.round(x)}px`;
  ripple.style.top = `${Math.round(y)}px`;
  ripple.style.setProperty('--share-cursor-color', sharePointerSenderColor(sender));
  document.body.appendChild(ripple);
  ripple.addEventListener('animationend', () => ripple.remove(), {once: true});
  ripple.addEventListener('animationcancel', () => ripple.remove(), {once: true});
}

function renderSharePointerGhost(payload = {}) {
  if (!shareViewMode && !shareHasActiveShare()) return;
  if (payload.sender && payload.sender === shareClientId) return;
  const point = sharePointFromPointerPayload(payload);
  if (!point) return;
  const sender = sharePointerSenderKey(payload.sender || '');
  const ghost = ensureSharePointerGhost(sender);
  ghost.style.transform = `translate3d(${Math.round(point.x)}px, ${Math.round(point.y)}px, 0)`;
  ghost.classList.add('visible');
  const existingTimer = sharePointerHideTimers.get(sender);
  if (existingTimer) clearTimeout(existingTimer);
  const timer = setTimeout(() => {
    sharePointerHideTimers.delete(sender);
    sharePointerGhosts.get(sender)?.classList.remove('visible');
  }, 1800);
  sharePointerHideTimers.set(sender, timer);
  sharePointerHideTimer = timer;
  if (payload.click === true) renderShareClickRipple(point.x, point.y, sender);
}

function sharePublishPointerEvent(event, options = {}) {
  if (event?.isPrimary === false) return;
  const payload = sharePointerPayloadForEvent(event, options);
  if (!payload) return;
  if (!shareViewMode && shareReplayFeatureEnabled()) {
    sharePublish('pointer', {...payload, visible: true});
    return;
  }
  sharePublish('pointer', payload);
}

function queueSharePointerMove(event) {
  if (!shareCanPublishUi()) return;
  sharePointerLastEvent = event;
  if (sharePointerFramePending) return;
  sharePointerFramePending = true;
  requestAnimationFrame(() => {
    sharePointerFramePending = false;
    const now = performance.now();
    if (now - sharePointerLastPublishedAt < sharePointerPublishIntervalMs) return;
    sharePointerLastPublishedAt = now;
    const latest = sharePointerLastEvent;
    sharePointerLastEvent = null;
    if (latest) sharePublishPointerEvent(latest);
  });
}

function installSharePointerPublisher() {
  document.addEventListener('pointermove', queueSharePointerMove, {passive: true});
  document.addEventListener('pointerdown', event => {
    if (!shareCanPublishUi() || event?.isPrimary === false) return;
    sharePublishPointerEvent(event, {click: true});
  }, {passive: true});
}

function shareScrollTargetForElement(element) {
  if (!element?.closest) return null;
  const editorPanel = element.closest('.file-editor-panel');
  if (editorPanel?.dataset?.filePath) {
    const item = editorPanel.dataset.layoutItem || (typeof fileEditorPanelItem === 'function' ? fileEditorPanelItem(editorPanel) : '') || '';
    const path = editorPanel.dataset.filePath || '';
    const source = element.closest('.file-editor-preview-pane-panel') ? 'preview' : 'editor';
    const scroller = source === 'preview'
      ? editorPanel.querySelector('.file-editor-preview-pane-panel')
      : (editorPanel._cmView?.scrollDOM || element.closest('.cm-scroller'));
    if (scroller) return {target: `editor:${item || path}:${source}`, kind: 'editor', item, path, source, element: scroller, panel: editorPanel};
  }
  const finderScroller = element.closest('.file-explorer-tree-panel, .file-explorer-changes-panel');
  if (finderScroller) {
    const mode = normalizeFileExplorerMode(fileExplorerMode);
    return {target: `finder:${mode}`, kind: 'finder', mode, element: finderScroller};
  }
  const preferencesScroller = element.closest('.preferences-scroll');
  if (preferencesScroller) return {target: 'preferences', kind: 'preferences', element: preferencesScroller};
  const infoScroller = element.closest('#info-content');
  if (infoScroller) return {target: 'info', kind: 'info', element: infoScroller};
  const terminal = element.closest('.terminal[id^="term-"]');
  if (terminal) {
    const session = terminal.id.slice('term-'.length);
    const item = terminals.get(session);
    const viewport = terminal.querySelector('.xterm-viewport') || element;
    return {target: `terminal:${session}`, kind: 'terminal', session, element: viewport, term: item?.term || null};
  }
  return null;
}

function shareScrollPayloadForElement(element) {
  const descriptor = shareScrollTargetForElement(element);
  if (!descriptor?.target || !descriptor.element) return null;
  const payload = {
    target: descriptor.target,
    kind: descriptor.kind,
    top: Math.max(0, Math.round(Number(descriptor.element.scrollTop || 0))),
    left: Math.max(0, Math.round(Number(descriptor.element.scrollLeft || 0))),
  };
  if (descriptor.kind === 'editor') {
    payload.path = descriptor.path || '';
    payload.item = descriptor.item || '';
    payload.source = descriptor.source || 'editor';
    const view = descriptor.panel?._cmView || null;
    const selection = view?.state?.selection?.main;
    if (selection) {
      payload.anchor = Math.max(0, Number(selection.anchor || 0));
      payload.head = Math.max(0, Number(selection.head ?? selection.anchor ?? 0));
    }
    if (typeof fileEditorVisibleLineNumber === 'function') {
      const line = fileEditorVisibleLineNumber(view);
      if (line > 0) payload.line = line;
    }
  } else if (descriptor.kind === 'finder') {
    payload.mode = descriptor.mode || '';
  } else if (descriptor.kind === 'terminal') {
    payload.session = descriptor.session || '';
    const viewportY = Number(descriptor.term?.buffer?.active?.viewportY);
    if (Number.isFinite(viewportY)) payload.top = Math.max(0, Math.round(viewportY));
  }
  return payload;
}

function scheduleShareScrollPublishForElement(element) {
  if (!shareViewMode && shareReplayFeatureEnabled() && !applyingShareRemoteScroll) {
    scheduleShareReplayScrollPublishForElement(element);
  }
  if (!shareCanPublishScroll() || applyingShareRemoteScroll) {
    if (shareViewMode && !shareWriteMode) restoreShareReadonlyScrollTarget(element);
    return;
  }
  const payload = shareScrollPayloadForElement(element);
  if (!payload?.target) return;
  if (shareScrollPublishTimers.has(payload.target)) {
    shareScrollPublishTimers.get(payload.target).payload = payload;
    return;
  }
  const state = {payload};
  state.timer = setTimeout(() => {
    shareScrollPublishTimers.delete(payload.target);
    if (shareCanPublishScroll()) sharePublish('scroll', state.payload);
  }, 50);
  shareScrollPublishTimers.set(payload.target, state);
}

function scheduleShareReplayScrollPublishForElement(element) {
  if (shareViewMode || !shareReplayFeatureEnabled() || applyingShareRemoteScroll) return;
  const entry = shareReplayScrollEntryForElement(element);
  if (!entry?.nodeId) return;
  const key = String(entry.nodeId);
  if (shareReplayScrollPublishTimers.has(key)) {
    shareReplayScrollPublishTimers.get(key).entry = entry;
    return;
  }
  const state = {entry};
  state.timer = setTimeout(() => {
    shareReplayScrollPublishTimers.delete(key);
    shareReplayPublishDeltaPayload({scroll: [state.entry]}, 'scroll');
  }, 50);
  shareReplayScrollPublishTimers.set(key, state);
}

function sharePublishFileVersion(path, options = {}) {
  const cleanPath = String(path || '').trim();
  if (!cleanPath || !shareCanPublishUi()) return;
  sharePublish('file-version', {
    path: cleanPath,
    mtime: Number(options.mtime || 0) || 0,
    size: Number(options.size || 0) || 0,
    version: Date.now(),
  });
}

function shareScrollElementForPayload(payload = {}) {
  const target = String(payload.target || '');
  if (target.startsWith('editor:')) {
    const path = String(payload.path || '');
    const item = String(payload.item || '');
    const source = String(payload.source || 'editor') === 'preview' ? 'preview' : 'editor';
    const panels = Array.from(document.querySelectorAll('.file-editor-panel'));
    const panel = panels.find(candidate => (
      (item && candidate.dataset?.layoutItem === item)
      || (path && candidate.dataset?.filePath === path)
    ));
    if (!panel) return null;
    const element = source === 'preview'
      ? panel.querySelector('.file-editor-preview-pane-panel')
      : (panel._cmView?.scrollDOM || panel.querySelector('.cm-scroller'));
    return element ? {kind: 'editor', source, element, panel} : null;
  }
  if (target.startsWith('finder:')) {
    const mode = String(payload.mode || target.slice('finder:'.length) || '');
    const selector = mode === 'diff' || mode === 'tabber' ? '.file-explorer-changes-panel' : '.file-explorer-tree-panel';
    const element = Array.from(document.querySelectorAll(selector)).find(node => node.offsetParent !== null) || document.querySelector(selector);
    return element ? {kind: 'finder', element} : null;
  }
  if (target === 'preferences') {
    const element = Array.from(document.querySelectorAll('.preferences-scroll')).find(node => node.offsetParent !== null) || document.querySelector('.preferences-scroll');
    return element ? {kind: 'preferences', element} : null;
  }
  if (target === 'info') {
    const element = document.getElementById('info-content');
    return element ? {kind: 'info', element} : null;
  }
  if (target.startsWith('terminal:')) {
    const session = String(payload.session || target.slice('terminal:'.length) || '');
    const item = terminals.get(session);
    const element = item?.container?.querySelector?.('.xterm-viewport') || null;
    return item ? {kind: 'terminal', session, term: item.term, element} : null;
  }
  return null;
}

function applyShareScrollDescriptorPosition(descriptor, top, left) {
  if (!descriptor) return false;
  if (descriptor.kind === 'terminal' && typeof descriptor.term?.scrollToLine === 'function') {
    descriptor.term.scrollToLine(top);
    return true;
  }
  if (!descriptor.element) return false;
  descriptor.element.scrollTop = top;
  descriptor.element.scrollLeft = left;
  // Setting scrollTop before CodeMirror's first measure can leave its virtual viewport at the old
  // rows without emitting another scroll event. Force the same editor view to consume the host offset.
  if (descriptor.kind === 'editor') descriptor.panel?._cmView?.requestMeasure?.();
  return true;
}

function applyShareScrollState(payload = {}) {
  if (!payload || typeof payload !== 'object') return;
  const target = String(payload.target || '');
  const top = Math.max(0, Math.round(Number(payload.top || 0)));
  const left = Math.max(0, Math.round(Number(payload.left || 0)));
  if (target) {
    shareLastAppliedScrollByTarget.set(target, {top, left, payload: {...payload, target, top, left}});
  }
  if (String(payload.kind || '') === 'editor' || target.startsWith('editor:')) {
    shareRememberEditorViewState(payload, top, left);
  }
  const descriptor = shareScrollElementForPayload(payload);
  if (!descriptor) {
    scheduleShareScrollRestoreByKey(target);
    return;
  }
  const previous = applyingShareRemoteScroll;
  applyingShareRemoteScroll = true;
  try {
    applyShareScrollDescriptorPosition(descriptor, top, left);
    if (descriptor.kind === 'editor' && descriptor.panel?._cmView) {
      const view = descriptor.panel._cmView;
      const docLength = view.state?.doc?.length || 0;
      const anchor = Math.max(0, Math.min(docLength, Number(payload.anchor || 0)));
      const head = Math.max(0, Math.min(docLength, Number(payload.head ?? anchor)));
      if (Number.isFinite(anchor) && Number.isFinite(head)) {
        try { view.dispatch({selection: {anchor, head}}); } catch (_) {}
      }
    }
  } finally {
    scheduleShareScrollRestoreByKey(target);
    requestAnimationFrame(() => { applyingShareRemoteScroll = previous; });
  }
}

function applyShareScrollSnapshot(scroll = []) {
  if (!Array.isArray(scroll)) return;
  for (const payload of scroll.slice(0, 100)) applyShareScrollState(payload);
}

async function applyShareFileVersion(payload = {}) {
  if (!shareViewMode || !payload || typeof payload !== 'object') return false;
  const path = String(payload.path || '').trim();
  if (!path || !openFiles.has(path)) return false;
  const state = openFiles.get(path);
  if (state?.dirty) return false;
  return replaceOpenFileStateFromDisk(path);
}

function installShareScrollPublisher() {
  document.addEventListener('scroll', event => {
    if (shareViewMode && !shareWriteMode) {
      restoreShareReadonlyScrollTarget(event.target);
      return;
    }
    scheduleShareScrollPublishForElement(event.target);
  }, true);
}

function shareRoundedRect(rect = {}) {
  return {
    left: Math.round(Number(rect.left || 0)),
    top: Math.round(Number(rect.top || 0)),
    width: Math.round(Number(rect.width || 0)),
    height: Math.round(Number(rect.height || 0)),
  };
}

function shareFontFingerprint() {
  const canvas = document.createElement?.('canvas');
  const context = canvas?.getContext?.('2d');
  if (!context) return {};
  context.font = `${Math.max(6, Math.round(numberSetting('appearance.ui_font_size', 13)))}px ${getComputedStyle(document.documentElement).getPropertyValue('--ui-font') || 'sans-serif'}`;
  const ui = Math.round(context.measureText('YOLOmux Tabs README.md 0123456789').width * 10) / 10;
  context.font = `${Math.max(6, Math.round(terminalFontSize))}px ${terminalFontFamily}`;
  const mono = Math.round(context.measureText('YOLOmux Tabs README.md 0123456789').width * 10) / 10;
  return {ui, mono};
}

const shareWrappedTextDigestSelectors = [
  'textarea[data-setting-path]',
  'input[type="text"][data-setting-path]',
  '.preferences-setting-help',
  '.preferences-global-reset-warning',
  '.app-menu-command-label',
  '.pane-tab .session-button-dir',
  '.file-tree-name',
  '.changes-file-name',
  '.info-tree-record',
  '.tabber-row-label',
];

function shareWrappedTextElementKey(element, index) {
  const data = element?.dataset || {};
  return String(
    data.settingPath
    || data.path
    || data.paneTab
    || data.item
    || data.session
    || element?.getAttribute?.('aria-label')
    || element?.id
    || `${String(element?.localName || element?.tagName || 'node').toLowerCase()}:${index}`,
  ).slice(0, 240);
}

function shareWrappedTextValue(element) {
  if (!element) return '';
  if ('value' in element) return String(element.value || '');
  return String(element.textContent || '');
}

function shareWrappedTextNodesByKey() {
  const result = new Map();
  const root = appRootElement();
  const queryRoot = root?.querySelectorAll ? root : document;
  const nodes = [];
  for (const selector of shareWrappedTextDigestSelectors) {
    try {
      queryRoot.querySelectorAll(selector).forEach(node => {
        if (node && !nodes.includes(node)) nodes.push(node);
      });
    } catch (_) {}
  }
  nodes.forEach((node, index) => {
    const key = shareWrappedTextElementKey(node, index);
    if (key && !result.has(key)) result.set(key, node);
  });
  return result;
}

function applyShareTextWrapMetrics(metrics = []) {
  if (!shareViewMode || !Array.isArray(metrics)) return;
  shareAppliedTextWrapMetricsByKey.clear();
  const nodesByKey = shareWrappedTextNodesByKey();
  for (const metric of metrics.slice(0, 80)) {
    if (!metric || typeof metric !== 'object') continue;
    const key = String(metric.key || '').trim();
    if (!key) continue;
    shareAppliedTextWrapMetricsByKey.set(key, metric);
    const node = nodesByKey.get(key);
    if (!node?.style) continue;
    const tag = String(node.localName || node.tagName || '').toLowerCase();
    const rect = metric.rect && typeof metric.rect === 'object' ? metric.rect : {};
    const width = Math.round(Number(rect.width || 0));
    const height = Math.round(Number(rect.height || 0));
    if (tag === 'textarea' && node.dataset?.settingPath !== undefined) {
      if (width > 0) node.style.width = `${width}px`;
      if (height > 0) {
        node.style.height = `${height}px`;
        node.style.minHeight = `${height}px`;
        node.style.maxHeight = `${height}px`;
      }
      node.style.overflowY = Number(metric.scrollHeight || 0) > height ? 'auto' : 'hidden';
    } else if (tag === 'input' && node.dataset?.settingPath !== undefined) {
      if (width > 0) node.style.width = `${width}px`;
      if (height > 0) node.style.height = `${height}px`;
    } else {
      const current = shareRoundedRect(appSpaceRect(node));
      const widthDiffers = width > 0 && Math.round(Number(current.width || 0)) !== width;
      const heightDiffers = height > 0 && Math.round(Number(current.height || 0)) !== height;
      if (widthDiffers) {
        node.style.width = `${width}px`;
        node.style.maxWidth = `${width}px`;
      }
      if (heightDiffers) {
        node.style.height = `${height}px`;
        node.style.minHeight = `${height}px`;
        node.style.maxHeight = `${height}px`;
      }
      if (widthDiffers || heightDiffers) node.style.overflow = 'hidden';
    }
  }
}

function shareTextWrapDigestEntryWithHostMetrics(entry, metric = null) {
  if (!shareViewMode || !metric || typeof metric !== 'object') return entry;
  const rect = metric.rect && typeof metric.rect === 'object' ? metric.rect : {};
  const hostLeft = Math.round(Number(rect.left));
  const hostTop = Math.round(Number(rect.top));
  const hostWidth = Math.round(Number(rect.width || 0));
  const hostHeight = Math.round(Number(rect.height || 0));
  const nextRect = {...entry.rect};
  if (Number.isFinite(hostLeft)) nextRect.left = hostLeft;
  if (Number.isFinite(hostTop)) nextRect.top = hostTop;
  if (hostWidth > 0) nextRect.width = hostWidth;
  if (hostHeight > 0) nextRect.height = hostHeight;
  return {
    ...entry,
    rect: nextRect,
    clientWidth: hostWidth > 0 ? hostWidth : entry.clientWidth,
    clientHeight: hostHeight > 0 ? hostHeight : entry.clientHeight,
    scrollWidth: Math.round(Number(metric.scrollWidth || entry.scrollWidth || 0)),
    scrollHeight: Math.round(Number(metric.scrollHeight || entry.scrollHeight || 0)),
    font: String(metric.font || entry.font || '').slice(0, 200),
    fontFamily: String(metric.fontFamily || entry.fontFamily || '').slice(0, 160),
    fontSize: String(metric.fontSize || entry.fontSize || ''),
    lineHeight: String(metric.lineHeight || entry.lineHeight || ''),
    letterSpacing: String(metric.letterSpacing || entry.letterSpacing || ''),
    whiteSpace: String(metric.whiteSpace || entry.whiteSpace || ''),
    wordBreak: String(metric.wordBreak || entry.wordBreak || ''),
    overflowWrap: String(metric.overflowWrap || entry.overflowWrap || ''),
  };
}

function shareWrappedTextDigestSnapshot() {
  const root = appRootElement();
  const nodes = [];
  const addNode = node => {
    if (!node || nodes.includes(node)) return;
    const rect = node.getBoundingClientRect?.();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    nodes.push(node);
  };
  for (const selector of shareWrappedTextDigestSelectors) {
    const queryRoot = root?.querySelectorAll ? root : document;
    try {
      queryRoot.querySelectorAll(selector).forEach(addNode);
    } catch (_) {}
  }
  return nodes.slice(0, 80).map((node, index) => {
    const style = getComputedStyle(node);
    const rect = shareRoundedRect(appSpaceRect(node));
    const value = shareWrappedTextValue(node);
    const key = shareWrappedTextElementKey(node, index);
    const entry = {
      key,
      tag: String(node.localName || node.tagName || '').toLowerCase(),
      rect,
      clientWidth: Math.round(Number(node.clientWidth || rect.width || 0)),
      clientHeight: Math.round(Number(node.clientHeight || rect.height || 0)),
      scrollWidth: Math.round(Number(node.scrollWidth || 0)),
      scrollHeight: Math.round(Number(node.scrollHeight || 0)),
      textHash: shareHashText(value),
      textLength: value.length,
      font: String(style?.font || '').slice(0, 200),
      fontFamily: String(style?.fontFamily || '').slice(0, 160),
      fontSize: String(style?.fontSize || ''),
      lineHeight: String(style?.lineHeight || ''),
      letterSpacing: String(style?.letterSpacing || ''),
      whiteSpace: String(style?.whiteSpace || ''),
      wordBreak: String(style?.wordBreak || ''),
      overflowWrap: String(style?.overflowWrap || ''),
    };
    return shareTextWrapDigestEntryWithHostMetrics(entry, shareAppliedTextWrapMetricsByKey.get(key));
  }).sort((a, b) => a.key.localeCompare(b.key) || a.tag.localeCompare(b.tag));
}

function shareTerminalCellDigest(session, item) {
  const cell = item?.term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || item?.term?._core?._renderService?.dimensions?.css?.cell
    || {};
  return {
    session: String(session || ''),
    cols: Number(item?.term?.cols || 0),
    rows: Number(item?.term?.rows || 0),
    cellWidth: Math.round(Number(cell.width || 0) * 10) / 10,
    cellHeight: Math.round(Number(cell.height || 0) * 10) / 10,
  };
}

function shareEditorDigest(panel) {
  const item = panel?.dataset?.layoutItem || '';
  const path = panel?.dataset?.filePath || '';
  const mode = typeof fileEditorPanelMode === 'function' ? fileEditorPanelMode(panel) : '';
  const state = openFiles.get(path);
  const kind = state?.loading ? 'loading' : String(state?.kind || 'missing');
  const content = kind === 'text' ? String(state?.content || '') : '';
  const error = kind === 'error' || kind === 'too-large' || kind === 'missing'
    ? String(state?.error || state?.message || '')
    : '';
  return {
    item,
    path,
    mode,
    rect: shareRoundedRect(appSpaceRect(panel)),
    kind,
    dirty: state?.dirty === true,
    contentHash: content ? shareHashText(content) : '',
    contentLength: content.length,
    errorHash: error ? shareHashText(error) : '',
    size: Number.isFinite(Number(state?.size)) ? Number(state.size) : 0,
    mtime: Number.isFinite(Number(state?.mtime)) ? Number(state.mtime) : 0,
  };
}

function shareSlotDigestSnapshot() {
  return {
    layout: layoutParamValue(layoutSlots),
    slots: layoutSlotKeys().map(slot => ({
      slot,
      placeholder: paneIsPlaceholder(slot),
    })),
  };
}

function shareGeometryDigestSnapshot() {
  const viewport = appViewport();
  const slots = shareSlotDigestSnapshot();
  const tabStrips = Array.from(document.querySelectorAll('.dv-tabs-container, .pane-tabs')).map((strip, index) => {
    const tabs = Array.from(strip.querySelectorAll('.dockview-pane-tab, .pane-tab'));
    const rect = shareRoundedRect(appSpaceRect(strip));
    const first = tabs[0] ? shareRoundedRect(appSpaceRect(tabs[0])) : null;
    const last = tabs.length ? shareRoundedRect(appSpaceRect(tabs[tabs.length - 1])) : null;
    const hiddenStrip = rect.width === 0 && rect.height === 0
      && (!first || (first.width === 0 && first.height === 0))
      && (!last || (last.width === 0 && last.height === 0));
    if (hiddenStrip) return null;
    return {
      index,
      rect,
      first,
      last,
      items: tabs.map(tab => tab.dataset?.paneTab || tab.dataset?.item || tab.textContent?.trim?.() || '').filter(Boolean),
      count: tabs.length,
    };
  }).filter(Boolean);
  const terminalCells = Array.from(terminals.entries()).map(([session, item]) => shareTerminalCellDigest(session, item)).sort((a, b) => a.session.localeCompare(b.session));
  const editors = Array.from(document.querySelectorAll('.file-editor-panel'))
    .map(shareEditorDigest)
    .sort((a, b) => (a.item || a.path).localeCompare(b.item || b.path));
  return {
    viewport: {width: viewport.width, height: viewport.height},
    slots,
    tabStrips,
    terminalCells,
    editors,
    fonts: shareFontFingerprint(),
    textWraps: shareWrappedTextDigestSnapshot(),
  };
}

function stableDigestJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableDigestJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableDigestJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function shareHashText(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function shareGeometryDigestValue(snapshot = shareGeometryDigestSnapshot()) {
  return shareHashText(stableDigestJson(snapshot));
}

function shareGeometryDigestFrame() {
  const snapshot = shareGeometryDigestSnapshot();
  return {digest: shareGeometryDigestValue(snapshot), snapshot};
}

function shareGeometryDigestSnapshotCounts(snapshot = {}) {
  return {
    tabStrips: Array.isArray(snapshot.tabStrips) ? snapshot.tabStrips.length : 0,
    terminalCells: Array.isArray(snapshot.terminalCells) ? snapshot.terminalCells.length : 0,
    editors: Array.isArray(snapshot.editors) ? snapshot.editors.length : 0,
    textWraps: Array.isArray(snapshot.textWraps) ? snapshot.textWraps.length : 0,
  };
}

function shareGeometryRectsWithinTolerance(hostRect = {}, localRect = {}, tolerancePx = 1) {
  for (const key of ['left', 'top', 'width', 'height']) {
    const hostValue = Math.round(Number(hostRect?.[key] || 0));
    const localValue = Math.round(Number(localRect?.[key] || 0));
    if (Math.abs(hostValue - localValue) > tolerancePx) return false;
  }
  return true;
}

function shareTabStripEntriesEquivalent(hostStrip = {}, localStrip = {}) {
  return Number(hostStrip?.index) === Number(localStrip?.index)
    && Number(hostStrip?.count || 0) === Number(localStrip?.count || 0)
    && stableDigestJson(hostStrip?.items || []) === stableDigestJson(localStrip?.items || [])
    && shareGeometryRectsWithinTolerance(hostStrip?.rect, localStrip?.rect)
    && shareGeometryRectsWithinTolerance(hostStrip?.first, localStrip?.first)
    && shareGeometryRectsWithinTolerance(hostStrip?.last, localStrip?.last);
}

function shareTabStripsEquivalent(hostTabStrips = [], localTabStrips = []) {
  if (!Array.isArray(hostTabStrips) || !Array.isArray(localTabStrips)) return false;
  if (hostTabStrips.length !== localTabStrips.length) return false;
  for (let index = 0; index < hostTabStrips.length; index += 1) {
    if (!shareTabStripEntriesEquivalent(hostTabStrips[index], localTabStrips[index])) return false;
  }
  return true;
}

function shareGeometryFirstDifference(host = {}, local = {}) {
  const hostSnapshot = host.snapshot && typeof host.snapshot === 'object' ? host.snapshot : {};
  const localSnapshot = local.snapshot && typeof local.snapshot === 'object' ? local.snapshot : {};
  for (const key of ['viewport', 'fonts', 'slots', 'tabStrips', 'terminalCells', 'editors', 'textWraps']) {
    if (key === 'tabStrips' && shareTabStripsEquivalent(hostSnapshot[key], localSnapshot[key])) continue;
    if (stableDigestJson(hostSnapshot[key]) !== stableDigestJson(localSnapshot[key])) return key;
  }
  return '';
}

function shareDebugSecretValues() {
  const values = new Set();
  const add = value => {
    const text = String(value || '');
    if (text.length >= 4) values.add(text);
  };
  add(shareToken);
  add(shareBootstrap?.token);
  for (const share of activeShares || []) add(share?.token);
  return Array.from(values).sort((a, b) => b.length - a.length);
}

function shareRedactSecretText(value) {
  return shareReplayRedactText(value);
}

function shareRedactDiagnosticValue(value, depth = 0) {
  if (depth > 12) return '[truncated-depth]';
  if (typeof value === 'string') return shareRedactSecretText(value);
  if (typeof value !== 'object' || value === null) return value;
  if (Array.isArray(value)) return value.map(item => shareRedactDiagnosticValue(item, depth + 1));
  const result = {};
  for (const [key, rawValue] of Object.entries(value)) {
    result[key] = /token|secret/i.test(key) && typeof rawValue === 'string'
      ? '[redacted-share-token]'
      : shareRedactDiagnosticValue(rawValue, depth + 1);
  }
  return result;
}

function shareDebugNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.round(number * 100) / 100 : null;
}

function shareDebugRect(rect) {
  if (!rect) return null;
  return {
    left: shareDebugNumber(rect.left),
    top: shareDebugNumber(rect.top),
    right: shareDebugNumber(rect.right),
    bottom: shareDebugNumber(rect.bottom),
    width: shareDebugNumber(rect.width),
    height: shareDebugNumber(rect.height),
  };
}

function shareDebugVisualViewportSnapshot() {
  const viewport = window.visualViewport;
  if (!viewport) return null;
  return {
    width: shareDebugNumber(viewport.width),
    height: shareDebugNumber(viewport.height),
    offsetLeft: shareDebugNumber(viewport.offsetLeft),
    offsetTop: shareDebugNumber(viewport.offsetTop),
    pageLeft: shareDebugNumber(viewport.pageLeft),
    pageTop: shareDebugNumber(viewport.pageTop),
    scale: shareDebugNumber(viewport.scale),
  };
}

function shareDebugLocationSnapshot() {
  return {
    protocol: location.protocol,
    host: location.host,
    pathname: location.pathname,
    search: shareRedactSecretText(location.search || ''),
    hash: location.hash ? shareRedactSecretText(location.hash) : '',
  };
}

function shareDebugContextSnapshot() {
  const root = appRootElement();
  return shareRedactDiagnosticValue({
    share: {
      id: shareBootstrap?.id || '',
      mode: shareBootstrap?.mode || '',
      view: shareViewMode,
      write: shareWriteMode,
      fit: shareViewFit,
      viewerId: shareViewerId || '',
      repairInFlight: shareGeometryRepairInFlight,
      resyncInFlight: shareGeometryResyncInFlight,
    },
    location: shareDebugLocationSnapshot(),
    browser: {
      userAgent: navigator.userAgent || '',
      platform: navigator.platform || '',
      language: navigator.language || '',
      devicePixelRatio: shareDebugNumber(window.devicePixelRatio || 1),
    },
    viewport: {
      native: nativeViewport(),
      app: appViewport(),
      visual: shareDebugVisualViewportSnapshot(),
      documentElement: {
        clientWidth: document.documentElement?.clientWidth || 0,
        clientHeight: document.documentElement?.clientHeight || 0,
      },
    },
    mirror: {
      transform: appMirrorTransformState(),
      rootRect: shareDebugRect(root?.getBoundingClientRect?.()),
      stageRect: shareDebugRect(shareMirrorStage?.getBoundingClientRect?.()),
    },
  });
}

function shareGeometryDebugEntryKey(bucket, entry, index) {
  if (!entry || typeof entry !== 'object') return String(index);
  if (bucket === 'textWraps') return `${entry.key || index}:${entry.tag || ''}`;
  if (bucket === 'terminalCells') return String(entry.session || index);
  if (bucket === 'editors') return String(entry.item || entry.path || index);
  if (bucket === 'tabStrips') return String((entry.index ?? (entry.items || []).join('|')) || index);
  if (bucket === 'slots') return String(entry.slot || index);
  return String(entry.key || entry.id || entry.name || index);
}

function shareGeometryDebugArrayDelta(bucket, hostValue = [], localValue = []) {
  const hostMap = new Map(hostValue.map((entry, index) => [shareGeometryDebugEntryKey(bucket, entry, index), entry]));
  const localMap = new Map(localValue.map((entry, index) => [shareGeometryDebugEntryKey(bucket, entry, index), entry]));
  const hostOnly = Array.from(hostMap.keys()).filter(key => !localMap.has(key));
  const localOnly = Array.from(localMap.keys()).filter(key => !hostMap.has(key));
  const changed = [];
  for (const key of hostMap.keys()) {
    if (!localMap.has(key)) continue;
    const hostEntry = hostMap.get(key);
    const localEntry = localMap.get(key);
    if (stableDigestJson(hostEntry) === stableDigestJson(localEntry)) continue;
    changed.push({
      key,
      host: shareRedactDiagnosticValue(hostEntry),
      local: shareRedactDiagnosticValue(localEntry),
    });
    if (changed.length >= 8) break;
  }
  return {
    match: !hostOnly.length && !localOnly.length && !changed.length,
    hostCount: hostValue.length,
    localCount: localValue.length,
    hostOnly: hostOnly.slice(0, 12),
    localOnly: localOnly.slice(0, 12),
    changed,
  };
}

function shareGeometryDebugObjectDelta(bucket, hostValue = {}, localValue = {}) {
  const keys = Array.from(new Set([...Object.keys(hostValue || {}), ...Object.keys(localValue || {})]));
  const changed = [];
  for (const key of keys) {
    if (stableDigestJson(hostValue?.[key]) === stableDigestJson(localValue?.[key])) continue;
    changed.push({
      key,
      host: shareRedactDiagnosticValue(hostValue?.[key]),
      local: shareRedactDiagnosticValue(localValue?.[key]),
    });
    if (changed.length >= 12) break;
  }
  return {
    match: changed.length === 0,
    bucket,
    changed,
  };
}

function shareGeometryDebugBucketDelta(bucket, hostValue, localValue) {
  if (!bucket) return {match: true};
  if (Array.isArray(hostValue) || Array.isArray(localValue)) {
    return shareGeometryDebugArrayDelta(bucket, Array.isArray(hostValue) ? hostValue : [], Array.isArray(localValue) ? localValue : []);
  }
  if (hostValue && typeof hostValue === 'object' && localValue && typeof localValue === 'object') {
    if (bucket === 'slots') {
      return {
        ...shareGeometryDebugObjectDelta(bucket, hostValue, localValue),
        slotEntries: shareGeometryDebugArrayDelta(bucket, hostValue.slots || [], localValue.slots || []),
      };
    }
    return shareGeometryDebugObjectDelta(bucket, hostValue, localValue);
  }
  return {
    match: stableDigestJson(hostValue) === stableDigestJson(localValue),
    host: shareRedactDiagnosticValue(hostValue),
    local: shareRedactDiagnosticValue(localValue),
  };
}

function shareGeometryDebugDeltas(hostSnapshot = {}, localSnapshot = {}) {
  return Object.fromEntries(['viewport', 'fonts', 'slots', 'tabStrips', 'terminalCells', 'editors', 'textWraps'].map(bucket => [
    bucket,
    shareGeometryDebugBucketDelta(bucket, hostSnapshot[bucket], localSnapshot[bucket]),
  ]));
}

function shareReplayFrameByteLength(value = {}) {
  const text = stableDigestJson(shareRedactDiagnosticValue(value));
  return utf8ByteLength(text);
}

function shareReplayFrameLatencyMs(payload = {}) {
  const capturedAt = Number(payload?.capturedAt);
  const createdAt = Number(payload?.createdAt);
  const timestampMs = Number.isFinite(capturedAt) && capturedAt > 0
    ? capturedAt
    : Number.isFinite(createdAt) && createdAt > 0
      ? createdAt * 1000
      : 0;
  return timestampMs > 0 ? Math.max(0, Math.round(Date.now() - timestampMs)) : null;
}

function shareReplayRecordFrameMetrics(kind = '', payload = {}, message = {}) {
  shareReplayLastFrameReceivedAt = Date.now();
  shareReplayLastLatencyMs = shareReplayFrameLatencyMs(payload);
  const bytes = shareReplayFrameByteLength({type: message.type || kind, payload, epoch: message.epoch, sequence: message.sequence});
  if (kind === shareMirrorProtocol.frames.domKeyframe) {
    shareReplayLastKeyframeBytes = bytes;
    const policyVersion = Number(payload?.redaction?.policyVersion);
    if (Number.isFinite(policyVersion)) shareReplayLastRedactionPolicyVersion = policyVersion;
  } else if (kind === shareMirrorProtocol.frames.domDelta) {
    shareReplayLastDeltaBytes = bytes;
  }
}

function shareReplayTerminalPlaceholderDiagnostics() {
  const streamStatusForItem = item => {
    if (!item?.term) return 'not-mounted';
    const readyState = Number(item?.socket?.readyState);
    if (readyState === 0) return 'connecting';
    if (readyState === 1) return item.shareTerminalBytesReceived === true ? 'received-bytes' : 'open-no-bytes';
    if (readyState === 2) return 'closing';
    if (readyState === 3) return 'closed';
    return 'no-socket';
  };
  const entries = Array.from(shareReplayTerminalPlaceholders.values()).map(entry => {
    const item = terminals.get(entry.session);
    const lastByteAt = Math.max(0, Math.round(Number(item?.shareTerminalLastByteAt) || 0));
    const lastResetAt = Math.max(0, Math.round(Number(item?.shareTerminalLastResetAt) || 0));
    return {
      placeholderId: entry.placeholderId,
      session: entry.session,
      rows: entry.rows,
      cols: entry.cols,
      terminalEpoch: entry.terminalEpoch,
      connected: Boolean(document.querySelector?.(`[data-share-terminal-placeholder="${cssEscape(entry.session)}"]`)),
      streamStatus: streamStatusForItem(item),
      socketReadyState: Number.isFinite(Number(item?.socket?.readyState)) ? Number(item.socket.readyState) : null,
      receivedBytes: item?.shareTerminalBytesReceived === true,
      byteCount: Math.max(0, Math.round(Number(item?.shareTerminalByteCount) || 0)),
      lastByteAgeMs: lastByteAt > 0 ? Math.max(0, Math.round(Date.now() - lastByteAt)) : null,
      lastResetAgeMs: lastResetAt > 0 ? Math.max(0, Math.round(Date.now() - lastResetAt)) : null,
      skippedResetCount: Math.max(0, Math.round(Number(item?.shareTerminalSkippedResetCount) || 0)),
    };
  });
  const connected = entries.filter(entry => entry.connected).length;
  return {
    count: entries.length,
    connected,
    disconnected: entries.length - connected,
    healthy: connected === entries.length,
    entries,
  };
}

function shareReplayHealthDiagnostics() {
  const terminalPlaceholders = shareReplayTerminalPlaceholderDiagnostics();
  return shareRedactDiagnosticValue({
    kind: 'share-replay-health',
    at: new Date().toISOString(),
    match: shareReplayShellState.status === 'mirrored' && terminalPlaceholders.healthy,
    status: shareReplayShellState.status || 'idle',
    userStatus: shareReplayUserStatusText(shareReplayShellState.status || ''),
    epoch: shareReplayCurrentEpoch,
    sequence: shareReplayLastSequence,
    keyframeBytes: shareReplayLastKeyframeBytes,
    deltaBytes: shareReplayLastDeltaBytes,
    droppedFrames: shareReplayDroppedFrames,
    staleFrames: shareReplayStaleFrames,
    keyframeRequests: shareReplayKeyframeRequestCount,
    keyframeRequestsSuppressed: shareReplayKeyframeRequestSuppressedCount,
    keyframeRequestBackoffMs: shareReplayKeyframeBackoffMs,
    keyframeRequestInFlight: shareReplayKeyframeInFlight,
    hostKeyframesSuppressed: shareReplayHostKeyframeSuppressedCount,
    hostKeyframePending: Boolean(shareReplayHostKeyframeTimer),
    replayLatencyMs: shareReplayLastLatencyMs,
    lastFrameAgeMs: shareReplayLastFrameReceivedAt ? Math.max(0, Math.round(Date.now() - shareReplayLastFrameReceivedAt)) : null,
    domDigest: shareReplayCurrentDomDigest(),
    redactionPolicyVersion: shareReplayLastRedactionPolicyVersion,
    nodeCount: shareReplayNodeMap.size,
    lastReplayError: shareReplayLastReplayError,
    hostPerformance: shareReplayHostPerformanceDiagnostics(),
    terminalPlaceholders,
    context: shareDebugContextSnapshot(),
  });
}

function shareDebugTextForClipboard(report = shareDebugReports[shareDebugReports.length - 1] || null) {
  const fallback = shareReplayShellActive ? shareReplayHealthDiagnostics() : {message: t('share.debug.noDiagnostics')};
  return JSON.stringify(shareRedactDiagnosticValue(report || fallback), null, 2);
}

function shareDebugProfileUploadEnabled() {
  if (!shareViewMode || !shareToken) return false;
  if (shareBootstrap?.debugProfile === true || shareBootstrap?.debug_profile === true) return true;
  return Boolean(shareViewerCurrentShare()?.debugProfile);
}

function shareDebugProfileUploadKind(kind = '') {
  return String(kind || 'share-debug-profile').replace(/[^A-Za-z0-9_.:-]+/g, '-').slice(0, 120) || 'share-debug-profile';
}

function shareDebugProfileUploadAllowed(kind = '', floorMs = shareDebugProfileUploadMinIntervalMs) {
  const key = shareDebugProfileUploadKind(kind);
  const now = Date.now();
  const last = Math.max(0, Number(shareDebugProfileLastUploadAtByKind.get(key)) || 0);
  const floor = Math.max(0, Number(floorMs) || 0);
  if (last > 0 && now - last < floor) return false;
  shareDebugProfileLastUploadAtByKind.set(key, now);
  return true;
}

function shareDebugProfileUploadPayload(kind = '', detail = {}) {
  return shareRedactDiagnosticValue({
    kind: shareDebugProfileUploadKind(kind),
    at: new Date().toISOString(),
    viewerId: shareViewerId || shareClientId || '',
    shareId: shareBootstrap?.id || shareViewerCurrentShare()?.shortId || '',
    detail,
  });
}

async function shareUploadDebugProfile(kind = 'share-debug-profile', detail = {}, options = {}) {
  if (!shareDebugProfileUploadEnabled()) return false;
  if (options.force !== true && !shareDebugProfileUploadAllowed(kind, options.floorMs ?? shareDebugProfileUploadMinIntervalMs)) return false;
  const payload = shareDebugProfileUploadPayload(kind, detail);
  try {
    await apiFetchJson('/api/share/debug-profile', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    return true;
  } catch (error) {
    if (shareDebugEnabled) console.warn('share debug profile upload failed', error);
    return false;
  }
}

async function copyShareDebugDiagnostics() {
  await copyTextToClipboard(shareDebugTextForClipboard());
}

function exposeShareDebugApi() {
  if (!shareViewMode) return;
  window.yolomuxShareDebug = {
    enabled: shareDebugEnabled,
    uploadEnabled: shareDebugProfileUploadEnabled(),
    latest: shareDebugReports[shareDebugReports.length - 1] || null,
    reports: shareDebugReports.slice(),
    replayHealth: shareReplayHealthDiagnostics,
    text: shareDebugTextForClipboard,
    copy: copyShareDebugDiagnostics,
    upload: shareUploadDebugProfile,
  };
}

function recordShareGeometryDebugReport(payload = {}, local = {}, options = {}) {
  const hostSnapshot = payload.snapshot && typeof payload.snapshot === 'object' ? payload.snapshot : {};
  const localSnapshot = local.snapshot && typeof local.snapshot === 'object' ? local.snapshot : {};
  const diff = options.diff || shareGeometryFirstDifference(payload, local);
  const report = {
    kind: 'share-geometry-drift',
    sequence: ++shareDebugSequence,
    at: new Date().toISOString(),
    phase: options.phase || 'persistent',
    match: options.match === true,
    diff,
    initialDiff: options.initialDiff || '',
    hostDigest: payload.digest || shareGeometryDigestValue(hostSnapshot),
    localDigest: local.digest || shareGeometryDigestValue(localSnapshot),
    context: shareDebugContextSnapshot(),
    delta: shareGeometryDebugBucketDelta(diff, hostSnapshot[diff], localSnapshot[diff]),
    deltas: shareGeometryDebugDeltas(hostSnapshot, localSnapshot),
    snapshots: {
      host: shareRedactDiagnosticValue(hostSnapshot),
      local: shareRedactDiagnosticValue(localSnapshot),
    },
  };
  shareDebugReports = [...shareDebugReports, report].slice(-shareDebugReportLimit);
  exposeShareDebugApi();
  void shareUploadDebugProfile(report.kind, report);
  if (shareDebugEnabled || options.phase === 'persistent') {
    console.warn('share mirror geometry drift', report);
  }
  return report;
}

async function resyncShareViewerUiState() {
  const now = Date.now();
  const lastStartedAt = Math.max(0, Math.round(Number(shareGeometryResyncLastStartedAt) || 0));
  if (!shareViewMode || shareGeometryResyncInFlight) return false;
  if (lastStartedAt > 0 && now - lastStartedAt < shareGeometryResyncMinIntervalMs) return false;
  shareGeometryResyncLastStartedAt = now;
  shareGeometryResyncInFlight = true;
  try {
    const payload = await apiFetchJson('/api/share', {cache: 'no-store'});
    const uiState = payload?.uiState && typeof payload.uiState === 'object' ? payload.uiState : {};
    await applyShareUiState({
      ...uiState,
      layout: payload?.layout || uiState.layout || '',
      tabs: payload?.tabs || uiState.tabs || '',
      viewport: payload?.viewport || uiState.viewport || {},
      appearance: payload?.appearance || uiState.appearance || {},
      finder: payload?.finder || uiState.finder || {},
      terminalDims: payload?.terminalDims || uiState.terminalDims || [],
    });
    return true;
  } catch (error) {
    console.warn('share mirror resync failed', error);
    return false;
  } finally {
    shareGeometryResyncInFlight = false;
  }
}

function updateShareMirrorDigestStatus(result = shareLastGeometryDigestResult) {
  shareLastGeometryDigestResult = result;
  if (!shareViewerBanner) return;
  const node = shareViewerBanner.querySelector('.share-viewer-mirror-status');
  if (!node) return;
  if (shareReplayShellActive) {
    const status = shareReplayShellState.status || 'waiting';
    node.classList.toggle('match', status === 'mirrored');
    node.classList.toggle('mismatch', status === 'error');
    node.textContent = shareReplayUserStatusText(status);
    return;
  }
  if (!result) {
    node.textContent = t('share.mirror.checking');
    node.classList.remove('mismatch', 'match');
    return;
  }
  node.classList.toggle('match', result.match === true);
  node.classList.toggle('mismatch', result.match === false);
  node.textContent = result.match
    ? t('share.mirror.synced')
    : t('share.mirror.drift');
}

function applyShareViewBodyClasses() {
  if (!shareViewMode) return;
  document.body?.classList?.add('share-view-mode');
  document.body?.classList?.toggle('share-view-readonly', !shareWriteMode);
  document.body?.classList?.toggle('share-view-write', shareWriteMode);
}

function shareNextAnimationFrame() {
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

async function waitForShareGeometryResyncIdle(maxFrames = 90) {
  for (let frame = 0; frame < maxFrames; frame += 1) {
    if (!shareGeometryResyncInFlight) return true;
    await shareNextAnimationFrame();
  }
  return !shareGeometryResyncInFlight;
}

function shareGeometryDigestCompare(payload = {}) {
  let local = shareGeometryDigestFrame();
  let match = payload.digest === local.digest;
  let diff = match ? '' : shareGeometryFirstDifference(payload, local);
  if (!match && !diff) match = true;
  if (!match && diff === 'textWraps') {
    applyShareTextWrapMetrics(payload.snapshot?.textWraps || []);
    local = shareGeometryDigestFrame();
    match = payload.digest === local.digest;
    diff = match ? '' : shareGeometryFirstDifference(payload, local);
    if (!match && !diff) match = true;
  }
  return {match, diff, local};
}

function shareGeometryRepairActionForDiff(diff = '') {
  const bucket = String(diff || '');
  if (bucket === 'terminalCells') return shareMirrorProtocol.frames.terminalHostResize;
  if (bucket === 'textWraps') return shareMirrorProtocol.frames.textWrapMetrics;
  if (bucket === shareMirrorProtocol.frames.popupLayer) return shareMirrorProtocol.frames.popupLayer;
  if (bucket === 'domDigest') return shareMirrorProtocol.frames.domKeyframe;
  if (['slots', 'tabStrips', 'editors', 'viewport', 'fonts'].includes(bucket)) return shareMirrorProtocol.frames.uiState;
  return shareMirrorProtocol.frames.uiState;
}

function applyShareTerminalCellsRepair(terminalCells = []) {
  if (!shareViewMode || !Array.isArray(terminalCells)) return false;
  let applied = false;
  for (const entry of terminalCells) {
    if (!entry || typeof entry !== 'object') continue;
    const session = String(entry.session || '').trim();
    const rows = Math.floor(Number(entry.rows) || 0);
    const cols = Math.floor(Number(entry.cols) || 0);
    if (!session || rows <= 0 || cols <= 0) continue;
    updateShareHostTerminalSize(session, rows, cols);
    applied = true;
  }
  return applied;
}

async function repairShareGeometryBucket(payload = {}, diff = '') {
  const action = shareGeometryRepairActionForDiff(diff);
  if (action === shareMirrorProtocol.frames.terminalHostResize) {
    return applyShareTerminalCellsRepair(payload.snapshot?.terminalCells || []);
  }
  if (action === shareMirrorProtocol.frames.textWrapMetrics) {
    applyShareTextWrapMetrics(payload.snapshot?.textWraps || []);
    return true;
  }
  if (action === shareMirrorProtocol.frames.popupLayer || action === shareMirrorProtocol.frames.domKeyframe) {
    return false;
  }
  return resyncShareViewerUiState();
}

async function repairShareGeometryDigest(payload = {}, initialDiff = '') {
  if (shareGeometryRepairInFlight) return;
  shareGeometryRepairInFlight = true;
  try {
    let {match, diff, local} = shareGeometryDigestCompare(payload);
    for (let attempt = 0; attempt < 2 && !match; attempt += 1) {
      if (shareGeometryResyncInFlight) await waitForShareGeometryResyncIdle();
      else await repairShareGeometryBucket(payload, diff);
      await waitForShareGeometryResyncIdle();
      await shareNextAnimationFrame();
      await shareNextAnimationFrame();
      ({match, diff, local} = shareGeometryDigestCompare(payload));
    }
    updateShareMirrorDigestStatus({match, diff});
    if (!match) {
      recordShareGeometryDebugReport(payload, local, {phase: 'persistent', diff: diff || initialDiff, initialDiff, match});
    }
  } finally {
    shareGeometryRepairInFlight = false;
  }
}

function applyShareGeometryDigest(payload = {}) {
  if (!payload || typeof payload !== 'object') return;
  shareLastGeometryDigest = payload;
  if (!shareViewMode) return;
  const {match, diff, local} = shareGeometryDigestCompare(payload);
  if (match) {
    updateShareMirrorDigestStatus({match, diff});
    return;
  }
  if (!shareGeometryRepairInFlight) void repairShareGeometryDigest(payload, diff);
}

function publishShareGeometryDigest() {
  if (shareViewMode || !shareCanPublishUi()) return;
  if (!shareHostHasConnectedViewers()) {
    shareReplayRecordHostPerfSkip('geometryDigest');
    return;
  }
  const startedAt = performanceNow();
  const frame = shareGeometryDigestFrame();
  shareReplayRecordHostPerf('geometryDigest', startedAt, {
    digest: frame.digest,
    ...shareGeometryDigestSnapshotCounts(frame.snapshot),
  });
  sharePublish('geometry-digest', frame);
}

function installShareGeometryDigestLoop() {
  if (shareGeometryDigestTimer) clearInterval(shareGeometryDigestTimer);
  shareGeometryDigestTimer = setInterval(publishShareGeometryDigest, shareGeometryDigestPublishMs);
}

function applyShareUiMessage(message) {
  if ((!shareViewMode && !shareHasActiveShare()) || !message || message.ch !== 'ui') return;
  if (shareReplayShellActive) {
    applyShareReplayShellMessage(message);
    return;
  }
  if (shareReadOnlyReplayModeEnabled()) return;
  if (message.sender && message.sender === shareClientId) return;
  if (shareDropStaleMirrorFrame(message)) return;
  const payload = message.payload && typeof message.payload === 'object' ? message.payload : {};
  if (message.type === shareMirrorProtocol.frames.inputIntent) {
    applyShareInputIntent(payload);
    return;
  }
  if (message.type === shareMirrorProtocol.frames.domKeyframeRequest) {
    const reason = shareReplayKeyframeReason(payload.reason || message.reason || 'join', 'join');
    sharePublishDomKeyframe(reason);
    return;
  }
  if (message.type === 'ui-state') {
    void applyShareUiState(payload);
    return;
  }
  const finishRemoteApply = beginShareRemoteUiApply();
  try {
    if (message.type === 'layout') {
      const next = layoutFromParam(payload.layout || '', payload.tabs || '', {preserveMissingFileExplorer: true});
      if (next) applyLayoutSlots(next, {prune: false, preserveMissingFileExplorer: true});
      return;
    }
    if (message.type === 'active-tab') {
      activatePaneTab(payload.slot, payload.item);
      return;
    }
    if (message.type === 'focus') {
      setFocusedPanelItem(payload.item);
      return;
    }
    if (message.type === 'finder-mode' && payload.session) {
      switchFileExplorerChangesSession(payload.session);
      return;
    }
    if (message.type === 'menu') {
      return;
    }
    if (message.type === 'viewport') {
      applyShareViewportState(payload);
      return;
    }
    if (message.type === 'appearance') {
      applyShareAppearanceState(payload);
      return;
    }
    if (message.type === 'scroll') {
      applyShareScrollState(payload);
      return;
    }
    if (message.type === 'file-version') {
      void applyShareFileVersion(payload);
      return;
    }
    if (message.type === 'geometry-digest') {
      applyShareGeometryDigest(payload);
      return;
    }
    if (message.type === 'share-status') {
      mergeShareStatusPayload(payload);
      return;
    }
    if (message.type === 'popup-layer') {
      applySharePopupLayer(payload, message.sender || '');
      return;
    }
    if (message.type === 'pointer') {
      renderSharePointerGhost({...payload, sender: message.sender || payload.sender || ''});
      return;
    }
    if (message.type === 'host-resize' || message.type === shareMirrorProtocol.frames.terminalHostResize) {
      updateShareHostTerminalSize(payload.session, payload.rows, payload.cols);
    }
  } finally {
    finishRemoteApply();
  }
}
