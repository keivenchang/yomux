"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

// H5/H7/H8 are NOT unimplemented. Each one is already written against the shared gate browser
// harness in tests/test_gate_panels.py, which drives a real Selenium `browser` fixture that this
// Node harness cannot provide. The three bodies here used to be unconditional
// assert.fail("not implemented: requires the shared gate browser harness"), so this shard was red
// at HEAD for a harness that exists — a second copy of a contract that already has an owner.
//
// This file now pins the ownership instead of duplicating the contract: each gate must have exactly
// one implementation, in Python, and must still be marked xfail(strict=True) while F9 SubsystemSpec
// is unbuilt. When F9 lands and those xfail markers are removed, these checks fail and force this
// placeholder to be retired rather than silently drifting from its owner.
const GATE_OWNER = "tests/test_gate_panels.py";
const owner = fs.readFileSync(path.join(__dirname, "test_gate_panels.py"), "utf8");

function ownedGate(functionName) {
  const index = owner.indexOf(`def ${functionName}(`);
  assert.notEqual(index, -1, `${GATE_OWNER} owns ${functionName}`);
  assert.equal(
    owner.indexOf(`def ${functionName}(`, index + 1),
    -1,
    `${GATE_OWNER} declares ${functionName} exactly once`,
  );
  const decorators = owner.slice(0, index);
  assert.ok(/@pytest\.mark\.browser\s*\n(?:@[^\n]*\n)*$/.test(decorators), `${functionName} runs on the shared gate browser harness`);
  return decorators;
}

test("H5 Preferences toggles a subsystem and its rendered row changes state without reload", () => {
  // The test must execute the UI event path, then observe the corresponding row state change without a page reload.
  const decorators = ownedGate("test_h5_preferences_toggle_changes_its_subsystem_row_without_reload");
  assert.ok(/@pytest\.mark\.xfail\(strict=True, reason="NOT-APPLICABLE on v0\.6\.10; waits for F9 SubsystemSpec Preferences controls"\)\s*\n$/.test(decorators), "H5 stays a strict xfail until F9 SubsystemSpec Preferences controls exist");
  assert.ok(owner.includes("[data-subsystem-toggle]") && owner.includes("performance.getEntriesByType('navigation')"), "H5 drives the toggle and proves no navigation reload");
});

test("H7 a paused subsystem is recoverable from the rendered UI", () => {
  // The test must recover a paused subsystem through UI controls and observe its new state without YAML edits.
  const decorators = ownedGate("test_h7_paused_subsystem_recovers_from_its_rendered_ui_control");
  assert.ok(/@pytest\.mark\.xfail\(strict=True, reason="NOT-APPLICABLE on v0\.6\.10; waits for F9 SubsystemSpec subsystem recovery"\)\s*\n$/.test(decorators), "H7 stays a strict xfail until F9 SubsystemSpec subsystem recovery exists");
  assert.ok(owner.includes("[data-subsystem-resume]"), "H7 recovers through the rendered row control, not a YAML edit");
});

test("H8 Preferences and Tabs ordering share one feature-derived ordering", () => {
  // The test must compare the rendered Preferences and Tabs drop-down order derived from SubsystemSpec.features.
  const decorators = ownedGate("test_h8_preferences_and_tabs_dropdown_share_feature_order");
  assert.ok(/@pytest\.mark\.xfail\(strict=True, reason="NOT-APPLICABLE on v0\.6\.10; waits for F9 SubsystemSpec feature ordering"\)\s*\n$/.test(decorators), "H8 stays a strict xfail until F9 SubsystemSpec feature ordering exists");
  assert.ok(owner.includes("[data-preferences-subsystem-feature]") && owner.includes("[data-tabs-subsystem-feature]"), "H8 compares both rendered feature orders");
});
