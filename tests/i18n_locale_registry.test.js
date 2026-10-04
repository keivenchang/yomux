// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

const assert = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const {makeCatalogT, runSuites, sourceBetween, testAsync} = require('./browser_helpers/layout_test_helper');

const source = fs.readFileSync('static_src/js/yomux/05_i18n.js', 'utf8');
const layoutSource = fs.readFileSync('static_src/js/yomux/20_layout_state.js', 'utf8');
const menuSource = fs.readFileSync('static_src/js/yomux/30_app_menus.js', 'utf8');
const terminalSource = [
  'static_src/js/yomux/99_terminal_boot.js',
  'static_src/js/yomux/99_terminal_shortcuts_boot.js',
].map(path => fs.readFileSync(path, 'utf8')).join('\n');
const englishCatalog = JSON.parse(fs.readFileSync('static_src/locales/en.json', 'utf8'));

class ChromeNode {
  constructor(classes = []) {
    this.attributes = {};
    this.classNames = new Set(classes);
    this.dataset = {};
    this.hidden = false;
    this.textContent = '';
    this.title = '';
    this.classList = {contains: value => this.classNames.has(value)};
  }

  setAttribute(name, value) { this.attributes[name] = String(value); }
}

function testChromeOwnerBehavior() {
  const strings = {
    'app.latencyTitle': 'Latencia',
    'app.menusAria': 'Menus',
    'brand.version': 'Marca {version}',
    'brand.wordmark.initial': 'Y',
    'brand.wordmark.second': 'O',
    'brand.marker': 'UE',
    'common.close': 'Cerrar',
    'common.loading': 'Cargando',
    'menu.file.logout': 'Salir',
    'notify.delivery.summary': 'Dentro {inApp}; sistema {system}',
    'state.off': 'no',
    'notify.state.on': 'si',
    'pref.section.notifications': 'Avisos',
    'transcript.tailTitle': 'Cola {session}',
    'update.badgeAria': 'Actualizacion disponible',
    'update.badgeAvailable': 'Actualizacion {target}',
    'update.badgeLabel': 'actualizar',
    'update.badgeTitle': 'Actualizacion oculta',
  };
  const t = makeCatalogT(strings);

  const brand = new ChromeNode();
  const brandYo = new ChromeNode();
  const brandLo = new ChromeNode();
  const sessionButtons = new ChromeNode();
  const latencyMeter = new ChromeNode();
  const logoutButton = new ChromeNode();
  const menuContext = {
    bootstrap: {version: '1.2.3'},
    document: {
      querySelectorAll(selector) {
        if (selector === '.brand-title .brand-mark') return [brandYo];
        if (selector === '.brand-title .brand-second') return [brandLo];
        if (selector === '.brand-title') return [brand];
        return [];
      },
    },
    latencyMeter,
    logoutButton,
    sessionButtons,
    t,
    updateBrandTitles() {},
  };
  vm.createContext(menuContext);
  vm.runInContext(
    `${sourceBetween(menuSource, 'function renderBrandWordmark()', 'function createAppMenuBar()')}\nthis.api = {renderBrandWordmark, renderTopbarStaticChrome};`,
    menuContext,
  );
  menuContext.api.renderBrandWordmark();
  menuContext.api.renderTopbarStaticChrome();
  assert.equal(brandYo.textContent, 'Y');
  assert.equal(brandLo.textContent, 'O');
  assert.equal(brand.attributes['aria-label'], 'Marca 1.2.3');
  assert.equal(sessionButtons.attributes['aria-label'], 'Menus');
  assert.equal(latencyMeter.title, 'Latencia');
  assert.equal(logoutButton.textContent, 'Salir');
  assert.equal(logoutButton.title, 'Salir');
  assert.equal(logoutButton.attributes['aria-label'], 'Salir');

  const notifyToggle = new ChromeNode();
  const notificationDeliveryPopover = new ChromeNode();
  let openPopoverCalls = 0;
  const notifyContext = {
    notificationDeliveryEnabled(channel) { return channel !== 'system'; },
    notificationDeliveryPopover,
    notifyToggle,
    openNotificationDeliveryPopover() { openPopoverCalls += 1; },
    closeNotificationDeliveryPopover() {},
    syncPressedButton(button, _pressed, options) { button.textContent = options.labelOn; },
    t,
  };
  vm.createContext(notifyContext);
  vm.runInContext(
    `${sourceBetween(layoutSource, 'function renderNotifyToggle()', 'function refreshNotificationDeliveryMenuChecks()')}\nthis.api = {renderNotifyToggle};`,
    notifyContext,
  );
  notifyContext.api.renderNotifyToggle();
  assert.equal(notifyToggle.textContent, 'Avisos');
  assert.equal(notifyToggle.title, 'Dentro si; sistema no');
  assert.equal(openPopoverCalls, 1, 'an open notification popover is rebuilt in the new locale');

  const badge = new ChromeNode();
  badge.dataset.updateTarget = '2.0.0';
  const badgeContext = {
    document: {querySelector() { return badge; }},
    selfUpdateAvailableTarget: '',
    t,
  };
  vm.createContext(badgeContext);
  vm.runInContext(
    `${sourceBetween(terminalSource, 'function renderUpdateBadgeChrome()', 'function markSelfUpdateReloadPending(')}\nthis.api = {renderUpdateBadgeChrome};`,
    badgeContext,
  );
  badgeContext.api.renderUpdateBadgeChrome();
  assert.equal(badge.textContent, 'actualizar');
  assert.equal(badge.attributes['aria-label'], 'Actualizacion disponible');
  assert.equal(badge.title, 'Actualizacion  (2.0.0)');

  const modal = new ChromeNode(['open']);
  modal.dataset.modalKind = 'context';
  modal.dataset.modalSession = '8001';
  const modalTitle = new ChromeNode();
  const modalBody = new ChromeNode();
  modalBody.dataset.localeTextKey = 'common.loading';
  const closeModal = new ChromeNode();
  const modalNodes = {modal, modalTitle, modalBody, closeModal};
  const modalContext = {
    Boolean,
    CLS: {open: 'open'},
    document: {getElementById(id) { return modalNodes[id] || null; }},
    relocalizeShareModal() { throw new Error('wrong modal dispatcher'); },
    sessionLabel(session) { return session; },
    showAboutModal() { throw new Error('wrong modal dispatcher'); },
    t,
  };
  vm.createContext(modalContext);
  vm.runInContext(
    `${sourceBetween(terminalSource, 'function relocalizeModalChrome(', 'function globalShortcutTargetAllowsAppAction(')}\nthis.api = {relocalizeModalChrome};`,
    modalContext,
  );
  modalContext.api.relocalizeModalChrome();
  assert.equal(closeModal.title, 'Cerrar');
  assert.equal(closeModal.attributes['aria-label'], 'Cerrar');
  assert.equal(modalTitle.textContent, 'Cola 8001');
  assert.equal(modalBody.textContent, 'Cargando');
}

const attributes = {};
const fetches = [];
let transportWarningRenders = 0;
let tabMetaToggleRenders = 0;
let refreshMetaChromeRenders = 0;
let topbarStaticChromeRenders = 0;
let notifyToggleRenders = 0;
let brandWordmarkRenders = 0;
let updateBadgeChromeRenders = 0;
let modalChromeRenders = 0;
const context = {
  bootstrap: {
    locale: 'he',
    localeRegistry: {
      fallback: 'en',
      pseudo: 'en-XA',
      systemPreference: 'system',
      systemLocale: 'he',
      locales: [
        {id: 'en', endonym: 'English', direction: 'ltr'},
        {id: 'he', endonym: 'עברית', direction: 'rtl'},
        {id: 'pt-BR', endonym: 'Português (BR)', direction: 'ltr'},
      ],
    },
    strings: {
      en: {
        'pref.general.language.system': 'System',
        'pref.general.language.pseudo': 'Pseudo',
      },
      he: {},
    },
  },
  navigator: {language: 'en-US'},
  document: {
    documentElement: {setAttribute(name, value) { attributes[name] = value; }},
    getElementById() { return null; },
  },
  fetch: async url => {
    fetches.push(String(url));
    return {ok: true, async json() { return {}; }};
  },
  panelNodes: new Map(),
  tabTypeForItem() { return null; },
  renderTransportWarning() { transportWarningRenders += 1; },
  renderTabMetaToggle() { tabMetaToggleRenders += 1; },
  refreshMetaButtonChrome() { refreshMetaChromeRenders += 1; },
  renderTopbarStaticChrome() { topbarStaticChromeRenders += 1; },
  renderNotifyToggle() { notifyToggleRenders += 1; },
  renderBrandWordmark() { brandWordmarkRenders += 1; },
  renderUpdateBadgeChrome() { updateBadgeChromeRenders += 1; },
  relocalizeModalChrome() { modalChromeRenders += 1; },
  console,
  Intl,
  Map,
  Set,
};
vm.createContext(context);
vm.runInContext(
  `${source}\nthis.api = {applyLocale, i18nActiveLocaleId, i18nIsRtl, i18nLocaleChoices, i18nNormalizeLocale, i18nSupportedLocales, resolveLocalePref};`,
  context,
);

async function runI18nLocaleRegistrySuite() {
  await testAsync('plural formatting reuses locale rules across tree labels without caching translations', async () => {
    const constructions = [];
    const pluralContext = {
      bootstrap: {
        locale: 'en',
        localeRegistry: {fallback: 'en', locales: [{id: 'en'}, {id: 'fr'}]},
        strings: {
          en: {'row.one': '{count} row', 'row.other': '{count} rows'},
          fr: {'row.one': '{count} ligne', 'row.other': '{count} lignes'},
        },
      },
      Intl: {PluralRules: class extends Intl.PluralRules {
        constructor(locale) { super(locale); constructions.push(locale); }
      }},
    };
    vm.createContext(pluralContext);
    vm.runInContext(`${source}\nthis.plural = tPlural;`, pluralContext);
    for (let count = 0; count < 2210; count += 1) {
      assert.equal(pluralContext.plural('row', count), `${count} ${count === 1 ? 'row' : 'rows'}`);
    }
    assert.deepStrictEqual(constructions, ['en'], 'one formatter serves the entire tree');
    vm.runInContext("i18nActiveLocale = 'fr';", pluralContext);
    assert.equal(pluralContext.plural('row', 0), '0 ligne');
    assert.equal(pluralContext.plural('row', 2), '2 lignes');
    vm.runInContext("i18nCatalogs.get('fr')['row.other'] = '{count} nouvelles';", pluralContext);
    assert.equal(pluralContext.plural('row', 2), '2 nouvelles');
    vm.runInContext("i18nActiveLocale = 'en';", pluralContext);
    assert.equal(pluralContext.plural('row', 1), '1 row');
    assert.deepStrictEqual(constructions, ['en', 'fr']);
  });
  await testAsync('locale registry normalizes locales and relocalizes global chrome', async () => {
  const api = context.api;
  assert.equal(englishCatalog['app.latencyTitle'], 'Browser ↔ YOmux round-trip latency (ms)', 'latency hover explains browser round-trip units');
  assert.deepEqual([...api.i18nSupportedLocales()], ['en', 'he', 'pt-BR']);
  assert.deepEqual(
    [...api.i18nLocaleChoices()].map(choice => [choice.value, choice.label]),
    [['system', 'System'], ['en', 'English'], ['he', 'עברית'], ['pt-BR', 'Português (BR)'], ['en-XA', 'Pseudo']],
  );
  assert.equal(api.resolveLocalePref('system'), 'he', 'system uses the server-resolved secondary Accept-Language choice');
  assert.equal(api.resolveLocalePref('SYSTEM'), 'he', 'system preference matching is case-insensitive');
  assert.equal(api.i18nNormalizeLocale('PT-br'), 'pt-BR');
  assert.equal(api.i18nNormalizeLocale('../../he'), 'en');
  assert.equal(api.i18nIsRtl('HE'), true);
  assert.equal(api.i18nIsRtl('../../he'), false);

  await api.applyLocale('PT-br');
  assert.equal(api.i18nActiveLocaleId(), 'pt-BR');
  assert.equal(attributes.lang, 'pt-BR');
  assert.equal(attributes.dir, 'ltr');
  assert.equal(transportWarningRenders, 1, 'locale changes repaint one-shot transport warning chrome through the shared global registry');
  assert.equal(tabMetaToggleRenders, 1, 'locale changes repaint the one-shot tab metadata toggle through the shared global registry');
  assert.equal(refreshMetaChromeRenders, 1, 'locale changes repaint the one-shot Refresh control through the shared global registry');
  assert.equal(topbarStaticChromeRenders, 1, 'locale changes repaint the server-rendered topbar labels through their shared owner');
  assert.equal(notifyToggleRenders, 1, 'locale changes repaint the notification delivery control through its existing owner');
  assert.equal(brandWordmarkRenders, 1, 'locale changes repaint the brand text and accessibility label through one owner');
  assert.equal(updateBadgeChromeRenders, 1, 'locale changes repaint the update badge through one owner');
  assert.equal(modalChromeRenders, 1, 'locale changes repaint the active modal through one dispatch owner');
  assert.ok(fetches.includes('/static/locales/pt-BR.json'));

  const fetchCount = fetches.length;
  await api.applyLocale('../../outside');
  assert.equal(api.i18nActiveLocaleId(), 'en');
  assert.equal(fetches.length, fetchCount, 'an invalid locale falls back to the seeded source catalog without an unsafe fetch');

  await api.applyLocale('EN-xa');
  assert.equal(api.i18nActiveLocaleId(), 'en-XA');
  assert.ok(fetches.includes('/static/locales/en-XA.json'));

  assert.doesNotMatch(source, /navigator\.language|startsWith\('zh'\)|base === 'ar'/, 'the client has no parallel language-tag or RTL classifier');
  testChromeOwnerBehavior();
  });
}

module.exports = {runI18nLocaleRegistrySuite};

if (require.main === module) {
  runSuites([runI18nLocaleRegistrySuite]);
}
