// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Drop-action registry and terminal drop suggestions.
// DOIT.57: one data-driven file-drop action registry. Agent panes keep the shipped "path first, then
// append a deictic clause" behavior; shell and server actions compose full commands/results from the
// selected action so they do not run a stray bare path before the useful command.
const DROP_SUGGESTION_CATEGORY_EXTS = {
  image: ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.heic', '.heif', '.avif'],
  log: ['.log', '.out', '.err'],
  diff: ['.diff', '.patch'],
  data: ['.csv', '.tsv', '.json', '.ndjson', '.yaml', '.yml', '.parquet'],
  doc: ['.md', '.markdown', '.rst', '.pdf', '.txt', '.docx', '.html'],
  config: ['.toml', '.ini', '.env', '.cfg', '.conf'],
  code: ['.py', '.js', '.ts', '.tsx', '.jsx', '.mjs', '.rs', '.go', '.java', '.c', '.h', '.cpp', '.cc', '.rb', '.php', '.lua', '.sql', '.css', '.sh', '.bash', '.zsh'],
  archive: ['.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.whl'],
};

function fileDropCategory(pathOrName, kind = 'file') {
  if (kind === 'dir') return 'dir';
  const name = String(pathOrName || '').toLowerCase();
  const base = name.slice(Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\')) + 1);
  const dot = base.lastIndexOf('.');
  const ext = dot > 0 ? base.slice(dot) : '';
  for (const category of Object.keys(DROP_SUGGESTION_CATEGORY_EXTS)) {
    if (DROP_SUGGESTION_CATEGORY_EXTS[category].includes(ext)) return category;
  }
  return 'any';
}

const DROP_ACTION_CATEGORIES = Object.freeze(['any', 'image', 'log', 'code', 'diff', 'data', 'doc', 'config', 'archive', 'dir']);
const DEFAULT_IMAGE_DROP_ACTION_ORDER = Object.freeze([
  'Extract the text (OCR): ; do OCR on this image and extract all of the text.',
  'Diagnose the error: ; diagnose the error/problem shown in this screenshot & suggest a fix.',
  'Describe the image: ; describe what is shown in this image.',
  'info',
]);
const DROP_ACTIONS = [
  {id: 'insert-path', cats: DROP_ACTION_CATEGORIES, kind: 'insert', label: 'Insert path', labelKey: 'drop.action.insertPath', readOnly: true},
  {id: 'img-error', cats: ['image'], kind: 'prompt', agent: true, label: 'Diagnose the error', labelKey: 'drop.action.imgError', prompt: () => 'diagnose the error/problem shown in this screenshot & suggest a fix.', aliases: ['Diagnose the error in this screenshot', 'diagnose the error or problem shown in this screenshot and suggest a fix.', 'Diagnose the error in this screenshot: ; diagnose the error or problem shown in this screenshot and suggest a fix.']},
  {id: 'img-describe', cats: ['image'], kind: 'prompt', agent: true, label: 'Describe the image', labelKey: 'drop.action.imgDescribe', prompt: () => 'describe what is shown in this image.'},
  {id: 'img-ocr', cats: ['image'], kind: 'prompt', agent: true, label: 'Extract the text (OCR)', labelKey: 'drop.action.imgOcr', prompt: () => 'do OCR on this image and extract all of the text.'},
  {id: 'log-errors', cats: ['log'], kind: 'prompt', agent: true, label: 'Find the errors', labelKey: 'drop.action.logErrors', prompt: () => 'read this log and list the errors and warnings, most important first.'},
  {id: 'log-cause', cats: ['log'], kind: 'prompt', agent: true, label: 'Find the root cause', labelKey: 'drop.action.logCause', prompt: () => 'read this log, find the root cause of the failure, and suggest a fix.'},
  {id: 'code-review', cats: ['code'], kind: 'prompt', agent: true, label: 'Review for bugs', labelKey: 'drop.action.codeReview', prompt: () => 'review this file for bugs and correctness issues.'},
  {id: 'code-explain', cats: ['code', 'config'], kind: 'prompt', agent: true, label: 'Explain what it does', labelKey: 'drop.action.codeExplain', prompt: () => 'explain what this file does.'},
  {id: 'code-security', cats: ['code', 'config'], kind: 'prompt', agent: true, label: 'Find security issues', labelKey: 'drop.action.codeSecurity', prompt: () => 'review this file for security problems.'},
  {id: 'code-tests', cats: ['code'], kind: 'prompt', agent: true, label: 'Write tests', labelKey: 'drop.action.codeTests', prompt: () => 'write tests for this file.'},
  {id: 'diff-review', cats: ['diff'], kind: 'prompt', agent: true, label: 'Review the diff', labelKey: 'drop.action.diffReview', prompt: () => 'review this diff for risks and regressions.'},
  {id: 'diff-commit', cats: ['diff'], kind: 'prompt', agent: true, label: 'Write a commit message', labelKey: 'drop.action.diffCommit', prompt: () => 'write a commit message for the change in this diff.'},
  {id: 'data-summary', cats: ['data'], kind: 'prompt', agent: true, label: 'Summarize the data', labelKey: 'drop.action.dataSummary', prompt: () => 'summarize the structure and contents of this data file (columns/schema, row count, anything notable).'},
  {id: 'data-anomaly', cats: ['data'], kind: 'prompt', agent: true, label: 'Find anomalies', labelKey: 'drop.action.dataAnomaly', prompt: () => 'look at this data file and point out anomalies or outliers.'},
  {id: 'doc-summary', cats: ['doc'], kind: 'prompt', agent: true, label: 'Summarize', labelKey: 'drop.action.docSummary', prompt: () => 'summarize this document.'},
  {id: 'doc-todos', cats: ['doc'], kind: 'prompt', agent: true, label: 'Extract the action items', labelKey: 'drop.action.docTodos', prompt: () => 'extract the action items and TODOs from this document.'},
  {id: 'dir-tree', cats: ['dir'], kind: 'prompt', agent: true, label: 'Summarize this folder', labelKey: 'drop.action.dirTree', prompt: () => 'summarize the contents of this folder.'},
  {id: 'dir-large', cats: ['dir'], kind: 'prompt', agent: true, label: 'Find the largest files', labelKey: 'drop.action.dirLarge', prompt: () => 'find the largest files in this folder.'},
  {id: 'multi-diff', cats: DROP_ACTION_CATEGORIES, kind: 'prompt', agent: true, label: 'Compare these files', labelKey: 'drop.action.multiDiff', minFiles: 2, prompt: ctx => `compare these ${ctx.paths.length} files and summarize the important differences.`},
  {id: 'multi-summary', cats: DROP_ACTION_CATEGORIES, kind: 'prompt', agent: true, label: 'Summarize all files', labelKey: 'drop.action.multiSummary', minFiles: 2, prompt: ctx => `summarize these ${ctx.paths.length} files together and call out common themes.`},
  {id: 'analyze', cats: ['any'], kind: 'prompt', agent: true, label: 'Take a look at it', labelKey: 'drop.action.analyze', prompt: () => 'take a look at this file and tell me what it is and anything notable.'},
  {id: 'shell-file', cats: DROP_ACTION_CATEGORIES.filter(cat => cat !== 'dir'), kind: 'shell', shell: true, readOnly: true, label: 'Show file type', labelKey: 'drop.action.shellFile', command: ctx => `file ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-wc', cats: ['log', 'code', 'diff', 'data', 'doc', 'config', 'any'], kind: 'shell', shell: true, readOnly: true, label: 'Count lines and bytes', labelKey: 'drop.action.shellWc', command: ctx => `wc -l -c ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-tail', cats: ['log', 'any'], kind: 'shell', shell: true, readOnly: true, label: 'Tail and watch', labelKey: 'drop.action.shellTail', command: ctx => `tail -F ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-jq', cats: ['data'], kind: 'shell', shell: true, readOnly: true, label: 'Pretty-print JSON with jq', labelKey: 'drop.action.shellJq', command: ctx => `jq . ${dropActionQuotedPaths(ctx).join(' ')}`},
  {id: 'shell-column', cats: ['data'], kind: 'shell', shell: true, readOnly: true, label: 'Show as table', labelKey: 'drop.action.shellColumn', command: ctx => `column -t -s, ${dropActionQuotedPaths(ctx).join(' ')} | less -S`},
  {id: 'shell-du', cats: ['dir'], kind: 'shell', shell: true, readOnly: true, label: 'Largest files here', labelKey: 'drop.action.shellDu', command: ctx => `du -ah ${dropActionQuotedPaths(ctx).join(' ')} | sort -h | tail -40`},
  {id: 'server-info', cats: DROP_ACTION_CATEGORIES, kind: 'server', readOnly: true, label: 'Server: file info', labelKey: 'drop.action.serverInfo'},
  {id: 'server-head', cats: ['log', 'code', 'diff', 'data', 'doc', 'config', 'any'], kind: 'server', readOnly: true, label: 'Server: preview head', labelKey: 'drop.action.serverHead'},
  {id: 'server-log-errors', cats: ['log', 'any'], kind: 'server', readOnly: true, label: 'Server: scan errors', labelKey: 'drop.action.serverLogErrors'},
  {id: 'server-data-stats', cats: ['data'], kind: 'server', readOnly: true, label: 'Server: data stats + chart', labelKey: 'drop.action.serverDataStats'},
  {id: 'server-ocr', cats: ['image'], kind: 'server', readOnly: true, label: 'Server: OCR image', labelKey: 'drop.action.serverOcr'},
];

function customDropActions() {
  const lines = nestedSetting(clientSettings, 'uploads.custom_actions', []);
  if (!Array.isArray(lines)) return [];
  return lines.map((line, index) => customDropActionFromLine(line, index)).filter(Boolean);
}

function customDropActionFromLine(line, index = 0) {
  const parts = String(line || '').split('|').map(part => part.trim());
  if (parts.length < 2 || !parts[0] || !parts[1]) return null;
  const rawCats = (parts[2] || 'any').split(',').map(cat => cat.trim().toLowerCase()).filter(Boolean);
  const cats = rawCats.filter(cat => DROP_ACTION_CATEGORIES.includes(cat));
  const body = parts[1];
  const shell = body.toLowerCase().startsWith('shell:');
  return {
    id: `custom-${index}-${fuzzyCanonicalPrefixText(parts[0]).slice(0, 24) || 'action'}`,
    custom: true,
    cats: cats.length ? cats : ['any'],
    kind: shell ? 'shell' : 'prompt',
    shell,
    agent: !shell,
    readOnly: shell,
    label: parts[0],
    template: shell ? body.slice(6).trim() : body,
  };
}

function dropActionMatchesCategory(action, category) {
  const cats = Array.isArray(action?.cats) ? action.cats : ['any'];
  if (category === 'dir') return cats.includes('dir');
  return cats.includes('any') || cats.includes(category);
}

function dropActionLastKey(category) {
  return `yolomux.dropAction.last.${category || 'any'}`;
}

function rememberDropAction(category, actionId) {
  if (!actionId) return;
  storageSet(dropActionLastKey(category), actionId);
}

function normalizedDropActionOrderText(value) {
  return String(value || '')
    .trim()
    .replace(/^;\s*/, '')
    .replace(/[.:]+$/g, '')
    .replace(/\s+/g, ' ')
    .toLowerCase();
}

function dropActionPromptText(action) {
  if (action?.kind !== 'prompt') return '';
  return String(action.template ? action.template : action.prompt?.({paths: [''], category: 'image'}) || '').trim();
}

function dropActionLabelAliases(action) {
  const label = String(action?.label || '').trim();
  const aliases = [label, label.replace(/\s*\([^)]*\)\s*/g, ' ').replace(/\s+/g, ' ').trim()];
  return aliases.map(normalizedDropActionOrderText).filter(Boolean);
}

function dropActionDisplayLabel(action) {
  if (!action) return '';
  if (action.menuLabel) return String(action.menuLabel);
  if (action.labelKey) return t(action.labelKey);
  return String(action.label || action.id || '');
}

function dropActionOrderAliases(action) {
  const aliases = [action?.id, ...dropActionLabelAliases(action)];
  if (Array.isArray(action?.aliases)) aliases.push(...action.aliases);
  if (action?.kind === 'prompt') {
    const clause = dropActionPromptText(action);
    if (clause) {
      aliases.push(clause, `; ${clause}`);
      dropActionLabelAliases(action).forEach(label => {
        aliases.push(`${label}: ${clause}`, `${label}: ; ${clause}`);
      });
    }
  }
  if (action?.id === 'server-info') aliases.push('info', 'file info', 'server info');
  if (action?.id === 'server-ocr') aliases.push('server ocr', 'ocr result');
  if (action?.id === 'shell-file') aliases.push('file', 'file type');
  if (action?.id === 'insert-path') aliases.push('insert path', 'path');
  return aliases.map(normalizedDropActionOrderText).filter(Boolean);
}

function imageDropActionPreferenceId(value) {
  const raw = String(value || '').trim();
  const normalized = normalizedDropActionOrderText(value);
  if (!normalized) return '';
  if (['insert-path', 'insert path', 'path', 'server-ocr', 'server ocr', 'server ocr image', 'ocr result', 'shell-file', 'show file type', 'file', 'file type'].includes(normalized)) return '';
  const exact = DROP_ACTIONS.find(candidate => dropActionOrderAliases(candidate).includes(normalized));
  if (exact) return exact.id;
  const colonIndex = raw.indexOf(':');
  if (colonIndex < 0) return '';
  const labelText = normalizedDropActionOrderText(raw.slice(0, colonIndex));
  const promptText = normalizedDropActionOrderText(raw.slice(colonIndex + 1));
  if (!promptText) return '';
  const promptMatches = DROP_ACTIONS.filter(candidate => dropActionOrderAliases(candidate).includes(promptText));
  if (!promptMatches.length) return '';
  const labelMatch = promptMatches.find(candidate => dropActionLabelAliases(candidate).includes(labelText));
  return (labelMatch || promptMatches[0]).id || '';
}

function canonicalDropActionPreferenceLabel(action) {
  if (!action) return '';
  if (action.id === 'server-info') return action.label || 'Server: file info';
  const prompt = dropActionPromptText(action);
  if (action.kind === 'prompt' && prompt) return `${action.label}: ; ${prompt}`;
  return action.label || action.id || '';
}

function imageDropActionPreferenceLabel(value, actionId) {
  const action = DROP_ACTIONS.find(candidate => candidate.id === actionId);
  const raw = String(value || '').trim();
  if (!raw) return canonicalDropActionPreferenceLabel(action);
  if (actionId === 'server-info') return action?.label || 'Server: file info';
  if (normalizedDropActionOrderText(raw) === normalizedDropActionOrderText(actionId)) return canonicalDropActionPreferenceLabel(action);
  return raw;
}

function preferredDropActionEntries(category) {
  if (category !== 'image') return [];
  const configured = nestedSetting(clientSettings, 'uploads.image_action_order', DEFAULT_IMAGE_DROP_ACTION_ORDER);
  const rawOrder = Array.isArray(configured) && configured.length ? configured : DEFAULT_IMAGE_DROP_ACTION_ORDER;
  const seen = new Set();
  const ordered = [];
  const addValue = value => {
    const actionId = imageDropActionPreferenceId(value);
    if (!actionId || seen.has(actionId)) return;
    seen.add(actionId);
    ordered.push({id: actionId, label: imageDropActionPreferenceLabel(value, actionId)});
  };
  rawOrder.forEach(addValue);
  if (!ordered.length && rawOrder !== DEFAULT_IMAGE_DROP_ACTION_ORDER) DEFAULT_IMAGE_DROP_ACTION_ORDER.forEach(addValue);
  return ordered;
}

function preferredDropActionOrder(category) {
  return preferredDropActionEntries(category).map(entry => entry.id);
}

function sortDropActionsByPreference(actions, category, options = {}) {
  const preferred = preferredDropActionEntries(category);
  if (!preferred.length) return actions;
  const byId = new Map(actions.map(action => [action.id, action]));
  const ordered = [];
  preferred.forEach(entry => {
    const action = byId.get(entry.id);
    if (!action) return;
    ordered.push({...action, menuLabel: action.custom ? (entry.label || action.label) : ''});
  });
  return ordered;
}

function sortDropActionsForCategory(actions, category, options = {}) {
  const hasPreferredOrder = preferredDropActionOrder(category).length > 0;
  actions = sortDropActionsByPreference(actions, category, options);
  if (hasPreferredOrder) return actions;
  const lastId = storageGet(dropActionLastKey(category), '');
  if (!lastId) return actions;
  const insert = actions.find(action => action.id === 'insert-path');
  const rest = actions.filter(action => action.id !== 'insert-path');
  const last = rest.find(action => action.id === lastId);
  if (!last) return actions;
  const ordered = [last, ...rest.filter(action => action !== last)];
  return insert && options.pathInserted !== true ? [insert, ...ordered] : ordered;
}

function dropActionsFor(category, agentKind, count = 1, options = {}) {
  const isAgent = agentKind === 'claude' || agentKind === 'codex';
  const preferredImageIds = category === 'image' ? new Set(preferredDropActionOrder(category)) : new Set();
  const all = [DROP_ACTIONS[0], ...customDropActions(), ...DROP_ACTIONS.slice(1)];
  const filtered = all.filter(action => {
    const configuredImageAction = preferredImageIds.has(action.id);
    if (action.id === 'insert-path' && options.pathInserted === true) return false;
    if (action.agent && !isAgent) return false;
    if (action.shell && isAgent && options.includeShellForAgents !== true && !configuredImageAction) return false;
    if (action.kind === 'server' && options.includeServer === false) return false;
    const minFiles = Number(action.minFiles || 1);
    const maxFiles = Number(action.maxFiles || 0);
    if (count < minFiles) return false;
    if (maxFiles > 0 && count > maxFiles) return false;
    return dropActionMatchesCategory(action, category);
  });
  return sortDropActionsForCategory(filtered, category, options).slice(0, 9);
}

function dropSuggestionsFor(category, agentKind, count = 1, options = {}) {
  return dropActionsFor(category, agentKind, count, options);
}

function dropActionContext(action, paths, category, agentKind, options = {}) {
  return {action, paths, category, agentKind, session: options.session || '', kind: options.kind || 'file', pathInserted: options.pathInserted === true};
}

function dropActionQuotedPaths(context) {
  return (context.paths || []).map(shellQuote);
}

function formatDropActionTemplate(template, context) {
  const paths = context.paths || [];
  const first = paths[0] || '';
  const values = {
    path: first,
    qpath: shellQuote(first),
    paths: paths.join(' '),
    qpaths: paths.map(shellQuote).join(' '),
    name: basenameOf(first),
    count: String(paths.length),
    category: context.category || 'any',
  };
  return String(template || '').replace(/\{(path|qpath|paths|qpaths|name|count|category)\}/g, (_m, key) => values[key] || '');
}

function composeDropSuggestion(action, context = {}) {
  if (!action) return '';
  const paths = Array.isArray(context.paths) && context.paths.length ? context.paths : ['/var/log/app.log'];
  const category = context.category || fileDropCategory(paths[0], context.kind || 'file');
  const fullContext = dropActionContext(action, paths, category, context.agentKind || '', context);
  if (action.kind === 'insert') return `${paths.map(shellQuote).join(' ')} `;
  if (action.kind === 'shell') {
    const command = action.template ? formatDropActionTemplate(action.template, fullContext) : action.command?.(fullContext);
    return String(command || '').trim();
  }
  if (action.kind === 'server') return '';
  const clause = action.template ? formatDropActionTemplate(action.template, fullContext) : action.prompt?.(fullContext);
  return String(clause || '').trim();
}

function insertedDropActionText(action, context = {}) {
  const text = composeDropSuggestion(action, context);
  if (!text) return '';
  const pathInserted = context.pathInserted === true;
  if (action.kind === 'prompt' && pathInserted) return `; ${text}`;
  if (action.kind === 'shell' && pathInserted) {
    const isAgent = context.agentKind === 'claude' || context.agentKind === 'codex';
    if (isAgent) {
      if (action.id === 'shell-file') return '; show the file type';
      return `; ${String(action.label || text).toLowerCase()}`;
    }
    return `\u0015${text}`;
  }
  return text;
}

function terminalDropShouldInsertPathFirst(session, payload) {
  const paths = Array.isArray(payload?.paths) ? payload.paths.filter(Boolean) : [payload?.path].filter(Boolean);
  if (!paths.length) return false;
  const agentKind = sessionAgentKind(session);
  if (agentKind !== 'claude' && agentKind !== 'codex') return false;
  const category = fileDropCategory(paths[0], payload?.kind);
  return dropActionsFor(category, agentKind, paths.length, {pathInserted: true, includeServer: false}).some(action => action.kind === 'prompt');
}

async function runDropAction(action, context) {
  const paths = context.paths || [];
  const category = context.category || fileDropCategory(paths[0], context.kind || 'file');
  rememberDropAction(category, action.id);
  if (action.kind === 'server') {
    await runServerDropAction(action, paths);
    return;
  }
  const text = composeDropSuggestion(action, context);
  if (!text) return;
  const suffix = insertedDropActionText(action, context);
  const shellActionActsAsAgentPrompt = action.kind === 'shell' && context.pathInserted && (context.agentKind === 'claude' || context.agentKind === 'codex');
  const autoEnter = action.kind === 'shell' && action.readOnly === true && !shellActionActsAsAgentPrompt && boolSetting('uploads.suggestion_autorun', false);
  const inserted = insertIntoTerminal(context.session, `${suffix}${autoEnter ? '\r' : ''}`);
  const displayLabel = dropActionDisplayLabel(action);
  statusEl.innerHTML = inserted
    ? `<span class="ok">${localizedHtml(autoEnter ? 'status.ranDropAction' : 'status.insertedDropAction', {name: displayLabel})}</span>`
    : `<span class="err">${terminalNotConnectedHtml(context.session)}</span>`;
}

async function runServerDropAction(action, paths) {
  try {
    const payload = await apiFetchJson('/api/drop-action/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action.id, paths}),
    });
    showDropActionResult(payload);
  } catch (error) {
    statusErr(localizedHtml('common.copyFailed', {error: userMessageText(error, error)}));
  }
}

function showDropActionResult(payload) {
  const presentation = dropActionResultPresentation(payload);
  showFileEditorDecisionDialog({
    title: presentation.title,
    bodyHtml: `<div class="drop-action-result"><pre>${esc(presentation.body)}</pre></div>`,
    actions: [{id: 'close', label: t('common.close')}],
    className: 'drop-action-result-dialog',
  });
}

function dropActionResultPresentation(payload) {
  const source = payload && typeof payload === 'object' ? payload : {};
  const result = source.result && typeof source.result === 'object' ? source.result : {};
  const title = structuredMessageText({...result, title: source.title}, 'title', t('upload.dropActionResultTitle'));
  const blocks = Array.isArray(result.blocks) ? result.blocks : [];
  const renderedBlocks = blocks.map(block => {
    const sections = Array.isArray(block?.sections) ? block.sections : [];
    const renderedSections = sections.map(section => (Array.isArray(section) ? section : [])
      .map(item => messageDescriptorText({
        key: item?.key,
        params: item?.params,
        fallback: Object.prototype.hasOwnProperty.call(item || {}, 'raw') ? String(item.raw ?? '') : '',
      }))
      .filter(Boolean)
      .join('\n'))
      .filter(Boolean);
    const heading = block?.path ? `## ${String(block.path)}` : '';
    return [heading, ...renderedSections].filter(Boolean).join('\n\n');
  }).filter(Boolean);
  return {
    title,
    body: renderedBlocks.join('\n\n') || source.body || userMessageText(source),
  };
}

let terminalDropSuggestionState = null;

function dismissTerminalDropSuggestions() {
  const state = terminalDropSuggestionState;
  if (!state) return;
  terminalDropSuggestionState = null;
  clearTimeout(state.timer);
  document.removeEventListener('keydown', state.onKeyDown, true);
  document.removeEventListener('pointerdown', state.onPointerDown, true);
  state.node.remove();
}

function dropSuggestionIndexFromKeyEvent(event) {
  if (event?.ctrlKey || event?.metaKey) return -1;
  const code = String(event?.code || '');
  if (/^Digit[1-9]$/.test(code)) return Number(code.slice(5)) - 1;
  if (/^Numpad[1-9]$/.test(code)) return Number(code.slice(6)) - 1;
  const key = String(event?.key || '');
  if (/^[1-9]$/.test(key)) return Number(key) - 1;
  return -1;
}

function showTerminalDropSuggestions(session, payload, x, y, options = {}) {
  dismissTerminalDropSuggestions();
  const paths = Array.isArray(payload?.paths) ? payload.paths.filter(Boolean) : [payload?.path].filter(Boolean);
  if (!paths.length) return false;
  const category = fileDropCategory(paths[0], payload?.kind);
  const agentKind = sessionAgentKind(session);
  const pathInserted = options.pathInserted === true;
  const suggestions = dropActionsFor(category, agentKind, paths.length, {pathInserted});
  if (!suggestions.length) return false;
  const rows = suggestions.map(action => ({
    label: dropActionDisplayLabel(action),
    run: () => runDropAction(action, dropActionContext(action, paths, category, agentKind, {pathInserted, session, kind: payload?.kind})),
  }));

  const node = document.createElement('div');
  node.className = 'terminal-drop-suggestions';
  node.setAttribute('role', 'listbox');
  const head = document.createElement('div');
  head.className = 'terminal-drop-suggestions-head';
  const prefix = pathInserted ? t('drop.pathInserted') : (paths.length > 1 ? tPlural('drop.files', paths.length) : basenameOf(paths[0]));
  head.textContent = t('drop.suggestionHint', {prefix, max: Math.min(rows.length, 9)});
  node.appendChild(head);
  rows.forEach((row, index) => {
    const item = document.createElement('div');
    item.className = 'terminal-drop-suggestion';
    item.setAttribute('role', 'option');
    item.tabIndex = -1;
    const combo = document.createElement('span');
    combo.className = 'terminal-drop-suggestion-combo';
    combo.textContent = String(index + 1);
    const label = document.createElement('span');
    label.className = 'terminal-drop-suggestion-label';
    label.textContent = row.label;
    item.append(combo, label);
    item.addEventListener('click', () => { row.run(); dismissTerminalDropSuggestions(); });
    node.appendChild(item);
  });
  document.body.appendChild(node);
  // Anchor at the drop point when there is one; for paste (no drop point) anchor near the session's
  // terminal, falling back to the viewport so the overlay is always on-screen.
  let anchorX = x;
  let anchorY = y;
  if (!Number.isFinite(anchorX) || !Number.isFinite(anchorY)) {
    const host = document.getElementById(terminalDomId(session)) || document.getElementById(panelDomId(session));
    const hostRect = host?.getBoundingClientRect?.();
    const fallbackViewport = appViewport();
    anchorX = hostRect ? hostRect.left + 16 : fallbackViewport.width / 2;
    anchorY = hostRect ? hostRect.top + 16 : fallbackViewport.height / 3;
  }
  const rect = node.getBoundingClientRect();
  const viewport = appViewport();
  node.style.left = `${Math.round(Math.max(8, Math.min(anchorX, viewport.width - rect.width - 8)))}px`;
  node.style.top = `${Math.round(Math.max(8, Math.min(anchorY, viewport.height - rect.height - 8)))}px`;

  const onKeyDown = event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      dismissTerminalDropSuggestions();
      return;
    }
    // Press 1..9 to pick a row. Accept top-row digits, numpad digits, and browsers that only provide
    // event.key. Exclude platform browser tab-switch shortcuts.
    const index = dropSuggestionIndexFromKeyEvent(event);
    if (index >= 0) {
      event.preventDefault();
      event.stopPropagation();
      if (index < rows.length) {
        rows[index].run();
        dismissTerminalDropSuggestions();
      }
      return;
    }
    // Any other key: the overlay is advisory — dismiss it but let the keystroke reach the terminal.
    dismissTerminalDropSuggestions();
  };
  const onPointerDown = event => { if (!node.contains(event.target)) dismissTerminalDropSuggestions(); };
  const timeoutMs = Number.isFinite(Number(options.timeoutMs)) ? Math.max(1, Number(options.timeoutMs)) : 6000;
  const timer = setTimeout(dismissTerminalDropSuggestions, timeoutMs);
  terminalDropSuggestionState = {node, timer, onKeyDown, onPointerDown};
  document.addEventListener('keydown', onKeyDown, true);
  document.addEventListener('pointerdown', onPointerDown, true);
  return true;
}

// Decide what a drop onto a terminal does. 'ignore' = let it bubble to the layout (files keep opening
// in the editor when suggestions are off); 'editor' = open a split (edge drops); 'suggest' = the
// transient overlay (center drops when uploads.show_suggestions is on); 'insert' = legacy dir path-insert.
function terminalDropMode(payload, intent) {
  if (!payload?.path) return 'ignore';
  const center = !intent?.targetSlot || intent.zone === 'middle';
  if (!center) return payload.kind === 'dir' ? 'editor' : 'ignore';
  if (boolSetting('uploads.show_suggestions', true)) return 'suggest';
  return payload.kind === 'dir' ? 'insert' : 'ignore';
}

function installFilePathDropTarget(session, target) {
  if (readOnlyMode) return;
  target.addEventListener('dragover', event => {
    const payload = fileDragPayload(event);
    const intent = dropIntentForEvent(event);
    const mode = terminalDropMode(payload, intent);
    if (mode === 'ignore') return;
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = 'copy';
    if (mode === 'editor' && intent?.targetSlot && pathDropIntentAllowsPayload(payload, intent)) showDropPreview(intent);
    else clearDropPreview();
    target.classList.add(CLS.pathDragOver);
  });
  target.addEventListener('dragleave', event => {
    if (target.contains(event.relatedTarget)) return;
    target.classList.remove(CLS.pathDragOver);
    clearDropPreview();
  });
  target.addEventListener('drop', event => {
    const payload = fileDragPayload(event);
    const intent = dropIntentForEvent(event);
    const mode = terminalDropMode(payload, intent);
    if (mode === 'ignore') return;
    event.preventDefault();
    event.stopPropagation();
    target.classList.remove(CLS.pathDragOver);
    clearDropPreview();
    if (mode === 'editor') {
      if (intent?.targetSlot && pathDropIntentAllowsPayload(payload, intent)) {
        openDraggedFilesInEditor(payload, {targetSlot: intent.targetSlot, targetZone: intent.zone});
      }
      return;
    }
    if (mode === 'suggest') {
      const pathInserted = terminalDropShouldInsertPathFirst(session, payload);
      if (pathInserted) insertFileDragPayloadIntoTerminal(session, payload);
      const shown = showTerminalDropSuggestions(session, payload, event.clientX, event.clientY, {pathInserted});
      if (!shown && !pathInserted) insertFileDragPayloadIntoTerminal(session, payload);
      return;
    }
    insertFileDragPayloadIntoTerminal(session, payload);
  });
}

function installTerminalFileDrop(session, container) {
  installFilePathDropTarget(session, container);
}
