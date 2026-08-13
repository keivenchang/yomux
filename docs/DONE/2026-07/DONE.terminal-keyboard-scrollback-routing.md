# 2026-07-19 Terminal keyboard scrollback routing

- Completed and removed `DOIT.terminal-pgup-scrollback.md`. Plain PgUp/PgDn now follows the same signal-authoritative route as wheel/touch: normal-screen panes page tmux history; alternate-screen panes retain their native app key. Shift+PgUp/PgDn and Cmd/Ctrl+Arrow explicitly force tmux history, and the mobile palette exposes PgUp/PgDn through that shared route.
- Live tmux evidence showed Claude panes are alternate-screen (`alternate_on=1`) while a bash pane is normal-screen (`0`); the GUI contract now uses the live signal, not the application name. Focused Selenium passed (1), all Node layout shards passed, 7772 restarted as leader, and local/ereview HTTPS pings returned 401.
