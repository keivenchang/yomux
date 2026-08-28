const commandContractClasses = new Set(['background', 'optimistic', 'pending']);
const commandMutatingMethods = new Set(['DELETE', 'PATCH', 'POST', 'PUT']);
const commandRequestAuthorization = Symbol('yolomux-command-route');
const commandSourceInFlight = new WeakMap();
const commandDetachedInFlight = new Map();
let commandStatusSequence = 0;

function commandRoute(specification = {}) {
  const id = String(specification.id || '').trim();
  const method = String(specification.method || 'POST').trim().toUpperCase();
  const path = String(specification.path || '').trim();
  const contractClass = String(specification.contractClass || 'pending').trim();
  if (!id) throw new TypeError('command route id is required');
  if (!commandMutatingMethods.has(method)) throw new TypeError(`command route ${id} has non-mutating method ${method}`);
  if (!path.startsWith('/')) throw new TypeError(`command route ${id} requires an absolute application path`);
  if (!commandContractClasses.has(contractClass)) throw new TypeError(`command route ${id} has unknown contract class ${contractClass}`);
  return Object.freeze({
    ...specification,
    id,
    method,
    path,
    contractClass,
    pendingLabel: String(specification.pendingLabel || 'Working...'),
    overdueLabel: String(specification.overdueLabel || 'Still working...'),
    overdueMs: Math.max(0, Number(specification.overdueMs ?? 250) || 0),
  });
}

const COMMAND_ROUTES = Object.freeze({
  'tmux-status-cycle': commandRoute({id: 'tmux-status-cycle', method: 'POST', path: '/api/tmux-status', contractClass: 'pending'}),
  'terminal-upload': commandRoute({id: 'terminal-upload', method: 'POST', path: '/api/upload', contractClass: 'pending'}),
  'editor-upload': commandRoute({id: 'editor-upload', method: 'POST', path: '/api/upload', contractClass: 'pending'}),
  'tmux-window-select': commandRoute({id: 'tmux-window-select', method: 'POST', path: '/api/tmux-window', contractClass: 'optimistic'}),
  'auto-approve-toggle': commandRoute({id: 'auto-approve-toggle', method: 'POST', path: '/api/auto-approve', contractClass: 'optimistic'}),
  'event-log-post': commandRoute({id: 'event-log-post', method: 'POST', path: '/api/event', contractClass: 'background'}),
  'self-update': commandRoute({id: 'self-update', method: 'POST', path: '/api/self-update', contractClass: 'pending'}),
  'js-debug-observation-flush': commandRoute({id: 'js-debug-observation-flush', method: 'POST', path: '/api/stats-observations', contractClass: 'background'}),
  'pricing-catalog-refresh': commandRoute({id: 'pricing-catalog-refresh', method: 'POST', path: '/api/pricing-catalog/refresh', contractClass: 'pending'}),
  'debug-service-control': commandRoute({id: 'debug-service-control', method: 'POST', path: '/api/runtime/service-control', contractClass: 'pending'}),
  'yolo-rule-open': commandRoute({id: 'yolo-rule-open', method: 'POST', path: '/api/yolo-rules/open', contractClass: 'pending'}),
  'yolo-rule-reload': commandRoute({id: 'yolo-rule-reload', method: 'POST', path: '/api/yolo-rules/reload', contractClass: 'pending'}),
  'ensure-session': commandRoute({id: 'ensure-session', method: 'POST', path: '/api/ensure-session', contractClass: 'pending'}),
  'create-session': commandRoute({id: 'create-session', method: 'POST', path: '/api/create-session', contractClass: 'pending'}),
  'rename-session': commandRoute({id: 'rename-session', method: 'POST', path: '/api/rename-session', contractClass: 'optimistic'}),
  'kill-session': commandRoute({id: 'kill-session', method: 'POST', path: '/api/kill-session', contractClass: 'pending'}),
  'settings-save': commandRoute({id: 'settings-save', method: 'POST', path: '/api/settings', contractClass: 'pending'}),
  'yoagent-cancel': commandRoute({id: 'yoagent-cancel', method: 'POST', path: '/api/yoagent/chat/*/cancel', contractClass: 'pending'}),
  'yoagent-reset': commandRoute({id: 'yoagent-reset', method: 'POST', path: '/api/yoagent/reset', contractClass: 'pending'}),
  'yoagent-job-update': commandRoute({id: 'yoagent-job-update', method: 'POST', path: '/api/yoagent/jobs/*/*', contractClass: 'optimistic'}),
  'yoagent-wait-clear': commandRoute({id: 'yoagent-wait-clear', method: 'POST', path: '/api/yoagent/waits/*/clear', contractClass: 'optimistic'}),
  'yoagent-chat-start': commandRoute({id: 'yoagent-chat-start', method: 'POST', path: '/api/yoagent/chat', contractClass: 'optimistic'}),
  'yoagent-action-send': commandRoute({id: 'yoagent-action-send', method: 'POST', path: '/api/yoagent/actions/execute-send', contractClass: 'optimistic'}),
  'yoagent-prewarm': commandRoute({id: 'yoagent-prewarm', method: 'POST', path: '/api/yoagent/prewarm', contractClass: 'background'}),
  'fs-batch-repair': commandRoute({id: 'fs-batch-repair', method: 'POST', path: '/api/fs/batch', contractClass: 'background'}),
  'fs-batch-flush': commandRoute({id: 'fs-batch-flush', method: 'POST', path: '/api/fs/batch', contractClass: 'background'}),
  'finder-unindex': commandRoute({id: 'finder-unindex', method: 'POST', path: '/api/fs/unindex', contractClass: 'pending'}),
  'settings-unindex': commandRoute({id: 'settings-unindex', method: 'POST', path: '/api/fs/unindex', contractClass: 'background'}),
  'recovery-preflight': commandRoute({id: 'recovery-preflight', method: 'POST', path: '/api/recovery/preflight', contractClass: 'pending'}),
  'recovery-preflight-confirm': commandRoute({id: 'recovery-preflight-confirm', method: 'POST', path: '/api/recovery/preflight', contractClass: 'pending'}),
  'recovery-attach-existing': commandRoute({id: 'recovery-attach-existing', method: 'POST', path: '/api/recovery/attach-existing', contractClass: 'pending'}),
  'recovery-repair-pane': commandRoute({id: 'recovery-repair-pane', method: 'POST', path: '/api/recovery/repair-pane', contractClass: 'pending'}),
  'recovery-recover': commandRoute({id: 'recovery-recover', method: 'POST', path: '/api/recovery/recover', contractClass: 'pending'}),
  'recovery-dismiss': commandRoute({id: 'recovery-dismiss', method: 'POST', path: '/api/recovery/dismiss', contractClass: 'optimistic'}),
  'recovery-recover-all-start': commandRoute({id: 'recovery-recover-all-start', method: 'POST', path: '/api/recovery/recover-all', contractClass: 'pending'}),
  'recovery-recover-all-next': commandRoute({id: 'recovery-recover-all-next', method: 'POST', path: '/api/recovery/recover-all', contractClass: 'pending'}),
  'recovery-recover-all-action': commandRoute({id: 'recovery-recover-all-action', method: 'POST', path: '/api/recovery/recover-all', contractClass: 'pending'}),
  'tmux-copy-selection': commandRoute({id: 'tmux-copy-selection', method: 'POST', path: '/api/tmux-copy-selection', contractClass: 'pending'}),
  'recovery-adopt': commandRoute({id: 'recovery-adopt', method: 'POST', path: '/api/recovery/adopt', contractClass: 'pending'}),
  'chat-api-post': commandRoute({id: 'chat-api-post', method: 'POST', path: '/api/chat/*', contractClass: 'optimistic'}),
  'drop-action-run': commandRoute({id: 'drop-action-run', method: 'POST', path: '/api/drop-action/run', contractClass: 'pending'}),
  'attention-ack': commandRoute({id: 'attention-ack', method: 'POST', path: '/api/attention-ack', contractClass: 'background'}),
  'editor-save': commandRoute({id: 'editor-save', method: 'POST', path: '/api/fs/write', contractClass: 'pending'}),
  'stats-read-fence-retry': commandRoute({id: 'stats-read-fence-retry', method: 'POST', path: '/api/stats-retry', contractClass: 'background'}),
  'stats-manual-retry': commandRoute({id: 'stats-manual-retry', method: 'POST', path: '/api/stats-retry', contractClass: 'pending'}),
  'finder-file-create': commandRoute({id: 'finder-file-create', method: 'POST', path: '/api/fs/write', contractClass: 'pending'}),
  'finder-folder-create': commandRoute({id: 'finder-folder-create', method: 'POST', path: '/api/fs/mkdir', contractClass: 'pending'}),
  'finder-delete': commandRoute({id: 'finder-delete', method: 'POST', path: '/api/fs/delete', contractClass: 'optimistic'}),
  'finder-rename': commandRoute({id: 'finder-rename', method: 'POST', path: '/api/fs/rename', contractClass: 'optimistic'}),
  'watch-roots-sync': commandRoute({id: 'watch-roots-sync', method: 'POST', path: '/api/watch/roots', contractClass: 'background'}),
});

// Internal ownership coordination is a mutation, but it is not a user command and therefore does
// not belong in the user-command inventory asserted by K0. It remains declared here so the same
// pre-network guard covers it instead of granting a blanket exception to internal POST requests.
const INTERNAL_COMMAND_ROUTES = Object.freeze({
  'terminal-file-resolve': commandRoute({id: 'terminal-file-resolve', method: 'POST', path: '/api/fs/resolve-file-candidates', contractClass: 'background'}),
  'background-owner-claim': commandRoute({id: 'background-owner-claim', method: 'POST', path: '/api/background/claim', contractClass: 'background'}),
  'operation-terminal-ack': commandRoute({id: 'operation-terminal-ack', method: 'POST', path: '/api/operations/ack', contractClass: 'background'}),
});

function commandRequestMethod(options = {}) {
  return String(options?.method || 'GET').trim().toUpperCase();
}

function commandRequestPath(url) {
  try {
    return new URL(String(url || ''), globalThis.location?.href || 'http://localhost/').pathname;
  } catch (_) {
    return String(url || '').split(/[?#]/, 1)[0];
  }
}

function commandRoutePathMatches(pattern, path) {
  const expected = String(pattern || '').split('/');
  const actual = String(path || '').split('/');
  if (expected.length !== actual.length) return false;
  return expected.every((segment, index) => segment === '*' || segment === actual[index]);
}

function commandRouteMatchesRequest(route, url, options = {}) {
  return route?.method === commandRequestMethod(options)
    && commandRoutePathMatches(route.path, commandRequestPath(url));
}

function registeredCommandRouteForRequest(url, options = {}) {
  const authorized = options?.[commandRequestAuthorization];
  if (authorized && commandRouteMatchesRequest(authorized, url, options)) return authorized;
  return [...Object.values(COMMAND_ROUTES), ...Object.values(INTERNAL_COMMAND_ROUTES)]
    .find(route => commandRouteMatchesRequest(route, url, options)) || null;
}

function unregisteredMutatingCommandError(url, method) {
  const path = commandRequestPath(url);
  const error = new Error(`unregistered_mutating_command: ${method} ${path} has no command route`);
  error.name = 'UnregisteredMutatingCommandError';
  error.code = 'unregistered_mutating_command';
  error.reason_code = error.code;
  error.payload = {reason_code: error.code, method, path};
  return error;
}

const commandBoundaryApiFetch = apiFetch;
apiFetch = async function guardedApiFetch(url, options = {}, internalOptions = {}) {
  const method = commandRequestMethod(options);
  if (commandMutatingMethods.has(method) && !registeredCommandRouteForRequest(url, options)) {
    throw unregisteredMutatingCommandError(url, method);
  }
  const requestOptions = {...options};
  delete requestOptions[commandRequestAuthorization];
  return commandBoundaryApiFetch(url, requestOptions, internalOptions);
};

function commandFetchJson(route, url, options = {}) {
  return apiFetchJson(url, {
    ...options,
    method: route.method,
    [commandRequestAuthorization]: route,
  });
}

function commandStatusHost() {
  return globalThis.document?.body || globalThis.document?.getElementById?.('grid') || null;
}

function commandPlaceAssociatedStatus(status, control) {
  commandStatusHost()?.appendChild(status);
  const bounds = control?.getBoundingClientRect?.() || null;
  status.style.position = 'fixed';
  status.style.left = `${Math.max(8, Number(bounds?.left) || 8)}px`;
  status.style.top = `${Math.max(8, Number(bounds?.bottom) + 4 || 8)}px`;
  status.style.zIndex = '2147483647';
  status.style.maxWidth = 'min(32rem, calc(100vw - 16px))';
  status.style.padding = '2px 4px';
}

function commandPublishGlobalStatus(message, tone = 'advisory') {
  const status = globalThis.document?.getElementById?.('status') || null;
  if (!status) return;
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', tone === 'danger' ? 'assertive' : 'polite');
  status.setAttribute('aria-atomic', 'true');
  if (typeof showLayoutStatus === 'function') {
    showLayoutStatus(String(message || ''), tone);
    return;
  }
  status.textContent = String(message || '');
  status.classList?.add?.('layout-status-visible');
  status.classList?.toggle?.('layout-status-danger', tone === 'danger');
  status.classList?.toggle?.('layout-status-advisory', tone !== 'danger');
}

function commandDescriptionIds(control) {
  return String(control?.getAttribute?.('aria-describedby') || '').split(/\s+/).filter(Boolean);
}

function commandRemoveOwnedDescriptions(control) {
  if (!control) return;
  const retained = [];
  for (const id of commandDescriptionIds(control)) {
    const node = globalThis.document?.getElementById?.(id) || null;
    if (node?.dataset?.commandStatusOwned === 'true') node.remove?.();
    else retained.push(id);
  }
  if (retained.length) control.setAttribute?.('aria-describedby', retained.join(' '));
  else control.removeAttribute?.('aria-describedby');
}

function commandPendingState(route, source) {
  const control = source && typeof source === 'object' ? source : null;
  commandRemoveOwnedDescriptions(control);
  const previous = control ? {
    disabled: control.disabled === true,
    ariaBusy: control.getAttribute?.('aria-busy'),
    ariaDescribedBy: control.getAttribute?.('aria-describedby'),
  } : null;
  const status = route.contractClass === 'background'
    ? null
    : globalThis.document?.createElement?.('div') || null;
  if (status) {
    commandStatusSequence += 1;
    status.id = `command-status-${route.id}-${commandStatusSequence}`;
    status.className = 'command-pending-status';
    status.dataset.commandStatusOwned = 'true';
    status.dataset.commandStatusPhase = 'pending';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');
    status.textContent = route.pendingLabel;
    status.style.display = 'block';
    status.style.minHeight = '2px';
    status.style.minWidth = '2px';
    commandPlaceAssociatedStatus(status, control);
  }
  if (control) {
    control.disabled = true;
    control.setAttribute?.('aria-busy', 'true');
    if (status) control.setAttribute?.('aria-describedby', status.id);
  }
  if (route.contractClass !== 'background') commandPublishGlobalStatus(route.pendingLabel);
  const state = {control, previous, status, route, overdueTimer: null, settled: false};
  if (route.contractClass !== 'background' && route.overdueMs > 0) {
    state.overdueTimer = setTimeout(() => {
      if (state.settled) return;
      if (state.status) {
        state.status.dataset.commandStatusPhase = 'overdue';
        state.status.textContent = route.overdueLabel;
      }
      commandPublishGlobalStatus(route.overdueLabel);
    }, route.overdueMs);
  }
  return state;
}

function commandFailureText(error) {
  const payload = error?.payload && typeof error.payload === 'object' ? error.payload : {};
  const reason = payload.reason || payload.error || payload.message || error?.reason || error?.message;
  if (reason) return String(reason);
  if (typeof userMessageText === 'function') return userMessageText(error, 'Command failed');
  return 'Command failed';
}

function settleCommandPendingState(state, error = null) {
  state.settled = true;
  if (state.overdueTimer !== null) {
    clearTimeout(state.overdueTimer);
    state.overdueTimer = null;
  }
  if (state.control && state.previous) {
    state.control.disabled = state.previous.disabled;
    if (state.previous.ariaBusy === null) state.control.removeAttribute?.('aria-busy');
    else state.control.setAttribute?.('aria-busy', state.previous.ariaBusy);
  }
  if (error) {
    const reason = commandFailureText(error);
    if (state.status) {
      state.status.className = 'command-error-status';
      state.status.dataset.commandStatusPhase = 'error';
      state.status.setAttribute('role', 'alert');
      state.status.setAttribute('aria-live', 'assertive');
      state.status.textContent = reason;
    }
    commandPublishGlobalStatus(reason, 'danger');
    return;
  }
  if (state.control && state.previous) {
    if (state.previous.ariaDescribedBy === null) state.control.removeAttribute?.('aria-describedby');
    else state.control.setAttribute?.('aria-describedby', state.previous.ariaDescribedBy);
  }
  state.status?.remove?.();
}

function commandInFlightMap(source) {
  if (!source || (typeof source !== 'object' && typeof source !== 'function')) return commandDetachedInFlight;
  let inFlight = commandSourceInFlight.get(source);
  if (!inFlight) {
    inFlight = new Map();
    commandSourceInFlight.set(source, inFlight);
  }
  return inFlight;
}

function commandRequestOptions(route, params = {}) {
  const supplied = params?.requestOptions && typeof params.requestOptions === 'object'
    ? params.requestOptions
    : {};
  const options = {...supplied, method: route.method};
  if (
    Object.prototype.hasOwnProperty.call(params, 'payload')
    && !Object.prototype.hasOwnProperty.call(options, 'body')
    && route.method !== 'DELETE'
  ) {
    options.headers = {...(options.headers || {}), 'Content-Type': 'application/json'};
    options.body = JSON.stringify(params.payload);
  }
  return options;
}

function dispatchCommand(route, params = {}, source = null) {
  const descriptor = commandRoute(route);
  const inFlight = commandInFlightMap(source);
  const existing = inFlight.get(descriptor.id);
  if (existing) return existing;

  const pendingState = commandPendingState(descriptor, source);
  const operation = (async () => {
    let undo;
    let failure = null;
    try {
      undo = descriptor.optimistic?.(params);
      const url = String(params?.url || descriptor.path);
      const result = await commandFetchJson(descriptor, url, commandRequestOptions(descriptor, params));
      return typeof descriptor.applyResult === 'function'
        ? descriptor.applyResult(result, params)
        : result;
    } catch (error) {
      failure = error;
      if (typeof descriptor.rollback === 'function') descriptor.rollback(undo, params, error);
      throw error;
    } finally {
      settleCommandPendingState(pendingState, failure);
    }
  })();
  const tracked = operation.then(
    value => {
      if (inFlight.get(descriptor.id) === tracked) inFlight.delete(descriptor.id);
      return value;
    },
    error => {
      if (inFlight.get(descriptor.id) === tracked) inFlight.delete(descriptor.id);
      throw error;
    },
  );
  inFlight.set(descriptor.id, tracked);
  return tracked;
}
