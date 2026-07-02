// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

document.querySelectorAll('[data-locale-picker]').forEach(picker => {
  const toggle = picker.querySelector('[data-locale-toggle]');
  const input = picker.querySelector('[data-locale-input]');
  const options = picker.querySelector('.locale-options');
  if (!toggle || !input || !options) return;
  const close = () => {
    options.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  };
  toggle.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    const open = options.hidden;
    document.querySelectorAll('.locale-options').forEach(node => {
      if (node !== options) node.hidden = true;
    });
    options.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  options.addEventListener('click', event => {
    const option = event.target.closest('[data-locale-value]');
    if (!option) return;
    event.preventDefault();
    input.value = option.dataset.localeValue || 'system';
    document.cookie = `yolomux_locale=${encodeURIComponent(input.value)}; Path=/; Max-Age=600; SameSite=Lax`;
    location.reload();
  });
  picker.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    close();
    toggle.focus();
  });
  document.addEventListener('click', event => {
    if (!picker.contains(event.target)) close();
  });
});
