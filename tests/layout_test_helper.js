const assert = require('assert');
const fs = require('fs');
const UI_PINS = JSON.parse(fs.readFileSync('tests/ui_pins.json', 'utf8'));  // shared color pins (see test_ui_pins.py)
const vm = require('vm');
const FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST = 'yolomux.fileExplorerOpen.v1';
const DEFAULT_TEST_SETTINGS = Object.freeze({
  appearance: Object.freeze({}),
  editor: Object.freeze({
    trim_trailing_whitespace_on_save: false,
    ensure_final_newline_on_save: false,
  }),
  file_explorer: Object.freeze({
    index_refresh_seconds: 120,
    new_entry_highlight_ms: 60000,
  }),
  notifications: Object.freeze({
    toast_duration_ms: 10000,
  }),
  performance: Object.freeze({
    latency_refresh_ms: 3000,
    event_log_refresh_ms: 5000,
    tabber_activity_refresh_ms: 15000,
    agent_status_pulse_period_ms: 1550,
    workflow_transition_glow_seconds: 60,
    popover_show_delay_ms: 1000,
    popover_hide_delay_ms: 300,
    menu_hover_open_delay_ms: 800,
    tab_popover_show_delay_ms: 1000,
    tab_popover_follow_delay_ms: 120,
    remote_resize_delay_ms: 220,
  }),
});

class TestClassList {
  constructor() {
    this.names = new Set();
  }

  add(...names) {
    names.forEach(name => this.names.add(name));
  }

  remove(...names) {
    names.forEach(name => this.names.delete(name));
  }

  toggle(name, force) {
    if (force === true) {
      this.names.add(name);
      return true;
    }
    if (force === false) {
      this.names.delete(name);
      return false;
    }
    if (this.names.has(name)) {
      this.names.delete(name);
      return false;
    }
    this.names.add(name);
    return true;
  }

  contains(name) {
    return this.names.has(name);
  }
}

class TestStyle {
  constructor() {
    this.properties = new Map();
  }

  setProperty(name, value) {
    this.properties.set(name, value);
  }

  removeProperty(name) {
    this.properties.delete(name);
  }

  getPropertyValue(name) {
    return this.properties.get(name) || '';
  }
}

class TestWebSocket {
  constructor(url) {
    this.url = String(url || '');
    this.readyState = TestWebSocket.OPEN;
    this.sent = [];
    TestWebSocket.instances.push(this);
    setImmediate(() => this.onopen?.({target: this}));
  }

  send(message) {
    this.sent.push(message);
  }

  close() {
    this.readyState = TestWebSocket.CLOSED;
    this.onclose?.({target: this});
  }
}
TestWebSocket.instances = [];
TestWebSocket.OPEN = 1;
TestWebSocket.CLOSING = 2;
TestWebSocket.CLOSED = 3;

function testDatasetKeyForAttribute(name) {
  return String(name || '').replace(/-([a-z])/g, (_match, char) => char.toUpperCase());
}

function testHtmlAttributeValue(attrs, name) {
  const match = String(attrs || '').match(new RegExp(`\\b${name}="([^"]*)"`));
  return match ? match[1] : '';
}

function populateTestDatasetFromHtmlAttrs(node, attrs) {
  String(attrs || '').replace(/\bdata-([A-Za-z0-9_-]+)="([^"]*)"/g, (_match, name, value) => {
    node.dataset[testDatasetKeyForAttribute(name)] = value;
    return '';
  });
}

function testElementFromTmuxWindowBarHtml(html) {
  const source = String(html || '');
  const barMatch = source.match(/^<div\b([^>]*)>([\s\S]*)<\/div>$/);
  if (!barMatch || !/\btmux-window-bar\b/.test(barMatch[1])) return null;
  const [, barAttrs, body] = barMatch;
  const bar = new TestElement('', 'div');
  bar.className = testHtmlAttributeValue(barAttrs, 'class') || 'tmux-window-bar';
  populateTestDatasetFromHtmlAttrs(bar, barAttrs);
  bar._innerHTML = body;
  for (const buttonMatch of body.matchAll(/<button\b([^>]*)>/g)) {
    const attrs = buttonMatch[1] || '';
    const button = new TestElement('', 'button');
    button.className = testHtmlAttributeValue(attrs, 'class');
    populateTestDatasetFromHtmlAttrs(button, attrs);
    const pressed = testHtmlAttributeValue(attrs, 'aria-pressed');
    if (pressed) button.setAttribute('aria-pressed', pressed);
    bar.appendChild(button);
  }
  return bar;
}

class TestElement {
  constructor(id = '', tagName = 'div') {
    this.id = id;
    this.localName = tagName;
    this.tagName = String(tagName || 'div').toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this._innerHTML = '';
    this.textContent = '';
    this.removed = false;
    this.parentElement = null;
    this.scrollTop = 0;
    this.clientHeight = 200;
    this.isConnected = true;
    this.rect = {width: 1200, height: 800, left: 0, top: 0, right: 1200, bottom: 800};
    this.style = new TestStyle();
    this.classList = new TestClassList();
    this.listeners = new Map();
  }

  get className() { return Array.from(this.classList.names).join(' '); }
  set className(value) {
    this.classList = new TestClassList();
    String(value || '').split(/\s+/).filter(Boolean).forEach(name => this.classList.add(name));
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value || '');
    this.children.forEach(node => {
      node.parentElement = null;
    });
    this.children = [];
    const tmuxWindowBar = testElementFromTmuxWindowBarHtml(this._innerHTML);
    if (tmuxWindowBar) this.appendChild(tmuxWindowBar);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  removeEventListener(type, listener) {
    const items = this.listeners.get(type) || [];
    this.listeners.set(type, items.filter(item => item !== listener));
  }
  append(...nodes) { nodes.forEach(node => this.appendChild(node)); }
  prepend(...nodes) {
    for (const node of nodes.reverse()) {
      this.insertBefore(node, this.children[0] || null);
    }
  }
  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
  insertBefore(node, before) {
    const existingIndex = this.children.indexOf(node);
    if (existingIndex >= 0) this.children.splice(existingIndex, 1);
    const index = before ? this.children.indexOf(before) : -1;
    node.parentElement = this;
    if (index >= 0) this.children.splice(index, 0, node);
    else this.children.push(node);
    return node;
  }
  insertAdjacentElement(position, node) {
    if (position !== 'afterend' || !this.parentElement) return this.appendChild(node);
    const siblings = this.parentElement.children;
    const existingIndex = siblings.indexOf(node);
    if (existingIndex >= 0) siblings.splice(existingIndex, 1);
    const index = siblings.indexOf(this);
    node.parentElement = this.parentElement;
    siblings.splice(index + 1, 0, node);
    return node;
  }
  after(...nodes) {
    if (!this.parentElement) return;
    let before = this.nextElementSibling;
    for (const node of nodes) {
      this.parentElement.insertBefore(node, before || null);
      before = node.nextElementSibling;
    }
  }
  get lastElementChild() {
    return this.children[this.children.length - 1] || null;
  }
  get firstElementChild() {
    return this.children[0] || null;
  }
  get nextElementSibling() {
    const siblings = this.parentElement?.children || [];
    const index = siblings.indexOf(this);
    return index >= 0 ? siblings[index + 1] || null : null;
  }
  cloneNode() {
    const clone = new TestElement(`${this.id}-clone`, this.localName);
    clone.dataset = {...this.dataset};
    clone.attributes = {...this.attributes};
    clone.innerHTML = this.innerHTML;
    clone.textContent = this.textContent;
    clone.rect = {...this.rect};
    return clone;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some(child => child?.contains?.(node));
  }
  getBoundingClientRect() { return this.rect; }
  insertAdjacentHTML() {}
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    if (selector === String(this.localName || '').toLowerCase()) return true;
    if (selector === ':hover') return this.hovered === true;
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    const dataPaneTabMatch = selector.match(/^\.pane-tab\[data-pane-tab="([^"]+)"\]$/);
    if (dataPaneTabMatch) {
      return this.classList.contains('pane-tab') && this.dataset.paneTab === dataPaneTabMatch[1];
    }
    if (selector === '[data-window-dir]') return this.dataset.windowDir !== undefined;
    if (selector === '[data-window-index]') return this.dataset.windowIndex !== undefined;
    if (selector === '[data-detail-toggle]') return this.dataset.detailToggle !== undefined;
    const dataDetailToggleMatch = selector.match(/^\[data-detail-toggle="([^"]+)"\]$/);
    if (dataDetailToggleMatch) return this.dataset.detailToggle === dataDetailToggleMatch[1];
    const classDataMatch = selector.match(/^\.([A-Za-z0-9_-]+)\[data-([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (classDataMatch) {
      const [, className, attrName, attrValue] = classDataMatch;
      const key = testDatasetKeyForAttribute(attrName);
      return this.classList.contains(className) && this.dataset[key] !== undefined && (attrValue === undefined || this.dataset[key] === attrValue);
    }
    const dataMatch = selector.match(/^\[data-([A-Za-z0-9_-]+)(?:="([^"]*)")?\]$/);
    if (dataMatch) {
      const [, attrName, attrValue] = dataMatch;
      const key = testDatasetKeyForAttribute(attrName);
      return this.dataset[key] !== undefined && (attrValue === undefined || this.dataset[key] === attrValue);
    }
    if (selector === 'textarea[data-setting-path]') return this.localName === 'textarea' && this.dataset.settingPath !== undefined;
    if (selector === 'input[type="text"][data-setting-path]') return this.localName === 'input' && this.attributes.type === 'text' && this.dataset.settingPath !== undefined;
    if (selector === '[role="tree"]') return this.attributes.role === 'tree';
    if (selector === '.file-explorer-tree-panel') return this.classList.contains('file-explorer-tree-panel');
    if (selector === '.file-tree-row[data-path]') return this.classList.contains('file-tree-row') && Boolean(this.dataset.path);
    const fileTreePathMatch = selector.match(/^\.file-tree-row\[data-path="([^"]+)"\]$/);
    if (fileTreePathMatch) return this.classList.contains('file-tree-row') && this.dataset.path === fileTreePathMatch[1];
    if (/^\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$/.test(selector)) {
      return selector.slice(1).split('.').every(className => this.classList.contains(className));
    }
    if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
    return false;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (node.matches(selector)) return node;
      node = node.parentElement;
    }
    return null;
  }
  querySelector(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      if (className.includes('[')) {
        return this.children.find(child => child.matches(`.${className}`)) || null;
      }
      return this.children.find(child => child.classList?.contains(className)) || null;
    }
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    if (selector.startsWith(':scope > .')) {
      const className = selector.slice(':scope > .'.length);
      const scopedSelector = className.includes('[') ? `.${className}` : null;
      return this.children.filter(child => (
        scopedSelector ? child.matches(scopedSelector) : child.classList?.contains(className)
      ));
    }
    const matches = [];
    const visit = node => {
      for (const child of node.children || []) {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }
  remove() {
    this.removed = true;
    if (this.parentElement) {
      const index = this.parentElement.children.indexOf(this);
      if (index >= 0) this.parentElement.children.splice(index, 1);
    }
    this.parentElement = null;
  }
  scrollIntoView() {}
  getAttribute(name) { return this.attributes[name]; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name); }
  removeAttribute(name) { delete this.attributes[name]; }
  replaceChildren(...nodes) {
    this.children.forEach(node => {
      node.parentElement = null;
    });
    this.children = [];
    nodes.forEach(node => this.appendChild(node));
  }
  replaceWith(node) {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    const index = siblings.indexOf(this);
    if (index < 0) return;
    if (node.parentElement) {
      const previousSiblings = node.parentElement.children;
      const previousIndex = previousSiblings.indexOf(node);
      if (previousIndex >= 0) previousSiblings.splice(previousIndex, 1);
    }
    node.parentElement = this.parentElement;
    siblings.splice(index, 1, node);
    this.parentElement = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

class TestFile {
  constructor(parts, name, options = {}) {
    this.parts = parts;
    this.name = name;
    this.type = options.type || '';
    this.size = parts.reduce((total, part) => total + (Number(part?.size) || String(part || '').length), 0);
  }
}

class TestFormData {
  constructor() {
    this.fields = [];
  }

  append(name, value, filename = undefined) {
    this.fields.push({name, value, filename});
  }
}

class TestEventSource {
  constructor(url) {
    this.url = String(url || '');
    this.readyState = 1;
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  removeEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    this.listeners.set(type, listeners.filter(item => item !== listener));
  }

  close() {
    this.readyState = 2;
  }
}

function assertNoStandalonePrBadge(html, label) {
  assert.equal(html.includes('>PR<'), false, `${label} avoids redundant PR text`);
  assert.equal(/class="[^"]*pr-indicator[^"]*"[^>]*>PR</.test(html), false, `${label} never renders a standalone PR text pill`);
  assert.equal(html.includes('pr-indicator'), false, `${label} suppresses the separate PR number badge`);
}

function assertSingleCiBadge(html, label) {
  assert.ok(html.includes('>CI</span>'), `${label} renders a CI badge`);
  assert.equal((html.match(/>CI<\/span>/g) || []).length, 1, `${label} renders exactly one CI badge`);
}

function loadYolomux(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64', accessRole = 'admin', options = {}) {
  const source = fs.readFileSync('static/yolomux.js', 'utf8');
  const bootStart = source.indexOf("if (refreshMeta) {");
  assert.ok(bootStart > 0, 'could not find browser boot section');
  const bootstrapOverrides = options.bootstrapOverrides || Object.fromEntries(Object.entries(options).filter(([key]) => !['sessionStorage', 'localStorage', 'fireAllTimeouts'].includes(key)));
  const fireAllTimeouts = options.fireAllTimeouts === true;

  const bootstrapPayload = {
    sessions,
    availableAgents: [],
    accessRole,
    homePath: '/home/test',
    repoRoot: '/home/test/yolomux.dev',
    maxSessionTabs: 99,
    serverHostname: 'test-host',
    localeRegistry: {
      fallback: 'en',
      pseudo: 'en-XA',
      systemPreference: 'system',
      systemLocale: 'en',
      locales: [
        ['en', 'English', 'ltr'], ['zh-Hant', '繁體中文', 'ltr'], ['zh-Hans', '简体中文', 'ltr'], ['ja', '日本語', 'ltr'], ['ko', '한국어', 'ltr'], ['es', 'Español', 'ltr'], ['de', 'Deutsch', 'ltr'], ['fr', 'Français', 'ltr'], ['it', 'Italiano', 'ltr'], ['pt-BR', 'Português (BR)', 'ltr'], ['pl', 'Polski', 'ltr'], ['nl', 'Nederlands', 'ltr'], ['he', 'עברית', 'rtl'], ['ar', 'العربية', 'rtl'], ['ru', 'Русский', 'ltr'], ['hi', 'हिन्दी', 'ltr'], ['vi', 'Tiếng Việt', 'ltr'], ['th', 'ไทย', 'ltr'], ['tr', 'Türkçe', 'ltr'],
      ].map(([id, endonym, direction]) => ({id, endonym, direction})),
    },
    // Seed the en catalog the way production inlines bootstrap.strings, so localized labels (the brand
    // tab labels infoTabLabel()/yoagentTabLabel() etc.) resolve synchronously at first render under en.
    strings: {en: JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8'))},
    settingsPayload: {defaults: DEFAULT_TEST_SETTINGS, settings: {}, mtime_ns: 1},
    ...bootstrapOverrides,
  };
  const bootstrap = JSON.stringify(bootstrapPayload);
  const elements = new Map();
  const documentListeners = new Map();
  const windowListeners = new Map();
  const storage = new Map(Object.entries(options.localStorage || {}).map(([key, value]) => [String(key), String(value)]));
  const sessionStorageMap = new Map(Object.entries(options.sessionStorage || {}).map(([key, value]) => [String(key), String(value)]));
  const localStorage = {
    getItem(key) { return storage.has(String(key)) ? storage.get(String(key)) : null; },
    setItem(key, value) { storage.set(String(key), String(value)); },
    removeItem(key) { storage.delete(String(key)); },
  };
  const sessionStorage = {
    getItem(key) { return sessionStorageMap.has(String(key)) ? sessionStorageMap.get(String(key)) : null; },
    setItem(key, value) { sessionStorageMap.set(String(key), String(value)); },
    removeItem(key) { sessionStorageMap.delete(String(key)); },
  };
  const location = {
    search,
    pathname: '/',
    hash: '',
    protocol,
    hostname: 'localhost',
    port: '7777',
    host: 'localhost:7777',
    reload() { context.__reloadCount = (context.__reloadCount || 0) + 1; },
  };
  const testSetTimeout = options.setTimeout || ((callback, ms) => {
    if ((fireAllTimeouts || ms === 8) && typeof callback === 'function') return setImmediate(callback);
    return 0;
  });
  const testClearTimeout = options.clearTimeout || (() => {});
  const testSetInterval = options.setInterval || (() => {});
  const testClearInterval = options.clearInterval || (() => {});
  const notification = {permission: 'denied'};
  const element = id => {
    if (!elements.has(id)) elements.set(id, new TestElement(id));
    const node = elements.get(id);
    if (id === 'yolomux-bootstrap') node.textContent = bootstrap;
    return node;
  };
  const context = {
    console,
    EventSource: TestEventSource,
    File: TestFile,
    FormData: TestFormData,
    URLSearchParams,
    WebSocket: TestWebSocket,
    __testWebSocketInstances: TestWebSocket.instances,
    AbortController,
    clearInterval: testClearInterval,
    clearTimeout: testClearTimeout,
    document: {
      __listeners: documentListeners,
      addEventListener(type, listener) {
        if (!documentListeners.has(type)) documentListeners.set(type, []);
        documentListeners.get(type).push(listener);
      },
      body: element('body'),
      createElement: tag => new TestElement('', tag),
      documentElement: element('html'),
      execCommand(command) {
        if (String(command || '').toLowerCase() !== 'copy') return false;
        let prevented = false;
        const event = {
          clipboardData: {
            setData(type, value) {
              if (type === 'text/plain') context.__clipboardText = String(value);
            },
          },
          preventDefault() { prevented = true; },
          stopPropagation() {},
          stopImmediatePropagation() {},
        };
        for (const listener of [...(documentListeners.get('copy') || [])]) listener(event);
        return prevented;
      },
      getElementById: element,
      querySelector: () => null,
      querySelectorAll: () => [],
      removeEventListener(type, listener) {
        const listeners = documentListeners.get(type) || [];
        documentListeners.set(type, listeners.filter(item => item !== listener));
      },
      activeElement: null,
    },
    fetch() { return Promise.reject(new Error('fetch disabled in layout URL tests')); },
    getComputedStyle: () => ({direction: 'ltr'}),
    history: {
      replaceState(_state, _title, url) {
        context.__lastUrl = url;
      },
    },
    location,
    navigator: {
      platform: navigatorPlatform,
      userAgent: navigatorPlatform,
      clipboard: {
        writeText(text) {
          context.__clipboardText = String(text ?? '');
          return Promise.resolve();
        },
      },
    },
    Notification: notification,
    performance: {now: () => 0},
    requestAnimationFrame(callback) { return callback(); },
    setInterval: testSetInterval,
    // The bundle schedules the batched /api/fs/batch directory-listing flush via
    // setTimeout(flushFileExplorerFsBatch, fileExplorerFsBatchDelayMs) — and 8ms is UNIQUE to that flush
    // (verified: no other bundle timer uses an 8ms delay). All other bundle timers (polls, debounces,
    // share publishers) must stay no-ops here. So fire ONLY the 8ms flush, on a real setImmediate, so any
    // code that `await`s a directory listing settles instead of hanging forever (which used to leave the
    // trailing suite IIFE unsettled and silently exit 0 — see the suite watchdog + AGENTS.md note).
    // Synchronous back-to-back enqueues still batch into one request (no await between them = no yield),
    // so batching/coalescing tests are unaffected.
    setTimeout: testSetTimeout,
    window: {
      __listeners: windowListeners,
      Notification: notification,
      addEventListener(type, listener) {
        if (!windowListeners.has(type)) windowListeners.set(type, []);
        windowListeners.get(type).push(listener);
      },
      clearTimeout: testClearTimeout,
      confirm: () => true,
      EventSource: TestEventSource,
      innerHeight: 800,
      innerWidth: 1200,
      location,
      localStorage,
      open(url, name, features) {
        const record = {
          url: String(url || ''),
          name: String(name || ''),
          features: String(features || ''),
        };
        const documentStub = {
          readyState: 'complete',
          body: element(`popout-body-${context.__openedWindows.length}`),
          title: '',
          open() { this.html = ''; },
          write(html) { this.html = String(html || ''); },
          close() {},
          querySelector() { return null; },
        };
        const popoutWindow = {
          closed: false,
          document: documentStub,
          location: {pathname: record.url.split('?')[0] || ''},
          addEventListener() {},
          close() { this.closed = true; },
          focus() { record.focused = true; },
        };
        record.window = popoutWindow;
        context.__openedWindows.push(record);
        return popoutWindow;
      },
      sessionStorage,
      removeEventListener(type, listener) {
        const listeners = windowListeners.get(type) || [];
        windowListeners.set(type, listeners.filter(item => item !== listener));
      },
      setTimeout: testSetTimeout,
    },
    localStorage,
    sessionStorage,
    __clipboardText: '',
    // the OSC 52 clipboard bridge decodes base64 UTF-8; expose the host implementations.
    atob,
    btoa,
    TextDecoder,
    Uint8Array,
  };
  context.globalThis = context;
  context.__openedWindows = [];
  vm.createContext(context);
  vm.runInContext(`${source.slice(0, bootStart)}
globalThis.__layoutTestApi = {
  activeItemForSide,
  agentErrorIsBlocking,
  appModifier,
  appMenuTree,
  appShortcutText,
  aboutBrandHtml,
  showAboutModal,
  startupHelperCatalog,
  readStartupHelperIndex,
  writeStartupHelperIndex,
  showStartupHelperTip,
  t,
  tPlural,
  yoagentInlineMarkdown,
  yoagentTightMarkdown,
  joinAndNormalize,
  i18nNormalizeLocale,
  resolveLocalePref,
  i18nLocaleChoices,
  i18nIsRtl,
  relativeTimeFormat,
  compactRelativeTimeFormat,
  sessionFileLookbackDefaultHoursForTest: sessionFileLookbackDefaultHours,
  sessionFileLookbackHourValuesForTest: sessionFileLookbackHourValues,
  sessionFileLookbackLabelForTest: sessionFileLookbackLabel,
  sessionFileLookbackOptionsForTest: sessionFileLookbackOptions,
  normalizeSessionFileLookbackHoursForTest: normalizeSessionFileLookbackHours,
  backgroundOwnerSearchIndexSummaryForTest: backgroundOwnerSearchIndexSummary,
  backgroundOwnerStatsSummaryForTest: backgroundOwnerStatsSummary,
  backgroundOwnerSessionFilesSummaryForTest: backgroundOwnerSessionFilesSummary,
  backgroundOwnerOwnsAllRolesForTest: backgroundOwnerOwnsAllRoles,
  backgroundOwnerCurrentOwnerLiveForTest: backgroundOwnerCurrentOwnerLive,
  topbarOwnerStatusHtmlForTest: topbarOwnerStatusHtml,
  topbarOwnerStatusTitleForTest: topbarOwnerStatusTitle,
  createTopbarOwnerStatusForTest: createTopbarOwnerStatus,
  showBackgroundOwnerContextMenuForTest: showBackgroundOwnerContextMenu,
  setBackgroundOwnerStatusPayloadForTest(payload) {
    backgroundOwnerStatusPayload = payload;
    backgroundOwnerStatusLoaded = Boolean(payload);
    backgroundOwnerStatusLoading = false;
    backgroundOwnerStatusError = '';
  },
  i18nActiveLocaleId,
  i18nSetCatalogForTest,
  transcriptItemHtmlForTest: transcriptItemHtml,
  transcriptAgentErrorTextForTest: transcriptAgentErrorText,
  transcriptContextLoadErrorTextForTest: transcriptContextLoadErrorText,
  transcriptMetadataLoadErrorTextForTest: transcriptMetadataLoadErrorText,
  setTranscriptMetadataLoadErrorForTest(value) { transcriptMetaLoadError = value; },
  repoComparisonErrorHtmlForTest: repoComparisonErrorHtml,
  setActiveLocaleForTest(locale) { i18nActiveLocale = locale; },
  updateNotificationAllowsVersionForTest: updateNotificationAllowsVersion,
  normalizeUpdateNotificationLevelForTest: normalizeUpdateNotificationLevel,
  createAppMenuCommand,
  backgroundTabItems,
  canPaneExpand,
  codeMirrorHtmlSemanticEmphasisExtension,
  codeMirrorApiIsUsable,
  codeMirrorLanguageExtension,
  codeMirrorHighlightExtension,
  codeMirrorMarkdownCodeLanguages,
  createEditableCodeMirrorState,
  codeMirrorPlainEditableExtensions,
  normalizeFileEditorSaveContentForTest: normalizeFileEditorSaveContent,
  fileEditorTextMetricsForTest: fileEditorTextMetrics,
  fileEditorCountStatusTextForTest: fileEditorCountStatusText,
  fileEditorImageVersionForTest: fileEditorImageVersion,
  updateCodeMirrorCursorStatusForTest: updateCodeMirrorCursorStatus,
  codeMirrorSearchMatches,
  codeMirrorSearchMatchSummary,
  codeMirrorPhraseValues,
  emptyPlaceholderPaneState,
  emptyLayoutSlots,
  editorNav,
  editorNavBackForTest: editorNavBack,
  recordEditorNav,
  cursorStyleFileReference,
  fileEditorPaneTabHtml,
  fileQuickOpenItem,
  fileQuickOpenItems,
  movingEllipsisHtml,
  stripTrailingEllipsisText,
  fileQuickOpenExtraRootsForSearchQuery,
  fileQuickOpenRootMatchesPathAlias,
  fileQuickOpenRootForFile,
  fileQuickOpenRootForSearch,
  fileQuickOpenRootsForSearch,
  fileQuickOpenTargetSlot,
  fileQuickOpenSearchText,
  fileQuickOpenScopeLabel,
  fileIndexStatusFromPayloadForTest: fileIndexStatusFromPayload,
  fileExplorerDirectoryIsIndexed,
  fileExplorerIndexBadgeText,
  gitStatusRowClass,
  fileEditorGitActionControlsVisible,
  fileStateCanRenderDiffView,
  diffModeShouldFallBackToEdit,
  setFileExplorerIndexedDirsForTest(paths) { setFileExplorerIndexedDirs(paths); },
  setFileExplorerIndexStatusForTest(root, status) { fileExplorerIndexStatus.set(normalizeStoredFileExplorerIndexedDir(root), status); },
  createTopbarSearch,
  createTopbarNav,
  normalizeWatchedPrRef,
  watchedPrStatusSnapshot,
  watchedPrTransitionKeys,
  shouldNotifyTransitionKey,
  codeMirrorDiffLayout,
  buildDiffOverviewGradientForTest: buildDiffOverviewGradient,
  buildDiffOverviewGradientFromBandsForTest: buildDiffOverviewGradientFromBands,
  diffOverviewRowsFromCodeMirrorChunksForTest: diffOverviewRowsFromCodeMirrorChunks,
  diffOverviewRowsFromCodeMirrorRenderedWeightsForTest: diffOverviewRowsFromCodeMirrorRenderedWeights,
  diffOverviewScrollLooksCurrentOnlyForTest: diffOverviewScrollLooksCurrentOnly,
  diffOverviewCodeMirrorChunksForTest: diffOverviewCodeMirrorChunks,
  diffOverviewRemovedLineCountForTest: diffOverviewRemovedLineCount,
  updateCodeMirrorDiffOverviewForTest: updateCodeMirrorDiffOverview,
  setDiffExpandUnchangedForTest(value) { diffExpandUnchanged = value === true; },
  fileExplorerChangesPanelHtml,
  bindChangesPanelForTest: bindChangesPanel,
  fileTreeExpandCollapseAllButtonsHtml,
  fileExplorerDirectoryPathsForRootForTest: fileExplorerDirectoryPathsForRoot,
  setAllFileTreeDirectoriesExpandedForTest: setAllFileTreeDirectoriesExpanded,
  fileExplorerSessionFilesTargetSessionForTest: fileExplorerSessionFilesTargetSession,
  sessionFilesCacheKeyForTest: sessionFilesCacheKey,
  noteFileExplorerChangesSessionInteractionForTest: noteFileExplorerChangesSessionInteraction,
  setFileExplorerChangesSelectedSessionForTest(value) { fileExplorerChangesSelectedSession = String(value || ''); },
  changesGroupsSnapshotHtmlForTest: changesGroupsSnapshotHtml,
  fetchSessionFilesForTest: fetchSessionFiles,
  fileExplorerChangesCollapseToggleHtml,
  fileExplorerChangesAllReposCollapsedForTest: fileExplorerChangesAllReposCollapsed,
  toggleAllFileExplorerChangesForTest: toggleAllFileExplorerChanges,
  projectMetaHtml,
  paneInfoBarMetaHtml,
  cycleSessionRepoDisplayForTest: cycleSessionRepoDisplay,
  diffRefControlsHtml,
  diffRefResetButtonHtml,
  diffRefSelectOptionsHtml,
  diffRefPopoverItems,
  diffRefCompactDisplayForTest: diffRefCompactDisplay,
  diffRefPopoverSubjectPartsForTest: diffRefPopoverSubjectParts,
  diffRefParams,
  diffRefFromSuggestions,
  diffRefToSuggestions,
  fileDiffRefHistoryItems,
  fileRepoForPath,
  setDiffRefsByRepoForTest(repo, refs) {
    if (!repo) return;
    if (!refs) delete diffRefsByRepo[repo];
    else diffRefsByRepo[repo] = {from: refs.from, to: refs.to};
  },
  globalActivitySummaryHtml,
  yoagentChatHtml,
  yoagentResolvedBackendForTest: yoagentResolvedBackend,
  yoagentAvailableBackendOptionsForTest: yoagentAvailableBackendOptions,
  yoagentChatEnabledForTest: yoagentChatEnabled,
  setYoagentDraftForTest(value) { yoagentDraft = String(value || ''); },
  setYoagentBusyForTest(value) { yoagentBusy = Boolean(value); },
  setYoagentActiveChatRequestForTest(value) { yoagentActiveChatRequest = value; },
  yoagentActiveChatRequestForTest() { return yoagentActiveChatRequest; },
  yoagentChatQueueForTest() { return yoagentChatQueue.slice(); },
  cancelQueuedYoagentChatMessageForTest: cancelQueuedYoagentChatMessage,
  cancelActiveYoagentChatRequestForTest: cancelActiveYoagentChatRequest,
  sendYoagentChatMessageForTest: sendYoagentChatMessage,
  setYoagentErrorForTest(value) { yoagentError = value && typeof value === 'object' ? value : String(value || ''); },
  setYoagentNoticeForTest(value) { yoagentNotice = value; },
  setYoagentMessagesForTest(value) { yoagentMessages = Array.isArray(value) ? value : []; resetYoagentComposerHistory(); },
  applyYoagentStreamPayloadForTest: applyYoagentStreamPayload,
  refreshActivitySummaryForTest: refreshActivitySummary,
  showYoagentStartupInfoOnceForTest: showYoagentStartupInfoOnce,
  showYoagentStartupInfoForLatestActivityForTest: showYoagentStartupInfoForLatestActivity,
  hideYoagentStartupInfoForTest: hideYoagentStartupInfo,
  applyActivitySummaryPayloadFromPushForTest: applyActivitySummaryPayloadFromPush,
  yoagentOpenMessageDetailsStateForTest: yoagentOpenMessageDetailsState,
  restoreYoagentOpenMessageDetailsStateForTest: restoreYoagentOpenMessageDetailsState,
  yoagentUserMessageHistoryForTest: yoagentUserMessageHistory,
  yoagentNavigateChatHistoryForTest: yoagentNavigateChatHistory,
  resetYoagentComposerHistoryForTest: resetYoagentComposerHistory,
  applyYoagentConversationPayloadForTest: applyYoagentConversationPayload,
  applyYoagentJobsPayloadForTest: applyYoagentJobsPayload,
  yoagentJobsHtmlForTest: yoagentJobsHtml,
  loadYoagentJobsForTest: loadYoagentJobs,
  confirmYoagentJobForTest: confirmYoagentJob,
  cancelYoagentJobForTest: cancelYoagentJob,
  clearYoagentPendingWaitForTest: clearYoagentPendingWait,
  sessionActivitySummary,
  fitAppMenuPopover,
  finderDirectoryForItem,
  finderTargetPathForItem,
  activeFinderDirectoryPath,
  activeFinderTargetPath,
  activeTmuxDirectoryPath,
  commonAncestorPath,
  cancelPendingFileExplorerActiveSync,
  fileExplorerSyncPlanForTest: fileExplorerSyncPlan,
  fileExplorerSessionHighlightClassForPath,
  fileExplorerSessionHighlightClassForTest(path, kind = 'dir', preferredItem = null) {
    return fileExplorerSessionHighlightClassForPath(path, kind, {sessionHighlightSets: fileExplorerSessionHighlightSets(preferredItem)});
  },
  fileExplorerSessionHighlightSetsForTest(preferredItem = null) {
    const sets = fileExplorerSessionHighlightSets(preferredItem);
    return {repoRoots: [...sets.repoRoots], touchedDirs: [...sets.touchedDirs], expandedDirs: [...sets.expandedDirs]};
  },
  fileExplorerRootForOpen,
  fileExplorerRootModeValue,
  setFileExplorerRootMode,
  scheduleFileExplorerActiveTabSyncForTest: scheduleFileExplorerActiveTabSync,
  shareReadOnlyFinderStateIsHostOwnedForTest: shareReadOnlyFinderStateIsHostOwned,
  fileExplorerLabel,
  fileExplorerPanelCloseClass,
  fileEditorPanelCloseClass,
  fileIconFor,
  fileIconClassFor,
  fileExplorerNeedsLeftDock,
  openFileExplorerAtForTest: openFileExplorerAt,
  visibleFileEditorWatchFilesForTest: visibleFileEditorWatchFiles,
  backgroundFileEditorWatchFilesForTest: backgroundFileEditorWatchFiles,
  clientServerWatchStateForTest: clientServerWatchState,
  clientPushCanSupplyDataForTest: clientPushCanSupplyData,
  readOnlyModeForTest() { return readOnlyMode; },
  setClientEventsSourceForTest(value = {}) { clientEventsSource = value; },
  syncServerWatchRootsForTest: syncServerWatchRoots,
  fileExplorerPaneTabHtml,
  fetchDirectoryForTest: fetchDirectory,
  fileExplorerEntriesByWatchedDirectoryForTest: fileExplorerEntriesByWatchedDirectory,
  refreshFileExplorerFromPushForTest: refreshFileExplorerFromPush,
  refreshWatchedFilesystemForTest: refreshWatchedFilesystem,
  filesystemWatchTokenForTest() { return fileExplorerFilesystemWatchToken; },
  setFilesystemWatchTokenForTest(value) { fileExplorerFilesystemWatchToken = String(value || ''); },
  setFilesystemLastFullAtForTest(value) { fileExplorerFilesystemLastFullAt = Number(value) || 0; },
  currentFileExplorerListErrorForTest: currentFileExplorerListError,
  setFileExplorerPushRefreshDepthForTest(value) { fileExplorerPushRefreshDepth = Math.max(0, Number(value) || 0); },
  setFileExplorerLastListErrorForTest(path, error = 'failed') { setFileExplorerListError(path, error, 500); },
  fetchFilePathInfoForTest: fetchFilePathInfo,
  deleteFileTreePathForTest: deleteFileTreePath,
  flushFileExplorerFsBatchForTest: flushFileExplorerFsBatch,
  firstEmptyPane,
  filePopoverRows,
  fuzzySearchScore,
  fuzzyHighlightHtml,
  fuzzySubsequenceMatch,
  fuzzySubsequenceScore,
  lineDiffRows,
  fileConflictCompareHtml,
  childPathParts,
  debugModeEnabledForTest() { return debugModeEnabled; },
  debugModeExplicitUrlEnabledForTest() { return debugModeExplicitUrlEnabled; },
  debugPaneItemId,
  debugPanelHtmlForTest: debugPanelHtml,
  debugGraphMetaHtmlForTest: debugGraphMetaHtml,
  debugGraphBucketSummaryForTest: debugGraphBucketSummary,
  debugGraphAgentTokenDisplayBucketsForTest: debugGraphAgentTokenDisplayBuckets,
  debugGraphApplyServerHistoryForTest: debugGraphApplyServerHistory,
  debugGraphMovingAverageValuesForTest: debugGraphMovingAverageValues,
  debugGraphSeriesDataForTest: nowMs => debugGraphSeriesData(debugGraphDisplayBuckets(nowMs)),
  jsDebugStatsPanelVisibleForTest: jsDebugStatsPanelVisible,
  jsDebugStatsPollingStateForTest() { return {firstSampleReceived: jsDebugStatsFirstSampleReceived, inFlight: jsDebugStatsPollInFlight}; },
  startJsDebugStatsPollingForTest: startJsDebugStatsPolling,
  syncJsDebugStatsPollingForTest: syncJsDebugStatsPolling,
  stopJsDebugStatsPollingForTest: stopJsDebugStatsPolling,
  flushJsDebugStatsHistoryForTest: flushJsDebugStatsHistory,
  pollJsDebugStatsSampleForTest: pollJsDebugStatsSample,
  jsDebugStatsClientIdForRequestForTest: jsDebugStatsClientIdForRequest,
  recordJsDebugClientEventsConnectionStateForTest: recordJsDebugClientEventsConnectionState,
  recordJsDebugDisconnectedSpanForTest: recordJsDebugDisconnectedSpan,
  recordJsDebugStatsSampleForTest: recordJsDebugStatsSample,
  bindDebugPanelForTest: bindDebugPanel,
  setDebugGraphScaleForTest: setDebugGraphScale,
  setDebugGraphRangeForTest: setDebugGraphRange,
  clearDebugGraphZoomForTest: clearDebugGraphZoom,
  clearJsDebugEventsForTest: clearJsDebugEvents,
  jsDebugEventsForTest() { return jsDebugEvents.map(event => ({...event})); },
  terminalRemovalLatencySummaryForTest: terminalRemovalLatencySummary,
  jsDebugTextForClipboardForTest: jsDebugTextForClipboard,
  recordSseDebugEventForTest: recordSseDebugEvent,
  recordClientPerfCounterForTest: recordClientPerfCounter,
  clientPerfSummaryForTest: clientPerfSummary,
  clearClientPerfCountersForTest: clearClientPerfCounters,
  shareDebugTextForClipboardForTest: shareDebugTextForClipboard,
  shareDebugProfileUploadPayloadForTest: shareDebugProfileUploadPayload,
  recordJsDebugEventForTest: recordJsDebugEvent,
  inactiveTabItems,
  infoItemId,
  infoRelationshipRecords,
  infoGroupTree,
  infoDimensionCountTextForTest: infoDimensionCountText,
  infoGroupDimensions,
  infoGroupDimensionsForLevel,
  infoGroupingControlsHtmlForTest: infoGroupingControlsHtml,
  infoRecordHtmlForTest: infoRecordHtml,
  infoTreeHtmlForTest: infoTreeHtml,
  infoTreeGroupCollapseKeyForTest: infoTreeGroupCollapseKey,
  setInfoTreeGroupCollapsedForTest: setInfoTreeGroupCollapsed,
  infoCollapsedGroupKeysForTest() { return [...infoCollapsedGroupKeys]; },
  infoFilteredRecordsForTest: infoFilteredRecords,
  infoRecordSearchFieldsForTest: infoRecordSearchFields,
  infoSortFields,
  infoGroupingPresetsForTest: infoGroupingPresets,
  currentInfoGroupingForTest: currentInfoGrouping,
  setInfoGroupingForTest: setInfoGrouping,
  setInfoGroupingPresetForTest: setInfoGroupingPreset,
  currentInfoSortForTest: currentInfoSort,
  setInfoSortForTest: setInfoSort,
  currentInfoSearchForTest: currentInfoSearch,
  setInfoSearchForTest: setInfoSearch,
  searchHistoryItemId,
  searchHistoryPanelHtmlForTest: searchHistoryPanelHtml,
  searchHistoryResultsHtmlForTest: searchHistoryResultsHtml,
  runHistoryRowsHtmlForTest: runHistoryRowsHtml,
  runSearchHistoryQueryForTest: runSearchHistoryQuery,
  refreshRunHistoryDataForTest: refreshRunHistoryData,
  setSearchHistoryStateForTest(query = '', searchPayload = {query: '', results: []}, historyPayload = {runs: []}) {
    searchHistoryQuery = String(query || '');
    searchHistoryPayload = searchPayload || {query: '', results: []};
    runHistoryPayload = historyPayload || {runs: []};
    searchHistoryLoading = false;
    runHistoryLoading = false;
    searchHistoryError = '';
    runHistoryError = '';
  },
  infoPanelSubTabForTest() { return infoPanelSubTab; },
  setInfoPanelSubTabForTest(value) { infoPanelSubTab = normalizedInfoSubTab(value); },
  infoSessionFileLookbackHoursForTest() { return infoSessionFileLookbackHours; },
  setInfoSessionFileLookbackHoursForTest: setInfoSessionFileLookbackHours,
  tabberLookbackControlHtmlForTest: tabberLookbackControlHtml,
  tabberSessionFileLookbackHoursForTest() { return tabberSessionFileLookbackHours; },
  setTabberSessionFileLookbackHoursForTest: setTabberSessionFileLookbackHours,
  tabMetaVisibleForTest() { return tabMetaVisible; },
  setTabMetaVisibleForTest(value) {
    tabMetaVisible = value === true;
    renderTabMetaToggle();
  },
  toggleTabMetadataForTest: toggleTabMetadata,
  itemInLayout,
  itemLabel,
  itemParam,
  resolveLayoutItem,
  itemIsBackgroundPaneTab,
  layoutFromParam,
  layoutParamValue,
  layoutSlotKeys,
  largestPaneSlotForFileEditor,
  layoutWithFileExplorerDockedLeft,
  layoutWithReplacedItem,
  layoutWithoutItem,
  layoutWithItems,
  applyLayoutMode,
  setLayoutToSinglePane,
  setLayoutToSplitPanes,
  setLayoutToGridPanes,
  setLayoutToWallPanes,
  layoutModeValues,
  layoutTabsParamValue,
  layoutTreeKey,
  leafNode,
  yoagentItemId,
  fileExplorerItemId,
  prefsItemId,
  menuTabCommand,
  activateTerminalDetailTabForTest: activateTab,
  activatePaneTab,
  paneTabTraversalPositionsForTest: paneTabTraversalPositions,
  adjacentPaneTabPosition,
  selectAdjacentPaneTab,
  currentSessionActionTarget,
  setFocusedPanelItem,
  setFocusedTerminal,
  clearFocusedTerminal,
  handleFocusedTerminalCopyShortcutForTest: handleFocusedTerminalCopyShortcut,
  visualActivePaneItemForTest: visualActivePaneItem,
  codeMirrorWrapMarkerRowsForBlock,
  lastActivePaneItemForTest() { return lastActivePaneItem; },
  focusedPanelItemForTest() { return focusedPanelItem; },
  setAutoFocusEnabledForTest(value) { autoFocusEnabled = Boolean(value); },
  autoFocusEnabledForTest() { return autoFocusEnabled; },
  selectSession,
  selectPanelOnHover,
  claimVisibleTerminalResizeAuthorityForTest: claimVisibleTerminalResizeAuthority,
  focusTerminalWhenAutoFocus,
  focusPanel,
  focusTerminalFromUserAction,
  handleTerminalDataForTest: handleTerminalData,
  toggleFileExplorerShortcut,
  clearFileExplorerShortcutRestoreSlotsForTest() { fileExplorerShortcutRestoreSlots = null; },
  focusedTerminalForTest() { return focusedTerminal; },
  globalShortcutTargetAllowsAppAction,
  globalShortcutTargetAllowsFinderShortcut,
  globalShortcutShouldToggleFinderForTest: globalShortcutShouldToggleFinder,
  installTerminalCopyShortcutForTest: installTerminalCopyShortcut,
  osc52ClipboardText,
  installTerminalOsc52BridgeForTest: installTerminalOsc52Bridge,
  rememberTerminalAppClipboardTextForTest: rememberTerminalAppClipboardText,
  terminalContextMenuSelectionForTest: terminalContextMenuSelection,
  terminalVisibleSelectionStateForTest: terminalVisibleSelectionState,
  clearTerminalVisibleSelectionForTest: clearTerminalVisibleSelection,
  apiFetchJsonQuietForTest: apiFetchJsonQuiet,
  setFetchForTest(fn) { globalThis.fetch = fn; },
  setClipboardForTest(clipboard, ClipboardItemType = class {}) {
    navigator.clipboard = clipboard;
    globalThis.ClipboardItem = ClipboardItemType;
  },
  setSaveBlobDownloadForTest(fn) { saveBlobDownload = fn; },
  setConfirmForTest(fn) { window.confirm = fn; },
  setShowToastForTest(fn) { showToast = fn; },
  copyImageFileToClipboardForTest: copyImageFileToClipboard,
  triggerFileDownloadForTest: triggerFileDownload,
  triggerFolderZipDownloadForTest: triggerFolderZipDownload,
  applyAndSaveGlobalThemeForTest: applyAndSaveGlobalTheme,
  reloadCountForTest() { return globalThis.__reloadCount || 0; },
  yoagentJobNotificationTitleForTest: yoagentJobNotificationTitle,
  yoagentJobNotificationBodyForTest: yoagentJobNotificationBody,
  applyUpdateAvailableForTest: applyUpdateAvailable,
  triggerSelfUpdateForTest: triggerSelfUpdate,
  maybeHandleServerVersionChangeForTest: maybeHandleServerVersionChange,
  startSelfUpdateReloadPollingForTest: startSelfUpdateReloadPolling,
  pollSelfUpdateReloadForTest: pollSelfUpdateReload,
  reloadIsSafeForTest: reloadIsSafe,
  selfUpdateReloadStateForTest() {
    return {
      pending: selfUpdateReloadPending,
      target: selfUpdateReloadTarget,
      attempts: selfUpdateReloadAttempts,
      deferredToastShown: selfUpdateReloadDeferredToastShown,
      serverVersionReloadHandled,
    };
  },
  clipboardTextForTest() { return globalThis.__clipboardText; },
  clearClipboardTextForTest() { globalThis.__clipboardText = ''; },
  setBrowserSelectionForTest(text, anchorNode = null, focusNode = anchorNode) {
    globalThis.__browserSelectionClearCount = 0;
    let value = String(text || '');
    const selection = {
      toString: () => value,
      anchorNode,
      focusNode,
      removeAllRanges() {
        value = '';
        globalThis.__browserSelectionClearCount += 1;
      },
    };
    globalThis.getSelection = () => selection;
    window.getSelection = globalThis.getSelection;
  },
  browserSelectionClearCountForTest() { return globalThis.__browserSelectionClearCount || 0; },
  clearBrowserSelectionForTest() {
    delete globalThis.getSelection;
    delete window.getSelection;
    globalThis.__browserSelectionClearCount = 0;
  },
  bindClipboardPasteForTest: bindClipboardPaste,
  dataTransferHasImagePayloadForTest: dataTransferHasImagePayload,
  dataTransferImageFilesForTest: dataTransferImageFiles,
  documentListenersForTest(type) { return [...(document.__listeners.get(type) || [])]; },
  setDocumentQuerySelectorForTest(fn) { document.querySelector = fn; },
  setDocumentQuerySelectorAllForTest(fn) { document.querySelectorAll = fn; },
  setDocumentVisibilityForTest(value) { Object.defineProperty(document, 'visibilityState', {value: String(value || 'visible'), configurable: true}); },
  commandPaletteItemScore,
  commandPaletteRankItems,
  commandPaletteCandidateItems,
  searchRankWeights,
  commandPaletteSearchQuery,
  commandPaletteCommandItems,
  commandPaletteItems,
  invokeCommandPaletteItemForTest(item, event = null) {
    commandPaletteItemsCache = item ? [item] : [];
    commandPaletteIndex = 0;
    return invokeCommandPaletteSelection(event);
  },
  dedupeFileSearchResults,
  setCommandPaletteStateForTest(mode, query) { commandPaletteMode = mode; commandPaletteQuery = query || ''; },
  commandPaletteMatches,
  openFileQuickOpen,
  testElementForId(id) { return document.getElementById(id); },
  registerTerminalForTest(session, term, socket = {readyState: WebSocket.OPEN}) {
    const item = {term, socket, container: document.getElementById('terminal-pane-' + session)};
    terminals.set(session, item);
    return item;
  },
  terminalAttentionQuestionTextsForTest: terminalAttentionQuestionTexts,
  terminalAttentionQuestionRowForTest: terminalAttentionQuestionRow,
  syncTerminalAttentionHighlightForTest: syncTerminalAttentionHighlight,
  clearTerminalAttentionHighlightForTest: clearTerminalAttentionHighlight,
  transcriptInfoForTest(session) { return transcriptMeta.sessions?.[session]; },
  applyTranscriptsPayloadForTest: applyTranscriptsPayload,
  applyTmuxSignalsPayloadForTest: applyTmuxSignalsPayload,
  syncAgentWindowActivityAnimationDelaysForTest: syncAgentWindowActivityAnimationDelays,
  restartAgentWindowActivityPulseAnimationsForTest: restartAgentWindowActivityPulseAnimations,
  scheduleTmuxWindowReadbackForTest: scheduleTmuxWindowReadback,
  setTmuxWindowActiveIndexOverrideForTest: setTmuxWindowActiveIndexOverride,
  setTmuxWindowActiveIndexPendingForTest: setTmuxWindowActiveIndexPending,
  tmuxWindowActiveIndexOverrideForTest: tmuxWindowActiveIndexOverride,
  activeTmuxSignalWindowForSessionForTest: activeTmuxSignalWindowForSession,
  seedSessionTeardownStateForTest(session) {
    const closed = {transcript: 0, summary: 0};
    transcriptStreams.set(session, {close() { closed.transcript += 1; }});
    summaryStreams.set(session, {close() { closed.summary += 1; }});
    autoApproveStates.set(session, {enabled: true});
    uploadResultsBySession.set(session, [{text: 'uploaded'}]);
    uploadCleanupTimers.set(session, 123);
    return closed;
  },
  stopSessionUiForTest: stopSessionUi,
  sessionTeardownStateForTest(session) {
    return {
      terminal: terminals.has(session),
      transcript: transcriptStreams.has(session),
      summary: summaryStreams.has(session),
      autoApprove: autoApproveStates.has(session),
      uploads: uploadResultsBySession.has(session),
      uploadTimer: uploadCleanupTimers.has(session),
    };
  },
  tmuxSessionActionCommands,
  tmuxSessionViewCommands,
  tmuxSessionNameError,
  replaceTmuxSessionInClient,
  createNextSessionForTest: createNextSession,
  confirmSessionGoneOrReconnectForTest: confirmSessionGoneOrReconnect,
  markPendingTmuxSessionForTest: markPendingTmuxSession,
  pendingTmuxSessionNamesForTest: pendingTmuxSessionNames,
  normalizedSessionOrder,
  fileDropCategory,
  dropSuggestionIndexFromKeyEvent,
  rememberDropActionForTest: rememberDropAction,
  dropSuggestionsFor,
  dropActionDisplayLabel,
  composeDropSuggestion,
  insertedDropActionText,
  runDropActionForTest: runDropAction,
  customDropActionFromLine,
  commandPaletteDropActionItems,
  normalizeLayoutSlots,
  compactLayoutSlots,
  layoutSlotsSignature,
  dockviewJsonFromLayoutSlots,
  layoutSlotsFromDockviewJson,
  adoptDockviewLayoutForTest(json, hostRect = null) {
    if (hostRect) {
      const host = document.createElement('div');
      host.id = 'dockviewHost';
      host.clientWidth = Number(hostRect.width) || 0;
      host.clientHeight = Number(hostRect.height) || 0;
      host.rect = {
        width: Number(hostRect.width) || 0,
        height: Number(hostRect.height) || 0,
        left: 0,
        top: 0,
        right: Number(hostRect.width) || 0,
        bottom: Number(hostRect.height) || 0,
      };
      dockviewLayoutState.host = host;
    } else {
      dockviewLayoutState.host = null;
    }
    dockviewLayoutState.api = {
      toJSON() { return json; },
      fromJSON() {},
      clear() {},
    };
    adoptDockviewLayout();
    return layoutSlots;
  },
  dockviewLayoutContentSignature,
  dockviewHostCanAdoptLayout,
  isPinnableTab,
  tabIsPinned,
  orderPaneTabs,
  pinnedTabIconHtml,
  setTabPinned,
  toggleTabPinned,
  toggleActiveTabPinned,
  pinnedTabsForTest() { return [...pinnedTabItems]; },
  setPinnedTabsForTest(items) {
    pinnedTabItems = normalizePinnedTabItems(items || []);
    writeStoredPinnedTabs();
  },
  paneIsPlaceholder,
  panelControlsHtml,
  setPanelDetailsCollapsedForTest: setPanelDetailsCollapsed,
  platformWindowControlClass,
  positionPaneTabPopover,
  pathIsInsideDirectory,
  scrollFileTreeRowIntoView,
  bindPaneTabStrip,
  paneTabShouldPreserve,
  restorePaneTabPopover,
  syncPreservedPaneTab,
  paneTabPopoverItemToRestore,
  clearPaneTabDropPreview,
  showSessionContextMenu,
  showTabContextMenu,
  openedWindowsForTest() { return globalThis.__openedWindows.map(record => ({url: record.url, name: record.name, features: record.features, focused: record.focused === true})); },
  bodyChildren() { return document.body.children; },
  defaultLayoutSlots,
  layoutShapeSignature,
  dedentSelectionText,
  dropIntentForEvent,
  dropIntentAllowsSession,
  fileDropIntentAllowsPayload,
  pathDropIntentAllowsPayload,
  paneSwapAllowed,
  paneSwapIntentForEvent,
  paneSwapIntentAllowed,
  swapPaneSlots,
  directoryEntriesSignature,
  editorModeLabel,
  editorWrapValue,
  editorViewModeFor,
  editorPreviewModeAvailable,
  setFileEditorViewMode,
  fileEditorDiffExpandUnchangedForItemForTest: fileEditorDiffExpandUnchangedForItem,
  setFileEditorDiffExpandUnchangedForItemForTest: setFileEditorDiffExpandUnchangedForItem,
  fileEditorBlameControlsVisible,
  updateFileEditorBlameButton,
  updateFileEditorDiffButton,
  updateFileEditorDiffExpandButton,
  codeMirrorConfigSignature,
  openFileDiffAvailable,
  localizedDateTimeFormat,
  sessionFileTimeText,
  sessionFileRelativeTimeText,
  sessionFileDisplayTimeText,
	  fileExplorerTreeDateModeLabel,
	  fileExplorerTreeDateModeButtonLabel,
	  fileExplorerTreeDateModeTitle,
	  nextFileExplorerTreeDateMode,
	  cycleFileExplorerTreeDateModeForTest: cycleFileExplorerTreeDateMode,
	  fileExplorerTreeDateModeForTest() { return fileExplorerTreeDateMode; },
	  setFileExplorerTreeDateModeForTest(value) { fileExplorerTreeDateMode = normalizeFileExplorerTreeDateMode(value); },
  fileTreeRecencyStateForMtimeForTest(mtime, nowMs) {
    const state = fileTreeRecencyStateForMtime(mtime, nowMs);
    return state ? {...state} : null;
  },
  setFileTreeRecencyNowForTest(value) {
    if (value == null) delete globalThis.__yolomuxFileTreeRecencyNowMs;
    else globalThis.__yolomuxFileTreeRecencyNowMs = Number(value);
  },
  dateTimeHourCycleForTest() { return dateTimeHourCycle; },
  setDateTimeHourCycleForTest(value) { dateTimeHourCycle = normalizeDateTimeHourCycle(value); },
  activeEditorSchemeForTest() { return activeEditorScheme(); },
  configuredEditorSchemeForMode,
  editorSchemeCssVariables,
  editorThemeLabel,
  editorPreviewThemeStateForTest: editorPreviewThemeState,
  applyEditorCursorStyle,
  setFileEditorThemeMode,
  setFileEditorPreviewDisplayMode,
  cycleEditorThemeMode,
  fileEditorThemeModeForTest() { return fileEditorThemeMode; },
  fileEditorPreviewDisplayModeForTest() { return fileEditorPreviewDisplayMode; },
  fileEditorCursorStyleForTest() { return fileEditorCursorStyle; },
  setFileEditorCursorStyleForTest(value) { fileEditorCursorStyle = value; },
  editorVisualHighlightHtml,
  editorVisualLineFragments,
  applyGlobalThemeMode,
  globalThemeLabel,
  globalThemeIsDark,
  nextGlobalThemeMode,
  terminalThemeForGlobalTheme,
  terminalMinimumContrastRatio,
  globalThemeModeForTest() { return globalThemeMode; },
  setGlobalThemeModeForTest(value) { globalThemeMode = normalizeGlobalThemeMode(value); },
  resolvedGlobalThemeModeForTest: resolvedGlobalThemeMode,
  setSystemPrefersDarkForTest(value) { window.matchMedia = () => ({matches: value === true, addEventListener() {}, addListener() {}}); },
  shareResolvedGlobalThemeModeForTest() { return shareResolvedGlobalThemeMode; },
  terminalThemeModeForTest() { return terminalThemeMode; },
  setTerminalThemeModeForTest(value) { terminalThemeMode = normalizeTerminalThemeMode(value); },
  expandPaneFromLayout,
  terminalThemeSettingForGlobalMode,
  sessionConfirmedGone,
  globalActivityCounts,
  globalActivityStatusLineHtml,
  openTabberActivityOverviewForTest: openTabberActivityOverview,
  clearPromptAttentionForSessionForTest(session, options = {}) { return clearPromptAttentionForSession(session, {...options, localOnly: options.localOnly !== false}); },
  attentionAcknowledgementKeyIsRecordedForTest: attentionAcknowledgementKeyIsRecorded,
  applyAttentionAcknowledgementResponseForTest: applyAttentionAcknowledgementResponse,
  terminalDataShouldAcknowledgeAttentionForTest: terminalDataShouldAcknowledgeAttention,
  updateSessionButtonStatesForTest: updateSessionButtonStates,
  browserFaviconBadgeCount,
  browserFaviconBadgeLabel,
  tmuxSignalPaneForTarget,
  tmuxSignalAgentStateForSession,
  setTmuxSignalStateForTest(payload) { tmuxSignalState = payload; },
  setAutoApproveStateForTest(session, state) { autoApproveStates.set(session, state); },
  applyAutoApprovePayloadForTest: applyAutoApprovePayload,
  setAgentAuthForTest(value) { agentAuth = value || {}; },
  maxTabsPerPane,
  tabsToEvictForCap,
  recordTabActivation,
  setTabLastActivatedForTest(item, ts) { tabLastActivatedAt.set(item, ts); },
  infoBranchRows,
  fileContextMenuState,
  fileEditorItemFor,
  fileEditorCopyItemFor,
  fileEditorDiffPreviewItemFor,
  fileEntryChanged,
  fileItemPath,
  filePanelItemsForPath,
  imageOpenUsesSharedViewer,
  imageViewerItemFor,
  markdownPreviewInputAllowed,
  previewRendererForPath,
  previewPathIsPreviewable,
  previewKindForPath,
  previewMediaKindForPath,
  previewMimeForPath,
  markdownPreviewHtml,
  markdownPreviewImageTarget,
  sanitizeStandaloneSvg,
  isMermaidFenceLanguage,
  keyboardShortcutsHtml,
  openFileEditorItems,
  pullRequestStatusLabel,
  pullRequestStatusDisplay,
  pullRequestStatusClass,
  pullRequestInlineStatusDisplay,
  pullRequestCiState,
  pullRequestCiStatus,
  pullRequestChecksHtml,
  pullRequestLinkLabel,
  pullRequestApprovalIndicatorHtml,
  pullRequestCompactBadgesHtml,
  pullRequestNumberIndicatorHtml,
  pullRequestReviewInlineHtml,
  sessionStateHtml,
  openFileStatus,
  fileErrorStateForTest: fileErrorState,
  missingFileStateForTest: missingFileState,
  tooLargeFileStateForTest: tooLargeFileState,
  fileErrorTextForTest: fileErrorText,
  fileInspectionErrorMessageForTest: fileInspectionErrorMessage,
  setOpenFileOwner,
  renderTransportWarning,
  captureFileEditorPanelViewStateForTest: captureFileEditorPanelViewState,
  restoreFileEditorPanelViewStateForTest: restoreFileEditorPanelViewState,
  fileEditorViewStateForTest(item) {
    const state = fileEditorViewState.get(item);
    return state ? {...state} : null;
  },
  pendingFileEditorLineTargetForTest(item) {
    return pendingFileEditorLineTargets.get(item) || null;
  },
  renderFileEditorPanelShouldCaptureViewStateForTest: renderFileEditorPanelShouldCaptureViewState,
  renderFileEditorPanel,
  setFileEditorPanelStatusForTest: setFileEditorPanelStatus,
  reloadOpenFileFromDiskForTest: reloadOpenFileFromDisk,
  saveFileEditorForTest: saveFileEditor,
  renderEditorPreviewPane,
  openFileIsMissing,
  terminalTabLabel,
  terminalTabTitle,
  terminalTabDisplayLabel,
  terminalTmuxPrefixWindowShortcutForTest: terminalTmuxPrefixWindowShortcut,
  terminalTmuxAltWindowShortcutForTest: terminalTmuxAltWindowShortcut,
  tmuxWindowForTest: tmuxWindow,
  registerFileEditorLayoutItemForTest: registerFileEditorLayoutItem,
  setOpenFileStateForTest(path, state) { setFileState(path, state); },
  currentFileStateForTest(path) {
    const state = fileStateFor(path);
    return state ? {...state} : null;
  },
  openFileStateForTest(path) {
    const state = fileStateFor(path);
    return state ? {...state} : null;
  },
  renderTreeChildrenForTest(container, parentPath, entries, depth = 0, entriesByDirPairs = [], options = {}) {
    renderTreeChildren(container, parentPath, entries, depth, {...options, entriesByDir: new Map(entriesByDirPairs)});
  },
  expandDirectoryRowForTest: expandDirectoryRow,
  collapseDirectoryRowForTest: collapseDirectoryRow,
  setFileExplorerRepoInfoForTest(path, repo) {
    fileExplorerRepoInfoCache.set(normalizeDirectoryPath(path), repo);
  },
  repoInfoPopoverHtml,
  fileTreeRepoSyncMeta,
  fileTreeDisplayParts,
  setChangesFolderCollapsedForTest(keys) { changesFolderCollapsed = new Set((keys || []).map(String)); },
  changesFolderCollapsedForTest() { return Array.from(changesFolderCollapsed).sort(); },
  changesRepoCollapsedForTest() { return Array.from(changesRepoCollapsed).sort(); },
  rawFileUrl,
  rawFileDownloadUrl,
  zipFileDownloadUrl,
  downloadFilenameFromContentDisposition,
  displayQuickAccessPath,
  expandQuickAccessPath,
  markOpenFileDiffUnavailable,
  openChangedFileInDiffForTest: openChangedFileInDiff,
  openFileInEditorForTest: openFileInEditor,
  openFileInAdditionalEditorTabForTest: openFileInAdditionalEditorTab,
  focusPreferencesSearch,
  renderPreferencesPanelsForTest: renderPreferencesPanels,
  renderPaneTabStrips,
  paneTabDisplayContext,
  fileTabParentDisambiguators,
  ensureFileTabStateForItem,
  setDragSessionForTest(session) { dragSession = session; },
  pendingTabStripRenderForTest() { return pendingTabStripRender; },
  renderSessionButtonsForTest: renderSessionButtons,
  sessionButtonsForTest() { return sessionButtons; },
  pendingSessionButtonsRenderForTest() { return pendingSessionButtonsRender; },
  setPendingSessionButtonsRenderForTest(value) { pendingSessionButtonsRender = Boolean(value); },
  setDocumentActiveElementForTest(element) { document.activeElement = element; },
  topbarControlIsActiveForTest: topbarControlIsActive,
  flushPendingSessionButtonsRenderForTest: flushPendingSessionButtonsRender,
  pendingPreferencesRenderForTest() { return pendingPreferencesRender; },
  setPendingPreferencesRenderForTest(value) { pendingPreferencesRender = Boolean(value); },
  preferencesScrollActiveUntilForTest() { return preferencesScrollActiveUntil; },
  setPreferencesScrollActiveUntilForTest(value) { preferencesScrollActiveUntil = Number(value) || 0; },
  notePreferencesScrollActivityForTest: notePreferencesScrollActivity,
  renderPanels,
  pendingPanelsRenderForTest() { return Boolean(pendingLayoutRender); },
  pendingLayoutRenderForTest() { return pendingLayoutRender; },
  setPendingPanelsRenderForTest(value) {
    pendingLayoutRender = value ? {previousActive: [], prevShape: '', nextShape: '', options: {}, reason: 'test', forceFull: true} : null;
  },
  setClientSettingsPatchForTest(patch) {
    clientSettings = mergeSettingObjects(clientSettings, patch || {});
  },
  setClientSettingsPayloadPatchForTest(patch) {
    clientSettingsPayload = {...clientSettingsPayload, ...(patch || {})};
  },
  setWindowConfirmForTest(fn) {
    window.confirm = fn;
  },
  preferenceItemMatches,
  preferenceSectionMatches,
  settingsLoadedAgeText,
  preferencesPanelHtmlForTest(query, collapsed = []) {
    preferencesSearchText = query || '';
    collapsedPreferenceSections = new Set(collapsed);
    preferencesResetConfirmVisible = false;
    return preferencesPanelHtml();
  },
  preferencesResetConfirmHtmlForTest() {
    preferencesResetConfirmVisible = true;
    return preferencesPanelHtml();
  },
  registerFileEditorLayoutItem,
  registerImageViewerLayoutItem,
  requestFileEditorPanelFocus,
  focusFileEditorPanelIfReady,
  minimizePaneFromLayout,
  removePaneFromLayout,
  removeSessionFromLayout,
  runtimeIntervalDelay,
  sessionPopoverHtml,
  setFileQuickOpenCandidatesForTest(root, files) {
    fileQuickOpenRoot = root;
    fileQuickOpenCandidates = files;
    fileQuickOpenLoading = false;
    fileQuickOpenError = '';
    commandPaletteMode = 'files';
  },
  setFileQuickOpenLoadingForTest(loading) {
    fileQuickOpenLoading = Boolean(loading);
    fileQuickOpenError = '';
    commandPaletteMode = 'files';
  },
  setFileQuickOpenErrorForTest(error) {
    fileQuickOpenCandidates = [];
    fileQuickOpenLoading = false;
    fileQuickOpenError = error;
    commandPaletteMode = 'files';
  },
  setCommandPaletteQueryForTest(value) {
    commandPaletteQuery = String(value || '');
    commandPaletteIndex = 0;
  },
  commandPaletteResultsHtmlForTest() {
    const query = commandPaletteSearchQuery();
    const rows = commandPaletteRankItems(commandPaletteItems(), query).slice(0, 60);
    return rows.length ? commandPaletteResultsHtml(rows, query) : '';
  },
  commandPaletteItemLabelHtmlForTest: commandPaletteItemLabelHtml,
  commandPaletteStatusHtmlForTest: commandPaletteStatusHtml,
  setTabsMenuSearchTextForTest(value) { tabsMenuSearchText = String(value || ''); },
  sessionState,
  slotForNewFileEditorTab,
  slotForNewTmuxSession,
  slotForSession,
  slotForTabActivation,
  focusedActivationSlotForTest: focusedActivationSlot,
  fileEditorActivationSlotForTest: fileEditorActivationSlot,
  simpleCodeSyntaxHtml,
  smallLayoutSlotCandidate,
  slotCanAutoPrune,
  splitPercentForPointer,
  layoutNodeMinWidth,
  minSplitPaneWidthPx,
  minSplitPaneHeightPx,
  layoutVisiblePaneCount,
  fileImagePreviewMinShowDelayMs,
  splitPercentForNewItem,
  sessionNotificationTitleForTest: sessionNotificationTitle,
  attentionToastLineForTest: attentionToastLine,
  handleDropDragOver,
  installFilePathDropTarget,
  showPaneTabDropPreview,
  showDropPreview,
  clearDropPreview,
  shouldPreserveSourceSlotForSplit,
  startSessionDrag,
  startPaneDrag,
  paneDragPayload,
  endSessionDrag,
  startFileTreeDrag,
  stopCustomDragPreview,
  syncInitialLayoutUrl,
  tabMenuDetailText,
  tabSearchFields,
  tabSearchScore,
  TAB_TYPES,
  tabTypeForItem,
  terminalWheelSignedLines,
  sessionPaneIsAlternateScreen,
  terminalWrappedLineLinks,
  terminalWrappedLineReferences,
  terminalReferenceAtPosition,
  terminalReferenceProviderLinks,
  terminalFileReferenceUnderlineSegments,
  terminalFileReferenceUnderlineIsActiveForTest: terminalFileReferenceUnderlineIsActive,
  installTerminalFileReferenceUnderlines,
  terminalFileReferenceAbsolutePath,
  terminalFileReferenceTarget,
  terminalFileReferenceTargetCacheSizeForTest: () => terminalFileReferenceTargetCache.size,
  terminalFileReferenceTargetCacheHasForTest: (session, reference) => terminalFileReferenceTargetCache.has(terminalFileReferenceCacheKey(session, reference)),
  terminalPositionFromClientPoint,
  requestFileEditorLineTarget,
  applyPendingFileEditorLineTarget,
  showTerminalContextMenuForTest: showTerminalContextMenu,
  transcriptPathRowHtml,
  splitNode,
  splitSessionAtSlot,
  splitSessionAtLayoutBoundary,
  splitSessionAtGutter,
  updateActiveSessionParam,
  openYoagentRightPane,
  rightmostExistingPaneSlot,
  paneTabDropIndex,
  paneTabDropPlacement,
  dockviewTabDropWouldNoop,
  dockviewTabEdgeReorderIntent,
  dockviewTabStripEndDropIntent,
  dockviewTabDropViolatesPinnedPartitionForTest: dockviewTabDropViolatesPinnedPartition,
  windowStepButtonFromEvent,
  tmuxWindowRecords,
  tmuxWindowBarLabelMode,
  tmuxWindowBarHtml,
  updatePanelInfoBarMetaForTest: updatePanelInfoBarMeta,
  updatePanelWindowStepButtonsForTest: updatePanelWindowStepButtons,
  agentWindowVisibleTonesForTest() { return [...AGENT_WINDOW_VISIBLE_TONES]; },
  agentWindowAggregateTonesForTest() { return [...AGENT_WINDOW_AGGREGATE_TONES]; },
  agentWindowVisibleToneForTest: agentWindowVisibleTone,
  agentWindowStatusToneForItemForTest: agentWindowStatusToneForItem,
  agentWindowActivityToneWrapperClassForTest: agentWindowActivityToneWrapperClass,
  agentWindowStatusSampleItemForTest: agentWindowStatusSampleItem,
  agentWindowStatusDotHtmlForToneForTest: agentWindowStatusDotHtmlForTone,
  agentWindowStatusDotHtmlForTest: agentWindowStatusDotHtml,
  agentWindowActivityStyleAttributeForTest: agentWindowActivityStyleAttribute,
  agentWindowActivityIconForTest: agentWindowActivityIcon,
  topbarActivityCountBallHtmlForTest: topbarActivityCountBallHtml,
  keyboardLegendStatusSampleForTest: keyboardLegendStatusSample,
  preferencesStatusPulseExampleHtmlForTest: preferencesStatusPulseExampleHtml,
  setWorkflowTransitionGlowSecondsForTest(value) { workflowTransitionGlowSeconds = Math.max(0, Number(value) || 0); },
  acknowledgeAgentWindowActivityForTest(session, windowIndex = null, options = {}) { return acknowledgeAgentWindowActivity(session, windowIndex, {...options, localOnly: options.localOnly !== false}); },
  acknowledgeAgentWindowStoppedTransitionForTest: acknowledgeAgentWindowStoppedTransition,
  agentWindowAcknowledgementVisualActiveForTest: agentWindowAcknowledgementVisualActive,
  agentWindowActivityIconHtmlForStatusForTest: agentWindowActivityIconHtmlForStatus,
  sessionAgentWindowStatusModelForTest: sessionAgentWindowStatusModel,
  sessionAgentWindowStatusPayloadsForTest: sessionAgentWindowStatusPayloads,
  tmuxWindowCanonicalLabelForTest: tmuxWindowCanonicalLabel,
  buildTabberTreeForTest: buildTabberTree,
  tabMenuItems,
  sortTabItemsForMenu,
  setTabsMenuSortMode,
  tmuxPaneTabHtml,
  paneTabs,
  paneStateWithTabs,
  markdownSyntaxHtml,
  markdownTextWithSourceAnchors,
  markdownTaskLineEntries,
  markdownTextWithTaskLineToggled,
  markdownPreviewBlockedTagsForTest() { return Array.from(MARKDOWN_PREVIEW_BLOCKED_TAGS); },
  moveSessionToSlot,
  openFileEditorPane,
  onFileTreeRowClick,
  pathRelativeToDirectory,
  replaceHtmlPreservingScroll,
  pruneFileExplorerSelectionForRoot,
  selectFileTreePath,
  selectFileTreeRange,
  updateFileTreeSelectionFromClick,
  fileExplorerKeyIntent,
  sharedTreeKeyIntentForTest: sharedTreeKeyIntent,
  handleFileExplorerArrowNavForTest: handleFileExplorerArrowNav,
  activeTabberRowPathForTest: activeTabberRowPath,
  syncTabberTreeActiveSelectionForTest: syncTabberTreeActiveSelection,
  tabberTreeSelectionForTest() {
    return {
      paths: Array.from(tabberTreeSelectedPaths).sort(),
      lead: tabberTreeSelectionLead,
    };
  },
  sharedTreeControllerNamesForTest: sharedTreeInteractionControllerNames,
  currentSlots() { return layoutSlots; },
  fileExplorerSelectionForTest() {
    return {
      paths: Array.from(fileExplorerSelectedPaths).sort(),
      anchor: fileExplorerSelectionAnchor,
      manual: fileExplorerManualSelectionActive,
    };
  },
  setFileExplorerSelectionForTest(paths, anchor = null) {
    fileExplorerSelectedPaths.clear();
    for (const path of paths || []) fileExplorerSelectedPaths.add(path);
    fileExplorerSelectionAnchor = anchor;
    fileExplorerManualSelectionActive = false;
  },
  activeFileForTest() { return activeFile; },
  setFileExplorerExpandedForTest(paths) {
    fileExplorerExpanded.clear();
    for (const path of paths || []) fileExplorerExpanded.add(path);
  },
  fileExplorerExpandedForTest() { return Array.from(fileExplorerExpanded).sort(); },
  setFileExplorerRootForTest(path) { fileExplorerRoot = normalizeDirectoryPath(path); },
  fileExplorerRootForTest() { return fileExplorerRoot; },
  setFileExplorerDirListingForTest(path, entries) {
    fileExplorerDirListingCache.set(normalizeDirectoryPath(path), {entries, at: Date.now()});
  },
  setAutoApproveStateForTest(session, payload) {
    autoApproveStates.set(session, payload);
  },
  setTranscriptInfoForTest(session, info) {
    transcriptMeta.sessions = {...(transcriptMeta.sessions || {}), [session]: info};
  },
  setTranscriptSessionOrderForTest(values) {
    transcriptMeta.session_order = Array.isArray(values) ? values.map(value => String(value)) : [];
  },
  setActivitySummaryPayloadForTest(payload) {
    activitySummaryPayload = payload;
    yoagentStartupActivitySummaryPayload = null;
  },
  yoagentRecentAgentsHtmlForTest: yoagentRecentAgentsHtml,
  applyServerMetadataPulsesForTest(session, pulses) {
    updateMetadataBadgePulses({sessions: {[session]: {metadata_badge_pulse_remaining_ms: pulses}}});
  },
  storageValueForTest(key) { return localStorage.getItem(key); },
  sessionStorageValueForTest(key) { return sessionStorage.getItem(key); },
  rememberFileExplorerOpenIntentForTest: rememberFileExplorerOpenIntent,
  fileExplorerClosedByUserForTest: fileExplorerClosedByUser,
  windowListenersForTest(type) { return [...(window.__listeners?.get?.(type) || [])]; },
  appRootForTest: appRootElement,
  appViewport,
  effectiveViewportWidthForTest: effectiveViewportWidth,
  setAppViewportOverrideForTest: setAppViewportOverride,
  appSpaceRect,
  appSpacePoint,
  visualPointFromAppSpace,
  setAppMirrorTransformForTest: setAppMirrorTransform,
  ensureShareMirrorStageForTest: ensureShareMirrorStage,
  shareMirrorFitTransform,
  normalizeShareViewFit,
  shareAppearanceSnapshotForTest: shareAppearanceSnapshot,
  applyShareAppearanceStateForTest: applyShareAppearanceState,
  shareCreateFormHtmlForTest: shareCreateFormHtml,
  shareCreatePayloadFromFormForTest: shareCreatePayloadFromForm,
  shareBuildUiMessageForTest: shareBuildUiMessage,
  setActiveSharesForTest(shares) { setActiveShares(shares || []); },
  setShareHostSocketForTest(token, socket) { shareHostSockets.set(String(token || ''), socket); },
  shareHostQueueForTest(token) { return [...(shareHostQueues.get(String(token || '')) || [])]; },
  sharePublishPointerEventForTest: sharePublishPointerEvent,
  applyShareUiMessageForTest: applyShareUiMessage,
  shareMirrorProtocolForTest: shareMirrorProtocol,
  shareReplayFeatureEnabledForTest: shareReplayFeatureEnabled,
  shareReplaySemanticEscapeEnabledForTest() { return shareReplaySemanticEscapeEnabled === true; },
  shareReadOnlyReplayModeEnabledForTest: shareReadOnlyReplayModeEnabled,
  shareSemanticReadOnlyMirrorEnabledForTest: shareSemanticReadOnlyMirrorEnabled,
  shareReplayShellEnabledForTest: shareReplayShellEnabled,
  installShareReplayShellForTest: installShareReplayShell,
  shareReplayShellStateForTest() { return {...shareReplayShellState}; },
  setShareReplaySequenceStateForTest(epoch, sequence) {
    shareReplayShellActive = true;
    shareReplayShellState = {status: 'mirrored'};
    shareReplayCurrentEpoch = Math.max(0, Math.round(Number(epoch) || 0));
    shareReplayLastSequence = Math.max(0, Math.round(Number(sequence) || 0));
    shareReplayDroppedFrames = 0;
    shareReplayStaleFrames = 0;
    shareReplayKeyframeRequestCount = 0;
    shareReplayKeyframeRequestSuppressedCount = 0;
    shareReplayKeyframeLastRequestAt = 0;
    shareReplayKeyframeBackoffMs = 0;
    shareReplayKeyframeInFlight = false;
  },
  shareReplayNodeMapSizeForTest() { return shareReplayNodeMap.size; },
  shareReplayTerminalPlaceholderCountForTest() { return shareReplayTerminalPlaceholders.size; },
  shareReplayMutationEntriesForTest: shareReplayMutationEntries,
  shareReplayEnqueueMutationRecordsForTest: shareReplayEnqueueMutationRecords,
  shareReplayFlushMutationDeltasForTest: shareReplayFlushMutationDeltas,
  shareReplayScrollEntryForElementForTest: shareReplayScrollEntryForElement,
  shareReplayScrollSnapshotForTest: shareReplayScrollSnapshot,
  scheduleShareReplayScrollPublishForElementForTest: scheduleShareReplayScrollPublishForElement,
  shareReplayPointerPayloadForTest: shareReplayPointerPayload,
  shareReplayApplyPointerForTest: shareReplayApplyPointer,
  shareReplayDeltaSequenceStatusForTest: shareReplayDeltaSequenceStatus,
  shareReplayDeltaCanApplyBestEffortForTest: shareReplayDeltaCanApplyBestEffort,
  applyShareReplayKeyframeForTest: applyShareReplayKeyframe,
  applyShareReplayDeltaForTest: applyShareReplayDelta,
  bindShareReplayPaneTabPopoversForTest: bindShareReplayPaneTabPopovers,
  sharePointerPayloadForPointForTest: sharePointerPayloadForPoint,
  shareReplayLastDeltaBatchForTest() { return shareReplayLastDeltaBatch ? JSON.parse(JSON.stringify(shareReplayLastDeltaBatch)) : null; },
  shareReplaySequenceStateForTest() {
    return {
      epoch: shareReplayCurrentEpoch,
      sequence: shareReplayLastSequence,
      dropped: shareReplayDroppedFrames,
      stale: shareReplayStaleFrames,
      requests: shareReplayKeyframeRequestCount,
      suppressed: shareReplayKeyframeRequestSuppressedCount,
      backoffMs: shareReplayKeyframeBackoffMs,
      inFlight: shareReplayKeyframeInFlight,
    };
  },
  shareReplayRequestKeyframeForTest: shareReplayRequestKeyframe,
  shareReplayHealthDiagnosticsForTest: shareReplayHealthDiagnostics,
  shareReplayUserStatusTextForTest: shareReplayUserStatusText,
  shareReplayCurrentDomDigestForTest: shareReplayCurrentDomDigest,
  shareReplayRedactTextForTest: shareReplayRedactText,
  shareReplaySanitizeAttributeForTest: shareReplaySanitizeAttribute,
  shareCreateDomKeyframePayloadForTest: shareCreateDomKeyframePayload,
  shareCreateDomKeyframeMessageForTest: shareCreateDomKeyframeMessage,
  shareDropStaleMirrorFrameForTest: shareDropStaleMirrorFrame,
  scheduleShareTopologySnapshotForTest: scheduleShareTopologySnapshot,
  shareTopologyDomKeyframeDelayMsForTest: shareTopologyDomKeyframeDelayMs,
  setSharePointerLastPublishedAtForTest(value) { sharePointerLastPublishedAt = Number(value); },
  setShareReplayHostLastKeyframeAtForTest(value) { shareReplayHostLastKeyframeAt = Number(value) || 0; },
  setShareReplayTopologyKeyframeQueuedAtForTest(value) { shareReplayTopologyKeyframeQueuedAt = Number(value) || 0; },
  shareMirrorLastFrameForTest(sender = '', family = '') {
    const cleanSender = String(sender || 'host');
    const key = family ? cleanSender + ':' + family : cleanSender;
    const state = shareMirrorLastFrameBySender.get(key) || null;
    return state ? {...state} : null;
  },
  shareCreateUiStateSnapshotForTest: shareCreateUiStateSnapshot,
  sharePopupLayerPayloadForTest: sharePopupLayerPayload,
  applySharePopupLayerForTest: applySharePopupLayer,
  sharePopupLayerLastSeqForTest(owner = '') { return sharePopupLayerLastSeqBySender.get(String(owner || 'host')) || 0; },
  sharePopupLayerNodeForTest() { return sharePopupLayerNode; },
  shareUiStateSnapshotForTest: shareUiStateSnapshot,
  shareGeometryDigestSnapshotForTest: shareGeometryDigestSnapshot,
  shareGeometryFirstDifferenceForTest: shareGeometryFirstDifference,
  shareGeometryRepairActionForDiffForTest: shareGeometryRepairActionForDiff,
  publishShareGeometryDigestForTest: publishShareGeometryDigest,
  shareHostConnectedViewerCountForTest: shareHostConnectedViewerCount,
  shareHostHasConnectedViewersForTest: shareHostHasConnectedViewers,
  shareReplayHostPerformanceForTest: shareReplayHostPerformanceDiagnostics,
  applyShareTerminalCellsRepairForTest: applyShareTerminalCellsRepair,
  shareWrappedTextDigestSnapshotForTest: shareWrappedTextDigestSnapshot,
  applyShareUiStateForTest: applyShareUiState,
  applyShareScrollStateForTest: applyShareScrollState,
  shareCanPublishUiForTest: shareCanPublishUi,
  shareCanPublishScrollForTest: shareCanPublishScroll,
  setShareLastAppliedScrollForTest(target, state, payload = {}) {
    const cleanTarget = String(target || '');
    const cleanState = state || {};
    shareLastAppliedScrollByTarget.set(cleanTarget, {
      ...cleanState,
      payload: {...payload, target: cleanTarget, ...cleanState},
    });
  },
  shareLastAppliedScrollForTest(target) {
    const state = shareLastAppliedScrollByTarget.get(String(target || ''));
    return state ? {top: state.top, left: state.left} : null;
  },
  shareLastAppliedScrollPayloadForTest(target) {
    const state = shareLastAppliedScrollByTarget.get(String(target || ''))?.payload;
    return state ? {...state} : null;
  },
  restoreShareReadonlyScrollTargetForTest: restoreShareReadonlyScrollTarget,
  restoreShareScrollTargetByKeyForTest: restoreShareScrollTargetByKey,
  scheduleShareScrollRestoreByKeyForTest: scheduleShareScrollRestoreByKey,
  applyShareViewBodyClassesForTest: applyShareViewBodyClasses,
  shareHostTerminalSizeForTest: shareHostTerminalSize,
  updateShareHostTerminalSizeForTest: updateShareHostTerminalSize,
  fitTerminalForTest: fitTerminal,
  shareReadonlyTargetIsMirroredSurfaceForTest: shareReadonlyTargetIsMirroredSurface,
  shareReadonlyKeyboardAllowsDefault,
  shareReadonlyShouldPreventDefault,
  blockShareReadonlyInteraction,
  setSessionFilesPayloadForTest(payload) {
    fileExplorerSessionFilesPayload = payload;
  },
  setSessionFilesPayloadForDestinationForTest(payload) {
    setSessionFilesPayloadForDestination('finder', payload);
  },
  setSessionFilesLoadingForTest(loading) {
    fileExplorerSessionFilesLoading = Boolean(loading);
  },
  setFileExplorerSessionFilesPayloadForTest(payload) {
    fileExplorerSessionFilesPayload = payload;
  },
  sessionFilesPayloadForTest() {
    return fileExplorerSessionFilesPayload;
  },
  setSessionFilesCachePayloadForTest(session, payload) {
    fileExplorerSessionFilesCache.set(sessionFilesCacheKey(session), {
      payload: {...payload, session},
      signature: sessionFilesPayloadSignatureForPayload(payload),
    });
  },
  applySessionFilesPayloadFromPushForTest: applySessionFilesPayloadFromPush,
  sessionFilesPanelIsLoadingForTest: sessionFilesPanelIsLoading,
  sessionFilesPushRequestMatchesCurrentForTest: sessionFilesPushRequestMatchesCurrent,
  changedFileOwnerSessionForPathForTest: changedFileOwnerSessionForPath,
  fileTreeChangedAncestorStatsForTest(payload) {
    return Array.from(fileTreeChangedAncestorStats(payload).entries()).map(([path, stats]) => [path, {...stats}]);
  },
  updateFileTreeGitStatusRowsForTest(rows) {
    const previousQuerySelectorAll = document.querySelectorAll;
    document.querySelectorAll = selector => selector === '.file-tree-row[data-path]:not([data-tabber-type])' || selector === '.file-tree-row[data-path]' ? rows : previousQuerySelectorAll(selector);
    try {
      updateFileTreeGitStatusRows();
    } finally {
      document.querySelectorAll = previousQuerySelectorAll;
    }
  },
  setFileExplorerModeForTest(mode) {
    fileExplorerMode = normalizeFileExplorerMode(mode);
  },
  fileExplorerModeForTest() { return fileExplorerMode; },
  readStoredFileExplorerModeForTest(value) {
    if (value == null) localStorage.removeItem(fileExplorerModeStorageKey);
    else localStorage.setItem(fileExplorerModeStorageKey, value);
    return readStoredFileExplorerMode();
  },
  buildTabberTree,
  renderTabberTree,
  fetchTabberSessionFilesForTest: fetchTabberSessionFiles,
  fetchTabberSessionFilesBatchForTest: fetchTabberSessionFilesBatch,
  tabberSessionForNumericKey,
  openTabberSessionForTest: openTabberSession,
  bindTabberPanelForTest: bindTabberPanel,
  fileExplorerChangesPanelStaticHtmlForTest: fileExplorerChangesPanelStaticHtml,
  fileExplorerTreeSortSelectHtmlForTest: fileExplorerTreeSortSelectHtml,
  fileExplorerModeSwitcherHtml,
  normalizeFileExplorerMode,
  setTabberActivityForTest(payload) {
    tabberActivityPayload = payload;
    tabberActivityRequestGeneration = 0;
    tabberActivityAppliedRequestGeneration = 0;
  },
  applyTabberActivityPayloadForTest: applyTabberActivityPayload,
  setFileExplorerTreeSortModeForTest(mode) { fileExplorerTreeSortMode = mode; },
  setTabberSessionFilesForTest(session, files) {
    const state = tabberSessionFilesState(session);
    state.files = files;
    state.loaded = true;
    state.loading = false;
  },
  setTabberSessionFilesLoadingForTest(session) {
    const state = tabberSessionFilesState(session);
    state.files = [];
    state.loaded = false;
    state.loading = true;
  },
  setTabberCollapsedForTest(paths) {
    fileExplorerTabberCollapsed.clear();
    fileExplorerTabberExpanded.clear();
    for (const path of paths || []) fileExplorerTabberCollapsed.add(path);
  },
  setTabberPathExpandedForTest: setTabberPathExpanded,
  tabberRenderedRowsForTest(options = {}) {
    if (options.preserveCollapsed !== true) {
      fileExplorerTabberCollapsed.clear();
      fileExplorerTabberExpanded.clear();
      if (options.defaultCollapsed !== true) {
        const {entries, entriesByDir} = buildTabberTree();
        const expandAll = (list, parent) => {
          for (const entry of list || []) {
            if (entry.kind !== 'dir') continue;
            const path = parent === '/' ? '/' + entry.name : parent + '/' + entry.name;
            fileExplorerTabberExpanded.add(path);
            expandAll(entriesByDir.get(normalizeDirectoryPath(path)), path);
          }
        };
        expandAll(entries, '/');
      }
    }
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    return Array.from(el.querySelectorAll('.file-tree-row')).map(row => {
      const dateCell = row.querySelector('.file-tree-date') || {};
      const dateHtml = dateCell.innerHTML || '';
      const dateText = dateHtml ? dateHtml.replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim() : (dateCell.textContent || '');
      return {
        type: row.dataset.tabberType || '',
        name: (row.querySelector('.file-tree-name') || {}).textContent || '',
        icon: (row.querySelector('.file-tree-icon') || {}).textContent || '',
        path: row.dataset.path || '',
        repoRoot: row.dataset.tabberRepoRoot || '',
        branch: row.dataset.tabberBranch || '',
        nameHtml: (row.querySelector('.file-tree-name') || {}).innerHTML || '',
        status: (row.querySelector('.file-tree-git-status') || {}).textContent || '',
        statusHidden: row.querySelector('.file-tree-git-status')?.hidden !== false,
        date: dateText,
        dateHtml,
        recency: row.dataset.recency || '',
        title: row.dataset.tabberTitle || row.getAttribute('title') || '',
        nativeTitle: row.getAttribute('title') || '',
        ariaCurrent: row.getAttribute('aria-current') || '',
        ariaExpanded: row.getAttribute('aria-expanded') || '',
        ariaSelected: row.getAttribute('aria-selected') || '',
        classes: Array.from(row.classList?.names || row.classList || []).sort(),
        dateClasses: Array.from(dateCell.classList?.names || dateCell.classList || []).sort(),
        dateAttentionDelay: dateCell.style?.getPropertyValue?.('--attention-animation-delay') || '',
        datasetKeys: Object.keys(row.dataset).sort(),
      };
    });
  },
  tabberRenderedNamesForTest() {
    fileExplorerTabberCollapsed.clear();
    const el = document.createElement('div');
    el.className = 'changes-groups';
    renderTabberTree(el);
    renderTabberTree(el);
    return Array.from(el.querySelectorAll('.file-tree-row')).map(row => {
      const name = row.querySelector('.file-tree-name');
      return name ? (name.textContent || '') : '';
    });
  },
  setSessionFilesSortModeForTest(mode) {
    sessionFilesSortMode = normalizeSessionFilesSortMode(mode);
  },
  sortedSessionFiles,
  sessionYoloIsWorking,
  runningAgentCount,
  updateDocumentTitle,
  documentTitleForTest() { return document.title; },
  setDocumentTitleNowForTest(value) { window.__yolomuxDocumentTitleNowMs = Number(value); },
  modalClassForTest() { return document.getElementById('modal').className; },
  modalTitleForTest() { return document.getElementById('modalTitle').textContent; },
  modalBodyHtmlForTest() { return document.getElementById('modalBody').innerHTML; },
  statusTextForTest() { return statusEl.textContent; },
  statusHtmlForTest() { return statusEl.innerHTML; },
  serialize(slots) {
    return {
      tree: slots[layoutTreeKey],
      panes: Object.fromEntries(layoutSlotKeys(slots).map(slot => [
        slot,
        {
          tabs: paneTabs(slot, slots),
          active: activeItemForSide(slot, slots),
          ...(paneIsPlaceholder(slot, slots) ? {placeholder: true} : {}),
        },
      ])),
    };
  },
  setLayoutSlotsForTest(nextSlots) {
    layoutSlots = normalizeLayoutSlots(nextSlots);
    activeSessions = sessionsFromLayout();
    updateActiveSessionParam();
    return globalThis.__lastUrl;
  },
  syncInitialLayoutUrlForTest() {
    syncInitialLayoutUrl();
    return globalThis.__lastUrl;
  },
  lastUrlForTest() {
    return globalThis.__lastUrl || '';
  },
  setGridPreviewNodesForTest(nodes) {
    grid.querySelectorAll = () => nodes;
  },
  gridForTest() {
    return grid;
  },
  sharePointerPayloadForPoint,
  sharePointerPayloadForEvent,
  sharePointFromPointerPayload,
  shareScrollPayloadForElement,
  applyShareScrollState,
  stableDigestJson,
  shareGeometryDigestValue,
  shareGeometryFirstDifference,
  setAppMenuBarRectForTest(rect) {
    renderSessionButtons({force: true});
    const bars = sessionButtons.querySelectorAll('.app-menu-bar');
    for (const bar of bars) bar.rect = rect;
    const buttons = Array.from(sessionButtons.querySelectorAll('.app-menu'))
      .map(menu => menu.querySelector(':scope > .app-menu-button'))
      .filter(Boolean);
    const buttonWidth = buttons.length ? rect.width / buttons.length : 0;
    buttons.forEach((button, index) => {
      button.rect = {
        left: rect.left + (buttonWidth * index),
        right: rect.left + (buttonWidth * (index + 1)),
        top: rect.top,
        bottom: rect.bottom,
        width: buttonWidth,
        height: rect.height,
      };
    });
    return bars.length;
  },
  setAppMenuButtonRectForTest(menuId, rect) {
    renderSessionButtons({force: true});
    const bars = sessionButtons.querySelectorAll('.app-menu-bar');
    for (const bar of bars) {
      bar.rect = {left: 0, right: 500, top: 0, bottom: 32, width: 500, height: 32};
    }
    for (const button of Array.from(sessionButtons.querySelectorAll('.app-menu')).map(menu => menu.querySelector(':scope > .app-menu-button')).filter(Boolean)) {
      button.rect = {left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0};
    }
    const menu = Array.from(sessionButtons.querySelectorAll('.app-menu')).find(node => node.dataset.appMenu === menuId);
    const button = menu?.querySelector(':scope > .app-menu-button');
    if (button) button.rect = rect;
    return Boolean(button);
  },
  setLayoutColumnRectsForTest(rects) {
    grid.querySelector = selector => {
      const text = String(selector || '');
      const prefix = '.layout-column[data-slot="';
      const slot = text.startsWith(prefix) && text.endsWith('"]') ? text.slice(prefix.length, -2) : '';
      const rect = slot ? rects[slot] : null;
      if (!rect) return null;
      const node = document.createElement('div');
      node.rect = rect;
      return node;
    };
  },
  defaultLayoutForTest() {
    return globalThis.__layoutTestApi.serialize(defaultLayoutSlots());
  },
  customDragPreviewForTest() {
    return customDragPreview;
  },
  clampToViewport,
  createHoverPopoverForTest(options) {
    return createHoverPopover(options);
  },
  httpsWarningForTest() {
    return document.getElementById('httpsWarning');
  },
  bodyClassListForTest() {
    return document.body.classList;
  },
  documentElementStyleForTest() {
    return document.documentElement.style;
  },
};`, context);
  context.document.__listeners = documentListeners;
  const api = context.__layoutTestApi;
  // Mirror production boot: preload the en catalog so t() resolves to English in tests (fetch is
  // disabled here, so the live applyLocale() never runs).
  try {
    api.i18nSetCatalogForTest('en', JSON.parse(fs.readFileSync('static/locales/en.json', 'utf8')));
  } catch (_) {}
  return api;
}

function fileExplorerClosedOptions() {
  return {sessionStorage: {[FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST]: '0'}};
}

function loadYolomuxWithFileExplorerClosed(search = '', sessions = ['1', '2', '3', '4', '5', '6'], protocol = 'http:', navigatorPlatform = 'Linux x86_64', accessRole = 'admin') {
  return loadYolomux(search, sessions, protocol, navigatorPlatform, accessRole, fileExplorerClosedOptions());
}

function treeKeyEvent(key, target, options = {}) {
  return {
    key,
    target,
    altKey: options.altKey === true,
    ctrlKey: options.ctrlKey === true,
    metaKey: options.metaKey === true,
    shiftKey: options.shiftKey === true,
    preventDefault() { this.defaultPrevented = true; },
    stopPropagation() { this.propagationStopped = true; },
    stopImmediatePropagation() { this.immediatePropagationStopped = true; },
  };
}

function tabElement(session, left, width, top = 0) {
  const tab = new TestElement(session);
  tab.dataset.paneTab = session;
  tab.rect = {left, right: left + width, top, bottom: top + 27, width, height: 27};
  return tab;
}

function tabStrip(tabs) {
  const strip = new TestElement('strip');
  strip.children = tabs;
  strip.rect = {left: 100, right: 406, top: 0, bottom: 28, width: 306, height: 28};
  strip.querySelectorAll = selector => {
    assert.equal(selector, '.pane-tab');
    return tabs;
  };
  return strip;
}

function dragEvent(clientX, session = '9') {
  return {
    clientX,
    clientY: 8,
    target: null,
    dataTransfer: {
      dropEffect: '',
      effectAllowed: '',
      dragImage: null,
      types: ['application/x-yolomux-session'],
      getData(type) {
        if (type === 'application/x-yolomux-session') return JSON.stringify({session, sourceSlot: 'right'});
        return '';
      },
      setData(type, value) {
        this[type] = value;
      },
      setDragImage(node, x, y) {
        this.dragImage = {node, x, y};
      },
    },
    defaultPrevented: false,
    propagationStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
    stopImmediatePropagation() {
      this.propagationStopped = true;
    },
  };
}

function fileDragEvent(target, payload = {path: '/home/test/pic.png', paths: ['/home/test/pic.png'], kind: 'file', name: 'pic.png'}, clientX = 20, clientY = 160) {
  const event = dragEvent(clientX);
  event.clientY = clientY;
  event.target = target;
  event.currentTarget = target;
  event.dataTransfer.types = ['application/x-yolomux-file', 'text/plain'];
  event.dataTransfer.getData = type => {
    if (type === 'application/x-yolomux-file') return JSON.stringify(payload);
    if (type === 'text/plain') return (payload.paths || [payload.path]).filter(Boolean).join('\n');
    return '';
  };
  return event;
}

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : String(status),
    json: async () => payload,
  };
}

function flushAsyncWork() {
  return new Promise(resolve => setImmediate(resolve));
}

function terminalLine(text, isWrapped = false) {
  return {
    isWrapped,
    translateToString() {
      return text;
    },
  };
}

function nestedSlots(api) {
  const slots = api.emptyLayoutSlots();
  slots[api.layoutTreeKey] = api.splitNode(
    'row',
    api.leafNode('left'),
    api.splitNode('column', api.leafNode('slot1'), api.leafNode('slot2'), 62.5),
    37.5,
  );
  slots.left = api.paneStateWithTabs(['5', '6'], '6');
  slots.slot1 = api.paneStateWithTabs(['1'], '1');
  slots.slot2 = api.paneStateWithTabs(['3'], '3');
  return api.normalizeLayoutSlots(slots);
}

function parseUrl(url) {
  const query = url.split('?')[1] || '';
  return new URLSearchParams(query);
}

function canonical(value) {
  return JSON.parse(JSON.stringify(value));
}

function makeFileTree(paths) {
  const tree = new TestElement('file-tree');
  tree.setAttribute('role', 'tree');
  tree.classList.add('file-explorer-tree-panel');
  tree.rect = {left: 0, top: 0, right: 260, bottom: 160, width: 260, height: 160};
  tree.clientHeight = 160;
  const rows = paths.map((path, index) => {
    const row = new TestElement(`row-${index}`);
    row.classList.add('file-tree-row');
    row.dataset.path = path;
    row.dataset.kind = 'file';
    row.rect = {left: 0, top: index * 20, right: 260, bottom: index * 20 + 18, width: 260, height: 18};
    tree.appendChild(row);
    return row;
  });
  return {tree, rows};
}

let __testPass = 0;
let __testFail = 0;
function test(label, fn) {
  // Per-test isolation: a failing assertion is recorded and the rest of the suite still runs, so one
  // failure no longer hides every later check. A pass/fail summary + non-zero exit print at the end.
  try {
    fn();
    __testPass++;
  } catch (error) {
    __testFail++;
    // Fail the process IMMEDIATELY on any failing test. The tail .finally also sets this, but it is
    // skipped whenever the trailing async block does not settle (an await that never resolves) \u2014 which
    // silently let red tests exit 0 and pass the cps gate. Setting it here does not depend on the tail.
    process.exitCode = 1;
    console.error(`\u2717 ${label}: ${(error && error.message) || error}`);
  }
}

async function testAsync(label, fn) {
  try {
    await fn();
    __testPass++;
  } catch (error) {
    __testFail++;
    process.exitCode = 1;
    console.error(`\u2717 ${label}: ${(error && error.message) || error}`);
  }
}

// Gate-integrity watchdog: a single hung `await` in the trailing suite block (e.g. an un-flushed batched
// fs listing) used to leave this IIFE unsettled, so its .finally never ran, the event loop drained, and
// node exited 0 with NO summary — silently masking every later test and passing the cps gate. This timer
// runs in the REAL node context (the harness setTimeout stub only affects vm-eval'd bundle code), so it
// both keeps the process alive to detect the hang AND fails loud instead of false-green. Cleared on settle.
const SUITE_WATCHDOG_MS = 60000;
let suiteWatchdog = null;

function startSuiteWatchdog() {
  if (suiteWatchdog) return;
  suiteWatchdog = setTimeout(() => {
    console.error(`\n✗ layout suite DID NOT SETTLE within ${SUITE_WATCHDOG_MS}ms — a trailing await is hung (commonly an un-flushed batched /api/fs/batch listing; drive flushFileExplorerFsBatchForTest). Failing instead of exiting 0.`);
    process.exitCode = 1;
    process.exit(1);
  }, SUITE_WATCHDOG_MS);
}

function finishSuite() {
  if (suiteWatchdog) clearTimeout(suiteWatchdog);
  suiteWatchdog = null;
  console.log(`\nlayout suite: ${__testPass} passed, ${__testFail} failed`);
  if (__testFail) process.exitCode = 1;
}

async function runSuites(suites) {
  startSuiteWatchdog();
  try {
    for (const runSuite of suites) await runSuite();
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    finishSuite();
  }
}

module.exports = {
  assert,
  fs,
  UI_PINS,
  vm,
  FILE_EXPLORER_OPEN_INTENT_STORAGE_KEY_FOR_TEST,
  DEFAULT_TEST_SETTINGS,
  TestClassList,
  TestStyle,
  testDatasetKeyForAttribute,
  TestElement,
  TestFile,
  TestFormData,
  assertNoStandalonePrBadge,
  assertSingleCiBadge,
  loadYolomux,
  fileExplorerClosedOptions,
  loadYolomuxWithFileExplorerClosed,
  treeKeyEvent,
  tabElement,
  tabStrip,
  dragEvent,
  fileDragEvent,
  jsonResponse,
  flushAsyncWork,
  terminalLine,
  nestedSlots,
  parseUrl,
  canonical,
  makeFileTree,
  test,
  testAsync,
  runSuites,
  finishSuite,
};
