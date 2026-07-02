// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Markdown preview parsing, sanitizing, source anchors, and Mermaid/SVG helpers split from 95_codemirror_editor.js.

function markdownTextWithSourceAnchors(text) {
  return String(text || '');
}

function applyMarkdownSourceLines(container, source) {
  const lines = String(source || '').split('\n');
  let searchFrom = 0;
  const blocks = Array.from(container.querySelectorAll('h1,h2,h3,h4,h5,h6,p,blockquote,pre,ul,ol,table,hr'));
  for (const block of blocks) {
    const text = String(block.textContent || '').trim();
    let lineIndex = -1;
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
      if (text && trimmed.includes(text.slice(0, Math.min(text.length, 40)))) {
        lineIndex = index;
        break;
      }
    }
    if (lineIndex >= 0) {
      block.dataset.sourceLine = String(lineIndex + 1);
      const anchor = document.createElement('span');
      anchor.className = 'markdown-source-anchor';
      anchor.dataset.sourceLine = String(lineIndex + 1);
      block.appendChild(anchor);
      searchFrom = lineIndex + 1;
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
    htmlLabels: true,
    flowchart: {
      htmlLabels: true,
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

function safeDecodeMarkdownUrlPath(value) {
  try {
    return decodeURIComponent(String(value || ''));
  } catch (_) {
    return String(value || '');
  }
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
  const resolved = joinAndNormalize(dirnameOf(markdownPath), safeDecodeMarkdownUrlPath(rawPath));
  return {src: rawFileUrl(resolved), path: resolved, external: false};
}

function markdownImageFallbackNode(path, label = '') {
  const node = document.createElement('span');
  node.className = 'markdown-image-error';
  const text = document.createElement('span');
  text.textContent = label || t('preview.markdown.imageUnavailable', {path});
  node.appendChild(text);
  if (path) {
    const open = document.createElement('a');
    open.href = rawFileUrl(path);
    open.target = '_blank';
    open.rel = 'noopener noreferrer';
    open.textContent = t('common.open');
    const download = document.createElement('a');
    download.href = rawFileDownloadUrl(path);
    download.textContent = t('common.download');
    node.append(document.createTextNode(' '), open, document.createTextNode(' · '), download);
  }
  return node;
}

function rewriteMarkdownPreviewImages(root, markdownPath) {
  if (!root || !markdownPath) return;
  for (const img of Array.from(root.querySelectorAll?.('img[src]') || [])) {
    const original = img.getAttribute('src') || '';
    const target = markdownPreviewImageTarget(original, markdownPath);
    if (!target) continue;
    img.classList.add('markdown-preview-image');
    img.dataset.originalSrc = original;
    if (target.path) img.dataset.resolvedPath = target.path;
    img.setAttribute('src', target.src);
    if (!img.getAttribute('alt') && target.path) img.setAttribute('alt', basenameOf(target.path));
    img.addEventListener('error', () => {
      img.replaceWith(markdownImageFallbackNode(target.path, t('preview.markdown.imageUnavailable', {path: target.path || original})));
    }, {once: true});
  }
}

const MARKDOWN_TASK_LINE_RE = /^(\s*(?:[-+*]|\d+[.)])\s+\[)([ xX])(\]\s*)/;

function markdownTaskLineEntries(text) {
  return String(text || '').split('\n')
    .map((line, index) => {
      const match = line.match(MARKDOWN_TASK_LINE_RE);
      return match ? {line: index + 1, checked: match[2].toLowerCase() === 'x'} : null;
    })
    .filter(Boolean);
}

function markdownTextWithTaskLineToggled(text, sourceLine, checked) {
  const lines = String(text || '').split('\n');
  const index = Math.max(0, Math.floor(Number(sourceLine) || 1) - 1);
  if (index >= lines.length || !MARKDOWN_TASK_LINE_RE.test(lines[index])) return null;
  lines[index] = lines[index].replace(MARKDOWN_TASK_LINE_RE, (_, prefix, _marker, suffix) => `${prefix}${checked ? 'x' : ' '}${suffix}`);
  return lines.join('\n');
}

function updateMarkdownTaskFromPreview(container, input) {
  const path = container?.dataset?.mdPath || '';
  const sourceLine = Number(input?.dataset?.sourceLine || 0);
  const state = openFiles.get(path);
  if (readOnlyMode || !path || !sourceLine || !state || state.kind !== 'text') return false;
  const next = markdownTextWithTaskLineToggled(state.content, sourceLine, input.checked === true);
  if (next === null || next === state.content) return false;
  const sourcePanel = container.closest?.('.file-editor-panel') || fileEditorPanelsForPath(path)[0] || null;
  handleFileEditorContentChanged(sourcePanel, path, next, {syntax: false});
  for (const panel of fileEditorPanelsForPath(path)) {
    if (panel?._cmView) syncCodeMirrorDocument(panel._cmView, next, {path});
  }
  return true;
}

function bindMarkdownTaskCheckboxes(container, text, markdownPath) {
  const tasks = markdownTaskLineEntries(text);
  const checkboxes = Array.from(container.querySelectorAll('input[type="checkbox"]'));
  checkboxes.forEach((input, index) => {
    const task = tasks[index];
    if (!task) return;
    input.dataset.sourceLine = String(task.line);
    input.classList.add('markdown-task-checkbox');
    input.checked = task.checked;
    if (markdownPath && !readOnlyMode) {
      input.disabled = false;
      input.removeAttribute('disabled');
      input.setAttribute('aria-label', t('editor.toggleTaskLine', {line: task.line}));
    }
  });
  if (markdownPath && !container.dataset.mdTaskBound) {
    container.dataset.mdTaskBound = '1';
    container.addEventListener('change', event => {
      const input = event.target?.closest?.('input[type="checkbox"].markdown-task-checkbox[data-source-line]');
      if (!input || !container.contains(input)) return;
      event.preventDefault();
      event.stopPropagation();
      const updated = updateMarkdownTaskFromPreview(container, input);
      if (!updated) input.checked = !input.checked;
    });
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
        items.push(`<li class="task-list-item"><input type="checkbox"${checked} disabled> ${markdownInlineHtml(item[2])}</li>`);
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
    return window.marked.parse(markdownTextWithSourceAnchors(text), {gfm: true, breaks: true});
  }
  return fallbackMarkdownToHtml(markdownTextWithSourceAnchors(text));
}

function renderMarkdownPreviewInto(container, text, markdownPath, options = {}) {
  container._previewAsync = null;
  const html = markdownPreviewHtml(text);
  const frag = sanitizeMarkdownPreviewHtml(html);
  trimMarkdownCodeBlockEdgeNewlines(frag);
  applyMarkdownHtmlBackgroundClasses(frag);
  applyMarkdownAlertClasses(frag);
  linkifyBareUrls(frag);
  rewriteMarkdownPreviewImages(frag, markdownPath);
  container.replaceChildren(frag);
  applyMarkdownSourceLines(container, text);
  container._previewAsync = renderMarkdownMermaidBlocks(container, markdownPath, {context: options.context || ''});
  bindMarkdownTaskCheckboxes(container, text, markdownPath);
  installLinkContextMenu(container);   // right-click Copy URL / Open URL on rendered links
  // when this preview belongs to an on-disk file (file-editor preview, NOT a yoagent body),
  // remember the owning file's dir so relative links resolve, and bind the in-pane link handler once.
  if (markdownPath) {
    container.dataset.mdPath = markdownPath;
    container.dataset.basePath = dirnameOf(markdownPath);
    if (!container.dataset.mdLinkBound) {
      container.dataset.mdLinkBound = '1';
      container.addEventListener('click', handleMarkdownPreviewLinkClick);
    }
  }
  if (fileEditorPreviewDisplayMode !== 'vanilla') {
    container.querySelectorAll('pre code').forEach(block => {
      if (typeof window.hljs !== 'undefined') {
        try { window.hljs.highlightElement(block); } catch (_) {}
      }
      applyMarkdownFenceFallbackHighlight(block);
    });
  }
}

function markdownFenceLanguage(block) {
  const classes = Array.from(block?.classList || []);
  for (const className of classes) {
    const match = String(className || '').match(/^(?:language|lang)-(.+)$/);
    if (match) return match[1].toLowerCase();
  }
  return '';
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

function sanitizeStandaloneSvgNode(root) {
  const elementNode = globalThis.Node?.ELEMENT_NODE || 1;
  const blocked = new Set(['script', 'foreignobject', 'iframe', 'object', 'embed', 'audio', 'video', 'canvas', 'link', 'meta']);
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
    if (blocked.has(tagName)) {
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
    .replace(/<\s*(?:script|foreignObject|iframe|object|embed|audio|video|canvas|link|meta)\b[\s\S]*?<\s*\/\s*(?:script|foreignObject|iframe|object|embed|audio|video|canvas|link|meta)\s*>/gi, '')
    .replace(/<\s*(?:script|foreignObject|iframe|object|embed|audio|video|canvas|link|meta)\b[^>]*\/?\s*>/gi, '')
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
