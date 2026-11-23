// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
// Source entry for the vendored CodeMirror spike bundle.
// Rebuild with:
// cd tools/codemirror-bundle
// npm ci
// npm run build

import {collab, receiveUpdates, sendableUpdates, getSyncedVersion} from "@codemirror/collab";
import {basicSetup} from "codemirror";
import {Compartment, EditorState, ChangeSet, StateField, StateEffect, EditorSelection, Transaction} from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
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
  getOriginalDoc,
  originalDocChangeEffect,
  goToNextChunk,
  goToPreviousChunk,
  MergeView,
  unifiedMergeView,
} from "@codemirror/merge";
import {
  HighlightStyle,
  LanguageDescription,
  LanguageSupport,
  StreamLanguage,
  bracketMatching,
  defaultHighlightStyle,
  foldGutter,
  indentOnInput,
  syntaxHighlighting,
  ensureSyntaxTree,
} from "@codemirror/language";
import {javascript} from "@codemirror/lang-javascript";
import {python} from "@codemirror/lang-python";
import {rust} from "@codemirror/lang-rust";
import {json} from "@codemirror/lang-json";
import {html} from "@codemirror/lang-html";
import {css} from "@codemirror/lang-css";
import {markdown} from "@codemirror/lang-markdown";
import {Strikethrough, Table} from "@lezer/markdown";
import {xml} from "@codemirror/lang-xml";
import {yaml} from "@codemirror/lang-yaml";
import {shell} from "@codemirror/legacy-modes/mode/shell";
import {toml} from "@codemirror/legacy-modes/mode/toml";
import {stex} from "@codemirror/legacy-modes/mode/stex";
import {tags} from "@lezer/highlight";

window.YOmuxCodeMirror = {
  Transaction, WidgetType, collab, receiveUpdates, sendableUpdates, getSyncedVersion, ChangeSet, StateField, StateEffect, EditorSelection,
  EditorState,
  ensureSyntaxTree,
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
  LanguageSupport,
  lineNumbers,
  markdown,
  markdownStrikethrough: Strikethrough,
  markdownTable: Table,
  getOriginalDoc,
  originalDocChangeEffect,
  goToNextChunk,
  goToPreviousChunk,
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
  stex,
  StreamLanguage,
  syntaxHighlighting,
  tags,
  toggleComment,
  toml,
  unifiedMergeView,
  xml,
  yaml,
};
