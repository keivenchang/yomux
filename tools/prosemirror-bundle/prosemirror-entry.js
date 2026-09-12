// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
// Source entry for the vendored ProseMirror spike bundle.
// Rebuild with:
// cd tools/prosemirror-bundle
// npm ci
// npm run build

import {MarkdownParser, MarkdownSerializer, defaultMarkdownParser, defaultMarkdownSerializer} from 'prosemirror-markdown';
import {DOMParser, DOMSerializer, Schema} from 'prosemirror-model';
import {EditorState, Plugin, Selection, TextSelection, Transaction} from 'prosemirror-state';
import {baseKeymap, setBlockType, toggleMark} from 'prosemirror-commands';
import {EditorView} from 'prosemirror-view';
import {keymap} from 'prosemirror-keymap';
import {history, redo, undo} from 'prosemirror-history';
import {addListNodes, bulletList, listItem, orderedList, wrapInList} from 'prosemirror-schema-list';
import MarkdownIt from 'markdown-it';

window.YOLOmuxProseMirror = {
  DOMParser,
  DOMSerializer,
  EditorState,
  EditorView,
  MarkdownParser,
  MarkdownSerializer,
  Plugin,
  Schema,
  Selection,
  TextSelection,
  Transaction,
  addListNodes,
  bulletList,
  baseKeymap,
  defaultMarkdownParser,
  defaultMarkdownSerializer,
  keymap,
  history,
  listItem,
  orderedList,
  redo,
  setBlockType,
  toggleMark,
  undo,
  wrapInList,
  MarkdownIt,
};
