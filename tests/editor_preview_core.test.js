const {
  assert,
  fs,
  vm,
  TestElement,
  sourceBetween,
  test,
  runSuites,
} = require('./browser_helpers/layout_test_helper');
const {runEditorPreviewSuite} = require('./browser_helpers/editor_preview_suite');

test('numbered Markdown task labels are a post-parse presentation transform', () => {
  const source = fs.readFileSync('static_src/js/yolomux/88_markdown_preview.js', 'utf8');
  const context = {};
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    source,
    'function markdownTextWithSourceAnchors',
    'function applyMarkdownSourceLines'
  ), context);
  vm.runInContext(sourceBetween(
    source,
    'function markdownTextWithTaskLineToggled',
    'function updateMarkdownTaskFromPreview'
  ), context);
  vm.runInContext(sourceBetween(
    source,
    'function bindMarkdownTaskCheckboxes',
    'function markdownFallbackDestinationAndTitle'
  ), context);

  const markedContext = {globalThis: {}};
  markedContext.window = markedContext.globalThis;
  vm.createContext(markedContext);
  vm.runInContext(fs.readFileSync('static/vendor/marked.min.js', 'utf8'), markedContext);
  const numberedMarkdown = [
    '<input type="checkbox">',
    '',
    '<ul><li><input type="checkbox">raw</li></ul>',
    '',
    '- [x] 1. one',
    '- [ ] 2. two',
    '- [x] 3) three',
  ].join('\n');
  const parsed = markedContext.globalThis.marked.parse(numberedMarkdown, {
    gfm: true,
    breaks: true,
    renderer: context.markdownMarkedTaskRenderer(markedContext.globalThis.marked),
  });
  assert.match(parsed, /<ol>/, 'the vendored parser receives the original 1. task syntax');
  assert.match(parsed, /<ol start="2">/, 'the vendored parser retains the original 2. list start');
  assert.match(parsed, /<ol start="3">/, 'the vendored parser retains the original 3) list start');
  assert.equal((parsed.match(/markdown-rendered-task-checkbox/g) || []).length, 3, 'only parser-generated task inputs carry task provenance');
  assert.equal(source.includes('markdownTextForMarkedParser'), false, 'the preview path has no source-text preprocessor');
  assert.match(source, /window\.marked\.parse\(markdownTextWithSourceAnchors\(text\)/, 'marked parses the unmodified source text');

  const createElement = tagName => {
    const node = new TestElement('', tagName);
    const appendChild = node.appendChild.bind(node);
    node.appendChild = child => {
      const previous = child.parentElement;
      const index = previous?.children?.indexOf(child) ?? -1;
      if (index >= 0) previous.children.splice(index, 1);
      return appendChild(child);
    };
    return node;
  };
  context.document = {createElement};
  const root = createElement('div');
  const rawHtmlCheckbox = createElement('input');
  rawHtmlCheckbox.setAttribute('type', 'checkbox');
  root.appendChild(rawHtmlCheckbox);
  const rawHtmlList = createElement('ul');
  const rawHtmlListItem = createElement('li');
  const rawHtmlListCheckbox = createElement('input');
  rawHtmlListCheckbox.setAttribute('type', 'checkbox');
  rawHtmlListCheckbox.setAttribute('disabled', '');
  rawHtmlListItem.appendChild(rawHtmlListCheckbox);
  rawHtmlListItem.appendChild(createElement('span'));
  rawHtmlList.appendChild(rawHtmlListItem);
  root.appendChild(rawHtmlList);
  const list = createElement('ul');
  root.appendChild(list);
  const renderedTaskCheckboxClass = vm.runInContext('MARKDOWN_RENDERED_TASK_CHECKBOX_CLASS', context);

  const addTask = ({start = null, childCount = 1, inlineCode = false} = {}) => {
    const item = createElement('li');
    const checkbox = createElement('input');
    checkbox.setAttribute('type', 'checkbox');
    checkbox.classList.add(renderedTaskCheckboxClass);
    Object.defineProperty(checkbox, 'nextSibling', {
      get() {
        const index = item.children.indexOf(checkbox);
        return item.children[index + 1] || null;
      },
    });
    const ordered = createElement('ol');
    if (start !== null) ordered.setAttribute('start', String(start));
    for (let index = 0; index < childCount; index += 1) {
      const nestedItem = createElement('li');
      const content = createElement(inlineCode && index === 0 ? 'code' : 'span');
      content.textContent = inlineCode && index === 0 ? 'serversLoad' : `nested-${index + 1}`;
      nestedItem.appendChild(content);
      ordered.appendChild(nestedItem);
    }
    item.appendChild(checkbox);
    item.appendChild(ordered);
    list.appendChild(item);
    return {item, checkbox, ordered};
  };

  const one = addTask({inlineCode: true});
  const two = addTask({start: 2});
  const three = addTask({start: 3});
  const malformed = addTask({start: 2});
  const genuineNested = addTask({childCount: 2});
  const sourceText = [
    '<input type="checkbox">',
    '',
    '<ul><li><input type="checkbox">raw</li></ul>',
    '',
    '- [x] 1. one `serversLoad`',
    '- [ ] 2. two',
    '- [x] 3) three',
    '- [ ] 2.not-a-list',
    '- [x] Parent',
    '  1. nested one',
    '  2. nested two',
  ].join('\n');

  context.applyMarkdownTaskListClasses(root, sourceText);

  assert.ok(one.item.querySelector('.markdown-task-number'), 'raw HTML checkbox cannot steal numbered-task presentation pairing');
  assert.equal(rawHtmlListItem.classList.contains('task-list-item'), false, 'raw HTML list inputs remain ordinary sanitized HTML');
  assert.equal(list.classList.contains('contains-task-list'), true, 'the rendered task-list parent owns marker removal');
  for (const [task, expected] of [[one, '1. '], [two, '2. '], [three, '3) ']]) {
    const label = task.item.querySelector(':scope > .markdown-task-label');
    assert.equal(task.item.classList.contains('task-list-item'), true);
    assert.equal(label.children[0].classList.contains('markdown-task-number'), true);
    assert.equal(label.children[0].textContent, expected, 'the original number and delimiter remain visible');
    assert.equal(task.item.querySelector('ol'), null, 'only the qualifying single-item nested list is flattened for display');
  }
  assert.equal(one.item.querySelector('.markdown-task-label').children[1].tagName, 'CODE', 'inline markup stays inside the shared label');
  assert.equal(malformed.item.querySelector('.markdown-task-number'), null, 'a malformed pseudo-item is ordinary task prose');
  assert.equal(malformed.item.querySelector('ol'), malformed.ordered, 'a malformed source shape cannot authorize list flattening');
  assert.equal(genuineNested.item.querySelector('.markdown-task-number'), null, 'a genuine multi-item nested list has no inline number prefix');
  assert.equal(genuineNested.item.querySelector('ol').children.length, 2, 'a genuine multi-item nested list keeps its structure');

  context.bindMarkdownTaskCheckboxes(root, sourceText, '');
  assert.equal(rawHtmlCheckbox.dataset.sourceLine, undefined, 'sanitized raw HTML inputs do not steal Markdown task source lines');
  assert.equal(rawHtmlListCheckbox.dataset.sourceLine, undefined, 'sanitized raw list inputs do not steal Markdown task source lines');
  assert.equal(one.checkbox.dataset.sourceLine, '5', 'the first parser-generated task keeps the first task source line');
  assert.equal(genuineNested.checkbox.dataset.sourceLine, '9', 'parser-generated task pairing remains ordinal through nested content');
  one.checkbox.classList.remove(renderedTaskCheckboxClass);
  delete one.checkbox.dataset.sourceLine;
  context.bindMarkdownTaskCheckboxes(root, sourceText, '');
  assert.equal(one.checkbox.dataset.sourceLine, '5', 'an already-bound task remains eligible when preview code binds it again');

  const taskEntries = context.markdownTaskLineEntries(sourceText);
  assert.deepEqual(taskEntries.map(entry => [entry.line, entry.inlineNumber, entry.inlineDelimiter]), [
    [5, 1, '.'], [6, 2, '.'], [7, 3, ')'], [8, null, ''], [9, null, ''],
  ]);
  assert.equal(context.markdownTextWithTaskLineToggled(sourceText, 6, true).split('\n')[5], '- [x] 2. two', 'numbered task writes retain the source label');

  const css = fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8');
  const taskRowCss = sourceBetween(css, '.markdown-body li.task-list-item {', '}');
  assert.match(taskRowCss, /minmax\(0, 1fr\)/, 'the label column can shrink and wrap beside the checkbox');
  assert.equal(/white-space:\s*nowrap/.test(taskRowCss), false, 'task labels are not forced onto one physical line');
  const taskLabelCss = sourceBetween(css, '.markdown-body li.task-list-item > .markdown-task-label {', '}');
  assert.match(taskLabelCss, /grid-column:\s*2/, 'the unified task label owns the prose column');
  assert.match(taskLabelCss, /min-width:\s*0/, 'long task prose can wrap within the label column');
});

runSuites([() => runEditorPreviewSuite({shardIndex: 0, shardCount: 3})]);
