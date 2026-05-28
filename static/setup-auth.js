const setupStatus = document.getElementById('setupStatus');
const setupSecurity = document.getElementById('setupSecurity');
const setupCheckMs = 1500;

if (setupSecurity && location.protocol === 'https:') {
  setupSecurity.hidden = true;
}

function setWaitingStatus(text = 'waiting for auth.yaml changes...') {
  if (!setupStatus) return;
  const label = text.replace(/\.+$/, '');
  setupStatus.innerHTML = `${label.charAt(0).toUpperCase()}${label.slice(1)}<span class="setup-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>`;
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
      if (setupStatus) setupStatus.textContent = 'Auth updated. Reloading...';
      window.location.reload();
      return;
    }
    setWaitingStatus();
  } catch (_error) {
    setWaitingStatus('waiting for server...');
  }
}

setInterval(checkAuthSetup, setupCheckMs);
checkAuthSetup();
