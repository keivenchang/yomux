# SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Native filesystem event delivery through the rendered Finder."""

import json
import shlex
import tempfile
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode

import pytest

from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from tests.browser_helpers.browser_layout import browser  # noqa: F401
from tests.isolated_dev_server import isolated_dev_server_factory  # noqa: F401
from tests.gate_harness import run_fixture_cleanup_phases
from tests.tmux_runtime import run_isolated_tmux
from tests.browser_helpers.visual_contracts import wcag_contrast_ratio
from tests.browser_helpers.browser_console import assert_only_expected_browser_http_error
from tests.browser_helpers.browser_console import consume_only_expected_js_debug_api_error
from tests.helpers.terminal_url_fixtures import terminal_url_matrix_examples
from tools.product_environment import product_env


@pytest.mark.browser
@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_native_finder_get_info_permissions(browser, isolated_dev_server_factory, tmp_path, request, theme):
    server = isolated_dev_server_factory('get-info-' + theme)
    request.addfinalizer(lambda: browser.get('about:blank'))
    directory = tmp_path / 'info-folder'
    directory.mkdir()
    target = directory / '.hidden-file  spaces'
    target.write_bytes(b'contents')
    target.chmod(0o640)
    original_size = browser.get_window_size()
    request.addfinalizer(lambda: browser.set_window_size(original_size['width'], original_size['height']))
    browser.set_window_size(1280, 1200)
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script(
        "return typeof showFileGetInfo === 'function' && typeof selectSession === 'function' && initialAppShellPainted"
    ))
    browser.execute_script("document.body.classList.toggle('theme-light', arguments[0] === 'light'); showFileGetInfo(arguments[1]);", theme, str(target))
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '.file-get-info [data-permission-bit]')) == 9)
    dialog = browser.find_element(By.CSS_SELECTOR, '.file-get-info')
    assert str(target) in dialog.text
    assert '-rw-r-----' in dialog.text
    assert '0640' in dialog.text
    assert 'OS checks use the account running YOmux' in dialog.text
    assert 'Resolved path' not in dialog.text
    assert 'Symlink target' not in dialog.text
    assert 'Octal mode' not in dialog.text
    geometry = browser.execute_script("""
      const dialog=document.querySelector('.file-get-info .file-editor-dialog'),body=dialog.querySelector('.file-editor-dialog-body');
      return {height:dialog.getBoundingClientRect().height,width:dialog.getBoundingClientRect().width,
        font:parseFloat(getComputedStyle(body).fontSize),normal:parseFloat(getComputedStyle(document.body).getPropertyValue('--ui-font-size')),
        background:getComputedStyle(dialog).backgroundColor,
        colors:[...body.querySelectorAll('dt,dd,p,th,legend,h3')].map(node=>getComputedStyle(node).color)};
    """)
    assert geometry['font'] >= geometry['normal']
    assert geometry['height'] <= 650.203125 * 0.8, geometry
    assert geometry['width'] <= 640
    assert all(wcag_contrast_ratio(color, geometry['background']) >= 4.5 for color in geometry['colors']), geometry
    browser.find_element(By.CSS_SELECTOR, '.file-get-info input[aria-label="Owner Execute"]').click()
    assert target.stat().st_mode & 0o777 == 0o640
    button_geometry = browser.execute_script("""
      const table=document.querySelector('.file-info-access-controls table').getBoundingClientRect();
      const button=document.querySelector('[data-permission-apply]'),rect=button.getBoundingClientRect();
      return {buttonCount:document.querySelectorAll('[data-permission-apply]').length,left:rect.left,right:table.right,
        center:(rect.top+rect.bottom)/2,tableCenter:(table.top+table.bottom)/2};
    """)
    assert button_geometry['buttonCount'] == 1, button_geometry
    assert button_geometry['left'] >= button_geometry['right'], button_geometry
    assert abs(button_geometry['center'] - button_geometry['tableCenter']) < 12, button_geometry
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-permission-apply]').click()
    wait.until(lambda driver: driver.find_element(By.CSS_SELECTOR, '[data-info-result]').text and 'Applying' not in driver.find_element(By.CSS_SELECTOR, '[data-info-result]').text)
    assert 'Changed: 1' in browser.find_element(By.CSS_SELECTOR, '[data-info-result]').text
    assert target.stat().st_mode & 0o777 == 0o740
    assert '0740' in browser.find_element(By.CSS_SELECTOR, '[data-info-permissions]').text
    browser.execute_script('showFileGetInfo(arguments[0]);', str(directory))
    wait.until(lambda driver: 'Total:' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert '1 files' in browser.find_element(By.CSS_SELECTOR, '[data-info-size]').text
    assert browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').is_displayed() is False
    browser.find_element(By.CSS_SELECTOR, '[data-permission-recursive]').click()
    browser.find_element(By.CSS_SELECTOR, '.file-get-info input[aria-label="Others Write"]').click()
    browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').click()
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-dialog-action="no"]')) == 1)
    confirmation = browser.find_element(By.CSS_SELECTOR, '[data-dialog-action="no"]').find_element(By.XPATH, '../..')
    assert str(directory) in confirmation.text
    assert 'including execute' in confirmation.text
    browser.find_element(By.CSS_SELECTOR, '[data-dialog-action="no"]').click()
    assert browser.execute_script("return document.activeElement.matches('[data-permission-apply]')")
    assert target.stat().st_mode & 0o777 == 0o740
    browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').click()
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-dialog-action="yes"]')) == 1)
    browser.find_element(By.CSS_SELECTOR, '[data-dialog-action="yes"]').click()
    wait.until(lambda driver: 'Changed: 2' in driver.find_element(By.CSS_SELECTOR, '[data-info-result]').text)
    assert target.stat().st_mode & 0o777 == directory.stat().st_mode & 0o777
    link = tmp_path / 'info-link'
    link.symlink_to(target)
    browser.execute_script('showFileGetInfo(arguments[0]);', str(link))
    wait.until(lambda driver: 'Permissions cannot be edited on symlinks' in driver.find_element(By.CSS_SELECTOR, '[data-info-permissions]').text)
    assert not browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').is_enabled()
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    guest_server = isolated_dev_server_factory('get-info-guest-' + theme, auth_bypass=False)
    guest_server.paths.auth_config_path.write_text('users:\n  - username: reader\n    password: fixture-password\n    role: readonly\n', encoding='utf-8')
    browser.get(guest_server.base_url)
    browser.find_element(By.NAME, 'username').send_keys('reader')
    browser.find_element(By.NAME, 'password').send_keys('fixture-password')
    browser.find_element(By.CSS_SELECTOR, 'button[type=submit]').click()
    wait.until(lambda driver: driver.execute_script("return typeof showFileGetInfo === 'function'"))
    browser.execute_script("document.body.classList.toggle('theme-light',arguments[0]==='light');showFileGetInfo(arguments[1]);", theme, str(target))
    wait.until(lambda driver: 'administrator account' in driver.find_element(By.CSS_SELECTOR, '[data-info-permissions]').text)
    assert not browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').is_enabled()
    reason_colors = browser.execute_script("const p=document.querySelector('[data-info-permissions] p[role=status]');return [getComputedStyle(p).color,getComputedStyle(p.closest('.file-editor-dialog')).backgroundColor]")
    assert wcag_contrast_ratio(*reason_colors) >= 4.5
    # The native batch route requires admin; the guest's startup probe must be refused.
    assert_only_expected_browser_http_error(browser, path='/api/fs/batch', status=403, query={})
    consume_only_expected_js_debug_api_error(browser, path='/api/fs/batch', status=403, method='POST', query={})
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    browser.get(server.base_url)
    wait.until(lambda driver: driver.execute_script("return typeof showFileGetInfo === 'function'"))
    browser.execute_script("document.body.classList.toggle('theme-light',arguments[0]==='light')", theme)
    browser.execute_script("""
      window.originalSizeStream=fileMetadataStream;
      window.failNextGetInfoSize=false;
      fileMetadataStream=(url,options,signal,progress)=>{
        if(!url.startsWith('/api/fs/directory-size?')) return originalSizeStream(url,options,signal,progress);
        if(window.failNextGetInfoSize){window.failNextGetInfoSize=false;return Promise.reject(new Error('HTTP 501'))}
        return new Promise((resolve,reject)=>{
          const data={bytes:12,allocated_bytes:512,files:1,folders:0,complete:false,partial:false,errors:[]};
          signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
          window.finishGetInfoSize=()=>{const final={...data,partial:true,errors:[{path:'long-directory/'.repeat(30),error:'Permission denied'}]};progress(final);resolve(final)};
          progress(data);
        });
      };
      showFileGetInfo(arguments[0]);
    """, str(directory))
    wait.until(lambda driver: '512 bytes' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert '99 bytes' not in browser.find_element(By.CSS_SELECTOR, '[data-info-size]').text
    browser.find_element(By.CSS_SELECTOR, '[data-size-cancel]').click()
    wait.until(lambda driver: 'Cancelled (partial)' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    browser.find_element(By.CSS_SELECTOR, '[data-size-recalculate]').click()
    wait.until(lambda driver: 'Calculating:' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    browser.execute_script('finishGetInfoSize()')
    wait.until(lambda driver: 'Permission denied' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert browser.execute_script("const b=document.querySelector('.file-get-info .file-editor-dialog-body');return b.scrollWidth <= b.clientWidth")
    browser.execute_script("window.failNextGetInfoSize=true")
    browser.find_element(By.CSS_SELECTOR, '[data-size-recalculate]').click()
    wait.until(lambda driver: 'HTTP 501' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert browser.find_element(By.CSS_SELECTOR, '[data-size-cancel]').is_displayed() is False
    assert browser.find_element(By.CSS_SELECTOR, '[data-size-recalculate]').is_displayed()
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    browser.execute_script('fileMetadataStream=originalSizeStream;delete window.originalSizeStream;delete window.finishGetInfoSize;delete window.failNextGetInfoSize')
    browser.execute_script("""
      window.originalGetInfoFetch=apiFetchJson;
      apiFetchJson=async(url,options)=>{
        const data=await originalGetInfoFetch(url,options);
        if(url.startsWith('/api/fs/info?')) delete data.metadata_identity;
        return data;
      };
      showFileGetInfo(arguments[0]);
    """, str(target))
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-info-warning]')) == 1)
    assert 'backend' in browser.find_element(By.CSS_SELECTOR, '[data-info-warning]').text
    assert not browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').is_enabled()
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    browser.execute_script("""
      apiFetchJson=async(url,options)=>url.startsWith('/api/fs/info?') ? null : originalGetInfoFetch(url,options);
      showFileGetInfo(arguments[0]);
    """, str(target))
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-info-warning]')) == 1)
    assert not browser.find_element(By.CSS_SELECTOR, '[data-permission-apply]').is_enabled()
    browser.execute_script("apiFetchJson=originalGetInfoFetch;delete window.originalGetInfoFetch")
    browser.set_window_size(390, 700)
    browser.execute_script("document.querySelector('[data-info-warning]').textContent += ' ' + 'wrapped-path/'.repeat(40)")
    narrow = browser.execute_script("""
      const dialog=document.querySelector('.file-get-info .file-editor-dialog'),body=dialog.querySelector('.file-editor-dialog-body'),footer=dialog.querySelector('.file-editor-dialog-actions');
      const rect=dialog.getBoundingClientRect();
      return {width:rect.width,viewport:innerWidth,bottom:footer.getBoundingClientRect().bottom,height:innerHeight,
        scroll:body.scrollHeight,client:body.clientHeight,columns:getComputedStyle(dialog.querySelector('dl')).gridTemplateColumns};
    """)
    assert narrow['width'] <= narrow['viewport'] * 0.92 + 1, narrow
    assert narrow['bottom'] <= narrow['height'], narrow
    assert narrow['scroll'] > narrow['client'], narrow
    assert len(narrow['columns'].split()) == 1, narrow
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').send_keys(Keys.TAB)
    assert browser.execute_script("return document.activeElement.closest('.file-get-info') !== null")
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    browser.set_window_size(1280, 1200)
    for dialog_class in ['finder-move-dialog', 'markdown-link-url-dialog', 'drop-action-result-dialog',
                         'file-editor-compare-dialog', 'file-editor-conflict-dialog',
                         'file-editor-external-change-dialog', 'file-editor-close-dialog']:
        browser.execute_script("showFileEditorDecisionDialog({title:'Dialog contrast',message:'Readable explanation',className:arguments[0]})", dialog_class)
        colors = browser.execute_script("const d=document.querySelector('.'+arguments[0]+' .file-editor-dialog');return [getComputedStyle(d.querySelector('.file-editor-dialog-body')).color,getComputedStyle(d).backgroundColor]", dialog_class)
        assert wcag_contrast_ratio(*colors) >= 4.5, (dialog_class, colors)
        browser.find_element(By.CSS_SELECTOR, '.' + dialog_class + ' [data-dialog-action]').send_keys(Keys.ESCAPE)
    browser.execute_script("showFileEditorDecisionDialog({title:'Danger',message:'Danger explanation',messageVariant:'danger'})")
    danger = browser.execute_script("const d=document.querySelector('.file-editor-dialog');return [getComputedStyle(d.querySelector('.danger')).color,getComputedStyle(d).backgroundColor]")
    assert wcag_contrast_ratio(*danger) >= 4.5
    browser.find_element(By.CSS_SELECTOR, '[data-dialog-action]').click()
    browser.execute_async_script("const done=arguments[arguments.length-1];setTabberMode('advanced');storageSet('yomux.finder.permissions.v1','0');selectSession(finderItemId).then(()=>done(true),error=>done(String(error)))")
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-file-explorer-permissions]')) > 0)
    button = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '.file-explorer-panel[data-file-explorer-view="finder"] [data-file-explorer-permissions]') if node.is_displayed()), None))
    assert button.text == 'ⓘ'
    assert button.get_attribute('aria-label') == 'Show size and permissions'
    assert button.get_attribute('title') == 'Show size and permissions'
    geometry = browser.execute_script("""
      const button = arguments[0], bounds = button.getBoundingClientRect();
      const siblings = [...button.parentElement.children].filter(node => node !== button && node.offsetWidth > 0);
      return {width: bounds.width, overlaps: siblings.some(node => {
        const other = node.getBoundingClientRect();
        return bounds.left < other.right && bounds.right > other.left && bounds.top < other.bottom && bounds.bottom > other.top;
      })};
    """, button)
    assert geometry['width'] <= 24, geometry
    assert not geometry['overlaps'], geometry
    browser.execute_script("window.permissionClicks=[];window.originalPermissionToggle=toggleFileExplorerPermissions;toggleFileExplorerPermissions=()=>{permissionClicks.push(fileExplorerPermissionsVisible());return originalPermissionToggle()}")
    button.click()
    assert browser.execute_script('return permissionClicks') == [False]
    browser.execute_script('toggleFileExplorerPermissions=originalPermissionToggle;delete window.originalPermissionToggle;delete window.permissionClicks')
    wait.until(lambda driver: driver.execute_script('return fileExplorerPermissionsVisible()'))
    assert button.get_attribute('aria-pressed') == 'true'
    browser.execute_script("const row = document.createElement('div'); row.className = 'file-tree-row'; row.id='permission-column-repro'; row.style.cssText='position:fixed;top:100px;left:0;width:100%;pointer-events:none'; document.body.append(row); updateFileTreeRowContents(row,'F','file',{permissionsText:'-rwxr-xr-x', dateText:''});")
    column = browser.find_element(By.CSS_SELECTOR, '#permission-column-repro .file-tree-permissions')
    assert column.is_displayed()
    assert browser.execute_script("return arguments[0].previousElementSibling.className", column) == 'file-tree-date'
    browser.refresh()
    wait.until(lambda driver: driver.execute_script("return typeof fileExplorerPermissionsVisible === 'function' && fileExplorerPermissionsVisible()"))


@pytest.mark.browser
def test_native_finder_details_toolbar_control_is_compact(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('finder-details-toolbar')
    request.addfinalizer(lambda: browser.get('about:blank'))
    browser.set_window_size(480, 800)
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof selectSession === 'function'"))
    opened = browser.execute_async_script("""
      const root=arguments[0],done=arguments[arguments.length-1];
      (async()=>{setTabberMode('advanced');await selectSession(finderItemId);storageSet('yomux.finder.permissions.v1','0');
        return await openFileExplorerAt(root,{manualSelection:true,user:true});})().then(done,e=>done(String(e)));
    """, str(tmp_path))
    assert opened is True, opened
    button = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '.file-explorer-panel[data-file-explorer-view="finder"] [data-file-explorer-permissions]') if node.is_displayed()), None))
    assert button.text == 'ⓘ'
    assert button.get_attribute('aria-label') == 'Show size and permissions'
    assert button.get_attribute('title') == 'Show size and permissions'
    geometry = browser.execute_script("""
      const button=arguments[0],bounds=button.getBoundingClientRect();
      const siblings=[...button.parentElement.children].filter(node=>node!==button&&node.offsetWidth>0);
      return {width:bounds.width,overlaps:siblings.some(node=>{
        const other=node.getBoundingClientRect();
        return bounds.left<other.right&&bounds.right>other.left&&bounds.top<other.bottom&&bounds.bottom>other.top;
      })};
    """, button)
    assert geometry['width'] <= 24, geometry
    assert not geometry['overlaps'], geometry


@pytest.mark.browser
def test_native_get_info_refreshes_directory_size_progress_each_second(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('get-info-progress')
    request.addfinalizer(lambda: browser.get('about:blank'))
    directory = tmp_path / 'progress-folder'
    directory.mkdir()
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof showFileGetInfo === 'function'"))
    browser.execute_script("""
      window.originalGetInfoFetch=apiFetchJson;
      apiFetchJson=async(url,options)=>{
        const data=await originalGetInfoFetch(url,options);
        if(url.startsWith('/api/fs/info?')) delete data.metadata_identity;
        return data;
      };
      window.originalSizeStream=fileMetadataStream;
      fileMetadataStream=(url,options,signal,progress)=>{
        if(!url.startsWith('/api/fs/directory-size?')) return originalSizeStream(url,options,signal,progress);
        return new Promise((resolve,reject)=>{
          const data={allocated_bytes:512,files:1,folders:0,complete:false,partial:false,errors:[]};
          signal.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});
          progress({...data,bytes:12});
          progress({...data,bytes:99});
          window.finishGetInfoSize=()=>{const final={...data,bytes:267323707392,complete:true};progress(final);resolve(final)};
          setTimeout(()=>progress({...data,bytes:999}),1100);
        });
      };
      void showFileGetInfo(arguments[0]);
    """, str(directory))
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '[data-info-warning]')) == 1)
    size = browser.find_element(By.CSS_SELECTOR, '[data-info-size]')
    wait.until(lambda driver: '12' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert '99 bytes' not in size.text
    wait.until(lambda driver: '999 bytes' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    browser.execute_script('finishGetInfoSize()')
    wait.until(lambda driver: 'Total:' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    assert '1 files' in size.text
    assert '267,323,707,392 bytes (249 GB)' in size.text
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()
    browser.execute_script('fileMetadataStream=originalSizeStream;delete window.originalSizeStream;delete window.finishGetInfoSize')
    browser.execute_script('apiFetchJson=originalGetInfoFetch;delete window.originalGetInfoFetch')


@pytest.mark.browser
def test_native_get_info_directory_size_go_endpoint(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('get-info-directory-size-route')
    request.addfinalizer(lambda: browser.get('about:blank'))
    directory = tmp_path / 'real-size-folder'
    directory.mkdir()
    counted = directory / 'counted.txt'
    counted.write_bytes(b'count-me-123')
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof showFileGetInfo === 'function'"))
    browser.execute_script('void showFileGetInfo(arguments[0])', str(directory))
    wait.until(lambda driver: 'Total:' in driver.find_element(By.CSS_SELECTOR, '[data-info-size]').text)
    summary = browser.find_element(By.CSS_SELECTOR, '[data-info-size]').text
    allocated_size = counted.stat().st_blocks * 512
    expected_size = max(counted.stat().st_size, allocated_size)
    assert f'{expected_size:,} bytes' in summary, summary
    assert '1 files' in summary, summary
    assert '0 folders' in summary, summary
    assert 'HTTP 501' not in summary, summary
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-dialog-action]').click()


@pytest.mark.browser
def test_chmod_read_permission_reloads_an_open_unreadable_markdown_file(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('chmod-readable-editor-refresh')
    request.addfinalizer(lambda: browser.get('about:blank'))
    target = tmp_path / 'locked.md'
    target.write_text('# Recovered content\n', encoding='utf-8')
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof openFileInEditor === 'function'"))
    setup = browser.execute_script("""
      const path=arguments[0];
      const metadata={path,name:'locked.md',kind:'file',size:0,mtime:0,created:null,accessed:null,allocated_size:0,
        realpath:path,is_symlink:false,owner:'owner',owner_id:1,group:'group',group_id:2,mode:0o200,
        permissions:'--w-------',octal_mode:'0200',metadata_identity:'fixture-file-identity',permission_edit_reason:'',
        writable:true,write_reason:''};
      window.__chmodMetadata=metadata;window.__chmodCanRead=false;window.__chmodReadCount=0;
      window.__originalApiFetchJson=apiFetchJson;
      apiFetchJson=async(url,...args)=>{
        const parsed=new URL(String(url),location.href);
        if(parsed.pathname==='/api/fs/read'){
          window.__chmodReadCount++;
          if(!window.__chmodCanRead){const error=new Error('Permission denied');error.status=403;throw error;}
          return {...metadata,content:'# Recovered content\\n',size:20,mode:0o600,permissions:'-rw-------',octal_mode:'0600'};
        }
        if(parsed.pathname==='/api/fs/info')return {...metadata};
        return window.__originalApiFetchJson(url,...args);
      };
      window.__originalFileMetadataStream=fileMetadataStream;
      fileMetadataStream=async(url,options,signal,onProgress)=>{
        if(!String(url).includes('/api/fs/chmod'))return window.__originalFileMetadataStream(url,options,signal,onProgress);
        window.__chmodCanRead=true;
        const changed={...metadata,mode:0o600,permissions:'-rw-------',octal_mode:'0600',writable:true,write_reason:''};
        const result={path,complete:true,metadata:changed,files:1,changed:1,skipped:0,errors:[]};
        onProgress(result);
        return result;
      };
    """, str(target))
    browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      openFileInEditor(arguments[0],{name:'locked.md'},{viewMode:'preview',userInitiated:true}).then(done,error=>done(String(error)));
    """, str(target))
    wait.until(lambda driver: driver.execute_script("return fileState.get(arguments[0])?.kind==='error'", str(target)))
    assert browser.execute_script("return window.__chmodReadCount") == 1
    browser.execute_script('void showFileGetInfo(arguments[0])', str(target))
    wait.until(lambda driver: len(driver.find_elements(By.CSS_SELECTOR, '.file-get-info [data-permission-bit="256"]')) == 1)
    browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-permission-bit="256"]').click()
    apply = browser.find_element(By.CSS_SELECTOR, '.file-get-info [data-permission-apply]')
    assert apply.is_displayed()
    apply.click()
    wait.until(lambda driver: driver.execute_script("return fileState.get(arguments[0])?.kind==='text'", str(target)))
    result = browser.execute_script("""
      const path=arguments[0],state=fileState.get(path),item=fileEditorItemFor(path),panel=panelNodes.get(item);
      return {content:state.content,writable:state.writable,mode:editorViewModeFor(path,item),readCount:window.__chmodReadCount,
        editor:panel._cmView?.contentDOM.textContent||'',preview:panel.querySelector('.file-editor-preview-pane-panel')?.textContent||''};
    """, str(target))
    assert result['content'] == '# Recovered content\n' and result['writable'] is True, result
    assert result['mode'] == 'edit' and 'Recovered content' in result['editor'], result
    assert result['readCount'] == 2, result


@pytest.mark.browser
def test_native_create_empty_markdown_opens_editable(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('create-empty-markdown')
    request.addfinalizer(lambda: browser.get('about:blank'))
    directory = tmp_path / 'empty-markdown'
    directory.mkdir()
    target = directory / '_hello.md'
    target.touch()
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof openFileInEditor === 'function'"))
    browser.execute_async_script("""
      const done=arguments[arguments.length-1];
      openFileInEditor(arguments[0],{name:'_hello.md'},{userInitiated:true}).then(done,error=>done(String(error)));
    """, str(target))
    wait.until(lambda driver: driver.execute_script("""
      const state=fileState.get(arguments[0]),item=fileEditorItemFor(arguments[0]),panel=panelNodes.get(item);
      return state?.kind==='text' && !state.loading && panel?._cmView && panel._cmView.contentDOM.isContentEditable;
    """, str(target)))
    snapshot = browser.execute_script("""
      const path=arguments[0],state=fileState.get(path),item=fileEditorItemFor(path),panel=panelNodes.get(item);
      return {content:state.content,size:state.size,writable:state.writable,mode:editorViewModeFor(path,item),
        editable:panel._cmView.contentDOM.isContentEditable,editor:panel._cmView.contentDOM.textContent};
    """, str(target))
    assert snapshot['content'] == '' and snapshot['size'] == 0, snapshot
    assert snapshot['writable'] is not False and snapshot['mode'] == 'edit' and snapshot['editable'], snapshot
    editor = browser.find_element(By.CSS_SELECTOR, '.file-editor-panel .cm-content')
    ActionChains(browser).click(editor).send_keys('hello').perform()
    wait.until(lambda driver: driver.execute_script("return fileState.get(arguments[0])?.content==='hello'", str(target)))
    wait.until(lambda _driver: target.read_text(encoding='utf-8') == 'hello')


@pytest.mark.browser
def test_native_readonly_editor_and_finder_details(browser, isolated_dev_server_factory, tmp_path, request):
    server = isolated_dev_server_factory('readonly-details')
    request.addfinalizer(lambda: browser.get('about:blank'))
    target = tmp_path / '_HELLO.md'
    original = '# Locked\nOriginal content.\n'
    target.write_text(original, encoding='utf-8')
    target.chmod(0o444)
    other = tmp_path / 'larger.log'
    other.write_bytes(b'x' * 10240)
    browser.get(server.base_url)
    wait = WebDriverWait(browser, 20)
    wait.until(lambda driver: driver.execute_script("return initialAppShellPainted && typeof openFileInEditor === 'function'"))
    browser.execute_async_script("const done=arguments[arguments.length-1];openFileInEditor(arguments[0],'_HELLO.md',{viewMode:'split',userInitiated:true}).then(()=>done(true),e=>done(String(e)))", str(target))
    wait.until(lambda driver: driver.execute_script("const p=panelNodes.get(fileEditorItemFor(arguments[0]));return Boolean(p?._cmView && p?._pmView)", str(target)))
    snapshot = browser.execute_script("""
      const p=panelNodes.get(fileEditorItemFor(arguments[0]));window.readonlyPanel=p;
      window.readonlyTextView=p._cmView;window.readonlyViewView=p._pmView;
      return {text:p._cmView.contentDOM.contentEditable,view:p._pmView.editable,
        writable:fileState.get(arguments[0]).writable,saveHidden:p.querySelector('.file-editor-save-panel').hidden,
        uploadHidden:p.querySelector('.file-editor-upload-panel').hidden,status:p.querySelector('.file-editor-status-message').textContent};
    """, str(target))
    assert snapshot['text'] == 'false' and snapshot['view'] is False, snapshot
    assert snapshot['writable'] is False and snapshot['saveHidden'] and snapshot['uploadHidden'], snapshot
    assert 'read-only' in snapshot['status'].lower(), snapshot
    for selector in ['.cm-content', '.ProseMirror']:
        surface = browser.execute_script('return readonlyPanel.querySelector(arguments[0])', selector)
        ActionChains(browser).click(surface).send_keys('blocked').perform()
        assert browser.execute_script('return fileState.get(arguments[0]).content', str(target)) == original
    connection = HTTPConnection('127.0.0.1', server.port, timeout=10)
    try:
        connection.request('POST', '/api/fs/write', json.dumps({'path': str(target), 'content': 'must not replace'}),
                           headers={'Content-Type': 'application/json', 'Origin': server.base_url})
        response = connection.getresponse()
        failure = json.loads(response.read())
        status = response.status
        if status == 202:
            receipt = failure
            def terminal(_server):
                result = _server.request(receipt['operation']['status_url'])
                return result if result[0] != 202 else False
            status, _, body = WebDriverWait(server, 5, poll_frequency=0.05).until(terminal)
            failure = json.loads(body)
        assert status == 403 and 'read-only' in str(failure).lower(), failure
    finally:
        connection.close()
    assert target.read_text(encoding='utf-8') == original
    target.chmod(0o640)
    browser.execute_async_script('const done=arguments[arguments.length-1];refreshOpenFilesIfChanged({paths:[arguments[0]],fresh:true}).then(()=>done(true),e=>done(String(e)))', str(target))
    wait.until(lambda driver: driver.execute_script("return readonlyTextView.contentDOM.contentEditable==='true' && readonlyViewView.editable"))
    assert browser.execute_script('return readonlyPanel._cmView===readonlyTextView && readonlyPanel._pmView===readonlyViewView')
    target.chmod(0o444)
    browser.execute_async_script('const done=arguments[arguments.length-1];refreshOpenFilesIfChanged({paths:[arguments[0]],fresh:true}).then(()=>done(true),e=>done(String(e)))', str(target))
    wait.until(lambda driver: driver.execute_script("return readonlyTextView.contentDOM.contentEditable==='false' && !readonlyViewView.editable"))
    assert browser.execute_script('return readonlyPanel._cmView===readonlyTextView && readonlyPanel._pmView===readonlyViewView')
    browser.execute_async_script("""
      const root=arguments[0],done=arguments[arguments.length-1];
      (async()=>{setTabberMode('advanced');await selectSession(finderItemId);storageSet('yomux.finder.permissions.v1','0');
        setFileExplorerManualRootMode();return await openFileExplorerAt(root,{manualSelection:true,user:true});})().then(done,e=>done(String(e)));
    """, str(tmp_path))
    button = wait.until(lambda driver: next((node for node in driver.find_elements(By.CSS_SELECTOR, '[data-file-explorer-permissions]') if node.is_displayed()), None))
    button.click()
    def details(driver, path):
        return next((row for row in driver.find_elements(By.CSS_SELECTOR, f'.file-tree-row[data-path="{path}"]')
                     if row.is_displayed() and row.find_element(By.CSS_SELECTOR, '.file-tree-permissions').text.startswith('-')), None)
    first = wait.until(lambda driver: details(driver, target))
    second = wait.until(lambda driver: details(driver, other))
    assert first.find_element(By.CSS_SELECTOR, '.file-tree-permissions').text == '-r--r--r--'
    assert first.find_element(By.CSS_SELECTOR, '.file-tree-size').text == f'{len(original.encode())} B'
    assert second.find_element(By.CSS_SELECTOR, '.file-tree-size').text == '10 KB'
    browser.execute_script('updateFileTreeGitStatusRows()')
    assert first.find_element(By.CSS_SELECTOR, '.file-tree-permissions').text == '-r--r--r--'
    assert first.find_element(By.CSS_SELECTOR, '.file-tree-size').text == f'{len(original.encode())} B'
    geometry = browser.execute_script("""
      return arguments[0].map(row=>({permission:row.querySelector('.file-tree-permissions').getBoundingClientRect().right,
        size:row.querySelector('.file-tree-size').getBoundingClientRect().right,
        font:parseFloat(getComputedStyle(row.querySelector('.file-tree-permissions')).fontSize),
        name:parseFloat(getComputedStyle(row.querySelector('.file-tree-name')).fontSize)}));
    """, [first, second])
    assert abs(geometry[0]['permission'] - geometry[1]['permission']) < 1, geometry
    assert abs(geometry[0]['size'] - geometry[1]['size']) < 1, geometry
    assert all(row['font'] <= row['name'] for row in geometry), geometry
    assert target.read_text(encoding='utf-8') == original


def test_native_codex_wrapped_file_context_menu_opens_complete_path(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('codex-wrapped-file')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        "return typeof terminalReferenceAtClientPoint === 'function' && terminals.get(arguments[0])?.term?.element && initialAppShellPainted",
        session,
    ))
    with tempfile.TemporaryDirectory(prefix='yfl-', dir='/tmp') as directory:
        path = Path(directory) / 'ssc-resident-cintas-schedule-2026-10-26.md'
        content = 'Wrapped file link reached the complete file.\n'
        path.write_text(content, encoding='utf-8')
        for split in ('ssc-resident-', 'ssc-resident-cintas-'):
            rows = [f'See ({directory}/{split}', f'  {path.name[len(split):]}) to require all units', '  unrelated following prose']
            for row in (1, 2):
                browser.execute_script("selectSession(arguments[0]); terminals.get(arguments[0]).term.yomuxAgentKind='codex'", session)
                # The fixture shell is the only output producer; browser injection races its startup stream.
                output_format = r'\033[2J\033[H\033[3J' + r'%s\r\n' * len(rows)
                command = 'printf ' + ' '.join(shlex.quote(value) for value in [output_format, *rows])
                run_isolated_tmux(server.tmux, 'send-keys', '-t', f'{session}:', command, 'Enter')
                point = browser.execute_async_script(r"""
                  const session=arguments[0],rows=arguments[1],row=arguments[2],done=arguments[arguments.length-1];
                  (async()=>{
                    await selectSession(session);
                    const {term}=terminals.get(session),container=document.getElementById(`term-${session}`);
                    term.yomuxAgentKind='codex';
                    try {
                      await window.__yomuxTestWaitFor(()=>rows.every((text,index)=>
                        term.buffer.active.getLine(term.buffer.active.viewportY+index)?.translateToString(true)===text),{timeoutMs:10000});
                    } catch(error) {
                      return {error:String(error),cols:term.cols,viewportY:term.buffer.active.viewportY,
                        actual:Array.from({length:Math.min(term.buffer.active.length,30)},(_,index)=>term.buffer.active.getLine(index)?.translateToString(true))};
                    }
                    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                    const screen=terminalScreenElement(container),rect=screen.getBoundingClientRect();
                    const cell=terminalCellDimensions(term,container);
                    const x=row===1?rows[0].indexOf('/')+1:3;
                    const point={x:rect.left+(x-0.5)*cell.width,y:rect.top+(row-0.5)*cell.height};
                    const ref=terminalReferenceAtClientPoint(term,container,point.x,point.y);
                    return {...point,path:ref?.path,cols:term.cols,rows:rows.map((_,i)=>term.buffer.active.getLine(term.buffer.active.viewportY+i).translateToString(true))};
                  })().then(done,error=>done({error:String(error.stack||error)}));
                """, session, rows, row)
                assert 'error' not in point, json.dumps(point)
                assert point['cols'] >= max(map(len, rows)), point
                assert point['path'] == str(path), point
                for event_type in ('mousePressed', 'mouseReleased'):
                    browser.execute_cdp_cmd('Input.dispatchMouseEvent', {
                        'type': event_type, 'x': point['x'], 'y': point['y'], 'button': 'right', 'clickCount': 1,
                    })
                open_button = WebDriverWait(browser, 5).until(lambda driver: next((
                    button for button in driver.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')
                    if button.text == 'Open file'
                ), None))
                open_button.click()
                WebDriverWait(browser, 10).until(lambda driver: driver.execute_script("""
                  const item=fileEditorItemFor(arguments[0]), panel=panelNodes.get(item);
                  return itemIsActivePaneTab(item) && editorViewModeFor(arguments[0],item)==='preview'
                    && panel?.getBoundingClientRect().height>0
                    && fileStateFor(arguments[0])?.content===arguments[1]
                    && panel?._pmView?.state.doc.textContent===arguments[1].trimEnd();
                """, str(path), content))


@pytest.mark.parametrize('width', [40, 64, 80, 120])
def test_native_tmux_random_url_clipboard_matrix(browser, isolated_dev_server_factory, request, tmp_path, width):
    server = isolated_dev_server_factory(f'url-matrix-{width}')
    (server.paths.config_dir / 'settings.yaml').write_text('general:\n  startup_tips: false\n', encoding='utf-8')
    request.addfinalizer(lambda: browser.get('about:blank'))
    session = server.tmux.sessions[0]
    original_size = browser.get_window_size()
    request.addfinalizer(lambda: browser.set_window_size(original_size['width'], original_size['height']))
    browser.set_window_size(1500, 1900)
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    WebDriverWait(browser, 15).until(lambda driver: driver.execute_script(
        "return initialAppShellPainted && terminals.get(arguments[0])?.socket?.readyState === WebSocket.OPEN && document.querySelector('.xterm-rows')",
        session,
    ))
    assert browser.execute_script('return startupHelpersEnabled') is False
    browser.execute_cdp_cmd('Browser.grantPermissions', {
        'origin': server.base_url, 'permissions': ['clipboardReadWrite', 'clipboardSanitizedWrite'],
    })
    fitted = browser.execute_async_script("""
      const session=arguments[0],width=arguments[1],done=arguments[arguments.length-1];
      (async()=>{
        const {term,container}=terminals.get(session),cell=terminalCellDimensions(term,container);
        const style=getComputedStyle(container);
        container.style.setProperty('width',`${Math.ceil(width*cell.width+2+parseFloat(style.paddingLeft)+parseFloat(style.paddingRight))}px`,'important');
        container.style.setProperty('height',`${Math.ceil(80*cell.height+terminalFitBottomReservePx+parseFloat(style.paddingTop)+parseFloat(style.paddingBottom))}px`,'important');
        fitTerminal(session,{force:true,claim:true});
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        return {cols:term.cols,rows:term.rows};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, session, width)
    assert fitted == {'cols':width,'rows':80}, fitted
    for args in [('set-window-option', '-t', session, 'window-size', 'manual'),
                 ('resize-window', '-t', session, '-x', str(width), '-y', '80'),
                 ('send-keys', '-t', session, 'stty -echo; PS1=; PROMPT_COMMAND=', 'Enter')]:
        completed = run_isolated_tmux(server.tmux, *args)
        assert completed.returncode == 0, completed.stderr
    failures, results = [], []
    examples = terminal_url_matrix_examples()
    cases = [(index, expected, 'soft') for index, expected in enumerate(examples)]
    cases.extend((index, examples[index], layout) for index in [0, 13, 59] for layout in ['codex', 'opencode', 'quote'])
    evidence = Path(product_env('E2E_EVIDENCE_DIR', '') or tmp_path)
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / f'terminal-url-matrix-{width}.json'
    progress = evidence / f'terminal-url-matrix-{width}.jsonl'
    for index, expected, layout in cases:
        prefix = {'soft': '', 'codex': '    ', 'opencode': '   ', 'quote': '  | '}[layout]
        content_width = width - len(prefix)
        if layout == 'soft':
            printed = [expected]
        else:
            # Repeated OpenCode gutters use an initial URL delimiter to distinguish a wrapped token
            # from a complete URL followed by an unrelated indented word.
            first_width = content_width
            if layout == 'opencode' and len(expected) > content_width:
                first_width = max(position + 1 for position, char in enumerate(expected[:content_width]) if char in '/-.?&=#%')
            printed = [prefix + expected[:first_width]]
            printed.extend(prefix + expected[offset:offset + content_width] for offset in range(first_width, len(expected), content_width))
        marker = f'URL-MATRIX-DONE-{index}-{layout}'
        command = 'printf ' + ' '.join(shlex.quote(value) for value in [
            r'\033[2J\033[H\033[3J' + r'%s\r\n' * (len(printed) + 1), *printed, marker,
        ])
        completed = run_isolated_tmux(server.tmux, 'send-keys', '-t', session, command, 'Enter')
        assert completed.returncode == 0, completed.stderr
        observation = browser.execute_async_script("""
          const session=arguments[0],marker=arguments[1],kind=arguments[2],prefix=arguments[3],done=arguments[arguments.length-1];
          (async()=>{
            const {term}=terminals.get(session);
            term.yomuxAgentKind=kind;
            await window.__yomuxTestWaitFor(()=>Array.from({length:term.rows},(_,index)=>
              term.buffer.active.getLine(term.buffer.active.viewportY+index)?.translateToString(true)||'').some(line=>line===marker),
              {timeoutMs:10000,description:marker});
            await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
            const active=term.buffer.active;
            const lines=Array.from({length:term.rows},(_,index)=>active.getLine(active.viewportY+index)?.translateToString(true)||'');
            const end=lines.indexOf(marker);
            const rows=lines.slice(0,end);
            const container=document.getElementById(`term-${session}`),rect=terminalScreenElement(container).getBoundingClientRect();
            const cell=terminalCellDimensions(term,container);
            const points=[...new Set([0,Math.floor((rows.length-1)/2),rows.length-1])].map(row=>{
              const text=rows[row],indent=text.startsWith(prefix)?prefix.length:(text.match(/^\\s*/)||[''])[0].length;
              const column=row===rows.length-1?text.length-1:indent;
              return {row,column,x:rect.left+(column+0.5)*cell.width,y:rect.top+(row+0.5)*cell.height,
                reference:terminalReferenceAtPosition(term,{x:column+1,y:active.viewportY+row+1})?.href||null};
            });
            const afterColumn=rows.at(-1).length;
            const after=afterColumn<term.cols?{x:afterColumn+1,y:active.viewportY+rows.length}:{x:1,y:active.viewportY+rows.length+1};
            return {cols:term.cols,rows,points,realXterm:term.element.classList.contains('xterm'),
              wrapped:rows.map((_,index)=>active.getLine(active.viewportY+index).isWrapped),
              afterReference:terminalReferenceAtPosition(term,after)?.href||null};
          })().then(done,error=>done({error:String(error.stack||error)}));
        """, session, marker, 'opencode' if layout in ('opencode', 'quote') else 'codex', prefix)
        assert 'error' not in observation, observation
        tmux_width = run_isolated_tmux(server.tmux, 'display-message', '-p', '-t', session, '#{pane_width}').stdout.strip()
        assert observation['realXterm'] and observation['cols'] == width and tmux_width == str(width), observation
        captured = run_isolated_tmux(server.tmux, 'capture-pane', '-p', '-t', session).stdout.splitlines()
        assert captured[:captured.index(marker)] == observation['rows'], {'tmux':captured, 'xterm':observation}
        copied_values = []
        for point in observation['points']:
            point['hit'] = browser.execute_script("""
              const x=arguments[0],y=arguments[1],session=arguments[2],hit=document.elementFromPoint(x,y);
              const {term,container}=terminals.get(session);
              return {tag:hit?.tagName,className:hit?.className,inside:container.contains(hit),
                href:terminalReferenceAtClientPoint(term,container,x,y)?.href||null};
            """, point['x'], point['y'], session)
            assert point['hit']['inside'], {'index':index,'layout':layout,'point':point}
            for event_type in ('mousePressed', 'mouseReleased'):
                browser.execute_cdp_cmd('Input.dispatchMouseEvent', {
                    'type':event_type, 'x':point['x'], 'y':point['y'], 'button':'right', 'clickCount':1,
                })
            copy_button = next((button for button in browser.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')
                                if button.text == 'Copy URL'), None)
            if copy_button is None:
                copied_values.append({'row':point['row'], 'error':'Copy URL is missing', 'reference':point['reference'],
                                      'labels':[button.text for button in browser.find_elements(By.CSS_SELECTOR, '.terminal-context-menu button')]})
                browser.execute_script('closeTerminalContextMenu()')
                continue
            browser.execute_script('window.__urlMatrixCopyButton=arguments[0]', copy_button)
            copy_button.click()
            copied = browser.execute_async_script("""
              const done=arguments[arguments.length-1];
              window.__yomuxTestWaitFor(()=>window.__urlMatrixCopyButton.dataset.copyFeedbackActive==='true',{timeoutMs:5000})
                .then(()=>navigator.clipboard.readText()).then(done,error=>done({error:String(error)}));
            """)
            copied_values.append({'row':point['row'], 'copied':copied, 'reference':point['reference']})
        result = {'index':index,'width':width,'layout':layout,'expected':expected,**observation,'copies':copied_values}
        results.append(result)
        with progress.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(result) + '\n')
        if observation['afterReference'] is not None or any(value.get('copied') != expected for value in copied_values):
            failures.append(result)
    report.write_text(json.dumps({'seed':20261026,'results':results,'failures':failures}, indent=2), encoding='utf-8')
    print(json.dumps({'width':width,'urls':len(examples),'combinations':len(results),'copies':sum(len(row['copies']) for row in results),
                      'failures':len(failures),'evidence':str(report)}))
    assert not failures, json.dumps({'failed':len(failures),'examples':[
        {'index':row['index'],'layout':row['layout'],'length':len(row['expected']),'rows':len(row['rows']),
         'copies':[{'row':value['row'],'error':value.get('error'),'length':len(value.get('copied','')),
                    'correct':value.get('copied')==row['expected']} for value in row['copies']]}
        for row in failures[:3]],'evidence':str(report)})


def test_native_mixed_notices_scroll_below_editor_controls_on_short_viewports(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory('short-notice-stack')
    request.addfinalizer(lambda: browser.get('about:blank'))
    # The test owns explicit tips; automatic startup delivery must not replace its native wheel target.
    (server.paths.config_dir / 'settings.yaml').write_text('general:\n  startup_tips: false\n', encoding='utf-8')
    path = server.paths.home_dir / 'notices.json'
    path.write_text('{"notice":true}\n', encoding='utf-8')
    session = server.tmux.sessions[0]
    browser.get(server.base_url + f'/?sessions={session}&layout=left&tabs=left:{session}')
    setup = browser.execute_async_script("""
      const path=arguments[0],done=arguments[arguments.length-1];
      (async()=>{
        await window.__yomuxTestWaitFor(()=>initialAppShellPainted && clientEventTransportState.connected,{timeoutMs:10000});
        const item=await openFileInEditor(path,'notices.json',{viewMode:'edit',userInitiated:true});
        await window.__yomuxTestWaitFor(()=>panelNodes.get(item)?._cmView,{timeoutMs:10000});
        return {item,automaticTips:startupHelpersEnabled};
      })().then(done,error=>done({error:String(error.stack||error)}));
    """, str(path))
    assert 'error' not in setup, setup
    assert setup['automaticTips'] is False, setup
    original_compact = browser.execute_script("return document.body.classList.contains('app-topbar-touch-compact')")
    try:
        for width, height, usable_height in ((1000, 235, None), (400, 235, None), (1000, 480, 235)):
            browser.execute_cdp_cmd('Emulation.setDeviceMetricsOverride', {'width':width,'height':height,'deviceScaleFactor':1,'mobile':False})
            browser.execute_script('setAppViewportOverride(arguments[0])', {'width':width,'height':usable_height} if usable_height else None)
            for compact in (False, True):
                geometry = browser.execute_async_script("""
                  const item=arguments[0],compact=arguments[1],done=arguments[arguments.length-1];
                  (async()=>{
                    document.body.classList.toggle('app-topbar-touch-compact',compact);
                    await new Promise(requestAnimationFrame);
                    await new Promise(requestAnimationFrame);
                    const enabled=startupHelpersEnabled;startupHelpersEnabled=true;
                    const tip=showStartupHelperTip();startupHelpersEnabled=enabled;
                    // Each geometry case owns a distinct notice, including its rolling dedupe identity.
                    const command=showTimedStatusNotice(`The command could not complete. Please review request ${innerWidth}/${innerHeight}/${compact} and try again.`);
                    window.__shortNoticeIds=[tip,command].map(node=>Number(node.dataset.alertId));
                    const stack=tip.parentElement;
                    const reload=panelNodes.get(item).querySelector('.file-editor-reload-panel');
                    await window.__yomuxTestWaitFor(()=>{
                      const bounds=stack.getBoundingClientRect(),button=reload.getBoundingClientRect();
                      const header=reload.closest('.file-editor-toolbars').getBoundingClientRect();
                      return Math.abs(bounds.top-(Math.ceil(header.bottom)+12))<=1
                        && document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)===reload;
                    },{timeoutMs:5000,description:'notice and header geometry converge after viewport change'});
                    const bounds=stack.getBoundingClientRect(),button=reload.getBoundingClientRect();
                    const body=tip.querySelector('.toast-body').getBoundingClientRect();
                    const bodyHit=document.elementFromPoint(body.left+4,Math.max(body.top,bounds.top)+2);
                    return {top:bounds.top,bottom:bounds.bottom,height:Math.min(innerHeight,appViewport().height),reloadBottom:button.bottom,headerBottom:reload.closest('.file-editor-toolbars').getBoundingClientRect().bottom,
                      hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2)===reload,
                      passthrough:!bodyHit?.closest('.attention-alerts'),
                      scrollHeight:stack.scrollHeight,clientHeight:stack.clientHeight,
                      horizontalOverflow:stack.scrollWidth > stack.clientWidth+1,
                      clipped:[tip,command].some(node=>node.scrollHeight > node.clientHeight+1),
                      remaining:Number(command.dataset.toastRemainingMs),
                      tipId:tip.dataset.alertId,commandId:command.dataset.alertId,
                      groups:[...document.querySelectorAll('.dv-groupview')].map(node=>({rect:node.getBoundingClientRect().toJSON(),heads:[...node.querySelectorAll('.panel-head,.file-editor-toolbars')].map(head=>head.getBoundingClientRect().toJSON())})),
                      button:button.toJSON(),width:innerWidth,compact};
                  })().then(done,error=>done({error:String(error.stack||error)}));
                """, setup['item'], compact)
                assert 'error' not in geometry, geometry
                assert geometry['hit'] and geometry['top'] >= geometry['reloadBottom'], json.dumps(geometry)
                assert abs(geometry['top'] - (geometry['headerBottom'] + 12)) <= 1, geometry
                assert geometry['bottom'] <= geometry['height'] - 12 + 1, geometry
                assert geometry['scrollHeight'] > geometry['clientHeight'] and geometry['remaining'] > 0 and not geometry['clipped'] and not geometry['horizontalOverflow'], geometry
                if compact:
                    assert geometry['passthrough'], geometry
                command_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-keep]')
                tip_keep = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]')
                ActionChains(browser).scroll_from_origin(ScrollOrigin.from_element(tip_keep), 0, geometry['scrollHeight']).perform()
                WebDriverWait(browser, 5).until(lambda driver: driver.execute_script("return document.querySelector('.attention-alerts').scrollTop > 0"))
                command_keep.click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]').get_attribute('class')
                command_close = browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"] [data-toast-close]')
                command_close.click()
                WebDriverWait(browser, 5).until(lambda driver: not driver.find_elements(By.CSS_SELECTOR, f'[data-alert-id="{geometry["commandId"]}"]'))
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-keep]').click()
                assert 'kept' in browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"]').get_attribute('class')
                browser.find_element(By.CSS_SELECTOR, f'[data-alert-id="{geometry["tipId"]}"] [data-toast-close]').click()
    finally:
        browser.execute_script("""
          for(const id of window.__shortNoticeIds||[]) removeAttentionAlert(id);
          document.body.classList.toggle('app-topbar-touch-compact',arguments[0]);
          setAppViewportOverride(null);
        """, original_compact)
        browser.execute_cdp_cmd('Emulation.clearDeviceMetricsOverride', {})


def test_native_filesystem_root_lists_through_http_and_rendered_finder(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-filesystem-root")
    request.addfinalizer(lambda: browser.get("about:blank"))
    alias = server.paths.workspace_dir / "filesystem-root"
    alias.symlink_to("/", target_is_directory=True)

    def get_terminal(path):
        response = server.request(path)
        if response[0] != 202:
            return response
        receipt = json.loads(response[2])

        def terminal(_server):
            result = _server.request(receipt["operation"]["status_url"])
            return result if result[0] != 202 else False

        return WebDriverWait(server, 5, poll_frequency=0.05).until(terminal)

    for root in ("/", "/./", "/tmp/..", str(alias)):
        for route in ("/api/fs/fast/list", "/api/fs/list", "/api/fs/info"):
            status, _, body = get_terminal(route + "?" + urlencode({"path": root}))
            payload = json.loads(body)
            assert status == 200, (root, route, payload)
            if route.endswith("/info"):
                assert payload["kind"] == "dir" and payload["realpath"] == "/"
            else:
                assert payload["entries"], (root, route, payload)
        status, _, body = get_terminal("/api/fs/read?" + urlencode({"path": root}))
        assert status == 400 and json.loads(body)["error_code"] == "invalid_request", body

    connection = HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        connection.request("POST", "/api/fs/batch", headers={"Content-Type": "application/json"}, body=json.dumps({
            "requests": [{"type": kind, "path": "/", "include_repo_info": False} for kind in ("list", "info")],
        }))
        response = connection.getresponse()
        status, body = response.status, response.read()
    finally:
        connection.close()
    if status == 202:
        receipt = json.loads(body)
        status, _, body = get_terminal(receipt["operation"]["status_url"])
    assert status == 200, body
    assert all(item["ok"] and item["status"] == 200 for item in json.loads(body)["responses"]), body

    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    try:
        result = browser.execute_async_script("""
          const done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => typeof openFileExplorerManualRoot === 'function'
              && typeof openFileExplorerPane === 'function', {timeoutMs:10000});
            await openFileExplorerPane();
            await openFileExplorerManualRoot('/');
            await window.__yomuxTestWaitFor(() => currentFileExplorerRoot() === '/'
              && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === '/usr'),
              {timeoutMs:5000});
            return {root:currentFileExplorerRoot(), visible:[...document.querySelectorAll('.file-tree-row')]
              .some(row => row.dataset.path === '/usr')};
          })().then(done, error => done({error:String(error.stack || error)}));
        """)
        assert result == {"root": "/", "visible": True}, result
    finally:
        browser.get("about:blank")


def test_native_filesystem_events_refresh_visible_finder_and_hidden_finder_editor(browser, isolated_dev_server_factory, request):
    server = isolated_dev_server_factory("native-finder")
    request.addfinalizer(lambda: browser.get("about:blank"))
    directory = server.paths.home_dir / "finder-watch"
    directory.mkdir()
    existing = directory / "existing.json"
    existing.write_text('{"baseline":true}\n', encoding="utf-8")
    created = directory / "created.json"
    browser.get(server.base_url + "/?sessions=__finder__&layout=left&tabs=left:__finder__")
    setup = browser.execute_async_script("""
      const root = arguments[0], existing = arguments[1], done = arguments[arguments.length - 1];
      (async () => {
        await window.__yomuxTestWaitFor(() => typeof openFileExplorerAt === 'function'
          && typeof clientEventTransportState !== 'undefined', {timeoutMs:10000});
        await window.__yomuxTestWaitFor(() => clientEventTransportState.connected, {timeoutMs:10000});
        await openFileExplorerPane();
        await window.__yomuxTestWaitFor(() => fileExplorerTreePaneIsVisible(), {timeoutMs:5000});
        await openFileExplorerManualRoot(root);
        await syncServerWatchRoots({immediate:true});
        await window.__yomuxTestWaitFor(() => serverWatchRootsState.registered
          && serverWatchRootsState.signature.includes(root) && fileExplorerFilesystemWatchToken
          && clientEventTransportState.connected && clientEventTransportState.source
          && [...document.querySelectorAll('.file-tree-row')].some(row => row.dataset.path === existing), {timeoutMs:10000});
        window.__nativeFinderEvents = [];
        window.__nativeFinderObservedSources = new WeakSet();
        window.__observeNativeFinderSource = source => {
          if (window.__nativeFinderObservedSources.has(source)) return;
          window.__nativeFinderObservedSources.add(source);
          source.addEventListener('fs_changed', event => window.__nativeFinderEvents.push(JSON.parse(event.data)));
        };
        window.__observeNativeFinderSource(clientEventTransportState.source);
        return {root:currentFileExplorerRoot(), token:fileExplorerFilesystemWatchToken,
          registered:serverWatchRootsState.registered, connected:clientEventTransportState.connected};
      })().then(done, error => done({error:String(error.stack || error),
        root:typeof currentFileExplorerRoot === 'function' ? currentFileExplorerRoot() : '',
        token:typeof fileExplorerFilesystemWatchToken === 'string' ? fileExplorerFilesystemWatchToken : '',
        registered:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registered : null,
        rootMode:typeof fileExplorerRootMode === 'string' ? fileExplorerRootMode : '',
        finderVisible:typeof fileExplorerTreePaneIsVisible === 'function' ? fileExplorerTreePaneIsVisible() : null,
        registrationPending:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.registrationPending : null,
        inFlight:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.inFlight : null,
        baselinePromise:typeof serverWatchRootsState === 'object' ? Boolean(serverWatchRootsState.watchBaselinePromise) : null,
        rejectedSignature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.rejectedSignature : '',
        signature:typeof serverWatchRootsState === 'object' ? serverWatchRootsState.signature : '',
        connected:typeof clientEventTransportState === 'object' ? clientEventTransportState.connected : null,
        apiFailures:typeof jsDebugEvents === 'object' ? jsDebugEvents.filter(event => event.type === 'api'
          && (event.error || event.ok === false)).slice(-5).map(event => ({path:event.endpoint,status:event.status,
            error:event.error,code:event.code})) : [],
        visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)}));
    """, str(directory), str(existing))
    status, _, watch_body = server.request("/api/fs/watch-diff?full=1")
    setup["watchHttpStatus"] = status
    setup["watchResponse"] = json.loads(watch_body)
    evidence = Path(product_env("E2E_EVIDENCE_DIR", "") or server.paths.log_dir) / "native-finder-setup.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(setup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(setup, sort_keys=True))
    assert "error" not in setup, setup
    observations = []
    try:
        for path, present in ((created, True), (existing, False)):
            before = browser.execute_script("return window.__nativeFinderEvents.length;")
            if present:
                path.write_text('{"created":true}\n', encoding="utf-8")
            else:
                path.unlink()
            result = browser.execute_async_script("""
              const root = arguments[0], path = arguments[1], present = arguments[2], before = arguments[3];
              const done = arguments[arguments.length - 1];
              (async () => {
                await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.slice(before).some(event =>
                  (event.payload?.paths || []).some(changed => changed === root || changed === path)), {timeoutMs:10000});
                let converged = true;
                try {
                  await window.__yomuxTestWaitFor(() => [...document.querySelectorAll('.file-tree-row')]
                    .some(row => row.dataset.path === path) === present, {timeoutMs:5000});
                } catch (_) { converged = false; }
                return {path, present, converged, token:fileExplorerFilesystemWatchToken,
                  events:window.__nativeFinderEvents.slice(before),
                  visible:[...document.querySelectorAll('.file-tree-row')].map(row => row.dataset.path)};
              })().then(done, error => done({error:String(error.stack || error)}));
            """, str(directory), str(path), present, before)
            assert "error" not in result, result
            observations.append(result)
            evidence.with_name("native-finder-transitions.json").write_text(
                json.dumps(observations, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        editor_setup = browser.execute_async_script("""
          const path = arguments[0], done = arguments[arguments.length - 1];
          (async () => {
            const item = await openFileInEditor(path, {name:'created.json'}, {userInitiated:true});
            removeSessionFromLayout(fileExplorerItemId);
            syncClientEventDemand({immediate:true});
            await window.__yomuxTestWaitFor(() => clientEventTransportState.connected
              && !clientEventTransportState.replacementSource, {timeoutMs:10000});
            await syncServerWatchRoots({immediate:true});
            await window.__yomuxTestWaitFor(() => !fileExplorerTreePaneIsVisible()
              && serverWatchRootsState.registered && JSON.parse(serverWatchRootsState.signature).files.includes(path)
              && fileState.get(path)?.content.includes('"created":true')
              && panelNodes.get(item)?.querySelector('.cm-content'), {timeoutMs:10000});
            window.__nativeFinderEvents = [];
            window.__observeNativeFinderSource(clientEventTransportState.source);
            return {item, content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              signature:serverWatchRootsState.signature, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created))
        evidence.with_name("native-editor-setup.json").write_text(
            json.dumps(editor_setup, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor_setup, editor_setup
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.cm-content');", editor_setup["item"]
        ).click()
        assert browser.execute_script("return fileEditorPathHasFocus(arguments[0]);", str(created)) is True
        watch_read_start = browser.execute_script("return jsDebugEvents.length;")
        created.write_text('{"external":true,"revision":2}\n', encoding="utf-8")
        editor = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => window.__nativeFinderEvents.some(event =>
              (event.payload?.paths || []).includes(path)), {timeoutMs:10000});
            let converged = true;
            try {
              await window.__yomuxTestWaitFor(() => {
                const state = fileState.get(path), panel = panelNodes.get(item);
                const status = panel?.querySelector('.file-editor-status-message');
                return state?.externalChanged && state?.externalReloadDeferred && fileEditorPathHasFocus(path)
                  && state.content === '{"created":true}\\n'
                  && panel?.querySelector('.cm-content')?.textContent.includes('"created":true')
                  && status?.textContent === t('dialog.externalTitle') && status.getClientRects().length > 0;
              }, {timeoutMs:5000});
            } catch (_) { converged = false; }
            return {converged, finderVisible:fileExplorerTreePaneIsVisible(), content:fileState.get(path)?.content,
              externalChanged:fileState.get(path)?.externalChanged,
              deferred:Boolean(fileState.get(path)?.externalReloadDeferred), focused:fileEditorPathHasFocus(path),
              status:panelNodes.get(item)?.querySelector('.file-editor-status-message')?.textContent,
              watchReadbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api'
                && event.endpoint === '/api/fs/watch-diff').map(event => ({url:event.url,status:event.status,requestId:event.requestId})),
              events:window.__nativeFinderEvents, token:fileExplorerFilesystemWatchToken};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], watch_read_start)
        evidence.with_name("native-editor-transition.json").write_text(
            json.dumps(editor, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in editor, editor
        assert editor["watchReadbacks"] and all(row["status"] == 200 and row["requestId"] for row in editor["watchReadbacks"]), editor
        observations.append(editor)
        read_start = browser.execute_script("return jsDebugEvents.length;")
        toast_geometry = browser.execute_script("""
          const item=arguments[0], reload=panelNodes.get(item).querySelector('.file-editor-reload-panel');
          const enabled=startupHelpersEnabled, compact=document.body.classList.contains('app-topbar-touch-compact');
          startupHelpersEnabled=true;
          const tip=showStartupHelperTip();
          startupHelpersEnabled=enabled;
          const stack=tip.parentElement, samples=[];
          const sample=label => {
            const button=reload.getBoundingClientRect(), bounds=stack.getBoundingClientRect();
            samples.push({label,bottom:bounds.bottom,viewport:innerHeight,top:bounds.top,
              reloadBottom:button.bottom,
              hit:document.elementFromPoint(button.x+button.width/2,button.y+button.height/2) === reload});
          };
          try {
            sample('startup');
            const command=showTimedStatusNotice('A command could not complete.');
            sample('command-present');
            document.body.classList.add('app-topbar-touch-compact');
            sample('compact');
            removeAttentionAlert(Number(command.dataset.alertId));
            sample('command-removed');
          } finally {
            document.body.classList.toggle('app-topbar-touch-compact',compact);
          }
          sample('restored');
          return {kind:tip.dataset.toastKind,samples};
        """, editor_setup["item"])
        evidence.with_name("native-editor-toast-geometry.json").write_text(
            json.dumps(toast_geometry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert toast_geometry["kind"] == "startup-helper", toast_geometry
        assert all(sample["hit"] and sample["top"] >= sample["reloadBottom"]
                   and sample["bottom"] <= sample["viewport"] - 12 + 1
                   for sample in toast_geometry["samples"]), toast_geometry
        browser.execute_script(
            "return panelNodes.get(arguments[0]).querySelector('.file-editor-reload-panel');", editor_setup["item"]
        ).click()
        reloaded = browser.execute_async_script("""
          const path = arguments[0], item = arguments[1], before = arguments[2], done = arguments[arguments.length - 1];
          (async () => {
            await window.__yomuxTestWaitFor(() => fileState.get(path)?.content === '{"external":true,"revision":2}\\n'
              && panelNodes.get(item)?.querySelector('.cm-content')?.textContent.includes('"external":true')
              && !fileState.get(path)?.externalChanged, {timeoutMs:5000});
            return {content:fileState.get(path)?.content, finderVisible:fileExplorerTreePaneIsVisible(),
              readbacks:jsDebugEvents.slice(before).filter(event => event.type === 'api' && event.endpoint === '/api/fs/read'
                && new URL(event.url, location.href).searchParams.get('path') === path)
                .map(event => ({url:event.url,status:event.status,requestId:event.requestId}))};
          })().then(done, error => done({error:String(error.stack || error)}));
        """, str(created), editor_setup["item"], read_start)
        evidence.with_name("native-editor-reload.json").write_text(
            json.dumps(reloaded, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        assert "error" not in reloaded, reloaded
        assert reloaded["finderVisible"] is False and reloaded["readbacks"], reloaded
        assert created.read_text(encoding="utf-8") == reloaded["content"]
    finally:
        run_fixture_cleanup_phases("native filesystem browser", [
            ("browser", lambda: browser.get("about:blank")),
            ("server", server.stop),
        ])
    assert all(item["converged"] for item in observations), observations
