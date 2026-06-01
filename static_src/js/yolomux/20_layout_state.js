function emptyLayoutSlots() {
  return {[layoutTreeKey]: null};
}

function emptyPaneState() {
  return {active: null, tabs: []};
}

function emptyPlaceholderPaneState() {
  return {active: null, tabs: [], placeholder: true};
}

function emptyPlaceholderLayoutSlots(slot = 'left') {
  const next = emptyLayoutSlots();
  next[slot] = emptyPlaceholderPaneState();
  next[layoutTreeKey] = leafNode(slot);
  return next;
}

function leafNode(slot) {
  return {slot};
}

function splitNode(direction, first, second, pct = defaultSplitPercent) {
  return {split: direction, pct: splitPercent(pct), children: [first, second]};
}

function splitPercent(value) {
  if (value === null || value === undefined || value === '') return defaultSplitPercent;
  const number = Number(value);
  if (!Number.isFinite(number)) return defaultSplitPercent;
  return Math.min(maxSplitPercent, Math.max(minSplitPercent, number));
}

function splitPercentForDisplay(value) {
  const rounded = Math.round(splitPercent(value) * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded);
}

function layoutLeafSlots(node) {
  if (!node) return [];
  if (node.slot) return [node.slot];
  return (node.children || []).flatMap(layoutLeafSlots);
}

function layoutSlotKeys(slots = layoutSlots) {
  const treeSlots = layoutLeafSlots(slots?.[layoutTreeKey]);
  if (treeSlots.length) return treeSlots;
  return Object.keys(slots || {}).filter(key => key !== layoutTreeKey && paneHasLayoutContent(key, slots));
}

function cloneLayoutSlots(slots = layoutSlots) {
  return JSON.parse(JSON.stringify(slots || emptyLayoutSlots()));
}

function layoutSlotsSignature(slots = layoutSlots) {
  return JSON.stringify(normalizeLayoutSlots(cloneLayoutSlots(slots)));
}

function compactCurrentLayoutSlots(options = {}) {
  const normalized = normalizeLayoutSlots(layoutSlots);
  if (layoutSlotsSignature(normalized) === layoutSlotsSignature(layoutSlots)) return false;
  applyLayoutSlots(normalized, {
    focusSession: options.focusSession || focusedPanelItem || undefined,
    prune: false,
  });
  return true;
}

function nextLayoutSlot(slots = layoutSlots) {
  const used = new Set(Object.keys(slots || {}));
  let index = 1;
  while (used.has(`slot${index}`)) index += 1;
  return `slot${index}`;
}

function normalizePaneState(raw, seen, options = {}) {
  const state = emptyPaneState();
  const items = Array.isArray(raw) ? raw : Array.isArray(raw?.tabs) ? raw.tabs : [];
  const preserveRemovedItems = new Set(options.preserveRemovedItems || []);
  let hadPreservedRemovedItem = false;
  for (const value of items) {
    if (preserveRemovedItems.has(String(value))) hadPreservedRemovedItem = true;
    const item = resolveLayoutItem(value);
    if (preserveRemovedItems.has(item)) hadPreservedRemovedItem = true;
    if (isLayoutItem(item) && !seen.has(item)) {
      state.tabs.push(item);
      seen.add(item);
    }
  }
  const active = resolveLayoutItem(raw?.active);
  state.active = state.tabs.includes(active) ? active : state.tabs[0] || null;
  if (!state.tabs.length && !Array.isArray(raw) && raw?.placeholder === true) state.placeholder = true;
  if (!state.tabs.length && options.preserveRemovedSlots === true && hadPreservedRemovedItem) state.placeholder = true;
  return state;
}

function normalizeLayoutSlots(value, options = {}) {
  let normalized;
  if (!value || typeof value !== 'object') normalized = emptyPlaceholderLayoutSlots();
  else if (value[layoutTreeKey]) normalized = normalizeTreeLayout(value, options);
  else normalized = normalizeLegacyLayoutSlots(value, options);
  return normalizeFileExplorerDock(normalized);
}

function normalizeTreeLayout(value, options = {}) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  next[layoutTreeKey] = normalizeLayoutNode(value[layoutTreeKey], value, next, seen, options);
  return compactLayoutSlots(next);
}

function normalizeLayoutNode(node, value, next, seen, options = {}) {
  if (!node || typeof node !== 'object') return null;
  if (typeof node.slot === 'string') {
    next[node.slot] = normalizePaneState(value[node.slot], seen, options);
    return leafNode(node.slot);
  }
  const direction = node.split === 'column' ? 'column' : 'row';
  const children = (node.children || []).map(child => normalizeLayoutNode(child, value, next, seen, options)).filter(Boolean);
  if (children.length >= 2) return splitNode(direction, children[0], children[1], node.pct);
  return children[0] || null;
}

function normalizeLegacyLayoutSlots(value, options = {}) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (const side of paneKeys) {
    next[side] = normalizePaneState(value[side], seen, options);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function legacyLayoutTree(slots) {
  const columns = basePaneKeys.map(column => legacyColumnTree(column, slots)).filter(Boolean);
  if (columns.length >= 2) return splitNode('row', columns[0], columns[1]);
  return columns[0] || null;
}

function legacyColumnTree(column, slots) {
  if (paneHasLayoutContent(column, slots)) return leafNode(column);
  const top = verticalSplitSlot(column, 'top');
  const bottom = verticalSplitSlot(column, 'bottom');
  const topNode = paneHasLayoutContent(top, slots) ? leafNode(top) : null;
  const bottomNode = paneHasLayoutContent(bottom, slots) ? leafNode(bottom) : null;
  if (topNode && bottomNode) return splitNode('column', topNode, bottomNode);
  return topNode || bottomNode;
}

function compactLayoutSlots(slots) {
  const next = emptyLayoutSlots();
  for (const key of layoutSlotKeys(slots)) next[key] = paneStateForLayoutSlot(key, slots);
  next[layoutTreeKey] = compactLayoutNode(slots[layoutTreeKey], next);
  const keys = layoutSlotKeys(next);
  if (keys.length && keys.every(key => paneIsPlaceholder(key, next))) return emptyPlaceholderLayoutSlots(keys[0] || 'left');
  return next[layoutTreeKey] ? next : emptyPlaceholderLayoutSlots();
}

function compactLayoutNode(node, slots) {
  return compactLayoutNodeInfo(node, slots)?.node || null;
}

function compactLayoutNodeInfo(node, slots) {
  if (!node) return null;
  if (node.slot) {
    if (!paneHasLayoutContent(node.slot, slots)) return null;
    const placeholderOnly = paneIsPlaceholder(node.slot, slots);
    const containsFileExplorer = paneTabs(node.slot, slots).includes(fileExplorerItemId);
    return {
      node: leafNode(node.slot),
      containsFileExplorer,
      directFileExplorerLeaf: containsFileExplorer,
      placeholderOnly,
    };
  }
  const direction = node.split === 'column' ? 'column' : 'row';
  const children = (node.children || []).map(child => compactLayoutNodeInfo(child, slots)).filter(Boolean);
  if (!children.length) return null;
  if (children.length === 1) return children[0];
  const hasFileExplorer = children.some(child => child.containsFileExplorer);
  const hasDirectFileExplorerLeaf = children.some(child => child.directFileExplorerLeaf);
  const kept = direction === 'row' && hasDirectFileExplorerLeaf
    ? children
    : children.filter(child => !child.placeholderOnly);
  const compacted = kept.length ? kept : [children[0]];
  if (compacted.length < 2) return compacted[0];
  const nextNode = splitNode(direction, compacted[0].node, compacted[1].node, node.pct);
  return {
    node: nextNode,
    containsFileExplorer: compacted.some(child => child.containsFileExplorer),
    directFileExplorerLeaf: false,
    placeholderOnly: compacted.every(child => child.placeholderOnly),
  };
}

function layoutNodeHasContent(node, slots = layoutSlots) {
  if (!node) return false;
  if (node.slot) return paneHasLayoutContent(node.slot, slots);
  return (node.children || []).some(child => layoutNodeHasContent(child, slots));
}

function layoutNodeContainsItem(node, item, slots = layoutSlots) {
  if (!node) return false;
  if (node.slot) return paneTabs(node.slot, slots).includes(item);
  return (node.children || []).some(child => layoutNodeContainsItem(child, item, slots));
}

function layoutHasHorizontalContentBeforeItem(node, item, slots = layoutSlots) {
  if (!node || node.slot) return false;
  const children = node.children || [];
  if (node.split === 'row') {
    let hasContentBefore = false;
    for (const child of children) {
      if (layoutNodeContainsItem(child, item, slots)) {
        return hasContentBefore || layoutHasHorizontalContentBeforeItem(child, item, slots);
      }
      if (layoutNodeHasContent(child, slots)) hasContentBefore = true;
    }
    return false;
  }
  return children.some(child => (
    layoutNodeContainsItem(child, item, slots)
    && layoutHasHorizontalContentBeforeItem(child, item, slots)
  ));
}

function fileExplorerNeedsLeftDock(slots = layoutSlots) {
  return layoutHasHorizontalContentBeforeItem(slots?.[layoutTreeKey], fileExplorerItemId, slots);
}

function normalizeFileExplorerDock(slots) {
  return fileExplorerNeedsLeftDock(slots) ? layoutWithFileExplorerDockedLeft(slots) : slots;
}

function layoutFromSessionList(values) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (const raw of values) {
    const item = resolveLayoutItem(raw);
    if (!isLayoutItem(item) || seen.has(item)) continue;
    const state = next.left || emptyPaneState();
    state.tabs.push(item);
    if (!state.active) state.active = item;
    next.left = state;
    seen.add(item);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutFromParam(raw, tabsRaw = '') {
  const text = String(raw || '').trim();
  if (!text) return null;
  if (text.toLowerCase() === 'empty') return emptyPlaceholderLayoutSlots();
  if (text.startsWith(layoutTreeParamPrefix)) return treeLayoutFromParam(text.slice(layoutTreeParamPrefix.length));
  if (compactLayoutParamLooksLikeTree(text)) return compactTreeLayoutFromParam(text, tabsRaw);
  const namedSlotLayout = namedSlotLayoutFromParam(text, tabsRaw);
  if (namedSlotLayout) return namedSlotLayout;
  const sides = text.split(',');
  if (!sides.some(value => value.trim())) return null;
  const next = emptyLayoutSlots();
  const seen = new Set();
  for (let index = 0; index < basePaneKeys.length; index += 1) {
    const side = basePaneKeys[index];
    for (const value of (sides[index] || '').split('+')) {
      if (!value.trim()) continue;
      const item = resolveLayoutItem(value.trim());
      if (isLayoutItem(item) && !seen.has(item)) {
        if (!next[side]) next[side] = emptyPaneState();
        next[side].tabs.push(item);
        if (!next[side].active) next[side].active = item;
        seen.add(item);
      }
    }
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return sessionsFromSlots(next).length ? next : null;
}

function namedSlotLayoutFromParam(raw, tabsRaw) {
  const tabStates = layoutTabStatesFromParam(tabsRaw);
  if (!tabStates.size) return null;
  const slotNames = String(raw || '')
    .split(',')
    .map(value => layoutSlotName(readableParamComponentDecode(value.trim())))
    .filter(Boolean);
  if (!slotNames.length) return null;
  const next = emptyLayoutSlots();
  const leaves = [];
  const seen = new Set();
  for (const slot of slotNames) {
    if (seen.has(slot)) continue;
    seen.add(slot);
    const state = tabStates.get(slot);
    if (!state || !paneHasLayoutContent(slot, {[slot]: state})) continue;
    next[slot] = state;
    leaves.push(leafNode(slot));
  }
  if (!leaves.length) return null;
  next[layoutTreeKey] = leaves.reduce((tree, leaf) => (tree ? splitNode('row', tree, leaf) : leaf), null);
  const normalized = normalizeLayoutSlots(next);
  return sessionsFromSlots(normalized).length || layoutSlotKeys(normalized).some(slot => paneIsPlaceholder(slot, normalized)) ? normalized : null;
}

function treeLayoutFromParam(raw) {
  try {
    const payload = JSON.parse(raw);
    if (!payload || typeof payload !== 'object') return null;
    const tree = layoutTreeFromParamNode(payload.tree);
    const slots = payload.slots && typeof payload.slots === 'object' ? payload.slots : {};
    const next = emptyLayoutSlots();
    next[layoutTreeKey] = tree;
    for (const slot of layoutLeafSlots(tree)) {
      const rawState = slots[slot];
      const tabs = Array.isArray(rawState?.tabs) ? rawState.tabs : Array.isArray(rawState) ? rawState : [];
      const active = resolveLayoutItem(rawState?.active);
      next[slot] = rawState?.placeholder === true && !tabs.length
        ? emptyPlaceholderPaneState()
        : paneStateWithTabs(tabs.map(resolveLayoutItem), active);
    }
    const normalized = normalizeLayoutSlots(next);
    return sessionsFromSlots(normalized).length ? normalized : null;
  } catch (_) {
    return null;
  }
}

function compactLayoutParamLooksLikeTree(text) {
  return /^(row|col|column)(?:@\d+(?:\.\d+)?)?\(/.test(text);
}

function compactTreeLayoutFromParam(raw, tabsRaw) {
  const parser = {text: String(raw || ''), index: 0};
  const tree = parseCompactLayoutNode(parser);
  skipCompactLayoutWhitespace(parser);
  if (!tree || parser.index !== parser.text.length) return null;
  const tabStates = layoutTabStatesFromParam(tabsRaw);
  const next = emptyLayoutSlots();
  next[layoutTreeKey] = tree;
  for (const slot of layoutLeafSlots(tree)) {
    next[slot] = tabStates.get(slot) || emptyPlaceholderPaneState();
  }
  const normalized = normalizeLayoutSlots(next);
  return sessionsFromSlots(normalized).length ? normalized : null;
}

function parseCompactLayoutNode(parser) {
  skipCompactLayoutWhitespace(parser);
  const name = readCompactLayoutToken(parser);
  if (!name) return null;
  const splitMatch = name.toLowerCase().match(/^(row|col|column)(?:@(\d+(?:\.\d+)?))?$/);
  skipCompactLayoutWhitespace(parser);
  if (splitMatch && parser.text[parser.index] === '(') {
    parser.index += 1;
    const children = [];
    while (parser.index < parser.text.length) {
      const child = parseCompactLayoutNode(parser);
      if (!child) return null;
      children.push(child);
      skipCompactLayoutWhitespace(parser);
      if (parser.text[parser.index] === ',') {
        parser.index += 1;
        continue;
      }
      if (parser.text[parser.index] === ')') {
        parser.index += 1;
        break;
      }
      return null;
    }
    if (children.length < 2) return children[0] || null;
    return splitNode(splitMatch[1] === 'row' ? 'row' : 'column', children[0], children[1], splitMatch[2]);
  }
  const slot = layoutSlotName(readableParamComponentDecode(name));
  return slot ? leafNode(slot) : null;
}

function readCompactLayoutToken(parser) {
  const start = parser.index;
  while (parser.index < parser.text.length && !/[(),\s]/.test(parser.text[parser.index])) parser.index += 1;
  return parser.text.slice(start, parser.index);
}

function skipCompactLayoutWhitespace(parser) {
  while (/\s/.test(parser.text[parser.index] || '')) parser.index += 1;
}

function layoutTreeFromParamNode(node) {
  if (!node || typeof node !== 'object') return null;
  const slot = layoutSlotName(node.slot);
  if (slot) return leafNode(slot);
  const children = (node.children || []).map(layoutTreeFromParamNode).filter(Boolean);
  if (children.length >= 2) return splitNode(node.split === 'column' ? 'column' : 'row', children[0], children[1], node.pct);
  return children[0] || null;
}

function layoutSlotName(value) {
  const slot = String(value || '').trim();
  return slot && slot !== layoutTreeKey ? slot : null;
}

function layoutParamValue(slots) {
  const tree = slots?.[layoutTreeKey];
  if (tree) return compactLayoutTreeParam(tree);
  const keys = layoutSlotKeys(slots);
  if (!keys.length) return 'empty';
  return keys.map(side => paneTabs(side, slots).map(readableItemParam).join('+')).join(',');
}

function compactLayoutTreeParam(node) {
  if (!node) return '';
  if (node.slot) return readableParamComponent(node.slot);
  const name = node.split === 'column' ? 'col' : 'row';
  return `${name}@${splitPercentForDisplay(node.pct)}(${(node.children || []).map(compactLayoutTreeParam).filter(Boolean).join(',')})`;
}

function layoutTabsParamValue(slots) {
  const slotValues = [];
  for (const slot of layoutSlotKeys(slots)) {
    if (paneIsPlaceholder(slot, slots)) {
      slotValues.push(`${readableParamComponent(slot)}:${emptyPaneParam}`);
      continue;
    }
    const active = activeItemForSide(slot, slots);
    const tabs = paneTabs(slot, slots).map((item, index) => {
      const marker = item === active && index > 0 ? '*' : '';
      return `${readableItemParam(item)}${marker}`;
    });
    if (tabs.length) slotValues.push(`${readableParamComponent(slot)}:${tabs.join(',')}`);
  }
  return slotValues.join(';');
}

function layoutTabStatesFromParam(raw) {
  const result = new Map();
  for (const part of String(raw || '').split(';')) {
    if (!part.trim()) continue;
    const separator = part.indexOf(':');
    if (separator <= 0) continue;
    const slot = layoutSlotName(readableParamComponentDecode(part.slice(0, separator)));
    if (!slot) continue;
    const tabs = [];
    let active = null;
    let placeholder = false;
    for (const rawItem of part.slice(separator + 1).split(',')) {
      let token = rawItem.trim();
      if (!token) continue;
      const activeToken = token.endsWith('*');
      if (activeToken) token = token.slice(0, -1);
      const decoded = readableParamComponentDecode(token);
      if (decoded === emptyPaneParam) {
        placeholder = true;
        continue;
      }
      const item = resolveLayoutItem(decoded);
      if (isLayoutItem(item) && !tabs.includes(item)) {
        tabs.push(item);
        if (activeToken) active = item;
      }
    }
    result.set(slot, placeholder && !tabs.length ? emptyPlaceholderPaneState() : paneStateWithTabs(tabs, active));
  }
  return result;
}

function readableItemParam(item) {
  return readableParamComponent(itemParam(item));
}

function readableParamComponent(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

function readableParamComponentDecode(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
}

function initialLayoutSlots() {
  const params = new URLSearchParams(location.search);
  const layoutFromUrl = layoutFromParam(params.get('layout') || '', params.get('tabs') || '');
  if (layoutFromUrl) return layoutFromUrl;
  const raw = params.get('sessions') || params.get('active') || '';
  const selected = [];
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !selected.includes(item)) selected.push(item);
  }
  if (selected.length) return layoutFromSessionList(selected);
  return defaultLayoutSlots();
}

function defaultLayoutSlots() {
  const sorted = visibleSessions.slice().sort((left, right) => String(left).localeCompare(String(right)));
  const next = emptyLayoutSlots();
  if (!sorted.length) {
    next.left = emptyPlaceholderPaneState();
  } else {
    next.left = paneStateWithTabs(sorted, sorted[0]);
  }
  next[layoutTreeKey] = legacyLayoutTree(next);
  return compactLayoutSlots(next);
}

function layoutWithItems(value, items, preferredSlot = null) {
  const next = normalizeLayoutSlots(value);
  const present = new Set(paneItems(next));
  const missing = items.filter(item => isLayoutItem(item) && !present.has(item));
  if (!missing.length) return next;
  let slot = preferredSlot && layoutSlotKeys(next).includes(preferredSlot) ? preferredSlot : firstEmptyPane(next) || layoutSlotKeys(next)[0];
  if (!slot) {
    slot = 'left';
    next[layoutTreeKey] = leafNode(slot);
    next[slot] = emptyPlaceholderPaneState();
  }
  if (!preferredSlot
    && activeItemForSide(slot, next) === fileExplorerItemId
    && paneTabs(slot, next).length === 1
    && missing.some(isTmuxSession)) {
    const sessionTabs = missing.filter(isTmuxSession);
    const otherTabs = missing.filter(item => !isTmuxSession(item));
    const newSlot = nextLayoutSlot(next);
    next[newSlot] = paneStateWithTabs(sessionTabs, sessionTabs[0] || null);
    next[slot] = paneStateWithTabs([...paneTabs(slot, next), ...otherTabs], activeItemForSide(slot, next));
    next[layoutTreeKey] = splitNode('row', leafNode(slot), leafNode(newSlot), fileExplorerSplitPercent);
    return compactLayoutSlots(next);
  }
  const tabs = [...paneTabs(slot, next), ...missing];
  const active = activeItemForSide(slot, next) || tabs.find(isTmuxSession) || tabs[0] || null;
  next[slot] = paneStateWithTabs(tabs, active);
  return compactLayoutSlots(next);
}

function paneTabs(side, slots = layoutSlots) {
  const state = slots?.[side];
  if (Array.isArray(state)) return state;
  return Array.isArray(state?.tabs) ? state.tabs : [];
}

function paneIsPlaceholder(side, slots = layoutSlots) {
  const state = slots?.[side];
  return Boolean(!Array.isArray(state) && state?.placeholder === true && !paneTabs(side, slots).length);
}

function paneHasLayoutContent(side, slots = layoutSlots) {
  return paneTabs(side, slots).length > 0 || paneIsPlaceholder(side, slots);
}

function paneStateForLayoutSlot(side, slots = layoutSlots) {
  return paneIsPlaceholder(side, slots)
    ? emptyPlaceholderPaneState()
    : paneStateWithTabs(paneTabs(side, slots), activeItemForSide(side, slots));
}

function slotColumn(slot) {
  if (String(slot).startsWith('right')) return 'right';
  return 'left';
}

function verticalSplitSlot(column, position) {
  return `${column}${position === 'top' ? 'Top' : 'Bottom'}`;
}

function activeItemForSide(side, slots = layoutSlots) {
  if (paneIsPlaceholder(side, slots)) return null;
  const stack = paneTabs(side, slots);
  const state = slots?.[side];
  const active = !Array.isArray(state) ? state?.active : null;
  return stack.includes(active) ? active : stack[0] || null;
}

function paneStateWithTabs(tabs, active = null) {
  const unique = [];
  for (const item of tabs) {
    if (isLayoutItem(item) && !unique.includes(item)) unique.push(item);
  }
  return {tabs: unique, active: unique.includes(active) ? active : unique[0] || null};
}

function paneItems(slots = layoutSlots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    for (const item of paneTabs(side, slots)) {
      if (!result.includes(item)) result.push(item);
    }
  }
  return result;
}

function activePaneItems(slots = layoutSlots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    const item = activeItemForSide(side, slots);
    if (item && !result.includes(item)) result.push(item);
  }
  return result;
}

function itemInLayout(item, slots = layoutSlots) {
  return paneItems(slots).includes(item);
}

function itemIsActivePaneTab(item, slots = layoutSlots) {
  return activePaneItems(slots).includes(item);
}

function itemIsBackgroundPaneTab(item, slots = layoutSlots) {
  return itemInLayout(item, slots) && !itemIsActivePaneTab(item, slots);
}

function allTabItems() {
  return [infoItemId, yoagentItemId, fileExplorerItemId, prefsItemId, changesItemId, ...openFileEditorItems(), ...visibleSessions];
}

function sortTabItems(items) {
  return items
    .slice()
    .sort((left, right) => itemSortNumber(left) - itemSortNumber(right) || itemLabel(left).localeCompare(itemLabel(right)));
}

function backgroundTabItems(slots = layoutSlots) {
  const activeItems = new Set(activePaneItems(slots));
  return sortTabItems(paneItems(slots).filter(item => !activeItems.has(item)));
}

function sessionsFromSlots(slots) {
  const result = [];
  for (const side of layoutSlotKeys(slots)) {
    const session = activeItemForSide(side, slots);
    if (session && !result.includes(session)) result.push(session);
  }
  return result;
}

function sessionsFromLayout() {
  return sessionsFromSlots(layoutSlots);
}

function isInfoItem(item) {
  return tabTypeForItem(item)?.key === 'info';
}

function isVirtualItem(item) {
  return Boolean(tabTypeForItem(item));
}

function openFileEditorItems() {
  const items = [];
  if (sharedImageViewerPath && openFiles.has(sharedImageViewerPath)) {
    items.push(imageViewerItemFor(sharedImageViewerPath));
  }
  for (const path of fileEditorTabPaths) {
    if (openFiles.has(path)) items.push(fileEditorItemFor(path));
  }
  for (const path of filePreviewTabPaths) {
    if (openFiles.has(path)) items.push(filePreviewItemFor(path));
  }
  return items;
}

function computeLayoutItems() {
  return [infoItemId, yoagentItemId, fileExplorerItemId, prefsItemId, changesItemId, ...openFileEditorItems(), ...visibleSessions];
}

function isTmuxSession(item) {
  return sessions.includes(item);
}

function isLayoutItem(item) {
  return layoutItems.includes(item);
}

function registerFileEditorLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  fileEditorTabPaths.add(path);
  if (!openFiles.has(path)) {
    openFiles.set(path, {
      mtime: 0,
      kind: 'loading',
      original: '',
      content: '',
      dirty: false,
      loading: true,
    });
  }
  syncFileLayoutItems();
  return fileEditorItemFor(path);
}

function registerFilePreviewLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  filePreviewTabPaths.add(path);
  if (!openFiles.has(path)) {
    openFiles.set(path, {
      mtime: 0,
      kind: 'loading',
      original: '',
      content: '',
      dirty: false,
      loading: true,
    });
  }
  syncFileLayoutItems();
  return filePreviewItemFor(path);
}

function registerImageViewerLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  sharedImageViewerPath = path;
  if (!openFiles.has(path)) {
    openFiles.set(path, {
      mtime: 0,
      kind: 'image',
      original: '',
      content: '',
      dirty: false,
      loading: true,
    });
  }
  syncFileLayoutItems();
  return imageViewerItemFor(path);
}

function resolveLayoutItem(value) {
  const text = String(value || '');
  const type = tabTypeForParam(text);
  if (type?.prefix === imageViewerItemPrefix) return registerImageViewerLayoutItem(text.slice(imageViewerItemPrefix.length)) || text;
  if (type?.prefix === fileEditorItemPrefix) return registerFileEditorLayoutItem(text.slice(fileEditorItemPrefix.length)) || text;
  if (type?.prefix === filePreviewItemPrefix) return registerFilePreviewLayoutItem(text.slice(filePreviewItemPrefix.length)) || text;
  if (type?.id) return type.id;
  if (sessions.includes(text)) return text;
  const ordinal = Number(text);
  if (Number.isInteger(ordinal) && ordinal > 0) return sessionForLabel(String(ordinal));
  return text;
}

function itemLabel(item) {
  const type = tabTypeForItem(item);
  if (type?.label) return type.label(item);
  return sessionLabel(item);
}

function itemSortNumber(item) {
  const type = tabTypeForItem(item);
  if (type) return type.sortRank;
  const label = Number(sessionLabel(item));
  return Number.isFinite(label) ? label : Number.MAX_SAFE_INTEGER;
}

function itemParam(item) {
  const type = tabTypeForItem(item);
  if (type) return tabTypeParam(type, item);
  return String(item);
}

const stateDefs = {
  'needs-approval': {label: 'Needs approval', short: 'EXEC?', priority: 0, attention: true},
  'yolo-approval': {label: 'YOLO pending approval', short: 'YOLO?', priority: 0, attention: false},
  'needs-input': {label: 'Needs input', short: 'QUES?', priority: 1, attention: true},
  blocked: {label: 'Blocked', short: 'BLK', priority: 2, attention: true},
  disconnected: {label: 'Disconnected', short: 'OFF', priority: 3, attention: true},
  'tests-running': {label: 'Tests running', short: 'TEST', priority: 4, attention: false},
  'ready-review': {label: 'Ready for review', short: 'PR', priority: 5, attention: false},
  working: {label: 'Working', short: 'RUN', priority: 6, attention: false},
  idle: {label: 'Idle', short: 'IDLE', priority: 7, attention: false},
  done: {label: 'Done', short: 'DONE', priority: 8, attention: false},
};

function stateDef(key) {
  return stateDefs[key] || stateDefs.idle;
}

function terminalDisconnected(session) {
  if (!activeSessions.includes(session)) return false;
  const item = terminals.get(session);
  if (!item) return false;
  return item.socket?.readyState === WebSocket.CLOSED || item.socket?.readyState === WebSocket.CLOSING;
}

function sessionState(session, info = transcriptMeta.sessions?.[session]) {
  if (!isTmuxSession(session)) return {key: 'idle', ...stateDefs.idle, reason: 'not a tmux session'};
  const auto = autoApproveStates.get(session) || {};
  const autoEnabled = autoApproveEnabledForSession(auto);
  const approvalPrompt = auto.prompt || {};
  const screen = auto.screen || {};
  const lastAction = String(auto.last_action || '').toLowerCase();
  const approvalPromptVisible = approvalPrompt.visible === true;
  const approvalYesSelected = approvalPrompt.yes_selected === true;
  const approvalPromptText = String(approvalPrompt.text || 'approval prompt is visible');
  const screenKey = String(screen.key || '');
  const screenText = String(screen.text || '');
  const agents = Array.isArray(info?.agents) ? info.agents : [];
  const panes = Array.isArray(info?.panes) ? info.panes : [];
  const agentText = agents
    .map(agent => `${agent.kind || ''} ${agent.status || ''} ${agent.error || ''}`)
    .join(' ')
    .toLowerCase();
  const paneText = panes
    .map(pane => `${pane.command || ''} ${pane.title || ''}`)
    .join(' ')
    .toLowerCase();
  const pr = info?.project?.pull_request;
  const prStatus = pullRequestStatusLabel(pr).toLowerCase();
  const checksState = String(pr?.checks?.state || '').toLowerCase();

  if (terminalDisconnected(session) || (!info && terminals.has(session))) {
    return stateValue('disconnected', 'terminal connection is closed');
  }
  if (screenKey === 'disconnected') {
    return stateValue('disconnected', screenText || 'terminal screen unavailable');
  }
  if (/blocked|denied|rejected/.test(lastAction)) {
    return stateValue('blocked', 'YOLO blocked an approval prompt');
  }
  if (approvalPromptVisible && approvalYesSelected && autoEnabled) {
    return stateValue('yolo-approval', 'YOLO sees the prompt and will press Enter');
  }
  if (approvalPromptVisible && approvalYesSelected) {
    return stateValue('needs-approval', approvalPromptText || 'approval prompt is visible');
  }
  if (approvalPromptVisible) {
    return stateValue('needs-input', 'approval prompt is visible but Yes is not selected');
  }
  if (!autoEnabled && /permission|approval|approve|confirm/.test(agentText)) {
    return stateValue('needs-approval', approvalPromptText || 'approval prompt is visible');
  }
  if (screenKey === 'working') {
    return stateValue('working', screenText || 'agent is working');
  }
  if (screenKey === 'needs-input') {
    return stateValue('needs-input', screenText || 'agent is waiting for input');
  }
  if (screenKey === 'error') {
    return stateValue('blocked', screenText || 'agent screen detection failed');
  }
  if (/needs input|waiting for input|awaiting input|user input|input required|waiting for user|paused/.test(agentText)) {
    return stateValue('needs-input', 'agent is waiting for input');
  }
  if (agents.some(agent => agentErrorIsBlocking(agent.error)) || /blocked|error|failed|failure|stuck/.test(agentText)) {
    return stateValue('blocked', 'agent reported an error or blocker');
  }
  if (/pytest|cargo test|npm test|pnpm test|yarn test|vitest|jest|ctest|go test|python3 -m pytest|python -m pytest|ruff|mypy|pre-commit/.test(paneText)) {
    return stateValue('tests-running', 'test command is active');
  }
  if (pr?.number && !pr.draft && prStatus !== 'closed' && prStatus !== 'merged' && (prStatus.includes('passing') || checksState === 'success')) {
    return stateValue('ready-review', 'PR checks are passing');
  }
  if (/done|completed|complete|finished|success/.test(agentText)) {
    return stateValue('done', 'agent status is complete');
  }
  if (agents.length || panes.some(pane => pane.active) || terminals.get(session)?.socket?.readyState === WebSocket.OPEN) {
    return stateValue('working', 'agent or active pane detected');
  }
  return stateValue('idle', 'no active agent state detected');
}

function agentErrorIsBlocking(error) {
  const text = String(error || '').toLowerCase();
  if (!text) return false;
  return !(/transcript not found/.test(text) || /^missing /.test(text));
}

function stateValue(key, reason, extra = {}) {
  const def = stateDef(key);
  return {key, ...def, reason, ...extra};
}

function autoApproveEnabledHere(payload) {
  return payload?.enabled === true;
}

function autoApproveEnabledElsewhere(payload) {
  return payload?.enabled_elsewhere === true || (payload?.locked === true && payload?.enabled !== true);
}

function autoApproveEnabledForSession(payload) {
  return autoApproveEnabledHere(payload) || autoApproveEnabledElsewhere(payload);
}

function yoloEnabledSessions() {
  return sessions.filter(session => autoApproveEnabledHere(autoApproveStates.get(session)));
}

function autoApproveScreenIsWorking(payload) {
  return String(payload?.screen?.key || '') === 'working';
}

function sessionYoloIsWorking(session, payload = autoApproveStates.get(session)) {
  return autoApproveEnabledHere(payload) && autoApproveScreenIsWorking(payload);
}

function yoloRotationDelay(now = Date.now()) {
  const duration = Math.max(0, Number(yoloRotateMs) || 0);
  if (duration <= 0) return '0s';
  return `${-((now % duration) / 1000).toFixed(3)}s`;
}

function attentionAnimationDelay(now = Date.now()) {
  return `${-((now % redReminderMs) / 1000).toFixed(3)}s`;
}

function attentionAnimationStyle() {
  return `--attention-animation-delay: ${attentionAnimationDelay()}`;
}

function syncAttentionAnimation(node, active) {
  if (!node?.style) return;
  if (active) {
    if (!node.style.getPropertyValue('--attention-animation-delay')) {
      node.style.setProperty('--attention-animation-delay', attentionAnimationDelay());
    }
  } else {
    node.style.removeProperty('--attention-animation-delay');
  }
}

function stateBadgeHtml(key, short, title) {
  const classes = ['session-state-badge', 'tab-symbol', `session-state-${key}`];
  const attention = stateDef(key).attention;
  if (attention) classes.push('session-state-reminder');
  const style = attention ? ` style="${attentionAnimationStyle()}"` : '';
  return `<span class="${esc(classes.join(' '))}"${style} title="${esc(title)}">${esc(short)}</span>`;
}

function sessionStateHtml(state) {
  if (!state || ['working', 'tests-running', 'done', 'disconnected', 'yolo-approval'].includes(state.key)) return '';
  return stateBadgeHtml(state.key, state.short, `${state.label}: ${state.reason}`);
}

function inactiveTabItems() {
  const inPane = new Set(paneItems());
  return sortTabItems(allTabItems().filter(item => !inPane.has(item)));
}

function renderNotifyToggle() {
  if (!notifyToggle) return;
  const supported = 'Notification' in window;
  notifyToggle.disabled = readOnlyMode;
  syncPressedButton(notifyToggle, notificationsEnabled, {
    labelOn: 'Notify',
    labelOff: 'Notify',
  });
  const browserState = supported ? Notification.permission : 'unsupported';
  notifyToggle.title = readOnlyMode
    ? 'Notify is admin-only'
    : `notify when a session needs attention; browser notifications: ${browserState}`;
}

async function toggleNotifications() {
  if (readOnlyMode) {
    statusEl.innerHTML = '<span class="err">readonly access cannot change Notify</span>';
    return;
  }
  const nextEnabled = !notificationsEnabled;
  let browserPermission = 'unsupported';
  if (nextEnabled && 'Notification' in window && Notification.permission === 'default') {
    const permission = await Notification.requestPermission();
    browserPermission = permission;
  } else if ('Notification' in window) {
    browserPermission = Notification.permission;
  }
  try {
    const response = await apiFetch(`/api/notify?enabled=${nextEnabled ? '1' : '0'}`, {method: 'POST'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || response.statusText || `HTTP ${response.status}`);
    notificationsEnabled = payload.enabled === true;
  } catch (error) {
    statusEl.innerHTML = `<span class="err">Notify request failed: ${esc(error)}</span>`;
    return;
  }
  renderNotifyToggle();
  renderSessionButtons();
  if (notificationsEnabled) {
    if (browserPermission !== 'granted') {
      statusEl.innerHTML = `<span class="ok">in-page alerts on; browser notifications ${esc(browserPermission)}</span>`;
    }
    sendTestNotification();
    notifyCurrentAttentionStates();
  } else {
    statusEl.innerHTML = '<span class="ok">Notify off</span>';
  }
}

async function loadNotifyStatus() {
  try {
    const response = await apiFetch('/api/notify', {cache: 'no-store'});
    const payload = await response.json();
    notificationsEnabled = response.ok && payload.enabled === true;
  } catch (_) {
    notificationsEnabled = false;
  }
  renderNotifyToggle();
}

function logOut() {
  window.location.href = '/logout';
}

function appMenuUiIcon(kind, active = false) {
  return `<span class="app-menu-ui-icon app-menu-ui-icon-${esc(kind)} ${active ? 'active' : ''}" aria-hidden="true"></span>`;
}

function tabTypeIconHtml(item, options = {}) {
  if (options.menu !== true) return '';
  const type = tabTypeForItem(item);
  const icon = typeof type?.icon === 'function' ? type.icon(item) : type?.icon;
  return icon ? appMenuUiIcon(icon) : '';
}

function projectReadmePath() {
  const root = String(repoRoot || '').replace(/\/+$/, '');
  return root ? `${root}/README.md` : '';
}

async function openProjectReadme() {
  const path = projectReadmePath();
  if (!path) {
    statusEl.innerHTML = '<span class="err">README path is unavailable</span>';
    return;
  }
  await openFileInEditor(path, 'README.md');
}

function keyboardShortcutCatalog() {
  return [
    {section: 'App', items: [
      {label: 'Command palette', keys: appShortcutText('P', {shift: true})},
      {label: 'File quick-open', keys: appShortcutText('P')},
      {label: `Toggle ${fileExplorerLabel()}`, keys: appShortcutText('B')},
      {label: 'Open Preferences', keys: appShortcutText(',')},
      {label: 'Keyboard shortcuts', keys: '?'},
    ]},
    {section: 'Editor', items: [
      {label: 'Save active editor', keys: appShortcutText('S')},
      {label: 'Find', keys: appShortcutText('F')},
      {label: 'Replace', keys: appShortcutText('H')},
      {label: 'Go to line', keys: appShortcutText('G')},
      {label: 'Toggle line comment', keys: appShortcutText('/')},
      {label: 'Indent / outdent', keys: 'Tab / Shift+Tab'},
      {label: 'Undo / redo', keys: `${appShortcutText('Z')} / ${appShortcutText('Z', {shift: true})}`},
    ]},
    {section: 'Diff', items: [
      {label: 'Undo accept/reject chunk', keys: appShortcutText('Z')},
      {label: 'Redo accept/reject chunk', keys: appShortcutText('Z', {shift: true})},
    ]},
    {section: 'Tabs / Panes', items: [
      {label: 'Close active editor/viewer tab', keys: `${appShortcutText('W')} · ${appShortcutText('Backspace')} outside text`},
      {label: 'Move or split tab', keys: 'Drag a tab'},
      {label: 'Session actions', keys: 'Right-click a tmux tab'},
      {label: 'Close menu or dialog', keys: 'Esc'},
    ]},
  ];
}

function keyboardShortcutsHtml() {
  return keyboardShortcutCatalog().map(section => `
    <section class="keyboard-shortcuts-section">
      <h3>${esc(section.section)}</h3>
      <div class="keyboard-shortcuts-list">
        ${section.items.map(item => `
          <div class="keyboard-shortcut-row">
            <span>${esc(item.label)}</span>
            <kbd>${esc(item.keys)}</kbd>
          </div>`).join('')}
      </div>
    </section>`).join('');
}

function ensureKeyboardShortcutsOverlay() {
  if (keyboardShortcutsNode) return keyboardShortcutsNode;
  const node = document.createElement('div');
  node.className = 'keyboard-shortcuts-overlay';
  node.hidden = true;
  node.innerHTML = `
    <div class="keyboard-shortcuts-dialog" role="dialog" aria-modal="true" aria-label="Keyboard shortcuts">
      <div class="keyboard-shortcuts-head">
        <h2>Keyboard shortcuts</h2>
        <button type="button" class="keyboard-shortcuts-close" aria-label="Close keyboard shortcuts">×</button>
      </div>
      <div class="keyboard-shortcuts-body"></div>
    </div>`;
  node.addEventListener('mousedown', event => {
    if (event.target === node) closeKeyboardShortcutsOverlay();
  });
  node.querySelector('.keyboard-shortcuts-close')?.addEventListener('click', closeKeyboardShortcutsOverlay);
  document.body.appendChild(node);
  keyboardShortcutsNode = node;
  return node;
}

function openKeyboardShortcutsOverlay() {
  const node = ensureKeyboardShortcutsOverlay();
  closeAppMenus();
  closeCommandPalette();
  const body = node.querySelector('.keyboard-shortcuts-body');
  if (body) body.innerHTML = keyboardShortcutsHtml();
  node.hidden = false;
  node.classList.add('open');
  node.querySelector('.keyboard-shortcuts-close')?.focus?.({preventScroll: true});
}

function closeKeyboardShortcutsOverlay() {
  if (!keyboardShortcutsNode) return;
  keyboardShortcutsNode.hidden = true;
  keyboardShortcutsNode.classList.remove('open');
}

function commandPaletteAllTabItems() {
  return Array.from(new Set([...activePaneItems(), ...backgroundTabItems(), ...inactiveTabItems()]));
}

function flattenMenuCommands(items, prefix = []) {
  const result = [];
  for (const item of items || []) {
    if (item.type === 'submenu') {
      result.push(...flattenMenuCommands(item.items, [...prefix, item.label]));
    } else if (item.type === 'command' && item.action && !item.disabled) {
      result.push({
        group: 'Menu',
        label: [...prefix, item.label].join(' / '),
        detail: item.detail || '',
        key: `menu:${[...prefix, item.label].join('/')}`,
        targetItem: item.targetItem || '',
        keybinding: commandPaletteKeybinding([...prefix, item.label].join(' / '), item.detail || ''),
        run: item.action,
      });
    }
  }
  return result;
}

function commandPaletteKeybinding(label, detail = '') {
  const text = `${label} ${detail}`;
  if (/command palette/i.test(text)) return appShortcutText('P', {shift: true});
  if (/open file/i.test(text)) return appShortcutText('P');
  if (/toggle .*file explorer|toggle .*finder/i.test(text)) return appShortcutText('B');
  if (/preferences/i.test(text)) return appShortcutText(',');
  if (/keyboard shortcuts/i.test(text)) return '?';
  if (/close menu|dialog/i.test(text)) return 'Esc';
  return /\b(?:Ctrl|Cmd|Shift|Esc|Alt)[^,;]*/.exec(detail)?.[0] || '';
}

function commandPaletteCommandItems() {
  const tabItems = commandPaletteAllTabItems().map(item => ({
    group: 'Tabs',
    label: itemLabel(item),
    detail: menuTabDetail(item),
    key: `tab:${item}`,
    targetItem: item,
    searchFields: tabSearchFields(item),
    keybinding: 'Enter',
    run: () => selectSession(item),
  }));
  const tabItemIds = new Set(commandPaletteAllTabItems());
  const menuItems = appMenuTree()
    .flatMap(menu => flattenMenuCommands(menu.items, [menu.label]))
    .filter(item => !(item.targetItem && isVirtualItem(item.targetItem) && tabItemIds.has(item.targetItem)));
  const settingItems = preferenceSections().flatMap(section => section.items.map(item => ({
    group: 'Settings',
    label: `${section.title} / ${item.label}`,
    detail: item.help || item.path,
    key: `setting:${item.path}`,
    keybinding: 'Enter',
    run: () => {
      preferencesSearchText = item.label;
      collapsedPreferenceSections.delete(section.title);
      writeStoredCollapsedPreferenceSections();
      selectSession(prefsItemId);
      requestAnimationFrame(() => {
        renderPreferencesPanels({force: true});
        const control = document.querySelector(`[data-setting-path="${cssEscape(item.path)}"]`);
        control?.focus?.({preventScroll: false});
        control?.scrollIntoView?.({block: 'center', inline: 'nearest'});
      });
    },
  })));
  return [...tabItems, ...menuItems, ...settingItems];
}

function fileQuickOpenRootForSearch() {
  const target = currentSessionActionTarget();
  const gitRoot = isTmuxSession(target) ? transcriptMeta.sessions?.[target]?.project?.git?.root : '';
  if (gitRoot) return normalizeDirectoryPath(gitRoot);
  const activeTmux = activeTmuxDirectoryPath(target);
  if (activeTmux) return activeTmux;
  if (activeFile) return dirnameOf(activeFile);
  if (fileExplorerRoot) return fileExplorerRoot;
  return repoRoot || homePath || '/';
}

function fileQuickOpenRootsForSearch(root = fileQuickOpenRoot || fileQuickOpenRootForSearch()) {
  return fileExplorerIndexedSearchRoots(root);
}

function fileQuickOpenScopeLabel(root = fileQuickOpenRoot || fileQuickOpenRootForSearch()) {
  const roots = fileQuickOpenRootsForSearch(root);
  if (roots.length <= 1) return compactHomePath(roots[0] || root || '/');
  return `${compactHomePath(roots[0])} + ${roots.length - 1} indexed`;
}

function fileQuickOpenQueryParts(query = commandPaletteQuery) {
  const raw = String(query || '').trim();
  const match = raw.match(/^(.*?)(?::(\d+))?$/);
  return {
    query: (match?.[1] || '').trim(),
    line: match?.[2] ? Math.max(1, Number(match[2])) : null,
    commandMode: raw.startsWith('>'),
    symbolMode: raw.startsWith('@'),
  };
}

function fileQuickOpenPathQuery(query = commandPaletteQuery) {
  const text = fileQuickOpenQueryParts(query).query;
  if (!text.startsWith('/') && !text.startsWith('~')) return {active: false, directory: '', filter: ''};
  if (text === '~') return {active: true, directory: '~', filter: ''};
  if (text.endsWith('/')) return {active: true, directory: text || '/', filter: ''};
  const slash = text.lastIndexOf('/');
  if (text.startsWith('~/') && slash <= 1) return {active: true, directory: '~', filter: text.slice(2)};
  if (slash <= 0) return {active: true, directory: '/', filter: text.slice(1)};
  return {active: true, directory: text.slice(0, slash) || '/', filter: text.slice(slash + 1)};
}

function joinDirectoryPath(directory, name) {
  if (!directory || directory === '/') return `/${name}`;
  if (directory === '~') return `~/${name}`;
  return `${directory.replace(/\/+$/, '')}/${name}`;
}

function descendFileQuickOpenDirectory(path) {
  const directory = normalizeDirectoryPath(path);
  commandPaletteQuery = `${directory}${directory.endsWith('/') ? '' : '/'}`;
  commandPaletteIndex = 0;
  const input = commandPaletteNode?.querySelector?.('.command-palette-input');
  if (input) input.value = commandPaletteQuery;
  renderCommandPaletteResults();
  refreshFileQuickOpenCandidates(commandPaletteQuery);
}

function revealOpenFileLineSoon(path, line) {
  if (!line) return;
  requestAnimationFrame(() => {
    for (const panel of fileEditorPanelsForPath(path)) {
      if (scrollFileEditorPanelToSourceLine(panel, 'editor', line)) break;
    }
  });
}

async function openFileQuickOpenPath(path, options = {}) {
  const label = basenameOf(path);
  let openedItem = null;
  if (options.split === true) {
    const targetSlot = largestPaneSlotForFileEditor();
    const splitBaseSlot = targetSlot || slotForSession(currentActiveMenuItem()) || largestPaneSlot();
    openedItem = await openFileInEditor(path, {name: label}, splitBaseSlot
      ? {targetSlot: splitBaseSlot, targetZone: targetSlot ? 'middle' : 'right', forceNewTab: true, userInitiated: true}
      : {forceNewTab: true, userInitiated: true});
  } else {
    openedItem = await openFileInEditor(path, {name: label}, {userInitiated: true});
  }
  focusQuickOpenedFile(openedItem);
  revealOpenFileLineSoon(path, options.line || null);
}

function focusQuickOpenedFile(item) {
  if (!item) return;
  focusPanel(item, {userInitiated: true});
  renderPaneTabStrips();
  requestAnimationFrame(() => focusPanel(item, {userInitiated: true}));
}

function fileQuickOpenItem(path, options = {}) {
  const label = basenameOf(path);
  const isDir = options.kind === 'dir';
  const detail = compactHomePath(path);
  return {
    group: options.group || 'Files',
    label: isDir ? `${label}/` : label,
    detail,
    key: `file:${path}`,
    iconText: isDir ? '▸' : fileIconFor(label),
    keybinding: isDir ? 'Enter' : `${appShortcutText('Enter')} split`,
    searchFields: [label, path, detail, options.relativePath || ''],
    sortBonus: Number(options.sortBonus || 0),
    run: () => isDir ? descendFileQuickOpenDirectory(path) : openFileQuickOpenPath(path, {line: fileQuickOpenQueryParts().line}),
    splitRun: isDir ? null : () => openFileQuickOpenPath(path, {line: fileQuickOpenQueryParts().line, split: true}),
  };
}

function recentFileQuickOpenItems() {
  return Array.from(openFiles.keys()).reverse().map((path, index) => fileQuickOpenItem(path, {
    group: 'Recent',
    sortBonus: 120 - index,
  }));
}

function fileQuickOpenItems() {
  const seen = new Set();
  const items = [];
  const add = item => {
    const path = item.searchFields?.[1] || item.detail || item.label;
    if (!path || seen.has(path)) return;
    seen.add(path);
    items.push(item);
  };
  recentFileQuickOpenItems().forEach(add);
  for (const file of fileQuickOpenCandidates) {
    const path = file.path || '';
    if (!path) continue;
    const indexedRoot = normalizeStoredFileExplorerIndexedDir(file.indexed_root || '');
    const baseRoot = normalizeStoredFileExplorerIndexedDir(fileQuickOpenRoot || '');
    add(fileQuickOpenItem(path, {
      group: indexedRoot && indexedRoot !== baseRoot ? `Indexed ${compactHomePath(indexedRoot)}` : 'Files',
      relativePath: file.relative_path || file.name || '',
      kind: file.kind || 'file',
      sortBonus: file.uploaded === true ? -500 : 0,
    }));
  }
  if (fileQuickOpenError) {
    items.push({
      group: 'Files',
      label: 'Search failed',
      detail: fileQuickOpenError,
      searchFields: ['search failed', fileQuickOpenError],
      disabled: true,
      run: null,
    });
  }
  if (fileQuickOpenLoading) {
    items.push({
      group: 'Files',
      label: 'Searching...',
      detail: compactHomePath(fileQuickOpenRoot),
      searchFields: ['searching'],
      disabled: true,
      run: null,
    });
  }
  return items;
}

function commandPaletteItems() {
  return commandPaletteEffectiveMode() === 'files' ? fileQuickOpenItems() : commandPaletteCommandItems();
}

function commandPaletteMatches(item, query) {
  return Number.isFinite(commandPaletteItemScore(item, query));
}

function commandPaletteItemScore(item, query) {
  if (item.disabled) return 0;
  const bonus = Number(item.sortBonus || 0) + commandPaletteRecentBonus(item);
  if (!String(query || '').trim()) return bonus;
  const base = fuzzySearchScore(query, item.searchFields || [item.label, item.detail, item.group]);
  return Number.isFinite(base) ? base + bonus : base;
}

function commandPaletteRecentBonus(item) {
  const key = item.key || `${item.group}:${item.label}`;
  const sequence = commandPaletteRecentKeys.get(key);
  return sequence ? 1000 + sequence : 0;
}

function rememberCommandPaletteItem(item) {
  if (!item || item.disabled) return;
  const key = item.key || `${item.group}:${item.label}`;
  setLimitedMapEntry(commandPaletteRecentKeys, key, ++commandPaletteRecentSequence, commandPaletteRecentKeyLimit);
}

function commandPaletteEffectiveMode() {
  return commandPaletteMode === 'files' && !fileQuickOpenQueryParts().commandMode ? 'files' : 'command';
}

function commandPaletteSearchQuery(query = commandPaletteQuery) {
  if (commandPaletteMode !== 'files') return String(query || '').trim();
  const parts = fileQuickOpenQueryParts(query);
  if (parts.commandMode) return String(query || '').replace(/^>\s*/, '').trim();
  if (parts.symbolMode) return String(query || '').replace(/^@\s*/, '').trim();
  const pathQuery = fileQuickOpenPathQuery(query);
  if (pathQuery.active) return pathQuery.filter;
  return parts.query;
}

function commandPalettePlaceholder() {
  if (commandPaletteMode === 'files' && commandPaletteQuery.trim().startsWith('>')) return 'Run command';
  if (commandPaletteMode === 'files' && commandPaletteQuery.trim().startsWith('@')) return 'Symbol search is not available yet';
  return commandPaletteMode === 'files'
    ? `Open file in ${fileQuickOpenScopeLabel(fileQuickOpenRoot || fileQuickOpenRootForSearch())}`
    : 'Find tabs, commands, settings';
}

function commandPaletteLabel() {
  return commandPaletteMode === 'files' ? 'File quick-open' : 'Command palette';
}

function commandPaletteEmptyText() {
  if (commandPaletteMode === 'files' && fileQuickOpenLoading) return 'Searching...';
  if (commandPaletteMode === 'files' && commandPaletteQuery.trim().startsWith('@')) return 'Symbol search is not available yet';
  return commandPaletteMode === 'files' ? 'No files found' : 'No matches';
}

function ensureCommandPalette() {
  if (commandPaletteNode) return commandPaletteNode;
  const node = document.createElement('div');
  node.className = 'command-palette';
  node.hidden = true;
  node.innerHTML = `
    <div class="command-palette-dialog" role="dialog" aria-modal="true" aria-label="Command palette">
      <input type="search" class="command-palette-input" placeholder="Find tabs, commands, settings" aria-label="Find tabs, commands, settings">
      <div class="command-palette-results" role="listbox"></div>
    </div>`;
  node.addEventListener('mousedown', event => {
    if (event.target === node) closeCommandPalette();
  });
  const input = node.querySelector('.command-palette-input');
  input.addEventListener('input', () => {
    commandPaletteQuery = input.value || '';
    commandPaletteIndex = 0;
    renderCommandPaletteResults();
    if (commandPaletteMode === 'files') scheduleFileQuickOpenSearch();
  });
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeCommandPalette();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      commandPaletteIndex = commandPaletteItemsCache.length ? (commandPaletteIndex + 1) % commandPaletteItemsCache.length : 0;
      renderCommandPaletteResults();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      commandPaletteIndex = commandPaletteItemsCache.length ? (commandPaletteIndex - 1 + commandPaletteItemsCache.length) % commandPaletteItemsCache.length : 0;
      renderCommandPaletteResults();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      invokeCommandPaletteSelection(event);
    }
  });
  node.querySelector('.command-palette-results').addEventListener('click', event => {
    const row = event.target.closest('[data-command-index]');
    if (!row || !node.contains(row)) return;
    commandPaletteIndex = Number(row.dataset.commandIndex || 0);
    invokeCommandPaletteSelection();
  });
  document.body.appendChild(node);
  commandPaletteNode = node;
  return node;
}

function renderCommandPaletteResults() {
  const node = ensureCommandPalette();
  const dialog = node.querySelector('.command-palette-dialog');
  const input = node.querySelector('.command-palette-input');
  const results = node.querySelector('.command-palette-results');
  const query = commandPaletteSearchQuery();
  dialog?.setAttribute('aria-label', commandPaletteLabel());
  if (input) {
    input.placeholder = commandPalettePlaceholder();
    input.setAttribute('aria-label', commandPaletteLabel());
  }
  commandPaletteItemsCache = commandPaletteItems()
    .map((item, sortIndex) => ({...item, score: commandPaletteItemScore(item, query), sortIndex}))
    .filter(item => Number.isFinite(item.score))
    .sort((left, right) => query
      ? right.score - left.score || left.group.localeCompare(right.group) || left.label.localeCompare(right.label)
      : right.score - left.score || left.sortIndex - right.sortIndex)
    .slice(0, 60);
  commandPaletteIndex = Math.min(commandPaletteIndex, Math.max(0, commandPaletteItemsCache.length - 1));
  if (!commandPaletteItemsCache.length) {
    results.innerHTML = `<div class="command-palette-empty">${esc(commandPaletteEmptyText())}</div>`;
    return;
  }
  results.innerHTML = commandPaletteItemsCache.map((item, index) => `
    <button type="button" class="command-palette-row${index === commandPaletteIndex ? ' active' : ''}" data-command-index="${index}" role="option" aria-selected="${index === commandPaletteIndex ? 'true' : 'false'}"${item.disabled ? ' disabled' : ''}>
      <span class="command-palette-group">${esc(item.group)}</span>
      <span class="command-palette-main"><span class="command-palette-title">${item.iconText ? `<span class="command-palette-file-icon" aria-hidden="true">${esc(item.iconText)}</span>` : ''}<span class="command-palette-label">${fuzzyHighlightHtml(query, item.label)}</span></span><span class="command-palette-detail">${fuzzyHighlightHtml(query, item.detail || '')}</span></span>
      <span class="command-palette-keybinding">${esc(item.keybinding || '')}</span>
    </button>`).join('');
  results.querySelector('.command-palette-row.active')?.scrollIntoView?.({block: 'nearest'});
}

function openCommandPalette(options = {}) {
  const node = ensureCommandPalette();
  closeAppMenus();
  commandPaletteMode = options.mode === 'files' ? 'files' : 'command';
  commandPaletteQuery = '';
  commandPaletteIndex = 0;
  node.hidden = false;
  node.classList.add('open');
  const input = node.querySelector('.command-palette-input');
  input.value = '';
  if (commandPaletteMode === 'files') {
    fileQuickOpenRoot = fileQuickOpenRootForSearch();
    fileQuickOpenCandidates = [];
    fileQuickOpenLoading = false;
    fileQuickOpenError = '';
    scheduleFileQuickOpenSearch({immediate: true});
  }
  renderCommandPaletteResults();
  input.focus({preventScroll: true});
}

function openFileQuickOpen() {
  openCommandPalette({mode: 'files'});
}

function closeCommandPalette() {
  if (!commandPaletteNode) return;
  commandPaletteNode.hidden = true;
  commandPaletteNode.classList.remove('open');
  if (fileQuickOpenDebounce) clearTimeout(fileQuickOpenDebounce);
  fileQuickOpenDebounce = null;
}

function focusCommandPaletteTarget(item) {
  const target = item?.targetItem;
  if (!isLayoutItem(target)) return;
  focusPanel(target, {userInitiated: true});
  renderPaneTabStrips();
  requestAnimationFrame(() => focusPanel(target, {userInitiated: true}));
}

async function invokeCommandPaletteSelection(event = null) {
  const item = commandPaletteItemsCache[commandPaletteIndex];
  if (!item || item.disabled) return;
  rememberCommandPaletteItem(item);
  closeCommandPalette();
  const action = appModifier(event) && item.splitRun ? item.splitRun : item.run;
  await Promise.resolve(action?.());
  focusCommandPaletteTarget(item);
}

function scheduleFileQuickOpenSearch(options = {}) {
  if (fileQuickOpenDebounce) clearTimeout(fileQuickOpenDebounce);
  const run = () => {
    if (commandPaletteMode === 'files' && !commandPaletteQuery.trim().startsWith('>')) refreshFileQuickOpenCandidates(commandPaletteQuery);
  };
  if (options.immediate) run();
  else fileQuickOpenDebounce = setTimeout(run, 160);
}

function abortFileQuickOpenSearch() {
  if (fileQuickOpenAbortController) {
    try { fileQuickOpenAbortController.abort(); } catch (_) {}
  }
  fileQuickOpenAbortController = null;
  fileQuickOpenRequestId += 1;
  fileQuickOpenLoading = false;
}

async function refreshFileQuickOpenCandidates(query = '') {
  const root = fileQuickOpenRoot || fileQuickOpenRootForSearch();
  if (!root) return;
  abortFileQuickOpenSearch();
  const requestId = ++fileQuickOpenRequestId;
  fileQuickOpenAbortController = typeof AbortController === 'function' ? new AbortController() : null;
  const fetchOptions = fileQuickOpenAbortController ? {signal: fileQuickOpenAbortController.signal} : {};
  fileQuickOpenLoading = true;
  renderCommandPaletteResults();
  try {
    const pathQuery = fileQuickOpenPathQuery(query);
    if (pathQuery.active) {
      const response = await apiFetch(`/api/fs/list?path=${encodeURIComponent(pathQuery.directory || '/')}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || response.status);
      if (requestId !== fileQuickOpenRequestId) return;
      fileQuickOpenRoot = payload.path || pathQuery.directory || root;
      const filter = String(pathQuery.filter || '').toLowerCase();
      fileQuickOpenCandidates = (Array.isArray(payload.entries) ? payload.entries : [])
        .filter(entry => entry?.kind === 'file' || entry?.kind === 'dir')
        .filter(entry => !filter || String(entry.name || '').toLowerCase().includes(filter))
        .map(entry => ({
          name: entry.name || '',
          path: joinDirectoryPath(payload.path || pathQuery.directory || '/', entry.name || ''),
          relative_path: entry.name || '',
          kind: entry.kind || 'file',
          size: entry.size,
          mtime: entry.mtime,
        }));
    } else {
      const searchRoots = fileQuickOpenRootsForSearch(root);
      const results = await Promise.all(searchRoots.map(async searchRoot => {
        try {
          const normalizedRoot = normalizeStoredFileExplorerIndexedDir(searchRoot);
          const recursive = normalizedRoot && fileExplorerDirectoryIsIndexed(normalizedRoot) ? '&recursive=1' : '';
          const response = await apiFetch(`/api/fs/search?root=${encodeURIComponent(searchRoot)}&query=${encodeURIComponent(commandPaletteSearchQuery(query))}&limit=500${recursive}`, fetchOptions);
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.error || response.status);
          return {ok: true, root: payload.root || searchRoot, files: Array.isArray(payload.files) ? payload.files : []};
        } catch (error) {
          return {ok: false, root: searchRoot, error};
        }
      }));
      if (requestId !== fileQuickOpenRequestId) return;
      const successful = results.filter(result => result.ok);
      if (!successful.length) {
        const firstError = results.find(result => result.error)?.error || 'search failed';
        throw new Error(firstError);
      }
      fileQuickOpenRoot = root;
      const seenPaths = new Set();
      fileQuickOpenCandidates = successful.flatMap(result => result.files.map(file => ({...file, indexed_root: result.root})))
        .filter(file => {
          const path = file?.path || '';
          if (!path || seenPaths.has(path)) return false;
          seenPaths.add(path);
          return true;
        });
    }
    fileQuickOpenError = '';
  } catch (error) {
    if (requestId !== fileQuickOpenRequestId) return;
    if (error?.name === 'AbortError') return;
    fileQuickOpenCandidates = [];
    fileQuickOpenError = String(error);
  } finally {
    if (requestId === fileQuickOpenRequestId) {
      fileQuickOpenAbortController = null;
      fileQuickOpenLoading = false;
      renderCommandPaletteResults();
    }
  }
}

function shouldNotifyState(state) {
  const configured = initialSetting('notifications.notify_transitions', ['needs-input', 'needs-approval', 'blocked']);
  const transitions = Array.isArray(configured) ? configured : ['needs-input', 'needs-approval', 'blocked'];
  return transitions.includes(state.key);
}

function sendBrowserNotification(title, options = {}) {
  const notification = new Notification(title, options);
  notification.onclick = () => {
    window.focus();
    if (options.session) selectSession(options.session);
  };
  return notification;
}

function setToastCountdown(node, durationMs) {
  if (!node) return;
  if (!Number.isFinite(durationMs)) {
    node.style.removeProperty('--toast-countdown-duration');
    return;
  }
  node.style.setProperty('--toast-countdown-duration', `${Math.max(1, durationMs)}ms`);
}

// Upload and attention/status messages share this renderer. Keep visual differences out of call sites.
function ensureToastShell(node, options = {}) {
  let bodyNode = node.querySelector('.toast-body');
  if (!bodyNode) {
    node.innerHTML = `
      <div class="toast-header">
        <div class="toast-title"></div>
        <div class="toast-control-row">
          <button type="button" class="toast-keep" data-toast-keep aria-label="${esc(options.keepLabel || 'Keep alert visible')}">Keep</button>
          <button type="button" class="toast-close" data-toast-close aria-label="${esc(options.closeLabel || 'Close alert')}">x</button>
        </div>
      </div>
      <div class="toast-body"></div>
      <div class="toast-actions"></div>`;
    bodyNode = node.querySelector('.toast-body');
  }
  const titleNode = node.querySelector('.toast-title');
  if (titleNode) titleNode.textContent = options.title || '';
  const actionsNode = node.querySelector('.toast-actions');
  if (actionsNode) {
    actionsNode.replaceChildren(...(options.actions || []));
    actionsNode.hidden = !actionsNode.children.length;
  }
  const closeButton = node.querySelector('[data-toast-close]');
  if (closeButton) {
    closeButton.onclick = event => {
      event.stopPropagation();
      options.onClose?.();
    };
  }
  const keepButton = node.querySelector('[data-toast-keep]');
  if (keepButton) {
    keepButton.onclick = event => {
      event.stopPropagation();
      node.classList.add('kept');
      keepButton.hidden = true;
      options.onKeep?.();
    };
  }
  return bodyNode;
}

function renderToastLines(bodyNode, lines, options = {}) {
  bodyNode.replaceChildren();
  for (const item of summarizeToastLines(lines, options)) {
    const lineText = typeof item === 'object' && item !== null ? item.text : item;
    const countdownMs = typeof item === 'object' && item !== null ? item.countdownMs : options.countdownMs;
    const line = document.createElement('div');
    line.className = 'toast-line';
    setToastCountdown(line, countdownMs || toastDurationMs);
    line.textContent = lineText;
    bodyNode.appendChild(line);
  }
}

function normalizeToastLine(item, options = {}) {
  const objectItem = typeof item === 'object' && item !== null;
  const text = objectItem ? item.text : item;
  return {
    text: compactToastText(text),
    countdownMs: objectItem ? item.countdownMs : options.countdownMs,
  };
}

function compactToastText(text) {
  const value = String(text || '').replace(/\s+/g, ' ').trim();
  if (value.length <= toastMaxLineChars) return value;
  return `${value.slice(0, toastMaxLineChars - 3)}...`;
}

function summarizeToastLines(lines, options = {}) {
  const normalized = (Array.isArray(lines) ? lines : toastTextLines(lines)).map(item => normalizeToastLine(item, options));
  if (normalized.length <= toastMaxLines) return normalized;
  const visible = normalized.slice(0, toastMaxLines - 1);
  const hidden = normalized.slice(toastMaxLines - 1);
  const countdownValues = hidden.map(item => item.countdownMs).filter(Number.isFinite);
  visible.push({
    text: `+${hidden.length} more`,
    countdownMs: countdownValues.length ? Math.max(...countdownValues) : options.countdownMs,
  });
  return visible;
}

function toastTextLines(text) {
  const lines = String(text || '').split('\n').map(line => line.trim()).filter(Boolean);
  return lines.length ? lines : [''];
}

function showToast(title, lines, options = {}) {
  const container = options.container || attentionAlerts;
  if (!container) return null;
  const id = ++attentionAlertSequence;
  const node = document.createElement('div');
  node.className = options.className || 'attention-alert toast';
  node.dataset.alertId = String(id);
  const bodyNode = ensureToastShell(node, {
    title,
    closeLabel: options.closeLabel,
    keepLabel: options.keepLabel,
    actions: options.actions,
    onKeep: () => {
      if (attentionAlertTimers.has(id)) {
        clearTimeout(attentionAlertTimers.get(id));
        attentionAlertTimers.delete(id);
      }
      options.onKeep?.();
    },
    onClose: () => {
      options.onClose?.();
      removeAttentionAlert(id);
    },
  });
  renderToastLines(bodyNode, Array.isArray(lines) ? lines : toastTextLines(lines), {
    countdownMs: options.countdownMs || toastDurationMs,
  });
  node.addEventListener('click', event => {
    if (event.target.closest('[data-toast-close], .toast-actions')) return;
    options.onClick?.();
  });
  container.appendChild(node);
  while (container.children.length > 5) {
    const first = container.firstElementChild;
    if (!first) break;
    removeAttentionAlert(Number(first.dataset.alertId || 0));
  }
  attentionAlertTimers.set(id, window.setTimeout(() => removeAttentionAlert(id), toastDurationMs));
  return node;
}

function displayToastContainer(session) {
  const sessionContainer = session ? document.getElementById(`panel-toasts-${session}`) : null;
  if (sessionContainer && sessionContainer.isConnected !== false) return sessionContainer;
  const candidates = [focusedPanelItem, ...activeSessions];
  for (const item of candidates) {
    const node = item ? document.getElementById(`panel-toasts-${item}`) : null;
    if (node && node.isConnected !== false) return node;
  }
  return document.querySelector('.panel-toast-stack') || attentionAlerts;
}

function showAttentionAlert(session, state) {
  const node = showToast(
    `YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`,
    state.reason,
    {
      container: displayToastContainer(session),
      onClick: () => selectSession(session),
    },
  );
  if (node) {
    node.dataset.toastSession = session;
    node.dataset.toastKind = 'attention';
  }
}

function dismissAttentionAlertsForSession(session) {
  for (const node of document.querySelectorAll('.toast[data-toast-kind="attention"]')) {
    if (node.dataset.toastSession !== session) continue;
    removeAttentionAlert(Number(node.dataset.alertId || 0));
  }
}

function attentionAlreadyVisible(session) {
  if (document.visibilityState !== 'visible') return false;
  if (!activeSessions.includes(session)) return false;
  const panel = document.getElementById(`panel-${session}`);
  if (!panel || !panel.isConnected) return false;
  return focusedPanelItem === session || focusedTerminal === session || activeSessions.length === 1;
}

function removeAttentionAlert(id) {
  if (attentionAlertTimers.has(id)) {
    clearTimeout(attentionAlertTimers.get(id));
    attentionAlertTimers.delete(id);
  }
  document.querySelector(`[data-alert-id="${id}"]`)?.remove();
}

function sendTestNotification() {
  showToast(`YOLOmux - ${serverHostname}: notifications enabled`, 'YOLOmux in-page alerts are enabled.', {
    container: displayToastContainer(focusedPanelItem),
  });
  if (!notificationsEnabled || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(`YOLOmux - ${serverHostname}: notifications enabled`, {
      body: 'YOLOmux can send browser notifications from this server.',
      tag: `yolomux:test:${Date.now()}`,
    });
    postEvent(null, 'notification_test_sent', 'notification test sent', {hostname: serverHostname});
  } catch (error) {
    statusEl.innerHTML = `<span class="err">notification failed: ${esc(error)}</span>`;
    postEvent(null, 'notification_error', `notification test failed: ${error}`, {hostname: serverHostname});
  }
}

function notifyCurrentAttentionStates() {
  for (const session of sessions.filter(isTmuxSession)) {
    const state = sessionState(session, transcriptMeta.sessions?.[session]);
    if (shouldNotifyState(state)) maybeNotifyState(session, state, {force: true});
  }
}

function eventMessageForState(session, state) {
  return `${sessionLabel(session)} ${state.label}: ${state.reason}`;
}

function stateSignature(state) {
  return `${state.key}:${state.reason || ''}`;
}

function trackSessionStateChanges() {
  for (const session of sessions.filter(isTmuxSession)) {
    const state = sessionState(session, transcriptMeta.sessions?.[session]);
    const previous = sessionStateKeys.get(session);
    const signature = stateSignature(state);
    sessionStateKeys.set(session, {key: state.key, reason: state.reason, signature});
    if (!stateTrackingReady || previous == null || previous.signature === signature) continue;
    postEvent(session, 'state_changed', eventMessageForState(session, state), {
      from: previous.key,
      from_reason: previous.reason,
      to: state.key,
      reason: state.reason,
    });
    maybeNotifyState(session, state);
  }
  stateTrackingReady = true;
}

function maybeNotifyState(session, state, options = {}) {
  if (!notificationsEnabled) return;
  if (!shouldNotifyState(state)) return;
  const key = `${session}:${stateSignature(state)}`;
  const now = Date.now();
  if (attentionAlreadyVisible(session)) {
    setLimitedMapEntry(notificationLastSent, key, now, notificationLastSentLimit);
    dismissAttentionAlertsForSession(session);
    postEvent(session, 'alert_suppressed_visible', eventMessageForState(session, state), {
      state: state.key,
      reason: state.reason,
    });
    return;
  }
  const lastSent = notificationLastSent.get(key) || 0;
  if (options.force !== true && now - lastSent < 60_000) return;
  setLimitedMapEntry(notificationLastSent, key, now, notificationLastSentLimit);
  const body = `${state.reason} · ${projectDirName(session, transcriptMeta.sessions?.[session])}`;
  showAttentionAlert(session, state);
  postEvent(session, 'alert_shown', eventMessageForState(session, state), {
    state: state.key,
    reason: state.reason,
  });
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(`YOLOmux - ${serverHostname}: ${sessionLabel(session)} ${state.label}`, {
      body,
      tag: key,
      renotify: true,
      session,
    });
    postEvent(session, 'notification_sent', eventMessageForState(session, state), {
      state: state.key,
      reason: state.reason,
    });
  } catch (error) {
    postEvent(session, 'notification_error', `notification failed: ${error}`, {
      state: state.key,
    });
  }
}

function updateTopbarMetrics() {
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height || 38);
  document.documentElement?.style?.setProperty('--topbar-height', `${height}px`);
}

function scheduleTopbarMetricsUpdate() {
  requestAnimationFrame(updateTopbarMetrics);
}

function bindTopbarMetrics() {
  updateTopbarMetrics();
  if (topbarResizeObserver || !topbar || !window.ResizeObserver) return;
  topbarResizeObserver = new ResizeObserver(updateTopbarMetrics);
  topbarResizeObserver.observe(topbar);
}

function scheduleTabStripOverflowCheck(strip) {
  if (!strip) return;
  if (strip === sessionButtons || strip.classList?.contains('pane-tabs')) {
    strip.classList.remove('tabs-overflowing');
    scheduleTopbarMetricsUpdate();
    return;
  }
  strip.classList.remove('tabs-overflowing');
  requestAnimationFrame(() => {
    strip.classList.toggle('tabs-overflowing', strip.scrollWidth > strip.clientWidth + 1);
  });
}

function scheduleAllTabStripOverflowChecks() {
  scheduleTabStripOverflowCheck(sessionButtons);
  for (const strip of document.querySelectorAll('.pane-tabs')) {
    scheduleTabStripOverflowCheck(strip);
  }
}

function normalizedSessionOrder(nextSessions) {
  if (!Array.isArray(nextSessions)) return null;
  const next = [];
  for (const session of nextSessions) {
    if (typeof session === 'string' && session && !next.includes(session)) next.push(session);
  }
  return next;
}

function setSessionOrder(next) {
  sessions = next;
  visibleSessions = sessions.slice(0, maxSessionTabs);
  layoutItems = computeLayoutItems();
}

function updateSessionList(nextSessions) {
  const next = normalizedSessionOrder(nextSessions);
  if (!next) return false;
  const changed = next.length !== sessions.length || next.some((session, index) => session !== sessions[index]);
  if (!changed) return false;
  const removedSessions = visibleSessions.filter(session => !next.includes(session));
  setSessionOrder(next);
  layoutSlots = normalizeLayoutSlots(layoutSlots, {
    preserveRemovedItems: removedSessions,
    preserveRemovedSlots: true,
  });
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  return true;
}

function applyLayoutSlots(nextSlots, options = {}) {
  const previousActive = activeSessions.slice();
  layoutSlots = normalizeLayoutSlots(nextSlots);
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  renderSessionButtons();
  renderPanels(previousActive, {prune: options.prune});
  for (const session of activeSessions.filter(isTmuxSession)) ensureTerminalRunning(session);
  refreshTranscripts();
  renderAutoApproveButtons();
  if (autoFocusEnabled && options.focusSession && activeSessions.includes(options.focusSession)) {
    setTimeout(() => focusPanel(options.focusSession), 80);
  } else if (options.message && activeSessions.length) {
    statusEl.textContent = options.message;
  } else {
    updateStatus();
  }
}

function updateActiveSessionParam() {
  const params = new URLSearchParams(location.search);
  params.delete('active');
  params.delete('sessions');
  params.delete('layout');
  params.delete('tabs');
  const queryParts = [];
  const inactiveItems = inactiveTabItems();
  if (activeSessions.length || inactiveItems.length) {
    if (activeSessions.length) {
      queryParts.push(`sessions=${activeSessions.map(readableItemParam).join(',')}`);
    }
    queryParts.push(`layout=${layoutParamValue(layoutSlots)}`);
    const tabs = layoutTabsParamValue(layoutSlots);
    if (tabs) queryParts.push(`tabs=${tabs}`);
  }
  const remaining = params.toString();
  if (remaining) queryParts.push(remaining);
  const query = queryParts.join('&');
  history.replaceState(null, '', `${location.pathname}${query ? `?${query}` : ''}${location.hash}`);
}

function syncInitialLayoutUrl() {
  updateActiveSessionParam();
}
