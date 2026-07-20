// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Editor navigation history helpers split from 92_codemirror_editor.js.

// — back/forward navigation history. The stack holds the layout ITEM ids of visited tabs (any
// kind: file editors/previews, terminals, Finder, Prefs, …), so Back returns to the previous tab worked
// on — not just files. recordEditorNav pushes a user-initiated activation; back/forward re-activate the
// item (re-opening a since-closed file from its path-encoded id). Bounded so the history can't grow
// without limit — the oldest entries drop past the cap.
const NAV_STACK_LIMIT = 50;
function recordEditorNav(item) {
  if (editorNav.navigating || !item) return;
  if (editorNav.stack[editorNav.index] === item) return;   // dedupe consecutive same-tab activations
  editorNav.stack = editorNav.stack.slice(0, editorNav.index + 1);   // a new activation after Back drops the forward tail
  editorNav.stack.push(item);
  collapseEditorNavPingPong();
  if (editorNav.stack.length > NAV_STACK_LIMIT) {
    editorNav.stack = editorNav.stack.slice(editorNav.stack.length - NAV_STACK_LIMIT);
  }
  editorNav.index = editorNav.stack.length - 1;
  updateEditorNavButtons();
}

function collapseEditorNavPingPong() {
  while (editorNav.stack.length >= 4) {
    const end = editorNav.stack.length;
    const first = editorNav.stack[end - 4];
    const second = editorNav.stack[end - 3];
    if (first !== editorNav.stack[end - 2] || second !== editorNav.stack[end - 1]) return;
    editorNav.stack.splice(end - 2, 2);
  }
}

// Re-activate a history item: focus its tab if still open; if it's a closed file editor/preview, re-open
// it from the path encoded in its id. Returns false when the item is gone and can't be restored (a
// closed terminal/Finder/etc.) so the caller can skip it.
async function activateNavItem(item) {
  const side = slotForItem(item);
  if (side) {
    activatePaneTab(side, item);   // userInitiated defaults falsey → does not re-record
    return true;
  }
  if (isFileEditorItem(item)) {
    const path = fileItemPath(item);
    if (path) {
      await openFileInEditor(path, basenameOf(path), {item});
      return true;
    }
  }
  return false;
}

async function editorNavGo(delta) {
  // Walk in `delta` direction, skipping entries that can't be re-activated (closed non-file tabs), so a
  // stale entry never dead-ends the history. The first activatable entry becomes the new position.
  let idx = editorNav.index + delta;
  while (idx >= 0 && idx < editorNav.stack.length) {
    const item = editorNav.stack[idx];
    editorNav.navigating = true;   // re-activation must NOT record a new entry
    let activated = false;
    try {
      activated = await activateNavItem(item);
    } finally {
      editorNav.navigating = false;
    }
    if (activated) {
      editorNav.index = idx;
      updateEditorNavButtons();
      return;
    }
    idx += delta;
  }
  updateEditorNavButtons();
}

function editorNavBack() { return editorNavGo(-1); }
function editorNavForward() { return editorNavGo(1); }

// The back/forward control lives in the GLOBAL TOPBAR (left of the search bar), not per editor pane —
// it's one global file-history control, like a browser's. Always visible; disabled at the ends.
function updateEditorNavButtons() {
  const back = document.getElementById('topbarNavBack');
  const forward = document.getElementById('topbarNavForward');
  if (back) back.disabled = editorNav.index <= 0;
  if (forward) forward.disabled = editorNav.index >= editorNav.stack.length - 1;
}
