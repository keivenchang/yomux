// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
// Source entry for the vendored CodeMirror spike bundle.
// Rebuild with:
// cd tools/codemirror-bundle
// npm ci
// npm run build

import {basicSetup} from "codemirror";
import {Compartment, EditorState} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  crosshairCursor,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
  rectangularSelection,
} from "@codemirror/view";
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentLess,
  indentMore,
  indentWithTab,
  toggleComment,
} from "@codemirror/commands";
import {
  closeSearchPanel,
  findNext,
  findPrevious,
  gotoLine,
  highlightSelectionMatches,
  openSearchPanel,
  replaceAll,
  replaceNext,
  search,
  searchKeymap,
} from "@codemirror/search";
import {
  MergeView,
  unifiedMergeView,
} from "@codemirror/merge";
import {
  HighlightStyle,
  LanguageDescription,
  StreamLanguage,
  bracketMatching,
  defaultHighlightStyle,
  foldGutter,
  indentOnInput,
  syntaxHighlighting,
} from "@codemirror/language";
import {javascript} from "@codemirror/lang-javascript";
import {python} from "@codemirror/lang-python";
import {rust} from "@codemirror/lang-rust";
import {json} from "@codemirror/lang-json";
import {html} from "@codemirror/lang-html";
import {css} from "@codemirror/lang-css";
import {markdown} from "@codemirror/lang-markdown";
import {xml} from "@codemirror/lang-xml";
import {yaml} from "@codemirror/lang-yaml";
import {shell} from "@codemirror/legacy-modes/mode/shell";
import {toml} from "@codemirror/legacy-modes/mode/toml";
import {tags} from "@lezer/highlight";

window.YOLOmuxCodeMirror = {
  EditorState,
  EditorView,
  ViewPlugin,
  basicSetup,
  bracketMatching,
  closeSearchPanel,
  Compartment,
  crosshairCursor,
  Decoration,
  css,
  defaultHighlightStyle,
  defaultKeymap,
  drawSelection,
  dropCursor,
  findNext,
  findPrevious,
  foldGutter,
  HighlightStyle,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSelectionMatches,
  history,
  historyKeymap,
  html,
  gotoLine,
  indentOnInput,
  indentLess,
  indentMore,
  indentWithTab,
  javascript,
  json,
  keymap,
  LanguageDescription,
  lineNumbers,
  markdown,
  MergeView,
  openSearchPanel,
  python,
  rectangularSelection,
  replaceAll,
  replaceNext,
  rust,
  search,
  searchKeymap,
  shell,
  StreamLanguage,
  syntaxHighlighting,
  tags,
  toggleComment,
  toml,
  unifiedMergeView,
  xml,
  yaml,
};
