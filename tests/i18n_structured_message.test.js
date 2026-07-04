const assert = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');

const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const panelSource = fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8');
const terminalSource = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
const i18nSource = fs.readFileSync('static_src/js/yolomux/05_i18n.js', 'utf8');
const shareSource = fs.readFileSync('static_src/js/yolomux/98_share_admin.js', 'utf8');
const fileSource = fs.readFileSync('static_src/js/yolomux/45_file_explorer_actions.js', 'utf8');
const editorSource = fs.readFileSync('static_src/js/yolomux/95_codemirror_editor.js', 'utf8');
const coreStart = coreSource.indexOf('function messageDescriptorText(');
const coreEnd = coreSource.indexOf('function clientPushCanSupplyData(', coreStart);
const eventStart = terminalSource.indexOf('function eventItemHtml(');
const eventEnd = terminalSource.indexOf('function formatEventTime(', eventStart);
const ownerStart = terminalSource.indexOf('function autoApproveOwnerLabel(');
const ownerEnd = terminalSource.indexOf('function renderAutoApproveButtons(', ownerStart);
assert.ok(coreStart >= 0 && coreEnd > coreStart && eventStart >= 0 && eventEnd > eventStart && ownerStart >= 0 && ownerEnd > ownerStart);

const catalog = {
  'events.message.test': 'localized event {count}',
  'share.error.test': 'localized share {reason}',
  'yolo.status.test': 'localized YOLO {state}',
};
const context = {
  i18nResolve: key => catalog[key] ?? null,
  i18nInterpolate: (text, params) => String(text).replace(/\{(\w+)\}/g, (match, name) => Object.prototype.hasOwnProperty.call(params || {}, name) ? String(params[name]) : match),
  esc: value => String(value),
  formatEventTime: value => String(value),
  t: key => key === 'common.eventLabel' ? 'event' : key === 'yolo.ownerFallback' ? 'another YOLOmux' : key,
};
vm.runInNewContext(
  `${coreSource.slice(coreStart, coreEnd)}\n${terminalSource.slice(eventStart, eventEnd)}\n${terminalSource.slice(ownerStart, ownerEnd)}\nthis.api = {eventItemHtml, structuredMessageText, structuredMessageSnapshot, userMessageSnapshot, userMessageText, autoApproveOwnerLabel};`,
  context,
);
const api = context.api;

const eventHtml = api.eventItemHtml({
  time: '2026-01-01T00:00:00Z',
  type: 'test',
  message: 'raw event fallback',
  message_key: 'events.message.test',
  message_params: {count: 3},
  details: {session: '1'},
});
assert.ok(eventHtml.includes('localized event 3'), 'Event Log renders the active-locale message');
assert.equal(eventHtml.includes('raw event fallback'), false, 'Event Log does not leak the raw fallback when its key resolves');
assert.ok(eventHtml.includes('session=1'), 'Event Log keeps diagnostic details beside the localized message');

const yoloState = {
  last_action: 'raw YOLO fallback',
  last_action_key: 'yolo.status.test',
  last_action_params: {state: 'ready'},
};
assert.equal(api.structuredMessageText(yoloState, 'last_action'), 'localized YOLO ready');
assert.equal(api.autoApproveOwnerLabel(yoloState), 'localized YOLO ready');
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(api.structuredMessageSnapshot(yoloState, 'last_action'))),
  yoloState,
  'YO!share preserves the structured YOLO status and its raw classifier fallback',
);
assert.equal(
  api.userMessageText(api.userMessageSnapshot({
    payload: {
      error: 'raw share diagnostic',
      user_message: {key: 'share.error.test', params: {reason: 'failure'}, fallback: 'fallback'},
    },
  })),
  'localized share failure',
  'structured share errors resolve from the current catalog instead of storing rendered prose',
);
assert.match(
  shareSource,
  /shareCreateErrorPayload = userMessageSnapshot\(err\)/,
  'the share modal stores its structured failure payload',
);
assert.doesNotMatch(
  shareSource,
  /querySelector\('\.share-error:not\(\[hidden\]\)'\)\?\.textContent/,
  'locale changes do not carry already-rendered share error text into the next render',
);

assert.deepStrictEqual(
  JSON.parse(JSON.stringify(api.userMessageSnapshot({
    payload: {
      error: 'raw diagnostic',
      user_message: {key: 'status.sessionEnded', params: {session: '8'}, fallback: 'fallback'},
    },
  }))),
  {
    error: 'raw diagnostic',
    user_message: {key: 'status.sessionEnded', params: {session: '8'}, fallback: 'fallback'},
  },
);
assert.equal(
  api.structuredMessageText({message: 'legacy raw event'}, 'message'),
  'legacy raw event',
  'legacy payloads without descriptors remain readable',
);
assert.match(
  i18nSource,
  /const localeGlobalSurfaceHooks[\s\S]*refreshOpenEventLogs\(\)/,
  'runtime locale changes refresh an already-open Event Log through the shared global-surface registry',
);

Object.assign(catalog, {
  'common.requestFailed': 'localized request failure',
  'common.unknown': 'localized unknown',
  'events.message.test': 'localized event {count}',
  'searchHistory.error.discovery': 'localized discovery {error}',
  'searchHistory.runState.waiting': 'localized waiting',
  'searchHistory.source.event': 'localized event source',
  'searchHistory.source.sessionSummary': 'localized session summary source',
  'state.done': 'localized done',
  'transcript.contextLoadFailed': 'localized context failure: {error}',
  'transcript.error.unavailable': 'localized transcript failure: {error}',
  'transcript.itemHeader': '{role} localized ({meta})',
  'transcript.role.assistant': 'localized assistant',
});
const searchStart = panelSource.indexOf('function runHistoryStateLabel(');
const searchEnd = panelSource.indexOf('function runHistoryMetaParts(', searchStart);
const transcriptErrorStart = terminalSource.indexOf('function transcriptMetadataLoadErrorText(');
const transcriptErrorEnd = terminalSource.indexOf('function clearTranscriptContextLoadError(', transcriptErrorStart);
const transcriptItemStart = terminalSource.indexOf('function transcriptItemHtml(');
const transcriptItemEnd = terminalSource.indexOf('function eventItemHtml(', transcriptItemStart);
assert.ok(searchStart >= 0 && searchEnd > searchStart && transcriptErrorStart >= 0 && transcriptErrorEnd > transcriptErrorStart && transcriptItemStart >= 0 && transcriptItemEnd > transcriptItemStart);

const i16Context = {
  structuredMessageText: api.structuredMessageText,
  userMessageText: api.userMessageText,
  transcriptMetadataState: {
    error: {
      error: 'raw metadata diagnostic',
      user_message: {key: 'transcript.error.unavailable', params: {error: 'metadata missing'}, fallback: 'raw metadata diagnostic'},
    },
  },
  stateDefs: {done: {}},
  stateDef: () => ({label: catalog['state.done']}),
  normalizeRole: role => String(role || '').toLowerCase(),
  esc: value => String(value),
  t: (key, params = {}) => {
    const template = catalog[key] ?? key;
    return String(template).replace(/\{(\w+)\}/g, (match, name) => Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
  },
};
vm.runInNewContext(
  `${panelSource.slice(searchStart, searchEnd)}\n${terminalSource.slice(transcriptErrorStart, transcriptErrorEnd)}\n${terminalSource.slice(transcriptItemStart, transcriptItemEnd)}\nthis.i16 = {runHistoryStateLabel, searchHistorySourceLabel, searchHistoryErrorText, transcriptMetadataLoadErrorText, transcriptAgentErrorText, transcriptContextLoadErrorText, transcriptItemHtml};`,
  i16Context,
);
const i16 = i16Context.i16;
assert.equal(i16.runHistoryStateLabel('done'), 'localized done');
assert.equal(i16.runHistoryStateLabel('waiting'), 'localized waiting');
assert.equal(i16.searchHistorySourceLabel({source: 'event'}), 'localized event source');
assert.equal(i16.searchHistorySourceLabel({kind: 'summary'}), 'localized session summary source');
assert.equal(i16.searchHistoryErrorText({
  message: 'raw discovery diagnostic',
  message_key: 'searchHistory.error.discovery',
  message_params: {error: 'tmux down'},
}), 'localized discovery tmux down');
assert.equal(i16.transcriptAgentErrorText({
  error: 'raw transcript diagnostic',
  error_key: 'transcript.error.unavailable',
  error_params: {error: 'file missing'},
}), 'localized transcript failure: file missing');
assert.equal(i16.transcriptContextLoadErrorText({
  error: 'raw request diagnostic',
  user_message: {key: 'transcript.error.unavailable', params: {error: 'stream missing'}, fallback: 'raw request diagnostic'},
}), 'localized context failure: localized transcript failure: stream missing');
assert.equal(i16.transcriptMetadataLoadErrorText(), 'localized transcript failure: metadata missing');
const transcriptHtml = i16.transcriptItemHtml({
  role: 'assistant',
  header: 'assistant (raw English)',
  timestamp: '2026-07-02T01:02:03Z',
  cwd: '/repo/demo',
  text: 'content stays verbatim',
});
assert.ok(transcriptHtml.includes('localized assistant localized (2026-07-02T01:02:03Z, /repo/demo)'));
assert.ok(transcriptHtml.includes('content stays verbatim'));
assert.equal(transcriptHtml.includes('assistant (raw English)'), false);

catalog['transcript.error.unavailable'] = 'second-locale transcript: {error}';
catalog['events.message.test'] = 'second-locale event {count}';
assert.equal(i16.transcriptAgentErrorText({
  error: 'raw',
  error_key: 'transcript.error.unavailable',
  error_params: {error: 'gone'},
}), 'second-locale transcript: gone', 'cached transcript descriptors resolve again after a locale switch');
assert.equal(api.structuredMessageText({
  message: 'raw',
  message_key: 'events.message.test',
  message_params: {count: 2},
}, 'message'), 'second-locale event 2', 'cached Search & Runs descriptors resolve again after a locale switch');

const persistedSearchEvent = {
  title: 'YO!agent job failed: target session s9 is missing',
  title_key: 'yoagent.job.notification.failed',
  title_params: {
    id: 'yj_9',
    reason: {
      key: 'yoagent.error.targetSessionMissing',
      params: {session: 's9'},
      fallback: 'target session s9 is missing',
    },
  },
  details: {diagnostic: 'tmux target s9 disappeared'},
};
Object.assign(catalog, {
  'yoagent.job.notification.failed': 'first-locale job {id} failed: {reason}',
  'yoagent.error.targetSessionMissing': 'first-locale missing {session}',
});
assert.equal(api.structuredMessageText(persistedSearchEvent, 'title'), 'first-locale job yj_9 failed: first-locale missing s9');
Object.assign(catalog, {
  'yoagent.job.notification.failed': 'second-locale job {id} failed: {reason}',
  'yoagent.error.targetSessionMissing': 'second-locale missing {session}',
});
assert.equal(
  api.structuredMessageText(persistedSearchEvent, 'title'),
  'second-locale job yj_9 failed: second-locale missing s9',
  'one persisted Search & Runs descriptor re-resolves in a second catalog without refetching',
);
assert.equal(persistedSearchEvent.details.diagnostic, 'tmux target s9 disappeared', 'locale changes retain raw diagnostics in event details');
assert.equal(persistedSearchEvent.title, 'YO!agent job failed: target session s9 is missing', 'locale changes retain the raw persisted title fallback');

Object.assign(catalog, {
  'brand.tab.agent': 'first-locale agent',
  'yoagent.job.notification.idle': 'first-locale idle: {session}',
  'dialog.missingOnDisk': 'first-locale missing',
  'editor.fileLoadFailed': 'first-locale load failed',
  'fs.error.notDirectory': 'first-locale not a directory: {path}',
  'transcript.lookupFailed': 'first-locale transcript lookup failed',
});
const fileStateStart = fileSource.indexOf('function fileErrorMessageSnapshot(');
const fileStateEnd = fileSource.indexOf('function openFileIsMissing(', fileStateStart);
const notificationStart = terminalSource.indexOf('function yoagentJobNotificationTitle(');
const notificationEnd = terminalSource.indexOf('function tmuxSignalsPayloadWithWindowOverrides(', notificationStart);
assert.ok(fileStateStart >= 0 && fileStateEnd > fileStateStart && notificationStart >= 0 && notificationEnd > notificationStart);
const eagerStateContext = {
  MAX_FILE_PREVIEW_BYTES: 1024,
  fileEntryMtime: () => 0,
  previewMimeForPath: () => '',
  previewRendererForMime: () => null,
  previewRendererForPath: () => null,
  userMessageSnapshot: api.userMessageSnapshot,
  userMessageText: api.userMessageText,
  structuredMessageText: api.structuredMessageText,
  t: i16Context.t,
};
vm.runInNewContext(
  `${fileSource.slice(fileStateStart, fileStateEnd)}\n${terminalSource.slice(notificationStart, notificationEnd)}\nthis.eagerState = {fileErrorState, missingFileState, fileErrorText, yoagentJobNotificationTitle, yoagentJobNotificationBody};`,
  eagerStateContext,
);
const eagerState = eagerStateContext.eagerState;
const fileLoadState = eagerState.fileErrorState();
const missingState = eagerState.missingFileState();
const serverState = eagerState.fileErrorState({
  payload: {
    error: 'raw English diagnostic',
    user_message: {key: 'fs.error.notDirectory', params: {path: '/tmp/demo'}, fallback: 'raw English diagnostic'},
  },
});
const transcriptFallbackState = api.userMessageSnapshot(
  {message: 'raw browser network error'},
  {key: 'transcript.lookupFailed', params: {}, fallback: ''},
);
const localizedCachedState = () => ({
  fileLoad: eagerState.fileErrorText(fileLoadState.error, 'editor.fileLoadFailed'),
  missing: eagerState.fileErrorText(missingState.error, 'editor.fileLoadFailed'),
  server: eagerState.fileErrorText(serverState.error, 'editor.fileLoadFailed'),
  transcript: api.userMessageText(transcriptFallbackState),
  notification: eagerState.yoagentJobNotificationTitle({}),
  notificationBody: eagerState.yoagentJobNotificationBody({
    body: 'raw English fallback',
    body_key: 'yoagent.job.notification.idle',
    body_params: {session: '6'},
  }),
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(localizedCachedState())), {
  fileLoad: 'first-locale load failed',
  missing: 'first-locale missing',
  server: 'first-locale not a directory: /tmp/demo',
  transcript: 'first-locale transcript lookup failed',
  notification: 'first-locale agent',
  notificationBody: 'first-locale idle: 6',
});
Object.assign(catalog, {
  'brand.tab.agent': 'second-locale agent',
  'yoagent.job.notification.idle': 'second-locale idle: {session}',
  'dialog.missingOnDisk': 'second-locale missing',
  'editor.fileLoadFailed': 'second-locale load failed',
  'fs.error.notDirectory': 'second-locale not a directory: {path}',
  'transcript.lookupFailed': 'second-locale transcript lookup failed',
});
assert.deepStrictEqual(JSON.parse(JSON.stringify(localizedCachedState())), {
  fileLoad: 'second-locale load failed',
  missing: 'second-locale missing',
  server: 'second-locale not a directory: /tmp/demo',
  transcript: 'second-locale transcript lookup failed',
  notification: 'second-locale agent',
  notificationBody: 'second-locale idle: 6',
}, 'cached file errors and the notification brand resolve again after a locale switch');
assert.match(editorSource, /function renderErrorEditor[\s\S]*fileErrorText\(state\.error/);
assert.doesNotMatch(terminalSource, /userMessageSnapshot\(error,\s*t\(/);
assert.doesNotMatch(terminalSource, /notification\.title \|\| 'YO!agent'/);
assert.match(panelSource, /function searchHistoryPanelErrors\(\)[\s\S]*runHistoryState.payload\?\.errors[\s\S]*searchHistoryErrorText/);
assert.match(terminalSource, /function refreshActivePanelHeaders\(\)[\s\S]*relocalizeTranscriptPanelStatus\(session\)/);
assert.doesNotMatch(terminalSource, /transcriptMetadataState\.error = String\(error\)/);

console.log('structured message suite: 1 passed, 0 failed');
