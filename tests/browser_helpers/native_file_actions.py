# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native Finder actions preserve the same live input through metadata publication."""

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.native_keyboard import native_editor_modifier, native_logical_key, native_logical_text
from tests.browser_helpers.native_pointer import click_visible_selector


def rename_finder_file_with_native_input(browser, path, new_name):
    row = WebDriverWait(browser, 2).until(lambda driver: driver.execute_script("""
      return [...document.querySelectorAll('#panel-__finder__ .file-tree-row[data-path]')]
        .find(row=>row.dataset.path===arguments[0]) || null;
    """, path))
    ActionChains(browser).context_click(row).perform()
    click_visible_selector(browser, '.file-context-menu button:has([data-menu-icon="rename"])')
    rename_input = WebDriverWait(browser, 2).until(lambda driver: driver.execute_script("""
      const input=document.querySelector('#panel-__finder__ .file-tree-rename-input');
      return input && input===document.activeElement ? input : null;
    """))
    native_logical_key(browser, 'a', [native_editor_modifier(browser)])
    native_logical_text(browser, new_name)
    state = browser.execute_script("""
      const input=arguments[0],row=input.closest('.file-tree-row');
      input.setSelectionRange(2,5);
      const before={value:input.value,start:input.selectionStart,end:input.selectionEnd};
      // Both the background metadata path and full row publication share this editing owner.
      updateFileTreeGitStatusRows([row]);
      const metadata={sameInput:row.querySelector('.file-tree-rename-input')===input,
        focused:document.activeElement===input,value:input.value,start:input.selectionStart,end:input.selectionEnd};
      updateFileTreeRow(row,dirnameOf(row.dataset.path),{name:row.dataset.name,kind:row.dataset.kind},0);
      return {before,metadata,render:{sameInput:row.querySelector('.file-tree-rename-input')===input,
        focused:document.activeElement===input,value:input.value,start:input.selectionStart,end:input.selectionEnd,
        draggable:row.draggable}};
    """, rename_input)
    expected = {'sameInput':True,'focused':True,'value':new_name,'start':2,'end':5}
    assert state['before'] == {'value':new_name,'start':2,'end':5}, state
    assert state['metadata'] == expected, state
    assert state['render'] == {**expected,'draggable':False}, state
    native_logical_key(browser, 'Enter')
