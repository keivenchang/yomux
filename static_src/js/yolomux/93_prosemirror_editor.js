const prosemirrorLoadPromise = {value: null};
const PROSEMIRROR_SERIALIZE_DELAY_MS = 1250;

function prosemirrorFailureMessage(error) {
  const raw = userMessageText(error, String(error?.message || error || 'unknown error'));
  const detail = raw.replace(/^ProseMirror ViewEditor failed:\s*/i, '');
  return t('editor.prosemirrorFailed', {error: detail});
}

function renderProseMirrorFailure(panel, path, parts, error) {
  const pane = parts?.previewPane;
  if (!pane) return false;
  const source = fileEditorPanelState(panel)?.content || '';
  if (pane.dataset.prosemirrorState === 'error' && panel._pmFailureSource === source) return true;
  const message = prosemirrorFailureMessage(error);
  panel._pmError = message;
  panel._pmFailureSource = source;
  panel._pmRequired = true;
  renderMarkdownPreviewInto(pane, source, path, {context: fileEditorPanelMode(panel), readOnly: true, historical: true});
  const failure = document.createElement('section');
  failure.className = 'file-editor-prosemirror-error file-editor-prosemirror-watermark';
  failure.setAttribute('role', 'alert');
  const title = document.createElement('strong');
  title.textContent = t('editor.prosemirrorErrorTitle');
  const detail = document.createElement('p');
  detail.textContent = message;
  const help = document.createElement('p');
  help.textContent = t('editor.prosemirrorErrorHelp');
  failure.append(title, detail, help);
  pane.prepend(failure);
  pane.hidden = false;
  pane.dataset.prosemirrorState = 'error';
  setFileEditorPanelStatus(panel, message, 'error');
  statusErr(esc(message));
  return true;
}

function renderProseMirrorLoading(parts) {
  const pane = parts?.previewPane;
  if (!pane || pane.querySelector('.file-editor-prosemirror-loading')) return;
  disposeMarkdownPreviewEditing(pane);
  cleanupStandardPreviewStrategy(pane);
  pane.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'file-editor-prosemirror-loading';
  loading.textContent = t('editor.prosemirrorLoading');
  pane.append(loading);
  pane.hidden = false;
  pane.dataset.prosemirrorState = 'loading';
}

function loadProseMirrorApi() {
  if (window.YOLOmuxProseMirror) return Promise.resolve(window.YOLOmuxProseMirror);
  if (prosemirrorLoadPromise.value) return prosemirrorLoadPromise.value;
  const asset = bootstrap.proseMirrorAssetUrl || '/static/prosemirror.js';
  const promise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = asset;
    script.onload = () => window.YOLOmuxProseMirror ? resolve(window.YOLOmuxProseMirror) : reject(new Error('ProseMirror bundle did not initialize'));
    script.onerror = () => reject(new Error(`Unable to load ${asset}`));
    document.head.appendChild(script);
  });
  prosemirrorLoadPromise.value = promise;
  promise.catch(() => {
    if (prosemirrorLoadPromise.value === promise) prosemirrorLoadPromise.value = null;
  });
  return promise;
}

function prosemirrorMarkdownSchema(api) {
  let nodes = api.addListNodes(api.defaultMarkdownParser.schema.spec.nodes, 'paragraph block*', 'block');
  nodes = nodes.addBefore('blockquote', 'details', {
    group: 'block',
    content: 'block+',
    defining: true,
    attrs: {summary: {default: ''}},
    parseDOM: [{tag: 'details', getAttrs(dom) {
      return {summary: dom.querySelector(':scope > summary')?.textContent || ''};
    }}],
    toDOM() { return ['details', 0]; },
  });
  nodes = nodes.addBefore('blockquote', 'table', {
    group: 'block',
    content: 'table_row+',
    isolating: true,
    parseDOM: [{tag: 'table'}],
    toDOM() { return ['table', ['tbody', 0]]; },
  }).addBefore('blockquote', 'table_row', {
    content: '(table_header | table_cell)+',
    parseDOM: [{tag: 'tr'}],
    toDOM() { return ['tr', 0]; },
  }).addBefore('blockquote', 'table_header', {
    content: 'inline*',
    attrs: {align: {default: null}},
    parseDOM: [{tag: 'th', getAttrs(dom) { return {align: dom.style.textAlign || null}; }}],
    toDOM(node) { return ['th', node.attrs.align ? {style: `text-align:${node.attrs.align}`} : {}, 0]; },
  }).addBefore('blockquote', 'table_cell', {
    content: 'inline*',
    attrs: {align: {default: null}},
    parseDOM: [{tag: 'td', getAttrs(dom) { return {align: dom.style.textAlign || null}; }}],
    toDOM(node) { return ['td', node.attrs.align ? {style: `text-align:${node.attrs.align}`} : {}, 0]; },
  });
  nodes = nodes.addBefore('blockquote', 'markdown_comment_block', {
    group: 'block',
    atom: true,
    attrs: {source: {default: '<!---->'}},
    toDOM() { return ['div', {'data-markdown-comment': 'block', hidden: 'hidden'}]; },
  }).addBefore('hard_break', 'markdown_comment_inline', {
    inline: true,
    group: 'inline',
    atom: true,
    attrs: {source: {default: '<!---->'}},
    toDOM() { return ['span', {'data-markdown-comment': 'inline', hidden: 'hidden'}]; },
  }).addBefore('blockquote', 'markdown_literal_block', {
    group: 'block',
    atom: true,
    attrs: {source: {default: ''}},
    toDOM(node) { return ['div', {'data-markdown-literal': 'block'}, node.attrs.source]; },
  }).addBefore('hard_break', 'markdown_literal_inline', {
    inline: true,
    group: 'inline',
    atom: true,
    attrs: {source: {default: ''}},
    toDOM(node) { return ['span', {'data-markdown-literal': 'inline'}, node.attrs.source]; },
  });
  nodes = nodes.addBefore('hard_break', 'soft_break', {
    inline: true,
    group: 'inline',
    selectable: false,
    parseDOM: [{tag: 'span[data-markdown-soft-break]'}],
    toDOM() { return ['span', {'data-markdown-soft-break': 'true'}, ' ']; },
  });
  const marks = api.defaultMarkdownParser.schema.spec.marks.addToEnd('strike', {
    parseDOM: [{tag: 's'}, {tag: 'del'}, {style: 'text-decoration: line-through'}],
    toDOM() { return ['s', 0]; },
  }).addToEnd('underline', {
    parseDOM: [{tag: 'u'}, {style: 'text-decoration: underline'}],
    toDOM() { return ['u', 0]; },
  }).addToEnd('highlight', {
    parseDOM: [{tag: 'mark'}],
    toDOM() { return ['mark', 0]; },
  }).addToEnd('kbd', {
    parseDOM: [{tag: 'kbd'}],
    toDOM() { return ['kbd', 0]; },
  }).addToEnd('superscript', {
    parseDOM: [{tag: 'sup'}],
    toDOM() { return ['sup', 0]; },
  });
  return new api.Schema({
    nodes,
    marks,
  });
}

function prosemirrorMarkdownParser(api, schema) {
  const parser = api.defaultMarkdownParser;
  const tokenizer = new api.MarkdownIt({html: true, breaks: false});
  tokenizer.enable('strikethrough');
  const markdownItParse = tokenizer.parse.bind(tokenizer);
  tokenizer.parse = (source, environment) => {
    environment = environment || {};
    const htmlElement = html => {
      const template = document.createElement('template');
      template.innerHTML = String(html || '').trim();
      return template.content.firstElementChild;
    };
    const imageAttrs = html => {
      const image = htmlElement(html);
      if (!image || image.tagName !== 'IMG' || !image.getAttribute('src')) return null;
      return {
        src: image.getAttribute('src'),
        alt: image.getAttribute('alt'),
        title: image.getAttribute('title'),
      };
    };
    const ignoredCommentLines = new Set();
    const ignoredCommentRanges = [];
    const normalizeHtmlTokens = (tokens, sourceLine = 0) => {
      const normalized = [];
      let listItemDepth = 0;
      for (const token of tokens || []) {
        const html = String(token.content || '').trim();
        const tokenLine = Array.isArray(token.map) ? Number(token.map[0]) + 1 : sourceLine;
        if (token.type === 'list_item_open') listItemDepth += 1;
        if (token.type === 'inline' && listItemDepth > 0) {
          const firstText = token.children?.find(child => child.type === 'text');
          const taskMarker = String(firstText?.content || token.content || '').match(/^\[[ xX]\]\s+/);
          if (taskMarker) {
            token.content = token.content.slice(taskMarker[0].length);
            let remaining = taskMarker[0].length;
            for (const child of token.children || []) {
              if (remaining <= 0 || child.type !== 'text') continue;
              const removed = Math.min(remaining, child.content.length);
              child.content = child.content.slice(removed);
              remaining -= removed;
            }
            token.children = (token.children || []).filter(child => child.type !== 'text' || child.content);
          }
        }
        if ((token.type === 'html_inline' || token.type === 'html_block') && /^<!--[\s\S]*-->$/.test(html)) {
          // Keep comments in the document as hidden atoms so ViewEditor never paints them but a
          // later edit/save can serialize their exact contents instead of deleting user metadata.
          if (Array.isArray(token.map)) {
            ignoredCommentRanges.push({from: Number(token.map[0]) + 1, to: Number(token.map[1] ?? token.map[0] + 1)});
            for (let line = Number(token.map[0]); line < Number(token.map[1] ?? token.map[0] + 1); line += 1) {
              ignoredCommentLines.add(line);
            }
          }
          token.type = token.type === 'html_block' ? 'markdown_comment_block' : 'markdown_comment_inline';
          token.meta = {source: String(token.content || '').replace(/\n$/, '')};
          normalized.push(token);
          continue;
        }
        if (token.type === 'html_inline') {
          const open = html.match(/^<(u|mark|kbd|sup)(?:\s[^>]*)?>$/i)?.[1]?.toLowerCase();
          const close = html.match(/^<\/(u|mark|kbd|sup)\s*>$/i)?.[1]?.toLowerCase();
          const markName = {u: 'underline', mark: 'highlight', kbd: 'kbd', sup: 'superscript'};
          if (open) token.type = `${markName[open]}_open`;
          else if (close) token.type = `${markName[close]}_close`;
          else if (/^<br\s*\/?>$/i.test(html)) token.type = 'html_break';
          else {
            const attrs = imageAttrs(html);
            if (attrs) {
              token.type = 'html_image';
              token.meta = attrs;
            }
          }
        } else if (token.type === 'html_block') {
          const details = html.match(/^<details\s*>\s*<summary>([\s\S]*?)<\/summary>\s*$/i);
          if (details) {
            token.type = 'details_open';
            token.meta = {summary: details[1].replace(/<[^>]+>/g, '').trim()};
          } else if (/^<\/details>\s*$/i.test(html)) {
            token.type = 'details_close';
          } else {
            const attrs = imageAttrs(html);
            if (attrs) {
              token.type = 'html_image';
              token.meta = attrs;
            }
          }
        }
        if ((token.type === 'html_inline' || token.type === 'html_block') && html) {
          // Preserve unrecognized XML-like tags as ordinary visible text.
          token.type = token.type === 'html_block' ? 'markdown_literal_block' : 'markdown_literal_inline';
          token.meta = {source: html};
        }
        if (token.children) token.children = normalizeHtmlTokens(token.children, tokenLine);
        normalized.push(token);
        if (token.type === 'list_item_close') listItemDepth = Math.max(0, listItemDepth - 1);
      }
      return normalized;
    };
    const parseSource = String(source || '').replace(
      /<details>\s*\n\s*<summary>([\s\S]*?)<\/summary>/gi,
      '<details><summary>$1</summary>',
    );
    const tokens = normalizeHtmlTokens(markdownItParse(parseSource, environment));
    // markdown-it collapses extra blank lines. Preserve the additional empty paragraphs that
    // ViewEditor creates with consecutive Enter presses so a later source sync cannot erase them.
    const spacedTokens = [];
    let previousBlockEnd = null;
    let previousBlockType = '';
    for (const token of tokens) {
        const topLevelBlock = token.level === 0 && (token.nesting === 1 || ['code_block', 'fence', 'hr', 'markdown_comment_block', 'markdown_literal_block'].includes(token.type));
      const blockStart = topLevelBlock && Array.isArray(token.map) ? token.map[0] : null;
      if (blockStart !== null && previousBlockEnd !== null) {
        let visibleGapLines = 0;
        for (let line = previousBlockEnd; line < blockStart; line += 1) {
          if (!ignoredCommentLines.has(line)) visibleGapLines += 1;
        }
        const emptyParagraphs = ['hr', 'markdown_comment_block', 'markdown_literal_block'].includes(token.type)
          || ['hr', 'markdown_comment_block'].includes(previousBlockType)
          ? 0
          : Math.max(0, visibleGapLines - 1);
        for (let index = 0; index < emptyParagraphs; index += 1) {
          spacedTokens.push(
            {type: 'paragraph_open', tag: 'p', nesting: 1, level: 0, map: [blockStart, blockStart], block: true, children: null, content: ''},
            {type: 'inline', tag: '', nesting: 0, level: 1, map: [blockStart, blockStart], block: true, children: [], content: ''},
            {type: 'paragraph_close', tag: 'p', nesting: -1, level: 0, map: null, block: true, children: null, content: ''},
          );
        }
      }
      spacedTokens.push(token);
      if (topLevelBlock && Array.isArray(token.map)) {
        previousBlockEnd = token.map[1];
        previousBlockType = token.type;
      }
    }
    const sourceLines = String(source).split('\n');
    while (sourceLines.length && ignoredCommentLines.has(sourceLines.length - 1)) sourceLines.pop();
    const trailingNewlines = (sourceLines.join('\n').match(/\n+$/) || [''])[0].length;
    const trailingEmptyParagraphs = Math.max(0, trailingNewlines - 1);
    for (let index = 0; index < trailingEmptyParagraphs; index += 1) {
      spacedTokens.push(
        {type: 'paragraph_open', tag: 'p', nesting: 1, level: 0, map: null, block: true, children: null, content: ''},
        {type: 'inline', tag: '', nesting: 0, level: 1, map: null, block: true, children: [], content: ''},
        {type: 'paragraph_close', tag: 'p', nesting: -1, level: 0, map: null, block: true, children: null, content: ''},
      );
    }
    environment.yolomuxTopLevelSourceLines = spacedTokens
      .filter(token => token.level === 0 && (token.nesting === 1 || ['code_block', 'fence', 'hr', 'markdown_comment_block', 'markdown_literal_block'].includes(token.type)))
      .map(token => Array.isArray(token.map) ? Number(token.map[0]) + 1 : null);
    environment.yolomuxIgnoredCommentRanges = ignoredCommentRanges;
    return spacedTokens;
  };
  const tokens = {
    ...parser.tokens,
    softbreak: {node: 'soft_break'},
    html_break: {node: 'hard_break'},
    html_image: {node: 'image', getAttrs: token => token.meta},
    markdown_comment_block: {node: 'markdown_comment_block', getAttrs: token => token.meta},
    markdown_comment_inline: {node: 'markdown_comment_inline', getAttrs: token => token.meta},
    markdown_literal_block: {node: 'markdown_literal_block', getAttrs: token => token.meta},
    markdown_literal_inline: {node: 'markdown_literal_inline', getAttrs: token => token.meta},
    details: {block: 'details', getAttrs: token => token.meta},
    table: {block: 'table'},
    thead: {ignore: true},
    tbody: {ignore: true},
    tr: {block: 'table_row'},
    th: {block: 'table_header', getAttrs: token => ({align: token.attrGet('style')?.match(/text-align\s*:\s*(left|center|right)/i)?.[1]?.toLowerCase() || null})},
    td: {block: 'table_cell', getAttrs: token => ({align: token.attrGet('style')?.match(/text-align\s*:\s*(left|center|right)/i)?.[1]?.toLowerCase() || null})},
    s: {mark: 'strike'},
    underline: {mark: 'underline'},
    highlight: {mark: 'highlight'},
    kbd: {mark: 'kbd'},
    superscript: {mark: 'superscript'},
  };
  return new api.MarkdownParser(schema, tokenizer, tokens);
}

const PROSEMIRROR_SAFE_HTML_TAGS = new Set(['u', 'mark', 'kbd', 'sup', 'br', 'img', 'details', 'summary']);
const PROSEMIRROR_KNOWN_HTML_TAGS = new Set([
  'a', 'abbr', 'address', 'article', 'aside', 'audio', 'b', 'bdi', 'bdo', 'blockquote', 'body', 'button',
  'caption', 'cite', 'code', 'col', 'colgroup', 'data', 'datalist', 'dd', 'del', 'div', 'dl', 'dt', 'em',
  'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header',
  'hgroup', 'html', 'i', 'iframe', 'input', 'ins', 'label', 'legend', 'li', 'link', 'main', 'map', 'menu',
  'meta', 'meter', 'nav', 'noscript', 'object', 'ol', 'optgroup', 'option', 'output', 'p', 'picture', 'pre',
  'progress', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'script', 'section', 'select', 'small', 'source', 'span',
  'strong', 'style', 'sub', 'summary', 'table', 'tbody', 'td', 'template', 'textarea', 'tfoot', 'th', 'thead',
  'time', 'title', 'tr', 'track', 'u', 'ul', 'var', 'video',
]);

function prosemirrorUnsupportedHtmlSource(source) {
  // Do not let an incomplete tag-shaped fragment in prose consume later Markdown lines while
  // searching for a `>`; only a tag completed on the same line is actual HTML here.
  const tags = String(source || '').matchAll(/<\/?([a-z][a-z0-9:-]*)\b[^>\r\n]*>/gi);
  for (const match of tags) {
    const name = match[1].toLowerCase();
    if (PROSEMIRROR_KNOWN_HTML_TAGS.has(name) && !PROSEMIRROR_SAFE_HTML_TAGS.has(name)) return match[0];
  }
  return null;
}

function prosemirrorMarkdownSerializer(api) {
  return new api.MarkdownSerializer({
    ...api.defaultMarkdownSerializer.nodes,
    paragraph(state, node) {
      if (node.content.size) {
        state.renderInline(node);
      } else {
        // A Markdown paragraph separator is two newlines. Empty ViewEditor paragraphs need one
        // additional source newline each or Markdown would collapse them on the next parse.
        state.flushClose(1);
        state.write('\n');
      }
      state.closeBlock(node);
    },
    soft_break(state) { state.write('\n'); },
    // Keep an explicit end-of-line break valid Markdown. HTML is disabled in the parser, so
    // serializing `<br>` would round-trip as literal text in the next ViewEditor refresh.
    hard_break(state) { state.write('\\\n'); },
    markdown_comment_block(state, node) {
      state.write(node.attrs.source || '<!---->');
      state.closeBlock(node);
    },
    markdown_comment_inline(state, node) { state.write(node.attrs.source || '<!---->'); },
    markdown_literal_block(state, node) { state.write(node.attrs.source || ''); state.closeBlock(node); },
    markdown_literal_inline(state, node) { state.write(node.attrs.source || ''); },
    details(state, node) {
      state.write(`<details>\n<summary>${node.attrs.summary}</summary>\n\n`);
      state.renderContent(node);
      state.write('\n</details>');
      state.closeBlock(node);
    },
    table(state, node) {
      const inline = cell => {
        const saved = {out: state.out, delim: state.delim, closed: state.closed, atBlockStart: state.atBlockStart, inTightList: state.inTightList};
        state.out = '';
        state.delim = '';
        state.closed = null;
        state.atBlockStart = true;
        state.renderInline(cell);
        const result = state.out.trim();
        Object.assign(state, saved);
        return result.replace(/\|/g, '\\|');
      };
      const cells = row => {
        const result = [];
        row.forEach(cell => result.push({text: inline(cell), align: cell.attrs.align || ''}));
        return result;
      };
      const alignment = align => align === 'left' ? ':---' : align === 'right' ? '---:' : align === 'center' ? ':---:' : '---';
      const rows = [];
      node.forEach(row => rows.push(cells(row)));
      if (!rows.length) return;
      state.write(`| ${rows[0].map(cell => cell.text).join(' | ')} |`);
      state.ensureNewLine();
      state.write(`| ${rows[0].map(cell => alignment(cell.align)).join(' | ')} |`);
      for (const row of rows.slice(1)) {
        state.ensureNewLine();
        state.write(`| ${row.map(cell => cell.text).join(' | ')} |`);
      }
      state.closeBlock(node);
    },
  }, {
    ...api.defaultMarkdownSerializer.marks,
    strike: {open: '~~', close: '~~', mixable: true},
    underline: {open: '<u>', close: '</u>', mixable: true},
    highlight: {open: '<mark>', close: '</mark>', mixable: true},
    kbd: {open: '<kbd>', close: '</kbd>', mixable: true},
    superscript: {open: '<sup>', close: '</sup>', mixable: true},
  });
}

function prosemirrorDetailsNodeView(node) {
  const dom = document.createElement('details');
  dom.open = true;
  const summary = document.createElement('summary');
  summary.textContent = node.attrs.summary;
  const contentDOM = document.createElement('div');
  dom.append(summary, contentDOM);
  return {dom, contentDOM};
}

function prosemirrorImageNodeView(node, panel, markdownPath) {
  const image = document.createElement('img');
  const original = String(node.attrs.src || '');
  image.className = 'markdown-preview-image prosemirror-image';
  image.alt = node.attrs.alt || '';
  if (node.attrs.title) image.title = node.attrs.title;
  image.dataset.originalSrc = original;
  image.loading = 'eager';
  image.decoding = 'async';
  image.style.display = 'inline-block';
  image.style.verticalAlign = 'text-bottom';
  const target = markdownPreviewImageTarget(original, markdownPath);
  if (!target) {
    image.src = original;
    panel._pmMediaPromises ||= [];
    panel._pmMediaPromises.push(waitForImageDecode(image));
  } else if (target.external) {
    image.src = target.src;
    panel._pmMediaPromises ||= [];
    panel._pmMediaPromises.push(waitForImageDecode(image));
  } else {
    image.dataset.resolvedPath = target.path;
    panel._pmMediaPromises ||= [];
    panel._pmMediaPromises.push(new Promise(resolve => requestAnimationFrame(() => (
      prosemirrorPreviewImageSource(image, markdownPath).then(resolve)
    ))));
    image.addEventListener('error', () => {
      image.classList.add('prosemirror-image-error');
      image.title = t('preview.markdown.imageUnavailable', {path: target.path});
    }, {once: true});
  }
  return {dom: image, destroy() { releaseRawFileMediaSource(image); }};
}

function prosemirrorCodeBlockNodeView(node, panel, markdownPath) {
  const language = String(node.attrs.params || '').trim().split(/\s+/, 1)[0].toLowerCase();
  if (!isMermaidFenceLanguage(language)) {
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    if (language) {
      pre.dataset.params = language;
      code.className = `language-${language}`;
    }
    pre.appendChild(code);
    let observer = null;
    const highlight = () => {
      if (!code.isConnected || !code.textContent) return;
      observer?.disconnect();
      observer = null;
      applyMarkdownFenceHighlight(code);
    };
    if (typeof MutationObserver === 'function') {
      observer = new MutationObserver(highlight);
      observer.observe(code, {childList: true, subtree: true, characterData: true});
    }
    requestAnimationFrame(highlight);
    return {dom: pre, contentDOM: code, destroy() { observer?.disconnect(); }};
  }
  const host = document.createElement('div');
  host.className = 'mermaid-preview-host';
  panel._pmMediaPromises ||= [];
  panel._pmMediaPromises.push(renderMermaidSourceInto(host, node.textContent || '', {
    full: false,
    path: markdownPath,
    zoomKey: 'mermaid',
  }));
  return {dom: host};
}

function hydrateProseMirrorExternalImages(panel, path) {
  const promises = [];
  for (const image of Array.from(panel?._pmView?.dom?.querySelectorAll?.('img.prosemirror-image') || [])) {
    if (image.naturalWidth > 0) continue;
    const target = markdownPreviewImageTarget(image.dataset.originalSrc || '', path);
    if (!target?.external) continue;
    image.src = target.src;
    promises.push(waitForImageDecode(image));
  }
  if (promises.length) panel._pmMediaPromises = [...(panel._pmMediaPromises || []), ...promises];
}

function prosemirrorTaskListItemNodeView(task) {
  const dom = document.createElement('li');
  const contentDOM = document.createElement('div');
  if (task) {
    const input = document.createElement('input');
    input.className = `${MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS} markdown-task-checkbox`;
    input.type = 'checkbox';
    input.checked = task.checked;
    input.dataset.sourceLine = String(task.line);
    dom.appendChild(input);
  }
  dom.appendChild(contentDOM);
  return {dom, contentDOM};
}

function applyProseMirrorPreviewDisplayMode(container) {
  const vanilla = fileEditorPreviewDisplayMode === 'vanilla';
  container.classList.toggle('vanilla-preview-body', vanilla);
  container.classList.toggle('editor-preview-vanilla', vanilla);
  container.style.setProperty('background-color', vanilla ? '#ffffff' : '');
  container.style.setProperty('color', vanilla ? '#111827' : '');
  container.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(node => {
    node.style.setProperty('color', vanilla ? '#111827' : '', 'important');
  });
}

function bindProseMirrorTaskCheckboxes(container, source, markdownPath) {
  if (!container) return;
  if (markdownPath) container.dataset.mdPath = markdownPath;
  const tasks = String(source || '').split('\n').map((line, index) => {
    const match = line.match(/^\s*(?:[-+*]|\d+[.)])\s+\[([ xX])\]/);
    return match ? {line: index + 1, checked: match[1].toLowerCase() === 'x'} : null;
  }).filter(Boolean);
  const items = Array.from(container.querySelectorAll('li')).filter(item => item.parentElement?.tagName === 'UL' || item.parentElement?.tagName === 'OL');
  for (const [index, item] of items.entries()) {
    const task = tasks[index];
    if (!task) continue;
    const input = Array.from(item.children).find(child => child.tagName === 'INPUT' && child.type === 'checkbox') || document.createElement('input');
    input.className = `${MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS} markdown-task-checkbox`;
    input.type = 'checkbox';
    input.checked = task.checked;
    input.dataset.sourceLine = String(task.line);
    input.disabled = readOnlyMode || container._markdownReadOnly === true;
    input.dataset.sourceLine = String(task.line || index + 1);
    if (!input.parentElement) item.insertBefore(input, item.firstChild);
  }
  if (markdownPath) {
    bindMarkdownTaskCheckboxes(container, source, markdownPath);
    container.addEventListener('change', event => {
      const input = event.target?.closest?.('input.markdown-task-checkbox[data-source-line]');
      if (!input) return;
      updateMarkdownTaskFromPreview(container, input);
    }, {once: true});
  }
}

function applyProseMirrorCodeBlockLanguages(view, source = '') {
  if (!view?.dom) return;
  const sourceLanguages = String(source).split('\n')
    .filter(line => line.startsWith('```'))
    .map(line => line.slice(3).trim().split(/\s+/, 1)[0].toLowerCase());
  Array.from(view.dom.querySelectorAll('pre')).forEach((pre, index) => {
    const code = pre?.querySelector('code');
    const language = String(pre.dataset.params || code?.dataset.params || '').trim().split(/\s+/, 1)[0].toLowerCase()
      || sourceLanguages[index] || '';
    if (code && language) {
      pre.dataset.params = language;
      code.classList.add(`language-${language}`);
    }
  });
  refreshMarkdownFenceHighlights(view.dom);
}

function scheduleProseMirrorCodeBlockHighlight(view, source) {
  const apply = () => applyProseMirrorCodeBlockLanguages(view, source);
  requestAnimationFrame(apply);
  setTimeout(apply, 0);
  setTimeout(apply, 50);
  setTimeout(apply, 250);
}

function normalizeProseMirrorEndBreakSource(text) {
  return String(text || '').replace(/\\\n(?=\S)/g, '\n');
}

function normalizeLegacyBreakMarkup(text) {
  // Files produced by the earlier adapter may contain literal `<br>` markers. Treat only the
  // standalone marker as a Markdown hard break; ordinary prose containing that text stays text.
  return String(text || '').replace(/(^|\n)([^\n]*?)<br\s*\/?>\s*(?=\n|$)/gi, '$1$2\\\n');
}

function destroyProseMirrorPanel(panel) {
  const view = panel?._pmView;
  if (!view) return;
  panel._pmContextMenuGeneration = Number(panel._pmContextMenuGeneration || 0) + 1;
  panel._pmContextMenuDispose?.();
  delete panel._pmContextMenuDispose;
  flushProseMirrorSource(panel, panel._pmPath);
  releaseRawFileMediaSources(view.dom);
  view.destroy();
  delete panel._pmView;
  delete panel._pmPath;
  delete panel._pmSource;
  delete panel._pmPlugins;
  delete panel._pmPreviewPane;
}

function installProseMirrorContextMenuGuard() {
  if (document.__yolomuxProseMirrorContextMenuGuard) return;
  const guard = event => {
    const anchor = event.target?.closest?.('[data-prosemirror-editor] a[href]');
    if (anchor) event.preventDefault();
  };
  document.addEventListener('contextmenu', guard, true);
  document.__yolomuxProseMirrorContextMenuGuard = guard;
}

function prosemirrorSelectionForDocument(api, doc, anchor, head) {
  const max = doc.content.size;
  if (max <= 0) return null;
  const from = Math.max(0, Math.min(Number(anchor) || 0, max));
  const to = Math.max(0, Math.min(Number(head) || 0, max));
  if (from === to) return api.Selection.near(doc.resolve(from));
  return api.TextSelection.between(doc.resolve(from), doc.resolve(to));
}

function setProseMirrorSelection(view, from, to) {
  const api = window.YOLOmuxProseMirror;
  const selection = prosemirrorSelectionForDocument(api, view.state.doc, from, to);
  if (!selection) return false;
  view.dispatch(view.state.tr.setSelection(selection));
  return true;
}

function syncProseMirrorPanelSource(panel, path, state, options = {}) {
  if (!panel?._pmView || panel._pmPath !== path || !state) return false;
  restoreProseMirrorPanelDom(panel, {previewPane: panel._pmPreviewPane});
  applyProseMirrorPreviewDisplayMode(panel._pmView.dom);
  const next = normalizeLegacyBreakMarkup(state.content || '');
  const textEditorCommit = options.sourceSurface === 'text-editor';
  if (textEditorCommit) {
    if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
    panel._pmSerializeTimer = null;
    panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
    panel._pmSource = null;
  }
  if (panel._pmSource === next) return true;
  if (!textEditorCommit && panel._pmSerializeTimer && panel._pmSource === null) return true;
  if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
  panel._pmSerializeTimer = null;
  panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
  try {
    const doc = panel._pmParser.parse(next);
    const selection = panel._pmView.state.selection;
    const api = window.YOLOmuxProseMirror;
    const options = {doc, plugins: panel._pmPlugins || []};
    const nextSelection = prosemirrorSelectionForDocument(api, doc, selection.anchor, selection.head);
    if (nextSelection) options.selection = nextSelection;
    panel._pmView.updateState(api.EditorState.create(options));
    bindProseMirrorTaskCheckboxes(panel._pmView.dom, next, path);
    applyProseMirrorCodeBlockLanguages(panel._pmView);
    panel._pmSource = next;
    return true;
  } catch (error) {
    renderProseMirrorFailure(panel, path, editorPanelParts(panel), error);
    return false;
  }
}

function restoreProseMirrorPanelDom(panel, parts) {
  const view = panel?._pmView;
  const pane = parts?.previewPane;
  if (!view || !pane || view.dom.isConnected) return Boolean(view?.dom?.isConnected);
  cleanupStandardPreviewStrategy(pane);
  disposeMarkdownPreviewEditing(pane);
  pane._previewRendererId = null;
  pane.replaceChildren(view.dom);
  clearProseMirrorFallback(parts);
  pane.dataset.prosemirrorState = 'ready';
  return true;
}

function prosemirrorSupportedSource(path, state) {
  return state?.kind === 'text' && previewKindForPath(path) === 'markdown' && state.historical !== true;
}

function clearProseMirrorFallback(parts) {
  const fallback = parts?.previewPane?.querySelector('.file-editor-preview-fallback');
  fallback?.remove();
}

function serializeProseMirrorSource(panel) {
  if (!panel?._pmSerializer || !panel._pmView) return null;
  return panel._pmSerializer.serialize(panel._pmView.state.doc);
}

function commitProseMirrorSource(panel, path, options = {}) {
  if (!panel?._pmView || panel._pmPath !== path) return false;
  const next = normalizeProseMirrorEndBreakSource(serializeProseMirrorSource(panel));
  if (next === null) return false;
  const state = fileEditorPanelState(panel);
  if (!state || state.historical === true) return false;
  if (next === state.content) {
    panel._pmSource = next;
    return true;
  }
  panel._pmSource = next;
  handleFileEditorContentChanged(panel, path, next, {
    syntax: false,
    previewEdit: true,
    sourceSurface: 'view-editor',
    skipPreviewPanel: panel.querySelector('.file-editor-preview-pane-panel'),
    deferAutosave: options.deferAutosave === true,
  });
  syncCodeMirrorToCanonicalPanels(path, next, panel, 'view-editor');
  return true;
}

function syncCodeMirrorToCanonicalPanels(path, content, sourcePanel = null, sourceSurface = '') {
  for (const linked of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(linked)?.historical === true) continue;
    if (linked._cmView && !(linked === sourcePanel && sourceSurface === 'text-editor')) {
      syncCodeMirrorDocument(linked._cmView, content, {path});
    }
    if (linked._pmView && !(linked === sourcePanel && sourceSurface === 'view-editor')) {
      syncProseMirrorPanelSource(linked, path, fileEditorPanelState(linked), {sourceSurface});
    }
  }
}

function updateProseMirrorSource(panel, path) {
  if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
  const generation = Number(panel._pmSerializeGeneration || 0) + 1;
  panel._pmSerializeGeneration = generation;
  panel._pmSerializeTimer = setTimeout(() => {
    if (generation !== panel._pmSerializeGeneration || panel._pmPath !== path || !panel._pmView?.dom?.isConnected) return;
    panel._pmSerializeTimer = null;
    commitProseMirrorSource(panel, path, {deferAutosave: true});
  }, PROSEMIRROR_SERIALIZE_DELAY_MS);
}

function scheduleProseMirrorAutosave(panel, path) {
  if (panel._pmAutosaveTimer) clearTimeout(panel._pmAutosaveTimer);
  panel._pmAutosaveTimer = setTimeout(() => {
    panel._pmAutosaveTimer = null;
    if (panel._pmPath !== path || !panel._pmView?.dom?.isConnected) return;
    const state = fileEditorPanelState(panel);
    if (state?.dirty) scheduleFileAutosave(path);
  }, PROSEMIRROR_SERIALIZE_DELAY_MS);
}

function flushProseMirrorSource(panel, path) {
  if (!panel?._pmView || panel._pmPath !== path) return false;
  if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
  panel._pmSerializeTimer = null;
  panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
  if (panel._pmAutosaveTimer) clearTimeout(panel._pmAutosaveTimer);
  panel._pmAutosaveTimer = null;
  return commitProseMirrorSource(panel, path);
}

function insertProseMirrorHardBreak(api, schema) {
  return (state, dispatch) => {
    const {$from, $to} = state.selection;
    if (!$from.sameParent($to) || !$from.parent.isTextblock) return false;
    const type = schema.nodes.hard_break;
    if (!type) return false;
    if (dispatch) {
      dispatch(state.tr.replaceSelectionWith(type.create()).scrollIntoView());
    }
    return true;
  };
}

function insertProseMirrorSoftBreak(api, schema) {
  return (state, dispatch) => {
    const {$from, $to} = state.selection;
    if (!$from.sameParent($to) || !$from.parent.isTextblock || !schema.nodes.soft_break) return false;
    if ($from.parentOffset >= $from.parent.content.size) return false;
    if (dispatch) dispatch(state.tr.replaceSelectionWith(schema.nodes.soft_break.create()).scrollIntoView());
    return true;
  };
}

function insertProseMirrorEnter(api, schema) {
  return (state, dispatch) => {
    const {$from} = state.selection;
    if ($from.parentOffset >= $from.parent.content.size) {
      if (dispatch) {
        const transaction = state.tr.split($from.pos).scrollIntoView();
        dispatch(transaction);
      }
      return true;
    }
    return insertProseMirrorSoftBreak(api, schema)(state, dispatch);
  };
}

function clearLinkedCodeMirrorSelection(panel, path) {
  for (const linked of fileEditorPanelsForPath(path)) {
    const view = linked._cmView;
    const main = view?.state?.selection?.main;
    if (!view || !main || main.empty) continue;
    view.dispatch({selection: {anchor: main.head}});
  }
}

function prosemirrorSelectionContext(view) {
  const {from, to} = view.state.selection;
  const domNode = view.domAtPos(from)?.node;
  const domElement = domNode?.nodeType === 1 ? domNode : domNode?.parentElement;
  return {
    selectedText: from === to ? '' : view.state.doc.textBetween(from, to, '\n'),
    from,
    to,
    block: domElement?.closest?.('p,h1,h2,h3,h4,h5,h6') || null,
  };
}

function prosemirrorSelectionAtClientPoint(view, event) {
  const link = event.target?.closest?.('a[href]');
  if (link && view.dom.contains(link)) {
    const textNode = link.firstChild;
    const position = textNode ? view.posAtDOM(textNode, 0) : NaN;
    const href = link.getAttribute('href') || '';
    if (Number.isFinite(position)) {
      const linkMark = view.state.doc.resolve(position + 1).marks().find(
        mark => mark.type.name === 'link' && mark.attrs.href === href,
      );
      if (linkMark) {
        let from = position;
        let to = position;
        view.state.doc.nodesBetween(position, position + Math.max(1, link.textContent.length + 1), (node, nodePosition) => {
          if (!node.isText || !node.marks.some(mark => mark.eq(linkMark))) return;
          from = Math.min(from, nodePosition);
          to = Math.max(to, nodePosition + node.nodeSize);
        });
        if (from < to) {
          setProseMirrorSelection(view, from, to);
          return prosemirrorSelectionContext(view);
        }
      }
    }
  }
  const selection = view.state.selection;
  if (!selection.empty) return prosemirrorSelectionContext(view);
  const point = view.posAtCoords({left: event.clientX, top: event.clientY});
  if (!point) return prosemirrorSelectionContext(view);
  const {$from} = view.state.doc.resolve(point.pos);
  const text = $from.parent.textContent || '';
  if (!text) return prosemirrorSelectionContext(view);
  const offset = Math.max(0, Math.min($from.parentOffset, text.length));
  let start = offset;
  let end = offset;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const base = $from.start();
  setProseMirrorSelection(view, base + start, base + end);
  return prosemirrorSelectionContext(view);
}

function prosemirrorSelectionHasMark(view, mark) {
  if (!mark || view.state.selection.empty) return false;
  let sawText = false;
  let covered = true;
  view.state.doc.nodesBetween(view.state.selection.from, view.state.selection.to, node => {
    if (!node.isText || !node.nodeSize) return;
    sawText = true;
    if (!mark.isInSet(node.marks)) covered = false;
  });
  return sawText && covered;
}

function applyProseMirrorFormat(api, view, schema, command) {
  const marks = schema.marks;
  const nodes = schema.nodes;
  const run = command === 'bold' ? api.toggleMark(marks.strong)
    : command === 'italic' ? api.toggleMark(marks.em)
    : command === 'code' ? api.toggleMark(marks.code)
      : command === 'strike' ? api.toggleMark(marks.strike)
        : command === 'underline' ? api.toggleMark(marks.underline)
      : command === 'bullet' ? api.wrapInList(nodes.bullet_list)
          : command === 'normal' ? api.setBlockType(nodes.paragraph)
            : /^h[1-5]$/.test(command) ? api.setBlockType(nodes.heading, {level: Number(command.slice(1))})
              : null;
  return run ? run(view.state, view.dispatch, view) : false;
}

async function markdownLinkUrlDialog(view, currentUrl, title) {
  const action = await showFileEditorDecisionDialog({
    title,
    bodyHtml: `<label class="markdown-link-url-field">${esc(title)}<input type="url" data-markdown-link-url-input value="${esc(currentUrl)}" /></label>`,
    actions: [
      {id: 'cancel', label: t('common.cancel')},
      {id: 'save', label: t('common.save')},
    ],
    className: 'markdown-link-url-dialog',
    focusSelector: '[data-markdown-link-url-input]',
    onMount: backdrop => {
      const input = backdrop.querySelector('[data-markdown-link-url-input]');
      input?.focus?.();
      input?.select?.();
    },
    onInput: value => { view._markdownLinkUrlDialogValue = value; },
  });
  const nextUrl = view._markdownLinkUrlDialogValue || currentUrl;
  delete view._markdownLinkUrlDialogValue;
  return action === 'save' ? nextUrl : null;
}

async function modifyMarkdownLinkUrl(view, link) {
  const currentUrl = link?.getAttribute?.('href') || '';
  const nextUrl = await markdownLinkUrlDialog(view, currentUrl, t('contextmenu.modifyUrl'));
  if (nextUrl === null) return false;
  if (nextUrl === currentUrl) return false;
  const textNode = link?.firstChild;
  const position = textNode ? view.posAtDOM(textNode, 0) : NaN;
  if (!Number.isFinite(position)) return false;
  const linkMark = view.state.doc.resolve(position + 1).marks().find(
    mark => mark.type.name === 'link' && mark.attrs.href === currentUrl,
  );
  if (!linkMark) return false;
  let from = position;
  let to = position;
  view.state.doc.nodesBetween(position, position + Math.max(1, link.textContent.length + 1), (node, nodePosition) => {
    if (!node.isText || !node.marks.some(mark => mark.eq(linkMark))) return;
    from = Math.min(from, nodePosition);
    to = Math.max(to, nodePosition + node.nodeSize);
  });
  if (from === to) return false;
  view.dispatch(view.state.tr.removeMark(from, to, linkMark.type).addMark(
    from,
    to,
    linkMark.type.create({...linkMark.attrs, href: nextUrl}),
  ));
  return true;
}

function removeMarkdownLinkUrl(view, link) {
  const currentUrl = link?.getAttribute?.('href') || '';
  const textNode = link?.firstChild;
  const position = textNode ? view.posAtDOM(textNode, 0) : NaN;
  if (!Number.isFinite(position)) return false;
  const linkMark = view.state.doc.resolve(position + 1).marks().find(
    mark => mark.type.name === 'link' && mark.attrs.href === currentUrl,
  );
  if (!linkMark) return false;
  let from = position;
  let to = position;
  view.state.doc.nodesBetween(position, position + Math.max(1, link.textContent.length + 1), (node, nodePosition) => {
    if (!node.isText || !node.marks.some(mark => mark.eq(linkMark))) return;
    from = Math.min(from, nodePosition);
    to = Math.max(to, nodePosition + node.nodeSize);
  });
  if (from === to) return false;
  view.dispatch(view.state.tr.removeMark(from, to, linkMark.type));
  return true;
}

async function addMarkdownLinkUrl(view) {
  const {from, to} = view.state.selection;
  if (from === to) return false;
  const label = t('contextmenu.addUrl');
  const nextUrl = await markdownLinkUrlDialog(view, '', label === 'contextmenu.addUrl' ? 'Add URL' : label);
  if (!nextUrl) return false;
  const link = view.state.schema.marks.link;
  view.dispatch(view.state.tr.addMark(from, to, link.create({href: nextUrl})));
  return true;
}

function installProseMirrorInteractions(panel, path, view, schema, api) {
  view.dom.addEventListener('focus', () => clearLinkedCodeMirrorSelection(panel, path));
  view.dom.addEventListener('blur', () => flushProseMirrorSource(panel, path));
  const onContextMenu = event => {
    const image = event.target?.closest?.('img');
    if (image && view.dom.contains(image)) {
      event.preventDefault();
      event.stopPropagation();
      showImageContextMenu(image, event.clientX, event.clientY);
      return;
    }
    const link = event.target?.closest?.('a[href]');
    const context = prosemirrorSelectionAtClientPoint(view, event);
    if (!context.block) return;
    event.preventDefault();
    event.stopPropagation();
    markdownFormattingContextMenu(event, context, {
      href: link && view.dom.contains(link) ? link.href : '',
      modifyUrl: link && view.dom.contains(link) ? () => modifyMarkdownLinkUrl(view, link) : null,
      removeUrl: link && view.dom.contains(link) ? () => removeMarkdownLinkUrl(view, link) : null,
      addUrl: !link && context.selectedText ? () => addMarkdownLinkUrl(view) : null,
      applyCommand: command => applyProseMirrorFormat(api, view, schema, command),
      isActive: command => {
        const mark = command === 'bold' ? schema.marks.strong
          : command === 'italic' ? schema.marks.em
            : command === 'code' ? schema.marks.code
              : command === 'strike' ? schema.marks.strike
                : command === 'underline' ? schema.marks.underline
                  : null;
        return prosemirrorSelectionHasMark(view, mark);
      },
    });
  };
  const previewPane = panel._pmPreviewPane;
  const onPanelContextMenu = event => {
    const link = event.target?.closest?.('[data-prosemirror-editor] a[href]');
    if (!link || !previewPane?.contains(link) || event.defaultPrevented) return;
    onContextMenu(event);
  };
  view.dom.addEventListener('contextmenu', onContextMenu);
  previewPane?.addEventListener('contextmenu', onPanelContextMenu, true);
  panel._pmContextMenuDispose = () => {
    view.dom.removeEventListener('contextmenu', onContextMenu);
    previewPane?.removeEventListener('contextmenu', onPanelContextMenu, true);
  };
}

function createProseMirrorPanel(panel, item, path, state, parts, api) {
  if (!parts?.previewPane) return false;
  panel._pmMediaPromises = [];
  installProseMirrorContextMenuGuard();
  const schema = prosemirrorMarkdownSchema(api);
  const parser = prosemirrorMarkdownParser(api, schema);
  const serializer = prosemirrorMarkdownSerializer(api);
  const parseEnvironment = {};
  const unsupportedHtml = prosemirrorUnsupportedHtmlSource(state.content || '');
  if (unsupportedHtml) {
    throw new Error(`unsupported HTML tag ${unsupportedHtml}`);
  }
  const doc = parser.parse(state.content || '', parseEnvironment);
  const container = document.createElement('div');
  container.className = 'prosemirror-editor markdown-body';
  applyProseMirrorPreviewDisplayMode(container);
  container.setAttribute('data-prosemirror-editor', 'true');
  const plugins = [
    api.keymap({
      'Mod-s': () => {
        flushProseMirrorSource(panel, path);
        void saveFileEditor(path, panel);
        return true;
      },
      'Shift-Enter': insertProseMirrorHardBreak(api, schema),
      Enter: insertProseMirrorEnter(api, schema),
    }),
    api.history(),
    api.keymap({'Mod-z': api.undo, 'Shift-Mod-z': api.redo, 'Mod-y': api.redo}),
    api.keymap(api.baseKeymap),
  ];
  const editorState = api.EditorState.create({doc, plugins});
  if (panel.dataset.filePath !== path || !['preview', 'split'].includes(editorViewModeFor(path, item))) {
    return false;
  }
  if (panel._pmView) {
    panel._pmContextMenuDispose?.();
    panel._pmView.destroy();
  }
  const taskEntries = String(state.content || '').split('\n').map((line, index) => {
    const match = line.match(/^\s*(?:[-+*]|\d+[.)])\s+\[([ xX])\]/);
    return match ? {line: index + 1, checked: match[1].toLowerCase() === 'x'} : null;
  }).filter(Boolean);
  let taskNodeViewIndex = 0;
  cleanupStandardPreviewStrategy(parts.previewPane);
  disposeMarkdownPreviewEditing(parts.previewPane);
  parts.previewPane._previewRendererId = null;
  parts.previewPane.replaceChildren(container);
  clearProseMirrorFallback(parts);
  const view = new api.EditorView(container, {
    state: editorState,
    nodeViews: {
      details: prosemirrorDetailsNodeView,
      image: node => prosemirrorImageNodeView(node, panel, path),
      list_item: () => {
        const index = taskNodeViewIndex++;
        const task = taskEntries[index] || null;
        return prosemirrorTaskListItemNodeView(task);
      },
    },
    dispatchTransaction(transaction) {
      const nextState = view.state.apply(transaction);
      view.updateState(nextState);
      if (transaction.docChanged) {
        panel._pmSource = null;
        updateProseMirrorSource(panel, path);
        scheduleProseMirrorAutosave(panel, path);
      }
    },
  });
  panel._pmView = view;
  hydrateProseMirrorExternalImages(panel, path);
  applyProseMirrorPreviewDisplayMode(view.dom);
  bindMarkdownPreviewEditing(parts.previewPane, state.content || '', path);
  parts.previewPane._previewAsync = Promise.all(panel._pmMediaPromises || []);
  applyProseMirrorCodeBlockLanguages(view, state.content || '');
  scheduleProseMirrorCodeBlockHighlight(view, state.content || '');
  requestAnimationFrame(() => requestAnimationFrame(() => refreshMarkdownFenceHighlights(view.dom)));
  const attachSourceLines = () => Array.from(view.dom.children).forEach((element, index) => {
    const sourceLine = Number(parseEnvironment.yolomuxTopLevelSourceLines?.[index]);
    if (Number.isFinite(sourceLine) && sourceLine > 0) element.dataset.sourceLine = String(sourceLine);
  });
  attachSourceLines();
  requestAnimationFrame(attachSourceLines);
  const sourceLines = String(state.content || '').split('\n');
  const sourceHeadings = sourceLines.map((line, index) => {
    const match = line.match(/^\s*#{1,6}\s+(.+?)\s*$/);
    return match ? {line: index + 1, text: match[1].trim()} : null;
  }).filter(Boolean);
  const attachHeadingSourceLines = () => {
    let headingSearchFrom = 0;
    for (const heading of Array.from(view.dom.querySelectorAll('h1, h2, h3, h4, h5, h6'))) {
      const headingText = String(heading.textContent || '').trim();
      if (!headingText) continue;
      const sourceHeading = sourceHeadings.find(candidate => candidate.line > headingSearchFrom && candidate.text === headingText);
      if (!sourceHeading) continue;
      heading.dataset.sourceLine = String(sourceHeading.line);
      headingSearchFrom = sourceHeading.line - 1;
    }
  };
  attachHeadingSourceLines();
  setTimeout(attachHeadingSourceLines, 0);
  requestAnimationFrame(attachHeadingSourceLines);
  container._prosemirrorView = view;
  panel._pmPreviewPane = parts.previewPane;
  panel._pmPath = path;
  panel._pmSchema = schema;
  panel._pmParser = parser;
  panel._pmSerializer = serializer;
  panel._pmPlugins = plugins;
  panel._pmSource = normalizeLegacyBreakMarkup(state.content || '');
  panel._pmSourceLines = parseEnvironment.yolomuxTopLevelSourceLines || [];
  panel._pmIgnoredCommentRanges = parseEnvironment.yolomuxIgnoredCommentRanges || [];
  bindProseMirrorTaskCheckboxes(view.dom, state.content || '', path);
  attachSourceLines();
  attachHeadingSourceLines();
  setTimeout(() => bindProseMirrorTaskCheckboxes(view.dom, panel._pmSource, path), 0);
  requestAnimationFrame(() => {
    attachSourceLines();
    attachHeadingSourceLines();
  });
  delete panel._pmError;
  delete panel._pmFailureSource;
  parts.previewPane.dataset.prosemirrorState = 'ready';
  installProseMirrorInteractions(panel, path, view, schema, api);
  return true;
}

async function ensureProseMirrorPanel(panel, item, path, state, parts) {
  if (!prosemirrorSupportedSource(path, state)) return false;
  if (panel._pmView && panel._pmPath === path) {
    restoreProseMirrorPanelDom(panel, parts);
    return syncProseMirrorPanelSource(panel, path, state);
  }
  if (panel._pmEnsurePromise) return panel._pmEnsurePromise;
  destroyProseMirrorPanel(panel);
  const promise = (async () => {
    try {
      const api = await loadProseMirrorApi();
      if (panel.dataset.filePath !== path || !['preview', 'split'].includes(editorViewModeFor(path, item))) return false;
      try {
        const ready = createProseMirrorPanel(panel, item, path, state, parts, api);
        if (ready) {
          delete panel._pmUnsupportedSource;
          delete panel._pmError;
        }
        return ready;
      } catch (error) {
        renderProseMirrorFailure(panel, path, parts, error);
        return false;
      }
    } catch (error) {
      renderProseMirrorFailure(panel, path, parts, error);
      return false;
    }
  })();
  panel._pmEnsurePromise = promise;
  promise.finally(() => {
    if (panel._pmEnsurePromise === promise) delete panel._pmEnsurePromise;
  });
  return promise;
}

function renderProseMirrorPreviewMode(panel, item, path, state, parts) {
  if (!prosemirrorSupportedSource(path, state)) return false;
  if (parts.previewPane) parts.previewPane.hidden = false;
  panel._pmRequired = true;
  const ensureGeneration = Number(panel._pmEnsureGeneration || 0) + 1;
  panel._pmEnsureGeneration = ensureGeneration;
  if (!panel._pmView) {
    renderProseMirrorLoading(parts);
  }
  if (panel._pmError) {
    renderProseMirrorFailure(panel, path, parts, panel._pmError);
    return true;
  }
  const ensurePromise = ensureProseMirrorPanel(panel, item, path, state, parts);
  parts.previewPane._previewAsync = ensurePromise;
  void ensurePromise.then(ready => {
    if (ensureGeneration !== panel._pmEnsureGeneration) return;
    if (!ready && panel.dataset.filePath === path) {
      renderProseMirrorFailure(panel, path, parts, panel._pmError || t('editor.prosemirrorDidNotInitialize'));
    }
  });
  return true;
}

function prosemirrorViewEditorHealth(panel) {
  const container = panel?.querySelector?.('[data-editor-surface="view-editor"]');
  return {
    connected: Boolean(panel?._pmView?.dom?.isConnected),
    roots: container?.querySelectorAll?.('.ProseMirror').length || 0,
    text: panel?._pmView?.state?.doc?.textContent || '',
    error: panel?._pmError || '',
  };
}
