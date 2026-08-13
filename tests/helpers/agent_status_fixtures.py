"""Agent-status fixture markup shared by browser-layout journeys."""

CLAUDE_WORKING_ICON_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true">
  <rect width="24" height="24" rx="5.5" fill="#cf7554"/>
  <g fill="#fff7f1">
    <path d="M11.1 2.4h1.8l1.1 7.9-2 .6-2-.6 1.1-7.9z"/>
    <path d="m17.8 4.3 1.4 1.1-4.3 6.7-2.1-1.3 5-6.5z"/>
    <path d="m21.5 10.2.3 1.8-8.2 2-1-2.3 8.9-1.5z"/>
    <path d="m20.2 16.8-1.1 1.4-6.7-4.3 1.3-2.1 6.5 5z"/>
    <path d="m13.8 21.5-1.8.3-2-8.2 2.3-1 1.5 8.9z"/>
    <path d="m6.2 19.7-1.4-1.1 4.3-6.7 2.1 1.3-5 6.5z"/>
    <path d="m2.5 13.8-.3-1.8 8.2-2 1 2.3-8.9 1.5z"/>
    <path d="m3.8 7.2 1.1-1.4 6.7 4.3-1.3 2.1-6.5-5z"/>
    <circle cx="12" cy="12" r="2.2"/>
  </g>
</svg>"""

CODEX_WORKING_ICON_SVG = """<svg viewBox="0 0 24 24" aria-hidden="true">
  <path fill="#667ef8" d="M7.3 20.8c-3.1 0-5.7-2.4-5.9-5.5-.2-2.4 1.1-4.6 3.1-5.7C4.8 5.9 7.9 3 11.8 3c3.3 0 6.2 2.2 7 5.4 2.4.7 4 2.8 4 5.4 0 3.2-2.6 5.8-5.8 5.8-.9 1.1-2.2 1.8-3.8 1.8-1.2 0-2.3-.4-3.1-1.1-.8.3-1.8.5-2.8.5z"/>
  <path fill="#fff" d="M6.4 8.2c.5-.5 1.2-.5 1.7 0l2.8 2.8c.5.5.5 1.2 0 1.7l-2.8 2.8c-.5.5-1.2.5-1.7 0s-.5-1.2 0-1.7l1.9-1.9-1.9-1.9c-.5-.5-.5-1.3 0-1.8zM13 13.2h5.1c.7 0 1.2.5 1.2 1.2s-.5 1.2-1.2 1.2H13c-.7 0-1.2-.5-1.2-1.2s.5-1.2 1.2-1.2z"/>
</svg>"""


def agent_status_glyph_html(kind, state, element_id, *, subwindow=False):
    svg = CLAUDE_WORKING_ICON_SVG if kind == "claude" else CODEX_WORKING_ICON_SVG
    label = f"{'Claude' if kind == 'claude' else 'Codex'} {state}"
    dot_classes = ["status-indicator", "status-indicator--dot", f"status-indicator--{state}", "heartbeat-pulse", "agent-window-activity-icon", "agent-window-status-dot", f"agent-window-activity-icon--{state}"]
    if state in ("attention", "cooldown"):
        dot_classes.append("attention-pulse")
    return f"""
      <span class="agent-window-activity{' agent-window-activity--subwindow' if subwindow else ''} agent-window-activity--{state}" title="{label}" aria-label="{label}" style="--attention-animation-delay:0s">
        <span id="{element_id}" class="agent-icon {kind} agent-window-activity-icon agent-window-agent-icon agent-window-activity-icon--{state} agent-window-agent-icon--{state}" aria-label="{label}" title="{label}">
          {svg}
        </span>
        <span id="{element_id}-dot" class="{' '.join(dot_classes)}" aria-hidden="true">●</span>
      </span>
    """


def working_agent_glyph_html(kind, element_id, *, subwindow=False):
    return agent_status_glyph_html(kind, "working", element_id, subwindow=subwindow)


def tabber_window_button_html(kind, label, glyph_html, active=False):
    active_class = " active" if active else ""
    return f"""
      <span class="tabber-window-token tmux-window-bar" data-tmux-window-label-mode="names" data-tmux-window-bar-context="info">
        <span class="tab tmux-window-button tabber-window-button{active_class}" data-tabber-window-button="shared">
          <span class="tmux-window-name-label">
            {glyph_html}
            <span class="tmux-window-name-text">{label}</span>
          </span>
        </span>
      </span>
    """
