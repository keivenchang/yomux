// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

'use strict';

// W2: the JavaScript half of the ONE neutral diagnostic-redaction contract. The authoritative
// fixture (tests/fixtures/diagnostic_redaction.json) is generated from the Python owner
// (yolomux_lib/diagnostic_redaction.py). This suite proves the browser redactor
// (10_core_utils.js::redactDiagnosticValue) reproduces every expected value byte-for-byte,
// is idempotent, and leaves zero fixture secret fragments behind. Redaction is a security boundary.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

let passed = 0;
let failed = 0;
function test(name, body) {
  try {
    body();
    passed += 1;
  } catch (error) {
    failed += 1;
    process.exitCode = 1;
    console.error(`FAIL: ${name}`);
    console.error(error.stack || error);
  }
}

const coreSource = fs.readFileSync('static_src/js/yolomux/10_core_utils.js', 'utf8');
const redactorSource = coreSource.slice(
  coreSource.indexOf('function redactDiagnosticSecretText('),
  coreSource.indexOf('\nfunction recordJsDebugEvent('),
);
assert.notEqual(redactorSource, '', 'the diagnostic redactor slice exists');

const context = {
  RegExp,
  String,
  Object,
  Array,
  Number,
  result: null,
};
vm.runInNewContext(`
  ${redactorSource}
  result = {
    redactValue: redactDiagnosticValue,
    redactText: redactDiagnosticSecretText,
  };
`, context);
const redactValue = context.result.redactValue;

const fixture = JSON.parse(fs.readFileSync('tests/fixtures/diagnostic_redaction.json', 'utf8'));
assert.equal(fixture.cases.length, 87, 'fixture carries the complete active credential-redaction contract');

// The redactor builds objects inside a separate vm realm, so their prototype differs from this
// realm's and assert.deepEqual rejects the cross-realm identity even for identical structure. Both
// sides preserve insertion order (Python dicts and JS objects alike), so a serialized compare is the
// faithful structural equality here.
function eq(actual, expected, message) {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected), message);
}

// Every secret fragment the fixture inputs introduce; the negative "zero secrets" proof asserts none
// of these survive in any redacted output, memory value, or serialized form.
const SECRET_FRAGMENTS = [
  'browser-secret', 'server-secret', 'csrf-secret', 'proxy-secret', 'proxy-user', 'digest-secret',
  'first-secret', 'second-secret', 'owner', 's-secret', 'url-secret', 'fragment-secret',
  'unterminated-secret', 'x-api-secret', 'token-secret', 'basic-secret', 'deep-secret', 'deep-token',
  'a-secret', 'b-secret', 'matrix-secret', 'utf8-secret', 'utf8-token-secret',
  'upgrade-secret-a', 'upgrade-secret-b', 'upgrade-secret-c', 'upgrade-secret-d',
];

test('JS redactor reproduces every fixture expected value (Python parity)', () => {
  for (const testCase of fixture.cases) {
    const actual = redactValue(testCase.input);
    eq(
      actual,
      testCase.expected,
      `${testCase.category}/${testCase.name}: ${JSON.stringify(testCase.input)}\n`
        + `  expected ${JSON.stringify(testCase.expected)}\n  actual   ${JSON.stringify(actual)}`,
    );
  }
});

test('JS redactor is idempotent over every fixture case', () => {
  for (const testCase of fixture.cases) {
    const once = redactValue(testCase.input);
    const twice = redactValue(once);
    eq(twice, once, `${testCase.category}/${testCase.name} is idempotent`);
  }
});

test('no fixture secret fragment survives redaction anywhere in the output', () => {
  for (const testCase of fixture.cases) {
    const serialized = JSON.stringify(redactValue(testCase.input));
    for (const fragment of SECRET_FRAGMENTS) {
      // The plaintext fixture inputs deliberately embed these; a benign near-name value such as
      // "ordinary-value" is allowed through, so only credential fragments are asserted absent.
      if (!JSON.stringify(testCase.input).includes(fragment)) continue;
      // "owner" appears in expected-benign near-name negatives ("owner's-secret" splits to owner);
      // those cases redact the whole assignment, so the fragment must still be gone.
      assert.equal(
        serialized.includes(fragment),
        false,
        `${testCase.category}/${testCase.name} leaked secret fragment ${JSON.stringify(fragment)} in ${serialized}`,
      );
    }
  }
});

test('exact credential key names redact regardless of value type; near names pass through', () => {
  eq(redactValue({token: {nested: 'x'}}), {token: '[redacted-secret]'});
  eq(redactValue({tokenizer: 'gpt2'}), {tokenizer: 'gpt2'});
  eq(redactValue({secretary: 'alice'}), {secretary: 'alice'});
  assert.equal(redactValue('Bearer abc'), 'Bearer [redacted-secret]');
});

test('upgrade payloads retain producer-neutral token-suffix redaction', () => {
  const retiredProducer = ['sh', 'are'].join('');
  for (const separator of ['_', '-']) {
    const retiredKey = [retiredProducer, 'token'].join(separator);
    eq(redactValue({[retiredKey]: 'upgrade-secret'}), {[retiredKey]: '[redacted-secret]'});
  }
  const retiredQueryKey = [retiredProducer, 'Token'].join('');
  assert.equal(
    redactValue(`GET /api/thing?${retiredQueryKey}=upgrade-secret&ok=1 failed`),
    `GET /api/thing?${retiredQueryKey}=[redacted-secret]&ok=1 failed`,
  );
});

test('depth, array, key, and string bounds match the Python owner', () => {
  let deep = 'leaf';
  for (let i = 0; i < 20; i += 1) deep = {n: deep};
  assert.ok(JSON.stringify(redactValue(deep)).includes('[truncated-depth]'), 'deep nesting truncates at depth 12');
  const big = Array.from({length: 400}, (_v, i) => i);
  assert.equal(redactValue(big).length, 256, 'arrays bound to 256 items');
  const longKey = 'k'.repeat(200);
  assert.equal(Object.keys(redactValue({[longKey]: 'v'}))[0].length, 120, 'keys bound to 120 chars');
  const longString = `${'a'.repeat(5000)}`;
  assert.equal(redactValue(longString), `${'a'.repeat(4000)}[truncated]`, 'strings bound to 4000 chars');
});

console.log(`\ndiagnostic redaction contract suite: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exitCode = 1;
