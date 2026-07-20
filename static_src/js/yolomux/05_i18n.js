// i18n runtime: a tiny key-based string catalog with a t() helper. Catalogs are fetched from
// /static/locales/<locale>.json (all-static-fetch delivery); `en` is the source-of-truth fallback.
// This partial loads right after the bootstrap (00) and before everything else (10+), so all code
// can call t(). Keys are dotted ids (e.g. pref.appearance.theme.label); values may hold {tokens}.

function i18nRegistryFromBootstrap() {
  const boot = typeof bootstrap === 'object' && bootstrap ? bootstrap : {};
  const raw = boot.localeRegistry && typeof boot.localeRegistry === 'object' ? boot.localeRegistry : {};
  const seen = new Set();
  const locales = (Array.isArray(raw.locales) ? raw.locales : []).map(item => {
    const source = item && typeof item === 'object' ? item : {};
    const id = String(source.id || '').trim();
    if (!id || seen.has(id.toLowerCase())) return null;
    seen.add(id.toLowerCase());
    return {
      id,
      endonym: String(source.endonym || id),
      direction: String(source.direction || '').toLowerCase() === 'rtl' ? 'rtl' : 'ltr',
    };
  }).filter(Boolean);
  const byCasefold = new Map(locales.map(spec => [spec.id.toLowerCase(), spec.id]));
  const bootLocale = String(boot.locale || '').trim();
  const catalogLocale = Object.keys(boot.strings && typeof boot.strings === 'object' ? boot.strings : {})[0] || '';
  const fallbackCandidate = String(raw.fallback || '').trim();
  const fallback = byCasefold.get(fallbackCandidate.toLowerCase()) || locales[0]?.id || bootLocale || catalogLocale;
  const pseudo = String(raw.pseudo || '').trim();
  const systemPreference = String(raw.systemPreference || '').trim();
  const allowed = new Map([...byCasefold, ...(pseudo ? [[pseudo.toLowerCase(), pseudo]] : [])]);
  const systemCandidate = String(raw.systemLocale || '').trim();
  const systemLocale = allowed.get(systemCandidate.toLowerCase()) || fallback;
  return {fallback, pseudo, systemPreference, systemLocale, locales, allowed};
}

const i18nLocaleRegistry = i18nRegistryFromBootstrap();
const i18nFallbackLocale = i18nLocaleRegistry.fallback;
function i18nNormalizeLocale(value, options = {}) {
  const text = String(value || '').trim();
  if (options.allowSystem === true && text.toLowerCase() === i18nLocaleRegistry.systemPreference.toLowerCase()) {
    return i18nLocaleRegistry.systemPreference;
  }
  return i18nLocaleRegistry.allowed.get(text.toLowerCase()) || i18nFallbackLocale;
}

let i18nActiveLocale = i18nNormalizeLocale(typeof bootstrap === 'object' && bootstrap ? bootstrap.locale : '');
const i18nCatalogs = new Map();  // locale -> {dottedKey: string}
let i18nApplyLocaleRequestId = 0;
// seed from the INLINED bootstrap catalogs (active locale + en fallback) so t() resolves
// SYNCHRONOUSLY on the very first render — the menu bar/tabs/wordmark paint at boot before any fetch.
if (typeof bootstrap === 'object' && bootstrap && bootstrap.strings && typeof bootstrap.strings === 'object') {
  for (const [loc, catalog] of Object.entries(bootstrap.strings)) {
    if (catalog && typeof catalog === 'object') i18nCatalogs.set(loc, catalog);
  }
}

function i18nCatalogValue(locale, key) {
  const catalog = i18nCatalogs.get(locale);
  const value = catalog ? catalog[key] : undefined;
  return typeof value === 'string' ? value : null;
}

// Resolve a key: active locale -> en fallback -> null (caller shows the key itself).
function i18nResolve(key) {
  return i18nCatalogValue(i18nActiveLocale, key)
    ?? (i18nActiveLocale === i18nFallbackLocale ? null : i18nCatalogValue(i18nFallbackLocale, key));
}

function i18nInterpolate(text, params) {
  if (!params) return text;
  return String(text).replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
}

// Translate KEY, interpolating {params}. Falls back active -> en -> the key itself (never blank).
function t(key, params) {
  const value = i18nResolve(key);
  return i18nInterpolate(value == null ? String(key) : value, params);
}

// Plural form via Intl.PluralRules. Catalog keys are `${key}.${category}` (e.g. files.changed.one /
// files.changed.other); `count` is always available as a {count} token.
function tPlural(key, count, params) {
  const number = Number(count) || 0;
  let category = 'other';
  try { category = new Intl.PluralRules(i18nActiveLocale).select(number); } catch (_) {}
  const value = i18nCatalogValue(i18nActiveLocale, `${key}.${category}`)
    ?? i18nCatalogValue(i18nActiveLocale, `${key}.other`)
    ?? i18nCatalogValue(i18nFallbackLocale, `${key}.${category}`)
    ?? i18nCatalogValue(i18nFallbackLocale, `${key}.other`);
  return i18nInterpolate(value == null ? String(key) : value, {...(params || {}), count: number});
}

function i18nActiveLocaleId() {
  return i18nActiveLocale;
}

// Phase 3: render a relative time in the active locale's native phrasing via
// Intl.RelativeTimeFormat. Falls back through local catalog strings if the browser API is unavailable.
function relativeTimeFormat(secondsAgo) {
  const sec = Math.max(0, Math.round(Number(secondsAgo) || 0));
  let value;
  let unit;
  if (sec < 60) { value = sec; unit = 'second'; }
  else if (sec < 3600) { value = Math.round(sec / 60); unit = 'minute'; }
  else if (sec < 86400) { value = Math.round(sec / 3600); unit = 'hour'; }
  else if (sec < 604800) { value = Math.round(sec / 86400); unit = 'day'; }
  else if (sec < 2629800) { value = Math.round(sec / 604800); unit = 'week'; }
  else if (sec < 31557600) { value = Math.round(sec / 2629800); unit = 'month'; }
  else { value = Math.round(sec / 31557600); unit = 'year'; }
  try {
    return new Intl.RelativeTimeFormat(i18nActiveLocale, {numeric: 'always'}).format(-value, unit);
  } catch (_) {
    if (unit === 'second') return new Intl.RelativeTimeFormat('en', {numeric: 'always'}).format(-value, unit);
    return t(`relative.${unit}`, {count: value});
  }
}

function compactRelativeTimeFormat(secondsAgo) {
  const sec = Math.max(0, Math.round(Number(secondsAgo) || 0));
  let value;
  let unit;
  if (sec < 3600) { value = Math.max(1, Math.round(sec / 60)); unit = 'minute'; }
  else if (sec < 86400) { value = Math.max(1, Math.round(sec / 3600)); unit = 'hour'; }
  else if (sec < 604800) { value = Math.max(1, Math.round(sec / 86400)); unit = 'day'; }
  else if (sec < 2629800) { value = Math.max(1, Math.round(sec / 604800)); unit = 'week'; }
  else if (sec < 31557600) { value = Math.max(1, Math.round(sec / 2629800)); unit = 'month'; }
  else { value = Math.max(1, Math.round(sec / 31557600)); unit = 'year'; }
  try {
    return new Intl.RelativeTimeFormat(i18nActiveLocale, {numeric: 'always', style: 'short'})
      .format(-value, unit)
      .replace(/\./g, '');
  } catch (_) {
    return relativeTimeFormat(secondsAgo);
  }
}

function normalizeDateTimeHourCycle(value) {
  return String(value || '') === '12' ? '12' : '24';
}

function dateTimeFormatOptionsForHourCycle(options = {}) {
  const next = {...(options || {})};
  if (!Object.prototype.hasOwnProperty.call(next, 'hour')) return next;
  if (Object.prototype.hasOwnProperty.call(next, 'hour12')) return next;
  if (Object.prototype.hasOwnProperty.call(next, 'hourCycle')) return next;
  if (normalizeDateTimeHourCycle(dateTimeHourCycle) === '12') next.hour12 = true;
  else next.hourCycle = 'h23';
  return next;
}

function localizedDateTimeFormat(timestampSeconds, options = {}) {
  const value = Number(timestampSeconds || 0);
  if (!value) return '';
  const date = new Date(value * 1000);
  const dateTimeOptions = dateTimeFormatOptionsForHourCycle(options);
  try {
    return new Intl.DateTimeFormat(i18nActiveLocale, dateTimeOptions).format(date);
  } catch (_) {
    try {
      return new Intl.DateTimeFormat(i18nFallbackLocale, dateTimeOptions).format(date);
    } catch (_) {
      return '';
    }
  }
}

function localizedExactDateTimeFormat(timestampSeconds) {
  const value = Number(timestampSeconds || 0);
  if (!value) return '';
  if (!String(i18nActiveLocale || '').toLowerCase().startsWith('en')) {
    return localizedDateTimeFormat(value, {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }
  const date = new Date(value * 1000);
  const pad = number => String(number).padStart(2, '0');
  let hour = date.getHours();
  let suffix = '';
  if (normalizeDateTimeHourCycle(dateTimeHourCycle) === '12') {
    suffix = hour >= 12 ? ' PM' : ' AM';
    hour = hour % 12 || 12;
  }
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(hour)}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${suffix}`;
}

function i18nSetCatalogForTest(locale, catalog) {
  i18nCatalogs.set(locale, catalog || {});
}

async function i18nLoadCatalog(locale) {
  const normalized = i18nNormalizeLocale(locale);
  if (i18nCatalogs.has(normalized)) return i18nCatalogs.get(normalized);
  try {
    const response = await fetch(`/static/locales/${encodeURIComponent(normalized)}.json`, {cache: 'no-store'});
    if (!response.ok) return null;
    const data = await response.json();
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      i18nCatalogs.set(normalized, data);
      return data;
    }
  } catch (_) {}
  return null;
}

// Switch the active locale: ensure the en fallback + the active catalog are loaded, then re-render
// the localized surfaces. Safe to call before the render functions exist (guarded).
async function applyLocale(locale) {
  const requestId = ++i18nApplyLocaleRequestId;
  const next = i18nNormalizeLocale(locale);
  if (typeof preferenceSections === 'function' && typeof setCollapsedPreferenceSections === 'function') {
    setCollapsedPreferenceSections(collapsedPreferenceSections, {sections: preferenceSections(), persist: true});
  }
  await i18nLoadCatalog(i18nFallbackLocale);
  if (next !== i18nFallbackLocale) await i18nLoadCatalog(next);
  if (requestId !== i18nApplyLocaleRequestId) return;
  i18nActiveLocale = next;
  // Phase 2: flip the document direction so RTL locales (ar) mirror the whole layout.
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.setAttribute('dir', i18nIsRtl(next) ? 'rtl' : 'ltr');
    document.documentElement.setAttribute('lang', next);
  }
  rerenderForLocale({localeChange: true});
  if (typeof scheduleShareAppearancePublish === 'function') scheduleShareAppearancePublish();
  if (typeof scheduleSharePopupLayerPublish === 'function') scheduleSharePopupLayerPublish({immediate: true});
}

// The real (non-pseudo) locales in the Python registry's product-priority order.
function i18nSupportedLocales() {
  return i18nLocaleRegistry.locales.map(spec => spec.id);
}

function i18nIsRtl(locale) {
  const normalized = i18nNormalizeLocale(locale);
  return i18nLocaleRegistry.locales.find(spec => spec.id === normalized)?.direction === 'rtl';
}

// The language-switcher choices (Preferences picker + topbar switcher) project the Python registry.
function i18nLocaleChoices() {
  const choices = [
    {value: i18nLocaleRegistry.systemPreference, label: t('pref.general.language.system')},
    ...i18nLocaleRegistry.locales.map(spec => ({value: spec.id, label: spec.endonym})),
  ];
  if (i18nLocaleRegistry.pseudo) choices.push({value: i18nLocaleRegistry.pseudo, label: t('pref.general.language.pseudo')});
  return choices;
}

// Resolve a `general.language` pref through the server-resolved registry value.
function resolveLocalePref(pref) {
  const value = String(pref || i18nLocaleRegistry.systemPreference);
  const normalized = i18nNormalizeLocale(value, {allowSystem: true});
  return normalized === i18nLocaleRegistry.systemPreference ? i18nLocaleRegistry.systemLocale : normalized;
}

const localeGlobalSurfaceHooks = Object.freeze([
  options => typeof renderSessionButtons === 'function' && renderSessionButtons({force: true}),
  options => typeof renderTabMetaToggle === 'function' && renderTabMetaToggle(),
  options => typeof renderPaneTabStrips === 'function' && renderPaneTabStrips(),
  options => typeof refreshActivePanelHeaders === 'function' && refreshActivePanelHeaders(),
  options => typeof relocalizeKeyboardShortcutsOverlay === 'function' && relocalizeKeyboardShortcutsOverlay(),
  options => typeof refreshOpenEventLogs === 'function' && refreshOpenEventLogs(),
  options => options.localeChange === true && typeof refreshActivitySummary === 'function'
    && refreshActivitySummary({force: true, silent: true, localeChange: true}),
  options => typeof renderTopbarStaticChrome === 'function' && renderTopbarStaticChrome(),
  options => typeof renderNotifyToggle === 'function' && renderNotifyToggle(),
  options => typeof renderBrandWordmark === 'function' && renderBrandWordmark(),
  options => typeof renderUpdateBadgeChrome === 'function' && renderUpdateBadgeChrome(),
  options => typeof updateDocumentTitle === 'function' && updateDocumentTitle(),
  options => typeof renderTransportWarning === 'function' && renderTransportWarning(),
  options => typeof refreshMetaButtonChrome === 'function' && refreshMetaButtonChrome(),
  options => typeof relocalizeModalChrome === 'function' && relocalizeModalChrome(),
  options => typeof renderFileExplorerChangesPanels === 'function' && renderFileExplorerChangesPanels({force: true}),
  options => typeof relocalizeFileUploadDropLabels === 'function' && relocalizeFileUploadDropLabels(),
]);

function relocalizeMountedPanels(options = {}) {
  if (!(panelNodes instanceof Map)) return;
  for (const [item, panel] of [...panelNodes.entries()]) {
    const relocalize = tabTypeForItem(item)?.relocalize;
    if (typeof relocalize === 'function') relocalize(item, panel, options);
  }
}

function rerenderForLocale(options = {}) {
  // Mounted panel types own their relocalization path through TAB_TYPES. Global chrome lives in the
  // separate registry below, so adding a surface cannot require another bespoke branch here.
  relocalizeMountedPanels(options);
  localeGlobalSurfaceHooks.forEach(run => run(options));
}

function rerenderDateTimeFormatSurfaces() {
  relocalizeMountedPanels({dateTimeFormatChange: true});
  if (typeof refreshOpenEventLogs === 'function') refreshOpenEventLogs();
  if (typeof renderFileExplorerChangesPanels === 'function') renderFileExplorerChangesPanels({force: true});
}
