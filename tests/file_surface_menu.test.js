const assert = require('node:assert/strict');
const fs = require('node:fs');

let passed = 0;
let failed = 0;
function test(label, fn) {
  try {
    fn();
    passed += 1;
  } catch (error) {
    failed += 1;
    process.exitCode = 1;
    console.error(`✗ ${label}: ${error.message}`);
  }
}

const menus = fs.readFileSync('static_src/js/yolomux/30_app_menus.js', 'utf8');
const terminalBoot = fs.readFileSync('static_src/js/yolomux/99_terminal_boot.js', 'utf8');
const css = fs.readFileSync('static_src/css/yolomux/10_topbar_menus.css', 'utf8');
const filePanelCss = fs.readFileSync('static_src/css/yolomux/60_editor_file_panels.css', 'utf8');
const panel = fs.readFileSync('static_src/js/yolomux/90_changes_editor.js', 'utf8');
const shell = fs.readFileSync('static_src/js/yolomux/78_panel_shell.js', 'utf8');
const actions = fs.readFileSync('static_src/js/yolomux/70_layout_actions.js', 'utf8');
const dockview = fs.readFileSync('static_src/js/yolomux/75_dockview_layout.js', 'utf8');
const coreUtils = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');

test('File submenu restores the desktop triplet through the layout owner', () => {
  assert.match(menus, /function fileSurfaceMenuItems\(\)[\s\S]*Array\.isArray\(fileSurfaceItems\)[\s\S]*openFileSurfaceFromMenu\(item\)/);
  assert.match(menus, /if \(narrowSingleColumnMode\(\)\)[\s\S]*virtualCommands\.unshift\(\.\.\.fileSurfaces\)[\s\S]*else \{[\s\S]*menuCommand\(t\('yoagent\.capability\.finderDifferTabber\.name'\), \(\) => openFileSurfaceFromMenu\(finderItemId\)/);
  assert.match(menus, /checked: fileSurfaceItems\.every\(item => itemInLayout\(item\)\),[\s\S]*partial: fileSurfaceItems\.some\(item => itemInLayout\(item\)\) && !fileSurfaceItems\.every\(item => itemInLayout\(item\)\)/);
  assert.match(actions, /async function openFileSurfaceFromMenu\(item\)[\s\S]*sidePaneConstrainedMode\(\)[\s\S]*openFileSurfacePane\(resolved\)[\s\S]*fileSurfaceItems\.filter\(candidate => !itemInLayout\(candidate\)\)[\s\S]*layoutWithSidePaneItems\(layoutSlots, missing, \{[\s\S]*side: paneSideLeft[\s\S]*forceCreate: true[\s\S]*activatePaneTab/);
  assert.doesNotMatch(menus.slice(menus.indexOf('function fileSurfaceMenuItems()'), menus.indexOf('function appMenuTree()')), /toggleFinderPane\(|selectSession\(fileExplorerItemId\)/);
});

test('partial triplet availability paints the left menu indicator without claiming all are open', () => {
  assert.match(menus, /item\.partial === true \? 'partially-checked' : ''/);
  assert.match(css, /\.app-menu-command\[data-partial="true"\] \.app-menu-check\s*\{[\s\S]*color-mix\(in srgb, var\(--active-control-bg\) 48%, transparent\)/);
});

test('File destinations have a single-line icon label detail presentation', () => {
  assert.match(menus, /className: 'app-menu-file-destination'/);
  assert.match(menus, /item\.className === 'app-menu-file-destination' \? 'app-menu-label' : ''/);
  assert.match(css, /\.app-menu-file-destination \.app-menu-content\s*\{[\s\S]*grid-template-columns/);
  assert.match(css, /\.app-menu-file-destination \.app-menu-detail\s*\{[\s\S]*text-align:\s*end/);
});

test('File navigation destinations share one localized alphabetical sort owner', () => {
  assert.match(menus, /function compareLocalizedMenuLabels\(left, right\)[\s\S]*localeCompare/);
  assert.match(menus, /function sortMenuCommandsByLabel\(commands\)[\s\S]*compareLocalizedMenuLabels\(left\?\.label, right\?\.label\)/);
  assert.match(menus, /const fileDestinationCommands = sortMenuCommandsByLabel\(\[[\s\S]*fileMenuPanelCommands\(\)[\s\S]*menu\.file\.share[\s\S]*common\.preferences[\s\S]*\]\)/);
});

test('Cmd/Ctrl+B delegates the triplet transaction without terminal fallback placement', () => {
  const shortcut = terminalBoot.slice(terminalBoot.indexOf('function toggleFileExplorerShortcut()'), terminalBoot.indexOf('function handleFocusedTerminalCopyShortcut()'));
  assert.match(shortcut, /typeof toggleAllFileSurfaces === 'function'\) return toggleAllFileSurfaces\(\)/);
  assert.doesNotMatch(shortcut, /applyLayoutSlots\(|selectSession\(fileExplorerItemId\)|layoutWithoutItem/);
});

test('file-surface pane headers use shared minimize and expand controls, not a local close X', () => {
  const builder = panel.slice(panel.indexOf('function createFileExplorerPanel('), panel.indexOf('function refreshFileExplorerPanelTree('));
  assert.match(builder, /controlsHtml: virtualPanelInnerControlsHtml\(item\)/);
  assert.doesNotMatch(builder, /file-explorer-panel-close/);
  assert.match(filePanelCss, /\.panel\.file-explorer-panel \.file-explorer-toolbar\s*\{\s*clear:\s*both;/);
  assert.doesNotMatch(filePanelCss, /\.panel\.file-explorer-panel \.virtual-panel-controls/);
});

test('independent triplets retain their pane tab strip and generic actions exclude Side Panes', () => {
  const strip = shell.slice(shell.indexOf('function updatePaneTabStrip('), shell.indexOf('function reconcilePaneTabChildren('));
  assert.doesNotMatch(strip, /isFileExplorerItem\(activeItemForSide\(side\)\)[\s\S]*strip\.hidden = true/);
  assert.match(actions, /function paneTabsForGenericActions\([\s\S]*slotIsSidePane\(slot, slots\) \? \[\] : paneTabs\(slot, slots\)/);
  assert.match(actions, /function canPaneExpand[\s\S]*slotIsSidePane\(targetSlot, slots\)/);
});

test('Dockview keeps the group tab strip for singleton file surfaces', () => {
  const leaf = dockview.slice(dockview.indexOf('function dockviewSerializedLeaf('), dockview.indexOf('function adoptDockviewLayout('));
  assert.match(leaf, /hideHeader: placeholder/);
  assert.doesNotMatch(leaf, /tabs\.length === 1 && layoutIsFileSurfaceItem/);
});

test('Dockview center-drops an allowed Differ into the triplet home through the shared layout move', () => {
  assert.match(dockview, /const paneInfo = dockviewPaneContentDropInfo\(event\);[\s\S]*paneInfo\.intent\.zone === 'middle'[\s\S]*dockviewPaneContentDropAllowed\(paneInfo\)[\s\S]*moveSessionToSlot\(paneInfo\.item, paneInfo\.intent\.targetSlot/);
  assert.match(dockview, /const tabInsertion = dockviewTabInsertionInfo\(event\);[\s\S]*slotIsSidePane\(tabInsertion\.targetSlot\)[\s\S]*paneRoleAllowsItemTransfer\(tabInsertion\.item, tabInsertion\.sourceSlot, tabInsertion\.targetSlot\)[\s\S]*moveSessionToSlot\(tabInsertion\.item, tabInsertion\.targetSlot/);
  assert.match(dockview, /dockviewFinishTabPointerDrag\(event\)[\s\S]*dockviewGroupForPoint[\s\S]*dropIntentAllowsSession\(state\.item, contentIntent\)[\s\S]*moveSessionToSlot\(state\.item, contentTargetSlot/);
});

test('Vertical Side Pane tab menus omit More desc and reuse the shared directional Move row', () => {
  assert.match(coreUtils, /if \(!slotIsSidePane\(sourceSlot\)\) appendDescription\(\)/);
  assert.match(coreUtils, /showTabContextMenu\(item, x, y, options = \{\}\)[\s\S]*appendTabSplitCommands\(menu, item, options\)/);
  assert.doesNotMatch(coreUtils, /vertical-side-pane-edge-move|appendVerticalSidePaneEdgeMoveCommand/);
});

test('Finder and Differ render shared selectors with independent selected-session owners', () => {
  const finderBuilder = panel.slice(panel.indexOf('function createFileExplorerPanel('), panel.indexOf('function refreshFileExplorerPanelTree('));
  assert.match(finderBuilder, /file-explorer-primary-row[\s\S]*fileExplorerDiffSessionControlHtml\(fileExplorerFinderTargetSession\(\), 'finder'\)[\s\S]*file-explorer-path-row[\s\S]*view === 'differ'[\s\S]*fileExplorerDiffSessionControlHtml\(fileExplorerSessionFilesTargetSession\(\), 'differ'\)/);
  assert.match(panel, /function switchFileExplorerFinderSession\(session\)[\s\S]*fileExplorerFinderSelectedSession = session[\s\S]*scheduleFileExplorerActiveTabSync\(session, \{explicit: true\}\)/);
  assert.match(panel, /function switchFileExplorerChangesSession\(session\)[\s\S]*fileExplorerChangesSelectedSession = session/);
});

test('Dockview file surfaces inherit the common outer header controls and never render an inner copy', () => {
  assert.match(terminalBoot, /function virtualPanelInnerControlsHtml\(session, options = \{\}\)[\s\S]*dockviewLayoutEnabled\(\) \? '' : virtualPanelControlsHtml\(session, options\)/);
  assert.match(dockview, /function dockviewHeaderActionsHtml\(item, slot = slotForItem\(item\)\)[\s\S]*if \(!isLayoutItem\(item\)\) return ''[\s\S]*if \(slotIsSidePane\(slot\)\)[\s\S]*if \(isVirtualItem\(item\)\) return `\$\{paneHandle\}\$\{virtualPanelControlsHtml\(item/);
  assert.match(dockview, /function hideDockviewInnerPaneTabs\(panel\)[\s\S]*head\.querySelector\('\.virtual-panel-controls'\)[\s\S]*controls\.remove\(\)/);
  assert.match(shell, /function paneTabInnerHtml\(item, rowOptions = \{\}\)[\s\S]*const isLegacyFiles = type\?\.key === 'files'[\s\S]*if \(!isLegacyFiles\)/);
});

test('Finder rows require their own pointer-down before pointer-up can activate them', () => {
  const finderSource = fs.readFileSync('static_src/js/yolomux/40_file_explorer_files.js', 'utf8');
  assert.match(finderSource, /row\.onpointerup = event => \{[\s\S]*const start = row\.__fileTreePointerDown;[\s\S]*if \(!start\)[\s\S]*row\.__fileTreeSuppressClick = true/);
  assert.match(finderSource, /row\.onclick = event => \{[\s\S]*if \(row\.__fileTreeSuppressClick\)[\s\S]*return;/);
});

console.log(`\nfile-surface menu suite: ${passed} passed, ${failed} failed`);
