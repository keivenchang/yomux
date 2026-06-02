// i18n runtime: a tiny key-based string catalog with a t() helper. Catalogs are fetched from
// /static/locales/<locale>.json (all-static-fetch delivery); `en` is the source-of-truth fallback.
// This partial loads right after the bootstrap (00) and before everything else (10+), so all code
// can call t(). Keys are dotted ids (e.g. pref.appearance.theme.label); values may hold {tokens}.

let i18nActiveLocale = (typeof bootstrap === 'object' && bootstrap && bootstrap.locale) ? String(bootstrap.locale) : 'en';
const i18nFallbackLocale = 'en';
const i18nCatalogs = new Map();  // locale -> {dottedKey: string}
// DOIT.8: seed from the INLINED bootstrap catalogs (active locale + en fallback) so t() resolves
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
  const value = i18nResolve(`${key}.${category}`) ?? i18nResolve(`${key}.other`);
  return i18nInterpolate(value == null ? String(key) : value, {...(params || {}), count: number});
}

function i18nActiveLocaleId() {
  return i18nActiveLocale;
}

function i18nSetCatalogForTest(locale, catalog) {
  i18nCatalogs.set(locale, catalog || {});
}

async function i18nLoadCatalog(locale) {
  if (!locale) return null;
  if (i18nCatalogs.has(locale)) return i18nCatalogs.get(locale);
  try {
    const response = await fetch(`/static/locales/${encodeURIComponent(locale)}.json`, {cache: 'no-store'});
    if (!response.ok) return null;
    const data = await response.json();
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      i18nCatalogs.set(locale, data);
      return data;
    }
  } catch (_) {}
  return null;
}

// Switch the active locale: ensure the en fallback + the active catalog are loaded, then re-render
// the localized surfaces. Safe to call before the render functions exist (guarded).
async function applyLocale(locale) {
  const next = String(locale || i18nFallbackLocale);
  await i18nLoadCatalog(i18nFallbackLocale);
  if (next !== i18nFallbackLocale) await i18nLoadCatalog(next);
  i18nActiveLocale = next;
  rerenderForLocale();
}

// The real (non-pseudo) locales that ship a catalog, most-specific first. 'system' resolves against
// navigator.language to one of these. Add new locales here as their catalogs ship.
function i18nSupportedLocales() {
  return ['zh-Hant', 'zh-Hans', 'es', 'ja', 'en'];
}

// The language-switcher choices (Preferences picker + topbar switcher). Endonyms stay in their own
// script (never translated); 'system'/pseudo are localized. Traditional Chinese before Simplified.
function i18nLocaleChoices() {
  return [
    {value: 'system', label: t('pref.general.language.system')},
    {value: 'en', label: 'English'},
    {value: 'zh-Hant', label: '繁體中文'},
    {value: 'zh-Hans', label: '简体中文'},
    {value: 'es', label: 'Español'},
    {value: 'ja', label: '日本語'},
    {value: 'en-XA', label: t('pref.general.language.pseudo')},
  ];
}

// Resolve a `general.language` pref to a concrete locale. "system" matches navigator.language against
// the locales that ship a catalog: zh-TW/HK/MO/Hant -> zh-Hant, other zh-* -> zh-Hans, else by language
// prefix, falling back to en.
function resolveLocalePref(pref) {
  const value = String(pref || 'system');
  if (value !== 'system') return value;
  const nav = (typeof navigator === 'object' && navigator && navigator.language) ? String(navigator.language).toLowerCase() : 'en';
  if (nav.startsWith('zh')) return /hant|\b(tw|hk|mo)\b|-tw|-hk|-mo/.test(nav) ? 'zh-Hant' : 'zh-Hans';
  for (const loc of i18nSupportedLocales()) {
    const base = loc.toLowerCase().split('-')[0];
    if (nav === loc.toLowerCase() || nav === base || nav.startsWith(base + '-')) return loc;
  }
  return 'en';
}

function rerenderForLocale() {
  // DOIT.6 #50: force-re-render EVERY localized surface so a language switch repaints the open UI on
  // the same interaction. Guarded so this is safe at any load order. Preferences must be forced past
  // the active-control guard (the language <select> is the active control when the switch fires).
  if (typeof renderPreferencesPanels === 'function') renderPreferencesPanels({force: true});
  if (typeof renderSessionButtons === 'function') renderSessionButtons();  // rebuilds the app menu bar
  if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
  if (typeof renderInfoPanel === 'function') renderInfoPanel();
  if (typeof renderYoagentPanel === 'function') renderYoagentPanel({preserveDraft: true});
  if (typeof renderBrandWordmark === 'function') renderBrandWordmark();
}
