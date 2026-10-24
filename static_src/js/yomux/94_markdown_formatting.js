// All formatting entry points share admission, command metadata, and native editor adapters.
const markdownFormatCommands = [
  {id: 'bold', label: 'Bold', glyph: 'B', mark: 'strong', htmlTag: 'strong', markers: ['**', '**'], key: 'b'},
  {id: 'italic', label: 'Italic', glyph: 'I', mark: 'em', htmlTag: 'em', markers: ['*', '*'], key: 'i'},
  {id: 'underline', label: 'Underline', glyph: 'U', mark: 'underline', htmlTag: 'u', markers: ['<u>', '</u>'], key: 'u'},
  {id: 'strike', label: 'Strikethrough', glyph: 'S', mark: 'strike', htmlTag: 's', markers: ['~~', '~~'], key: 'x', shift: true},
  {id: 'code', label: 'Inline code', glyph: '<>', mark: 'code', markers: ['`', '`']},
  {id: 'clearInline', label: 'Clear formatting', glyph: 'Clear', key: '\\'},
  {id: 'normal', label: 'Normal text', key: '0', alt: true, group: 'paragraph'},
  ...Array.from({length: 6}, (_, index) => ({id: `h${index + 1}`, label: `Heading ${index + 1}`, key: String(index + 1), alt: true, group: 'paragraph'})),
  {id: 'pre', label: 'Preformatted block', key: 'c', alt: true, shift: true, group: 'paragraph'},
  {id: 'number', label: 'Numbered list', key: '7', shift: true, group: 'list'},
  {id: 'bullet', label: 'Bulleted list', key: '8', shift: true, group: 'list'},
  {id: 'check', label: 'Checklist', key: '9', shift: true, group: 'list'},
  {id: 'listOutdent', label: 'Decrease indent', glyph: '⇤', key: '[', group: 'listIndent', surface: 'view-editor'},
  {id: 'listIndent', label: 'Increase indent', glyph: '⇥', key: ']', group: 'listIndent', surface: 'view-editor'},
];

function markdownFormatShortcut(command, mac = isMacPlatform()) {
  if (!command.key) return '';
  if (command.id === 'strike' && !mac) return 'Alt+Shift+5';
  return [mac ? 'Cmd' : 'Ctrl', command.alt ? (mac ? 'Option' : 'Alt') : '', command.shift ? 'Shift' : '', command.key.toUpperCase()].filter(Boolean).join('+');
}

function markdownFormatShortcutCommand(event, mac = isMacPlatform()) {
  if (event.isComposing || event.defaultPrevented) return null;
  return markdownFormatCommands.find(command => {
    if (!command.key) return false;
    const strike = command.id === 'strike' && !mac;
    const platformMod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
    const key = String(event.key || '').toLowerCase();
    const code = command.key === '\\' ? 'Backslash' : command.key === '[' ? 'BracketLeft' : command.key === ']' ? 'BracketRight' : /^\d$/.test(command.key) ? `Digit${command.key}` : `Key${command.key.toUpperCase()}`;
    return (strike ? !event.ctrlKey && !event.metaKey : platformMod)
      && Boolean(event.altKey) === (strike || Boolean(command.alt))
      && Boolean(event.shiftKey) === Boolean(command.shift)
      && (strike ? event.code === 'Digit5' || key === '5' : event.code === code || key === command.key);
  }) || null;
}

function markdownFormattingPanelWritable(panel) {
  if (!panel?.isConnected || readOnlyMode) return false;
  const path = panel.dataset.filePath;
  const item = panel.dataset.layoutItem || fileEditorItemFor(path);
  const state = fileEditorPanelState(panel);
  return panelNodes.get(item) === panel && state?.kind === 'text' && state.historical !== true
    && previewRendererForPath(path)?.id === 'markdown';
}

function prosemirrorPanelEditable(panel) {
  return markdownFormattingPanelWritable(panel) && panel._pmSearchReadOnly !== true && !panel._pmError
    && ['preview', 'split'].includes(editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem));
}

function markdownFormattingWritable(panel, surface) {
  if (!markdownFormattingPanelWritable(panel)) return false;
  if (surface === 'view-editor') return prosemirrorPanelEditable(panel) && panel._pmView?.dom?.isConnected;
  return ['edit', 'split'].includes(editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem))
    && panel._cmMode === 'edit' && panel._cmView?.dom?.isConnected;
}

function markdownFormattingTarget(panel) {
  const mode = editorViewModeFor(panel.dataset.filePath, panel.dataset.layoutItem);
  return mode === 'preview' ? 'view-editor' : mode === 'edit' ? 'text-editor' : panel._markdownFormatSurface || 'text-editor';
}

function dispatchMarkdownFormat(panel, command, surface = markdownFormattingTarget(panel)) {
  if (!markdownFormattingWritable(panel, surface)) return false;
  const entry = markdownFormatCommands.find(item => item.id === command);
  if (!entry) return false;
  if (entry.surface && entry.surface !== surface) return false;
  const applied = surface === 'view-editor'
    ? applyProseMirrorFormat(window.YOmuxProseMirror, panel._pmView, panel._pmSchema, command)
    : applyCodeMirrorMarkdownFormat(panel, entry);
  refreshMarkdownFormattingToolbar(panel);
  return applied;
}

function handleMarkdownFormattingShortcut(event) {
  const command = markdownFormatShortcutCommand(event);
  if (!command) return false;
  const panel = event.target?.closest?.('.file-editor-panel');
  const surface = panel?._pmView?.dom?.contains(event.target) ? 'view-editor'
    : panel?._cmView?.contentDOM?.contains(event.target) ? 'text-editor' : '';
  if (command.surface && command.surface !== surface) return false;
  if (!surface || !markdownFormattingWritable(panel, surface)) return false;
  // Claim only native Markdown surfaces, before the global Finder shortcut changes the workspace.
  event.preventDefault();
  event.stopPropagation();
  dispatchMarkdownFormat(panel, command.id, surface);
  return true;
}

const markdownSourceFormatCache = new WeakMap();

function markdownSourceMarkRanges(view) {
  if (markdownSourceFormatCache.has(view.state)) return markdownSourceFormatCache.get(view.state);
  const api = window.YOmuxCodeMirror;
  const text = view.state.doc.toString();
  const ranges = [];
  ranges.blocks = [];
  ranges.ignored = [];
  ranges.code = [];
  ranges.references = [];
  const htmlTags = [];
  const kinds = {StrongEmphasis: 'bold', Emphasis: 'italic', Strikethrough: 'strike', InlineCode: 'code'};
  const tree = api.ensureSyntaxTree(view.state, text.length, 100);
  if (!tree) return null;
  tree.iterate({enter(node) {
    if (node.name === 'Image') {
      ranges.ignored.push({from: node.from, to: node.to});
      return false;
    }
    if (node.name === 'Link') {
      const first = node.node.firstChild, last = node.node.lastChild;
      const collapsed = last?.name === 'LinkLabel' && text.slice(last.from, last.to) === '[]';
      if (first && last && (collapsed || last.name === 'LinkMark' && text.slice(last.from, last.to) === ']')) {
        const closing = collapsed ? last.prevSibling : last;
        ranges.references.push({start: first.to, end: closing.from, from: collapsed ? last.from : last.to, to: last.to, insert: `[${text.slice(first.to, closing.from)}]`});
      }
    }
    if (/^(Paragraph|ATXHeading[1-6]|SetextHeading[12]|TableCell)$/.test(node.name)) ranges.blocks.push({from: node.from, to: node.to});
    if (/^(Comment|HTMLTag|URL|LinkMark|LinkLabel|LinkTitle|HeaderMark|QuoteMark|EmphasisMark|StrikethroughMark|CodeMark|TableDelimiter)$/.test(node.name)) ranges.ignored.push({from: node.from, to: node.to});
    if (node.name === 'HTMLTag') {
      const source = text.slice(node.from, node.to);
      const open = source.match(/^<(strong|em|s|u|mark|kbd|sup)\b[^>]*>$/i);
      const close = source.match(/^<\/(strong|em|s|u|mark|kbd|sup)\s*>$/i);
      const tag = open || close;
      if (tag) htmlTags.push({from: node.from, to: node.to, parent: node.node.parent.from, closing: Boolean(close), name: tag[1].toLowerCase(), source});
    }
    const command = kinds[node.name];
    if (!command) return;
    const first = node.node.firstChild, last = node.node.lastChild;
    if (first && last && first !== last) {
      const range = {command, from: node.from, start: first.to, end: last.from, to: node.to};
      if (command === 'code') {
        const raw = text.slice(range.start, range.end);
        // CommonMark strips one paired padding space around nonblank code content. Keeping it
        // outside the semantic range prevents a style split from turning padding into visible text.
        if (raw.startsWith(' ') && raw.endsWith(' ') && raw.trim()) {
          ranges.ignored.push({from: range.start, to: range.start + 1}, {from: range.end - 1, to: range.end});
          range.start += 1; range.end -= 1;
        }
        ranges.code.push(range);
      }
      ranges.push(range);
    }
  }});
  const stacks = new Map();
  for (const tag of htmlTags) {
    const stack = stacks.get(tag.name) || [];
    stacks.set(tag.name, stack);
    if (!tag.closing) stack.push(tag);
    else if (stack.length) {
      const open = stack.pop();
      const entry = markdownFormatCommands.find(entry => entry.htmlTag === tag.name);
      if (open.parent === tag.parent) ranges.push({command: entry?.id || 'html:' + tag.name, htmlTag: tag.name, sourceMarkers: [open.source, tag.source], from: open.from, start: open.to, end: tag.from, to: tag.to});
    }
  }
  markdownSourceFormatCache.set(view.state, ranges);
  return ranges;
}

function markdownSourceFormatState(view, entry) {
  const selection = view.state.selection.main;
  const ranges = markdownSourceMarkRanges(view) || [];
  if (entry.mark) {
    if (selection.empty) return ranges.some(range => range.command === entry.id && range.start <= selection.head && range.end >= selection.head) ? 'true' : 'false';
    let yes = false, no = false;
    for (let position = selection.from; position < selection.to; position += 1) {
      if (/\s/.test(view.state.doc.sliceString(position, position + 1)) || ranges.ignored?.some(range => range.from <= position && range.to > position)) continue;
      if (ranges.some(range => range.command === entry.id && range.start <= position && range.end > position)) yes = true;
      else no = true;
    }
    return yes && no ? 'mixed' : yes ? 'true' : 'false';
  }
  const line = view.state.doc.lineAt(selection.head).text;
  if (entry.id === 'normal') return !/^\s*(#{1,6}\s|[-+*]\s|\d+[.)]\s|```)/.test(line) ? 'true' : 'false';
  if (/^h[1-6]$/.test(entry.id)) return new RegExp(`^#{${entry.id.slice(1)}} `).test(line) ? 'true' : 'false';
  if (entry.group === 'list') {
    const kind = /^\s*[-+*]\s+\[[ xX]\]/.test(line) ? 'check' : /^\s*\d+[.)]\s/.test(line) ? 'number' : /^\s*[-+*]\s/.test(line) ? 'bullet' : '';
    return kind === entry.id ? 'true' : 'false';
  }
  return 'false';
}

function markdownCodeStyleMarkers(entry) {
  return entry.sourceMarkers || (entry.htmlTag ? [`<${entry.htmlTag}>`, `</${entry.htmlTag}>`] : entry.markers);
}

function markdownSourceRangeMark(range) {
  const entry = markdownFormatCommands.find(item => item.id === range.command);
  if (entry) return range.sourceMarkers ? {...entry, sourceMarkers: range.sourceMarkers} : entry;
  return range.htmlTag ? {id: range.command, htmlTag: range.htmlTag, sourceMarkers: range.sourceMarkers} : null;
}

function prosemirrorCodeStyleMark(entry, fallback) {
  const delimiter = (side, index) => (state, mark, parent, childIndex) => {
    let hasCode = false;
    parent.forEach(node => { if (node.marks.some(mark => mark.type.name === 'code')) hasCode = true; });
    // Markdown emphasis delimiters next to a code span can violate CommonMark flanking after a
    // partial clear. Safe inline tags preserve those mark boundaries without adding visible spaces.
    const value = fallback[side];
    return hasCode ? markdownCodeStyleMarkers(entry)[index] : typeof value === 'function' ? value(state, mark, parent, childIndex) : value;
  };
  return {...fallback, open: delimiter('open', 0), close: delimiter('close', 1)};
}

function markdownEscapeInline(content) {
  return content.replace(/[\\`*_{}\[\]<>#+.!|~-]/g, '\\$&');
}

function markdownCodeMarkers(content) {
  const maximum = Math.max(0, ...Array.from(content.matchAll(/`+/g), match => match[0].length));
  const delimiter = '`'.repeat(maximum + 1);
  const padding = content.startsWith('`') || content.endsWith('`') || /^ .+ $/.test(content) && content.trim() ? ' ' : '';
  return [delimiter + padding, padding + delimiter];
}

function markdownSourceInlineRegions(text, ranges, from, to, command, codeSelection = null) {
  const regions = [];
  const ignored = [...ranges.ignored.filter(range => !codeSelection || !ranges.code.some(code => code.from <= range.from && code.to >= range.to)), ...(command !== 'code' && command !== 'clearInline' ? ranges.code.filter(code => !codeSelection || code.from < codeSelection.to && code.to > codeSelection.from) : [])];
  const appendRegion = (from, to) => {
    const selected = text.slice(from, to);
    const start = from + selected.length - selected.trimStart().length;
    const end = to - (selected.length - selected.trimEnd().length);
    if (start < end) regions.push({start, end});
  };
  for (const block of ranges.blocks) {
    const end = Math.min(to, block.to);
    let start = Math.max(from, block.from), position = start;
    while (position < end) {
      const token = ignored.filter(range => range.from <= position && range.to > position).sort((a, b) => b.to - a.to)[0];
      if (token) {
        appendRegion(start, position);
        position = Math.min(end, token.to); start = position;
      } else position += 1;
    }
    appendRegion(start, end);
  }
  return regions;
}

function applyCodeMirrorMarkdownFormat(panel, entry) {
  const view = panel._cmView;
  const {from, to, anchor, head, empty} = view.state.selection.main;
  const text = view.state.doc.toString();
  if (!entry.mark && entry.id !== 'clearInline') return applyCodeMirrorMarkdownBlocks(view, entry);
  const blockChanges = entry.id === 'clearInline'
    ? markdownSourceBlockChanges(view, markdownFormatCommands.find(item => item.id === 'normal')).filter(change => change.from !== change.to || change.insert)
    : [];
  if (empty && entry.id === 'clearInline') {
    if (blockChanges.length) view.dispatch({changes: blockChanges, userEvent: 'input.format'});
    return true;
  }
  const ranges = markdownSourceMarkRanges(view);
  if (!ranges) return false;
  if (empty && entry.mark) {
    const pair = panel._markdownTypingPair;
    if (pair?.command === entry.id && from === pair.closeFrom && text.slice(pair.openFrom, pair.openFrom + pair.open.length) === pair.open && text.slice(pair.closeFrom, pair.closeFrom + pair.close.length) === pair.close) {
      panel._markdownTypingPair = null;
      if (pair.closeFrom === pair.openFrom + pair.open.length) {
        view.dispatch({changes: {from: pair.openFrom, to: pair.closeFrom + pair.close.length, insert: ''}, selection: {anchor: pair.openFrom}, userEvent: 'input.format'});
      } else view.dispatch({selection: {anchor: pair.closeFrom + pair.close.length}});
      return true;
    }
    const [open, close] = entry.markers;
    view.dispatch({changes: {from, insert: open + close}, selection: {anchor: from + open.length}, userEvent: 'input.format'});
    panel._markdownTypingPair = {command: entry.id, open, close, openFrom: from, closeFrom: from + open.length};
    return true;
  }
  if (empty) return true;
  // Partition native syntax blocks before editing: Markdown marks cannot cross paragraph or
  // list boundaries. Token boundaries also keep link destinations and comments out of the range.
  const regions = markdownSourceInlineRegions(text, ranges, from, to, entry.id);
  const selectedCode = entry.id === 'clearInline' || entry.mark && entry.id !== 'code' ? ranges.filter(range => range.command === 'code' && range.start < to && range.end > from) : [];
  if (!regions.length && !selectedCode.length && !blockChanges.length) return false;
  const targets = [...regions, ...selectedCode.map(code => ({start: Math.max(from, code.start), end: Math.min(to, code.end)}))].sort((a, b) => a.start - b.start);
  const active = markdownSourceFormatState(view, entry) === 'true';
  const changes = [...blockChanges];
  const codeSelections = [];
  const inserts = new Map();
  const insert = (position, value) => inserts.set(position, (inserts.get(position) || '') + value);
  const start = targets.length ? targets[0].start : from;
  const end = targets.length ? targets.at(-1).end : to;
  const affected = ranges.filter(range => !selectedCode.includes(range) && (entry.id === 'clearInline' || range.command === entry.id) && targets.some(region => range.start < region.end && range.end > region.start));
  // Styling an implicit reference label changes its lookup key. Retain the original key
  // explicitly so collapsed and shortcut links still resolve after formatting and reopening.
  for (const reference of ranges.references) {
    if ((entry.mark || affected.some(range => range.start < reference.end && range.end > reference.start))
      && targets.some(region => region.start < reference.end && region.end > reference.start)) changes.push({from: reference.from, to: reference.to, insert: reference.insert});
  }
  for (const range of affected) {
    changes.push({from: range.from, to: range.start, insert: ''}, {from: range.end, to: range.to, insert: ''});
    const open = text.slice(range.from, range.start), close = text.slice(range.end, range.to);
    for (const outside of markdownSourceInlineRegions(text, ranges, range.start, range.end, range.command, {from, to})) {
      for (const piece of [{start: outside.start, end: Math.min(outside.end, start)}, {start: Math.max(outside.start, end), end: outside.end}]) {
        if (piece.start >= piece.end) continue;
        const value = text.slice(piece.start, piece.end);
        const left = piece.start + value.length - value.trimStart().length;
        const right = piece.end - (value.length - value.trimEnd().length);
        if (left < right) {
          const usesCode = ranges.code.some(code => code.from < right && code.to > left);
          const owner = markdownSourceRangeMark(range);
          const markers = usesCode ? markdownCodeStyleMarkers(owner) : [open, close];
          insert(left, markers[0]); insert(right, markers[1]);
        }
      }
    }
  }
  for (const code of selectedCode) {
    const left = Math.max(from, code.start), right = Math.min(to, code.end);
    const original = entry.id === 'clearInline'
      ? ranges.filter(range => range.command !== 'code' && range.start <= code.start && range.end >= code.end).map(markdownSourceRangeMark).filter(Boolean)
      : ranges.some(range => range.command === entry.id && range.start <= code.start && range.end >= code.end) ? [entry] : [];
    const pieces = [
      {text: text.slice(code.start, left), marks: original, code: true},
      {text: text.slice(left, right), marks: entry.id === 'clearInline' || active ? [] : [entry], code: entry.id !== 'clearInline'},
      {text: text.slice(right, code.end), marks: original, code: true},
    ].filter(piece => piece.text);
    const merged = [];
    for (const piece of pieces) {
      if (merged.at(-1)?.code === piece.code && merged.at(-1)?.marks.map(mark => mark.id).join(',') === piece.marks.map(mark => mark.id).join(',')) merged.at(-1).text += piece.text;
      else merged.push({...piece});
    }
    let sourceOffset = code.start, replacementOffset = 0;
    const segments = [];
    const replacement = merged.map(piece => {
      const content = piece.text.replace(/\n/g, ' ');
      const [open, close] = piece.code ? markdownCodeMarkers(content) : ['', ''];
      const prefix = piece.marks.map(mark => markdownCodeStyleMarkers(mark)[0]).join('') + open;
      const suffix = close + piece.marks.slice().reverse().map(mark => markdownCodeStyleMarkers(mark)[1]).join('');
      const valueText = piece.code ? content : markdownEscapeInline(content);
      segments.push({from: sourceOffset, to: sourceOffset + piece.text.length, offset: replacementOffset + prefix.length, source: content, escaped: !piece.code});
      const value = prefix + valueText + suffix;
      sourceOffset += piece.text.length; replacementOffset += value.length;
      return value;
    }).join('');
    codeSelections.push({code, segments});
    changes.push({from: code.from, to: code.to, insert: replacement});
  }
  if (entry.mark && !active) for (const region of regions) {
    let [open, close] = entry.markers;
    if (entry.id === 'code') [open, close] = markdownCodeMarkers(text.slice(region.start, region.end));
    insert(region.start, open); insert(region.end, close);
  }
  // Merge insertions at deletion boundaries into a single legal CodeMirror ChangeSet.
  for (const change of changes) {
    if (inserts.has(change.from)) { change.insert = inserts.get(change.from); inserts.delete(change.from); }
  }
  for (const [position, value] of inserts) changes.push({from: position, insert: value});
  changes.sort((a, b) => a.from - b.from || (a.to || a.from) - (b.to || b.from));
  const transaction = view.state.update({changes, userEvent: 'input.format'});
  const mapSelection = (position, association) => {
    for (const {code, segments} of codeSelections) {
      const segment = segments.find(segment => association > 0 ? position >= segment.from && position < segment.to : position > segment.from && position <= segment.to);
      if (segment) {
        const prefix = segment.source.slice(0, position - segment.from);
        const offset = segment.escaped ? markdownEscapeInline(prefix).length : prefix.length;
        return transaction.changes.mapPos(code.from, -1) + segment.offset + offset;
      }
    }
    return transaction.changes.mapPos(position, association);
  };
  const mappedFrom = mapSelection(start, 1), mappedTo = mapSelection(end, -1);
  view.dispatch(view.state.update({changes, selection: {anchor: anchor > head ? mappedTo : mappedFrom, head: anchor > head ? mappedFrom : mappedTo}, userEvent: 'input.format'}));
  return true;
}

function markdownSourceSelectionOverlaps(selection, from, to) {
  return selection.empty ? from <= selection.head && to >= selection.head : from < selection.to && to > selection.from;
}

function markdownSourceBlockChanges(view, entry) {
  const selection = view.state.selection.main, doc = view.state.doc;
  let first = doc.lineAt(selection.from), last = doc.lineAt(Math.max(selection.from, selection.to - (selection.empty ? 0 : 1)));
  const codeBlocks = [];
  const setextBlocks = [];
  const indentedBlocks = [];
  const listItems = [];
  let selectedTable = false;
  const tree = window.YOmuxCodeMirror.ensureSyntaxTree(view.state, doc.length, 100);
  tree?.iterate({enter(node) {
    if (node.name === 'Table' && markdownSourceSelectionOverlaps(selection, node.from, node.to)) selectedTable = true;
    if (/^SetextHeading[12]$/.test(node.name)) setextBlocks.push({first: doc.lineAt(node.from), last: doc.lineAt(Math.max(node.from, node.to - 1))});
    if (node.name === 'CodeBlock' && markdownSourceSelectionOverlaps(selection, node.from, node.to)) indentedBlocks.push({first: doc.lineAt(node.from).number, last: doc.lineAt(node.to).number});
    if (node.name === 'ListItem') listItems.push({from: node.from, to: node.to, parent: node.node.parent.from});
    if (node.name !== 'FencedCode' || !markdownSourceSelectionOverlaps(selection, node.from, node.to)) return;
    const opening = doc.lineAt(node.from), closing = doc.lineAt(node.to);
    if (opening.number < closing.number && /^\s*(?:```|~~~)/.test(closing.text)) {
      codeBlocks.push({opening, closing, indent: opening.text.match(/^ */)[0].length});
    }
  }});
  if (selectedTable) return [];
  const selectedSetext = entry.id === 'pre' ? [] : setextBlocks.filter(block => markdownSourceSelectionOverlaps(selection, block.first.from, block.last.to));
  if (selectedSetext.length) {
    first = doc.line(Math.min(first.number, selectedSetext[0].first.number));
    last = doc.line(Math.max(last.number, selectedSetext.at(-1).last.number));
  }
  const listOwner = line => listItems.findLast(item => item.from <= line.to && item.to > line.from);
  if (entry.group === 'list') {
    const owner = listOwner(first);
    if (owner) first = doc.lineAt(owner.from);
  }
  if (codeBlocks.length && first.number > codeBlocks[0].opening.number && last.number < codeBlocks.at(-1).closing.number) {
    first = doc.line(codeBlocks[0].opening.number + 1);
    last = doc.line(codeBlocks.at(-1).closing.number - 1);
  }
  if (indentedBlocks.length) {
    first = doc.line(Math.min(first.number, indentedBlocks[0].first));
    last = doc.line(Math.max(last.number, indentedBlocks.at(-1).last));
  }
  const lines = Array.from({length: last.number - first.number + 1}, (_, index) => doc.line(first.number + index));
  const listMatch = line => line.text.match(/^(\s*)(?:([-+*]|\d+[.)])\s+(?:\[([ xX])\]\s*)?)?/);
  const same = entry.group === 'list' && lines.every(line => {
    if (listItems.length && !line.text.trim()) return true;
    const owner = listOwner(line);
    if (owner && doc.lineAt(owner.from).number !== line.number) return true;
    const match = listMatch(line);
    return match[2] && (match[3] !== undefined ? 'check' : /^\d/.test(match[2]) ? 'number' : 'bullet') === entry.id;
  });
  const changes = [];
  const indentationShifts = new Map();
  const listCounters = new Map();
  for (const block of selectedSetext) {
    const underline = block.last;
    changes.push(underline.to < doc.length
      ? {from: underline.from, to: underline.to + 1, insert: ''}
      : {from: underline.from > 0 ? underline.from - 1 : underline.from, to: underline.to, insert: ''});
  }
  const preservedFences = entry.group === 'list' ? codeBlocks.filter(block => listOwner(block.opening)) : [];
  if (entry.id !== 'pre') for (const block of codeBlocks) {
    if (preservedFences.includes(block)) continue;
    const {opening, closing} = block;
    changes.push({from: opening.from, to: opening.to + 1, insert: ''}, {from: closing.from - 1, to: closing.to, insert: ''});
  }
  if (entry.id === 'pre' && indentedBlocks.length) {
    for (const line of lines) changes.push({from: line.from, to: line.from + Math.min(4, line.text.match(/^ */)[0].length), insert: ''});
  } else if (entry.id === 'pre') {
    const fenced = codeBlocks.length === 1 && codeBlocks[0].opening.number <= first.number && codeBlocks[0].closing.number >= last.number;
    if (fenced) {
      const {opening, closing} = codeBlocks[0];
      changes.push({from: opening.from, to: opening.to + 1, insert: ''}, {from: closing.from - 1, to: closing.to, insert: ''});
    }
    else changes.push({from: first.from, insert: '```\n'}, {from: last.to, insert: '\n```'});
  } else lines.forEach(line => {
    if (selectedSetext.some(block => block.last.number === line.number)) return;
    if (entry.group === 'list' && listItems.length && !line.text.trim()) return;
    if (codeBlocks.some(block => !preservedFences.includes(block) && (line.number === block.opening.number || line.number === block.closing.number))) return;
    const match = listMatch(line);
    const owner = listOwner(line);
    if (entry.group === 'list' && owner && doc.lineAt(owner.from).number !== line.number) {
      const inherited = indentationShifts.get(line.number) || 0;
      if (inherited) changes.push({from: line.from, to: line.from + match[1].length, insert: ' '.repeat(Math.max(0, match[1].length + inherited))});
      return;
    }
    const fencedBlock = codeBlocks.find(block => line.number > block.opening.number && line.number < block.closing.number);
    const dedent = indentedBlocks.some(block => line.number >= block.first && line.number <= block.last)
      ? Math.min(4, match[1].length)
      : fencedBlock ? Math.min(fencedBlock.indent, match[1].length) : 0;
    const isCodeContent = entry.id === 'normal' && (codeBlocks.some(block => line.number > block.opening.number && line.number < block.closing.number)
      || indentedBlocks.some(block => line.number >= block.first && line.number <= block.last));
    if (isCodeContent) {
      changes.push({from: line.from, to: line.to, insert: markdownEscapeInline(line.text.slice(dedent))});
      return;
    }
    const heading = line.text.slice(match[0].length).match(/^#{1,6}\s+/)?.[0] || '';
    const prefixEnd = line.from + match[0].length + heading.length;
    const counterKey = owner ? `list:${owner.parent}` : `indent:${match[1]}`;
    const count = (listCounters.get(counterKey) || 0) + 1;
    listCounters.set(counterKey, count);
    const marker = entry.group === 'list' ? same ? '' : entry.id === 'number' ? `${count}. ` : entry.id === 'check' ? '- [ ] ' : '- '
      : /^h[1-6]$/.test(entry.id) ? `${'#'.repeat(Number(entry.id.slice(1)))} ` : '';
    const inherited = (indentationShifts.get(line.number) || 0) - dedent;
    const indentation = ' '.repeat(Math.max(0, match[1].length + inherited));
    const openingTaskFence = entry.id === 'check' && !same && preservedFences.some(block => block.opening.number === line.number);
    const listPrefix = indentation + marker + (openingTaskFence ? `\n${indentation}  ` : '');
    changes.push({from: line.from, to: prefixEnd, insert: entry.group === 'list' ? listPrefix : match[0].slice(dedent) + marker});
    if (entry.group === 'list' && match[2]) {
      const nextWidth = same ? 0 : entry.id === 'number' ? String(count).length + 2 : 2;
      const delta = nextWidth - match[2].length - 1;
      if (owner && delta) for (let number = line.number + 1; number <= doc.lineAt(owner.to).number; number += 1) indentationShifts.set(number, (indentationShifts.get(number) || 0) + delta);
    }
  });
  for (const [number, delta] of indentationShifts) {
    if (number >= first.number && number <= last.number) continue;
    const line = doc.line(number), whitespace = line.text.match(/^\s*/)[0];
    if (!line.text.trim()) continue;
    changes.push({from: line.from, to: line.from + whitespace.length, insert: ' '.repeat(Math.max(0, whitespace.length + delta))});
  }
  changes.sort((a, b) => a.from - b.from);
  return changes;
}

function applyCodeMirrorMarkdownBlocks(view, entry) {
  view.dispatch({changes: markdownSourceBlockChanges(view, entry), userEvent: 'input.format'});
  return true;
}

function mapMarkdownTypingPair(panel, transaction) {
  const pair = panel._markdownTypingPair;
  if (!pair || !transaction.docChanged) return;
  pair.openFrom = transaction.changes.mapPos(pair.openFrom, -1);
  pair.closeFrom = transaction.changes.mapPos(pair.closeFrom, 1);
  const text = transaction.state.doc;
  if (text.sliceString(pair.openFrom, pair.openFrom + pair.open.length) !== pair.open || text.sliceString(pair.closeFrom, pair.closeFrom + pair.close.length) !== pair.close) panel._markdownTypingPair = null;
}

function markdownProseMirrorFormatState(view, entry) {
  const {selection, doc, storedMarks} = view.state;
  if (entry.mark) {
    const mark = view.state.schema.marks[entry.mark];
    if (selection.empty) return mark.isInSet(storedMarks || selection.$from.marks()) ? 'true' : 'false';
    let yes = false, no = false;
    doc.nodesBetween(selection.from, selection.to, node => {
      if (!node.isText || !node.text.trim()) return;
      if (mark.isInSet(node.marks)) yes = true; else no = true;
    });
    return yes && no ? 'mixed' : yes ? 'true' : 'false';
  }
  const values = new Set();
  doc.nodesBetween(selection.from, selection.to, (node, position) => {
    if (!node.isTextblock) return;
    const resolved = doc.resolve(position);
    const parent = resolved.parent;
    const list = resolved.depth > 0 ? resolved.node(resolved.depth - 1) : null;
    values.add(entry.group === 'list' ? parent.type.name === 'list_item' ? parent.attrs.taskChecked !== null ? 'check' : list?.type.name === 'ordered_list' ? 'number' : 'bullet' : ''
      : node.type.name === 'heading' ? `h${node.attrs.level}` : node.type.name === 'code_block' ? 'pre' : 'normal');
  });
  return values.has(entry.id) ? values.size > 1 ? 'mixed' : 'true' : 'false';
}

function markdownFormattingState(panel, entry, surface = markdownFormattingTarget(panel)) {
  if (!markdownFormattingWritable(panel, surface)) return 'false';
  return surface === 'view-editor' ? markdownProseMirrorFormatState(panel._pmView, entry) : markdownSourceFormatState(panel._cmView, entry);
}

function refreshMarkdownFormattingToolbar(panel) {
  const toolbar = panel.querySelector('.markdown-formatting-toolbar');
  if (!toolbar) return;
  const surface = markdownFormattingTarget(panel);
  toolbar.hidden = !markdownFormattingWritable(panel, surface);
  for (const button of toolbar.querySelectorAll('[data-markdown-format]')) {
    const entry = markdownFormatCommands.find(command => command.id === button.dataset.markdownFormat);
    button.setAttribute('aria-pressed', markdownFormattingState(panel, entry, surface));
  }
  for (const select of toolbar.querySelectorAll('select')) {
    const active = markdownFormatCommands.filter(entry => entry.group === select.dataset.formatGroup && markdownFormattingState(panel, entry, surface) === 'true');
    select.value = active.length === 1 ? active[0].id : '';
  }
  for (const button of toolbar.querySelectorAll('[data-markdown-list-indent]')) {
    const command = button.dataset.markdownListIndent;
    const available = surface === 'view-editor' && markdownProseMirrorListItemSelection(panel._pmView);
    button.hidden = !available;
    button.disabled = !available || !markdownProseMirrorListIndentEnabled(window.YOmuxProseMirror, panel._pmView, panel._pmSchema, command);
  }
}

function ensureMarkdownFormattingToolbar(panel) {
  if (panel.querySelector('.markdown-formatting-toolbar')) return refreshMarkdownFormattingToolbar(panel);
  const toolbar = document.createElement('div');
  toolbar.className = 'markdown-formatting-toolbar';
  toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', t('editor.toolbar.aria'));
  const focusTarget = () => {
    const surface = markdownFormattingTarget(panel);
    (surface === 'view-editor' ? panel._pmView : panel._cmView)?.focus();
  };
  for (const entry of markdownFormatCommands.filter(command => !command.group && command.id !== 'clearInline')) {
    const hint = markdownFormatShortcut(entry);
    const button = makeButton({label: entry.glyph, title: entry.label + (hint ? ` (${hint})` : ''), ariaLabel: entry.label});
    button.dataset.markdownFormat = entry.id;
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => { dispatchMarkdownFormat(panel, entry.id); focusTarget(); });
    toolbar.append(button);
  }
  for (const [group, label] of [['paragraph', 'Paragraph style'], ['list', 'List style']]) {
    const select = document.createElement('select'); select.dataset.formatGroup = group;
    select.setAttribute('aria-label', label);
    const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = label; select.append(placeholder);
    for (const entry of markdownFormatCommands.filter(command => command.group === group)) {
      const option = document.createElement('option'); option.value = entry.id;
      const hint = markdownFormatShortcut(entry); option.textContent = entry.label + (hint ? ` (${hint})` : ''); select.append(option);
    }
    select.addEventListener('change', () => { dispatchMarkdownFormat(panel, select.value); focusTarget(); });
    toolbar.append(select);
  }
  for (const entry of markdownFormatCommands.filter(command => command.group === 'listIndent')) {
    const button = makeButton({label: entry.glyph, title: entry.label + ` (${markdownFormatShortcut(entry)})`, ariaLabel: entry.label});
    button.dataset.markdownListIndent = entry.id;
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => { dispatchMarkdownFormat(panel, entry.id, 'view-editor'); focusTarget(); });
    toolbar.append(button);
  }
  const clear = markdownFormatCommands.find(command => command.id === 'clearInline');
  const clearHint = markdownFormatShortcut(clear);
  const clearButton = makeButton({label: clear.glyph, title: clear.label + (clearHint ? ` (${clearHint})` : ''), ariaLabel: clear.label});
  clearButton.dataset.markdownFormat = clear.id;
  clearButton.addEventListener('mousedown', event => event.preventDefault());
  clearButton.addEventListener('click', () => { dispatchMarkdownFormat(panel, clear.id); focusTarget(); });
  toolbar.append(clearButton);
  panel.querySelector('.file-editor-toolbar')?.after(toolbar);
  panel.addEventListener('focusin', event => {
    if (panel._pmView?.dom?.contains(event.target)) panel._markdownFormatSurface = 'view-editor';
    else if (panel._cmView?.dom?.contains(event.target)) panel._markdownFormatSurface = 'text-editor';
    refreshMarkdownFormattingToolbar(panel);
  });
  refreshMarkdownFormattingToolbar(panel);
}

function markdownListItemDepth($position) {
  for (let depth = $position.depth; depth > 0; depth -= 1) {
    if ($position.node(depth).type.name === 'list_item') return depth;
  }
  return 0;
}

function markdownProseMirrorListItemSelection(view) {
  if (!view?.state?.selection) return false;
  const {$from, $to} = view.state.selection;
  return markdownListItemDepth($from) > 0 && markdownListItemDepth($to) > 0;
}

function markdownProseMirrorListIndentEnabled(api, view, schema, command) {
  if (!markdownProseMirrorListItemSelection(view) || !schema?.nodes?.list_item) return false;
  if (command === 'listOutdent') {
    const {$from, $to} = view.state.selection;
    for (const position of [$from, $to]) {
      const itemDepth = markdownListItemDepth(position);
      if (itemDepth < 3 || position.node(itemDepth - 2).type.name !== 'list_item') return false;
    }
  }
  const apply = command === 'listIndent' ? api?.sinkListItem : api?.liftListItem;
  return typeof apply === 'function' && apply(schema.nodes.list_item)(view.state);
}

function applyProseMirrorListIndent(api, view, schema, command) {
  if (!markdownProseMirrorListIndentEnabled(api, view, schema, command)) return false;
  const apply = command === 'listIndent' ? api.sinkListItem : api.liftListItem;
  return apply(schema.nodes.list_item)(view.state, view.dispatch, view);
}

function applyProseMirrorListFormat(api, view, schema, command) {
  const {selection, doc} = view.state;
  const bookmarks = [selection.$anchor, selection.$head].map(position => ({node: position.parent, offset: position.parentOffset, position: position.pos}));
  const blockReplacements = new Map();
  const lists = [];
  const selectedBlocks = [];
  doc.descendants((node, position, parent) => {
    if (node.type.name === 'bullet_list' || node.type.name === 'ordered_list') {
      const items = [];
      node.forEach((item, offset) => {
        // A parent item is selected through its own textblock, not merely a selected child list.
        const start = position + offset + 2, end = start + item.firstChild.content.size;
        const selected = selection.empty ? selection.from >= start && selection.from <= end : selection.from <= end && selection.to > start;
        items.push({item, selected, position: position + offset + 1});
      });
      if (items.some(item => item.selected)) lists.push({node, position, items});
    }
    if (node.isTextblock && parent.type.name !== 'list_item' && parent.canReplaceWith(doc.resolve(position).index(), doc.resolve(position).index() + 1, schema.nodes.bullet_list) && (selection.empty ? selection.from > position && selection.from < position + node.nodeSize : selection.from < position + node.nodeSize && selection.to > position)) selectedBlocks.push({node, position, parent});
  });
  const selectedItems = lists.flatMap(list => list.items.filter(item => item.selected).map(({item}) => ({item, node: list.node})));
  const listKind = (item, node) => item.attrs.taskChecked !== null ? 'check' : node.type.name === 'ordered_list' ? 'number' : 'bullet';
  const remove = selectedItems.length > 0 && selectedBlocks.length === 0 && selectedItems.every(({item, node}) => listKind(item, node) === command);
  const listType = command === 'number' ? schema.nodes.ordered_list : schema.nodes.bullet_list;
  const convertedItem = item => schema.nodes.list_item.create({...item.attrs, taskChecked: command === 'check' ? false : null, taskLine: null}, item.content);
  const operations = [];
  for (const {node, position, items} of lists.sort((a, b) => b.position - a.position)) {
    const withChildren = item => {
      const owner = items.find(candidate => candidate.item === item);
      const children = operations.filter(operation => operation.from > owner.position && operation.to < owner.position + item.nodeSize);
      if (!children.length) return item;
      const inner = api.EditorState.create({doc: item}).tr;
      const roots = children.filter(operation => !children.some(other => other !== operation && other.from < operation.from && other.to > operation.to));
      for (const operation of roots.sort((a, b) => b.from - a.from)) inner.replaceWith(operation.from - owner.position - 1, operation.to - owner.position - 1, operation.replacement);
      return inner.doc;
    };
    const replacement = [];
    let buffer = [], selected = items[0].selected;
    const flush = () => {
      if (!buffer.length) return;
      if (selected && remove) buffer.forEach(item => withChildren(item).content.forEach(child => replacement.push(child)));
      else replacement.push((selected ? listType : node.type).create(selected ? {order: 1} : {...node.attrs, order: node.type.name === 'ordered_list' ? node.attrs.order + items.findIndex(item => item.item === buffer[0]) : undefined}, buffer.map(item => selected ? convertedItem(withChildren(item)) : withChildren(item))));
      buffer = [];
    };
    for (const item of items) {
      if (item.selected !== selected) { flush(); selected = item.selected; }
      buffer.push(item.item);
    }
    flush();
    operations.push({from: position, to: position + node.nodeSize, replacement});
  }
  // Adjacent paragraphs become one list; list children and unselected wrappers retain their nodes.
  let group = [];
  const flushBlocks = () => {
    if (!group.length) return;
    const items = group.map(({node}) => {
      const paragraph = node.type === schema.nodes.paragraph ? node : schema.nodes.paragraph.create(null, node.content);
      blockReplacements.set(node, paragraph);
      return convertedItem(schema.nodes.list_item.create(null, paragraph));
    });
    operations.push({from: group[0].position, to: group.at(-1).position + group.at(-1).node.nodeSize, replacement: listType.create({order: 1}, items)});
    group = [];
  };
  for (const block of selectedBlocks) {
    if (group.length && (group.at(-1).parent !== block.parent || group.at(-1).position + group.at(-1).node.nodeSize !== block.position)) flushBlocks();
    group.push(block);
  }
  flushBlocks();
  if (!operations.length) return false;
  const transaction = view.state.tr;
  // An outer selected item already carries its child lists; never replace an overlapping child twice.
  const roots = operations.filter(operation => !operations.some(other => other !== operation && other.from < operation.from && other.to > operation.to));
  for (const operation of roots.sort((a, b) => b.from - a.from)) transaction.replaceWith(operation.from, operation.to, operation.replacement);
  const positions = bookmarks.map(bookmark => {
    const target = blockReplacements.get(bookmark.node) || bookmark.node;
    let found = null;
    transaction.doc.descendants((node, position) => {
      if (node === target && found === null) found = position + 1 + Math.min(bookmark.offset, node.content.size);
    });
    return found === null ? transaction.mapping.map(bookmark.position) : found;
  });
  transaction.setSelection(api.TextSelection.between(transaction.doc.resolve(positions[0]), transaction.doc.resolve(positions[1])));
  view.dispatch(transaction.scrollIntoView());
  return true;
}
