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
  return JSON.stringify(normalizeLayoutSlotsForViewport(cloneLayoutSlots(slots)));
}

function layoutSlotsExactSignature(slots = layoutSlots) {
  return JSON.stringify(cloneLayoutSlots(slots));
}

function compactCurrentLayoutSlots(options = {}) {
  const normalized = normalizeLayoutSlotsForViewport(layoutSlots);
  // layoutSlotsSignature intentionally normalizes both inputs for renderer comparisons. That makes
  // it unable to detect a newly narrowed viewport: the old multi-pane layout is normalized before
  // comparison too. Responsive compaction instead compares the actual stored layout to the shared
  // viewport-normalized result.
  if (layoutSlotsExactSignature(normalized) === layoutSlotsExactSignature(layoutSlots)) return false;
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
  return legacyLayoutModeValues.includes(text) ? text : defaultLayoutMode;
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
  if (options.preserveMissingFileExplorer === true && !itemInLayout(fileExplorerItemId, normalized)) return normalized;
  return normalizeFileExplorerDock(normalized);
}

function normalizeTreeLayout(value, options = {}) {
  const next = emptyLayoutSlots();
  const seen = new Set();
  next[layoutTreeKey] = normalizeLayoutNode(value[layoutTreeKey], value, next, seen, options);
  return compactLayoutSlots(next, options);
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

function compactLayoutSlots(slots, options = {}) {
  const next = emptyLayoutSlots();
  for (const key of layoutSlotKeys(slots)) next[key] = paneStateForLayoutSlot(key, slots);
  next[layoutTreeKey] = compactLayoutNode(slots[layoutTreeKey], next, options);
  const keys = layoutSlotKeys(next);
  if (keys.length && keys.every(key => paneIsPlaceholder(key, next))) return emptyPlaceholderLayoutSlots(keys[0] || 'left');
  return next[layoutTreeKey] ? next : emptyPlaceholderLayoutSlots();
}

function compactLayoutNode(node, slots, options = {}) {
  return compactLayoutNodeInfo(node, slots, options)?.node || null;
}

function compactLayoutNodeInfo(node, slots, options = {}) {
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
  const children = (node.children || []).map(child => compactLayoutNodeInfo(child, slots, options)).filter(Boolean);
  if (!children.length) return null;
  if (children.length === 1) return children[0];
  const hasFileExplorer = children.some(child => child.containsFileExplorer);
  const hasDirectFileExplorerLeaf = children.some(child => child.directFileExplorerLeaf);
  // Most mutations prune a now-empty peer. Directional tab split and temporary Fill/Restore are
  // deliberately different: their empty peer is a user-visible placement target, not stale debris.
  const kept = options.preservePlaceholderSlots === true || (direction === 'row' && hasDirectFileExplorerLeaf)
    ? children
    : children.filter(child => !child.placeholderOnly);
  const compacted = kept.length ? kept : [children[0]];
  if (compacted.length < 2) return compacted[0];
  const pct = splitPercentForCompactedLayoutNode(direction, node.pct, compacted, {
    preserveMissingFileExplorer: options.preserveMissingFileExplorer === true,
  });
  const nextNode = splitNode(direction, compacted[0].node, compacted[1].node, pct);
  return {
    node: nextNode,
    containsFileExplorer: compacted.some(child => child.containsFileExplorer),
    directFileExplorerLeaf: false,
    placeholderOnly: compacted.every(child => child.placeholderOnly),
  };
}

function splitPercentForCompactedLayoutNode(direction, pct, children, options = {}) {
  const value = splitPercent(pct);
  if (direction === 'row'
    && options.preserveMissingFileExplorer !== true
    && value >= fileExplorerSplitPercent
    && value < minNonFileExplorerSplitPercent
    && !children?.[0]?.containsFileExplorer) {
    return defaultSplitPercent;
  }
  return value;
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
  if (fileExplorerUsesNormalTabMovement()) return false;
  return layoutHasHorizontalContentBeforeItem(slots?.[layoutTreeKey], fileExplorerItemId, slots);
}

function normalizeFileExplorerDock(slots) {
  if (fileExplorerUsesNormalTabMovement()) return slots;
  let next = slots;
  const finderSlot = slotForItem(fileExplorerItemId, slots);
  const finderSharesPane = Boolean(finderSlot && paneTabs(finderSlot, slots).some(item => item !== fileExplorerItemId));
  if (tabletUsesDesktopLayout() && finderSharesPane) {
    // Rotating a compact tablet back to wide landscape restores the same dedicated Finder pane
    // that a laptop uses; otherwise its portrait tab would remain mixed into terminal tabs.
    next = layoutWithFileExplorerDockedLeft(slots);
  } else if (!itemInLayout(fileExplorerItemId, slots) && !fileExplorerClosedByUser() && paneItems(slots).length) {
    next = layoutWithFileExplorerDockedLeft(slots);
  } else if (fileExplorerNeedsLeftDock(slots)) {
    next = layoutWithFileExplorerDockedLeft(slots);
  }
  return layoutWithFileExplorerPeerPane(next);
}

function soleFileExplorerSlot(slots) {
  const keys = layoutSlotKeys(slots).filter(slot => paneHasLayoutContent(slot, slots));
  if (keys.length !== 1) return null;
  const slot = keys[0];
  const tabs = paneTabs(slot, slots);
  return tabs.length === 1 && tabs[0] === fileExplorerItemId ? slot : null;
}

function layoutWithFileExplorerPeerPane(slots) {
  if (fileExplorerUsesNormalTabMovement()) return slots;
  const finderSlot = soleFileExplorerSlot(slots);
  if (!finderSlot) return slots;
  const next = cloneLayoutSlots(slots);
  const peerSlot = fileExplorerPeerSlot(slots, finderSlot);
  next[peerSlot] = emptyPlaceholderPaneState();
  next[layoutTreeKey] = splitNode('row', leafNode(finderSlot), leafNode(peerSlot), fileExplorerSplitPercent);
  return compactLayoutSlots(next);
}

function fileExplorerPeerSlot(slots, finderSlot) {
  const formerLeaf = layoutLeafSlots(slots?.[layoutTreeKey]).find(slot => slot !== finderSlot && !paneHasLayoutContent(slot, slots));
  if (formerLeaf) return formerLeaf;
  const dormantSlot = Object.keys(slots || {}).find(slot => slot !== layoutTreeKey && slot !== finderSlot && !paneHasLayoutContent(slot, slots));
  return dormantSlot || nextLayoutSlot(slots);
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

function layoutFromParam(raw, tabsRaw = '', options = {}) {
  const text = String(raw || '').trim();
  if (!text) return null;
  if (text.toLowerCase() === 'empty') return emptyPlaceholderLayoutSlots();
  if (text.startsWith(layoutTreeParamPrefix)) return treeLayoutFromParam(text.slice(layoutTreeParamPrefix.length), options);
  if (compactLayoutParamLooksLikeTree(text)) return compactTreeLayoutFromParam(text, tabsRaw, options);
  const namedSlotLayout = namedSlotLayoutFromParam(text, tabsRaw, options);
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

function namedSlotLayoutFromParam(raw, tabsRaw, options = {}) {
  const tabStates = layoutTabStatesFromParam(tabsRaw);
  if (!tabStates.size) return null;
  const slotNames = String(raw || '')
    .split(',')
    .map(value => layoutSlotName(safeDecodeURIComponent(value.trim())))
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
  const normalized = normalizeLayoutSlots(next, options);
  return layoutHasRestorableContent(normalized) ? normalized : null;
}

function treeLayoutFromParam(raw, options = {}) {
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
    const normalized = normalizeLayoutSlots(next, options);
    return layoutHasRestorableContent(normalized) ? normalized : null;
  } catch (_) {
    return null;
  }
}

function compactLayoutParamLooksLikeTree(text) {
  return /^(row|col|column)(?:@\d+(?:\.\d+)?)?\(/.test(text);
}

function compactTreeLayoutFromParam(raw, tabsRaw, options = {}) {
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
  const normalized = normalizeLayoutSlots(next, options);
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
  const slot = layoutSlotName(safeDecodeURIComponent(name));
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
    const slot = layoutSlotName(safeDecodeURIComponent(part.slice(0, separator)));
    if (!slot) continue;
    const tabs = [];
    let active = null;
    let placeholder = false;
    for (const rawItem of part.slice(separator + 1).split(',')) {
      let token = rawItem.trim();
      if (!token) continue;
      const activeToken = token.endsWith('*');
      if (activeToken) token = token.slice(0, -1);
      const decoded = safeDecodeURIComponent(token);
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

// Legacy compatibility: old share snapshots may still carry the merged-pane sub-tab marker. New
// yoagent/yosup URL tokens resolve directly to the standalone YO!agent tab through TAB_TYPES.
function maybeAdoptYoagentDeepLink(params) {
  const raw = [params.get('tabs') || '', params.get('sessions') || '', params.get('active') || ''].join(',');
  const referencesYoagent = raw
    .split(/[,:|]/)
    .map(value => value.trim())
    .some(value => tabTypeForParam(value)?.key === 'yoagent');
  if (referencesYoagent) {
    infoPanelSubTab = 'yoagent';
    writeStoredInfoSubTab('yoagent');
  }
}

function maybeAdoptFileExplorerModeDeepLink(params) {
  const mode = fileExplorerModeFromUrlParam(params.get('finder'));
  if (!mode) return;
  fileExplorerMode = mode;
  writeStoredFileExplorerMode(fileExplorerMode);
}

function layoutUrlStateFromParams(params) {
  const raw = params?.get?.('state') || '';
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (_) {
    return null;
  }
}

function layoutUrlStringArray(values, limit = 1000) {
  if (!Array.isArray(values)) return [];
  const out = [];
  for (const value of values) {
    const text = String(value || '').trim();
    if (!text) continue;
    out.push(text);
    if (out.length >= limit) break;
  }
  return out;
}

function layoutUrlReplaceSet(target, values, limit = 1000) {
  if (!target?.clear) return;
  target.clear();
  for (const value of layoutUrlStringArray(values, limit)) target.add(value);
}

function applyLayoutUrlFinderSeed(finder = {}) {
  if (!finder || typeof finder !== 'object') return;
  if ('mode' in finder) fileExplorerMode = normalizeFileExplorerMode(finder.mode);
  if ('rootMode' in finder) fileExplorerRootMode = finder.rootMode === 'fixed' ? 'fixed' : 'sync';
  if ('root' in finder) {
    const root = String(finder.root || '').trim();
    if (root) fileExplorerRoot = normalizeDirectoryPath(expandUserPath(root));
  }
  if ('session' in finder && isTmuxSession(String(finder.session || '').trim())) {
    fileExplorerChangesSelectedSession = String(finder.session || '').trim();
    setExplicitPaneFocusItem(fileExplorerChangesSelectedSession, {allowInactive: true, renderMenu: false});
  }
  if ('showHidden' in finder) fileExplorerShowHidden = finder.showHidden === true;
  if ('treeDateMode' in finder) fileExplorerTreeDateMode = normalizeFileExplorerTreeDateMode(finder.treeDateMode);
  if ('treeSortMode' in finder) fileExplorerTreeSortMode = ['az', 'za', 'newest', 'oldest'].includes(finder.treeSortMode) ? finder.treeSortMode : 'az';
  if ('sessionFilesSortMode' in finder) sessionFilesSortMode = normalizeSessionFilesSortMode(finder.sessionFilesSortMode);
  if ('diffRefFrom' in finder) diffRefFrom = cleanDiffRef(finder.diffRefFrom, diffRefFrom || 'HEAD');
  if ('diffRefTo' in finder) diffRefTo = cleanDiffRef(finder.diffRefTo, diffRefTo || 'current');
  if ('diffRefsByRepo' in finder) {
    diffRefsByRepo = typeof shareCleanDiffRefsByRepo === 'function'
      ? shareCleanDiffRefsByRepo(finder.diffRefsByRepo)
      : {};
  }
  if ('expanded' in finder) layoutUrlReplaceSet(fileExplorerExpanded, finder.expanded);
  if ('selectedPaths' in finder) layoutUrlReplaceSet(fileExplorerSelectedPaths, finder.selectedPaths);
  if ('selectionAnchor' in finder) fileExplorerSelectionAnchor = String(finder.selectionAnchor || '');
  if ('selectionLead' in finder) fileExplorerSelectionLead = String(finder.selectionLead || '');
  if ('changesFolderCollapsed' in finder) layoutUrlReplaceSet(changesFolderCollapsed, finder.changesFolderCollapsed);
  if ('changesRepoCollapsed' in finder) layoutUrlReplaceSet(changesRepoCollapsed, finder.changesRepoCollapsed);
  if ('tabberCollapsed' in finder) layoutUrlReplaceSet(fileExplorerTabberCollapsed, finder.tabberCollapsed);
}

function applyEditorStateFields(editor = {}, {applyModeEntry = null} = {}) {
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
  for (const entry of (Array.isArray(editor.modes) ? editor.modes : [])) applyModeEntry?.(entry);
}

function applyLayoutUrlPreferencesSeed(preferences = {}, options = {}) {
  if (!preferences || typeof preferences !== 'object') return;
  if ('searchText' in preferences) preferencesSearchText = String(preferences.searchText || '');
  if (Array.isArray(preferences.collapsedSections)) {
    const sections = options.includeLocalizedSections === true ? preferenceSections() : [];
    setCollapsedPreferenceSections(layoutUrlStringArray(preferences.collapsedSections, 200), {sections});
  }
  if ('resetConfirmVisible' in preferences) preferencesResetConfirmVisible = preferences.resetConfirmVisible === true;
}

function applyLayoutUrlStateSeed(state) {
  if (!state || typeof state !== 'object') return false;
  applyLayoutUrlFinderSeed(state.finder || {});
  applyEditorStateFields(state.editor || {}, {applyModeEntry: typeof shareApplyEditorModeEntry === 'function' ? shareApplyEditorModeEntry : null});
  applyLayoutUrlPreferencesSeed(state.preferences || {});
  layoutUrlState.pending = state;
  layoutUrlState.applied = false;
  return true;
}

function maybeAdoptLayoutStateDeepLink(params) {
  const state = layoutUrlStateFromParams(params);
  if (!state) return;
  applyLayoutUrlStateSeed(state);
}

function layoutUrlCaptureCurrentPaneState() {
  for (const item of paneItems(layoutSlots)) {
    capturePaneViewStateForItemIfPresent(item);
  }
}

function layoutUrlStateSnapshot() {
  layoutUrlCaptureCurrentPaneState();
  const state = {v: 1};
  if (paneItems(layoutSlots).some(isFileExplorerItem) && typeof shareFinderStateSnapshot === 'function') {
    state.finder = shareFinderStateSnapshot({compact: false});
  }
  if (typeof shareEditorStateSnapshot === 'function') {
    const editor = shareEditorStateSnapshot({compact: false});
    if (editor?.modes?.length) state.editor = editor;
  }
  if (paneItems(layoutSlots).includes(prefsItemId) && typeof sharePreferencesStateSnapshot === 'function') {
    state.preferences = sharePreferencesStateSnapshot({compact: false});
  }
  if (typeof shareScrollStateSnapshot === 'function') {
    const scroll = shareScrollStateSnapshot();
    if (scroll.length) state.scroll = scroll;
  }
  return Object.keys(state).length > 1 ? state : null;
}

function layoutUrlStateParamValue() {
  const state = layoutUrlStateSnapshot();
  if (!state) return '';
  try {
    return readableParamComponent(JSON.stringify(state));
  } catch (_) {
    return '';
  }
}

function applyPendingLayoutUrlState() {
  if (layoutUrlState.applied) return false;
  const state = layoutUrlState.pending;
  if (!state || typeof state !== 'object') {
    layoutUrlState.applied = true;
    return false;
  }
  applyLayoutUrlPreferencesSeed(state.preferences || {}, {includeLocalizedSections: true});
  if (Array.isArray(state.scroll) && typeof applyShareScrollSnapshot === 'function') {
    applyShareScrollSnapshot(state.scroll);
    requestAnimationFrame(() => applyShareScrollSnapshot(state.scroll));
    setTimeout(() => applyShareScrollSnapshot(state.scroll), 0);
  }
  layoutUrlState.applied = true;
  return true;
}

function schedulePendingLayoutUrlStateApply() {
  if (!layoutUrlState.pending || layoutUrlState.applied) return;
  requestAnimationFrame(applyPendingLayoutUrlState);
  setTimeout(applyPendingLayoutUrlState, 0);
}

function scheduleLayoutUrlStateRefresh() {
  if (shareViewMode) return;
  if (layoutUrlState.refreshTimer) return;
  const timer = setTimeout(() => {
    if (layoutUrlState.refreshTimer !== timer) return;
    layoutUrlState.refreshTimer = null;
    updateActiveSessionParam();
  }, 250);
  layoutUrlState.refreshTimer = timer;
}

function refreshLayoutUrlStateSoon() {
  scheduleLayoutUrlStateRefresh();
}

function shareBootstrapLayoutParams() {
  if (!shareViewMode || !shareBootstrap) return null;
  const params = new URLSearchParams();
  const layout = String(shareBootstrap.layout || '').trim();
  const tabs = String(shareBootstrap.tabs || '').trim();
  const sharedSessions = Array.isArray(shareBootstrap.sessions)
    ? shareBootstrap.sessions.map(session => String(session || '').trim()).filter(Boolean)
    : [];
  if (layout) params.set('layout', layout);
  if (tabs) params.set('tabs', tabs);
  if (sharedSessions.length) params.set('sessions', sharedSessions.join(','));
  return layout || tabs || sharedSessions.length ? params : null;
}

function mobileRecentTmuxItems() {
  const source = Array.isArray(bootstrap.recentSessions) && bootstrap.recentSessions.length
    ? bootstrap.recentSessions
    : visibleSessions;
  const items = [];
  for (const value of source) {
    const item = resolveLayoutItem(value);
    if (isTmuxSession(item) && !items.includes(item)) items.push(item);
    if (items.length >= mobileSinglePaneTabLimit) break;
  }
  return items;
}

function mobileSinglePaneLayoutSlots(slots = null, options = {}) {
  const requested = slots && typeof slots === 'object' ? paneItems(slots) : [];
  // A URL can restore several desktop panes into one phone pane. Its visible active tabs carry
  // the only durable selection signal at boot, so retain the final one instead of discarding it
  // and always selecting the first recent session.
  const restoredActive = requested.length ? activePaneItems(slots).at(-1) : null;
  const explicitFocus = resolveLayoutItem(options.focusSession);
  const preferred = explicitFocus || resolveLayoutItem(restoredActive);
  // Seed a fresh phone view from the three most-recent tmux sessions, but once it has a concrete
  // layout, treat that list as the user's selection. Otherwise closing a tab immediately adds it
  // back on the next one-column normalization.
  // A created or explicitly selected tab must win the three-tab phone budget. Without putting it
  // first, launching another terminal successfully creates tmux state but normalization retains
  // older tabs and makes the launch look like a no-op.
  const candidates = requested.length
    ? (explicitFocus ? [explicitFocus, ...requested] : requested)
    : [preferred, ...mobileRecentTmuxItems()];
  const tmuxItems = [];
  for (const item of candidates) {
    if (isTmuxSession(item) && !tmuxItems.includes(item)) tmuxItems.push(item);
    if (tmuxItems.length >= mobileSinglePaneTabLimit) break;
  }
  const supplementary = [];
  for (const item of candidates) {
    if (isLayoutItem(item) && !isTmuxSession(item) && !supplementary.includes(item)) supplementary.push(item);
  }
  const items = [...tmuxItems, ...supplementary];
  const active = items.includes(preferred) ? preferred : items[0] || null;
  return layoutSlotsForItems(items, 'single', {active, emptySlot: 'left'});
}

function narrowSingleColumnLayoutSlots(slots = null, options = {}) {
  if (mobileSinglePaneMode()) return mobileSinglePaneLayoutSlots(slots, options);
  const requested = slots && typeof slots === 'object' ? paneItems(slots) : [];
  const preferred = resolveLayoutItem(options.focusSession);
  const visibleActive = activePaneItems(slots || layoutSlots);
  const active = [
    preferred,
    ...visibleActive.filter(item => !isFileExplorerItem(item)),
    ...visibleActive,
    ...requested,
  ]
    .find(item => requested.includes(item)) || requested[0] || null;
  // Finder is intentionally retained in this tab list. On narrow tablet screens it is a normal
  // tab in the sole column, rather than a reserved left-hand pane.
  return layoutSlotsForItems(requested, 'single', {active, emptySlot: 'left'});
}

function normalizeLayoutSlotsForViewport(value, options = {}) {
  const source = narrowSingleColumnMode()
    ? narrowSingleColumnLayoutSlots(value, {focusSession: options.focusSession})
    : value;
  return normalizeLayoutSlots(source, {
    ...options,
    preserveMissingFileExplorer: narrowSingleColumnMode() || options.preserveMissingFileExplorer === true,
  });
}

function availableLayoutModes() {
  return narrowSingleColumnMode() ? ['single'] : layoutModeValues;
}

function initialLayoutSlots() {
  const shareParams = shareBootstrapLayoutParams();
  const params = shareParams || new URLSearchParams(location.search);
  if (!shareParams) maybeAdoptFileExplorerModeDeepLink(params);
  if (!shareParams) maybeAdoptLayoutStateDeepLink(params);
  maybeAdoptYoagentDeepLink(params);
  const layoutFromUrl = layoutFromParam(params.get('layout') || '', params.get('tabs') || '', {
    preserveMissingFileExplorer: shareParams !== null,
  });
  if (layoutFromUrl) return normalizeLayoutSlotsForViewport(layoutFromUrl);
  if (mobileSinglePaneMode()) return mobileSinglePaneLayoutSlots();
  const raw = params.get('sessions') || params.get('active') || '';
  const selected = [];
  for (const part of raw.split(',')) {
    const value = part.trim();
    if (!value) continue;
    const item = resolveLayoutItem(value);
    if (isLayoutItem(item) && !selected.includes(item)) selected.push(item);
  }
  if (selected.length) {
    const selectedLayout = layoutFromSessionList(selected);
    return narrowSingleColumnMode() ? narrowSingleColumnLayoutSlots(selectedLayout) : selectedLayout;
  }
  return defaultLayoutSlots();
}

function defaultLayoutSlots() {
  if (mobileSinglePaneMode()) return mobileSinglePaneLayoutSlots();
  const sorted = visibleSessions.slice().sort((left, right) => String(left).localeCompare(String(right)));
  if (fileExplorerUsesNormalTabMovement()) {
    const items = fileExplorerClosedByUser() ? sorted : [fileExplorerItemId, ...sorted];
    const regular = layoutSlotsForItems(items, configuredDefaultLayoutMode(), {active: sorted[0] || fileExplorerItemId});
    return narrowSingleColumnMode() ? narrowSingleColumnLayoutSlots(regular) : regular;
  }
  const standard = layoutWithFileExplorerDockedLeft(layoutSlotsForItems(sorted, configuredDefaultLayoutMode()), {
    preservePlaceholders: false,
  });
  return narrowSingleColumnMode() ? narrowSingleColumnLayoutSlots(standard) : standard;
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

const pendingTmuxSessionGraceMs = 30000;

function pruneExpiredPendingTmuxSessions(now = Date.now()) {
  let changed = false;
  for (const [session, expiresAt] of pendingTmuxSessions.entries()) {
    if (Number(expiresAt) <= now) {
      pendingTmuxSessions.delete(session);
      changed = true;
    }
  }
  return changed;
}

function pendingTmuxSessionNames() {
  pruneExpiredPendingTmuxSessions();
  return Array.from(pendingTmuxSessions.keys());
}

function isPendingTmuxSession(session) {
  const key = String(session || '').trim();
  if (!key) return false;
  pruneExpiredPendingTmuxSessions();
  return pendingTmuxSessions.has(key);
}

function markPendingTmuxSession(session) {
  const key = String(session || '').trim();
  if (!key) return '';
  pendingTmuxSessions.set(key, Date.now() + pendingTmuxSessionGraceMs);
  layoutItems = computeLayoutItems();
  return key;
}

function clearPendingTmuxSession(session) {
  const key = String(session || '').trim();
  if (!key || !pendingTmuxSessions.delete(key)) return false;
  layoutItems = computeLayoutItems();
  return true;
}

function clearConfirmedPendingTmuxSessions(nextSessions = []) {
  let changed = false;
  for (const session of nextSessions) {
    if (pendingTmuxSessions.delete(session)) changed = true;
  }
  return changed;
}

function sessionOrderIncludingPending(nextSessions = []) {
  const next = normalizedSessionOrder(nextSessions) || [];
  for (const session of pendingTmuxSessionNames()) {
    if (!next.includes(session)) next.push(session);
  }
  return next;
}

function computeLayoutItems() {
  const items = [];
  for (const item of [...virtualTabItems(), ...openFileEditorItems(), ...visibleSessions, ...pendingTmuxSessionNames()]) {
    if (!items.includes(item)) items.push(item);
  }
  return items;
}

function isTmuxSession(item) {
  return sessions.includes(item) || pendingTmuxSessions.has(item);
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
  '.search-history-scroll',
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
    refreshLayoutUrlStateSoon();
  });
}

function restorePaneViewState(item, panel) {
  if (!item || !panel) return;
  restorePaneElementScrollState(panel, paneViewState.get(item));
  if (isFileEditorItem(item) && typeof restoreFileEditorPanelViewState === 'function') {
    restoreFileEditorPanelViewState(item, panel);
  }
}

function registerFileEditorLayoutItem(path, options = {}) {
  if (!path || !path.startsWith('/')) return null;
  const optionItem = options.item && fileItemPath(options.item) === path ? options.item : '';
  const item = optionItem || fileEditorItemFor(path);
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

function registerDynamicVirtualLayoutItem(item) {
  const text = String(item || '');
  if (!tabTypeForItem(text)?.prefix) return null;
  dynamicVirtualLayoutItems.add(text);
  layoutItems = computeLayoutItems();
  return text;
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
  if (type?.prefix === chatMediaItemPrefix) return chatMediaUrlForItem(text) ? registerDynamicVirtualLayoutItem(text) : null;
  if (type?.key === 'file-editor') {
    const path = fileItemPath(text);
    return (path && registerFileEditorLayoutItem(path, {item: text})) || text;
  }
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
  const label = Number(sessionShortcutLabel(item));
  return Number.isFinite(label) ? label : Number.MAX_SAFE_INTEGER;
}

function itemParam(item) {
  const type = tabTypeForItem(item);
  if (type) return tabTypeParam(type, item);
  return String(item);
}

const stateDefs = {
  [STATE_KEY.needsApproval]: {hasShort: false, priority: 0, attention: true},
  'yolo-approval': {hasShort: true, priority: 0, attention: false},
  [STATE_KEY.needsInput]: {hasShort: false, priority: 1, attention: true},
  [STATE_KEY.blocked]: {hasShort: true, priority: 2, attention: true},
  disconnected: {hasShort: true, priority: 3, attention: true},
  'tests-running': {hasShort: true, priority: 4, attention: false},
  'ready-review': {hasShort: true, priority: 5, attention: false},
  [STATE_KEY.working]: {hasShort: false, priority: 6, attention: false},
  [STATE_KEY.idle]: {hasShort: false, priority: 7, attention: false},
  done: {hasShort: false, priority: 8, attention: false},
};
const ATTENTION_SCREEN_KEYS = new Set([STATE_KEY.approval, STATE_KEY.needsApproval, STATE_KEY.needsInput]);
const AGENT_WINDOW_ATTENTION_STATES = new Set([...ATTENTION_SCREEN_KEYS, STATE_KEY.interrupted]);
const AGENT_WINDOW_APPROVAL_STATES = new Set([STATE_KEY.approval, STATE_KEY.needsApproval]);

function stateReason(key, params = {}) {
  return t(`state.reason.${key}`, params);
}

function stateDef(key) {
  // #121: resolve the human label through t() on each access so a runtime language switch
  // re-localizes it (stateDefs is frozen at load). Compact badge text is localized too.
  const resolvedKey = stateDefs[key] ? key : STATE_KEY.idle;
  const base = stateDefs[resolvedKey];
  return {...base, label: t(`state.${resolvedKey}`), short: base.hasShort ? t(`state.short.${resolvedKey}`) : ''};
}

function attentionAcknowledgementKey(parts = []) {
  return JSON.stringify((Array.isArray(parts) ? parts : []).map(part => String(part || '')));
}

function attentionAcknowledgementKeySession(key) {
  try {
    const parts = JSON.parse(String(key || ''));
    return Array.isArray(parts) && parts.length > 1 ? String(parts[1] || '') : '';
  } catch (_error) {
    return '';
  }
}

function attentionAcknowledgementRecord(key, create = false) {
  const value = String(key || '');
  if (!value) return null;
  let record = attentionAcknowledgementRecords.get(value) || null;
  if (!record && create) {
    record = {recordedAt: null, timer: null, pending: false};
    attentionAcknowledgementRecords.set(value, record);
  }
  return record;
}

function pruneAttentionAcknowledgementRecords() {
  while (attentionAcknowledgementRecords.size > attentionAcknowledgementRecordLimit) {
    let removed = false;
    for (const [key, record] of attentionAcknowledgementRecords) {
      if (record?.timer !== null || record?.pending === true) continue;
      attentionAcknowledgementRecords.delete(key);
      removed = true;
      break;
    }
    if (!removed) break;
  }
}

function releaseIdleAttentionAcknowledgementRecord(key, record = attentionAcknowledgementRecord(key)) {
  if (!record || record.recordedAt !== null || record.timer !== null || record.pending === true) return false;
  attentionAcknowledgementRecords.delete(String(key || ''));
  return true;
}

function clearSessionAttentionAcknowledgementRecords(session) {
  const value = String(session || '');
  if (!value) return;
  for (const [key, record] of attentionAcknowledgementRecords) {
    if (attentionAcknowledgementKeySession(key) !== value) continue;
    if (record?.timer !== null) clearTimeout(record.timer);
    attentionAcknowledgementRecords.delete(key);
  }
}

function attentionAcknowledgementKeysFromPayload(payload = {}) {
  const keys = payload?.attention_acks?.keys;
  return Array.isArray(keys) ? keys.map(key => String(key || '')).filter(Boolean) : [];
}

function attentionAcknowledgementKeyIsGeneratedWindowKey(key) {
  // The server adds a per-window generation to every generated agent-window key. That generation
  // changes before a genuinely later prompt can reuse a key, so a delayed pre-ack snapshot must
  // not resurrect a red/yellow marker that the shared ledger has already acknowledged.
  const value = String(key || '');
  return value.startsWith('["agent-window",') && value.split(',').length >= 7;
}

function attentionAcknowledgementKeyIsRecorded(key, payload = null) {
  const value = String(key || '');
  if (!value) return false;
  const record = attentionAcknowledgementRecord(value);
  // Legacy/fallback keys have no server-owned generation, so an explicit later false may re-arm
  // them. Generated keys do have one; keeping their shared ledger record prevents a stale cached
  // auto-status response from re-adding the ball after the user has clicked or typed in its pane.
  const explicitlyUnacknowledged = payload?.attention_acknowledged === false || payload?.cooldown_acknowledged === false;
  if (explicitlyUnacknowledged && record?.pending !== true && !attentionAcknowledgementKeyIsGeneratedWindowKey(value)) return false;
  if (record && record.recordedAt !== null) return true;
  if (payload && attentionAcknowledgementKeysFromPayload(payload).includes(value)) return true;
  return false;
}

function recordAttentionAcknowledgementKey(key) {
  const value = String(key || '');
  if (!value) return false;
  const record = attentionAcknowledgementRecord(value, true);
  record.recordedAt = Date.now();
  attentionAcknowledgementRecords.delete(value);
  attentionAcknowledgementRecords.set(value, record);
  pruneAttentionAcknowledgementRecords();
  return true;
}

function applyAttentionAcknowledgementResponse(payload = {}) {
  const keys = Array.isArray(payload?.acknowledged) ? payload.acknowledged.map(key => String(key || '')).filter(Boolean) : [];
  for (const key of keys) recordAttentionAcknowledgementKey(key);
  const changedSessions = new Set();
  for (const [session, current] of autoApproveStates) {
    if (!current || typeof current !== 'object') continue;
    const next = {...current};
    let changed = false;
    if (keys.includes(String(next.prompt_attention_key || next.prompt?.attention_key || '')) && next.prompt_attention_acknowledged !== true) {
      next.prompt_attention_acknowledged = true;
      if (next.prompt && typeof next.prompt === 'object') next.prompt = {...next.prompt, attention_acknowledged: true};
      changed = true;
    }
    if (Array.isArray(next.agent_windows)) {
      next.agent_windows = next.agent_windows.map(agent => {
        if (!agent || typeof agent !== 'object') return agent;
        if (keys.includes(String(agent.attention_key || '')) && agent.attention_acknowledged !== true) {
          changed = true;
          return {...agent, attention_acknowledged: true};
        }
        if (keys.includes(String(agent.cooldown_attention_key || '')) && agent.cooldown_acknowledged !== true) {
          changed = true;
          return {...agent, cooldown_acknowledged: true};
        }
        return agent;
      });
    }
    if (changed) {
      autoApproveStates.set(session, next);
      changedSessions.add(session);
    }
  }
  if (payload?.auto_approve && typeof applyAutoApprovePayload === 'function') {
    applyAutoApprovePayload(payload.auto_approve);
  } else {
    for (const session of changedSessions) refreshTrackedSessionChrome(session);
    if (!changedSessions.size) return false;
    updateTopbarActivityStatus();
    syncTerminalAttentionHighlights();
  }
  return true;
}

async function submitAttentionAcknowledgementKeys(keys) {
  try {
    const payload = await apiFetchJson('/api/attention-ack', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({keys}),
    });
    const acknowledged = (Array.isArray(payload?.acknowledged) ? payload.acknowledged : [])
      .map(key => String(key || ''))
      .filter(key => attentionAcknowledgementRecord(key)?.pending === true);
    applyAttentionAcknowledgementResponse({...payload, acknowledged});
  } catch (error) {
    console.warn('attention acknowledgement failed', error);
  } finally {
    for (const key of keys) {
      const record = attentionAcknowledgementRecord(key);
      if (!record) continue;
      record.pending = false;
      releaseIdleAttentionAcknowledgementRecord(key, record);
    }
    pruneAttentionAcknowledgementRecords();
  }
}

function postAttentionAcknowledgementKeys(keys, options = {}) {
  const unique = Array.from(new Set((Array.isArray(keys) ? keys : [keys]).map(key => String(key || '')).filter(Boolean)));
  if (!unique.length) return false;
  if (options.localOnly === true || typeof apiFetchJson !== 'function') {
    applyAttentionAcknowledgementResponse({acknowledged: unique});
    return true;
  }
  const pending = unique.filter(key => attentionAcknowledgementRecord(key)?.pending !== true && !attentionAcknowledgementKeyIsRecorded(key));
  if (!pending.length) return true;
  for (const key of pending) attentionAcknowledgementRecord(key, true).pending = true;
  pruneAttentionAcknowledgementRecords();
  submitAttentionAcknowledgementKeys(pending);
  return true;
}

function acknowledgeAttentionKeys(keys, options = {}) {
  const unique = Array.from(new Set((Array.isArray(keys) ? keys : [keys]).map(key => String(key || '')).filter(Boolean)));
  if (!unique.length) return false;
  const delayMs = Math.max(0, Number(options.delayMs) || 0);
  if (delayMs > 0) {
    for (const key of unique) {
      const record = attentionAcknowledgementRecord(key, true);
      if (record.timer !== null) continue;
      const timer = setTimeout(() => {
        const current = attentionAcknowledgementRecord(key);
        if (!current || current.timer !== timer) return;
        current.timer = null;
        postAttentionAcknowledgementKeys([key], {...options, delayMs: 0});
        releaseIdleAttentionAcknowledgementRecord(key, current);
      }, delayMs);
      record.timer = timer;
    }
    pruneAttentionAcknowledgementRecords();
    return true;
  }
  return postAttentionAcknowledgementKeys(unique, options);
}

function promptAttentionClearKey(session, signature, payload = null) {
  return String(payload?.prompt_attention_key || payload?.prompt?.attention_key || '') || attentionAcknowledgementKey(['prompt', session, signature]);
}

function promptAttentionPayloadSignature(payload = {}) {
  const prompt = payload?.prompt || {};
  const screen = payload?.screen || {};
  if (prompt.visible === true) {
    return String(prompt.signature || prompt.hash || prompt.question_text || prompt.text || prompt.command || '');
  }
  if (ATTENTION_SCREEN_KEYS.has(String(screen.key || ''))) {
    return String(screen.signature || screen.hash || screen.question_text || screen.text || screen.key || '');
  }
  return '';
}

function promptAttentionIsCleared(session, signature, payload = null) {
  return Boolean(signature) && attentionAcknowledgementKeyIsRecorded(promptAttentionClearKey(session, signature, payload), payload);
}

function promptAttentionExtra(session, payload = {}) {
  const signature = promptAttentionPayloadSignature(payload);
  const clearKey = promptAttentionClearKey(session, signature, payload);
  const cleared = promptAttentionIsCleared(session, signature, payload);
  return {
    session,
    promptSignature: signature,
    promptAttentionKey: clearKey,
    promptAttentionCleared: cleared,
    attention: !cleared,
    showBadge: !cleared,
  };
}

function clearPromptAttentionForSession(session, options = {}) {
  const payload = autoApproveStates.get(session) || {};
  const signature = promptAttentionPayloadSignature(payload);
  if (!signature) return false;
  const key = promptAttentionClearKey(session, signature, payload);
  return acknowledgeAttentionKeys([key], options);
}

function terminalDisconnected(session) {
  if (!activeSessions.includes(session)) return false;
  const item = terminals.get(session);
  if (!item) return false;
  return item.socket?.readyState === WebSocket.CLOSED || item.socket?.readyState === WebSocket.CLOSING;
}

function sessionState(session, info = transcriptMetadataState.payload.sessions?.[session]) {
  if (!isTmuxSession(session)) return {key: STATE_KEY.idle, ...stateDef(STATE_KEY.idle), reason: t('state.notTmux')};
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
  const agentWindowSummary = typeof sessionAgentWindowStatusSummary === 'function'
    ? sessionAgentWindowStatusSummary(session, info, auto)
    : null;
  const hasAttributedAgentWindows = agentWindowSummary?.hasAttributedWindows === true;
  const agentWindowTone = typeof agentWindowStatusToneForItem === 'function'
    ? agentWindowStatusToneForItem(agentWindowSummary?.item)
    : '';
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
  const checksState = pullRequestCiState(pr);

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
    return stateValue(STATE_KEY.blocked, stateReason('yoloBlockedApproval'));
  }
  if (!hasAttributedAgentWindows && approvalPromptVisible && approvalYesSelected) {
    return stateValue(STATE_KEY.needsApproval, approvalPromptText || stateReason('approvalPromptVisible'), promptAttentionExtra(session, auto));
  }
  if (!hasAttributedAgentWindows && approvalPromptVisible) {
    return stateValue(STATE_KEY.needsInput, stateReason('approvalYesNotSelected'), promptAttentionExtra(session, auto));
  }
  if (!hasAttributedAgentWindows && !autoEnabled && /permission|approval|approve|confirm/.test(agentText)) {
    return stateValue(STATE_KEY.needsApproval, approvalPromptText || stateReason('approvalPromptVisible'));
  }
  if (!hasAttributedAgentWindows && screenKey === STATE_KEY.approval) {
    return stateValue(STATE_KEY.needsApproval, screenText || approvalPromptText || stateReason('approvalPromptVisible'), promptAttentionExtra(session, auto));
  }
  if (agentWindowTone === 'attention') {
    const agent = agentWindowSummary.agent;
    const stateKey = AGENT_WINDOW_APPROVAL_STATES.has(String(agent?.state || '')) ? STATE_KEY.needsApproval : STATE_KEY.needsInput;
    const windowLabel = String(agent?.window_label || agent?.window || agent?.kind || '').trim();
    const reasonText = String(agent?.screen_text || '').trim() || (stateKey === STATE_KEY.needsApproval ? stateReason('approvalPromptVisible') : stateReason('agentWaitingInput'));
    return stateValue(stateKey, windowLabel ? `${windowLabel}: ${reasonText}` : reasonText, {
      agentWindowState: 'attention',
      attentionAgent: agent,
      attentionAgentItem: agentWindowSummary.item,
    });
  }
  const tmuxSignalStateForSession = tmuxSignalAgentStateForSession(session);
  if (tmuxSignalStateForSession) {
    return tmuxSignalStateForSession;
  }
  if (!hasAttributedAgentWindows && screenKey === STATE_KEY.working) {
    return stateValue(STATE_KEY.working, screenText || stateReason('agentWorking'));
  }
  if (!hasAttributedAgentWindows && screenKey === STATE_KEY.needsInput) {
    return stateValue(STATE_KEY.needsInput, screenText || stateReason('agentWaitingInput'), promptAttentionExtra(session, auto));
  }
  if (screenKey === 'error') {
    return stateValue(STATE_KEY.blocked, screenText || stateReason('agentScreenFailed'));
  }
  if (agentWindowTone === 'cooldown') {
    const agent = agentWindowSummary.agent;
    const label = String(agent?.window_label || agent?.window || agent?.kind || '').trim();
    return stateValue('cooldown', label ? `${label}: ${stateReason('agentCooldown')}` : stateReason('agentCooldown'), {agentWindowState: 'cooldown'});
  }
  if (/needs input|waiting for input|awaiting input|user input|input required|waiting for user|paused/.test(agentText)) {
    return stateValue(STATE_KEY.needsInput, stateReason('agentWaitingInput'));
  }
  if (agents.some(agent => agentErrorIsBlocking(agent.error)) || /blocked|error|failed|failure|stuck/.test(agentText)) {
    return stateValue(STATE_KEY.blocked, stateReason('agentErrorBlocker'));
  }
  if (/pytest|cargo test|npm test|pnpm test|yarn test|vitest|jest|ctest|go test|python3 -m pytest|python -m pytest|ruff|mypy|pre-commit/.test(paneText)) {
    return stateValue('tests-running', stateReason('testsActive'));
  }
  if (pr?.number && !pr.draft && prStatus !== 'closed' && prStatus !== 'merged' && checksState === 'passing') {
    return stateValue('ready-review', stateReason('prChecksPassing'));
  }
  if (/done|completed|complete|finished|success/.test(agentText)) {
    return stateValue('done', stateReason('agentComplete'));
  }
  if (agents.length || panes.some(pane => pane.active) || terminals.get(session)?.socket?.readyState === WebSocket.OPEN) {
    return stateValue(STATE_KEY.working, stateReason('agentActivePaneDetected'));
  }
  return stateValue(STATE_KEY.idle, stateReason('noActiveAgent'));
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

function autoApproveScreenIsWorking(payload) {
  return typeof sessionAgentWindowScreenIsWorking === 'function'
    ? sessionAgentWindowScreenIsWorking(payload)
    : String(payload?.screen?.key || '') === STATE_KEY.working;
}

function sessionHasWorkingAgentWindow(session, payload = autoApproveStates.get(session), info = transcriptMetadataState.payload.sessions?.[session]) {
  return typeof sessionAgentWindowHasWorkingSignal === 'function'
    ? sessionAgentWindowHasWorkingSignal(session, info, payload)
    : false;
}

function sessionYoloIsWorking(session, payload = autoApproveStates.get(session)) {
  // The shared model resolves both live window rows and the screen-capture proxy before any UI
  // surface asks whether this session is working.
  return sessionHasWorkingAgentWindow(session, payload);
}

function yoloWorkingSessions() {
  return sessions.filter(session => sessionYoloIsWorking(session));
}

function runningAgentCount(counts = globalActivityCounts()) {
  return Math.max(0, Number(counts?.running || 0));
}

function autoApprovePayloadCountsInTopbar(session, payload = autoApproveStates.get(session) || {}) {
  if (payload?.prompt?.visible === true) return true;
  const key = String(payload?.screen?.key || '');
  if (key && key !== STATE_KEY.idle) return true;
  if (autoApproveEnabledForSession(payload)) return true;
  const info = transcriptMetadataState.payload.sessions?.[session];
  return typeof sessionAgentWindowStatusPayloads === 'function' && sessionAgentWindowStatusPayloads(session, info, payload).length > 0;
}

function sessionCountsAsRunning(session, stateKey = '') {
  if (sessionYoloIsWorking(session)) return true;
  return ['tests-running', 'yolo-approval'].includes(String(stateKey || ''));
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

function browserFaviconBadgeCount(counts = globalActivityCounts()) {
  const running = Math.max(0, Number(counts?.running || 0));
  return Math.floor(running);
}

function browserFaviconBadgeLabel(count) {
  const value = Math.max(0, Math.floor(Number(count) || 0));
  return value > 99 ? '99+' : String(value);
}

function tmuxSignalWindows() {
  return Array.isArray(tmuxSignalState?.windows) ? tmuxSignalState.windows : [];
}

const tmuxSignalAgentCommands = new Set(['claude', 'codex']);

function tmuxSignalWindowSession(windowRecord) {
  return String(windowRecord?.session || windowRecord?.session_name || '').trim();
}

function tmuxSignalWindowKey(windowRecord) {
  const key = String(windowRecord?.key || '').trim();
  if (key) return key;
  const session = tmuxSignalWindowSession(windowRecord);
  const index = String(windowRecord?.window_index ?? windowRecord?.window ?? '').trim();
  return session && index ? `${session}:${index}` : '';
}

function tmuxSignalWindowActivityTs(windowRecord) {
  const value = Number(windowRecord?.activity_ts ?? windowRecord?.window_activity ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function tmuxSignalWindowIsRecentlyActive(windowRecord, nowMs = documentTitleNowMs()) {
  const activityTs = tmuxSignalWindowActivityTs(windowRecord);
  if (activityTs <= 0) return false;
  return Math.max(0, nowMs - activityTs * 1000) <= tmuxSignalActivityWindowMs;
}

function tmuxSignalPanes() {
  return tmuxSignalWindows().flatMap(windowRecord => {
    const panes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
    return panes.map(pane => ({...pane, _tmux_window: windowRecord}));
  });
}

function tmuxSignalPaneCommand(pane) {
  return String(pane?.agent || pane?.current_command || pane?.pane_current_command || '').trim().toLowerCase();
}

function tmuxSignalPaneIsAgent(pane) {
  return tmuxSignalAgentCommands.has(tmuxSignalPaneCommand(pane));
}

function tmuxSignalWindowAgentPanes(windowRecord) {
  const panes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
  return panes.filter(tmuxSignalPaneIsAgent);
}

function tmuxSignalWindowHasAgentPane(windowRecord) {
  return tmuxSignalWindowAgentPanes(windowRecord).length > 0;
}

function tmuxSignalPaneTarget(pane) {
  return String(pane?.target || pane?.pane_id || '').trim();
}

function tmuxSignalPaneSession(pane) {
  return String(pane?.session || pane?._tmux_window?.session || '').trim();
}

function tmuxSignalPaneWindowKey(pane) {
  const key = String(pane?.window_key || '').trim();
  if (key) return key;
  const windowKey = tmuxSignalWindowKey(pane?._tmux_window);
  if (windowKey) return windowKey;
  const session = tmuxSignalPaneSession(pane);
  const index = String(pane?.window_index ?? '').trim();
  return session && index ? `${session}:${index}` : '';
}

function tmuxSignalWindowForPane(pane) {
  if (pane?._tmux_window) return pane._tmux_window;
  const key = tmuxSignalPaneWindowKey(pane);
  return tmuxSignalWindows().find(windowRecord => tmuxSignalWindowKey(windowRecord) === key) || null;
}

function tmuxSignalWindowsForSession(session) {
  const sessionText = String(session || '').trim();
  if (!sessionText) return [];
  return tmuxSignalWindows().filter(windowRecord => tmuxSignalWindowSession(windowRecord) === sessionText);
}

function tmuxSignalSnapshotAuthoritativeForSession(session) {
  const sessionText = String(session || '').trim();
  if (!sessionText || tmuxSignalState?.ok !== true || !Array.isArray(tmuxSignalState.windows)) return false;
  const sessionRecords = tmuxSignalState.sessions;
  return Boolean(sessionRecords && typeof sessionRecords === 'object' && sessionRecords[sessionText])
    || tmuxSignalWindowsForSession(sessionText).length > 0;
}

function tmuxSignalWindowForSessionIndex(session, windowIndex) {
  const sessionText = String(session || '').trim();
  const windowText = String(windowIndex ?? '').trim();
  if (!sessionText || !windowText) return null;
  return tmuxSignalWindowsForSession(sessionText).find(windowRecord => String(windowRecord?.window_index ?? '').trim() === windowText) || null;
}

function tmuxSignalSessionActivityTs(session) {
  const sessionText = String(session || '').trim();
  if (!sessionText) return 0;
  const sessionRecord = tmuxSignalState?.sessions?.[sessionText];
  const sessionTs = Number(sessionRecord?.activity_ts || 0);
  const windowTs = tmuxSignalWindowsForSession(sessionText).reduce((max, windowRecord) => Math.max(max, tmuxSignalWindowActivityTs(windowRecord)), 0);
  return Math.max(Number.isFinite(sessionTs) ? sessionTs : 0, windowTs);
}

function tmuxSignalActivePaneForSession(session) {
  const windows = tmuxSignalWindowsForSession(session);
  const override = typeof tmuxWindowActiveIndexOverride === 'function' ? tmuxWindowActiveIndexOverride(session) : undefined;
  const overrideWindow = override !== undefined && override !== '__pending__' ? tmuxSignalWindowForSessionIndex(session, override) : null;
  const windowRecord = overrideWindow || windows.find(item => item?.active === true) || windows[0] || null;
  const panes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
  return panes.find(pane => pane?.active === true) || panes[0] || null;
}

function tmuxSignalPanePathForSession(session) {
  const path = String(tmuxSignalActivePaneForSession(session)?.current_path || '').trim();
  return path ? normalizeDirectoryPath(path) : '';
}

// An alternate-screen pane is a mouse-owning TUI (claude, codex, vim, less): it has its own
// scroll view and its tmux pane carries no scrollback (history_size stays 0), so routing the
// wheel into tmux copy-mode is a dead end. Callers use this to let xterm forward the wheel to
// the app instead of hijacking it.
function sessionPaneIsAlternateScreen(session) {
  return tmuxSignalActivePaneForSession(session)?.alternate_on === true;
}

function tmuxSignalPaneForTarget(target) {
  const value = String(target || '').trim();
  if (!value) return null;
  return tmuxSignalPanes().find(pane => value === tmuxSignalPaneTarget(pane) || value === String(pane?.pane_id || '').trim()) || null;
}

function tmuxSignalAgentPanesForSession(session) {
  const sessionText = String(session || '').trim();
  if (!sessionText) return [];
  return tmuxSignalPanes().filter(pane => tmuxSignalPaneSession(pane) === sessionText && tmuxSignalPaneIsAgent(pane));
}

function tmuxSignalPaneModeLabels(pane) {
  const labels = [];
  if (pane?.in_mode === true || String(pane?.mode || '').trim()) labels.push(String(pane?.mode || 'copy-mode'));
  if (pane?.input_off === true) labels.push(t('common.readOnly'));
  if (pane?.synchronized === true) labels.push(t('tmux.mode.sync'));
  return labels;
}

function tmuxSignalWindowClientNames(windowRecord) {
  const details = Array.isArray(windowRecord?.active_client_details) ? windowRecord.active_client_details : [];
  const names = details.map(client => String(client?.name || client?.client_name || '').trim()).filter(Boolean);
  if (names.length) return names;
  return String(windowRecord?.active_clients_list || '').replace(/,/g, ' ').split(/\s+/).map(item => item.trim()).filter(Boolean);
}

function tmuxSignalWindowPresenceText(windowRecord) {
  const count = Math.max(0, Math.round(Number(windowRecord?.active_clients ?? tmuxSignalWindowClientNames(windowRecord).length) || 0));
  if (count <= 0) return '';
  return tPlural('tmux.viewer', count);
}

function tmuxSignalDeadText(pane) {
  if (pane?.dead !== true) return '';
  const status = pane?.dead_status;
  const signal = pane?.dead_signal;
  if (status !== null && status !== undefined && status !== '') return stateReason('tmuxAgentExitedStatus', {status});
  if (signal !== null && signal !== undefined && signal !== '') return stateReason('tmuxAgentExitedSignal', {signal});
  return stateReason('tmuxAgentExited');
}

function tmuxSignalPaneTitle(pane) {
  return String(pane?.title || pane?.pane_title || '').trim();
}

function tmuxSignalPaneActionRequired(pane) {
  return /\[\s*[!.]\s*\]\s*action required/i.test(tmuxSignalPaneTitle(pane));
}

function tmuxSignalAgentStateForSession(session) {
  const sessionText = String(session || '').trim();
  const sessionPanes = tmuxSignalPanes().filter(pane => tmuxSignalPaneSession(pane) === sessionText);
  const actionRequiredPane = sessionPanes.find(pane => pane?.dead !== true && tmuxSignalPaneActionRequired(pane));
  if (actionRequiredPane) {
    return stateValue(STATE_KEY.needsInput, stateReason('tmuxActionRequired'), {tmuxSignal: 'action-required'});
  }
  const panes = sessionPanes.filter(tmuxSignalPaneIsAgent);
  if (!panes.length) return null;
  const livePanes = panes.filter(pane => pane?.dead !== true);
  const deadPanes = panes.filter(pane => pane?.dead === true);
  const windows = panes.map(tmuxSignalWindowForPane).filter(Boolean);
  const bellWindow = windows.find(windowRecord => windowRecord?.bell_flag === true);
  if (bellWindow) {
    return stateValue(STATE_KEY.needsInput, stateReason('tmuxBellAlert'), {tmuxSignal: 'bell'});
  }
  const quietWindow = windows.find(windowRecord => windowRecord?.silence_flag === true && !tmuxSignalWindowIsRecentlyActive(windowRecord));
  if (quietWindow && livePanes.length) {
    return stateValue('done', stateReason('tmuxSilenceAlert'), {tmuxSignal: 'silence', showBadge: true});
  }
  if (!livePanes.length && deadPanes.length) {
    return stateValue('done', tmuxSignalDeadText(deadPanes[0]), {tmuxSignal: 'agent-exited', showBadge: true});
  }
  const runningPane = livePanes.find(pane => tmuxSignalAgentCommands.has(tmuxSignalPaneCommand(pane)) && (pane?.alternate_on === true || Number(pane?.pid || 0) > 0));
  if (runningPane) {
    const command = tmuxSignalPaneCommand(runningPane);
    const mode = t(runningPane?.alternate_on === true ? 'tmux.mode.alternateScreen' : 'tmux.mode.process');
    return stateValue(STATE_KEY.working, stateReason('tmuxAgentRunning', {command, mode}), {tmuxSignal: 'agent-running'});
  }
  return null;
}

function browserFaviconRoundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// Favicon background AND the "Y" follow the user's Active color preference + theme (no
// hardcoded hex): the background is --active-accent and the "Y" is its theme-aware contrast
// color --active-accent-text (dark glyph on light accents like green/yellow/white, light
// glyph on dark accents like blue/purple). Both flip with the active_color choice and the
// dark/light theme — and the favicon repaints on either change because applyActiveColor()
// (called on active-color change AND on every theme switch) forces a refresh. Falls back to
// the legacy lime background / dark glyph if the vars aren't ready (e.g. before CSS loads).
function browserFaviconAccentColors() {
  const fallbackBg = '#99d441';
  const fallbackText = '#111111';
  try {
    const root = document.documentElement;
    if (!root || typeof getComputedStyle !== 'function') return {bg: fallbackBg, text: fallbackText};
    const cs = getComputedStyle(root);
    const bg = (cs.getPropertyValue('--active-accent') || '').trim();
    const text = (cs.getPropertyValue('--active-accent-text') || '').trim();
    return {bg: bg || fallbackBg, text: text || fallbackText};
  } catch (_) {
    return {bg: fallbackBg, text: fallbackText};
  }
}

function renderBrowserAppIconDataUrl(options = {}) {
  const logicalSize = 64;
  const size = Math.max(logicalSize, Math.round(Number(options.size) || logicalSize));
  const showBadge = options.showBadge === true;
  const count = Math.max(0, Math.floor(Number(options.count) || 0));
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext?.('2d');
  if (!ctx || typeof canvas.toDataURL !== 'function') return '';

  const faviconAccent = browserFaviconAccentColors();
  ctx.clearRect(0, 0, size, size);
  ctx.save();
  ctx.scale(size / logicalSize, size / logicalSize);
  browserFaviconRoundedRect(ctx, 2, 2, 60, 60, 10);
  ctx.fillStyle = faviconAccent.bg;
  ctx.fill();

  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';
  ctx.fillStyle = faviconAccent.text;
  ctx.font = '900 86px Arial, sans-serif';
  ctx.save();
  ctx.translate(showBadge ? 25 : 32, 39);
  ctx.scale(1.22, 1);
  ctx.fillText('Y', 0, 0);
  ctx.restore();

  if (showBadge) {
    const label = browserFaviconBadgeLabel(count);
    ctx.textAlign = 'right';
    ctx.font = label.length > 2 ? '900 24px Arial, sans-serif' : label.length > 1 ? '900 32px Arial, sans-serif' : '900 42px Arial, sans-serif';
    ctx.lineWidth = 5;
    ctx.strokeStyle = '#f9fafb';
    ctx.strokeText(label, 62, 50);
    ctx.fillStyle = count > 0 ? '#d92d20' : '#374151';
    ctx.fillText(label, 62, 50);
  }
  ctx.restore();
  return canvas.toDataURL('image/png');
}

function renderBrowserFaviconDataUrl(count) {
  return renderBrowserAppIconDataUrl({count, showBadge: true});
}

let browserFaviconLastBadge = null;

function browserFaviconLink() {
  const existing = document.querySelector?.('link[rel~="icon"][data-yolomux-favicon]')
    || document.querySelector?.('link[rel~="icon"]');
  if (existing) {
    existing.dataset.yolomuxFavicon = '1';
    existing.type = 'image/png';
    return existing;
  }
  const link = document.createElement('link');
  link.rel = 'icon';
  link.type = 'image/png';
  link.dataset.yolomuxFavicon = '1';
  (document.head || document.documentElement || document.body)?.appendChild(link);
  return link;
}

function updateBrowserFavicon(options = {}) {
  const count = browserFaviconBadgeCount(options.counts);
  // Include the accent in the dedupe signature so a theme/active-color change re-renders
  // on the next refresh tick even when the badge count is unchanged.
  const accent = browserFaviconAccentColors();
  const signature = `${count}|${accent.bg}|${accent.text}`;
  if (!options.force && browserFaviconLastBadge === signature) return false;
  const dataUrl = renderBrowserFaviconDataUrl(count);
  if (!dataUrl) return false;
  const link = browserFaviconLink();
  if (!link) return false;
  link.href = dataUrl;
  browserFaviconLastBadge = signature;
  return true;
}

function updateDocumentTitle() {
  const counts = globalActivityCounts();
  updateBrowserFavicon({counts});
  const count = runningAgentCount(counts);
  if (count > 0) {
    documentTitleIdleSinceMs = null;
    document.title = t('document.title.running', {count});
    return;
  }
  const now = documentTitleNowMs();
  if (documentTitleIdleSinceMs === null) documentTitleIdleSinceMs = now;
  const idleMinutes = documentTitleIdleMinutes(now);
  document.title = idleMinutes
    ? t('document.title.idleFor', {minutes: idleMinutes})
    : t('document.title.idle');
}

// Cross-session YOLO screen-state rollup for the always-visible top-bar status line. It intentionally
// ignores "YOLO enabled" and broad session heuristics; idle enabled sessions must count as 0 working/attention/blocked.
function globalActivityCounts() {
  const agentWindowCounts = globalActivityCountsFromAgentWindows();
  if (agentWindowCounts) return agentWindowCounts;
  const signalWindows = tmuxSignalWindows();
  let running = 0;
  let ask = 0;
  let blocked = 0;
  const countedSignalSessions = new Set();
  const countedSignalWindows = new Set();
  const runningSignalSessions = new Set();
  const bellSignalWindows = new Set();
  const actionRequiredSignalWindows = new Set();
  const signalAttentionSessions = new Set();
  for (const windowRecord of signalWindows) {
    const signalSession = tmuxSignalWindowSession(windowRecord);
    const signalKey = tmuxSignalWindowKey(windowRecord);
    const signalPayload = signalSession ? (autoApproveStates.get(signalSession) || {}) : {};
    const signalPromptSignature = signalSession ? promptAttentionPayloadSignature(signalPayload) : '';
    const signalPromptCleared = signalSession ? promptAttentionIsCleared(signalSession, signalPromptSignature, signalPayload) : false;
    const panes = Array.isArray(windowRecord?.panes) ? windowRecord.panes : [];
    const hasAgentPane = tmuxSignalWindowHasAgentPane(windowRecord);
    const hasActionRequiredPane = panes.some(tmuxSignalPaneActionRequired);
    if (windowRecord?.bell_flag === true && hasAgentPane && !signalPromptCleared) {
      if (signalKey) bellSignalWindows.add(signalKey);
      if (signalSession) signalAttentionSessions.add(signalSession);
    }
    if (hasActionRequiredPane && !signalPromptCleared) {
      if (signalKey) actionRequiredSignalWindows.add(signalKey);
      if (signalSession) signalAttentionSessions.add(signalSession);
    }
    if (signalKey && (hasAgentPane || hasActionRequiredPane)) countedSignalWindows.add(signalKey);
    if (signalSession && (hasAgentPane || hasActionRequiredPane)) countedSignalSessions.add(signalSession);
    if (!hasAgentPane) continue;
    if (!tmuxSignalWindowIsRecentlyActive(windowRecord)) continue;
    running += 1;
    if (signalSession) runningSignalSessions.add(signalSession);
  }
  ask += new Set([...bellSignalWindows, ...actionRequiredSignalWindows]).size;
  const countedSessions = new Set([...autoApproveStates.keys()]
    .map(value => String(value || ''))
    .filter(session => session && autoApprovePayloadCountsInTopbar(session, autoApproveStates.get(session) || {})));
  for (const session of countedSessions) {
    if (!isTmuxSession(session)) continue;
    const payload = autoApproveStates.get(session) || {};
    const key = String(payload?.screen?.key || '');
    const promptVisible = payload?.prompt?.visible === true;
    const promptAttentionKey = promptVisible
      ? (payload?.prompt?.yes_selected === true ? STATE_KEY.needsApproval : STATE_KEY.needsInput)
      : key;
    const promptSignature = promptAttentionPayloadSignature(payload);
    const promptCleared = promptAttentionIsCleared(session, promptSignature, payload);
    if (key === STATE_KEY.working && !runningSignalSessions.has(session)) running += 1;
    else if (ATTENTION_SCREEN_KEYS.has(promptAttentionKey) && !promptCleared && !signalAttentionSessions.has(session)) ask += 1;
    else if (key === STATE_KEY.blocked) blocked += 1;
  }
  const fallbackTotal = [...countedSessions].filter(isTmuxSession).length;
  const total = countedSignalWindows.size
    ? countedSignalWindows.size + [...countedSessions].filter(session => isTmuxSession(session) && !countedSignalSessions.has(session)).length
    : fallbackTotal;
  const attention = ask + blocked;
  return {running, ask, blocked, attention, idle: Math.max(0, total - running - attention), total};
}

function globalActivityCountSessions() {
  if (typeof tabberOrderedSessions === 'function') return tabberOrderedSessions();
  const order = Array.isArray(transcriptMetadataState.payload.session_order) ? transcriptMetadataState.payload.session_order : [];
  const all = transcriptMetadataState.payload.sessions && typeof transcriptMetadataState.payload.sessions === 'object' ? Object.keys(transcriptMetadataState.payload.sessions) : [];
  const seen = new Set();
  const sessions = [];
  for (const value of [...order, ...all]) {
    const session = String(value || '').trim();
    if (!session || seen.has(session) || !isTmuxSession(session)) continue;
    seen.add(session);
    sessions.push(session);
  }
  return sessions;
}

function globalActivityCountsFromAgentWindows() {
  if (
    typeof sessionAgentWindowStatusPayloads !== 'function'
    || typeof agentWindowActivityIconForStatusItem !== 'function'
    || typeof agentWindowActivityOptionsForStatus !== 'function'
    || typeof agentWindowKind !== 'function'
  ) return null;
  let running = 0;
  let ask = 0;
  let blocked = 0;
  let total = 0;
  for (const session of globalActivityCountSessions()) {
    const info = transcriptMetadataState.payload.sessions?.[session] || {};
    const autoPayload = autoApproveStates.get(session) || {};
    const rows = sessionAgentWindowStatusPayloads(session, info, autoPayload);
    for (const agent of rows) {
      if (!agentWindowKind(agent?.kind)) continue;
      const itemOptions = agentWindowActivityOptionsForStatus(agent, session, {scheduleRefresh: false});
      const item = agentWindowActivityIconForStatusItem(agent, agent.kind, session, itemOptions);
      total += 1;
      if (!item || item.acknowledged === true) continue;
      if (item.state === STATE_KEY.working) running += 1;
      else if (item.state === 'attention') ask += 1;
      else if (item.state === 'cooldown') blocked += 1;
    }
  }
  if (!total) return null;
  const attention = ask + blocked;
  return {running, ask, blocked, attention, idle: Math.max(0, total - running - attention), total};
}

function globalActivityStatusLineHtml() {
  const counts = globalActivityCounts();
  if (!counts.total) return '';
  const parts = [];
  parts.push(topbarActivityCountBallHtml(counts.running, STATE_KEY.working, 'topbar-activity-working'));
  parts.push(topbarActivityCountBallHtml(counts.ask, 'attention', 'topbar-activity-ask'));
  parts.push(topbarActivityCountBallHtml(counts.blocked, 'cooldown', 'topbar-activity-blocked'));
  parts.push(`<span class="${esc(statusIndicatorInlineClasses('', 'topbar-activity-idle'))}">${esc(t('topbar.activity.idle', {count: counts.idle}))}</span>`);
  return parts.join('<span class="topbar-activity-sep" aria-hidden="true">·</span>');
}

function topbarActivityUsesMobileCountBalls() {
  return typeof topbarPackingHasStep === 'function' && topbarPackingHasStep('compact-activity');
}

// The activity rail is useful before phone-only Menus mode: compact balls keep their status
// visible while leaving the menu row and its Cmd-P launcher room to shrink naturally.
function topbarActivityUsesActionsRail() {
  return topbarActivityUsesMobileCountBalls();
}

function syncTopbarActionCapacity() {
  return typeof syncTopbarPacking === 'function' ? syncTopbarPacking() : [];
}

function scheduleTopbarActionCapacity() {
  if (typeof scheduleTopbarPacking === 'function') scheduleTopbarPacking();
}

function syncTopbarActivityPlacement() {
  const activity = document.getElementById('topbarActivity');
  const normalHost = document.querySelector('.topbar-right-tools');
  const actions = document.querySelector('.actions');
  if (!activity || !normalHost || !actions) return false;
  if (topbarActivityUsesActionsRail()) {
    const refresh = actions.querySelector('#refreshMeta');
    if (activity.parentElement !== actions || activity.nextElementSibling !== refresh) actions.insertBefore(activity, refresh || null);
    scheduleTopbarActionCapacity();
    return true;
  }
  if (activity.parentElement !== normalHost) normalHost.append(activity);
  scheduleTopbarActionCapacity();
  return true;
}

function topbarActivityVisibleCount(counts = {}) {
  return Math.max(0, Number(counts.running) || 0) + Math.max(0, Number(counts.ask) || 0) + Math.max(0, Number(counts.blocked) || 0);
}

function topbarActivityCountBallHtml(count, tone, extraClass = '') {
  const activityToneClass = typeof agentWindowActivityToneWrapperClass === 'function' ? agentWindowActivityToneWrapperClass(tone) : '';
  const dotHtml = agentWindowStatusDotHtmlForTone(tone, {surface: 'topbar', pulse: false});
  return `
    <span class="${esc(statusIndicatorInlineClasses('', 'topbar-activity-count', extraClass, count ? 'active' : ''))}">
      <span class="topbar-activity-count-number">${esc(String(count))}</span>
      <span class="${esc(['agent-window-activity', 'agent-window-activity--status-only', activityToneClass, 'topbar-activity-ball'].join(' '))}" aria-hidden="true">
        ${dotHtml}
      </span>
    </span>
  `;
}

function createTopbarActivityStatus() {
  return makeButton({
    id: 'topbarActivity',
    className: 'topbar-activity topbar-status-surface',
    title: t('topbar.activity.title'),
    ariaLabel: t('topbar.activity.aria'),
    onClick: () => openTabberActivityOverview(),
  });
}

function updateTopbarActivityStatus() {
  const node = document.getElementById('topbarActivity');
  if (!node) return;
  const counts = globalActivityCounts();
  const html = globalActivityStatusLineHtml();
  node.innerHTML = html;
  const mobileCountBalls = topbarActivityUsesMobileCountBalls();
  node.hidden = !html || (mobileCountBalls && !topbarActivityVisibleCount(counts));
  node.classList.toggle('topbar-activity--mobile-count-balls', mobileCountBalls);
  node.classList.toggle('has-attention', counts.attention > 0);
  syncTopbarActivityPlacement();
  scheduleTopbarActionCapacity();
  if (typeof scheduleAgentWindowActivityAnimationSync === 'function') scheduleAgentWindowActivityAnimationSync(node);
}

function topbarOwnerStatusCombinedHtml(summaries = []) {
  const activeSummaries = summaries.filter(item => item && typeof item === 'object');
  if (!activeSummaries.length) return '';
  const state = activeSummaries.every(item => item.ownsRole === true || item.ownsIndex === true) ? 'leader' : 'follower';
  const labels = activeSummaries.map(item => String(item.label || '')).filter(Boolean).join('|');
  return `<span class="topbar-owner-status-part topbar-owner-status-shared" data-owner-role="${esc(state)}"><span class="topbar-owner-status-key">${esc(labels)}</span><span class="topbar-owner-status-separator">:</span> <span class="topbar-owner-status-value">${esc(t(`backgroundOwner.role.${state}`))}</span></span>`;
}

function topbarOwnerStatusTitle(indexSummary = {}, statsSummary = {}, sessionSummary = {}) {
  const lines = [
    t('backgroundOwner.connectedServer', {server: indexSummary.currentLabel || statsSummary.currentLabel || sessionSummary.currentLabel || t('backgroundOwner.thisServer')}),
    indexSummary.ownerLabel ? t('backgroundOwner.leader', {role: 'IDX', server: indexSummary.ownerLabel}) : '',
    statsSummary.ownerLabel ? t('backgroundOwner.leader', {role: 'STATS', server: statsSummary.ownerLabel}) : '',
    sessionSummary.ownerLabel ? t('backgroundOwner.leader', {role: 'SESS', server: sessionSummary.ownerLabel}) : '',
    indexSummary.status ? t('backgroundOwner.status', {role: t('backgroundOwner.index'), status: indexSummary.status}) : '',
    statsSummary.status ? t('backgroundOwner.status', {role: t('tab.debug'), status: statsSummary.status}) : '',
    sessionSummary.status ? t('backgroundOwner.status', {role: t('backgroundOwner.sessionFiles'), status: sessionSummary.status}) : '',
    indexSummary.error || statsSummary.error || sessionSummary.error || '',
  ];
  return lines.filter(Boolean).join('\n');
}

function topbarOwnerStatusSummaries(payload = backgroundOwnerStatusState.payload) {
  if (!payload || typeof payload !== 'object' || typeof backgroundOwnerSearchIndexSummary !== 'function' || typeof backgroundOwnerStatsSummary !== 'function' || typeof backgroundOwnerSessionFilesSummary !== 'function') return [];
  return [
    {...backgroundOwnerSearchIndexSummary(payload), label: 'IDX'},
    {...backgroundOwnerStatsSummary(payload), label: 'STATS'},
    {...backgroundOwnerSessionFilesSummary(payload), label: 'SESS'},
  ];
}

function backgroundOwnerOwnsAllRoles(payload = backgroundOwnerStatusState.payload) {
  const summaries = topbarOwnerStatusSummaries(payload).filter(item => item && typeof item === 'object');
  return Boolean(summaries.length) && summaries.every(item => item.ownsRole === true || item.ownsIndex === true);
}

function backgroundOwnerCurrentOwnerLive(payload = backgroundOwnerStatusState.payload, nowSeconds = Date.now() / 1000) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const owner = data.current_owner && typeof data.current_owner === 'object' ? data.current_owner : {};
  const latest = data.latest_generation && typeof data.latest_generation === 'object' ? data.latest_generation : {};
  if (owner.generation_id && latest.generation_id && owner.generation_id === latest.generation_id) return true;
  const heartbeat = Number(owner.last_heartbeat || 0);
  return Number.isFinite(heartbeat) && heartbeat > 0 && Number(nowSeconds) - heartbeat <= 10;
}

async function claimBackgroundOwnerLeader() {
  try {
    const result = await apiFetchJson('/api/background/claim', {method: 'POST'});
    if (result?.status && typeof applyBackgroundOwnerStatusPayload === 'function') {
      applyBackgroundOwnerStatusPayload(result.status, {render: false});
    }
    if (typeof refreshBackgroundOwnerStatus === 'function') {
      await refreshBackgroundOwnerStatus({force: true, render: false});
    }
    statusOk(localizedHtml(result?.was_owner ? 'status.backgroundOwnerAlreadyLeader' : 'status.backgroundOwnerClaimed'));
  } catch (error) {
    statusErr(localizedHtml('status.backgroundOwnerClaimFailed', {error}));
    if (typeof refreshBackgroundOwnerStatus === 'function') {
      refreshBackgroundOwnerStatus({force: true, render: false}).catch(refreshError => console.warn('background-owner status refresh failed', refreshError));
    }
  }
}

function showBackgroundOwnerContextMenu(event) {
  event.preventDefault();
  event.stopPropagation();
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu background-owner-context-menu';
  menu.setAttribute('role', 'menu');
  const alreadyLeader = backgroundOwnerOwnsAllRoles();
  if (alreadyLeader || readOnlyMode) {
    appendContextMenuButton(menu, t(alreadyLeader ? 'backgroundOwner.alreadyLeader' : 'common.notAvailable'), () => {}, () => backgroundOwnerContextMenu.close(), {disabled: true});
  } else {
    appendContextMenuButton(menu, t('backgroundOwner.takeOver'), () => {
      const payload = backgroundOwnerStatusState.payload && typeof backgroundOwnerStatusState.payload === 'object' ? backgroundOwnerStatusState.payload : {};
      const owner = payload.current_owner && typeof payload.current_owner === 'object' ? payload.current_owner : {};
      if (backgroundOwnerCurrentOwnerLive(payload)) {
        const label = backgroundServerLabel(owner, t('common.unknown'));
        const message = t('backgroundOwner.takeoverConfirm', {server: label});
        if (typeof window.confirm === 'function' && !window.confirm(message)) return;
      }
      claimBackgroundOwnerLeader();
    }, () => backgroundOwnerContextMenu.close());
  }
  backgroundOwnerContextMenu.open(menu, event.clientX, event.clientY);
}

function topbarOwnerStatusHtml() {
  if (shareViewMode) return '';
  if (backgroundOwnerStatusState.loading && !backgroundOwnerStatusState.payload) {
    return '<span class="topbar-owner-status-part topbar-owner-status-shared" data-owner-role="loading"><span class="topbar-owner-status-key">IDX|STATS|SESS</span><span class="topbar-owner-status-separator">:</span> <span class="topbar-owner-status-value">...</span></span>';
  }
  if (backgroundOwnerStatusState.error && !backgroundOwnerStatusState.payload) {
    return '<span class="topbar-owner-status-part topbar-owner-status-shared" data-owner-role="error"><span class="topbar-owner-status-key">IDX|STATS|SESS</span><span class="topbar-owner-status-separator">:</span> <span class="topbar-owner-status-value">?</span></span>';
  }
  return topbarOwnerStatusCombinedHtml(topbarOwnerStatusSummaries(backgroundOwnerStatusState.payload));
}

function createTopbarOwnerStatus() {
  return makeButton({
    id: 'topbarOwnerStatus',
    className: 'topbar-owner-status topbar-status-surface',
    title: t('backgroundOwner.refresh'),
    ariaLabel: t('backgroundOwner.aria'),
    onClick: () => {
      if (typeof refreshBackgroundOwnerStatus === 'function') {
        refreshBackgroundOwnerStatus({force: true}).catch(error => console.warn('background-owner status refresh failed', error));
      }
    },
    events: {contextmenu: showBackgroundOwnerContextMenu},
  });
}

function updateTopbarOwnerStatus() {
  const node = document.getElementById('topbarOwnerStatus');
  if (!node) return;
  const html = topbarOwnerStatusHtml();
  node.innerHTML = html;
  node.hidden = !html;
  node.classList.toggle('has-follower', /\bdata-owner-role="follower"/.test(html));
  node.classList.toggle('has-error', /\bdata-owner-role="error"/.test(html));
  const summaries = topbarOwnerStatusSummaries(backgroundOwnerStatusState.payload);
  if (summaries.length) {
    const [indexSummary, statsSummary, sessionSummary] = summaries;
    node.title = topbarOwnerStatusTitle(indexSummary, statsSummary, sessionSummary) || t('backgroundOwner.refresh');
  } else if (backgroundOwnerStatusState.error) {
    node.title = userMessageText(backgroundOwnerStatusState.error, t('common.requestFailed'));
  } else {
    node.title = t('backgroundOwner.refresh');
  }
}

const attentionAnimationDelayProperty = '--attention-animation-delay';
let attentionAnimationClockDurationMs = 0;

function attentionAnimationDurationMs(durationMs = agentStatusPulsePeriodMs) {
  const duration = Math.max(1, Number(durationMs) || Number(agentStatusPulsePeriodMs) || 1);
  return duration;
}

function statusPulseAnimationEnabled() {
  return typeof globalThis !== 'undefined' && globalThis.yolomuxEnableBroadStatusPulse === true;
}

function attentionAnimationPhaseMs(now = Date.now(), durationMs = agentStatusPulsePeriodMs) {
  const duration = attentionAnimationDurationMs(durationMs);
  return ((Number(now) || 0) % duration + duration) % duration;
}

function attentionAnimationDelay(now = Date.now(), durationMs = agentStatusPulsePeriodMs) {
  const duration = attentionAnimationDurationMs(durationMs);
  return `${-((now % duration) / 1000).toFixed(3)}s`;
}

function attentionAnimationClockDelay(now = Date.now(), durationMs = agentStatusPulsePeriodMs) {
  const duration = attentionAnimationDurationMs(durationMs);
  const current = document?.documentElement?.style?.getPropertyValue?.(attentionAnimationDelayProperty)?.trim();
  return current && attentionAnimationClockDurationMs === duration ? current : setAttentionAnimationClockDelay(now, duration);
}

function attentionAnimationStyle(now = Date.now(), durationMs = agentStatusPulsePeriodMs, property = attentionAnimationDelayProperty) {
  // This style is stamped into newly rendered status markup. Derive it from the current wall-clock
  // phase instead of copying the root's older delay: renderers can replace a ball without going
  // through the explicit animation synchronizer, and reusing that old delay restarts its pulse.
  const value = attentionAnimationDelay(now, durationMs);
  return `${property}: ${value}`;
}

function setAttentionAnimationClockDelay(now = Date.now(), durationMs = agentStatusPulsePeriodMs) {
  const duration = attentionAnimationDurationMs(durationMs);
  const delay = attentionAnimationDelay(now, duration);
  document?.documentElement?.style?.setProperty?.(attentionAnimationDelayProperty, delay);
  attentionAnimationClockDurationMs = duration;
  return delay;
}

function syncAttentionAnimation(node, active) {
  if (!node?.style) return;
  const enabled = active === true && statusPulseAnimationEnabled();
  node.classList?.toggle?.('attention-pulse', enabled);
  node.classList?.toggle?.('heartbeat-pulse', enabled);
  if (enabled) {
    attentionAnimationClockDelay();
    node.style.removeProperty(attentionAnimationDelayProperty);
  } else {
    node.style.removeProperty(attentionAnimationDelayProperty);
  }
}

function statusIndicatorClassItems(...classes) {
  const items = [];
  classes.forEach(item => {
    if (Array.isArray(item)) items.push(...statusIndicatorClassItems(...item));
    else if (item) items.push(item);
  });
  return items;
}

function statusIndicatorClasses(...classes) {
  return ['status-indicator', ...statusIndicatorClassItems(...classes)].join(' ');
}

function statusIndicatorToneClasses(tone, options = {}) {
  const pulseEnabled = options.pulse !== false && statusPulseAnimationEnabled();
  if (tone === 'positive') return ['status-indicator--positive'];
  if (tone === STATE_KEY.working) return ['status-indicator--working', pulseEnabled ? 'heartbeat-pulse' : ''];
  if (tone === 'cooldown') return ['status-indicator--cooldown', pulseEnabled ? 'heartbeat-pulse' : '', pulseEnabled ? 'attention-pulse' : ''];
  if (tone === 'attention') return ['status-indicator--attention', pulseEnabled ? 'heartbeat-pulse' : '', pulseEnabled ? 'attention-pulse' : ''];
  if (tone === 'active') return ['status-indicator--active'];
  if (tone === 'settled') return ['status-indicator--settled'];
  if (tone === STATE_KEY.idle) return ['status-indicator--idle'];
  return [];
}

function statusIndicatorToneStyle(tone, options = {}) {
  return options.pulse !== false && statusPulseAnimationEnabled() && [STATE_KEY.working, 'active', 'attention', 'cooldown'].includes(String(tone || '')) ? ` style="${attentionAnimationStyle()}"` : '';
}

function statusIndicatorExtractOptions(classes) {
  const last = classes.length ? classes[classes.length - 1] : null;
  if (!last || typeof last !== 'object' || Array.isArray(last)) return {};
  if (!Object.prototype.hasOwnProperty.call(last, 'pulse') && !Object.prototype.hasOwnProperty.call(last, 'modifierPosition')) return {};
  classes.pop();
  return last;
}

function statusIndicatorModifiedClasses(modifier, tone, classes, options = {}) {
  const items = statusIndicatorClassItems(classes);
  const toneClasses = statusIndicatorToneClasses(tone, options);
  if (options.modifierPosition === 'after-all') return statusIndicatorClasses(items, toneClasses, modifier);
  if (!items.length) return statusIndicatorClasses(modifier, toneClasses);
  return statusIndicatorClasses(items[0], modifier, items.slice(1), toneClasses);
}

function statusIndicatorTextClasses(tone, ...classes) {
  const options = statusIndicatorExtractOptions(classes);
  return statusIndicatorModifiedClasses('status-indicator--text', tone, classes, options);
}

function statusIndicatorLabelClasses(tone, ...classes) {
  const options = statusIndicatorExtractOptions(classes);
  return statusIndicatorModifiedClasses('status-indicator--label', tone, classes, options);
}

function statusIndicatorDotClasses(tone, ...classes) {
  const options = statusIndicatorExtractOptions(classes);
  return statusIndicatorModifiedClasses('status-indicator--dot', tone, classes, options);
}

function statusIndicatorInlineClasses(tone, ...classes) {
  const options = statusIndicatorExtractOptions(classes);
  return statusIndicatorModifiedClasses('status-indicator--inline', tone, classes, {...options, modifierPosition: 'after-all'});
}

function statusIndicatorLabelHtml(text, tone, ...classes) {
  const value = String(text || '').trim();
  if (!value) return '';
  const options = statusIndicatorExtractOptions(classes);
  const style = statusIndicatorToneStyle(tone, options);
  const optionArgs = Object.keys(options).length ? [options] : [];
  return `<span class="${esc(statusIndicatorLabelClasses(tone, ...classes, ...optionArgs))}"${style}>${esc(value)}</span>`;
}

function stateBadgeHtml(key, short, title, options = {}) {
  const classes = ['session-state-badge', 'tab-symbol', `session-state-${key}`];
  const attention = stateDef(key).attention;
  const tone = attention ? 'attention' : '';
  if (attention) classes.push('session-state-reminder');
  const style = statusIndicatorToneStyle(tone);
  const clearable = options.clearable === true && options.session && options.promptSignature;
  const attrs = clearable
    ? ` role="button" tabindex="0" data-prompt-attention-clear="1" data-session="${esc(options.session)}" data-prompt-signature="${esc(options.promptSignature)}" aria-label="${esc(t('state.clearAsk'))}"`
    : '';
  return `<span class="${esc(statusIndicatorTextClasses(tone, classes))}"${style} title="${esc(title)}"${attrs}>${esc(short)}</span>`;
}

function sessionStateHtml(state) {
  // 'ready-review' is dropped — the dedicated #NNNN / CI / Approved PR chips already convey
  // "PR ready", so the standalone "PR" state pill is redundant on the tab.
  if (state?.promptAttentionCleared) return '';
  if ([STATE_KEY.needsApproval, STATE_KEY.needsInput].includes(state?.key)) return '';
  if (!state || [STATE_KEY.idle, STATE_KEY.working, 'done', 'cooldown'].includes(state.key) || (!state.showBadge && ['tests-running', 'disconnected', 'yolo-approval', 'ready-review'].includes(state.key))) return '';
  return stateBadgeHtml(state.key, state.short || stateDef(state.key).short, `${state.label}: ${state.reason}`, {
    clearable: [STATE_KEY.needsApproval, STATE_KEY.needsInput].includes(state.key) && Boolean(state.promptSignature),
    session: state.session,
    promptSignature: state.promptSignature,
  });
}

function inactiveTabItems() {
  const inPane = new Set(paneItems());
  return sortTabItems(allTabItems().filter(item => !inPane.has(item)));
}

function readNotificationDelivery() {
  try {
    const stored = JSON.parse(storageGet(notificationDeliveryStorageKey, ''));
    if (!stored || typeof stored !== 'object') return {...notificationDeliveryDefaults};
    return {inApp: stored.inApp !== false, system: stored.system === true};
  } catch (_) {
    return {...notificationDeliveryDefaults};
  }
}

function notificationDeliveryEnabled(channel) {
  if (channel === 'inApp') return notificationDelivery.inApp === true;
  if (channel === 'system') return notificationDelivery.system === true;
  return notificationDelivery.inApp === true || notificationDelivery.system === true;
}

function notificationSystemPermission() {
  return 'Notification' in window ? Notification.permission : 'unsupported';
}

function notificationDeliveryDescriptors() {
  const permission = notificationSystemPermission();
  const permissionLabel = t(`notify.permission.${permission}`);
  return [
    {
      channel: 'inApp',
      label: t('notify.delivery.inApp.label'),
      help: t('notify.delivery.inApp.help'),
      className: 'notification-delivery-in-app',
      disabled: false,
    },
    {
      channel: 'system',
      label: t('notify.delivery.system.label', {platform: isMacPlatform() ? 'macOS' : 'PC'}),
      help: t('notify.delivery.system.help', {permission: permissionLabel}),
      className: 'notification-delivery-system',
      disabled: permission === 'unsupported',
    },
  ];
}

function notificationDeliveryItems() {
  return notificationDeliveryDescriptors().map(descriptor => menuCommand(
    descriptor.label,
    () => setNotificationDelivery(descriptor.channel, !notificationDeliveryEnabled(descriptor.channel)),
    {
      checked: notificationDeliveryEnabled(descriptor.channel),
      keepOpen: true,
      renderMenu: false,
      className: descriptor.className,
      detail: descriptor.help,
      disabled: descriptor.disabled,
    },
  ));
}

let notificationDeliveryPopover = null;

function closeNotificationDeliveryPopover() {
  if (!notificationDeliveryPopover) return;
  notificationDeliveryPopover.hidden = true;
  notifyToggle?.setAttribute('aria-expanded', 'false');
}

function openNotificationDeliveryPopover() {
  if (!notifyToggle) return;
  if (!notificationDeliveryPopover) {
    notificationDeliveryPopover = document.createElement('div');
    notificationDeliveryPopover.className = 'app-menu-popover notification-delivery-popover';
    notificationDeliveryPopover.hidden = true;
    notificationDeliveryPopover.setAttribute('role', 'menu');
    document.body.appendChild(notificationDeliveryPopover);
    document.addEventListener('pointerdown', event => {
      if (!notificationDeliveryPopover.hidden && !notificationDeliveryPopover.contains(event.target) && event.target !== notifyToggle) closeNotificationDeliveryPopover();
    });
  }
  notificationDeliveryPopover.replaceChildren(...notificationDeliveryItems().map(createAppMenuItem));
  notificationDeliveryPopover.hidden = false;
  const rect = notifyToggle.getBoundingClientRect();
  const width = notificationDeliveryPopover.getBoundingClientRect().width;
  notificationDeliveryPopover.style.top = `${rect.bottom + 4}px`;
  notificationDeliveryPopover.style.left = `${Math.max(8, rect.right - width)}px`;
  notifyToggle.setAttribute('aria-expanded', 'true');
}

function renderNotifyToggle() {
  if (!notifyToggle) return;
  const label = t('pref.section.notifications');
  syncPressedButton(notifyToggle, notificationDeliveryEnabled(), {labelOn: label, labelOff: label});
  notifyToggle.title = t('notify.delivery.summary', {
    inApp: t(notificationDeliveryEnabled('inApp') ? 'notify.state.on' : 'state.off'),
    system: t(notificationDeliveryEnabled('system') ? 'notify.state.on' : 'state.off'),
  });
  notifyToggle.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    if (notificationDeliveryPopover && !notificationDeliveryPopover.hidden) closeNotificationDeliveryPopover();
    else openNotificationDeliveryPopover();
  };
  if (notificationDeliveryPopover && !notificationDeliveryPopover.hidden) openNotificationDeliveryPopover();
}

function refreshNotificationDeliveryMenuChecks() {
  for (const [className, channel] of [['notification-delivery-in-app', 'inApp'], ['notification-delivery-system', 'system']]) {
    for (const node of document.querySelectorAll(`.${className}`)) {
      const checked = notificationDeliveryEnabled(channel);
      node.dataset.checked = checked ? 'true' : 'false';
      node.setAttribute('aria-checked', checked ? 'true' : 'false');
    }
  }
}

async function setNotificationDelivery(channel, enabled) {
  let system = notificationDelivery.system === true;
  if (channel === 'system') {
    system = enabled === true;
    if (system && notificationSystemPermission() === 'default') system = await Notification.requestPermission() === 'granted';
    if (system && notificationSystemPermission() !== 'granted') system = false;
  }
  notificationDelivery = {inApp: channel === 'inApp' ? enabled === true : notificationDelivery.inApp === true, system};
  storageSet(notificationDeliveryStorageKey, JSON.stringify(notificationDelivery));
  renderNotifyToggle();
  refreshNotificationDeliveryMenuChecks();
  if (typeof syncClientEventDemand === 'function') syncClientEventDemand({immediate: true});
  if (notificationDeliveryEnabled(channel)) sendTestNotification({[channel]: true});
}

function loadNotificationDelivery() {
  notificationDelivery = readNotificationDelivery();
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
      {label: t('common.commandPalette'), keys: appShortcutText('P', {shift: true})},
      {label: t('shortcuts.fileQuickOpen'), keys: appShortcutText('P')},
      {label: t('brand.share'), keys: appShortcutText('K')},
      {label: t('shortcuts.openYoagentRight'), keys: appShortcutText('B', {alt: true})},
      {label: t('shortcuts.toggleFinder', {name: fileExplorerLabel()}), keys: appShortcutText('B')},
      {label: t('shortcuts.openPreferences'), keys: appShortcutText(',')},
      {label: t('common.keyboardShortcuts'), keys: '?'},
      {label: t('shortcuts.closeMenu'), keys: 'Esc'},
    ]},
    {section: t('shortcuts.section.terminal'), items: [
      {label: t('shortcuts.copyVisibleTerminalSelection'), keys: appShortcutText('C')},
      {label: t('common.copyTmuxSelection'), keys: appShortcutText('C', {alt: true})},
      {label: t('shortcuts.switchTmuxWindow'), keys: `${metaShortcutText('←')} / ${metaShortcutText('→')}`},
      {label: t('shortcuts.pageTmuxScrollback'), keys: `${appShortcutText('↑')} / ${appShortcutText('↓')}`},
    ]},
    {section: t('shortcuts.section.editor'), items: [
      {label: t('shortcuts.saveEditor'), keys: appShortcutText('S')},
      {label: t('common.find'), keys: appShortcutText('F')},
      {label: t('common.replace'), keys: appShortcutText('H')},
      {label: t('shortcuts.goToLine'), keys: appShortcutText('G')},
      {label: t('shortcuts.toggleComment'), keys: appShortcutText('/')},
      {label: t('shortcuts.indentOutdent'), keys: 'Tab / Shift+Tab'},
      {label: t('shortcuts.undoRedo'), keys: `${appShortcutText('Z')} / ${appShortcutText('Z', {shift: true})}`},
      {label: t('shortcuts.editorNav'), keys: `${appShortcutText('[', {alt: true})} / ${appShortcutText(']', {alt: true})}`},
    ]},
    {section: t('common.diff'), items: [
      {label: t('shortcuts.undoChunk'), keys: appShortcutText('Z')},
      {label: t('shortcuts.redoChunk'), keys: appShortcutText('Z', {shift: true})},
    ]},
    {section: t('shortcuts.section.tabsPanes'), items: [
      {label: t('shortcuts.pinTab'), keys: t('shortcuts.keys.pinTab', {k: appShortcutText('K', {shift: true})})},
      {label: t('shortcuts.closeTab'), keys: t('shortcuts.keys.closeTab', {w: appShortcutText('W'), bs: `${appShortcutText('Backspace')} / ${appShortcutText('Delete')}`})},
      {label: t('shortcuts.moveTab'), keys: t('shortcuts.keys.dragTab')},
      {label: t('common.sessionActions'), keys: t('shortcuts.keys.rightClick')},
    ]},
    {section: t('shortcuts.section.finderDiffer', {finder: fileExplorerLabel(), differ: t('brand.tab.changes')}), items: [
      {label: t('shortcuts.finderDiffer.moveSelection'), keys: '↑ / ↓ / Home / End'},
      {label: t('shortcuts.finderDiffer.extendSelection'), keys: 'Shift+↑ / ↓ / Home / End'},
      {label: t('shortcuts.finderDiffer.expandCollapseFolders'), keys: '→ / ←'},
      {label: t('shortcuts.finderDiffer.renameSelectedFile'), keys: 'Return'},
      {label: t('shortcuts.finderDiffer.openSelected'), keys: appShortcutText('↓')},
      {label: t('shortcuts.finderDiffer.openEnclosingFolder'), keys: appShortcutText('↑')},
      {label: t('shortcuts.finderDiffer.quickPreview'), keys: 'Space'},
      {label: t('shortcuts.finderDiffer.selectAllRows'), keys: appShortcutText('A')},
      {label: t('shortcuts.finderDiffer.deleteSelectedRows'), keys: isMacPlatform() ? `${appShortcutText('Backspace')} / ${appShortcutText('Delete')}` : 'Delete'},
    ]},
    {section: t('shortcuts.section.menus'), items: [
      {label: t('shortcuts.menus.move'), keys: '↑ / ↓'},
      {label: t('shortcuts.menus.openSubmenu'), keys: '← / →'},
      {label: t('shortcuts.menus.runFocused'), keys: 'Enter / Space'},
      {label: t('shortcuts.menus.openSplit'), keys: appShortcutText('Enter')},
      {label: t('shortcuts.menus.close'), keys: 'Esc'},
      {label: t('shortcuts.menus.chooseNumbered'), keys: '1-9'},
    ]},
  ];
}

function keyboardLegendStatusSample(kind, text = '●', options = {}) {
  if (agentWindowVisibleTone(kind)) {
    const subwindow = options.glyph === true;
    const dotHtml = agentWindowStatusDotHtmlForTone(kind, {
      surface: subwindow ? 'subwindow' : 'legend',
      pulse: options.pulse === true,
      icon: text,
    });
    if (!subwindow) return dotHtml;
    const toneClass = agentWindowActivityToneWrapperClass(kind);
    return `<span class="tmux-window-button keyboard-legend-status-glyph" aria-hidden="true"><span class="${esc(['agent-window-activity', 'agent-window-activity--status-only', 'agent-window-activity--subwindow', toneClass].join(' '))}">${dotHtml}</span></span>`;
  }
  if (kind === 'idle') return `<span class="status-indicator status-indicator--dot status-indicator--idle">${esc(text)}</span>`;
  return `<span class="keyboard-legend-swatch keyboard-legend-swatch-${esc(kind)}" aria-hidden="true"></span>`;
}

function keyboardLegendCatalog() {
  const yoMarker = esc(t('brand.marker'));
  return [
    {section: t('legend.section.agentStatus'), items: [
      {sampleHtml: keyboardLegendStatusSample('working', '●', {glyph: true}), label: t('legend.shape.working.label'), detail: t('legend.shape.working.detail')},
      {sampleHtml: keyboardLegendStatusSample('cooldown', '●', {glyph: true}), label: t('legend.shape.cooldown.label'), detail: t('legend.shape.cooldown.detail')},
      {sampleHtml: keyboardLegendStatusSample('attention', '●', {glyph: true}), label: t('legend.shape.attention.label'), detail: t('legend.shape.attention.detail')},
    ]},
    {section: t('legend.section.colors'), items: [
      {sampleHtml: keyboardLegendStatusSample('working'), label: t('legend.color.green.label'), detail: t('legend.color.green.detail')},
      {sampleHtml: keyboardLegendStatusSample('cooldown'), label: t('legend.color.yellow.label'), detail: t('legend.color.yellow.detail')},
      {sampleHtml: keyboardLegendStatusSample('attention'), label: t('legend.color.red.label'), detail: t('legend.color.red.detail')},
      {sampleHtml: keyboardLegendStatusSample('merged'), label: t('legend.color.purple.label'), detail: t('legend.color.purple.detail')},
      {sampleHtml: keyboardLegendStatusSample('closed'), label: t('legend.color.gray.label'), detail: t('legend.color.gray.detail')},
      {sampleHtml: `<span class="keyboard-legend-meta keyboard-legend-meta-branch">${esc(t('info.field.gitBranch'))}</span>`, label: t('legend.color.metadata.label'), detail: t('legend.color.metadata.detail')},
    ]},
    {section: t('legend.section.icons'), items: [
      {sampleHtml: appMenuUiIcon('branch-info'), label: infoTabLabel(), detail: t('legend.icon.info.detail')},
      {sampleHtml: appMenuUiIcon('yoagent'), label: yoagentTabLabel(), detail: t('legend.icon.yoagent.detail')},
      {sampleHtml: appMenuUiIcon('gear'), label: t('legend.icon.gear.label'), detail: t('legend.icon.gear.detail')},
      {sampleHtml: appMenuUiIcon('tab-meta'), label: t('legend.icon.tabMetadata.label'), detail: t('legend.icon.tabMetadata.detail')},
      {sampleHtml: appMenuUiIcon('share'), label: t('legend.icon.share.label'), detail: t('legend.icon.share.detail')},
      {sampleHtml: '<span class="pane-tab-pin-icon keyboard-legend-pin" aria-hidden="true"></span>', label: t('legend.icon.pin.label'), detail: t('legend.icon.pin.detail')},
    ]},
    {section: t('legend.section.yo'), items: [
      {sampleHtml: `<span class="session-yolo-marker inactive">${yoMarker}</span>`, label: t('legend.yo.inactive.label'), detail: t('legend.yo.inactive.detail')},
      {sampleHtml: `<span class="session-yolo-marker active">${yoMarker}</span>`, label: t('legend.yo.active.label'), detail: t('legend.yo.active.detail')},
      {sampleHtml: `<span class="session-yolo-marker active working">${yoMarker}</span><span class="status-indicator status-indicator--dot status-indicator--working" aria-hidden="true">●</span>`, label: t('legend.yo.working.label'), detail: t('legend.yo.working.detail')},
      {sampleHtml: `<span class="session-yolo-marker locked">${yoMarker}</span>`, label: t('legend.yo.locked.label'), detail: t('legend.yo.locked.detail')},
    ]},
  ];
}

function keyboardHelpSectionHtml(section, className = '') {
  const extraClass = className ? ` ${className}` : '';
  return `
    <section class="keyboard-shortcuts-section${extraClass}">
      <h3>${esc(section.section)}</h3>
      <div class="keyboard-shortcuts-list">
        ${section.items.map(item => `
          <div class="keyboard-shortcut-row">
            <span>${esc(item.label)}</span>
            <kbd>${esc(item.keys)}</kbd>
          </div>`).join('')}
      </div>
    </section>`;
}

function keyboardLegendSectionHtml(section) {
  return `
    <section class="keyboard-shortcuts-section keyboard-legends-section">
      <h3>${esc(section.section)}</h3>
      <div class="keyboard-shortcuts-list keyboard-legends-list">
        ${section.items.map(item => `
          <div class="keyboard-legend-row">
            <span class="keyboard-legend-sample">${item.sampleHtml || ''}</span>
            <span class="keyboard-legend-text">
              <span class="keyboard-legend-label">${esc(item.label)}</span>
              <span class="keyboard-legend-detail">${esc(item.detail)}</span>
            </span>
          </div>`).join('')}
      </div>
    </section>`;
}

function keyboardShortcutsSectionWeight(section, isLegend = false) {
  const itemWeight = isLegend ? 1.35 : 1;
  return 1 + (Array.isArray(section?.items) ? section.items.length * itemWeight : 0);
}

function keyboardShortcutsHtml() {
  const sections = [
    ...keyboardShortcutCatalog().map(section => ({
      html: keyboardHelpSectionHtml(section),
      weight: keyboardShortcutsSectionWeight(section, false),
    })),
    ...keyboardLegendCatalog().map(section => ({
      html: keyboardLegendSectionHtml(section),
      weight: keyboardShortcutsSectionWeight(section, true),
    })),
  ];
  const columns = Array.from({length: 4}, () => ({html: [], weight: 0}));
  for (const section of sections) {
    const column = columns.reduce((best, current) => current.weight < best.weight ? current : best);
    column.html.push(section.html);
    column.weight += section.weight;
  }
  return columns.map(column => `
    <div class="keyboard-shortcuts-column">
      ${column.html.join('')}
    </div>`).join('');
}

function ensureKeyboardShortcutsOverlay() {
  if (keyboardShortcutsNode) return keyboardShortcutsNode;
  const node = document.createElement('div');
  node.className = 'app-modal-overlay keyboard-shortcuts-overlay';
  node.hidden = true;
  node.innerHTML = `
    <div class="keyboard-shortcuts-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('common.keyboardShortcuts'))}">
      <div class="keyboard-shortcuts-head">
        <h2>${esc(t('common.keyboardShortcuts'))}</h2>
        <button type="button" class="keyboard-shortcuts-close" aria-label="${esc(t('shortcuts.close'))}">×</button>
      </div>
      <div class="keyboard-shortcuts-body"></div>
    </div>`;
  node.addEventListener('mousedown', event => {
    if (event.target === node) closeKeyboardShortcutsOverlay();
  });
  node.querySelector('.keyboard-shortcuts-close')?.addEventListener('click', closeKeyboardShortcutsOverlay);
  appOverlayRootElement().appendChild(node);
  keyboardShortcutsNode = node;
  return node;
}

function renderKeyboardShortcutsOverlay(node = keyboardShortcutsNode) {
  if (!node) return false;
  const dialog = node.querySelector('.keyboard-shortcuts-dialog');
  const heading = node.querySelector('.keyboard-shortcuts-head h2');
  const close = node.querySelector('.keyboard-shortcuts-close');
  const body = node.querySelector('.keyboard-shortcuts-body');
  if (dialog) dialog.setAttribute('aria-label', t('common.keyboardShortcuts'));
  if (heading) heading.textContent = t('common.keyboardShortcuts');
  if (close) close.setAttribute('aria-label', t('shortcuts.close'));
  if (body) body.innerHTML = keyboardShortcutsHtml();
  return true;
}

function relocalizeKeyboardShortcutsOverlay() {
  return renderKeyboardShortcutsOverlay(keyboardShortcutsNode);
}

function openKeyboardShortcutsOverlay() {
  const node = ensureKeyboardShortcutsOverlay();
  closeAppMenus();
  closeCommandPalette();
  renderKeyboardShortcutsOverlay(node);
  node.hidden = false;
  node.classList.add(CLS.open);
  node.querySelector('.keyboard-shortcuts-close')?.focus?.({preventScroll: true});
}

function closeKeyboardShortcutsOverlay() {
  if (!keyboardShortcutsNode) return;
  keyboardShortcutsNode.hidden = true;
  keyboardShortcutsNode.classList.remove(CLS.open);
}

function commandPaletteAllTabItems() {
  return Array.from(new Set([...activePaneItems(), ...backgroundTabItems(), ...inactiveTabItems()]));
}

const commandPaletteFileTabValidationInflight = new Set();

function commandPaletteFilePathKnownMissing(path) {
  const normalized = normalizeDirectoryPath(path || '');
  const state = normalized ? fileStateFor(normalized) : null;
  return Boolean(state?.externalMissing === true && state?.dirty !== true);
}

function commandPaletteFilePathValidationDue(path, now = Date.now()) {
  const state = fileStateFor(path);
  if (state?.externalMissing !== true) return true;
  const checkedAt = Math.max(0, Number(state.externalMissingCheckedAt) || 0);
  return now - checkedAt >= commandPaletteMissingPathRetryMs;
}

function commandPaletteValidateFileTabPaths(items = commandPaletteAllTabItems()) {
  if (typeof fetchFilePathInfo !== 'function') return;
  for (const item of items) {
    const path = fileItemPath(item);
    const normalized = normalizeDirectoryPath(path || '');
    if (!normalized || commandPaletteFileTabValidationInflight.has(normalized) || !commandPaletteFilePathValidationDue(normalized)) continue;
    commandPaletteFileTabValidationInflight.add(normalized);
    fetchFilePathInfo(normalized, {user: true})
      .then(async info => {
        if (info?.kind === 'file') {
          await recoverOpenFileAfterMissing(normalized, info);
        } else if (info?.kind) {
          markOpenFileMissing(normalized);
        }
      })
      .catch(error => {
        if (Number(error?.status) === 404) {
          markOpenFileMissing(normalized);
          if (commandPaletteState.node && !commandPaletteState.node.hidden) renderCommandPaletteResults();
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
  if (/yo!share|sharing/i.test(text)) return appShortcutText('K');
  if (/toggle .*file explorer|toggle .*finder/i.test(text)) return appShortcutText('B');
  if (/preferences/i.test(text)) return appShortcutText(',');
  if (/keyboard shortcuts/i.test(text)) return '?';
  if (/close menu|dialog/i.test(text)) return 'Esc';
  return /\b(?:Ctrl|Cmd|Shift|Esc|Alt)[^,;]*/.exec(detail)?.[0] || '';
}

// a short localized label for an editor/preview view mode, shown as a chip on a deduped row.
function commandPaletteViewModeLabel(mode) {
  if (mode === 'preview') return t('common.preview');
  if (mode === 'split') return t('editor.mode.split');
  if (mode === 'diff') return t('common.diff');
  return t('common.edit');
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
    group: t('common.tabsLabel'),
    category: 'pane',
    label: itemLabel(item),
    detail: commandPaletteTabDetail(item),
    key: `tab:${item}`,
    targetItem: item,
    mtime: commandPalettePaneMtime(item),
    searchFields: tabSearchFields(item),
    keybinding: 'Enter',
    run: () => {
      if (isFileExplorerItem(item)) return selectSession(item, {userInitiated: true});
      return activateTabInExistingPane(item, {preferFocused: true, userInitiated: true});
    },
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
      collapsedPreferenceSections.delete(section.id);
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
  if (commandPaletteFilePathKnownMissing(path)) return [];
  const kind = commandPaletteDropActionKind(path);
  const paths = [path];
  const category = fileDropCategory(path, kind);
  const session = commandPaletteDropActionSession();
  const agentKind = session ? sessionAgentKind(session) : '';
  return dropActionsFor(category, agentKind, paths.length, {pathInserted: false, includeShellForAgents: true})
    .map(action => {
      const needsTerminal = action.kind !== 'server';
      const label = dropActionDisplayLabel(action);
      return {
        group: t('palette.group.fileActions'),
        category: 'command',
        label,
        detail: compactHomePath(path),
        key: `drop-action:${action.id}:${path}`,
        keybinding: 'Enter',
        disabled: needsTerminal && !session,
        searchFields: [t('palette.group.fileActions'), label, path, compactHomePath(path)],
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
  return normalizeDirectoryPath(fileQuickOpenState.root || repoRoot || '');
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

function fileQuickOpenRootsForSearch(root = fileQuickOpenState.root || fileQuickOpenRootForSearch(), query = commandPaletteSearchQuery()) {
  return fileExplorerIndexedSearchRoots(root, fileQuickOpenExtraRootsForSearchQuery(query));
}

function fileQuickOpenScopeLabel(root = fileQuickOpenState.root || fileQuickOpenRootForSearch()) {
  const roots = fileQuickOpenRootsForSearch(root);
  if (roots.length <= 1) return compactHomePath(roots[0] || root || '/');
  return tPlural('palette.scope.indexedRoot', roots.length - 1, {path: compactHomePath(roots[0])});
}

function fileQuickOpenQueryParts(query = commandPaletteState.query) {
  const raw = String(query || '').trim();
  const match = raw.match(/^(.*?)(?::(\d+))?$/);
  return {
    query: (match?.[1] || '').trim(),
    line: match?.[2] ? Math.max(1, Number(match[2])) : null,
    commandMode: raw.startsWith('>'),
    symbolMode: raw.startsWith('@'),
  };
}

function fileQuickOpenSearchText(query = commandPaletteState.query) {
  return fileQuickOpenQueryParts(query).query.replace(/[:.]+$/g, '').trim();
}

function fileQuickOpenPathQuery(query = commandPaletteState.query) {
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
  commandPaletteState.query = `${directory}${directory.endsWith('/') ? '' : '/'}`;
  commandPaletteState.index = 0;
  const input = commandPaletteState.node?.querySelector?.('.command-palette-input');
  if (input) input.value = commandPaletteState.query;
  renderCommandPaletteResults();
  refreshFileQuickOpenCandidates(commandPaletteState.query);
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
  const requested = String(commandPaletteState.targetSlot || '');
  if (requested && layoutSlotKeys().includes(requested)) return requested;
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
  if (previewMediaKindForPath(fullPath) === 'image') {
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
    group: options.group || t('palette.group.files'),
    category: 'file',
    label: cursorReference?.label || (isDir ? `${label}/` : label),
    detail,
    key: `file:${path}`,
    path,
    mtime: commandPaletteNumericTime(options.mtime || options.mtimeNs || 0),
    iconText: isDir ? disclosureTriangleCollapsedGlyph : fileIconFor(label),
    keybinding: isDir ? 'Enter' : `${appShortcutText('Enter')} ${t('editor.mode.split')}`,
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
    label: t(previewMediaKindForPath(path) === 'image' ? 'palette.openImage' : 'palette.openFile', {name}),
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
    const match = fileQuickOpenState.candidates.find(entry => entry.kind === 'dir' && String(entry.name || '').toLowerCase() === filter);
    if (match?.path) target = match.path;
  }
  if (!target) return null;
  return {
    group: t('palette.group.files'),
    label: t('palette.openFolder', {name: fileExplorerLabel()}),
    detail: compactHomePath(target),
    key: `open-folder:${target}`,
    iconText: disclosureTriangleCollapsedGlyph,
    keybinding: appShortcutText('Enter'),
    pinTop: true,
    searchFields: [t('palette.openFolder', {name: fileExplorerLabel()}), target],
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
  for (const file of fileQuickOpenState.candidates) {
    const path = file.path || '';
    if (!path) continue;
    if (!fileQuickOpenDoitCandidateAllowed(file)) continue;
    // Only an entry that ACTUALLY came from an indexed-root search carries an indexed_root; path-mode
    // listing entries have none. Guard the empty case — normalizeStoredFileExplorerIndexedDir('') returns
    // '/' (empty -> root), which used to mislabel every path-mode directory row as "Indexed /".
    const indexedRoot = file.indexed_root ? normalizeStoredFileExplorerIndexedDir(file.indexed_root) : '';
    const baseRoot = normalizeStoredFileExplorerIndexedDir(fileQuickOpenState.root || '');
    const externalIndexed = Boolean(indexedRoot && indexedRoot !== baseRoot);
    if (externalIndexed && !fileQuickOpenExternalIndexedMatchAllowed(file)) continue;
    const isImage = (file.kind || 'file') !== 'dir' && previewMediaKindForPath(path) === 'image';
    if (isImage) imageIndex += 1;
    add(fileQuickOpenItem(path, {
      group: externalIndexed ? t('common.indexedPath', {path: compactHomePath(indexedRoot)}) : t('palette.group.files'),
      relativePath: file.relative_path || file.name || '',
      kind: file.kind || 'file',
      imageIndex,
      mtime: file.mtime || 0,
      mtimeNs: file.mtime_ns || 0,
      sortBonus: (externalIndexed ? -250 : 250) + (file.uploaded === true ? -500 : 0),
    }));
  }
  if (fileQuickOpenState.error) {
    const errorText = userMessageText(fileQuickOpenState.error, t('common.searchFailed'));
    items.push({
      group: t('palette.group.files'),
      label: t('common.searchFailed'),
      detail: errorText,
      searchFields: [t('common.searchFailed'), errorText],
      disabled: true,
      run: null,
    });
  }
  if (fileQuickOpenState.loading) {
    items.push({
      group: t('palette.group.files'),
      label: t('palette.searchingFiles'),
      detail: compactHomePath(fileQuickOpenState.root),
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

function commandPaletteCandidateItems(mode = commandPaletteMode, rawQuery = commandPaletteState.query) {
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
  return commandPaletteCandidateItems(commandPaletteMode, commandPaletteState.query);
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
  if (surface === 'files') return {primary: 'file', secondary: ['pane', 'command', 'setting']};
  if (surface === 'command') return {primary: 'pane', secondary: ['command', 'setting', 'file']};
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
  const secondaryDomains = Array.isArray(domains.secondary) ? domains.secondary : [domains.secondary].filter(Boolean);
  const rowsByDomain = new Map(secondaryDomains.map(domain => [domain, ranked
    .map((item, index) => ({item, index}))
    .filter(row => commandPaletteItemDomain(row.item) === domain)]));
  const secondaryCandidates = [];
  const selectedItems = new Set();
  while (secondaryCandidates.length < maxSecondary) {
    let added = false;
    for (const domain of secondaryDomains) {
      const row = (rowsByDomain.get(domain) || []).find(candidate => !selectedItems.has(candidate.item));
      if (!row) continue;
      secondaryCandidates.push(row);
      selectedItems.add(row.item);
      added = true;
      if (secondaryCandidates.length >= maxSecondary) break;
    }
    if (!added) break;
  }
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
  const selectedForMove = new Set(selected.map(row => row.item));
  const result = ranked.filter(item => !selectedForMove.has(item));
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
    + commandPalettePaneExactIdentifierBonus(item, query)
    + commandPalettePaneNameContiguousBonus(item, query, options)
    + commandPaletteFinderAliasBonus(item, query, options)
    + commandPaletteFileNameBonus(item, query)
    + commandPaletteRecencyBonus(item, options)
    + commandPaletteRepoAffinityBonus(item, options);
  if (!String(query || '').trim()) return bonus;
  const base = fuzzySearchScore(query, item.searchFields || [item.label, item.detail, item.group]);
  return Number.isFinite(base) ? base + bonus : base;
}

function commandPalettePaneNameContiguousBonus(item, query, options = {}) {
  if (commandPaletteItemDomain(item) !== 'pane') return 0;
  const tokens = String(query || '').trim().toLowerCase().split(/\s+/).filter(Boolean);
  const label = String(item.label || '').toLowerCase();
  if (!tokens.length || !label || !tokens.every(token => label.includes(token))) return 0;
  const surface = commandPaletteSurface(options);
  return searchRankWeights.paneNameContiguous[surface] || 0;
}

function commandPaletteIdentifierKey(value) {
  return String(value || '').trim().toUpperCase().replace(/^PR[\s#-]*/, '').replace(/^#/, '').replace(/[^A-Z0-9]+/g, '');
}

function commandPaletteIdentifierField(value) {
  return /^(?:#?\d{3,}|PR[\s#-]*\d{3,}|[A-Z][A-Z0-9]+-\d+)$/i.test(String(value || '').trim());
}

function commandPalettePaneExactIdentifierBonus(item, query) {
  if (commandPaletteItemDomain(item) !== 'pane') return 0;
  const needle = commandPaletteIdentifierKey(query);
  if (!needle) return 0;
  return (item.searchFields || []).some(field => commandPaletteIdentifierField(field) && commandPaletteIdentifierKey(field) === needle)
    ? searchRankWeights.paneExactIdentifier
    : 0;
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
  const aliases = [t('palette.group.files'), fileExplorerLabel(), 'file', 'files', 'finder', 'file explorer']
    .map(alias => String(alias || '').trim().toLowerCase().replace(/\s+/g, ' '))
    .filter(Boolean);
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

function commandPaletteSearchQuery(query = commandPaletteState.query, mode = commandPaletteMode) {
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
  const q = commandPaletteState.query.trim();
  if (q.startsWith('>')) return t('palette.placeholderCommand');
  if (q.startsWith('@')) return t('palette.symbolUnavailable');
  return t('palette.placeholderWithFiles');
}

function commandPaletteLabel() {
  // Identical aria label for both entry points.
  return t('palette.quickOpen');
}

function commandPaletteEmptyText() {
  if (fileQuickOpenState.loading) return t('search.searching');
  if (commandPaletteState.query.trim().startsWith('@')) return t('palette.symbolUnavailable');
  return t('palette.noMatches');
}

function commandPaletteStatusText() {
  return fileQuickOpenState.loading ? t('palette.searchingFiles') : '';
}

function commandPaletteLoadingTextHtml(text) {
  return textWithMovingEllipsisHtml(text, 'command-palette-loading-dots');
}

function commandPaletteStatusHtml() {
  return fileQuickOpenState.loading ? commandPaletteLoadingTextHtml(commandPaletteStatusText()) : '';
}

function commandPaletteResultsHtml(items, query) {
  return items.map((item, index) => `
    <button type="button" class="command-palette-row${index === commandPaletteState.index ? ' active' : ''}" data-command-index="${index}" role="option" aria-selected="${index === commandPaletteState.index ? 'true' : 'false'}"${item.disabled ? ' disabled' : ''}>
      <span class="command-palette-group">${esc(item.group)}</span>
      <span class="command-palette-main"><span class="command-palette-title">${item.iconText ? `<span class="command-palette-file-icon" aria-hidden="true">${esc(item.iconText)}</span>` : ''}<span class="command-palette-label">${commandPaletteItemLabelHtml(item, query)}</span>${(item.viewModes && item.viewModes.length) ? `<span class="command-palette-views">${item.viewModes.map(v => `<span class="command-palette-view-chip" role="button" tabindex="-1" data-view-item="${esc(v.item)}" data-view-mode="${esc(v.mode)}" title="${esc(t('palette.openView', {view: v.label}))}">${esc(v.label)}</span>`).join('')}</span>` : ''}</span><span class="command-palette-detail">${fuzzyHighlightHtml(query, item.detail || '')}</span></span>
      <span class="command-palette-keybinding">${esc(item.keybinding || '')}</span>
    </button>`).join('');
}

function commandPaletteItemLabelHtml(item, query) {
  if (item?.loading === true) return commandPaletteLoadingTextHtml(item.label);
  return fuzzyHighlightHtml(query, item.label);
}

function ensureCommandPalette() {
  if (commandPaletteState.node) return commandPaletteState.node;
  const node = document.createElement('div');
  node.className = 'app-modal-overlay command-palette';
  node.hidden = true;
  node.innerHTML = `
    <div class="command-palette-dialog" role="dialog" aria-modal="true" aria-label="${esc(t('common.commandPalette'))}">
      <input type="search" class="command-palette-input" placeholder="${esc(t('palette.placeholder'))}" aria-label="${esc(t('palette.placeholder'))}">
      <div class="command-palette-status" aria-live="polite" hidden></div>
      <div class="command-palette-results" role="listbox"></div>
    </div>`;
  node.addEventListener('mousedown', event => {
    if (event.target === node) closeCommandPalette();
  });
  const input = node.querySelector('.command-palette-input');
  input.addEventListener('input', () => {
    commandPaletteState.query = input.value || '';
    commandPaletteState.index = 0;
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
      commandPaletteState.index = commandPaletteState.items.length ? (commandPaletteState.index + 1) % commandPaletteState.items.length : 0;
      renderCommandPaletteResults();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      commandPaletteState.index = commandPaletteState.items.length ? (commandPaletteState.index - 1 + commandPaletteState.items.length) % commandPaletteState.items.length : 0;
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
    commandPaletteState.index = Number(row.dataset.commandIndex || 0);
    invokeCommandPaletteSelection();
  });
  appOverlayRootElement().appendChild(node);
  commandPaletteState.node = node;
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
    input.setAttribute('aria-busy', fileQuickOpenState.loading ? 'true' : 'false');
  }
  if (status) {
    const text = commandPaletteStatusText();
    const html = commandPaletteStatusHtml();
    status.hidden = !html;
    status.setAttribute('aria-label', text);
    status.innerHTML = html;
  }
  commandPaletteState.items = commandPaletteRankItems(commandPaletteItems(), query).slice(0, 60);
  commandPaletteState.index = Math.min(commandPaletteState.index, Math.max(0, commandPaletteState.items.length - 1));
  if (!commandPaletteState.items.length) {
    results.innerHTML = `<div class="command-palette-empty">${esc(commandPaletteEmptyText())}</div>`;
    return;
  }
  results.innerHTML = commandPaletteResultsHtml(commandPaletteState.items, query);
  results.querySelector('.command-palette-row.active')?.scrollIntoView?.({block: 'nearest'});
}

function openCommandPalette(options = {}) {
  const node = ensureCommandPalette();
  closeAppMenus();
  abortFileQuickOpenSearch();
  commandPaletteMode = options.mode === 'files' ? 'files' : 'command';
  commandPaletteState.targetSlot = String(options.targetSlot || '');
  commandPaletteState.query = '';
  commandPaletteState.index = 0;
  node.hidden = false;
  node.classList.add(CLS.open);
  const input = node.querySelector('.command-palette-input');
  input.value = '';
  // Reset file-search state for BOTH entry points so a typed query can blend files in either mode.
  fileQuickOpenState.root = fileQuickOpenRootForSearch();
  fileQuickOpenState.candidates = [];
  fileQuickOpenState.loading = false;
  fileQuickOpenState.error = '';
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
  if (!commandPaletteState.node) return;
  commandPaletteState.node.hidden = true;
  commandPaletteState.node.classList.remove(CLS.open);
  if (fileQuickOpenState.debounce) clearTimeout(fileQuickOpenState.debounce);
  fileQuickOpenState.debounce = null;
  abortFileQuickOpenSearch();
}

function focusCommandPaletteTarget(item) {
  const target = item?.targetItem;
  if (!isLayoutItem(target)) return;
  focusPanel(target, {userInitiated: true});
  renderPaneTabStrips();
  requestAnimationFrame(() => focusPanel(target, {userInitiated: true}));
}

async function invokeCommandPaletteSelection(event = null) {
  const item = commandPaletteState.items[commandPaletteState.index];
  if (!item || item.disabled) return;
  rememberCommandPaletteItem(item);
  closeCommandPalette();
  const action = appModifier(event) && item.splitRun ? item.splitRun : item.run;
  await Promise.resolve(action?.());
  focusCommandPaletteTarget(item);
}

function scheduleFileQuickOpenSearch(options = {}) {
  if (fileQuickOpenState.debounce) clearTimeout(fileQuickOpenState.debounce);
  const run = () => {
    fileQuickOpenState.debounce = null;
    const q = commandPaletteState.query.trim();
    if (!q.startsWith('>') && !q.startsWith('@')) refreshFileQuickOpenCandidates(commandPaletteState.query);
  };
  if (options.immediate) run();
  else fileQuickOpenState.debounce = setTimeout(run, fileQuickOpenDebounceMs);
}

function abortFileQuickOpenSearch() {
  if (fileQuickOpenState.abortController) {
    try { fileQuickOpenState.abortController.abort(); } catch (_) {}
  }
  fileQuickOpenState.abortController = null;
  fileQuickOpenState.requestId += 1;
  fileQuickOpenState.loading = false;
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
  const root = fileQuickOpenState.root || fileQuickOpenRootForSearch();
  if (!root) return;
  abortFileQuickOpenSearch();
  const requestId = ++fileQuickOpenState.requestId;
  fileQuickOpenState.abortController = typeof AbortController === 'function' ? new AbortController() : null;
  const fetchOptions = fileQuickOpenState.abortController ? {signal: fileQuickOpenState.abortController.signal} : {};
  fileQuickOpenState.loading = true;
  renderCommandPaletteResults();
  try {
    const pathQuery = fileQuickOpenPathQuery(query);
    if (pathQuery.active) {
      const payload = await apiFetchJson(`/api/fs/list?path=${encodeURIComponent(pathQuery.directory || '/')}`, fetchOptions);
      if (requestId !== fileQuickOpenState.requestId) return;
      fileQuickOpenState.root = payload.path || pathQuery.directory || root;
      const filter = String(pathQuery.filter || '').toLowerCase();
      fileQuickOpenState.candidates = (Array.isArray(payload.entries) ? payload.entries : [])
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
          const payload = await apiFetchJson(`/api/fs/search?root=${encodeURIComponent(searchRoot)}&query=${encodeURIComponent(commandPaletteSearchQuery(query))}&limit=500${recursive}`, fetchOptions);
          return {ok: true, root: normalizeStoredFileExplorerIndexedDir(payload.root || payload.root_realpath || searchRoot) || searchRoot, files: Array.isArray(payload.files) ? payload.files : []};
        } catch (error) {
          return {ok: false, root: searchRoot, error};
        }
      }));
      if (requestId !== fileQuickOpenState.requestId) return;
      const successful = results.filter(result => result.ok);
      if (!successful.length) {
        const firstError = results.find(result => result.error)?.error;
        if (firstError) throw firstError;
        const fallback = t('common.searchFailed');
        fileQuickOpenState.error = userMessageSnapshot('', {key: 'common.searchFailed', params: {}, fallback});
        return;
      }
      fileQuickOpenState.root = root;
      fileQuickOpenState.candidates = dedupeFileSearchResults(
        successful.flatMap(result => result.files.map(file => ({...file, indexed_root: result.root}))),
      );
    }
    fileQuickOpenState.error = '';
  } catch (error) {
    if (requestId !== fileQuickOpenState.requestId) return;
    if (error?.name === 'AbortError') return;
    fileQuickOpenState.candidates = [];
    fileQuickOpenState.error = userMessageSnapshot(error, {
      key: 'common.searchFailed',
      params: {},
      fallback: t('common.searchFailed'),
    });
  } finally {
    if (requestId === fileQuickOpenState.requestId) {
      fileQuickOpenState.abortController = null;
      fileQuickOpenState.loading = false;
      renderCommandPaletteResults();
    }
  }
}

function shouldNotifyTransitionKey(key) {
  const defaultTransitions = [STATE_KEY.needsInput, STATE_KEY.needsApproval, STATE_KEY.blocked];
  const configured = initialSetting('notifications.notify_transitions', defaultTransitions);
  const transitions = Array.isArray(configured) ? configured : defaultTransitions;
  return transitions.includes(key);
}

function shouldNotifyState(state) {
  return shouldNotifyTransitionKey(state.key);
}

function sendBrowserNotification(title, options = {}) {
  const targetItem = String(options.targetItem || options.session || '').trim();
  if (targetItem && notificationTargetIsFocused(targetItem)) return null;
  const {onClick, session, targetItem: _targetItem, url, ...notificationOptions} = options;
  const icon = notificationOptions.icon === undefined
    ? renderBrowserAppIconDataUrl({size: 192, showBadge: false})
    : '';
  const notification = new Notification(title, icon ? {icon, ...notificationOptions} : notificationOptions);
  if (targetItem) {
    if (!browserNotificationsByTarget.has(targetItem)) browserNotificationsByTarget.set(targetItem, new Set());
    browserNotificationsByTarget.get(targetItem).add(notification);
    notification.onclose = () => {
      const notifications = browserNotificationsByTarget.get(targetItem);
      notifications?.delete(notification);
      if (!notifications?.size) browserNotificationsByTarget.delete(targetItem);
    };
  }
  notification.onclick = () => {
    window.focus();
    notification.close?.();
    if (typeof onClick === 'function') onClick();
    else if (session) selectSession(session, {userInitiated: true});
    // a watched-PR notification opens the PR (no session to focus); safe blank-target open.
    else if (url) {
      try { window.open(url, '_blank', 'noopener,noreferrer'); } catch (_) {}
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

// This is the sole product-policy owner for transient notifications.  Producers supply content
// and an optional target/action; they do not decide whether an event is pane-local, global, OS
// deliverable, suppressible, or coalesced.  That separation keeps a new notification from quietly
// becoming a third routing model.
const notificationEventDefinitions = Object.freeze({
  sessionAttention: Object.freeze({scope: 'global', target: 'session', system: true, suppressFocused: true, priority: 'attention', coalesce: true}),
  agentAttention: Object.freeze({scope: 'global', target: 'session', system: true, suppressFocused: true, priority: 'attention', coalesce: true, persistent: true}),
  agentDone: Object.freeze({scope: 'global', target: 'session', system: true, suppressFocused: true, priority: 'done', coalesce: true}),
  terminalConnection: Object.freeze({scope: 'global', target: 'session', system: false, suppressFocused: true, priority: 'connection', coalesce: true}),
  chatMessage: Object.freeze({scope: 'global', target: 'chat', system: true, suppressFocused: true, priority: 'message', coalesce: true}),
  yoagentJob: Object.freeze({scope: 'global', target: 'session', system: true, suppressFocused: true, priority: 'job', coalesce: true}),
  watchedPullRequest: Object.freeze({scope: 'global', system: true, priority: 'pullRequest', coalesce: true}),
  update: Object.freeze({scope: 'global', system: false, priority: 'update', coalesce: true}),
  startupTip: Object.freeze({scope: 'global', system: false, priority: 'tip', coalesce: true}),
  notificationTest: Object.freeze({scope: 'global', system: true, priority: 'test', coalesce: true}),
  // Uploads retain their stacked file-result renderer, but that renderer is still dispatched
  // through this policy row so its pane scope cannot become a second routing decision.
  uploadResult: Object.freeze({scope: 'pane', target: 'session', system: false, priority: 'operation', deliver: ({target, payload}) => deliverUploadResultNotification(target, payload.uploadPayload, payload.inserted === true)}),
  fileTransfer: Object.freeze({scope: 'pane', target: 'operation', system: false, priority: 'operation'}),
  fileOpen: Object.freeze({scope: 'pane', target: 'finder', system: false, priority: 'operation'}),
  yoloRules: Object.freeze({scope: 'pane', target: 'preferences', system: false, priority: 'operation'}),
  previewOpen: Object.freeze({scope: 'pane', target: 'operation', system: false, priority: 'operation'}),
});

function notificationEventDefinition(type) {
  return notificationEventDefinitions[type] || null;
}

function notificationEventTarget(definition, payload = {}) {
  if (definition?.target === 'chat') return chatItemId;
  if (definition?.target === 'finder') return fileExplorerItemId;
  if (definition?.target === 'preferences') return prefsItemId;
  return String(payload.targetItem || payload.session || payload.item || '').trim();
}

function notificationEventContainer(definition, target) {
  return definition?.scope === 'pane' ? displayToastContainer(target) : attentionAlerts;
}

function notificationEventKey(type, definition, target, payload = {}) {
  if (payload.coalesceKey) return String(payload.coalesceKey);
  if (definition?.coalesce !== true) return '';
  return `${type}:${target || String(payload.key || '')}`;
}

function dismissCoalescedToast(key) {
  if (!key) return;
  for (const [id, record] of toastRecords.entries()) {
    if (record.node?.dataset?.toastCoalesceKey === key) removeAttentionAlert(id);
  }
}

function emitNotification(type, payload = {}) {
  const definition = notificationEventDefinition(type);
  if (!definition) throw new Error(`unknown notification event: ${type}`);
  const target = notificationEventTarget(definition, payload);
  const key = notificationEventKey(type, definition, target, payload);
  if (definition.deliver) return {inApp: definition.deliver({target, payload}), system: null, suppressed: false};
  const targetItem = definition.suppressFocused === true ? target : '';
  if (targetItem && notificationTargetIsFocused(targetItem)) {
    dismissNotificationsForTarget(targetItem);
    return {inApp: null, system: null, suppressed: true};
  }
  let inApp = null;
  if (payload.inApp !== false && notificationDeliveryEnabled('inApp')) {
    dismissCoalescedToast(key);
    inApp = showToast(payload.title || '', payload.lines || payload.body || '', {
      container: notificationEventContainer(definition, target),
      targetItem,
      onClick: payload.onClick,
      actions: payload.actions,
      className: payload.className,
      countdownMs: definition.persistent === true ? (payload.countdownMs || 4 * 60 * 60 * 1000) : payload.countdownMs,
    });
    if (inApp) {
      inApp.dataset.toastEvent = type;
      inApp.dataset.toastPriority = definition.priority;
      if (target) inApp.dataset.toastTargetItem = target;
      if (key) inApp.dataset.toastCoalesceKey = key;
    }
  }
  let system = null;
  if (definition.system === true && payload.system !== false && notificationDeliveryEnabled('system') && 'Notification' in window && Notification.permission === 'granted') {
    system = sendBrowserNotification(payload.systemTitle || payload.title || '', {
      body: payload.systemBody || payload.body || toastTextLines(payload.lines || '').join('\n'),
      tag: payload.systemTag || key || `${type}:${Date.now()}`,
      renotify: payload.renotify === true,
      targetItem,
      session: payload.session,
      onClick: payload.onClick,
      url: payload.url,
    });
  }
  return {inApp, system, suppressed: false};
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
    if (typeof item?.render === 'function') item.render(line);
    else line.textContent = lineText;
    bodyNode.appendChild(line);
  }
}

function normalizeToastLine(item, options = {}) {
  const objectItem = typeof item === 'object' && item !== null;
  const text = objectItem ? item.text : item;
  return {
    text: compactToastText(text),
    countdownMs: objectItem ? item.countdownMs : options.countdownMs,
    render: objectItem && typeof item.render === 'function' ? item.render : null,
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

function clearToastRemovalTimer(id) {
  const record = toastRecords.get(id);
  if (!record?.timer) return;
  clearTimeout(record.timer);
  record.timer = null;
}

function scheduleToastRemoval(id, node, remainingMs) {
  const delay = Math.max(1, Number(remainingMs) || 1);
  let record = toastRecords.get(id);
  if (!record) {
    record = {node, timer: null};
    toastRecords.set(id, record);
  } else {
    record.node = node;
  }
  clearToastRemovalTimer(id);
  node.dataset.toastRemainingMs = String(delay);
  node.dataset.toastRemovalStartedAt = String(Date.now());
  record.timer = window.setTimeout(() => removeAttentionAlert(id), delay);
}

function pauseToastRemoval(id, node, reason) {
  if (!node || node.classList.contains('kept')) return;
  node.dataset[`toastPause${reason}`] = 'true';
  if (node.classList.contains('toast-countdown-paused')) return;
  const remainingMs = Number(node.dataset.toastRemainingMs);
  const startedAt = Number(node.dataset.toastRemovalStartedAt);
  const elapsedMs = Number.isFinite(startedAt) ? Math.max(0, Date.now() - startedAt) : 0;
  node.dataset.toastRemainingMs = String(Math.max(1, (Number.isFinite(remainingMs) ? remainingMs : toastDurationMs) - elapsedMs));
  clearToastRemovalTimer(id);
  node.classList.add('toast-countdown-paused');
}

function resumeToastRemoval(id, node, reason) {
  if (!node || node.classList.contains('kept')) return;
  delete node.dataset[`toastPause${reason}`];
  if (node.dataset.toastPausePointer || node.dataset.toastPauseFocus || !node.classList.contains('toast-countdown-paused')) return;
  node.classList.remove('toast-countdown-paused');
  scheduleToastRemoval(id, node, Number(node.dataset.toastRemainingMs));
}

function showToast(title, lines, options = {}) {
  const targetItem = String(options.targetItem || '').trim();
  if (targetItem && notificationTargetIsFocused(targetItem)) {
    dismissNotificationsForTarget(targetItem);
    return null;
  }
  const container = options.container || attentionAlerts;
  if (!container) return null;
  const id = ++attentionAlertSequence;
  const node = document.createElement('div');
  node.className = options.className || 'attention-alert toast';
  node.dataset.alertId = String(id);
  if (targetItem) node.dataset.toastTargetItem = targetItem;
  const bodyNode = ensureToastShell(node, {
    title,
    closeLabel: options.closeLabel,
    keepLabel: options.keepLabel,
    actions: options.actions,
    onKeep: () => {
      clearToastRemovalTimer(id);
      options.onKeep?.();
    },
    onClose: () => {
      options.onClose?.();
      removeAttentionAlert(id);
    },
  });
  const toastLines = Array.isArray(lines) ? lines : (lines && typeof lines === 'object' ? [lines] : toastTextLines(lines));
  renderToastLines(bodyNode, toastLines, {
    countdownMs: options.countdownMs || toastDurationMs,
  });
  node.addEventListener('click', event => {
    if (event.target.closest('[data-toast-close], .toast-actions')) return;
    options.onClick?.();
  });
  node.addEventListener('pointerenter', () => pauseToastRemoval(id, node, 'Pointer'));
  node.addEventListener('pointerleave', () => resumeToastRemoval(id, node, 'Pointer'));
  node.addEventListener('focusin', () => pauseToastRemoval(id, node, 'Focus'));
  node.addEventListener('focusout', event => {
    if (!node.contains(event.relatedTarget)) resumeToastRemoval(id, node, 'Focus');
  });
  container.appendChild(node);
  while (container.children.length > 5) {
    const first = container.firstElementChild;
    if (!first) break;
    removeAttentionAlert(Number(first.dataset.alertId || 0));
  }
  // remove the toast when its countdown bar finishes — honor options.countdownMs (the
  // reconnect toast animates over that, not the fixed toastDurationMs) so the bar and removal align.
  scheduleToastRemoval(id, node, options.countdownMs || toastDurationMs);
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
    {title: t('common.rsyncLargeFiles'), lines: [t('startupHelper.tip.largeUpload.body')]},
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

function startupHelperNavigationGroup(index, total, showRelativeTip) {
  const group = document.createElement('span');
  group.className = 'startup-helper-nav';
  group.setAttribute('role', 'group');
  group.setAttribute('aria-label', t('startupHelper.navigation'));
  group.append(
    makeButton({
      label: '<',
      className: 'startup-helper-nav-button',
      title: t('startupHelper.action.previous'),
      ariaLabel: t('startupHelper.action.previous'),
      onClick: event => {
        event.stopPropagation();
        showRelativeTip(-1);
      },
    }),
    makeButton({
      label: '>',
      className: 'startup-helper-nav-button',
      title: t('startupHelper.action.next'),
      ariaLabel: t('startupHelper.action.next'),
      onClick: event => {
        event.stopPropagation();
        showRelativeTip(1);
      },
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
  const offAction = makeButton({
    label: t('startupHelper.action.offForever'),
    onClick: event => {
      event.stopPropagation();
      startupHelpersEnabled = false;
      closeStartupHelperToast(node);
      saveSettingsPatch(settingPatch('general.startup_tips', false))
        .then(() => { statusEl.textContent = t('startupHelper.status.disabled'); })
        .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
    },
  });
  node = emitNotification('startupTip', {
    title: startupHelperPromptTitle(index, tips.length, tip), lines: tip.lines,
    className: 'attention-alert toast startup-helper-toast',
    actions: [navAction, offAction],
    countdownMs: 45000,
    coalesceKey: 'startup-tip',
  }).inApp;
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

function notificationTargetIsFocused(item) {
  const target = String(item || '').trim();
  if (!target || document.visibilityState !== 'visible' || !itemIsActivePaneTab(target)) return false;
  // A status render can briefly blur xterm even though the user remains on this pane. The shared
  // logical active item survives that DOM churn, so notifications about the pane being viewed are
  // already acknowledged and must not appear inside that same pane.
  return focusedPanelItem === target || focusedTerminal === target;
}

function compactNotificationTitle(scope, message, options = {}) {
  const suffix = String(message || '').trim();
  const label = String(scope || '').trim();
  const product = t('app.documentTitle');
  if (options.inApp === true) return suffix || label;
  if (!label) return suffix ? `${product} ${suffix}` : product;
  return suffix ? `${product}[${label}] ${suffix}` : `${product}[${label}]`;
}

function sessionNotificationScope(session) {
  const label = sessionLabel(session);
  const desc = sessionTabDescription(session, transcriptMetadataState.payload.sessions?.[session]);
  return desc ? `${label} ${desc}` : label;
}

function sessionNotificationTitle(session, state, options = {}) {
  return compactNotificationTitle(sessionNotificationScope(session), state?.label || '', options);
}

function hostNotificationTitle(message, options = {}) {
  return compactNotificationTitle(serverHostname, message, options);
}

function attentionToastAgent(session, state) {
  const agent = state?.attentionAgent;
  if (agentWindowIsAttentionState(agent?.state)) {
    return {agent, item: state?.attentionAgentItem || null};
  }
  const info = transcriptMetadataState.payload.sessions?.[session];
  const summary = typeof sessionAgentWindowStatusSummary === 'function'
    ? sessionAgentWindowStatusSummary(session, info, autoApproveStates.get(session))
    : null;
  return agentWindowIsAttentionState(summary?.agent?.state) ? summary : null;
}

function attentionToastReason(state, agent) {
  const reason = String(state?.reason || '').trim();
  const label = String(agent?.window_label || agent?.window || '').trim();
  const prefix = label ? `${label}:` : '';
  return prefix && reason.startsWith(prefix) ? reason.slice(prefix.length).trim() : reason;
}

function attentionToastTitle(session, state) {
  const attention = attentionToastAgent(session, state);
  const agent = attention?.agent;
  const windowLabel = String(agent?.window_label || agent?.window || '').trim();
  return windowLabel ? `[${sessionLabel(session)}] ${windowLabel}: ${state?.label || ''}` : sessionNotificationTitle(session, state, {inApp: true});
}

async function focusAgentToastTarget(session, agent) {
  await selectSession(session, {userInitiated: true});
  const windowIndex = agentWindowIndex(agent);
  if (windowIndex === null) return;
  tmuxWindow(session, {windowIndex}, t('terminal.window.title', {name: windowIndex}));
}

function agentToastTargetLine(session, agent, options = {}) {
  const reason = String(options.reason || '').trim();
  if (!agent || typeof tmuxWindowButtonHtml !== 'function' || typeof tmuxPaneTabTokenHtml !== 'function') return reason;
  const visibleName = String(agent.window_label || agentWindowCanonicalLabel(agent.window_index ?? agent.window, agent.kind, agent.kind)).trim();
  if (!visibleName) return reason;
  return {
    text: reason,
    render: line => {
      line.classList.add('toast-line--attention');
      const marker = document.createElement('span');
      marker.className = 'attention-toast-controls';
      const info = transcriptMetadataState.payload.sessions?.[session];
      const auto = autoApproveStates.get(session)?.enabled === true;
      const status = options.state || sessionState(session, info);
      const tabHtml = tmuxPaneTabTokenHtml(session, {
        tag: 'span',
        action: false,
        active: true,
        info,
        state: status,
        auto,
        classes: ['attention-toast-session-tab'],
      });
      const windowHtml = tmuxWindowButtonHtml({
        tag: 'span',
        visibleName,
        showNumberLabel: false,
        active: true,
        ariaPressed: false,
        attrs: ['role="img"'],
        classes: ['attention-toast-agent-button'],
        agentStatus: agent,
        agentKey: agent.kind,
        session,
        activityAnimate: false,
        activityIconHtml: agentWindowActivityIconHtml(agent.kind, agent.state, agentWindowIdleSeconds(agent), {
          ...agentWindowActivityOptionsForStatus(agent, session),
          item: options.item || undefined,
          animate: false,
          statusBeforeAgent: true,
        }),
      });
      marker.innerHTML = `${tabHtml}${windowHtml}`;
      const text = document.createElement('span');
      text.className = 'attention-toast-reason';
      text.textContent = reason;
      line.append(marker, text);
    },
  };
}

function attentionToastLine(session, state) {
  const attention = attentionToastAgent(session, state);
  return agentToastTargetLine(session, attention?.agent, {
    reason: attentionToastReason(state, attention?.agent),
    item: attention?.item,
  });
}

function workingAgentTransitionNotificationTarget(agentKey, tone, options = {}) {
  const kind = typeof agentWindowKind === 'function' ? agentWindowKind(agentKey) : String(agentKey || '').trim().toLowerCase();
  const state = tone === 'attention' ? STATE_KEY.needsInput : STATE_KEY.idle;
  return {
    ...options,
    kind,
    state,
    window_index: options.window_index ?? options.windowIndex ?? options.window,
    window_label: options.window_label || options.windowLabel || '',
  };
}

function showAttentionAlert(session, state) {
  const node = emitNotification('sessionAttention', {
    session,
    title: attentionToastTitle(session, state),
    lines: attentionToastLine(session, state),
    body: state.reason || state.label || '',
    systemTitle: sessionNotificationTitle(session, state),
    systemTag: `yolomux:session:${session}:state:${stateSignature(state)}`,
    coalesceKey: `session-attention:${session}`,
    system: false,
    onClick: () => {
      dismissSessionToasts(session);
      void focusAgentToastTarget(session, attentionToastAgent(session, state)?.agent);
    },
  }).inApp;
  if (node) {
    node.dataset.toastSession = session;
    node.dataset.toastKind = 'attention';
  }
}

function dismissNotificationsForTarget(item, options = {}) {
  const target = String(item || '').trim();
  if (!target) return;
  const kind = String(options.kind || '');
  for (const [id, record] of toastRecords.entries()) {
    const node = record.node;
    if (node.dataset.toastTargetItem !== target && node.dataset.toastSession !== target) continue;
    if (!kind || node.dataset.toastKind === kind) removeAttentionAlert(id);
  }
  const notifications = browserNotificationsByTarget.get(target);
  if (notifications) {
    for (const notification of [...notifications]) notification.close?.();
    browserNotificationsByTarget.delete(target);
  }
}

function dismissSessionToasts(session, options = {}) {
  dismissNotificationsForTarget(session, options);
}

function dismissAttentionAlertsForSession(session) {
  dismissSessionToasts(session, {kind: 'attention'});
}

function attentionAlreadyVisible(session) {
  return notificationTargetIsFocused(session);
}

function removeAttentionAlert(id) {
  const node = toastRecords.get(id)?.node || null;
  clearToastRemovalTimer(id);
  node?.remove();
  toastRecords.delete(id);
}

function sendTestNotification(options = {}) {
  const inApp = options.inApp === true || (options.inApp === undefined && notificationDeliveryEnabled('inApp'));
  const system = options.system === true || (options.system === undefined && notificationDeliveryEnabled('system'));
  try {
    emitNotification('notificationTest', {
      title: hostNotificationTitle(t('notify.testToastTitle'), {inApp: true}),
      body: t('notify.testBody'), systemTitle: t('notify.testTitle', {host: serverHostname}),
      systemBody: t('notify.browserTestBody'), coalesceKey: 'notification-test', inApp, system,
    });
    postEvent(null, 'notification_test_sent', 'notification test sent', {hostname: serverHostname});
  } catch (error) {
    statusErr(localizedHtml('status.notificationFailed', {error}));
    postEvent(null, 'notification_error', `notification test failed: ${error}`, {hostname: serverHostname});
  }
}

function notifyCurrentAttentionStates() {
  for (const session of sessions.filter(isTmuxSession)) {
    const state = sessionState(session, transcriptMetadataState.payload.sessions?.[session]);
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
    const state = sessionState(session, transcriptMetadataState.payload.sessions?.[session]);
    const record = sessionStatusRecord(session, true);
    const previous = record?.state || null;
    const signature = stateSignature(state);
    if (record) record.state = {key: state.key, reason: state.reason, signature};
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
  if (!notificationDeliveryEnabled()) return;
  if (!shouldNotifyState(state)) return;
  // A green->red agent transition belongs to the dedicated per-window notification path below.
  // Letting the generic session-state path handle it too duplicates the popup and bypasses the
  // user's working-AI attention preference when that preference is off.
  if (workingAgentTransitionOwnsSessionStateNotification(session, state)) return;
  const key = `state:${stateSignature(state)}`;
  const now = Date.now();
  if (attentionAlreadyVisible(session)) {
    recordSessionNotificationSent(session, key, now);
    dismissAttentionAlertsForSession(session);
    postEvent(session, 'alert_suppressed_visible', eventMessageForState(session, state), {
      state: state.key,
      reason: state.reason,
    });
    return;
  }
  const lastSent = sessionNotificationLastSentAt(session, key);
  if (options.force !== true && now - lastSent < 60_000) return;
  recordSessionNotificationSent(session, key, now);
  const body = `${state.reason} · ${projectDirName(session, transcriptMetadataState.payload.sessions?.[session])}`;
  if (notificationDeliveryEnabled('inApp')) showAttentionAlert(session, state);
  postEvent(session, 'alert_shown', eventMessageForState(session, state), {
    state: state.key,
    reason: state.reason,
  });
  if (!notificationDeliveryEnabled('system') || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    emitNotification('sessionAttention', {session, title: attentionToastTitle(session, state), body, systemTitle: sessionNotificationTitle(session, state), systemTag: `yolomux:session:${session}:${key}`, renotify: true, inApp: false});
    postEvent(session, 'notification_sent', eventMessageForState(session, state), {state: state.key, reason: state.reason});
  } catch (error) {
    postEvent(session, 'notification_error', `notification failed: ${error}`, {state: state.key});
  }
}

const workingAgentTransitionNotificationDescriptors = Object.freeze({
  attention: Object.freeze({settingPath: 'notifications.notify_working_attention', defaultEnabled: true, copyKey: 'attention'}),
  cooldown: Object.freeze({settingPath: 'notifications.notify_working_done', defaultEnabled: false, copyKey: 'done'}),
});
const workingAgentTransitionNotificationConfirmMs = 5000;

function workingAgentTransitionNotificationDescriptor(tone) {
  return workingAgentTransitionNotificationDescriptors[tone] || null;
}

function workingAgentTransitionNotificationEnabled(tone) {
  const descriptor = workingAgentTransitionNotificationDescriptor(tone);
  return descriptor ? boolSetting(descriptor.settingPath, descriptor.defaultEnabled) : false;
}

function workingAgentTransitionNotificationPendingMap(session, create = false) {
  return sessionStatusRecord(session, create)?.workingAgentTransitionNotificationPending || null;
}

function clearWorkingAgentTransitionNotificationPending(session, key) {
  const pendingMap = workingAgentTransitionNotificationPendingMap(session);
  const pending = pendingMap?.get(key);
  if (!pending) return false;
  clearTimeout(pending.timer);
  pendingMap.delete(key);
  return true;
}

function clearWorkingAgentTransitionNotificationSession(session) {
  const record = sessionStatusRecord(session);
  if (!record) return;
  for (const pending of record.workingAgentTransitionNotificationPending.values()) clearTimeout(pending.timer);
  record.workingAgentTransitionNotificationPending.clear();
  record.workingAgentNotificationTones.clear();
}

function confirmWorkingAgentTransitionNotification(key, pending) {
  const session = pending.session;
  const record = sessionStatusRecord(session);
  if (record?.workingAgentTransitionNotificationPending.get(key) !== pending) return false;
  record.workingAgentTransitionNotificationPending.delete(key);
  if (record.workingAgentNotificationTones.get(key) !== pending.tone) return false;
  return maybeNotifyWorkingAgentTransition(pending.session, pending.kind, pending.tone, pending.options);
}

function scheduleWorkingAgentTransitionNotification(session, kind, key, tone, options = {}) {
  const pendingMap = workingAgentTransitionNotificationPendingMap(session, true);
  if (!pendingMap) return false;
  if (!workingAgentTransitionNotificationEnabled(tone) || !notificationDeliveryEnabled()) {
    clearWorkingAgentTransitionNotificationPending(session, key);
    return false;
  }
  const existing = pendingMap.get(key);
  if (existing?.tone === tone) return false;
  clearWorkingAgentTransitionNotificationPending(session, key);
  const pending = {session, kind, tone, options: {...options}, timer: 0};
  pending.timer = setTimeout(
    () => confirmWorkingAgentTransitionNotification(key, pending),
    workingAgentTransitionNotificationConfirmMs,
  );
  pendingMap.set(key, pending);
  return true;
}

function rekeyWorkingAgentTransitionNotificationSession(oldSession, newSession) {
  const record = sessionStatusRecord(oldSession);
  if (!record) return;
  for (const pending of record.workingAgentTransitionNotificationPending.values()) pending.session = newSession;
}

function maybeNotifyWorkingAgentTransition(session, agentKey, tone, options = {}) {
  const descriptor = workingAgentTransitionNotificationDescriptor(tone);
  if (!session || !descriptor || !workingAgentTransitionNotificationEnabled(tone) || !notificationDeliveryEnabled()) return false;
  const marker = tone === 'attention'
    ? String(options.attentionKey || options.attentionSignature || '').trim()
    : String(options.stoppedAt || '').trim();
  if (!marker) return false;
  const target = workingAgentTransitionNotificationTarget(agentKey, tone, options);
  const targetKey = typeof agentWindowActivityTransitionKey === 'function'
    ? agentWindowActivityTransitionKey(agentKey, {...target, session: ''})
    : `${target.window_index ?? target.window ?? ''}:${agentKey}`;
  const key = `agent-window-transition:${targetKey}:${tone}:${marker}`;
  const now = Date.now();
  if (notificationTargetIsFocused(session)) {
    recordSessionNotificationSent(session, key, now);
    dismissNotificationsForTarget(session);
    postEvent(session, 'agent_window_transition_notification_suppressed_visible', '', {agent: target.kind, tone});
    return false;
  }
  const throttleMs = Math.max(0, numberSetting('notifications.throttle_seconds', 60)) * 1000;
  const lastSent = sessionNotificationLastSentAt(session, key);
  if (now - lastSent < throttleMs) return false;
  recordSessionNotificationSent(session, key, now);
  const kind = typeof agentWindowKind === 'function' ? agentWindowKind(agentKey) : '';
  const agent = kind ? agentLabel(kind) : t('common.agentLabel');
  const title = t(`notify.working.${descriptor.copyKey}.title`);
  const body = t(`notify.working.${descriptor.copyKey}.body`, {agent, session: sessionLabel(session)});
  if (notificationDeliveryEnabled('inApp')) {
    const node = emitNotification(tone === 'attention' ? 'agentAttention' : 'agentDone', {
      session, title, lines: agentToastTargetLine(session, target, {reason: body}), body,
      systemTitle: title, systemTag: `yolomux:session:${session}:${key}`, renotify: true,
      coalesceKey: `agent-${tone}:${session}:${targetKey}`,
      onClick: () => {
        dismissSessionToasts(session);
        void focusAgentToastTarget(session, target);
      },
      system: false,
    }).inApp;
    if (node) {
      node.dataset.toastSession = session;
      node.dataset.toastKind = 'working-agent-transition';
    }
  }
  postEvent(session, 'agent_window_transition_notification', body, {agent: kind, tone});
  if (!notificationDeliveryEnabled('system') || !('Notification' in window) || Notification.permission !== 'granted') return true;
  try {
    emitNotification(tone === 'attention' ? 'agentAttention' : 'agentDone', {session, title, body, systemTitle: title, systemTag: `yolomux:session:${session}:${key}`, renotify: true, inApp: false});
    postEvent(session, 'notification_sent', body, {agent: kind, tone});
  } catch (error) {
    postEvent(session, 'notification_error', `notification failed: ${error}`, {agent: kind, tone});
  }
  return true;
}

function workingAgentNotificationTone(agent) {
  const state = String(agent?.state || '').trim();
  if (typeof agentWindowIsWorkingState === 'function' && agentWindowIsWorkingState(state)) return STATE_KEY.working;
  if (typeof agentWindowIsAttentionState === 'function' && agentWindowIsAttentionState(state)) return 'attention';
  const stoppedAt = Number(agent?.working_stopped_ts || agent?.workingStoppedTs || 0);
  return Number.isFinite(stoppedAt) && stoppedAt > 0 ? 'cooldown' : '';
}

function workingAgentFinishedRecently(agent, nowSeconds = Date.now() / 1000) {
  const stoppedAt = Number(agent?.working_stopped_ts || agent?.workingStoppedTs || 0);
  if (!Number.isFinite(stoppedAt) || stoppedAt <= 0) return false;
  const freshnessSeconds = Math.max(1, Number(workflowTransitionGlowSeconds) || 0);
  return nowSeconds >= stoppedAt && nowSeconds - stoppedAt <= freshnessSeconds;
}

function workingAgentTransitionSnapshot(session, agent) {
  const kind = typeof agentWindowKind === 'function' ? agentWindowKind(agent?.kind) : '';
  if (!session || !kind || typeof agentWindowActivityTransitionKey !== 'function') return null;
  const options = typeof agentWindowActivityOptionsForStatus === 'function'
    ? agentWindowActivityOptionsForStatus(agent, session, {scheduleRefresh: false})
    : {...agent, session};
  const key = agentWindowActivityTransitionKey(kind, {...options, session: ''});
  if (!key) return null;
  const tone = workingAgentNotificationTone(agent);
  const toneMap = sessionStatusRecord(session, true)?.workingAgentNotificationTones;
  return {agent, kind, options, key, tone, previousTone: toneMap?.get(key) || ''};
}

function workingAgentTransitionOwnsSessionStateNotification(session, state) {
  const agent = attentionToastAgent(session, state)?.agent;
  const transition = workingAgentTransitionSnapshot(session, agent);
  return transition?.tone === 'attention';
}

// Notifications are driven by one authoritative auto-approve payload, never by a surface renderer.
// A hidden tab, a rebuilt Info Bar, or a late popover must not change whether a live green->red/yellow
// transition notifies the user. Both exit tones share one confirmation timer so detector flicker and
// auto-approve turnarounds return to working without producing a false popup.
function reconcileWorkingAgentTransitionNotifications(session, payload = {}) {
  if (!session || typeof agentWindowActivityTransitionKey !== 'function') return 0;
  const record = sessionStatusRecord(session, true);
  const toneMap = record?.workingAgentNotificationTones;
  const pendingMap = record?.workingAgentTransitionNotificationPending;
  if (!toneMap || !pendingMap) return 0;
  const rows = typeof sessionAgentWindowStatusPayloads === 'function'
    ? sessionAgentWindowStatusPayloads(session, transcriptMetadataState.payload.sessions?.[session], payload)
    : typeof agentWindowPayloadRows === 'function'
      ? agentWindowPayloadRows(payload.agent_windows)
      : [];
  const nextKeys = new Set();
  let notified = 0;
  for (const agent of rows) {
    const transition = workingAgentTransitionSnapshot(session, agent);
    if (!transition) continue;
    const {kind, options, key, tone, previousTone} = transition;
    nextKeys.add(key);
    const shouldNotify = (previousTone === STATE_KEY.working && (tone === 'attention' || tone === 'cooldown'))
      // `working_stopped_ts` is written by the server only after it observes working->idle. Use that
      // durable transition proof when a browser refresh missed its transient green snapshot.
      || (tone === 'cooldown' && previousTone === '' && workingAgentFinishedRecently(agent));
    if (shouldNotify) {
      const attentionKey = tone === 'attention' && typeof agentWindowActivityAcknowledgementKey === 'function'
        ? agentWindowActivityAcknowledgementKey(kind, agent.state, options, options.attention_signature || options.screen_text)
        : '';
      notified += Number(scheduleWorkingAgentTransitionNotification(session, kind, key, tone, {
        ...agent,
        ...options,
        attentionKey,
        attentionSignature: options.attention_signature || options.screen_text,
        stoppedAt: Number(options.working_stopped_ts || 0),
      }));
    } else if (pendingMap.get(key)?.tone !== tone) {
      clearWorkingAgentTransitionNotificationPending(session, key);
    }
    toneMap.set(key, tone);
  }
  for (const key of toneMap.keys()) {
    if (nextKeys.has(key)) continue;
    toneMap.delete(key);
    clearWorkingAgentTransitionNotificationPending(session, key);
  }
  return notified;
}

// a stable snapshot of the watched-PR status dimensions we diff for notifications.
function watchedPrStatusSnapshot(pr) {
  return {
    merged: pullRequestIsMerged(pr),
    ci: pullRequestCiState(pr),
    review: pullRequestReviewDecision(pr),
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
// throttled by notifications.throttle_seconds via the bounded watched-PR throttle map.
function maybeNotifyWatchedPr(ref, key, message, url) {
  if (!notificationDeliveryEnabled()) return;
  if (!shouldNotifyTransitionKey(key)) return;
  const signature = `watched-pr:${ref}:${key}`;
  const now = Date.now();
  const throttleMs = Math.max(0, (Number(initialSetting('notifications.throttle_seconds', 60)) || 0) * 1000);
  const lastSent = watchedPrNotificationLastSent.get(signature) || 0;
  if (now - lastSent < throttleMs) return;
  setLimitedMapEntry(watchedPrNotificationLastSent, signature, now, notificationLastSentLimit);
  if (notificationDeliveryEnabled('inApp')) emitNotification('watchedPullRequest', {title: message, lines: [ref], body: ref, url, coalesceKey: signature, onClick: () => { try { window.open(url, '_blank', 'noopener,noreferrer'); } catch (_) {} }, system: false});
  postEvent(null, 'watched_pr_alert', message, {ref, transition: key});
  if (!notificationDeliveryEnabled('system') || !('Notification' in window) || Notification.permission !== 'granted') return;
  try {
    emitNotification('watchedPullRequest', {title: message, body: ref, systemTitle: hostNotificationTitle(message), systemTag: signature, renotify: true, url, inApp: false});
    postEvent(null, 'watched_pr_notification', message, {ref, transition: key});
  } catch (error) {
    postEvent(null, 'watched_pr_notification_error', `notification failed: ${error}`, {ref, transition: key});
  }
}

function updateTopbarMetrics() {
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height || 38);
  document.documentElement?.style?.setProperty('--topbar-height', `${height}px`);
  scheduleTopbarActionCapacity();
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

function flushScheduledTabStripOverflowChecks() {
  const strips = Array.from(tabStripOverflowCheckSet);
  tabStripOverflowCheckSet.clear();
  tabStripOverflowCheckFrame = null;
  for (const strip of strips) {
    if (!strip?.isConnected) continue;
    strip.classList.toggle('tabs-overflowing', strip.scrollWidth > strip.clientWidth + 1);
  }
}

function scheduleTabStripOverflowCheck(strip) {
  if (!strip) return;
  if (strip === sessionButtons || strip.classList?.contains('pane-tabs')) {
    strip.classList.remove('tabs-overflowing');
    scheduleTopbarMetricsUpdate();
    return;
  }
  strip.classList.remove('tabs-overflowing');
  tabStripOverflowCheckSet.add(strip);
  if (tabStripOverflowCheckFrame) return;
  tabStripOverflowCheckFrame = requestAnimationFrame(flushScheduledTabStripOverflowChecks);
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
  const authoritative = normalizedSessionOrder(next) || [];
  sessions = sessionOrderIncludingPending(authoritative);
  visibleSessions = sessions.slice(0, maxSessionTabs);
  layoutItems = computeLayoutItems();
}

function updateSessionList(nextSessions, options = {}) {
  const next = normalizedSessionOrder(nextSessions);
  if (!next) return false;
  pruneExpiredPendingTmuxSessions();
  if (options.clearConfirmedPending === true) clearConfirmedPendingTmuxSessions(next);
  const effectiveNext = sessionOrderIncludingPending(next);
  const changed = effectiveNext.length !== sessions.length || effectiveNext.some((session, index) => session !== sessions[index]);
  if (!changed) {
    layoutItems = computeLayoutItems();
    return false;
  }
  const removedSessions = visibleSessions.filter(session => !effectiveNext.includes(session));
  setSessionOrder(effectiveNext);
  layoutSlots = normalizeLayoutSlotsForViewport(layoutSlots, {
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
  // A later layout mutation means the saved Fill workspace snapshot is no longer a valid restore
  // target. The fill/restore transaction explicitly opts out while it applies its own snapshot.
  if (options.preserveFilledWorkspaceLayout !== true) filledWorkspaceLayout = null;
  // detect a same-shape change (reorder/activate/move/replace) so we can skip the full
  // topbar + grid teardown. Compute the shape signature before and after the slot reassignment.
  const prevShape = layoutShapeSignature(layoutSlots);
  layoutSlots = normalizeLayoutSlotsForViewport(nextSlots, {
    focusSession: options.focusSession,
    preserveMissingFileExplorer: options.preserveMissingFileExplorer === true,
    preservePlaceholderSlots: options.preservePlaceholderSlots === true,
  });
  activeSessions = sessionsFromLayout();
  clearFocusForInactiveLayout();
  updateActiveSessionParam();
  sharePublishLayout();
  requestLayoutRender({
    previousActive,
    prevShape,
    nextShape: layoutShapeSignature(layoutSlots),
    options: {prune: options.prune},
    reason: 'applyLayoutSlots',
    forceFull: options.forceFull === true,
  });
  for (const session of activeSessions.filter(isTmuxSession)) ensureTerminalRunning(session);
  scheduleShareTopologySnapshot(options.shareReason || 'layout');
  // do NOT re-poll the server on a pure client-side layout change. refreshTranscripts()
  // fires 3..(3+N) network round-trips and a second full render wave gated behind their latency —
  // the bulk of the "moving a tab takes several seconds" delay. Freshness is already covered by the
  // metadata interval (50_editor_settings_runtime.js), and the session-changing mutations
  // (create/rename/kill, 70_layout_actions.js) call refreshTranscripts() at their own sites.
  renderAutoApproveButtons();
  updatePanelInactiveOverlays();
  if (typeof syncJsDebugStatsPolling === 'function') syncJsDebugStatsPolling({pollNow: true});
  if (autoFocusCanFollowCursor() && options.focusSession && activeSessions.includes(options.focusSession)) {
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
    && (grid.querySelector('.drop-slot[data-slot]') || dockviewLayoutActive());
}

function performLayoutRender(request = {}) {
  const renderRequest = layoutRenderRequest(request);
  const previousActive = renderRequest.previousActive;
  if (layoutRenderCanUseCheap(renderRequest)) {
    // Cheap path: the tree shape is unchanged. Swap only the slots whose active item changed and
    // reconcile the (already keyed) tab strips — no innerHTML='', no topbar rebuild.
    syncActivePanelsInPlace();
    // Dockview's in-place sync already refreshes its tab renderers and mounted panels. Running the
    // generic strip pass again repeats that same work during every active-tab adoption.
    if (!dockviewLayoutActive()) renderPaneTabStrips();
    syncPanelVisibility(previousActive);
    return;
  }
  renderSessionButtons();
  renderPanels(previousActive, {prune: renderRequest.options.prune});
}

function requestLayoutRender(request = {}) {
  const renderRequest = layoutRenderRequest(request);
  if (dragState.item != null) {
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
  if (shareViewMode) return `${location.pathname}${location.search || ''}${location.hash || ''}`;
  const params = new URLSearchParams(location.search);
  params.delete('active');
  params.delete('sessions');
  params.delete('layout');
  params.delete('tabs');
  params.delete('finder');
  params.delete('state');
  const queryParts = [];
  const inactiveItems = inactiveTabItems();
  if (activeSessions.length || inactiveItems.length) {
    if (activeSessions.length) {
      queryParts.push(`sessions=${activeSessions.map(readableItemParam).join(',')}`);
    }
    queryParts.push(`layout=${layoutParamValue(layoutSlots)}`);
    const tabs = layoutTabsParamValue(layoutSlots);
    if (tabs) queryParts.push(`tabs=${tabs}`);
    if (paneItems(layoutSlots).some(isFileExplorerItem)) {
      queryParts.push(`finder=${readableParamComponent(normalizeFileExplorerMode(fileExplorerMode))}`);
    }
    const state = layoutUrlStateParamValue();
    if (state) queryParts.push(`state=${state}`);
  }
  const remaining = params.toString();
  if (remaining) queryParts.push(remaining);
  const query = queryParts.join('&');
  const nextUrl = `${location.pathname}${query ? `?${query}` : ''}${location.hash}`;
  history.replaceState(null, '', nextUrl);
  return nextUrl;
}

function syncInitialLayoutUrl() {
  schedulePendingLayoutUrlStateApply();
  updateActiveSessionParam();
}
