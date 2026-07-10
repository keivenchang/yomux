// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Shared conversation markup and text-selection helpers for YO!agent and YO!chat.

function conversationMessageShellHtml(options = {}) {
  const roleClass = options.self === true ? 'user' : 'assistant';
  const className = ['conversation-message', roleClass, String(options.className || '')].filter(Boolean).join(' ');
  const timestamp = String(options.timestampHtml || '');
  const body = String(options.bodyHtml || '');
  const extras = String(options.extrasHtml || '');
  return `<article class="${esc(className)}"${options.attributes ? ` ${options.attributes}` : ''}>
    <div class="conversation-message-role yoagent-message-role"><span>${esc(options.author || '')}</span>${timestamp}</div>
    ${body}${extras}
  </article>`;
}

function conversationSendButtonHtml({className = '', title = '', ariaLabel = title, disabled = false} = {}) {
  return `<button type="submit" class="conversation-send conversation-send-primary ${esc(className)}"${disabled ? ' disabled' : ''} title="${esc(title)}" aria-label="${esc(ariaLabel)}"><svg class="conversation-send-icon yoagent-chat-send-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h12M12 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`;
}

function conversationComposerHtml({formClassName = '', formAttributes = '', inputHtml = '', leadingControlsHtml = '', trailingControlsHtml = '', sendHtml = ''} = {}) {
  return `<form class="conversation-composer ${esc(formClassName)}"${formAttributes ? ` ${formAttributes}` : ''}>${inputHtml}<div class="conversation-composer-controls yoagent-chat-controls">${leadingControlsHtml}<span class="conversation-composer-controls-spacer yoagent-chat-controls-spacer"></span>${trailingControlsHtml}${sendHtml}</div></form>`;
}

function conversationGraphemeBoundaries(text) {
  const value = String(text || '');
  if (typeof Intl?.Segmenter !== 'function') return Array.from({length: value.length + 1}, (_item, index) => index);
  const boundaries = [0];
  for (const segment of new Intl.Segmenter(i18nActiveLocale, {granularity: 'grapheme'}).segment(value)) {
    boundaries.push(segment.index + segment.segment.length);
  }
  return [...new Set(boundaries)].sort((a, b) => a - b);
}

function conversationClampSelectionToGraphemes(text, selectionStart, selectionEnd) {
  const value = String(text || '');
  const boundaries = conversationGraphemeBoundaries(value);
  const start = Math.max(0, Math.min(value.length, Number(selectionStart) || 0));
  const end = Math.max(start, Math.min(value.length, Number(selectionEnd) || start));
  const clampedStart = boundaries.filter(boundary => boundary <= start).at(-1) ?? 0;
  const clampedEnd = boundaries.find(boundary => boundary >= end) ?? value.length;
  return {start: clampedStart, end: clampedEnd};
}

function conversationInsertAtSelection(input, insertion) {
  if (!input) return '';
  const value = String(input.value || '');
  const selection = conversationClampSelectionToGraphemes(value, input.selectionStart, input.selectionEnd);
  const inserted = String(insertion || '');
  input.value = `${value.slice(0, selection.start)}${inserted}${value.slice(selection.end)}`;
  const caret = selection.start + inserted.length;
  input.setSelectionRange?.(caret, caret);
  const inputEvent = typeof Event === 'function' ? new Event('input', {bubbles: true}) : {type: 'input', bubbles: true};
  input.dispatchEvent?.(inputEvent);
  input.focus?.();
  return input.value;
}

function conversationAutosizeTextarea(textarea, maxHeight = Number.POSITIVE_INFINITY) {
  if (!textarea) return 0;
  // Measure content from a collapsed baseline. `auto` can preserve a grid/flex-stretched height
  // after its pane shrinks, feeding that stale height back into scrollHeight on every resize.
  textarea.style.height = '0px';
  const limit = Number.isFinite(Number(maxHeight)) ? Math.max(0, Number(maxHeight)) : Number.POSITIVE_INFINITY;
  const height = Math.min(textarea.scrollHeight, limit);
  textarea.style.height = `${height}px`;
  textarea.style.overflowY = textarea.scrollHeight > limit ? 'auto' : 'hidden';
  return height;
}
