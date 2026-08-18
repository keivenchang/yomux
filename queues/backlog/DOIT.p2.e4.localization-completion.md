# DOIT.p2.e4.localization-completion.md - Finish Structured Browser Localization

## Goal

Localize the auxiliary tmux-wall shell and remaining upload, filesystem, search, run-history, transcript, and API errors while retaining raw OS, Git, tmux, and model text only as diagnostic detail.

## Plan

- [ ] Inventory every browser-visible literal and error envelope, classify structured user text versus raw diagnostic detail, and map each structured message to one locale key and argument schema.
- [ ] Add translated catalog entries locale by locale; do not seed untranslated English as if coverage were complete.
- [ ] Preserve typed error identity, argument escaping, locale switching, fallback visibility, pluralization, and tmux-wall/browser parity.

## Done Criteria

- [ ] Negative source/runtime inventories find no unregistered browser-visible structured literals in the named surfaces, and each supported locale reports exact coverage rather than fabricated completion.
- [ ] Locale Node/browser tests, generated assets, the canonical gate, and restarted language-switch/error journeys pass.
