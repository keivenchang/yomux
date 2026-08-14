// The ONE topbar health indicator: one state object, one DOM node, one insertion point
// (.topbar-right-tools). Two independent signals feed it:
//   * this browser cannot reach the server at all (consecutive apiFetch transport failures), and
//   * the server told us, over the pushed `backend_health_changed` event on the existing `core`
//     client-event channel, that one of its own services is down or degraded.
// It is push-fed on purpose. Nothing here polls /api/system-status: the whole point is that a dead
// service becomes visible without the System panel being open.
const backendHealthFailureThreshold = 3;
// The observer already debounces its own transitions; this debounces the CLEAR in the browser. One
// healthy revision after an outage is a sample, not a recovery, so the warning survives it.
const backendHealthRecoveryRevisions = 2;
// Bounded twice: we retain at most this many degraded resources, and we NAME at most one of them.
// Six service names in a topbar chip is a wall nobody reads; "and 2 more" is the readable form.
const backendHealthResourceLimit = 8;
const backendHealthNamedResourceLimit = 1;
// The states the observer publishes (yolomux_lib/backend_health/store.py BACKEND_HEALTH_STATES).
// `starting` and `ready` are not failures and must never raise the indicator, or every page load
// during startup shows a warning. Everything else is a failure at one of two severities.
const backendHealthDownStates = Object.freeze(['down', 'upgrade_required']);
const backendHealthDegradedStates = Object.freeze(['degraded', 'backoff', 'unknown']);
// Precedence, highest first. A browser talking to nothing must not be described as "one service is
// slow", and a service outage must not be hidden because the last request happened to succeed.
const backendHealthSeverityRank = Object.freeze({'': 0, degraded: 1, down: 2, unresponsive: 3});

const backendHealthState = {
  consecutiveFailures: 0,
  epoch: '',
  revision: -1,
  serviceSeverity: '',
  resources: [],
  resourceCount: 0,
  healthyRevisions: 0,
};

function backendHealthStateSeverity(state) {
  const value = String(state ?? '');
  if (backendHealthDownStates.includes(value)) return 'down';
  if (backendHealthDegradedStates.includes(value)) return 'degraded';
  return '';
}

function backendHealthResourcesFromPayload(resources) {
  if (!Array.isArray(resources)) return [];
  const failing = [];
  for (const entry of resources) {
    if (!entry || typeof entry !== 'object') continue;
    const severity = backendHealthStateSeverity(entry.state);
    if (!severity) continue;
    failing.push({
      id: String(entry.id ?? ''),
      // The server owns the human name. `id` is a process name (watchd, indexd) and is kept only for
      // diagnostics — it must never reach the rendered text, which is why the renderer reads `label`
      // and falls back to a generic translated noun rather than to `id`.
      label: String(entry.label ?? '').trim(),
      severity,
      reasonCode: String(entry.reason_code ?? ''),
    });
  }
  // Worst first, so the one resource we name is the one that matters most.
  failing.sort((left, right) => backendHealthSeverityRank[right.severity] - backendHealthSeverityRank[left.severity]);
  return failing;
}

function backendHealthPayloadSeverity(overallState, resources) {
  let severity = backendHealthStateSeverity(overallState);
  for (const resource of resources) {
    if (backendHealthSeverityRank[resource.severity] > backendHealthSeverityRank[severity]) severity = resource.severity;
  }
  return severity;
}

// Apply one `backend_health_changed` payload: {epoch, revision, overall_state, degraded_resources}.
// Returns false for a stale or replayed revision so a reconnect replay cannot walk the state backwards.
function applyBackendHealthPayload(payload = {}) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const revision = Number(source.revision);
  if (!Number.isFinite(revision)) return false;
  const epoch = String(source.epoch ?? '');
  if (epoch === backendHealthState.epoch && revision <= backendHealthState.revision) return false;
  if (epoch !== backendHealthState.epoch) {
    // A new observer epoch restarts the revision counter, so a partial recovery count from the
    // previous epoch means nothing here: start counting healthy revisions again.
    backendHealthState.epoch = epoch;
    backendHealthState.healthyRevisions = 0;
  }
  backendHealthState.revision = revision;
  const resources = backendHealthResourcesFromPayload(source.degraded_resources);
  const severity = backendHealthPayloadSeverity(source.overall_state, resources);
  if (severity) {
    backendHealthState.serviceSeverity = severity;
    backendHealthState.resources = resources.slice(0, backendHealthResourceLimit);
    backendHealthState.resourceCount = resources.length;
    backendHealthState.healthyRevisions = 0;
  } else {
    backendHealthState.healthyRevisions += 1;
    if (backendHealthState.healthyRevisions >= backendHealthRecoveryRevisions) {
      backendHealthState.serviceSeverity = '';
      backendHealthState.resources = [];
      backendHealthState.resourceCount = 0;
    }
  }
  syncBackendHealthIndicator();
  return true;
}

// The rendered model, or null when there is nothing to show. This is where precedence lives:
// `unresponsive` outranks `down` outranks `degraded`, and it is decided in exactly one place.
function backendHealthIndicatorModel() {
  if (backendHealthState.consecutiveFailures >= backendHealthFailureThreshold) {
    return {
      severity: 'unresponsive',
      text: `${t('common.requestFailed')} · ${t('tmuxWall.status.disconnectedRetrying')}`,
      reasonCode: '',
    };
  }
  const severity = backendHealthState.serviceSeverity;
  if (severity !== 'down' && severity !== 'degraded') return null;
  const named = backendHealthState.resources.find(resource => resource.label) || null;
  const label = named ? named.label : t('backendHealth.service');
  const count = Math.max(0, backendHealthState.resourceCount - backendHealthNamedResourceLimit);
  let text;
  if (severity === 'down') {
    text = count > 0 ? t('backendHealth.down.multiple', {label, count}) : t('backendHealth.down.single', {label});
  } else {
    text = count > 0 ? t('backendHealth.degraded.multiple', {label, count}) : t('backendHealth.degraded.single', {label});
  }
  return {severity, text, reasonCode: named ? named.reasonCode : ''};
}

// The System view is the YO!stats/Debug pane's `system` sub-tab. This is the same pair of calls its
// own tab button makes (85_debug_panel.js bindDebugPanel -> setDebugSubTab), not a second route in.
async function openBackendHealthDetails() {
  await selectSession(debugPaneItemId, {userInitiated: true});
  setDebugSubTab('system');
  return true;
}

function backendHealthIndicatorHost() {
  if (!topbar) return null;
  return topbar.querySelector('.topbar-right-tools') || topbar;
}

// One glyph for the fixed icon shell. It is a marker, never the message: the severity is carried by
// the WORDS in the role=status label, so a monochrome or forced-colours theme still reads correctly.
function backendHealthIndicatorGlyph(severity) {
  return severity ? '⚠' : '';
}

// The permanently mounted, fixed-size backend-health control. It is built once by
// createTopbarRightTools() and NEVER inserted or removed on a health transition: a change only
// repaints it and rewrites its accessible sentence, so the topbar, #grid, and every xterm keep the
// same geometry before, during, and after a warning. The full localized sentence lives in the
// role=status live region AND on the control's aria-label/title; the whole control is the System
// Details route, so there is no separate variable-width Details button to grow the row.
function createBackendHealthIndicator() {
  const indicator = makeButton({
    id: 'backendHealthIndicator',
    className: 'backend-health-indicator',
    onClick: () => {
      openBackendHealthDetails().catch(error => console.warn('backend health details failed to open', error));
    },
  });
  const icon = document.createElement('span');
  icon.className = 'backend-health-indicator-icon';
  icon.setAttribute('aria-hidden', 'true');
  const message = document.createElement('span');
  message.className = 'backend-health-indicator-text';
  message.setAttribute('role', 'status');
  message.setAttribute('aria-live', 'polite');
  indicator.append(icon, message);
  applyBackendHealthIndicatorState(indicator, backendHealthIndicatorModel());
  return indicator;
}

// Push one model (or null when healthy) into the existing control. Healthy is a state of the SAME
// node -- an inert, empty, transparent icon shell that still occupies its fixed slot -- not the
// node's absence, so recovery does not remove layout content.
function applyBackendHealthIndicatorState(indicator, model) {
  const severity = model ? model.severity : '';
  indicator.dataset.backendHealth = severity;
  if (model && model.reasonCode) indicator.dataset.backendHealthReason = model.reasonCode;
  else delete indicator.dataset.backendHealthReason;
  indicator.querySelector('.backend-health-indicator-icon').textContent = backendHealthIndicatorGlyph(severity);
  // The severity is carried by the WORDS ("is not running" vs "is degraded"), not by the colour the
  // data-backend-health token selects, so the warning survives a monochrome or high-contrast theme.
  indicator.querySelector('.backend-health-indicator-text').textContent = model ? model.text : '';
  if (model) {
    indicator.disabled = false;
    // The accessible NAME describes what activating the control does (open the System details); the
    // live-region text above announces the STATE sentence, and the tooltip carries the full sentence.
    indicator.setAttribute('aria-label', t('backendHealth.detailsAria'));
    indicator.setAttribute('title', model.text);
    indicator.removeAttribute('aria-hidden');
  } else {
    indicator.disabled = true;
    indicator.removeAttribute('aria-label');
    indicator.removeAttribute('title');
    indicator.setAttribute('aria-hidden', 'true');
  }
}

function syncBackendHealthIndicator(host = backendHealthIndicatorHost()) {
  const model = backendHealthIndicatorModel();
  // Look the existing node up across the whole topbar, not just the host: the control is normally
  // built into .topbar-right-tools, but if that host is torn down and rebuilt -- fallback-mounting a
  // control in the topbar while a detached one waits to be re-inserted -- there must still be exactly
  // ONE. Keep the first in document order and collapse any extra so a rebuild cannot leave two owners.
  const scope = topbar || host || document;
  const existing = Array.from(scope.querySelectorAll('[data-backend-health]'));
  let indicator = existing[0] || null;
  for (let index = 1; index < existing.length; index += 1) existing[index].remove();
  if (!indicator) {
    // The control is missing only before the right-tools builder has run, or in the fallback where
    // .topbar-right-tools was removed at runtime. Mount one and keep updating it in place thereafter.
    if (!host) return null;
    indicator = createBackendHealthIndicator();
    host.prepend(indicator);
  }
  applyBackendHealthIndicatorState(indicator, model);
  return indicator;
}

function noteBackendHealthFailure() {
  backendHealthState.consecutiveFailures += 1;
  syncBackendHealthIndicator();
}

function noteBackendHealthSuccess() {
  if (!backendHealthState.consecutiveFailures) return;
  backendHealthState.consecutiveFailures = 0;
  syncBackendHealthIndicator();
}

function applyApiRequestIdHeader(url, requestOptions) {
  const endpoint = jsDebugEndpointText(url);
  if (!endpoint.startsWith('/api/')) return '';
  const requestId = `r-web-${Date.now().toString(36)}-${(++apiDebugRequestSequence).toString(36)}`;
  const validRequestId = value => /^r-[A-Za-z0-9._-]{1,120}$/.test(String(value || ''));
  if (typeof Headers === 'function') {
    const headers = new Headers(requestOptions.headers || {});
    if (!validRequestId(headers.get('X-YOLOmux-Request-ID'))) headers.set('X-YOLOmux-Request-ID', requestId);
    requestOptions.headers = headers;
    return String(headers.get('X-YOLOmux-Request-ID') || requestId);
  }
  const headers = {...(requestOptions.headers || {})};
  const existingKey = Object.keys(headers).find(key => key.toLowerCase() === 'x-yolomux-request-id');
  if (!existingKey || !validRequestId(headers[existingKey])) headers[existingKey || 'X-YOLOmux-Request-ID'] = requestId;
  requestOptions.headers = headers;
  return String(headers[existingKey || 'X-YOLOmux-Request-ID'] || requestId);
}

const apiFetchDefaultDeadlineMs = 15000;
const apiFetchLongOperationDeadlineMs = 300000;

function apiFetchDeadlineMs(url, options = {}) {
  if (Object.prototype.hasOwnProperty.call(options, 'deadlineMs')) {
    const override = Number(options.deadlineMs);
    return Number.isFinite(override) && override > 0 ? override : apiFetchDefaultDeadlineMs;
  }
  if (Object.prototype.hasOwnProperty.call(options, 'timeoutMs')) {
    const override = Number(options.timeoutMs);
    return Number.isFinite(override) && override > 0 ? override : apiFetchDefaultDeadlineMs;
  }
  if (typeof FormData === 'function' && options.body instanceof FormData) return apiFetchLongOperationDeadlineMs;
  const path = String(url || '').split('?', 1)[0];
  if (path.endsWith('/api/self-update')) return apiFetchLongOperationDeadlineMs;
  return apiFetchDefaultDeadlineMs;
}

function apiFetchDeadlineError(deadlineMs, subject = 'request') {
  const seconds = Math.round(deadlineMs / 1000);
  const message = `deadline_expired: ${String(subject || 'request')} exceeded its ${seconds}s deadline`;
  const error = new Error(message);
  error.name = 'ApiFetchDeadlineError';
  error.code = 'deadline_expired';
  error.status = 504;
  error.statusText = 'Gateway Timeout';
  error.payload = {error: message, reason_code: error.code, timeout_ms: deadlineMs};
  return error;
}

function apiFetchResponseWithDeadline(response, deadlineState) {
  if (!response || response.body === null) {
    deadlineState.onConsumed?.();
    deadlineState.cleanup();
    return response;
  }
  let consumed = false;
  const noteConsumed = () => {
    if (consumed) return;
    consumed = true;
    deadlineState.onConsumed?.();
  };
  const wrappedMethods = new Map();
  for (const name of ['arrayBuffer', 'blob', 'formData', 'json', 'text']) {
    const consume = response[name];
    if (typeof consume !== 'function') continue;
    wrappedMethods.set(name, async (...args) => {
      try {
        return await consume.apply(response, args);
      } catch (error) {
        const timeoutError = deadlineState.timeoutError();
        if (timeoutError) {
          deadlineState.noteTimeout();
          throw timeoutError;
        }
        throw error;
      } finally {
        noteConsumed();
        deadlineState.cleanup();
      }
    });
  }
  return new Proxy(response, {
    get(target, property) {
      if (wrappedMethods.has(property)) return wrappedMethods.get(property);
      const value = Reflect.get(target, property, target);
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });
}

async function apiFetch(url, options = {}, internalOptions = {}) {
  const transportLifecycle = pageTransportLifecycle;
  const transportToken = transportLifecycle.begin();
  const requestOptions = {...options};
  const abortOnTimeout = Object.prototype.hasOwnProperty.call(requestOptions, 'timeoutMs');
  const deadlineMs = apiFetchDeadlineMs(url, requestOptions);
  delete requestOptions.deadlineMs;
  delete requestOptions.timeoutMs;
  if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
  const recordDebug = internalOptions.recordDebug !== false;
  const quietStatuses = Array.isArray(internalOptions.quietStatuses)
    ? new Set(internalOptions.quietStatuses.map(Number))
    : null;
  const diagnosticProvenance = ['controlled_probe', 'confirmed_real'].includes(internalOptions.provenance)
    ? internalOptions.provenance
    : '';
  const startedAt = recordDebug ? jsDebugPerformanceNow() : 0;
  const method = recordDebug ? jsDebugRequestMethod(requestOptions) : '';
  const requestBytes = recordDebug ? jsDebugRequestBytes(url, requestOptions) : 0;
  const requestId = recordDebug ? applyApiRequestIdHeader(url, requestOptions) : '';
  if (recordDebug) notePageLoadApiStarted(startedAt);
  const upstreamSignal = requestOptions.signal || null;
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  let upstreamAbort = null;
  if (controller) {
    upstreamAbort = () => controller.abort(upstreamSignal?.reason);
    if (upstreamSignal?.aborted) upstreamAbort();
    else upstreamSignal?.addEventListener?.('abort', upstreamAbort, {once: true});
    requestOptions.signal = controller.signal;
  }
  let timeoutId = null;
  let timeoutError = null;
  let timeoutNoted = false;
  const cleanup = () => {
    if (timeoutId !== null) {
      clearTimeout(timeoutId);
      timeoutId = null;
    }
    if (upstreamAbort) upstreamSignal?.removeEventListener?.('abort', upstreamAbort);
  };
  const noteTimeout = () => {
    if (timeoutNoted) return;
    timeoutNoted = true;
    noteBackendHealthFailure();
  };
  let response;
  try {
    const requestPromise = fetch(url, requestOptions).catch(error => {
      if (timeoutError) throw timeoutError;
      throw error;
    });
    const deadlinePromise = new Promise((_, reject) => {
      timeoutId = setTimeout(() => {
        timeoutId = null;
        timeoutError = abortOnTimeout
          ? new DOMException('The operation was aborted.', 'AbortError')
          : apiFetchDeadlineError(deadlineMs);
        reject(timeoutError);
        controller?.abort(timeoutError);
        if (upstreamAbort) upstreamSignal?.removeEventListener?.('abort', upstreamAbort);
      }, deadlineMs);
    });
    response = await Promise.race([requestPromise, deadlinePromise]);
  } catch (error) {
    const retirementReason = timeoutError ? '' : transportLifecycle.reasonSince(transportToken);
    if (timeoutError) noteTimeout();
    else if (!retirementReason) noteBackendHealthFailure();
    if (recordDebug) {
      notePageLoadApiCompleted();
      recordApiDebugEvent(url, method, startedAt, {
        error,
        ...(retirementReason ? {deliveryOutcome: 'retired', reason: retirementReason} : {deliveryOutcome: 'failed'}),
        requestBytes,
        requestId,
        provenance: diagnosticProvenance,
      });
    }
    cleanup();
    throw error;
  }
  transportLifecycle.noteDelivery(transportToken); noteBackendHealthSuccess();
  if (recordDebug) notePageLoadApiCompleted();
  let debugEvent = null;
  // An expected status (a controlled probe's own verdict) is not an API failure; do not record it.
  const suppressExpectedStatus = Boolean(quietStatuses && quietStatuses.has(Number(response.status)));
  if (recordDebug && !suppressExpectedStatus) {
    debugEvent = recordApiDebugEvent(url, method, startedAt, {
      status: response.status,
      ok: response.ok,
      requestBytes,
      requestId,
      provenance: diagnosticProvenance,
    });
    recordApiDebugResponseBytes(debugEvent, response);
    noteApiDebugHeaders(debugEvent, url, startedAt);
  }
  if (response.status === 401 && internalOptions.returnUnauthorizedResponse !== true) {
    if (registeredCommandRouteForRequest(url, requestOptions)?.contractClass === 'background') {
      cleanup();
      const error = new Error('authentication required');
      error.status = 401;
      throw error;
    }
    await redirectToLogin(response);
    cleanup();
    throw new Error('authentication required');
  }
  return apiFetchResponseWithDeadline(response, {
    cleanup,
    noteTimeout,
    timeoutError: () => timeoutError,
    onConsumed: () => {
      if (recordDebug) noteApiDebugResponseConsumed(debugEvent, url, startedAt);
    },
  });
}

class ApiPendingResponse extends Error {
  constructor({status = 202, ticket = null, key = '', epoch = '', request = {}, operation = {}, retryAfterSeconds = 0} = {}) {
    super('request_queued');
    this.name = 'ApiPendingResponse';
    this.pending = true;
    this.reason = 'request_queued';
    this.status = Number(status) || 202;
    this.ticket = ticket == null ? null : String(ticket);
    this.request = request && typeof request === 'object' ? {...request} : {};
    this.operation = operation && typeof operation === 'object' ? {...operation} : {};
    this.operationId = String(this.operation.id || '');
    this.key = String(key || this.operationId || '');
    this.epoch = String(epoch || this.operation?.cursor?.epoch || '');
    this.retryAfterSeconds = Number(retryAfterSeconds) || 0;
  }
}

function isApiPendingResponse(value) {
  return value instanceof ApiPendingResponse
    || Boolean(value && value.name === 'ApiPendingResponse' && value.pending === true && value.reason === 'request_queued');
}

function apiGenerationReadyKey(value) {
  return String(value || '').replace(/^storaged\.products:/, '');
}

function apiGenerationReadyMatchesKey(pendingKey, payload = {}) {
  const expected = apiGenerationReadyKey(pendingKey);
  const received = apiGenerationReadyKey(payload?.key);
  return Boolean(expected && received && expected === received);
}

function apiPendingResponseFromPayload(payload, options = {}) {
  const responseStatus = Number(options.status) || 0;
  const lifecycleState = String(payload?.state || '').trim().toLowerCase();
  const operation = payload?.operation && typeof payload.operation === 'object' ? payload.operation : {};
  const operationId = String(operation.id || '').trim();
  if (responseStatus === 202 && lifecycleState === 'queued' && operationId) {
    return new ApiPendingResponse({
      status: responseStatus,
      key: operationId,
      epoch: operation?.cursor?.epoch,
      request: payload?.request,
      operation,
    });
  }
  const retryAfterSeconds = Number(payload?.retry_after_seconds) || 0;
  if (responseStatus === 202
      && lifecycleState === 'queued'
      && !operationId
      && String(payload?.status || '').trim().toLowerCase() === 'pending'
      && Number.isInteger(retryAfterSeconds)
      && retryAfterSeconds >= 1
      && retryAfterSeconds <= 60) {
    return new ApiPendingResponse({
      status: responseStatus,
      request: payload?.request,
      retryAfterSeconds,
    });
  }
  const payloadStatus = String(payload?.status || '').trim().toUpperCase();
  if (responseStatus !== 202 && payloadStatus !== 'QUEUED') return null;
  const key = String(payload?.key || '').trim();
  if ((responseStatus && responseStatus !== 202) || payloadStatus !== 'QUEUED' || !key) return null;
  return new ApiPendingResponse({
    status: responseStatus || 202,
    ticket: Object.prototype.hasOwnProperty.call(payload, 'ticket') ? payload.ticket : null,
    key,
    epoch: payload?.epoch,
  });
}

function apiOperationTerminalCursor(payload = {}) {
  const cursor = payload?.operation?.cursor;
  return {
    epoch: String(cursor?.epoch || ''),
    seq: Number(cursor?.seq || 0),
  };
}

function apiOperationTerminalIsNewer(current = null, incoming = {}) {
  const next = apiOperationTerminalCursor(incoming);
  if (!next.epoch || !Number.isSafeInteger(next.seq)) return false;
  if (!current) return next.seq > 0;
  const previous = apiOperationTerminalCursor(current);
  if (previous.epoch !== next.epoch) return false;
  return next.seq > previous.seq;
}

function apiOperationTerminalMatchesRecord(record, payload) {
  const accepted = apiOperationTerminalCursor({operation: {cursor: record?.cursor}});
  const terminal = apiOperationTerminalCursor(payload);
  if (!terminal.epoch || !Number.isSafeInteger(terminal.seq) || terminal.seq <= 0) return false;
  if (accepted.epoch && accepted.epoch !== terminal.epoch) return false;
  return terminal.seq > accepted.seq;
}

function pruneApiOperationReplay() {
  if (apiOperationState.terminal.size <= apiOperationReplayLimit) return 0;
  let removed = 0;
  for (const operationId of apiOperationState.terminal.keys()) {
    if (apiOperationState.terminal.size <= apiOperationReplayLimit) break;
    if (apiOperationState.pending.has(operationId) || apiOperationState.waiters.has(operationId)) continue;
    const record = apiOperationState.records.get(operationId);
    apiOperationState.terminal.delete(operationId);
    if (record?.phase === 'terminal') apiOperationState.records.delete(operationId);
    removed += 1;
  }
  return removed;
}

function operationTerminalAckCursorMatches(left, right) {
  return String(left?.epoch || '') === String(right?.epoch || '')
    && Number(left?.seq || 0) === Number(right?.seq || 0);
}

function scheduleOperationTerminalAckFlush(delayMs = operationTerminalAckDelayMs) {
  if (operationTerminalAckState.timer !== null || operationTerminalAckState.request || !operationTerminalAckState.pending.size) return;
  operationTerminalAckState.timer = setTimeout(() => {
    operationTerminalAckState.timer = null;
    void flushOperationTerminalAcks();
  }, delayMs);
}

function enqueueOperationTerminalAck(operationId, cursor) {
  const id = String(operationId || '');
  const normalizedCursor = {epoch: String(cursor?.epoch || ''), seq: Number(cursor?.seq || 0)};
  if (!id || !normalizedCursor.epoch || !Number.isSafeInteger(normalizedCursor.seq) || normalizedCursor.seq <= 0) return false;
  operationTerminalAckState.pending.set(id, normalizedCursor);
  scheduleOperationTerminalAckFlush();
  return true;
}

async function flushOperationTerminalAcks() {
  if (operationTerminalAckState.request || !operationTerminalAckState.pending.size) return false;
  const batch = Array.from(operationTerminalAckState.pending, ([id, cursor]) => ({id, cursor: {...cursor}}))
    .slice(0, operationTerminalAckLimit);
  const request = apiFetchJsonQuiet('/api/operations/ack', {
    method: 'POST',
    keepalive: true,
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({acks: batch}),
  });
  operationTerminalAckState.request = request;
  try {
    const response = await request;
    const terminalIds = new Set([
      ...(Array.isArray(response?.acknowledged) ? response.acknowledged : []),
      ...(Array.isArray(response?.ignored) ? response.ignored : []),
    ].map(String));
    for (const item of batch) {
      const current = operationTerminalAckState.pending.get(item.id);
      if (terminalIds.has(item.id) && operationTerminalAckCursorMatches(current, item.cursor)) {
        operationTerminalAckState.pending.delete(item.id);
      }
    }
  } catch (_) {
    // Exact replay remains durable while the acknowledgement is retried.
  } finally {
    if (operationTerminalAckState.request === request) operationTerminalAckState.request = null;
    if (operationTerminalAckState.pending.size) {
      scheduleOperationTerminalAckFlush(batch.length >= operationTerminalAckLimit ? operationTerminalAckDelayMs : operationTerminalAckRetryMs);
    }
  }
  return true;
}

function completeApiOperationRecord(record, payload) {
  if (!record || record.phase !== 'accepted' || !apiOperationTerminalMatchesRecord(record, payload)) return false;
  const result = payload.result;
  record.phase = 'terminal';
  record.cursor = {...payload.operation.cursor};
  record.source?.close?.();
  record.source = null;
  apiOperationState.pending.delete(record.id);
  settleApiOperationWaiters(record.id, payload);
  recordApiOperationWait(record, result);
  const sessionLifecycleCurrent = !record.sessionLifecycleToken
    || typeof tmuxSessionLifecycleTokenIsCurrent !== 'function'
    || tmuxSessionLifecycleTokenIsCurrent(record.sessionLifecycleToken);
  if (sessionLifecycleCurrent && typeof handleApiOperationTerminalResult === 'function') {
    record.handlerInvocations += 1;
    handleApiOperationTerminalResult(record, result);
  }
  window.dispatchEvent(new CustomEvent('yolomux:operation-terminal', {detail: payload}));
  enqueueOperationTerminalAck(record.id, record.cursor);
  return true;
}

function applyApiOperationTerminal(payload = {}) {
  const operationId = String(payload?.operation?.id || '');
  const result = payload?.result;
  if (!operationId || !result || typeof result !== 'object') return false;
  const record = apiOperationState.records.get(operationId);
  if (record && record.phase !== 'accepted') return false;
  if (record && !apiOperationTerminalMatchesRecord(record, payload)) return false;
  const prior = apiOperationState.terminal.get(operationId);
  if (!apiOperationTerminalIsNewer(prior, payload)) return false;
  apiOperationState.terminal.set(operationId, payload);
  if (!record) {
    pruneApiOperationReplay();
    return true;
  }
  const completed = completeApiOperationRecord(record, payload);
  pruneApiOperationReplay();
  return completed;
}

function apiOperationTerminalError(record, payload = {}) {
  const result = payload?.result && typeof payload.result === 'object' ? payload.result : {};
  const error = new Error(userMessageText(result, 'filesystem operation failed'));
  error.name = 'ApiOperationTerminalError';
  error.status = Number(payload?.status || result?.status || result?.error?.status || 0);
  error.code = String(result?.error?.code || result?.code || result?.user_message?.key || 'operation_failed');
  error.operationId = String(record?.id || payload?.operation?.id || '');
  error.result = result;
  return error;
}

function apiOperationTerminalData(record, payload = {}) {
  const result = payload?.result && typeof payload.result === 'object' ? payload.result : {};
  if (result.state === 'ready' && result.data && typeof result.data === 'object') return result.data;
  throw apiOperationTerminalError(record, payload);
}

function recordApiOperationTerminalFailure(record, error, expected = {}) {
  if (error?.name !== 'ApiOperationTerminalError' || !expected.url) return;
  const status = Number(error.status);
  // An expected terminal status (a controlled probe's own verdict, e.g. a deletion-confirmation 404) is
  // not an API failure; suppress it. Any other status, and non-status transport errors, still record.
  if (Array.isArray(expected.quietStatuses)
    && Number.isSafeInteger(status)
    && expected.quietStatuses.map(Number).includes(status)) return;
  recordApiDebugEvent(expected.url, expected.method || 'GET', record.acceptedAt, {
    ...(Number.isSafeInteger(status) && status >= 100 && status <= 599 ? {status, ok: false} : {error}),
    requestId: record.request?.id,
    source: error.result?.error?.origin,
  });
}

function settleApiOperationWaiters(operationId, payload) {
  const waiters = apiOperationState.waiters.get(operationId);
  if (!waiters) return;
  apiOperationState.waiters.delete(operationId);
  for (const waiter of waiters) {
    waiter.cleanup();
    try {
      waiter.resolve(apiOperationTerminalData(waiter.record, payload));
    } catch (error) {
      waiter.reject(error);
    }
  }
}

function detachApiOperationWaiter(record, waiter, error) {
  const waiters = apiOperationState.waiters.get(record.id);
  if (!waiters?.delete(waiter)) return false;
  waiter.cleanup();
  if (!waiters.size) {
    apiOperationState.waiters.delete(record.id);
  }
  waiter.reject(error);
  return true;
}

function waitForApiOperationResult(pending, expected = {}) {
  const record = registerApiOperationReceipt(pending);
  if (!record) return Promise.reject(new Error('operation receipt is missing an id'));
  const expectedKind = String(expected.kind || '');
  const expectedOperation = String(expected.operation || '');
  if ((expectedKind && record.kind !== expectedKind)
      || (expectedOperation && String(record.context?.operation || '') !== expectedOperation)) {
    return Promise.reject(new Error(`unexpected operation receipt ${record.kind}:${record.context?.operation || ''}`));
  }
  const retained = apiOperationState.terminal.get(record.id);
  if (record.phase === 'terminal' && retained) {
    try {
      return Promise.resolve(apiOperationTerminalData(record, retained));
    } catch (error) {
      recordApiOperationTerminalFailure(record, error, expected);
      return Promise.reject(error);
    }
  }
  const deadlineMs = Number.isFinite(Number(expected.deadlineMs)) && Number(expected.deadlineMs) > 0
    ? Number(expected.deadlineMs)
    : apiFetchDefaultDeadlineMs;
  const signal = expected.signal || null;
  return new Promise((resolve, reject) => {
    const waiters = apiOperationState.waiters.get(record.id) || new Set();
    const waiter = {
      record,
      resolve,
      reject,
      timer: null,
      abort: null,
      cleanup() {
        if (this.timer !== null) clearTimeout(this.timer);
        this.timer = null;
        if (this.abort) signal?.removeEventListener?.('abort', this.abort);
        this.abort = null;
      },
    };
    const elapsed = Math.max(0, performanceNow() - record.acceptedAt);
    const remaining = Math.max(0, deadlineMs - elapsed);
    waiters.add(waiter);
    apiOperationState.waiters.set(record.id, waiters);
    waiter.timer = setTimeout(() => {
      const error = apiFetchDeadlineError(deadlineMs);
      if (expected.url) {
        recordApiDebugEvent(expected.url, expected.method || 'GET', record.acceptedAt, {
          error,
          requestId: record.request?.id,
        });
      }
      detachApiOperationWaiter(record, waiter, error);
    }, remaining);
    if (signal) {
      waiter.abort = () => detachApiOperationWaiter(
        record,
        waiter,
        signal.reason || new DOMException('The operation was aborted.', 'AbortError'),
      );
      if (signal.aborted) {
        waiter.abort();
        return;
      }
      signal.addEventListener?.('abort', waiter.abort, {once: true});
    }
    const raced = apiOperationState.terminal.get(record.id);
    if (raced && record.phase === 'accepted') completeApiOperationRecord(record, raced);
  }).catch(error => {
    recordApiOperationTerminalFailure(record, error, expected);
    throw error;
  });
}

function startApiOperationTransport(record) {
  if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
  return null;
}

function registerApiOperationReceipt(pending) {
  const operation = pending?.operation;
  const operationId = String(operation?.id || '');
  if (!operationId) return null;
  const existing = apiOperationState.records.get(operationId);
  if (existing) return existing;
  const context = operation.context && typeof operation.context === 'object' ? {...operation.context} : {};
  const record = {
    id: operationId,
    request: {...(pending.request || {})},
    kind: String(operation.kind || ''),
    context,
    statusUrl: String(operation.status_url || ''),
    eventsUrl: String(operation.events_url || ''),
    cursor: {...(operation.cursor || {})},
    source: null,
    acceptedAt: performanceNow(),
    journeyId: newClientJourneyId('operation'),
    handlerInvocations: 0,
    phase: 'accepted',
    sessionLifecycleToken: context.session && typeof tmuxSessionLifecycleToken === 'function'
      ? tmuxSessionLifecycleToken(context.session)
      : null,
  };
  apiOperationState.records.set(operationId, record);
  apiOperationState.pending.set(operationId, record);
  const terminal = apiOperationState.terminal.get(operationId);
  if (terminal && apiOperationTerminalMatchesRecord(record, terminal)) {
    apiOperationState.terminal.delete(operationId);
    apiOperationState.terminal.set(operationId, terminal);
    completeApiOperationRecord(record, terminal);
    pruneApiOperationReplay();
  } else {
    if (terminal) apiOperationState.terminal.delete(operationId);
    startApiOperationTransport(record);
  }
  return record;
}

function apiPendingResponseFromNestedEnvelope(envelope = {}) {
  const payload = envelope?.payload && typeof envelope.payload === 'object'
    ? envelope.payload
    : (envelope?.data && typeof envelope.data === 'object' ? envelope.data : envelope);
  return apiPendingResponseFromPayload(payload, {status: envelope?.status});
}

async function apiJsonResponse(response) {
  const payload = await response.json().catch(error => {
    if (error?.code === 'deadline_expired') throw error;
    return {};
  });
  const pending = apiPendingResponseFromPayload(payload, {status: response?.status});
  if (pending) {
    registerApiOperationReceipt(pending);
    throw pending;
  }
  if (response.status === 202 || String(payload?.state || '').toLowerCase() === 'queued') {
    const error = new Error('invalid_response_contract');
    error.code = 'invalid_response_contract';
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  if (!response.ok) {
    const error = new Error(userMessageText(payload, response.statusText || `HTTP ${response.status}`));
    error.status = response.status;
    error.statusText = response.statusText || '';
    error.payload = payload || {};
    error.response = response;
    throw error;
  }
  const lifecycleState = String(payload?.state || '').toLowerCase();
  const canonicalRequestId = String(payload?.request?.id || '').trim();
  if (lifecycleState === 'failed') {
    const error = new Error('invalid_response_contract');
    error.code = 'invalid_response_contract';
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  if (lifecycleState === 'ready' && canonicalRequestId) {
    if (!payload?.data || typeof payload.data !== 'object') {
      const error = new Error('invalid_response_contract');
      error.code = 'invalid_response_contract';
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload.data;
  }
  return payload;
}

async function apiFetchJson(url, options = {}, internalOptions = {}) {
  const lifecycle = typeof tmuxSessionLifecycleRequestLease === 'function'
    ? tmuxSessionLifecycleRequestLease(url, options)
    : {session: '', lease: null, blocked: false};
  if (lifecycle.blocked) throw tmuxSessionLifecycleStaleRequestError(lifecycle.session);
  const lifecycleToken = lifecycle.lease?.token || null;
  let leaseReleased = false;
  const releaseLease = () => {
    if (leaseReleased) return;
    leaseReleased = true;
    lifecycle.lease?.release?.();
  };
  try {
    const result = await apiJsonResponse(await apiFetch(url, options, internalOptions));
    if (lifecycleToken && !tmuxSessionLifecycleTokenIsCurrent(lifecycleToken)) {
      throw tmuxSessionLifecycleStaleRequestError(lifecycle.session);
    }
    return result;
  } catch (error) {
    if (!isApiPendingResponse(error) || error?.operation?.kind !== 'filesystem_operation') throw error;
    releaseLease();
    const result = await waitForApiOperationResult(error, {
      kind: 'filesystem_operation',
      operation: String(error?.operation?.context?.operation || ''),
      deadlineMs: apiFetchDeadlineMs(url, options),
      signal: options.signal,
      url,
      method: jsDebugRequestMethod(options),
      quietStatuses: internalOptions.quietStatuses,
    });
    if (lifecycleToken && !tmuxSessionLifecycleTokenIsCurrent(lifecycleToken)) {
      throw tmuxSessionLifecycleStaleRequestError(lifecycle.session);
    }
    return result;
  } finally {
    releaseLease();
  }
}

async function apiFetchJsonQuiet(url, options = {}, phaseTimings = null) {
  const fetchStartedAt = performanceNow();
  let response;
  try {
    response = await apiFetch(url, options, {recordDebug: false});
  } finally {
    if (phaseTimings && typeof phaseTimings === 'object') phaseTimings.fetchMs = performanceNow() - fetchStartedAt;
  }
  const parseStartedAt = performanceNow();
  try {
    return await apiJsonResponse(response);
  } finally {
    if (phaseTimings && typeof phaseTimings === 'object') phaseTimings.parseMs = performanceNow() - parseStartedAt;
  }
}

function messageDescriptorText(descriptor, fallback = '') {
  const value = descriptor && typeof descriptor === 'object' ? descriptor : {};
  const key = String(value.key || '').trim();
  if (key) {
    const template = i18nResolve(key);
    if (template !== null) {
      const rawParams = value.params && typeof value.params === 'object' ? value.params : {};
      const params = Object.fromEntries(Object.entries(rawParams).map(([name, param]) => [
        name,
        param && typeof param === 'object' && ('key' in param || 'fallback' in param)
          ? messageDescriptorText(param)
          : param,
      ]));
      return i18nInterpolate(template, params);
    }
  }
  return String(value.fallback || fallback || '');
}

function messageFieldDescriptor(value, field = 'message') {
  const source = value && typeof value === 'object' ? value : {};
  const name = String(field || 'message');
  const params = source[`${name}_params`];
  return {
    key: String(source[`${name}_key`] || ''),
    params: params && typeof params === 'object' ? params : {},
    fallback: String(source[name] || ''),
  };
}

function structuredMessageText(value, field = 'message', fallback = '') {
  return messageDescriptorText(messageFieldDescriptor(value, field), fallback);
}

function structuredMessageSnapshot(value, field = 'message') {
  const descriptor = messageFieldDescriptor(value, field);
  const name = String(field || 'message');
  return {
    [name]: descriptor.fallback,
    [`${name}_key`]: descriptor.key,
    [`${name}_params`]: {...descriptor.params},
  };
}

function userMessageText(value, fallback = '') {
  const source = value && typeof value === 'object' ? value : {};
  const payload = source.payload && typeof source.payload === 'object' ? source.payload : source;
  const canonicalError = payload.error && typeof payload.error === 'object'
    ? payload.error
    : (payload.code && payload.message && typeof payload.message === 'object' ? payload : null);
  const descriptor = canonicalError?.message && typeof canonicalError.message === 'object'
    ? canonicalError.message
    : (payload.user_message && typeof payload.user_message === 'object' ? payload.user_message : {});
  const rawError = typeof payload.error === 'string' ? payload.error : '';
  return messageDescriptorText(descriptor, rawError || source.message || fallback || '');
}

function userMessageSnapshot(value, fallback = '') {
  const source = value && typeof value === 'object' ? value : {};
  const payload = source.payload && typeof source.payload === 'object' ? source.payload : source;
  const canonicalError = payload.error && typeof payload.error === 'object'
    ? payload.error
    : (payload.code && payload.message && typeof payload.message === 'object' ? payload : null);
  const descriptor = canonicalError?.message && typeof canonicalError.message === 'object'
    ? canonicalError.message
    : (payload.user_message && typeof payload.user_message === 'object' ? payload.user_message : {});
  const fallbackDescriptor = fallback && typeof fallback === 'object' ? fallback : {};
  const fallbackText = typeof fallback === 'object' ? String(fallbackDescriptor.fallback || '') : String(fallback || '');
  const key = String(descriptor.key || fallbackDescriptor.key || '');
  const rawParams = descriptor.key ? descriptor.params : fallbackDescriptor.params;
  const params = rawParams && typeof rawParams === 'object' ? rawParams : {};
  const sourceText = typeof value === 'string' || typeof value === 'number' ? String(value) : '';
  const rawError = typeof payload.error === 'string' ? payload.error : '';
  const rawFallback = String(
    rawError
    || descriptor.fallback
    || source.message
    || sourceText
    || fallbackText
    || '',
  );
  return {
    error: rawFallback,
    user_message: {
      key,
      params: {...params},
      fallback: String(descriptor.fallback || rawFallback),
    },
  };
}

function worktreeDisplayText(worktree) {
  const value = worktree && typeof worktree === 'object' ? worktree : {};
  const name = String(value.name || value.path || '');
  const root = String(value.parent_root || '');
  return root ? t('popover.worktreeOf', {name, root}) : name;
}

function clientPushCanSupplyData() {
  return Boolean(clientEventTransportState.source && location.protocol !== 'file:');
}

function clientPushConnectedForData() {
  return clientPushCanSupplyData() && clientEventTransportState.connected === true;
}

function loginRedirectUrlForCurrentLocation() {
  const nextPath = `${window.location.pathname}${window.location.search}`;
  return `/login?next=${encodeURIComponent(nextPath || '/')}`;
}

function claimLoginRedirect() {
  if (authRedirectStarted) return;
  authRedirectStarted = true;
  return true;
}

function redirectToLoginUrl(loginUrl = '') {
  if (!claimLoginRedirect()) return false;
  window.location.assign(loginUrl || loginRedirectUrlForCurrentLocation());
  return true;
}

async function redirectToLogin(response) {
  if (!claimLoginRedirect()) return;
  let loginUrl = loginRedirectUrlForCurrentLocation();
  try {
    const payload = await response.clone().json();
    if (payload?.login_url) loginUrl = payload.login_url;
  } catch (_) {}
  window.location.assign(loginUrl);
}

function performanceNow() {
  const value = globalThis.performance?.now?.();
  return Number.isFinite(value) ? value : Date.now();
}

function safeDecodeURIComponent(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function utf8ByteLength(text) {
  const value = String(text || '');
  if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
  return value.length;
}

function domDataAttributeName(key) {
  return `data-${String(key).replace(/[A-Z]/g, match => `-${match.toLowerCase()}`)}`;
}

function singleLineText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function jsDebugPerformanceNow() {
  return performanceNow();
}

function jsDebugRequestMethod(options = {}) {
  return String(options?.method || 'GET').toUpperCase();
}

function jsDebugRequestBytes(url, options = {}) {
  let bytes = utf8ByteLength(jsDebugUrlText(url));
  const body = options?.body;
  if (typeof body === 'string') bytes += utf8ByteLength(body);
  else if (body instanceof ArrayBuffer) bytes += body.byteLength;
  else if (body?.byteLength) bytes += Number(body.byteLength) || 0;
  return bytes;
}

function jsDebugDurationMs(startedAt) {
  if (!Number.isFinite(startedAt)) return null;
  const duration = jsDebugPerformanceNow() - startedAt;
  return Number.isFinite(duration) ? Number(duration.toFixed(1)) : null;
}

function jsDebugUrlText(url) {
  const value = String(url || '');
  try {
    const parsed = new URL(value, window.location.origin);
    return `${parsed.pathname}${parsed.search}`;
  } catch (_) {
    return value.slice(0, 240);
  }
}

function jsDebugEndpointText(url) {
  try {
    return new URL(String(url || ''), window.location.origin).pathname.slice(0, 240) || '/';
  } catch (_) {
    return String(url || '').split('?', 1)[0].slice(0, 240) || '/';
  }
}

function jsDebugRoundedMs(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Number(number.toFixed(3)) : null;
}

function jsDebugResourcePhaseTimings(entry, startedAt = null) {
  if (!entry || typeof entry !== 'object') return {};
  const phase = (end, start) => jsDebugRoundedMs(Number(end) - Number(start));
  const secureStart = Number(entry.secureConnectionStart) || 0;
  const entryStartedAt = Number(entry.startTime);
  const callStartedAt = Number(startedAt);
  const beforeResourceMs = Number.isFinite(callStartedAt) && Number.isFinite(entryStartedAt)
    ? Math.max(0, entryStartedAt - callStartedAt)
    : 0;
  const beforeRequestMs = Math.max(0, Number(entry.requestStart) - Number(entry.fetchStart));
  const connectionMs = Math.max(0, Number(entry.connectEnd) - Number(entry.connectStart));
  const phases = {
    queueMs: jsDebugRoundedMs(beforeResourceMs + Math.max(0, beforeRequestMs - connectionMs)),
    connectMs: phase(secureStart > 0 ? secureStart : entry.connectEnd, entry.connectStart),
    tlsMs: secureStart > 0 ? phase(entry.connectEnd, secureStart) : null,
    ttfbMs: phase(entry.responseStart, entry.requestStart),
    downloadMs: phase(entry.responseEnd, entry.responseStart),
  };
  return Object.fromEntries(Object.entries(phases).filter(([_key, value]) => value !== null));
}

function jsDebugResourceTimingEntry(url, startedAt) {
  const timings = globalThis.performance;
  if (typeof timings?.getEntriesByName !== 'function') return null;
  let absolute = String(url || '');
  try {
    absolute = new URL(absolute, window.location.origin).href;
  } catch (_) {}
  const entries = timings.getEntriesByName(absolute).filter(entry => !entry.initiatorType || entry.initiatorType === 'fetch');
  if (!entries.length) return null;
  return entries.reduce((closest, entry) => (
    Math.abs(Number(entry.startTime) - startedAt) < Math.abs(Number(closest.startTime) - startedAt) ? entry : closest
  ));
}

function updateApiDebugResourcePhases(event, url, startedAt) {
  if (!event) return;
  const entry = jsDebugResourceTimingEntry(url, startedAt);
  if (!entry) return;
  event.phaseTimings = {...(event.phaseTimings || {}), ...jsDebugResourcePhaseTimings(entry, startedAt)};
  event.connectionProtocol = String(entry.nextHopProtocol || '').toLowerCase().slice(0, 24);
}

function noteApiDebugHeaders(event, url, startedAt) {
  updateApiDebugResourcePhases(event, url, startedAt);
}

function noteApiDebugResponseConsumed(event, url, startedAt) {
  if (!event) return;
  updateApiDebugResourcePhases(event, url, startedAt);
  const consumedAt = jsDebugPerformanceNow();
  const afterPaint = () => {
    const applyRenderMs = jsDebugRoundedMs(jsDebugPerformanceNow() - consumedAt);
    if (applyRenderMs !== null) {
      event.phaseTimings = {...(event.phaseTimings || {}), applyRenderMs};
      scheduleJsDebugPanelRefresh();
    }
  };
  if (typeof requestAnimationFrame !== 'function') {
    afterPaint();
    return;
  }
  requestAnimationFrame(() => requestAnimationFrame(afterPaint));
}

function notePageLoadApiStarted(startedAt) {
  if (pageLoadProfileState.emitted || !Number.isFinite(startedAt)) return;
  if (pageLoadProfileState.firstApiStartedAt === null) pageLoadProfileState.firstApiStartedAt = startedAt;
  pageLoadProfileState.lastApiStartedAt = startedAt;
  pageLoadProfileState.apiCount += 1;
  pageLoadProfileState.activeApiCount += 1;
  pageLoadProfileState.maxConcurrency = Math.max(
    pageLoadProfileState.maxConcurrency,
    pageLoadProfileState.activeApiCount,
  );
}

function notePageLoadApiCompleted() {
  if (pageLoadProfileState.emitted) return;
  pageLoadProfileState.activeApiCount = Math.max(0, pageLoadProfileState.activeApiCount - 1);
}

function pageLoadNavigationTiming() {
  const entry = globalThis.performance?.getEntriesByType?.('navigation')?.[0];
  return entry && typeof entry === 'object' ? entry : null;
}

function pageLoadPaintTiming(name) {
  const entry = (globalThis.performance?.getEntriesByType?.('paint') || []).find(item => item?.name === name);
  return jsDebugRoundedMs(Number(entry?.startTime));
}

function pageLoadProfileEvent(completedAt = jsDebugPerformanceNow()) {
  const navigation = pageLoadNavigationTiming();
  const firstApi = pageLoadProfileState.firstApiStartedAt;
  const lastApi = pageLoadProfileState.lastApiStartedAt;
  return {
    url: jsDebugEndpointText(window.location.pathname || '/'),
    phaseTimings: {
      navigationMs: jsDebugRoundedMs(navigation ? Number(navigation.responseEnd) - Number(navigation.startTime) : 0),
      bundleParseEvalMs: jsDebugRoundedMs(pageLoadProfileState.bundleEvalEndedAt - pageLoadProfileState.bundleEvalStartedAt),
      firstPaintMs: pageLoadPaintTiming('first-paint'),
      firstContentfulPaintMs: pageLoadPaintTiming('first-contentful-paint'),
      firstApiMs: jsDebugRoundedMs(firstApi === null ? 0 : firstApi),
      fanoutMs: jsDebugRoundedMs(firstApi === null || lastApi === null ? 0 : lastApi - firstApi),
      interactiveMs: jsDebugRoundedMs(completedAt),
      appReadyMs: jsDebugRoundedMs(completedAt),
    },
    fanoutCount: pageLoadProfileState.apiCount,
    maxConcurrency: pageLoadProfileState.maxConcurrency,
    journeyId: reloadClientJourneyId,
  };
}

function clientElementIsVisible(element) {
  if (!element || element.isConnected === false) return false;
  let current = element;
  while (current) {
    if (current.hasAttribute?.('hidden')) return false;
    current = current.parentElement;
  }
  return true;
}

function newFinderUsableJourney() {
  return {
    id: newClientJourneyId('finder'),
    startedAt: performanceNow(),
    scheduled: false,
  };
}

function scheduleFinderUsableObservation(tree, entries, journey) {
  if (!journey || journey.scheduled || !Array.isArray(entries) || !clientElementIsVisible(tree)) return false;
  journey.scheduled = true;
  const complete = () => {
    if (!clientElementIsVisible(tree)) return;
    recordJsDebugEvent('finder_usable', {
      journeyId: journey.id,
      durationMs: jsDebugRoundedMs(performanceNow() - journey.startedAt),
      entryCount: entries.length,
    });
  };
  if (typeof requestAnimationFrame !== 'function') complete();
  else requestAnimationFrame(() => requestAnimationFrame(complete));
  return true;
}

function recordApiOperationWait(record, result) {
  const acceptedAt = Number(record?.acceptedAt);
  if (!Number.isFinite(acceptedAt)) return null;
  const outcome = String(result?.state || result?.status || (result?.error ? 'failed' : 'ready')).toLowerCase();
  return recordJsDebugEvent('operation_wait', {
    journeyId: record.journeyId || newClientJourneyId('operation'),
    durationMs: jsDebugRoundedMs(performanceNow() - acceptedAt),
    operationKind: String(record.kind || 'operation').slice(0, 64),
    outcome: ['ready', 'failed'].includes(outcome) ? outcome : 'ready',
    requestId: String(record.request?.id || '').slice(0, 128),
  });
}

function schedulePageLoadProfileCompletion() {
  if (pageLoadProfileState.emitted) return;
  const complete = () => {
    if (pageLoadProfileState.emitted) return;
    pageLoadProfileState.emitted = true;
    recordJsDebugEvent('page_load', pageLoadProfileEvent());
  };
  if (typeof requestAnimationFrame !== 'function') {
    complete();
    return;
  }
  requestAnimationFrame(() => requestAnimationFrame(complete));
}

function jsDebugErrorText(error) {
  return String(error?.message || error || '').slice(0, 500);
}

function jsDebugFailureText(value, maximum = 500) {
  return String(value?.message || value || '')
    .replace(/[\u0000-\u001f\u007f]+/g, ' ')
    .trim()
    .slice(0, maximum) || 'Unknown client failure';
}

function jsDebugFailureStack(value) {
  return String(value?.stack || '')
    .replace(/([a-z]+:\/\/[^?\s)]+)\?[^\s):]*/gi, '$1')
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]+/g, ' ')
    .trim()
    .slice(0, 4000);
}

function jsDebugFailureSource(value, stack = '') {
  if (value) return jsDebugEndpointText(value);
  const match = String(stack || '').match(/(?:https?:\/\/[^/\s)]+)?(\/[^?\s():]+)(?:\?[^\s):]*)?(?::\d+)?(?::\d+)?/);
  return match?.[1] ? jsDebugEndpointText(match[1]) : '/';
}

function jsDebugFailureSignature(type, message, stack, source, line, column) {
  const input = `${type}|${message}|${String(stack || '').split('\n', 2).join('\n')}|${source}|${line}|${column}`;
  let hash = 0x811c9dc5;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `jsf-${hash.toString(16).padStart(8, '0')}`;
}

function jsDebugFailureDetails(type, value, source = '', line = 0, column = 0) {
  const message = jsDebugFailureText(value);
  const stack = jsDebugFailureStack(value);
  const safeSource = jsDebugFailureSource(source, stack);
  const safeLine = Math.max(0, Math.trunc(Number(line) || 0));
  const safeColumn = Math.max(0, Math.trunc(Number(column) || 0));
  return {
    message,
    stack,
    source: safeSource,
    line: safeLine,
    column: safeColumn,
    signature: jsDebugFailureSignature(type, message, stack, safeSource, safeLine, safeColumn),
  };
}

function recordApiDebugEvent(url, method, startedAt, result = {}) {
  const payload = {
    method,
    url: jsDebugUrlText(url),
    endpoint: jsDebugEndpointText(url),
    durationMs: jsDebugDurationMs(startedAt),
  };
  if (result.requestId) payload.requestId = String(result.requestId).slice(0, 128);
  if (result.source) payload.source = jsDebugFailureSource(result.source);
  if (['controlled_probe', 'confirmed_real'].includes(result.provenance)) payload.provenance = result.provenance;
  if (Number.isFinite(result.requestBytes)) payload.requestBytes = result.requestBytes;
  if (Number.isFinite(result.status)) payload.status = result.status;
  if (typeof result.ok === 'boolean') payload.ok = result.ok;
  if (result.error) payload.error = jsDebugErrorText(result.error);
  for (const field of ['deliveryOutcome', 'reason']) if (result[field]) payload[field] = String(result[field]).slice(0, 64);
  return recordJsDebugEvent('api', payload);
}

function recordApiDebugResponseBytes(event, response) {
  if (!event || !response) return;
  const headerBytes = Number(response.headers?.get?.('Content-Length') || NaN);
  if (Number.isFinite(headerBytes) && headerBytes >= 0) {
    event.responseBytes = headerBytes;
    finalizeJsDebugCurrentObservationBytes(event);
    scheduleJsDebugPanelRefresh();
    return;
  }
  if (typeof response.clone !== 'function') return;
  response.clone().arrayBuffer().then(buffer => {
    event.responseBytes = buffer.byteLength;
    finalizeJsDebugCurrentObservationBytes(event);
    scheduleJsDebugPanelRefresh();
  }).catch(() => {});
}

const diagnosticPacificTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Los_Angeles',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
  timeZoneName: 'short',
});

function diagnosticPacificWallTime(value) {
  const timestampMs = Number(value);
  if (!Number.isFinite(timestampMs)) return '';
  const date = new Date(timestampMs);
  if (!Number.isFinite(date.getTime())) return '';
  const parts = Object.fromEntries(
    diagnosticPacificTimeFormatter.formatToParts(date).map(part => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} ${parts.timeZoneName}`;
}

function redactDiagnosticSecretText(value) {
  let text = String(value ?? '');
  text = text
    .replace(DIAGNOSTIC_TOKEN_QUERY_RE, '$1[redacted-secret]')
    .replace(DIAGNOSTIC_AUTHORIZATION_HEADER_RE, diagnosticRedactSecretHeader)
    .replace(DIAGNOSTIC_MALFORMED_AUTHORIZATION_HEADER_RE, diagnosticRedactSecretHeader)
    .replace(DIAGNOSTIC_COOKIE_HEADER_RE, diagnosticRedactSecretHeader)
    .replace(DIAGNOSTIC_MALFORMED_COOKIE_HEADER_RE, diagnosticRedactSecretHeader)
    .replace(DIAGNOSTIC_SECRET_ASSIGNMENT_RE, diagnosticRedactSecretAssignment)
    .replace(DIAGNOSTIC_BEARER_VALUE_RE, '$1$2[redacted-secret]');
  return text.length > 4000 ? `${text.slice(0, 4000)}[truncated]` : text;
}

function redactDiagnosticValue(value, key = '', depth = 0) {
  if (depth > 12) return '[truncated-depth]';
  if (DIAGNOSTIC_SECRET_KEY_RE.test(String(key || ''))) return '[redacted-secret]';
  if (Array.isArray(value)) return value.slice(0, 256).map(item => redactDiagnosticValue(item, key, depth + 1));
  if (value && typeof value === 'object') {
    const result = {};
    for (const [name, rawValue] of Object.entries(value)) {
      result[String(name).slice(0, 120)] = redactDiagnosticValue(rawValue, String(name), depth + 1);
    }
    return result;
  }
  if (typeof value === 'string') return redactDiagnosticSecretText(value);
  return value;
}

// Diagnostics can outlive the producer that named a credential. Keep upgrade data safe via a
// bounded token-suffix grammar instead of retaining producer-specific identifiers here.
const DIAGNOSTIC_TOKEN_PREFIX_SOURCE = '[A-Za-z](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?';
const DIAGNOSTIC_SECRET_NAME_SOURCE = '(?:token|secret|password|passwd|(?:proxy[_-]?)?authorization|'
  + '(?:set[_-]?)?cookie|bearer|(?:x[_-]?)?api[_-]?key|client[_-]?secret|(?:access|refresh)[_-]?token|'
  + `${DIAGNOSTIC_TOKEN_PREFIX_SOURCE}[_-]token)`;
const DIAGNOSTIC_SECRET_KEY_RE = new RegExp(`^${DIAGNOSTIC_SECRET_NAME_SOURCE}$`, 'i');
const DIAGNOSTIC_TOKEN_QUERY_RE = /([?#&](?:[A-Za-z][A-Za-z0-9_-]{0,63})?token=)[^&#\s"']+/gi;
const DIAGNOSTIC_AUTHORIZATION_HEADER_RE = /\b(?<name>(?:proxy[-_]?)?authorization)(?<separator>[ \t]*(?::|=)[ \t]*)(?:Basic|Bearer)[ \t]+[^\s,;"'<>}]+(?![^\r\n]*=)(?=[ \t]+(?:failed\b|after\b|at[ \t]+\/|Cookie[ \t]*:)|[;\r\n]|$)/gi;
const DIAGNOSTIC_MALFORMED_AUTHORIZATION_HEADER_RE = /(?!\b(?:proxy[-_]?)?authorization[ \t]*(?::|=)[ \t]*\[redacted-secret\])(?!\b(?:proxy[-_]?)?authorization[ \t]*(?::|=)[ \t]*(?:\r?\n|$))(?!\b(?:proxy[-_]?)?authorization[ \t]*(?::|=)[ \t]*["'])\b(?<name>(?:proxy[-_]?)?authorization)(?<separator>[ \t]*(?::|=)[ \t]*)[^\r\n]+/gi;
const DIAGNOSTIC_COOKIE_HEADER_RE = /\b(?<name>(?:Set-)?Cookie)(?<separator>[ \t]*:[ \t]*)[^\s=;,"'<>}]+[ \t]*=[ \t]*(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\s;,"'<>}]+)(?=\s|;|\r?$)(?:[ \t]*;[ \t]*[^\s=;,"'<>}]+[ \t]*=[ \t]*(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'(?:\\[^\r\n]|[^'\\\r\n])*'|[^\s;,"'<>}]+)(?=\s|;|\r?$))*(?![ \t]*;)(?![^\r\n]*=)/gi;
const DIAGNOSTIC_MALFORMED_COOKIE_HEADER_RE = /(?!\b(?:Set-)?Cookie[ \t]*:[ \t]*\[redacted-secret\])(?!\b(?:Set-)?Cookie[ \t]*:[ \t]*(?:\r?\n|$))\b(?<name>(?:Set-)?Cookie)(?<separator>[ \t]*:[ \t]*)[^\r\n]+/gi;
const DIAGNOSTIC_SECRET_ASSIGNMENT_RE = new RegExp(
  '\\b(?<prefix>' + DIAGNOSTIC_SECRET_NAME_SOURCE + '\\b["\']?[ \\t]*(?:=|:)[ \\t]*)'
  + '(?:(?<quote>["\'])(?<quoted_value>(?:\\\\[^\\r\\n]|(?!\\k<quote>)[^\\\\\\r\\n])*)\\k<quote>|'
  + '(?<unterminated_quote>["\'])(?<unterminated_value>[^\\r\\n]*)|'
  + '(?<value>[^&#\\s,;"\'<>}]+))',
  'gi',
);
const DIAGNOSTIC_BEARER_VALUE_RE = /\b(Bearer)([ \t]+)([^\s,;:="'<>]+)/gi;

function diagnosticRedactSecretHeader(...args) {
  const groups = args[args.length - 1];
  return `${groups.name}${groups.separator}[redacted-secret]`;
}

function diagnosticRedactSecretAssignment(...args) {
  const groups = args[args.length - 1];
  const quote = groups.quote || '';
  if (groups.unterminated_quote) return `${groups.prefix}[redacted-secret]`;
  const value = quote ? groups.quoted_value : groups.value;
  if (typeof value === 'string' && value.startsWith('[redacted-')) return args[0];
  return `${groups.prefix}${quote}[redacted-secret]${quote}`;
}

function recordJsDebugEvent(type, payload = {}) {
  const timestampMs = Date.now();
  // W2: sanitize the caller payload BEFORE retention, then write the authoritative event identity
  // fields AFTER the spread so a payload carrying its own id/ts/type can never overwrite the
  // trusted monotonic identity this producer assigns. `wallTime` is NOT one of those fields: a
  // browser-lifecycle failure records the Pacific wall time it observed the failure at, and the
  // finalizer reports THAT time, not the later moment this event was retained -- so a caller
  // wallTime is preserved and the producer only stamps its own when the caller supplied none.
  const redacted = redactDiagnosticValue(payload);
  const event = {
    ...redacted,
    id: ++jsDebugEventSeq,
    ts: new Date(timestampMs).toISOString(),
    wallTime: String(redacted.wallTime || diagnosticPacificWallTime(timestampMs)),
    type: String(type || 'event'),
  };
  jsDebugEvents.push(event);
  if (typeof recordJsDebugEventForGraph === 'function') recordJsDebugEventForGraph(event);
  if (jsDebugEvents.length > jsDebugEventLimit) {
    jsDebugEvents.splice(0, jsDebugEvents.length - jsDebugEventLimit);
  }
  scheduleJsDebugPanelRefresh();
  return event;
}

function jsDebugFailureClassification(event) {
  const type = String(event?.type || '');
  if (type === 'unhandledrejection') return {releaseBlocking: true, kind: 'rejection', observationKind: type};
  const apiFailure = type === 'api' && event?.deliveryOutcome !== 'retired' && (
    event?.ok === false
    || (Number.isFinite(event?.status) && event.status >= 400)
    || Boolean(event?.error)
  );
  const statsLevel = String(event?.level || '').toLowerCase();
  const releaseBlocking = type === 'error'
    || type === 'client_failure'
    || apiFailure
    || (type === 'sse' && Boolean(event?.error))
    || (type === 'stats_history' && ['warning', 'error'].includes(statsLevel));
  return {
    releaseBlocking,
    kind: releaseBlocking ? 'error' : '',
    observationKind: type === 'stats_history' ? statsLevel : (releaseBlocking ? 'error' : type),
  };
}

function jsDebugFailureEvents(kind = 'all') {
  const requested = String(kind || 'all');
  return jsDebugEvents.filter(event => {
    const classification = jsDebugFailureClassification(event);
    if (requested === 'error') return classification.kind === 'error';
    if (requested === 'rejection') return classification.kind === 'rejection';
    return classification.releaseBlocking;
  });
}

function clientPerfActiveAnimationCount() {
  const animations = typeof document?.getAnimations === 'function' ? document.getAnimations({subtree: true}) : [];
  return animations.filter(animation => animation?.playState === 'running').length;
}

function recordClientPerfCounter(name, durationMs = null, details = {}) {
  const key = String(name || '').trim();
  if (!key) return null;
  let counter = clientPerfCounters.get(key);
  if (!counter) {
    counter = {name: key, count: 0, totalMs: 0, maxMs: 0, lastMs: null, lastAt: '', rows: 0, nodes: 0, bytes: 0, skipped: 0};
    clientPerfCounters.set(key, counter);
    if (clientPerfCounters.size > clientPerfCounterLimit) {
      clientPerfCounters.delete(clientPerfCounters.keys().next().value);
    }
  }
  counter.count += 1;
  const duration = Number(durationMs);
  if (Number.isFinite(duration) && duration >= 0) {
    const rounded = Number(duration.toFixed(2));
    counter.totalMs = Number((counter.totalMs + rounded).toFixed(2));
    counter.maxMs = Number(Math.max(counter.maxMs, rounded).toFixed(2));
    counter.lastMs = rounded;
  }
  for (const field of ['rows', 'nodes', 'bytes', 'skipped']) {
    const value = Number(details?.[field]);
    if (Number.isFinite(value) && value > 0) counter[field] += value;
  }
  counter.lastAt = new Date().toISOString();
  counter.lastDetails = {...details};
  if (typeof jsDebugStatsPanelVisible === 'function' && jsDebugStatsPanelVisible()) scheduleJsDebugPanelRefresh();
  return counter;
}

function clientPerfStart(name) {
  return {name: String(name || ''), startedAt: performanceNow()};
}

function clientPerfEnd(token, details = {}) {
  if (!token?.name) return null;
  return recordClientPerfCounter(token.name, performanceNow() - Number(token.startedAt || 0), details);
}

function clientPerfMeasure(name, fn, details = {}) {
  const token = clientPerfStart(name);
  try {
    return fn();
  } finally {
    clientPerfEnd(token, typeof details === 'function' ? details() : details);
  }
}

function clientPerfSummary() {
  return Array.from(clientPerfCounters.values()).map(counter => ({
    ...counter,
    avgMs: counter.count ? Number((counter.totalMs / counter.count).toFixed(2)) : 0,
  }));
}

function clientPerfLongTaskSummary() {
  const samples = clientPerfLongTaskSamples.slice();
  const total = samples.reduce((sum, sample) => sum + Number(sample.durationMs || 0), 0);
  const max = samples.reduce((value, sample) => Math.max(value, Number(sample.durationMs || 0)), 0);
  return {
    count: samples.length,
    averageMs: samples.length ? Number((total / samples.length).toFixed(1)) : 0,
    maxMs: Number(max.toFixed(1)),
    samples,
  };
}

function clearClientPerfCounters() {
  clientPerfCounters.clear();
  clientPerfLongTaskSamples = [];
}

function installClientPerfLongTaskObserver() {
  if (clientPerfLongTaskObserverInstalled || typeof globalThis.PerformanceObserver !== 'function') return;
  clientPerfLongTaskObserverInstalled = true;
  try {
    const observer = new globalThis.PerformanceObserver(list => {
      for (const entry of list.getEntries?.() || []) {
        const durationMs = Number(entry.duration || 0);
        const sample = {ts: new Date().toISOString(), durationMs: Number(durationMs.toFixed(1)), name: String(entry.name || 'longtask')};
        clientPerfLongTaskSamples.push(sample);
        if (clientPerfLongTaskSamples.length > clientPerfLongTaskSampleLimit) {
          clientPerfLongTaskSamples.splice(0, clientPerfLongTaskSamples.length - clientPerfLongTaskSampleLimit);
        }
        recordClientPerfCounter('longTask', durationMs);
        if (clientPerfLongTaskDurableCount < clientPerfDurableExemplarLimit) {
          clientPerfLongTaskDurableCount += 1;
          recordJsDebugEvent('long_task', {journeyId: reloadClientJourneyId, durationMs: sample.durationMs});
        }
      }
    });
    observer.observe({entryTypes: ['longtask']});
  } catch (_) {}
}

function clientPerfInteractionEvent(entry) {
  const startTime = Number(entry?.startTime || 0);
  const processingStart = Number(entry?.processingStart || startTime);
  const processingEnd = Number(entry?.processingEnd || processingStart);
  const durationMs = Number(entry?.duration || 0);
  if (!Number.isFinite(durationMs) || durationMs < 0) return null;
  return {
    journeyId: newClientJourneyId('action'),
    durationMs: jsDebugRoundedMs(durationMs),
    inputDelayMs: jsDebugRoundedMs(Math.max(0, processingStart - startTime)),
    processingMs: jsDebugRoundedMs(Math.max(0, processingEnd - processingStart)),
    presentationDelayMs: jsDebugRoundedMs(Math.max(0, startTime + durationMs - processingEnd)),
    interactionType: String(entry?.name || 'interaction').toLowerCase().slice(0, 32),
  };
}

function installClientPerfInteractionObserver() {
  if (clientPerfInteractionObserverInstalled || typeof globalThis.PerformanceObserver !== 'function') return;
  clientPerfInteractionObserverInstalled = true;
  try {
    const observer = new globalThis.PerformanceObserver(list => {
      for (const entry of list.getEntries?.() || []) {
        const durationMs = Number(entry?.duration || 0);
        if (
          Number(entry?.interactionId || 0) <= 0
          || durationMs <= clientPerfInteractionMaximumMs
          || clientPerfInteractionDurableCount >= clientPerfDurableExemplarLimit
        ) continue;
        const event = clientPerfInteractionEvent(entry);
        if (!event) continue;
        clientPerfInteractionMaximumMs = durationMs;
        clientPerfInteractionDurableCount += 1;
        recordJsDebugEvent('interaction', event);
      }
    });
    observer.observe({type: 'event', buffered: true, durationThreshold: 16});
  } catch (_) {}
}

function terminalRemovalLatencyNowMs() {
  const value = Date.now();
  return Number.isFinite(value) ? value : 0;
}

function terminalRemovalLatencyKey(targetKind, target) {
  return `${String(targetKind || 'target')}:${String(target || '')}`;
}

function noteTerminalRemovalLatencyStart(targetKind, target, details = {}) {
  const key = terminalRemovalLatencyKey(targetKind, target);
  terminalRemovalLatencyPending.set(key, {
    targetKind: String(targetKind || 'target'),
    target: String(target || ''),
    origin: String(details.origin || 'unknown'),
    startedAtMs: terminalRemovalLatencyNowMs(),
    details: {...details},
  });
}

function clearTerminalRemovalLatency(targetKind, target) {
  terminalRemovalLatencyPending.delete(terminalRemovalLatencyKey(targetKind, target));
}

function completeTerminalRemovalLatency(targetKind, target, details = {}) {
  const key = terminalRemovalLatencyKey(targetKind, target);
  const pending = terminalRemovalLatencyPending.get(key) || null;
  const explicitStartedAtMs = Number(details.startedAtMs);
  const startedAtMs = Number.isFinite(explicitStartedAtMs)
    ? explicitStartedAtMs
    : Number(pending?.startedAtMs);
  if (!Number.isFinite(startedAtMs) || startedAtMs <= 0) return null;
  terminalRemovalLatencyPending.delete(key);
  const nowMs = terminalRemovalLatencyNowMs();
  const durationMs = Math.max(0, nowMs - startedAtMs);
  const sample = {
    ts: new Date(nowMs).toISOString(),
    targetKind: String(targetKind || pending?.targetKind || 'target'),
    target: String(target || pending?.target || ''),
    origin: String(details.origin || pending?.origin || 'unknown'),
    reason: String(details.reason || ''),
    durationMs: Number(durationMs.toFixed(1)),
    startedAtMs,
    removedAtMs: nowMs,
  };
  if (Number.isFinite(Number(details.eventAtMs))) sample.eventAtMs = Number(details.eventAtMs);
  if (details.eventType) sample.eventType = String(details.eventType);
  if (Number.isFinite(Number(details.closeCode))) sample.closeCode = Number(details.closeCode);
  if (typeof details.wasClean === 'boolean') sample.wasClean = details.wasClean;
  terminalRemovalLatencySamples.push(sample);
  if (terminalRemovalLatencySamples.length > terminalRemovalLatencySampleLimit) {
    terminalRemovalLatencySamples.splice(0, terminalRemovalLatencySamples.length - terminalRemovalLatencySampleLimit);
  }
  recordJsDebugEvent('terminal_removal', {
    message: `${sample.targetKind} ${sample.target} removed after ${sample.durationMs}ms from ${sample.origin}`,
    durationMs: sample.durationMs,
    targetKind: sample.targetKind,
    target: sample.target,
    origin: sample.origin,
    reason: sample.reason,
    eventType: sample.eventType,
    closeCode: sample.closeCode,
    wasClean: sample.wasClean,
  });
  return sample;
}

function completeTerminalRemovalLatencyFromEpochSeconds(targetKind, target, epochSeconds, details = {}) {
  const eventAtMs = Number(epochSeconds) * 1000;
  if (!Number.isFinite(eventAtMs) || eventAtMs <= 0) return null;
  return completeTerminalRemovalLatency(targetKind, target, {
    ...details,
    startedAtMs: eventAtMs,
    eventAtMs,
  });
}

function terminalRemovalLatencySummary() {
  const samples = terminalRemovalLatencySamples.slice();
  const total = samples.reduce((sum, sample) => sum + Number(sample.durationMs || 0), 0);
  const max = samples.reduce((value, sample) => Math.max(value, Number(sample.durationMs || 0)), 0);
  return {
    count: samples.length,
    pending: terminalRemovalLatencyPending.size,
    averageMs: samples.length ? Number((total / samples.length).toFixed(1)) : 0,
    maxMs: Number(max.toFixed(1)),
    last: samples.at(-1) || null,
    samples,
  };
}

function clearJsDebugEvents() {
  jsDebugEvents = [];
  terminalRemovalLatencyPending.clear();
  terminalRemovalLatencySamples = [];
  clearClientPerfCounters();
  if (typeof clearJsDebugGraphData === 'function') clearJsDebugGraphData();
  if (typeof clearJsDebugServerHistory === 'function') clearJsDebugServerHistory();
  if (jsDebugRenderTimer) {
    clearTimeout(jsDebugRenderTimer);
    jsDebugRenderTimer = null;
  }
  jsDebugRenderForce = false;
  jsDebugRenderDragDeferred = false;
  if (typeof renderDebugPanels === 'function') renderDebugPanels({force: true, scrollLogToBottom: true});
}

function runJsDebugPanelRefresh() {
  jsDebugRenderTimer = null;
  if (dragState.item != null) {
    jsDebugRenderDragDeferred = true;
    return;
  }
  const force = jsDebugRenderForce;
  jsDebugRenderForce = false;
  refreshDebugPanelsFromEvents({force});
}

function scheduleJsDebugPanelRefresh(options = {}) {
  if (typeof refreshDebugPanelsFromEvents !== 'function') return;
  if (options.force === true) jsDebugRenderForce = true;
  if (dragState.item != null) {
    jsDebugRenderDragDeferred = true;
    return;
  }
  if (options.immediate === true) {
    if (jsDebugRenderTimer) clearTimeout(jsDebugRenderTimer);
    runJsDebugPanelRefresh();
    return;
  }
  if (jsDebugRenderTimer) return;
  jsDebugRenderTimer = setTimeout(runJsDebugPanelRefresh, jsDebugRenderDebounceMs);
}

function flushDeferredJsDebugPanelRefresh() {
  if (!jsDebugRenderDragDeferred) return false;
  jsDebugRenderDragDeferred = false;
  scheduleJsDebugPanelRefresh({force: jsDebugRenderForce});
  return true;
}

function installJsDebugEventCapture() {
  if (jsDebugEventCaptureInstalled || !window?.addEventListener) return;
  jsDebugEventCaptureInstalled = true;
  window.addEventListener('error', event => {
    recordJsDebugEvent('error', jsDebugFailureDetails(
      'error', event.error || event.message, event.filename, event.lineno, event.colno,
    ));
  });
  window.addEventListener('unhandledrejection', event => {
    recordJsDebugEvent(
      'unhandledrejection',
      jsDebugFailureDetails('unhandledrejection', event.reason),
    );
  });
}

function enableDebugMode() {
  debugModeEnabled = true;
  installJsDebugEventCapture();
  scheduleJsDebugPanelRefresh();
}

installJsDebugEventCapture();
installClientPerfLongTaskObserver();
installClientPerfInteractionObserver();

let appViewportOverride = null;
const APP_VIEWPORT_CHANGE_EVENT = 'yolomux:app-viewport-change';
const nativeAppViewportSettleDelayMs = 250;
const nativeAppViewportState = {
  height: 0,
  frame: 0,
  settleTimer: 0,
  pendingForce: false,
  installed: false,
};
let appMirrorTransform = {scale: 1, tx: 0, ty: 0};

function normalizeAppViewport(value, fallback = null) {
  const source = value && typeof value === 'object' ? value : {};
  const fallbackSource = fallback && typeof fallback === 'object' ? fallback : {};
  const width = Math.max(1, Math.round(Number(source.width ?? source.w ?? fallbackSource.width ?? fallbackSource.w ?? 0) || 0));
  const height = Math.max(1, Math.round(Number(source.height ?? source.h ?? fallbackSource.height ?? fallbackSource.h ?? 0) || 0));
  return {width, height, w: width, h: height};
}

function nativeViewport() {
  const doc = document.documentElement || {};
  const width = Math.max(1, Math.round(Number(window.innerWidth) || Number(doc.clientWidth) || 1)); // static-build-allow-window-viewport
  const layoutHeight = Math.max(1, Math.round(Number(window.innerHeight) || Number(doc.clientHeight) || 1)); // static-build-allow-window-viewport
  const visualViewport = window.visualViewport;
  const visualHeight = Math.max(0, Math.round(Number(visualViewport?.height) || 0));
  const visualScale = Number(visualViewport?.scale);
  // iPad Safari can retain a short innerHeight after its browser chrome changes while the current
  // unzoomed visual viewport is taller. Never accept a *smaller* visual height here (that remains
  // browser chrome or keyboard territory), but accept a larger one so the app reaches the screen.
  const height = Math.max(1, Number.isFinite(visualScale) && Math.abs(visualScale - 1) <= 0.01 && visualHeight > layoutHeight
    ? visualHeight
    : layoutHeight);
  return {width, height, w: width, h: height};
}

// A soft keyboard shrinks the visual viewport by a large amount; the iPad/iOS Safari
// toolbar (URL/tab bar) shrinks it by a much smaller amount with NO keyboard present.
// Only a large reduction is real keyboard geometry — treating the toolbar delta as
// "usable height" pins --app-root-height below 100vh, so the panes stop short of the
// screen and leave dead space at the bottom. Ignore reductions at/below this threshold
// (a real soft keyboard is ~250-400px; Safari chrome is well under 140px).
const KEYBOARD_MIN_REDUCTION_PX = 140;
const viewportDiagnosticsState = {node: null};

function viewportDiagnosticsFocusedElementText(element = document.activeElement) {
  if (!element || element === document.body || element === document.documentElement) return 'none';
  const tag = String(element.tagName || element.localName || 'element').toLowerCase();
  const label = String(element.getAttribute?.('aria-label') || element.placeholder || element.id || element.className || '').trim();
  return label ? `${tag}:${label}` : tag;
}

function viewportDiagnosticsSnapshot() {
  const native = nativeViewport();
  const visual = window.visualViewport;
  const rootRect = appRootElement()?.getBoundingClientRect?.();
  const visualWidth = Math.max(0, Math.round(Number(visual?.width) || 0));
  const visualHeight = Math.max(0, Math.round(Number(visual?.height) || 0));
  const reduction = visualHeight ? native.height - visualHeight : 0;
  const scale = Number(visual?.scale);
  return {
    layout: native,
    document: {
      width: Math.max(0, Math.round(Number(document.documentElement?.clientWidth) || 0)),
      height: Math.max(0, Math.round(Number(document.documentElement?.clientHeight) || 0)),
    },
    visual: {
      width: visualWidth,
      height: visualHeight,
      top: Math.round(Number(visual?.offsetTop) || 0),
      left: Math.round(Number(visual?.offsetLeft) || 0),
      scale: Number.isFinite(scale) ? Math.round(scale * 100) / 100 : 0,
    },
    reduction,
    keyboardThreshold: KEYBOARD_MIN_REDUCTION_PX,
    keyboardHeight: nativeAppViewportState.height,
    keyboardCandidate: Boolean(visualHeight && Math.abs((Number.isFinite(scale) ? scale : 1) - 1) <= 0.01 && reduction > KEYBOARD_MIN_REDUCTION_PX),
    root: {
      width: Math.max(0, Math.round(Number(rootRect?.width) || 0)),
      height: Math.max(0, Math.round(Number(rootRect?.height) || 0)),
      bottom: Math.max(0, Math.round(Number(rootRect?.bottom) || 0)),
    },
    focused: viewportDiagnosticsFocusedElementText(),
  };
}

function viewportDiagnosticsText(snapshot = viewportDiagnosticsSnapshot()) {
  const {layout, document: documentViewport, visual, root} = snapshot;
  return [
    `layout ${layout.width}×${layout.height} · doc ${documentViewport.width}×${documentViewport.height}`,
    `visual ${visual.width}×${visual.height} @${visual.left},${visual.top} · scale ${visual.scale || 'n/a'}`,
    `delta ${snapshot.reduction}px · keyboard ${snapshot.keyboardCandidate ? `yes (${snapshot.keyboardHeight}px)` : `no (>${snapshot.keyboardThreshold}px)`}`,
    `root ${root.width}×${root.height} · bottom ${root.bottom} · focus ${snapshot.focused}`,
  ].join('\n');
}

function renderViewportDiagnostics() {
  if (!debugModeExplicitUrlEnabled) return false;
  let node = viewportDiagnosticsState.node;
  if (!node?.isConnected) {
    node = document.createElement('output');
    node.id = 'viewportDiagnostics';
    node.className = 'viewport-diagnostics';
    node.setAttribute('aria-live', 'off');
    document.body?.appendChild(node);
    viewportDiagnosticsState.node = node;
  }
  node.textContent = viewportDiagnosticsText();
  return true;
}

function nativeUsableViewportHeight(viewport = nativeViewport()) {
  const visualViewport = window.visualViewport;
  if (!visualViewport) return 0;
  const scale = Number(visualViewport.scale);
  // A pinch changes visual viewport width/height too. Responsive layout remains tied to the layout
  // viewport; only an unzoomed visual-height reduction is usable keyboard geometry.
  if (Number.isFinite(scale) && Math.abs(scale - 1) > 0.01) return 0;
  const visualHeight = Math.max(1, Math.round(Number(visualViewport.height) || 0));
  return viewport.height - visualHeight > KEYBOARD_MIN_REDUCTION_PX ? visualHeight : 0;
}

function appViewport() {
  const native = nativeViewport();
  if (appViewportOverride) return normalizeAppViewport(appViewportOverride, native);
  return nativeAppViewportState.height
    ? normalizeAppViewport({width: native.width, height: nativeAppViewportState.height}, native)
    : native;
}

const MIN_VIEWPORT_WIDTH_PX = 320;
const DEFAULT_VIEWPORT_WIDTH_PX = 1200;
const OFFSCREEN_POSITION_PX = -10000;

function effectiveViewportWidth(viewport = appViewport(), fallback = DEFAULT_VIEWPORT_WIDTH_PX) {
  const width = Number(viewport?.width ?? viewport?.w);
  const fallbackWidth = Number(fallback) || DEFAULT_VIEWPORT_WIDTH_PX;
  return Math.max(MIN_VIEWPORT_WIDTH_PX, width || fallbackWidth);
}

const appViewportBreakpointPx = [1500, 1280, 1200, 1100, 1080, 980, 760, 720, 600, 560];

// Topbar presentation is selected by its measured rendered width in syncTopbarPacking(). Keep
// this compatibility helper for fixture callers; viewport width is deliberately not a topbar input.
function compactTopbarForViewport(_viewport = appViewport()) {
  return false;
}

function syncAppViewportBreakpointClasses() {
  const viewport = appViewport();
  // Pointer affordances are independent of topbar capacity. The measured packing pass decides
  // which controls fit for every viewport, font, locale, zoom level, and activity state.
  const touchTopbar = browserUsesCoarsePointer();
  // This mirrors the shared phone-only one-pane policy (not the broader narrow-iPad rule), so
  // phone chrome can reduce a redundant focus surround without changing tablet split geometry.
  const phoneSinglePane = mobileSinglePaneMode();
  const coarsePointer = browserUsesCoarsePointer();
  const targets = [document.body, appRootElement()].filter(Boolean);
  for (const target of targets) {
    for (const breakpoint of appViewportBreakpointPx) {
      target.classList?.toggle(`app-vw-lte-${breakpoint}`, viewport.width <= breakpoint);
    }
    target.classList?.toggle('app-topbar-touch-compact', touchTopbar);
    target.classList?.toggle('app-topbar-coarse-pointer', coarsePointer);
    target.classList?.toggle('app-phone-single-pane', phoneSinglePane);
  }
  if (typeof syncTopbarActivityPlacement === 'function') syncTopbarActivityPlacement();
}

function appRootElement() {
  return appRoot || document.getElementById?.('appRoot') || document.body;
}

function appOverlayRootElement() {
  const root = appRootElement();
  if (!root || root === document.body) return document.body;
  let overlay = document.getElementById?.('appOverlayRoot');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'appOverlayRoot';
    overlay.className = 'app-overlay-root';
  }
  if (overlay.parentElement !== root) root.appendChild(overlay);
  return overlay;
}

function cleanupDetachedPopoverAnchor(anchor, keep = null) {
  const previous = anchor?.__yolomuxDetachedPopover;
  if (previous && previous !== keep) previous.remove();
  if (previous && previous !== keep) anchor.__yolomuxDetachedPopover = null;
}

function cleanupDetachedPopoversWithin(root) {
  if (!root) return;
  const anchors = [root, ...Array.from(root.querySelectorAll?.('*') || [])];
  for (const anchor of anchors) cleanupDetachedPopoverAnchor(anchor);
}

function applyAppRootViewportSize() {
  const root = appRootElement();
  if (!root?.style) return;
  const viewport = appViewport();
  if (appViewportOverride) root.style.setProperty('--app-root-width', `${viewport.width}px`);
  else root.style.removeProperty('--app-root-width');
  // Safari's CSS 100vh can be its large viewport while innerHeight is the currently visible
  // browser viewport. Always publish the measured height so app-root cannot extend below Safari
  // chrome; keyboard mode merely supplies the smaller visual viewport through appViewport().
  root.style.setProperty('--app-root-height', `${viewport.height}px`);
}

function setAppViewportOverride(viewport = null) {
  appViewportOverride = viewport ? normalizeAppViewport(viewport) : null;
  applyAppRootViewportSize();
  syncAppViewportBreakpointClasses();
  return appViewport();
}

function notifyAppViewportChange() {
  if (typeof window.dispatchEvent !== 'function' || typeof Event !== 'function') return;
  window.dispatchEvent(new Event(APP_VIEWPORT_CHANGE_EVENT));
}

function syncNativeAppViewport(options = {}) {
  if (appViewportOverride) return false;
  const nextHeight = nativeUsableViewportHeight();
  const changed = nextHeight !== nativeAppViewportState.height;
  nativeAppViewportState.height = nextHeight;
  if (!changed && options.force !== true) return false;
  applyAppRootViewportSize();
  syncAppViewportBreakpointClasses();
  renderViewportDiagnostics();
  notifyAppViewportChange();
  return true;
}

function scheduleNativeAppViewportSync(options = {}) {
  nativeAppViewportState.pendingForce ||= options.force === true;
  if (nativeAppViewportState.frame) return;
  nativeAppViewportState.frame = requestAnimationFrame(() => {
    nativeAppViewportState.frame = 0;
    const force = nativeAppViewportState.pendingForce;
    nativeAppViewportState.pendingForce = false;
    syncNativeAppViewport({force});
  });
}

function installNativeAppViewportOwner() {
  if (nativeAppViewportState.installed) return;
  nativeAppViewportState.installed = true;
  const resize = () => scheduleNativeAppViewportSync({force: true});
  const settle = () => {
    resize();
    if (nativeAppViewportState.settleTimer) clearTimeout(nativeAppViewportState.settleTimer);
    nativeAppViewportState.settleTimer = setTimeout(() => {
      nativeAppViewportState.settleTimer = 0;
      resize();
    }, nativeAppViewportSettleDelayMs);
  };
  window.addEventListener('resize', resize);
  window.addEventListener('orientationchange', settle);
  window.visualViewport?.addEventListener?.('resize', resize);
  window.visualViewport?.addEventListener?.('scroll', resize);
  // Re-fit when the tab returns to the foreground: a viewport change made while
  // this tab was backgrounded (e.g. Safari showing/hiding its tab bar when a
  // second tab opens/closes) is missed by the resize listeners, leaving a stale
  // --app-root-height that clips the toolbar. settle() recomputes immediately and
  // once more after the geometry settles.
  document.addEventListener('visibilitychange', () => { if (!document.hidden) settle(); });
  window.addEventListener('pageshow', settle);
  if (debugModeExplicitUrlEnabled) {
    document.addEventListener('focusin', renderViewportDiagnostics, true);
    document.addEventListener('focusout', () => requestAnimationFrame(renderViewportDiagnostics), true);
  }
}

// Mobile browsers do not reliably dispatch contextmenu for a long touch (iOS Safari
// often claims it for selection/callout), while the app's actions already have one
// contextmenu owner per surface. Bridge a stationary touch into that existing event
// instead of creating a second menu implementation for Finder, tabs, and terminals.
const TOUCH_CONTEXT_MENU_DELAY_MS = 550;
const TOUCH_CONTEXT_MENU_MOVE_TOLERANCE_PX = 12;
const touchContextMenuSyntheticEvents = new WeakSet();
const touchContextMenuState = {
  installed: false,
  timer: 0,
  pointerId: null,
  target: null,
  x: 0,
  y: 0,
  suppressTarget: null,
  suppressUntil: 0,
};

function clearTouchContextMenuTimer() {
  if (!touchContextMenuState.timer) return;
  clearTimeout(touchContextMenuState.timer);
  touchContextMenuState.timer = 0;
}

function dispatchTouchContextMenu(target, x, y) {
  if (!target?.dispatchEvent || target.isConnected === false) return false;
  const event = new MouseEvent('contextmenu', {
    bubbles: true,
    cancelable: true,
    clientX: x,
    clientY: y,
  });
  touchContextMenuSyntheticEvents.add(event);
  return !target.dispatchEvent(event);
}

function installTouchContextMenuOwner() {
  if (touchContextMenuState.installed) return;
  touchContextMenuState.installed = true;
  const cancel = event => {
    if (event?.pointerId != null && event.pointerId !== touchContextMenuState.pointerId) return;
    clearTouchContextMenuTimer();
    touchContextMenuState.pointerId = null;
    touchContextMenuState.target = null;
  };
  document.addEventListener('pointerdown', event => {
    if (event.pointerType !== 'touch' || event.isPrimary === false || Number(event.button || 0) !== 0) return;
    cancel();
    touchContextMenuState.pointerId = event.pointerId;
    touchContextMenuState.target = event.target;
    touchContextMenuState.x = event.clientX;
    touchContextMenuState.y = event.clientY;
    touchContextMenuState.timer = setTimeout(() => {
      touchContextMenuState.timer = 0;
      const target = touchContextMenuState.target;
      const handled = dispatchTouchContextMenu(target, touchContextMenuState.x, touchContextMenuState.y);
      if (handled) {
        // Block the delayed native event so one long press cannot open both menus.
        touchContextMenuState.suppressTarget = target;
        touchContextMenuState.suppressUntil = performance.now() + TOUCH_CONTEXT_MENU_DELAY_MS;
      }
      touchContextMenuState.pointerId = null;
      touchContextMenuState.target = null;
    }, TOUCH_CONTEXT_MENU_DELAY_MS);
  }, {capture: true, passive: true});
  document.addEventListener('pointermove', event => {
    if (event.pointerId !== touchContextMenuState.pointerId) return;
    if (Math.hypot(event.clientX - touchContextMenuState.x, event.clientY - touchContextMenuState.y) > TOUCH_CONTEXT_MENU_MOVE_TOLERANCE_PX) cancel(event);
  }, {capture: true, passive: true});
  document.addEventListener('pointerup', cancel, true);
  document.addEventListener('pointercancel', cancel, true);
  document.addEventListener('contextmenu', event => {
    if (touchContextMenuSyntheticEvents.has(event) || performance.now() > touchContextMenuState.suppressUntil) return;
    if (!touchContextMenuState.suppressTarget?.contains?.(event.target) && event.target !== touchContextMenuState.suppressTarget) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
}

function appMirrorTransformState() {
  return {
    scale: Math.max(0.0001, Number(appMirrorTransform.scale) || 1),
    tx: Number(appMirrorTransform.tx) || 0,
    ty: Number(appMirrorTransform.ty) || 0,
  };
}

function setAppMirrorTransform(transform = {}) {
  appMirrorTransform = {
    scale: Math.max(0.0001, Number(transform.scale) || 1),
    tx: Number(transform.tx) || 0,
    ty: Number(transform.ty) || 0,
  };
  return appMirrorTransformState();
}

function appSpaceRect(elementOrRect) {
  const rect = elementOrRect?.getBoundingClientRect ? elementOrRect.getBoundingClientRect() : elementOrRect;
  const transform = appMirrorTransformState();
  const left = (Number(rect?.left) || 0) - transform.tx;
  const top = (Number(rect?.top) || 0) - transform.ty;
  const width = Math.max(0, Number(rect?.width) || Math.max(0, (Number(rect?.right) || 0) - (Number(rect?.left) || 0))) / transform.scale;
  const height = Math.max(0, Number(rect?.height) || Math.max(0, (Number(rect?.bottom) || 0) - (Number(rect?.top) || 0))) / transform.scale;
  const mappedLeft = left / transform.scale;
  const mappedTop = top / transform.scale;
  return {
    left: mappedLeft,
    top: mappedTop,
    width,
    height,
    right: mappedLeft + width,
    bottom: mappedTop + height,
  };
}

function appSpacePoint(x, y) {
  const transform = appMirrorTransformState();
  return {
    x: (Number(x) - transform.tx) / transform.scale,
    y: (Number(y) - transform.ty) / transform.scale,
  };
}

function visualPointFromAppSpace(x, y) {
  const transform = appMirrorTransformState();
  return {
    x: (Number(x) * transform.scale) + transform.tx,
    y: (Number(y) * transform.scale) + transform.ty,
  };
}

function agentLabel(kind) {
  const key = String(kind || '').toLowerCase();
  if (key === 'codex') return 'Codex';
  if (key === 'claude') return 'Claude';
  return String(kind || '');
}

const sessionFileLookbackDefaultHours = 24;
const sessionFileLookbackHourValues = Object.freeze([
  0.5, 1, 2, 4, 8, 12, 24, 48, 72, 96, 120, 144, 168, 192, 216, 240, 264, 288, 312, 336,
]);

function normalizeSessionFileLookbackHours(value, fallback = sessionFileLookbackDefaultHours) {
  const parsed = Number(value);
  const candidate = Number.isFinite(parsed) ? parsed : Number(fallback);
  if (sessionFileLookbackHourValues.includes(candidate)) return candidate;
  if (sessionFileLookbackHourValues.includes(Number(fallback))) return Number(fallback);
  return sessionFileLookbackDefaultHours;
}

function sessionFileLookbackLabel(hours) {
  const value = Number(hours);
  if (value < 1) return t('duration.minuteShort', {count: Math.round(value * 60)});
  if (value < 24) return tPlural('duration.hour', value);
  return tPlural('duration.day', value / 24);
}

function sessionFileLookbackOptions() {
  return sessionFileLookbackHourValues.map(hours => ({hours, label: sessionFileLookbackLabel(hours)}));
}

// localStorage can throw (privacy mode, blocked, quota) — these swallow failures so a blocked store
// never breaks the page. storageGet returns the raw string (or `fallback` when absent/blocked);
// storageSet coerces to string and no-ops on failure. Every readStored*/writeStored* builds on these.
function storageGet(key, fallback = null) {
  try {
    const value = window.localStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function storageSet(key, value) {
  try {
    window.localStorage?.setItem(key, String(value));
  } catch (_) {}
}

function readStoredInfoLookbackHours() {
  return normalizeSessionFileLookbackHours(storageGet(infoLookbackHoursStorageKey));
}

function writeStoredInfoLookbackHours(hours) {
  const normalized = normalizeSessionFileLookbackHours(hours);
  storageSet(infoLookbackHoursStorageKey, normalized);
  return normalized;
}

let infoSessionFileLookbackHours = readStoredInfoLookbackHours();

function readStoredTabberLookbackHours() {
  return normalizeSessionFileLookbackHours(storageGet(fileExplorerTabberLookbackHoursStorageKey));
}

function writeStoredTabberLookbackHours(hours) {
  const normalized = normalizeSessionFileLookbackHours(hours);
  storageSet(fileExplorerTabberLookbackHoursStorageKey, normalized);
  return normalized;
}

let tabberSessionFileLookbackHours = readStoredTabberLookbackHours();

function sessionStorageGet(key, fallback = null) {
  try {
    const value = window.sessionStorage?.getItem(key);
    return value == null ? fallback : value;
  } catch (_) {
    return fallback;
  }
}

function sessionStorageSet(key, value) {
  try {
    window.sessionStorage?.setItem(key, String(value));
  } catch (_) {}
}

function fileExplorerClosedByUser() {
  return sessionStorageGet(fileExplorerOpenIntentStorageKey) === '0';
}

function rememberFileExplorerOpenIntent(open) {
  sessionStorageSet(fileExplorerOpenIntentStorageKey, open ? '1' : '0');
}

function safeJsonParse(raw, fallback = null) {
  try {
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function readStoredSet(key) {
  const parsed = safeJsonParse(storageGet(key), []);
  return new Set(Array.isArray(parsed) ? parsed.map(String) : []);
}

function readStoredJson(key, fallback = null) {
  return safeJsonParse(storageGet(key), fallback);
}

function readStoredPinnedTabs() {
  const parsed = readStoredJson(pinnedTabsStorageKey, []);
  if (!Array.isArray(parsed)) return [];
  const result = [];
  for (const raw of parsed) {
    const item = String(raw || '').trim();
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function writeStoredPinnedTabs() {
  storageSet(pinnedTabsStorageKey, JSON.stringify(pinnedTabItems));
}

function normalizeFileStateRecord(state) {
  if (!state || typeof state !== 'object') state = {};
  if (!(state.editorTabItems instanceof Set)) state.editorTabItems = new Set();
  if (!(state.ownerSessions instanceof Set)) state.ownerSessions = new Set();
  if (!(state.viewMode instanceof Map)) state.viewMode = new Map();
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = null;
  if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = false;
  return state;
}

function physicalFileIdentityFromPayload(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const explicit = String(payload.file_identity || payload.fileIdentity || '').trim();
  if (explicit) return explicit;
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  if (fileId) return `id:${fileId}`;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  return realpath ? `realpath:${realpath}` : '';
}

function applyFileIdentityMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  const realpath = String(payload.realpath || payload.realPath || '').trim();
  const fileId = String(payload.file_id || payload.fileId || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (realpath) state.realpath = realpath;
  if (fileId) state.fileId = fileId;
  if (identity) state.fileIdentity = identity;
  return state;
}

function registerFileIdentityForPath(path, payload) {
  const normalized = String(path || '').trim();
  const identity = physicalFileIdentityFromPayload(payload);
  if (!normalized || !identity) return '';
  applyFileIdentityMetadata(ensureFileState(normalized), payload);
  return identity;
}

function primaryOpenPathForFileIdentity(identity) {
  const text = String(identity || '').trim();
  if (!text) return '';
  for (const [path, state] of fileState.entries()) {
    if (state?.externalMissing === true) continue;
    if (physicalFileIdentityFromPayload(state) === text) {
      return path;
    }
  }
  return '';
}

function openPathForPhysicalFile(path, payload = null) {
  const identity = registerFileIdentityForPath(path, payload) || physicalFileIdentityFromPayload(fileStateFor(path)) || physicalFileIdentityFromPayload(payload);
  return primaryOpenPathForFileIdentity(identity);
}

function normalizedFileGitHistory(value) {
  return Array.isArray(value) ? value.filter(item => item && typeof item === 'object' && item.ref) : [];
}

function applyFileGitMetadata(state, payload) {
  if (!state || typeof state !== 'object' || !payload || typeof payload !== 'object') return state;
  applyFileIdentityMetadata(state, payload);
  const gitHistory = normalizedFileGitHistory(payload.git_history);
  state.gitRoot = payload.git_root ? normalizeDirectoryPath(payload.git_root) : '';
  state.gitTracked = payload.git_tracked === true;
  state.gitHistory = gitHistory;
  state.gitHasHistory = payload.git_has_history === true && gitHistory.length > 1;
  return state;
}

function fileStateHasRepo(path, state) {
  const root = state?.gitRoot ? normalizeDirectoryPath(state.gitRoot) : '';
  const normalized = normalizeDirectoryPath(path || '');
  return Boolean(root && normalized && pathIsInsideDirectory(normalized, root));
}

function fileStateHasUsefulGitHistory(state) {
  const readMetadataHasHistory = state?.gitTracked === true
    && state?.gitHasHistory === true
    && Array.isArray(state.gitHistory)
    && state.gitHistory.length > 1;
  const missingWorkingSideHasHistory = state?.diffLoaded === true
    && state?.diffUnavailable !== true
    && state?.diffWorkingMissing === true
    && Boolean(state?.diff || state?.diffOriginal);
  return readMetadataHasHistory || missingWorkingSideHasHistory;
}

function ensureFileState(path, defaults = null) {
  if (!path) return null;
  let state = fileState.get(path);
  if (!state) {
    state = defaults && typeof defaults === 'object' ? defaults : {};
    fileState.set(path, state);
  } else if (defaults && typeof defaults === 'object' && state !== defaults) {
    Object.assign(state, defaults);
  }
  return normalizeFileStateRecord(state);
}

function fileStateFor(path) {
  const state = path ? fileState.get(path) : null;
  return state ? normalizeFileStateRecord(state) : null;
}

function setFileState(path, state) {
  if (!path) return null;
  const previous = fileStateFor(path);
  if (previous && previous !== state && state && typeof state === 'object') {
    if (!(state.editorTabItems instanceof Set)) state.editorTabItems = previous.editorTabItems;
    if (!(state.ownerSessions instanceof Set)) state.ownerSessions = previous.ownerSessions;
    if (!(state.viewMode instanceof Map)) state.viewMode = previous.viewMode;
    if (!(state.previewZoom instanceof Map)) state.previewZoom = previous.previewZoom;
    if (!Object.prototype.hasOwnProperty.call(state, 'diffPinnedFromRef')) state.diffPinnedFromRef = previous.diffPinnedFromRef;
    if (!Object.prototype.hasOwnProperty.call(state, 'diffPinnedToRef')) state.diffPinnedToRef = previous.diffPinnedToRef;
    if (!Object.prototype.hasOwnProperty.call(state, 'imageMode')) state.imageMode = previous.imageMode;
    if (!Object.prototype.hasOwnProperty.call(state, 'blame')) state.blame = previous.blame;
    if (!Object.prototype.hasOwnProperty.call(state, 'conflictDialogOpen')) state.conflictDialogOpen = previous.conflictDialogOpen;
    if (!Object.prototype.hasOwnProperty.call(state, 'realpath')) state.realpath = previous.realpath;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileId')) state.fileId = previous.fileId;
    if (!Object.prototype.hasOwnProperty.call(state, 'fileIdentity')) state.fileIdentity = previous.fileIdentity;
    if (!Object.prototype.hasOwnProperty.call(state, 'openPromise')) state.openPromise = previous.openPromise;
  }
  const normalized = normalizeFileStateRecord(state);
  fileState.set(path, normalized);
  return normalized;
}

function deleteFileState(path) {
  if (!path) return false;
  return fileState.delete(path);
}

function fileOpenPromiseFor(path) {
  return fileStateFor(path)?.openPromise || null;
}

function setFileOpenPromise(path, promise) {
  const state = ensureFileState(path);
  if (state) state.openPromise = promise;
  return promise;
}

function clearFileOpenPromise(path, promise) {
  const state = fileStateFor(path);
  if (state?.openPromise === promise) delete state.openPromise;
}

function fileEditorTabItemsForPath(path) {
  return Array.from(fileStateFor(path)?.editorTabItems || []);
}

function addFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  const state = ensureFileState(path);
  if (state && item) state.editorTabItems.add(item);
}

function removeFileEditorTabItem(path, item = fileEditorItemFor(path)) {
  fileStateFor(path)?.editorTabItems.delete(item);
}

function fileEditorViewModesForPath(path, create = false) {
  const state = create ? ensureFileState(path) : fileStateFor(path);
  return state?.viewMode || new Map();
}

function normalizedPreviewZoomKey(key) {
  return String(key || 'default');
}

function normalizePreviewZoomState(value) {
  const mode = value?.mode === 'manual' || value?.mode === 'actual' ? value.mode : 'fit';
  const scale = Number.parseFloat(String(value?.scale || ''));
  return {
    mode,
    scale: Number.isFinite(scale) && scale > 0 ? scale : 1,
  };
}

function fileEditorPreviewZoomStateForPath(path, key = 'default') {
  const state = fileStateFor(path);
  return normalizePreviewZoomState(state?.previewZoom?.get(normalizedPreviewZoomKey(key)));
}

function setFileEditorPreviewZoomStateForPath(path, key, zoomState) {
  const state = ensureFileState(path);
  if (!state) return;
  if (!(state.previewZoom instanceof Map)) state.previewZoom = new Map();
  state.previewZoom.set(normalizedPreviewZoomKey(key), normalizePreviewZoomState(zoomState));
}

function resetFileEditorPreviewZoomStateForPath(path, keyPrefix = '') {
  const state = fileStateFor(path);
  if (!(state?.previewZoom instanceof Map)) return false;
  const prefix = normalizedPreviewZoomKey(keyPrefix);
  let changed = false;
  for (const key of Array.from(state.previewZoom.keys())) {
    if (!prefix || key === prefix || key.startsWith(`${prefix}:`)) {
      state.previewZoom.delete(key);
      changed = true;
    }
  }
  return changed;
}

function editorBlameForPath(path) {
  return fileStateFor(path)?.blame || null;
}

function setEditorBlameForPath(path, blame) {
  const state = ensureFileState(path);
  if (state) state.blame = blame || null;
}

function hasEditorBlameForPath(path) {
  return Boolean(editorBlameForPath(path));
}

function fileConflictDialogOpen(path) {
  return fileStateFor(path)?.conflictDialogOpen === true;
}

function setFileConflictDialogOpen(path, open) {
  const state = ensureFileState(path);
  if (state) state.conflictDialogOpen = open === true;
}

function repoRootKey(value) {
  return String(value || '').replace(/\/+$/, '');
}

// `work_graph` owns repository identity. These selectors are deliberately shared by the tab,
// popover, layout, Finder/Differ, and command-palette paths so every surface chooses the same
// canonical branch and PR edges.
function sessionWorkGraph(info = {}) {
  const graph = info?.work_graph;
  const required = ['git_worktrees', 'local_repositories', 'local_branches', 'hosted_repositories', 'pull_requests', 'linear_issues', 'path_observations', 'runtime_actors', 'tmux_sessions', 'tmux_windows', 'tmux_panes', 'worktree_branch_activity'];
  return graph?.version === 1 && required.every(key => graph[key] && typeof graph[key] === 'object') ? graph : null;
}

function graphEntityValues(graph, key, ids = []) {
  return (ids || []).map(id => graph?.[key]?.[id]).filter(Boolean);
}

function graphTmuxPaneIdsForTarget(graph, tmuxTarget = '') {
  const target = String(tmuxTarget || '').trim();
  const panes = Object.values(graph?.tmux_panes || {});
  if (target) {
    const match = panes.find(pane => String(pane?.target || '') === target || String(pane?.id || '') === target);
    if (match?.id) return [match.id];
  }
  const active = panes.filter(pane => pane?.active === true || pane?.window_active === true).map(pane => pane.id).filter(Boolean);
  return active.length ? active : panes.map(pane => pane?.id).filter(Boolean);
}

function graphObservationIdsForTmuxPaneIds(graph, tmuxPaneIds = []) {
  const ids = new Set();
  for (const pane of graphEntityValues(graph, 'tmux_panes', tmuxPaneIds)) {
    for (const id of pane.path_observation_ids || []) ids.add(id);
    for (const actor of graphEntityValues(graph, 'runtime_actors', pane.runtime_actor_ids)) {
      for (const id of actor.path_observation_ids || []) ids.add(id);
    }
  }
  return [...ids];
}

function focusedRepositoryIdsForTmuxTarget(info, tmuxTarget = '') {
  const graph = sessionWorkGraph(info);
  if (!graph) return [];
  const ids = new Set();
  for (const observation of graphEntityValues(graph, 'path_observations', graphObservationIdsForTmuxPaneIds(graph, graphTmuxPaneIdsForTarget(graph, tmuxTarget)))) {
    const worktree = graph.git_worktrees?.[observation.git_worktree_id];
    if (worktree?.local_repository_id) ids.add(worktree.local_repository_id);
  }
  return [...ids];
}

function focusedGitWorktreeIdsForTmuxTarget(info, tmuxTarget = '') {
  const graph = sessionWorkGraph(info);
  if (!graph) return [];
  const ids = new Set();
  for (const observation of graphEntityValues(graph, 'path_observations', graphObservationIdsForTmuxPaneIds(graph, graphTmuxPaneIdsForTarget(graph, tmuxTarget)))) {
    if (observation.git_worktree_id) ids.add(observation.git_worktree_id);
  }
  return [...ids];
}

function branchIdsForGitWorktree(info, gitWorktreeId) {
  const graph = sessionWorkGraph(info);
  const worktree = graph?.git_worktrees?.[gitWorktreeId];
  const repository = graph?.local_repositories?.[worktree?.local_repository_id];
  return repository?.local_branch_ids ? [...repository.local_branch_ids] : [];
}

function focusedBranchIdsForTmuxTarget(info, tmuxTarget = '') {
  const graph = sessionWorkGraph(info);
  if (!graph) return [];
  const ids = new Set();
  for (const worktreeId of focusedGitWorktreeIdsForTmuxTarget(info, tmuxTarget)) {
    const branchId = graph.git_worktrees?.[worktreeId]?.current_branch_id;
    if (branchId) ids.add(branchId);
  }
  return [...ids];
}

function pullRequestIdsForTmuxTarget(info, tmuxTarget = '') {
  const graph = sessionWorkGraph(info);
  if (!graph) return [];
  const ids = new Set();
  for (const branch of graphEntityValues(graph, 'local_branches', focusedBranchIdsForTmuxTarget(info, tmuxTarget))) {
    for (const prId of branch.pull_request_ids || []) ids.add(prId);
  }
  return [...ids];
}

function graphBranchSummary(graph, branchId, currentBranchId = '') {
  const branch = graph?.local_branches?.[branchId];
  if (!branch) return null;
  const pullRequests = graphEntityValues(graph, 'pull_requests', branch.pull_request_ids);
  const linear = graphEntityValues(graph, 'linear_issues', branch.linear_issue_ids);
  return {...branch, current: String(branch.id) === String(currentBranchId), pull_requests: pullRequests, pull_request: pullRequests[0] || null, linear};
}

function branchUpdatedCommitTimestamp(branch) {
  const timestamp = Number(branch?.updated_ts ?? branch?.updatedTs ?? 0);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function branchesNewestCommitFirst(branches = []) {
  return (Array.isArray(branches) ? branches : []).slice().sort((left, right) => (
    branchUpdatedCommitTimestamp(right) - branchUpdatedCommitTimestamp(left)
  ));
}

function graphRepoSummary(graph, worktree) {
  const repository = graph?.local_repositories?.[worktree?.local_repository_id] || {};
  const hostedRepository = graph?.hosted_repositories?.[worktree?.hosted_repository_id || repository?.hosted_repository_id] || null;
  const branches = (repository.local_branch_ids || []).map(branchId => graphBranchSummary(graph, branchId, worktree?.current_branch_id)).filter(Boolean);
  const currentBranch = branches.find(branch => branch.current) || null;
  const root = repoRootKey(worktree?.root || '');
  return {
    id: String(worktree?.id || ''),
    worktree_id: String(worktree?.id || ''),
    local_repository_id: String(repository?.id || ''),
    hosted_repository_id: String(hostedRepository?.id || ''),
    root,
    cwd: root,
    branch: String(currentBranch?.name || ''),
    // Branch identity belongs to the LocalGitRepository, while checkout state belongs to this
    // GitWorktree. Repositories can share branches across linked worktrees with different dirty
    // and ahead/behind state, so never project those worktree fields from the branch record.
    ahead: worktree?.git?.ahead,
    behind: worktree?.git?.behind,
    dirty_count: worktree?.git?.dirty_count,
    head: worktree?.git?.head || '',
    upstream: worktree?.git?.upstream || '',
    activity_ts: worktree?.activity_ts ?? currentBranch?.updated_ts,
    activity_source: worktree?.activity_source || '',
    github_repo: hostedRepository,
    other_branches: {branches, hidden_count: 0},
    worktree: {path: root, kind: worktree?.kind || '', parent_root: worktree?.parent_root || ''},
  };
}

function sessionWorkSummary(session, info = {}, options = {}) {
  const graph = sessionWorkGraph(info);
  const target = String(options.tmuxTarget || options.target || info?.selected_pane?.target || '');
  if (graph) {
    const repositories = Object.values(graph.git_worktrees || {}).map(worktree => graphRepoSummary(graph, worktree)).filter(repo => repo.root);
    const focusedWorktreeIds = focusedGitWorktreeIdsForTmuxTarget(info, target);
    const focusedRepositoryIds = focusedRepositoryIdsForTmuxTarget(info, target);
    const focusedBranchIds = focusedBranchIdsForTmuxTarget(info, target);
    const pullRequestIds = pullRequestIdsForTmuxTarget(info, target);
    const focusedRepo = repositories.find(repo => focusedRepositoryIds.includes(repo.local_repository_id)) || repositories.find(repo => focusedWorktreeIds.includes(repo.worktree_id)) || repositories[0] || null;
    const observations = graphEntityValues(graph, 'path_observations', graphObservationIdsForTmuxPaneIds(graph, graphTmuxPaneIdsForTarget(graph, target)));
    const selectedPath = observations.sort((left, right) => Number(right?.last_observed_at || 0) - Number(left?.last_observed_at || 0)).find(observation => observation?.path)?.path || focusedRepo?.cwd || '';
    const pullRequests = graphEntityValues(graph, 'pull_requests', pullRequestIds)
      .sort((left, right) => Number(right?.updated_at || right?.updated_ts || 0) - Number(left?.updated_at || left?.updated_ts || 0) || Number(left?.number || 0) - Number(right?.number || 0));
    const linearIssues = graphEntityValues(graph, 'linear_issues', [...new Set(graphEntityValues(graph, 'local_branches', focusedBranchIds).flatMap(branch => branch.linear_issue_ids || []))]);
    const focusedPullRequest = pullRequests.length === 1 ? pullRequests[0] : null;
    return {graph, graphBacked: true, repositories, focusedWorktreeIds, focusedRepositoryIds, focusedBranchIds, pullRequestIds, pullRequests, focusedPullRequest, linearIssues, selectedRepo: focusedRepo, git: focusedRepo ? gitFromRepoSummary(focusedRepo) : null, selectedPath};
  }
  return {graph: null, graphBacked: false, repositories: [], focusedWorktreeIds: [], focusedRepositoryIds: [], focusedBranchIds: [], pullRequestIds: [], pullRequests: [], focusedPullRequest: null, linearIssues: [], selectedRepo: null, git: null, selectedPath: info?.selected_pane?.current_path || ''};
}

// Normalized view of a session's transcript metadata. A schema-valid graph is authoritative even
// when empty; metadata without one has no Git work rather than a hidden compatibility path.
function sessionTranscriptInfo(session) {
  const info = transcriptMetadataState.payload.sessions?.[session] || {};
  const summary = sessionWorkSummary(session, info);
  const git = summary.git || {};
  return {gitRoot: git.root || '', gitCwd: git.cwd || '', gitBranch: git.branch || '', selectedPath: summary.selectedPath || '', info};
}

function sessionRepoSummaries(info) {
  const summary = sessionWorkSummary('', info);
  if (summary.graphBacked) return summary.repositories;
  return summary.repositories;
}

function selectedSessionRepoIndex(session, info) {
  const repos = sessionRepoSummaries(info);
  if (!repos.length) return -1;
  const selectedRoot = repoRootKey(sessionRepoDisplayRoot.get(session));
  const selectedIndex = selectedRoot ? repos.findIndex(repo => repoRootKey(repo.root) === selectedRoot) : -1;
  return selectedIndex >= 0 ? selectedIndex : 0;
}

function selectedSessionRepo(session, info) {
  const repos = sessionRepoSummaries(info);
  const index = selectedSessionRepoIndex(session, info);
  return index >= 0 ? repos[index] : null;
}

function gitFromRepoSummary(repo) {
  if (!repo) return null;
  return {
    root: repo.root || '',
    cwd: repo.cwd || repo.root || '',
    branch: repo.branch || '',
    ahead: repo.ahead,
    behind: repo.behind,
    dirty_count: repo.dirty_count,
    head: repo.head || '',
    upstream: repo.upstream || '',
    activity_ts: repo.activity_ts,
    activity_source: repo.activity_source || '',
    github_repo: repo.github_repo || null,
    other_branches: repo.other_branches || null,
    worktree: repo.worktree || null,
  };
}

function displayedSessionGit(session, info) {
  const summary = sessionWorkSummary(session, info);
  const git = summary.git;
  const repo = selectedSessionRepo(session, info);
  if (!repo) return git;
  if (git && repoRootKey(git.root) === repoRootKey(repo.root)) return git;
  return gitFromRepoSummary(repo);
}

function cycleSessionRepoDisplay(session, info, direction) {
  const repos = sessionRepoSummaries(info);
  if (repos.length < 2) return null;
  const current = selectedSessionRepoIndex(session, info);
  const delta = Number(direction) < 0 ? -1 : 1;
  const next = (Math.max(0, current) + delta + repos.length) % repos.length;
  sessionRepoDisplayRoot.set(session, repos[next].root);
  return repos[next];
}

// Centralized status-line writers: the err/ok pill markup is defined here, not re-inlined at the ~55
// call sites that report a result. Both take already-built (and esc'd) inner HTML.
function resetLayoutStatusSurface() {
  statusEl.classList.remove('layout-status-visible', 'layout-status-danger', 'layout-status-advisory');
  statusEl.removeAttribute('data-layout-status-kind');
  delete statusEl.dataset.layoutStatusKind;
}

function statusErr(html) {
  resetLayoutStatusSurface();
  statusEl.innerHTML = `<span class="err">${html}</span>`;
}

function statusOk(html) {
  resetLayoutStatusSurface();
  statusEl.innerHTML = `<span class="ok">${html}</span>`;
}

function showLayoutStatus(message, kind = '') {
  const tone = kind === 'danger' || kind === 'advisory' ? kind : '';
  statusEl.textContent = String(message || '');
  resetLayoutStatusSurface();
  if (tone && statusEl.textContent.trim()) {
    statusEl.classList.add('layout-status-visible', `layout-status-${tone}`);
    statusEl.dataset.layoutStatusKind = tone;
  }
}

function localizedHtml(key, params) {
  return esc(t(key, params));
}

function terminalNotConnectedText(session) {
  return t('terminal.connection.notConnected', {session: sessionLabel(session)});
}

function terminalNotConnectedHtml(session) {
  return esc(terminalNotConnectedText(session));
}

function readStoredTabMetaVisible() {
  return storageGet(tabMetaStorageKey) !== '0';  // absent (null) or anything but '0' => visible
}

function writeStoredTabMetaVisible(value) {
  storageSet(tabMetaStorageKey, value ? '1' : '0');
}

// Legacy share/deeplink marker from the old merged YO!info/YO!agent pane.
function normalizedInfoSubTab(value) {
  return value === 'yoagent' ? 'yoagent' : 'info';
}

function readStoredInfoSubTab() {
  return normalizedInfoSubTab(storageGet(infoSubTabStorageKey));
}

function writeStoredInfoSubTab(value) {
  storageSet(infoSubTabStorageKey, normalizedInfoSubTab(value));
}

function readStoredEditorWrap() {
  return storageGet(fileEditorWrapStorageKey) === '1';
}

function writeStoredEditorWrap(value) {
  storageSet(fileEditorWrapStorageKey, value ? '1' : '0');
}

function readStoredEditorLineNumbers() {
  return storageGet(fileEditorLineNumbersStorageKey) === '1';
}

function writeStoredEditorLineNumbers(value) {
  storageSet(fileEditorLineNumbersStorageKey, value ? '1' : '0');
}

function defaultCollapsedPreferenceSections() {
  return new Set(DEFAULT_COLLAPSED_PREFERENCE_SECTION_IDS);
}

function normalizeCollapsedPreferenceSections(values, sections = []) {
  const validIds = new Set(Object.values(PREFERENCE_SECTION_IDS));
  const legacyTitleIds = new Map(Object.entries(LEGACY_PREFERENCE_SECTION_IDS_BY_ENGLISH_TITLE));
  for (const section of sections) {
    const id = String(section?.id || '');
    const title = String(section?.title || '');
    if (validIds.has(id) && title) legacyTitleIds.set(title, id);
  }
  return new Set(Array.from(values || [], value => {
    const text = String(value || '');
    if (validIds.has(text)) return text;
    return legacyTitleIds.get(text) || '';
  }).filter(Boolean));
}

function readStoredCollapsedPreferenceSections() {
  const raw = storageGet(preferencesCollapsedStorageKey);
  if (!raw) return defaultCollapsedPreferenceSections();
  const parsed = safeJsonParse(raw, null);
  return Array.isArray(parsed) ? normalizeCollapsedPreferenceSections(parsed) : defaultCollapsedPreferenceSections();
}

function writeStoredCollapsedPreferenceSections() {
  storageSet(preferencesCollapsedStorageKey, JSON.stringify(Array.from(collapsedPreferenceSections)));
}

function setCollapsedPreferenceSections(values, options = {}) {
  const previousIds = Array.from(collapsedPreferenceSections || []);
  const next = normalizeCollapsedPreferenceSections(values, options.sections || []);
  const nextIds = Array.from(next);
  collapsedPreferenceSections = next;
  if (options.persist === true && (previousIds.length !== nextIds.length || previousIds.some((id, index) => id !== nextIds[index]))) {
    writeStoredCollapsedPreferenceSections();
  }
  return collapsedPreferenceSections;
}

function cleanDiffRef(value, fallback = '') {
  const text = String(value || '').trim();
  if (!text) return fallback;
  if (/[\x00\r\n]/.test(text)) return fallback;
  return text;
}

function readStoredDiffRef(key, fallback) {
  return cleanDiffRef(storageGet(key), fallback);
}

function writeStoredDiffRefs() {
  storageSet(diffRefFromStorageKey, diffRefFrom);
  storageSet(diffRefToStorageKey, diffRefTo);
  // C6: persist the per-repo overrides alongside the global default.
  try {
    storageSet(diffRefsByRepoStorageKey, JSON.stringify(diffRefsByRepo || {}));
  } catch (_error) {
    storageSet(diffRefsByRepoStorageKey, '{}');
  }
}

function readStoredDiffRefsByRepo() {
  // C6: restore {repoPath: {from, to}}; tolerate corrupt/absent storage by returning an empty map.
  const parsed = safeJsonParse(storageGet(diffRefsByRepoStorageKey), null);
  if (!parsed || typeof parsed !== 'object') return {};
  const result = {};
  for (const [repo, refs] of Object.entries(parsed)) {
    if (typeof repo !== 'string' || !refs || typeof refs !== 'object') continue;
    const from = cleanDiffRef(refs.from, '');
    const to = cleanDiffRef(refs.to, '');
    if (from || to) result[repo] = {from: from || 'HEAD', to: to || 'current'};
  }
  return result;
}

function normalizeFileExplorerTreeDateMode(value) {
  return fileExplorerTreeDateModes.includes(value) ? value : 'none';
}

function readStoredFileExplorerTreeDateMode() {
  const value = storageGet(fileExplorerTreeDateModeStorageKey);
  if (value !== null) return normalizeFileExplorerTreeDateMode(value);
  return storageGet(fileExplorerTreeShowDatesStorageKey) === '1' ? 'date' : 'none';
}

function writeStoredFileExplorerTreeDateMode(value) {
  storageSet(fileExplorerTreeDateModeStorageKey, normalizeFileExplorerTreeDateMode(value));
}

function normalizeSessionFilesSortMode(value) {
  if (value === 'mtime') return 'newest';
  if (value === 'name') return 'az';
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'newest';
}

function readStoredFileExplorerTreeSortMode() {
  const value = storageGet(fileExplorerTreeSortStorageKey);
  return ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az';
}

function writeStoredFileExplorerTreeSortMode(value) {
  storageSet(fileExplorerTreeSortStorageKey, ['az', 'za', 'newest', 'oldest'].includes(value) ? value : 'az');
}

function normalizeFileExplorerView(view) {
  return ['finder', 'tabber', 'differ'].includes(view) ? view : 'finder';
}

function normalizeFileExplorerViewSettings(value, fallback = {}) {
  const settings = value && typeof value === 'object' ? value : {};
  return {
    treeDateMode: normalizeFileExplorerTreeDateMode(settings.treeDateMode ?? fallback.treeDateMode),
    treeSortMode: normalizeSessionFilesSortMode(settings.treeSortMode ?? fallback.treeSortMode),
  };
}

function readStoredFileExplorerViewSettings() {
  const legacyFinder = {
    treeDateMode: readStoredFileExplorerTreeDateMode(),
    treeSortMode: readStoredFileExplorerTreeSortMode(),
  };
  const stored = readStoredJson(fileExplorerViewSettingsStorageKey, null);
  const tabberFallback = legacyFinder;
  const differFallback = {treeDateMode: legacyFinder.treeDateMode, treeSortMode: 'newest'};
  return {
    finder: normalizeFileExplorerViewSettings(stored?.finder, legacyFinder),
    tabber: normalizeFileExplorerViewSettings(stored?.tabber, tabberFallback),
    differ: normalizeFileExplorerViewSettings(stored?.differ, differFallback),
  };
}

function fileExplorerViewSettingsFor(view = 'finder') {
  const key = normalizeFileExplorerView(view);
  const fallback = key === 'differ' ? {treeDateMode: 'none', treeSortMode: 'newest'} : {treeDateMode: 'none', treeSortMode: 'az'};
  const settings = normalizeFileExplorerViewSettings(fileExplorerViewSettings?.[key], fallback);
  if (!fileExplorerViewSettings || fileExplorerViewSettings[key] !== settings) {
    fileExplorerViewSettings = {...(fileExplorerViewSettings || {}), [key]: settings};
  }
  return settings;
}

function fileExplorerTreeDateModeForView(view = 'finder') {
  return fileExplorerViewSettingsFor(view).treeDateMode;
}

function fileExplorerTreeSortModeForView(view = 'finder') {
  return fileExplorerViewSettingsFor(view).treeSortMode;
}

function writeStoredFileExplorerViewSettings() {
  storageSet(fileExplorerViewSettingsStorageKey, JSON.stringify(fileExplorerViewSettings));
}

function setFileExplorerViewSetting(view, key, value, options = {}) {
  const surface = normalizeFileExplorerView(view);
  const current = fileExplorerViewSettingsFor(surface);
  const next = normalizeFileExplorerViewSettings({...current, [key]: value}, current);
  if (next.treeDateMode === current.treeDateMode && next.treeSortMode === current.treeSortMode) return false;
  fileExplorerViewSettings = {...fileExplorerViewSettings, [surface]: next};
  writeStoredFileExplorerViewSettings();
  if (options.refresh !== false) refreshFileExplorerViewSettingsSurface(surface);
  return true;
}

// URL state uses one payload shape. Keep legacy-field migration here so restored URLs cannot
// teach the three fixed surfaces different rules.
function applyFileExplorerViewSettingsSeed(seed = {}) {
  if (!seed || typeof seed !== 'object') return;
  if (seed.viewSettings && typeof seed.viewSettings === 'object') {
    for (const view of ['finder', 'tabber', 'differ']) {
      if (seed.viewSettings[view]) fileExplorerViewSettings = {...fileExplorerViewSettings, [view]: normalizeFileExplorerViewSettings(seed.viewSettings[view], fileExplorerViewSettingsFor(view))};
    }
    return;
  }
  if ('treeDateMode' in seed) fileExplorerViewSettings = {...fileExplorerViewSettings, finder: {...fileExplorerViewSettingsFor('finder'), treeDateMode: normalizeFileExplorerTreeDateMode(seed.treeDateMode)}};
  if ('treeSortMode' in seed) fileExplorerViewSettings = {...fileExplorerViewSettings, finder: {...fileExplorerViewSettingsFor('finder'), treeSortMode: normalizeSessionFilesSortMode(seed.treeSortMode)}};
  if ('sessionFilesSortMode' in seed) fileExplorerViewSettings = {...fileExplorerViewSettings, differ: {...fileExplorerViewSettingsFor('differ'), treeSortMode: normalizeSessionFilesSortMode(seed.sessionFilesSortMode)}};
}

function normalizeStoredFileExplorerIndexedDir(path) {
  const normalized = normalizeDirectoryPath(expandUserPath(path));
  return normalized.startsWith('/') ? normalized : '';
}

function readStoredFileExplorerIndexedDirs() {
  const paths = readStoredJson(fileExplorerIndexedDirsStorageKey, []);
  return new Set((Array.isArray(paths) ? paths : []).map(normalizeStoredFileExplorerIndexedDir).filter(Boolean));
}

function writeStoredFileExplorerIndexedDirs() {
  const paths = Array.from(fileExplorerIndexedDirs || [])
    .map(normalizeStoredFileExplorerIndexedDir)
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right));
  storageSet(fileExplorerIndexedDirsStorageKey, JSON.stringify(Array.from(new Set(paths))));
}

// A synchronous SHA-256 over a string's UTF-8 bytes, returning the lowercase hex digest. The
// search-progress bus keys every root by the server's opaque digest sha256(canonical_root_key)[:16]
// (yolomux_lib/search/file_index.py::_root_scope_id): a filesystem path in a signal's payload would
// disclose one client's directory to every other client on the globally-fanned-out bus, so the frame
// carries only the digest. The browser recomputes the SAME digest from a snapshot's root_realpath to
// correlate a path-free progress signal back to the root it searched. crypto.subtle.digest is async
// and absent from the node test VM, so this is a self-contained synchronous implementation.
const SHA256_ROUND_CONSTANTS = Object.freeze([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function sha256HexOfString(text) {
  const bytes = new TextEncoder().encode(String(text ?? ''));
  const length = bytes.length;
  // Message padding: 0x80, then zeros to a 56-mod-64 boundary, then the 64-bit big-endian bit length.
  const withPadding = new Uint8Array((((length + 8) >> 6) + 1) << 6);
  withPadding.set(bytes);
  withPadding[length] = 0x80;
  const bitLength = length * 8;
  const view = new DataView(withPadding.buffer);
  view.setUint32(withPadding.length - 4, bitLength >>> 0, false);
  view.setUint32(withPadding.length - 8, Math.floor(bitLength / 0x100000000), false);
  const hash = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const words = new Uint32Array(64);
  const rotr = (value, bits) => (value >>> bits) | (value << (32 - bits));
  for (let offset = 0; offset < withPadding.length; offset += 64) {
    for (let i = 0; i < 16; i += 1) words[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 64; i += 1) {
      const s0 = rotr(words[i - 15], 7) ^ rotr(words[i - 15], 18) ^ (words[i - 15] >>> 3);
      const s1 = rotr(words[i - 2], 17) ^ rotr(words[i - 2], 19) ^ (words[i - 2] >>> 10);
      words[i] = (words[i - 16] + s0 + words[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = hash;
    for (let i = 0; i < 64; i += 1) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + SHA256_ROUND_CONSTANTS[i] + words[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    hash[0] = (hash[0] + a) >>> 0; hash[1] = (hash[1] + b) >>> 0;
    hash[2] = (hash[2] + c) >>> 0; hash[3] = (hash[3] + d) >>> 0;
    hash[4] = (hash[4] + e) >>> 0; hash[5] = (hash[5] + f) >>> 0;
    hash[6] = (hash[6] + g) >>> 0; hash[7] = (hash[7] + h) >>> 0;
  }
  return Array.from(hash, value => value.toString(16).padStart(8, '0')).join('');
}

// The 16-hex-char opaque root digest the search-progress bus uses (the first 16 hex chars of the
// SHA-256 of the canonical root path). Correlates a path-free progress signal to a searched root.
function fileSearchScopeId(realpath) {
  const canonical = String(realpath ?? '').trim();
  return canonical ? sha256HexOfString(canonical).slice(0, 16) : '';
}

function nestedSetting(source, path, fallback) {
  let current = source;
  for (const part of String(path || '').split('.')) {
    if (!part) continue;
    if (!current || typeof current !== 'object' || !(part in current)) return fallback;
    current = current[part];
  }
  return current === undefined || current === null ? fallback : current;
}

function settingFallback(path, fallback) {
  if (arguments.length >= 2) return fallback;
  return Object.prototype.hasOwnProperty.call(SETTING_FALLBACKS, path) ? SETTING_FALLBACKS[path] : undefined;
}

function initialSetting(path, fallback) {
  const defaultValue = arguments.length >= 2 ? fallback : settingFallback(path);
  return nestedSetting(clientSettings, path, nestedSetting(clientSettingsDefaults, path, defaultValue));
}

function themeBodyClass(mode) {
  return THEME_CLASS_BY_MODE[mode] || THEME_CLASS_BY_MODE.dark;
}

function themeResolvedBodyClass(mode) {
  return THEME_RESOLVED_CLASS_BY_MODE[mode] || THEME_RESOLVED_CLASS_BY_MODE.dark;
}

function editorThemeBodyClass(mode) {
  return EDITOR_THEME_CLASS_BY_MODE[mode] || EDITOR_THEME_CLASS_BY_MODE.dark;
}

function mergeSettingObjects(base, patch) {
  const result = Array.isArray(base) ? base.slice() : {...(base || {})};
  if (!patch || typeof patch !== 'object' || Array.isArray(patch)) return result;
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === 'object' && !Array.isArray(value) && result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
      result[key] = mergeSettingObjects(result[key], value);
    } else {
      result[key] = Array.isArray(value) ? value.slice() : value;
    }
  }
  return result;
}

function readStoredFileExplorerRootMode() {
  return storageGet(fileExplorerRootModeStorageKey) === 'fixed' ? 'fixed' : 'sync';
}

function writeStoredFileExplorerRootMode(mode) {
  storageSet(fileExplorerRootModeStorageKey, mode === 'sync' ? 'sync' : 'fixed');
}

function normalizeFileExplorerMode(mode) {
  return mode === 'diff' || mode === 'tabber' ? mode : 'files';
}

function fileExplorerModeFromUrlParam(value) {
  const mode = String(value || '').trim().toLowerCase();
  if (mode === 'finder' || mode === 'files') return 'files';
  if (mode === 'differ' || mode === 'diff') return 'diff';
  if (mode === 'tabber') return 'tabber';
  return '';
}

function readStoredFileExplorerMode() {
  const stored = storageGet(fileExplorerModeStorageKey);
  if (stored === 'diff' || stored === 'files') return stored;
  return storageGet(legacyFileExplorerChangesHiddenStorageKey) === '0' ? 'diff' : 'files';
}

function writeStoredFileExplorerMode(mode) {
  storageSet(fileExplorerModeStorageKey, normalizeFileExplorerMode(mode));
}

function normalizeEditorSchemeId(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'light' || normalized === 'white') return defaultLightEditorScheme;
  const legacySchemePrefix = ['vs', 'code'].join('');
  const aliases = {
    [`${legacySchemePrefix}-dark-plus`]: 'popular-ide-dark-plus',
    [`${legacySchemePrefix}-light-plus`]: 'popular-ide-light-plus',
  };
  const id = aliases[normalized] || normalized;
  return EDITOR_SCHEMES[id] ? id : defaultEditorScheme;
}

function normalizeGlobalThemeMode(value = globalThemeMode) {
  // Default to the LIVE globalThemeMode (like resolvedGlobalThemeMode) so a no-arg call reflects the
  // current theme — calling it with no argument used to fall through to defaultGlobalTheme ('dark'),
  // which made the View -> Theme menu always mark Dark as active regardless of the real theme.
  const normalized = String(value || '').trim().toLowerCase();
  return ['system', 'dark', 'light'].includes(normalized) ? normalized : defaultGlobalTheme;
}

function globalThemeMediaQuery() {
  try { return window.matchMedia?.('(prefers-color-scheme: dark)') || null; }
  catch (_) { return null; }
}

function systemPrefersDarkTheme() {
  const query = globalThemeMediaQuery();
  return query ? query.matches === true : true;
}

function normalizeResolvedGlobalThemeMode(value = '') {
  const normalized = String(value || '').trim().toLowerCase();
  return normalized === 'dark' || normalized === 'light' ? normalized : '';
}

function resolvedGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return systemPrefersDarkTheme() ? 'dark' : 'light';
  return normalized;
}

function globalThemeIsDark(mode = globalThemeMode) {
  return resolvedGlobalThemeMode(mode) === 'dark';
}

function globalThemeLabel(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return t('pref.appearance.theme.systemResolved', {
    resolved: t(`pref.appearance.theme.${resolvedGlobalThemeMode(mode)}`),
  });
  return t(`pref.appearance.theme.${normalized}`);
}

function nextGlobalThemeMode(mode = globalThemeMode) {
  const normalized = normalizeGlobalThemeMode(mode);
  if (normalized === 'system') return 'dark';
  if (normalized === 'dark') return 'light';
  return 'system';
}

function normalizeTerminalThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return ['dark', 'light', 'follow-app'].includes(normalized) ? normalized : defaultTerminalTheme;
}

function resolvedTerminalThemeMode(mode = terminalThemeMode, appMode = globalThemeMode) {
  const normalized = normalizeTerminalThemeMode(mode);
  return normalized === 'follow-app' ? resolvedGlobalThemeMode(appMode) : normalized;
}

function terminalThemeForGlobalTheme(mode = globalThemeMode) {
  const theme = TERMINAL_THEMES[resolvedTerminalThemeMode(terminalThemeMode, mode)] || TERMINAL_THEMES.dark;
  return {...theme};
}

// on a WHITE (light) terminal, agents emit 24-bit truecolor escapes tuned for a dark
// terminal that render faint on white. xterm's minimumContrastRatio auto-darkens ANY text color
// (including app 24-bit colors) against the bg.
// the DARK terminal used to keep 1 (no adjustment), which left low-contrast cells alone — so
// an agent composer that draws light text on an ANSI-white box (Codex's input, ~contrast 1) was
// white-on-white. Use a moderate 3 for dark: enough to force that composer to a readable foreground,
// low enough that intentionally-dim dark-palette text (already at/above 3:1) is mostly untouched. Light
// stays at the stricter WCAG-AA 4.5 (faint colors on white need more help).
function terminalMinimumContrastRatio(mode = globalThemeMode) {
  return resolvedTerminalThemeMode(terminalThemeMode, mode) === 'light' ? 4.5 : 3;
}

function normalizeEditorThemeMode(value) {
  const normalized = String(value || '').trim().toLowerCase();
  if (['inherit', 'system', 'global', 'auto', ''].includes(normalized)) return editorThemeInheritMode;
  return normalizeEditorSchemeId(normalized);
}

function normalizeEditorPreviewDisplayMode(value) {
  return String(value || '').trim().toLowerCase() === 'vanilla' ? 'vanilla' : 'theme';
}

function normalizeEditorSchemeForMode(value, dark) {
  const id = normalizeEditorSchemeId(value);
  const scheme = EDITOR_SCHEMES[id];
  if (scheme && scheme.dark === dark) return id;
  return dark ? defaultEditorScheme : defaultLightEditorScheme;
}

function activeEditorScheme() {
  if (fileEditorThemeMode === editorThemeInheritMode) {
    const inherited = configuredEditorSchemeForMode(globalThemeIsDark());
    return EDITOR_SCHEMES[inherited] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
  }
  return EDITOR_SCHEMES[normalizeEditorSchemeId(fileEditorThemeMode)] || EDITOR_SCHEMES[defaultEditorScheme] || EDITOR_SCHEMES.dark;
}

function configuredEditorSchemeForMode(dark) {
  const path = dark ? 'appearance.editor_dark_color_scheme' : 'appearance.editor_light_color_scheme';
  const fallback = dark ? defaultEditorScheme : defaultLightEditorScheme;
  return normalizeEditorSchemeForMode(initialSetting(path, fallback), dark);
}

function readStoredEditorThemeMode() {
  return normalizeEditorThemeMode(storageGet(fileEditorThemeModeStorageKey) || editorThemeInheritMode);
}

function writeStoredEditorThemeMode(mode) {
  storageSet(fileEditorThemeModeStorageKey, normalizeEditorThemeMode(mode));
}

function readStoredEditorPreviewDisplayMode() {
  return normalizeEditorPreviewDisplayMode(storageGet(fileEditorPreviewDisplayModeStorageKey) || 'theme');
}

function writeStoredEditorPreviewDisplayMode(mode) {
  storageSet(fileEditorPreviewDisplayModeStorageKey, normalizeEditorPreviewDisplayMode(mode));
}

function readConfiguredEditorScheme() {
  return normalizeEditorThemeMode(readStoredEditorThemeMode());
}

function syncPressedButton(button, active, options = {}) {
  if (!button) return;
  const activeClass = options.activeClass || 'active';
  button.classList.toggle(activeClass, active);
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  const label = active ? options.labelOn : options.labelOff;
  if (label) {
    button.title = label;
    button.setAttribute('aria-label', label);
  }
}

function syncFileExplorerHiddenButton(button) {
  syncPressedButton(button, fileExplorerShowHidden, {
    labelOn: t('finder.toolbar.hideHidden'),
    labelOff: t('finder.toolbar.hidden'),
  });
}

function syncFileExplorerHiddenButtons() {
  syncFileExplorerHiddenButton(fileExplorerHiddenToggle);
  document.querySelectorAll('.file-explorer-hidden-toggle-panel').forEach(syncFileExplorerHiddenButton);
}

function fileExplorerTreeDateModeLabel(mode = fileExplorerTreeDateModeForView('finder')) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return t(`finder.dateMode.${normalized}`);
}

function fileExplorerTreeDateModeButtonLabel(mode = fileExplorerTreeDateModeForView('finder')) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  return normalized === 'none' ? t('finder.dateMode.date') : fileExplorerTreeDateModeLabel(normalized);
}

function fileExplorerTreeDateModeTitle(mode = fileExplorerTreeDateModeForView('finder')) {
  return t('finder.dateMode.title', {
    mode: fileExplorerTreeDateModeLabel(mode),
    none: fileExplorerTreeDateModeLabel('none'),
    date: fileExplorerTreeDateModeLabel('date'),
    relative: fileExplorerTreeDateModeLabel('relative'),
  });
}

function syncFileExplorerTreeDateButton(button) {
  if (!button) return;
  const view = normalizeFileExplorerView(button.dataset.fileExplorerView || fileExplorerViewForItem(button.closest?.('.file-explorer-panel')?.dataset?.panelItem));
  const mode = fileExplorerTreeDateModeForView(view);
  const active = mode !== 'none';
  button.classList.toggle(CLS.active, active);
  button.dataset.dateMode = mode;
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  button.textContent = fileExplorerTreeDateModeButtonLabel(mode);
  const label = fileExplorerTreeDateModeTitle(mode);
  button.title = label;
  button.setAttribute('aria-label', label);
}

function syncFileExplorerTreeDateButtons(scope = document) {
  for (const button of scope.querySelectorAll?.('[data-file-explorer-tree-dates]') || []) {
    syncFileExplorerTreeDateButton(button);
  }
}

function nextFileExplorerTreeDateMode(mode = fileExplorerTreeDateModeForView('finder')) {
  const normalized = normalizeFileExplorerTreeDateMode(mode);
  const index = fileExplorerTreeDateModes.indexOf(normalized);
  return fileExplorerTreeDateModes[(index + 1) % fileExplorerTreeDateModes.length];
}

function refreshFileExplorerViewSettingsSurface(view) {
  const surface = normalizeFileExplorerView(view);
  if (surface === 'finder') {
    if (typeof refreshFileExplorerTrees === 'function') void refreshFileExplorerTrees({preserveExpanded: true, preserveScroll: true});
  } else if (typeof renderFileExplorerChangesPanels === 'function') {
    renderFileExplorerChangesPanels({force: true, view: surface});
  }
  syncFileExplorerTreeDateButtons();
}

function setFileExplorerTreeDateMode(mode, view = 'finder') {
  setFileExplorerViewSetting(view, 'treeDateMode', mode);
}

function cycleFileExplorerTreeDateMode(view = 'finder') {
  setFileExplorerTreeDateMode(nextFileExplorerTreeDateMode(fileExplorerTreeDateModeForView(view)), view);
}

function renderTabMetaToggle() {
  document.body?.classList.toggle('tab-meta-hidden', !tabMetaVisible);
  if (!tabMetaToggle) return;
  syncPressedButton(tabMetaToggle, tabMetaVisible, {
    labelOn: t('menu.view.tabMeta.hide'),
    labelOff: t('menu.view.tabMeta.show'),
  });
}

function toggleTabMetadata() {
  tabMetaVisible = !tabMetaVisible;
  writeStoredTabMetaVisible(tabMetaVisible);
  renderTabMetaToggle();
  renderSessionButtons();
  scheduleTopbarMetricsUpdate();
}

function recordFocusNavTransition(previousItem, nextItem) {
  if (!nextItem) return;
  if (previousItem && previousItem !== nextItem) recordEditorNav(previousItem);
  recordEditorNav(nextItem);
}

function rememberActivePaneItem(item) {
  if (!item || !itemIsActivePaneTab(item)) return;
  lastActivePaneItem = item;
  if (!isFileExplorerItem(item)) lastActiveNonFileExplorerPaneItem = item;
}

function explicitPaneFocusItem() {
  return explicitPaneFocusState.item && itemIsActivePaneTab(explicitPaneFocusState.item) ? explicitPaneFocusState.item : null;
}

function explicitTmuxPaneFocusSession() {
  const session = explicitPaneFocusState.tmuxSession;
  return isTmuxSession(session) && activeSessions.includes(session) ? session : '';
}

function setExplicitPaneFocusItem(item, options = {}) {
  const next = String(item || '').trim();
  if (next && options.allowInactive !== true && !itemIsActivePaneTab(next)) return false;
  const nextTmuxSession = isTmuxSession(next)
    ? next
    : (options.clearTmux === true ? null : explicitPaneFocusState.tmuxSession);
  if (explicitPaneFocusState.item === (next || null) && explicitPaneFocusState.tmuxSession === nextTmuxSession) return false;
  explicitPaneFocusState.item = next || null;
  explicitPaneFocusState.tmuxSession = nextTmuxSession;
  if (options.renderMenu !== false && typeof renderSessionButtons === 'function') renderSessionButtons();
  return true;
}

function visualActivePaneItem() {
  if (focusedPanelItem && itemIsActivePaneTab(focusedPanelItem)) return focusedPanelItem;
  if (lastActivePaneItem && itemIsActivePaneTab(lastActivePaneItem)) return lastActivePaneItem;
  return null;
}

function seedVisualActivePaneItem(preferredItems = []) {
  const candidates = [
    ...preferredItems,
    focusedPanelItem,
    focusedTerminal,
    lastFocusedTmuxSession,
    ...activePaneItems(),
  ];
  const item = candidates.find(candidate => candidate && itemIsActivePaneTab(candidate));
  if (item) lastActivePaneItem = item;
  return item || null;
}

function attentionAcknowledgeDelayMsFromOptions(options = {}) {
  return Number.isFinite(Number(options.acknowledgeAgentWindowDelayMs))
    ? Math.max(0, Number(options.acknowledgeAgentWindowDelayMs))
    : (typeof agentWindowActivityAcknowledgeDelayMs === 'number' ? agentWindowActivityAcknowledgeDelayMs : 0);
}

function acknowledgeTerminalAttentionFromUserAction(session, windowIndex = null, options = {}) {
  const sessionKey = String(session || '').trim();
  if (!sessionKey || !isTmuxSession(sessionKey)) return false;
  const explicitWindowIndex = windowIndex !== null && windowIndex !== undefined;
  const acknowledgeDelayMs = attentionAcknowledgeDelayMsFromOptions(options);
  let acknowledged = false;
  // A parent session tab acknowledges the highest-priority stopped/attention child, not whichever
  // tmux window happens to be active. Direct sub-window actions remain tied to their exact target.
  if (options.acknowledgeAgentWindow !== false && typeof acknowledgeAgentWindowActivity === 'function') {
    acknowledged = acknowledgeAgentWindowActivity(sessionKey, explicitWindowIndex ? windowIndex : null, {
      ...options,
      preferSummary: options.preferSummary === true || !explicitWindowIndex,
      delayMs: acknowledgeDelayMs,
    }) || acknowledged;
  }
  if (options.acknowledgePromptAttention !== false && typeof clearPromptAttentionForSession === 'function') {
    acknowledged = clearPromptAttentionForSession(sessionKey, {...options, delayMs: acknowledgeDelayMs}) || acknowledged;
  }
  return acknowledged;
}

function activateTmuxWindowFromUserAction(session, windowIndex, label, options = {}) {
  const sessionKey = String(session || '').trim();
  if (!sessionKey || !isTmuxSession(sessionKey) || windowIndex === null || windowIndex === undefined || String(windowIndex).trim() === '') return false;
  // Every visible direct-window surface must acknowledge the exact target before switching. The
  // acknowledgement captures the red/yellow generation while the old window model is still live.
  acknowledgeTerminalAttentionFromUserAction(sessionKey, windowIndex, options);
  if (typeof tmuxWindow !== 'function') return false;
  tmuxWindow(sessionKey, {windowIndex}, label);
  return true;
}

function setFocusedTerminal(session, options = {}) {
  const perf = clientPerfStart('focusSet');
  try {
    return setFocusedTerminalMeasured(session, options);
  } finally {
    clientPerfEnd(perf, {sessions: activeSessions.length, user: options.userInitiated === true ? 1 : 0});
  }
}

function updateFocusOnlyChrome() {
  // Focus does not change pane-tab content. Dockview/classic layout activation owns active-tab
  // chrome, while acknowledgement updates arrive through their existing status-render path.
  updateTopbarActivityStatus();
  for (const activeSession of activeSessions) updateTypingIndicator(activeSession);
  updatePanelInactiveOverlays();
}

function setFocusedTerminalMeasured(session, options = {}) {
  const previousItem = focusedPanelItem;
  const alreadyFocused = focusedTerminal === session && focusedPanelItem === session;
  if (alreadyFocused) {
    rememberActivePaneItem(session);
    if (isTmuxSession(session)) lastFocusedTmuxSession = session;
    if (options.userInitiated === true) {
      dismissAttentionAlertsForSession(session);
      acknowledgeTerminalAttentionFromUserAction(session, null, options);
      if (options.syncFinder !== false) {
        rememberFileExplorerExplicitSyncSession(session);
        scheduleFileExplorerActiveTabSync(session, {explicit: true});
      }
    }
    return;
  }
  if (previousItem !== session) capturePaneViewStateForItemIfPresent(previousItem);
  focusedTerminal = session;
  focusedPanelItem = session;
  rememberActivePaneItem(session);
  clearPendingFileEditorFocusExcept(session);
  if (isTmuxSession(session)) lastFocusedTmuxSession = session;
  dismissAttentionAlertsForSession(session);
  updateFocusOnlyChrome();
  if (options.userInitiated === true) {
    acknowledgeTerminalAttentionFromUserAction(session, null, options);
    rememberFileExplorerExplicitSyncSession(session);
    scheduleFileExplorerActiveTabSync(session, {explicit: true});
    recordFocusNavTransition(previousItem, session);
  }
  else recordAutoFocusNav(session, previousItem);
}

function clearFocusedTerminal(session) {
  if (focusedTerminal !== session) return;
  focusedTerminal = null;
  focusedPanelItem = null;
  updateFocusOnlyChrome();
}

function applyUserInitiatedPanelFocus(item, previousItem, options = {}) {
  if (isTmuxSession(item)) {
    acknowledgeTerminalAttentionFromUserAction(item, null, {...options, preferSummary: true});
    rememberFileExplorerExplicitSyncSession(item);
  } else {
    setExplicitPaneFocusItem(item);
  }
  if (isFileEditorItem(item)) {
    activeFile = fileItemPath(item);
    scheduleFileExplorerActiveFileReveal(activeFile);
  }
  const explicitFinderSync = isTmuxSession(item) || isFileEditorItem(item);
  if (!isFileExplorerItem(item)) scheduleFileExplorerActiveTabSync(item, {explicit: explicitFinderSync});
  if (previousItem !== item) recordFocusNavTransition(previousItem, item);
}

function setFocusedPanelItem(item, options = {}) {
  const previousItem = focusedPanelItem;
  const alreadyFocused = focusedPanelItem === item;
  if (alreadyFocused) {
    rememberActivePaneItem(item);
    if (isTmuxSession(item)) lastFocusedTmuxSession = item;
    dismissNotificationsForTarget(item);
    if (options.userInitiated === true) {
      applyUserInitiatedPanelFocus(item, previousItem, options);
    }
    return;
  }
  if (previousItem !== item) capturePaneViewStateForItemIfPresent(previousItem);
  if (focusedTerminal !== item) focusedTerminal = null;
  focusedPanelItem = item;
  rememberActivePaneItem(item);
  dismissNotificationsForTarget(item);
  clearPendingFileEditorFocusExcept(item);
  if (isTmuxSession(item)) {
    lastFocusedTmuxSession = item;
  }
  updateFocusOnlyChrome();
  if (options.userInitiated === true) {
    applyUserInitiatedPanelFocus(item, previousItem, options);
  }
  else recordAutoFocusNav(item, previousItem);
}

let autoFocusNavTimer = null;
// an AUTO-FOCUS-driven focus change records back/forward nav "as if clicked", so Back
// returns to where you were. Debounced by a short dwell so rapid auto-focus flapping (focus chasing
// needs-attention) records only the focus that LANDS, not every transient flip. User clicks already
// record immediately (activatePaneTab userInitiated); a back/forward re-activation lands on the item
// already at the stack head, so recordEditorNav's consecutive-dedupe makes this a no-op there.
function recordAutoFocusNav(item, previousItem = null) {
  if (!autoFocusCanFollowCursor() || !item) return;
  if (autoFocusNavTimer) clearTimeout(autoFocusNavTimer);
  autoFocusNavTimer = setTimeout(() => {
    autoFocusNavTimer = null;
    if (focusedPanelItem === item) recordFocusNavTransition(previousItem, item);
  }, 500);
}

function clearPendingFileEditorFocusExcept(item) {
  for (const pendingItem of Array.from(pendingFileEditorFocus)) {
    if (pendingItem !== item) pendingFileEditorFocus.delete(pendingItem);
  }
}

function focusTerminalWhenAutoFocus(session, delay = 0) {
  if (!autoFocusCanFollowCursor()) return;
  focusTerminalDom(session, delay);
}

function focusTerminalFromUserAction(session, delay = 0, options = {}) {
  // A tab detail opened from a touch action has no hover-leave event. Terminal engagement is an
  // explicit change of context, so it must dismiss that detail before focusing the xterm surface.
  if (typeof closeOtherSessionPopovers === 'function') closeOtherSessionPopovers(null, {force: true});
  noteFileExplorerChangesSessionInteraction(session);
  setFocusedTerminal(session, {...options, userInitiated: true});
  focusTerminalDom(session, delay);
}

function focusTerminalDom(session, delay = 0) {
  const run = () => {
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;
    terminals.get(session)?.term?.focus?.();
    // xterm focuses its hidden textarea without a preventScroll option. In a full-height app with
    // a tall sibling pane, Chrome can scroll the otherwise overflow-hidden document and move the
    // entire top bar above the viewport when switching terminal tabs.
    if (window.scrollX !== scrollX || window.scrollY !== scrollY) window.scrollTo(scrollX, scrollY);
  };
  if (delay > 0) setTimeout(run, delay);
  else run();
}

function clearFocusForInactiveLayout() {
  if (focusedTerminal && !activeSessions.includes(focusedTerminal)) focusedTerminal = null;
  if (focusedPanelItem && !activeSessions.includes(focusedPanelItem)) focusedPanelItem = null;
  if (lastActivePaneItem && !itemIsActivePaneTab(lastActivePaneItem)) lastActivePaneItem = null;
  if (lastActiveNonFileExplorerPaneItem && !itemIsActivePaneTab(lastActiveNonFileExplorerPaneItem)) lastActiveNonFileExplorerPaneItem = null;
  if (lastFocusedTmuxSession && !activeSessions.includes(lastFocusedTmuxSession)) lastFocusedTmuxSession = null;
  if (explicitPaneFocusState.item && !itemIsActivePaneTab(explicitPaneFocusState.item)) explicitPaneFocusState.item = null;
  if (explicitPaneFocusState.tmuxSession && !activeSessions.includes(explicitPaneFocusState.tmuxSession)) explicitPaneFocusState.tmuxSession = null;
}

function terminalPaneIsActive(session) {
  return document.getElementById(`terminal-pane-${session}`)?.classList.contains(CLS.active) === true;
}

function selectPanelOnHover(item, event = null) {
  if (!item) return;
  if (!autoFocusCanFollowCursor(event)) return;
  if (isTmuxSession(item) && terminalPaneIsActive(item)) {
    setFocusedTerminal(item);
    scheduleFit(item);
    focusTerminalWhenAutoFocus(item, 0);
    return;
  }
  if (focusedPanelItem === item) return;
  setFocusedPanelItem(item);
}

function updatePanelInactiveOverlays() {
  const activeItem = visualActivePaneItem() || seedVisualActivePaneItem();
  for (const [item, panel] of panelNodes.entries()) {
    panel.classList.toggle('focused-pane', item === activeItem);
    panel.classList.toggle('active-pane', item === activeItem);
  }
  // Re-color the active terminal's cursor yellow (and revert the rest) whenever focus moves.
  if (typeof refreshActiveTerminalCursor === 'function') refreshActiveTerminalCursor();
  const focusedSlot = focusedPanelItem ? slotForItem(focusedPanelItem) : '';
  const focusMatchesActiveLayout = !focusedSlot || activeItemForSide(focusedSlot) === focusedPanelItem;
  if (focusMatchesActiveLayout && typeof scheduleTabberTreeLayoutStateSync === 'function') scheduleTabberTreeLayoutStateSync();
  else if (focusMatchesActiveLayout && typeof syncTabberTreeLayoutState === 'function') syncTabberTreeLayoutState();
  if (typeof syncClientEventDemand === 'function') syncClientEventDemand();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

const disclosureChevronGlyph = '›';
const disclosureTriangleExpandedGlyph = disclosureChevronGlyph;
const disclosureTriangleCollapsedGlyph = disclosureChevronGlyph;

function disclosureTriangleGlyph(expanded) {
  return expanded === true ? disclosureTriangleExpandedGlyph : disclosureTriangleCollapsedGlyph;
}

function disclosureTriangleHtml(expanded, className = '', attrs = '') {
  const classes = ['ui-disclosure-triangle', className].filter(Boolean).join(' ');
  const extraAttrs = attrs ? ` ${attrs}` : '';
  return `<span class="${esc(classes)}" data-disclosure-expanded="${expanded === true ? 'true' : 'false'}" aria-hidden="true"${extraAttrs}>${esc(disclosureTriangleGlyph(expanded))}</span>`;
}

function setDisclosureTriangleElement(element, expanded) {
  if (!element) return;
  element.classList?.add?.('ui-disclosure-triangle');
  element.dataset.disclosureExpanded = expanded === true ? 'true' : 'false';
  element.setAttribute?.('aria-hidden', 'true');
  element.textContent = disclosureTriangleGlyph(expanded);
}

function stripTrailingEllipsisText(value) {
  return String(value ?? '').replace(/\s*(?:\.{3}|…)+\s*$/u, '').trimEnd();
}

function movingEllipsisHtml(className = '') {
  const classes = ['moving-ellipsis', className].filter(Boolean).join(' ');
  return `<span class="${esc(classes)}" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`;
}

function textWithMovingEllipsisHtml(value, className = '') {
  return `${esc(stripTrailingEllipsisText(value))}${movingEllipsisHtml(className)}`;
}

const searchRankWeights = Object.freeze({
  perChar: 8,
  contiguous: 10,
  contiguousSubstring: 30000,
  wordStart: 6,
  gapPenalty: 0.2,
  haystackLengthPenalty: 0.01,
  anchorPrimary: 20000,
  anchorSecondary: 12000,
  fieldIndexPenalty: 20,
  domainPrior: {
    files: {file: 6000, pane: 3000, command: 0, setting: 0},
    command: {pane: 6000, command: 3000, setting: 3000, file: 0},
  },
  fileNamePrefix: 3500,
  fileNameContains: 1800,
  fileNameSubsequence: 600,
  paneNameContiguous: {files: 50000, command: 100000},
  finderAlias: 25000,
  finderAliasFilesMode: 2500,
  paneExactIdentifier: 30000,
  recentSelectionBase: 1000,
  recencyCap: 900,
  recencyHalfLifeSeconds: 7 * 24 * 60 * 60,
  repoAffinity: 400,
  mixWindow: 8,
  mixSecondarySlots: 4,
  mixFirstSecondaryIndex: 2,
  mixSecondaryStep: 2,
});

function fuzzySubsequenceMatch(query, text) {
  const needle = String(query || '').toLowerCase().replace(/\s+/g, '');
  const haystack = String(text || '').toLowerCase();
  if (!needle) return {score: 0, indexes: []};
  // A contiguous occurrence is the strongest fuzzy match. Select its actual indexes
  // before the greedy subsequence walk so result highlighting points at the same
  // evidence that receives the contiguous-substring rank bonus. Without this, a
  // query such as "t5t" could rank a path for `/t5t/` but highlight the earlier
  // `t` in `/notes/` plus `5t` in the matching directory.
  const contiguousStart = haystack.indexOf(needle);
  if (contiguousStart >= 0) {
    const indexes = Array.from({length: needle.length}, (_, offset) => contiguousStart + offset);
    let score = needle.length * searchRankWeights.perChar;
    for (const index of indexes) {
      const previousChar = haystack[index - 1] || '';
      if (index === contiguousStart && (index === 0 || /[\s/_:.-]/.test(previousChar))) {
        score += searchRankWeights.wordStart;
      } else if (index > contiguousStart) {
        score += searchRankWeights.contiguous;
      }
    }
    score += searchRankWeights.contiguousSubstring;
    return {score: score - Math.max(0, haystack.length - needle.length) * searchRankWeights.haystackLengthPenalty, indexes};
  }
  let position = 0;
  let previousIndex = -1;
  let score = 0;
  const indexes = [];
  for (const char of needle) {
    const index = haystack.indexOf(char, position);
    if (index < 0) return null;
    const previousChar = haystack[index - 1] || '';
    const contiguous = previousIndex >= 0 && index === previousIndex + 1;
    const wordStart = index === 0 || /[\s/_:.-]/.test(previousChar);
    score += searchRankWeights.perChar;
    if (contiguous) score += searchRankWeights.contiguous;
    if (wordStart) score += searchRankWeights.wordStart;
    score -= Math.max(0, index - position) * searchRankWeights.gapPenalty;
    previousIndex = index;
    position = index + 1;
    indexes.push(index);
  }
  return {score: score - Math.max(0, haystack.length - needle.length) * searchRankWeights.haystackLengthPenalty, indexes};
}

function fuzzySubsequenceScore(query, text) {
  const match = fuzzySubsequenceMatch(query, text);
  return match ? match.score : Number.NEGATIVE_INFINITY;
}

function fuzzyCanonicalPrefixText(value) {
  return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
}

function fuzzyFieldStartsWithQuery(query, text) {
  const needle = fuzzyCanonicalPrefixText(query);
  return Boolean(needle) && fuzzyCanonicalPrefixText(text).startsWith(needle);
}

function focusPanelSearchInput(panel, inputSelector, options = {}) {
  const panelSelector = String(options.panelSelector || '');
  const root = panel && panel.isConnected !== false
    ? panel
    : (panelSelector
      ? (Array.from(document.querySelectorAll(panelSelector)).find(candidate => candidate.offsetParent !== null) || document.querySelector(panelSelector))
      : null);
  const search = root?.querySelector?.(inputSelector);
  if (!search) return false;
  search.focus?.({preventScroll: true});
  if (options.select === true) search.select?.();
  else {
    const position = String(search.value || '').length;
    search.setSelectionRange?.(position, position);
  }
  return true;
}

function fuzzySearchScore(query, fields) {
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  const values = (Array.isArray(fields) ? fields : [fields]).map(value => String(value || '')).filter(Boolean);
  if (!values.length) return Number.NEGATIVE_INFINITY;
  let total = 0;
  for (const token of tokens) {
    let best = Number.NEGATIVE_INFINITY;
    for (const [index, value] of values.entries()) {
      let fieldScore = fuzzySubsequenceScore(token, value);
      if (Number.isFinite(fieldScore) && fuzzyFieldStartsWithQuery(token, value)) {
        fieldScore += index === 0 ? searchRankWeights.anchorPrimary : searchRankWeights.anchorSecondary;
      }
      if (Number.isFinite(fieldScore)) best = Math.max(best, fieldScore - index * searchRankWeights.fieldIndexPenalty);
    }
    if (!Number.isFinite(best)) return Number.NEGATIVE_INFINITY;
    total += best;
  }
  return total;
}

function sessionScopedId(key, create = randomBrowserInstanceId) {
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const created = create();
    sessionStorage.setItem(key, created);
    return created;
  } catch (_) {
    return create();
  }
}

function fuzzyHighlightHtml(query, text, {markClass = 'fuzzy-match'} = {}) {
  const value = String(text ?? '');
  // Highlight EVERY query token's subsequence match, not just the first — mirrors fuzzySearchScore, which
  // scores all tokens. So "pa exploration" highlights both "PA" and "exploration", not only "pa".
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  const indexes = new Set();
  for (const token of tokens) {
    const match = fuzzySubsequenceMatch(token, value);
    if (match) for (const matchIndex of match.indexes) indexes.add(matchIndex);
  }
  if (!indexes.size) return esc(value);
  const chars = Array.from(value);
  const parts = [];
  let index = 0;
  while (index < chars.length) {
    if (!indexes.has(index)) {
      parts.push(esc(chars[index]));
      index += 1;
      continue;
    }
    const start = index;
    while (index < chars.length && indexes.has(index)) index += 1;
    parts.push(`<mark class="${esc(markClass)}">${esc(chars.slice(start, index).join(''))}</mark>`);
  }
  return parts.join('');
}

function restoreElementScrollPosition(element, scrollTop, scrollLeft) {
  if (!element) return;
  element.scrollTop = scrollTop;
  element.scrollLeft = scrollLeft;
  requestAnimationFrame(() => {
    element.scrollTop = scrollTop;
    element.scrollLeft = scrollLeft;
  });
}

function captureKeyedScrollPositions(root, selector) {
  const positions = new Map();
  for (const element of root?.querySelectorAll?.(selector) || []) {
    const key = String(element.dataset?.jsDebugCostTable || '').trim();
    const scrollOwner = element.closest?.('.js-debug-cost-table-wrap') || element;
    if (key) positions.set(key, {scrollTop: scrollOwner.scrollTop || 0, scrollLeft: scrollOwner.scrollLeft || 0});
  }
  return positions;
}

function restoreKeyedScrollPositions(root, selector, positions) {
  if (!(positions instanceof Map)) return;
  for (const element of root?.querySelectorAll?.(selector) || []) {
    const position = positions.get(String(element.dataset?.jsDebugCostTable || '').trim());
    if (position) restoreElementScrollPosition(element.closest?.('.js-debug-cost-table-wrap') || element, position.scrollTop, position.scrollLeft);
  }
}

function replaceHtmlPreservingScroll(element, html) {
  if (!element) return;
  const scrollTop = element.scrollTop || 0;
  const scrollLeft = element.scrollLeft || 0;
  element.innerHTML = html;
  restoreElementScrollPosition(element, scrollTop, scrollLeft);
}

function reconcilePanelBody({body, html, anchors = [], replace = null, afterReplace = null} = {}) {
  if (!body) return false;
  const captured = anchors.map(anchor => ({
    anchor,
    value: typeof anchor.capture === 'function' ? anchor.capture(body) : undefined,
  }));
  if (typeof replace === 'function') replace(body, html);
  else body.innerHTML = html;
  normalizeAppOwnedControls(body);
  for (const {anchor, value} of captured) anchor.restore?.(body, value);
  afterReplace?.(body);
  return true;
}

function elementScrollAnchor(selector, fallbackToBody = false) {
  return {
    capture(body) {
      const element = body.querySelector?.(selector) || (fallbackToBody ? body : null);
      return {scrollTop: element?.scrollTop || 0, scrollLeft: element?.scrollLeft || 0};
    },
    restore(body, value) {
      restoreElementScrollPosition(body.querySelector?.(selector) || (fallbackToBody ? body : null), value?.scrollTop || 0, value?.scrollLeft || 0);
    },
  };
}

function keyedScrollAnchor(selector) {
  return {
    capture: body => captureKeyedScrollPositions(body, selector),
    restore: (body, value) => restoreKeyedScrollPositions(body, selector, value),
  };
}

function wsUrl(session) {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const params = new URLSearchParams({session, client: browserClientId});
  return `${scheme}//${location.host}/ws?${params.toString()}`;
}

function renderTransportWarning() {
  if (!httpsWarning) return;
  const secure = location.protocol === 'https:';
  httpsWarning.hidden = secure;
  if (secure) return;
  const port = location.port || '9998';
  const selfSigned = `python3 yolomux.py --port ${port} --self-signed`;
  const cert = `python3 yolomux.py --port ${port} --cert /path/fullchain.pem --key /path/privkey.pem`;
  httpsWarning.dataset.tip = t('app.noHttpsDetail', {selfSigned, cert});
  httpsWarning.setAttribute('aria-label', httpsWarning.dataset.tip);
  httpsWarning.tabIndex = 0;
}

function stripTerminalQueryResponses(data) {
  return String(data)
    .replace(/\x1b\[[?>]?[0-9;]*c/g, '')
    .replace(/\x1bP[>|!][^\x1b]*(?:\x1b\\|\x9c)/g, '');
}

const terminalLinkPattern = /(?:https?:\/\/|file:\/\/|www\.)[^\s<>"'`]+/gi;
const terminalFileReferencePattern = /(^|[\s([{<"'`])((?:(?:~\/|\.{1,2}\/|\/)?[A-Za-z0-9._@%+=-]+(?:\/[A-Za-z0-9._@%+=-]+)+)|(?:(?:~\/|\.{1,2}\/|\/)?[A-Za-z0-9._@%+=-]+\.[A-Za-z0-9][A-Za-z0-9+_-]{0,31}))(?::([1-9]\d{0,6}))?/g;
const terminalFileReferenceExtensions = new Set(['c', 'cc', 'cpp', 'cs', 'css', 'csv', 'go', 'h', 'hpp', 'html', 'ini', 'java', 'js', 'json', 'jsx', 'lock', 'lua', 'md', 'mjs', 'php', 'py', 'rb', 'rs', 'scss', 'sh', 'sql', 'toml', 'ts', 'tsx', 'txt', 'xml', 'yaml', 'yml']);
const terminalFileReferenceNegativeCacheMs = 5_000;
const terminalFileReferencePositiveCacheMs = 30_000;
const terminalWrappedUrlMaxRows = 8;
const terminalLinkClosePairs = [
  [')', '('],
  [']', '['],
  ['}', '{'],
];

function countChar(value, char) {
  let count = 0;
  for (const item of value) {
    if (item === char) count += 1;
  }
  return count;
}

function trimTerminalLinkCandidate(value) {
  let text = String(value || '').replace(/^[<("'`]+/, '');
  let changed = true;
  while (changed && text) {
    changed = false;
    const trimmed = text.replace(/[.,;:!?"'`>]+$/, '');
    if (trimmed !== text) {
      text = trimmed;
      changed = true;
    }
    for (const [closeChar, openChar] of terminalLinkClosePairs) {
      if (text.endsWith(closeChar) && countChar(text, closeChar) > countChar(text, openChar)) {
        text = text.slice(0, -1);
        changed = true;
      }
    }
  }
  return text;
}

function normalizeTerminalLink(value) {
  const text = trimTerminalLinkCandidate(value);
  if (!text) return '';
  if (/^www\./i.test(text)) return `https://${text}`;
  return text;
}

function trimTerminalFileReferenceCandidate(value) {
  return String(value || '').replace(/\.+$/, '');
}

function terminalRangesOverlap(leftStart, leftEnd, rightStart, rightEnd) {
  return leftStart < rightEnd && rightStart < leftEnd;
}

function terminalTextUrlReferences(lineText, rangeForOffsets, y = null) {
  const refs = [];
  terminalLinkPattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalLinkPattern)) {
    const raw = match[0] || '';
    const text = trimTerminalLinkCandidate(raw);
    if (!text) continue;
    const startIndex = (match.index || 0) + raw.indexOf(text);
    const endIndex = startIndex + text.length;
    const range = rangeForOffsets(startIndex, endIndex);
    if (!range) continue;
    if (Number.isFinite(y) && (range.start.y > y || range.end.y < y)) continue;
    refs.push({
      type: 'url',
      text,
      href: normalizeTerminalLink(text),
      range,
      startIndex,
      endIndex,
    });
  }
  return refs;
}

function terminalTextFileReferences(lineText, rangeForOffsets, y = null, excludedRanges = []) {
  const refs = [];
  terminalFileReferencePattern.lastIndex = 0;
  for (const match of lineText.matchAll(terminalFileReferencePattern)) {
    const prefix = match[1] || '';
    const path = trimTerminalFileReferenceCandidate(match[2]);
    if (!path || /^[a-z][a-z0-9+.-]*:/i.test(path)) continue;
    if (!terminalTextLooksLikeFileReference(path)) continue;
    const line = Number(match[3] || 0);
    const startIndex = (match.index || 0) + prefix.length;
    const endIndex = startIndex + path.length + (match[3] ? match[3].length + 1 : 0);
    if (terminalFileReferenceIsHttpRequestTarget(lineText, startIndex, endIndex)) continue;
    if (excludedRanges.some(range => terminalRangesOverlap(startIndex, endIndex, range.startIndex, range.endIndex))) continue;
    const range = rangeForOffsets(startIndex, endIndex);
    if (!range) continue;
    if (Number.isFinite(y) && (range.start.y > y || range.end.y < y)) continue;
    refs.push({
      type: 'file',
      text: line ? `${path}:${line}` : path,
      path,
      line: line || null,
      range,
      startIndex,
      endIndex,
    });
  }
  return refs;
}

function terminalFileReferenceIsHttpRequestTarget(lineText, startIndex, endIndex) {
  const before = String(lineText || '').slice(0, startIndex);
  const after = String(lineText || '').slice(endIndex);
  return /(?:^|[\s"'])(?:GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS|CONNECT)\s+$/.test(before)
    && /^(?:\?[^\s]*)?\s+HTTP\/\d(?:\.\d)?(?:[\s"']|$)/.test(after);
}

// Terminal output frequently contains dotted JavaScript symbols and slash-separated status
// labels. Only probe paths that carry an explicit path prefix or a conventional file suffix.
function terminalTextLooksLikeFileReference(path) {
  const value = String(path || '');
  if (/^(?:~\/|\.{1,2}\/|\/)/.test(value)) return true;
  const basename = value.split('/').pop() || '';
  const extension = basename.includes('.') ? basename.split('.').pop().toLowerCase() : '';
  if (!terminalFileReferenceExtensions.has(extension)) return false;
  return value.includes('/') || basename.includes('.');
}

function terminalTextReferences(lineText, rangeForOffsets, y = null) {
  const urls = terminalTextUrlReferences(lineText, rangeForOffsets, y);
  const files = terminalTextFileReferences(lineText, rangeForOffsets, y, urls);
  return [...urls, ...files].sort((a, b) => a.range.start.y - b.range.start.y || a.range.start.x - b.range.start.x);
}

function terminalTextLinks(lineText, rangeForOffsets, y = null) {
  return terminalTextReferences(lineText, rangeForOffsets, y);
}

function terminalLineLinks(lineText, y) {
  return terminalTextLinks(lineText, (startIndex, endIndex) => ({
    start: {x: startIndex + 1, y},
    end: {x: endIndex, y},
  }));
}

function terminalBufferLineText(line) {
  return line?.translateToString?.(true) || '';
}

// did `line` fill the terminal to its right edge? translateToString(true) trims trailing
// blanks, so a row whose printed text reaches `cols` had a non-blank last cell — evidence the content
// was CLIPPED at the edge and wrapped, not that it merely happened to end at the row. Used to gate the
// hanging-URL stitch: a complete URL ending well short of the edge (e.g. `See https://x.com`) is NOT a
// clipped URL and must not absorb the indented next row. cols<=0 (unknown width) → treat as not clipped.
function terminalRowReachesRightEdge(line, cols) {
  if (!Number.isFinite(cols) || cols <= 0) return false;
  return terminalBufferLineText(line).length >= Math.max(1, cols - 1);
}

// does the joined group text end mid-URL? True when the LAST url token reaches the very end of
// the string (no trailing whitespace/terminator). Used to decide whether to stitch a hanging-indent
// continuation row onto the group — only EXTEND a url token that runs off the row's right edge.
function terminalTailIsUnterminatedUrl(text) {
  if (!text) return false;
  terminalLinkPattern.lastIndex = 0;
  let last = null;
  for (const match of text.matchAll(terminalLinkPattern)) last = match;
  if (!last) return false;
  return last.index + last[0].length === text.length;
}

function terminalRowStartsNewUrlToken(text) {
  return /^(?:https?:\/\/|file:\/\/|www\.)/i.test(String(text || ''));
}

// a row shaped like a hanging-indent continuation — optional leading whitespace, then a URL-valid char
// (not a quote/bracket). Returns {indent, text} with the indent stripped, or null. isWrapped rows are
// not hanging continuations (they are real terminal soft-wraps and are handled by the isWrapped sweep).
function terminalRowHangingShape(buffer, index) {
  const line = buffer.getLine(index);
  if (!line || line.isWrapped === true) return null;
  const raw = terminalBufferLineText(line);
  const match = /^(\s*)([^\s<>"'`])/.exec(raw);
  if (!match) return null;
  const text = raw.slice(match[1].length);
  if (!text || terminalRowStartsNewUrlToken(text)) return null;
  return {indent: match[1].length, text};
}

// row `index` continues the URL printed on row `index - 1` — its own row shape is a hanging
// indent AND the previous row's tail is an unterminated url token. Gates tightly so ordinary indented
// prose under a line that merely happens to end at a URL is not merged.
// ALSO require the previous row to reach the terminal's right edge, proving the URL was
// clipped/hard-wrapped. Without this, a complete URL at end-of-line falsely swallows the next row.
function terminalRowIsHangingUrlContinuation(buffer, index, cols, depth = 0) {
  if (depth >= terminalWrappedUrlMaxRows) return false;
  const shape = terminalRowHangingShape(buffer, index);
  if (!shape) return false;
  const prev = buffer.getLine(index - 1);
  if (!prev) return false;
  if (!terminalRowReachesRightEdge(prev, cols)) return false;
  return terminalTailIsUnterminatedUrl(terminalBufferLineText(prev))
    || terminalRowIsHangingUrlContinuation(buffer, index - 1, cols, depth + 1);
}

function terminalWrappedLineGroup(term, y) {
  const buffer = term.buffer?.active;
  if (!buffer?.getLine) return null;
  // terminal width gates the hanging-URL stitch (a clipped URL fills to the right edge).
  const cols = Number(term.cols) || 0;
  const requested = Math.max(0, y - 1);
  if (!buffer.getLine(requested)) return null;
  // Walk back to the logical line's first row: over terminal soft-wraps (isWrapped) AND over
  // hanging-indent URL continuations (agent-hard-wrapped URLs whose continuation is its own
  // non-wrapped, indented row). So querying ANY row of the wrapped URL yields the same full group.
  let start = requested;
  for (;;) {
    if (start > 0 && buffer.getLine(start)?.isWrapped === true) { start -= 1; continue; }
    if (start > 0 && terminalRowIsHangingUrlContinuation(buffer, start, cols)) { start -= 1; continue; }
    break;
  }
  // Forward pass from start. Include soft-wrap rows (indent 0) and, while the joined text still ends
  // mid-URL, hanging-indent continuation rows (indent stripped for link matching, but recorded so the
  // underline maps back to the row's REAL columns). Stop at the first row that is neither.
  const rows = [];
  let offset = 0;
  let joined = '';
  let index = start;
  for (;;) {
    let text;
    let indent = 0;
    if (index === start) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (buffer.getLine(index)?.isWrapped === true) {
      text = terminalBufferLineText(buffer.getLine(index));
    } else if (terminalTailIsUnterminatedUrl(joined) && terminalRowReachesRightEdge(buffer.getLine(index - 1), cols)) {
      const shape = terminalRowHangingShape(buffer, index);
      if (!shape) break;
      indent = shape.indent;
      text = shape.text;
    } else {
      break;
    }
    rows.push({y: index + 1, text, indent, start: offset, end: offset + text.length});
    offset += text.length;
    joined += text;
    index += 1;
    if (rows.length >= terminalWrappedUrlMaxRows) break;
    if (!buffer.getLine(index)) break;
  }
  return {text: joined, rows};
}

function terminalWrappedOffsetPosition(group, offset, endPosition = false) {
  const target = endPosition ? Math.max(0, offset - 1) : offset;
  const row = group.rows.find(candidate => target >= candidate.start && target < candidate.end) || group.rows[group.rows.length - 1];
  if (!row) return null;
  // A stitched continuation row had `indent` leading spaces stripped before joining, so its real
  // terminal column is shifted right by that indent.
  return {x: Math.max(1, target - row.start + 1 + (row.indent || 0)), y: row.y};
}

function terminalWrappedRange(group, startIndex, endIndex) {
  const start = terminalWrappedOffsetPosition(group, startIndex, false);
  const end = terminalWrappedOffsetPosition(group, endIndex, true);
  if (!start || !end) return null;
  return {start, end};
}

function terminalWrappedGroupTailMayContinue(term, group) {
  const lastRow = group?.rows?.[group.rows.length - 1];
  if (!lastRow) return false;
  const buffer = term.buffer?.active;
  const line = buffer?.getLine?.(lastRow.y - 1);
  if (!terminalRowReachesRightEdge(line, Number(term.cols) || 0)) return false;
  const nextLine = buffer?.getLine?.(lastRow.y);
  return nextLine?.isWrapped === true || !terminalBufferLineText(nextLine);
}

function terminalCompletedWrappedReferences(term, group, references) {
  if (!terminalWrappedGroupTailMayContinue(term, group)) return references;
  return references.filter(reference => (
    reference.type !== 'file' || Number(reference.endIndex) < group.text.length
  ));
}

function terminalWrappedLineLinks(term, y) {
  return terminalWrappedLineReferences(term, y);
}

function terminalWrappedLineReferences(term, y) {
  const group = terminalWrappedLineGroup(term, y);
  if (!group) return [];
  const references = group.rows.length === 1
    ? terminalTextReferences(group.text, (startIndex, endIndex) => ({
      start: {x: startIndex + 1, y},
      end: {x: endIndex, y},
    }), y)
    : terminalTextReferences(group.text, (startIndex, endIndex) => terminalWrappedRange(group, startIndex, endIndex), y);
  return terminalCompletedWrappedReferences(term, group, references);
}

function terminalReferenceXtermLink(reference) {
  if (!reference?.range) return null;
  return {
    range: reference.range,
    text: reference.text || reference.href || '',
    activate: () => {},
    decorations: {underline: true, pointerCursor: false},
  };
}

async function terminalReferenceProviderLinks(session, term, y) {
  const refs = terminalWrappedLineReferences(term, y);
  const links = refs.filter(ref => ref.type === 'url').map(terminalReferenceXtermLink).filter(Boolean);
  const fileRefs = refs.filter(ref => ref.type === 'file');
  if (!fileRefs.length) return links;
  const fileTargets = await Promise.all(fileRefs.map(ref => terminalFileReferenceTarget(session, ref, {fresh: false, user: true})));
  fileRefs.forEach((ref, index) => {
    if (fileTargets[index]) {
      const link = terminalReferenceXtermLink(ref);
      if (link) links.push(link);
    }
  });
  return links.sort((a, b) => a.range.start.y - b.range.start.y || a.range.start.x - b.range.start.x);
}

const TERMINAL_FILE_UNDERLINE_REFRESH_MS = 1700;

function terminalFileReferenceViewportSignature(term) {
  const references = terminalVisibleFileReferences(term)
    .map(terminalFileReferenceKey)
    .sort()
    .join('\x1e');
  return [
    Math.max(0, Math.floor(Number(term?.cols || 0))),
    Math.max(0, Math.floor(Number(term?.rows || 0))),
    Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0))),
    references,
  ].join(':');
}

function terminalFileReferenceKey(reference) {
  const range = reference?.range || {};
  const start = range.start || {};
  const end = range.end || {};
  return [
    reference?.path || '',
    reference?.line || '',
    reference?.text || '',
    start.x || 0,
    start.y || 0,
    end.x || 0,
    end.y || 0,
  ].join('\x1f');
}

function terminalFileReferenceCacheKey(session, reference) {
  return [
    terminalFileReferenceAbsolutePath(session, reference) || reference?.path || '',
    reference?.line || '',
    reference?.path || '',
    reference?.text || '',
  ].join('\x1f');
}

function terminalVisibleFileReferences(term) {
  const rows = Math.max(0, Math.floor(Number(term?.rows || 0)));
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  const refs = [];
  const seen = new Set();
  for (let screenRow = 1; screenRow <= rows; screenRow += 1) {
    for (const ref of terminalWrappedLineReferences(term, viewportY + screenRow)) {
      if (ref.type !== 'file') continue;
      const key = terminalFileReferenceKey(ref);
      if (seen.has(key)) continue;
      seen.add(key);
      refs.push(ref);
    }
  }
  return refs;
}

function terminalFileReferenceUnderlineSegments(term, reference) {
  const range = reference?.range;
  if (!range?.start || !range?.end) return [];
  const cols = Math.max(0, Math.floor(Number(term?.cols || 0)));
  const rows = Math.max(0, Math.floor(Number(term?.rows || 0)));
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  if (!rows) return [];
  const firstY = Math.max(range.start.y, viewportY + 1);
  const lastY = Math.min(range.end.y, viewportY + rows);
  const segments = [];
  for (let y = firstY; y <= lastY; y += 1) {
    let startX = y === range.start.y ? range.start.x : 1;
    let endX = y === range.end.y ? range.end.x : cols;
    startX = Math.max(1, Math.floor(Number(startX) || 1));
    endX = Math.floor(Number(endX) || 0);
    if (cols > 0) endX = Math.min(cols, endX);
    if (endX < startX) continue;
    segments.push({x: startX, y, cells: endX - startX + 1});
  }
  return segments;
}

function terminalFileUnderlineLayer(container) {
  if (!container) return null;
  let layer = container.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  if (!layer) {
    layer = document.createElement('div');
    layer.className = 'terminal-file-link-underlines';
    layer.setAttribute('aria-hidden', 'true');
    container.appendChild(layer);
  }
  return layer;
}

function clearTerminalFileReferenceUnderlines(container) {
  const layer = container?.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  layer?.replaceChildren?.();
  return 0;
}

function updateTerminalFileReferenceUnderlineHover(container, hoverKey = '') {
  const layer = container?.querySelector?.(':scope > .terminal-file-link-underlines') || null;
  const nodes = layer?.querySelectorAll?.('.terminal-file-link-underline') || [];
  for (const node of nodes) {
    node.classList?.toggle?.('terminal-file-link-underline--hover', Boolean(hoverKey) && node.dataset.referenceKey === hoverKey);
  }
}

function renderTerminalFileReferenceUnderlines(term, container, references, options = {}) {
  let renderedNodes = 0;
  const perf = clientPerfStart('terminalUnderlineRender');
  try {
    renderedNodes = renderTerminalFileReferenceUnderlinesMeasured(term, container, references, options);
    return renderedNodes;
  } finally {
    clientPerfEnd(perf, {nodes: renderedNodes, rows: Math.max(0, Number(term?.rows || 0))});
  }
}

function renderTerminalFileReferenceUnderlinesMeasured(term, container, references, options = {}) {
  const layer = terminalFileUnderlineLayer(container);
  if (!layer) return 0;
  const cell = terminalCellDimensions(term, container);
  const screen = terminalScreenElement(container);
  const screenRect = screen?.getBoundingClientRect?.();
  const containerRect = container?.getBoundingClientRect?.();
  const cellWidth = Number(cell.width || 0);
  const cellHeight = Number(cell.height || 0);
  if (!screenRect || !containerRect || !(cellWidth > 0) || !(cellHeight > 0)) {
    return clearTerminalFileReferenceUnderlines(container);
  }
  const viewportY = Math.max(0, Math.floor(Number(term?.buffer?.active?.viewportY || 0)));
  const leftOrigin = screenRect.left - containerRect.left;
  const topOrigin = screenRect.top - containerRect.top;
  const nodes = [];
  for (const reference of references || []) {
    const key = terminalFileReferenceKey(reference);
    for (const segment of terminalFileReferenceUnderlineSegments(term, reference)) {
      const screenRow = segment.y - viewportY;
      if (screenRow < 1) continue;
      const line = document.createElement('div');
      line.className = 'terminal-file-link-underline';
      line.dataset.path = reference.targetPath || reference.path || '';
      line.dataset.text = reference.text || '';
      line.dataset.referenceKey = key;
      if (key && key === options.hoverKey) line.classList.add('terminal-file-link-underline--hover');
      line.style.left = `${leftOrigin + ((segment.x - 1) * cellWidth)}px`;
      line.style.top = `${topOrigin + (screenRow * cellHeight) - 2}px`;
      line.style.width = `${segment.cells * cellWidth}px`;
      nodes.push(line);
    }
  }
  layer.replaceChildren(...nodes);
  return nodes.length;
}

function terminalFileReferenceUnderlineIsActive(session, container) {
  return document.visibilityState !== 'hidden'
    && itemIsActivePaneTab(session)
    && terminalIsVisible(session, container);
}

function installTerminalFileReferenceUnderlines(session, term, container, options = {}) {
  if (!session || !term || !container) return null;
  const targetResolver = options.targetResolver || terminalFileReferenceTarget;
  const isActive = typeof options.isActive === 'function' ? options.isActive : terminalFileReferenceUnderlineIsActive;
  const disposables = [];
  let disposed = false;
  let timer = 0;
  let renderFrame = 0;
  let sequence = 0;
  let lastRenderedViewportSignature = '';
  let existingReferenceKeys = new Set();
  const existingReferenceTargets = new Map();
  let hoverKey = '';
  let refreshRequest = null;

  const active = () => !disposed && Boolean(isActive(session, container));

  const clearInactive = () => {
    sequence += 1;
    if (timer) clearTimeout(timer);
    if (renderFrame) cancelAnimationFrame(renderFrame);
    timer = 0;
    renderFrame = 0;
    existingReferenceKeys = new Set();
    hoverKey = '';
    lastRenderedViewportSignature = '';
    return clearTerminalFileReferenceUnderlines(container);
  };

  const setHoverKey = nextKey => {
    const normalizedKey = nextKey && existingReferenceKeys.has(nextKey) ? nextKey : '';
    if (normalizedKey === hoverKey) return;
    hoverKey = normalizedKey;
    updateTerminalFileReferenceUnderlineHover(container, hoverKey);
  };

  const updateHover = event => {
    if (!active()) return;
    const reference = terminalReferenceAtClientPoint(term, container, event?.clientX, event?.clientY);
    setHoverKey(reference?.type === 'file' ? terminalFileReferenceKey(reference) : '');
  };

  const clearHover = () => setHoverKey('');

  const renderCached = () => {
    if (!active()) return clearInactive();
    const existingRefs = [];
    for (const ref of terminalVisibleFileReferences(term)) {
      const key = terminalFileReferenceCacheKey(session, ref);
      const targetPath = existingReferenceTargets.get(key);
      if (targetPath) existingRefs.push({...ref, targetPath});
    }
    existingReferenceKeys = new Set(existingRefs.map(terminalFileReferenceKey));
    if (hoverKey && !existingReferenceKeys.has(hoverKey)) hoverKey = '';
    const count = renderTerminalFileReferenceUnderlines(term, container, existingRefs, {hoverKey});
    lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
    return count;
  };

  const refreshNow = async () => {
    if (disposed) return 0;
    if (!active()) return clearInactive();
    if (timer) {
      clearTimeout(timer);
      timer = 0;
    }
    const currentSequence = ++sequence;
    const refs = terminalVisibleFileReferences(term);
    if (!refs.length) {
      existingReferenceKeys = new Set();
      hoverKey = '';
      const count = renderTerminalFileReferenceUnderlines(term, container, []);
      lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
      return count;
    }
    const targets = await Promise.all(refs.map(ref => (
      Promise.resolve(targetResolver(session, ref, {fresh: false, user: true})).catch(() => null)
    )));
    if (disposed || currentSequence !== sequence) return 0;
    if (!active()) return clearInactive();
    const existingRefs = refs
      .map((ref, index) => {
        const cacheKey = terminalFileReferenceCacheKey(session, ref);
        if (!targets[index]) {
          existingReferenceTargets.delete(cacheKey);
          return null;
        }
        const targetPath = targets[index].path || ref.path || '';
        existingReferenceTargets.set(cacheKey, targetPath);
        return {...ref, targetPath};
      })
      .filter(Boolean);
    existingReferenceKeys = new Set(existingRefs.map(terminalFileReferenceKey));
    if (hoverKey && !existingReferenceKeys.has(hoverKey)) hoverKey = '';
    const count = renderTerminalFileReferenceUnderlines(term, container, existingRefs, {hoverKey});
    lastRenderedViewportSignature = terminalFileReferenceViewportSignature(term);
    return count;
  };

  const refresh = () => {
    if (refreshRequest) return refreshRequest;
    const request = refreshNow();
    refreshRequest = request;
    request.finally(() => {
      if (refreshRequest === request) refreshRequest = null;
    });
    return request;
  };

  const scheduleCachedRender = () => {
    if (!active()) {
      clearInactive();
      return;
    }
    if (renderFrame) return;
    renderFrame = requestAnimationFrame(() => {
      renderFrame = 0;
      if (active()) renderCached();
      else clearInactive();
    });
  };

  const schedule = (scheduleOptions = {}) => {
    if (!active()) {
      clearInactive();
      return;
    }
    const viewportSignature = terminalFileReferenceViewportSignature(term);
    const viewportChanged = scheduleOptions.viewportChanged === true || viewportSignature !== lastRenderedViewportSignature;
    const contentChanged = scheduleOptions.contentChanged === true || ['output', 'render'].includes(scheduleOptions.reason);
    if (viewportChanged || contentChanged) scheduleCachedRender();
    if ((viewportChanged || contentChanged) && !timer) {
      timer = setTimeout(() => {
        timer = 0;
        if (active()) refresh();
        else clearInactive();
      }, TERMINAL_FILE_UNDERLINE_REFRESH_MS);
    }
  };

  const bindTerminalEvent = (name, callback) => {
    const disposable = typeof term?.[name] === 'function' ? term[name](callback) : null;
    if (disposable?.dispose) disposables.push(disposable);
  };
  bindTerminalEvent('onScroll', () => schedule({reason: 'scroll', viewportChanged: true}));
  bindTerminalEvent('onResize', () => schedule({reason: 'resize', viewportChanged: true}));
  bindTerminalEvent('onRender', () => schedule({reason: 'render', contentChanged: true}));
  container.addEventListener?.('mousemove', updateHover);
  container.addEventListener?.('mouseleave', clearHover);
  disposables.push({
    dispose() {
      container.removeEventListener?.('mousemove', updateHover);
      container.removeEventListener?.('mouseleave', clearHover);
    },
  });
  schedule();

  return {
    schedule,
    refresh,
    dispose() {
      disposed = true;
      sequence += 1;
      if (timer) clearTimeout(timer);
      if (renderFrame) cancelAnimationFrame(renderFrame);
      timer = 0;
      renderFrame = 0;
      disposables.forEach(disposable => {
        try { disposable.dispose(); } catch (_) {}
      });
      clearTerminalFileReferenceUnderlines(container);
      container.querySelector?.(':scope > .terminal-file-link-underlines')?.remove?.();
    },
  };
}

function installTerminalLinkProvider(session, term) {
  if (typeof term.registerLinkProvider !== 'function') return;
  term.registerLinkProvider({
    provideLinks: (y, callback) => {
      terminalReferenceProviderLinks(session, term, y).then(callback).catch(() => callback([]));
    },
  });
}

function terminalCellDimensions(term, container) {
  const cell = term?._core?._renderService?._renderer?.dimensions?.css?.cell
    || term?._core?._renderService?.dimensions?.css?.cell
    || {};
  const width = Number(cell.width || 0);
  const height = Number(cell.height || 0);
  if (width > 0 && height > 0) return {width, height};
  const node = container?.querySelector?.('.xterm-rows') || container?.querySelector?.('.xterm-screen') || container;
  const rect = node?.getBoundingClientRect?.();
  const cols = Number(term?.cols || 0);
  const rows = Number(term?.rows || 0);
  return {
    width: cols > 0 && rect?.width ? rect.width / cols : 0,
    height: rows > 0 && rect?.height ? rect.height / rows : 0,
  };
}

function terminalScreenElement(container) {
  return container?.querySelector?.('.xterm-rows')
    || container?.querySelector?.('.xterm-screen')
    || container?.querySelector?.('.xterm')
    || container;
}

function terminalPositionFromClientPoint(term, container, clientX, clientY) {
  const node = terminalScreenElement(container);
  const rect = node?.getBoundingClientRect?.();
  const cell = terminalCellDimensions(term, container);
  if (!rect || !(cell.width > 0) || !(cell.height > 0)) return null;
  const localX = Number(clientX) - rect.left;
  const localY = Number(clientY) - rect.top;
  if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) return null;
  const cols = Math.max(1, Number(term?.cols || 1));
  const rows = Math.max(1, Number(term?.rows || 1));
  const x = Math.max(1, Math.min(cols, Math.floor(localX / cell.width) + 1));
  const screenRow = Math.max(1, Math.min(rows, Math.floor(localY / cell.height) + 1));
  const viewportY = Math.max(0, Number(term?.buffer?.active?.viewportY || 0));
  return {x, y: viewportY + screenRow};
}

function terminalTouchWordSelectionAtClientPoint(term, container, clientX, clientY) {
  const position = terminalPositionFromClientPoint(term, container, clientX, clientY);
  if (!position || typeof term?.select !== 'function') return null;
  const row = Math.max(0, position.y - 1);
  const column = Math.max(0, position.x - 1);
  const line = term?.buffer?.active?.getLine?.(row);
  const text = String(line?.translateToString?.(true) || '');
  if (!text || column >= text.length || /\s/.test(text[column])) return null;
  let start = column;
  let end = column + 1;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const selected = text.slice(start, end);
  if (!selected) return null;
  term.select(start, row, end - start);
  return {text: selected, start: {column: start, row}, end: {column: end - 1, row}};
}

function terminalExtendTouchSelection(term, selection, container, clientX, clientY) {
  const position = terminalPositionFromClientPoint(term, container, clientX, clientY);
  const anchor = selection?.start;
  if (!position || !anchor || typeof term?.select !== 'function') return false;
  const cols = Math.max(1, Number(term?.cols || 1));
  const lead = {column: Math.max(0, position.x - 1), row: Math.max(0, position.y - 1)};
  const anchorOffset = anchor.row * cols + anchor.column;
  const leadOffset = lead.row * cols + lead.column;
  const start = anchorOffset <= leadOffset ? anchor : lead;
  const end = anchorOffset <= leadOffset ? lead : selection.end || anchor;
  const length = Math.max(1, Math.abs(leadOffset - anchorOffset) + 1);
  term.select(start.column, start.row, length);
  selection.end = end;
  return true;
}

function terminalRangeContainsPosition(range, position) {
  if (!range || !position) return false;
  const start = range.start || {};
  const end = range.end || {};
  if (position.y < start.y || position.y > end.y) return false;
  if (start.y === end.y) return position.x >= start.x && position.x <= end.x;
  if (position.y === start.y) return position.x >= start.x;
  if (position.y === end.y) return position.x <= end.x;
  return true;
}

function terminalReferenceAtPosition(term, position) {
  if (!position) return null;
  const refs = terminalWrappedLineReferences(term, position.y);
  return refs.find(ref => terminalRangeContainsPosition(ref.range, position)) || null;
}

function terminalReferenceAtClientPoint(term, container, clientX, clientY) {
  return terminalReferenceAtPosition(term, terminalPositionFromClientPoint(term, container, clientX, clientY));
}

function dedentSelectionText(value) {
  const text = String(value ?? '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = text.split('\n');
  const indents = lines
    .filter(line => line.trim().length > 0 && /^[ \t]+/.test(line))
    .map(line => (line.match(/^[ \t]+/) || [''])[0].length);
  const stripBullet = line => line.replace(/^[ \t]*[●•]\s*/, '');
  if (!indents.length) return lines.map(stripBullet).join('\n');
  const commonIndent = Math.min(...indents);
  return lines
    .map(line => line.trim().length > 0 && /^[ \t]+/.test(line) ? line.slice(commonIndent) : line)
    .map(stripBullet)
    .join('\n');
}

const copyFeedbackMs = 1500;
const copyFeedbackStates = new Map();

function copyConfirmationLabel() {
  const label = String(t('status.copied') || '');
  return label ? `${label[0].toLocaleUpperCase(i18nActiveLocaleId())}${label.slice(1)}` : label;
}

function copyFeedbackLabel(key, fallback, nowMs = Date.now()) {
  const state = copyFeedbackStates.get(String(key || ''));
  return state && nowMs < state.until ? state.label : fallback;
}

function syncCopyFeedbackButtons(key) {
  const normalizedKey = String(key || '');
  if (!normalizedKey) return;
  const active = Boolean(copyFeedbackStates.get(normalizedKey)?.until > Date.now());
  for (const button of document.querySelectorAll(`[data-copy-feedback-key="${cssEscape(normalizedKey)}"]`)) {
    const fallback = button.dataset.copyFeedbackLabel || button.textContent || t('common.copy');
    const label = copyFeedbackLabel(normalizedKey, fallback);
    button.textContent = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    if (active) button.dataset.copyFeedbackActive = 'true';
    else delete button.dataset.copyFeedbackActive;
  }
}

function showCopyFeedback(options = {}) {
  const configuredStatusText = typeof options.statusText === 'function' ? options.statusText(options.result || null) : options.statusText;
  const statusText = String(configuredStatusText || t('status.copied'));
  const button = options.button || null;
  const key = String(options.feedbackKey || button?.dataset?.copyFeedbackKey || '');
  const label = copyConfirmationLabel();
  statusOk(esc(statusText));
  if (!button && !key) return;
  const fallback = String(options.buttonLabel || button?.dataset?.copyFeedbackLabel || button?.textContent || button?.getAttribute?.('aria-label') || t('common.copy'));
  if (button && !button.dataset.copyFeedbackLabel) button.dataset.copyFeedbackLabel = fallback;
  if (key) copyFeedbackStates.set(key, {until: Date.now() + copyFeedbackMs, label});
  if (button) {
    button.textContent = label;
    button.setAttribute('aria-label', label);
    button.setAttribute('title', label);
    button.dataset.copyFeedbackActive = 'true';
  }
  if (key) syncCopyFeedbackButtons(key);
  setTimeout(() => {
    if (key && Date.now() < (copyFeedbackStates.get(key)?.until || 0)) return;
    if (key) {
      copyFeedbackStates.delete(key);
      syncCopyFeedbackButtons(key);
    }
    if (button?.isConnected && !key) {
      button.textContent = fallback;
      button.setAttribute('aria-label', fallback);
      button.setAttribute('title', fallback);
      delete button.dataset.copyFeedbackActive;
    }
  }, copyFeedbackMs);
}

function copyTextWithFeedback(text, options = {}) {
  return copyTextToClipboard(text).then(() => {
    showCopyFeedback(options);
    return true;
  }, error => {
    statusErr(localizedHtml('common.copyFailed', {error}));
    if (options.rethrow === true) throw error;
    return false;
  });
}

async function copyTextToClipboard(text) {
  const clipboard = globalThis.navigator?.clipboard;
  const value = String(text ?? '');
  if (globalThis.isSecureContext !== false && clipboard?.writeText) {
    try {
      await clipboard.writeText(value);
      return;
    } catch (_) {
      // Fall through to execCommand. Some browsers expose navigator.clipboard
      // but reject writes on self-signed or permission-limited pages.
    }
  }
  if (copyTextToClipboardViaCopyEvent(value)) return;
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = `${OFFSCREEN_POSITION_PX}px`;
  textarea.style.top = `${OFFSCREEN_POSITION_PX}px`;
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand?.('copy') === true;
  textarea.remove();
  if (!copied) throw new Error(t('common.clipboardUnavailable'));
}

function copyTextToClipboardViaCopyEvent(text) {
  const value = String(text ?? '');
  let copied = false;
  const onCopy = event => {
    if (!event?.clipboardData) return;
    event.clipboardData.setData('text/plain', value);
    event.preventDefault();
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
    copied = true;
  };
  document.addEventListener?.('copy', onCopy, true);
  try {
    return document.execCommand?.('copy') === true && copied;
  } finally {
    document.removeEventListener?.('copy', onCopy, true);
  }
}

// ONE clipboard-write chain for terminal-initiated copies (shortcut copy AND the OSC 52
// bridge): synchronous copy-event first — it stays inside any live user activation — then the async
// navigator.clipboard path as fallback. Status text reports success/failure either way.
function writeTerminalTextToClipboard(text, options = {}) {
  const config = typeof options === 'string' ? {label: options} : (options || {});
  const action = config.action || TERMINAL_COPY_ACTIONS.selected;
  const label = config.label || terminalCopyStatusText(action, config.params || {});
  const afterCopy = typeof config.afterCopy === 'function' ? config.afterCopy : null;
  let cleanupDone = false;
  const cleanup = () => {
    if (cleanupDone || !afterCopy) return;
    cleanupDone = true;
    afterCopy();
  };
  if (copyTextToClipboardViaCopyEvent(text)) {
    copyDebug('clipboard', {via: 'copy-event', chars: String(text ?? '').length, ok: true});
    showCopyFeedback({statusText: label});
    cleanup();
    return;
  }
  copyTextWithFeedback(text, {statusText: label}).then(ok => {
    copyDebug('clipboard', {via: 'async', chars: String(text ?? '').length, ok});
  });
  cleanup();
}

// opt-in live instrumentation for the copy path. Set storage key 'yolomux.debugCopy' to '1'
// and every copy decision logs ONE compact console line — enough to see which link breaks without
// changing behavior.
function copyDebugEnabled() {
  return storageGet('yolomux.debugCopy') === '1';
}

function copyDebug(stage, fields = {}) {
  if (!copyDebugEnabled()) return;
  const parts = Object.entries(fields).map(([key, value]) => `${key}=${value}`).join(' ');
  console.log(`[copy-debug] ${stage} ${parts}`);
}

function overlayFocusableElements(overlay) {
  return Array.from(overlay?.querySelectorAll?.('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])') || []);
}

function createDismissableOverlayController(options = {}) {
  let overlay = null;
  let trigger = null;
  let removeOnClose = false;
  const close = (closeOptions = {}) => {
    if (!overlay) return false;
    const closed = overlay;
    const returnTarget = trigger;
    if (removeOnClose) closed.remove();
    else closed.hidden = true;
    overlay = null;
    trigger = null;
    removeOnClose = false;
    document.removeEventListener('pointerdown', pointerdown, true);
    document.removeEventListener('keydown', keydown, true);
    window.removeEventListener('blur', blur);
    options.onClose?.(closed);
    if (closeOptions.returnFocus !== false && returnTarget?.isConnected !== false) returnTarget?.focus?.();
    return true;
  };
  const pointerdown = event => {
    if (overlay?.contains(event.target) || trigger?.contains?.(event.target)) return;
    close({returnFocus: false});
  };
  const keydown = event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab' || options.trapFocus !== true) return;
    const focusable = overlayFocusableElements(overlay);
    const index = focusable.indexOf(document.activeElement);
    if (!focusable.length || (!event.shiftKey && index < focusable.length - 1) || (event.shiftKey && index > 0)) return;
    event.preventDefault();
    focusable[event.shiftKey ? focusable.length - 1 : 0].focus();
  };
  const blur = () => close({returnFocus: false});
  return {
    close,
    isOpen: () => Boolean(overlay),
    open(nextOverlay, openOptions = {}) {
      close({returnFocus: false});
      overlay = nextOverlay;
      trigger = openOptions.trigger || null;
      removeOnClose = openOptions.removeOnClose === true;
      overlay.hidden = false;
      overlay.addEventListener('pointerdown', event => event.stopPropagation());
      document.addEventListener('pointerdown', pointerdown, true);
      document.addEventListener('keydown', keydown, true);
      if (openOptions.closeOnBlur !== false) window.addEventListener('blur', blur);
      options.onOpen?.(overlay);
      return overlay;
    },
  };
}

function createContextMenuController() {
  const overlay = createDismissableOverlayController();
  return {
    close: overlay.close,
    isOpen: overlay.isOpen,
    open(menu, x, y) {
      appOverlayRootElement().appendChild(menu);
      positionContextMenu(menu, x, y);
      overlay.open(menu, {removeOnClose: true});
    },
  };
}

function makeButton(options = {}) {
  const button = document.createElement('button');
  button.type = options.type || 'button';
  const classNames = new Set(['btn-base', ...String(options.className || '').split(/\s+/).filter(Boolean)]);
  setDomBuilderOptions(button, {...options, className: [...classNames].join(' ')});
  button.disabled = options.disabled === true;
  if (options.pressed !== undefined) button.setAttribute('aria-pressed', options.pressed ? 'true' : 'false');
  if (options.checked !== undefined) {
    button.setAttribute('aria-checked', options.checked ? 'true' : 'false');
    if (options.checked === true) button.dataset.checked = 'true';
  }
  if (typeof options.onClick === 'function') button.addEventListener('click', options.onClick);
  for (const [type, listener] of Object.entries(options.events || {})) {
    if (typeof listener === 'function') button.addEventListener(type, listener);
  }
  return button;
}

const bindOnceRecords = new WeakMap();

function bindOnce(root, key, installer) {
  if (!root || key === undefined || key === null || typeof installer !== 'function') return null;
  let records = bindOnceRecords.get(root);
  if (!records) {
    records = new Map();
    bindOnceRecords.set(root, records);
  }
  const existing = records.get(key);
  if (existing) return existing.dispose;
  const uninstall = installer(root);
  let disposed = false;
  const dispose = () => {
    if (disposed) return false;
    disposed = true;
    if (records.get(key)?.dispose === dispose) records.delete(key);
    if (records.size === 0) bindOnceRecords.delete(root);
    if (typeof uninstall === 'function') uninstall();
    else uninstall?.dispose?.();
    return true;
  };
  records.set(key, {dispose});
  return dispose;
}

function bindScopedOnce(root, key, installer) {
  return bindOnce(root, key, () => {
    const scope = createLifecycleScope();
    const uninstall = installer(scope, root);
    return () => {
      if (typeof uninstall === 'function') uninstall();
      else uninstall?.dispose?.();
      scope.dispose(`bind-once:${String(key)}`);
    };
  });
}

function createLifecycleScope(options = {}) {
  const resources = new Map();
  let disposed = false;
  const disposeResource = record => {
    if (!record || record.disposed) return false;
    record.disposed = true;
    record.dispose?.(record.value);
    return true;
  };
  const scope = {
    current() {
      return !disposed && (typeof options.isCurrent !== 'function' || options.isCurrent() === true);
    },
    value(key) {
      return resources.get(key)?.value ?? null;
    },
    replace(key, value, dispose) {
      const existing = resources.get(key);
      if (existing?.value === value) return value;
      if (existing) disposeResource(existing);
      resources.delete(key);
      if (value === null || value === undefined) return value;
      if (disposed) {
        disposeResource({value, dispose, disposed: false});
        return value;
      }
      resources.set(key, {value, dispose, disposed: false});
      return value;
    },
    release(key, value = undefined) {
      const record = resources.get(key);
      if (!record || (value !== undefined && record.value !== value)) return false;
      resources.delete(key);
      return disposeResource(record);
    },
    relinquish(key, value = undefined) {
      const record = resources.get(key);
      if (!record || (value !== undefined && record.value !== value)) return false;
      resources.delete(key);
      record.disposed = true;
      return true;
    },
    ownEvent(key, target, type, listener, listenerOptions = undefined) {
      target?.addEventListener?.(type, listener, listenerOptions);
      return scope.replace(key, listener, () => target?.removeEventListener?.(type, listener, listenerOptions));
    },
    ownTimer(key, timer, clear = clearTimeout) {
      return scope.replace(key, timer, value => clear(value));
    },
    ownObserver(key, observer) {
      return scope.replace(key, observer, value => value?.disconnect?.());
    },
    ownStream(key, stream) {
      return scope.replace(key, stream, value => value?.close?.());
    },
    dispose(reason = 'disposed') {
      if (disposed) return false;
      disposed = true;
      for (const record of [...resources.values()].reverse()) disposeResource(record);
      resources.clear();
      options.onDispose?.(reason);
      return true;
    },
    disposed() { return disposed; },
  };
  return Object.freeze(scope);
}

function createLatestResource(options = {}) {
  let value = options.initial;
  let target = null;
  let request = null;
  let error = null;
  let lifecycleScope = createLifecycleScope();

  const snapshot = () => Object.freeze({value, target, request, error, loading: request !== null});
  const notify = (phase, context = null) => options.onState?.(snapshot(), Object.freeze({phase, context}));
  const renewScope = reason => {
    lifecycleScope.dispose(reason);
    lifecycleScope = createLifecycleScope();
    return lifecycleScope;
  };
  const assign = (nextValue, nextTarget = target) => {
    target = nextTarget;
    value = nextValue;
    error = null;
    return value;
  };
  const resource = {
    snapshot,
    read(nextTarget, context = null) {
      if (request && Object.is(target, nextTarget)) return request;
      const scope = renewScope('latest-resource-superseded');
      target = nextTarget;
      error = null;
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      if (controller) scope.replace('request-controller', controller, value => value.abort());
      let loaded;
      try {
        loaded = options.load(nextTarget, Object.freeze({context, signal: controller?.signal}));
      } catch (failure) {
        loaded = Promise.reject(failure);
      }
      const currentRequest = Promise.resolve(loaded)
        .then(payload => {
          if (!scope.current()) return typeof options.staleResult === 'function' ? options.staleResult() : value;
          const applied = options.apply(payload, Object.freeze({context, target: nextTarget, previous: value}));
          if (applied !== undefined) assign(applied, nextTarget);
          error = null;
          notify('applied', context);
          return typeof options.result === 'function' ? options.result(payload, applied) : value;
        })
        .catch(failure => {
          if (!scope.current()) return typeof options.staleResult === 'function' ? options.staleResult() : value;
          error = failure;
          notify('failed', context);
          return typeof options.failureResult === 'function' ? options.failureResult(failure, value) : value;
        })
        .finally(() => {
          if (!scope.current() || request !== currentRequest) return;
          request = null;
          scope.release('request-controller', controller);
          notify('settled', context);
        });
      request = currentRequest;
      notify('loading', context);
      return currentRequest;
    },
    replace(nextValue, nextTarget = target, context = null) {
      renewScope('latest-resource-replaced');
      request = null;
      assign(nextValue, nextTarget);
      notify('replaced', context);
      return value;
    },
    assign,
    invalidate(context = null) {
      renewScope('latest-resource-invalidated');
      request = null;
      error = null;
      notify('invalidated', context);
      return value;
    },
  };
  return Object.freeze(resource);
}

function delegate(parent, type, selector, handler, options = {}) {
  if (!parent || typeof handler !== 'function') return null;
  const listener = event => {
    const target = event.target?.closest?.(selector);
    if (!target || (typeof parent.contains === 'function' && !parent.contains(target))) return;
    handler(event, target);
  };
  parent.addEventListener(type, listener, options);
  return listener;
}

function copyPathButtonValue(button) {
  return String(button?.dataset?.copyPath || '');
}

function copyPathButtonStopEvent(event) {
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation?.();
}

function activateCopyPathButton(event, button) {
  copyPathButtonStopEvent(event);
  const path = copyPathButtonValue(button);
  if (!path) return;
  void copyTextWithFeedback(path, {button});
}

function handleCopyPathPointerUp(event, button) {
  button.__yolomuxCopyPointerHandled = true;
  activateCopyPathButton(event, button);
}

function handleCopyPathClick(event, button) {
  const pointerHandled = button.__yolomuxCopyPointerHandled === true;
  button.__yolomuxCopyPointerHandled = false;
  if (pointerHandled && event.detail !== 0) {
    copyPathButtonStopEvent(event);
    return;
  }
  activateCopyPathButton(event, button);
}

delegate(document, 'pointerup', '[data-copy-path]', handleCopyPathPointerUp, {capture: true});
delegate(document, 'click', '[data-copy-path]', handleCopyPathClick, {capture: true});

// One owner for the per-session/-item DOM id scheme. Both the element that sets the id and every
// getElementById/querySelector that looks it up route through these, so the prefix lives in one place
// (the ids are produced + consumed across 7 partials). Changing a prefix is then a one-line edit.
const panelDomId = item => `panel-${item}`;
const paneTabDomId = session => `panel-tab-${session}`;
const terminalDomId = session => `term-${session}`;
const transcriptDomId = session => `transcript-${session}`;
const summaryDomId = session => `summary-${session}`;

// One inflight-dedup wrapper: run makeRequest() at most once per key while a call is outstanding, so
// concurrent callers for the same key share the single in-flight promise and clean up after it settles.
// When canReuse is false the caller wants an untracked fresh fetch, so it runs without registering. The
// TTL cache-hit check and any resource-specific guards stay at the call site; this owns only the inflight
// Map bookkeeping that was hand-rolled identically per filesystem resource (dir listing, path info, blame).
function dedupeInflight(inflight, key, canReuse, makeRequest) {
  if (canReuse) {
    const existing = inflight.get(key);
    if (existing) return existing;
  }
  const request = makeRequest();
  if (!canReuse) return request;
  inflight.set(key, request);
  return (async () => {
    try {
      return await request;
    } finally {
      if (inflight.get(key) === request) inflight.delete(key);
    }
  })();
}

function appendContextMenuButton(menu, label, handler, closeMenu, options = {}) {
  const iconHtml = options.iconHtml ? stripTitleAttrs(options.iconHtml) : '';
  const shortcutHtml = options.shortcut ? `<span class="context-menu-shortcut">${esc(options.shortcut)}</span>` : '';
  const buttonHtml = iconHtml || shortcutHtml
    ? `<span class="context-menu-line">${iconHtml ? `<span class="context-menu-icon">${iconHtml}</span>` : ''}<span class="context-menu-label">${esc(label)}</span>${shortcutHtml}</span>`
    : undefined;
  const className = ['control-active-hover', options.className || ''].filter(Boolean).join(' ');
  const button = makeButton({
    ...options,
    className,
    html: buttonHtml,
    label: buttonHtml ? undefined : label,
    ariaLabel: options.ariaLabel || label,
    role: options.checked !== undefined ? 'menuitemcheckbox' : 'menuitem',
  });
  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (!button.disabled) handler(button);
    if (options.keepOpen !== true) closeMenu();
  });
  menu.appendChild(button);
  return button;
}

function appendContextMenuSeparator(menu) {
  const separator = document.createElement('div');
  separator.className = 'terminal-context-menu-separator';
  separator.role = 'separator';
  menu.appendChild(separator);
  return separator;
}

function contextMenuIsOpen() {
  return terminalContextMenu.isOpen() || fileContextMenu.isOpen() || sessionContextMenu.isOpen() || linkContextMenu.isOpen();
}

function rootCssLengthPx(name) {
  if (!document.body || typeof window.getComputedStyle !== 'function') return 0;
  const value = window.getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!value) return 0;
  const probe = document.createElement('div');
  probe.style.position = 'fixed';
  probe.style.visibility = 'hidden';
  probe.style.pointerEvents = 'none';
  probe.style.width = value;
  probe.style.height = '0';
  document.body.appendChild(probe);
  const width = probe.getBoundingClientRect().width || 0;
  probe.remove();
  return Math.max(0, width);
}

function minSplitPaneWidthPx() {
  return rootCssLengthPx('--min-split-pane-width') || MIN_SPLIT_PANE_WIDTH_FALLBACK_PX;
}

function minSplitPaneHeightPx() {
  return rootCssLengthPx('--min-split-pane-height') || MIN_SPLIT_PANE_HEIGHT_FALLBACK_PX;
}

function popoverEdgeGapPx() {
  return rootCssLengthPx('--popover-edge-gap');
}

function positionContextMenu(menu, x, y) {
  const rect = menu.getBoundingClientRect();
  const edgeGap = popoverEdgeGapPx();
  const viewport = appViewport();
  const sheet = menu.classList?.contains('tab-action-sheet');
  const desiredLeft = sheet ? x - rect.width / 2 : x;
  const left = Math.min(Math.max(edgeGap, desiredLeft), Math.max(edgeGap, viewport.width - rect.width - edgeGap));
  const maxTop = Math.max(edgeGap, viewport.height - rect.height - edgeGap);
  const shouldOpenAbove = sheet && y + rect.height > viewport.height - edgeGap && y - rect.height - edgeGap >= edgeGap;
  const desiredTop = shouldOpenAbove ? y - rect.height - edgeGap : y;
  const top = Math.min(Math.max(edgeGap, desiredTop), maxTop);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
  menu.style.bottom = 'auto';
}

function closeTerminalContextMenu() {
  terminalContextMenu.close();
}

function closeFileContextMenu() {
  fileContextMenu.close();
}

function closeSessionContextMenu() {
  sessionContextMenu.close();
}

function closeLinkContextMenu() {
  linkContextMenu.close();
}

function closeContextMenus() {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeLinkContextMenu();
}

function normalizedExternalHttpUrl(value, options = {}) {
  const raw = String(value || '').trim();
  if (!raw || raw.length > Math.max(1, Number(options.maxLength) || 8192)) return '';
  try {
    const url = new URL(options.decodeHtmlAmpersands === true ? raw.replace(/&amp;/gi, '&') : raw);
    if (!['http:', 'https:'].includes(url.protocol.toLowerCase()) || url.username || url.password) return '';
    return url.href;
  } catch (_) {
    return '';
  }
}

function openExternalLinkFromEvent(event, root = document) {
  const anchor = event?.target?.closest?.('a[href]');
  if (!anchor || (root && !root.contains?.(anchor))) return false;
  const url = normalizedExternalHttpUrl(anchor.href || anchor.getAttribute?.('href'));
  if (!url) return false;
  let target;
  try {
    target = new URL(url, window.location.href);
  } catch (_) {
    return false;
  }
  if (target.origin === window.location.origin) return false;
  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (!opened) return false;
  event.preventDefault?.();
  return true;
}

function triggerExternalUrlDownload(value) {
  const url = normalizedExternalHttpUrl(value);
  if (!url || !document.body) return false;
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = basenameOf(new URL(url).pathname) || 'download';
  anchor.target = '_blank';
  anchor.rel = 'noopener noreferrer';
  anchor.hidden = true;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  return true;
}

function appendUrlContextMenuItems(menu, href, closeMenu, options = {}) {
  const url = String(href || '');
  if (!url) return false;
  const selectedText = String(options.selectionText || '');
  const action = (reason, handler) => (
    options.term || options.container
      ? consumeTerminalSelection(options.session, options.term, options.container, reason, handler)
      : handler
  );
  appendContextMenuButton(menu, t('contextmenu.openUrl'), action('open-url', () => window.open(url, '_blank', 'noopener,noreferrer')), closeMenu);
  appendContextMenuButton(menu, t('contextmenu.copyUrl'), action('copy-url', button => copyTextWithFeedback(url, {button})), closeMenu);
  if (options.includeSelectedText && selectedText && selectedText !== url) {
    appendContextMenuButton(menu, t('contextmenu.copySelectedText'), action('copy-selected-text', button => copyTextWithFeedback(selectedText, {button})), closeMenu);
  }
  return true;
}

// right-click menu for links in AI/markdown content — Open URL / Copy URL. Bound on the
// YO!agent body and markdown previews via installLinkContextMenu(container).
function showLinkContextMenu(anchor, x, y) {
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeSessionContextMenu();
  closeOtherSessionPopovers(null);
  const href = anchor?.href || '';
  if (!href) return;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu link-context-menu';
  menu.setAttribute('role', 'menu');
  appendUrlContextMenuItems(menu, href, closeLinkContextMenu);
  linkContextMenu.open(menu, x, y);
}

function installLinkContextMenu(container) {
  if (!container) return null;
  return bindScopedOnce(container, 'link-context-menu', scope => {
    scope.ownEvent('contextmenu', container, 'contextmenu', event => {
      const anchor = event.target?.closest?.('a[href]');
      if (!anchor || !container.contains(anchor)) return;
      event.preventDefault();
      event.stopPropagation();
      showLinkContextMenu(anchor, event.clientX, event.clientY);
    });
  });
}

function nodeInsideElement(element, node) {
  if (!element || !node) return false;
  if (element === node) return true;
  if (element.contains?.(node)) return true;
  let current = node.parentElement || node.parentNode || null;
  while (current) {
    if (current === element) return true;
    current = current.parentElement || current.parentNode || null;
  }
  return false;
}

function browserSelectionTextInside(container) {
  if (!container) return '';
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  const text = String(selection?.toString?.() || '');
  if (!text) return '';
  const anchorNode = selection.anchorNode || null;
  const focusNode = selection.focusNode || null;
  if (!anchorNode && !focusNode) return '';
  return nodeInsideElement(container, anchorNode) || nodeInsideElement(container, focusNode) ? text : '';
}

function terminalSelectedText(term, container = null) {
  return term.getSelection?.() || browserSelectionTextInside(container);
}

function browserSelectionTouchesContainer(container, selection = null) {
  if (!container) return false;
  const current = selection || globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  if (!current) return false;
  const anchorNode = current.anchorNode || null;
  const focusNode = current.focusNode || null;
  if (!anchorNode && !focusNode) return false;
  return nodeInsideElement(container, anchorNode) || nodeInsideElement(container, focusNode);
}

function terminalVisibleSelectionState(session, term, container = null) {
  const xtermText = String(term?.getSelection?.() || '');
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  const browserInside = browserSelectionTouchesContainer(container, selection);
  const browserText = browserInside ? String(selection?.toString?.() || '') : '';
  const appClipboard = recentTerminalAppClipboardText(session);
  let paneMode = '';
  try {
    const panes = typeof tmuxSignalAgentPanesForSession === 'function' ? tmuxSignalAgentPanesForSession(session) : [];
    const labels = typeof tmuxSignalPaneModeLabels === 'function'
      ? panes.flatMap(pane => tmuxSignalPaneModeLabels(pane))
      : [];
    paneMode = labels.join(',');
  } catch (_error) {
    paneMode = '';
  }
  return {
    xtermChars: xtermText.length,
    browserChars: browserText.length,
    browserInside,
    recentOsc52Chars: String(appClipboard || '').length,
    paneMode,
  };
}

function clearTerminalVisibleSelection(session, term, container = null, reason = 'terminal-selection-consumed') {
  const before = terminalVisibleSelectionState(session, term, container);
  const selection = globalThis.getSelection?.() || globalThis.window?.getSelection?.();
  let browserCleared = false;
  if (browserSelectionTouchesContainer(container, selection) && typeof selection?.removeAllRanges === 'function') {
    selection.removeAllRanges();
    browserCleared = true;
  }
  const xtermClearCalled = typeof term?.clearSelection === 'function';
  if (xtermClearCalled) term.clearSelection();
  if (browserCleared || xtermClearCalled || before.xtermChars || before.browserChars || before.recentOsc52Chars || before.paneMode) {
    copyDebug('selection-clear', {session, reason, ...before, browserCleared, xtermClearCalled});
  }
  return {before, browserCleared, xtermClearCalled};
}

function withTerminalVisibleSelectionCleanup(session, term, container, reason, handler) {
  return async () => {
    try {
      return await handler();
    } finally {
      clearTerminalVisibleSelection(session, term, container, reason);
    }
  };
}

const TERMINAL_COPY_ACTIONS = Object.freeze({
  selected: Object.freeze({
    labelKey: 'common.copy',
    statusKey: 'status.copied',
    reason: 'copy-selection',
    dedent: false,
  }),
  selectedDedent: Object.freeze({
    labelKey: 'terminal.copyWithoutIndent',
    statusKey: 'status.copiedWithoutIndent',
    reason: 'copy-without-indent',
    dedent: true,
  }),
  tmux: Object.freeze({
    labelKey: 'common.copyTmuxSelection',
    statusPluralKey: 'status.copiedTmuxSelection',
    reason: 'copy-tmux-selection',
  }),
  osc52: Object.freeze({
    statusPluralKey: 'status.copiedTerminalChars',
    reason: 'copy-osc52-selection',
  }),
});

function terminalCopyActionForOptions(options = {}) {
  if (options.action) return options.action;
  return options.dedent ? TERMINAL_COPY_ACTIONS.selectedDedent : TERMINAL_COPY_ACTIONS.selected;
}

function terminalCopyActionLabel(action) {
  return action?.labelKey ? t(action.labelKey) : '';
}

function terminalCopyStatusText(action, params = {}) {
  if (action?.statusPluralKey) return tPlural(action.statusPluralKey, params.count, params);
  return t(action?.statusKey || 'status.copied', params);
}

function consumeTerminalSelection(session, term, container, action, handler) {
  const reason = typeof action === 'string' ? action : (action?.reason || 'terminal-selection-consumed');
  return withTerminalVisibleSelectionCleanup(session, term, container, reason, handler);
}

const TERMINAL_APP_CLIPBOARD_MAX_AGE_MS = 15000;
const terminalAppClipboardText = new Map();

function rememberTerminalAppClipboardText(session, text, timestamp = Date.now()) {
  const value = String(text ?? '');
  if (!value) return;
  terminalAppClipboardText.set(String(session || ''), {text: value, timestamp});
}

function recentTerminalAppClipboardText(session, timestamp = Date.now()) {
  const key = String(session || '');
  const entry = terminalAppClipboardText.get(key);
  if (!entry) return '';
  if (timestamp - entry.timestamp > TERMINAL_APP_CLIPBOARD_MAX_AGE_MS) {
    terminalAppClipboardText.delete(key);
    return '';
  }
  return entry.text;
}

function terminalContextMenuSelection(session, term, container = null, presetSelection = null) {
  const selected = presetSelection == null ? terminalSelectedText(term, container) : String(presetSelection || '');
  if (selected) return {text: selected, source: 'terminal'};
  const appSelection = recentTerminalAppClipboardText(session);
  return appSelection ? {text: appSelection, source: 'app-clipboard'} : {text: '', source: 'none'};
}

function terminalFileReferenceCandidatePaths(session, reference) {
  const raw = String(reference?.path || '').trim();
  if (!raw || raw.includes('\0') || /[\r\n]/.test(raw)) return [];
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw)) return [];
  if (raw === '~') return homePath ? [normalizeDirectoryPath(homePath)] : [];
  if (raw.startsWith('~/')) return homePath ? [joinAndNormalize(homePath, raw.slice(2))] : [];
  if (raw.startsWith('/')) return [normalizeDirectoryPath(raw)];
  const info = sessionTranscriptInfo(session);
  const selectedRepo = selectedSessionRepo(session, info.info);
  const bases = [
    terminalCurrentPath(session),
    info.selectedPath,
    info.gitCwd,
    selectedRepo?.cwd,
    selectedRepo?.root,
    info.gitRoot,
    homePath,
  ];
  const paths = [];
  for (const base of bases) {
    const normalizedBase = normalizeDirectoryPath(base || '');
    const candidate = normalizedBase ? joinAndNormalize(normalizedBase, raw) : '';
    if (!candidate || paths.includes(candidate)) continue;
    paths.push(candidate);
  }
  return paths;
}

function terminalFileReferenceAbsolutePath(session, reference) {
  return terminalFileReferenceCandidatePaths(session, reference)[0] || '';
}

function terminalFileReferenceRejectionLabel(reason) {
  return `${t('editor.fileOpenFailedTitle')}: ${reason}`;
}

async function terminalFileReferenceTarget(session, reference, options = {}) {
  if (reference?.type !== 'file') return null;
  const canReuse = options.fresh === false;
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const cacheKey = terminalFileReferenceCacheKey(session, reference);
  if (canReuse && terminalFileReferenceTargetCache.has(cacheKey)) {
    const cached = terminalFileReferenceTargetCache.get(cacheKey);
    if (cached.expiresAt > now()) {
      setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, cached, fileExplorerMemoryCacheLimit);
      return cached.promise || cached.value;
    }
    terminalFileReferenceTargetCache.delete(cacheKey);
  }
  const fetchOptions = {
    user: options.user !== false,
    fresh: !canReuse,
  };
  const targetPromise = (async () => {
    let rejection = null;
    for (const path of terminalFileReferenceCandidatePaths(session, reference)) {
      try {
        const info = await fetchFilePathInfo(path, fetchOptions);
        if (info?.kind === 'file') return {path, info, line: reference.line || null, text: reference.text || path};
      } catch (error) {
        // Try the next context-derived candidate; a missing cwd-relative path can still be repo-relative.
        const reason = userMessageText(error, t('common.requestFailed'));
        rejection = {
          kind: 'rejected',
          label: terminalFileReferenceRejectionLabel(reason),
          path,
          reason,
        };
      }
    }
    if (rejection && options.reportRejection) {
      recordJsDebugEvent('client_failure', {
        operation: 'terminal-file-reference',
        reason_code: 'file_info_rejected',
        path: rejection.path,
        error: rejection.reason,
      });
    }
    return options.reportRejection ? rejection : null;
  })();
  if (!canReuse) return targetPromise;
  const cacheEntry = {promise: targetPromise, value: null, expiresAt: Number.POSITIVE_INFINITY, paths: terminalFileReferenceCandidatePaths(session, reference)};
  setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, cacheEntry, fileExplorerMemoryCacheLimit);
  try {
    const target = await targetPromise;
    if (terminalFileReferenceTargetCache.get(cacheKey) === cacheEntry) {
      cacheEntry.promise = null;
      cacheEntry.value = target;
      cacheEntry.expiresAt = now() + (target ? terminalFileReferencePositiveCacheMs : terminalFileReferenceNegativeCacheMs);
      setLimitedMapEntry(terminalFileReferenceTargetCache, cacheKey, cacheEntry, fileExplorerMemoryCacheLimit);
    }
    return target;
  } catch (error) {
    if (terminalFileReferenceTargetCache.get(cacheKey) === cacheEntry) terminalFileReferenceTargetCache.delete(cacheKey);
    throw error;
  }
}

function invalidateTerminalFileReferenceTargets(roots = []) {
  const normalizedRoots = (Array.isArray(roots) ? roots : [roots])
    .map(path => normalizeDirectoryPath(String(path || '')))
    .filter(Boolean);
  if (!normalizedRoots.length) return 0;
  let invalidated = 0;
  for (const [key, entry] of terminalFileReferenceTargetCache) {
    const paths = Array.isArray(entry?.paths) ? entry.paths : [key.split('\x1f')[0]];
    if (!paths.some(path => normalizedRoots.some(root => pathIsInsideDirectory(path, root)))) continue;
    terminalFileReferenceTargetCache.delete(key);
    invalidated += 1;
  }
  return invalidated;
}

function requestFileEditorLineTarget(item, line) {
  const cleanLine = Math.max(1, Math.floor(Number(line) || 0));
  if (!item || !cleanLine) return false;
  pendingFileEditorLineTargets.set(item, cleanLine);
  const panel = panelNodes.get(item);
  if (panel?._cmView && typeof applyPendingFileEditorLineTarget === 'function') {
    return applyPendingFileEditorLineTarget(item, panel);
  }
  return true;
}

async function openTerminalFileReference(target) {
  if (!target?.path) return;
  const item = await openFileInEditor(target.path, target.info || {name: basenameOf(target.path)}, {viewMode: 'edit', userInitiated: true});
  if (item && target.line) requestFileEditorLineTarget(item, target.line);
}

function appendTerminalReferenceContextMenuItems(menu, reference, fileTarget = null, options = {}) {
  if (!reference) return false;
  if (reference.type === 'url') {
    const href = reference.href || normalizeTerminalLink(reference.text);
    if (!href) return false;
    return appendUrlContextMenuItems(menu, href, closeTerminalContextMenu, {
      includeSelectedText: true,
      selectionText: options.selectionText,
      session: options.session,
      term: options.term,
      container: options.container,
    });
  }
  if (reference.type === 'file' && fileTarget?.kind === 'rejected') {
    appendContextMenuButton(menu, fileTarget.label, () => {}, closeTerminalContextMenu, {disabled: true});
    appendContextMenuButton(menu, t('contextmenu.copyPath'), button => copyTextWithFeedback(fileTarget.path, {button, statusText: t('status.copiedPath')}), closeTerminalContextMenu);
    return true;
  }
  if (reference.type === 'file' && fileTarget) {
    appendContextMenuButton(menu, t('common.openFile'), () => openTerminalFileReference(fileTarget), closeTerminalContextMenu);
    appendContextMenuButton(menu, t('contextmenu.copyPath'), button => copyTextWithFeedback(fileTarget.path, {button, statusText: t('status.copiedPath')}), closeTerminalContextMenu);
    return true;
  }
  return false;
}

async function copyTerminalSelection(session, term, options = {}, container = null) {
  // N7: the context menu passes the selection captured at right-click time, because by the time the user
  // clicks the menu the live selection may be gone (focus moved to the menu).
  const selected = options.selectionText != null ? options.selectionText : terminalSelectedText(term, container);
  const action = terminalCopyActionForOptions(options);
  if (!selected) {
    statusEl.textContent = t('status.nothingSelected');
    return;
  }
  const text = action.dedent ? dedentSelectionText(selected) : selected;
  try {
    await copyTextWithFeedback(text, {button: options.button, statusText: terminalCopyStatusText(action)});
  } finally {
    clearTerminalVisibleSelection(session, term, container, action.reason);
  }
}

function copyTerminalSelectionFromShortcut(session, term, options = {}, container = null) {
  const selected = terminalSelectedText(term, container);
  const action = terminalCopyActionForOptions(options);
  if (!selected) {
    statusEl.textContent = t('status.nothingSelected');
    return false;
  }
  const text = action.dedent ? dedentSelectionText(selected) : selected;
  writeTerminalTextToClipboard(text, {
    action,
    afterCopy: () => clearTerminalVisibleSelection(session, term, container, action.reason),
  });
  return true;
}

function copyTerminalSelectionToClipboardEvent(session, term, event, container = null) {
  const selected = terminalSelectedText(term, container);
  if (!selected || !event?.clipboardData) return false;
  event.clipboardData.setData('text/plain', selected);
  event.preventDefault();
  event.stopPropagation();
  showCopyFeedback({statusText: terminalCopyStatusText(TERMINAL_COPY_ACTIONS.selected)});
  clearTerminalVisibleSelection(session, term, container, TERMINAL_COPY_ACTIONS.selected.reason);
  return true;
}

async function copyTmuxSelectionToClipboard(session, term = null, container = null, options = {}) {
  const payloadPromise = fetchTmuxSelectionText(session);
  try {
    await copyDeferredTextToClipboard(payloadPromise, {
      button: options.button,
      statusText: result => terminalCopyStatusText(TERMINAL_COPY_ACTIONS.tmux, {count: Number(result?.payload?.chars) || String(result?.text || '').length}),
    });
    return true;
  } catch (error) {
    if (error?.noClipboardText) {
      statusEl.textContent = error.message || t('status.nothingSelected');
      return false;
    }
    statusErr(esc(userMessageText(error, t('common.copyFailed', {error}))));
    return false;
  } finally {
    clearTerminalVisibleSelection(session, term, container, 'copy-tmux-selection');
  }
}

async function fetchTmuxSelectionText(session) {
  const payload = await apiFetchJson(`/api/tmux-copy-selection?session=${encodeURIComponent(session)}`, {method: 'POST'});
  const text = payload?.copied ? String(payload.text || '') : '';
  if (!text) {
    const error = new Error(userMessageText(payload, t('status.nothingSelected')));
    error.noClipboardText = true;
    throw error;
  }
  return {payload, text};
}

async function copyDeferredTextToClipboard(payloadPromise, options = {}) {
  const clipboard = globalThis.navigator?.clipboard;
  if (clipboard?.write && typeof globalThis.ClipboardItem === 'function' && typeof globalThis.Blob === 'function') {
    const textBlob = payloadPromise.then(({text}) => new Blob([text], {type: 'text/plain'}));
    try {
      await clipboard.write([new ClipboardItem({'text/plain': textBlob})]);
      const result = await payloadPromise;
      showCopyFeedback({...options, result});
      return result;
    } catch (error) {
      if (error?.noClipboardText) throw error;
      const result = await payloadPromise;
      await copyTextWithFeedback(result.text, {...options, result, rethrow: true});
      return result;
    }
  }
  const result = await payloadPromise;
  await copyTextWithFeedback(result.text, {...options, result, rethrow: true});
  return result;
}

// ROOT CAUSE: while Claude (or any TUI) owns the mouse inside tmux, the visible selection is the
// APP's — a plain drag never creates an xterm selection, and the copied text instead arrives as an
// OSC 52 clipboard escape (app -> tmux `set-clipboard` passthrough -> our PTY -> xterm.js). xterm.js
// DROPS OSC 52 unless a handler is registered, so those copies silently vanished. This bridge decodes
// the escape and writes the browser clipboard. It also catches tmux copy-mode `copy-pipe` buffers.
// Payload format: "Pc;Pd" — Pc selects clipboard(s) (c/p/s/q...), Pd is base64 text or '?' (a READ
// request, which we never answer so apps cannot exfiltrate the browser clipboard).
function osc52ClipboardText(data) {
  const raw = String(data ?? '');
  const semi = raw.indexOf(';');
  if (semi < 0) return null;
  const payload = raw.slice(semi + 1);
  if (!payload || payload === '?') return null;
  try {
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const decoded = new TextDecoder('utf-8', {fatal: false}).decode(bytes);
    return decoded || null;
  } catch (_error) {
    return null; // not valid base64: ignore rather than copy garbage
  }
}

function installTerminalOsc52Bridge(session, term) {
  if (!term?.parser?.registerOscHandler) return false;
  term.parser.registerOscHandler(52, data => {
    const text = osc52ClipboardText(data);
    copyDebug('osc52', {session, payloadChars: String(data ?? '').length, textChars: text ? text.length : 0});
    if (text) {
      rememberTerminalAppClipboardText(session, text);
      writeTerminalTextToClipboard(text, {action: TERMINAL_COPY_ACTIONS.osc52, params: {count: text.length}});
    }
    return true; // consumed either way; '?' queries get no reply
  });
  return true;
}

function handleTerminalCopyShortcutKeydown(session, term, container, event) {
  if (event.type !== 'keydown') return false;
  if (event.code !== 'KeyC' && event.key?.toLowerCase() !== 'c') return false;
  const isTmuxCopyShortcut = event.altKey
    && !event.shiftKey
    && ((isMacPlatform() && event.metaKey && !event.ctrlKey)
      || (!isMacPlatform() && event.ctrlKey && !event.metaKey));
  if (isTmuxCopyShortcut) {
    event.preventDefault();
    copyTmuxSelectionToClipboard(session, term, container);
    return true;
  }
  const isCmdC = event.metaKey && !event.ctrlKey && !event.altKey;
  const isCtrlC = event.ctrlKey && !event.metaKey && !event.altKey;
  if (!isCmdC && !isCtrlC) return false;
  const xtermSelected = term.getSelection?.() || '';
  const browserSelected = browserSelectionTextInside(container);
  const selected = xtermSelected || browserSelected;
  copyDebug('shortcut', {
    session,
    combo: isCmdC ? 'cmd-c' : 'ctrl-c',
    xtermSel: xtermSelected.length,
    browserSel: browserSelected.length,
    branch: selected ? 'copy' : (isCmdC ? 'no-selection' : 'sigint'),
  });
  if (!selected) {
    if (isCmdC) {
      event.preventDefault();
      // in a Claude/tmux pane the APP owns the mouse, so a plain drag never creates an xterm
      // selection — tell the user the working gestures instead of dead-ending.
      statusEl.textContent = isMacPlatform()
        ? t('terminal.copyHintMac')
        : t('terminal.copyHintPc');
      return true;
    }
    return false; // no selection: let Ctrl-C through as SIGINT
  }
  event.preventDefault();
  copyTerminalSelectionFromShortcut(session, term, {}, container);
  return true;
}

function terminalTmuxWindowShortcutDirection(event) {
  if (!event || event.type !== 'keydown' || !event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return 0;
  if (event.key === 'ArrowLeft' || event.code === 'ArrowLeft') return -1;
  if (event.key === 'ArrowRight' || event.code === 'ArrowRight') return 1;
  return 0;
}

function terminalTmuxWindowShortcutItem(session) {
  const activeItem = visualActivePaneItem();
  return activeItem || session;
}

function handleTerminalTmuxWindowShortcutKeydown(session, event) {
  const direction = terminalTmuxWindowShortcutDirection(event);
  if (!direction) return false;
  event.preventDefault?.();
  if (typeof selectAdjacentPaneTab === 'function') {
    selectAdjacentPaneTab(direction, {item: terminalTmuxWindowShortcutItem(session), userInitiated: true});
  }
  return true;
}

function terminalKeyScrollIntent(event) {
  if (event?.type !== 'keydown') return null;
  if (appModifier(event) && !event.shiftKey) {
    if (event.key === 'ArrowUp' || event.code === 'ArrowUp') return {direction: -1, source: 'keyboard', forceTmuxScrollback: true};
    if (event.key === 'ArrowDown' || event.code === 'ArrowDown') return {direction: 1, source: 'keyboard', forceTmuxScrollback: true};
    return null;
  }
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (event.key === 'PageUp' || event.code === 'PageUp') return {direction: -1, source: 'page-key', forceTmuxScrollback: event.shiftKey === true};
  if (event.key === 'PageDown' || event.code === 'PageDown') return {direction: 1, source: 'page-key', forceTmuxScrollback: event.shiftKey === true};
  return null;
}

function terminalHasMouseTracking(term) {
  const mode = term?.modes?.mouseTrackingMode;
  return mode === 'x10' || mode === 'vt200' || mode === 'drag' || mode === 'any';
}

function handleTerminalTmuxHistoryNavigationKeydown(session, term, event) {
  const intent = terminalKeyScrollIntent(event);
  if (!intent) return false;
  const item = terminals.get(session);
  const handled = routeTerminalScrollLines(session, term, item?.container || document.getElementById(terminalDomId(session)), intent.direction * terminalScrollPageLines(term), {
    source: intent.source,
    forceTmuxScrollback: intent.forceTmuxScrollback,
  });
  if (handled) event.preventDefault?.();
  return handled;
}

function handleTerminalScrollbackKeydown(session, term, container, event) {
  return handleTerminalTmuxHistoryNavigationKeydown(session, term, event);
}

function installTerminalCopyShortcut(session, term, container = null) {
  // Ctrl-C / Cmd-C copy the xterm selection. Plain Ctrl-C with NO selection
  // must still send SIGINT to the PTY, and Cmd-C must stay browser/xterm copy
  // only. Tmux copy-mode text has a separate explicit shortcut/menu action.
  container?.addEventListener?.('keydown', event => {
    if (!handleTerminalScrollbackKeydown(session, term, container, event)
      && !handleTerminalTmuxWindowShortcutKeydown(session, event)
      && !handleTerminalCopyShortcutKeydown(session, term, container, event)) return;
    event.stopImmediatePropagation?.();
    event.stopPropagation?.();
  }, {capture: true});
  term.attachCustomKeyEventHandler?.(event => {
    return (handleTerminalScrollbackKeydown(session, term, container, event)
      || handleTerminalTmuxWindowShortcutKeydown(session, event)
      || handleTerminalCopyShortcutKeydown(session, term, container, event)) ? false : true;
  });
}

async function showTerminalContextMenu(session, term, x, y, options = {}) {
  const {container = null, presetSelection = null, reference = null} = options || {};
  closeFileContextMenu();
  closeSessionContextMenu();
  closeFileImagePreview();
  closeOtherSessionPopovers(null);
  const terminalReference = reference || terminalReferenceAtClientPoint(term, container, x, y);
  const fileTarget = terminalReference?.type === 'file' ? await terminalFileReferenceTarget(session, terminalReference, {reportRejection: true}) : null;
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu';
  menu.setAttribute('role', 'menu');
  // N7: prefer the selection captured at right-click time over a live re-read (which can be empty by now).
  // Claude and other TUIs may own the visible selection and only expose it through OSC 52, so fall back
  // to the recent app clipboard payload instead of re-reading a tiny under-cursor browser fragment.
  const selection = terminalContextMenuSelection(session, term, container, presetSelection);
  const selected = selection.text;
  copyDebug('contextmenu', {session, selectionSource: selection.source, chars: selected.length});
  const hasUrlReference = terminalReference?.type === 'url';
  if (hasUrlReference) {
    if (appendTerminalReferenceContextMenuItems(menu, terminalReference, fileTarget, {selectionText: selected, session, term, container})) {
      appendContextMenuSeparator(menu);
    }
  } else {
    const items = [
      [TERMINAL_COPY_ACTIONS.selected, false],
      [TERMINAL_COPY_ACTIONS.selectedDedent, true],
    ];
    for (const [action, dedent] of items) {
      appendContextMenuButton(menu, terminalCopyActionLabel(action), button => copyTerminalSelection(session, term, {action, button, dedent, selectionText: selected}, container), closeTerminalContextMenu, {disabled: !selected});
    }
    appendContextMenuSeparator(menu);
    if (appendTerminalReferenceContextMenuItems(menu, terminalReference, fileTarget, {selectionText: selected, session, term, container})) appendContextMenuSeparator(menu);
  }
  appendContextMenuButton(menu, terminalCopyActionLabel(TERMINAL_COPY_ACTIONS.tmux), button => copyTmuxSelectionToClipboard(session, term, container, {button}), closeTerminalContextMenu);
  if (hasUrlReference) {
    appendContextMenuButton(menu, terminalCopyActionLabel(TERMINAL_COPY_ACTIONS.selectedDedent), button => copyTerminalSelection(session, term, {action: TERMINAL_COPY_ACTIONS.selectedDedent, button, dedent: true, selectionText: selected}, container), closeTerminalContextMenu, {disabled: !selected});
  }
  // Long-press starts this probe while Safari still grants user activation.  Do not offer a
  // dead Paste action when that probe found no text/image, and reuse the one paste transport.
  if (!readOnlyMode && typeof terminalClipboardPasteAvailable === 'function' && terminalClipboardPasteAvailable()) {
    appendContextMenuButton(menu, t('common.paste'), () => pasteTerminalMobileAccessoryClipboard(session), closeTerminalContextMenu);
  }
  terminalContextMenu.open(menu, x, y);
}

function installTerminalContextMenu(session, term, container) {
  // N7: right-click must NOT clear the highlight. xterm clears its selection on mousedown, so capture the
  // selected text on the right-mousedown (capture phase, before xterm's handler) and stopPropagation so
  // xterm never processes that mousedown — the highlight stays visible AND the menu has the text even if
  // focus moves to the menu. No preventDefault, so the contextmenu event still fires normally.
  let rightClickSelection = null;
  container.addEventListener('pointerdown', event => {
    if (event.pointerType === 'touch') void primeTerminalClipboardAvailability();
  }, {capture: true, passive: true});
  container.addEventListener('mousedown', event => {
    if (event.button !== 2) return;
    rightClickSelection = terminalSelectedText(term, container);
    event.stopPropagation();
  }, {capture: true});
  container.addEventListener('contextmenu', event => {
    event.preventDefault();
    event.stopPropagation();
    const touchSelection = touchContextMenuSyntheticEvents.has(event)
      ? terminalTouchWordSelectionAtClientPoint(term, container, event.clientX, event.clientY)
      : null;
    // The touch-scroll owner observes this same bridged contextmenu after us, so its existing
    // state machine can claim the press and extend this selection without another gesture owner.
    if (touchSelection) event.yolomuxTerminalTouchSelection = touchSelection;
    showTerminalContextMenu(session, term, event.clientX, event.clientY, {container, presetSelection: touchSelection?.text || rightClickSelection});
    rightClickSelection = null;
  });
}

function openTabDescriptionPopover(item, anchor) {
  if (!anchor?.isConnected) return false;
  const popover = typeof paneTabPopoverForAnchor === 'function' ? paneTabPopoverForAnchor(anchor) : null;
  if (!popover) return false;
  closeSessionContextMenu();
  closeOtherSessionPopovers(anchor, {force: true});
  if (typeof positionPaneTabPopover === 'function') positionPaneTabPopover(anchor, popover);
  anchor.classList.add('popover-open');
  popover.classList.add('popover-open');
  if (typeof maybeLoadFileTabForPopover === 'function') maybeLoadFileTabForPopover(anchor, item);
  return true;
}

function showTabContextMenu(item, x, y, options = {}) {
  if (!isLayoutItem(item)) return;
  closeAppMenus();
  closeTerminalContextMenu();
  closeFileContextMenu();
  closeOtherSessionPopovers(null);
  const menu = document.createElement('div');
  menu.className = `terminal-context-menu session-context-menu${options.presentation === 'sheet' ? ' tab-action-sheet' : ''}`;
  menu.setAttribute('role', 'menu');
  const refreshPosition = () => positionContextMenu(menu, x, y);
  const appendDescription = () => {
    const info = transcriptMetadataState.payload.sessions?.[item];
    const tab = itemLabel(item);
    const rawDescription = isTmuxSession(item)
      ? sessionTabDescription(item, info)
      : tabMenuDetailText(item, info);
    const description = rawDescription && rawDescription !== tab ? rawDescription : t('common.notAvailable');
    const text = t('tab.actions.moreDescription', {tab, description});
    const line = makeButton({
      className: 'tab-action-description',
      label: text,
      ariaLabel: text,
      title: t('common.details'),
      onClick: event => {
        event.preventDefault();
        event.stopPropagation();
        openTabDescriptionPopover(item, options.tab);
      },
    });
    menu.appendChild(line);
  };
  const renderActions = () => {
    menu.replaceChildren();
    const sourceSlot = options.sourceSlot || slotForItem(item);
    if (!slotIsSidePane(sourceSlot)) appendDescription();
    appendTabSplitCommands(menu, item, options);
    if (tabWorkspaceIsFilled(item) || tabCanFillWorkspace(item)) {
      appendContextMenuButton(
        menu,
        tabWorkspaceIsFilled(item) ? t('layout.status.restored', {item: itemLabel(item)}) : t('pane.expand'),
        () => { toggleTabWorkspaceFill(item); },
        closeSessionContextMenu,
      );
    }
    appendContextMenuSeparator(menu);
    appendTabActionCommands(menu, item, options);
    refreshPosition();
  };
  renderActions();
  sessionContextMenu.open(menu, x, y);
}

function tabDirectionalActionIconHtml(zone) {
  return `<span class="tab-directional-action-icon tab-directional-action-icon--${zone}" aria-hidden="true"></span>`;
}

function tabDirectionalMoveActionLabel(capabilities, sourceSlot, zone) {
  // `moveLayoutItemDirectional` uses this same target map: a neighboring target receives a Move,
  // while no generic target creates a local split. Side-pane horizontal actions transfer to the
  // opposite edge. Keep the visible and accessible verb aligned with that one action path.
  const sourceRole = paneRoleForSlot(sourceSlot);
  const transfersSideEdge = sourceRole.kind === paneRoleSide && (zone === 'left' || zone === 'right');
  const verb = capabilities.targets[zone] || transfersSideEdge ? t('tab.actions.move') : t('menu.view.layout.split');
  return `${verb} ${t(`layout.zone.${zone}`)}`;
}

function appendTabSplitCommands(menu, item, options = {}) {
  const sourceSlot = options.sourceSlot || slotForItem(item);
  const capabilities = tabDirectionalActionCapabilities(item, sourceSlot);
  const zones = ['left', 'right', 'top', 'bottom'];
  const sourceRect = sourceSlot ? layoutSlotScreenRect(sourceSlot) : null;
  const canPresent = Boolean(
    sourceSlot
      && sourceRect
      && !narrowSingleColumnMode()
      && (!isFileExplorerItem(activeItemForSide(sourceSlot)) || slotIsSidePane(sourceSlot)),
  );
  if (!canPresent) return;
  const actionGroups = document.createElement('div');
  actionGroups.className = 'tab-directional-action-groups';
  const appendDirectionalActions = (kind, label, action) => {
    const group = document.createElement('section');
    group.className = `tab-split-actions tab-${kind}-actions`;
    group.dataset.tabActionKind = kind;
    const title = document.createElement('div');
    title.className = 'tab-directional-actions-title';
    title.textContent = label;
    group.appendChild(title);
    for (const zone of zones) {
      const directionLabel = kind === 'move'
        ? tabDirectionalMoveActionLabel(capabilities, sourceSlot, zone)
        : `${label} ${t(`layout.zone.${zone}`)}`;
      const button = appendContextMenuButton(
        group,
        directionLabel,
        () => { void action(zone); },
        closeSessionContextMenu,
        {
          disabled: capabilities[kind][zone] !== true,
          className: `tab-split-action tab-${kind}-action`,
          iconHtml: tabDirectionalActionIconHtml(zone),
          ariaLabel: directionLabel,
          title: directionLabel,
        },
      );
      button.dataset.direction = zone;
    }
    actionGroups.appendChild(group);
  };
  appendDirectionalActions('move', t('tab.actions.move'), zone => moveLayoutItemDirectional(item, sourceSlot, zone));
  appendDirectionalActions('swap', t('layout.drop.swap'), zone => swapLayoutItemDirectional(item, sourceSlot, zone));
  menu.appendChild(actionGroups);
}

function appendTabActionCommands(menu, item, options = {}) {
  if (isPinnableTab(item)) {
    const pinned = tabIsPinned(item);
    appendContextMenuButton(
      menu,
      pinned ? t('tab.unpin') : t('tab.pin'),
      () => setTabPinned(item, !pinned),
      closeSessionContextMenu,
      {
        checked: pinned,
        iconHtml: appMenuUiIcon('pin', pinned),
        shortcut: `${appShortcutText('K', {shift: true})} Enter`,
      },
    );
    if (typeof paneCanPopout === 'function' && paneCanPopout(item)) {
      appendContextMenuButton(menu, t('tab.popout'), () => openPanePopout(item), closeSessionContextMenu);
    }
  }
  if (isTmuxSession(item)) {
    if (isPinnableTab(item)) appendContextMenuSeparator(menu);
    const renameAction = options.tab ? () => beginPaneTabRename(options.tab, item) : () => renameTmuxSession(item);
    for (const command of tmuxSessionActionCommands(item, {renameAction, includeKill: false})) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {disabled: command.disabled, checked: command.checked});
    }
    const paneInfoBarLabel = t('menu.tmux.paneDetails');
    const viewItems = tmuxSessionViewCommands(item, {includeStatus: false}).filter(command => command.label !== paneInfoBarLabel);
    for (const command of viewItems) {
      appendContextMenuButton(menu, command.label, command.action, closeSessionContextMenu, {
        disabled: command.disabled,
        checked: command.checked,
        title: command.detail || '',
      });
    }
    appendContextMenuSeparator(menu);
    const killItem = tmuxSessionKillCommand(item);
    appendContextMenuButton(menu, killItem.label, killItem.action, closeSessionContextMenu, {disabled: killItem.disabled, className: 'danger'});
  }
}

function showSessionContextMenu(session, x, y, options = {}) {
  showTabContextMenu(session, x, y, options);
}
