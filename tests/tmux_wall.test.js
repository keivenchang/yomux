const assert = require('assert');

globalThis.tmuxWallBootstrap = {
  locale: 'zh-Hans',
  catalog: {
    'common.unknown': '未知',
    'state.needs-input': '需要输入',
    'tmuxWall.error.captureFailed': '无法捕获 {target}。',
    'tmuxWall.pane.empty': '空',
  },
};

const {
  messageFieldText,
  paneBadgeItems,
  paneErrorText,
  paneTitle,
  wallText,
} = require('../static/tmux-wall.js');

assert.strictEqual(wallText('tmuxWall.error.captureFailed', {target: 'dev:0.0'}), '无法捕获 dev:0.0。');
assert.strictEqual(paneTitle({target: ''}), '空');

assert.strictEqual(
  paneErrorText({
    error: 'tmux capture failed: socket unavailable',
    error_key: 'tmuxWall.error.captureFailed',
    error_params: {target: 'dev:0.0'},
  }),
  '无法捕获 dev:0.0。',
);
assert.strictEqual(
  messageFieldText({error: 'raw diagnostic', error_key: 'missing.catalog.key'}, 'error'),
  'raw diagnostic',
);

const localizedBadges = paneBadgeItems({
  display: {
    attention_kind: 'question',
    attention_label: 'Question',
    attention_label_key: 'state.needs-input',
  },
  reason_code: 'needs-input',
  reason_label: 'needs-input',
  reason_label_key: 'state.needs-input',
  agent_kind: 'claude',
});
assert.deepStrictEqual(localizedBadges, [
  {text: '需要输入', kind: 'question'},
  {text: 'claude', kind: 'agent'},
]);

const unknownBadges = paneBadgeItems({
  reason_code: 'internal_reason_code',
  reason_label: 'internal_reason_code',
  reason_label_key: 'common.unknown',
});
assert.deepStrictEqual(unknownBadges, [{text: '未知', kind: 'internal_reason_code'}]);
assert.ok(unknownBadges.every(badge => badge.text !== 'internal_reason_code'));

const legacyBadges = paneBadgeItems({reason_code: 'internal_reason_code'});
assert.deepStrictEqual(legacyBadges, []);

console.log('tmux-wall i18n suite: passed');
