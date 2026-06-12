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

// a signature of the layout TREE SHAPE — topology + slot ids + split dirs/pcts — that
// EXCLUDES each pane's tabs order and active item (those live in the per-slot pane state, not the
// tree). Equal signatures mean a same-shape change (reorder / activate / move-within-or-between
// existing panes / replace-in-place / close-a-non-last tab) that needs no grid/topbar teardown.
function layoutShapeSignature(slots = layoutSlots) {
  const sig = node => {
    if (!node) return '';
    if (node.slot) return `S:${node.slot}`;
    return `${node.split}:${splitPercent(node.pct)}:[${(node.children || []).map(sig).join(',')}]`;
  };
  return sig(slots?.[layoutTreeKey]);
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

function normalizeLayoutMode(value) {
  const text = String(value || '').trim();
  return layoutModeValues.includes(text) ? text : defaultLayoutMode;
}

function configuredDefaultLayoutMode() {
  return normalizeLayoutMode(initialSetting('general.default_layout', defaultLayoutMode));
}

function layoutModePaneCount(mode, items) {
  const count = Array.isArray(items) ? items.length : 0;
  if (!count) return 0;
  const normalized = normalizeLayoutMode(mode);
  if (normalized === 'single') return 1;
  if (normalized === 'split') return Math.min(2, count);
  if (normalized === 'grid') return Math.min(4, count);
  return count;
}

function layoutModeBaseSlots(mode) {
  if (mode === 'grid') return ['leftTop', 'rightTop', 'leftBottom', 'rightBottom'];
  if (mode === 'split') return ['left', 'right'];
  if (mode === 'wall') return ['left', 'right'];
  return ['left'];
}

function layoutModeSlotNames(mode, count, options = {}) {
  const finderSlot = options.finderSlot || null;
  const used = {[layoutTreeKey]: null};
  if (finderSlot) used[finderSlot] = emptyPaneState();
  const slots = [];
  for (const slot of options.preferredSlots || []) {
    if (slots.length >= count) break;
    if (!slot || slot === finderSlot || used[slot]) continue;
    slots.push(slot);
    used[slot] = emptyPaneState();
  }
  for (const slot of layoutModeBaseSlots(normalizeLayoutMode(mode))) {
    if (slots.length >= count) break;
    if (slot === finderSlot || used[slot]) continue;
    slots.push(slot);
    used[slot] = emptyPaneState();
  }
  while (slots.length < count) {
    const slot = nextLayoutSlot(used);
    slots.push(slot);
    used[slot] = emptyPaneState();
  }
  return slots;
}

function partitionLayoutItems(items, count) {
  const groups = [];
  let start = 0;
  for (let index = 0; index < count; index += 1) {
    const remainingItems = items.length - start;
    const remainingGroups = count - index;
    const size = Math.ceil(remainingItems / remainingGroups);
    groups.push(items.slice(start, start + size));
    start += size;
  }
  return groups;
}

function rowTreeForSlots(slots) {
  return slots.map(leafNode).reduce((tree, leaf) => (tree ? splitNode('row', tree, leaf) : leaf), null);
}

function columnTreeForSlots(slots) {
  return slots.map(leafNode).reduce((tree, leaf) => (tree ? splitNode('column', tree, leaf) : leaf), null);
}

function gridTreeForSlots(slots) {
  if (slots.length <= 2) return rowTreeForSlots(slots);
  const left = columnTreeForSlots([slots[0], slots[2]].filter(Boolean));
  const right = columnTreeForSlots([slots[1], slots[3]].filter(Boolean));
  return right ? splitNode('row', left, right) : left;
}

function layoutModeTreeForSlots(mode, slots) {
  return normalizeLayoutMode(mode) === 'grid' ? gridTreeForSlots(slots) : rowTreeForSlots(slots);
}

function layoutSlotsForItems(items, mode, options = {}) {
  const selected = (Array.isArray(items) ? items : []).filter(item => isLayoutItem(item));
  if (!selected.length) return emptyPlaceholderLayoutSlots(options.emptySlot || 'left');
  const normalizedMode = normalizeLayoutMode(mode);
  const paneCount = layoutModePaneCount(normalizedMode, selected);
  const slots = layoutModeSlotNames(normalizedMode, paneCount, options);
  const groups = partitionLayoutItems(selected, slots.length);
  const active = selected.includes(options.active) ? options.active : selected[0];
  const next = emptyLayoutSlots();
  for (let index = 0; index < slots.length; index += 1) {
    const group = groups[index] || [];
    next[slots[index]] = paneStateWithTabs(group, group.includes(active) ? active : group[0]);
  }
  next[layoutTreeKey] = layoutModeTreeForSlots(normalizedMode, slots);
  return compactLayoutSlots(next);
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
  state.tabs = orderPaneTabs(state.tabs);
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
  const seen = new Set();
  const items = [];
  for (const raw of values) {
    const item = resolveLayoutItem(raw);
    if (!isLayoutItem(item) || seen.has(item)) continue;
    items.push(item);
    seen.add(item);
  }
  return layoutWithFileExplorerDockedLeft(layoutSlotsForItems(items, configuredDefaultLayoutMode()));
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
  return layoutHasRestorableContent(next) ? next : null;
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
  return layoutHasRestorableContent(normalized) ? normalized : null;
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
    return layoutHasRestorableContent(normalized) ? normalized : null;
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
  return layoutHasRestorableContent(normalized) ? normalized : null;
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

// a bookmarked ?…=yoagent / __yoagent__ (or legacy __yosup__) URL opens the merged pane on
// the YO!agent sub-tab. The aliases resolve to __info__ during normalization (losing the yoagent intent),
// so detect them in the raw params here and pre-select the sub-tab before the layout is built.
function maybeAdoptYoagentDeepLink(params) {
  const raw = [params.get('tabs') || '', params.get('sessions') || '', params.get('active') || ''].join(',');
  const referencesYoagent = raw
    .split(/[,:|]/)
    .map(value => value.trim())
    .some(value => tabTypeForParam(value)?.key === 'info' && value !== 'info' && value !== infoItemId);
  if (referencesYoagent) {
    infoPanelSubTab = 'yoagent';
    writeStoredInfoSubTab('yoagent');
  }
}

function initialLayoutSlots() {
  const params = new URLSearchParams(location.search);
  maybeAdoptYoagentDeepLink(params);
  const layoutFromUrl = layoutFromParam(params.get('layout') || '', params.get('tabs') || '');
  if (layoutFromUrl) return layoutWithDebugPaneActive(layoutFromUrl);
  const raw = params.get('sessions') || params.get('active') || '';
  const selected = [];
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !selected.includes(item)) selected.push(item);
  }
  if (selected.length) return layoutWithDebugPaneActive(layoutFromSessionList(selected));
  return layoutWithDebugPaneActive(defaultLayoutSlots());
}

function debugPanePreferredSlot(slots) {
  const keys = layoutSlotKeys(slots);
  return keys.find(slot => paneTabs(slot, slots).includes(infoItemId) || paneTabs(slot, slots).includes(prefsItemId))
    || keys.find(slot => {
      const active = activeItemForSide(slot, slots);
      return active && !isFileExplorerItem(active);
    })
    || keys[0]
    || 'left';
}

function layoutWithDebugPaneActive(slots) {
  if (!debugModeEnabled || !isLayoutItem(debugPaneItemId)) return slots;
  const next = cloneLayoutSlots(slots);
  const existingSlot = layoutSlotKeys(next).find(slot => paneTabs(slot, next).includes(debugPaneItemId));
  const slot = existingSlot || debugPanePreferredSlot(next);
  const tabs = paneTabs(slot, next).filter(item => item !== debugPaneItemId);
  next[slot] = paneStateWithTabs([...tabs, debugPaneItemId], debugPaneItemId);
  if (!next[layoutTreeKey]) next[layoutTreeKey] = leafNode(slot);
  return compactLayoutSlots(next);
}

function defaultLayoutSlots() {
  const sorted = visibleSessions.slice().sort((left, right) => String(left).localeCompare(String(right)));
  return layoutWithFileExplorerDockedLeft(layoutSlotsForItems(sorted, configuredDefaultLayoutMode()), {
    preservePlaceholders: false,
  });
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

function layoutHasRestorableContent(slots = layoutSlots) {
  return paneItems(slots).length > 0 || layoutSlotKeys(slots).some(slot => paneIsPlaceholder(slot, slots));
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

function normalizePinnedTabItems(items = pinnedTabItems) {
  const result = [];
  for (const raw of items || []) {
    const item = String(raw || '').trim();
    if (item && isPinnableTab(item) && !result.includes(item)) result.push(item);
  }
  return result;
}

function isPinnableTab(item) {
  return isLayoutItem(item) && !isFileExplorerItem(item);
}

function tabIsPinned(item) {
  return pinnedTabItems.includes(item) && isPinnableTab(item);
}

function orderPaneTabs(tabs) {
  const unique = [];
  for (const item of tabs) {
    if (isLayoutItem(item) && !unique.includes(item)) unique.push(item);
  }
  return [
    ...unique.filter(tabIsPinned),
    ...unique.filter(item => !tabIsPinned(item)),
  ];
}

function paneStateWithTabs(tabs, active = null) {
  const ordered = orderPaneTabs(tabs);
  return {tabs: ordered, active: ordered.includes(active) ? active : ordered[0] || null};
}

function pinnedTabIconHtml(item) {
  if (!tabIsPinned(item)) return '';
  const label = t('tab.pinned');
  return `<span class="pane-tab-pin-icon" title="${esc(label)}" aria-label="${esc(label)}"></span>`;
}

function setTabPinned(item, pinned) {
  const resolved = resolveLayoutItem(item);
  if (!isPinnableTab(resolved)) {
    statusEl.textContent = t('tab.pinUnavailable');
    return false;
  }
  const nextPinned = pinnedTabItems.filter(tab => tab !== resolved);
  if (pinned) nextPinned.push(resolved);
  pinnedTabItems = normalizePinnedTabItems(nextPinned);
  writeStoredPinnedTabs();
  const next = cloneLayoutSlots(layoutSlots);
  for (const slot of layoutSlotKeys(next)) {
    next[slot] = paneStateWithTabs(paneTabs(slot, next), activeItemForSide(slot, next));
  }
  applyLayoutSlots(next, {focusSession: resolved, prune: false, forceFull: dockviewLayoutActive()});
  statusEl.textContent = pinned ? t('tab.pinnedStatus', {name: itemLabel(resolved)}) : t('tab.unpinnedStatus', {name: itemLabel(resolved)});
  return true;
}

function toggleTabPinned(item) {
  const resolved = resolveLayoutItem(item);
  return setTabPinned(resolved, !tabIsPinned(resolved));
}

function toggleActiveTabPinned() {
  const item = currentActiveMenuItem();
  if (!item) return false;
  return toggleTabPinned(item);
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
  return [...virtualTabItems(), ...openFileEditorItems(), ...visibleSessions];
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
  for (const [path, state] of openFiles.entries()) {
    normalizeFileStateRecord(state);
    for (const item of state.editorTabItems) items.push(item);
  }
  return items;
}

function computeLayoutItems() {
  return [...virtualTabItems(), ...openFileEditorItems(), ...visibleSessions];
}

function isTmuxSession(item) {
  return sessions.includes(item);
}

function isLayoutItem(item) {
  return layoutItems.includes(item);
}

const paneScrollContainerSelector = [
  '.preferences-scroll',
  '.terminal .xterm-viewport',
  '.transcript-preview',
  '.summary-preview',
  '.event-list',
  '.info-list',
  '.yoagent-chat-history',
  '.file-explorer-tree-panel',
  '.file-explorer-changes-panel',
  '.file-editor-raw-panel',
  '.file-editor-preview-pane',
  '.file-editor-preview-pane-panel',
  '.file-editor-image-panel',
  '.file-editor-diff-codemirror .cm-mergeView',
  '.file-editor-codemirror .cm-scroller',
  '.file-editor-codemirror-panel .cm-scroller',
].join(', ');

function paneScrollContainerHasLayout(element) {
  if (!element || typeof element.isConnected === 'boolean' && !element.isConnected) return false;
  if (typeof element.clientHeight === 'number' && element.clientHeight <= 0) return false;
  if (typeof element.clientWidth === 'number' && element.clientWidth <= 0) return false;
  return true;
}

function paneScrollContainers(panel) {
  if (!panel?.querySelectorAll) return [];
  return Array.from(panel.querySelectorAll(paneScrollContainerSelector));
}

function paneScrollContainerKey(element, index) {
  const id = element?.id || '';
  if (id) return `id:${id}`;
  const explicit = element?.dataset?.paneScrollKey || '';
  if (explicit) return `data:${explicit}`;
  const classes = Array.from(element?.classList || []).slice(0, 4).join('.');
  return `${String(element?.tagName || 'node').toLowerCase()}.${classes}:${index}`;
}

function capturePaneElementScrollState(panel) {
  const entries = [];
  paneScrollContainers(panel).forEach((element, index) => {
    if (!paneScrollContainerHasLayout(element)) return;
    entries.push({
      key: paneScrollContainerKey(element, index),
      scrollTop: Number(element.scrollTop || 0),
      scrollLeft: Number(element.scrollLeft || 0),
    });
  });
  return entries;
}

function restorePaneElementScrollState(panel, state) {
  const entries = Array.isArray(state?.scrollContainers) ? state.scrollContainers : [];
  if (!entries.length) return;
  const current = new Map();
  paneScrollContainers(panel).forEach((element, index) => {
    current.set(paneScrollContainerKey(element, index), element);
  });
  const restore = () => {
    for (const entry of entries) {
      const element = current.get(entry.key);
      if (!paneScrollContainerHasLayout(element)) continue;
      element.scrollTop = Number(entry.scrollTop || 0);
      element.scrollLeft = Number(entry.scrollLeft || 0);
    }
  };
  restore();
  requestAnimationFrame(restore);
  requestAnimationFrame(() => requestAnimationFrame(restore));
  setTimeout(restore, 0);
}

function capturePaneViewState(item, panel) {
  if (!item || !panel) return false;
  const scrollContainers = capturePaneElementScrollState(panel);
  if (scrollContainers.length) {
    paneViewState.set(item, {
      scrollContainers,
      capturedAt: Date.now(),
    });
  }
  if (isFileEditorItem(item) && typeof captureFileEditorPanelViewState === 'function') {
    captureFileEditorPanelViewState(item, panel);
  }
  return scrollContainers.length > 0 || fileEditorViewState.has(item);
}

function capturePaneViewStateForItemIfPresent(item) {
  const panel = panelNodes.get(item);
  if (!panel) return false;
  return capturePaneViewState(item, panel);
}

function schedulePaneViewStateCapture(item, panel) {
  if (!item || !panel) return;
  if (pendingPaneViewStateCaptures.has(item)) return;
  pendingPaneViewStateCaptures.add(item);
  requestAnimationFrame(() => {
    pendingPaneViewStateCaptures.delete(item);
    const currentPanel = panelNodes.get(item) || panel;
    capturePaneViewState(item, currentPanel);
  });
}

function restorePaneViewState(item, panel) {
  if (!item || !panel) return;
  restorePaneElementScrollState(panel, paneViewState.get(item));
  if (isFileEditorItem(item) && typeof restoreFileEditorPanelViewState === 'function') {
    restoreFileEditorPanelViewState(item, panel);
  }
}

function registerFileEditorLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  const item = fileEditorItemFor(path);
  addFileEditorTabItem(path, item);
  if (openFiles.get(path)?.loading !== true && openFiles.get(path)?.kind) {
    syncFileLayoutItems();
    return item;
  }
  ensureFileState(path, {
      mtime: 0,
      kind: 'file',
      original: '',
      content: '',
      dirty: false,
      loading: true,
  });
  syncFileLayoutItems();
  return item;
}

function registerImageViewerLayoutItem(path) {
  if (!path || !path.startsWith('/')) return null;
  sharedImageViewerPath = path;
  if (!openFiles.get(path)?.kind) {
    ensureFileState(path, {
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
  if (text === 'changes' || text === '__changes__') {
    fileExplorerMode = 'diff';
    writeStoredFileExplorerMode(fileExplorerMode);
    return fileExplorerItemId;
  }
  const type = tabTypeForParam(text);
  if (type?.prefix === imageViewerItemPrefix) return registerImageViewerLayoutItem(text.slice(imageViewerItemPrefix.length)) || text;
  if (type?.prefix === fileEditorItemPrefix) return registerFileEditorLayoutItem(text.slice(fileEditorItemPrefix.length)) || text;
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

function stateReason(key, params = {}) {
  return t(`state.reason.${key}`, params);
}

function stateDef(key) {
  // #121: resolve the human label through t() on each access so a runtime language switch
  // re-localizes it (stateDefs is frozen at load). Compact badge text is localized too.
  const resolvedKey = stateDefs[key] ? key : 'idle';
  return {...stateDefs[resolvedKey], label: t(`state.${resolvedKey}`), short: t(`state.short.${resolvedKey}`)};
}

function terminalDisconnected(session) {
  if (!activeSessions.includes(session)) return false;
  const item = terminals.get(session);
  if (!item) return false;
  return item.socket?.readyState === WebSocket.CLOSED || item.socket?.readyState === WebSocket.CLOSING;
}

function sessionState(session, info = transcriptMeta.sessions?.[session]) {
  if (!isTmuxSession(session)) return {key: 'idle', ...stateDef('idle'), reason: t('state.notTmux')};
  const auto = autoApproveStates.get(session) || {};
  const autoEnabled = autoApproveEnabledForSession(auto);
  const approvalPrompt = auto.prompt || {};
  const screen = auto.screen || {};
  const lastAction = String(auto.last_action || '').toLowerCase();
  const approvalPromptVisible = approvalPrompt.visible === true;
  const approvalYesSelected = approvalPrompt.yes_selected === true;
  const approvalPromptText = String(approvalPrompt.text || stateReason('approvalPromptVisible'));
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
    return stateValue('disconnected', stateReason('terminalConnectionClosed'));
  }
  if (screenKey === 'disconnected') {
    const disconnectedReason = screenText && screenText !== 'failed to capture pane'
      ? screenText
      : stateReason('terminalScreenUnavailable');
    return stateValue('disconnected', disconnectedReason);
  }
  if (/blocked|denied|rejected/.test(lastAction)) {
    return stateValue('blocked', stateReason('yoloBlockedApproval'));
  }
  if (approvalPromptVisible && approvalYesSelected && autoEnabled) {
    return stateValue('yolo-approval', stateReason('yoloWillPressEnter'));
  }
  if (approvalPromptVisible && approvalYesSelected) {
    return stateValue('needs-approval', approvalPromptText || stateReason('approvalPromptVisible'));
  }
  if (approvalPromptVisible) {
    return stateValue('needs-input', stateReason('approvalYesNotSelected'));
  }
  if (!autoEnabled && /permission|approval|approve|confirm/.test(agentText)) {
    return stateValue('needs-approval', approvalPromptText || stateReason('approvalPromptVisible'));
  }
  if (screenKey === 'approval') {
    return stateValue('needs-approval', screenText || approvalPromptText || stateReason('approvalPromptVisible'));
  }
  if (screenKey === 'working') {
    return stateValue('working', screenText || stateReason('agentWorking'));
  }
  if (screenKey === 'needs-input') {
    return stateValue('needs-input', screenText || stateReason('agentWaitingInput'));
  }
  if (screenKey === 'error') {
    return stateValue('blocked', screenText || stateReason('agentScreenFailed'));
  }
  if (/needs input|waiting for input|awaiting input|user input|input required|waiting for user|paused/.test(agentText)) {
    return stateValue('needs-input', stateReason('agentWaitingInput'));
  }
  if (agents.some(agent => agentErrorIsBlocking(agent.error)) || /blocked|error|failed|failure|stuck/.test(agentText)) {
    return stateValue('blocked', stateReason('agentErrorBlocker'));
  }
  if (/pytest|cargo test|npm test|pnpm test|yarn test|vitest|jest|ctest|go test|python3 -m pytest|python -m pytest|ruff|mypy|pre-commit/.test(paneText)) {
    return stateValue('tests-running', stateReason('testsActive'));
  }
  if (pr?.number && !pr.draft && prStatus !== 'closed' && prStatus !== 'merged' && (prStatus.includes('passing') || checksState === 'success')) {
    return stateValue('ready-review', stateReason('prChecksPassing'));
  }
  if (/done|completed|complete|finished|success/.test(agentText)) {
    return stateValue('done', stateReason('agentComplete'));
  }
  if (agents.length || panes.some(pane => pane.active) || terminals.get(session)?.socket?.readyState === WebSocket.OPEN) {
    return stateValue('working', stateReason('agentActivePaneDetected'));
  }
  return stateValue('idle', stateReason('noActiveAgent'));
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
  // Spin the YO ball whenever the agent is working, regardless of auto-approve state.
  return autoApproveScreenIsWorking(payload);
}

function runningAgentCount() {
  return sessions.filter(session => autoApproveScreenIsWorking(autoApproveStates.get(session))).length;
}

function documentTitleNowMs() {
  const testNow = Number(window.__yolomuxDocumentTitleNowMs);
  return Number.isFinite(testNow) ? testNow : Date.now();
}

function documentTitleIdleMinutes(now) {
  if (documentTitleIdleSinceMs === null) return 0;
  const elapsedMs = Math.max(0, now - documentTitleIdleSinceMs);
  return elapsedMs >= documentTitleIdleThresholdMs ? Math.max(2, Math.floor(elapsedMs / 60000)) : 0;
}

function updateDocumentTitle() {
  const count = runningAgentCount();
  if (count > 0) {
    documentTitleIdleSinceMs = null;
    document.title = `YOLOmux [${count} running]`;
    return;
  }
  const now = documentTitleNowMs();
  if (documentTitleIdleSinceMs === null) documentTitleIdleSinceMs = now;
  const idleMinutes = documentTitleIdleMinutes(now);
  document.title = idleMinutes ? `YOLOmux (idle for ${idleMinutes} min)` : 'YOLOmux [idle]';
}

// Cross-session "what's going on" rollup for the always-visible top-bar status line: how many tmux
// agents are running, how many need the user, how many are idle. Reads the same live per-session
// state the tabs use (stateful, no recompute).
function globalActivityCounts() {
  let running = 0;
  let attention = 0;
  let total = 0;
  for (const session of sessions) {
    if (!isTmuxSession(session)) continue;
    total += 1;
    const key = sessionState(session).key;
    if (['working', 'tests-running', 'yolo-approval'].includes(key)) running += 1;
    else if (['needs-input', 'needs-approval', 'blocked', 'disconnected'].includes(key)) attention += 1;
  }
  return {running, attention, idle: Math.max(0, total - running - attention), total};
}

function globalActivityStatusLineHtml() {
  const counts = globalActivityCounts();
  if (!counts.total) return '';
  const parts = [];
  if (counts.running) parts.push(`<span class="topbar-activity-run">${counts.running} running</span>`);
  if (counts.attention) parts.push(`<span class="topbar-activity-attn">${counts.attention} need you</span>`);
  parts.push(`<span class="topbar-activity-idle">${counts.idle} idle</span>`);
  return parts.join('<span class="topbar-activity-sep" aria-hidden="true">·</span>');
}

function createTopbarActivityStatus() {
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'topbarActivity';
  button.className = 'topbar-activity';
  button.title = 'Open the cross-session AI activity summary';
  button.setAttribute('aria-label', 'AI activity summary across all sessions');
  button.onclick = () => openInfoSubTab('yoagent');
  return button;
}

function updateTopbarActivityStatus() {
  const node = document.getElementById('topbarActivity');
  if (!node) return;
  const counts = globalActivityCounts();
  const html = globalActivityStatusLineHtml();
  node.innerHTML = html;
  node.hidden = !html;
  node.classList.toggle('has-attention', counts.attention > 0);
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
  // 'ready-review' is dropped — the dedicated #NNNN / CI / Approved PR chips already convey
  // "PR ready", so the standalone "PR" state pill is redundant on the tab.
  if (!state || ['working', 'tests-running', 'done', 'disconnected', 'yolo-approval', 'ready-review'].includes(state.key)) return '';
  return stateBadgeHtml(state.key, state.short || stateDef(state.key).short, `${state.label}: ${state.reason}`);
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
    ? t('notify.adminOnlyTitle')
    : t('notify.toggleTitle', {state: browserState});
}

async function toggleNotifications() {
  if (readOnlyMode) {
    statusErr(localizedHtml('status.readOnlyChangeNotify'));
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
    statusErr(localizedHtml('status.notifyRequestFailed', {error}));
    return;
  }
  renderNotifyToggle();
  renderSessionButtons();
  if (notificationsEnabled) {
    if (browserPermission !== 'granted') {
      statusOk(`in-page alerts on; browser notifications ${esc(browserPermission)}`);
    }
    sendTestNotification();
    notifyCurrentAttentionStates();
  } else {
    statusOk(`${esc(t('status.notifyOff'))}`);
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
    statusErr(localizedHtml('status.readmePathUnavailable'));
    return;
  }
  // open the README as rendered markdown by default (the user can switch to edit via the
  // mode control); README.md is markdown so editorPreviewModeAvailable is true.
  await openFileInEditor(path, 'README.md', {viewMode: 'preview'});
}

function keyboardShortcutCatalog() {
  return [
    {section: t('shortcuts.section.app'), items: [
      {label: t('shortcuts.commandPalette'), keys: appShortcutText('P', {shift: true})},
      {label: t('shortcuts.fileQuickOpen'), keys: appShortcutText('P')},
      {label: t('shortcuts.toggleFinder', {name: fileExplorerLabel()}), keys: appShortcutText('B')},
      {label: t('shortcuts.openPreferences'), keys: appShortcutText(',')},
      {label: t('shortcuts.keyboardShortcuts'), keys: '?'},
    ]},
    {section: t('shortcuts.section.terminal'), items: [
      {label: t('shortcuts.copyTmuxSelection'), keys: appShortcutText('C', {alt: true})},
    ]},
    {section: t('shortcuts.section.editor'), items: [
      {label: t('shortcuts.saveEditor'), keys: appShortcutText('S')},
      {label: t('shortcuts.find'), keys: appShortcutText('F')},
      {label: t('shortcuts.replace'), keys: appShortcutText('H')},
      {label: t('shortcuts.goToLine'), keys: appShortcutText('G')},
      {label: t('shortcuts.toggleComment'), keys: appShortcutText('/')},
      {label: t('shortcuts.indentOutdent'), keys: 'Tab / Shift+Tab'},
      {label: t('shortcuts.undoRedo'), keys: `${appShortcutText('Z')} / ${appShortcutText('Z', {shift: true})}`},
      {label: t('shortcuts.editorNav'), keys: `${appShortcutText('[', {alt: true})} / ${appShortcutText(']', {alt: true})}`},
    ]},
    {section: t('shortcuts.section.diff'), items: [
      {label: t('shortcuts.undoChunk'), keys: appShortcutText('Z')},
      {label: t('shortcuts.redoChunk'), keys: appShortcutText('Z', {shift: true})},
    ]},
    {section: t('shortcuts.section.tabsPanes'), items: [
      {label: t('shortcuts.pinTab'), keys: t('shortcuts.keys.pinTab', {k: appShortcutText('K')})},
      {label: t('shortcuts.closeTab'), keys: t('shortcuts.keys.closeTab', {w: appShortcutText('W'), bs: appShortcutText('Backspace')})},
      {label: t('shortcuts.moveTab'), keys: t('shortcuts.keys.dragTab')},
      {label: t('shortcuts.sessionActions'), keys: t('shortcuts.keys.rightClick')},
      {label: t('shortcuts.closeMenu'), keys: 'Esc'},
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
    <div class="keyboard-shortcuts-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('shortcuts.title'))}">
      <div class="keyboard-shortcuts-head">
        <h2>${esc(t('shortcuts.title'))}</h2>
        <button type="button" class="keyboard-shortcuts-close" aria-label="${esc(t('shortcuts.close'))}">×</button>
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

const commandPaletteMissingFileTabPaths = new Set();
const commandPaletteFileTabValidationInflight = new Set();

function commandPaletteFilePathKnownMissing(path) {
  const normalized = normalizeDirectoryPath(path || '');
  return Boolean(normalized && (openFileIsMissing(normalized) || commandPaletteMissingFileTabPaths.has(normalized)));
}

function commandPaletteValidateFileTabPaths(items = commandPaletteAllTabItems()) {
  if (typeof fetchFilePathInfo !== 'function') return;
  for (const item of items) {
    const path = fileItemPath(item);
    const normalized = normalizeDirectoryPath(path || '');
    if (!normalized || commandPaletteFileTabValidationInflight.has(normalized) || openFileIsMissing(normalized)) continue;
    commandPaletteFileTabValidationInflight.add(normalized);
    fetchFilePathInfo(normalized, {user: true})
      .then(info => {
        if (info?.kind) commandPaletteMissingFileTabPaths.delete(normalized);
      })
      .catch(error => {
        if (Number(error?.status) === 404) {
          commandPaletteMissingFileTabPaths.add(normalized);
          markOpenFileMissing(normalized);
          if (commandPaletteNode && !commandPaletteNode.hidden) renderCommandPaletteResults();
        }
      })
      .finally(() => {
        commandPaletteFileTabValidationInflight.delete(normalized);
      });
  }
}

function commandPaletteVisibleTabItems() {
  const items = commandPaletteAllTabItems();
  commandPaletteValidateFileTabPaths(items);
  return items.filter(item => {
    const path = fileItemPath(item);
    return !path || !commandPaletteFilePathKnownMissing(path);
  });
}

function commandPaletteCommandTargetsMissingFile(item) {
  const targetPath = fileItemPath(item?.targetItem || '');
  return Boolean(targetPath && commandPaletteFilePathKnownMissing(targetPath));
}

function flattenMenuCommands(items, prefix = []) {
  const result = [];
  for (const item of items || []) {
    if (item.type === 'submenu') {
      result.push(...flattenMenuCommands(item.items, [...prefix, item.label]));
    } else if (item.type === 'command' && item.action && !item.disabled) {
      result.push({
        group: t('palette.group.menu'),
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

// a short localized label for an editor/preview view mode, shown as a chip on a deduped row.
function commandPaletteViewModeLabel(mode) {
  if (mode === 'preview') return t('editor.mode.preview');
  if (mode === 'split') return t('editor.mode.split');
  if (mode === 'diff') return t('editor.diff');
  return t('editor.mode.edit');
}

function commandPaletteNumericTime(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number) || number <= 0) return 0;
  if (number > 1e15) return number / 1e9;
  if (number > 1e11) return number / 1000;
  return number;
}

function commandPalettePaneMtime(item) {
  if (!isTmuxSession(item)) return 0;
  const info = sessionTranscriptInfo(item).info || {};
  return commandPaletteNumericTime(
    info.last_activity_ts || info.transcript_mtime || info.updated_ts || info.rolling_updated_ts || info.mtime || 0
  );
}

function commandPaletteCommandItems() {
  // Group FILE items by path and emit ONE row per file; non-file tabs (sessions, Finder/Info/Prefs)
  // stay one row each.
  const tabRow = (item, extra = {}) => ({
    group: t('palette.group.tabs'),
    category: 'pane',
    label: itemLabel(item),
    detail: menuTabDetail(item),
    key: `tab:${item}`,
    targetItem: item,
    mtime: commandPalettePaneMtime(item),
    searchFields: tabSearchFields(item),
    keybinding: 'Enter',
    run: () => (item === infoItemId ? openInfoSubTab('info') : selectSession(item, {userInitiated: true})),
    ...extra,
  });
  const fileGroups = new Map();   // path -> [items], in discovery order
  const tabItems = [];
  const allTabItems = commandPaletteVisibleTabItems();
  for (const item of allTabItems) {
    const path = fileItemPath(item);
    if (!path) { tabItems.push(tabRow(item)); continue; }
    if (!fileGroups.has(path)) fileGroups.set(path, []);
    fileGroups.get(path).push(item);
  }
  for (const [path, items] of fileGroups) {
    if (items.length === 1) { tabItems.push(tabRow(items[0])); continue; }
    const editorItem = items[0];
    // Each chip is clickable to jump to that exact view — carry the mode + the layout item.
    const viewModes = [];
    const seenModes = new Set();
    for (const it of items) {
      const mode = editorViewModeFor(path, it);
      if (seenModes.has(mode)) continue;
      seenModes.add(mode);
      viewModes.push({mode, label: commandPaletteViewModeLabel(mode), item: String(it)});
    }
    tabItems.push(tabRow(editorItem, {key: `file:${path}`, viewModes}));
  }
  const tabItemIds = new Set(allTabItems);
  const menuItems = appMenuTree()
    .flatMap(menu => flattenMenuCommands(menu.items, [menu.label]))
    .filter(item => !commandPaletteCommandTargetsMissingFile(item))
    .filter(item => !(item.targetItem && isVirtualItem(item.targetItem) && tabItemIds.has(item.targetItem)))
    .map(item => ({...item, category: 'command'}));
  const settingItems = preferenceSections().flatMap(section => section.items.map(item => ({
    group: t('palette.group.settings'),
    category: 'setting',
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
  return [...tabItems, ...menuItems, ...settingItems, ...commandPaletteDropActionItems()];
}

function commandPaletteDropActionPath() {
  const activeItem = currentActiveMenuItem();
  const activeItemPath = isFileEditorItem(activeItem) ? fileItemPath(activeItem) : '';
  if (activeItemPath) return activeItemPath;
  if (activeFile) return activeFile;
  if (fileExplorerSelectionLead) return fileExplorerSelectionLead;
  return '';
}

function commandPaletteDropActionKind(path) {
  const row = path ? document.querySelector?.(`.file-tree-row[data-path="${cssEscape(path)}"]`) : null;
  return row?.dataset?.kind === 'dir' ? 'dir' : 'file';
}

function commandPaletteDropActionSession() {
  const target = currentSessionActionTarget();
  if (isTmuxSession(target)) return target;
  if (isTmuxSession(focusedTerminal)) return focusedTerminal;
  return activeSessions.find(item => isTmuxSession(item)) || '';
}

function commandPaletteDropActionItems() {
  const path = commandPaletteDropActionPath();
  if (!path) return [];
  const kind = commandPaletteDropActionKind(path);
  const paths = [path];
  const category = fileDropCategory(path, kind);
  const session = commandPaletteDropActionSession();
  const agentKind = session ? sessionAgentKind(session) : '';
  return dropActionsFor(category, agentKind, paths.length, {pathInserted: false, includeShellForAgents: true})
    .map(action => {
      const needsTerminal = action.kind !== 'server';
      return {
        group: 'File Actions',
        category: 'command',
        label: action.label,
        detail: compactHomePath(path),
        key: `drop-action:${action.id}:${path}`,
        keybinding: 'Enter',
        disabled: needsTerminal && !session,
        searchFields: ['do something with file', 'file action', action.label, path, compactHomePath(path)],
        run: () => runDropAction(action, dropActionContext(action, paths, category, agentKind, {session, kind, pathInserted: false})),
      };
    });
}

function fileQuickOpenRootForFile(path) {
  const normalized = normalizeDirectoryPath(path || '');
  if (!normalized || normalized === '/') return '';
  const rawGitRoot = openFiles.get(normalized)?.gitRoot || '';
  const gitRoot = rawGitRoot ? normalizeDirectoryPath(rawGitRoot) : '';
  if (gitRoot && pathIsInsideDirectory(normalized, gitRoot)) return gitRoot;
  return dirnameOf(normalized);
}

function fileQuickOpenPathAliasSegment(query) {
  const text = String(query || '').trim();
  if (!text || text.startsWith('/') || text.startsWith('~')) return '';
  const slash = text.indexOf('/');
  return slash > 0 ? text.slice(0, slash) : '';
}

function fileQuickOpenRootMatchesPathAlias(root, query) {
  const alias = fuzzyCanonicalPrefixText(fileQuickOpenPathAliasSegment(query));
  if (alias.length < 2) return false;
  return fuzzyCanonicalPrefixText(basenameOf(root)).startsWith(alias);
}

function fileQuickOpenDoitNeedle(query = commandPaletteSearchQuery()) {
  const needle = String(fileQuickOpenSearchText(query) || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
  return needle.startsWith('doit') && needle.length >= 4 ? needle : '';
}

function fileQuickOpenRepoFamilyBase() {
  return normalizeDirectoryPath(fileQuickOpenRoot || repoRoot || '');
}

function fileQuickOpenRepoFamilyPrefix() {
  const name = basenameOf(fileQuickOpenRepoFamilyBase());
  return String(name || '').replace(/[._-]dev\d*$/i, '');
}

function fileQuickOpenPathInRepoFamily(path) {
  const base = fileQuickOpenRepoFamilyBase();
  const parent = dirnameOf(base);
  const prefix = fileQuickOpenRepoFamilyPrefix();
  const normalized = normalizeDirectoryPath(path || '');
  if (!parent || !prefix || !normalized || !pathIsInsideDirectory(normalized, parent)) return false;
  const top = childPathParts(parent, normalized)[0] || '';
  return top === prefix || top.startsWith(`${prefix}.`) || top.startsWith(`${prefix}-`);
}

function fileQuickOpenDoitCandidateAllowed(file, query = commandPaletteSearchQuery()) {
  const needle = fileQuickOpenDoitNeedle(query);
  if (!needle) return true;
  const path = String(file?.path || '');
  const name = String(file?.name || basenameOf(path));
  const stem = name.includes('.') ? name.slice(0, name.lastIndexOf('.')) : name;
  const nameText = name.toLowerCase().replace(/[^a-z0-9]+/g, '');
  const stemText = stem.toLowerCase().replace(/[^a-z0-9]+/g, '');
  if (!(stemText.startsWith(needle) || nameText.startsWith(needle) || stemText.includes(needle) || nameText.includes(needle))) return false;
  return fileQuickOpenPathInRepoFamily(path);
}

function fileQuickOpenExtraRootsForSearchQuery(query) {
  const familyRoot = fileQuickOpenDoitNeedle(query) ? dirnameOf(fileQuickOpenRepoFamilyBase()) : '';
  const aliasRoots = [repoRoot, fileQuickOpenRootForFile(activeFile)]
    .map(normalizeDirectoryPath)
    .filter(root => root && root !== '/' && fileQuickOpenRootMatchesPathAlias(root, query));
  return compactNestedPaths([...aliasRoots, familyRoot]
    .map(normalizeDirectoryPath)
    .filter(root => root && root !== '/'));
}

function fileQuickOpenRootForSearch() {
  const activeItem = currentActiveMenuItem();
  const activePath = isFileEditorItem(activeItem) ? fileItemPath(activeItem) : '';
  const activeFileRoot = fileQuickOpenRootForFile(activePath);
  if (activeFileRoot) return activeFileRoot;
  const target = currentSessionActionTarget();
  const gitRoot = isTmuxSession(target) ? sessionTranscriptInfo(target).gitRoot : '';
  if (gitRoot) return normalizeDirectoryPath(gitRoot);
  const activeTmux = activeTmuxDirectoryPath(target);
  if (activeTmux) return activeTmux;
  const fallbackFileRoot = fileQuickOpenRootForFile(activeFile);
  if (fallbackFileRoot) return fallbackFileRoot;
  if (fileExplorerRoot) return fileExplorerRoot;
  return repoRoot || homePath || '/';
}

function fileQuickOpenRootsForSearch(root = fileQuickOpenRoot || fileQuickOpenRootForSearch(), query = commandPaletteSearchQuery()) {
  return fileExplorerIndexedSearchRoots(root, fileQuickOpenExtraRootsForSearchQuery(query));
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

function fileQuickOpenSearchText(query = commandPaletteQuery) {
  return fileQuickOpenQueryParts(query).query.replace(/[:.]+$/g, '').trim();
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

function fileQuickOpenTargetSlot() {
  return focusedActivationSlot();
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
    const targetSlot = fileQuickOpenTargetSlot();
    openedItem = await openFileInEditor(path, {name: label}, targetSlot
      ? {targetSlot, userInitiated: true}
      : {userInitiated: true});
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

function cursorStyleFileReference(path, options = {}) {
  const fullPath = String(path || '');
  if (!fullPath || options.kind === 'dir') return null;
  if (IMAGE_EXTENSIONS.has(fileExtensionOf(fullPath))) {
    const index = Math.max(1, Math.floor(Number(options.imageIndex || 1)));
    return {label: `[Image #${index}]`, detail: shellQuote(fullPath)};
  }
  return null;
}

function fileQuickOpenItem(path, options = {}) {
  const label = basenameOf(path);
  const isDir = options.kind === 'dir';
  const cursorReference = cursorStyleFileReference(path, options);
  const detail = cursorReference?.detail || compactHomePath(path);
  return {
    group: options.group || 'Files',
    category: 'file',
    label: cursorReference?.label || (isDir ? `${label}/` : label),
    detail,
    key: `file:${path}`,
    path,
    mtime: commandPaletteNumericTime(options.mtime || options.mtimeNs || 0),
    iconText: isDir ? '▸' : fileIconFor(label),
    keybinding: isDir ? 'Enter' : `${appShortcutText('Enter')} split`,
    searchFields: [label, path, detail, options.relativePath || '', cursorReference?.label || ''],
    sortBonus: Number(options.sortBonus || 0),
    run: () => isDir ? descendFileQuickOpenDirectory(path) : openFileQuickOpenPath(path, {line: fileQuickOpenQueryParts().line}),
    splitRun: isDir ? null : () => openFileQuickOpenPath(path, {line: fileQuickOpenQueryParts().line, split: true}),
  };
}

function recentFileQuickOpenItems() {
  return Array.from(openFiles.keys()).reverse()
    .filter(path => !commandPaletteFilePathKnownMissing(path))
    .map((path, index) => fileQuickOpenItem(path, {
      group: t('palette.group.recent'),
      mtime: openFiles.get(path)?.mtime || 0,
      sortBonus: 120 - index,
    }));
}

function fileQuickOpenExactPathItem() {
  const pathQuery = fileQuickOpenPathQuery();
  if (!pathQuery.active || !pathQuery.filter) return null;
  const path = normalizeDirectoryPath(joinDirectoryPath(pathQuery.directory || '/', pathQuery.filter));
  const name = basenameOf(path);
  if (!name || !fileExtensionOf(name)) return null;
  return {
    ...fileQuickOpenItem(path, {group: t('palette.group.files')}),
    label: IMAGE_EXTENSIONS.has(fileExtensionOf(path)) ? `Open image ${name}` : `Open ${name}`,
    detail: compactHomePath(path),
    key: `exact-file:${path}`,
    keybinding: appShortcutText('Enter'),
    pinTop: true,
    searchFields: [name, path],
  };
}

// C15 follow-up: while a directory PATH is typed in cmd-P, offer "Open <dir> in Finder" as the pinned
// top row, so Enter (the default highlight) opens that directory in the Finder/File Explorer — while
// arrowing down to a listed entry opens a file or descends into a subfolder. The target is the directory
// the input points at: the listed dir, or its `filter` subfolder when the typed name is an exact subdir.
function fileQuickOpenOpenFolderItem() {
  const pathQuery = fileQuickOpenPathQuery();
  if (!pathQuery.active) return null;
  let target = normalizeDirectoryPath(pathQuery.directory);
  if (pathQuery.filter) {
    const filter = String(pathQuery.filter).toLowerCase();
    const match = fileQuickOpenCandidates.find(entry => entry.kind === 'dir' && String(entry.name || '').toLowerCase() === filter);
    if (match?.path) target = match.path;
  }
  if (!target) return null;
  return {
    group: t('palette.group.files'),
    label: t('palette.openFolder', {name: fileExplorerLabel()}),
    detail: compactHomePath(target),
    key: `open-folder:${target}`,
    iconText: '▸',
    keybinding: appShortcutText('Enter'),
    pinTop: true,
    searchFields: ['open folder', target],
    run: async () => { await openFileExplorerPane(); await openFileExplorerAt(target); },
  };
}

function fileQuickOpenStrictExternalIndexedQuery(query = commandPaletteSearchQuery()) {
  const text = String(query || '').trim();
  if (!text || text.includes('/') || text.startsWith('~')) return false;
  return /[.]/.test(text);
}

function fileQuickOpenExternalIndexedMatchAllowed(file, query = commandPaletteSearchQuery()) {
  if (!fileQuickOpenStrictExternalIndexedQuery(query)) return true;
  const path = String(file?.path || '');
  const basename = String(file?.name || basenameOf(path));
  const stem = basename.includes('.') ? basename.slice(0, basename.lastIndexOf('.')) : basename;
  const needle = fuzzyCanonicalPrefixText(query);
  if (!needle) return true;
  return [basename, stem]
    .map(fuzzyCanonicalPrefixText)
    .some(value => value && (value.startsWith(needle) || value.includes(needle)));
}

function fileQuickOpenItems() {
  const seen = new Set();
  const items = [];
  let imageIndex = 0;
  const add = item => {
    const path = item.searchFields?.[1] || item.detail || item.label;
    if (!path || seen.has(path)) return;
    seen.add(path);
    items.push(item);
  };
  const exactPath = fileQuickOpenExactPathItem();
  if (exactPath) add(exactPath);
  const openFolder = fileQuickOpenOpenFolderItem();
  if (openFolder) items.push(openFolder);
  recentFileQuickOpenItems().forEach(add);
  for (const file of fileQuickOpenCandidates) {
    const path = file.path || '';
    if (!path) continue;
    if (!fileQuickOpenDoitCandidateAllowed(file)) continue;
    // Only an entry that ACTUALLY came from an indexed-root search carries an indexed_root; path-mode
    // listing entries have none. Guard the empty case — normalizeStoredFileExplorerIndexedDir('') returns
    // '/' (empty -> root), which used to mislabel every path-mode directory row as "Indexed /".
    const indexedRoot = file.indexed_root ? normalizeStoredFileExplorerIndexedDir(file.indexed_root) : '';
    const baseRoot = normalizeStoredFileExplorerIndexedDir(fileQuickOpenRoot || '');
    const externalIndexed = Boolean(indexedRoot && indexedRoot !== baseRoot);
    if (externalIndexed && !fileQuickOpenExternalIndexedMatchAllowed(file)) continue;
    const isImage = (file.kind || 'file') !== 'dir' && IMAGE_EXTENSIONS.has(fileExtensionOf(path));
    if (isImage) imageIndex += 1;
    add(fileQuickOpenItem(path, {
      group: externalIndexed ? `Indexed ${compactHomePath(indexedRoot)}` : 'Files',
      relativePath: file.relative_path || file.name || '',
      kind: file.kind || 'file',
      imageIndex,
      mtime: file.mtime || 0,
      mtimeNs: file.mtime_ns || 0,
      sortBonus: (externalIndexed ? -250 : 250) + (file.uploaded === true ? -500 : 0),
    }));
  }
  if (fileQuickOpenError) {
    items.push({
      group: t('palette.group.files'),
      label: t('search.failed'),
      detail: fileQuickOpenError,
      searchFields: ['search failed', fileQuickOpenError],
      disabled: true,
      run: null,
    });
  }
  if (fileQuickOpenLoading) {
    items.push({
      group: t('palette.group.files'),
      label: t('palette.searchingFiles'),
      detail: compactHomePath(fileQuickOpenRoot),
      loading: true,
      searchFields: ['searching'],
      disabled: true,
      run: null,
    });
  }
  return items.map(item => ({...item, category: 'file'}));
}

function commandPaletteFilePath(item) {
  return item?.path || item?.searchFields?.[1] || fileItemPath(item?.targetItem || '') || item?.detail || item?.label || '';
}

function commandPaletteMergedItems() {
  const openTabPaths = new Set(commandPaletteVisibleTabItems().map(fileItemPath).filter(Boolean));
  const dedupedFileItems = fileQuickOpenItems().filter(item => !openTabPaths.has(commandPaletteFilePath(item)));
  return [...dedupedFileItems, ...commandPaletteCommandItems()];
}

function commandPaletteCandidateItems(mode = commandPaletteMode, rawQuery = commandPaletteQuery) {
  // Unified palette provider. `mode` is a priority flag: Cmd-P and Shift-Cmd-P draw from the same
  // candidate universe and differ only in searchRankWeights.domainPrior.
  const parts = fileQuickOpenQueryParts(rawQuery);
  if (parts.commandMode) return commandPaletteCommandItems();                          // `>` = actions only
  if (parts.symbolMode || fileQuickOpenPathQuery(rawQuery).active) return fileQuickOpenItems(); // `@` / path = files only
  // Lean on open: an empty box shows only the priority's home category (no command dump — #7).
  if (!commandPaletteSearchQuery(rawQuery, mode)) {
    return mode === 'files' ? fileQuickOpenItems() : commandPaletteCommandItems();
  }
  // On type: BOTH entry points search the full corpus; the priority sort floats the home category up.
  // S2: a file that is already an open tab shows ONCE — as the deduped Tabs row (which carries
  // both edit + preview chips). Drop its Recent/Files duplicate so it isn't listed twice. Only here, in
  // the merged view; the files-only and empty-box modes above have no Tabs rows, so Recent stays intact.
  return commandPaletteMergedItems();
}

function commandPaletteItems() {
  return commandPaletteCandidateItems(commandPaletteMode, commandPaletteQuery);
}

function commandPaletteRankItems(items, query, options = {}) {
  const ranked = (items || [])
    .map((item, sortIndex) => ({...item, score: commandPaletteItemScore(item, query, options), sortIndex}))
    .filter(item => Number.isFinite(item.score))
    .sort((left, right) => query
      ? right.score - left.score || left.group.localeCompare(right.group) || left.label.localeCompare(right.label)
      : right.score - left.score || left.sortIndex - right.sortIndex);
  return commandPaletteMixFirstScreenResults(ranked, query, options);
}

function commandPaletteMixDomains(options = {}) {
  const surface = commandPaletteSurface(options);
  if (surface === 'files') return {primary: 'file', secondary: 'pane'};
  if (surface === 'command') return {primary: 'pane', secondary: 'file'};
  return null;
}

function commandPaletteMixFirstScreenResults(ranked, query, options = {}) {
  if (!String(query || '').trim()) return ranked;
  const domains = commandPaletteMixDomains(options);
  if (!domains) return ranked;
  const windowSize = Math.max(0, Number(searchRankWeights.mixWindow || 0));
  const maxSecondary = Math.max(0, Number(searchRankWeights.mixSecondarySlots || 0));
  if (!windowSize || !maxSecondary) return ranked;
  const hasPrimary = ranked.slice(0, windowSize).some(item => commandPaletteItemDomain(item) === domains.primary);
  const secondaryCandidates = ranked
    .map((item, index) => ({item, index}))
    .filter(row => commandPaletteItemDomain(row.item) === domains.secondary)
    .slice(0, maxSecondary);
  if (!hasPrimary || !secondaryCandidates.length) return ranked;
  const selected = [];
  for (const [secondaryIndex, row] of secondaryCandidates.entries()) {
    const target = Math.min(
      windowSize - 1,
      searchRankWeights.mixFirstSecondaryIndex + secondaryIndex * searchRankWeights.mixSecondaryStep
    );
    if (row.index > target) selected.push({item: row.item, target});
  }
  if (!selected.length) return ranked;
  const selectedItems = new Set(selected.map(row => row.item));
  const result = ranked.filter(item => !selectedItems.has(item));
  for (const row of selected) {
    result.splice(Math.min(row.target, result.length), 0, row.item);
  }
  return result;
}

function commandPaletteMatches(item, query) {
  return Number.isFinite(commandPaletteItemScore(item, query));
}

function commandPaletteSurface(options = {}) {
  return options.surface === 'files' || options.surface === 'command' ? options.surface : commandPaletteMode;
}

function commandPaletteItemDomain(item) {
  if (item?.category === 'file') return 'file';
  if (item?.category === 'pane') return 'pane';
  if (item?.category === 'setting') return 'setting';
  return 'command';
}

function commandPaletteDomainPrior(item, options = {}) {
  const surface = commandPaletteSurface(options);
  const domain = commandPaletteItemDomain(item);
  return searchRankWeights.domainPrior[surface]?.[domain] || 0;
}

function commandPaletteNowSeconds(options = {}) {
  const explicit = Number(options.nowSeconds);
  return Number.isFinite(explicit) && explicit > 0 ? explicit : Date.now() / 1000;
}

function commandPaletteItemMtime(item) {
  return commandPaletteNumericTime(item?.mtime || item?.mtime_ns || 0);
}

function commandPaletteRecencyBonus(item, options = {}) {
  const timestamp = commandPaletteItemMtime(item);
  if (!timestamp) return 0;
  const age = Math.max(0, commandPaletteNowSeconds(options) - timestamp);
  return searchRankWeights.recencyCap * Math.pow(0.5, age / searchRankWeights.recencyHalfLifeSeconds);
}

function commandPaletteFocusedRepoRoots(options = {}) {
  if (Array.isArray(options.focusedRepoRoots)) {
    return options.focusedRepoRoots.map(normalizeDirectoryPath).filter(Boolean);
  }
  const focused = currentSessionActionTarget() || focusedPanelItem || focusedTerminal;
  const info = isTmuxSession(focused) ? sessionTranscriptInfo(focused) : null;
  const roots = [
    info?.gitRoot || '',
    focusedRepoRootForSync(info?.selectedPath || info?.gitCwd || '', sessionFilesRepoRoots()),
  ].map(normalizeDirectoryPath).filter(Boolean);
  return Array.from(new Set(roots));
}

function commandPaletteRepoAffinityBonus(item, options = {}) {
  if (item?.category !== 'file') return 0;
  const path = normalizeDirectoryPath(commandPaletteFilePath(item));
  if (!path) return 0;
  return commandPaletteFocusedRepoRoots(options).some(root => pathIsInsideDirectory(path, root))
    ? searchRankWeights.repoAffinity
    : 0;
}

function commandPaletteItemScore(item, query, options = {}) {
  if (item.disabled) return 0;
  // C15 follow-up: the path-mode "Open this folder in Finder" row always sorts to the top (and survives
  // any filter text) so it is the default Enter action while a directory path is typed.
  if (item.pinTop) return 1e9;
  const bonus = commandPaletteDomainPrior(item, options)
    + Number(item.sortBonus || 0)
    + commandPaletteRecentBonus(item)
    + commandPaletteFinderAliasBonus(item, query, options)
    + commandPaletteFileNameBonus(item, query)
    + commandPaletteRecencyBonus(item, options)
    + commandPaletteRepoAffinityBonus(item, options);
  if (!String(query || '').trim()) return bonus;
  const base = fuzzySearchScore(query, item.searchFields || [item.label, item.detail, item.group]);
  return Number.isFinite(base) ? base + bonus : base;
}

function commandPaletteFileNameBonus(item, query) {
  if (item?.category !== 'file') return 0;
  const tokens = String(query || '').trim().split(/\s+/).filter(Boolean);
  if (!tokens.length) return 0;
  const filename = String(item.searchFields?.[0] || item.label || '');
  const canonicalName = fuzzyCanonicalPrefixText(filename);
  if (!canonicalName) return 0;
  let bonus = 0;
  for (const token of tokens) {
    const canonicalToken = fuzzyCanonicalPrefixText(token);
    if (!canonicalToken) continue;
    if (canonicalName.startsWith(canonicalToken)) bonus += searchRankWeights.fileNamePrefix;
    else if (canonicalName.includes(canonicalToken)) bonus += searchRankWeights.fileNameContains;
    else if (Number.isFinite(fuzzySubsequenceScore(token, filename))) bonus += searchRankWeights.fileNameSubsequence;
  }
  return bonus;
}

function commandPaletteFinderAliasBonus(item, query, options = {}) {
  if (item?.targetItem !== fileExplorerItemId) return 0;
  const text = String(query || '').trim().toLowerCase().replace(/\s+/g, ' ');
  if (text.length < 3) return 0;
  const aliases = ['file', 'files', 'finder', 'file explorer'];
  if (!aliases.some(alias => alias === text || alias.startsWith(text))) return 0;
  return commandPaletteSurface(options) === 'files'
    ? searchRankWeights.finderAliasFilesMode
    : searchRankWeights.finderAlias;
}

function commandPaletteRecentBonus(item) {
  const key = item.key || `${item.group}:${item.label}`;
  const sequence = commandPaletteRecentKeys.get(key);
  return sequence ? searchRankWeights.recentSelectionBase + sequence : 0;
}

function rememberCommandPaletteItem(item) {
  if (!item || item.disabled) return;
  const key = item.key || `${item.group}:${item.label}`;
  setLimitedMapEntry(commandPaletteRecentKeys, key, ++commandPaletteRecentSequence, commandPaletteRecentKeyLimit);
}

function commandPaletteEffectiveMode() {
  return commandPaletteMode === 'files' && !fileQuickOpenQueryParts().commandMode ? 'files' : 'command';
}

function commandPaletteSearchQuery(query = commandPaletteQuery, mode = commandPaletteMode) {
  if (mode !== 'files') return String(query || '').trim();
  const parts = fileQuickOpenQueryParts(query);
  if (parts.commandMode) return String(query || '').replace(/^>\s*/, '').trim();
  if (parts.symbolMode) return String(query || '').replace(/^@\s*/, '').trim();
  const pathQuery = fileQuickOpenPathQuery(query);
  if (pathQuery.active) return pathQuery.filter;
  return fileQuickOpenSearchText(query);
}

function commandPalettePlaceholder() {
  // Identical for Cmd-P and Cmd-Shift-P — they differ only in result ordering, not in labels.
  const q = commandPaletteQuery.trim();
  if (q.startsWith('>')) return t('palette.placeholderCommand');
  if (q.startsWith('@')) return t('palette.symbolUnavailable');
  return t('palette.placeholderWithFiles');
}

function commandPaletteLabel() {
  // Identical aria label for both entry points.
  return t('palette.quickOpen');
}

function commandPaletteEmptyText() {
  if (fileQuickOpenLoading) return t('search.searching');
  if (commandPaletteQuery.trim().startsWith('@')) return t('palette.symbolUnavailable');
  return t('palette.noMatches');
}

function commandPaletteStatusText() {
  return fileQuickOpenLoading ? t('palette.searchingFiles') : '';
}

function commandPaletteLoadingTextHtml(text) {
  return textWithMovingEllipsisHtml(text, 'command-palette-loading-dots');
}

function commandPaletteStatusHtml() {
  return fileQuickOpenLoading ? commandPaletteLoadingTextHtml(commandPaletteStatusText()) : '';
}

function commandPaletteItemLabelHtml(item, query) {
  if (item?.loading === true) return commandPaletteLoadingTextHtml(item.label);
  return fuzzyHighlightHtml(query, item.label);
}

function ensureCommandPalette() {
  if (commandPaletteNode) return commandPaletteNode;
  const node = document.createElement('div');
  node.className = 'command-palette';
  node.hidden = true;
  node.innerHTML = `
    <div class="command-palette-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('palette.aria'))}">
      <input type="search" class="command-palette-input" placeholder="${esc(t('palette.placeholder'))}" aria-label="${esc(t('palette.placeholder'))}">
      <div class="command-palette-status" aria-live="polite" hidden></div>
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
    // Both entry points fetch files on type so a typed query can blend files into either ordering.
    scheduleFileQuickOpenSearch();
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
    // follow-up: a click on a view chip jumps to THAT view (edit/preview/diff) and closes the
    // palette, instead of the row's default (focus the editor view).
    const chip = event.target.closest('[data-view-item]');
    if (chip && node.contains(chip)) {
      const viewItem = chip.dataset.viewItem;
      closeCommandPalette();
      if (viewItem) selectSession(viewItem, {userInitiated: true});
      return;
    }
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
  const status = node.querySelector('.command-palette-status');
  const results = node.querySelector('.command-palette-results');
  const query = commandPaletteSearchQuery();
  dialog?.setAttribute('aria-label', commandPaletteLabel());
  if (input) {
    input.placeholder = commandPalettePlaceholder();
    input.setAttribute('aria-label', commandPaletteLabel());
    input.setAttribute('aria-busy', fileQuickOpenLoading ? 'true' : 'false');
  }
  if (status) {
    const text = commandPaletteStatusText();
    const html = commandPaletteStatusHtml();
    status.hidden = !html;
    status.setAttribute('aria-label', text);
    status.innerHTML = html;
  }
  commandPaletteItemsCache = commandPaletteRankItems(commandPaletteItems(), query).slice(0, 60);
  commandPaletteIndex = Math.min(commandPaletteIndex, Math.max(0, commandPaletteItemsCache.length - 1));
  if (!commandPaletteItemsCache.length) {
    results.innerHTML = `<div class="command-palette-empty">${esc(commandPaletteEmptyText())}</div>`;
    return;
  }
  results.innerHTML = commandPaletteItemsCache.map((item, index) => `
    <button type="button" class="command-palette-row${index === commandPaletteIndex ? ' active' : ''}" data-command-index="${index}" role="option" aria-selected="${index === commandPaletteIndex ? 'true' : 'false'}"${item.disabled ? ' disabled' : ''}>
      <span class="command-palette-group">${esc(item.group)}</span>
      <span class="command-palette-main"><span class="command-palette-title">${item.iconText ? `<span class="command-palette-file-icon" aria-hidden="true">${esc(item.iconText)}</span>` : ''}<span class="command-palette-label">${commandPaletteItemLabelHtml(item, query)}</span>${(item.viewModes && item.viewModes.length) ? `<span class="command-palette-views">${item.viewModes.map(v => `<span class="command-palette-view-chip" role="button" tabindex="-1" data-view-item="${esc(v.item)}" data-view-mode="${esc(v.mode)}" title="${esc(t('palette.openView', {view: v.label}))}">${esc(v.label)}</span>`).join('')}</span>` : ''}</span><span class="command-palette-detail">${fuzzyHighlightHtml(query, item.detail || '')}</span></span>
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
  // Reset file-search state for BOTH entry points so a typed query can blend files in either mode.
  fileQuickOpenRoot = fileQuickOpenRootForSearch();
  fileQuickOpenCandidates = [];
  fileQuickOpenLoading = false;
  fileQuickOpenError = '';
  // Only Cmd-P (files priority) shows files on an empty box, so only it searches immediately;
  // Cmd-Shift-P fetches files on the first keystroke (via the input handler).
  if (commandPaletteMode === 'files') scheduleFileQuickOpenSearch({immediate: true});
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
    const q = commandPaletteQuery.trim();
    if (!q.startsWith('>') && !q.startsWith('@')) refreshFileQuickOpenCandidates(commandPaletteQuery);
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

// Fold TRUE duplicate file-search hits: same path, same resolved realpath (symlink / overlay
// overlap), or same basename+size (content-mirror copies kept in two repos). Genuinely-different
// same-name files (different size) both survive; unknown-size hits dedupe by path/realpath only.
function dedupeFileSearchResults(files) {
  const seen = new Set();
  const out = [];
  for (const file of Array.isArray(files) ? files : []) {
    const path = file?.path || '';
    if (!path) continue;
    const keys = [`p:${path}`, `r:${file.realpath || path}`];
    if (Number.isFinite(Number(file.size))) keys.push(`n:${basenameOf(path)}|${Number(file.size)}`);
    if (keys.some(key => seen.has(key))) continue;
    keys.forEach(key => seen.add(key));
    out.push(file);
  }
  return out;
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
      const searchRoots = fileQuickOpenRootsForSearch(root, commandPaletteSearchQuery(query));
      const results = await Promise.all(searchRoots.map(async searchRoot => {
        try {
          const normalizedRoot = normalizeStoredFileExplorerIndexedDir(searchRoot);
          const recursive = normalizedRoot && fileExplorerDirectoryIsIndexed(normalizedRoot) ? '&recursive=1' : '';
          const response = await apiFetch(`/api/fs/search?root=${encodeURIComponent(searchRoot)}&query=${encodeURIComponent(commandPaletteSearchQuery(query))}&limit=500${recursive}`, fetchOptions);
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.error || response.status);
          return {ok: true, root: normalizeStoredFileExplorerIndexedDir(payload.root || payload.root_realpath || searchRoot) || searchRoot, files: Array.isArray(payload.files) ? payload.files : []};
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
      fileQuickOpenCandidates = dedupeFileSearchResults(
        successful.flatMap(result => result.files.map(file => ({...file, indexed_root: result.root}))),
      );
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

function shouldNotifyTransitionKey(key) {
  const configured = initialSetting('notifications.notify_transitions', ['needs-input', 'needs-approval', 'blocked']);
  const transitions = Array.isArray(configured) ? configured : ['needs-input', 'needs-approval', 'blocked'];
  return transitions.includes(key);
}

function shouldNotifyState(state) {
  return shouldNotifyTransitionKey(state.key);
}

function sendBrowserNotification(title, options = {}) {
  const notification = new Notification(title, options);
  notification.onclick = () => {
    window.focus();
    if (options.session) selectSession(options.session, {userInitiated: true});
    // a watched-PR notification opens the PR (no session to focus); safe blank-target open.
    else if (options.url) {
      try { window.open(options.url, '_blank', 'noopener,noreferrer'); } catch (_) {}
    }
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
          <button type="button" class="toast-keep" data-toast-keep aria-label="${esc(options.keepLabel || t('toast.keepAlert'))}">${esc(t('toast.keep'))}</button>
          <button type="button" class="toast-close" data-toast-close aria-label="${esc(options.closeLabel || t('toast.closeAlert'))}">X</button>
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
    text: t('common.more', {count: hidden.length}),
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
  // remove the toast when its countdown bar finishes — honor options.countdownMs (the
  // reconnect toast animates over that, not the fixed toastDurationMs) so the bar and removal align.
  attentionAlertTimers.set(id, window.setTimeout(() => removeAttentionAlert(id), options.countdownMs || toastDurationMs));
  return node;
}

function startupHelperCatalog() {
  return [
    {title: t('startupHelper.tip.dragFiles.title'), lines: [t('startupHelper.tip.dragFiles.body')]},
    {title: t('startupHelper.tip.imageDrag.title'), lines: [t('startupHelper.tip.imageDrag.body')]},
    {title: t('startupHelper.tip.yoagentQuestions.title'), lines: [t('startupHelper.tip.yoagentQuestions.body')]},
    {title: t('startupHelper.tip.yoloAutoApprove.title'), lines: [t('startupHelper.tip.yoloAutoApprove.body')]},
    {title: t('startupHelper.tip.yoloRules.title'), lines: [t('startupHelper.tip.yoloRules.body')]},
    {title: t('startupHelper.tip.diffView.title'), lines: [t('startupHelper.tip.diffView.body')]},
    {title: t('startupHelper.tip.finderSync.title'), lines: [t('startupHelper.tip.finderSync.body')]},
    {title: t('startupHelper.tip.finderReload.title'), lines: [t('startupHelper.tip.finderReload.body')]},
    {title: t('startupHelper.tip.editorDiff.title'), lines: [t('startupHelper.tip.editorDiff.body')]},
    {title: t('startupHelper.tip.notifications.title'), lines: [t('startupHelper.tip.notifications.body')]},
    {title: t('startupHelper.tip.watchedPrs.title'), lines: [t('startupHelper.tip.watchedPrs.body')]},
    {title: t('startupHelper.tip.quickOpen.title'), lines: [t('startupHelper.tip.quickOpen.body')]},
    {title: t('startupHelper.tip.markdownPreview.title'), lines: [t('startupHelper.tip.markdownPreview.body')]},
    {title: t('startupHelper.tip.largeUpload.title'), lines: [t('startupHelper.tip.largeUpload.body')]},
  ];
}

function readStartupHelperIndex(count) {
  const parsed = Number(storageGet(startupHelperIndexStorageKey, '0'));
  if (!Number.isFinite(parsed) || count <= 0) return 0;
  return Math.max(0, Math.floor(parsed)) % count;
}

function writeStartupHelperIndex(index) {
  storageSet(startupHelperIndexStorageKey, Math.max(0, Math.floor(Number(index) || 0)));
}

function startupHelperWrappedIndex(index, count) {
  if (!Number.isFinite(Number(index)) || count <= 0) return 0;
  return ((Math.floor(Number(index)) % count) + count) % count;
}

function startupHelperAction(label, onClick, options = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  if (options.className) button.className = options.className;
  if (options.title) button.title = options.title;
  if (options.ariaLabel) button.setAttribute('aria-label', options.ariaLabel);
  button.addEventListener('click', event => {
    event.stopPropagation();
    onClick?.();
  });
  return button;
}

function startupHelperNavigationGroup(index, total, showRelativeTip) {
  const group = document.createElement('span');
  group.className = 'startup-helper-nav';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', 'Tip navigation');
  group.append(
    startupHelperAction('<', () => showRelativeTip(-1), {
      className: 'startup-helper-nav-button',
      title: 'Previous tip',
      ariaLabel: 'Previous tip',
    }),
    startupHelperAction('>', () => showRelativeTip(1), {
      className: 'startup-helper-nav-button',
      title: t('startupHelper.action.next'),
      ariaLabel: t('startupHelper.action.next'),
    }),
  );
  return group;
}

function startupHelperPromptAction(title) {
  const text = String(title || '').trim();
  if (!text) return t('startupHelper.defaultAction');
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function startupHelperPromptTitle(index, total, tip) {
  return t('startupHelper.titleTemplate', {
    index: index + 1,
    total,
    action: startupHelperPromptAction(tip?.title),
  });
}

function closeStartupHelperToast(node) {
  removeAttentionAlert(Number(node?.dataset?.alertId || 0));
}

function showStartupHelperTip(options = {}) {
  if (readOnlyMode || !startupHelpersEnabled) return null;
  const tips = startupHelperCatalog();
  if (!tips.length) return null;
  const index = readStartupHelperIndex(tips.length);
  const tip = tips[index];
  writeStartupHelperIndex((index + 1) % tips.length);
  let node = null;
  const showRelativeTip = delta => {
    closeStartupHelperToast(node);
    writeStartupHelperIndex(startupHelperWrappedIndex(index + delta, tips.length));
    showStartupHelperTip({manual: true});
  };
  const navAction = startupHelperNavigationGroup(index, tips.length, showRelativeTip);
  const offAction = startupHelperAction(t('startupHelper.action.offForever'), () => {
    startupHelpersEnabled = false;
    closeStartupHelperToast(node);
    saveSettingsPatch(settingPatch('general.startup_tips', false))
      .then(() => { statusEl.textContent = t('startupHelper.status.disabled'); })
      .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error})); refreshSettings({force: true}); });
  });
  node = showToast(startupHelperPromptTitle(index, tips.length, tip), tip.lines, {
    className: 'attention-alert toast startup-helper-toast',
    container: displayToastContainer(focusedPanelItem),
    actions: [navAction, offAction],
    countdownMs: 45000,
  });
  if (node) node.dataset.toastKind = 'startup-helper';
  return node;
}

function scheduleStartupHelperTip() {
  if (readOnlyMode || !startupHelpersEnabled) return;
  if (location.protocol === 'file:') return;
  window.setTimeout(() => {
    showStartupHelperTip();
  }, 1400);
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

function compactNotificationTitle(scope, message) {
  const suffix = String(message || '').trim();
  const label = String(scope || '').trim();
  if (!label) return suffix ? `YOLOmux ${suffix}` : 'YOLOmux';
  return suffix ? `YOLOmux[${label}] ${suffix}` : `YOLOmux[${label}]`;
}

function sessionNotificationTitle(session, state) {
  return compactNotificationTitle(sessionLabel(session), state?.label || '');
}

function hostNotificationTitle(message) {
  return compactNotificationTitle(serverHostname, message);
}

function showAttentionAlert(session, state) {
  const node = showToast(
    sessionNotificationTitle(session, state),
    state.reason,
    {
      container: displayToastContainer(session),
      onClick: () => selectSession(session, {userInitiated: true}),
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
  const panel = document.getElementById(panelDomId(session));
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
  showToast(t('notify.testTitle', {host: serverHostname}), t('notify.testBody'), {
    container: displayToastContainer(focusedPanelItem),
  });
  if (!notificationsEnabled || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(t('notify.testTitle', {host: serverHostname}), {
      body: t('notify.browserTestBody'),
      tag: `yolomux:test:${Date.now()}`,
    });
    postEvent(null, 'notification_test_sent', 'notification test sent', {hostname: serverHostname});
  } catch (error) {
    statusErr(localizedHtml('status.notificationFailed', {error}));
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
    sendBrowserNotification(sessionNotificationTitle(session, state), {
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

// a stable snapshot of the watched-PR status dimensions we diff for notifications.
function watchedPrStatusSnapshot(pr) {
  return {
    merged: pr?.merged === true || pullRequestStatusLabel(pr).toLowerCase() === 'merged',
    ci: String(pr?.checks?.state || '').toLowerCase(),
    review: String(pr?.review_decision || '').toUpperCase(),
  };
}

// Pure transition detector: which notifiable transitions fired between two status snapshots. Merged
// (→ merged), CI flipped into failing, review decision changed. No previous snapshot → no transition
// (first sighting only records a baseline, so a load with already-merged/failing PRs does not storm).
function watchedPrTransitionKeys(prev, next) {
  const keys = [];
  if (!prev || !next) return keys;
  if (!prev.merged && next.merged) keys.push('pr-merged');
  if (prev.ci !== 'failing' && next.ci === 'failing') keys.push('pr-ci-failing');
  if (prev.review !== next.review && next.review) keys.push('pr-review');
  return keys;
}

// Diff each watched PR's new status against the last-seen one and notify on a transition.
function notifyWatchedPrTransitions(prs) {
  for (const pr of Array.isArray(prs) ? prs : []) {
    const ref = String(pr?.ref || (pr?.number ? `#${pr.number}` : ''));
    if (!ref) continue;
    const next = watchedPrStatusSnapshot(pr);
    const prev = watchedPrLastStatus.get(ref);
    watchedPrLastStatus.set(ref, next);
    for (const key of watchedPrTransitionKeys(prev, next)) {
      let message;
      if (key === 'pr-merged') message = t('notify.pr.merged', {ref});
      else if (key === 'pr-ci-failing') message = t('notify.pr.ciFailing', {ref});
      else message = t('notify.pr.review', {ref, decision: pullRequestApprovalLabel(next.review) || next.review});
      maybeNotifyWatchedPr(ref, key, message, pr.url);
    }
  }
}

// Fire a watched-PR transition through the shared notification channel: an in-page toast (clicks open
// the PR) + a browser Notification, gated by notificationsEnabled + notify_transitions, deduped and
// throttled by notifications.throttle_seconds via the shared notificationLastSent map.
function maybeNotifyWatchedPr(ref, key, message, url) {
  if (!notificationsEnabled) return;
  if (!shouldNotifyTransitionKey(key)) return;
  const signature = `watched-pr:${ref}:${key}`;
  const now = Date.now();
  const throttleMs = Math.max(0, (Number(initialSetting('notifications.throttle_seconds', 60)) || 0) * 1000);
  const lastSent = notificationLastSent.get(signature) || 0;
  if (now - lastSent < throttleMs) return;
  setLimitedMapEntry(notificationLastSent, signature, now, notificationLastSentLimit);
  showToast(message, [ref], {
    onClick: () => { try { window.open(url, '_blank', 'noopener,noreferrer'); } catch (_) {} },
  });
  postEvent(null, 'watched_pr_alert', message, {ref, transition: key});
  if (!('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    sendBrowserNotification(hostNotificationTitle(message), {
      body: ref,
      tag: signature,
      renotify: true,
      url,
    });
    postEvent(null, 'watched_pr_notification', message, {ref, transition: key});
  } catch (error) {
    postEvent(null, 'watched_pr_notification_error', `notification failed: ${error}`, {ref, transition: key});
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
  // detect a same-shape change (reorder/activate/move/replace) so we can skip the full
  // topbar + grid teardown. Compute the shape signature before and after the slot reassignment.
  const prevShape = layoutShapeSignature(layoutSlots);
  layoutSlots = normalizeLayoutSlots(nextSlots);
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  requestLayoutRender({
    previousActive,
    prevShape,
    nextShape: layoutShapeSignature(layoutSlots),
    options: {prune: options.prune},
    reason: 'applyLayoutSlots',
    forceFull: options.forceFull === true,
  });
  for (const session of activeSessions.filter(isTmuxSession)) ensureTerminalRunning(session);
  // do NOT re-poll the server on a pure client-side layout change. refreshTranscripts()
  // fires 3..(3+N) network round-trips and a second full render wave gated behind their latency —
  // the bulk of the "moving a tab takes several seconds" delay. Freshness is already covered by the
  // metadata interval (50_editor_settings_runtime.js), and the session-changing mutations
  // (create/rename/kill, 70_layout_actions.js) call refreshTranscripts() at their own sites.
  renderAutoApproveButtons();
  updatePanelInactiveOverlays();
  if (autoFocusEnabled && options.focusSession && activeSessions.includes(options.focusSession)) {
    setTimeout(() => focusPanel(options.focusSession), 80);
  } else if (options.message && activeSessions.length) {
    statusEl.textContent = options.message;
  } else {
    updateStatus();
  }
  if (clientPushCanSupplyData() && typeof syncServerWatchRoots === 'function') syncServerWatchRoots();
}

function layoutRenderRequest(request = {}) {
  return {
    previousActive: Array.isArray(request.previousActive) ? request.previousActive.slice() : [],
    prevShape: String(request.prevShape || ''),
    nextShape: String(request.nextShape || ''),
    options: request.options || {},
    reason: request.reason || '',
    forceFull: request.forceFull === true,
  };
}

function mergePendingLayoutRender(current, next) {
  if (!current) return next;
  return layoutRenderRequest({
    previousActive: current.previousActive,
    prevShape: current.prevShape,
    nextShape: next.nextShape || current.nextShape,
    options: {...current.options, ...next.options},
    reason: [current.reason, next.reason].filter(Boolean).join('+'),
    forceFull: current.forceFull || next.forceFull,
  });
}

function layoutRenderCanUseCheap(request) {
  return !request.forceFull
    && request.prevShape === request.nextShape
    && grid.querySelector('.drop-slot[data-slot]');
}

function performLayoutRender(request = {}) {
  const renderRequest = layoutRenderRequest(request);
  const previousActive = renderRequest.previousActive;
  if (layoutRenderCanUseCheap(renderRequest)) {
    // Cheap path: the tree shape is unchanged. Swap only the slots whose active item changed and
    // reconcile the (already keyed) tab strips — no innerHTML='', no topbar rebuild.
    syncActivePanelsInPlace();
    renderPaneTabStrips();
    syncPanelVisibility(previousActive);
    return;
  }
  renderSessionButtons();
  renderPanels(previousActive, {prune: renderRequest.options.prune});
}

function requestLayoutRender(request = {}) {
  const renderRequest = layoutRenderRequest(request);
  if (dragSession != null) {
    pendingLayoutRender = mergePendingLayoutRender(pendingLayoutRender, renderRequest);
    return;
  }
  performLayoutRender(renderRequest);
}

function flushPendingLayoutRender(reason = 'drag-flush') {
  const renderRequest = pendingLayoutRender;
  pendingLayoutRender = null;
  if (renderRequest) requestLayoutRender({...renderRequest, reason});
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
