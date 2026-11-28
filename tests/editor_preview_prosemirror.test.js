const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'tools/prosemirror-bundle/package.json'), 'utf8'));
const entry = fs.readFileSync(path.join(root, 'tools/prosemirror-bundle/prosemirror-entry.js'), 'utf8');
const adapter = fs.readFileSync(path.join(root, 'static_src/js/yomux/93_prosemirror_editor.js'), 'utf8');
const settingsRuntime = fs.readFileSync(path.join(root, 'static_src/js/yomux/50_editor_settings_runtime.js'), 'utf8');
const changesEditor = fs.readFileSync(path.join(root, 'static_src/js/yomux/86_changes_editor.js'), 'utf8');

assert.equal(manifest.dependencies['prosemirror-markdown'], '1.13.7');
assert.equal(manifest.dependencies['prosemirror-model'], '1.25.11');
assert.equal(manifest.dependencies['prosemirror-history'], '1.5.0');
assert.equal(manifest.dependencies['prosemirror-state'], '1.4.4');
assert.equal(manifest.dependencies['prosemirror-view'], '1.42.3');
assert.match(entry, /window\.YOmuxProseMirror/);
assert.match(entry, /Decoration, DecorationSet, EditorView/);
assert.match(adapter, /function createProseMirrorPanel/);
assert.match(changesEditor, /pmAnchor: Number\(selection\?\.anchor \|\| 0\)/);
assert.match(changesEditor, /pmHead: Number\(selection\?\.head \|\| selection\?\.anchor \|\| 0\)/);
assert.match(changesEditor, /pmScrollTop: pmScrollDOM\.scrollTop/);
assert.doesNotMatch(changesEditor, /pmFocused/);
assert.match(changesEditor, /function restoreProseMirrorPanelViewState/);
assert.match(adapter, /restoreProseMirrorPanelViewState\(item, panel\)/);
assert.match(adapter, /ProseMirror owns every descendant of EditorView\.dom/);
assert.match(adapter, /function syncProseMirrorPanelSource/);
assert.match(adapter, /handleFileEditorContentChanged/);
assert.match(adapter, /MarkdownParser/);
assert.match(adapter, /PROSEMIRROR_SERIALIZE_DELAY_MS = 1250/);
assert.match(adapter, /'Shift-Enter': insertProseMirrorHardBreak\(api, schema\)/);
assert.match(adapter, /Enter: insertProseMirrorEnter/);
assert.match(adapter, /api\.baseKeymap\.Enter/);
assert.match(adapter, /function scheduleProseMirrorAutosave/);
assert.match(adapter, /deferAutosave: true/);
assert.match(adapter, /disposeMarkdownPreviewEditing\(parts\.previewPane\)/);
assert.match(adapter, /setTimeout\(\(\) =>/);
assert.match(adapter, /syncCodeMirrorDocument/);
assert.match(adapter, /TextSelection\.between/);
assert.match(adapter, /soft_break/);
assert.match(adapter, /skipPreviewPanel/);
assert.match(adapter, /flushProseMirrorSource/);
assert.match(adapter, /api\.history\(\)/);
assert.match(adapter, /syncProseMirrorPanelSource[\s\S]*clearTimeout\(panel\._pmSerializeTimer\)/);
assert.match(adapter, /function prosemirrorSelectionForDocument/);
assert.match(adapter, /return api\.TextSelection\.between\(doc\.resolve\(from\), doc\.resolve\(to\)\)/);
assert.match(adapter, /_pmSourceDocument\?\.eq\(panel\._pmView\.state\.doc\)/);
assert.match(adapter, /panel\._pmSourceDocument = doc/);
assert.match(adapter, /_pmDocumentChanged = true/);
assert.match(adapter, /if \(panel\._pmDocumentChanged !== true\) return true/);
assert.match(adapter, /state\?\.content\?\.endsWith\('\\n'\)/);
assert.match(adapter, /sourceSurface: 'view-editor'/);
assert.match(adapter, /prosemirrorSelectionAtClientPoint/);
assert.match(adapter, /closest\?\.\('p,h1,h2,h3,h4,h5,h6,td,th'\)/);
assert.match(adapter, /formatState: command => markdownFormattingState/);
assert.match(fs.readFileSync(path.join(root, 'static_src/js/yomux/94_markdown_formatting.js'), 'utf8'), /mark: 'strike'/);
assert.match(fs.readFileSync(path.join(root, 'static_src/js/yomux/94_markdown_formatting.js'), 'utf8'), /mark: 'underline'/);
assert.match(adapter, /strike: prosemirrorCodeStyleMark/);
assert.match(adapter, /underline: \{open: '<u>'/);
assert.match(adapter, /s: \{mark: 'strike'\}/);
assert.match(adapter, /type: 'code_inline', tag: 'code', nesting: 0, content, markup: ''/);
assert.match(adapter, /sup: 'superscript'/);
assert.match(adapter, /superscript: \{mark: 'superscript'\}/);
assert.match(adapter, /underline: \{mark: 'underline'\}/);
assert.match(adapter, /html_break: \{node: 'hard_break'\}/);
assert.match(adapter, /html_image: \{node: 'image'/);
assert.match(adapter, /details: \{block: 'details'/);
assert.match(adapter, /table: \{block: 'table'\}/);
assert.match(adapter, /tr: \{block: 'table_row'\}/);
assert.match(adapter, /th: \{block: 'table_header'/);
assert.match(adapter, /td: \{block: 'table_cell'/);
assert.match(adapter, /highlight: \{mark: 'highlight'\}/);
assert.match(adapter, /kbd: \{mark: 'kbd'\}/);
assert.match(adapter, /details\(state, node\)/);
assert.match(adapter, /function prosemirrorDetailsNodeView/);
assert.match(adapter, /function prosemirrorImageNodeView/);
assert.match(adapter, /code_block:\s*node => prosemirrorCodeBlockNodeView/);
assert.match(adapter, /if \(!isMermaidFenceLanguage\(language\)\) return null/);
assert.match(adapter, /function prosemirrorMermaidCodeBlockNodeView/);
assert.doesNotMatch(adapter, /scheduleProseMirrorCodeBlockHighlight/);
assert.doesNotMatch(adapter, /applyMarkdownFenceHighlight|refreshMarkdownFenceHighlights/);
assert.doesNotMatch(adapter, /MutationObserver/);
assert.doesNotMatch(adapter, /applyProseMirrorCodeBlockLanguages|attachSourceLines|attachHeadingSourceLines/);
assert.doesNotMatch(adapter, /bindMarkdownPreviewEditing\(parts\.previewPane/);
assert.match(adapter, /toDOM: prosemirrorCodeBlockDom/);
assert.match(adapter, /prosemirrorSourceLinePlugin\(api, panel\)/);
assert.match(adapter, /api\.Decoration\.node/);
assert.match(adapter, /api\.DecorationSet\.create/);
assert.match(adapter, /function applyProseMirrorViewDisplayMode[\s\S]*view\.setProps/);
assert.doesNotMatch(adapter, /applyProseMirrorPreviewDisplayMode\(panel\._pmView\.dom\)|applyProseMirrorPreviewDisplayMode\(view\.dom\)/);
assert.doesNotMatch(adapter, /image\.src = rawFileUrl\(target\.path\)/);
assert.match(adapter, /image\.dataset\.resolvedPath = target\.path/);
assert.match(adapter, /prosemirrorPreviewImageSource\(image, markdownPath, \{isCurrent\}\)/);
assert.match(adapter, /cancelAnimationFrame\(loadFrame\)/);
assert.match(adapter, /list_item\(state, node\)[\s\S]*taskChecked[\s\S]*state\.renderContent\(node\)/);
assert.match(adapter, /setNodeMarkup\(position, undefined,[\s\S]*taskChecked: input\.checked === true/);
assert.doesNotMatch(adapter, /function bindProseMirrorTaskCheckboxes/);
assert.match(adapter, /refreshProseMirrorSourceLines\(panel, next\)/);
assert.match(adapter, /headingSearchFrom = sourceHeading\.line;/);
assert.match(adapter, /parsedTaskLines\.push\(Number\(node\.attrs\.taskLine \|\| 0\)\)/);
assert.match(adapter, /setMeta\('yomuxSourceLineRefresh', true\)/);
assert.match(adapter, /transaction\.getMeta\('yomuxSourceLineRefresh'\) !== true/);
assert.doesNotMatch(adapter, /_pmEnsurePromise/);
assert.match(adapter, /ensureGeneration === panel\._pmEnsureGeneration/);
assert.doesNotMatch(adapter, /ensureGeneration === panel\._pmEnsureGeneration[\s\S]{0,160}!panel\.isConnected/);
assert.match(adapter, /panelNodes\.get\(item\) === panel/);
assert.match(adapter, /!ready && prosemirrorPanelInitializationCurrent\(panel, item, path, state, ensureGeneration\)/);
assert.doesNotMatch(adapter, /function startProseMirrorImageLoads/);
assert.match(adapter, /markdown-it collapses extra blank lines/);
assert.match(adapter, /ignoredCommentLines/);
assert.match(adapter, /Math\.max\(0, visibleGapLines - 1\)/);
assert.match(adapter, /markdown_comment_block.*includes\(token\.type\)/);
assert.match(adapter, /environment\.yomuxTopLevelSourceLines/);
assert.match(adapter, /environment\.yomuxIgnoredCommentRanges/);
assert.match(adapter, /prosemirrorSourceLineMetadata/);
assert.match(adapter, /'data-source-line': String\(sourceLine\)/);
assert.match(adapter, /metadata\?\.headings\?\.find[\s\S]*'data-source-line': String\(sourceHeading\.line\)/);
assert.match(adapter, /taskLine: \{default: 0\}/);
assert.match(adapter, /taskChecked: \{default: null\}/);
assert.match(adapter, /\['span', \{'data-markdown-soft-break': 'true'\}, ' '\]/);
assert.match(adapter, /markdownPreviewImageTarget\(original, markdownPath\)/);
assert.doesNotMatch(adapter, /installRawFileMediaSource\(image, path/);
assert.match(adapter, /image: \(node, _view, getPos\) => prosemirrorImageNodeView\(node, panel, path, getPos\)/);
assert.match(adapter, /table\(state, node\)/);
assert.match(adapter, /details: prosemirrorDetailsNodeView/);
assert.match(adapter, /Keep comments in the document as hidden atoms/);
assert.match(adapter, /markdown_comment_block/);
assert.match(adapter, /markdown_comment_inline/);
assert.match(adapter, /markdown_literal_block/);
assert.match(adapter, /markdown_literal_inline/);
assert.match(adapter, /token\.type === 'html_inline' \|\| token\.type === 'html_block'/);
assert.match(adapter, /Preserve unrecognized XML-like tags|Preserve unrecognized XML-like tags as ordinary visible text/);
assert.match(adapter, /token\.type = token\.type === 'html_block' \? 'markdown_literal_block'/);
assert.match(adapter, /function prosemirrorUnsupportedHtmlSource/);
assert.match(adapter, /PROSEMIRROR_SAFE_HTML_TAGS/);
assert.match(adapter, /PROSEMIRROR_KNOWN_HTML_TAGS/);
assert.match(adapter, /PROSEMIRROR_KNOWN_HTML_TAGS\.has\(name\) && !PROSEMIRROR_SAFE_HTML_TAGS\.has\(name\)/);
assert.doesNotMatch(adapter, /s_open: \{mark: 'strike'\}/);
assert.doesNotMatch(adapter, /html_inline: \{mark: 'underline'\}/);
assert.match(adapter, /const doc = parser\.parse\(state\.content \|\| '', parseEnvironment\);[\s\S]*parts\.previewPane\.replaceChildren\(container\);[\s\S]*new api\.EditorView/);
assert.match(adapter, /function renderProseMirrorFailure/);
assert.match(adapter, /statusErr\(esc\(message\)\)/);
assert.doesNotMatch(adapter, /keeping Markdown Preview/);
assert.doesNotMatch(settingsRuntime, /_pmView\?\.domObserver|view\.dom\.setAttribute\('contenteditable'/);
assert.match(settingsRuntime, /const prosemirrorRoot = host\?\._pmView\?\.dom \|\| null/);


// Exercise real CodeMirror states and syntax trees, without substituting selected-word matching.
const vm = require('node:vm');
const sourceContext = {window: {}, console, setTimeout, clearTimeout};
vm.createContext(sourceContext);
vm.runInContext(fs.readFileSync(path.join(root, 'static/codemirror.js'), 'utf8'), sourceContext);
vm.runInContext(fs.readFileSync(path.join(root, 'static_src/js/yomux/94_markdown_formatting.js'), 'utf8') + `
  window.formatSource = (text, from, to, command, twice = false) => {
    const api = window.YOmuxCodeMirror;
    const panel = {};
    const view = {state: api.EditorState.create({doc: text, selection: {anchor: from, head: to}, extensions: [api.markdown({extensions: [api.markdownStrikethrough, api.markdownTable].filter(Boolean)})]}), dispatch(spec) { this.state = this.state.update(spec).state; }};
    panel._cmView = view;
    applyCodeMirrorMarkdownFormat(panel, markdownFormatCommands.find(entry => entry.id === command));
    if (twice) applyCodeMirrorMarkdownFormat(panel, markdownFormatCommands.find(entry => entry.id === command));
    return view.state.doc.toString();
  };
`, sourceContext);
const sourceFormat = (text, command, from = 0, to = text.length) => sourceContext.window.formatSource(text, from, to, command);
assert.equal(sourceFormat('[label](url) more', 'bold'), '[**label**](url) **more**');
assert.equal(sourceFormat('before [label](url)', 'bold'), '**before** [**label**](url)');
assert.equal(sourceFormat('**before [label](url) after**', 'bold', 10, 15), '**before** [label](url) **after**');
assert.equal(sourceFormat('**before [label](url) after**', 'clearInline', 10, 15), '**before** [label](url) **after**');
assert.equal(sourceFormat('- parent\n  - child\n- sibling', 'number'), '1. parent\n   1. child\n2. sibling');
assert.equal(sourceFormat('- a\n  continuation\n- b', 'number'), '1. a\n   continuation\n2. b');
assert.equal(sourceFormat('- a\n  continuation\n- b', 'bullet'), 'a\ncontinuation\nb');
assert.equal(sourceFormat('- a\n  continuation\n- b', 'number', 8, 13), '1. a\n   continuation\n- b');
assert.equal(sourceFormat('- a\n  continuation\n  - child\n    more\n- b', 'number'), '1. a\n   continuation\n   1. child\n      more\n2. b');
for (const [command, marker, indent] of [['number', '1. ', '   '], ['check', '- [ ] ', '  '], ['bullet', '', '']]) {
  const source = '- a\n  ```js\n  **literal**\n  ```\n- b';
  const tail = command === 'number' ? '2. ' : marker;
  assert.equal(sourceFormat(source, command), `${marker}a\n${indent}\`\`\`js\n${indent}**literal**\n${indent}\`\`\`\n${tail}b`);
}
assert.equal(sourceFormat('- ```js\n  **literal**\n  ```\n- b', 'check'), '- [ ] \n  ```js\n  **literal**\n  ```\n- [ ] b');
assert.equal(sourceFormat('- a\n\n      **literal**\n\n- b', 'number'), '1. a\n\n       **literal**\n\n2. b');
assert.equal(sourceFormat('`foo` bar', 'bold'), '<strong>`foo`</strong> **bar**');
assert.equal(sourceContext.window.formatSource('`foo` bar', 0, 9, 'bold', true), '`foo` bar');
assert.equal(sourceFormat('`foobar`', 'bold', 1, 4), '<strong>`foo`</strong>`bar`');
assert.equal(sourceFormat('` foo `', 'bold', 2, 5), '<strong>`foo`</strong>');
assert.equal(sourceFormat('**` foo `**', 'bold', 4, 7), '`foo`');
assert.equal(sourceFormat('**`foobar`**', 'clearInline', 3, 6), 'foo<strong>`bar`</strong>');
assert.equal(sourceFormat('`a*b`', 'clearInline', 1, 4), 'a\\*b');
assert.equal(sourceFormat('**`foobar`**', 'bold', 3, 6), '`foo`<strong>`bar`</strong>');
assert.equal(sourceFormat('**before `code` label**', 'bold', 16, 21), '<strong>before `code`</strong> label');
assert.equal(sourceFormat('a`b', 'code'), '``a`b``');
assert.equal(sourceFormat('foo <!-- <u>x</u> --> bar', 'clearInline'), 'foo <!-- <u>x</u> --> bar');
assert.equal(sourceFormat('```\n<u>x</u>\n```', 'clearInline'), '\\<u\\>x\\</u\\>');
assert.equal(sourceFormat('foo\n\nbar', 'bold'), '**foo**\n\n**bar**');
assert.equal(sourceFormat('~~same~~', 'strike', 2, 6), 'same');
assert.equal(sourceFormat('- ## item', 'normal'), '- item');
assert.equal(sourceFormat('- item', 'h6'), '- ###### item');
assert.equal(sourceFormat('~~same~~', 'clearInline'), 'same');
assert.equal(sourceFormat('~~~js\nfoo\n~~~', 'pre'), 'foo');
assert.equal(sourceFormat('    foo', 'normal', 5, 5), 'foo');
assert.equal(sourceFormat('    foo', 'h1', 5, 5), '# foo');
assert.equal(sourceFormat('    foo\n    bar', 'normal', 5, 5), 'foo\nbar');
assert.equal(sourceFormat('```\nfoo\n```', 'pre'), 'foo');
assert.equal(sourceFormat('- parent\n  - child\n- sibling', 'number', 2, 8), '1. parent\n   - child\n- sibling');
assert.equal(sourceFormat('- parent\n  - child\n- sibling', 'bullet', 2, 8), 'parent\n- child\n- sibling');
for (const underline of ['---', '===']) {
  for (const [command, expected] of [['h1', '# title'], ['h6', '###### title'], ['normal', 'title'], ['number', '1. title'], ['bullet', '- title'], ['check', '- [ ] title']]) {
    const input = `title\n${underline}`;
    for (const [from, to] of [[0, input.length], [2, 2], [input.length - 1, input.length - 1]]) {
      assert.equal(sourceFormat(input, command, from, to), expected, JSON.stringify({input, command, from, to}));
    }
  }
}
assert.equal(sourceFormat('[ref][id]\n\n[id]: https://x', 'bold'), '[**ref**][id]\n\n[id]: https://x');
assert.equal(sourceFormat('a | b\n--|--\nx|y', 'bold'), '**a** | **b**\n--|--\n**x**|**y**');
assert.equal(sourceContext.window.formatSource('a | b\n--|--\nx|y', 0, 15, 'bold', true), 'a | b\n--|--\nx|y');
assert.equal(sourceFormat('before\n\ntitle\n---\n\nafter', 'h2', 10, 10), 'before\n\n## title\n\nafter');
for (const following of ['a | b\n--|--\nx|y', 'title\n---', '```\ncode\n```', '    code']) {
  assert.equal(sourceFormat('before\n\n' + following, 'h1', 0, 8), '# before\n# \n' + following, following);
}
for (const command of ['h1', 'normal', 'pre', 'number', 'bullet', 'check']) {
  assert.equal(sourceFormat('a | b\n--|--\nx|y', command), 'a | b\n--|--\nx|y', command);
}
vm.runInContext(fs.readFileSync(path.join(root, 'static/prosemirror.js'), 'utf8'), sourceContext);
const markdownPreview = fs.readFileSync(path.join(root, 'static_src/js/yomux/88_markdown_preview.js'), 'utf8');
vm.runInContext(markdownPreview.slice(markdownPreview.indexOf('function markdownTexMathAt('), markdownPreview.indexOf('function renderTexMathSource(')), sourceContext);
vm.runInContext(adapter + `\nwindow.formatParser = prosemirrorMarkdownParser(window.YOmuxProseMirror, prosemirrorMarkdownSchema(window.YOmuxProseMirror));`, sourceContext);
const parser = sourceContext.window.formatParser;
const htmlCode = parser.parse('Use <code>npm test</code> to run it.');
const htmlCodeText = [];
htmlCode.descendants(node => {
  if (node.isText && node.marks.some(mark => mark.type.name === 'code')) htmlCodeText.push(node.text);
});
assert.deepEqual(htmlCodeText, ['npm test']);
const referenceShapes = ['[ref][id]\n\n[id]: https://x "title"', '[ref][]\n\n[ref]: https://x', '[ref]\n\n[ref]: https://x'];
const imageShapes = [...referenceShapes.map(source => '!' + source), '![alt](image.png "title")', '[![alt](image.png)](https://x "title")'];
for (const input of ['a | b\n--|--\nx|y', '| a | b |\n| :--- | ---: |\n| x | y |', '| a \\| b | c |\n| --- | --- |\n| x | y |', ...referenceShapes, ...imageShapes, ...imageShapes.map(source => 'before ' + source.replace('\n\n', ' after\n\n'))]) {
  for (const command of ['bold', 'italic', 'underline', 'strike', 'code', 'clearInline']) {
    const formatted = sourceFormat(input, command);
    const before = parser.parse(input), after = parser.parse(formatted);
    assert.equal(after.textContent, before.textContent, JSON.stringify({input, command, formatted}));
    const shape = doc => {
      const nodes = [];
      doc.descendants(node => {
        if (!node.isText) nodes.push({type: node.type.name, attrs: node.attrs});
        for (const mark of node.marks) if (mark.type.name === 'link') nodes.push({type: 'link', attrs: mark.attrs});
      });
      return nodes;
    };
    assert.deepEqual(shape(after), shape(before), JSON.stringify({input, command, formatted}));
  }
}
vm.runInContext(`window.formatSerializer = prosemirrorMarkdownSerializer(window.YOmuxProseMirror);`, sourceContext);
const htmlCodeSerialized = sourceContext.window.formatSerializer.serialize(htmlCode);
assert.equal(parser.parse(htmlCodeSerialized).eq(htmlCode), true, JSON.stringify({htmlCodeSerialized, htmlCode: htmlCode.toJSON(), reopened: parser.parse(htmlCodeSerialized).toJSON()}));
const explorerActions = fs.readFileSync(path.join(root, 'static_src/js/yomux/45_file_explorer_actions.js'), 'utf8');
const mergeStart = explorerActions.indexOf('function diffRowsForLines(');
const mergeEnd = explorerActions.indexOf('function mergeOpenFileText(');
vm.runInContext(explorerActions.slice(mergeStart, mergeEnd) + `
  function auditSharedFixture(source) {
    const api = window.YOmuxProseMirror;
    const parser = window.formatParser, environment = {};
    const doc = parser.parse(source, environment);
    const cm = window.YOmuxCodeMirror;
    const textState = cm.EditorState.create({doc: source,
      extensions: [cm.markdown({extensions: [cm.markdownStrikethrough, cm.markdownTable]})]});
    const panel = {
      _pmSource: source, _pmSharedSourceDoc: textState.doc,
      _cmView: {state: textState}, _pmParser: parser, _pmSerializer: window.formatSerializer,
      _pmSourceLineMetadata: prosemirrorSourceLineMetadata(source, environment),
    };
    return {api, doc, panel};
  }
  function splitCaretFixture(source) {
    const {doc, panel} = auditSharedFixture(source);
    panel._pmView = {state: {doc}};
    const coordinates = sharedProseMirrorCoordinates(panel);
    return {doc, panel, map: (offset, from) => mapSharedProseMirrorPosition(coordinates, offset, from)};
  }
  function renderedCaretWord(doc, word) {
    let start;
    doc.descendants((node, position) => {
      if (node.isText && node.text.includes(word)) start = position + node.text.indexOf(word);
    });
    return start;
  }
  window.auditSplitCaretCRLF = () => {
    const {panel} = splitCaretFixture('one\\r\\n\\r\\ntwo');
    panel._pmSharedSourceDoc = null;
    delete panel._pmShareCoordinates;
    const coordinates = sharedProseMirrorCoordinates(panel);
    const rich = mapSharedProseMirrorPosition(coordinates, 6, 'source');
    const source = mapSharedProseMirrorPosition(coordinates, rich.offset, 'rich');
    panel._cmView.state = panel._cmView.state.update({changes: {from: 6, insert: 'x'}}).state;
    return JSON.stringify({rich, source, pending: sharedProseMirrorCoordinates(panel)});
  };
  window.auditSplitCaretText = (source, word) => {
    const {doc, map} = splitCaretFixture(source);
    const start = renderedCaretWord(doc, word);
    return JSON.stringify([1, 2].map(offset => map(start + offset, 'rich')));
  };
  window.auditSplitCaretHidden = (source, hidden) => {
    const {map} = splitCaretFixture(source);
    const start = source.indexOf(hidden);
    return JSON.stringify([1, 2, 3].map(offset =>
      map(start + offset, 'source')));
  };
  window.auditSplitCaretRoundTrip = (source, word) => {
    const {doc, map} = splitCaretFixture(source);
    const start = source.lastIndexOf(word);
    return JSON.stringify({richStart: renderedCaretWord(doc, word),
      positions: Array.from({length: word.length}, (_, index) => {
        const rendered = map(start + index + 1, 'source');
        return {rendered, back: map(rendered.offset, 'rich')};
      })});
  };
  window.auditFormatCache = () => {
    const {panel} = auditSharedFixture('before **bold**');
    const view = panel._cmView;
    const first = markdownSourceMarkRanges(view);
    view.state = view.state.update({selection: {anchor: 4}}).state;
    const moved = markdownSourceMarkRanges(view);
    view.state = view.state.update({changes: {from: 0, to: 7, insert: 'after '}}).state;
    const edited = markdownSourceMarkRanges(view);
    return JSON.stringify({reused: first === moved, invalidated: moved !== edited});
  };
  window.auditSharedSequence = (source, head, keys) => {
    const {api, doc, panel} = auditSharedFixture(source);
    let state = api.EditorState.create({doc, selection: api.TextSelection.create(doc, head)});
    const result = [];
    for (const key of keys) {
      const dispatch = tr => {
        normalizeSharedProseMirrorEdit(tr);
        const edit = sharedProseMirrorEdit(panel, tr);
        if (!edit || !edit.changes) throw new Error(JSON.stringify({source: panel._pmSource, key, head: state.selection.head}));
        panel._pmSource = edit.source;
        panel._cmView.state = panel._cmView.state.update({changes: edit.changes}).state;
        panel._pmSharedSourceDoc = panel._cmView.state.doc;
        panel._pmSourceLineMetadata = edit.metadata;
        state = state.apply(tr);
      };
      if (key === 'Enter') insertProseMirrorEnter(api, doc.type.schema)(state, dispatch);
      else dispatch(state.tr.insertText(key));
      result.push([panel._pmSource, state.selection.$head.parent.textContent, state.selection.$head.parentOffset]);
    }
    return JSON.stringify(result);
  };
  window.auditRemoteHistory = () => {
    const api = window.YOmuxProseMirror, parser = window.formatParser;
    const source = '- hello\\n- \\n- next';
    const doc = parser.parse(source);
    let state = api.EditorState.create({doc, selection: api.TextSelection.create(doc, 12), plugins: [api.history()]});
    const dispatch = tr => { state = state.apply(tr); };
    const serialize = () => window.formatSerializer.serialize(state.doc);
    insertProseMirrorEnter(api, doc.type.schema)(state, dispatch);
    const local = serialize();
    const remote = '- peer hello\\n- \\n- \\n- [x] next';
    dispatch(prosemirrorIncomingTransaction(state, parser.parse(remote)).setMeta('addToHistory', false));
    const undone = api.undo(state, dispatch), afterUndo = serialize();
    const redone = api.redo(state, dispatch);
    return JSON.stringify({local, undone, afterUndo, redone, afterRedo: serialize()});
  };
  window.auditSharedEdit = (source, visit) => {
    const {api, doc, panel} = auditSharedFixture(source);
    const parser = panel._pmParser;
    const positions = [];
    doc.descendants((node, start) => {
      if (!node.isTextblock) return;
      for (let offset = 0; offset <= node.content.size; offset++) {
        const pos = doc.resolve(start + 1 + offset);
        if (!/[\\uD800-\\uDBFF]$/.test(pos.nodeBefore?.text || '')) positions.push(pos.pos);
      }
    });
    const sourceState = api.EditorState.create({doc});
    for (const anchor of positions) for (const head of positions) {
      const state = api.EditorState.create({doc, selection: api.TextSelection.create(doc, anchor, head)});
      const blankListItem = anchor === head && markdownListItemDepth(state.selection.$head) &&
        prosemirrorEmptyListItem(state.selection.$head.node(-1));
      const check = (action, tr = state.tr) => {
        if (!tr.docChanged) {
          visit({source, anchor, head, action, saved: source, caret: tr.selection.head,
            blankListItem, changed: false, converged: true});
          return;
        }
        normalizeSharedProseMirrorEdit(tr);
        const edit = sharedProseMirrorEdit(panel, tr);
        const saved = edit?.source;
        const reopened = saved === undefined ? null : parser.parse(saved);
        const converged = reopened && prosemirrorIncomingTransaction(sourceState, reopened).doc.eq(reopened);
        visit({source, anchor, head, action, saved, blankListItem, caret: tr.selection.head, changed: true,
          converged: Boolean(converged)});
      };
      const checkKey = (action, execute) => {
        let applied = false;
        execute(tr => {
          applied = true;
          check(action, tr);
        });
        if (!applied) check(action);
      };
      for (const key of ['Backspace', 'Delete']) {
        const direction = key === 'Backspace' ? -1 : 1;
        checkKey(key, dispatch => {
          if (deleteProseMirrorBoundary(api, direction)(state, dispatch) || api.baseKeymap[key](state, dispatch)) return;
          const pos = state.selection.$head;
          const neighbor = direction < 0 ? pos.nodeBefore : pos.nodeAfter;
          const size = neighbor?.isText ? Array.from(neighbor.text).at(direction < 0 ? -1 : 0).length : neighbor?.nodeSize || 0;
          if (size) dispatch(state.tr.delete(direction < 0 ? head - size : head, direction < 0 ? head : head + size));
        });
      }
      checkKey('Enter', dispatch => insertProseMirrorEnter(api, doc.type.schema)(state, dispatch));
      checkKey('Shift-Enter', dispatch => insertProseMirrorHardBreak(api, doc.type.schema)(state, dispatch));
      for (const letter of ['z', ' ', '-']) {
        const range = replaceProseMirrorProtectedSelection(api, state, state.selection.from, state.selection.to);
        check('insert ' + letter, (range || state.tr).insertText(letter));
      }
    }
  };
`, sourceContext);
assert.deepEqual(JSON.parse(sourceContext.window.auditSplitCaretCRLF()), {
  rich: {offset: 7, exact: true}, source: {offset: 6, exact: true}, pending: null,
});
assert.deepEqual(JSON.parse(sourceContext.window.auditFormatCache()), {reused: true, invalidated: true});
for (const [source, hidden] of [
  ['before <!-- hidden --> after', 'hidden'],
  ['before\n\n<!-- hidden\nmore -->\n\nafter', 'hidden'],
  ['before ![image](asset.png) after', 'asset.png'],
  ['| first | second |\n| --- | --- |\n| before <!-- hidden --> after | other |', 'hidden'],
]) {
  const positions = JSON.parse(sourceContext.window.auditSplitCaretHidden(source, hidden));
  assert.ok(positions[0].offset >= 0, source);
  for (const result of positions) assert.deepEqual(result, {offset: positions[0].offset, exact: false}, source);
}
for (const source of [
  '- [a](https://example.com) XXX',
  '- &amp; one\n- XXX',
  '- ![image](asset.png)\n- XXX',
  'before <!-- hidden --> XXX',
  'before ![image](asset.png) XXX',
  '| first | second | third |\n| --- | --- | --- |\n| [a](https://example.com) | XXX | after |',
  '- Second itemXXX has an entity &amp; and an inline comment <!-- hidden text for cursor testing -->.',
  '- A &#x1f600; and &NotEqualTilde; before XXX after',
  '> alpha &amp;\n> next XXX line',
  '<strong>alpha</strong> before <u title="XXX">XXX</u>',
  'before ![alt](repeated) XXX ![alt](encoded) after',
  '[word](word) then XXX [next](next)',
  '- Plain [XYZ-5394] and [unresolved][missing] before XXX',
  'before [fake\\*] &amp; XXX',
  'before ![nope] &amp; XXX and after',
  '11. 11',
  '- [x] checked task XXX',
  'before <custom-tag> XXX </custom-tag> after',
  '[first][Ref] before XXX\n\n[Ref]: https://example.com',
  '| aa | bb |\n|---|---|\n| foo \\| XXX | end |',
  '| aa | bb |\n|---|---|\n| <u>XXX</u> &amp; | ![a](test.png) |',
  '- one\n\n  | H | J |\n  | --- | --- |\n  | [ref](target) | XXX |',
  '> | H | J |\n> | --- | --- |\n> | [ref](target) | XXX |',
]) {
  const word = source === '11. 11' ? '11' : 'XXX';
  const start = source.lastIndexOf(word);
  const {richStart, positions} = JSON.parse(sourceContext.window.auditSplitCaretRoundTrip(source, word));
  for (const [index, result] of positions.entries()) {
    assert.deepEqual(result.rendered, {offset: richStart + index + 1, exact: true}, JSON.stringify({source, result}));
    assert.deepEqual(result.back, {offset: start + index + 1, exact: true}, JSON.stringify({source, result}));
  }
}
for (const [source, entity] of [['**&ast;**', '&ast;'], ['> &gt;', '&gt;']]) {
  const start = source.indexOf(entity);
  const {positions} = JSON.parse(sourceContext.window.auditSplitCaretRoundTrip(source, entity));
  assert.deepEqual(positions.at(-1).back, {offset: start + entity.length, exact: true}, source);
  assert.deepEqual(positions[0].back, {offset: start, exact: true}, source);
  assert.equal(positions[0].rendered.exact, false, source);
}
for (const source of ['![](x)**word**![](word)', '[a](x)[word](word)']) {
  const start = source.indexOf('word');
  for (const [index, result] of JSON.parse(sourceContext.window.auditSplitCaretText(source, 'word')).entries()) {
    if (result.exact) assert.equal(result.offset, start + index + 1, source);
  }
}
const editingSources = [
  'first second', '### first second', '### Wed\n- h\n- \n- \n- \n- next item\n\n### Tue',
  '- h\n', '- h\n\nNext paragraph', '- next\n- h\n', '+ h\n+ next', '- h\n\n- next',
  '3) h\n4) next', '- [ ] h\n- [x] next', '- h\n  - next\n- final', '- h\n\n  paragraph\n- next',
  '> h', '__h__ and _there_', '_h_', '[h](url)', '`h`', '| H |\n| --- |\n| h |',
  '<!-- hidden -->\n\nh', 'A &amp; h', 'soft\nh', '**around** h', 'plain 😀h',
  'h\n\nnext', '~~~\nh\n~~~', 'words\n- h\n- next',
  '', '\n\n', '\n\na', '- \n', '- h\n- \n\ntext', '* hello\n\n* \n- next',
  '1. h\n2. \n3. next', '- parent\n\n  - \n  - next', '- [ ] h\n- [x] \n- [ ] next',
  '# a\n\n---', '---\n\n# a', '- \n\n\n- ', '\n\n\n## h', '- a\n  1. \n  2. b\n- c',
  'a<!-- keep --><!-- keep -->b', 'a\n\n<!-- keep -->\n\nb', 'a\n\n---\n\nb',
  '`<!-- keep -->`', '~~~\n<!-- keep -->\n~~~', 'p\n\n~~~\na\nb\n~~~\n\nq',
  't\n\n| A | B |\n| --- | --- |\n| x | y |\n\nu',
  '| A | B |\n| --- | --- |\n| x | y |\n| z | |',
  '> > a\n> >\n> > b', '**a** *b* `c` [d](https://e)', 'a é 👩‍👩‍👧‍👦 b',
];
const sharedEdits = {};
for (const source of editingSources) {
  sourceContext.window.auditSharedEdit(source, result => {
    assert.ok(result.converged, JSON.stringify(result));
    if (source.includes('<!-- hidden -->')) assert.ok(result.saved.includes('<!-- hidden -->'), JSON.stringify(result));
    if (source === 'a<!-- keep --><!-- keep -->b') {
      assert.equal([...result.saved.matchAll(/<!-- keep -->/g)].length, 2, JSON.stringify(result));
    }
    sharedEdits[result.action] = (sharedEdits[result.action] || 0) + 1;
  });
}
console.log(`Shared ViewEdit position matrix across ${editingSources.length} Markdown fixtures: ${JSON.stringify(sharedEdits)}`);
assert.deepEqual(JSON.parse(sourceContext.window.auditRemoteHistory()), {
  local: '- hello\n- \n- \n- next',
  undone: true, afterUndo: '- peer hello\n- \n- [x] next',
  redone: true, afterRedo: '- peer hello\n- \n- \n- [x] next',
});
for (const [source, head, keys, finalSource, text, offset] of [
  ['- a\n- \n- b', 8, ['Enter', 'Enter', 'Enter', 'z'], '- a\n- \n- \n- \n- z\n- b', 'z', 1],
  ['', 1, ['1', '.'], '1\\.', '1.', 2],
  ['- ', 3, ['[', 'x', ']'], '- &#91;x\\]', '[x]', 3],
  ['# a', 2, [' ', '#'], '# a \\#', 'a #', 3],
  ['## h ##', 2, ['z'], '## hz ##', 'hz', 2],
  ['a\n===', 2, ['z'], 'az\n===', 'az', 2],
]) {
  const result = JSON.parse(sourceContext.window.auditSharedSequence(source, head, keys));
  assert.deepEqual(result.at(-1), [finalSource, text, offset], JSON.stringify({source, head, keys}));
}
for (const [source, anchor, head, key, saved, caret] of [
  ['abc', 1, 1, 'Backspace', 'abc', 1],
  ['abc', 4, 4, 'Delete', 'abc', 4],
  ['abc', 2, 2, 'Backspace', 'bc', 1],
  ['abc', 2, 2, 'Delete', 'ac', 2],
  ['abc', 2, 2, 'Enter', 'a\n\nbc', 4],
  ['abc', 2, 2, 'Shift-Enter', 'a\\\nbc', 3],
  ['abc', 4, 1, 'Enter', '\n\n', 3],
  ['a\n\nb', 4, 4, 'Backspace', 'ab', 2],
  ['a\n\nb', 2, 2, 'Delete', 'ab', 2],
  ['- h\n- \n- next', 8, 8, 'Backspace', '- h\n- next', 4],
  ['- h\n- \n- next', 8, 8, 'Delete', '- h\n- next', 8],
  ['- h\n- \n- next', 8, 8, 'Enter', '- h\n- \n- \n- next', 12],
]) {
  const outcomes = [];
  sourceContext.window.auditSharedEdit(source, result => {
    if (result.action === key && result.anchor === anchor && result.head === head) {
      outcomes.push([result.saved, result.caret]);
    }
  });
  assert.deepEqual(outcomes, [[saved, caret]], JSON.stringify({source, anchor, head, key}));
}

const cm = sourceContext.window.YOmuxCodeMirror;
for (const [source, anchor, head, key, saved, caret] of [
  ['abc', 0, 0, 'Backspace', 'abc', 0], ['abc', 3, 3, 'Delete', 'abc', 3],
  ['abc', 2, 0, 'Backspace', 'c', 0], ['abc', 0, 2, 'Delete', 'c', 0],
  ['abc', 2, 0, 'Enter', '\nc', 1], ['abc', 1, 1, 'Enter', 'a\nbc', 2],
  ['a\nb', 2, 2, 'Backspace', 'ab', 1], ['a\nb', 1, 1, 'Delete', 'ab', 1],
  ['a😀b', 3, 3, 'Backspace', 'ab', 1], ['a😀b', 1, 1, 'Delete', 'ab', 1],
  ['- h\n- \n- next', 6, 6, 'Enter', '- h\n- \n\n- next', 7],
]) {
  const view = {
    state: cm.EditorState.create({doc: source, selection: {anchor, head}}),
    dispatch(transaction) { view.state = transaction.state; },
  };
  cm.defaultKeymap.find(binding => binding.key === key).run(view);
  assert.deepEqual([view.state.doc.toString(), view.state.selection.main.head], [saved, caret],
    JSON.stringify({source, anchor, head, key}));
}
const referenceSource = 'before [label][ref] after\n\n[ref]: /target';
let checkedReference = false;
sourceContext.window.auditSharedEdit(referenceSource, result => {
  if (result.anchor === 1 && result.head === 1 && result.action === 'insert z') {
    assert.equal(result.saved, 'z' + referenceSource);
    checkedReference = true;
  }
});
assert.ok(checkedReference);
for (const [source, saved] of [
  ['- hello\n- \n- next', '- hello\n- \n- \n- next'],
  ['* hello\n\n* \n- next', '* hello\n\n* \n\n* \n- next'],
  ['* hello\n\n* \n\n- next', '* hello\n\n* \n\n* \n\n- next'],
  ['- hello\n- \n', '- hello\n\n'],
  ['- \n', '\n'],
  ['- hello\n- \n\ntext', '- hello\n\n&#32;\n\ntext'],
  ['1. hello\n2. \n3. next', '1. hello\n2. \n3. \n4. next'],
  ['1) hello\n2) \n3) next', '1) hello\n2) \n3) \n4) next'],
  ['1) hello\n2) \n\n- next', '1) hello\n2) \n3) \n\n- next'],
  ['- parent\n\n  - \n  - next', '- parent\n\n  - \n  - \n  - next'],
  ['- [ ] hello\n- [x] \n- [ ] next', '- [ ] hello\n- [x] \n- [ ] \n- [ ] next'],
]) {
  const results = [];
  sourceContext.window.auditSharedEdit(source, edit => {
    if (edit.action === 'Enter' && edit.blankListItem) results.push(edit.saved);
  });
  assert.deepEqual(results, [saved], source);
}
for (const [input, tight] of [
  ['- h\n- next item', true],
  ['+ h\n+ \n+ next item', true],
  ['* h\n\n* next item', false],
  ['- h\n\n- ', false],
  ['- h', true],
]) {
  const api = sourceContext.window.YOmuxProseMirror;
  const doc = parser.parse(input);
  const edited = api.EditorState.create({doc}).tr.delete(3, 4).doc;
  assert.equal(edited.firstChild.attrs.tight, tight, input);
  const saved = sourceContext.window.formatSerializer.serialize(edited);
  assert.ok(parser.parse(saved).eq(edited), JSON.stringify({input, saved}));
}
for (const source of [
  '> $$\n> x\n> $$',
  '- $$\n  x\n  $$',
  '$$\nx\n> 0\n\ny\n$$',
  '$a<b>c$',
  '$$x$$ trailing text',
]) {
  const actualSource = source.replaceAll('\\n', '\n');
  const first = parser.parse(actualSource);
  const serialized = sourceContext.window.formatSerializer.serialize(first);
  assert.equal(parser.parse(serialized).eq(first), true, `math round trip: ${actualSource} ${JSON.stringify({first:first.toJSON(), serialized, next:parser.parse(serialized).toJSON()})}`);
}

for (const input of ['<u>**\`foo\`**</u>', '<mark>\`foo\`</mark>', '<kbd>\`foo\`</kbd>', '<sup>\`foo\`</sup>', '~~\`foo\`~~', '*\`foo\`*', '<u>~~*\`foo\`*~~</u>']) {
  const doc = parser.parse(input);
  let state = sourceContext.window.YOmuxProseMirror.EditorState.create({doc});
  const transaction = state.tr;
  for (const mark of Object.values(state.schema.marks)) transaction.removeMark(1, 2, mark);
  state = state.apply(transaction);
  const serialized = sourceContext.window.formatSerializer.serialize(state.doc);
  const reopened = parser.parse(serialized);
  assert.equal(reopened.textContent, state.doc.textContent, JSON.stringify({input, serialized}));
  assert.equal(JSON.stringify(reopened.toJSON()), JSON.stringify(state.doc.toJSON()), JSON.stringify({input, serialized}));
}
for (const input of ['` foo `', '`` `foo` ``', '`   `', '`foo\nbar`', '**` foo `**', '**`` `foo` ``**']) {
  for (const command of ['bold', 'italic', 'clearInline']) {
    const result = sourceFormat(input, command);
    if (input === '`   `' && command === 'clearInline') assert.equal(result, '   ');
    else assert.equal(parser.parse(result).textContent, parser.parse(input).textContent, JSON.stringify({input, command, result}));
  }
}
for (let from = 10; from < 15; from += 1) {
  for (let to = from + 1; to <= 15; to += 1) {
    for (const command of ['bold', 'clearInline']) {
      const result = sourceFormat('**before [label](url) after**', command, from, to);
      const doc = parser.parse(result), characters = [];
      doc.descendants(node => {
        if (node.isText) for (const character of node.text) characters.push({character, bold: node.marks.some(mark => mark.type.name === 'strong'), link: node.marks.find(mark => mark.type.name === 'link')?.attrs.href || ''});
      });
      assert.equal(characters.map(item => item.character).join(''), 'before label after', result);
      for (let index = 7; index < 12; index += 1) {
        assert.equal(characters[index].link, 'url', result);
        assert.equal(characters[index].bold, index < from - 3 || index >= to - 3, result);
      }
      assert.equal(characters[0].bold, true, result);
      assert.equal(characters.at(-1).bold, true, result);
    }
  }
}
vm.runInContext(`window.enterMarkdown = (source, offset, hard, acrossCells = false) => {
  const api = window.YOmuxProseMirror, schema = prosemirrorMarkdownSchema(api);
  const parser = prosemirrorMarkdownParser(api, schema), serializer = prosemirrorMarkdownSerializer(api);
  const doc = parser.parse(source);
  let position, end;
  doc.descendants((node, from) => { if (node.isText && node.text === 'abc') position = from; if (node.isText && node.text === 'VALUE') end = from + 1; });
  let state = api.EditorState.create({doc, selection:api.TextSelection.create(doc,position+offset,acrossCells ? end : position+offset)});
  const command = hard ? insertProseMirrorHardBreak(api,schema) : insertProseMirrorEnter(api,schema);
  command(state, transaction => { state = state.apply(transaction); });
  const output = serializer.serialize(state.doc), reopened = parser.parse(output);
  const cellCounts = [];
  reopened.descendants(node => { if (node.type.name === 'table_row') cellCounts.push(node.childCount); });
  return {output, text:reopened.textContent, cellCounts, parent:state.selection.$from.parent.type.name, unchanged:doc.eq(state.doc)};
};`, sourceContext);
for (const source of ['```js\nabc\n```', '| h1 | h2 |\n| --- | --- |\n| abc | VALUE |']) {
  for (let offset = 0; offset <= 3; offset += 1) for (const hard of [false, true]) {
    const result = sourceContext.window.enterMarkdown(source, offset, hard);
    assert.equal(result.text.replaceAll('\n', ''), parser.parse(source).textContent, JSON.stringify(result));
    assert.equal(result.parent, source.startsWith('|') ? 'table_cell' : 'code_block', JSON.stringify(result));
    if (source.startsWith('|')) assert.deepStrictEqual([...result.cellCounts], [2, 2]);
    else assert.equal(result.output.match(/```/g).length, 2, result.output);
  }
}
for (const hard of [false, true]) {
  const result = sourceContext.window.enterMarkdown('| h1 | h2 |\n| --- | --- |\n| abc | VALUE |', 1, hard, true);
  assert.equal(result.unchanged, true, 'a cross-cell Enter selection cannot merge table columns');
}
vm.runInContext(`window.normalCode = (text, offset, heading) => {
  const api = window.YOmuxProseMirror, schema = prosemirrorMarkdownSchema(api);
  const parser = prosemirrorMarkdownParser(api, schema), serializer = prosemirrorMarkdownSerializer(api);
  const doc = schema.topNodeType.create(null, schema.nodes.code_block.create(null, schema.text(text)));
  const state = api.EditorState.create({doc, selection:api.TextSelection.create(doc,offset+1)}), transaction = state.tr;
  setProseMirrorTextBlock(api, transaction, schema, heading ? schema.nodes.heading : schema.nodes.paragraph, heading ? {level:2} : null);
  const output = serializer.serialize(transaction.doc);
  return {output, reopened:parser.parse(output).toJSON(), expected:transaction.doc.toJSON(), parentOffset:transaction.selection.$from.parentOffset};
};`, sourceContext);
for (const text of ['a\nb', '**bold**\n# literal', 'a\n', 'a\n\n']) {
  for (let offset = 0; offset <= text.length; offset += 1) for (const heading of [false, true]) {
    const result = sourceContext.window.normalCode(text, offset, heading);
    assert.equal(JSON.stringify(result.reopened), JSON.stringify(result.expected), result.output);
    assert.equal(result.parentOffset, text.slice(0, offset).split('\n').at(-1).length, result.output);
  }
}
console.log('prosemirror editor suite: 8 passed, 0 failed');
