// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const source = fs.readFileSync('static_src/js/yolomux/46_file_drop_actions.js', 'utf8');
const templates = {
  'drop.result.title.filePreview': 'Localized preview',
  'drop.result.preview.truncated': 'Localized truncation at {count}',
  'upload.dropActionResultTitle': 'Fallback result',
};
const interpolate = (text, params = {}) => String(text || '').replace(/\{(\w+)\}/g, (match, name) =>
  Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
const context = {
  t: (key, params) => interpolate(templates[key] || key, params),
  messageDescriptorText(descriptor, fallback = '') {
    const template = templates[String(descriptor?.key || '')];
    return template ? interpolate(template, descriptor?.params) : String(descriptor?.fallback || fallback || '');
  },
  userMessageText: value => String(value?.error || ''),
};
context.structuredMessageText = (value, field = 'message', fallback = '') => context.messageDescriptorText({
  key: value?.[`${field}_key`],
  params: value?.[`${field}_params`],
  fallback: value?.[field],
}, fallback);
vm.createContext(context);
vm.runInContext(`${source}\nthis.dropActionResultPresentationForTest = dropActionResultPresentation;`, context);

const presentation = context.dropActionResultPresentationForTest({
  title: 'Legacy preview',
  body: 'Legacy body',
  result: {
    title_key: 'drop.result.title.filePreview',
    title_params: {},
    blocks: [{
      path: '/tmp/app.log',
      sections: [[
        {raw: 'first\nsecond'},
        {key: 'drop.result.preview.truncated', params: {count: 80}},
      ]],
    }],
  },
});
assert.equal(presentation.title, 'Localized preview');
assert.equal(presentation.body, '## /tmp/app.log\n\nfirst\nsecond\nLocalized truncation at 80');
assert.deepEqual(
  context.dropActionResultPresentationForTest({title: 'Legacy title', body: 'Legacy body'}),
  {title: 'Legacy title', body: 'Legacy body'},
  'older server payloads retain their title/body compatibility path',
);
assert.ok(
  /function dropActionResultPresentation[\s\S]*structuredMessageText[\s\S]*messageDescriptorText/.test(source),
  'title and body segments reuse the shared structured-message parent',
);
console.log('drop-action result suite: 1 passed, 0 failed');
