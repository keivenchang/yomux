import pytest

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys


def setup_formatting_editor(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, '?sessions=1', sessions=['1'])
    result = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        const path = '/home/test/formatting.md', original = 'same same';
        const item = fileEditorItemFor(path);
        setFileState(path, {kind:'text', content:original, original, dirty:false, language:'markdown'});
        setFileEditorViewMode(path, 'split', item); addFileEditorTabItem(path, item);
        const panel = createFileEditorPanel(item);
        panel.classList.add('active-pane'); panel.style.cssText = 'width:900px;height:560px';
        panelNodes.set(item,panel); document.getElementById('grid').append(panel);
        renderFileEditorPanel(panel,item);
        await window.__yomuxTestWaitFor(() => panel._pmView && panel._cmView);
        window.formattingPanel = panel;
        const view = panel._pmView;
        view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc,6,10)));
        view.focus(); done(true);
      })().catch(error => done(String(error.stack)));
    """)
    assert result is True, result


def test_markdown_native_bold_second_occurrence(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same **same**'


def test_markdown_source_table_and_setext_native_formatting(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._cmView;
      const source = 'a | b\\n--|--\\nx|y';
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:source},selection:{anchor:0,head:source.length}});
      view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == '**a** | **b**\n--|--\n**x**|**y**'
    assert browser.execute_script("""
      flushCodeMirrorSource(formattingPanel, formattingPanel.dataset.filePath);
      return formattingPanel._pmView.state.doc.textContent;
    """) == 'abxy'
    browser.execute_script("""
      const view = formattingPanel._cmView;
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'title\\n---'},selection:{anchor:2}});
      view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).key_down(Keys.ALT).send_keys('1').key_up(Keys.ALT).key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == '# title'
    browser.execute_script("""
      const view = formattingPanel._cmView;
      const source = '![ref][]\\n\\n[ref]: https://x';
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:source},selection:{anchor:0,head:source.length}});
      view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == '![ref][]\n\n[ref]: https://x'


def test_markdown_native_source_range_and_typing_toggle(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._cmView;
      view.dispatch({selection:{anchor:9,head:5}}); view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same **same**'
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same same'
    browser.execute_script('formattingPanel._cmView.dispatch({selection:{anchor:9}})')
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('i').key_up(Keys.CONTROL).send_keys('X').key_down(Keys.CONTROL).send_keys('i').key_up(Keys.CONTROL).send_keys('Y').perform()
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same same*X*Y'


def test_markdown_native_viewedit_typing_marks_toolbar_and_undo(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._pmView;
      view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc,10)));
      view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).send_keys('X').key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).send_keys('Y').perform()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same same**X**Y'
    browser.execute_script("""
      const view = formattingPanel._pmView;
      view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc,6,10))); view.focus();
    """)
    browser.find_element('css selector', '.markdown-formatting-toolbar [data-markdown-format="italic"]').click()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same *same***X**Y'
    assert browser.execute_script('return formattingPanel._pmView.state.selection.from') == 6
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('z').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same same**X**Y'


def test_markdown_format_adapters_preserve_exact_ranges(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    cases = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView;
      const results = [];
      const source = (text, from, to, command) => {
        cm.dispatch({changes:{from:0,to:cm.state.doc.length,insert:text},selection:{anchor:from,head:to}});
        dispatchMarkdownFormat(panel,command,'text-editor');
        return cm.state.doc.toString();
      };
      results.push(source('**one** two',2,11,'bold'));
      results.push(source('**one two**',4,7,'bold'));
      results.push(source('one\\ntwo',0,7,'italic'));
      results.push(source('[**one**](url) <!-- keep -->',3,6,'clearInline'));
      results.push(source('same same',9,5,'underline'));
      results.push(source('text',4,4,'bold'));
      dispatchMarkdownFormat(panel,'bold','text-editor'); results.push(cm.state.doc.toString());
      return results;
    """)
    assert cases == ['**one** **two**', '**on**e t**wo**', '*one\ntwo*', '[one](url) <!-- keep -->', 'same <u>same</u>', 'text****', 'text'], cases


def test_markdown_list_conversion_blocks_and_protection(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror, view = panel._pmView;
      const set = (text, from, to) => {
        const doc = panel._pmParser.parse(text);
        view.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
        view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc,from,to)));
      };
      set('- [x] first\\n  - child\\n- second\\n- third\\n',3,8);
      dispatchMarkdownFormat(panel,'number','view-editor');
      const converted = serializeProseMirrorSource(panel);
      dispatchMarkdownFormat(panel,'number','view-editor');
      const removed = serializeProseMirrorSource(panel);
      set('one\\n\\ntwo',1,9); dispatchMarkdownFormat(panel,'check','view-editor');
      const checked = serializeProseMirrorSource(panel);
      const checkboxes = view.dom.querySelectorAll('input[type=checkbox]').length;
      set('word',1,5); dispatchMarkdownFormat(panel,'h6','view-editor');
      const heading = serializeProseMirrorSource(panel);
      dispatchMarkdownFormat(panel,'normal','view-editor');
      dispatchMarkdownFormat(panel,'pre','view-editor');
      const pre = serializeProseMirrorSource(panel);
      const before = view.state.doc.toJSON(); fileEditorPanelState(panel).historical = true; view.updateState(view.state);
      const readonly = view.dom.getAttribute('contenteditable');
      const refused = dispatchMarkdownFormat(panel,'bold','view-editor');
      view.dispatch(view.state.tr.insertText('bad'));
      const protectedDoc = JSON.stringify(before) === JSON.stringify(view.state.doc.toJSON());
      fileEditorPanelState(panel).historical = false; view.updateState(view.state);
      return {converted,removed,checked,checkboxes,heading,pre,readonly,refused,protectedDoc};
    """)
    assert result['converted'].startswith('1. first'), result
    assert 'child' in result['converted'] and 'second' in result['converted'] and 'third' in result['converted'], result
    assert result['removed'].startswith('first'), result
    assert result['checked'].count('[ ]') == 2 and result['checkboxes'] == 2, result
    assert result['heading'] == '###### word', result
    assert '```' in result['pre'] and 'word' in result['pre'], result
    assert result['readonly'] == 'false' and result['refused'] is False and result['protectedDoc'] is True, result


def test_markdown_syntax_boundaries_shortcuts_and_list_caret(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView, view = panel._pmView, api = YOmuxProseMirror;
      const source = (text, from, to, command) => {
        cm.dispatch({changes:{from:0,to:cm.state.doc.length,insert:text},selection:{anchor:from,head:to}});
        dispatchMarkdownFormat(panel,command,'text-editor'); return cm.state.doc.toString();
      };
      const comment = 'foo <!-- <u>x</u> --> bar', fence = '```\\n<u>x</u>\\n```';
      const comments = source(comment,0,comment.length,'clearInline');
      const fenced = source(fence,0,fence.length,'clearInline');
      const paragraphs = source('foo\\n\\nbar',0,8,'bold');
      const strike = source('~~same~~',2,6,'strike');
      const strikeClear = source('~~same~~',0,8,'clearInline');
      const normalCode = source('```\\nword\\n```',5,7,'normal');
      const headingCode = source('```\\nword\\n```',5,7,'h1');
      const doc = panel._pmParser.parse('hello world');
      view.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
      view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc,7)));
      dispatchMarkdownFormat(panel,'bullet','view-editor'); view.dispatch(view.state.tr.insertText('X'));
      const listCaret = serializeProseMirrorSource(panel);
      const quoteDoc = panel._pmParser.parse('> text');
      view.updateState(api.EditorState.create({doc:quoteDoc,plugins:panel._pmPlugins}));
      view.dispatch(view.state.tr.setSelection(api.TextSelection.create(quoteDoc,3)));
      dispatchMarkdownFormat(panel,'bullet','view-editor');
      const quoteList = serializeProseMirrorSource(panel);
      const shortcuts = [
        markdownFormatShortcutCommand({key:'x',code:'KeyX',metaKey:true,shiftKey:true},true)?.id,
        markdownFormatShortcutCommand({key:'%',code:'Digit5',altKey:true,shiftKey:true},false)?.id,
        markdownFormatShortcutCommand({key:'b',ctrlKey:true,isComposing:true},false),
        markdownFormatShortcutCommand({key:'b',ctrlKey:true,altKey:true},false),
        markdownFormatShortcutCommand({key:'6',code:'Digit6',ctrlKey:true,altKey:true},false)?.id,
        markdownFormatShortcut(markdownFormatCommands.find(entry => entry.id === 'pre'),true),
        markdownFormatShortcut(markdownFormatCommands.find(entry => entry.id === 'pre'),false),
        markdownFormatShortcutCommand({key:'c',code:'KeyC',metaKey:true,altKey:true,shiftKey:true},true)?.id,
        markdownFormatShortcutCommand({key:'c',code:'KeyC',ctrlKey:true,altKey:true,shiftKey:true},false)?.id,
      ];
      fileEditorPanelState(panel).historical = true;
      previewFindSetReadOnly(panel,true); previewFindSetReadOnly(panel,false);
      const searchReadonly = !view.editable;
      fileEditorPanelState(panel).historical = false; previewFindSetReadOnly(panel,false);
      panel.style.width = '220px';
      refreshMarkdownFormattingToolbar(panel);
      const row = panel.querySelector('.markdown-formatting-toolbar');
      const narrow = row.scrollWidth <= row.clientWidth;
      return {comments,fenced,paragraphs,strike,strikeClear,normalCode,headingCode,listCaret,quoteList,shortcuts,searchReadonly,narrow};
    """)
    assert result['comments'] == 'foo <!-- <u>x</u> --> bar', result
    assert result['fenced'] == r'\<u\>x\</u\>', result
    assert result['paragraphs'] == '**foo**\n\n**bar**', result
    assert result['strike'] == result['strikeClear'] == 'same', result
    assert result['normalCode'] == 'word', result
    assert result['headingCode'] == '# word', result
    assert 'hello Xworld' in result['listCaret'], result
    assert '> * text' in result['quoteList'], result
    assert result['shortcuts'] == ['strike', 'strike', None, None, 'h6', 'Cmd+Option+Shift+C', 'Ctrl+Alt+Shift+C', 'pre', 'pre'], result
    assert result['searchReadonly'] is True and result['narrow'] is True, result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
@pytest.mark.parametrize('key,modifiers,expected', [
    ('i', [Keys.CONTROL], '*same*'),
    ('u', [Keys.CONTROL], '<u>same</u>'),
    ('5', [Keys.ALT, Keys.SHIFT], '~~same~~'),
    ('6', [Keys.CONTROL, Keys.ALT], '###### same same'),
])
def test_markdown_platform_native_shortcut_matrix(browser, tmp_path, surface, key, modifiers, expected):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      if (arguments[0] === 'text-editor') {
        const view = formattingPanel._cmView;
        view.dispatch({selection:{anchor:5,head:9}}); view.focus();
      } else formattingPanel._pmView.focus();
    """, surface)
    actions = ActionChains(browser)
    for modifier in modifiers:
        actions.key_down(modifier)
    actions.send_keys(key)
    for modifier in reversed(modifiers):
        actions.key_up(modifier)
    actions.perform()
    actual = browser.execute_script("""
      return arguments[0] === 'text-editor' ? formattingPanel._cmView.state.doc.toString() : serializeProseMirrorSource(formattingPanel);
    """, surface)
    assert actual == (expected if key == '6' else 'same ' + expected), actual


def test_markdown_format_delayed_split_save_reopen(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return fileEditorPanelState(formattingPanel).content') == 'same same'
    result = browser.execute_async_script("""
      const done = arguments[arguments.length - 1];
      (async () => {
        const panel = formattingPanel, path = panel.dataset.filePath, item = panel.dataset.layoutItem;
        await window.__yomuxTestWaitFor(() => fileEditorPanelState(panel).content === 'same **same**');
        const settled = panel._cmView.state.doc.toString();
        const originalFetch = window.fetch;
        const saves = [];
        window.fetch = async (url, options = {}) => {
          if (String(url).includes('/api/fs/write')) {
            const body = JSON.parse(options.body); saves.push(body);
            return new Response(JSON.stringify({path,size:body.content.length,mtime:123.5}), {status:200,headers:{'Content-Type':'application/json'}});
          }
          return originalFetch(url,options);
        };
        try {
          await saveFileEditor(path,panel);
          const saved = saves.at(-1).content;
          setFileEditorViewMode(path,'edit',item); renderFileEditorPanel(panel,item);
          await window.__yomuxTestWaitFor(() => !panel._pmView && panel._cmView);
          setFileEditorViewMode(path,'split',item); renderFileEditorPanel(panel,item);
          await window.__yomuxTestWaitFor(() => panel._pmView);
          const reopened = serializeProseMirrorSource(panel);
          const marks = panel._pmView.state.doc.lastChild.lastChild.marks.map(mark => mark.type.name);
          done({settled,saved,reopened,marks,saves:saves.length});
        } finally { window.fetch = originalFetch; }
      })().catch(error => done({error:String(error.stack)}));
    """)
    assert 'error' not in result, result
    assert result['settled'] == result['saved'] == result['reopened'] == 'same **same**', result
    assert result['marks'] == ['strong'] and result['saves'] == 1, result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_markdown_native_bold_preserves_inline_code(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel=formattingPanel, api=YOmuxProseMirror;
      if (arguments[0] === 'text-editor') {
        const view=panel._cmView;
        view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'`foo` bar'},selection:{anchor:0,head:9}}); view.focus();
      } else {
        const view=panel._pmView, doc=panel._pmParser.parse('`foo` bar');
        view.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
        view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc,1,8))); view.focus();
      }
    """, surface)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('b').key_up(Keys.CONTROL).perform()
    result = browser.execute_async_script("""
      const done=arguments[arguments.length-1], surface=arguments[0];
      (async()=>{
        const panel=formattingPanel;
        if(surface==='text-editor') await window.__yomuxTestWaitFor(()=>panel._pmView.state.doc.textContent==='foo bar');
        const doc=panel._pmParser.parse(surface==='text-editor'?panel._cmView.state.doc.toString():serializeProseMirrorSource(panel));
        const parts=[];doc.descendants(node=>{if(node.isText)parts.push({text:node.text,marks:node.marks.map(mark=>mark.type.name)})});
        done(parts);
      })().catch(error=>done({error:String(error.stack)}));
    """, surface)
    assert isinstance(result, list), result
    assert ''.join(part['text'] for part in result) == 'foo bar', result
    assert all('strong' in part['marks'] for part in result if part['text'].strip()), result
    assert any('code' in part['marks'] and part['text'] == 'foo' for part in result), result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_markdown_partial_clear_code_round_trip(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel=formattingPanel, api=YOmuxProseMirror;
      if(arguments[0]==='text-editor') {
        const view=panel._cmView;
        view.dispatch({changes:{from:0,to:view.state.doc.length,insert:'**` foo `**'},selection:{anchor:4,head:5}});view.focus();
      } else {
        const view=panel._pmView,doc=panel._pmParser.parse('**` foo `**');
        view.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
        view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc,1,2)));view.focus();
      }
    """, surface)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('\\').key_up(Keys.CONTROL).perform()
    result = browser.execute_script("""
      const panel=formattingPanel, source=arguments[0]==='text-editor'?panel._cmView.state.doc.toString():serializeProseMirrorSource(panel);
      const parsed=panel._pmParser.parse(source), serialized=panel._pmSerializer.serialize(parsed), reopened=panel._pmParser.parse(serialized);
      const parts=[];reopened.descendants(node=>{if(node.isText)parts.push({text:node.text,marks:node.marks.map(mark=>mark.type.name)})});
      return {text:reopened.textContent,parts,source,serialized,supported:!prosemirrorUnsupportedHtmlSource('<strong>x</strong>'),unsupported:Boolean(prosemirrorUnsupportedHtmlSource('<span>x</span>'))};
    """, surface)
    assert result['text'] == 'foo', result
    assert result['parts'] == [{'text': 'f', 'marks': []}, {'text': 'oo', 'marks': ['strong', 'code']}], result
    assert result['supported'] is True and result['unsupported'] is True, result


def test_markdown_formatting_row_preserves_panel_geometry(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel=formattingPanel, container=panel.querySelector('.file-editor-toolbars');
      const body=panel.querySelector('.file-editor-panel-body').getBoundingClientRect();
      const frame=panel.getBoundingClientRect(), tools=container.getBoundingClientRect();
      return {sameParent:panel.querySelector('.file-editor-toolbar').parentElement===container&&panel.querySelector('.markdown-formatting-toolbar').parentElement===container,
        bodyTop:body.top,bodyBottom:body.bottom,bodyHeight:body.height,frameBottom:frame.bottom,frameHeight:frame.height,toolbarBottom:tools.bottom};
    """)
    assert result['sameParent'] is True, result
    assert result['bodyTop'] >= result['toolbarBottom'] - 1, result
    assert result['bodyBottom'] <= result['frameBottom'] + 1, result
    assert result['bodyHeight'] > result['frameHeight'] / 2, result


def test_markdown_preview_list_indent_controls_follow_list_selection(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, view = panel._pmView, api = YOmuxProseMirror;
      const load = source => {
        const doc = panel._pmParser.parse(source);
        view.updateState(api.EditorState.create({doc, plugins: panel._pmPlugins}));
      };
      const setCursor = text => {
        let position = null;
        view.state.doc.descendants((node, offset) => {
          if (node.isText && node.text.includes(text)) position = offset + 1;
        });
        if (position === null) throw new Error(`Missing list test text: ${text}`);
        view.dispatch(view.state.tr.setSelection(api.TextSelection.create(view.state.doc, position)));
        view.focus(); refreshMarkdownFormattingToolbar(panel);
      };
      const indent = panel.querySelector('[data-markdown-list-indent="listIndent"]');
      const outdent = panel.querySelector('[data-markdown-list-indent="listOutdent"]');
      const clear = panel.querySelector('[data-markdown-format="clearInline"]');
      load('plain text'); setCursor('plain');
      const hiddenForParagraph = indent.hidden && outdent.hidden;
      load('- first\\n- second\\n- third\\n'); setCursor('second');
      const visibleForBullet = !indent.hidden && !outdent.hidden;
      const canIndent = !indent.disabled;
      panel._cmView.focus(); refreshMarkdownFormattingToolbar(panel);
      const hiddenInSourceEditor = indent.hidden && outdent.hidden;
      view.focus(); refreshMarkdownFormattingToolbar(panel);
      indent.click();
      const indented = serializeProseMirrorSource(panel);
      const canOutdent = !outdent.disabled;
      outdent.click();
      const outdented = serializeProseMirrorSource(panel);
      load('- before\\n- [x] checked\\n  - child\\n- after\\n'); setCursor('checked');
      indent.click();
      const checkedIndent = serializeProseMirrorSource(panel);
      outdent.click();
      const checkedOutdent = serializeProseMirrorSource(panel);
      return {hiddenForParagraph, visibleForBullet, hiddenInSourceEditor, canIndent, canOutdent, indented, outdented,
        checkedIndent, checkedOutdent, clearText: clear.textContent.trim(), clearLabel: clear.getAttribute('aria-label'), clearTitle: clear.title};
    """)
    assert result['hiddenForParagraph'] is True, result
    assert result['visibleForBullet'] is True, result
    assert result['hiddenInSourceEditor'] is True, result
    assert result['canIndent'] is True and result['canOutdent'] is True, result
    assert result['indented'].startswith('- first\n  * second\n- third'), result
    assert result['outdented'].startswith('- first\n- second\n- third'), result
    assert '[x] checked' in result['checkedIndent'] and 'child' in result['checkedIndent'], result
    assert '[x] checked' in result['checkedOutdent'] and 'child' in result['checkedOutdent'], result
    assert result['clearText'] == 'Clear', result
    assert result['clearLabel'] == 'Clear formatting', result


def test_markdown_preview_list_indent_shortcuts(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._pmView, api = YOmuxProseMirror;
      const doc = formattingPanel._pmParser.parse('- first\\n- second\\n');
      view.updateState(api.EditorState.create({doc, plugins: formattingPanel._pmPlugins}));
      let position = null;
      doc.descendants((node, offset) => { if (node.isText && node.text === 'second') position = offset + 1; });
      view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc, position)));
      view.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys(']').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)').startswith('- first\n  * second')
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('[').key_up(Keys.CONTROL).perform()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)').startswith('- first\n- second')


def test_markdown_preview_nested_list_can_be_outdented(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, view = panel._pmView, api = YOmuxProseMirror;
      const source = '- before\\n- parent\\n  - child\\n- after\\n';
      const doc = panel._pmParser.parse(source);
      view.updateState(api.EditorState.create({doc, plugins: panel._pmPlugins}));
      let position = null;
      doc.descendants((node, offset) => { if (node.isText && node.text === 'child') position = offset + 1; });
      view.dispatch(view.state.tr.setSelection(api.TextSelection.create(doc, position)));
      view.focus(); refreshMarkdownFormattingToolbar(panel);
      const outdent = panel.querySelector('[data-markdown-list-indent="listOutdent"]');
      const enabled = !outdent.disabled;
      outdent.click();
      return {enabled, source: serializeProseMirrorSource(panel)};
    """)
    assert result['enabled'] is True, result
    assert result['source'].startswith('- before\n- parent\n- child\n- after'), result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_markdown_code_block_shortcut(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = 'first line\\n\\nsecond line\\n';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins: panel._pmPlugins}));
      panel._cmView.dispatch({changes:{from:0, to:panel._cmView.state.doc.length, insert:source}, selection:{anchor:0, head:source.length}});
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, 1, doc.content.size - 1)));
    """)
    if surface == 'view-editor':
        browser.execute_script('formattingPanel._pmView.focus()')
    else:
        browser.execute_script('formattingPanel._cmView.focus()')
    ActionChains(browser).key_down(Keys.CONTROL).key_down(Keys.ALT).key_down(Keys.SHIFT).send_keys('c').key_up(Keys.SHIFT).key_up(Keys.ALT).key_up(Keys.CONTROL).perform()
    value = browser.execute_script(
        "return arguments[0] === 'view-editor' ? serializeProseMirrorSource(formattingPanel) : formattingPanel._cmView.state.doc.toString()",
        surface,
    )
    assert value.startswith('```'), value
    assert value.count('```') == 2, value
    assert 'first line' in value and 'second line' in value and value.rstrip().endswith('```'), value


def test_markdown_code_block_shortcut_joins_only_selected_adjacent_blocks(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = 'before\\n\\nfirst\\n\\nsecond\\n\\nafter';
      const doc = panel._pmParser.parse(source), positions = {};
      doc.descendants((node, offset) => { if (node.isText && ['first', 'second'].includes(node.text)) positions[node.text] = offset; });
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, positions.first, positions.second + 'second'.length)));
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      const output = serializeProseMirrorSource(panel), blocks = [];
      panel._pmView.state.doc.descendants(node => { if (node.type.name === 'code_block') blocks.push(node.textContent); });
      return {applied, output, blocks};
    """)
    assert result == {
        'applied': True,
        'output': 'before\n\n```\nfirst\nsecond\n```\n\nafter',
        'blocks': ['first\nsecond'],
    }, result


def test_markdown_code_block_shortcut_keeps_caret_inside_new_block(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const doc = panel._pmParser.parse('first\\n\\nsecond\\n\\nafter');
      const positions = {};
      doc.descendants((node, offset) => { if (node.isText) positions[node.text] = offset; });
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, positions.first, positions.second + 'second'.length)));
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      const selection = panel._pmView.state.selection;
      const insideCodeBlock = selection.empty && selection.$from.parent.type.name === 'code_block';
      panel._pmView.dispatch(panel._pmView.state.tr.insertText('!'));
      return {applied, insideCodeBlock, source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is True, result
    assert result['insideCodeBlock'] is True, result
    assert result['source'] == '```\nfirst\nsecond!\n```\n\nafter', result


def test_markdown_code_block_shortcut_preserves_inline_image_as_source(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = 'before ![photo](/images/photo.png "title") after';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, 1, doc.content.size - 1)));
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      return {applied, source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is True, result
    assert result['source'] == '```\nbefore ![photo](/images/photo.png "title") after\n```', result


def test_markdown_code_block_shortcut_preserves_inline_literal_html(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = 'before <custom> after';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, 1, doc.content.size - 1)));
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      return {applied, source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is True, result
    assert result['source'] == '```\nbefore <custom> after\n```', result


def test_markdown_code_block_shortcut_preserves_link_destination(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = 'before [linked text](/target "title") after';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, 1, doc.content.size - 1)));
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      return {applied, source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is True, result
    assert result['source'] == '```\nbefore [linked text](/target "title") after\n```', result


def test_markdown_code_block_shortcut_leaves_table_cells_intact(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = '| heading |\\n| --- |\\n| cell text |\\n\\nafter';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      let cellPosition = null;
      doc.descendants((node, position) => { if (node.type.name === 'table_cell') cellPosition = position; });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, cellPosition + 2)));
      const before = panel._pmView.state.doc;
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      return {applied, unchanged:panel._pmView.state.doc.eq(before), source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is False, result
    assert result['unchanged'] is True, result
    assert '| cell text |' in result['source'] and 'after' in result['source'], result


def test_markdown_code_block_shortcut_refuses_list_item_without_partial_conversion(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const source = '- list item\\n\\nafter';
      const doc = panel._pmParser.parse(source);
      let textPosition = null;
      doc.descendants((node, position) => { if (node.isText && node.text === 'list item') textPosition = position; });
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, textPosition, textPosition + 'list item'.length)));
      const before = panel._pmView.state.doc;
      const applied = dispatchMarkdownFormat(panel, 'pre', 'view-editor');
      return {applied, unchanged:panel._pmView.state.doc.eq(before), source:serializeProseMirrorSource(panel)};
    """)
    assert result['applied'] is False, result
    assert result['unchanged'] is True, result
    assert result['source'].startswith('- list item') and 'after' in result['source'], result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_clear_removes_highlight_mark_in_both_editors(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, source = '<mark>highlight</mark>';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(YOmuxProseMirror.EditorState.create({doc, plugins:panel._pmPlugins}));
      let textFrom = null, textTo = null;
      doc.descendants((node, offset) => {
        if (node.isText && node.text === 'highlight') { textFrom = offset; textTo = offset + node.nodeSize; }
      });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(doc, textFrom, textTo)));
      const cm = panel._cmView;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:6, head:15}});
      const surface = arguments[0];
      const applied = dispatchMarkdownFormat(panel, 'clearInline', surface);
      return {applied, result:surface === 'view-editor' ? serializeProseMirrorSource(panel) : cm.state.doc.toString()};
    """, surface)
    assert result == {'applied': True, 'result': 'highlight'}, result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
@pytest.mark.parametrize('tag', ['mark class="x"', 'u class="x"'])
def test_clear_removes_attributed_html_marks(browser, tmp_path, surface, tag):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, source = `<${arguments[0]}>highlight</${arguments[0].split(' ')[0]}>`;
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(YOmuxProseMirror.EditorState.create({doc, plugins:panel._pmPlugins}));
      let textFrom = null, textTo = null;
      doc.descendants((node, offset) => {
        if (node.isText && node.text === 'highlight') { textFrom = offset; textTo = offset + node.nodeSize; }
      });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(doc, textFrom, textTo)));
      const cm = panel._cmView;
      const from = source.indexOf('highlight'), to = from + 'highlight'.length;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:from, head:to}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', arguments[1]);
      return {applied, result:arguments[1] === 'view-editor' ? serializeProseMirrorSource(panel) : cm.state.doc.toString()};
    """, tag, surface)
    assert result == {'applied': True, 'result': 'highlight'}, result


def test_clear_partial_attributed_source_mark_preserves_original_tags(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView, source = '<mark class="x">first second</mark>';
      const from = source.indexOf('first'), to = from + 'first'.length;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:from, head:to}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', 'text-editor');
      return {applied, source:cm.state.doc.toString()};
    """)
    assert result == {'applied': True, 'source': 'first <mark class="x">second</mark>'}, result


@pytest.mark.parametrize(('source', 'selected', 'expected'), [
    ('<mark class="x">first `second`</mark>', 'first', 'first <mark class="x">`second`</mark>'),
    ('<u class="x">`first` second</u>', 'second', '<u class="x">`first`</u> second'),
])
def test_clear_preserves_attributed_tags_around_unselected_inline_code(browser, tmp_path, source, selected, expected):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView, source = arguments[0], selected = arguments[1];
      const from = source.indexOf(selected), to = from + selected.length;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:from, head:to}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', 'text-editor');
      return {applied, source:cm.state.doc.toString()};
    """, source, selected)
    assert result == {'applied': True, 'source': expected}, result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
@pytest.mark.parametrize(('source', 'selected', 'expected'), [
    ('## **Heading**\n\nplain', 'Heading', 'Heading\n\nplain'),
    ('```\ncode\n```', 'code', 'code'),
    ('title\n=====\nplain', 'title', 'title\nplain'),
])
def test_clear_resets_block_style_to_normal_text(browser, tmp_path, surface, source, selected, expected):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror, source = arguments[0], selected = arguments[1];
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      let textFrom = null, textTo = null;
      doc.descendants((node, offset) => {
        if (node.isText && node.text.includes(selected) && textFrom === null) {
          textFrom = offset + node.text.indexOf(selected); textTo = textFrom + selected.length;
        }
      });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, textFrom, textTo)));
      const cm = panel._cmView, from = source.indexOf(selected), to = from + selected.length;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:from, head:to}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', arguments[2]);
      (arguments[2] === 'view-editor' ? panel._pmView : cm).focus();
      return {applied, source:arguments[2] === 'view-editor' ? serializeProseMirrorSource(panel) : cm.state.doc.toString()};
    """, source, selected, surface)
    expected_source = 'title\n\nplain' if source.startswith('title\n=====') and surface == 'view-editor' else expected
    assert result == {'applied': True, 'source': expected_source}, result
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('z').key_up(Keys.CONTROL).perform()
    restored = browser.execute_script(
        "return arguments[0] === 'view-editor' ? serializeProseMirrorSource(formattingPanel) : formattingPanel._cmView.state.doc.toString()",
        surface,
    )
    expected_restored = '# title\n\nplain' if source.startswith('title\n=====') and surface == 'view-editor' else source
    assert restored == expected_restored, restored


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_clear_code_block_keeps_literal_markdown_as_normal_text(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    literal = '**bold**\n# literal'
    source = f'```\n{literal}\n```'
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror, source = arguments[0], literal = arguments[1];
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      let textFrom = null, textTo = null;
      doc.descendants((node, offset) => {
        if (node.isText && node.text.includes(literal)) {
          textFrom = offset + node.text.indexOf(literal); textTo = textFrom + literal.length;
        }
      });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, textFrom, textTo)));
      const cm = panel._cmView;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:source.indexOf(literal), head:source.indexOf(literal) + literal.length}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', arguments[2]);
      const output = arguments[2] === 'view-editor' ? serializeProseMirrorSource(panel) : cm.state.doc.toString();
      const reopened = panel._pmParser.parse(output), parts = [];
      reopened.descendants(node => { if (node.isText) parts.push({text:node.text, marks:node.marks.map(mark => mark.type.name)}); });
      return {applied, output, text:reopened.textContent, parts};
    """, source, literal, surface)
    assert result['applied'] is True, result
    assert [part['text'] for part in result['parts']] == literal.split('\n'), result
    assert all(not part['marks'] for part in result['parts']), result


def test_clear_indented_fence_returns_content_to_normal_text(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView, source = '  ```\\n    literal\\n  ```\\n';
      const from = source.indexOf('literal'), to = from + 'literal'.length;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:from, head:to}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', 'text-editor');
      const output = cm.state.doc.toString();
      const tree = YOmuxCodeMirror.ensureSyntaxTree(cm.state, cm.state.doc.length, 100);
      const nodes = [];
      tree?.iterate({enter(node) { if (node.name === 'CodeBlock' || node.name === 'FencedCode') nodes.push(node.name); }});
      return {applied, output, nodes};
    """)
    assert result == {'applied': True, 'output': '  literal\n', 'nodes': []}, result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_clear_at_caret_resets_heading_block_to_normal_text(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror, source = '### Heading';
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc, plugins:panel._pmPlugins}));
      let textFrom = null;
      doc.descendants((node, offset) => { if (node.isText && node.text === 'Heading') textFrom = offset; });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc, textFrom + 2)));
      const cm = panel._cmView, caret = source.indexOf('Heading') + 2;
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:caret}});
      const applied = dispatchMarkdownFormat(panel, 'clearInline', arguments[0]);
      return {applied, source:arguments[0] === 'view-editor' ? serializeProseMirrorSource(panel) : cm.state.doc.toString()};
    """, surface)
    assert result == {'applied': True, 'source': 'Heading'}, result


@pytest.mark.parametrize(('source', 'shift', 'expected'), [
    ('```js\nabc\n```', False, '```js\na\nXbc\n```'),
    ('```js\nabc\n```', True, '```js\na\nXbc\n```'),
    ('| h1 | h2 |\n| --- | --- |\n| abc | VALUE |', False, '| h1 | h2 |\n| --- | --- |\n| a<br>Xbc | VALUE |'),
    ('| h1 | h2 |\n| --- | --- |\n| abc | VALUE |', True, '| h1 | h2 |\n| --- | --- |\n| a<br>Xbc | VALUE |'),
    ('- abc', False, '- a\n- Xbc'),
    ('- [x] abc', False, '- [x] a\n- [ ] Xbc'),
])
def test_native_enter_preserves_structured_markdown_and_split_sync(browser, tmp_path, source, shift, expected):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror, source = arguments[0];
      fileEditorPanelState(panel).content = source;
      panel._cmView.dispatch({changes:{from:0,to:panel._cmView.state.doc.length,insert:source}});
      const doc = panel._pmParser.parse(source);
      panel._pmView.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
      let caret;
      doc.descendants((node, position) => { if (node.isText && node.text === 'abc') caret = position + 1; });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc,caret)));
      panel._pmView.focus();
    """, source)
    keys = ActionChains(browser)
    if shift:
        keys.key_down(Keys.SHIFT)
    keys.send_keys(Keys.ENTER)
    if shift:
        keys.key_up(Keys.SHIFT)
    keys.perform()
    ActionChains(browser).send_keys('X').perform()
    result = browser.execute_script("""
      const panel = formattingPanel, source = serializeProseMirrorSource(panel);
      const reopened = panel._pmParser.parse(source), cellCounts = [];
      reopened.descendants(node => { if (node.type.name === 'table_row') cellCounts.push(node.childCount); });
      return {source,text:reopened.textContent,cellCounts};
    """)
    assert result['source'] == expected, result
    if source.startswith('|'):
        assert result['cellCounts'] == [2, 2] and 'VALUE' in result['text'], result
    settled = browser.execute_async_script("""
      const done = arguments[arguments.length - 1], expected = arguments[0];
      window.__yomuxTestWaitFor(() => fileEditorPanelState(formattingPanel).content === expected
        && formattingPanel._cmView.state.doc.toString() === expected,
        {timeoutMs:5000, description:'structured Enter Split convergence'}).then(() => done(true), error => done(String(error)));
    """, expected)
    assert settled is True, settled


def test_clear_selected_table_text_keeps_cells_and_other_columns(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const doc = panel._pmParser.parse('| h1 | h2 |\\n| --- | --- |\\n| **abc** | VALUE |');
      panel._pmView.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
      let position;
      doc.descendants((node, offset) => { if (node.isText && node.text === 'abc') position = offset; });
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc,position,position+3)));
      panel._pmView.focus();
    """)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('\\').key_up(Keys.CONTROL).perform()
    output = browser.execute_script('return serializeProseMirrorSource(formattingPanel)')
    assert output == '| h1 | h2 |\n| --- | --- |\n| abc | VALUE |', output


@pytest.mark.parametrize('command', ['number', 'check', 'bullet'])
@pytest.mark.parametrize('first_child', [False, True])
def test_source_list_conversion_preserves_nested_literal_code(browser, tmp_path, command, first_child):
    setup_formatting_editor(browser, tmp_path)
    result = browser.execute_script("""
      const panel = formattingPanel, cm = panel._cmView;
      const source = (arguments[1] ? '- ```js' : '- a\\n  ```js') + '\\n  **literal**\\n  ```\\n- b';
      cm.dispatch({changes:{from:0, to:cm.state.doc.length, insert:source}, selection:{anchor:0, head:source.length}});
      dispatchMarkdownFormat(panel, arguments[0], 'text-editor');
      const output = cm.state.doc.toString(), reopened = panel._pmParser.parse(output), code = [];
      reopened.descendants(node => { if (node.type.name === 'code_block') code.push({text:node.textContent, language:node.attrs.params}); });
      return {output, code};
    """, command, first_child)
    assert result['code'] == [{'text': '**literal**', 'language': 'js'}], result


@pytest.mark.parametrize('heading', [False, True])
def test_clear_at_caret_removes_typing_styles_after_block_normalization(browser, tmp_path, heading):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const panel = formattingPanel, api = YOmuxProseMirror;
      const doc = panel._pmParser.parse((arguments[0] ? '# ' : '') + '**abc**');
      panel._pmView.updateState(api.EditorState.create({doc,plugins:panel._pmPlugins}));
      panel._pmView.dispatch(panel._pmView.state.tr.setSelection(api.TextSelection.create(doc,2)));
      panel._pmView.focus();
    """, heading)
    ActionChains(browser).key_down(Keys.CONTROL).send_keys('\\').key_up(Keys.CONTROL).send_keys('X').perform()
    result = browser.execute_script("""
      const doc = formattingPanel._pmView.state.doc;
      const characters = [];
      doc.descendants(node => { if (node.isText) for (const text of node.text) characters.push({text,marks:node.marks.map(mark => mark.type.name)}); });
      return {type:doc.firstChild.type.name,characters};
    """)
    assert result['type'] == 'paragraph', result
    assert [character['text'] for character in result['characters']] == list('aXbc'), result
    assert result['characters'][1]['marks'] == [], result
    assert all(character['marks'] == ['strong'] for index, character in enumerate(result['characters']) if index != 1), result
