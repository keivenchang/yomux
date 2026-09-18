// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Markdown preview parsing, sanitizing, source anchors, and Mermaid/SVG helpers split from 92_codemirror_editor.js.

function markdownTextWithSourceAnchors(text) {
  return String(text || '');
}

function markdownInlinePlainText(value) {
  return String(value || '')
    .replace(/\\([\\`*_[\]{}()#+.!\-|>])/g, '$1')
    .replace(/(`+)(.*?)\1/g, '$2')
    .replace(/\*\*|__/g, '')
    .replace(/~~/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/[*_]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function markdownPreviewVisibleText(node) {
  if (!node) return '';
  if (node.nodeType === 3) return String(node.nodeValue || '');
  if (node.nodeType !== 1) return '';
  if (String(node.tagName || '').toUpperCase() === 'BR') return '\n';
  if (node.classList?.contains('markdown-source-anchor')) return '';
  return Array.from(node.childNodes || []).map(markdownPreviewVisibleText).join('');
}

function markdownInlineSourceFromNode(node) {
  if (!node) return '';
  if (node.nodeType === 3) return String(node.nodeValue || '');
  if (node.nodeType !== 1) return '';
  if (node.classList?.contains('markdown-source-anchor')) return '';
  const tagName = String(node.tagName || '').toUpperCase();
  if (tagName === 'BR') return '\n';
  const content = Array.from(node.childNodes || []).map(markdownInlineSourceFromNode).join('');
  if (!content) return '';
  if (tagName === 'STRONG' || tagName === 'B') return `**${content}**`;
  if (tagName === 'EM' || tagName === 'I') return `*${content}*`;
  if (tagName === 'CODE') return `\`${content}\``;
  if (tagName === 'DEL' || tagName === 'S') return `~~${content}~~`;
  if (tagName === 'U') return `<u>${content}</u>`;
  return content;
}

function markdownSourceContinuationLine(line) {
  const value = String(line || '').trim();
  return Boolean(value) && !/^(?:#{1,6}\s|[-+*]\s|\d+[.)]\s|>|```|~~~|\||---+$)/.test(value);
}

function markdownEditableSourceRange(text, sourceLine, block, options = {}) {
  const lines = String(text || '').split('\n');
  const start = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (start >= lines.length) return null;
  const tagName = String(block?.tagName || '').toUpperCase();
  const heading = lines[start].match(/^(\s*#{1,6}\s+)(.*)$/);
  const storedEnd = Number(block?.dataset?.sourceEndLine || 0) - 1;
  const end = Number.isInteger(storedEnd) && storedEnd >= start
    ? storedEnd
    : tagName === 'P'
    ? (() => {
      let index = start;
      while (index + 1 < lines.length && markdownSourceContinuationLine(lines[index + 1])) index += 1;
      return index;
    })()
    : start;
  const body = heading && start === end
    ? heading[2]
    : lines.slice(start, end + 1).map(line => line.trim()).join(' ');
  if (!body.trim() || (tagName !== 'P' && !heading)) return null;
  if (/^\s*(?:[-+*]|\d+[.)])\s+/.test(lines[start]) || /^\s*[>|`~]/.test(lines[start])) return null;
  if (options.validateText !== false && (block?.textContent || block?.childNodes)) {
    if (markdownInlinePlainText(body) !== markdownInlinePlainText(markdownPreviewVisibleText(block))) return null;
  }
  return {
    start,
    end,
    prefix: heading ? heading[1] : (lines[start].match(/^\s*/)?.[0] || ''),
    body,
    heading: Boolean(heading),
  };
}

function markdownSourceLineOffsets(text, lineNumber) {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(lineNumber) || 1) - 1);
  let start = 0;
  for (let current = 0; current < index; current += 1) start += lines[current].length + 1;
  return {start, end: start + (lines[index] || '').length, line: lines[index] || ''};
}

function markdownEditableRangeOffsets(text, sourceLine, sourceEndLine, block) {
  const start = markdownSourceLineOffsets(text, sourceLine);
  const end = markdownSourceLineOffsets(text, sourceEndLine || sourceLine);
  const tagName = String(block?.tagName || '').toUpperCase();
  const heading = start.line.match(/^(\s*#{1,6}\s+)/);
  const bodyStart = heading && /^H[1-6]$/.test(tagName) ? start.start + heading[1].length : start.start;
  return {start: bodyStart, end: end.end, sourceStart: start.start, sourceEnd: end.end, prefix: heading?.[1] || ''};
}

function markdownPreviewBlockBySourceLine(container, sourceLine) {
  return Array.from(container?.querySelectorAll?.('[data-markdown-preview-editable="true"]') || [])
    .find(block => Number(block.dataset.sourceLine || 0) === Number(sourceLine)) || null;
}

function markdownPreviewTextNodeAtOffset(root, offset) {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let remaining = Math.max(0, Number(offset) || 0);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.parentElement?.classList?.contains('markdown-source-anchor')) continue;
    if (remaining <= node.nodeValue.length) return {node, offset: remaining};
    remaining -= node.nodeValue.length;
  }
  return null;
}

function markdownSourceOffsetAtVisibleOffset(source, visibleOffset) {
  const text = String(source || '');
  const target = Math.max(0, Number(visibleOffset) || 0);
  let visible = 0;
  for (let index = 0; index < text.length; index += 1) {
    const br = text.slice(index).match(/^<br\s*\/?\s*>/i);
    if (br) {
      visible += 1;
      index += br[0].length - 1;
    } else if (!'*_~'.includes(text[index])) {
      visible += 1;
    }
    if (visible >= target) return index + 1;
  }
  return text.length;
}

function selectMarkdownPreviewSourceRange(path, sourceLine, selectedText = '') {
  for (const panel of fileEditorPanelsForPath(path)) {
    const container = panel.querySelector?.('.file-editor-preview-pane-panel');
    const block = markdownPreviewBlockBySourceLine(container, sourceLine);
    if (!container || !block) continue;
    const visible = markdownPreviewVisibleText(block);
    const start = selectedText ? visible.indexOf(selectedText) : 0;
    if (start < 0) continue;
    const from = markdownPreviewTextNodeAtOffset(block, start);
    const to = markdownPreviewTextNodeAtOffset(block, start + selectedText.length);
    if (!from || !to) continue;
    const range = container.ownerDocument.createRange();
    range.setStart(from.node, from.offset);
    range.setEnd(to.node, to.offset);
    const selection = container.ownerDocument.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
  }
  return false;
}

function markdownPreviewSourceChange(container, sourcePanel, path, start, end, replacement, options = {}) {
  const state = fileEditorPanelState(sourcePanel);
  if (!state || state.kind !== 'text' || state.historical === true) return false;
  const before = state.content;
  const next = `${before.slice(0, start)}${replacement}${before.slice(end)}`;
  if (next === before) return false;
  container._markdownPreviewHistory = container._markdownPreviewHistory || {entries: [], index: -1};
  const history = container._markdownPreviewHistory;
  history.entries.splice(history.index + 1);
  history.entries.push({before, after: next});
  history.index += 1;
  container._markdownPreviewLastEdit = history.entries[history.index];
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false, previewEdit: true, skipPreviewPanel: container});
  for (const panel of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    if (panel?._cmView) syncCodeMirrorDocument(panel._cmView, next, {path});
  }
  return true;
}

function markdownTextWithBackspaceAtOffset(text, sourceOffset, blockStart = 0) {
  const source = String(text || '');
  const offset = Math.max(blockStart, Math.min(source.length, Number(sourceOffset) || 0));
  if (offset <= blockStart) {
    if (source.slice(blockStart - 2, blockStart) === '\n\n') return `${source.slice(0, blockStart - 2)}${source.slice(blockStart)}`;
    if (source[blockStart - 1] === '\n') return `${source.slice(0, blockStart - 1)}${source.slice(blockStart)}`;
    return null;
  }
  if (source[offset - 1] === '\n') return `${source.slice(0, offset - 1)}${source.slice(offset)}`;
  const br = source.slice(Math.max(blockStart, offset - 7), offset).match(/<br\s*\/?\s*>$/i);
  if (br) return `${source.slice(0, offset - br[0].length)}${source.slice(offset)}`;
  return null;
}

function handleMarkdownPreviewBackspace(container, event = null) {
  const selection = container.ownerDocument?.getSelection?.();
  const context = markdownPreviewSelectionContext(container) || {
    block: event?.target?.closest?.('[data-markdown-preview-editable="true"]') || container._markdownPreviewActiveBlock,
    selectedText: '',
  };
  const block = context?.block;
  const panel = container.closest?.('.file-editor-panel');
  const state = panel ? fileEditorPanelState(panel) : null;
  const path = container.dataset.mdPath || panel?.dataset?.filePath || '';
  if (!block || !panel || !state || state.kind !== 'text' || state.historical === true || !selection?.rangeCount) return false;
  const range = selection.getRangeAt(0);
  if (!range.collapsed || !block.contains(range.startContainer)) return false;
  const line = Number(block.dataset.sourceLine || 0);
  const offsets = markdownEditableRangeOffsets(state.content, line, block.dataset.sourceEndLine, block);
  const prefix = container.ownerDocument.createRange();
  prefix.selectNodeContents(block);
  prefix.setEnd(range.startContainer, range.startOffset);
  const sourceOffset = offsets.start + markdownSourceOffsetAtVisibleOffset(
    state.content.slice(offsets.start, offsets.end),
    Math.min(prefix.toString().length, offsets.end - offsets.start),
  );
  const next = markdownTextWithBackspaceAtOffset(state.content, sourceOffset, offsets.start);
  if (next === null) return false;
  const nextCaret = Math.max(0, sourceOffset - 1);
  if (!markdownPreviewSourceChange(container, panel, path, 0, state.content.length, next)) return false;
  renderFileEditorPreviewSurface(panel, container, path, fileEditorPanelState(panel).content, {preserveSelection: false, force: true});
  requestAnimationFrame(() => selectMarkdownPreviewSourceRange(path, line, state.content.slice(offsets.start, nextCaret)));
  return true;
}

function handleMarkdownPreviewEnter(container, event = null) {
  const selection = container.ownerDocument?.getSelection?.();
  const eventBlock = event?.target?.closest?.('[data-markdown-preview-editable="true"]');
  const context = markdownPreviewSelectionContext(container) || {
    block: eventBlock || container._markdownPreviewActiveBlock,
    selectedText: '',
  };
  const block = context?.block;
  const panel = container.closest?.('.file-editor-panel');
  const state = panel ? fileEditorPanelState(panel) : null;
  const path = container.dataset.mdPath || panel?.dataset?.filePath || '';
  if (!block || !panel || !state || state.kind !== 'text' || state.historical === true) return false;
  if (/^H[1-6]$/.test(String(block.tagName || '').toUpperCase())) return false;
  const line = Number(block.dataset.sourceLine || 0);
  const offsets = markdownEditableRangeOffsets(state.content, line, block.dataset.sourceEndLine, block);
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (!range && !container._markdownPreviewCaretOffset) return false;
  if (!range) return false;
  if (!block.contains(range.startContainer)) return false;
  const prefix = container.ownerDocument.createRange();
  prefix.selectNodeContents(block);
  prefix.setEnd(range.startContainer, range.startOffset);
  const visibleOffset = Math.min(
    prefix.toString().length,
    Math.max(0, offsets.end - offsets.start),
  );
  const sourceOffset = offsets.start + markdownSourceOffsetAtVisibleOffset(
    state.content.slice(offsets.start, offsets.end),
    visibleOffset,
  );
  const replacement = `${state.content.slice(offsets.start, sourceOffset)}<br>${state.content.slice(sourceOffset, offsets.end)}`;
  if (!markdownPreviewSourceChange(container, panel, path, offsets.start, offsets.end, replacement)) return false;
  const nextContent = fileEditorPanelState(panel).content;
  renderFileEditorPreviewSurface(panel, container, path, nextContent, {preserveSelection: false, force: true});
  requestAnimationFrame(() => {
    const nextBlock = markdownPreviewBlockBySourceLine(container, line);
    if (!nextBlock) return;
    nextBlock.focus();
    const target = markdownPreviewTextNodeAtOffset(nextBlock, prefix.toString().length + 1);
    if (!target) return;
    const nextSelection = container.ownerDocument.getSelection?.();
    const nextRange = container.ownerDocument.createRange();
    nextRange.setStart(target.node, target.offset);
    nextRange.collapse(true);
    nextSelection.removeAllRanges();
    nextSelection.addRange(nextRange);
  });
  return true;
}

function handleMarkdownPreviewInput(container, block) {
  if (!block) return false;
  const panel = container.closest?.('.file-editor-panel');
  const path = container.dataset.mdPath || panel?.dataset?.filePath || '';
  const state = panel ? fileEditorPanelState(panel) : null;
  const line = Number(block.dataset.sourceLine || 0);
  const offsets = state && markdownEditableRangeOffsets(state.content, line, block.dataset.sourceEndLine, block);
  if (!state || !offsets) return false;
  const rendered = markdownInlineSourceFromNode(block).replace(/\n+/g, '\n\n').trim();
  const next = `${state.content.slice(offsets.start, offsets.start + (offsets.prefix || '').length)}${rendered}`;
  return markdownPreviewSourceChange(container, panel, path, offsets.start, offsets.end, next);
}

function markdownEditableSourceLine(line, block) {
  const raw = String(line || '');
  const tagName = String(block?.tagName || '').toUpperCase();
  const heading = raw.match(/^(\s*#{1,6}\s+)(.*)$/);
  const prefix = heading ? heading[1] : '';
  const body = heading ? heading[2] : raw;
  if (!body.trim() || (tagName !== 'P' && !heading)) return null;
  if (/^\s*(?:[-+*]|\d+[.)])\s+/.test(raw) || /^\s*[>|~]/.test(raw) || /[\[\]|]/.test(body)) return null;
  if (block?.textContent || block?.childNodes) {
    if (markdownInlinePlainText(body) !== markdownInlinePlainText(markdownPreviewVisibleText(block))) return null;
  }
  return {prefix, body, heading: Boolean(heading)};
}

function markdownTextWithInlineLineEdited(text, sourceLine, nextInline, blockKind = 'paragraph') {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (index >= lines.length) return null;
  const parsed = markdownEditableSourceRange(text, sourceLine, {
    tagName: blockKind === 'heading' ? 'H1' : 'P',
  }, {validateText: false});
  if (!parsed) return null;
  const replacement = `${parsed.prefix}${nextInline}`;
  lines.splice(parsed.start, parsed.end - parsed.start + 1, replacement);
  return lines.join('\n');
}

function markdownTextWithInlineFormat(text, sourceLine, selectedText, command) {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (index >= lines.length) return null;
  const selected = String(selectedText || '').trim();
  if (!selected) return null;
  const inlineMarkers = [
    {open: '**', close: '**', command: 'bold'},
    {open: '__', close: '__', command: 'bold'},
    {open: '*', close: '*', command: 'italic'},
    {open: '_', close: '_', command: 'italic'},
    {open: '~~', close: '~~', command: 'strike'},
    {open: '<u>', close: '</u>', command: 'underline'},
    {open: '`', close: '`', command: 'code'},
  ];
  const sourceTextLine = lines[index];
  const directStart = sourceTextLine.indexOf(selected);
  if (directStart < 0) return null;
  const before = sourceTextLine.slice(0, directStart);
  const after = sourceTextLine.slice(directStart + selected.length);
  const active = new Set();
  let left = before;
  let right = after;
  let changed = true;
  while (changed) {
    changed = false;
    for (const marker of inlineMarkers) {
      if (left.endsWith(marker.open) && right.startsWith(marker.close)) {
        active.add(marker.command);
        left = left.slice(0, -marker.open.length);
        right = right.slice(marker.close.length);
        changed = true;
        break;
      }
    }
  }
  if (command !== 'clearInline' && !inlineMarkers.some(marker => marker.command === command)) return null;
  if (command === 'clearInline') {
    lines[index] = `${left}${selected}${right}`;
    return lines.join('\n');
  }
  if (active.has(command)) active.delete(command);
  else active.add(command);
  const wrappers = [
    ['bold', '**', '**'],
    ['italic', '*', '*'],
    ['strike', '~~', '~~'],
    ['underline', '<u>', '</u>'],
    ['code', '`', '`'],
  ];
  let formatted = selected;
  for (const [kind, open, close] of wrappers.slice().reverse()) {
    if (active.has(kind)) formatted = `${open}${formatted}${close}`;
  }
  lines[index] = `${left}${formatted}${right}`;
  return lines.join('\n');
  /* legacy implementation retained for reference:
  if (command === 'clearInline') {
    const start = lines[index].indexOf(selected);
    if (start < 0) return null;
    const before = lines[index].slice(0, start);
    const after = lines[index].slice(start + selected.length);
    const prefixes = ['**', '__', '*', '_', '~~', '<u>', '`'];
    const suffixes = ['**', '__', '*', '_', '~~', '</u>', '`'];
    let nextBefore = before;
    let nextAfter = after;
    let removed = true;
    while (removed) {
      removed = false;
      for (let i = 0; i < prefixes.length; i += 1) {
        if (nextBefore.endsWith(prefixes[i]) && nextAfter.startsWith(suffixes[i])) {
          nextBefore = nextBefore.slice(0, -prefixes[i].length);
          nextAfter = nextAfter.slice(suffixes[i].length);
          removed = true;
          break;
        }
      }
    }
    lines[index] = `${nextBefore}${selected}${nextAfter}`;
    return lines.join('\n');
  }
  const plainLine = markdownInlinePlainText(lines[index]);
  const plainStart = plainLine.indexOf(selected);
  if (plainStart < 0 || plainLine.indexOf(selected, plainStart + selected.length) >= 0) return null;
  const visibleSourceText = value => String(value || '')
    .replace(/\\([\\`*_[\]{}()#+.!\-|>])/g, '$1')
    .replace(/(`+)(.*?)\1/g, '$2')
    .replace(/\*\*|__/g, '')
    .replace(/~~/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/[*_]/g, '');
  const sourceOffsetAtVisible = visibleOffset => {
    for (let offset = 0; offset <= lines[index].length; offset += 1) {
      if (visibleSourceText(lines[index].slice(0, offset)).length >= visibleOffset) return offset;
    }
    return lines[index].length;
  };
  const directStart = lines[index].indexOf(selected);
  const start = directStart >= 0 ? directStart : sourceOffsetAtVisible(plainStart);
  const end = directStart >= 0 ? directStart + selected.length : sourceOffsetAtVisible(plainStart + selected.length);
  const marker = command === 'bold' ? ['**', '**']
    : command === 'italic' ? ['*', '*']
    : command === 'strike' ? ['~~', '~~']
        : command === 'underline' ? ['<u>', '</u>']
          : command === 'code' ? ['`', '`'] : null;
  if (!marker) return null;
  const before = lines[index].slice(0, start);
  const after = lines[index].slice(end);
  const active = before.endsWith(marker[0]) && after.startsWith(marker[1])
    && !(marker[0] === '*' && (before.endsWith('**') || after.startsWith('**')));
  lines[index] = active
    ? `${before.slice(0, -marker[0].length)}${selected}${after.slice(marker[1].length)}`
    : `${before}${marker[0]}${selected}${marker[1]}${after}`;
  return lines.join('\n');
  */
}

function markdownTextWithBlockFormat(text, sourceLine, command) {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (index >= lines.length || !lines[index].trim()) return null;
  const current = lines[index];
  const indent = current.match(/^\s*/)?.[0] || '';
  if (command === 'bullet') {
    if (/^\s*(?:[-+*]|\d+[.)])\s+/.test(current)) return null;
    lines[index] = `${indent}- ${current.trim()}`;
    return lines.join('\n');
  }
  if (command === 'pre') {
    if (current.trim().startsWith('```')) return null;
    lines.splice(index, 1, `${indent}__YOLOMUX_FENCE_START__`, current, `${indent}__YOLOMUX_FENCE_END__`);
    return lines.join('\n').replace(/__YOLOMUX_FENCE_(?:START|END)__/g, '```');
  }
  const heading = command.match(/^h([1-6])$/);
  if (!heading) return null;
  const body = current.replace(/^\s*#{1,6}\s+/, '').trim();
  if (!body || /[\[\]|]/.test(body)) return null;
  lines[index] = `${indent}${'#'.repeat(Number(heading[1]))} ${body}`;
  return lines.join('\n');
}

function markdownPreviewInlineBlockIsEditable(block) {
  return !block?.querySelector?.('div,p,h1,h2,h3,h4,h5,h6,ul,ol,blockquote,pre,table');
}

function markdownPreviewSelectionContext(container, event = null) {
  const selection = document.getSelection?.();
  const selectionNode = selection?.anchorNode;
  const selectionElement = selectionNode?.nodeType === 1 ? selectionNode : selectionNode?.parentElement;
  const eventElement = event?.target?.nodeType === 1 ? event.target : event?.target?.parentElement;
  const block = selectionElement?.closest?.('.markdown-body > *')
    || eventElement?.closest?.('[data-markdown-preview-editable="true"]')
    || container._markdownPreviewSelectionContext?.block;
  if (!block || !container.contains(block)) {
    const image = eventElement?.closest?.('img');
    if (image && container.contains(image)) return {block: image, selectedText: image.alt || image.dataset.originalSrc || image.src || '', image};
    return null;
  }
  const selectedText = selection && !selection.isCollapsed
    && block.contains(selection.anchorNode) && block.contains(selection.focusNode)
    ? selection.toString()
    : container._markdownPreviewSelectionContext?.block === block
      ? container._markdownPreviewSelectionContext.selectedText
      : '';
  return {block, selectedText: selectedText.trim()};
}

function markdownPreviewCaptureSelection(container) {
  const selection = document.getSelection?.();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const block = range.commonAncestorContainer?.parentElement?.closest?.('[data-markdown-preview-editable="true"]');
  if (!block || !container.contains(block) || !block.contains(range.startContainer) || !block.contains(range.endContainer)) return null;
  return {block, selectedText: selection.toString().trim()};
}

function markdownPreviewFormatActive(container, context, command) {
  const path = container?.dataset?.mdPath || '';
  const panel = container.closest?.('.file-editor-panel');
  const state = panel ? fileEditorPanelState(panel) : fileState.get(path);
  const sourceLine = Number(context?.block?.dataset?.sourceLine || 0);
  if (!state || !sourceLine || !context?.selectedText) return false;
  return markdownInlineFormatState(state.content, sourceLine, context.selectedText).has(command);
}

function markdownInlineFormatState(text, sourceLine, selectedText) {
  const line = String(text || '').split('\n')[Math.max(0, Number(sourceLine || 1) - 1)] || '';
  const selected = String(selectedText || '').trim();
  const start = line.indexOf(selected);
  if (start < 0 || !selected) return new Set();
  let left = line.slice(0, start);
  let right = line.slice(start + selected.length);
  const markers = [
    ['bold', '**', '**'], ['bold', '__', '__'], ['italic', '*', '*'], ['italic', '_', '_'],
    ['strike', '~~', '~~'], ['underline', '<u>', '</u>'], ['code', '`', '`'],
  ];
  const active = new Set();
  let changed = true;
  while (changed) {
    changed = false;
    for (const [kind, open, close] of markers) {
      if (left.endsWith(open) && right.startsWith(close)) {
        active.add(kind);
        left = left.slice(0, -open.length);
        right = right.slice(close.length);
        changed = true;
        break;
      }
    }
  }
  return active;
}

function markdownPreviewBlockClass(block) {
  const tag = String(block?.tagName || '').toUpperCase();
  if (/^H[1-6]$/.test(tag)) return `h${tag.slice(1)}`;
  if (block?.closest?.('pre')) return 'pre';
  if (block?.closest?.('li')) return 'bullet';
  return 'normal';
}

function markdownPreviewCopySelection(selectedText) {
  if (!selectedText) return false;
  return copyTextWithFeedback(selectedText, {statusText: t('status.copiedText')});
}

function markdownPreviewCopySelectionWithStyle(context) {
  const selection = document.getSelection?.();
  if (!selection || selection.isCollapsed) return false;
  const container = context?.container || context?.block?.closest?.('.markdown-body');
  if (!container || !container.contains(selection.anchorNode) || !container.contains(selection.focusNode)) return false;
  const range = context?.range || selection.getRangeAt(0);
  const wrapper = document.createElement('div');
  wrapper.append(range.cloneContents());
  const images = [...wrapper.querySelectorAll('img[src]')];
  // Let the browser serialize ordinary selections. Image selections need explicit data URLs:
  // Google Docs cannot fetch this app's authenticated raw-file URLs from clipboard HTML.
  if (!images.length && document.execCommand?.('copy') === true) {
    showCopyFeedback({statusText: t('status.copiedStyledText')});
    return true;
  }
  const text = selection.toString() || wrapper.textContent || '';
  if (!wrapper.innerHTML || (!text && !images.length)) return false;
  const clipboard = globalThis.navigator?.clipboard;
  if (globalThis.isSecureContext !== false && clipboard?.write && globalThis.ClipboardItem) {
    const imageData = images.map(image => fetch(image.currentSrc || image.src, {credentials: 'same-origin'})
      .then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.blob(); })
      .then(blob => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve({blob, dataUrl: String(reader.result)});
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      })));
    const html = Promise.all(imageData).then(results => {
      results.forEach((result, index) => images[index].setAttribute('src', result.dataUrl));
      return new Blob([wrapper.innerHTML], {type: 'text/html'});
    });
    const firstImage = imageData[0]?.then(result => result.blob);
    const item = new ClipboardItem({
      'text/plain': new Blob([text], {type: 'text/plain'}),
      'text/html': html,
      ...(firstImage ? {'image/png': firstImage} : {}),
    });
    // Pass promises to ClipboardItem immediately so the user activation is retained while images fetch.
    void clipboard.write([item]).then(() => showCopyFeedback({statusText: t('status.copiedStyledText')}));
    return true;
  }
  return copyTextWithFeedback(text, {statusText: t('status.copiedStyledText')});
}

function markdownPreviewImageContextMenu(image, event) {
  event.preventDefault();
  event.stopPropagation();
  showImageContextMenu(image, event.clientX, event.clientY);
}

function markdownPreviewPasteSelection(container, context) {
  if (!navigator.clipboard?.readText) return false;
  void navigator.clipboard.readText().then(text => {
    if (!text || !context?.block) return;
    const selection = document.getSelection?.();
    if (!selection?.rangeCount || !context.block.contains(selection.anchorNode)) return;
    const range = selection.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(text));
    selection.collapseToEnd();
    context.block.dispatchEvent(new Event('input', {bubbles: true}));
  });
  return true;
}

function markdownPreviewSelectionTransform(container, command, context = null) {
  const selected = context || markdownPreviewSelectionContext(container);
  if (!selected?.block) return false;
  const path = container?.dataset?.mdPath || '';
  const sourceLine = Number(selected.block?.dataset?.sourceLine || 0);
  const sourcePanel = container.closest?.('.file-editor-panel') || fileEditorPanelsForPath(path)
    .find(panel => fileEditorPanelState(panel)?.historical !== true) || null;
  const state = sourcePanel ? fileEditorPanelState(sourcePanel) : fileState.get(path);
  if (readOnlyMode || !path || !sourceLine || !state || state.kind !== 'text' || state.historical === true) return false;
  const next = ['bold', 'italic', 'strike', 'underline', 'code', 'clearInline'].includes(command)
    ? markdownTextWithInlineFormat(state.content, sourceLine, selected.selectedText, command)
    : markdownTextWithBlockFormat(state.content, sourceLine, command);
  if (next === null || next === state.content) return false;
  const before = state.content;
  container._markdownPreviewHistory = container._markdownPreviewHistory || {entries: [], index: -1};
  container._markdownPreviewHistory.entries.splice(container._markdownPreviewHistory.index + 1);
  container._markdownPreviewHistory.entries.push({before, after: next});
  container._markdownPreviewHistory.index += 1;
  container._markdownPreviewLastEdit = {before, after: next};
  container._markdownPreviewSelectionContext = null;
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false, previewEdit: true, skipPreviewPanel: container});
  for (const panel of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    if (panel?._cmView) syncCodeMirrorDocument(panel._cmView, next, {path});
  }
  return true;
}

function markdownEditorSelectionTransform(view, panel, path, command, context) {
  const selectedText = String(context?.selectedText || '').trim();
  const sourceLine = Number(context?.sourceLine || 0);
  if (!view || !panel || !path || !sourceLine || !selectedText) return false;
  const state = fileEditorPanelState(panel);
  if (readOnlyMode || !state || state.kind !== 'text' || state.historical === true) return false;
  const next = ['bold', 'italic', 'strike', 'underline', 'code', 'clearInline'].includes(command)
    ? markdownTextWithInlineFormat(state.content, sourceLine, selectedText, command)
    : markdownTextWithBlockFormat(state.content, sourceLine, command);
  if (next === null || next === state.content) return false;
  handleFileEditorContentChanged(panel, path, next, {syntax: false});
  syncCodeMirrorDocument(view, next, {path});
  const line = view.state.doc.line(sourceLine);
  const selectedStart = line.text.indexOf(selectedText);
  if (selectedStart >= 0) {
    view.dispatch({selection: {anchor: line.from + selectedStart, head: line.from + selectedStart + selectedText.length}});
  }
  return true;
}

const MARKDOWN_TASK_LINE_RE = /^(\s*(?:[-+*]|\d+[.)])\s+\[)([ xX])(\]\s*)/;
const MARKDOWN_INLINE_NUMBERED_TASK_RE = /^\s*(?:[-+*]|\d+[.)])\s+\[[ xX]\]\s+(\d+)([.)])\s+\S/;
const MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS = 'markdown-rendered-task-checkbox';

function markdownTaskLineEntries(text) {
  return String(text || '').split('\n')
    .map((line, index) => {
      const task = line.match(MARKDOWN_TASK_LINE_RE);
      if (!task) return null;
      const numbered = line.match(MARKDOWN_INLINE_NUMBERED_TASK_RE);
      return {
        line: index + 1,
        checked: task[2].toLowerCase() === 'x',
        inlineNumber: numbered ? Number(numbered[1]) : null,
        inlineNumberText: numbered?.[1] || '',
        inlineDelimiter: numbered?.[2] || '',
      };
    })
    .filter(Boolean);
}

function markdownMarkedTaskRenderer(marked) {
  if (typeof marked?.Renderer !== 'function') return null;
  const renderer = new marked.Renderer();
  const renderListItem = renderer.listitem;
  renderer.listitem = function renderMarkedTaskListItem(text, task, checked) {
    const renderedText = task
      ? String(text).replace(/^<input\b/, `<input class="${MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS}"`)
      : text;
    return renderListItem.call(this, renderedText, task, checked);
  };
  return renderer;
}

function markdownRenderedTaskCheckboxes(root) {
  return Array.from(root?.querySelectorAll?.('input[type="checkbox"]') || []).filter(input => {
    const item = input.parentElement;
    const list = item?.parentElement;
    return String(item?.tagName || '').toUpperCase() === 'LI'
      && ['UL', 'OL'].includes(String(list?.tagName || '').toUpperCase())
      && (input.classList?.contains(MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS)
        || input.classList?.contains('markdown-task-checkbox'));
  });
}

function markdownInlineOrderedTask(item, input, task) {
  if (!task || task.inlineNumber === null) return null;
  const siblings = Array.from(item?.children || []).filter(node => node !== input);
  if (siblings.length !== 1) return null;
  const ordered = siblings[0];
  if (ordered.parentElement !== item || String(ordered.tagName || '').toUpperCase() !== 'OL') return null;
  if (ordered.children?.length !== 1 || String(ordered.firstElementChild?.tagName || '').toUpperCase() !== 'LI') return null;
  const nestedItem = ordered.firstElementChild;
  if (nestedItem.querySelector?.('ul,ol')) return null;
  const outsideText = Array.from(item.childNodes || []).some(node => (
    node !== input && node !== ordered && String(node.textContent || '').trim()
  ));
  if (outsideText) return null;
  const parsedStart = Number(ordered.getAttribute?.('start') || 1);
  return parsedStart === task.inlineNumber ? {nestedItem, ordered} : null;
}

function applyMarkdownTaskListClasses(root, sourceText = '') {
  const tasks = markdownTaskLineEntries(sourceText);
  for (const [index, input] of markdownRenderedTaskCheckboxes(root).entries()) {
    const item = input.parentElement;
    const list = item?.parentElement;
    if (!item || !['UL', 'OL'].includes(String(list?.tagName || '').toUpperCase())) continue;
    item.classList.add('task-list-item');
    list.classList.add('contains-task-list');
    if (input.parentElement !== item || item.querySelector?.(':scope > .markdown-task-label')) continue;
    // A grid treats each text node and inline element as a separate anonymous item. Keep the task
    // prose under one grid owner so inline code cannot take a full row and strand later text in the
    // checkbox column.
    const label = (item.ownerDocument || document).createElement('span');
    label.className = 'markdown-task-label';
    const inlineOrdered = markdownInlineOrderedTask(item, input, tasks[index]);
    if (inlineOrdered) {
      const number = (item.ownerDocument || document).createElement('span');
      number.className = 'markdown-task-number';
      number.textContent = `${tasks[index].inlineNumberText}${tasks[index].inlineDelimiter} `;
      label.appendChild(number);
      for (const node of Array.from(inlineOrdered.nestedItem.childNodes || inlineOrdered.nestedItem.children || [])) {
        label.appendChild(node);
      }
      item.replaceChildren(input, label);
      continue;
    }
    while (input.nextSibling) label.appendChild(input.nextSibling);
    item.appendChild(label);
  }
}

function applyMarkdownSourceLines(container, source) {
  const lines = String(source || '').split('\n');
  let searchFrom = 0;
  const blocks = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6,p,blockquote,pre,ul,ol,table,hr'));
  for (const block of blocks) {
    const text = markdownPreviewVisibleText(block).trim();
    let lineIndex = -1;
    let lineEnd = -1;
    for (let index = searchFrom; index < lines.length; index += 1) {
      const trimmed = lines[index].trim();
      if (!trimmed) continue;
      if (block.tagName === 'HR' && /^-{3,}$/.test(trimmed)) {
        lineIndex = index;
        break;
      }
      if (block.tagName === 'TABLE' && trimmed.startsWith('|')) {
        lineIndex = index;
        break;
      }
      if (text && (trimmed.includes(text.slice(0, Math.min(text.length, 40)))
        || markdownInlinePlainText(trimmed) === markdownInlinePlainText(text))) {
        lineIndex = index;
        lineEnd = index;
        if (block.tagName === 'P') {
          let sourceText = trimmed;
          while (lineEnd + 1 < lines.length && markdownSourceContinuationLine(lines[lineEnd + 1])) {
            sourceText += ` ${lines[lineEnd + 1].trim()}`;
            if (markdownInlinePlainText(sourceText) === markdownInlinePlainText(text)) break;
            lineEnd += 1;
          }
        }
        break;
      }
    }
    if (lineIndex >= 0) {
      block.dataset.sourceLine = String(lineIndex + 1);
      block.dataset.sourceEndLine = String((lineEnd >= lineIndex ? lineEnd : lineIndex) + 1);
      const anchor = document.createElement('span');
      anchor.className = 'markdown-source-anchor';
      anchor.dataset.sourceLine = String(lineIndex + 1);
      block.appendChild(anchor);
      searchFrom = (lineEnd >= lineIndex ? lineEnd : lineIndex) + 1;
    }
  }
}

const MARKDOWN_PREVIEW_BLOCKED_TAGS = new Set([
  'applet',
  'audio',
  'base',
  'button',
  'canvas',
  'embed',
  'form',
  'iframe',
  'link',
  'math',
  'meta',
  'object',
  'option',
  'script',
  'select',
  'source',
  'style',
  'svg',
  'textarea',
  'track',
  'video',
]);
const MARKDOWN_PREVIEW_URL_ATTRS = new Set(['href', 'src', 'poster', 'xlink:href']);
const MARKDOWN_PREVIEW_SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);
const MARKDOWN_PREVIEW_SAFE_IMAGE_DATA = /^data:image\/(?:png|gif|jpe?g|webp);/i;
const MARKDOWN_PREVIEW_INPUT_ATTRS = new Set(['type', 'checked', 'disabled', 'aria-label', 'class']);

function markdownPreviewUrlAllowed(value, tagName) {
  const raw = String(value || '').trim();
  if (!raw) return true;
  if (raw.startsWith('#') || raw.startsWith('/') || raw.startsWith('./') || raw.startsWith('../')) return true;
  if (!raw.startsWith('//') && !/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) return true;
  try {
    const base = globalThis.location?.href || 'http://localhost/';
    const url = new URL(raw, base);
    if (MARKDOWN_PREVIEW_SAFE_PROTOCOLS.has(url.protocol.toLowerCase())) return true;
    return tagName === 'img' && url.protocol.toLowerCase() === 'data:' && MARKDOWN_PREVIEW_SAFE_IMAGE_DATA.test(raw);
  } catch (_) {
    return false;
  }
}

function sanitizeMarkdownPreviewAttribute(element, attr) {
  const name = String(attr?.name || '').toLowerCase();
  if (!name) return;
  const tagName = String(element.tagName || '').toLowerCase();
  if (tagName === 'input' && !MARKDOWN_PREVIEW_INPUT_ATTRS.has(name)) {
    element.removeAttribute(attr.name);
    return;
  }
  if (name.startsWith('on') || name === 'style' || name === 'srcdoc' || name === 'srcset' || name === 'formaction') {
    element.removeAttribute(attr.name);
    return;
  }
  if (name.includes(':') && name !== 'xlink:href') {
    element.removeAttribute(attr.name);
    return;
  }
  if (MARKDOWN_PREVIEW_URL_ATTRS.has(name) && !markdownPreviewUrlAllowed(attr.value, tagName)) {
    element.removeAttribute(attr.name);
    return;
  }
  if (name === 'target' && element.getAttribute('target') === '_blank') {
    element.setAttribute('rel', 'noopener noreferrer');
  }
}

function markdownPreviewInputAllowed(element) {
  return String(element?.getAttribute?.('type') || '').toLowerCase() === 'checkbox';
}

function sanitizeMarkdownPreviewNode(root) {
  const elementNode = globalThis.Node?.ELEMENT_NODE || 1;
  const commentNode = globalThis.Node?.COMMENT_NODE || 8;
  for (const child of Array.from(root?.childNodes || [])) {
    if (child.nodeType === commentNode) {
      child.remove();
      continue;
    }
    if (child.nodeType !== elementNode) continue;
    const tagName = String(child.tagName || '').toLowerCase();
    if (tagName === 'input') {
      if (!markdownPreviewInputAllowed(child)) {
        child.remove();
        continue;
      }
      child.setAttribute('type', 'checkbox');
      child.setAttribute('disabled', '');
    }
    if (MARKDOWN_PREVIEW_BLOCKED_TAGS.has(tagName)) {
      child.remove();
      continue;
    }
    if (tagName === 'input' && String(child.getAttribute('type') || '').toLowerCase() !== 'checkbox') {
      child.remove();
      continue;
    }
    for (const attr of Array.from(child.attributes || [])) {
      if (tagName === 'input' && !MARKDOWN_PREVIEW_INPUT_ATTRS.has(String(attr?.name || '').toLowerCase())) {
        child.removeAttribute(attr.name);
        continue;
      }
      sanitizeMarkdownPreviewAttribute(child, attr);
    }
    sanitizeMarkdownPreviewNode(child);
  }
}

function sanitizeMarkdownPreviewHtml(html) {
  const template = document.createElement('template');
  if (!template.content) {
    const fallback = document.createElement('div');
    fallback.textContent = String(html ?? '');
    return fallback;
  }
  template.innerHTML = String(html ?? '');
  sanitizeMarkdownPreviewNode(template.content);
  return template.content;
}

const MARKDOWN_HTML_LIGHT_BG_CLASS = 'markdown-html-light-bg';
const MARKDOWN_PREVIEW_NAMED_BGCOLORS = {
  white: [255, 255, 255],
  yellow: [255, 255, 0],
  lightyellow: [255, 255, 224],
  lemonchiffon: [255, 250, 205],
  cornsilk: [255, 248, 220],
  ivory: [255, 255, 240],
  beige: [245, 245, 220],
};

function markdownPreviewBgcolorRgb(value) {
  const raw = String(value || '').trim().toLowerCase();
  if (!raw) return null;
  if (MARKDOWN_PREVIEW_NAMED_BGCOLORS[raw]) return MARKDOWN_PREVIEW_NAMED_BGCOLORS[raw];
  const match = raw.match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  const hex = match[1];
  if (hex.length === 3) {
    return Array.from(hex, digit => parseInt(`${digit}${digit}`, 16));
  }
  return [
    parseInt(hex.slice(0, 2), 16),
    parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16),
  ];
}

function markdownPreviewLinearColorChannel(channel) {
  const value = Number(channel) / 255;
  return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

function markdownPreviewBgcolorIsLight(value) {
  const rgb = markdownPreviewBgcolorRgb(value);
  if (!rgb) return false;
  const [red, green, blue] = rgb.map(markdownPreviewLinearColorChannel);
  return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue) >= 0.58;
}

function applyMarkdownHtmlBackgroundClasses(root) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('table[bgcolor], th[bgcolor], td[bgcolor]').forEach(element => {
    if (markdownPreviewBgcolorIsLight(element.getAttribute('bgcolor'))) {
      element.classList.add(MARKDOWN_HTML_LIGHT_BG_CLASS);
    }
  });
}

function trimMarkdownCodeBlockEdgeNewlines(root) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('pre > code').forEach(block => {
    const text = String(block.textContent || '');
    const trimmed = text.replace(/^(?:\r?\n)+|(?:\r?\n)+$/g, '');
    if (trimmed !== text) block.textContent = trimmed;
  });
}

const MARKDOWN_ALERT_MARKER_RE = /^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i;

function markdownAlertSpacingNodeIsInvisible(node) {
  if (!node) return false;
  if (node.nodeType === 3) return !String(node.nodeValue || '').trim();
  if (node.nodeType === 1) return node.classList?.contains?.('markdown-source-anchor');
  return false;
}

function markdownAlertHasVisiblePreviousSibling(node) {
  for (let sibling = node?.previousSibling; sibling; sibling = sibling.previousSibling) {
    if (!markdownAlertSpacingNodeIsInvisible(sibling)) return true;
  }
  return false;
}

function removeMarkdownAlertLeadingBreaks(container) {
  for (let child = container?.firstChild; child;) {
    const isLeadingBreak = child.nodeType === 1 && child.tagName === 'BR';
    if (!isLeadingBreak && !markdownAlertSpacingNodeIsInvisible(child)) break;
    const next = child.nextSibling;
    child.remove();
    child = next;
  }
}

function removeMarkdownAlertMarker(root) {
  const showText = globalThis.NodeFilter?.SHOW_TEXT || 4;
  const walker = document.createTreeWalker(root, showText);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!MARKDOWN_ALERT_MARKER_RE.test(node.nodeValue || '')) continue;
    const hadVisibleBefore = markdownAlertHasVisiblePreviousSibling(node);
    node.nodeValue = String(node.nodeValue || '').replace(MARKDOWN_ALERT_MARKER_RE, '');
    const parent = node.parentElement;
    if (parent && !hadVisibleBefore) removeMarkdownAlertLeadingBreaks(parent);
    if (parent?.matches?.('p') && !String(parent.textContent || '').trim() && parent.children.length === 0) {
      parent.remove();
    }
    return true;
  }
  return false;
}

function applyMarkdownAlertClasses(root) {
  if (!root?.querySelectorAll) return;
  root.querySelectorAll('blockquote').forEach(blockquote => {
    const firstParagraph = blockquote.querySelector(':scope > p') || blockquote.querySelector('p');
    const marker = String(firstParagraph?.textContent || blockquote.textContent || '').match(MARKDOWN_ALERT_MARKER_RE);
    if (!marker) return;
    const type = marker[1].toLowerCase();
    blockquote.classList.add('markdown-alert', `markdown-alert-${type}`);
    removeMarkdownAlertMarker(firstParagraph || blockquote);
  });
}

// turn bare http(s) URLs in rendered markdown into real <a> links — version-proof against
// marked's GFM autolink missing them (e.g. when per-line source anchors are interleaved). Skips text
// already inside <a>/<code>/<pre> so existing links and code samples are untouched. Reuses
// markdownPreviewUrlAllowed so only safe schemes link; mirrors the app's safe-link attributes.
function linkifyBareUrls(root) {
  if (!root || typeof document.createTreeWalker !== 'function') return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      for (let el = node.parentElement; el; el = el.parentElement) {
        const tag = el.tagName ? el.tagName.toLowerCase() : '';
        if (tag === 'a' || tag === 'code' || tag === 'pre') return NodeFilter.FILTER_REJECT;
      }
      return /\bhttps?:\/\/\S/.test(node.nodeValue || '') ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
    },
  });
  const targets = [];
  while (walker.nextNode()) targets.push(walker.currentNode);
  const urlRe = /\bhttps?:\/\/[^\s<>"')\]}]+/g;
  for (const textNode of targets) {
    const text = textNode.nodeValue;
    let last = 0;
    let match;
    const frag = document.createDocumentFragment();
    urlRe.lastIndex = 0;
    while ((match = urlRe.exec(text))) {
      const url = match[0].replace(/[.,;:!?]+$/, '');   // drop trailing sentence punctuation
      const start = match.index;
      const end = start + url.length;
      if (start > last) frag.appendChild(document.createTextNode(text.slice(last, start)));
      if (markdownPreviewUrlAllowed(url, 'a')) {
        const a = document.createElement('a');
        a.href = url;
        a.textContent = url;
        a.target = '_blank';
        a.rel = 'noreferrer noopener';
        frag.appendChild(a);
      } else {
        frag.appendChild(document.createTextNode(url));
      }
      last = end;
    }
    if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
    textNode.replaceWith(frag);
  }
}

function mermaidApiIsUsable(api) {
  return Boolean(api?.initialize && api?.render);
}

function mermaidBundleUrl(options = {}) {
  const base = '/static/vendor/mermaid.min.js';
  return options.force ? `${base}?retry=${Date.now()}` : base;
}

function loadMermaidBundleScript(options = {}) {
  if (!options.force && mermaidApiIsUsable(window.mermaid)) return Promise.resolve(window.mermaid);
  if (options.force) mermaidBundlePromise = null;
  if (!mermaidBundlePromise) {
    mermaidBundlePromise = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = mermaidBundleUrl(options);
      script.async = true;
      script.onload = () => resolve(window.mermaid || null);
      script.onerror = () => reject(new Error(`Mermaid bundle failed to load: ${script.src}`));
      document.head.appendChild(script);
    });
  }
  return mermaidBundlePromise;
}

// The two readable label/line "ink" colors for the Mermaid preview: MERMAID_LIGHT_INK on dark
// surfaces/fills, MERMAID_DARK_INK on light ones. One owner so the dark/light contrast pair, the
// dark-surface foreground fallback, and the label style default cannot drift apart.
const MERMAID_LIGHT_INK = '#e4e8ee';
const MERMAID_DARK_INK = '#0f172a';

function mermaidSurfacePalette() {
  // The diagram renders on the PREVIEW surface, whose Bright/Dark/Vanilla display mode is independent
  // of the app theme. Derive the palette from that mode, NOT from document `--text` (which follows the
  // app theme): a Bright preview on a dark app must still get dark lines/text on its white surface,
  // otherwise the light app `--text` paints light-gray lines that are illegible on white.
  const light = typeof editorPreviewThemeState === 'function' && editorPreviewThemeState() !== 'dark';
  if (light) {
    return {dark: false, fg: '#17202c', surfaceBg: '#ffffff', nodeBg: '#f4f6fa', border: '#c2c9d6', cluster: '#eef2f7'};
  }
  return {
    dark: true,
    fg: svgPreviewColor('--text', MERMAID_LIGHT_INK),
    surfaceBg: svgPreviewColor('--panel', '#151922'),
    nodeBg: svgPreviewColor('--panel', '#151922'),
    border: svgPreviewColor('--line', '#2a3140'),
    cluster: svgPreviewColor('--panel2', '#1e2430'),
  };
}

function mermaidPreviewConfig() {
  const p = mermaidSurfacePalette();
  return {
    startOnLoad: false,
    securityLevel: 'strict',
    deterministicIds: true,
    deterministicIDSeed: 'yolomux-preview',
    theme: 'base',
    htmlLabels: false,
    flowchart: {
      htmlLabels: false,
      useMaxWidth: true,
      nodeSpacing: 72,
      rankSpacing: 72,
    },
    themeVariables: {
      background: p.surfaceBg,
      mainBkg: p.nodeBg,
      primaryColor: p.nodeBg,
      primaryTextColor: p.fg,
      primaryBorderColor: p.border,
      lineColor: p.fg,
      textColor: p.fg,
      fontFamily: svgPreviewFontFamily(),
      fontSize: '16px',
      nodeBorder: p.border,
      clusterBkg: p.cluster,
      clusterBorder: p.border,
    },
  };
}

function configureMermaidApi(api) {
  api.initialize(mermaidPreviewConfig());
  return api;
}

async function loadMermaidApi() {
  if (mermaidApiIsUsable(window.mermaid)) return configureMermaidApi(window.mermaid);
  if (!mermaidApiPromise) {
    mermaidApiPromise = (async () => {
      let bundleError = null;
      try {
        let api = await loadMermaidBundleScript();
        if (mermaidApiIsUsable(api)) return configureMermaidApi(api);
        api = await loadMermaidBundleScript({force: true});
        if (mermaidApiIsUsable(api)) return configureMermaidApi(api);
        bundleError = new Error(t('preview.mermaid.renderFailed'));
      } catch (error) {
        bundleError = error;
      }
      throw bundleError || new Error(t('preview.mermaid.renderFailed'));
    })();
  }
  try {
    return await mermaidApiPromise;
  } catch (error) {
    mermaidApiPromise = null;
    throw error;
  }
}

function splitMarkdownResourceUrl(value) {
  const raw = String(value || '').trim();
  const match = raw.match(/^([^?#]*)([?#].*)?$/);
  return {
    path: match ? match[1] : raw,
    suffix: match ? (match[2] || '') : '',
  };
}

function markdownPreviewImageTarget(src, markdownPath) {
  const raw = String(src || '').trim();
  if (!raw || !markdownPath) return null;
  if (raw.startsWith('#') || raw.startsWith('//')) return null;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(raw)) {
    if (/^https?:/i.test(raw)) return {src: raw, path: '', external: true};
    if (/^data:/i.test(raw) && MARKDOWN_PREVIEW_SAFE_IMAGE_DATA.test(raw)) return {src: raw, path: '', external: true};
    return null;
  }
  const {path: rawPath} = splitMarkdownResourceUrl(raw);
  if (!rawPath) return null;
  const resolved = joinAndNormalize(dirnameOf(markdownPath), safeDecodeURIComponent(rawPath));
  return {src: rawFileUrl(resolved), path: resolved, external: false};
}

function prosemirrorPreviewImageSource(image, path) {
  if (!image || !path) return Promise.resolve(false);
  const target = markdownPreviewImageTarget(image.dataset.originalSrc || image.getAttribute('src') || '', path);
  if (!target || target.external) return Promise.resolve(false);
  return installRawFileMediaSource(image, target.path, {
    onFailure: error => {
      image.classList.add('prosemirror-image-error');
      image.title = userMessageText(error, t('preview.markdown.imageUnavailable', {path: target.path}));
    },
  }).then(result => result.ok === true);
}

function markdownImageFallbackNode(path, label = '') {
  const node = document.createElement('span');
  node.className = 'markdown-image-error';
  const text = document.createElement('span');
  text.textContent = label || t('preview.markdown.imageUnavailable', {path});
  node.appendChild(text);
  node.append(...previewFileActionLinks(path, {leadingSeparator: ' '}));
  return node;
}

function scheduleMarkdownImageFallbackAfterUserScroll(previewContainer, img, createFallback) {
  const completion = schedulePreviewDeferredWorkAfterUserScroll(img, 'markdown-image-failure', () => {
    if (previewContainer?._markdownPreviewGeneration && !previewContainer.contains(img)) return false;
    img.replaceWith(createFallback());
    return true;
  });
  trackPreviewAsyncCompletion(previewContainer, completion);
  return completion;
}

function rewriteMarkdownPreviewImages(root, markdownPath, options = {}) {
  if (!root || !markdownPath) return [];
  for (const img of Array.from(root.querySelectorAll?.('img[src]') || [])) {
    const original = img.getAttribute('src') || '';
    const target = markdownPreviewImageTarget(original, markdownPath);
    if (!target) continue;
    img.classList.add('markdown-preview-image');
    img.dataset.originalSrc = original;
    if (target.path) img.dataset.resolvedPath = target.path;
    if (!img.getAttribute('alt') && target.path) img.setAttribute('alt', basenameOf(target.path));
    if (target.external) {
      img.setAttribute('src', target.src);
      img.addEventListener('error', () => {
        void scheduleMarkdownImageFallbackAfterUserScroll(options.previewContainer, img, () => (
          markdownImageFallbackNode(target.path, t('preview.markdown.imageUnavailable', {path: target.path || original}))
        ));
      }, {once: true});
      continue;
    }
    // The fragment is detached until renderMarkdownPreviewInto replaces the container. Start the
    // request after attachment so the browser cannot start a relative request before authentication.
    img.dataset.markdownRawPath = target.path;
    img.removeAttribute('src');
    img.addEventListener('error', () => {
      if (options.isCurrent?.() === false) return;
      void scheduleMarkdownImageFallbackAfterUserScroll(options.previewContainer, img, () => (
        markdownImageFallbackNode(target.path, t('preview.markdown.imageUnavailable', {path: target.path || original}))
      ));
    }, {once: true});
  }
  return [];
}

function markdownTextWithTaskLineToggled(text, sourceLine, checked) {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (index >= lines.length || !MARKDOWN_TASK_LINE_RE.test(lines[index])) return null;
  lines[index] = lines[index].replace(MARKDOWN_TASK_LINE_RE, (_, prefix, _marker, suffix) => `${prefix}${checked ? 'x' : ' '}${suffix}`);
  return lines.join('\n');
}

function updateMarkdownInlineFromPreview(container, block) {
  const path = container?.dataset?.mdPath || '';
  const sourceLine = Number(block?.dataset?.sourceLine || 0);
  const sourcePanel = container.closest?.('.file-editor-panel') || fileEditorPanelsForPath(path)
    .find(panel => fileEditorPanelState(panel)?.historical !== true) || null;
  const state = sourcePanel ? fileEditorPanelState(sourcePanel) : fileState.get(path);
  if (readOnlyMode || !path || !sourceLine || !state || state.kind !== 'text' || state.historical === true) return false;
  if (!markdownPreviewInlineBlockIsEditable(block)) {
    renderFileEditorPreviewSurface(sourcePanel, container, path, state.content, {preserveSelection: false});
    return false;
  }
  const nextInline = markdownInlineSourceFromNode(block).replace(/\n+/g, ' ').trim();
  const kind = /^H[1-6]$/.test(String(block.tagName || '').toUpperCase()) ? 'heading' : 'paragraph';
  const next = markdownTextWithInlineLineEdited(state.content, sourceLine, nextInline, kind);
  if (next === null || next === state.content) return false;
  container._markdownPreviewLastEdit = {before: state.content, after: next};
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false, previewEdit: true, skipPreviewPanel: container});
  for (const panel of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    if (panel?._cmView) {
      panel._previewEditContainer = container;
      syncCodeMirrorDocument(panel._cmView, next, {path});
      delete panel._previewEditContainer;
    }
  }
  return true;
}

function updateMarkdownFormatFromPreview(container, block, command) {
  const path = container?.dataset?.mdPath || '';
  const sourceLine = Number(block?.dataset?.sourceLine || 0);
  const sourcePanel = container.closest?.('.file-editor-panel') || fileEditorPanelsForPath(path)
    .find(panel => fileEditorPanelState(panel)?.historical !== true) || null;
  const state = sourcePanel ? fileEditorPanelState(sourcePanel) : fileState.get(path);
  const selection = document.getSelection?.();
  const selectedText = selection && !selection.isCollapsed && block.contains(selection.anchorNode) && block.contains(selection.focusNode)
    ? selection.toString()
    : '';
  if (readOnlyMode || !path || !sourceLine || !state || state.kind !== 'text' || state.historical === true) return false;
  if (!markdownPreviewInlineBlockIsEditable(block)) return false;
  const next = markdownTextWithInlineFormat(state.content, sourceLine, selectedText, command);
  if (next === null || next === state.content) return false;
  container._markdownPreviewLastEdit = {before: state.content, after: next};
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false, previewEdit: true, skipPreviewPanel: container});
  for (const panel of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    if (panel?._cmView) syncCodeMirrorDocument(panel._cmView, next, {path});
  }
  return true;
}

function markdownPreviewEditorToolbar(container) {
  const toolbar = document.createElement('div');
  toolbar.className = 'markdown-preview-editor-toolbar';
  toolbar.setAttribute('role', 'toolbar');
  toolbar.setAttribute('aria-label', t('editor.toolbar.aria'));
  for (const [command, label] of [['bold', 'B'], ['italic', 'I']]) {
    const button = makeButton({
      className: 'markdown-preview-editor-format-button',
      label: command === 'bold' ? 'B' : 'I',
      title: `${t('editor.toolbar.aria')}: ${label}`,
      ariaLabel: `${t('editor.toolbar.aria')}: ${label}`,
    });
    button.dataset.markdownPreviewCommand = command;
    toolbar.appendChild(button);
  }
  container.prepend(toolbar);
  return toolbar;
}

function markdownFormattingContextMenu(event, context, options = {}) {
  const menu = document.createElement('div');
  menu.className = 'terminal-context-menu markdown-preview-context-menu';
  menu.setAttribute('role', 'menu');
  const closeMenu = () => markdownPreviewContextMenuController.close();
  const apply = command => options.applyCommand?.(command, context) === true;
  if (options.href) {
    appendUrlContextMenuItems(menu, options.href, closeMenu, {modifyUrl: options.modifyUrl, removeUrl: options.removeUrl});
    appendContextMenuSeparator(menu);
  } else if (context.selectedText && typeof options.addUrl === 'function') {
    const label = t('contextmenu.addUrl');
    appendContextMenuButton(menu, label === 'contextmenu.addUrl' ? 'Add URL' : label, options.addUrl, closeMenu);
    appendContextMenuSeparator(menu);
  }
  const label = (key, fallback) => {
    const translated = t(key);
    return translated === key ? fallback : translated;
  };
  appendContextMenuButton(menu, label('contextmenu.copyText', 'Copy text'), () => markdownPreviewCopySelection(context.selectedText), closeMenu, {disabled: !context.selectedText});
  const hasRichContent = Boolean(context.selectedText || context.block?.querySelector?.('img'));
  appendContextMenuButton(menu, label('contextmenu.copyWithStyle', 'Copy with style'), () => markdownPreviewCopySelectionWithStyle(context), closeMenu, {disabled: !hasRichContent});
  appendContextMenuButton(menu, 'Paste', () => options.paste?.(context), closeMenu, {disabled: typeof options.paste !== 'function'});
  appendContextMenuSeparator(menu);
  const action = (label, command, disabled = false, checked = undefined) => {
    const button = appendContextMenuButton(
      menu,
      label,
      () => apply(command),
      closeMenu,
      {disabled, checked},
    );
    button.dataset.markdownCommand = command;
    return button;
  };
  action('Bold', 'bold', !context.selectedText, options.isActive?.('bold', context) === true);
  action('Italic', 'italic', !context.selectedText, options.isActive?.('italic', context) === true);
  action('Strikethrough', 'strike', !context.selectedText, options.isActive?.('strike', context) === true);
  action('Underline', 'underline', !context.selectedText, options.isActive?.('underline', context) === true);
  action('Clear inline formatting', 'clearInline', !context.selectedText);
  appendContextMenuSeparator(menu);
  for (const [label, command] of [['Inline code', 'code'], ['Preformatted block', 'pre'], ['Bullet list', 'bullet'], ['Title / H1', 'h1'], ['Heading 2 / H2', 'h2'], ['Heading 3 / H3', 'h3'], ['Heading 4 / H4', 'h4'], ['Heading 5 / H5', 'h5']]) {
    action(label, command, false);
  }
  action('Normal text', 'normal', false, markdownPreviewBlockClass(context.block) === 'normal');
  markdownPreviewContextMenuController.open(menu, event.clientX, event.clientY);
}

function markdownPreviewContextMenu(container, event, context) {
  markdownFormattingContextMenu(event, context, {
    ...context,
    container,
    applyCommand: command => markdownPreviewSelectionTransform(container, command, context),
    paste: () => markdownPreviewPasteSelection(container, context),
    isActive: command => markdownPreviewFormatActive(container, context, command),
  });
}

function markdownEditorContextMenu(view, panel, path, event, context) {
  markdownFormattingContextMenu(event, context, {
    applyCommand: command => markdownEditorSelectionTransform(view, panel, path, command, context),
    isActive: command => markdownEditorFormatActive(panel, context, command),
  });
}

function markdownEditorFormatActive(panel, context, command) {
  const state = fileEditorPanelState(panel);
  return state ? markdownInlineFormatState(state.content, context?.sourceLine, context?.selectedText).has(command) : false;
}

function bindMarkdownPreviewEditing(container, text, markdownPath) {
  if (!markdownPath || container._markdownReadOnly === true || !container.closest?.('.file-editor-panel')) return;
  const editableBlocks = [];
  for (const block of Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6,p'))) {
    const line = Number(block.dataset.sourceLine || 0);
    if (!line || !markdownEditableSourceRange(text, line, block, {validateText: false})) continue;
    block.dataset.markdownPreviewEditable = 'true';
    block.contentEditable = 'true';
    block.spellcheck = true;
    block.setAttribute('role', 'textbox');
    block.setAttribute('aria-label', t('common.edit'));
    editableBlocks.push(block);
  }
  if (!editableBlocks.length) return;
  container.dataset.markdownPreviewEditor = 'true';
  const toolbar = markdownPreviewEditorToolbar(container);
  toolbar.contentEditable = 'false';
  container._markdownPreviewEditingDisposer = bindScopedOnce(container, 'markdown-preview-editing', scope => {
    editableBlocks.forEach((block, index) => {
      scope.ownEvent(`markdown-preview-enter-${index}`, block, 'keydown', event => {
        if (event.key !== 'Enter') return;
        if (handleMarkdownPreviewEnter(container, event)) event.preventDefault();
      });
    });
    scope.ownEvent('beforeinput-preview-enter', container, 'beforeinput', event => {
      if (event.inputType !== 'insertParagraph' && event.inputType !== 'insertLineBreak') return;
      if (handleMarkdownPreviewEnter(container, event)) event.preventDefault();
    }, {capture: true});
    scope.ownEvent('pointerdown', container, 'pointerdown', event => {
      if (event.button !== 2) return;
      const context = markdownPreviewCaptureSelection(container) || markdownPreviewSelectionContext(container, event);
      if (context) container._markdownPreviewSelectionContext = context;
    }, {capture: true});
    scope.ownEvent('mousedown', container, 'mousedown', event => {
      if (event.button !== 2) return;
      const context = markdownPreviewCaptureSelection(container) || markdownPreviewSelectionContext(container, event);
      if (context) container._markdownPreviewSelectionContext = context;
    }, {capture: true});
    scope.ownEvent('focusin', container, 'focusin', event => {
      const block = event.target?.closest?.('[data-markdown-preview-editable="true"]');
      if (block) {
        container._markdownPreviewActiveBlock = block;
        const state = fileEditorPanelState(container.closest?.('.file-editor-panel'));
        container._markdownPreviewActiveBlockSource = state?.content || '';
      }
    });
    scope.ownEvent('mousedown', container, 'mousedown', event => {
      const button = event.target?.closest?.('[data-markdown-preview-command]');
      if (!button) return;
      event.preventDefault();
    });
    scope.ownEvent('click', container, 'click', event => {
      const button = event.target?.closest?.('[data-markdown-preview-command]');
      if (!button) return;
      const selection = document.getSelection?.();
      const block = selection?.anchorNode?.parentElement?.closest?.('[data-markdown-preview-editable="true"]');
      if (block) updateMarkdownFormatFromPreview(container, block, button.dataset.markdownPreviewCommand);
    });
    scope.ownEvent('contextmenu', container, 'contextmenu', event => {
      const image = event.target?.closest?.('img');
      if (image && container.contains(image)) {
        markdownPreviewImageContextMenu(image, event);
        return;
      }
      const context = container._markdownPreviewSelectionContext || markdownPreviewCaptureSelection(container) || markdownPreviewSelectionContext(container, event);
      if (!context || !context.block) return;
      event.preventDefault();
      event.stopPropagation();
      markdownPreviewContextMenu(container, event, context);
    }, {capture: true});
    scope.ownEvent('keydown', container, 'keydown', event => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey || String(event.key).toLowerCase() !== 's') return;
      event.preventDefault();
      const panel = container.closest?.('.file-editor-panel');
      const path = container.dataset.mdPath || panel?.dataset?.filePath || '';
      if (path && panel) void saveFileEditor(path, panel);
    });
    scope.ownEvent('input', container, 'input', event => {
      if (event.target?.closest?.('.ProseMirror') && !event.target?.closest?.('[data-markdown-preview-editable="true"]')) return;
      const block = event.target?.closest?.('[data-markdown-preview-editable="true"]')
        || container._markdownPreviewActiveBlock
        || markdownPreviewSelectionContext(container)?.block;
      if (!block || !container.contains(block)) return;
      handleMarkdownPreviewInput(container, block);
    });
    scope.ownEvent('selectionchange', container.ownerDocument, 'selectionchange', () => {
      const context = markdownPreviewCaptureSelection(container);
      if (!context) return;
      const panel = container.closest?.('.file-editor-panel');
      const path = container.dataset.mdPath || '';
      const state = panel ? fileEditorPanelState(panel) : null;
      const line = Number(context.block.dataset.sourceLine || 0);
      const selection = container.ownerDocument.getSelection?.();
      if (!state || !selection?.rangeCount) return;
      const range = selection.getRangeAt(0);
      const prefix = container.ownerDocument.createRange();
      prefix.selectNodeContents(context.block);
      prefix.setEnd(range.startContainer, range.startOffset);
      const visibleStart = prefix.toString().length;
      const offsets = markdownEditableRangeOffsets(state.content, line, context.block.dataset.sourceEndLine, context.block);
      container._markdownPreviewSourceSelection = {
        path,
        sourceLine: line,
        start: offsets.start + visibleStart,
        end: offsets.start + visibleStart + context.selectedText.length,
        text: context.selectedText,
      };
      if (panel?._cmView && context.selectedText) {
        const lineInfo = panel._cmView.state.doc.line(line);
        const visibleStart = prefix.toString().length;
        const sourceOffset = markdownSourceOffsetAtVisibleOffset(lineInfo.text, visibleStart);
        const sourceEnd = markdownSourceOffsetAtVisibleOffset(lineInfo.text, visibleStart + context.selectedText.length);
        panel._cmView.dispatch({selection: {anchor: lineInfo.from + sourceOffset, head: lineInfo.from + sourceEnd}});
      }
    });
    scope.ownEvent('keydown-preview-enter', container, 'keydown', event => {
      if (event.key !== 'Enter') return;
      if (handleMarkdownPreviewEnter(container, event)) event.preventDefault();
    }, {capture: true});
    scope.ownEvent('keydown-preview-backspace', container, 'keydown', event => {
      if (event.key !== 'Backspace') return;
      if (handleMarkdownPreviewBackspace(container, event)) event.preventDefault();
    }, {capture: true});
    scope.ownEvent('paste', container, 'paste', event => {
      const context = markdownPreviewSelectionContext(container);
      const pasted = event.clipboardData?.getData?.('text/plain') || '';
      if (!context?.block || !pasted) return;
      event.preventDefault();
      const selection = document.getSelection?.();
      if (!selection?.rangeCount || !context.block.contains(selection.anchorNode) || !context.block.contains(selection.focusNode)) return;
      const range = selection.getRangeAt(0);
      range.deleteContents();
      range.insertNode(document.createTextNode(pasted));
      selection.collapseToEnd();
      context.block.dispatchEvent(new Event('input', {bubbles: true}));
    });
    scope.ownEvent('beforeinput', container, 'beforeinput', event => {
      if (event.inputType === 'insertParagraph' || event.inputType === 'insertLineBreak') {
        if (handleMarkdownPreviewEnter(container, event)) event.preventDefault();
        return;
      }
    });
    scope.ownEvent('beforeinput-preview-backspace', container, 'beforeinput', event => {
      if (event.inputType !== 'deleteContentBackward') return;
      if (handleMarkdownPreviewBackspace(container, event)) event.preventDefault();
    }, {capture: true});
    scope.ownEvent('keydown', container, 'keydown-preview-history', event => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      const key = String(event.key || '').toLowerCase();
      const history = container._markdownPreviewHistory;
      if (key !== 'z' || !history?.entries?.length) return;
      event.preventDefault();
      const panel = container.closest?.('.file-editor-panel');
      const path = container.dataset.mdPath || '';
      const nextIndex = event.shiftKey ? Math.min(history.entries.length - 1, history.index + 1) : history.index;
      if (!event.shiftKey && history.index < 0) return;
      if (event.shiftKey && nextIndex <= history.index) return;
      const edit = history.entries[nextIndex];
      const next = event.shiftKey ? edit.after : edit.before;
      const state = panel ? fileEditorPanelState(panel) : fileState.get(path);
      if (!state || state.content === next) return;
      history.index = event.shiftKey ? nextIndex : history.index - 1;
      container._markdownPreviewLastEdit = history.index >= 0 ? history.entries[history.index] : null;
      handleFileEditorContentChanged(panel, path, next, {syntax: false, previewEdit: true, skipPreviewPanel: container});
      for (const linked of fileEditorPanelsForPath(path)) {
        if (linked?._cmView) syncCodeMirrorDocument(linked._cmView, next, {path});
      }
      if (container._markdownPreviewSourceSelection) {
        selectMarkdownPreviewSourceRange(path, container._markdownPreviewSourceSelection.sourceLine, container._markdownPreviewSourceSelection.text);
      }
    });
  });
}

function disposeMarkdownPreviewEditing(container) {
  container?._markdownPreviewEditingDisposer?.();
  if (container) delete container._markdownPreviewEditingDisposer;
}

function updateMarkdownTaskFromPreview(container, input) {
  const path = container?.dataset?.mdPath || '';
  const sourceLine = Number(input?.dataset?.sourceLine || 0);
  const panels = fileEditorPanelsForPath(path);
  const sourcePanel = container.closest?.('.file-editor-panel') || panels.find(panel => fileEditorPanelState(panel)?.historical !== true) || null;
  const state = sourcePanel ? fileEditorPanelState(sourcePanel) : fileState.get(path);
  if (state?.historical === true) return false;
  if (readOnlyMode || !path || !sourceLine || !state || state.kind !== 'text') return false;
  const next = markdownTextWithTaskLineToggled(state.content, sourceLine, input.checked === true);
  if (next === null || next === state.content) return false;
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false});
  for (const panel of panels) {
    if (fileEditorPanelState(panel)?.historical === true) continue;
    if (panel?._cmView) syncCodeMirrorDocument(panel._cmView, next, {path});
  }
  return true;
}

function bindMarkdownTaskCheckboxes(container, text, markdownPath) {
  const tasks = markdownTaskLineEntries(text);
  const checkboxes = markdownRenderedTaskCheckboxes(container);
  checkboxes.forEach((input, index) => {
    const task = tasks[index];
    if (!task) return;
    input.dataset.sourceLine = String(task.line);
    input.classList.add('markdown-task-checkbox');
    input.checked = task.checked;
    if (markdownPath) {
      input.disabled = readOnlyMode || container._markdownReadOnly === true;
      if (input.disabled) input.setAttribute('disabled', 'disabled');
      else input.removeAttribute('disabled');
      input.setAttribute('aria-label', t('editor.toggleTaskLine', {line: task.line}));
    }
  });
  if (markdownPath) {
    bindScopedOnce(container, 'markdown-task-checkboxes', scope => scope.ownEvent('change', container, 'change', event => {
      const input = event.target?.closest?.('input[type="checkbox"].markdown-task-checkbox[data-source-line]');
      if (!input || !container.contains(input)) return;
      event.preventDefault();
      event.stopPropagation();
      const updated = updateMarkdownTaskFromPreview(container, input);
      if (!updated) input.checked = !input.checked;
    }));
  }
}

function markdownFallbackDestinationAndTitle(value) {
  const raw = String(value || '').trim();
  if (!raw) return {dest: '', title: ''};
  if (raw.startsWith('<')) {
    const end = raw.indexOf('>');
    if (end >= 0) return {dest: raw.slice(1, end), title: raw.slice(end + 1).trim().replace(/^["']|["']$/g, '')};
  }
  const titleMatch = raw.match(/^(.+?)\s+["']([^"']*)["']\s*$/);
  if (titleMatch) return {dest: titleMatch[1].trim(), title: titleMatch[2]};
  let dest = '';
  let escaped = false;
  let index = 0;
  for (; index < raw.length; index += 1) {
    const ch = raw[index];
    if (escaped) {
      dest += ch;
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (/\s/.test(ch)) break;
    dest += ch;
  }
  return {dest, title: raw.slice(index).trim().replace(/^["']|["']$/g, '')};
}

function findMarkdownInlineCloseBracket(text, start) {
  let escaped = false;
  for (let index = start; index < text.length; index += 1) {
    const ch = text[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (ch === ']') return index;
  }
  return -1;
}

function findMarkdownInlineCloseParen(text, start) {
  let escaped = false;
  let quote = '';
  let depth = 0;
  for (let index = start; index < text.length; index += 1) {
    const ch = text[index];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (quote) {
      if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === '(') {
      depth += 1;
      continue;
    }
    if (ch === ')') {
      if (depth === 0) return index;
      depth -= 1;
    }
  }
  return -1;
}

function markdownInlineResourceHtml(text, hold) {
  const source = String(text || '');
  let out = '';
  let index = 0;
  while (index < source.length) {
    const image = source.startsWith('![', index);
    const link = !image && source[index] === '[';
    if (!image && !link) {
      out += source[index];
      index += 1;
      continue;
    }
    const labelStart = index + (image ? 2 : 1);
    const labelEnd = findMarkdownInlineCloseBracket(source, labelStart);
    if (labelEnd < 0 || source[labelEnd + 1] !== '(') {
      out += source[index];
      index += 1;
      continue;
    }
    const destStart = labelEnd + 2;
    const destEnd = findMarkdownInlineCloseParen(source, destStart);
    if (destEnd < 0) {
      out += source[index];
      index += 1;
      continue;
    }
    const label = source.slice(labelStart, labelEnd);
    const rawDest = source.slice(destStart, destEnd);
    const {dest, title} = markdownFallbackDestinationAndTitle(rawDest);
    if (!dest) {
      out += source.slice(index, destEnd + 1);
      index = destEnd + 1;
      continue;
    }
    const titleAttr = title ? ` title="${esc(title)}"` : '';
    out += image
      ? hold(`<img alt="${esc(label)}" src="${esc(dest)}"${titleAttr}>`)
      : hold(`<a href="${esc(dest)}"${titleAttr}>${esc(label)}</a>`);
    index = destEnd + 1;
  }
  return out;
}

function markdownInlineHtml(text) {
  const placeholders = [];
  const hold = html => {
    const token = `@@YOLOMUX_MD_${placeholders.length}@@`;
    placeholders.push([token, html]);
    return token;
  };
  const source = markdownInlineResourceHtml(text, hold);
  let html = esc(source)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
  for (const [token, value] of placeholders) html = html.replaceAll(token, value);
  return html;
}

function markdownFallbackTableHtml(lines, start) {
  if (start + 1 >= lines.length || !/^\s*\|?[\s:-]+\|[\s|:-]*$/.test(lines[start + 1])) return null;
  const rows = [];
  let index = start;
  while (index < lines.length && /^\s*\|/.test(lines[index])) {
    if (index !== start + 1) rows.push(lines[index]);
    index += 1;
  }
  const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => markdownInlineHtml(cell.trim()));
  const header = cells(rows[0] || '');
  const bodyRows = rows.slice(1);
  const head = `<thead><tr>${header.map(cell => `<th>${cell}</th>`).join('')}</tr></thead>`;
  const body = `<tbody>${bodyRows.map(row => `<tr>${cells(row).map(cell => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody>`;
  return {html: `<table>${head}${body}</table>`, next: index};
}

function fallbackMarkdownToHtml(text) {
  const lines = String(text || '').split('\n');
  const out = [];
  let paragraph = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    out.push(`<p>${paragraph.map(line => markdownInlineHtml(line.trim())).join('<br>')}</p>`);
    paragraph = [];
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      continue;
    }
    const fence = trimmed.match(/^```([A-Za-z0-9_-]+)?\s*$/);
    if (fence) {
      flushParagraph();
      const language = String(fence[1] || 'text').toLowerCase();
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        code.push(lines[index]);
        index += 1;
      }
      out.push(`<pre><code class="language-${esc(language)}">${esc(code.join('\n'))}</code></pre>`);
      continue;
    }
    const table = markdownFallbackTableHtml(lines, index);
    if (table) {
      flushParagraph();
      out.push(table.html);
      index = table.next - 1;
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = heading[1].length;
      out.push(`<h${level}>${markdownInlineHtml(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*_]{3,}$/.test(trimmed)) {
      flushParagraph();
      out.push('<hr>');
      continue;
    }
    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      const quotedLines = [];
      while (index < lines.length) {
        const quoted = lines[index].trim().match(/^>\s?(.*)$/);
        if (!quoted) break;
        quotedLines.push(quoted[1]);
        index += 1;
      }
      const groups = [];
      let current = [];
      for (const quotedLine of quotedLines) {
        if (quotedLine.trim()) {
          current.push(quotedLine);
          continue;
        }
        if (current.length) groups.push(current);
        current = [];
      }
      if (current.length) groups.push(current);
      out.push(`<blockquote>${groups.map(group => `<p>${group.map(item => markdownInlineHtml(item.trim())).join('<br>')}</p>`).join('')}</blockquote>`);
      index -= 1;
      continue;
    }
    const task = trimmed.match(/^[-+*]\s+\[([ xX])\]\s+(.+)$/);
    if (task) {
      flushParagraph();
      const items = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^[-+*]\s+\[([ xX])\]\s+(.+)$/);
        if (!item) break;
        const checked = item[1].toLowerCase() === 'x' ? ' checked' : '';
        items.push(`<li class="task-list-item"><input class="${MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS}" type="checkbox"${checked} disabled> ${markdownInlineHtml(item[2])}</li>`);
        index += 1;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      index -= 1;
      continue;
    }
    const bullet = trimmed.match(/^[-+*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      const items = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^[-+*]\s+(.+)$/);
        if (!item || /^[-+*]\s+\[[ xX]\]\s+/.test(lines[index].trim())) break;
        items.push(`<li>${markdownInlineHtml(item[1])}</li>`);
        index += 1;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      index -= 1;
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  return out.join('');
}

function markdownPreviewHtml(text) {
  if (typeof window.marked !== 'undefined' && typeof window.marked.parse === 'function') {
    return window.marked.parse(markdownTextWithSourceAnchors(text), {
      gfm: true,
      breaks: true,
      renderer: markdownMarkedTaskRenderer(window.marked),
    });
  }
  return fallbackMarkdownToHtml(markdownTextWithSourceAnchors(text));
}

function invalidateMarkdownPreviewArtifacts(container) {
  // A Markdown source replacement invalidates every derived artifact below it:
  // Mermaid's async SVG/blob image, rewritten local images, syntax highlighting,
  // and zoom observers. Mark old Mermaid hosts stale before replacing the DOM so
  // a slow prior render cannot install its SVG after newer Markdown wins.
  const generation = Number(container?._markdownPreviewGeneration || 0) + 1;
  if (container) container._markdownPreviewGeneration = generation;
  releaseRawFileMediaSources(container);
  for (const host of Array.from(container?.querySelectorAll?.('.mermaid-preview-host') || [])) {
    disposeMermaidPreviewHost(host);
  }
  return generation;
}

function renderMarkdownPreviewInto(container, text, markdownPath, options = {}) {
  const generation = invalidateMarkdownPreviewArtifacts(container);
  container._previewAsync = null;
  disposeMarkdownPreviewEditing(container);
  const html = markdownPreviewHtml(text);
  const frag = sanitizeMarkdownPreviewHtml(html);
  applyMarkdownTaskListClasses(frag, text);
  trimMarkdownCodeBlockEdgeNewlines(frag);
  applyMarkdownHtmlBackgroundClasses(frag);
  applyMarkdownAlertClasses(frag);
  linkifyBareUrls(frag);
  const localImages = rewriteMarkdownPreviewImages(frag, markdownPath, {
    isCurrent: () => container._markdownPreviewGeneration === generation,
    previewContainer: container,
  });
  container._markdownReadOnly = options.readOnly === true;
  container.replaceChildren(frag);
  const localImagePromises = [];
  for (const img of Array.from(container.querySelectorAll?.('img')) || []) {
    const rawPath = String(img.dataset.markdownRawPath || '');
    if (!rawPath) continue;
    if (img.isConnected) {
      localImagePromises.push(installRawFileMediaSource(img, rawPath, {
        isCurrent: () => container._markdownPreviewGeneration === generation && img.isConnected,
        onFailure: error => {
          img.classList.add('markdown-preview-image-error');
          img.title = userMessageText(error, t('preview.markdown.imageUnavailable', {path: rawPath}));
        },
        onDecodeFailure: error => {
          img.classList.add('markdown-preview-image-error');
          img.title = userMessageText(error, t('preview.markdown.imageUnavailable', {path: rawPath}));
        },
      }));
    }
    delete img.dataset.markdownRawPath;
  }
  applyMarkdownSourceLines(container, text);
  if (options.readOnly !== true) bindMarkdownPreviewEditing(container, text, markdownPath);
  const mermaid = renderMarkdownMermaidBlocks(container, markdownPath, {
    context: options.context || '',
    isCurrent: () => container._markdownPreviewGeneration === generation,
  });
  container._previewAsync = Promise.all([mermaid, ...localImages, ...localImagePromises]);
  bindMarkdownTaskCheckboxes(container, text, markdownPath);
  installLinkContextMenu(container);   // right-click Copy URL / Open URL on rendered links
  // when this preview belongs to an on-disk file (file-editor preview, NOT a yoagent body),
  // remember the owning file's dir so relative links resolve, and bind the in-pane link handler once.
  if (markdownPath) {
    container.dataset.mdPath = markdownPath;
    container.dataset.basePath = dirnameOf(markdownPath);
    bindScopedOnce(container, 'markdown-preview-links', scope => (
      scope.ownEvent('click', container, 'click', handleMarkdownPreviewLinkClick)
    ));
  }
  container.querySelectorAll('pre code').forEach(block => {
    applyMarkdownFenceHighlight(block);
  });
}

function markdownFenceLanguage(block) {
  const classes = Array.from(block?.classList || []);
  for (const className of classes) {
    const match = String(className || '').match(/^(?:language|lang)-(.+)$/);
    if (match) return match[1].toLowerCase();
  }
  const params = block?.parentElement?.getAttribute?.('data-params') || '';
  return String(params).trim().split(/\s+/, 1)[0].toLowerCase();
}

function applyMarkdownFenceHighlight(block) {
  if (!block) return;
  const language = markdownFenceLanguage(block);
  if (language && !Array.from(block.classList).some(className => /^(?:language|lang)-/.test(className))) {
    block.classList.add(`language-${language}`);
  }
  if (fileEditorPreviewDisplayMode === 'vanilla') return;
  if (typeof window.hljs !== 'undefined') {
    try { window.hljs.highlightElement(block); } catch (_) {}
  }
  applyMarkdownFenceFallbackHighlight(block);
}

function refreshMarkdownFenceHighlights(container) {
  container?.querySelectorAll?.('pre > code').forEach(applyMarkdownFenceHighlight);
}

function isMermaidFenceLanguage(language) {
  return ['mermaid', 'mmd', 'diagram-mermaid'].includes(String(language || '').toLowerCase());
}

function sanitizeSvgStyleText(text) {
  return String(text || '')
    .replace(/@import[^;]+;?/gi, '')
    .replace(/url\([^)]*\)/gi, '');
}

function svgUrlValueUnsafe(value) {
  const raw = String(value || '').trim();
  if (!raw || raw.startsWith('#')) return false;
  return true;
}

function svgNumberAttribute(element, name, fallback = 0) {
  const value = Number.parseFloat(String(element?.getAttribute?.(name) || ''));
  return Number.isFinite(value) ? value : fallback;
}

function svgPreviewFontFamily() {
  const root = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement) : null;
  return root?.getPropertyValue?.('--ui-font')?.trim() || 'Inter, "Segoe UI", "Noto Sans", Arial, sans-serif';
}

function svgPreviewColor(name, fallback) {
  const root = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement) : null;
  return root?.getPropertyValue?.(name)?.trim() || fallback;
}

function svgParseColor(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!text || text === 'none' || text === 'transparent' || text === 'currentcolor' || text.startsWith('url(')) return null;
  const hex = text.match(/^#([0-9a-f]{3,8})$/i);
  if (hex) {
    let raw = hex[1];
    if (raw.length === 3 || raw.length === 4) raw = raw.split('').map(ch => ch + ch).join('');
    if (raw.length < 6) return null;
    return {
      r: Number.parseInt(raw.slice(0, 2), 16),
      g: Number.parseInt(raw.slice(2, 4), 16),
      b: Number.parseInt(raw.slice(4, 6), 16),
    };
  }
  const rgb = text.match(/^rgba?\(\s*([0-9.]+)[,\s]+([0-9.]+)[,\s]+([0-9.]+)/);
  if (rgb) {
    return {
      r: Number.parseFloat(rgb[1]),
      g: Number.parseFloat(rgb[2]),
      b: Number.parseFloat(rgb[3]),
    };
  }
  return null;
}

function svgColorLuminance(color) {
  const channel = value => {
    const normalized = Math.max(0, Math.min(255, value)) / 255;
    return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
  };
  return (0.2126 * channel(color.r)) + (0.7152 * channel(color.g)) + (0.0722 * channel(color.b));
}

function svgColorContrastRatio(foreground, background) {
  const fg = svgParseColor(foreground);
  const bg = svgParseColor(background);
  if (!fg || !bg) return 0;
  const a = svgColorLuminance(fg);
  const b = svgColorLuminance(bg);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

function svgColorIsDark(value) {
  const color = svgParseColor(value);
  return color ? svgColorLuminance(color) < 0.36 : true;
}

function svgStyleValue(element, property) {
  const style = String(element?.getAttribute?.('style') || '');
  const match = style.match(new RegExp(`${property}\\s*:\\s*([^;]+)`, 'i'));
  // Strip a trailing `!important` (Mermaid stamps it on every classDef fill, e.g.
  // `fill:#fef3c7 !important`); otherwise svgParseColor's anchored `^#...$` rejects the value and
  // the node reads as having no background, which flips its label to the wrong contrast color.
  return match ? match[1].replace(/\s*!important\s*$/i, '').trim() : '';
}

function svgStyleDeclarations(text) {
  const declarations = {};
  String(text || '').split(';').forEach(part => {
    const index = part.indexOf(':');
    if (index <= 0) return;
    const property = part.slice(0, index).trim().toLowerCase();
    const value = part.slice(index + 1).trim().replace(/\s*!important\s*$/i, '');
    if (property && value) declarations[property] = value;
  });
  return declarations;
}

function svgStyleRules(svg) {
  if (!svg) return [];
  if (Array.isArray(svg._yolomuxSvgStyleRules)) return svg._yolomuxSvgStyleRules;
  const rules = [];
  svg.querySelectorAll?.('style').forEach(style => {
    const text = String(style.textContent || '').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const match of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const declarations = svgStyleDeclarations(match[2]);
      match[1].split(',').map(selector => selector.trim()).filter(Boolean).forEach(selector => {
        rules.push({selector, declarations});
      });
    }
  });
  svg._yolomuxSvgStyleRules = rules;
  return rules;
}

function svgSimpleSelectorMatches(element, selector) {
  const raw = String(selector || '').trim();
  if (!raw || raw.includes(':') || raw.includes('>') || raw.includes('+') || raw.includes('~')) return false;
  const tag = raw.match(/^[a-z][a-z0-9_-]*/i)?.[0] || '';
  if (tag && tag !== '*' && String(element?.tagName || '').toLowerCase() !== tag.toLowerCase()) return false;
  for (const id of raw.matchAll(/#([a-z0-9_-]+)/gi)) {
    if (element?.id !== id[1]) return false;
  }
  for (const className of raw.matchAll(/\.([a-z0-9_-]+)/gi)) {
    if (!element?.classList?.contains(className[1])) return false;
  }
  return true;
}

function svgSelectorMatches(element, selector) {
  const parts = String(selector || '').trim().split(/\s+/).filter(Boolean);
  if (!parts.length || !element) return false;
  let node = element;
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    let match = null;
    for (let cursor = node; cursor; cursor = cursor.parentElement) {
      if (svgSimpleSelectorMatches(cursor, parts[index])) {
        match = cursor;
        break;
      }
    }
    if (!match) return false;
    node = match.parentElement;
  }
  return true;
}

function svgCssStyleValue(element, property) {
  const svg = element?.closest?.('svg');
  if (!svg) return '';
  const key = String(property || '').toLowerCase();
  let value = '';
  for (const rule of svgStyleRules(svg)) {
    if (rule.declarations[key] && svgSelectorMatches(element, rule.selector)) value = rule.declarations[key];
  }
  return value;
}

function svgPaintColor(element, property = 'fill') {
  return svgStyleValue(element, property) || svgCssStyleValue(element, property) || element?.getAttribute?.(property) || '';
}

function svgSetStyleProperty(element, property, value) {
  if (!element?.setAttribute) return;
  const existing = String(element.getAttribute('style') || '')
    .split(';')
    .map(part => part.trim())
    .filter(part => part && !part.toLowerCase().startsWith(`${property.toLowerCase()}:`));
  existing.push(`${property}:${value}`);
  element.setAttribute('style', `${existing.join(';')};`);
}

function svgNodeBackgroundFill(node) {
  // The background behind a node label is the node's own background SHAPE fill. Scan the node's
  // shapes (skipping label-internal shapes) and return the first with a parseable fill; the first
  // shape in document order can be a fill-less <path> or a label-internal shape, so picking it
  // blindly is wrong. A node with no parseable shape fill is transparent over the preview surface.
  const shapes = node?.querySelectorAll?.('rect, polygon, path, circle, ellipse') || [];
  for (const shape of Array.from(shapes)) {
    if (shape.closest?.('.label')) continue;
    const fill = svgPaintColor(shape, 'fill');
    if (svgParseColor(fill)) return fill;
  }
  return '';
}

function svgReadableLabelColor(element) {
  // Labels that sit on the preview surface (subgraph/edge labels, or a node with no/transparent fill)
  // use the surface foreground, which follows the preview's Bright/Dark/Vanilla mode (dark text on a
  // white Bright surface, light text on a dark surface).
  const surfaceFg = mermaidSurfacePalette().fg;
  const node = element?.closest?.('.node');
  if (!node) return surfaceFg;
  // A node with a fill: pick the text color that contrasts with the node's actual background SHAPE
  // fill (not the nearest ancestor fill, which for a label is the text color and gives dark-on-dark).
  const bg = svgNodeBackgroundFill(node);
  if (!bg || !svgParseColor(bg)) return surfaceFg;
  return svgColorContrastRatio(MERMAID_DARK_INK, bg) >= svgColorContrastRatio(MERMAID_LIGHT_INK, bg) ? MERMAID_DARK_INK : MERMAID_LIGHT_INK;
}

function svgReadableEdgeColor() {
  return mermaidSurfacePalette().fg;
}

function svgReadableLabelStyle(color = svgPreviewColor('--text', MERMAID_LIGHT_INK), size = 15, weight = 400) {
  return `font-family:${svgPreviewFontFamily()};font-size:${size}px;font-weight:${weight};fill:${color};stroke:none;stroke-width:0;`;
}

function svgApplyReadableLabelStyle(node, color) {
  if (!node?.setAttribute) return;
  svgSetStyleProperty(node, 'font-family', svgPreviewFontFamily());
  svgSetStyleProperty(node, 'font-weight', '400');
  svgSetStyleProperty(node, 'fill', color);
  svgSetStyleProperty(node, 'stroke', 'none');
  svgSetStyleProperty(node, 'stroke-width', '0');
}

function svgForeignObjectTextNode(element) {
  const clone = element.cloneNode(true);
  clone.querySelectorAll?.('script,style,iframe,object,embed,link,meta').forEach(node => node.remove());
  const text = String(clone.textContent || '').replace(/\s+/g, ' ').trim();
  if (!text) return null;
  const doc = element.ownerDocument || document;
  const node = doc.createElementNS('http://www.w3.org/2000/svg', 'text');
  const x = svgNumberAttribute(element, 'x');
  const y = svgNumberAttribute(element, 'y');
  const width = svgNumberAttribute(element, 'width');
  const height = svgNumberAttribute(element, 'height');
  node.textContent = text;
  node.setAttribute('x', String(x + (width / 2)));
  node.setAttribute('y', String(y + (height / 2)));
  node.setAttribute('text-anchor', 'middle');
  node.setAttribute('dominant-baseline', 'middle');
  node.setAttribute('class', 'mermaid-node-label');
  node.setAttribute('style', svgReadableLabelStyle(svgReadableLabelColor(element)));
  return node;
}

function styleStandaloneSvgText(svg) {
  const fontFamily = svgPreviewFontFamily();
  const edgeColor = svgReadableEdgeColor();
  svg.querySelectorAll?.('.edgePaths path, .edgePath path, .flowchart-link, path.flowchart-link, path.messageLine0, path.messageLine1, line.messageLine0, line.messageLine1, marker path, marker polygon').forEach(edge => {
    svgSetStyleProperty(edge, 'stroke', edgeColor);
    svgSetStyleProperty(edge, 'stroke-opacity', '0.95');
    const strokeWidth = Number.parseFloat(svgStyleValue(edge, 'stroke-width') || svgCssStyleValue(edge, 'stroke-width') || edge.getAttribute?.('stroke-width') || '');
    if (!Number.isFinite(strokeWidth) || strokeWidth < 2) svgSetStyleProperty(edge, 'stroke-width', '2px');
    if (String(edge.tagName || '').toLowerCase() !== 'path' || edge.closest?.('marker')) svgSetStyleProperty(edge, 'fill', edgeColor);
  });
  svg.querySelectorAll?.('.node rect, .node polygon, .node path').forEach(shape => {
    const fill = svgPaintColor(shape, 'fill');
    const stroke = svgPaintColor(shape, 'stroke');
    if (svgColorIsDark(fill) && (!stroke || svgColorIsDark(stroke))) svgSetStyleProperty(shape, 'stroke', edgeColor);
  });
  svg.querySelectorAll?.('text').forEach(text => {
    if (!text.getAttribute('font-family')) text.setAttribute('font-family', fontFamily);
    const labelColor = svgReadableLabelColor(text);
    svgApplyReadableLabelStyle(text, labelColor);
    text.querySelectorAll?.('tspan').forEach(tspan => svgApplyReadableLabelStyle(tspan, labelColor));
  });
}

const STANDALONE_SVG_BLOCKED_TAGS = Object.freeze([
  'script',
  'foreignobject',
  'iframe',
  'object',
  'embed',
  'audio',
  'video',
  'canvas',
  'link',
  'meta',
]);
const STANDALONE_SVG_BLOCKED_TAG_SET = new Set(STANDALONE_SVG_BLOCKED_TAGS);
const STANDALONE_SVG_BLOCKED_TAG_PATTERN = STANDALONE_SVG_BLOCKED_TAGS.map(escapeRegExpLiteral).join('|');
const STANDALONE_SVG_BLOCKED_PAIRED_TAG_RE = new RegExp(`<\\s*(?:${STANDALONE_SVG_BLOCKED_TAG_PATTERN})\\b[\\s\\S]*?<\\s*\\/\\s*(?:${STANDALONE_SVG_BLOCKED_TAG_PATTERN})\\s*>`, 'gi');
const STANDALONE_SVG_BLOCKED_SINGLE_TAG_RE = new RegExp(`<\\s*(?:${STANDALONE_SVG_BLOCKED_TAG_PATTERN})\\b[^>]*\\/?\\s*>`, 'gi');

function sanitizeStandaloneSvgNode(root) {
  const elementNode = globalThis.Node?.ELEMENT_NODE || 1;
  for (const child of Array.from(root?.childNodes || [])) {
    if (child.nodeType !== elementNode) {
      if (child.nodeType === (globalThis.Node?.COMMENT_NODE || 8)) child.remove();
      continue;
    }
    const tagName = String(child.tagName || '').toLowerCase();
    if (tagName === 'foreignobject') {
      const textNode = svgForeignObjectTextNode(child);
      if (textNode) child.replaceWith(textNode);
      else child.remove();
      continue;
    }
    if (STANDALONE_SVG_BLOCKED_TAG_SET.has(tagName)) {
      child.remove();
      continue;
    }
    if (tagName === 'style') {
      child.textContent = sanitizeSvgStyleText(child.textContent);
    }
    for (const attr of Array.from(child.attributes || [])) {
      const name = String(attr?.name || '').toLowerCase();
      if (!name || name.startsWith('on')) {
        child.removeAttribute(attr.name);
        continue;
      }
      if (name === 'style') {
        const sanitized = sanitizeSvgStyleText(attr.value);
        if (sanitized) child.setAttribute(attr.name, sanitized);
        else child.removeAttribute(attr.name);
        continue;
      }
      if ((name === 'href' || name === 'xlink:href' || name === 'src') && svgUrlValueUnsafe(attr.value)) {
        child.removeAttribute(attr.name);
      }
    }
    sanitizeStandaloneSvgNode(child);
  }
}

function sanitizeStandaloneSvgString(svgText) {
  return String(svgText || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(STANDALONE_SVG_BLOCKED_PAIRED_TAG_RE, '')
    .replace(STANDALONE_SVG_BLOCKED_SINGLE_TAG_RE, '')
    .replace(/\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    .replace(/\s+(?:href|xlink:href|src)\s*=\s*(?:"(?!#)[^"]*"|'(?!#)[^']*'|(?![#'"])[^\s>]+)/gi, '')
    .replace(/@import[^;]+;?/gi, '')
    .replace(/url\([^)]*\)/gi, '')
    .replace(/https?:\/\/[^"')\s<>]+/gi, '');
}

function sanitizeStandaloneSvg(svgText) {
  const template = document.createElement('template');
  if (!template.content) return sanitizeStandaloneSvgString(svgText);
  template.innerHTML = String(svgText || '');
  sanitizeStandaloneSvgNode(template.content);
  const svg = template.content?.querySelector?.('svg');
  if (svg) styleStandaloneSvgText(svg);
  return svg ? svg.outerHTML : '';
}

function svgImageUrl(svgText) {
  const svg = String(svgText || '');
  if (typeof Blob === 'function' && typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
    return URL.createObjectURL(new Blob([svg], {type: 'image/svg+xml'}));
  }
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
