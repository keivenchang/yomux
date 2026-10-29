const commandContractClasses = new Set(['background', 'optimistic', 'pending']);
const commandMutatingMethods = new Set(['DELETE', 'PATCH', 'POST', 'PUT']);
const commandRequestAuthorization = Symbol('yomux-command-route');
const commandSourceInFlight = new WeakMap();
const commandControlPending = new WeakMap();
const commandDetachedInFlight = new Map();
let commandStatusSequence = 0;
const commandGlobalPending = new Set();
let commandGlobalStatusPrevious = null;
let commandGlobalStatusNode = null;

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
  'event-log-batch-post': commandRoute({id: 'event-log-batch-post', method: 'POST', path: '/api/events', contractClass: 'background'}),
  'self-update': commandRoute({id: 'self-update', method: 'POST', path: '/api/self-update', contractClass: 'pending'}),
  'js-debug-observation-flush': commandRoute({id: 'js-debug-observation-flush', method: 'POST', path: '/api/stats-observations', contractClass: 'background'}),
  'pricing-catalog-refresh': commandRoute({id: 'pricing-catalog-refresh', method: 'POST', path: '/api/pricing-catalog/refresh', contractClass: 'pending'}),
  'debug-service-control': commandRoute({id: 'debug-service-control', method: 'POST', path: '/api/runtime/service-control', contractClass: 'pending'}),
  'yolo-rule-open': commandRoute({id: 'yolo-rule-open', method: 'POST', path: '/api/yolo-rules/open', contractClass: 'pending'}),
  'yolo-rule-reload': commandRoute({id: 'yolo-rule-reload', method: 'POST', path: '/api/yolo-rules/reload', contractClass: 'pending'}),
  'ensure-session': commandRoute({id: 'ensure-session', method: 'POST', path: '/api/ensure-session', contractClass: 'pending'}),
  'create-session': commandRoute({id: 'create-session', method: 'POST', path: '/api/create-session', contractClass: 'pending'}),
  'rename-session': commandRoute({id: 'rename-session', method: 'POST', path: '/api/rename-session', contractClass: 'optimistic'}),
  'tabber-description-rename': commandRoute({id: 'tabber-description-rename', method: 'POST', path: '/api/tabber-description', contractClass: 'pending'}),
  'tabber-window-view': commandRoute({id: 'tabber-window-view', method: 'POST', path: '/api/tabber-view-session', contractClass: 'pending'}),
  'tabber-activity-touch': commandRoute({id: 'tabber-activity-touch', method: 'POST', path: '/api/activity/touch', contractClass: 'background'}),
  'kill-session': commandRoute({id: 'kill-session', method: 'POST', path: '/api/kill-session', contractClass: 'pending'}),
  'kill-window': commandRoute({id: 'kill-window', method: 'POST', path: '/api/kill-window', contractClass: 'pending'}),
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
  'stats-history-refill': commandRoute({id: 'stats-history-refill', method: 'POST', path: '/api/stats-refill', contractClass: 'pending'}),
  'finder-file-create': commandRoute({id: 'finder-file-create', method: 'POST', path: '/api/fs/write', contractClass: 'pending'}),
  'finder-folder-create': commandRoute({id: 'finder-folder-create', method: 'POST', path: '/api/fs/mkdir', contractClass: 'pending'}),
  'finder-delete': commandRoute({id: 'finder-delete', method: 'POST', path: '/api/fs/delete', contractClass: 'optimistic'}),
  'finder-rename': commandRoute({id: 'finder-rename', method: 'POST', path: '/api/fs/rename', contractClass: 'optimistic'}),
  'watch-roots-sync': commandRoute({id: 'watch-roots-sync', method: 'POST', path: '/api/watch/roots', contractClass: 'background', applyResult: acceptedWatchRootsCommandResult}),
});

function acceptedWatchRootsCommandResult(result, params) {
  if (params.rawResponse === true || result?.ok === true) return result;
  const error = new Error(userMessageText(result, 'Server did not accept watch registration'));
  error.payload = result;
  throw error;
}

// Internal ownership coordination is a mutation, but it is not a user command and therefore does
// not belong in the user-command inventory asserted by K0. It remains declared here so the same
// pre-network guard covers it instead of granting a blanket exception to internal POST requests.
const INTERNAL_COMMAND_ROUTES = Object.freeze({
  'terminal-file-resolve': commandRoute({id: 'terminal-file-resolve', method: 'POST', path: '/api/fs/resolve-file-candidates', contractClass: 'background'}),
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
  if (options.commandRouteId) {
    const selected = COMMAND_ROUTES[options.commandRouteId] || INTERNAL_COMMAND_ROUTES[options.commandRouteId];
    return selected && commandRouteMatchesRequest(selected, url, options) ? selected : null;
  }
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
const commandBoundaryApiFetchJson = apiFetchJson;
apiFetch = async function guardedApiFetch(url, options = {}, internalOptions = {}) {
  const method = commandRequestMethod(options);
  if (commandMutatingMethods.has(method)) {
    const route = registeredCommandRouteForRequest(url, options);
    if (!route) throw unregisteredMutatingCommandError(url, method);
    if (!options[commandRequestAuthorization]) {
      return dispatchCommand(route, {url, requestOptions: options, internalOptions, rawResponse: true});
    }
  }
  const requestOptions = {...options};
  delete requestOptions[commandRequestAuthorization];
  delete requestOptions.commandRouteId;
  return commandBoundaryApiFetch(url, requestOptions, internalOptions);
};
apiFetchJson = async function registeredCommandFetchJson(url, options = {}, internalOptions = {}) {
  const method = commandRequestMethod(options);
  if (!commandMutatingMethods.has(method)) return commandBoundaryApiFetchJson(url, options, internalOptions);
  const route = registeredCommandRouteForRequest(url, options);
  if (!route) return Promise.reject(unregisteredMutatingCommandError(url, method));
  return dispatchCommand(route, {url, requestOptions: options, internalOptions});
};

function commandFetchJson(route, url, options = {}, internalOptions = {}, rawResponse = false) {
  const fetch = rawResponse ? apiFetch : commandBoundaryApiFetchJson;
  return fetch(url, {
    ...options,
    method: route.method,
    [commandRequestAuthorization]: route,
  }, internalOptions);
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

function commandPublishGlobalStatus(message) {
  if (typeof terminalAuthenticationActive === 'function' && terminalAuthenticationActive()) return null;
  const status = globalThis.document?.getElementById?.('status') || null;
  if (!status) return;
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  if (typeof showLayoutStatus === 'function') {
    showLayoutStatus(String(message || ''));
    return status.firstChild;
  }
  status.textContent = String(message || '');
  return status.firstChild;
}

function commandGlobalStatusSnapshot(status) {
  const attributes = status.attributes || {};
  return {
    html: status.innerHTML,
    className: status.className,
    attributes: typeof attributes.length === 'number'
      ? Array.from(attributes).map(attribute => [attribute.name, attribute.value])
      : Object.entries(attributes),
  };
}

function restoreCommandGlobalStatus(status, previous) {
  if (typeof terminalAuthenticationActive === 'function' && terminalAuthenticationActive()) return;
  for (const [name] of commandGlobalStatusSnapshot(status).attributes) status.removeAttribute(name);
  for (const [name, value] of previous.attributes) status.setAttribute(name, value);
  status.className = previous.className;
  status.innerHTML = previous.html;
}

function commandOwnsGlobalStatus() {
  return commandGlobalStatusNode !== null
    && globalThis.document?.getElementById?.('status')?.firstChild === commandGlobalStatusNode;
}

function beginCommandGlobalStatus(state) {
  if (state.route.contractClass === 'background') return;
  const status = globalThis.document?.getElementById?.('status');
  if (!status) return;
  if (!commandGlobalPending.size || !commandOwnsGlobalStatus()) {
    commandGlobalStatusPrevious = commandGlobalStatusSnapshot(status);
  }
  commandGlobalPending.add(state);
  commandGlobalStatusNode = commandPublishGlobalStatus(state.route.pendingLabel);
}

function publishCommandOverdueStatus(state) {
  state.overdue = true;
  if ([...commandGlobalPending].at(-1) !== state || !commandOwnsGlobalStatus()) return;
  commandGlobalStatusNode = commandPublishGlobalStatus(state.route.overdueLabel);
}

function settleCommandGlobalStatus(state) {
  commandGlobalPending.delete(state);
  if (commandOwnsGlobalStatus()) {
    const remaining = [...commandGlobalPending].at(-1);
    if (remaining) {
      commandGlobalStatusNode = commandPublishGlobalStatus(remaining.overdue
        ? remaining.route.overdueLabel : remaining.route.pendingLabel);
    } else if (commandGlobalStatusPrevious) {
      const status = globalThis.document.getElementById('status');
      restoreCommandGlobalStatus(status, commandGlobalStatusPrevious);
    }
  }
  if (!commandGlobalPending.size) {
    commandGlobalStatusPrevious = null;
    commandGlobalStatusNode = null;
  }
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
  const active = control ? commandControlPending.get(control) : null;
  if (!active) commandRemoveOwnedDescriptions(control);
  const previous = active?.previous || (control ? {
    disabled: control.disabled === true,
    ariaBusy: control.getAttribute?.('aria-busy'),
    ariaDescribedBy: control.getAttribute?.('aria-describedby'),
  } : null);
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
    if (status) control.setAttribute?.('aria-describedby', [...commandDescriptionIds(control), status.id].join(' '));
  }
  const state = {control, previous, status, route, overdueTimer: null, overdue: false, settled: false};
  beginCommandGlobalStatus(state);
  if (control) {
    const owner = active || {previous, states: new Set()};
    owner.states.add(state);
    commandControlPending.set(control, owner);
  }
  if (route.contractClass !== 'background' && route.overdueMs > 0) {
    state.overdueTimer = setTimeout(() => {
      if (state.settled) return;
      if (state.status) {
        state.status.dataset.commandStatusPhase = 'overdue';
        state.status.textContent = route.overdueLabel;
      }
      publishCommandOverdueStatus(state);
    }, route.overdueMs);
  }
  return state;
}

function commandFailureText(error) {
  const payload = error?.payload && typeof error.payload === 'object' ? error.payload : {};
  const reason = [payload.reason, payload.error, payload.message, error?.reason, error?.message].find(value => typeof value === 'string' && value);
  if (error?.operationLabel) return `${error.operationLabel}: ${reason || 'Command failed'}`;
  if (error?.message?.startsWith('Finder watch registration:')) return error.message;
  if (reason) return String(reason);
  if (typeof userMessageText === 'function') return userMessageText(error, 'Command failed');
  return 'Command failed';
}

function commandErrorPayload(error) {
  return error?.payload && typeof error.payload === 'object'
    ? error.payload
    : error?.result && typeof error.result === 'object' ? error.result : {};
}

function commandErrorCodes(error) {
  const payload = commandErrorPayload(error);
  const nested = payload.error && typeof payload.error === 'object' ? payload.error : {};
  return [payload.error_code, payload.reason_code, payload.code, nested.code, error?.code];
}

function commandFileChangedOnDiskError(error) {
  const payload = commandErrorPayload(error);
  const nested = payload.error && typeof payload.error === 'object' ? payload.error : {};
  return commandErrorCodes(error).includes('changed_on_disk')
    || [payload.message_key, payload.user_message?.key, nested.message_key, nested.message?.key].includes('fs.error.changedOnDisk');
}

function commandFileChangedOnDiskText(error) {
  const payload = commandErrorPayload(error);
  const nested = payload.error && typeof payload.error === 'object' ? payload.error : {};
  const path = payload.path || nested.path || error?.path || '';
  return typeof t === 'function' ? t('fs.error.changedOnDisk', {path}) : commandFailureText(error);
}

function settleCommandPendingState(state, error = null) {
  state.settled = true;
  if (state.overdueTimer !== null) {
    clearTimeout(state.overdueTimer);
    state.overdueTimer = null;
  }
  const owner = state.control ? commandControlPending.get(state.control) : null;
  owner?.states.delete(state);
  const controlSettled = !owner || owner.states.size === 0;
  if (controlSettled && state.control) commandControlPending.delete(state.control);
  if (controlSettled && state.control && state.previous) {
    state.control.disabled = state.previous.disabled;
    if (state.previous.ariaBusy === null) state.control.removeAttribute?.('aria-busy');
    else state.control.setAttribute?.('aria-busy', state.previous.ariaBusy);
  }
  if (state.control) {
    const descriptions = commandDescriptionIds(state.control).filter(id => id !== state.status?.id);
    if (descriptions.length) state.control.setAttribute?.('aria-describedby', descriptions.join(' '));
    else state.control.removeAttribute?.('aria-describedby');
  }
  state.status?.remove?.();
  settleCommandGlobalStatus(state);
  if (error && typeof showTimedStatusNotice === 'function') {
    // Every failure uses the existing timed notice; settlement must release all pending UI ownership.
    try {
      showTimedStatusNotice(commandFileChangedOnDiskError(error) ? commandFileChangedOnDiskText(error) : commandFailureText(error));
    } catch (toastError) {
      console.error('Unable to show command notice', toastError);
    }
  }
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

function commandOperationIdentity(route, params, url, options) {
  // Opaque uploads and independently cancellable requests cannot safely share transport ownership.
  if (route.contractClass === 'background' || params.rawResponse === true || options.signal
      || (options.body !== undefined && typeof options.body !== 'string')) return Symbol(route.id);
  if (typeof route.operationKey === 'function') return JSON.stringify([route.id, route.operationKey(params, url, options)]);
  return JSON.stringify([route.id, url, options, params.internalOptions || {}, params.rawResponse === true]);
}

function dispatchCommand(route, params = {}, source = null) {
  const descriptor = commandRoute(route);
  const url = String(params?.url || descriptor.path);
  const requestOptions = commandRequestOptions(descriptor, params);
  const identity = commandOperationIdentity(descriptor, params, url, requestOptions);
  const inFlight = commandInFlightMap(source);
  const existing = inFlight.get(identity);
  if (existing) return existing;

  const pendingState = commandPendingState(descriptor, source);
  const operation = (async () => {
    let undo;
    let failure = null;
    try {
      undo = descriptor.optimistic?.(params);
      const result = await commandFetchJson(descriptor, url, requestOptions, params.internalOptions, params.rawResponse === true);
      // Raw callers still inspect their HTTP response themselves, while command feedback records failure.
      if (params.rawResponse === true && !result.ok) {
        try {
          await apiJsonResponse(result.clone());
        } catch (error) {
          failure = error;
        }
        if (!failure || typeof result.clone !== 'function') failure = new Error(`HTTP ${result.status}`);
        failure.operationLabel = descriptor.id === 'watch-roots-sync' ? 'Finder watch registration' : descriptor.id;
      }
      return typeof descriptor.applyResult === 'function'
        ? await descriptor.applyResult(result, params)
        : result;
    } catch (error) {
      failure = error;
      if (descriptor.id === 'watch-roots-sync') error.message = `Finder watch registration: ${error.message}`;
      if (typeof descriptor.rollback === 'function') descriptor.rollback(undo, params, error);
      throw error;
    } finally {
      settleCommandPendingState(pendingState, failure);
    }
  })();
  const tracked = operation.then(
    value => {
      if (inFlight.get(identity) === tracked) inFlight.delete(identity);
      return value;
    },
    error => {
      if (inFlight.get(identity) === tracked) inFlight.delete(identity);
      throw error;
    },
  );
  inFlight.set(identity, tracked);
  return tracked;
}
