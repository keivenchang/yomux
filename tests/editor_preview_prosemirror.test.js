const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'tools/prosemirror-bundle/package.json'), 'utf8'));
const entry = fs.readFileSync(path.join(root, 'tools/prosemirror-bundle/prosemirror-entry.js'), 'utf8');
const adapter = fs.readFileSync(path.join(root, 'static_src/js/yomux/93_prosemirror_editor.js'), 'utf8');
const settingsRuntime = fs.readFileSync(path.join(root, 'static_src/js/yomux/50_editor_settings_runtime.js'), 'utf8');

assert.equal(manifest.dependencies['prosemirror-markdown'], '1.13.7');
assert.equal(manifest.dependencies['prosemirror-model'], '1.25.11');
assert.equal(manifest.dependencies['prosemirror-history'], '1.5.0');
assert.equal(manifest.dependencies['prosemirror-state'], '1.4.4');
assert.equal(manifest.dependencies['prosemirror-view'], '1.42.3');
assert.match(entry, /window\.YOmuxProseMirror/);
assert.match(entry, /Decoration, DecorationSet, EditorView/);
assert.match(adapter, /function createProseMirrorPanel/);
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
assert.match(adapter, /normalizeProseMirrorEndBreakSource/);
assert.match(adapter, /normalizeLegacyBreakMarkup/);
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
assert.match(adapter, /const markName = \{u: 'underline', strong: 'strong', em: 'em', s: 's', mark: 'highlight', kbd: 'kbd', sup: 'superscript'\}/);
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
assert.match(adapter, /trailingEmptyParagraphs = Math\.max\(0, trailingNewlines - 1\)/);
assert.match(adapter, /\['span', \{'data-markdown-soft-break': 'true'\}, ' '\]/);
assert.match(adapter, /paragraph\(state, node\)[\s\S]*state\.flushClose\(1\)/);
assert.match(adapter, /markdownPreviewImageTarget\(original, markdownPath\)/);
assert.doesNotMatch(adapter, /installRawFileMediaSource\(image, path/);
assert.match(adapter, /image: node => prosemirrorImageNodeView\(node, panel, path\)/);
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
vm.runInContext(adapter + `\nwindow.formatParser = prosemirrorMarkdownParser(window.YOmuxProseMirror, prosemirrorMarkdownSchema(window.YOmuxProseMirror));`, sourceContext);
const parser = sourceContext.window.formatParser;
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
  const output = normalizeProseMirrorEndBreakSource(serializer.serialize(transaction.doc));
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
