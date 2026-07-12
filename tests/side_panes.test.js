const {
  assert,
  canonical,
  fs,
  TestElement,
  loadYolomux,
  test,
  testAsync,
  runSuites,
} = require('./layout_test_helper');

async function runSidePaneSuite() {
  test('Side Pane role inherits the generic pane contract and overrides only specialization data', () => {
    const api = loadYolomux('', ['1']);
    const generic = canonical(api.genericPaneRoleDefinition);
    const side = canonical(api.sidePaneRoleDefinition);
    assert.deepStrictEqual(generic, {
      kind: 'generic',
      side: null,
      controls: 'standard',
      tabSizing: 'preference',
      preserveWidth: false,
      maxViewportFraction: 1,
      outermost: false,
    });
    assert.deepStrictEqual(side, {
      ...generic,
      kind: 'side',
      controls: 'minimize-only',
      tabSizing: 'intrinsic',
      preserveWidth: true,
      maxViewportFraction: 1 / 3,
      outermost: true,
    });
    assert.deepStrictEqual(canonical(api.paneRoleDefinition(api.paneRoleSide, api.paneSideRight)), {...side, side: 'right'});
    assert.deepStrictEqual(canonical(api.paneRoleDefinition('invalid', 'left')), generic);
  });

  test('TAB_TYPES owns the complete side-pane item placement policy', () => {
    const api = loadYolomux('', ['1']);
    const policies = Object.fromEntries(api.TAB_TYPES.map(type => [type.key, type.panePlacement || api.panePlacementGenericOnly]));
    assert.deepStrictEqual(
      Object.entries(policies).filter(([, policy]) => policy === api.panePlacementSideRequired).map(([key]) => key),
      ['finder', 'differ', 'tabber'],
    );
    assert.deepStrictEqual(
      Object.entries(policies).filter(([, policy]) => policy === api.panePlacementSideAllowed).map(([key]) => key),
      ['info', 'yoagent', 'chat', 'debug'],
    );
    assert.equal(policies.preferences, api.panePlacementGenericOnly);
    assert.equal(policies['search-history'], api.panePlacementGenericOnly);
    assert.equal(policies['file-editor'], api.panePlacementGenericOnly);
  });

  test('one role classifier enforces required, allowed, and generic-only placement', () => {
    const api = loadYolomux('', ['1']);
    const generic = api.paneRoleDefinition(api.paneRoleGeneric);
    const sideLeft = api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft);
    assert.equal(api.paneRoleAllowsItem(sideLeft, api.finderItemId), true);
    assert.equal(api.paneRoleAllowsItem(sideLeft, api.infoItemId), true);
    assert.equal(api.paneRoleAllowsItem(sideLeft, api.debugPaneItemId), true);
    assert.equal(api.paneRoleAllowsItem(sideLeft, '1'), false);
    assert.equal(api.paneRoleAllowsItem(sideLeft, api.prefsItemId), false);
    assert.equal(api.paneRoleAllowsItem(generic, api.finderItemId), false);
    assert.equal(api.paneRoleAllowsItem(generic, api.finderItemId, {allowRequiredInGeneric: true}), true);
    assert.equal(api.paneRoleAllowsItem(generic, api.infoItemId), true);
    assert.equal(api.paneRoleAllowsItem(generic, api.prefsItemId), true);
    assert.equal(api.paneRoleAllowsItem(generic, '1'), true);
  });

  test('side role and edge round-trip through compact tab state while generic state stays compatible', () => {
    const api = loadYolomux('', ['1']);
    const sideLeft = api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 22),
      left: api.paneStateWithTabs([api.finderItemId, api.differItemId], api.differItemId, sideLeft),
      right: api.paneStateWithTabs(['1'], '1'),
    };
    const encoded = api.layoutTabsParamValue(slots);
    assert.equal(encoded, 'left:@side-left,finder,differ*;right:1');
    const decoded = api.layoutTabStatesFromParam(encoded);
    assert.deepStrictEqual(canonical(decoded.get('left')), {
      tabs: [api.finderItemId, api.differItemId],
      active: api.differItemId,
      paneRole: api.paneRoleSide,
      side: api.paneSideLeft,
    });
    assert.deepStrictEqual(canonical(decoded.get('right')), {tabs: ['1'], active: '1'});
  });

  test('legacy redundant empty panes are pruned while intentional and final fillers round-trip', () => {
    const api = loadYolomux('', ['1']);
    const stale = api.layoutFromParam(
      'row@24.6(slot1,row@24.6(slot2,left))',
      'slot1:@side-left,finder,differ,tabber;slot2:__empty_pane__;left:1,debug',
    );
    assert.equal(api.layoutSlotKeys(stale).includes('slot2'), false,
      'a legacy placeholder beside real Generic content is repaired out of the URL layout');
    assert.deepStrictEqual(canonical(api.layoutSlotKeys(stale)), ['slot1', 'left']);

    const intentional = api.layoutFromParam(
      'row@22(slot1,left)',
      'slot1:@side-left,finder;left:__empty_pane_v2__',
    );
    assert.equal(api.paneIsPlaceholder('left', intentional), true,
      'a versioned intentional placeholder remains as the final Generic filler');
    assert.equal(api.layoutTabsParamValue(intentional),
      'slot1:@side-left,finder;left:__empty_pane_v2__');
  });

  test('untyped legacy triplet home migrates to explicit left Side Pane role', () => {
    const api = loadYolomux('?layout=row@22(slot1,right)&tabs=slot1:finder,differ,tabber;right:1', ['1']);
    const slot = api.currentSlots().slot1;
    assert.deepStrictEqual(canonical(api.paneRoleForState(slot)), canonical(api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)));
    assert.equal(api.layoutTabsParamValue(api.currentSlots()).startsWith('slot1:@side-left,finder,differ,tabber'), true);
  });

  test('invalid serialized Side Pane edge normalizes to generic', () => {
    const api = loadYolomux('', ['1']);
    const decoded = api.layoutTabStatesFromParam('left:@side-top,1');
    assert.deepStrictEqual(canonical(decoded.get('left')), {tabs: ['1'], active: '1'});
    assert.deepStrictEqual(canonical(api.paneRoleForState(decoded.get('left'))), canonical(api.genericPaneRoleDefinition));
  });

  test('share seed and geometry digest carry explicit Side Pane role', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId, api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1'], '1'),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.shareLayoutSeed().tabs, 'side:@side-left,finder;main:1');
    assert.deepStrictEqual(canonical(api.shareSlotDigestSnapshot().slots), [
      {slot: 'side', placeholder: false, paneRole: 'side', side: 'left'},
      {slot: 'main', placeholder: false, paneRole: 'generic', side: null},
    ]);
  });

  test('Dockview JSON adoption retains the prior explicit pane role', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId, api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1'], '1'),
    };
    const adopted = api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(slots), slots);
    assert.deepStrictEqual(canonical(api.paneRoleForSlot('side', adopted)), canonical(api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)));
    assert.deepStrictEqual(canonical(adopted.side), {
      tabs: [api.finderItemId],
      active: api.finderItemId,
      paneRole: api.paneRoleSide,
      side: api.paneSideLeft,
    });
  });

  test('Dockview adoption permits dual-role YO!* transfers and rejects incompatible transfers', () => {
    const api = loadYolomux('', ['1']);
    const previous = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.infoItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1'], '1'),
    };
    const invalid = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1', api.infoItemId], api.infoItemId),
    };
    const adopted = api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(invalid), previous);
    assert.deepStrictEqual(canonical(adopted), canonical(invalid), 'YO!info may be adopted into a Generic Pane');

    const invalidFinder = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.infoItemId], api.infoItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1', api.finderItemId], api.finderItemId),
    };
    const rejected = api.layoutSlotsFromDockviewJson(api.dockviewJsonFromLayoutSlots(invalidFinder), previous);
    assert.deepStrictEqual(canonical(rejected), canonical(previous), 'Finder remains Vertical-Side-only');
  });

  test('legacy Finder dock adapter preserves the triplet in an explicit Side Pane', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('left'), api.leafNode('right'), 22),
      left: api.paneStateWithTabs([api.finderItemId, api.differItemId, api.tabberItemId], api.differItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      right: api.paneStateWithTabs(['1'], '1'),
    };
    const docked = api.layoutWithFileExplorerDockedLeft(slots, {active: api.finderItemId});
    const sideSlot = Object.keys(docked).find(slot => api.paneRoleForSlot(slot, docked).kind === api.paneRoleSide);
    assert.ok(sideSlot);
    assert.deepStrictEqual(canonical(docked[sideSlot].tabs), [api.finderItemId, api.differItemId, api.tabberItemId]);
    assert.equal(docked[sideSlot].active, api.finderItemId);
    assert.deepStrictEqual(canonical(api.paneRoleForSlot(sideSlot, docked)),
      canonical(api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)));
  });

  test('role-neutral Side Pane helpers own both edges and legacy inferred-home names are gone', () => {
    const api = loadYolomux('', ['1']);
    const generic = {
      [api.layoutTreeKey]: api.leafNode('main'),
      main: api.paneStateWithTabs([api.prefsItemId], api.prefsItemId),
    };
    const withRight = api.layoutWithSidePaneItems(generic, [api.infoItemId], {
      side: api.paneSideRight,
      forceCreate: true,
      active: api.infoItemId,
    });
    const rightSlot = api.sidePaneSlot(api.paneSideRight, withRight);
    assert.ok(rightSlot);
    assert.equal(withRight[api.layoutTreeKey].children[1].slot, rightSlot);
    assert.equal(withRight[api.layoutTreeKey].pct, 78);
    assert.deepStrictEqual(canonical(api.layoutSidePaneRootSplit(withRight[api.layoutTreeKey], withRight)), {
      sideIndex: 1,
      sideSlot: rightSlot,
      sideSlots: [rightSlot],
      side: api.paneSideRight,
      contentIndex: 0,
      pct: 78,
    });
    const serialized = api.dockviewJsonFromLayoutSlots(withRight);
    assert.equal(serialized.panels[api.infoItemId].maximumWidth, 400,
      'the right Side Pane carries the same native one-third Dockview width cap as the left edge');
    assert.equal(Object.hasOwn(serialized.panels[api.prefsItemId], 'maximumWidth'), false,
      'ordinary edge panes remain unconstrained Generic Panes');

    const sources = [
      'static_src/js/yolomux/20_layout_state.js',
      'static_src/js/yolomux/70_layout_actions.js',
      'static_src/js/yolomux/75_dockview_layout.js',
      'static_src/js/yolomux/78_panel_shell.js',
      'static_src/js/yolomux/99_terminal_boot.js',
      'static_src/css/yolomux/40_layout_panes_tabs.css',
    ].map(path => fs.readFileSync(path, 'utf8')).join('\n');
    assert.doesNotMatch(sources, /layoutFileSurfaceHome|layoutWithFileSurfaceHome|layoutWithDefaultFileSurfaceHome|slotIsFileSurfaceHome|dockedFileExplorerRootSplit|preserveMissingFileExplorer|fileExplorerSplitPercent|file-explorer-home-column|file-surface-home-pane/);
  });

  test('normalization repairs malformed Side Panes to one outer vertical column per edge', () => {
    const api = loadYolomux('', ['1']);
    const malformed = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('genericLeft'),
        api.splitNode('column', api.leafNode('sideNested'), api.leafNode('genericBottom'), 50),
        60,
      ),
      genericLeft: api.paneStateWithTabs(['1'], '1'),
      sideNested: api.paneStateWithTabs([api.finderItemId, api.prefsItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      genericBottom: api.paneStateWithTabs([api.differItemId], api.differItemId),
    };
    const repaired = api.normalizeLayoutSlots(malformed);
    const sideSlot = api.sidePaneSlot(api.paneSideLeft, repaired);
    assert.equal(repaired[api.layoutTreeKey].children[0].slot, sideSlot);
    assert.deepStrictEqual(canonical(api.paneTabs(sideSlot, repaired)), [api.finderItemId, api.differItemId]);
    const genericSlot = api.layoutSlotKeys(repaired).find(slot => !api.slotIsSidePane(slot, repaired));
    assert.deepStrictEqual(canonical(api.paneTabs(genericSlot, repaired)), ['1', api.prefsItemId]);
    assert.equal(api.sidePaneSlots(repaired).length, 1);

    const duplicate = JSON.parse(JSON.stringify(repaired));
    const secondSide = 'duplicateSide';
    duplicate[secondSide] = api.paneStateWithTabs([api.tabberItemId], api.tabberItemId,
      api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft));
    duplicate[api.layoutTreeKey] = api.splitNode('row', duplicate[api.layoutTreeKey], api.leafNode(secondSide), 70);
    const merged = api.normalizeLayoutSlots(duplicate);
    assert.equal(api.sidePaneSlotsForSide(api.paneSideLeft, merged).length, 2);
    assert.equal(merged[api.layoutTreeKey].children[0].split, 'column');
    assert.deepStrictEqual(canonical(api.sidePaneSlotsForSide(api.paneSideLeft, merged).flatMap(slot => api.paneTabs(slot, merged))),
      [api.finderItemId, api.differItemId, api.tabberItemId]);
  });

  test('wide defaults create only a left Side Pane and side-only layouts retain a generic filler', () => {
    const api = loadYolomux('', ['1']);
    api.setNativeViewportForTest({width: 1200, height: 800});
    const defaults = api.defaultLayoutSlots();
    const defaultLeft = api.sidePaneSlot(api.paneSideLeft, defaults);
    assert.ok(defaultLeft);
    assert.equal(api.sidePaneSlot(api.paneSideRight, defaults), null);
    assert.equal(defaults[api.layoutTreeKey].children[0].slot, defaultLeft);
    assert.deepStrictEqual(canonical(api.paneTabs(defaultLeft, defaults)),
      [api.finderItemId, api.differItemId, api.tabberItemId]);

    const sideOnly = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('leftSide'), api.leafNode('rightSide'), 50),
      leftSide: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      rightSide: api.paneStateWithTabs([api.infoItemId], api.infoItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideRight)),
    };
    const repaired = api.normalizeLayoutSlots(sideOnly);
    const genericSlots = api.layoutSlotKeys(repaired).filter(slot => !api.slotIsSidePane(slot, repaired));
    assert.equal(genericSlots.length, 1);
    assert.equal(api.paneIsPlaceholder(genericSlots[0], repaired), true);
    assert.equal(repaired[api.layoutTreeKey].children[0].slot, api.sidePaneSlot(api.paneSideLeft, repaired));
    assert.equal(repaired[api.layoutTreeKey].children[1].children[1].slot, api.sidePaneSlot(api.paneSideRight, repaired));
  });

  test('Fill and generic layout actions preserve both Side Panes', () => {
    const api = loadYolomux('', ['1', '2']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('sideLeft'),
        api.splitNode('row', api.splitNode('row', api.leafNode('main1'), api.leafNode('main2'), 50), api.leafNode('sideRight'), 70),
        20,
      ),
      sideLeft: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main1: api.paneStateWithTabs(['1'], '1'),
      main2: api.paneStateWithTabs(['2'], '2'),
      sideRight: api.paneStateWithTabs([api.infoItemId], api.infoItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideRight)),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.tabCanFillWorkspace(api.finderItemId), false, 'Side Pane tabs cannot Fill the workspace');
    assert.equal(api.toggleTabWorkspaceFill('1'), true);
    const filled = api.currentSlots();
    assert.deepStrictEqual(canonical(api.paneTabs(api.sidePaneSlot(api.paneSideLeft, filled), filled)), [api.finderItemId]);
    assert.deepStrictEqual(canonical(api.paneTabs(api.sidePaneSlot(api.paneSideRight, filled), filled)), [api.infoItemId]);
    const genericSlots = api.layoutSlotKeys(filled).filter(slot => !api.slotIsSidePane(slot, filled));
    assert.equal(genericSlots.length, 1);
    assert.deepStrictEqual(canonical(api.paneTabs(genericSlots[0], filled)), ['1']);
  });

  test('Side Pane widths clamp to one third and survive generic compaction', () => {
    const api = loadYolomux('', ['1', '2']);
    const oversized = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('side'),
        api.splitNode('row', api.leafNode('main1'), api.leafNode('main2'), 45),
        60,
      ),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main1: api.paneStateWithTabs(['1'], '1'),
      main2: api.paneStateWithTabs(['2'], '2'),
    };
    const clamped = api.normalizeLayoutSlots(oversized);
    assert.ok(Math.abs(api.sidePaneWidthPercent(api.paneSideLeft, clamped) - (100 / 3)) < 1e-9);
    assert.ok(Math.abs(clamped[api.layoutTreeKey].pct - (100 / 3)) < 1e-9);

    const sized = JSON.parse(JSON.stringify(oversized));
    sized[api.layoutTreeKey].pct = 30;
    api.setLayoutSlotsForTest(sized);
    api.removePaneFromLayout('2');
    assert.equal(api.sidePaneWidthPercent(api.paneSideLeft, api.currentSlots()), 30,
      'removing an ordinary sibling does not make the Side Pane absorb its width');
  });

  test('viewport capability removes Side Panes when constrained and rehomes only the triplet when widened', () => {
    const api = loadYolomux('', ['1']);
    assert.equal(api.sidePaneMinimumViewportWidthPx(), 900);
    assert.equal(api.sidePanesAvailable({width: 899, height: 700}), false);
    assert.equal(api.sidePanesAvailable({width: 900, height: 700}), true);

    api.setNativeViewportForTest({width: 1200, height: 800});
    const wide = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.differItemId, api.tabberItemId, api.infoItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs([api.prefsItemId], api.prefsItemId),
    };
    api.setLayoutSlotsForTest(wide);

    api.setNativeViewportForTest({width: 800, height: 800});
    assert.equal(api.compactCurrentLayoutSlotsForTest(), true);
    const constrained = api.currentSlots();
    assert.equal(api.sidePaneSlots(constrained).length, 0);
    assert.equal(api.layoutSlotKeys(constrained).length, 2, 'Side Pane constraint preserves ordinary Generic Pane splits');
    const constrainedItems = api.layoutSlotKeys(constrained).flatMap(slot => api.paneTabs(slot, constrained));
    assert.equal(constrainedItems.filter(item => [api.finderItemId, api.differItemId, api.tabberItemId].includes(item)).length, 3);

    api.setNativeViewportForTest({width: 1200, height: 800});
    assert.equal(api.compactCurrentLayoutSlotsForTest(), true);
    const widened = api.currentSlots();
    const leftSide = api.sidePaneSlot(api.paneSideLeft, widened);
    assert.deepStrictEqual(canonical(api.paneTabs(leftSide, widened)), [api.finderItemId, api.differItemId, api.tabberItemId]);
    const infoSlot = api.layoutSlotKeys(widened).find(slot => api.paneTabs(slot, widened).includes(api.infoItemId));
    assert.equal(api.slotIsSidePane(infoSlot, widened), false, 'YO!* stays in generic placement after constrained-to-wide restoration');
  });

  test('Side Pane role drives identical DOM data, intrinsic tabs, and minimize-only chrome', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs([api.prefsItemId], api.prefsItemId),
    };
    api.setLayoutSlotsForTest(slots);
    for (const html of [api.panelControlsHtml(api.finderItemId), api.dockviewHeaderActionsHtml(api.finderItemId, 'side')]) {
      assert.equal((html.match(/data-pane-minimize=/g) || []).length, 1);
      assert.doesNotMatch(html, /data-pane-(?:actions|expand|popout|close|drag)=|data-detail-toggle=/);
    }
    const infoHtml = api.dockviewHeaderActionsHtml(api.infoItemId, 'side');
    assert.equal((infoHtml.match(/data-pane-minimize=/g) || []).length, 1);
    assert.doesNotMatch(infoHtml, /data-pane-(?:actions|expand|popout|close|drag)=|data-detail-toggle=/);

    const sideNode = new TestElement('side-role-node');
    api.syncPaneRoleDom(sideNode, 'side');
    assert.equal(sideNode.dataset.paneRole, api.paneRoleSide);
    assert.equal(sideNode.dataset.paneSide, api.paneSideLeft);
    assert.equal(sideNode.style.getPropertyValue('--side-pane-max-viewport-fraction'), String(1 / 3));
    const genericNode = new TestElement('generic-role-node');
    api.syncPaneRoleDom(genericNode, 'main');
    assert.equal(genericNode.dataset.paneRole, api.paneRoleGeneric);
    assert.equal(genericNode.dataset.paneSide, undefined);

    const css = fs.readFileSync('static_src/css/yolomux/40_layout_panes_tabs.css', 'utf8');
    assert.match(css, /\.layout-column\[data-pane-role="side"\] \.pane-tab\s*\{[\s\S]*width:\s*auto/);
    assert.match(css, /\.dv-groupview\[data-pane-role="side"\] \.dv-tab\s*\{[\s\S]*width:\s*max-content/);
    assert.match(css, /\.dv-groupview\[data-pane-role="side"\] \.dv-tab > \.dockview-pane-tab\s*\{[\s\S]*width:\s*auto/);
    assert.match(css, /max-inline-size:\s*calc\(100vw \* var\(--side-pane-max-viewport-fraction\)\)/);
  });

  test('drop, split, root, gutter, and pane-swap capability matrix follows item placement capability', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('sideLeft'),
        api.splitNode(
          'row',
          api.splitNode('row', api.leafNode('main1'), api.leafNode('main2'), 50),
          api.leafNode('sideRight'),
          72,
        ),
        20,
      ),
      sideLeft: api.paneStateWithTabs([api.finderItemId, api.infoItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main1: api.paneStateWithTabs(['1', api.debugPaneItemId], '1'),
      main2: api.paneStateWithTabs([api.prefsItemId], api.prefsItemId),
      sideRight: api.paneStateWithTabs([api.chatItemId], api.chatItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideRight)),
    };
    api.setLayoutSlotsForTest(slots);
    const middle = targetSlot => ({targetSlot, zone: 'middle', targetRect: {width: 800, height: 600}});
    assert.equal(api.dropIntentAllowsSession('1', middle('sideLeft')), false);
    assert.equal(api.dropIntentAllowsSession(api.prefsItemId, middle('sideLeft')), false);
    assert.equal(api.dropIntentAllowsSession(api.debugPaneItemId, middle('sideLeft')), true,
      'YO!stats may move from a Generic Pane into a Vertical Side Pane');
    assert.equal(api.dropIntentAllowsSession(api.infoItemId, middle('main1')), true,
      'YO!info may move from a Vertical Side Pane into a Generic Pane');
    assert.equal(api.dropIntentAllowsSession(api.finderItemId, middle('sideRight')), true,
      'Side-to-Side transfer remains within the Side role');
    assert.equal(api.dropIntentAllowsSession(api.infoItemId, {...middle('sideRight'), zone: 'bottom'}), true,
      'Side Pane top/bottom edges create another leaf in the same fixed-width Side column');
    for (const item of [api.infoItemId, api.debugPaneItemId, api.yoagentItemId, api.chatItemId]) {
      for (const targetSlot of ['sideLeft', 'sideRight']) {
        for (const zone of ['top', 'bottom']) {
          assert.equal(api.dropIntentAllowsSession(item, {
            targetSlot,
            zone,
            targetRect: {width: 240, height: 600},
          }), true, `${item} may create a ${zone} leaf in narrow ${targetSlot}`);
        }
      }
    }
    assert.equal(api.dropIntentAllowsSession(api.debugPaneItemId, {
      targetSlot: 'sideLeft', zone: 'bottom', targetRect: {width: 240, height: 200},
    }), false, 'a Side Pane too short for two leaves rejects the split');
    assert.equal(api.dropIntentAllowsSession(api.prefsItemId, {
      targetSlot: 'sideLeft', zone: 'bottom', targetRect: {width: 240, height: 600},
    }), false, 'generic-only tabs cannot use a Side Pane edge');
    assert.equal(api.dropIntentAllowsSession('1', {boundary: 'root', zone: 'right'}), true,
      'generic root-edge movement creates another generic pane');
    assert.equal(api.dropIntentAllowsSession(api.finderItemId, {boundary: 'root', zone: 'right'}), true,
      'a left Side source may create or target the opposite right Side Pane');
    assert.equal(api.dropIntentAllowsSession(api.finderItemId, {boundary: 'root', zone: 'left'}), false);
    assert.equal(api.dropIntentAllowsSession('1', {boundary: 'gutter', splitPath: '', zone: 'right'}), false,
      'the root gutter cannot split through Side Pane topology');
    assert.equal(api.dropIntentAllowsSession('1', {boundary: 'gutter', splitPath: '1.0', zone: 'right'}), true,
      'a gutter wholly inside the generic subtree remains available');
    assert.equal(api.paneSwapAllowed('sideLeft', 'main1'), false);
    assert.equal(api.paneSwapAllowed('main1', 'main2'), true);
  });

  test('tab transfers allow only side-allowed items to cross pane roles', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.infoItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1', api.debugPaneItemId], '1'),
    };
    assert.equal(api.paneRoleAllowsItemTransfer('1', 'main', 'main', slots), true);
    assert.equal(api.paneRoleAllowsItemTransfer('1', 'main', 'side', slots), false);
    assert.equal(api.paneRoleAllowsItemTransfer(api.infoItemId, 'side', 'main', slots), true);
    assert.equal(api.paneRoleAllowsItemTransfer(api.debugPaneItemId, 'main', 'side', slots), true,
      'side-allowed items may transfer in both directions');
    for (const item of [api.infoItemId, api.debugPaneItemId, api.yoagentItemId, api.chatItemId]) {
      assert.equal(api.panePlacementForItem(item), api.panePlacementSideAllowed);
      assert.equal(api.paneRoleAllowsItemTransfer(item, 'side', 'main', slots), true);
      assert.equal(api.paneRoleAllowsItemTransfer(item, 'main', 'side', slots), true);
    }
    assert.equal(api.paneRoleAllowsItemTransfer(api.finderItemId, 'side', 'main', slots), false,
      'Side-required Finder cannot enter a Generic Pane');
    assert.equal(api.paneRoleAllowsItemTransfer(api.prefsItemId, 'main', 'side', slots), false,
      'generic-only Preferences cannot enter a Vertical Side Pane');
    assert.equal(api.paneRoleAllowsItemTransfer(api.finderItemId, 'side', null, slots, {
      targetRole: api.paneRoleDefinition(api.paneRoleSide, api.paneSideRight),
    }), true);
  });

  await testAsync('root-edge moves preserve the source role instead of deriving role from the edge', async () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.infoItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs([api.prefsItemId], api.prefsItemId),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(await api.splitSessionAtLayoutBoundary(api.prefsItemId, 'right', 'main'), true);
    const genericRight = api.layoutSlotKeys(api.currentSlots()).find(slot => api.paneTabs(slot).includes(api.prefsItemId));
    assert.equal(api.paneRoleForSlot(genericRight).kind, api.paneRoleGeneric,
      'an ordinary tab at the right edge remains in an ordinary generic pane');
    assert.equal(api.paneRoleForSlot(genericRight).side, null);

    api.setLayoutSlotsForTest(slots);
    assert.equal(await api.splitSessionAtLayoutBoundary(api.infoItemId, 'right', 'side'), true);
    const sideRight = api.layoutSlotKeys(api.currentSlots()).find(slot => api.paneTabs(slot).includes(api.infoItemId));
    assert.equal(api.paneRoleForSlot(sideRight).kind, api.paneRoleSide);
    assert.equal(api.paneRoleForSlot(sideRight).side, api.paneSideRight);
  });

  await testAsync('Side Pane minimize, tab close, final close, and reopen preserve role boundaries', async () => {
    const api = loadYolomux('', ['1']);
    const itemsIn = slots => api.layoutSlotKeys(slots).flatMap(slot => api.paneTabs(slot, slots));
    const initial = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.differItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1'], '1'),
    };

    api.setLayoutSlotsForTest(initial);
    api.minimizePaneFromLayout(api.finderItemId);
    assert.equal(api.sidePaneSlots(api.currentSlots()).length, 0,
      'the pane minimize control removes the complete Side Pane');
    assert.deepStrictEqual(canonical(itemsIn(api.currentSlots())), ['1'],
      'minimizing a Side Pane never migrates its tabs into generic content');

    api.setLayoutSlotsForTest(initial);
    api.removeSessionFromLayout(api.finderItemId);
    const remainingSide = api.sidePaneSlot(api.paneSideLeft, api.currentSlots());
    assert.ok(remainingSide);
    assert.deepStrictEqual(canonical(api.paneTabs(remainingSide, api.currentSlots())), [api.differItemId],
      'closing one file-surface tab leaves its Side Pane sibling in place');

    api.removeSessionFromLayout(api.differItemId);
    assert.equal(api.sidePaneSlots(api.currentSlots()).length, 0,
      'closing the final Side tab removes the empty Side Pane');
    assert.deepStrictEqual(canonical(itemsIn(api.currentSlots())), ['1'],
      'generic content remains after the final Side tab closes');

    assert.equal(await api.openFileSurfacePane(api.finderItemId), true);
    const reopenedSide = api.sidePaneSlot(api.paneSideLeft, api.currentSlots());
    assert.ok(reopenedSide, 'reopening Finder recreates the left Side Pane');
    assert.deepStrictEqual(canonical(api.paneTabs(reopenedSide, api.currentSlots())), [api.finderItemId]);
    assert.equal(api.paneTabs(reopenedSide, api.currentSlots()).includes(api.finderItemId), true);
    assert.deepStrictEqual(canonical(itemsIn(api.currentSlots()).filter(item => item === '1')), ['1']);
  });

  await testAsync('Generic to Side split prunes an emptied source when another Generic pane exists', async () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.splitNode('column', api.leafNode('sideTop'), api.leafNode('sideBottom'), 50),
        api.splitNode('row', api.leafNode('chatSource'), api.leafNode('terminal'), 45),
        22,
      ),
      sideTop: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      sideBottom: api.paneStateWithTabs([api.differItemId], api.differItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      chatSource: api.paneStateWithTabs([api.chatItemId], api.chatItemId),
      terminal: api.paneStateWithTabs(['1'], '1'),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(await api.splitSessionAtSlot(api.chatItemId, 'sideTop', 'bottom', 'chatSource'), true);
    assert.equal(api.layoutSlotKeys(api.currentSlots()).includes('chatSource'), false,
      'the empty Generic source is compacted because terminal remains as Generic content');
    assert.equal(api.layoutSlotKeys(api.currentSlots()).some(slot => api.paneIsPlaceholder(slot)), false,
      'the cross-role split does not leave an unrelated Generic placeholder');
    api.removeSessionFromLayout(api.chatItemId);
    assert.equal(api.layoutSlotKeys(api.currentSlots()).some(slot => api.paneIsPlaceholder(slot)), false,
      'closing the lower YO!chat leaf cannot reveal the old Generic source');
    assert.deepStrictEqual(canonical(api.layoutSlotKeys(api.currentSlots()).flatMap(slot => api.paneTabs(slot)).sort()),
      [api.differItemId, api.finderItemId, '1'].sort());
  });

  await testAsync('Generic to Side split preserves the final Generic workspace filler', async () => {
    const api = loadYolomux('', []);
    const slots = {
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('chatSource'), 22),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      chatSource: api.paneStateWithTabs([api.chatItemId], api.chatItemId),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(await api.splitSessionAtSlot(api.chatItemId, 'side', 'bottom', 'chatSource'), true);
    assert.equal(api.paneIsPlaceholder('chatSource', api.currentSlots()), true,
      'Side-only content retains one protected Generic filler');
  });

  test('empty Generic panes close independently while the final workspace filler remains', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('side'),
        api.splitNode('column', api.leafNode('upper'), api.leafNode('lower'), 50),
        22,
      ),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      upper: api.emptyPlaceholderPaneState(),
      lower: api.emptyPlaceholderPaneState(),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.canCloseEmptyPane('upper'), true);
    assert.equal(api.closeEmptyPaneFromLayout('upper'), true);
    assert.equal(api.layoutSlotKeys(api.currentSlots()).includes('upper'), false);
    assert.equal(api.paneIsPlaceholder('lower'), true);
    assert.equal(api.canCloseEmptyPane('lower'), false,
      'the final Generic filler is protected so a Side Pane cannot absorb the workspace');
    assert.equal(api.closeEmptyPaneFromLayout('lower'), false);
    assert.equal(api.paneIsPlaceholder('lower'), true);
    assert.equal(api.sidePaneWidthPercent(api.paneSideLeft, api.currentSlots()), 22);
  });

  await testAsync('Side Pane tab actions expose only vertical Move and Swap within the fixed-width edge column', async () => {
    const api = loadYolomux('', ['1']);
    const sideRole = api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.splitNode('column', api.leafNode('top'), api.leafNode('bottom'), 50),
        api.leafNode('main'),
        22,
      ),
      top: api.paneStateWithTabs([api.finderItemId], api.finderItemId, sideRole),
      bottom: api.paneStateWithTabs([api.differItemId], api.differItemId, sideRole),
      main: api.paneStateWithTabs(['1'], '1'),
    };
    api.setLayoutSlotsForTest(slots);
    api.setLayoutColumnRectsForTest({
      top: {left: 0, top: 0, right: 260, bottom: 318, width: 260, height: 318},
      bottom: {left: 0, top: 322, right: 260, bottom: 640, width: 260, height: 318},
      main: {left: 264, top: 0, right: 1200, bottom: 640, width: 936, height: 640},
    });
    const topCaps = canonical(api.tabDirectionalActionCapabilities(api.finderItemId, 'top'));
    assert.deepStrictEqual(topCaps.move, {left: false, right: true, top: false, bottom: true});
    assert.deepStrictEqual(topCaps.swap, {left: false, right: false, top: false, bottom: true});
    assert.deepStrictEqual(topCaps.targets, {left: null, right: null, top: null, bottom: 'bottom'});

    assert.equal(await api.swapLayoutItemDirectional(api.finderItemId, 'top', 'bottom'), true);
    assert.deepStrictEqual(canonical(api.paneTabs('top')), [api.differItemId]);
    assert.deepStrictEqual(canonical(api.paneTabs('bottom')), [api.finderItemId]);

    api.setLayoutSlotsForTest({
      [api.layoutTreeKey]: api.splitNode('row', api.leafNode('side'), api.leafNode('main'), 22),
      side: api.paneStateWithTabs([api.finderItemId, api.differItemId], api.finderItemId, sideRole),
      main: api.paneStateWithTabs(['1'], '1'),
    });
    api.setLayoutColumnRectsForTest({
      side: {left: 0, top: 0, right: 260, bottom: 640, width: 260, height: 640},
      main: {left: 264, top: 0, right: 1200, bottom: 640, width: 936, height: 640},
    });
    const singleCaps = canonical(api.tabDirectionalActionCapabilities(api.finderItemId, 'side'));
    assert.deepStrictEqual(singleCaps.move, {left: false, right: true, top: true, bottom: true});
    assert.deepStrictEqual(singleCaps.swap, {left: false, right: false, top: false, bottom: false});
    assert.equal(await api.moveLayoutItemDirectional(api.finderItemId, 'side', 'bottom'), true);
    const movedSlots = api.sidePaneSlotsForSide(api.paneSideLeft, api.currentSlots());
    assert.equal(movedSlots.length, 2);
    assert.equal(api.currentSlots()[api.layoutTreeKey].children[0].split, 'column');
    assert.equal(api.sidePaneWidthPercent(api.paneSideLeft, api.currentSlots()), 22);

    api.setLayoutSlotsForTest(slots);
    api.setLayoutColumnRectsForTest({
      top: {left: 0, top: 0, right: 360, bottom: 318, width: 360, height: 318},
      bottom: {left: 0, top: 322, right: 360, bottom: 640, width: 360, height: 318},
      main: {left: 364, top: 0, right: 1200, bottom: 640, width: 836, height: 640},
    });
    api.setLayoutSlotsForTest({
      ...slots,
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.splitNode('column', api.leafNode('top'), api.leafNode('bottom'), 50),
        api.leafNode('main'),
        30,
      ),
    });
    assert.equal(await api.moveLayoutItemDirectional(api.finderItemId, 'top', 'right'), true);
    const movedRightSlot = api.layoutSlotKeys(api.currentSlots()).find(slot => api.paneTabs(slot).includes(api.finderItemId));
    assert.equal(api.paneRoleForSlot(movedRightSlot).side, api.paneSideRight);
    assert.ok(Math.abs(api.sidePaneWidthPercent(api.paneSideRight, api.currentSlots()) - 30) < 1e-9,
      `creating the opposite Vertical Side Pane copies the source width: ${api.sidePaneWidthPercent(api.paneSideRight, api.currentSlots())}`);
  });

  test('closing one empty Generic pane preserves other intentional placeholders beside content', () => {
    const api = loadYolomux('', ['1']);
    const slots = {
      [api.layoutTreeKey]: api.splitNode(
        'row',
        api.leafNode('side'),
        api.splitNode(
          'column',
          api.leafNode('main'),
          api.splitNode('column', api.leafNode('upper'), api.leafNode('lower'), 50),
          40,
        ),
        22,
      ),
      side: api.paneStateWithTabs([api.finderItemId], api.finderItemId,
        api.paneRoleDefinition(api.paneRoleSide, api.paneSideLeft)),
      main: api.paneStateWithTabs(['1'], '1'),
      upper: api.emptyPlaceholderPaneState(),
      lower: api.emptyPlaceholderPaneState(),
    };
    api.setLayoutSlotsForTest(slots);
    assert.equal(api.closeEmptyPaneFromLayout('upper'), true);
    assert.equal(api.layoutSlotKeys(api.currentSlots()).includes('upper'), false);
    assert.equal(api.paneIsPlaceholder('lower'), true);
    assert.deepStrictEqual(canonical(api.paneTabs('main')), ['1']);
    assert.equal(api.canCloseEmptyPane('lower'), true,
      'a placeholder remains disposable while another Generic content pane exists');
  });
}

module.exports = {runSidePaneSuite};

if (require.main === module) {
  runSuites([runSidePaneSuite]);
}
