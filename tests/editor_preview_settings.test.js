const {runSuites} = require('./browser_helpers/layout_test_helper');
const {runEditorPreviewSuite} = require('./browser_helpers/editor_preview_suite');

runSuites([() => runEditorPreviewSuite({shardIndex: 2, shardCount: 3})]);
