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
