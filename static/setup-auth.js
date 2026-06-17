const setupStatus = document.getElementById('setupStatus');
const setupSecurity = document.getElementById('setupSecurity');
const setupCheckMs = 1500;

// Localized strings injected server-side by setup_auth_html() (window.__setupStrings); the English
// values here are the fallback if the inline script is absent. The strings arrive already correctly
// cased for their locale, so we render them verbatim (the old charAt/toUpperCase transform mangled
// non-Latin scripts) and strip any trailing ASCII/ellipsis dots since the animated dots span follows.
const SETUP_STRINGS = Object.assign({
  waiting: 'Waiting for auth.yaml changes',
  waitingServer: 'Waiting for server',
  authUpdated: 'Auth updated. Reloading…',
}, window.__setupStrings || {});

if (setupSecurity && location.protocol === 'https:') {
  setupSecurity.hidden = true;
}

// DOIT.13: the setup-screen language picker carries the choice in a short-lived cookie (NOT a settings
// write — the setup screen is pre-auth/pre-users) and reloads; request_locale_pref reads the cookie,
// and the post-sign-in save_login_locale makes it permanent. Max-Age caps a stale value.
const setupLocalePicker = document.querySelector('.setup-locale [data-locale-picker]');
if (setupLocalePicker) {
  const toggle = setupLocalePicker.querySelector('[data-locale-toggle]');
  const input = setupLocalePicker.querySelector('[data-locale-input]');
  const options = setupLocalePicker.querySelector('.locale-options');
  const closeLocalePicker = () => {
    if (options) options.hidden = true;
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  };
  toggle?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    if (!options) return;
    const open = options.hidden;
    options.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  options?.addEventListener('click', event => {
    const option = event.target.closest('[data-locale-value]');
    if (!option || !input) return;
    event.preventDefault();
    input.value = option.dataset.localeValue || 'system';
    document.cookie = `yolomux_locale=${encodeURIComponent(input.value)}; Path=/; Max-Age=600; SameSite=Lax`;
    location.reload();
  });
  document.addEventListener('click', event => {
    if (setupLocalePicker.contains(event.target)) return;
    closeLocalePicker();
  });
  setupLocalePicker.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    closeLocalePicker();
    toggle?.focus();
  });
}

function setWaitingStatus(text) {
  if (!setupStatus) return;
  const label = String(text || SETUP_STRINGS.waiting).replace(/[.…]+$/, '');
  setupStatus.textContent = label;
  const dots = document.createElement('span');
  dots.className = 'setup-dots';
  dots.setAttribute('aria-hidden', 'true');
  dots.innerHTML = '<span>.</span><span>.</span><span>.</span>';
  setupStatus.append(dots);
}

async function checkAuthSetup() {
  try {
    const response = await fetch('/api/auth-setup', {cache: 'no-store'});
    if (!response.ok) {
      setWaitingStatus();
      return;
    }
    const payload = await response.json();
    if (payload.setup_required === false) {
      if (setupStatus) setupStatus.textContent = SETUP_STRINGS.authUpdated;
      window.location.reload();
      return;
    }
    setWaitingStatus();
  } catch (_error) {
    setWaitingStatus(SETUP_STRINGS.waitingServer);
  }
}

setInterval(checkAuthSetup, setupCheckMs);
checkAuthSetup();
