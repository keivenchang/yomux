const {runSuites, sourceBetween} = require('./browser_helpers/layout_test_helper');
const {runEditorPreviewSuite} = require('./browser_helpers/editor_preview_suite');

runSuites([() => runEditorPreviewSuite({shardIndex: 1, shardCount: 3})]);
