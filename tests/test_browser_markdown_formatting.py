import pytest

from tests.browser_helpers.browser_layout import *  # noqa: F401,F403
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from tests.browser_helpers.native_keyboard import native_editor_modifier, native_logical_key
from tests.browser_helpers.editor_mount import mount_split_file


def setup_formatting_editor(browser, tmp_path):
    load_live_runtime_boot_fixture(browser, tmp_path, '?sessions=1&platform=pc', sessions=['1'])
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


def mount_markdown_split(browser, tmp_path, markdown, *, math=False, before_mount=None):
    def prepare(driver):
        if before_mount:
            before_mount(driver)
        if math:
            assert driver.execute_async_script("""
              const done = arguments[0], script = document.createElement('script');
              script.src = '/static/vendor/katex/katex.min.js';
              script.onload = () => done(typeof katex.render === 'function');
              script.onerror = () => done(false);
              document.head.append(script);
              const css = document.createElement('link');
              css.rel = 'stylesheet';
              css.href = '/static/vendor/katex/katex.min.css';
              document.head.append(css);
            """)
    return mount_split_file(browser, tmp_path, markdown, '/home/test/markdown-split.md', 'markdown', before_mount=prepare)


def test_markdown_viewedit_task_checkbox_aligns_with_text_and_follows_editability(browser, tmp_path):
    source = '- [ ] Toggle this task in ViewEdit.\n- [x] Open the sample in YOmux.\n'
    item = mount_markdown_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 6)
    state = wait.until(lambda driver: driver.execute_script("""
      const view = panelNodes.get(arguments[0])?._pmView;
      const input = view?.dom.querySelector('input[type=checkbox]');
      const label = input?.parentElement.querySelector('p');
      if (!view?.editable || !label?.textContent) return false;
      const text = document.createTreeWalker(label, NodeFilter.SHOW_TEXT).nextNode();
      const range = document.createRange();
      range.setStart(text, 0);
      range.setEnd(text, 1);
      const glyph = range.getBoundingClientRect(), box = input.getBoundingClientRect();
      return {inline: box.right <= glyph.left && box.top < glyph.bottom && box.bottom > glyph.top,
        noBullet: getComputedStyle(input.parentElement).listStyleType === 'none', enabled: !input.disabled};
    """, item))
    assert state == {'inline': True, 'noBullet': True, 'enabled': True}, state

    browser.execute_script("previewFindSetReadOnly(panelNodes.get(arguments[0]), true)", item)
    checkbox = browser.find_element(By.CSS_SELECTOR, '.ProseMirror input[type=checkbox]')
    assert not checkbox.is_enabled() and not checkbox.is_selected()
    browser.execute_script("previewFindSetReadOnly(panelNodes.get(arguments[0]), false)", item)
    checkbox = browser.find_element(By.CSS_SELECTOR, '.ProseMirror input[type=checkbox]')
    assert checkbox.is_enabled()
    checkbox.click()
    assert browser.find_element(By.CSS_SELECTOR, '.ProseMirror input[type=checkbox]').is_selected()
    saved = browser.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      flushProseMirrorSource(panel, panel.dataset.filePath);
      return panel._cmView.state.doc.toString();
    """, item)
    assert saved == source.replace('[ ]', '[x]'), saved


@pytest.mark.parametrize('direction', ['LR', 'TD'])
def test_markdown_viewedit_mermaid_refits_when_the_pane_resizes(browser, tmp_path, direction):
    source = f'```mermaid\nflowchart {direction}\n  A[Owner] --> B[YO!Share] --> C[Guest tabs]\n```\n'
    item = mount_markdown_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 8)

    def metrics(driver):
        return driver.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          const shell = panel?._pmView?.dom.querySelector('.mermaid-preview-host');
          const image = shell?.querySelector('img');
          const viewport = shell?.querySelector('.file-editor-preview-zoom-viewport');
          if (!image?.naturalWidth || !shell.dataset.previewZoomScale ||
              shell.classList.contains('file-editor-preview-zoom-measuring')) return false;
          return {pane: shell.parentElement.clientWidth, viewport: viewport.clientWidth,
            image: image.getBoundingClientRect().width,
            viewportHeight: viewport.clientHeight, imageHeight: image.getBoundingClientRect().height,
            scrollHeight: viewport.scrollHeight,
            scale: Number(shell.dataset.previewZoomScale), mode: shell.dataset.previewZoomMode};
        """, item)

    def resize(width):
        browser.execute_script("panelNodes.get(arguments[0])._pmView.dom.style.width = arguments[1] + 'px'", item, width)

    wait.until(metrics)
    resize(360)
    large = wait.until(lambda driver: (state := metrics(driver)) and
                      state['pane'] == 360 and state['image'] <= state['viewport'] and state)
    assert large['mode'] == 'fit', large
    assert 0 <= large['viewportHeight'] - large['imageHeight'] <= 28, large
    assert large['scrollHeight'] == large['viewportHeight'], large
    assert (large['imageHeight'] > large['image']) == (direction == 'TD'), large
    if direction == 'TD':
        assert large['viewport'] < large['pane'] * 0.75, large
    resize(200)
    small = wait.until(lambda driver: (state := metrics(driver)) and
                       state['pane'] == 200 and state['image'] <= state['viewport'] and
                       (direction == 'TD' or state['scale'] < large['scale']) and state)
    assert small['mode'] == 'fit' and small['image'] <= small['viewport'], small
    assert 0 <= small['viewportHeight'] - small['imageHeight'] <= 28, small
    browser.find_element(By.CSS_SELECTOR, '.mermaid-preview-host [data-preview-zoom-action="actual"]').click()
    resize(360)
    actual = wait.until(lambda driver: (state := metrics(driver)) and state['pane'] == 360 and state)
    assert actual['mode'] == 'actual' and actual['scale'] == 1, actual
    browser.find_element(By.CSS_SELECTOR, '.mermaid-preview-host [data-preview-zoom-action="fit"]').click()
    fitted = wait.until(lambda driver: (state := metrics(driver)) and state['mode'] == 'fit' and state)
    assert fitted['image'] <= fitted['viewport'], fitted
    assert 0 <= fitted['viewportHeight'] - fitted['imageHeight'] <= 28, fitted
    assert fitted['scale'] == large['scale'], fitted
    if direction == 'TD':
        browser.execute_script("""
          const panel = panelNodes.get(arguments[0]);
          panel.querySelector('.file-editor-preview-pane-panel').style.height = '200px';
        """, item)
        shorter = wait.until(lambda driver: (state := metrics(driver)) and state['scale'] < fitted['scale'] and state)
        assert shorter['viewportHeight'] < 200, shorter
        assert shorter['scrollHeight'] == shorter['viewportHeight'], shorter


@pytest.mark.parametrize('surface', ['viewedit', 'read-only'])
def test_markdown_preview_fills_width_for_unconstrained_standalone_svg(browser, tmp_path, surface):
    source = '![Wide](wide.svg)\n\nInline ![Inline](wide.svg) image.\n\n<img src="wide.svg" alt="Sized" width="48">\n'

    def stub_svg(driver):
        driver.execute_script("""
          const original = fetchRawFileBlob;
          fetchRawFileBlob = (path, options) => path.endsWith('/wide.svg')
            ? Promise.resolve({ok:true,status:200,blob:new Blob(
              ['<svg xmlns="http://www.w3.org/2000/svg" width="110" height="55"><rect width="110" height="55"/></svg>'],
              {type:'image/svg+xml'})})
            : original(path, options);
        """)

    item = mount_markdown_split(browser, tmp_path, source, before_mount=stub_svg)
    if surface == 'read-only':
        assert browser.execute_async_script("""
          const [source, done] = arguments;
          const script = document.createElement('script');
          script.src = '/static/vendor/marked.min.js';
          script.onerror = () => done(false);
          script.onload = () => {
            const root = document.createElement('div');
            root.className = 'markdown-body';
            root.style.width = '400px';
            document.body.append(root);
            window.svgPreviewTestRoot = root;
            renderMarkdownPreviewInto(root, source, '/home/test/markdown-split.md', {readOnly: true});
            done(true);
          };
          document.head.append(script);
        """, source)
    result = WebDriverWait(browser, 8).until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const root = window.svgPreviewTestRoot || panel?._pmView?.dom;
      if (!root) return false;
      const images = [...root.querySelectorAll('img.markdown-preview-image')];
      if (images.length !== 3 || !images.every(image => image.naturalWidth > 0)) return false;
      return images.map(image => {
        const box = image.getBoundingClientRect();
        return {width: box.width, height: box.height, paragraph: image.parentElement.clientWidth};
      });
    """, item))
    wide, inline, sized = result
    assert abs(wide['width'] - wide['paragraph']) <= 1, result
    assert abs(wide['height'] * 2 - wide['width']) <= 1, result
    assert inline['width'] == 110 and sized['width'] == 48, result


def visible_markdown_source_selection(browser, item, start, markup, source):
    return browser.execute_script("""
      const [item, start, markup, source] = arguments;
      const cm = panelNodes.get(item)._cmView;
      const doc = cm.state.doc, selection = cm.state.selection.main;
      const viewport = cm.scrollDOM.getBoundingClientRect();
      const visible = [...cm.dom.querySelectorAll('.cm-selectionBackground')].some(node => {
        const bounds = node.getBoundingClientRect();
        return bounds.width > 0 && bounds.top < viewport.bottom && bounds.bottom > viewport.top &&
          getComputedStyle(node).backgroundColor !== 'rgba(0, 0, 0, 0)';
      });
      return visible && !cm.hasFocus && selection.from === start &&
        doc.sliceString(selection.from, selection.to) === markup && doc.toString() === source;
    """, item, start, markup, source)


def test_markdown_viewedit_clicking_image_selects_its_exact_source(browser, tmp_path):
    uri = ('data:image/png;base64,'
           'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    inline = f'![Pixel]({uri})'
    reference = '![Reference][p]'
    html = f'<img alt="HTML image" src="{uri}" width="32">'
    standalone = f'<img alt="Standalone image" src="{uri}" width="32">'
    source = ('<!--\n' + 'Hidden source line\n' * 90 + '-->\n\n'
              'First rendered line.<br>\nSecond rendered line.\n\n'
              f'Before {inline} and {inline} then {html}.\n\n'
              f'{standalone}\n\n'
              f'| Image |\n| --- |\n| {reference} |\n\n[p]: {uri}\n')
    item = mount_markdown_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 6)
    images = wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      const images = [...panel?._pmView?.dom.querySelectorAll('img.prosemirror-image') || []];
      return images.length === 5 && images.every(img => img.naturalWidth) && images;
    """, item))
    for index, markup, start in [
        (1, inline, source.index(inline, source.index(inline) + len(inline))),
        (2, html, source.index(html)),
        (3, standalone, source.index(standalone)),
        (4, reference, source.index(reference)),
    ]:
        images[index].click()
        wait.until(lambda driver: visible_markdown_source_selection(driver, item, start, markup, source),
                   f'No visible matching source highlight for image {index}')


def test_markdown_image_source_highlight_follows_selection(browser, tmp_path):
    markup = ('<img alt="Pixel" width="32" src="data:image/png;base64,'
              'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=">')
    item = mount_markdown_split(browser, tmp_path, f'# Example\n\nBefore {markup} after.\n')
    wait = WebDriverWait(browser, 6)
    image = wait.until(lambda driver: driver.execute_script("""
      const image = panelNodes.get(arguments[0])?._pmView?.dom.querySelector('img');
      return image?.naturalWidth && image;
    """, item))
    heading = browser.find_element(By.CSS_SELECTOR, '.ProseMirror h1')

    def selection():
        return browser.execute_script("""
          const view = panelNodes.get(arguments[0])._cmView;
          const {from, to} = view.state.selection.main;
          return view.state.doc.sliceString(from, to);
        """, item)

    image.click()
    wait.until(lambda _: selection() == markup)
    ActionChains(browser).move_to_element(heading).perform()
    browser.execute_async_script("requestAnimationFrame(() => arguments[0]())")
    assert selection() == markup, 'Moving the pointer must preserve the selected image source'
    browser.execute_script("panelNodes.get(arguments[0])._pmView.dom.blur()", item)
    assert selection() == markup, 'Losing focus alone must preserve the selected image source'
    browser.execute_script("panelNodes.get(arguments[0])._pmView.focus()", item)
    native_logical_key(browser, 'ArrowRight')
    wait.until(lambda _: selection() == '', 'Keyboard movement away from the image must clear its source highlight')
    image.click()
    wait.until(lambda _: selection() == markup)
    heading.click()
    wait.until(lambda _: selection() == '', 'Clicking prose must clear the image source highlight')
    image.click()
    wait.until(lambda _: selection() == markup)
    browser.execute_script("""
      panelNodes.get(arguments[0])._cmView.dispatch({selection: {anchor: 2, head: 9}});
    """, item)
    heading.click()
    assert selection() == 'Example'


def test_markdown_viewedit_equations_select_their_exact_source(browser, tmp_path):
    inline = '$x^2$'
    parens = r'\(y + 1\)'
    display = '$$\nz^2\n$$'
    quoted = '> \\[\n> u^2\n> \\]'
    listed = '- $$\n  v + 1\n  $$'
    table = r'$a \| b$'
    source = (f'# Math\n\nLiteral `{inline}` and <code>{inline}</code>, then {inline} and {inline}, then {parens}.\n\n'
              f'{display}\n\n{quoted}\n\n{listed}\n\n| Equation |\n| --- |\n| {table} |\n')
    item = mount_markdown_split(browser, tmp_path, source, math=True)
    wait = WebDriverWait(browser, 6)
    equations = wait.until(lambda driver: driver.execute_script("""
      const equations = [...panelNodes.get(arguments[0])?._pmView?.dom.querySelectorAll('[data-tex-source] .katex-html') || []];
      return equations.length === 7 && equations;
    """, item))

    def selection():
        return browser.execute_script("""
          const cm = panelNodes.get(arguments[0])._cmView, {from, to} = cm.state.selection.main;
          return [from, cm.state.doc.sliceString(from, to), cm.state.doc.toString()];
        """, item)

    for index, (markup, start) in enumerate([
        (inline, source.index(inline, source.index('then'))),
        (inline, source.index(inline, source.index('and', source.index('then')))),
        (parens, source.index(parens)),
        (display, source.index(display)),
        (quoted[2:], source.index(quoted) + 2),
        (listed[2:], source.index(listed) + 2),
        (table, source.index(table)),
    ]):
        equations[index].click()
        assert selection() == [start, markup, source], (index, selection())
        wait.until(lambda driver: visible_markdown_source_selection(driver, item, start, markup, source))
    heading = browser.find_element(By.CSS_SELECTOR, '.ProseMirror h1')
    ActionChains(browser).move_to_element(heading).perform()
    assert selection() == [source.index(table), table, source]
    heading.click()
    wait.until(lambda _: selection()[1] == '')


def test_markdown_viewedit_save_preserves_explicit_html_breaks(browser, tmp_path):
    code = '```html\n<span>Literal</span><br>\n' + 'continued ' + '\\' + '\nline\n```'
    source = f'# Example\n\nOne<br>\nTwo\n\nThree<br />\n<br>\nFour\n\n{code}\n\n<!-- Keep this -->\n'
    item = mount_markdown_split(browser, tmp_path, source)
    WebDriverWait(browser, 6).until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel?._pmView;
      if (!panel?._cmView || !view?.editable) return false;
      const heading = view.dom.querySelector('h1');
      const position = view.posAtDOM(heading, 0) + 1;
      view.focus();
      setProseMirrorSelection(view, position, position);
      return view.hasFocus();
    """, item))
    native_logical_key(browser, 'x')
    state = browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), view = panel._pmView;
      flushProseMirrorSource(panel, panel.dataset.filePath);
      const source = panel._cmView.state.doc.toString();
      const reopened = panel._pmParser.parse(source);
      return {source, same: reopened.eq(view.state.doc),
        rows: [...view.dom.querySelectorAll('p')].map(p => p.querySelectorAll('br').length)};
    """, item)
    assert state['same'] and state['rows'] == [1, 2], state
    assert state['source'].startswith('# Exxample\n') and '<!-- Keep this -->' in state['source'], state
    assert code in state['source'], state


def test_viewedit_link_hover_reveals_destination_after_one_and_a_half_seconds(browser, tmp_path):
    url = 'https://example.test/docs?q=a&amp;b#topic'
    item = mount_markdown_split(browser, tmp_path, f'[**Docs**]({url}) and [Local](./local.md)')
    WebDriverWait(browser, 4).until(lambda driver: driver.execute_script("""
      const link = panelNodes.get(arguments[0])?._pmView?.dom.querySelector('a');
      if (!link) return false;
      const rect = link.getBoundingClientRect();
      return link.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
    """, item))
    links = browser.find_elements(By.CSS_SELECTOR, '[data-prosemirror-editor] a[href]')
    browser.execute_script("""
      panelNodes.get(arguments[0])._pmView.dom.addEventListener('pointerover', event => {
        if (event.target.closest('a')) window.__linkHoveredAt = performance.now();
      });
    """, item)
    ActionChains(browser).move_to_element(links[0]).perform()
    assert browser.execute_script('return arguments[0].matches(":hover") && !!window.__linkHoveredAt', links[0])
    popup = browser.execute_async_script("""
      const done = arguments[0];
      window.__yomuxTestWaitFor(() => document.querySelector('.viewedit-link-tooltip'), {timeoutMs: 4000})
        .then(node => done({text: node.textContent, elapsed: performance.now() - window.__linkHoveredAt,
          visible: Boolean(node.getClientRects().length)}), error => done({error: String(error)}));
    """)
    assert popup.get('visible') is True and popup['elapsed'] >= 1500, popup
    assert popup['text'] == 'https://example.test/docs?q=a&b#topic'

    between = browser.execute_script("""
      const text = document.createTreeWalker(arguments[0].closest('p'), NodeFilter.SHOW_TEXT);
      let node;
      while ((node = text.nextNode()) && !node.data.includes(' and ')) {}
      const range = document.createRange();
      range.setStart(node, 2);
      range.setEnd(node, 3);
      const rect = range.getBoundingClientRect();
      return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
    """, links[0])
    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {'type': 'mouseMoved', **between})
    action = browser.find_element(By.CSS_SELECTOR, '.viewedit-link-tooltip a')
    ActionChains(browser).move_to_element(action).perform()
    assert action.is_displayed()

    ActionChains(browser).move_to_element(links[1]).perform()
    assert not browser.find_elements(By.CSS_SELECTOR, '.viewedit-link-tooltip')
    tooltip = WebDriverWait(browser, 4).until(
        lambda driver: driver.find_element(By.CSS_SELECTOR, '.viewedit-link-tooltip'))
    assert tooltip.text == './local.md'

    source = browser.execute_script('return panelNodes.get(arguments[0])._cmView.contentDOM.querySelector(".cm-line")', item)
    ActionChains(browser).move_to_element(source).perform()
    assert browser.execute_async_script("""
      setTimeout(() => arguments[0](Boolean(document.querySelector('.viewedit-link-tooltip'))), 1650);
    """) is False


def test_markdown_table_selection_hides_block_style_dropdowns(browser, tmp_path):
    item = mount_markdown_split(browser, tmp_path, 'Before\n\n| Header |\n| --- |\n| Cell |\n\nAfter\n')
    wait = WebDriverWait(browser, 5)
    wait.until(lambda driver: driver.execute_script("""
      const panel = panelNodes.get(arguments[0]);
      return !!panel?._pmView && !!panel._cmView && !!panel.querySelector('.markdown-formatting-toolbar');
    """, item))
    for surface in ('view-editor', 'text-editor'):
        for target, show_styles in [('Before', True), ('Header', False), ('Cell', False), ('After', True)]:
            state = browser.execute_script("""
              const [item, surface, target] = arguments;
              const panel = panelNodes.get(item), view = panel._pmView, cm = panel._cmView;
              if (surface === 'view-editor') {
                const node = [...view.dom.querySelectorAll('p,th,td')].find(node => node.textContent === target);
                const position = view.posAtDOM(node, 0) + 1;
                view.focus();
                setProseMirrorSelection(view, position, position);
              } else {
                const position = cm.state.doc.toString().indexOf(target) + 1;
                cm.focus();
                cm.dispatch({selection: {anchor: position}});
              }
              const visible = selector => Boolean(panel.querySelector(selector).getClientRects().length);
              return [markdownFormattingTarget(panel),
                visible('[data-format-group="paragraph"]'), visible('[data-format-group="list"]'),
                visible('[data-markdown-format="bold"]')];
            """, item, surface, target)
            assert state == [surface, show_styles, show_styles, True], (surface, target, state)


def test_viewedit_table_up_down_stays_in_the_same_column(browser, tmp_path):
    source = '| Left | Middle | Right |\n| --- | --- | --- |\n| One | **Target** | Three |\n| Four | x | Six |\n| Seven | | Nine |\n\nAfter\n'
    item = mount_markdown_split(browser, tmp_path, source)
    wait = WebDriverWait(browser, 5)
    wait.until(lambda driver: driver.execute_script("""
      const view = panelNodes.get(arguments[0])?._pmView;
      const cell = view?.dom.querySelector('tr:nth-child(2) td:nth-child(2)');
      if (!cell) return false;
      view.focus();
      const start = view.posAtDOM(cell, 0);
      setProseMirrorSelection(view, start + 3, start + 3);
      return view.hasFocus();
    """, item))
    for key, row, offset in [
        (Keys.ARROW_DOWN, 2, 1), (Keys.ARROW_DOWN, 3, 0), (Keys.ARROW_DOWN, 3, 0),
        (Keys.ARROW_UP, 2, 0), (Keys.ARROW_UP, 1, 0), (Keys.ARROW_UP, 0, 0), (Keys.ARROW_UP, 0, 0),
    ]:
        ActionChains(browser).send_keys(key).perform()
        caret = """
          const panel = panelNodes.get(arguments[0]), view = panel?._pmView;
          if (!view) return null;
          const {selection} = view.state, cell = view.domAtPos(selection.head).node;
          const element = cell.nodeType === 1 ? cell : cell.parentElement;
          const target = element.closest('td,th');
          return [target?.parentElement.rowIndex, target?.cellIndex,
            selection.$head.parentOffset, panel._cmView.state.doc.toString()];
        """
        # A native arrow key can settle after send_keys returns on a loaded host.
        expected = [row, 1, offset, source]
        try:
            wait.until(lambda driver: driver.execute_script(caret, item) == expected)
        except TimeoutException as error:
            raise AssertionError(f'{key!r}: {browser.execute_script(caret, item)} != {expected}') from error


def test_viewedit_table_menu_edits_clicked_cell_and_round_trips(browser, tmp_path):
    markdown = '| A | B |\n| :--- | ---: |\n| **C** | D |\n| E | F |\n\n| Only |\n| --- |\n'
    item = mount_markdown_split(browser, tmp_path, markdown)
    original = [['A', 'B'], ['C', 'D'], ['E', 'F']]
    cases = [
        ('Insert column left', 0, 1, 1, [['A', '', 'B'], ['C', '', 'D'], ['E', '', 'F']]),
        ('Insert column right', 0, 1, 1, [['A', 'B', ''], ['C', 'D', ''], ['E', 'F', '']]),
        ('Insert row above', 0, 0, 1, [['', ''], *original]),
        ('Insert row below', 0, 1, 1, [*original[:2], ['', ''], original[2]]),
        ('Delete row', 0, 0, 1, original[1:]),
        ('Delete column', 0, 1, 1, [['A'], ['C'], ['E']]),
        ('Delete row', 1, 0, 0, None),
        ('Delete column', 1, 0, 0, None),
    ]
    for label, table, row, column, expected in cases:
        cell = WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("""
          const [item, table, row, column] = arguments;
          const view = panelNodes.get(item)?._pmView;
          return view?.dom.querySelectorAll('table')[table]?.rows[row]?.cells[column] || false;
        """, item, table, row, column))
        ActionChains(browser).context_click(cell).perform()
        menu = WebDriverWait(browser, 4).until(
            lambda driver: driver.find_element(By.CSS_SELECTOR, '.markdown-table-context-menu.context-menu-icon-rows'))
        buttons = menu.find_elements(By.CSS_SELECTOR, 'button')
        assert [button.text for button in buttons[-2:]] == ['Delete row', 'Delete column']
        geometry = browser.execute_script("""
          return [...arguments[0].querySelectorAll('.tab-directional-action-icon')].map(icon => {
            const box = icon.getBoundingClientRect();
            const gutter = icon.parentElement.getBoundingClientRect();
            const label = icon.closest('.context-menu-line').querySelector('.context-menu-label').getBoundingClientRect();
            return {direction: icon.className, width: box.width, height: box.height,
              gap: label.left - box.right,
              verticalOverflow: Math.max(gutter.top - box.top, box.bottom - gutter.bottom),
              centerError: Math.abs((box.top + box.bottom) - (label.top + label.bottom)) / 2};
          });
        """, menu)
        assert len(geometry) == 4, geometry
        for icon in geometry:
            assert icon['width'] > icon['height'] > 0 and icon['gap'] >= 2, icon
            assert icon['verticalOverflow'] <= 1 and icon['centerError'] <= 1, icon
        action = next(button for button in buttons if button.text == label)
        assert action.find_elements(By.CSS_SELECTOR, '.tab-directional-action-icon, [data-menu-icon="delete"]')
        action.click()
        state = browser.execute_script("""
          const panel = panelNodes.get(arguments[0]), view = panel._pmView;
          flushProseMirrorSource(panel, panel.dataset.filePath);
          const source = panel._cmView.state.doc.toString();
          return {
            tables: [...view.dom.querySelectorAll('table')].map(table => [...table.rows].map(row =>
              [...row.cells].map(cell => cell.textContent))),
            headerCounts: [...view.dom.querySelectorAll('table')].map(table => [...table.rows].map(row =>
              [...row.cells].filter(cell => cell.tagName === 'TH').length)),
            roundTrip: panel._pmParser.parse(source).eq(view.state.doc),
            source,
          };
        """, item)
        assert state['tables'] == ([original] if expected is None else [expected, [['Only']]]), (label, state)
        assert state['headerCounts'] == [[len(rows[0]), *([0] * (len(rows) - 1))] for rows in state['tables']], state
        assert state['roundTrip'], (label, state)
        native_logical_key(browser, 'z', [native_editor_modifier(browser)])
        assert browser.execute_script("return serializeProseMirrorSource(panelNodes.get(arguments[0]))", item) == markdown


def test_viewedit_table_menu_offers_inline_formatting_and_column_alignment(browser, tmp_path):
    markdown = 'Outside text\n\n| A | B |\n| :--- | ---: |\n| Cell | Target |\n'
    item = mount_markdown_split(browser, tmp_path, markdown)
    wait = WebDriverWait(browser, 5)
    target = wait.until(lambda driver: driver.execute_script("""
      return panelNodes.get(arguments[0])?._pmView?.dom.querySelector('td:last-child');
    """, item))

    for previous, label, alignment, delimiter in [
        ('Align column right', 'Center column', 'center', ':---:'),
        ('Center column', 'Align column left', 'left', ':---'),
        ('Align column left', 'Default column alignment', '', '---'),
        ('Default column alignment', 'Align column right', 'right', '---:'),
    ]:
        ActionChains(browser).context_click(target).perform()
        menu = wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.markdown-table-context-menu'))
        commands = {button.get_attribute('data-markdown-command')
                    for button in menu.find_elements(By.CSS_SELECTOR, '[data-markdown-command]')}
        assert commands == {'bold', 'italic', 'underline', 'strike', 'code', 'clearInline'}
        buttons = {button.get_attribute('aria-label'): button for button in menu.find_elements(By.CSS_SELECTOR, 'button')}
        assert 'Paste' not in buttons and 'Add URL' in buttons
        assert buttons[previous].get_attribute('aria-checked') == 'true'
        assert buttons[label].get_attribute('aria-checked') == 'false'
        buttons[label].click()
        state = browser.execute_script(r"""
          const panel = panelNodes.get(arguments[0]), view = panel._pmView;
          flushProseMirrorSource(panel, panel.dataset.filePath);
          const source = panel._cmView.state.doc.toString();
          return {align: [...view.dom.querySelectorAll('tr')].map(row => [...row.cells].map(cell => cell.style.textAlign)),
            delimiter: source.split('\n').find(line => line.startsWith('| :--- |')),
            roundTrip: panel._pmParser.parse(source).eq(view.state.doc)};
        """, item)
        assert state == {'align': [['left', alignment], ['left', alignment]],
                         'delimiter': f'| :--- | {delimiter} |', 'roundTrip': True}
        target = browser.execute_script("""
          return panelNodes.get(arguments[0])._pmView.dom.querySelector('td:last-child');
        """, item)

    outside = browser.execute_script("""
      const view = panelNodes.get(arguments[0])._pmView;
      const outside = view.dom.querySelector('p');
      const start = view.posAtDOM(outside, 0);
      setProseMirrorSelection(view, start, start + outside.textContent.length);
      return outside;
    """, item)
    ActionChains(browser).context_click(target).perform()
    menu = wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.markdown-table-context-menu'))
    menu.find_element(By.CSS_SELECTOR, '[data-markdown-command="bold"]').click()
    assert browser.execute_script("""
      const panel = panelNodes.get(arguments[0]), dom = panel._pmView.dom;
      return dom.querySelector('td:last-child strong')?.textContent === 'Target' &&
        !dom.querySelector('p strong') && serializeProseMirrorSource(panel).includes('**Target**');
    """, item)
    ActionChains(browser).context_click(outside).perform()
    menu = wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '.markdown-preview-context-menu'))
    assert len(menu.find_elements(By.CSS_SELECTOR, '[data-markdown-command="h1"], [data-markdown-command="bullet"]')) == 2


def test_markdown_native_bold_second_occurrence(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    native_logical_key(browser, 'b', ['ctrl'])
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same **same**'


def test_markdown_split_source_updates_preview_before_serializing(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    original = browser.execute_script("""
      const panel = formattingPanel, view = panel._cmView;
      view.focus();
      view.dispatch({selection: {anchor: view.state.doc.length}});
      window.__commitBeforePreviewTest = commitCodeMirrorSource;
      commitCodeMirrorSource = () => false;
      return fileEditorPanelState(panel).content;
    """)
    try:
        native_logical_key(browser, 'x')
        result = browser.execute_async_script("""
          const done = arguments[0], panel = formattingPanel;
          window.__yomuxTestWaitFor(() => panel._pmView.dom.textContent.endsWith('x'), {
            timeoutMs: 1500, description: 'preview refresh while canonical commit is held'
          }).then(() => done({
            text: panel._cmView.state.doc.toString(), canonical: fileEditorPanelState(panel).content
          }), error => done(String(error)));
        """)
        assert result == {'text': original + 'x', 'canonical': original}, result
    finally:
        browser.execute_script("""
          const panel = formattingPanel;
          commitCodeMirrorSource = window.__commitBeforePreviewTest;
          flushCodeMirrorSource(panel, panel._cmPath);
        """)
    assert browser.execute_script('return fileEditorPanelState(formattingPanel).content') == original + 'x'


def test_markdown_split_undo_reconciles_preview_before_editing_it(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    original = browser.execute_script("""
      const panel = formattingPanel, view = panel._cmView;
      view.focus();
      view.dispatch({selection: {anchor: view.state.doc.length}});
      window.__commitBeforeUndoTest = commitCodeMirrorSource;
      commitCodeMirrorSource = () => false;
      return fileEditorPanelState(panel).content;
    """)
    try:
        native_logical_key(browser, 'x')
        result = browser.execute_async_script("""
          const done = arguments[0];
          window.__yomuxTestWaitFor(() => formattingPanel._pmView.dom.textContent.endsWith('x')).then(() => {
            window.__scheduleBeforeUndoTest = scheduleCodeMirrorSplitPreview;
            scheduleCodeMirrorSplitPreview = () => true;
            commitCodeMirrorSource = window.__commitBeforeUndoTest;
            done(true);
          }, error => done(String(error)));
        """)
        assert result is True, result
        native_logical_key(browser, 'z', [native_editor_modifier(browser)])
        result = browser.execute_script("""
          const panel = formattingPanel;
          panel._pmView.focus();
          return [panel._cmView.state.doc.toString(), panel._pmView.dom.textContent];
        """)
        assert result == [original, original], result
    finally:
        browser.execute_script("""
          commitCodeMirrorSource = window.__commitBeforeUndoTest;
          if (window.__scheduleBeforeUndoTest) scheduleCodeMirrorSplitPreview = window.__scheduleBeforeUndoTest;
        """)


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_native_logical_shortcut_keeps_key_distinct_from_physical_code(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view=arguments[0]==='text-editor'?formattingPanel._cmView:formattingPanel._pmView;
      if(view===formattingPanel._cmView)view.dispatch({selection:{anchor:5,head:9}});
      view.focus();window.__logicalShortcutEvents=[];
      window.addEventListener('keydown',event=>window.__logicalShortcutEvents.push({key:event.key,
        code:event.code,ctrl:event.ctrlKey,meta:event.metaKey,trusted:event.isTrusted}),{capture:true});
    """, surface)
    native_logical_key(browser, 'b', ['ctrl'], code='KeyN')
    result = browser.execute_script("""
      return {events:window.__logicalShortcutEvents,source:arguments[0]==='text-editor'
        ?formattingPanel._cmView.state.doc.toString():serializeProseMirrorSource(formattingPanel)};
    """, surface)
    assert result['events'] == [{'key': 'b', 'code': 'KeyN', 'ctrl': True, 'meta': False, 'trusted': True}], result
    assert result['source'] == 'same **same**', result


@pytest.mark.parametrize('surface', ['text-editor', 'view-editor'])
def test_markdown_dvorak_copy_and_italic_follow_layout(browser, tmp_path, surface):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      history.replaceState(null, '', location.pathname + '?platform=mac');
      const panel = formattingPanel;
      const view = arguments[0] === 'text-editor' ? panel._cmView : panel._pmView;
      if (view === panel._cmView) view.dispatch({selection:{anchor:5,head:9}});
      view.focus();
      window.dvorakCopyEvents = [];
      document.addEventListener('copy', () => dvorakCopyEvents.push(window.getSelection().toString()), {once:true});
    """, surface)
    # Dvorak C occupies the physical QWERTY I key. The character is Copy;
    # treating its physical code as the shortcut instead applies Italic.
    # CDP needs the native editing command on macOS; Chromium runs it only
    # when the keydown was not canceled by the application.
    browser.execute_cdp_cmd('Input.dispatchKeyEvent', {
        'type': 'keyDown', 'key': 'c', 'code': 'KeyI', 'modifiers': 4,
        'windowsVirtualKeyCode': 67, 'nativeVirtualKeyCode': 34,
        'commands': ['copy'],
    })
    result = browser.execute_script("""
      const panel = formattingPanel;
      return {source: arguments[0] === 'text-editor' ? panel._cmView.state.doc.toString() : serializeProseMirrorSource(panel),
        copies: dvorakCopyEvents};
    """, surface)
    assert result == {'source': 'same same', 'copies': ['same']}, result
    # Dvorak I occupies QWERTY G. Formatting must follow the same logical key.
    browser.execute_cdp_cmd('Input.dispatchKeyEvent', {
        'type': 'keyDown', 'key': 'i', 'code': 'KeyG', 'modifiers': 4,
        'windowsVirtualKeyCode': 73, 'nativeVirtualKeyCode': 5,
    })
    assert browser.execute_script("""
      return arguments[0] === 'text-editor' ? formattingPanel._cmView.state.doc.toString() : serializeProseMirrorSource(formattingPanel);
    """, surface) == 'same *same*'


def test_markdown_letter_shortcuts_ignore_physical_layout(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    mismatches = browser.execute_script("""
      const failures = [], letters = 'abcdefghijklmnopqrstuvwxyz';
      const expected = {b:'bold',i:'italic',u:'underline'};
      for (const mac of [true,false]) for (const key of letters) for (const physical of letters) {
        const actual = markdownFormatShortcutCommand({key,code:`Key${physical.toUpperCase()}`,
          metaKey:mac,ctrlKey:!mac},mac)?.id || null;
        if (actual !== (expected[key] || null)) failures.push({mac,key,physical,actual});
      }
      const symbols = [
        [{key:'Ç',code:'KeyC',metaKey:true,altKey:true,shiftKey:true},true,'pre'],
        [{key:'&',code:'Digit7',metaKey:true,shiftKey:true},true,'number'],
        [{key:'%',code:'Digit5',altKey:true,shiftKey:true},false,'strike'],
      ];
      for (const [event,mac,expected] of symbols) {
        const actual = markdownFormatShortcutCommand(event,mac)?.id;
        if (actual !== expected) failures.push({event,actual,expected});
      }
      return failures;
    """)
    assert mismatches == [], mismatches


def test_markdown_source_table_and_setext_native_formatting(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._cmView;
      const source = 'a | b\\n--|--\\nx|y';
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:source},selection:{anchor:0,head:source.length}});
      view.focus();
    """)
    native_logical_key(browser, 'b', ['ctrl'])
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
    native_logical_key(browser, '1', ['ctrl', 'alt'])
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == '# title'
    reference_image = ('![ref][]\n\n[ref]: data:image/png;base64,'
                       'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
    browser.execute_script("""
      const view = formattingPanel._cmView;
      const source = arguments[0];
      view.dispatch({changes:{from:0,to:view.state.doc.length,insert:source},selection:{anchor:0,head:source.length}});
      view.focus();
    """, reference_image)
    native_logical_key(browser, 'b', ['ctrl'])
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == reference_image


def test_markdown_native_source_range_and_typing_toggle(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._cmView;
      view.dispatch({selection:{anchor:9,head:5}}); view.focus();
    """)
    native_logical_key(browser, 'b', ['ctrl'])
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same **same**'
    native_logical_key(browser, 'b', ['ctrl'])
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same same'
    browser.execute_script('formattingPanel._cmView.dispatch({selection:{anchor:9}})')
    native_logical_key(browser, 'i', ['ctrl'])
    native_logical_key(browser, 'X')
    native_logical_key(browser, 'i', ['ctrl'])
    native_logical_key(browser, 'Y')
    assert browser.execute_script('return formattingPanel._cmView.state.doc.toString()') == 'same same*X*Y'


def test_markdown_native_viewedit_typing_marks_toolbar_and_undo(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      const view = formattingPanel._pmView;
      view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc,10)));
      view.focus();
    """)
    native_logical_key(browser, 'b', ['ctrl'])
    native_logical_key(browser, 'X')
    native_logical_key(browser, 'b', ['ctrl'])
    native_logical_key(browser, 'Y')
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same same**X**Y'
    browser.execute_script("""
      const view = formattingPanel._pmView;
      view.dispatch(view.state.tr.setSelection(YOmuxProseMirror.TextSelection.create(view.state.doc,6,10))); view.focus();
    """)
    browser.find_element('css selector', '.markdown-formatting-toolbar [data-markdown-format="italic"]').click()
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)') == 'same *same***X**Y'
    assert browser.execute_script('return formattingPanel._pmView.state.selection.from') == 6
    native_logical_key(browser, 'z', [native_editor_modifier(browser)])
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
    ('i', ['ctrl'], '*same*'),
    ('u', ['ctrl'], '<u>same</u>'),
    ('5', ['alt', 'shift'], '~~same~~'),
    ('6', ['ctrl', 'alt'], '###### same same'),
])
def test_markdown_platform_native_shortcut_matrix(browser, tmp_path, surface, key, modifiers, expected):
    setup_formatting_editor(browser, tmp_path)
    browser.execute_script("""
      if (arguments[0] === 'text-editor') {
        const view = formattingPanel._cmView;
        view.dispatch({selection:{anchor:5,head:9}}); view.focus();
      } else formattingPanel._pmView.focus();
    """, surface)
    native_logical_key(browser, key, modifiers)
    actual = browser.execute_script("""
      return arguments[0] === 'text-editor' ? formattingPanel._cmView.state.doc.toString() : serializeProseMirrorSource(formattingPanel);
    """, surface)
    assert actual == (expected if key == '6' else 'same ' + expected), actual


def test_markdown_format_delayed_split_save_reopen(browser, tmp_path):
    setup_formatting_editor(browser, tmp_path)
    native_logical_key(browser, 'b', ['ctrl'])
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
    native_logical_key(browser, 'b', ['ctrl'])
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
    native_logical_key(browser, '\\', ['ctrl'])
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
    native_logical_key(browser, ']', ['ctrl'])
    assert browser.execute_script('return serializeProseMirrorSource(formattingPanel)').startswith('- first\n  * second')
    native_logical_key(browser, '[', ['ctrl'])
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
    native_logical_key(browser, 'c', ['ctrl', 'alt', 'shift'])
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
    native_logical_key(browser, 'z', [native_editor_modifier(browser)])
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
    native_logical_key(browser, '\\', ['ctrl'])
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
    native_logical_key(browser, '\\', ['ctrl'])
    native_logical_key(browser, 'X')
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
