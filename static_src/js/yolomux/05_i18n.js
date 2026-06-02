// i18n runtime: a tiny key-based string catalog with a t() helper. Catalogs are fetched from
// /static/locales/<locale>.json (all-static-fetch delivery); `en` is the source-of-truth fallback.
// This partial loads right after the bootstrap (00) and before everything else (10+), so all code
// can call t(). Keys are dotted ids (e.g. pref.appearance.theme.label); values may hold {tokens}.

let i18nActiveLocale = (typeof bootstrap === 'object' && bootstrap && bootstrap.locale) ? String(bootstrap.locale) : 'en';
const i18nFallbackLocale = 'en';
const i18nCatalogs = new Map();  // locale -> {dottedKey: string}

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

function rerenderForLocale() {
  // Re-render the surfaces that contain localized text. Guarded so this is safe at any load order.
  if (typeof renderPreferencesPanels === 'function') renderPreferencesPanels();
  if (typeof renderSessionButtons === 'function') renderSessionButtons();
  if (typeof renderPaneTabStrips === 'function') renderPaneTabStrips();
}
