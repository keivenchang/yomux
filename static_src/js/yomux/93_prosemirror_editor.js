const prosemirrorLoadPromise = {value: null};
const PROSEMIRROR_SERIALIZE_DELAY_MS = 1250;
const PROSEMIRROR_EXPLICIT_SPACE = '&#32;';

// ProseMirror owns every descendant of EditorView.dom. After mount, visual metadata must flow
// through schema DOM output, transactions, decorations, or an isolated node view that ignores its
// own mutations. Calling Preview renderers or imperatively rewriting editable descendants can make
// ProseMirror restore the DOM forever and starve the entire page event loop.

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
  if (window.YOmuxProseMirror) return Promise.resolve(window.YOmuxProseMirror);
  if (prosemirrorLoadPromise.value) return prosemirrorLoadPromise.value;
  const asset = bootstrap.proseMirrorAssetUrl || '/static/prosemirror.js';
  const promise = loadLazyScript(asset).then(() => {
    if (!window.YOmuxProseMirror) throw new Error('ProseMirror bundle did not initialize');
    return window.YOmuxProseMirror;
  }, error => { throw new Error(`Unable to load ${asset}: ${error.message}`, {cause: error}); });
  prosemirrorLoadPromise.value = promise;
  promise.catch(() => {
    if (prosemirrorLoadPromise.value === promise) prosemirrorLoadPromise.value = null;
  });
  return promise;
}

function prosemirrorFenceLanguage(params) {
  return String(params || '').trim().split(/\s+/, 1)[0].toLowerCase();
}

function prosemirrorCodeBlockDom(node) {
  const params = String(node.attrs.params || '').trim();
  const language = prosemirrorFenceLanguage(params);
  return [
    'pre',
    params ? {'data-params': params} : {},
    ['code', language ? {class: `language-${language}`} : {}, 0],
  ];
}

function prosemirrorTexMathNodeView(node, panel, getPos) {
  const dom = document.createElement(node.isBlock ? 'div' : 'span');
  dom.setAttribute('data-tex-source', node.attrs.source);
  const math = markdownTexMathAt(node.attrs.source);
  if (math) renderTexMathSource(dom, math);
  else dom.textContent = node.attrs.source;
  const destroy = bindProseMirrorSourceSelection(panel, dom, getPos, 'Math');
  return {dom, destroy, ignoreMutation: () => true};
}

function prosemirrorMarkdownSchema(api) {
  let nodes = api.addListNodes(api.defaultMarkdownParser.schema.spec.nodes, 'paragraph block*', 'block');
  nodes = nodes.update('code_block', {
    ...nodes.get('code_block'),
    toDOM: prosemirrorCodeBlockDom,
  });
  nodes = nodes.update('list_item', {
    ...nodes.get('list_item'),
    content: '(paragraph | tex_math_block) block*',
    attrs: {
      taskLine: {default: 0},
      taskChecked: {default: null},
    },
  });
  nodes = nodes.update('bullet_list', {
    ...nodes.get('bullet_list'),
    attrs: {
      ...nodes.get('bullet_list').attrs,
      tight: {default: false},
      bullet: {default: '*'},
    },
  });
  nodes = nodes.update('ordered_list', {
    ...nodes.get('ordered_list'),
    attrs: {...nodes.get('ordered_list').attrs, tight: {default: false}, delimiter: {default: '.'}},
  });
  nodes = nodes.update('image', {
    ...nodes.get('image'),
    attrs: {
      ...nodes.get('image').attrs,
      width: {default: null},
    },
  });
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
  nodes = nodes.addBefore('blockquote', 'tex_math_block', {
    group: 'block',
    atom: true,
    attrs: {source: {default: ''}},
    toDOM(node) { return ['div', {'data-tex-source': node.attrs.source}, node.attrs.source]; },
    parseDOM: [{tag: 'div[data-tex-source]', getAttrs: dom => ({source: dom.getAttribute('data-tex-source')})}],
  });
  nodes = nodes.addBefore('hard_break', 'tex_math', {
    inline: true,
    group: 'inline',
    atom: true,
    attrs: {source: {default: ''}},
    toDOM(node) { return ['span', {'data-tex-source': node.attrs.source}, node.attrs.source]; },
    parseDOM: [{tag: 'span[data-tex-source]', getAttrs: dom => ({source: dom.getAttribute('data-tex-source')})}],
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
    // Code must serialize inside every style mark, including custom safe-HTML marks. Otherwise
    // their tags become literal code text after a partial clear and the next parse changes content.
    marks: marks.remove('code').addToEnd('code', marks.get('code')),
  });
}

function prosemirrorInlineCodeEnd(tokens, index) {
  const token = tokens[index];
  if (token.type !== 'html_inline' || !/^<code(?:\s[^>]*)?>$/i.test(token.content.trim())) return -1;
  return tokens.findIndex((candidate, candidateIndex) => candidateIndex > index &&
    candidate.type === 'html_inline' && /^<\/code\s*>$/i.test(candidate.content.trim()));
}

function prosemirrorMarkdownParser(api, schema) {
  const parser = api.defaultMarkdownParser;
  const tokenizer = new api.MarkdownIt({html: true, breaks: false});
  tokenizer.enable('strikethrough');
  tokenizer.inline.ruler.before('escape', 'tex_math', (state, silent) => {
    const math = markdownTexMathAt(state.src, state.pos);
    if (!math) return false;
    if (!silent) {
      const token = state.push('tex_math', '', 0);
      token.meta = {source: math.raw};
      token.mathOffset = state.pos;
    }
    state.pos += math.raw.length;
    return true;
  });
  tokenizer.block.ruler.before('blockquote', 'tex_math_block', (state, startLine, endLine, silent) => {
    const start = state.bMarks[startLine] + state.tShift[startLine];
    if (!/^(?:\$\$|\\\[)/.test(state.src.slice(start, start + 2))) return false;
    const source = state.getLines(startLine, endLine, state.blkIndent, false).trimStart();
    const math = markdownTexMathAt(source);
    if (!math?.display) return false;
    const end = math.raw.length;
    const lineEnd = source.indexOf('\n', end);
    if (source.slice(end, lineEnd < 0 ? source.length : lineEnd).trim()) return false;
    if (silent) return true;
    const nextLine = startLine + math.raw.split('\n').length;
    const token = state.push('tex_math_block', '', 0);
    token.block = true;
    token.map = [startLine, nextLine];
    token.meta = {source: math.raw};
    const last = state.eMarks[nextLine - 1];
    token.mathColumns = [
      start - state.src.lastIndexOf('\n', start - 1) - 1,
      state.src.slice(state.src.lastIndexOf('\n', last - 1) + 1, last).trimEnd().length,
    ];
    state.line = nextLine;
    return true;
  }, {alt: ['paragraph', 'reference', 'blockquote', 'list']});
  const markdownItParse = tokenizer.parse.bind(tokenizer);
  tokenizer.parse = (source, environment) => {
    environment = environment || {};
    const htmlElement = html => {
      const template = document.createElement('template');
      template.innerHTML = String(html || '').trim();
      return template.content.firstElementChild;
    };
    const imageAttrs = html => {
      if (!/^<img(?:\s|\/?>)/i.test(html)) return null;
      const image = htmlElement(html);
      if (!image || image.tagName !== 'IMG' || !image.getAttribute('src')) return null;
      return {
        src: image.getAttribute('src'),
        alt: image.getAttribute('alt'),
        title: image.getAttribute('title'),
        width: prosemirrorImageWidth(image.getAttribute('width')),
      };
    };
    const detailsOpenerEnds = new Map();
    const ignoredCommentLines = new Set();
    const ignoredCommentRanges = [];
    const normalizeHtmlTokens = (tokens, sourceLine = 0) => {
      const normalized = [];
      let listItemDepth = 0;
      const listItemTokens = [];
      const listTokens = [];
      let detailsDepth = 0;
      for (let index = 0; index < (tokens || []).length; index += 1) {
        const token = tokens[index];
        const html = String(token.content || '').trim();
        const tokenLine = Array.isArray(token.map) ? Number(token.map[0]) + 1 : sourceLine;
        const codeEnd = prosemirrorInlineCodeEnd(tokens, index);
        if (codeEnd > index) {
          const content = tokens.slice(index + 1, codeEnd).map(child => {
            if (child.type === 'text' || child.type === 'code_inline' || child.type === 'html_inline') return child.content;
            if (child.type === 'softbreak' || child.type === 'hardbreak') return '\n';
            return child.markup || child.content || '';
          }).join('');
          normalized.push({...token, type: 'code_inline', tag: 'code', nesting: 0, content, markup: ''});
          index = codeEnd;
          continue;
        }
        if (token.type === 'bullet_list_open' || token.type === 'ordered_list_open') listTokens.push(token);
        if (token.type === 'list_item_open') {
          listItemDepth += 1;
          listItemTokens.push(token);
        }
        if (token.type === 'inline' && listItemDepth > 0) {
          const taskMarker = String(token.content || '').match(/^\[[ xX]\](?:\s+|$)/);
          if (taskMarker) {
            const listItemToken = listItemTokens[listItemTokens.length - 1];
            listItemToken.meta = {
              ...(listItemToken.meta || {}),
              taskLine: tokenLine,
              taskChecked: taskMarker[0][1].toLowerCase() === 'x',
            };
            const listToken = listTokens[listTokens.length - 1];
            if (listToken) listToken.meta = {...(listToken.meta || {}), hasTasks: true};
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
        if (token.type === 'html_inline' || token.type === 'html_block') {
          const unsupportedHtml = prosemirrorUnsupportedHtmlSource(html);
          if (unsupportedHtml) throw new Error(`unsupported HTML tag ${unsupportedHtml}`);
        }
        if (token.type === 'html_inline') {
          const open = html.match(/^<(u|strong|em|s|mark|kbd|sup)(?:\s[^>]*)?>$/i)?.[1]?.toLowerCase();
          const close = html.match(/^<\/(u|strong|em|s|mark|kbd|sup)\s*>$/i)?.[1]?.toLowerCase();
          const markName = {u: 'underline', strong: 'strong', em: 'em', s: 's', mark: 'highlight', kbd: 'kbd', sup: 'superscript'};
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
            if (Array.isArray(token.map) && detailsOpenerEnds.has(token.map[0])) token.map[1] = detailsOpenerEnds.get(token.map[0]);
            token.meta = {summary: htmlElement(`<summary>${details[1]}</summary>`).textContent.trim()};
          } else if (/^<\/details>\s*$/i.test(html)) {
            token.type = 'details_close';
          } else {
            const attrs = imageAttrs(html);
            if (attrs) {
              // MarkdownParser's image node is inline. A standalone HTML image
              // needs a paragraph or createAndFill drops its containing block.
              normalized.push({type: 'paragraph_open', tag: 'p', nesting: 1, level: token.level + detailsDepth, map: token.map});
              token.type = 'html_image';
              token.meta = attrs;
              token.level += detailsDepth;
              normalized.push(token, {type: 'paragraph_close', tag: 'p', nesting: -1, level: token.level, map: null});
              continue;
            }
          }
        }
        if ((token.type === 'html_inline' || token.type === 'html_block') && html) {
          // Preserve unrecognized XML-like tags as ordinary visible text.
          token.type = token.type === 'html_block' ? 'markdown_literal_block' : 'markdown_literal_inline';
          token.meta = {source: html};
        }
        if (token.type === 'details_close') {
          detailsDepth = Math.max(0, detailsDepth - 1);
          token.nesting = -1;
        }
        token.level += detailsDepth;
        if (token.type === 'details_open') {
          token.nesting = 1;
          detailsDepth += 1;
        }
        if (token.type === 'inline') {
          if (token.content === PROSEMIRROR_EXPLICIT_SPACE) token.children = [];
          else if (token.children?.[0]?.content === ' ' &&
              ['<br>', '<!--'].some(tag => token.content.startsWith(PROSEMIRROR_EXPLICIT_SPACE + tag))) {
            token.children.shift();
          }
        }
        if (token.children) token.children = normalizeHtmlTokens(token.children, tokenLine);
        normalized.push(token);
        if (token.type === 'list_item_close') {
          listItemTokens.pop();
          listItemDepth = Math.max(0, listItemDepth - 1);
        }
        if (token.type === 'bullet_list_close' || token.type === 'ordered_list_close') listTokens.pop();
      }
      return normalized;
    };
    const parseSource = String(source || '').replace(
      /<details>\s*\n\s*<summary>([\s\S]*?)<\/summary>/gi,
      (match, summary, offset) => {
        const removedNewlines = (match.match(/\n/g) || []).length;
        const startLine = String(source || '').slice(0, offset).split('\n').length - 1;
        detailsOpenerEnds.set(startLine, startLine + removedNewlines + 1);
        const relocatedNewlines = removedNewlines - (summary.match(/\n/g) || []).length;
        return `<details><summary>${summary}</summary>${'\n'.repeat(relocatedNewlines)}`;
      },
    );
    const tokens = normalizeHtmlTokens(markdownItParse(parseSource, environment));
    // markdown-it collapses extra blank lines. Preserve the additional empty paragraphs that
    // ViewEditor creates with consecutive Enter presses so a later source sync cannot erase them.
    const sourceLines = String(source).split('\n');
    const spacedTokens = [];
    const spacingFrames = [{end: null, type: ''}];
    for (const token of tokens) {
      if (token.type === 'details_close') {
        spacedTokens.push(token);
        const frame = spacingFrames.length > 1 ? spacingFrames.pop() : null;
        if (frame?.opener.map && token.map) frame.opener.map = [frame.opener.map[0], token.map[1]];
        const parent = spacingFrames[spacingFrames.length - 1];
        if (Array.isArray(token.map)) parent.end = token.map[1];
        parent.type = 'details';
        continue;
      }
      const spacing = spacingFrames[spacingFrames.length - 1];
      const topLevelBlock = token.level === spacingFrames.length - 1 && (token.nesting === 1 || ['code_block', 'fence', 'hr', 'markdown_comment_block', 'markdown_literal_block', 'tex_math_block'].includes(token.type));
      const blockStart = topLevelBlock && Array.isArray(token.map) ? token.map[0] : null;
      if (blockStart !== null) {
        let visibleGapLines = 0;
        for (let line = spacing.end ?? 0; line < blockStart; line += 1) {
          if (!ignoredCommentLines.has(line)) visibleGapLines += 1;
        }
        const emptyParagraphs = ['hr', 'markdown_comment_block', 'markdown_literal_block', 'tex_math_block'].includes(token.type)
          || spacing.type === 'hr'
          ? 0
          : Math.max(0, visibleGapLines - 1);
        for (let index = 0; index < emptyParagraphs; index += 1) {
          spacedTokens.push(
            {type: 'paragraph_open', tag: 'p', nesting: 1, level: token.level, map: [blockStart, blockStart], block: true, children: null, content: ''},
            {type: 'inline', tag: '', nesting: 0, level: token.level + 1, map: [blockStart, blockStart], block: true, children: [], content: ''},
            {type: 'paragraph_close', tag: 'p', nesting: -1, level: token.level, map: null, block: true, children: null, content: ''},
          );
        }
      }
      spacedTokens.push(token);
      if (topLevelBlock && Array.isArray(token.map)) {
        spacing.end = token.map[1];
        if (['bullet_list_open', 'ordered_list_open'].includes(token.type)) {
          while (spacing.end > blockStart && !sourceLines[spacing.end - 1]?.trim()) spacing.end--;
          token.map = [blockStart, spacing.end];
        }
        spacing.type = token.type;
      }
      if (token.type === 'details_open') spacingFrames.push({end: Array.isArray(token.map) ? token.map[1] : null,
        type: 'details_open', opener: token});
    }
    while (sourceLines.length && ignoredCommentLines.has(sourceLines.length - 1)) sourceLines.pop();
    const visibleSource = sourceLines.join('\n');
    const trailingNewlines = (visibleSource.match(/\n+$/) || [''])[0].length;
    const trailingEmptyParagraphs = visibleSource.trim() ? Math.max(0, trailingNewlines - 1) : trailingNewlines;
    for (let index = 0; index < trailingEmptyParagraphs; index += 1) {
      spacedTokens.push(
        {type: 'paragraph_open', tag: 'p', nesting: 1, level: 0, map: null, block: true, children: null, content: ''},
        {type: 'inline', tag: '', nesting: 0, level: 1, map: null, block: true, children: [], content: ''},
        {type: 'paragraph_close', tag: 'p', nesting: -1, level: 0, map: null, block: true, children: null, content: ''},
      );
    }
    const blocks = spacedTokens.filter(token => token.level === 0 && (token.nesting === 1 ||
      ['code_block', 'fence', 'hr', 'markdown_comment_block', 'markdown_literal_block', 'tex_math_block'].includes(token.type)));
    environment.yomuxTopLevelSourceRanges = blocks.map(token => token.map || null);
    environment.yomuxTopLevelSourceLines = blocks.map(token => token.map ? token.map[0] + 1 : null);
    environment.yomuxListItemSourceLines = spacedTokens.filter(token => token.type === 'list_item_open')
      .map(token => token.map ? token.map[0] + 1 : null);
    environment.yomuxTextblockSourceRanges = spacedTokens
      .filter(token => ['paragraph_open', 'heading_open', 'code_block', 'fence'].includes(token.type))
      .map(token => token.map || null);
    environment.yomuxTableRowSourceRanges = spacedTokens.filter(token => token.type === 'tr_open')
      .map(token => token.map || null);
    environment.yomuxMathBlockSourceRanges = spacedTokens.filter(token => token.type === 'tex_math_block')
      .map(token => ({lines: token.map, columns: token.mathColumns}));
    environment.yomuxIgnoredCommentRanges = ignoredCommentRanges;
    return spacedTokens;
  };
  function listTight(token, tokenStream, index) {
    if (token.meta?.hasTasks) return true;
    let tight = true, previous;
    for (let next = index + 1; tokenStream[next]?.level > token.level; next++) {
      const child = tokenStream[next];
      if (child.level === token.level + 2 && child.type === 'paragraph_open') return child.hidden;
      if (child.level === token.level + 1 && child.type === 'list_item_open') {
        if (previous && child.map[0] > previous.map[1]) tight = false;
        if (previous && child.map[0] > previous.map[0] + 1 &&
            tokenStream[next - 2]?.type === 'list_item_open') tight = false;
        previous = child;
      }
    }
    return tight;
  }
  const tokens = {
    ...parser.tokens,
    bullet_list: {
      ...parser.tokens.bullet_list,
      getAttrs: (token, stream, index) => ({
        tight: listTight(token, stream, index), bullet: ['-', '+', '*'].includes(token.markup) ? token.markup : '*',
      }),
    },
    ordered_list: {
      ...parser.tokens.ordered_list,
      getAttrs: (token, stream, index) => ({
        ...parser.tokens.ordered_list.getAttrs(token, stream, index), tight: listTight(token, stream, index),
        delimiter: token.markup === ')' ? ')' : '.',
      }),
    },
    list_item: {block: 'list_item', getAttrs: token => token.meta || {}},
    tex_math: {node: 'tex_math', getAttrs: token => token.meta},
    tex_math_block: {node: 'tex_math_block', getAttrs: token => token.meta},
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

function prosemirrorImageWidth(value) {
  const width = String(value || '').trim();
  return /^[1-9]\d*$/.test(width) ? width : null;
}

function prosemirrorImageFitPlugin(api) {
  const decorate = doc => {
    const decorations = [];
    doc.forEach((node, position) => {
      const image = node.firstChild;
      if (node.type.name === 'paragraph' && node.childCount === 1 && image.type.name === 'image' &&
          markdownAutoFitSvg(image.attrs.src, image.attrs.width)) {
        decorations.push(api.Decoration.node(position, position + node.nodeSize, {class: 'markdown-fit-svg'}));
      }
    });
    return api.DecorationSet.create(doc, decorations);
  };
  const plugin = new api.Plugin({
    state: {
      init: (_config, state) => decorate(state.doc),
      apply: (transaction, decorations) => transaction.docChanged ? decorate(transaction.doc) : decorations,
    },
    props: {decorations: state => plugin.getState(state)},
  });
  return plugin;
}

const PROSEMIRROR_SAFE_HTML_TAGS = new Set(['u', 'strong', 'em', 's', 'mark', 'kbd', 'sup', 'code', 'br', 'img', 'details', 'summary']);
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

function prosemirrorEmptyListItem(node) {
  return node?.childCount === 1 && node.firstChild.type.name === 'paragraph' && !node.firstChild.content.size;
}

function prosemirrorHiddenComment(node) {
  return node.type.name === 'markdown_comment_block' || node.type.name === 'markdown_comment_inline';
}

function prosemirrorMarkdownSerializer(api, {explicitInline = false} = {}) {
  return new api.MarkdownSerializer({
    ...api.defaultMarkdownSerializer.nodes,
    image(state, node) {
      if (!node.attrs.width) {
        api.defaultMarkdownSerializer.nodes.image(state, node);
        return;
      }
      const attr = value => String(value || '').replace(/[&<>"']/g, character => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[character]));
      const title = node.attrs.title ? ` title="${attr(node.attrs.title)}"` : '';
      state.write(`<img src="${attr(node.attrs.src)}" alt="${attr(node.attrs.alt)}"${title} width="${node.attrs.width}">`);
    },
    paragraph(state, node, parent) {
      if (explicitInline) {
        let from = 0, to = node.content.size;
        while (node.childAfter(from).node?.type.name === 'soft_break') from++;
        while (to > from && node.childBefore(to).node?.type.name === 'soft_break') to--;
        if (from || to !== node.content.size) node = node.cut(from, to);
      }
      if (node.content.size) {
        state.renderInline(node);
      } else if (parent.type.name === 'blockquote' || explicitInline) {
        state.write(PROSEMIRROR_EXPLICIT_SPACE);
      } else {
        // A Markdown paragraph separator is two newlines. Empty ViewEditor paragraphs need one
        // additional source newline each or Markdown would collapse them on the next parse.
        state.flushClose(1);
        state.write('\n');
      }
      state.closeBlock(node);
    },
    soft_break(state) { state.write(state.inTableCell ? '<br>' : '\n'); },
    hard_break(state, _node, parent, index) {
      if (explicitInline && !state.inTableCell && index === 0) state.write(PROSEMIRROR_EXPLICIT_SPACE);
      // A backslash-newline followed by a soft newline would split the paragraph on reopen.
      const beforeSoftBreak = parent.maybeChild(index + 1)?.type.name === 'soft_break';
      state.write(state.inTableCell || explicitInline || beforeSoftBreak ? '<br>' : '\\\n');
    },
    list_item(state, node) {
      if (node.attrs.taskChecked !== null) state.write(`[${node.attrs.taskChecked ? 'x' : ' '}] `);
      if (prosemirrorEmptyListItem(node)) return;
      node.forEach((child, _, index) => {
        // A nested empty dash bullet immediately below text otherwise becomes a Setext heading.
        if (child.type.name === 'bullet_list' && child.attrs.bullet === '-' &&
            prosemirrorEmptyListItem(child.firstChild) && node.maybeChild(index - 1)?.type.name === 'paragraph') {
          state.flushClose(2);
        }
        state.render(child, node, index);
      });
    },
    ordered_list(state, node) {
      if (node.attrs.delimiter !== ')') return api.defaultMarkdownSerializer.nodes.ordered_list(state, node);
      const start = node.attrs.order;
      const width = String(start + node.childCount - 1).length;
      state.renderList(node, ' '.repeat(width + 2), index => (String(start + index) + ') ').padStart(width + 2));
    },
    text(state, node, parent, index) {
      if (!explicitInline || !parent.isTextblock || parent.type.spec.code) {
        return api.defaultMarkdownSerializer.nodes.text(state, node);
      }
      const lineBoundary = neighbor => !neighbor || neighbor.type.name.endsWith('_break');
      const previous = parent.maybeChild(index - 1);
      const leading = lineBoundary(previous) ? node.text.match(/^ */)[0].length : 0;
      const trailing = lineBoundary(parent.maybeChild(index + 1)) ? node.text.slice(leading).match(/ *$/)[0].length : 0;
      if (leading) state.write(PROSEMIRROR_EXPLICIT_SPACE.repeat(leading));
      const text = node.text.slice(leading, node.text.length - trailing);
      let escaped = state.inAutolink ? text : state.esc(text, lineBoundary(previous));
      if (!leading && lineBoundary(previous)) escaped = escaped.replace(/^(\d+)([.)])(?=\s|$)/, '$1\\$2');
      if (parent.type.name === 'heading' && index === parent.childCount - 1) escaped = escaped.replace(/#+$/, hashes => hashes.replaceAll('#', '\\#'));
      escaped = escaped.replace(/\\\[(?=[\s\S]*\\\])/g, '&#91;');
      state.text(escaped, false);
      if (trailing) state.write(PROSEMIRROR_EXPLICIT_SPACE.repeat(trailing));
    },
    markdown_comment_block(state, node) {
      state.write(node.attrs.source || '<!---->');
      state.closeBlock(node);
    },
    tex_math(state, node) { state.text(node.attrs.source, false); },
    tex_math_block(state, node) { state.text(node.attrs.source, false); state.closeBlock(node); },
    markdown_comment_inline(state, node, _parent, index) {
      if (explicitInline && index === 0) state.write(PROSEMIRROR_EXPLICIT_SPACE);
      state.write(node.attrs.source || '<!---->');
    },
    markdown_literal_block(state, node) { state.write(node.attrs.source || ''); state.closeBlock(node); },
    markdown_literal_inline(state, node) { state.write(node.attrs.source || ''); },
    details(state, node) {
      state.write(`<details>\n<summary>${esc(node.attrs.summary)}</summary>\n\n`);
      state.renderContent(node);
      state.write('\n</details>');
      state.closeBlock(node);
    },
    table(state, node) {
      const inline = cell => {
        const saved = {out: state.out, delim: state.delim, closed: state.closed, atBlockStart: state.atBlockStart, inTightList: state.inTightList, inTableCell: state.inTableCell};
        state.out = '';
        state.delim = '';
        state.closed = null;
        state.atBlockStart = true;
        state.inTableCell = true;
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
    ...Object.fromEntries(markdownFormatCommands.filter(entry => ['strong', 'em'].includes(entry.mark)).map(entry => [
      entry.mark, explicitInline ? {open: `<${entry.mark}>`, close: `</${entry.mark}>`, mixable: true} :
        prosemirrorCodeStyleMark(entry, api.defaultMarkdownSerializer.marks[entry.mark]),
    ])),
    strike: prosemirrorCodeStyleMark(markdownFormatCommands.find(entry => entry.id === 'strike'), {open: '~~', close: '~~', mixable: true}),
    underline: {open: '<u>', close: '</u>', mixable: true},
    highlight: {open: '<mark>', close: '</mark>', mixable: true},
    kbd: {open: '<kbd>', close: '</kbd>', mixable: true},
    superscript: {open: '<sup>', close: '</sup>', mixable: true},
  }, {escapeExtraCharacters: /\$/g});
}

function prosemirrorDetailsNodeView(node) {
  const dom = document.createElement('details');
  dom.open = true;
  const summary = document.createElement('summary');
  summary.textContent = node.attrs.summary;
  const contentDOM = document.createElement('div');
  dom.append(summary, contentDOM);
  return {
    dom,
    contentDOM,
    ignoreMutation: mutation => mutation.type === 'attributes' && mutation.target === dom && mutation.attributeName === 'open',
  };
}

function clearProseMirrorSourceSelection(panel, dom = null) {
  const highlight = panel._pmAtomSourceSelection;
  if (!highlight || (dom && highlight.dom !== dom)) return;
  delete panel._pmAtomSourceSelection;
  const {view, selection} = highlight;
  // A rendered node can be destroyed while CodeMirror is applying an update.
  queueMicrotask(() => {
    if (panel._pmAtomSourceSelection || panel._cmView !== view || view.state.selection !== selection) return;
    view.dispatch({selection: {anchor: selection.main.head}});
  });
}

function bindProseMirrorSourceSelection(panel, dom, getPos, kind) {
  const select = event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || fileEditorPanelMode(panel) !== 'split') return;
    const position = getPos();
    const range = sharedProseMirrorCoordinates(panel, 100)?.atoms.find(range => range.atom === kind && range.rich === position);
    if (!range) return;
    const view = panel._cmView;
    revealSplitEditorSourceSelection(panel, range.source, range.sourceEnd);
    panel._pmAtomSourceSelection = {dom, view, selection: view.state.selection};
  };
  dom.addEventListener('click', select);
  return () => {
    dom.removeEventListener('click', select);
    clearProseMirrorSourceSelection(panel, dom);
  };
}

function prosemirrorImageNodeView(node, panel, markdownPath, getPos) {
  const image = document.createElement('img');
  const original = String(node.attrs.src || '');
  image.className = 'markdown-preview-image prosemirror-image';
  image.alt = node.attrs.alt || '';
  if (node.attrs.title) image.title = node.attrs.title;
  const width = prosemirrorImageWidth(node.attrs.width);
  if (width) image.setAttribute('width', width);
  image.dataset.originalSrc = original;
  image.loading = 'eager';
  image.decoding = 'async';
  image.style.display = 'inline-block';
  image.style.verticalAlign = 'text-bottom';
  let active = true;
  let loadFrame = 0;
  let resolveScheduledLoad = null;
  // Dockview can detach a live editor while its authenticated image fetch is in flight.
  const isCurrent = () => active;
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
    panel._pmMediaPromises.push(new Promise(resolve => {
      resolveScheduledLoad = resolve;
      loadFrame = requestAnimationFrame(() => {
        loadFrame = 0;
        if (!isCurrent()) {
          resolveScheduledLoad = null;
          resolve(false);
          return;
        }
        prosemirrorPreviewImageSource(image, markdownPath, {isCurrent}).then(result => {
          resolveScheduledLoad = null;
          resolve(result);
        });
      });
    }));
    const onError = () => {
      if (!active) return;
      image.classList.add('prosemirror-image-error');
      image.title = t('preview.markdown.imageUnavailable', {path: target.path});
    };
    image.addEventListener('error', onError, {once: true});
    image._prosemirrorErrorHandler = onError;
  }
  const unbindSourceSelection = bindProseMirrorSourceSelection(panel, image, getPos, 'Image');
  return {
    dom: image,
    destroy() {
      active = false;
      unbindSourceSelection();
      if (loadFrame) cancelAnimationFrame(loadFrame);
      loadFrame = 0;
      resolveScheduledLoad?.(false);
      resolveScheduledLoad = null;
      if (image._prosemirrorErrorHandler) image.removeEventListener('error', image._prosemirrorErrorHandler);
      delete image._prosemirrorErrorHandler;
      releaseRawFileMediaSource(image);
    },
    ignoreMutation: () => true,
  };
}

function prosemirrorCodeBlockNodeView(node, panel, markdownPath) {
  const language = prosemirrorFenceLanguage(node.attrs.params);
  if (!isMermaidFenceLanguage(language)) return null;
  return prosemirrorMermaidCodeBlockNodeView(node, panel, markdownPath);
}

function prosemirrorMermaidCodeBlockNodeView(node, panel, markdownPath) {
  const host = document.createElement('div');
  host.className = 'mermaid-preview-host';
  host.contentEditable = 'false';
  let active = true;
  panel._pmMediaPromises ||= [];
  const render = renderMermaidSourceInto(host, node.textContent || '', {
    full: false,
    path: markdownPath,
    zoomKey: 'mermaid',
    wheelScrollParent: true,
    isCurrent: () => active,
  });
  panel._pmMediaPromises.push(render);
  return {
    dom: host,
    destroy() {
      active = false;
      disposeMermaidPreviewHost(host);
    },
    ignoreMutation: () => true,
    stopEvent: event => event.type !== 'contextmenu',
  };
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

function prosemirrorTaskListItemNodeView(node, view, getPos) {
  const dom = document.createElement('li');
  const contentDOM = document.createElement('div');
  const taskLine = Number(node.attrs.taskLine || 0);
  let input = null;
  if (node.attrs.taskChecked !== null) {
    dom.classList.add('task-list-item');
    contentDOM.classList.add('markdown-task-label');
    input = document.createElement('input');
    input.className = `${MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS} markdown-task-checkbox`;
    input.type = 'checkbox';
    input.checked = node.attrs.taskChecked === true;
    input.disabled = !view.editable;
    input.dataset.sourceLine = String(taskLine);
    input.setAttribute('aria-label', t('editor.toggleTaskLine', {line: taskLine}));
    input.addEventListener('change', () => {
      const position = getPos();
      if (!view.editable || !Number.isFinite(position)) return;
      const current = view.state.doc.nodeAt(position);
      if (current?.type.name !== 'list_item') return;
      view.dispatch(view.state.tr.setNodeMarkup(position, undefined, {
        ...current.attrs,
        taskChecked: input.checked === true,
      }));
    });
    dom.appendChild(input);
  }
  dom.appendChild(contentDOM);
  return {
    dom,
    contentDOM,
    ignoreMutation: mutation => Boolean(input && (mutation.target === input || input.contains(mutation.target))),
    stopEvent: event => Boolean(input && input.contains(event.target)),
  };
}

function prosemirrorTaskAccessPlugin(api) {
  return new api.Plugin({
    view(view) {
      let editable = view.editable;
      return {update(updated) {
        if (editable === updated.editable) return;
        editable = updated.editable;
        for (const input of markdownRenderedTaskCheckboxes(updated.dom)) input.disabled = !editable;
      }};
    },
  });
}

function prosemirrorPreviewClassName() {
  const vanilla = fileEditorPreviewDisplayMode === 'vanilla';
  return [
    'prosemirror-editor',
    'markdown-body',
    vanilla ? 'vanilla-preview-body' : '',
    vanilla ? 'editor-preview-vanilla' : '',
  ].filter(Boolean).join(' ');
}

function applyProseMirrorPreviewDisplayMode(container) {
  container.className = prosemirrorPreviewClassName();
}

function applyProseMirrorViewDisplayMode(view, path) {
  view.setProps({
    attributes: {
      class: prosemirrorPreviewClassName(),
      'data-md-path': path,
      'data-prosemirror-editor': 'true',
    },
  });
}

function prosemirrorSourceLineMetadata(source, parseEnvironment) {
  const headings = String(source || '').split('\n').map((line, index) => {
    const match = line.match(/^\s*#{1,6}\s+(.+?)\s*$/);
    return match ? {line: index + 1, text: match[1].trim()} : null;
  }).filter(Boolean);
  return {
    headings,
    topLevel: parseEnvironment.yomuxTopLevelSourceLines || [],
    ranges: parseEnvironment.yomuxTopLevelSourceRanges || [],
    listItemLines: parseEnvironment.yomuxListItemSourceLines || [],
    textblocks: parseEnvironment.yomuxTextblockSourceRanges || [],
    tableRows: parseEnvironment.yomuxTableRowSourceRanges || [],
    mathBlocks: parseEnvironment.yomuxMathBlockSourceRanges || [],
    references: parseEnvironment.references || {},
  };
}

function prosemirrorSourceLineDecorations(api, doc, metadata) {
  const decorations = [];
  const decoratedPositions = new Set();
  doc.forEach((node, offset, index) => {
    const sourceLine = Number(metadata?.topLevel?.[index]);
    if (!Number.isFinite(sourceLine) || sourceLine <= 0) return;
    decorations.push(api.Decoration.node(offset, offset + node.nodeSize, {'data-source-line': String(sourceLine)}));
    decoratedPositions.add(offset);
  });
  let headingSearchFrom = 0;
  doc.descendants((node, position) => {
    if (node.type.name !== 'heading') return true;
    const headingText = String(node.textContent || '').trim();
    const sourceHeading = metadata?.headings?.find(candidate => (
      candidate.line > headingSearchFrom && candidate.text === headingText
    ));
    if (!sourceHeading) return false;
    if (!decoratedPositions.has(position)) {
      decorations.push(api.Decoration.node(position, position + node.nodeSize, {
        'data-source-line': String(sourceHeading.line),
      }));
    }
    headingSearchFrom = sourceHeading.line;
    return false;
  });
  return api.DecorationSet.create(doc, decorations);
}

function prosemirrorSourceLinePlugin(api, panel) {
  let plugin = null;
  plugin = new api.Plugin({
    state: {
      init(_config, state) {
        return prosemirrorSourceLineDecorations(api, state.doc, panel._pmSourceLineMetadata);
      },
      apply(transaction, decorations) {
        const metadata = transaction.getMeta(plugin);
        if (metadata) return prosemirrorSourceLineDecorations(api, transaction.doc, metadata);
        return transaction.docChanged ? decorations.map(transaction.mapping, transaction.doc) : decorations;
      },
    },
    props: {
      decorations(state) { return plugin.getState(state); },
    },
  });
  return plugin;
}

function destroyProseMirrorPanel(panel, options = {}) {
  if (!panel) return;
  if (options.invalidateEnsure !== false) {
    panel._pmEnsureGeneration = Number(panel._pmEnsureGeneration || 0) + 1;
  }
  const view = panel._pmView;
  if (!view) return;
  panel._pmContextMenuGeneration = Number(panel._pmContextMenuGeneration || 0) + 1;
  panel._pmContextMenuDispose?.();
  delete panel._pmContextMenuDispose;
  flushProseMirrorSource(panel, panel._pmPath, {preserveTerminalNewline: true});
  releaseRawFileMediaSources(view.dom);
  view.destroy();
  delete panel._pmView;
  delete panel._pmPath;
  delete panel._pmSource;
  delete panel._pmPlugins;
  delete panel._pmSourceLinePlugin;
  delete panel._pmSharePresence;
  delete panel._pmSplitCaret;
  delete panel._pmSharePeers;
  delete panel._pmShareCoordinates;
  delete panel._pmSharedSourceDoc;
  delete panel._pmSourceDocument;
  delete panel._pmDocumentChanged;
  delete panel._pmPreviewPane;
  scheduleSplitEditorCaret(panel);
}

function installProseMirrorContextMenuGuard() {
  if (document.__yomuxProseMirrorContextMenuGuard) return;
  const guard = event => {
    const anchor = event.target?.closest?.('[data-prosemirror-editor] a[href]');
    if (anchor) event.preventDefault();
  };
  document.addEventListener('contextmenu', guard, true);
  document.__yomuxProseMirrorContextMenuGuard = guard;
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
  const api = window.YOmuxProseMirror;
  const selection = prosemirrorSelectionForDocument(api, view.state.doc, from, to);
  if (!selection) return false;
  view.dispatch(view.state.tr.setSelection(selection));
  return true;
}

function prosemirrorIncomingTransaction(state, doc) {
  const current = state.doc.content;
  const start = current.findDiffStart(doc.content);
  if (start === null) return state.tr;
  if (state.doc.childCount === doc.childCount) {
    const separate = state.tr;
    function update(before, after, from) {
      if (before.eq(after)) return;
      if (!before.sameMarkup(after) || before.isLeaf ||
          (!before.inlineContent && before.childCount !== after.childCount)) {
        separate.replaceWith(from, from + before.nodeSize, after);
        return;
      }
      if (before.inlineContent) {
        const first = before.content.findDiffStart(after.content);
        const {a, b} = before.content.findDiffEnd(after.content);
        const overlap = Math.max(0, first - Math.min(a, b));
        separate.replaceWith(from + 1 + first, from + 1 + a + overlap, after.content.cut(first, b + overlap));
        return;
      }
      let end = from + before.nodeSize - 1;
      for (let index = before.childCount - 1; index >= 0; index--) {
        const child = before.child(index);
        end -= child.nodeSize;
        update(child, after.child(index), end);
      }
    }
    // A batch can skip unchanged siblings containing local undo history.
    update(state.doc, doc, -1);
    if (separate.steps.length > 1 && separate.doc.eq(doc)) return separate;
  }
  const end = current.findDiffEnd(doc.content);
  const overlap = Math.max(0, start - Math.min(end.a, end.b));
  const slice = doc.slice(start, end.b + overlap);
  const small = state.tr;
  small.replace(start, end.a + overlap, slice);
  if (small.doc.eq(doc)) return small;
  // Fitting an open slice can drop a container. Replace the affected top-level blocks as a closed slice.
  let from = 0, first = 0, oldEnd = current.childCount, newEnd = doc.childCount;
  while (first < oldEnd && first < newEnd && current.child(first).eq(doc.child(first))) {
    from += current.child(first++).nodeSize;
  }
  let to = current.size, nextTo = doc.content.size;
  while (oldEnd > first && newEnd > first && current.child(oldEnd - 1).eq(doc.child(newEnd - 1))) {
    to -= current.child(--oldEnd).nodeSize;
    nextTo -= doc.child(--newEnd).nodeSize;
  }
  return state.tr.replace(from, to, doc.slice(from, nextTo));
}

function syncProseMirrorPanelSource(panel, path, state, options = {}) {
  if (!panel?._pmView || panel._pmPath !== path || !state) return false;
  restoreProseMirrorPanelDom(panel, {previewPane: panel._pmPreviewPane});
  applyProseMirrorViewDisplayMode(panel._pmView, path);
  const textEditorCommit = options.sourceSurface === 'text-editor';
  const previewText = options.previewText;
  const sharedText = state._shareActive ? codeMirrorPanelContent(panel) : null;
  const source = previewText ?? sharedText ?? (textEditorCommit ? state.content : codeMirrorSplitPreviewText(panel, path, state)) ?? '';
  if (panel._pmSource === source && !panel._pmDocumentChanged) return true;
  if (textEditorCommit) {
    if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
    panel._pmSerializeTimer = null;
    panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
    panel._pmSource = null;
    panel._pmDocumentChanged = false;
  }
  if (panel._pmSource === source) return true;
  if (!textEditorCommit && panel._pmSerializeTimer && panel._pmSource === null) return true;
  if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
  panel._pmSerializeTimer = null;
  panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
  try {
    const parseEnvironment = {};
    const doc = panel._pmParser.parse(source, parseEnvironment);
    panel._pmSourceLineMetadata = prosemirrorSourceLineMetadata(source, parseEnvironment);
    panel._pmSourceLines = panel._pmSourceLineMetadata.topLevel;
    panel._pmIgnoredCommentRanges = parseEnvironment.yomuxIgnoredCommentRanges || [];
    const view = panel._pmView;
    const transaction = prosemirrorIncomingTransaction(view.state, doc);
    transaction.setMeta(panel._pmSourceLinePlugin, panel._pmSourceLineMetadata).setMeta('addToHistory', false);
    view.updateState(view.state.apply(transaction));
    panel._pmSource = source;
    panel._pmSharedSourceDoc = state._shareActive ? panel._cmView?.state.doc : null;
    panel._pmSourceDocument = doc;
    panel._pmDocumentChanged = false;
    const pending = panel._pmSharePeers;
    if (state._shareActive && pending && pending.doc === panel._pmSharedSourceDoc) {
      paintSharedProseMirrorPresence(panel, pending.peers);
    }
    scheduleSplitEditorCaret(panel);
    return true;
  } catch (error) {
    // The source editor may still be typing an incomplete construct. A committed failure
    // uses the usual read-only fallback and diagnostic.
    if (previewText !== undefined) return false;
    renderProseMirrorFailure(panel, path, editorPanelParts(panel), error);
    return false;
  }
}

function restoreProseMirrorPanelDom(panel, parts) {
  const view = panel?._pmView;
  const pane = parts?.previewPane;
  if (!view || !pane) return false;
  if (view.dom.isConnected || pane.contains(view.dom)) return true;
  cleanupStandardPreviewStrategy(pane);
  disposeMarkdownPreviewEditing(pane);
  pane._previewRendererId = null;
  pane.replaceChildren(view.dom);
  clearProseMirrorFallback(parts);
  pane.dataset.prosemirrorState = 'ready';
  return true;
}

function prosemirrorSupportedSource(path, state) {
  return state?.kind === 'text' && previewKindForPath(path) === 'markdown' && state.historical !== true &&
    (!state._shareActive || fileEditorStateWritable(state));
}

function sharedProseMirrorSourceChanges(doc, start, raw, canonical, replacement) {
  const base = Array.from(canonical);
  const local = fileMergeEdits(base, Array.from(replacement));
  const spelling = raw === canonical ? [] : fileMergeEdits(base, Array.from(raw));
  if (!local || !spelling) return null;
  const rawOffsets = [start];
  for (const character of raw) rawOffsets.push(rawOffsets.at(-1) + character.length);
  const changes = [];
  for (const edit of local) {
    let startShift = 0, endShift = 0;
    for (const other of spelling) {
      if (edit.from === edit.to && other.from === other.to && edit.from === other.from) {
        if (edit.from === base.length) continue;
        return null;
      }
      const delta = other.lines.length - (other.to - other.from);
      if (other.to <= edit.from) {
        startShift += delta;
        endShift += delta;
      } else if (other.from < edit.to) {
        if (other.from < edit.from || other.to > edit.to) return null;
        endShift += delta;
      }
    }
    changes.push({from: rawOffsets[edit.from + startShift], to: rawOffsets[edit.to + endShift], insert: edit.lines.join('')});
  }
  return window.YOmuxCodeMirror.ChangeSet.of(changes, doc.length);
}

function sharedProseMirrorEquivalent(parsed, edited) {
  if (parsed.eq(edited)) return true;
  if (!parsed.sameMarkup(edited) && !(parsed.type === edited.type && (
    (edited.type.name === 'bullet_list' && parsed.attrs.bullet === edited.attrs.bullet) ||
    (edited.type.name === 'ordered_list' && parsed.attrs.order === edited.attrs.order &&
      parsed.attrs.delimiter === edited.attrs.delimiter) ||
    (edited.type.name === 'list_item' && parsed.attrs.taskChecked === edited.attrs.taskChecked)
  ))) return false;
  if (['paragraph', 'heading', 'table_cell', 'table_header'].includes(edited.type.name)) {
    let from = 0, to = edited.content.size;
    const trim = (node, direction) => node.isText ? node.text.match(direction < 0 ? /^[ \t]*/ : /[ \t]*$/)[0].length :
      node.type.name === 'soft_break' ? node.nodeSize : 0;
    for (let i = 0; i < edited.childCount; i++) {
      const node = edited.child(i), size = trim(node, -1);
      from += size;
      if (size !== node.nodeSize) break;
    }
    for (let i = edited.childCount - 1; i >= 0; i--) {
      const node = edited.child(i), size = trim(node, 1);
      to -= size;
      if (size !== node.nodeSize) break;
    }
    return parsed.content.eq(edited.content.cut(from, Math.max(from, to)));
  }
  if (parsed.childCount !== edited.childCount) return false;
  for (let index = 0; index < parsed.childCount; index++) {
    if (!sharedProseMirrorEquivalent(parsed.child(index), edited.child(index))) return false;
  }
  return true;
}

function normalizeSharedProseMirrorEdit(transaction) {
  if (!transaction.docChanged) return;
  const {doc, before} = transaction;
  const start = before.content.findDiffStart(doc.content);
  if (start === null) return;
  const end = before.content.findDiffEnd(doc.content).b;
  const removals = [], softBreaks = [];
  const from = Math.max(0, start - 1), to = Math.min(doc.content.size, Math.max(start, end) + 1);
  doc.nodesBetween(from, to, (node, position, parent) => {
    if (node.isText && !parent.type.spec.code && node.text.includes('\n')) {
      for (const match of node.text.matchAll(/\n/g)) {
        softBreaks.push({position: position + match.index, marks: node.marks});
      }
    }
    if (node.isTextblock) node.forEach((child, offset, index) => {
      if (child.type.name === 'soft_break' && node.maybeChild(index - 1)?.type === child.type) {
        removals.push([position + 1 + offset, child.nodeSize]);
      }
    });
    if (node.type.name !== 'list_item' || node.childCount < 2) return;
    let required = true;
    node.forEach((child, offset, index) => {
      if (child.type.name === 'paragraph' && !child.content.size &&
          (!required || ['paragraph', 'tex_math_block'].includes(node.maybeChild(index + 1)?.type.name))) {
        removals.push([position + 1 + offset, child.nodeSize]);
      } else {
        required = false;
      }
    });
  });
  for (const {position, marks} of softBreaks) {
    transaction.replaceWith(position, position + 1, doc.type.schema.nodes.soft_break.create(null, null, marks));
  }
  for (const [at, size] of removals.sort((a, b) => b[0] - a[0])) transaction.delete(at, at + size);
  const joins = [];
  const nextDoc = transaction.doc;
  const normalized = transaction.mapping.slice(transaction.mapping.maps.length - softBreaks.length - removals.length);
  nextDoc.nodesBetween(normalized.map(from), normalized.map(to), (node, position) => {
    if (node.type.name !== 'bullet_list') return;
    const boundary = position + node.nodeSize;
    const sibling = nextDoc.resolve(boundary).nodeAfter;
    if (sibling?.type === node.type && sibling.attrs.bullet === node.attrs.bullet) joins.push(boundary);
  });
  for (const boundary of joins.sort((a, b) => b - a)) transaction.join(boundary);
}

function sharedProseMirrorEdit(panel, transaction, includeNeighbors = false) {
  const view = panel._cmView;
  const source = panel._pmSource;
  if (source == null || (panel._pmSharedSourceDoc !== view.state.doc && source !== view.state.doc.toString())) return null;
  const before = transaction.before, after = transaction.doc;
  let first = 0, oldEnd = before.childCount, newEnd = after.childCount;
  while (first < oldEnd && first < newEnd && before.child(first).eq(after.child(first))) first++;
  while (oldEnd > first && newEnd > first && before.child(oldEnd - 1).eq(after.child(newEnd - 1))) {
    oldEnd--; newEnd--;
  }
  if (first === oldEnd && first === newEnd) return {source, selectionOnly: true};
  if (includeNeighbors) {
    if (first > 0) first--;
    while (oldEnd < before.childCount && newEnd < after.childCount) {
      const range = panel._pmSourceLineMetadata?.ranges?.[oldEnd];
      oldEnd++;
      newEnd++;
      if (range && range[0] < range[1]) break;
    }
  }
  const ranges = panel._pmSourceLineMetadata?.ranges;
  // An insertion at a block boundary needs a source range for its Markdown separators.
  if (first === oldEnd) {
    if (first > 0) first--;
    else if (before.childCount) { oldEnd++; newEnd++; }
  }
  const emptyParagraph = node => node?.type.name === 'paragraph' && !node.content.size;
  const atEnd = oldEnd === before.childCount && newEnd === after.childCount;
  const trailingParagraphs = atEnd && (emptyParagraph(before.lastChild) || emptyParagraph(after.lastChild));
  while (first > 0 && (!ranges?.[first] || ranges[first][1] <= ranges[first][0])) first--;
  const startLine = ranges?.[first]?.[0], endLine = ranges?.[oldEnd - 1]?.[1];
  const leadingParagraphs = first === 0 && Number.isInteger(startLine) && startLine === ranges[0][1];
  if (!Number.isInteger(startLine) || (!leadingParagraphs && !trailingParagraphs &&
      (!Number.isInteger(endLine) || endLine <= startLine))) {
    if (source.trim()) return includeNeighbors ? null : sharedProseMirrorEdit(panel, transaction, true);
  }
  const doc = view.state.doc;
  const start = !leadingParagraphs && Number.isInteger(startLine) ? doc.line(startLine + 1).from : 0;
  const end = trailingParagraphs || !Number.isInteger(endLine) ? source.length :
    leadingParagraphs && endLine === startLine ? doc.line(startLine + 1).from : doc.line(Math.min(doc.lines, endLine)).to;
  const previousNodes = [];
  for (let index = first; index < oldEnd; index++) previousNodes.push(before.child(index));
  const nodes = [];
  for (let index = first; index < newEnd; index++) nodes.push(after.child(index));
  const hiddenComments = children => {
    const comments = [];
    const collect = node => {
      if (prosemirrorHiddenComment(node)) comments.push(node.attrs.source);
    };
    for (const child of children) {
      collect(child);
      child.descendants(collect);
    }
    return comments;
  };
  const beforeComments = hiddenComments(previousNodes), afterComments = hiddenComments(nodes);
  if (beforeComments.length !== afterComments.length || beforeComments.some((comment, index) => comment !== afterComments[index])) return null;
  const serialize = (root, children, serializer = panel._pmSerializer) => serializer.serialize(root.type.create(null, children));
  const raw = source.slice(start, end);
  const retainNewline = trailingParagraphs && raw.endsWith('\n');
  const canonical = serialize(before, previousNodes) + (retainNewline && !emptyParagraph(before.lastChild) ? '\n' : '');
  const replacement = serialize(after, nodes) + (retainNewline && !emptyParagraph(after.lastChild) ? '\n' : '');
  function validate(changes) {
    if (!changes) return null;
    const candidateDoc = changes.apply(doc);
    const replacementSource = candidateDoc.sliceString(start, end + candidateDoc.length - doc.length);
    const candidate = candidateDoc.toString();
    if (oldEnd === first + 1 && newEnd === first + 1 && endLine === startLine + 1 &&
        before.child(first).type.name === 'paragraph' && nodes[0].type.name === 'paragraph' &&
        replacementSource.trim() && !replacementSource.includes('\n')) {
      const fragment = panel._pmParser.parse(replacementSource);
      if (fragment.childCount === 1 && sharedProseMirrorEquivalent(fragment.child(0), nodes[0])) {
        return {source: candidate, changes, metadata: panel._pmSourceLineMetadata};
      }
    }
    const environment = {};
    const parsed = panel._pmParser.parse(candidate, environment);
    if (!sharedProseMirrorEquivalent(parsed, after)) return null;
    return {source: candidate, changes, metadata: prosemirrorSourceLineMetadata(candidate, environment)};
  }
  const mapped = validate(sharedProseMirrorSourceChanges(doc, start, raw, canonical, replacement));
  if (mapped) return mapped;
  const replace = text => sharedProseMirrorSourceChanges(doc, start, raw, raw, text);
  const serializer = prosemirrorMarkdownSerializer(window.YOmuxProseMirror, {explicitInline: true});
  const explicitReplacement = serialize(after, nodes, serializer) + (retainNewline && !emptyParagraph(after.lastChild) ? '\n' : '');
  for (const text of new Set([replacement, explicitReplacement])) {
    const result = validate(replace(text));
    if (result) return result;
    if (start > 1 && source[start - 2] !== '\n') {
      const separated = validate(replace('\n' + text));
      if (separated) return separated;
    }
  }
  return includeNeighbors ? null : sharedProseMirrorEdit(panel, transaction, true);
}

function applySharedProseMirrorTransaction(panel, path, view, transaction) {
  const state = fileEditorPanelState(panel);
  let edit;
  try {
    normalizeSharedProseMirrorEdit(transaction);
    edit = sharedProseMirrorEdit(panel, transaction);
  } catch (_) { edit = null; }
  if (!edit) {
    syncProseMirrorPanelSource(panel, path, state);
    view.updateState(view.state);
    setFileEditorPanelStatus(panel, 'This edit could not be mapped to the shared Markdown. Use TextEdit for this change.', 'warn');
    return;
  }
  if (!edit.selectionOnly) {
    panel._pmSource = edit.source;
    if (panel._pmSourceLineMetadata !== edit.metadata) transaction.setMeta(panel._pmSourceLinePlugin, edit.metadata);
    panel._pmSourceLineMetadata = edit.metadata;
  }
  view.updateState(view.state.apply(transaction));
  panel._pmSourceDocument = view.state.doc;
  if (!edit.selectionOnly) {
    panel._pmApplyingSharedEdit = true;
    try {
      panel._cmView.dispatch({changes: edit.changes, annotations: panel._cmApi.Transaction.addToHistory.of(false)});
      panel._pmSharedSourceDoc = panel._cmView.state.doc;
    } finally {
      panel._pmApplyingSharedEdit = false;
    }
  }
  restartProseMirrorCursorBlink(view);
  scheduleSplitEditorCaret(panel);
}

function refreshSharedProseMirrorAccess(panel) {
  panel._pmView.setProps({editable: () => prosemirrorPanelEditable(panel)});
  refreshMarkdownFormattingToolbar(panel);
}

function splitCaretSourceSyntax(tree, doc, utils, references) {
  if (!tree) return null;
  const ranges = [];
  const cells = [];
  const unresolved = new Set();
  tree.iterate({enter(node) {
    const {from, to, name} = node;
    if (name === 'TableCell') cells.push({from, to});
    if (name === 'Link' || name === 'Image') {
      const children = node.node.getChildren('LinkMark');
      const last = node.node.lastChild;
      if (children.length === 2 && last) {
        const label = last.name === 'LinkLabel' && doc.sliceString(last.from + 1, last.to - 1) ||
          doc.sliceString(children[0].to, children[1].from);
        // Lezer recognizes unresolved reference syntax; Markdown-it renders it literally.
        if (!Object.hasOwn(references, utils.normalizeReference(label))) {
          unresolved.add(from);
          return;
        }
      }
    }
    if (name === 'Image' || name === 'Comment') {
      ranges.push({from, to, text: '', atom: name});
      return false;
    }
    if (name === 'HTMLTag') {
      const tag = doc.sliceString(from, to).match(/^<\/?([a-z]+)\b/i)?.[1]?.toLowerCase();
      if (tag) ranges.push({from, to, text: '', atom: tag === 'img' ? 'Image' : 'Tag'});
    } else if (name === 'HTMLBlock') {
      const raw = doc.sliceString(from, to);
      const image = raw.match(/^\s*(<img(?=[\s/>])(?:[^<>"']|"[^"]*"|'[^']*')*>)\s*$/i)?.[1];
      if (image) {
        const start = from + raw.indexOf(image);
        ranges.push({from: start, to: start + image.length, text: '', atom: 'Image'});
      }
    } else if (['LinkMark', 'LinkLabel', 'LinkTitle', 'ListMark', 'HeaderMark', 'QuoteMark',
      'EmphasisMark', 'StrikethroughMark', 'CodeMark', 'CodeInfo', 'TableDelimiter'].includes(name) ||
      name === 'URL' && node.node.parent?.name === 'Link') {
      if (!unresolved.has(node.node.parent?.from)) ranges.push({from, to, text: ''});
    } else if (name === 'Entity' || name === 'Escape') {
      const raw = doc.sliceString(from, to), text = utils.unescapeAll(raw);
      if (raw !== text) ranges.push({from, to, text});
    }
  }});
  return {ranges, cells};
}

function splitCaretSourceProjection(raw, start, changes) {
  const pieces = [];
  let text = '', cursor = start;
  const copy = end => {
    if (end <= cursor) return;
    pieces.push({start: text.length, end: text.length + end - cursor, source: cursor});
    text += raw.slice(cursor - start, end - start);
  };
  for (const change of changes) {
    if (change.from < cursor || change.to > start + raw.length) continue;
    copy(change.from);
    if (change.text) {
      pieces.push({start: text.length, end: text.length + change.text.length,
        source: change.from, sourceEnd: change.to});
      text += change.text;
    }
    cursor = change.to;
  }
  copy(start + raw.length);
  return {text, pieces};
}

function splitCaretLowerBound(ranges, key, boundary) {
  let first = 0, last = ranges.length;
  while (first < last) {
    const middle = (first + last) >>> 1;
    if (ranges[middle][key] < boundary) first = middle + 1;
    else last = middle;
  }
  return first;
}

function splitCaretSyntaxWithin(ranges, bounds) {
  const local = [];
  for (let index = splitCaretLowerBound(ranges, 'from', bounds.source);
    index < ranges.length && ranges[index].from < bounds.sourceEnd; index++) {
    if (ranges[index].to <= bounds.sourceEnd) local.push(ranges[index]);
  }
  return local;
}

function sharedProseMirrorCoordinates(panel, parseBudget = 0) {
  const state = panel._cmView?.state, source = state?.doc, view = panel._pmView;
  if (!source || !view) return null;
  const cached = panel._pmShareCoordinates;
  const api = window.YOmuxCodeMirror;
  const tree = api?.ensureSyntaxTree(state, source.length, parseBudget) || api?.ensureSyntaxTree(state, 0, 0);
  if (cached?.source === source && cached.rich === view.state.doc && cached.tree === tree) return cached;
  const original = panel._pmSource;
  const normalized = cached && cached.original === original ? cached.normalized : original?.replace(/\r\n?/g, '\n');
  if (panel._pmSharedSourceDoc !== source &&
      (normalized?.length !== source.length || normalized !== source.toString())) return null;
  const tokenizer = panel._pmParser.tokenizer;
  const {utils} = tokenizer;
  const {references, mathBlocks} = panel._pmSourceLineMetadata;
  const syntax = splitCaretSourceSyntax(tree, source, utils, references);
  const spans = [], blocks = [], emptyItems = [], textblocks = [], atoms = [];
  const listItemLines = panel._pmSourceLineMetadata?.listItemLines || [];
  let itemIndex = 0;
  if (listItemLines.length) view.state.doc.descendants((node, position) => {
    if (node.type.name !== 'list_item') return;
    const lineNumber = listItemLines[itemIndex++];
    if (!prosemirrorEmptyListItem(node) || !Number.isInteger(lineNumber) || lineNumber > source.lines) return;
    const line = source.line(lineNumber);
    if (/^[\t >]*(?:[-+*]|\d+[.)])(?:[ \t]+\[[ xX]\])?[ \t]*$/.test(line.text)) {
      emptyItems.push({rich: position + 2, source: line.to, sourceStart: line.from});
    }
  });
  const boundsFor = (range, rich, richEnd) => {
    if (!range || range[0] >= range[1] || range[1] > source.lines) return;
    return {rich, richEnd, source: source.line(range[0] + 1).from, sourceEnd: source.line(range[1]).to};
  };
  const collectSpans = (node, offset, bounds, raw, sourceSyntax) => {
    if (node.isTextblock && node.textContent === raw && node.content.size === raw.length) {
      spans.push({rich: offset + 1, source: bounds.source, length: raw.length});
      return;
    }
    let changes;
    if (sourceSyntax) {
      changes = sourceSyntax;
    } else {
      changes = Array.from(raw.matchAll(/\\[!-\/:-@[-\x60{-~]|&(?:#[xX][\da-fA-F]+|#\d+|[a-zA-Z][\da-zA-Z]*);/g),
        match => ({from: bounds.source + match.index, to: bounds.source + match.index + match[0].length,
          text: utils.unescapeAll(match[0])})).filter(change => change.to - change.from !== change.text.length);
    }
    const projected = splitCaretSourceProjection(raw, bounds.source, changes);
    const content = projected.text;
    const addSpan = (leaf, start) => {
      const end = start + leaf.text.length;
      const pieces = projected.pieces;
      for (let index = splitCaretLowerBound(pieces, 'end', start + 1);
        index < pieces.length && pieces[index].start < end; index++) {
        const piece = pieces[index];
        const from = Math.max(start, piece.start), to = Math.min(end, piece.end);
        const rich = leaf.rich + from - start;
        if (piece.sourceEnd === undefined) {
          spans.push({rich, source: piece.source + from - piece.start, length: to - from});
        } else {
          if (from === piece.start) spans.push({rich, source: piece.source, length: 0});
          if (to === piece.end) spans.push({rich: rich + to - from, source: piece.sourceEnd, length: 0});
          if (from === piece.start && to === piece.end) {
            atoms.push({source: piece.source, sourceEnd: piece.sourceEnd, rich, richEnd: rich + to - from});
          }
        }
      }
    };
    const leaves = [];
    node.descendants((child, position) => {
      if (child.isText) leaves.push({text: child.text, rich: offset + position + 1});
    });
    // Only cross delimiters and whitespace. A URL or HTML attribute can repeat visible text.
    const delimiter = /^(?:[\s*_~\x60>#|+:[\]().-]|\d+[.)])*$/;
    let left = 0, first = 0;
    for (; first < leaves.length; first++) {
      const leaf = leaves[first];
      const match = content.indexOf(leaf.text, left);
      if (match < 0 || !delimiter.test(content.slice(left, match))) break;
      addSpan(leaf, match);
      left = match + leaf.text.length;
    }
    let right = content.length;
    for (let index = leaves.length - 1; index >= first; index--) {
      const leaf = leaves[index];
      if (right - left < leaf.text.length) break;
      const match = content.lastIndexOf(leaf.text, right - leaf.text.length);
      if (match < left || !/^[\s*_~\x60]*$/.test(content.slice(match + leaf.text.length, right))) break;
      addSpan(leaf, match);
      right = match;
    }
  };
  const collectAtoms = (node, offset, bounds, raw, firstSpan, sourceSyntax) => {
    if (!raw.includes('<') && !raw.includes('![')) return;
    const localSpans = spans.slice(firstSpan);
    let after = bounds.source;
    node.descendants((child, position) => {
      const kind = prosemirrorHiddenComment(child) ? 'Comment' :
        child.type.name === 'image' ? 'Image' : child.type.name === 'markdown_literal_inline' ? 'Tag' : null;
      if (!kind) return;
      const rich = offset + position + 1, richEnd = rich + child.nodeSize;
      let from = bounds.source, to = bounds.sourceEnd;
      for (const span of localSpans) {
        if (span.rich + span.length <= rich) from = Math.max(from, span.source + span.length);
        if (span.rich >= richEnd) to = Math.min(to, span.source);
      }
      const range = sourceSyntax?.find(range => range.atom === kind && range.from >= Math.max(from, after) && range.to <= to);
      if (range) {
        from = range.from;
        to = range.to;
        after = to;
      } else if (kind === 'Comment') {
        const start = raw.indexOf(child.attrs.source, from - bounds.source);
        if (start < 0 || bounds.source + start + child.attrs.source.length > to) return;
        from = bounds.source + start;
        to = from + child.attrs.source.length;
      }
      if (from < to) atoms.push({source: from, sourceEnd: to, rich, richEnd, atom: range?.atom});
    });
  };
  const collect = (node, offset, bounds) => {
    const firstSpan = spans.length;
    const raw = source.sliceString(bounds.source, bounds.sourceEnd);
    let sourceSyntax = syntax && bounds.sourceEnd <= tree.length ? splitCaretSyntaxWithin(syntax.ranges, bounds) : null;
    const equations = [];
    if (raw.includes('$') || raw.includes('\\(') || raw.includes('\\[')) {
      node.descendants((child, position) => {
        if (child.type.name === 'tex_math') equations.push({child, rich: offset + position + 1});
      });
    }
    if (equations.length) {
      const tokens = [];
      tokenizer.inline.parse(raw, tokenizer, {references}, tokens);
      const matches = [];
      for (let index = 0; index < tokens.length; index++) {
        const codeEnd = prosemirrorInlineCodeEnd(tokens, index);
        if (codeEnd > index) index = codeEnd;
        else if (tokens[index].type === 'tex_math') matches.push(tokens[index]);
      }
      const tableCell = node.type.name === 'table_cell' || node.type.name === 'table_header';
      // Match the parsed sequence within this block or cell. Identical code spans never enter it.
      if (matches.length === equations.length && matches.every((token, index) =>
        token.meta.source === equations[index].child.attrs.source ||
        (tableCell && token.meta.source.replaceAll('\\|', '|') === equations[index].child.attrs.source))) {
        const ranges = matches.map(({mathOffset, meta}, index) => {
          const {child, rich} = equations[index];
          const from = bounds.source + mathOffset, to = from + meta.source.length;
          atoms.push({source: from, sourceEnd: to, rich, richEnd: rich + child.nodeSize, atom: 'Math'});
          return {from, to, text: ''};
        });
        if (sourceSyntax) {
          sourceSyntax = sourceSyntax.filter(change => !ranges.some(range => range.from < change.to && range.to > change.from))
            .concat(ranges).sort((a, b) => a.from - b.from);
        }
      }
    }
    const task = node.type.name === 'paragraph' && raw.match(/^[\t >]*(?:[-+*]|\d+[.)])[\t ]+(\[[ xX]\])(?=\s|$)/);
    if (task && sourceSyntax) {
      const from = bounds.source + task[0].length - task[1].length;
      sourceSyntax.splice(splitCaretLowerBound(sourceSyntax, 'from', from), 0,
        {from, to: bounds.source + task[0].length, text: ''});
    }
    collectSpans(node, offset, bounds, raw, sourceSyntax);
    collectAtoms(node, offset, bounds, raw, firstSpan, sourceSyntax);
  };
  const collectRow = ({node, position}, range) => {
    const bounds = boundsFor(range, position + 2, position + node.nodeSize - 1);
    if (!bounds) return;
    const cells = syntax && bounds.sourceEnd <= tree.length ? splitCaretSyntaxWithin(syntax.cells, bounds) : [];
    if (!cells.length) {
      textblocks.push(bounds);
      collect(node, position, bounds);
      return;
    }
    node.forEach((cell, offset, index) => {
      const cellPosition = position + offset + 1;
      const cellBounds = {source: cells[index]?.from ?? bounds.sourceEnd, sourceEnd: cells[index]?.to ?? bounds.sourceEnd,
        rich: cellPosition + 1, richEnd: cellPosition + cell.nodeSize - 1};
      textblocks.push(cellBounds);
      collect(cell, cellPosition, cellBounds);
    });
  };
  const textNodes = [], rows = [], blockEquations = [];
  view.state.doc.descendants((node, position) => {
    if (['paragraph', 'heading', 'code_block'].includes(node.type.name)) textNodes.push({node, position});
    if (node.type.name === 'table_row') rows.push({node, position});
    if (node.type.name === 'tex_math_block') blockEquations.push({node, position});
  });
  if (mathBlocks.length === blockEquations.length) blockEquations.forEach(({node, position}, index) => {
    const {lines, columns} = mathBlocks[index];
    const bounds = boundsFor(lines, position, position + node.nodeSize);
    if (!bounds || !columns) return;
    atoms.push({...bounds, source: bounds.source + columns[0], sourceEnd: source.line(lines[1]).from + columns[1], atom: 'Math'});
  });
  const rowRanges = panel._pmSourceLineMetadata?.tableRows || [];
  const scopedRows = rowRanges.length === rows.length;
  if (scopedRows) rows.forEach((row, index) => collectRow(row, rowRanges[index]));
  const textRanges = panel._pmSourceLineMetadata?.textblocks || [];
  const scoped = textRanges.length === textNodes.length && textNodes.length > 0;
  if (scoped) textNodes.forEach(({node, position}, index) => {
    const bounds = boundsFor(textRanges[index], position + 1, position + 1 + node.content.size);
    if (!bounds) return;
    textblocks.push(bounds);
    collect(node, position, bounds);
  });
  view.state.doc.forEach((block, offset, index) => {
    let richStart = offset + (block.isTextblock ? 1 : 0);
    if (!block.isTextblock && !block.isAtom) block.descendants((node, position) => {
      if (richStart !== offset) return false;
      if (node.isTextblock) {
        richStart = offset + position + 2;
        return false;
      }
    });
    const range = panel._pmSourceLineMetadata?.ranges?.[index];
    const bounds = boundsFor(range, richStart, offset + block.nodeSize);
    if (!bounds) return;
    blocks.push(bounds);
    if (prosemirrorHiddenComment(block)) {
      atoms.push(bounds);
    } else if (block.type.name === 'table' ? !scopedRows : !scoped) collect(block, offset, bounds);
  });
  return panel._pmShareCoordinates = {
    spans, blocks, textblocks, atoms, emptyItems: itemIndex === listItemLines.length ? emptyItems : [],
    source, rich: view.state.doc, original, normalized, tree,
  };
}

function mapSharedProseMirrorPosition(coordinates, position, from) {
  if (!coordinates) return {offset: -1, exact: false};
  const to = from === 'rich' ? 'source' : 'rich';
  const {emptyItems, atoms, spans, textblocks, blocks} = coordinates;
  const emptyItem = emptyItems.find(item => from === 'rich' ? position === item.rich :
    position >= item.sourceStart && position <= item.source);
  if (emptyItem) return {offset: emptyItem[to], exact: true};
  if (from === 'source') {
    const atom = atoms.find(range => position > range.source && position < range.sourceEnd);
    if (atom) return {offset: atom.rich, exact: false};
  }
  const span = spans.find(range => position >= range[from] && position <= range[from] + range.length);
  if (span) return {offset: span[to] + position - span[from], exact: true};
  if (from === 'rich') {
    const atom = atoms.find(range => position >= range.rich && position <= range.richEnd);
    if (atom) return {offset: position === atom.rich ? atom.source : atom.sourceEnd, exact: false};
  }
  const contains = (scope, point) => point >= scope[from] && point <= scope[from + 'End'];
  const scope = textblocks.find(range => contains(range, position)) || blocks.find(range => contains(range, position));
  const next = blocks.find(range => position < range[from]);
  if (!scope) return {offset: next?.[to] ?? blocks.at(-1)?.[to + 'End'] ?? (to === 'rich' ? 1 : 0), exact: false};
  let offset = scope[to], distance = Infinity;
  for (const span of spans) for (const end of [0, span.length]) {
    const point = span[from] + end;
    const gap = Math.abs(position - point);
    if (!contains(scope, point) || gap >= distance) continue;
    distance = gap;
    offset = span[to] + end;
  }
  return {offset, exact: false};
}

function sharedProseMirrorSelection(panel) {
  const rich = panel._pmView;
  const coordinates = sharedProseMirrorCoordinates(panel);
  const map = position => mapSharedProseMirrorPosition(coordinates, position, 'rich');
  const head = map(rich.state.selection.head), anchor = map(rich.state.selection.anchor);
  return {anchor: head.exact && anchor.exact ? anchor.offset : head.offset, head: head.offset,
    focused: rich.hasFocus(), surface: 'view', approximate: !head.exact};
}

const splitCodeMirrorCarets = new WeakMap();

function createSplitEditorCaret() {
  const marker = document.createElement('span');
  marker.className = 'split-editor-caret';
  marker.setAttribute('aria-hidden', 'true');
  return marker;
}

function paintSplitCodeMirrorGuide(view, api, range, revealPanel = null) {
  if (!view) return;
  let layer = splitCodeMirrorCarets.get(view);
  if (!layer) {
    if (!range) return;
    const effect = api.StateEffect.define();
    const field = api.StateField.define({
      create: () => null,
      update(previous, transaction) {
        const update = transaction.effects.find(item => item.is(effect));
        return update ? update.value : transaction.docChanged ? null : previous;
      },
    });
    const overlay = api.layer({
      above: true,
      update: update => update.startState.field(field) !== update.state.field(field),
      markers(view) {
        const range = view.state.field(field);
        if (!range) return [];
        const marker = range.empty ? 'split-editor-caret split-codemirror-caret' : 'split-editor-selection';
        return api.RectangleMarker.forRange(view, marker, range);
      },
    });
    layer = {effect, field};
    splitCodeMirrorCarets.set(view, layer);
    view.dispatch({effects: api.StateEffect.appendConfig.of([field, overlay])});
  }
  const next = range ? (range.head === range.from
    ? api.EditorSelection.range(range.to, range.from) : api.EditorSelection.range(range.from, range.to)) : null;
  const previous = view.state.field(layer.field);
  if (next ? !previous?.eq(next) : previous) {
    const effects = [layer.effect.of(next)];
    if (revealPanel && next) {
      setFileEditorScrollSyncGuardForSource('preview', revealPanel);
      const scroller = view.scrollDOM;
      const yMargin = Number.isFinite(range.clientTop) && elementCanScroll(scroller)
        ? Math.min(Math.max(0, scroller.clientHeight - view.defaultLineHeight), Math.max(0, range.clientTop - scroller.getBoundingClientRect().top))
        : null;
      effects.push(api.EditorView.scrollIntoView(next.head, yMargin === null ? undefined : {y: 'start', yMargin}));
    }
    view.dispatch({effects});
  }
}

function* projectMarkdownSplitSelection(coordinates, selection, from) {
  if (!coordinates) return;
  const to = from === 'rich' ? 'source' : 'rich';
  for (const span of coordinates.spans) {
    const start = Math.max(selection.from, span[from]), end = Math.min(selection.to, span[from] + span.length);
    if (start < end) yield {from: span[to] + start - span[from], to: span[to] + end - span[from]};
  }
  for (const atom of coordinates.atoms) {
    if (selection.from >= atom[from + 'End'] || selection.to <= atom[from]) continue;
    const node = coordinates.rich.nodeAt(atom.rich);
    if (node && prosemirrorHiddenComment(node)) continue;
    yield {from: atom[to], to: atom[to + 'End'], node: Boolean(node?.isAtom && !node.isText)};
  }
}

function scheduleSplitEditorCaret(panel, revealCaret = false) {
  if (revealCaret && panel._cmView?.hasFocus) panel._splitCaretRevealSelection = panel._cmView.state.selection;
  if ((!panel._pmView && !splitCodeMirrorCarets.has(panel._cmView) && !htmlEditorPreviewFrame(panel)) || panel._splitCaretFrame) return;
  panel._splitCaretFrame = requestAnimationFrame(() => {
    panel._splitCaretFrame = null;
    const frame = htmlEditorPreviewFrame(panel);
    const text = panel._cmView, rich = panel._pmView, layer = panel._pmSplitCaret;
    const selection = panel._splitCaretRevealSelection;
    panel._splitCaretRevealSelection = null;
    if (frame) {
      if (fileEditorPanelMode(panel) === 'split') paintHtmlSplitCaret(panel, frame, !!selection && selection === text?.state.selection);
      paintSharedHtmlPresence(panel, frame);
    }
    const textFocused = text?.hasFocus;
    const richFocused = rich?.hasFocus();
    let source = null, rendered = -1, renderedSelection = null;
    const htmlGuide = frame && rememberSharedHtmlSelection(panel, frame);
    if (panel.isConnected && text && rich?.editable && fileEditorPanelMode(panel) === 'split') {
      if (textFocused) {
        const coordinates = sharedProseMirrorCoordinates(panel);
        const range = text.state.selection.main;
        const head = prosemirrorCaretDestination(rich, mapSharedProseMirrorPosition(coordinates, range.head, 'source').offset);
        if (range.empty) rendered = head;
        else renderedSelection = projectMarkdownSplitSelection(coordinates, range, 'source');
        if (selection === text.state.selection && head >= 0) revealProseMirrorSplitCaret(panel, rich, text, range, head);
      } else if (richFocused) {
        const coordinates = sharedProseMirrorCoordinates(panel), range = rich.state.selection;
        if (range.empty) {
          const {offset} = mapSharedProseMirrorPosition(coordinates, range.head, 'rich');
          if (offset >= 0) source = {from: offset, to: offset};
        } else {
          for (const {from, to} of projectMarkdownSplitSelection(coordinates, range, 'rich')) {
            source = source ? {from: Math.min(from, source.from), to: Math.max(to, source.to)} : {from, to};
          }
        }
        // Like an HTML page caret, the ViewEdit caret's height is where TextEdit lines up its own.
        if (source) source.clientTop = rich.coordsAtPos(range.head).top;
      }
    }
    const htmlSplitGuide = fileEditorPanelMode(panel) === 'split' ? htmlGuide : null;
    const guide = htmlSplitGuide || source;
    paintSplitCodeMirrorGuide(text, panel._cmApi, textFocused ? null : guide, guide ? panel : null);
    if (!layer || richFocused) return;
    const api = window.YOmuxProseMirror;
    const marks = rendered >= 0
      ? [api.Decoration.widget(rendered, createSplitEditorCaret, {key: 'split-editor-caret', side: 1})]
      : Array.from(renderedSelection || [], ({from, to, node}) => {
        const attrs = {class: node ? 'split-editor-selection split-editor-selection-atom' : 'split-editor-selection'};
        return node ? api.Decoration.node(from, to, attrs) : api.Decoration.inline(from, to, attrs);
      });
    const decorations = api.DecorationSet.create(rich.state.doc, marks);
    if (layer.getState(rich.state).eq(decorations)) return;
    rich.updateState(rich.state.apply(rich.state.tr.setMeta(layer, decorations)));
  });
}

// The ViewEdit half of a TextEdit click: scroll ViewEdit so the rendered caret is level with the
// TextEdit caret, as the HTML page does.
function revealProseMirrorSplitCaret(panel, rich, text, range, head) {
  const scroller = fileEditorSourceElement(panel, 'preview');
  const source = text.coordsAtPos(range.head, range.head === range.from ? 1 : -1);
  if (!scroller || !source) return;
  const caret = rich.coordsAtPos(head), viewport = scroller.getBoundingClientRect();
  const targetTop = Math.max(0, Math.min(viewport.height - (caret.bottom - caret.top), source.top - viewport.top));
  revealSplitCaret(panel, scroller, caret.top - viewport.top, targetTop);
}

function prosemirrorCaretDestination(view, offset) {
  if (offset < 0) return -1;
  const api = window.YOmuxProseMirror;
  const point = view.state.doc.resolve(offset);
  return point.parent.inlineContent ? point.pos :
    ((api.Selection.findFrom(point, 1, true) || api.Selection.findFrom(point, -1, true))?.head ?? -1);
}

function paintSharedProseMirrorPresence(panel, peers) {
  const rich = panel._pmView, plugin = panel._pmSharePresence;
  if (!rich || !plugin) return;
  const api = window.YOmuxProseMirror;
  const coordinates = peers.length ? sharedProseMirrorCoordinates(panel) : null;
  panel._pmSharePeers = peers.length && !coordinates ? {doc: panel._cmView?.state.doc, peers} : null;
  const map = position => mapSharedProseMirrorPosition(coordinates, position, 'source');
  const decorations = [];
  for (const peer of peers) {
    const anchor = map(peer.anchor), destination = map(peer.head);
    const head = prosemirrorCaretDestination(rich, destination.offset);
    if (head < 0) continue;
    const approximate = peer.approximate || !destination.exact || head !== destination.offset;
    const color = shareParticipantStyle(peer);
    if (!peer.follower && !approximate && anchor.exact && anchor.offset !== head) {
      decorations.push(api.Decoration.inline(Math.min(anchor.offset, head), Math.max(anchor.offset, head),
        {class: 'share-remote-selection', style: color}));
    }
    decorations.push(api.Decoration.widget(head, () => {
      const caret = document.createElement('span');
      caret.className = 'share-rich-caret';
      caret.setAttribute('style', color);
      caret.dataset.shareCaretName = peer.name;
      caret.toggleAttribute('data-share-caret-follower', peer.follower === true);
      caret.toggleAttribute('data-share-caret-primary', peer.follower === false);
      if (approximate) caret.dataset.shareCaretApproximate = '';
      caret.setAttribute('aria-hidden', 'true');
      return caret;
    }, {key: `${peer.clientID}:${peer.name}:${color}:${Boolean(approximate)}:${peer.follower}`, side: 1}));
  }
  rich.updateState(rich.state.apply(rich.state.tr.setMeta(plugin, api.DecorationSet.create(rich.state.doc, decorations))));
}

function clearProseMirrorFallback(parts) {
  const fallback = parts?.previewPane?.querySelector('.file-editor-preview-fallback');
  fallback?.remove();
}

function serializeProseMirrorSource(panel) {
  if (!panel?._pmSerializer || !panel._pmView) return null;
  let source = panel._pmSerializer.serialize(panel._pmView.state.doc).replace(
    /^(\s*[-+*]\s+\[[ x]\]\s+)(\d+)\\\.(?=\s)/gm,
    '$1$2.',
  );
  const state = fileEditorPanelState(panel);
  if (state?.content?.endsWith('\n') && !source.endsWith('\n')) source += '\n';
  return source;
}

function refreshProseMirrorSourceLines(panel, source) {
  if (!panel?._pmView || !panel._pmParser || !panel._pmSourceLinePlugin) return;
  const parseEnvironment = {};
  const parsedDoc = panel._pmParser.parse(source, parseEnvironment);
  const metadata = prosemirrorSourceLineMetadata(source, parseEnvironment);
  panel._pmSourceLineMetadata = metadata;
  panel._pmSourceLines = metadata.topLevel;
  panel._pmIgnoredCommentRanges = parseEnvironment.yomuxIgnoredCommentRanges || [];
  const parsedTaskLines = [];
  parsedDoc.descendants(node => {
    if (node.type.name === 'list_item' && node.attrs.taskChecked !== null) {
      parsedTaskLines.push(Number(node.attrs.taskLine || 0));
    }
    return true;
  });
  let taskIndex = 0;
  let transaction = panel._pmView.state.tr;
  panel._pmView.state.doc.descendants((node, position) => {
    if (node.type.name !== 'list_item' || node.attrs.taskChecked === null) return true;
    const taskLine = parsedTaskLines[taskIndex++] || 0;
    if (taskLine !== Number(node.attrs.taskLine || 0)) {
      transaction = transaction.setNodeMarkup(position, undefined, {...node.attrs, taskLine});
    }
    return true;
  });
  transaction.setMeta(panel._pmSourceLinePlugin, metadata);
  transaction.setMeta('yomuxSourceLineRefresh', true);
  transaction.setMeta('addToHistory', false);
  panel._pmView.dispatch(transaction);
}

function commitProseMirrorSource(panel, path, options = {}) {
  if (!panel?._pmView || panel._pmPath !== path) return false;
  // Markdown parsing is intentionally semantic, so serializing a document that has only been
  // viewed can rewrite valid source spelling. Keep the source baseline authoritative until a
  // real ProseMirror document transaction changes the document.
  if (panel._pmDocumentChanged !== true) return true;
  if (panel._pmSourceDocument?.eq(panel._pmView.state.doc)) {
    panel._pmDocumentChanged = false;
    return true;
  }
  let next = serializeProseMirrorSource(panel);
  if (next === null) return false;
  const state = fileEditorPanelState(panel);
  if (!state || state.historical === true) return false;
  if (state.content.endsWith('\n') && !next.endsWith('\n')) next += '\n';
  refreshProseMirrorSourceLines(panel, next);
  if (next === state.content) {
    panel._pmSource = next;
    panel._pmSourceDocument = panel._pmView.state.doc;
    panel._pmDocumentChanged = false;
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
  panel._pmSourceDocument = panel._pmView.state.doc;
  panel._pmDocumentChanged = false;
  return true;
}

function syncCodeMirrorToCanonicalPanels(path, content, sourcePanel = null, sourceSurface = '') {
  for (const linked of fileEditorPanelsForPath(path)) {
    if (fileEditorPanelState(linked)?.historical === true) continue;
    if (linked._cmView && !(linked === sourcePanel && sourceSurface === 'text-editor')) {
      syncCodeMirrorDocument(linked._cmView, content, {path, selection: 'end-before-final-newline'});
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

function flushProseMirrorSource(panel, path, options = {}) {
  if (!panel?._pmView || panel._pmPath !== path) return false;
  if (panel._pmSerializeTimer) clearTimeout(panel._pmSerializeTimer);
  panel._pmSerializeTimer = null;
  panel._pmSerializeGeneration = Number(panel._pmSerializeGeneration || 0) + 1;
  if (panel._pmAutosaveTimer) clearTimeout(panel._pmAutosaveTimer);
  panel._pmAutosaveTimer = null;
  return commitProseMirrorSource(panel, path, options);
}

function prosemirrorTableCell(node, schema) {
  return node.type === schema.nodes.table_cell || node.type === schema.nodes.table_header;
}

function moveProseMirrorTableRow(api, direction) {
  return (state, dispatch) => {
    const {$head} = state.selection;
    const tableDepth = $head.depth - 2;
    if (tableDepth < 0 || !prosemirrorTableCell($head.parent, state.schema) ||
        $head.node(tableDepth).type !== state.schema.nodes.table) return false;
    const table = $head.node(tableDepth);
    const rowIndex = $head.index(tableDepth) + direction;
    const column = $head.index(tableDepth + 1);
    if (rowIndex < 0 || rowIndex >= table.childCount || column >= table.child(rowIndex).childCount) return true;
    const row = state.doc.resolve($head.posAtIndex(rowIndex, tableDepth) + 1);
    const cell = row.parent.child(column);
    const position = row.posAtIndex(column) + 1 + Math.min($head.parentOffset, cell.content.size);
    if (dispatch) dispatch(state.tr.setSelection(api.TextSelection.create(state.doc, position)).scrollIntoView());
    return true;
  };
}

function replaceProseMirrorProtectedSelection(api, state, from, to) {
  if (from === to) return null;
  const $from = state.doc.resolve(from), $to = state.doc.resolve(to);
  let protect = !$from.sameParent($to) &&
    (prosemirrorTableCell($from.parent, state.schema) || prosemirrorTableCell($to.parent, state.schema));
  const ranges = [];
  state.doc.nodesBetween(from, to, (node, position) => {
    if (prosemirrorHiddenComment(node)) protect = true;
    if (!node.isTextblock) return;
    let start = Math.max(from, position + 1);
    const end = Math.min(to, position + node.nodeSize - 1);
    node.forEach((child, offset) => {
      const next = position + 1 + offset;
      if (!prosemirrorHiddenComment(child) || next >= end || next + child.nodeSize <= start) return;
      protect = true;
      if (start < next) ranges.push([start, next]);
      start = next + child.nodeSize;
    });
    if (start < end) ranges.push([start, end]);
    return false;
  });
  if (!protect) return null;
  const tr = state.tr;
  for (const [start, end] of ranges.reverse()) tr.delete(start, end);
  return tr.setSelection(api.Selection.near(tr.doc.resolve(tr.mapping.map(from))));
}

function replaceProseMirrorRangeWithBreak(api, command, state, dispatch, view) {
  const tr = replaceProseMirrorProtectedSelection(api, state, state.selection.from, state.selection.to);
  if (!tr) return false;
  if (dispatch) {
    const interim = api.EditorState.create({doc: tr.doc, selection: tr.selection});
    const handled = command(interim, next => {
      for (const step of next.steps) tr.step(step);
      tr.setSelection(api.Selection.fromJSON(tr.doc, next.selection.toJSON())).setStoredMarks(next.storedMarks);
    }, view);
    if (handled || tr.docChanged) dispatch(tr.scrollIntoView());
  }
  return true;
}

function insertProseMirrorHardBreak(api, schema) {
  return function hardBreak(state, dispatch, view) {
    const {$from, $to} = state.selection;
    if (!$from.sameParent($to) && (prosemirrorTableCell($from.parent, schema) || prosemirrorTableCell($to.parent, schema))) return true;
    if (replaceProseMirrorRangeWithBreak(api, hardBreak, state, dispatch, view)) return true;
    if (!$from.sameParent($to)) return false;
    if (!$from.parent.isTextblock) return false;
    if ($from.parent.type.spec.code) return api.baseKeymap.Enter(state, dispatch, view);
    const type = schema.nodes.hard_break;
    if (!type) return false;
    if (dispatch) {
      const marks = state.storedMarks || $from.marks();
      dispatch(state.tr.replaceSelectionWith(type.create(), false).ensureMarks(marks).scrollIntoView());
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
  return function enter(state, dispatch, view) {
    const {$from, $to} = state.selection;
    // Splitting an inline-content table cell adds a column; GFM drops surplus cells on reopen.
    if (prosemirrorTableCell($from.parent, schema) || prosemirrorTableCell($to.parent, schema)) {
      if ($from.sameParent($to)) insertProseMirrorHardBreak(api, schema)(state, dispatch, view);
      return true;
    }
    if (replaceProseMirrorRangeWithBreak(api, enter, state, dispatch, view)) return true;
    const itemDepth = markdownListItemDepth($from);
    if (state.selection.empty && itemDepth && $from.depth === itemDepth + 1) {
      const item = $from.node(itemDepth);
      if (prosemirrorEmptyListItem(item)) {
        const listDepth = itemDepth - 1;
        const following = state.doc.resolve($from.after(listDepth)).nodeAfter;
        const continues = $from.index(listDepth) + 1 < $from.node(listDepth).childCount ||
          following?.type.name === 'bullet_list' || following?.type.name === 'ordered_list';
        if (continues) {
          if (dispatch) {
            const at = $from.after(itemDepth);
            const blank = item.type.create({...item.attrs, taskLine: 0, taskChecked: item.attrs.taskChecked === null ? null : false}, item.content);
            const tr = state.tr.insert(at, blank);
            tr.setSelection(api.TextSelection.create(tr.doc, at + 2));
            dispatch(tr.scrollIntoView());
          }
          return true;
        }
      }
    }
    const checklist = itemDepth && $from.node(itemDepth).attrs.taskChecked !== null;
    const splitDispatch = dispatch && (transaction => {
      const position = transaction.selection.$from;
      const depth = markdownListItemDepth(position);
      if (depth) {
        const item = position.node(depth);
        transaction.setNodeMarkup(position.before(depth), null, {...item.attrs, taskLine: 0, taskChecked: checklist ? false : item.attrs.taskChecked});
      }
      dispatch(transaction);
    });
    if (!$from.parent.type.spec.code && api.splitListItem(schema.nodes.list_item)(state, splitDispatch, view)) return true;
    return api.baseKeymap.Enter(state, dispatch, view);
  };
}

function deleteProseMirrorBoundary(api, direction) {
  return (state, dispatch) => {
    const {empty, $from, $to} = state.selection;
    if (!empty) {
      const tr = replaceProseMirrorProtectedSelection(api, state, $from.pos, $to.pos);
      if (!tr) return false;
      dispatch?.(tr.scrollIntoView());
      return true;
    }
    let point = $from;
    while ((direction < 0 ? point.nodeBefore : point.nodeAfter)?.type.name === 'markdown_comment_inline') {
      point = state.doc.resolve(point.pos + direction);
    }
    if (point !== $from) {
      dispatch?.(state.tr.setSelection(api.TextSelection.create(state.doc, point.pos)).scrollIntoView());
      return true;
    }
    if (!$from.parent.isTextblock || $from.parentOffset !== (direction < 0 ? 0 : $from.parent.content.size)) return false;
    if (prosemirrorTableCell($from.parent, state.schema)) return true;
    const adjacent = direction < 0 ? state.doc.resolve($from.before()).nodeBefore : state.doc.resolve($from.after()).nodeAfter;
    if (adjacent?.type.name === 'markdown_comment_block') {
      const target = $from.pos + direction * (adjacent.nodeSize + 1);
      if (target >= 0 && target <= state.doc.content.size) {
        dispatch?.(state.tr.setSelection(api.Selection.near(state.doc.resolve(target), direction)).scrollIntoView());
      }
      return true;
    }
    const depth = markdownListItemDepth($from);
    if (!depth || $from.depth !== depth + 1) return false;
    const list = $from.node(depth - 1);
    const index = $from.index(depth - 1);
    const item = list.child(index);
    let from = $from.before(depth), to = $from.after(depth);
    const blank = prosemirrorEmptyListItem(item);
    if (!blank) {
      const neighbor = list.maybeChild(index + direction);
      const edge = direction < 0 ? $from.index(depth) === 0 : $from.index(depth) === item.childCount - 1;
      if (!edge || !neighbor) return false;
      if (!prosemirrorEmptyListItem(neighbor)) {
        const other = direction < 0 ? neighbor.lastChild : neighbor.firstChild;
        if ($from.parent.type.name !== 'paragraph' || other?.type !== $from.parent.type) return false;
        const boundary = direction < 0 ? from : to;
        dispatch?.(state.tr.delete(boundary - 2, boundary + 2).scrollIntoView());
        return true;
      }
      if (direction < 0) { to = from; from -= neighbor.nodeSize; }
      else { from = to; to += neighbor.nodeSize; }
    } else if (list.childCount === 1) {
      return api.liftListItem(item.type)(state, dispatch);
    }
    if (dispatch) {
      const tr = state.tr.delete(from, to);
      if (blank) {
        tr.setSelection(api.Selection.near(tr.doc.resolve(from), direction));
      }
      dispatch(tr.scrollIntoView());
    }
    return true;
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
    block: domElement?.closest?.('p,h1,h2,h3,h4,h5,h6,td,th') || null,
  };
}

function prosemirrorSelectionAtClientPoint(view, event, cell = null) {
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
  const cellStart = cell ? view.posAtDOM(cell, 0) : null;
  if (!selection.empty && (!cell || (selection.$from.start() === cellStart && selection.$to.start() === cellStart))) {
    return prosemirrorSelectionContext(view);
  }
  const point = view.posAtCoords({left: event.clientX, top: event.clientY});
  if (!point && !cell) return prosemirrorSelectionContext(view);
  const clicked = point && view.state.doc.resolve(point.pos);
  const $from = cell && clicked?.start() !== cellStart ? view.state.doc.resolve(cellStart) : clicked;
  const text = $from.parent.textContent || '';
  if (!text) {
    if (cell) setProseMirrorSelection(view, $from.pos, $from.pos);
    return prosemirrorSelectionContext(view);
  }
  const offset = Math.max(0, Math.min($from.parentOffset, text.length));
  let start = offset;
  let end = offset;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const base = $from.start();
  setProseMirrorSelection(view, base + start, base + end);
  return prosemirrorSelectionContext(view);
}

function applyProseMirrorFormat(api, view, schema, command) {
  const panel = view.dom.closest('.file-editor-panel');
  if (!markdownFormattingWritable(panel, 'view-editor')) return false;
  const entry = markdownFormatCommands.find(item => item.id === command);
  if (!entry) return false;
  const {selection} = view.state;
  if (entry.mark) return api.toggleMark(schema.marks[entry.mark], null, {removeWhenPresent: false})(view.state, view.dispatch, view);
  if (command === 'clearInline') {
    const transaction = view.state.tr;
    setProseMirrorTextBlock(api, transaction, schema, schema.nodes.paragraph);
    // Selection and block changes reset stored marks, so clear typing styles after both.
    for (const mark of Object.values(schema.marks)) {
      if (mark.name === 'link') continue;
      if (transaction.selection.empty) transaction.removeStoredMark(mark);
      else transaction.removeMark(transaction.selection.from, transaction.selection.to, mark);
    }
    view.dispatch(transaction);
    return true;
  }
  if (entry.group === 'listIndent') return applyProseMirrorListIndent(api, view, schema, command);
  if (entry.group === 'list') return applyProseMirrorListFormat(api, view, schema, command);
  if (command === 'pre') return applyProseMirrorPreformatted(view, schema, prosemirrorMarkdownSerializer(api));
  const type = command === 'normal' ? schema.nodes.paragraph : schema.nodes.heading;
  const transaction = view.state.tr;
  setProseMirrorTextBlock(api, transaction, schema, type, /^h[1-6]$/.test(command) ? {level: Number(command.slice(1))} : null);
  view.dispatch(transaction);
  return true;
}

function setProseMirrorTextBlock(api, transaction, schema, type, attrs = null) {
  const {selection} = transaction;
  const codeBlocks = [];
  transaction.doc.nodesBetween(selection.from, selection.to, (node, position) => {
    if (node.type === schema.nodes.code_block) codeBlocks.push({node, position});
  });
  // Paragraph boundaries retain literal lines, including trailing empty lines, through commit.
  for (const {node, position} of codeBlocks.slice().reverse()) {
    const paragraphs = node.textContent.split('\n').map(text => schema.nodes.paragraph.create(null, text ? schema.text(text) : null));
    transaction.replaceWith(position, position + node.nodeSize, paragraphs);
  }
  const mapPosition = position => {
    const block = codeBlocks.find(block => position > block.position && position < block.position + block.node.nodeSize);
    if (!block) return transaction.mapping.map(position);
    const offset = position - block.position - 1;
    return transaction.mapping.map(block.position, -1) + 1 + offset + (block.node.textContent.slice(0, offset).match(/\n/g) || []).length;
  };
  transaction.setSelection(api.TextSelection.create(transaction.doc, mapPosition(selection.anchor), mapPosition(selection.head)));
  const from = codeBlocks.length ? Math.min(transaction.selection.from, transaction.mapping.map(codeBlocks[0].position, -1)) : transaction.selection.from;
  const last = codeBlocks.at(-1);
  const to = last ? Math.max(transaction.selection.to, transaction.mapping.map(last.position + last.node.nodeSize, 1)) : transaction.selection.to;
  transaction.setBlockType(from, to, type, attrs);
}

function applyProseMirrorPreformatted(view, schema, serializer) {
  const {selection} = view.state;
  const blocks = [];
  let unsupportedBlock = false;
  if (selection.empty) {
    const {$from} = selection;
    if ($from.parent.isTextblock) {
      const parent = $from.node($from.depth - 1);
      const index = $from.index($from.depth - 1);
      if (parent.canReplaceWith(index, index + 1, schema.nodes.code_block)) {
        blocks.push({node: $from.parent, from: $from.before($from.depth), parent, index});
      }
    }
  } else {
    view.state.doc.nodesBetween(selection.from, selection.to, (node, position, parent, index) => {
      if (!node.isTextblock) return;
      if (parent.canReplaceWith(index, index + 1, schema.nodes.code_block)) blocks.push({node, from: position, parent, index});
      else unsupportedBlock = true;
    });
  }
  if (!blocks.length || unsupportedBlock) return false;
  const groups = [];
  for (const block of blocks) {
    const previous = groups.at(-1);
    if (previous && previous.parent === block.parent && previous.last.index + 1 === block.index) {
      previous.last = block;
      previous.blocks.push(block);
    } else groups.push({parent: block.parent, first: block, last: block, blocks: [block]});
  }
  const transaction = view.state.tr;
  const replacements = [];
  for (const group of groups) {
    const text = group.blocks.map(({node}) => {
      if (node.type === schema.nodes.code_block) return node.textBetween(0, node.content.size);
      const paragraph = schema.nodes.paragraph.create(null, node.content);
      return serializer.serialize(schema.topNodeType.create(null, paragraph)).replace(/\n+$/, '');
    }).join('\n');
    const content = text ? schema.text(text) : null;
    const attrs = group.blocks.length === 1 && group.first.node.type === schema.nodes.code_block ? group.first.node.attrs : null;
    const codeBlock = schema.nodes.code_block.create(attrs, content);
    const to = group.last.from + group.last.node.nodeSize;
    replacements.push({from: group.first.from, to, text, size: codeBlock.nodeSize, codeBlock});
  }
  for (const replacement of replacements.slice().reverse()) {
    transaction.replaceWith(replacement.from, replacement.to, replacement.codeBlock);
  }
  const lastReplacement = replacements.at(-1);
  const precedingDelta = replacements.slice(0, -1).reduce((delta, replacement) => delta + replacement.size - (replacement.to - replacement.from), 0);
  const caret = lastReplacement.from + precedingDelta + 1 + lastReplacement.text.length;
  transaction.setSelection(view.state.selection.constructor.near(transaction.doc.resolve(caret), -1));
  view.dispatch(transaction);
  return true;
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

function prosemirrorLinkHoverPlugin(api) {
  return new api.Plugin({
    view(view) {
      const hover = installPreviewLinkHover(view.dom, {
        resolve: link => previewLinkDestination(link, view.dom.dataset.mdPath),
        isCurrent: () => view.editable && Boolean(view.dom.getClientRects().length),
        clientPoint: event => ({x: event.clientX, y: event.clientY}),
      });
      return {
        update(current, previous) {
          if (current.state.doc !== previous.doc || !current.editable) hover.close();
        },
        destroy: hover.destroy,
      };
    },
  });
}

function editProseMirrorTable(panel, view, cell, {axis, offset, align}) {
  if (panel._pmView !== view || !markdownFormattingWritable(panel, 'view-editor') || !view.dom.contains(cell)) return;
  const {state} = view;
  const {table, table_row, table_header, table_cell, paragraph} = state.schema.nodes;
  const position = state.doc.resolve(view.posAtDOM(cell, 0));
  const depth = position.depth;
  if (!prosemirrorTableCell(position.parent, state.schema) || depth < 3 ||
      position.node(depth - 2).type !== table) return;
  const original = position.node(depth - 2);
  const start = position.before(depth - 2);
  const row = position.index(depth - 2);
  const column = position.index(depth - 1);
  if (align !== undefined) {
    const transaction = state.tr;
    // GFM stores alignment in the delimiter row and applies it to the entire column.
    original.forEach((entry, rowOffset) => entry.forEach((node, cellOffset, index) => {
      if (index === column && node.attrs.align !== align) {
        transaction.setNodeMarkup(start + rowOffset + cellOffset + 2, null, {...node.attrs, align});
      }
    }));
    if (transaction.docChanged) view.dispatch(transaction);
    view.focus();
    return;
  }
  const rows = [];
  original.forEach(entry => {
    const cells = [];
    entry.forEach(node => cells.push(node));
    rows.push(cells);
  });
  const alignments = rows[0].map(node => node.attrs.align);
  let nextRow = row, nextColumn = column;
  if (axis === 'row') {
    if (offset) rows.splice(nextRow = row + (offset > 0 ? 1 : 0), 0,
      alignments.map(align => table_cell.create({align})));
    else rows.splice(row, 1);
  } else {
    if (offset) {
      nextColumn = column + (offset > 0 ? 1 : 0);
      for (const cells of rows) cells.splice(nextColumn, 0, table_cell.create());
    } else {
      for (const cells of rows) cells.splice(column, 1);
    }
  }
  const remaining = rows.length && rows[0].length;
  const result = remaining ? table.create(original.attrs, rows.map((cells, index) => {
    const type = index === 0 ? table_header : table_cell;
    return table_row.create(null, cells.map(node =>
      node.type === type ? node : type.create(node.attrs, node.content, node.marks)));
  })) : paragraph.create();
  const transaction = state.tr.replaceWith(start, start + original.nodeSize, result);
  let caret = start + 1;
  if (remaining) {
    nextRow = Math.min(nextRow, result.childCount - 1);
    nextColumn = Math.min(nextColumn, result.child(nextRow).childCount - 1);
    result.forEach((node, rowOffset, index) => {
      if (index !== nextRow) return;
      node.forEach((_cell, cellOffset, index) => {
        if (index === nextColumn) caret = start + rowOffset + cellOffset + 3;
      });
    });
  }
  transaction.setSelection(window.YOmuxProseMirror.TextSelection.create(transaction.doc, caret));
  view.dispatch(transaction.scrollIntoView());
  view.focus();
}

function installProseMirrorInteractions(panel, path, view, schema, api) {
  view.dom.addEventListener('focus', () => clearLinkedCodeMirrorSelection(panel, path));
  view.dom.addEventListener('blur', () => flushProseMirrorSource(panel, path));
  const onContextMenu = event => {
    const cell = event.target?.closest?.('td,th');
    const canFormat = markdownFormattingWritable(panel, 'view-editor');
    const table = cell && view.dom.contains(cell) && canFormat
      ? {align: cell.style.textAlign || null, edit: change => editProseMirrorTable(panel, view, cell, change)}
      : null;
    const image = event.target?.closest?.('img');
    if (!table && image && view.dom.contains(image)) {
      event.preventDefault();
      event.stopPropagation();
      showImageContextMenu(image, event.clientX, event.clientY);
      return;
    }
    const link = event.target?.closest?.('a[href]');
    const context = prosemirrorSelectionAtClientPoint(view, event, table ? cell : null);
    if (!context.block && !table) return;
    event.preventDefault();
    event.stopPropagation();
    markdownFormattingContextMenu(event, context, {
      href: link && view.dom.contains(link) ? link.getAttribute('href') || '' : '',
      copyHref: link && view.dom.contains(link) ? link.href : '',
      markdownPath: path,
      surface: 'view-editor',
      image: table ? image : null,
      table,
      modifyUrl: link && view.dom.contains(link) ? () => modifyMarkdownLinkUrl(view, link) : null,
      removeUrl: link && view.dom.contains(link) ? () => removeMarkdownLinkUrl(view, link) : null,
      addUrl: !link && context.selectedText ? () => addMarkdownLinkUrl(view) : null,
      applyCommand: command => dispatchMarkdownFormat(panel, command, 'view-editor'),
      canFormat,
      formatState: command => markdownFormattingState(panel, markdownFormatCommands.find(entry => entry.id === command), 'view-editor'),

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

function restartProseMirrorCursorBlink(view, composing = view.composing) {
  for (const animation of view.dom.getAnimations()) {
    if (animation.animationName !== 'editor-native-cursor-blink') continue;
    animation.currentTime = 0;
    if (composing) animation.pause();
    else animation.play();
  }
}

function prosemirrorCaretPlugin(api) {
  const plugin = new api.Plugin({
    state: {
      init: () => api.DecorationSet.empty,
      apply: (transaction, previous) => transaction.getMeta(plugin) || previous.map(transaction.mapping, transaction.doc),
    },
    props: {decorations: state => plugin.getState(state)},
  });
  return plugin;
}

function createProseMirrorPanel(panel, item, path, state, parts, api) {
  if (!parts?.previewPane) return false;
  panel._pmMediaPromises = [];
  installProseMirrorContextMenuGuard();
  const schema = prosemirrorMarkdownSchema(api);
  const parser = prosemirrorMarkdownParser(api, schema);
  const serializer = prosemirrorMarkdownSerializer(api);
  const parseEnvironment = {};
  const doc = parser.parse(state.content || '', parseEnvironment);
  panel._pmSourceLineMetadata = prosemirrorSourceLineMetadata(state.content || '', parseEnvironment);
  const container = document.createElement('div');
  container.className = 'prosemirror-editor markdown-body';
  container.dataset.mdPath = path;
  applyProseMirrorPreviewDisplayMode(container);
  container.setAttribute('data-prosemirror-editor', 'true');
  const sourceLinePlugin = prosemirrorSourceLinePlugin(api, panel);
  const sharePresence = prosemirrorCaretPlugin(api);
  const splitCaret = prosemirrorCaretPlugin(api);
  const plugins = [
    api.keymap({
      'Mod-s': () => {
        if (!fileEditorPanelState(panel)?._shareActive) flushProseMirrorSource(panel, path);
        void saveFileEditor(path, panel);
        return true;
      },
      'Shift-Enter': insertProseMirrorHardBreak(api, schema),
      Enter: insertProseMirrorEnter(api, schema),
      Backspace: deleteProseMirrorBoundary(api, -1),
      Delete: deleteProseMirrorBoundary(api, 1),
      ArrowUp: moveProseMirrorTableRow(api, -1),
      ArrowDown: moveProseMirrorTableRow(api, 1),
    }),
    api.history(),
    api.keymap({'Mod-z': api.undo, 'Shift-Mod-z': api.redo, 'Mod-y': api.redo}),
    api.keymap(api.baseKeymap),
    sourceLinePlugin,
    prosemirrorImageFitPlugin(api),
    prosemirrorLinkHoverPlugin(api),
    prosemirrorTaskAccessPlugin(api),
    sharePresence,
    splitCaret,
  ];
  const editorState = api.EditorState.create({doc, plugins});
  if (panel.dataset.filePath !== path || !['preview', 'split'].includes(editorViewModeFor(path, item))) {
    return false;
  }
  if (panel._pmView) {
    panel._pmContextMenuDispose?.();
    panel._pmView.destroy();
  }
  cleanupStandardPreviewStrategy(parts.previewPane);
  disposeMarkdownPreviewEditing(parts.previewPane);
  parts.previewPane._previewRendererId = null;
  parts.previewPane.replaceChildren(container);
  clearProseMirrorFallback(parts);
  const view = new api.EditorView(container, {
    state: editorState,
    editable: () => prosemirrorPanelEditable(panel),
    handleTextInput(view, from, to, text) {
      const tr = replaceProseMirrorProtectedSelection(api, view.state, from, to);
      if (!tr) return false;
      view.dispatch(tr.insertText(text).scrollIntoView());
      return true;
    },
    attributes: {
      class: prosemirrorPreviewClassName(),
      'data-md-path': path,
      'data-prosemirror-editor': 'true',
    },
    nodeViews: {
      // Mermaid fences render as isolated images; ViewEdit never exposes the diagram source as a
      // contentDOM, so changing it remains a TextEdit operation.
      code_block: node => prosemirrorCodeBlockNodeView(node, panel, path),
      tex_math: (node, _view, getPos) => prosemirrorTexMathNodeView(node, panel, getPos),
      tex_math_block: (node, _view, getPos) => prosemirrorTexMathNodeView(node, panel, getPos),
      details: prosemirrorDetailsNodeView,
      image: (node, _view, getPos) => prosemirrorImageNodeView(node, panel, path, getPos),
      list_item: prosemirrorTaskListItemNodeView,
    },
    handleDOMEvents: {
      focus() {
        panel._shareCaretSurface = 'view';
        const current = fileEditorPanelState(panel);
        if (current?._shareActive) syncProseMirrorPanelSource(panel, path, current);
        scheduleSplitEditorCaret(panel);
        return false;
      },
      blur() {
        scheduleSplitEditorCaret(panel);
        return false;
      },
      compositionstart(view) { restartProseMirrorCursorBlink(view, true); return false; },
      compositionend(view) { restartProseMirrorCursorBlink(view, false); return false; },
    },
    dispatchTransaction(transaction) {
      if (transaction.docChanged && !markdownFormattingWritable(panel, 'view-editor')) return;
      if (transaction.docChanged || (transaction.selectionSet && !transaction.selection.eq(view.state.selection))) {
        clearProseMirrorSourceSelection(panel);
      }
      if (transaction.docChanged && fileEditorPanelState(panel)?._shareActive) {
        applySharedProseMirrorTransaction(panel, path, view, transaction);
        return;
      }
      const nextState = view.state.apply(transaction);
      view.updateState(nextState);
      if (transaction.docChanged || transaction.selectionSet) {
        restartProseMirrorCursorBlink(view);
        scheduleSplitEditorCaret(panel);
      }
      refreshMarkdownFormattingToolbar(panel);
      if (transaction.docChanged && transaction.getMeta('yomuxSourceLineRefresh') !== true) {
        panel._pmSource = null;
        panel._pmDocumentChanged = true;
        updateProseMirrorSource(panel, path);
        scheduleProseMirrorAutosave(panel, path);
      }
    },
  });
  panel._pmView = view;
  hydrateProseMirrorExternalImages(panel, path);
  parts.previewPane._previewAsync = Promise.all(panel._pmMediaPromises || []);
  container._prosemirrorView = view;
  panel._pmPreviewPane = parts.previewPane;
  panel._pmPath = path;
  panel._pmSchema = schema;
  panel._pmParser = parser;
  panel._pmSerializer = serializer;
  panel._pmPlugins = plugins;
  panel._pmSourceLinePlugin = sourceLinePlugin;
  panel._pmSharePresence = sharePresence;
  panel._pmSplitCaret = splitCaret;
  panel._pmSource = state.content || '';
  panel._pmSourceDocument = doc;
  panel._pmDocumentChanged = false;
  panel._pmSourceLines = panel._pmSourceLineMetadata.topLevel;
  panel._pmIgnoredCommentRanges = parseEnvironment.yomuxIgnoredCommentRanges || [];
  restoreProseMirrorPanelViewState(item, panel);
  delete panel._pmError;
  delete panel._pmFailureSource;
  parts.previewPane.dataset.prosemirrorState = 'ready';
  installProseMirrorInteractions(panel, path, view, schema, api);
  ensureMarkdownFormattingToolbar(panel);
  panel._shareConnection?.refreshPresence();
  scheduleSplitEditorCaret(panel);
  return true;
}

async function ensureProseMirrorPanel(panel, item, path, state, parts, ensureGeneration) {
  if (!prosemirrorSupportedSource(path, state)) return false;
  if (panel._pmView && panel._pmPath === path) {
    restoreProseMirrorPanelDom(panel, parts);
    return syncProseMirrorPanelSource(panel, path, state);
  }
  destroyProseMirrorPanel(panel, {invalidateEnsure: false});
  try {
    const api = await loadProseMirrorApi();
    if (!prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration)) return false;
    try {
      const ready = createProseMirrorPanel(panel, item, path, state, parts, api);
      if (ready) {
        delete panel._pmUnsupportedSource;
        delete panel._pmError;
      }
      return ready;
    } catch (error) {
      if (prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration)) {
        renderProseMirrorFailure(panel, path, parts, error);
      }
      return false;
    }
  } catch (error) {
    if (prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration)) {
      renderProseMirrorFailure(panel, path, parts, error);
    }
    return false;
  }
}

function prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration) {
  return ensureGeneration === panel._pmEnsureGeneration
    && panelNodes.get(item) === panel
    && panel.dataset.filePath === path
    && fileEditorPanelState(panel)?.content === state.content
    && ['preview', 'split'].includes(editorViewModeFor(path, item));
}

function renderProseMirrorPreviewMode(panel, item, path, state, parts) {
  if (!prosemirrorSupportedSource(path, state)) return false;
  if (parts.previewPane) parts.previewPane.hidden = false;
  panel._pmRequired = true;
  if (panel._pmError) {
    renderProseMirrorFailure(panel, path, parts, panel._pmError);
    return true;
  }
  const ensureGeneration = Number(panel._pmEnsureGeneration || 0) + 1;
  panel._pmEnsureGeneration = ensureGeneration;
  if (!panel._pmView) {
    renderProseMirrorLoading(parts);
  }
  const ensurePromise = ensureProseMirrorPanel(panel, item, path, state, parts, ensureGeneration);
  parts.previewPane._previewAsync = ensurePromise;
  void ensurePromise.then(ready => {
    if (!ready && prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration)) {
      renderProseMirrorFailure(panel, path, parts, panel._pmError || t('editor.prosemirrorDidNotInitialize'));
    }
    if (prosemirrorPanelInitializationCurrent(panel, item, path, state, ensureGeneration)) {
      focusFileEditorPanelIfReady(panel, item);
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
