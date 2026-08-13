// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

function createPageTransportLifecycle(pageLifecycle = null) {
  let lifecycleScope = null;
  let retirementReason = '';
  let transportGeneration = 0;
  let retirementGeneration = -1;
  const owner = {
    start() {
      if (lifecycleScope?.current()) return owner;
      const scope = createLifecycleScope({
        isCurrent: () => lifecycleScope === scope,
        onDispose: () => {
          if (lifecycleScope === scope) lifecycleScope = null;
        },
      });
      lifecycleScope = scope;
      if (!pageLifecycle || typeof pageLifecycle.addEventListener !== 'function') return owner;
      for (const eventName of ['beforeunload', 'pagehide']) {
        scope.ownEvent(`page-${eventName}`, pageLifecycle, eventName, () => {
          if (retirementReason && transportGeneration <= retirementGeneration) return;
          retirementReason = `page_${eventName}`;
          retirementGeneration = transportGeneration;
        });
      }
      scope.ownEvent('page-pageshow', pageLifecycle, 'pageshow', () => { retirementReason = ''; });
      return owner;
    },
    begin() { return transportGeneration; },
    noteDelivery(startGeneration = null) {
      if (retirementReason && Number(startGeneration) <= retirementGeneration) {
        transportGeneration = retirementGeneration + 1;
      }
    },
    reasonSince(startGeneration) {
      return retirementReason && Number(startGeneration) <= retirementGeneration ? retirementReason : '';
    },
    dispose(reason = 'page-transport-lifecycle-stop') {
      retirementReason = '';
      return lifecycleScope?.dispose(reason) || false;
    },
  };
  return Object.freeze(owner);
}

const pageTransportLifecycle = createPageTransportLifecycle(globalThis.window || globalThis).start();
