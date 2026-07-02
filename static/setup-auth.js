const setupStatus = document.getElementById('setupStatus');
const setupSecurity = document.getElementById('setupSecurity');
const setupCheckMs = 1500;

// Localized strings injected server-side by setup_auth_html() (window.__setupStrings). If that inline
// bootstrap is absent, keep the already-localized server-rendered status instead of introducing a
// parallel English fallback table. Strings render verbatim; a casing transform would mangle scripts.
const setupInitialStatus = String(setupStatus?.textContent || '').replace(/[.…]+$/, '');
const SETUP_STRINGS = Object.assign({
  waiting: setupInitialStatus,
  waitingServer: setupInitialStatus,
  authUpdated: setupInitialStatus,
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
