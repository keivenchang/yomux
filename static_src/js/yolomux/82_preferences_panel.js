// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Preferences panel choices, rendering, and binding split from 80_panes_preferences.js.

function editorSchemePreferenceChoices(options = {}) {
  const preferredOrder = [
    'dark',
    'popular-ide-dark-plus',
    'one-dark',
    'dracula',
    'monokai',
    'nord',
    'popular-ide-light-plus',
    'yolomux-light',
    'github-light',
    'one-light',
    'solarized-light',
  ];
  const ids = [...preferredOrder, ...EDITOR_SCHEME_IDS.filter(id => !preferredOrder.includes(id))];
  return ids
    .filter(id => options.dark === undefined || EDITOR_SCHEMES[id]?.dark === options.dark)
    .map(id => {
      const scheme = EDITOR_SCHEMES[id];
      return {value: id, label: scheme.label, group: scheme.dark ? t('pref.editorScheme.group.dark') : t('pref.editorScheme.group.light')};
    });
}

function globalThemePreferenceChoices() {
  return [
    {value: 'system', label: t('pref.appearance.theme.system')},
    {value: 'dark', label: t('pref.appearance.theme.dark')},
    {value: 'light', label: t('pref.appearance.theme.light')},
  ];
}

function layoutModePreferenceChoices() {
  return layoutModeValues.map(value => ({value, label: t(`menu.view.layout.${value}`)}));
}

function activeColorPreferenceChoice(value, label) {
  const dark = uiColorVisualPreset(value, false);
  const light = uiColorVisualPreset(value, true);
  const swatches = dark && light ? [dark.bright, light.bright] : ['#86d600', '#4f9e3a'];
  return {value, label, swatches, joinedSwatches: true};
}

function activeColorPreferenceChoices() {
  return UI_COLOR_CHOICES.map(value => activeColorPreferenceChoice(value, t(UI_COLOR_PRESETS[value].labelKey)));
}

function cursorColorPreferenceChoice(value) {
  const preset = UI_COLOR_PRESETS[value];
  const label = value === 'theme'
    ? t('pref.appearance.editor_cursor_color.theme')
    : preset?.cursorLabelKey ? t(preset.cursorLabelKey) : preferenceChoiceLabel(value);
  if (value === 'theme') return {value, label, swatches: [activeEditorScheme().cursor]};
  const dark = cursorColorForPreset(value, false);
  const light = cursorColorForPreset(value, true);
  if (dark && light && dark !== light) return {value, label, swatches: [dark, light], joinedSwatches: true};
  return dark ? {value, label, swatches: [dark]} : {value, label};
}

function cursorColorPreferenceChoices() {
  const choices = Array.isArray(clientSettingsPayload?.choices?.['appearance.editor_cursor_color'])
    ? clientSettingsPayload.choices['appearance.editor_cursor_color']
    : CURSOR_COLOR_CHOICES;
  return choices
    .filter(value => value === 'theme' || UI_COLOR_PRESETS[value]?.cursor)
    .map(cursorColorPreferenceChoice);
}

function separatorColorPreferenceChoice(value) {
  if (value === 'theme') return {value, label: t('pref.appearance.editor_cursor_color.theme')};
  return activeColorPreferenceChoice(value, t(UI_COLOR_PRESETS[value].labelKey));
}

function separatorColorPreferenceChoices() {
  const choices = Array.isArray(clientSettingsPayload?.choices?.['appearance.separator_color'])
    ? clientSettingsPayload.choices['appearance.separator_color']
    : SEPARATOR_COLOR_CHOICES;
  return choices
    .filter(value => value === 'theme' || UI_COLOR_PRESETS[value])
    .map(separatorColorPreferenceChoice);
}

function updateNotifyLevelPreferenceChoices() {
  const choices = Array.isArray(clientSettingsPayload?.choices?.['updates.notify_level'])
    ? clientSettingsPayload.choices['updates.notify_level']
    : ['major', 'minor', 'patch', 'none'];
  return choices.map(value => ({value, label: t(`pref.updates.notify_level.${value}`)}));
}

const YOAGENT_CLAUDE_MODEL_LABEL_KEYS = {
  'claude-fable-5': 'pref.yoagent.claude_model.fable',
  'claude-opus-4-8': 'pref.yoagent.claude_model.opus',
  'claude-sonnet-4-6': 'pref.yoagent.claude_model.sonnet',
  'claude-haiku-4-5': 'pref.yoagent.claude_model.haiku',
};

const YOAGENT_CODEX_MODEL_LABEL_KEYS = {
  'gpt-5.3-codex-spark': 'pref.yoagent.codex_model.gpt53spark',
  'gpt-5.4-mini': 'pref.yoagent.codex_model.gpt54mini',
  'gpt-5.4': 'pref.yoagent.codex_model.gpt54',
  'gpt-5.5': 'pref.yoagent.codex_model.gpt55',
};

function settingCatalogEntry(path) {
  const catalog = clientSettingsPayload?.catalog || {};
  return catalog && typeof catalog[path] === 'object' ? catalog[path] : {};
}

function settingChoiceLabels(path) {
  const labels = settingCatalogEntry(path).choice_labels || {};
  return labels && typeof labels === 'object' ? labels : {};
}

function settingChoiceMetadata(path) {
  const metadata = settingCatalogEntry(path).choice_metadata || {};
  return metadata && typeof metadata === 'object' ? metadata : {};
}

function modelPreferenceChoices(path, fallbackValues, labelKeys) {
  const choices = Array.isArray(clientSettingsPayload?.choices?.[path])
    ? clientSettingsPayload.choices[path]
    : fallbackValues;
  const catalogLabels = settingChoiceLabels(path);
  return choices.map(value => ({value, label: catalogLabels[value] || (labelKeys[value] ? t(labelKeys[value]) : preferenceChoiceLabel(value))}));
}

function yoagentClaudeModelPreferenceChoices() {
  return modelPreferenceChoices('yoagent.claude_model', Object.keys(YOAGENT_CLAUDE_MODEL_LABEL_KEYS), YOAGENT_CLAUDE_MODEL_LABEL_KEYS);
}

function yoagentCodexModelPreferenceChoices() {
  return modelPreferenceChoices('yoagent.codex_model', Object.keys(YOAGENT_CODEX_MODEL_LABEL_KEYS), YOAGENT_CODEX_MODEL_LABEL_KEYS);
}

function effortPreferenceChoices(path, fallbackValues, labelPrefix) {
  const choices = Array.isArray(clientSettingsPayload?.choices?.[path])
    ? clientSettingsPayload.choices[path]
    : fallbackValues;
  return choices.map(value => ({value, label: t(`${labelPrefix}.${value}`)}));
}

function yoagentClaudeEffortPreferenceChoices() {
  return effortPreferenceChoices('yoagent.claude_effort', ['low', 'medium', 'high'], 'pref.yoagent.claude_effort');
}

function yoagentCodexEffortPreferenceChoices() {
  const selectedModel = String(preferenceValue('yoagent.codex_model') || '').trim();
  const modelMetadata = settingChoiceMetadata('yoagent.codex_model')[selectedModel] || {};
  const options = Array.isArray(modelMetadata.effort_options) && modelMetadata.effort_options.length
    ? modelMetadata.effort_options
    : ['low', 'medium', 'high', 'xhigh'];
  return options.map(value => ({value, label: t(`pref.yoagent.codex_effort.${value}`)}));
}

function yoagentModelPreferenceChoicesForBackend(backend) {
  return backend === 'claude' ? yoagentClaudeModelPreferenceChoices() : backend === 'codex' ? yoagentCodexModelPreferenceChoices() : [];
}

function yoagentEffortPreferenceChoicesForBackend(backend) {
  return backend === 'claude' ? yoagentClaudeEffortPreferenceChoices() : backend === 'codex' ? yoagentCodexEffortPreferenceChoices() : [];
}

function preferenceSections() {
  return [
    {title: t('pref.section.general'), items: [
      // #51: Language is the FIRST General preference.
      {path: 'general.language', label: t('pref.general.language.label'), type: 'select', choices: i18nLocaleChoices(), help: t('pref.general.language.help')},
      {path: 'general.auto_focus', label: t('pref.general.auto_focus.label'), type: 'boolean', help: t('pref.general.auto_focus.help')},
      {path: 'general.startup_tips', label: t('pref.general.startup_tips.label'), type: 'boolean', help: t('pref.general.startup_tips.help')},
    ]},
    {title: t('pref.section.appearance'), items: [
      {path: 'appearance.theme', label: t('pref.appearance.theme.label'), type: 'radio', choices: globalThemePreferenceChoices(), help: t('pref.appearance.theme.help')},
      {path: 'general.default_layout', label: t('pref.general.default_layout.label'), type: 'radio', choices: layoutModePreferenceChoices(), help: t('pref.general.default_layout.help')},
      {path: 'appearance.ui_font_size', label: t('pref.appearance.ui_font_size.label'), type: 'number', min: 6, max: 20, step: 1, suffix: 'px', help: t('pref.appearance.ui_font_size.help')},
      {path: 'appearance.file_explorer_font_size', label: t('pref.appearance.file_explorer_font_size.label', {name: fileExplorerLabel()}), type: 'number', min: 6, max: 24, step: 1, suffix: 'px', help: t('pref.appearance.file_explorer_font_size.help')},
      {type: 'note', text: t('pref.appearance.font_sizes.note')},
      {path: 'appearance.tab_width', label: t('pref.appearance.tab_width.label'), type: 'number', min: 120, max: 420, step: 5, suffix: 'px', help: t('pref.appearance.tab_width.help')},
      {path: 'appearance.max_tabs_per_pane', label: t('pref.appearance.max_tabs_per_pane.label'), type: 'number', min: 2, max: 30, step: 1, help: t('pref.appearance.max_tabs_per_pane.help')},
      {path: 'appearance.pane_spacing', label: t('pref.appearance.pane_spacing.label'), type: 'number', min: 0, max: 20, step: 1, suffix: 'px', help: t('pref.appearance.pane_spacing.help')},
      {path: 'appearance.pane_ring_opacity', label: t('pref.appearance.pane_ring_opacity.label'), type: 'range', min: 5, max: 100, step: 5, suffix: '%', help: t('pref.appearance.pane_ring_opacity.help')},
      {path: 'appearance.inactive_pane_opacity', label: t('pref.appearance.inactive_pane_opacity.label'), type: 'range', min: 0, max: 100, step: 5, suffix: '%', help: t('pref.appearance.inactive_pane_opacity.help')},
      {path: 'appearance.active_color', label: t('pref.appearance.active_color.label'), type: 'radio', choices: activeColorPreferenceChoices(), help: t('pref.appearance.active_color.help')},
      {path: 'appearance.separator_color', label: t('pref.appearance.separator_color.label'), type: 'radio', choices: separatorColorPreferenceChoices(), help: t('pref.appearance.separator_color.help')},
      {path: 'appearance.editor_cursor_color', label: t('pref.appearance.editor_cursor_color.label'), type: 'radio', choices: cursorColorPreferenceChoices(), help: t('pref.appearance.editor_cursor_color.help')},
      {path: 'appearance.date_time_hour_cycle', label: t('pref.appearance.date_time_hour_cycle.label'), type: 'radio', choices: [
        {value: '24', label: t('pref.appearance.date_time_hour_cycle.24')},
        {value: '12', label: t('pref.appearance.date_time_hour_cycle.12')},
      ], help: t('pref.appearance.date_time_hour_cycle.help')},
    ]},
    {title: t('pref.section.terminal_editor'), items: [
      {path: 'appearance.terminal_theme', label: t('pref.appearance.terminal_theme.label'), type: 'radio', choices: [
        {value: 'follow-app', label: t('pref.appearance.terminal_theme.follow-app')},
        {value: 'dark', label: t('pref.appearance.terminal_theme.dark')},
        {value: 'light', label: t('pref.appearance.terminal_theme.light')},
      ], help: t('pref.appearance.terminal_theme.help')},
      {path: 'appearance.terminal_font_size', label: t('pref.appearance.terminal_font_size.label'), type: 'number', min: 6, max: 28, step: 1, suffix: 'px', help: t('pref.appearance.terminal_font_size.help')},
      {path: 'appearance.editor_font_size', label: t('pref.appearance.editor_font_size.label'), type: 'number', min: 6, max: 28, step: 1, suffix: 'px', help: t('pref.appearance.editor_font_size.help')},
      {path: 'appearance.preview_font_size', label: t('pref.appearance.preview_font_size.label'), type: 'number', min: 6, max: 32, step: 1, suffix: 'px', help: t('pref.appearance.preview_font_size.help')},
      {path: 'terminal_editor.scrollback', label: t('pref.terminal_editor.scrollback.label'), type: 'number', min: 1000, max: 50000, step: 500, suffix: 'lines', help: t('pref.terminal_editor.scrollback.help')},
      {path: 'appearance.editor_dark_color_scheme', label: t('pref.appearance.editor_dark_color_scheme.label'), type: 'select', choices: editorSchemePreferenceChoices({dark: true}), help: t('pref.appearance.editor_dark_color_scheme.help')},
      {path: 'appearance.editor_light_color_scheme', label: t('pref.appearance.editor_light_color_scheme.label'), type: 'select', choices: editorSchemePreferenceChoices({dark: false}), help: t('pref.appearance.editor_light_color_scheme.help')},
      {path: 'appearance.editor_cursor_style', label: t('pref.appearance.editor_cursor_style.label'), type: 'radio', choices: [
        {value: 'line', label: t('pref.appearance.editor_cursor_style.line')},
        {value: 'block', label: t('pref.appearance.editor_cursor_style.block')},
      ], help: t('pref.appearance.editor_cursor_style.help')},
      {path: 'terminal_editor.word_wrap', label: t('pref.terminal_editor.word_wrap.label'), type: 'boolean', help: t('pref.terminal_editor.word_wrap.help')},
      {path: 'terminal_editor.line_numbers', label: t('pref.terminal_editor.line_numbers.label'), type: 'boolean', help: t('pref.terminal_editor.line_numbers.help')},
      {path: 'editor.autosave', label: t('pref.editor.autosave.label'), type: 'boolean', help: t('pref.editor.autosave.help')},
      {path: 'editor.autosave_delay_seconds', label: t('pref.editor.autosave_delay_seconds.label'), type: 'number', min: 0.5, max: 60, step: 0.5, suffix: 's', help: t('pref.editor.autosave_delay_seconds.help')},
      {path: 'editor.blame_all_lines', label: t('pref.editor.blame_all_lines.label'), type: 'boolean', help: t('pref.editor.blame_all_lines.help')},
      {path: 'editor.trim_trailing_whitespace_on_save', label: t('pref.editor.trim_trailing_whitespace_on_save.label'), type: 'boolean', help: t('pref.editor.trim_trailing_whitespace_on_save.help')},
      {path: 'editor.ensure_final_newline_on_save', label: t('pref.editor.ensure_final_newline_on_save.label'), type: 'boolean', help: t('pref.editor.ensure_final_newline_on_save.help')},
    ]},
    {title: t('pref.section.notifications'), items: [
      {path: 'general.reload_on_update', label: t('pref.general.reload_on_update.label'), type: 'boolean', help: t('pref.general.reload_on_update.help')},
      {path: 'general.reload_on_update_auto', label: t('pref.general.reload_on_update_auto.label'), type: 'boolean', help: t('pref.general.reload_on_update_auto.help')},
      {path: 'updates.notify_level', label: t('pref.updates.notify_level.label'), type: 'radio', choices: updateNotifyLevelPreferenceChoices(), help: t('pref.updates.notify_level.help')},
      {path: 'notifications.notify_transitions', label: t('pref.notifications.notify_transitions.label'), type: 'list', help: t('pref.notifications.notify_transitions.help')},
      {path: 'notifications.toast_duration_ms', label: t('pref.notifications.toast_duration_ms.label'), type: 'number', min: 1000, max: 60000, step: 500, suffix: 'ms', help: t('pref.notifications.toast_duration_ms.help')},
      {path: 'notifications.throttle_seconds', label: t('pref.notifications.throttle_seconds.label'), type: 'number', min: 0, max: 600, step: 5, suffix: 's', help: t('pref.notifications.throttle_seconds.help')},
      {path: 'performance.agent_status_pulse_period_ms', label: t('pref.performance.agent_status_pulse_period_ms.label'), type: 'number', min: 250, max: 10000, step: 250, suffix: 'ms', help: t('pref.performance.agent_status_pulse_period_ms.help')},
      {path: 'performance.workflow_transition_glow_seconds', label: t('pref.performance.workflow_transition_glow_seconds.label'), type: 'number', min: 0, max: 300, step: 1, suffix: 's', help: t('pref.performance.workflow_transition_glow_seconds.help')},
      {path: 'appearance.metadata_badge_pulse_seconds', label: t('pref.appearance.metadata_badge_pulse_seconds.label'), type: 'number', min: 0, max: 120, step: 1, suffix: 's', help: t('pref.appearance.metadata_badge_pulse_seconds.help')},
    ]},
    {title: fileExplorerLabel(), items: [
      {path: 'file_explorer.root_mode', label: t('pref.file_explorer.root_mode.label'), type: 'radio', choices: ['fixed', 'sync'], help: t('pref.file_explorer.root_mode.help')},
      {path: 'file_explorer.image_open_mode', label: t('pref.file_explorer.image_open_mode.label'), type: 'radio', choices: ['same-tab', 'new-tab'], help: t('pref.file_explorer.image_open_mode.help')},
      {path: 'file_explorer.image_preview_max_px', label: t('pref.file_explorer.image_preview_max_px.label'), type: 'number', min: 120, max: 1200, step: 20, suffix: 'px', help: t('pref.file_explorer.image_preview_max_px.help')},
      {path: 'file_explorer.quick_access_paths', label: t('pref.file_explorer.quick_access_paths.label'), type: 'list', help: t('pref.file_explorer.quick_access_paths.help')},
      {path: 'file_explorer.indexed_dirs', label: t('pref.file_explorer.indexed_dirs.label'), type: 'list', help: t('pref.file_explorer.indexed_dirs.help')},
      {path: 'file_explorer.index_refresh_seconds', label: t('pref.file_explorer.index_refresh_seconds.label'), type: 'number', min: 0, max: 3600, step: 10, suffix: 's', help: t('pref.file_explorer.index_refresh_seconds.help')},
      {path: 'file_explorer.companion_dirs', label: t('pref.file_explorer.companion_dirs.label'), type: 'list', help: t('pref.file_explorer.companion_dirs.help')},
      {path: 'file_explorer.dir_cache_ms', label: t('pref.file_explorer.dir_cache_ms.label'), type: 'number', min: 0, max: 10000, step: 100, suffix: 'ms', help: t('pref.file_explorer.dir_cache_ms.help')},
      {path: 'file_explorer.new_entry_highlight_ms', label: t('pref.file_explorer.new_entry_highlight_ms.label'), type: 'number', min: 0, max: 600000, step: 1000, suffix: 'ms', help: t('pref.file_explorer.new_entry_highlight_ms.help')},
    ]},
    {title: t('pref.section.uploads'), items: [
      {path: 'uploads.filename_template', label: t('pref.uploads.filename_template.label'), type: 'text', wide: true, help: t('pref.uploads.filename_template.help')},
      {path: 'uploads.subdir', label: t('pref.uploads.subdir.label'), type: 'text', help: t('pref.uploads.subdir.help')},
      {path: 'uploads.show_suggestions', label: t('pref.uploads.show_suggestions.label'), type: 'boolean', help: t('pref.uploads.show_suggestions.help')},
      {path: 'uploads.suggestion_autorun', label: t('pref.uploads.suggestion_autorun.label'), type: 'boolean', help: t('pref.uploads.suggestion_autorun.help')},
      {path: 'uploads.image_action_order', label: t('pref.uploads.image_action_order.label'), type: 'list', wide: true, rows: 7, maxItems: 9, autosize: true, help: t('pref.uploads.image_action_order.help')},
      {path: 'uploads.custom_actions', label: t('pref.uploads.custom_actions.label'), type: 'list', wide: true, help: t('pref.uploads.custom_actions.help')},
      {path: 'uploads.max_bytes', label: t('pref.uploads.max_bytes.label'), type: 'number', min: 1, max: 512, step: 1, suffix: 'MB', scale: 1048576, help: t('pref.uploads.max_bytes.help')},
    ]},
    {title: t('pref.section.performance'), items: [
      {path: 'performance.server_event_poll_ms', label: t('pref.performance.server_event_poll_ms.label'), type: 'number', min: 0.25, max: 60, step: 0.05, suffix: 's', scale: 1000, displayDecimals: 3, help: t('pref.performance.server_event_poll_ms.help')},
      {path: 'performance.server_background_file_event_poll_ms', label: t('pref.performance.server_background_file_event_poll_ms.label'), type: 'number', min: 0.25, max: 60, step: 0.05, suffix: 's', scale: 1000, displayDecimals: 3, help: t('pref.performance.server_background_file_event_poll_ms.help')},
      {path: 'performance.server_directory_event_poll_ms', label: t('pref.performance.server_directory_event_poll_ms.label'), type: 'number', min: 0.25, max: 60, step: 0.05, suffix: 's', scale: 1000, displayDecimals: 3, help: t('pref.performance.server_directory_event_poll_ms.help')},
      {path: 'performance.latency_refresh_ms', label: t('pref.performance.latency_refresh_ms.label'), type: 'number', min: 1, max: 30, step: 0.1, suffix: 's', scale: 1000, help: t('pref.performance.latency_refresh_ms.help')},
      {path: 'performance.event_log_refresh_ms', label: t('pref.performance.event_log_refresh_ms.label'), type: 'number', min: 1, max: 60, step: 0.1, suffix: 's', scale: 1000, help: t('pref.performance.event_log_refresh_ms.help')},
      {path: 'performance.tabber_activity_refresh_ms', label: t('pref.performance.tabber_activity_refresh_ms.label'), type: 'number', min: 1, max: 60, step: 0.5, suffix: 's', scale: 1000, help: t('pref.performance.tabber_activity_refresh_ms.help')},
      {path: 'performance.popover_show_delay_ms', label: t('pref.performance.popover_show_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.popover_show_delay_ms.help')},
      {path: 'performance.popover_hide_delay_ms', label: t('pref.performance.popover_hide_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.popover_hide_delay_ms.help')},
      {path: 'performance.menu_hover_open_delay_ms', label: t('pref.performance.menu_hover_open_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.menu_hover_open_delay_ms.help')},
      {path: 'performance.tab_popover_show_delay_ms', label: t('pref.performance.tab_popover_show_delay_ms.label'), type: 'number', min: 0, max: 3000, step: 50, suffix: 'ms', help: t('pref.performance.tab_popover_show_delay_ms.help')},
      {path: 'performance.tab_popover_follow_delay_ms', label: t('pref.performance.tab_popover_follow_delay_ms.label'), type: 'number', min: 0, max: 1000, step: 20, suffix: 'ms', help: t('pref.performance.tab_popover_follow_delay_ms.help')},
      {path: 'performance.remote_resize_delay_ms', label: t('pref.performance.remote_resize_delay_ms.label'), type: 'number', min: 50, max: 2000, step: 10, suffix: 'ms', help: t('pref.performance.remote_resize_delay_ms.help')},
    ]},
    {title: t('pref.section.github'), items: [
      {path: 'github.watched_prs', label: t('pref.github.watched_prs.label'), type: 'list', wide: true, help: t('pref.github.watched_prs.help')},
    ]},
    {title: t('pref.section.yoagent'), items: [
      {path: 'yoagent.backend', label: t('pref.yoagent.backend.label'), type: 'radio', choices: [
        {value: 'auto', label: t('pref.yoagent.backend.auto')},
        {value: 'codex', label: t('pref.yoagent.backend.codex')},
        {value: 'claude', label: t('pref.yoagent.backend.claude')},
      ], help: t('pref.yoagent.backend.help')},
      {path: 'yoagent.invocation', label: t('pref.yoagent.invocation.label'), type: 'radio', choices: [
        {value: 'cli', label: t('pref.yoagent.invocation.cli')},
      ], help: t('pref.yoagent.invocation.help')},
      {path: 'yoagent.claude_model', label: t('pref.yoagent.claude_model.label'), type: 'select', choices: yoagentClaudeModelPreferenceChoices(), help: t('pref.yoagent.claude_model.help')},
      {path: 'yoagent.claude_effort', label: t('pref.yoagent.claude_effort.label'), type: 'radio', choices: yoagentClaudeEffortPreferenceChoices(), help: t('pref.yoagent.claude_effort.help')},
      {path: 'yoagent.codex_model', label: t('pref.yoagent.codex_model.label'), type: 'select', choices: yoagentCodexModelPreferenceChoices(), help: t('pref.yoagent.codex_model.help')},
      {path: 'yoagent.codex_effort', label: t('pref.yoagent.codex_effort.label'), type: 'radio', choices: yoagentCodexEffortPreferenceChoices(), help: t('pref.yoagent.codex_effort.help')},
      {path: 'yoagent.system_prompt', label: t('pref.yoagent.system_prompt.label'), type: 'textarea', help: t('pref.yoagent.system_prompt.help'), alwaysEnableReset: true},
      {path: 'yoagent.intro', label: t('pref.yoagent.intro.label'), type: 'textarea', help: t('pref.yoagent.intro.help'), alwaysEnableReset: true},
      {path: 'yoagent.format', label: t('pref.yoagent.format.label'), type: 'textarea', help: t('pref.yoagent.format.help'), alwaysEnableReset: true},
    ]},
    {title: t('pref.section.share'), items: [
      {path: 'share.ttl_seconds', label: t('pref.share.ttl_seconds.label'), type: 'number', min: 1, max: 480, step: 1, suffix: t('unit.minute.short'), scale: 60, help: t('pref.share.ttl_seconds.help')},
      {path: 'share.max_viewers', label: t('pref.share.max_viewers.label'), type: 'number', min: 1, max: 300, step: 1, help: t('pref.share.max_viewers.help')},
      {path: 'share.read_only', label: t('pref.share.read_only.label'), type: 'boolean', help: t('pref.share.read_only.help')},
      {path: 'share.scheme', label: t('pref.share.scheme.label'), type: 'radio', choices: ['http', 'https'], help: t('pref.share.scheme.help')},
    ]},
    {id: 'yolo', title: t('pref.section.yolo'), items: [
      {path: 'performance.auto_approve_interval_seconds', label: t('pref.performance.auto_approve_interval_seconds.label'), type: 'number', min: 0.1, max: 10, step: 0.1, suffix: 's', help: t('pref.performance.auto_approve_interval_seconds.help')},
      {path: 'yolo.rule_file_path', label: t('pref.yolo.rule_file_path.label'), type: 'text', action: 'open-yolo-rule', wide: true, help: t('pref.yolo.rule_file_path.help')},
      {path: 'yolo.dry_run', label: t('pref.yolo.dry_run.label'), type: 'boolean', help: t('pref.yolo.dry_run.help')},
      {path: 'yolo.prompt_source', label: t('pref.yolo.prompt_source.label'), type: 'radio', choices: [
        {value: 'hybrid', label: t('pref.yolo.prompt_source.hybrid')},
        {value: 'pane', label: t('pref.yolo.prompt_source.pane')},
      ], help: t('pref.yolo.prompt_source.help')},
    ]},
  ];
}

function preferenceItemByPath(path) {
  for (const section of preferenceSections()) {
    const item = section.items.find(candidate => candidate.path === path);
    if (item) return item;
  }
  return null;
}

function preferenceValue(path) {
  return nestedSetting(clientSettings, path, nestedSetting(clientSettingsDefaults, path, ''));
}

function preferenceDefault(path) {
  return nestedSetting(clientSettingsDefaults, path, '');
}

function preferenceStatusText() {
  if (clientSettingsPayload.error) return t('pref.status.settingsError', {error: clientSettingsPayload.error});
  if (yoloRulesPayload.error) return t('pref.status.rulesError', {error: yoloRulesPayload.error});
  return settingsLoadedAgeText();
}

function settingsLoadedAgeText(nowMs = Date.now()) {
  const loadedMs = Number(clientSettingsPayload.mtime_ns || 0) / 1000000;
  if (!Number.isFinite(loadedMs) || loadedMs <= 0) return t('pref.status.loaded');
  const ageSeconds = Math.max(0, Math.floor((Number(nowMs) - loadedMs) / 1000));
  if (ageSeconds < 60) return t('pref.status.loadedSeconds', {count: ageSeconds});
  const ageMinutes = Math.floor(ageSeconds / 60);
  if (ageMinutes < 60) return t('pref.status.loadedMinutes', {count: ageMinutes});
  const ageHours = Math.floor(ageMinutes / 60);
  if (ageHours < 24) return t('pref.status.loadedHours', {count: ageHours});
  const ageDays = Math.floor(ageHours / 24);
  return tPlural('pref.status.loadedDays', ageDays);
}

function preferencesPathRowsHtml() {
  const settingsPath = settingsConfigPath();
  return `
    <div class="preferences-path-row">
      <span class="preferences-path-label">${esc(t('pref.path.settings'))}</span><span class="preferences-path-value">${esc(settingsPath)} ${esc(settingsLoadedAgeText())}</span>${pathCopyButtonHtml(settingsPath, {className: 'preferences-path-copy', title: t('pref.path.copySettings')})}
    </div>`;
}

function preferencesYoloRulesPathHtml() {
  const rulesPath = yoloRulePath();
  const rulesDetail = yoloRulesPayload.source ? ` · ${yoloRuleStatusDetail()}` : '';
  return `
    <div class="preferences-path-row preferences-path-row--section">
      <span class="preferences-path-label">${esc(t('pref.path.rules'))}</span><span class="preferences-path-value">${esc(rulesPath)}${esc(rulesDetail)}</span>${pathCopyButtonHtml(rulesPath, {className: 'preferences-path-copy', title: t('pref.path.copyRules')})}
    </div>`;
}

function preferenceSearchNeedle() {
  return preferencesSearchText.trim().toLowerCase();
}

const preferenceSearchAliasGroups = [
  ['large', 'larger', 'big', 'bigger', 'huge', 'small', 'smaller', 'tiny', 'text', 'scale', 'zoom', 'font', 'size'],
  ['wide', 'narrow', 'width'],
  ['duration', 'timeout', 'time', 'timing', 'ms', 'millisecond', 'milliseconds', 'second', 'seconds', 'speed', 'fast', 'slow', 'quick', 'lag', 'wait', 'debounce', 'period', 'rate', 'frequency', 'often', 'delay', 'refresh', 'interval'],
  ['refresh', 'reload', 'update', 'poll', 'polling', 'sync', 'live'],
  ['tooltip', 'popup', 'popover', 'peek', 'flyout', 'hover'],
  ['animation', 'animate', 'blink', 'flash', 'pulse', 'spin', 'glow', 'attention', 'reminder', 'red'],
  ['color', 'colour', 'theme', 'dark', 'light', 'background', 'bg', 'contrast', 'style', 'look'],
  ['shell', 'history', 'buffer', 'backlog', 'lines', 'scrollback', 'terminal'],
  ['code', 'edit', 'editor', 'codemirror', 'monaco'],
  ['wrap', 'wrapping', 'softwrap'],
  ['numbers', 'number', 'gutter'],
  ['notify', 'notification', 'notifications', 'alert', 'alerts', 'toast', 'message', 'banner', 'sound', 'ding', 'ping', 'bell', 'beep', 'desktop', 'dismiss'],
  ['throttle', 'mute', 'quiet', 'spam', 'cooldown'],
  ['finder', 'file', 'files', 'explorer', 'tree', 'sidebar', 'browser', 'directory', 'folder', 'navigator'],
  ['root', 'home', 'base', 'cwd'],
  ['shortcuts', 'bookmarks', 'favorites', 'pinned', 'jump'],
  ['yolo', 'autoapprove', 'approve', 'approval', 'permission', 'permissions', 'accept', 'confirm', 'rules', 'policy', 'safe', 'danger', 'dangerous'],
  ['yoagent', 'yo agent', 'assistant', 'chat', 'summary', 'activity', 'prompt', 'backend', 'claude', 'codex'],
  ['dry', 'simulate', 'test'],
  ['startup', 'launch', 'open', 'start', 'split', 'grid', 'layout'],
];
const preferenceSearchAliasMap = new Map();
for (const group of preferenceSearchAliasGroups) {
  for (const term of group) preferenceSearchAliasMap.set(term, group);
}

function preferenceSearchTokens(query) {
  return String(query || '').toLowerCase().match(/[a-z0-9_./-]+/g) || [];
}

function preferenceSearchAliasesForToken(token) {
  return preferenceSearchAliasMap.get(token) || [token];
}

function preferenceSearchKeywordsForItem(item) {
  const path = String(item?.path || '');
  const label = String(item?.label || '').toLowerCase();
  const keywords = [];
  const add = terms => keywords.push(...terms);
  if (path.includes('font_size') || path === 'appearance.tab_width') add(['large', 'larger', 'big', 'bigger', 'huge', 'small', 'smaller', 'tiny', 'text', 'scale', 'zoom', 'wide', 'narrow']);
  if (/(_ms|_seconds|_delay|_refresh|_interval|duration|period|pulse|rotate|throttle|resize)/.test(path)) add(['duration', 'timeout', 'time', 'timing', 'milliseconds', 'seconds', 'speed', 'fast', 'slow', 'quick', 'lag', 'wait', 'debounce', 'period', 'rate', 'frequency', 'often']);
  if (path.includes('_refresh_ms')) add(['reload', 'update', 'poll', 'polling', 'sync', 'live', 'auto']);
  if (path.includes('popover') || path.includes('hover')) add(['tooltip', 'popup', 'peek', 'flyout']);
  if (path.includes('red_reminder') || path.includes('yolo_rotate') || path.includes('badge_pulse')) add(['animation', 'animate', 'blink', 'flash', 'glow', 'attention', 'reminder']);
  if (path.startsWith('appearance.')) add(['color', 'colour', 'theme', 'dark', 'light', 'background', 'bg', 'contrast', 'style', 'look']);
  if (path === 'appearance.date_time_hour_cycle') add(['date', 'time', 'clock', 'hour', 'hours', '12', '24', 'am', 'pm']);
  if (path === 'terminal_editor.scrollback' || path === 'appearance.terminal_font_size' || path === 'appearance.terminal_theme') add(['shell', 'history', 'buffer', 'backlog', 'lines', 'terminal', 'tui', 'ansi', 'xterm', 'codex', 'claude']);
  if (path.startsWith('editor.') || path.includes('editor_') || path.startsWith('terminal_editor.')) add(['code', 'edit', 'codemirror', 'monaco']);
  if (path === 'terminal_editor.word_wrap') add(['softwrap', 'wrapping']);
  if (path === 'terminal_editor.line_numbers') add(['numbers', 'gutter']);
  if (path.startsWith('notifications.')) add(['notify', 'alert', 'toast', 'message', 'banner', 'sound', 'ding', 'ping', 'bell', 'beep', 'desktop', 'dismiss']);
  if (path.startsWith('updates.')) add(['notify', 'notification', 'alert', 'update', 'version', 'major', 'minor', 'patch', 'release', 'origin', 'main']);
  if (path.includes('throttle')) add(['mute', 'quiet', 'spam', 'cooldown', 'rate limit']);
  if (path.startsWith('file_explorer.')) add(['finder', 'files', 'tree', 'sidebar', 'browser', 'directory', 'folder', 'navigator']);
  if (path.startsWith('uploads.')) add(['upload', 'paste', 'drop', 'filename', 'template', 'file']);
  if (path.startsWith('share.')) add(['share', 'sharing', 'viewer', 'viewers', 'url', 'http', 'https', 'read-only', 'write']);
  if (path === 'file_explorer.root_mode') add(['root', 'home', 'base', 'working', 'cwd', 'follow', 'track']);
  if (path === 'file_explorer.quick_access_paths') add(['shortcuts', 'bookmarks', 'favorites', 'pinned', 'jump']);
  if (path === 'file_explorer.indexed_dirs') add(['index', 'indexed', 'quick open', 'quick-open', 'search', 'scan', 'directories', 'folders']);
  if (path === 'file_explorer.index_refresh_seconds') add(['index', 'refresh', 'auto', 'rebuild', 'background', 'quick-open', 'interval', 'stale']);
  if (path === 'file_explorer.companion_dirs') add(['companion', 'repos', 'sibling', 'extra', 'always', 'dirty', 'branch', 'status', 'frontend-crates']);
  if (path === 'file_explorer.image_preview_max_px') add(['image', 'picture', 'photo', 'preview', 'thumbnail', 'hover', 'popup', 'large', 'small', 'size']);
  if (path === 'file_explorer.new_entry_highlight_ms') add(['new file', 'recent']);
  if (path.startsWith('yolo.')) add(['auto approve', 'approve', 'approval', 'permission', 'accept', 'confirm', 'rules', 'policy', 'safe', 'danger']);
  if (path.startsWith('yoagent.')) add(['assistant', 'chat', 'summary', 'activity', 'prompt', 'backend', 'claude', 'codex']);
  if (path === 'yolo.dry_run') add(['test', 'simulate', 'what would']);
  if (path === 'yolo.rule_file_path') add(['yaml', 'config']);
  if (path === 'general.auto_focus') add(['click', 'focus', 'hover', 'menu', 'dropdown', 'select pane', 'terminal', 'editor', 'finder', 'file explorer', 'preferences', 'everything']);
  if (path === 'general.default_layout') add(['startup', 'launch', 'open', 'start', 'split', 'grid']);
  if (label.includes('quick')) add(['shortcuts', 'bookmarks', 'favorites']);
  return keywords;
}

function preferenceSearchHaystack(item) {
  const choices = Array.isArray(item.choices) ? item.choices.map(choice => [preferenceChoiceValue(choice), preferenceChoiceLabel(choice), preferenceChoiceGroup(choice)]).flat() : [];
  return [item.label, item.path, item.help, item.text, item.suffix, item.keywords, choices, preferenceSearchKeywordsForItem(item)]
    .flat(Infinity)
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function textMatchesPreferenceQuery(value, query) {
  const haystack = String(value || '').toLowerCase();
  const normalized = String(query || '').trim().toLowerCase();
  if (!normalized) return true;
  if (haystack.includes(normalized)) return true;
  return preferenceSearchTokens(normalized).every(token => (
    preferenceSearchAliasesForToken(token).some(alias => haystack.includes(alias))
  ));
}

function preferenceItemMatches(item, query) {
  if (!query) return true;
  return textMatchesPreferenceQuery(preferenceSearchHaystack(item), query);
}

function preferenceSectionMatches(section, query) {
  if (!query) return true;
  return textMatchesPreferenceQuery(section.title, query) || section.items.some(item => preferenceItemMatches(item, query));
}

function preferenceChoiceValue(choice) {
  return typeof choice === 'object' && choice !== null ? choice.value : choice;
}

function preferenceChoiceLabel(choice) {
  if (typeof choice === 'object' && choice !== null) return choice.label || choice.value;
  return String(choice || '')
    .replace(/-/g, ' ')
    .replace(/\b\w/g, match => match.toUpperCase());
}

function preferenceChoiceGroup(choice) {
  return typeof choice === 'object' && choice !== null ? (choice.group || '') : '';
}

function preferenceSelectOptionsHtml(item, value) {
  const choices = Array.isArray(item.choices) ? item.choices : [];
  const groups = [];
  const groupLookup = new Map();
  const optionHtml = choice => {
    const choiceValue = String(preferenceChoiceValue(choice));
    return `<option value="${esc(choiceValue)}"${choiceValue === String(value) ? ' selected' : ''}>${esc(preferenceChoiceLabel(choice))}</option>`;
  };
  const looseOptions = [];
  for (const choice of choices) {
    const group = preferenceChoiceGroup(choice);
    if (!group) {
      looseOptions.push(optionHtml(choice));
      continue;
    }
    if (!groupLookup.has(group)) {
      groupLookup.set(group, []);
      groups.push(group);
    }
    groupLookup.get(group).push(optionHtml(choice));
  }
  return [
    ...looseOptions,
    ...groups.map(group => `<optgroup label="${esc(group)}">${groupLookup.get(group).join('')}</optgroup>`),
  ].join('');
}

function preferenceControlHtml(item, query = '') {
  if (!preferenceItemMatches(item, query)) return '';
  if (item.type === 'note') {
    return `<div class="preferences-setting-row preferences-setting-note">${esc(item.text || '')}</div>`;
  }
  const value = preferenceValue(item.path);
  const defaultValue = preferenceDefault(item.path);
  const preferencesReadOnlyVisual = readOnlyMode && !shareViewMode;
  const disabled = preferencesReadOnlyVisual ? ' disabled' : '';
  const controlId = `preference-${item.path.replace(/[^A-Za-z0-9_-]+/g, '-')}`;
  const minAttr = item.min !== undefined ? ` data-setting-min="${esc(item.min)}"` : '';
  const maxAttr = item.max !== undefined ? ` data-setting-max="${esc(item.max)}"` : '';
  const baseAttrs = `id="${esc(controlId)}" data-setting-path="${esc(item.path)}" data-setting-type="${esc(item.type)}"${minAttr}${maxAttr}${disabled}`;
  let control = '';
  if (item.type === 'boolean') {
    control = `<input type="checkbox" ${baseAttrs}${value ? ' checked' : ''}>`;
  } else if (item.type === 'number') {
    control = `<input type="number" ${baseAttrs} inputmode="decimal" value="${esc(preferenceNumberDisplayValue(item, value))}" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(item.step || 1)}">`;
  } else if (item.type === 'range') {
    const rangeValue = preferenceNumberDisplayValue(item, value);
    control = `<input type="range" ${baseAttrs} value="${esc(rangeValue)}" min="${esc(item.min)}" max="${esc(item.max)}" step="${esc(item.step || 1)}"><output class="preferences-range-value" for="${esc(controlId)}">${esc(rangeValue)}</output>`;
  } else if (item.type === 'select') {
    control = `<select ${baseAttrs}>${preferenceSelectOptionsHtml(item, value)}</select>`;
  } else if (item.type === 'radio') {
    // #260: plain radio-button group (replaced the macOS-style theme-cards). One input + label per
    // choice; each input carries data-setting-path so the shared change handler -> savePreferenceControl
    // persists it (and live-applies appearance.theme). The current value is checked.
    const choices = item.choices || [];
    const groupHasSwatches = choices.some(choice => Array.isArray(choice?.swatches));
    const radios = choices.map(choice => {
      const choiceValue = String(preferenceChoiceValue(choice));
      const selected = String(value) === choiceValue;
      const radioId = `${controlId}-${choiceValue.replace(/[^A-Za-z0-9_-]+/g, '-')}`;
      const swatches = Array.isArray(choice?.swatches)
        ? `<span class="preferences-radio-swatches${choice.joinedSwatches ? ' joined' : ''}" aria-hidden="true">${choice.swatches.map(color => `<span class="preferences-radio-swatch" style="--preferences-radio-swatch:${esc(color)}"></span>`).join('')}</span>`
        : '';
      return `<label class="preferences-radio${swatches ? ' has-swatches' : ''}" for="${esc(radioId)}">
        <input type="radio" id="${esc(radioId)}" name="${esc(controlId)}" value="${esc(choiceValue)}" data-setting-path="${esc(item.path)}" data-setting-type="radio"${selected ? ' checked' : ''}${disabled}>
        ${swatches}<span>${esc(preferenceChoiceLabel(choice))}</span>
      </label>`;
    }).join('');
    control = `<div class="preferences-radio-group${groupHasSwatches ? ' has-swatches' : ''}" role="radiogroup" aria-label="${esc(item.label)}">${radios}</div>`;
  } else if (item.type === 'list') {
    const text = Array.isArray(value) ? value.join('\n') : String(value || '');
    const rows = Number.isFinite(Number(item.rows)) ? Math.max(1, Math.min(9, Math.floor(Number(item.rows)))) : 3;
    const maxItems = Number.isFinite(Number(item.maxItems)) ? Math.max(1, Math.floor(Number(item.maxItems))) : 0;
    const autosize = item.autosize ? ' data-setting-autosize="true"' : '';
    const maxItemsAttr = maxItems ? ` data-setting-max-items="${esc(maxItems)}"` : '';
    control = `<textarea ${baseAttrs}${autosize}${maxItemsAttr} rows="${esc(rows)}">${esc(text)}</textarea>`;
  } else if (item.type === 'textarea') {
    control = `<textarea ${baseAttrs} rows="3" data-setting-autosize="true">${esc(String(value || ''))}</textarea>`;
  } else {
    control = `<input type="text" ${baseAttrs} value="${esc(value)}">`;
  }
  const resetDisabled = preferencesReadOnlyVisual || (!item.alwaysEnableReset && JSON.stringify(value) === JSON.stringify(defaultValue)) ? ' disabled' : '';
  const extraControl = item.action === 'open-yolo-rule'
    ? `<button type="button" class="preferences-inline-action" data-yolo-rule-open${preferencesReadOnlyVisual ? ' disabled' : ''}>${esc(t('pref.openAction'))}</button>`
    : '';
  const suffix = item.suffix ? `<span class="preferences-setting-suffix">${esc(item.suffix)}</span>` : '';
  const help = item.help ? `<span class="preferences-setting-help">${esc(item.help)}</span>` : '';
  const advisory = preferenceAdvisoryHtml(item, value);
  const rowClass = item.type === 'textarea' || item.wide ? ' preferences-setting-row--wide' : '';
  return `<div class="preferences-setting-row${rowClass}"><label class="preferences-setting-label" for="${esc(controlId)}">${esc(item.label)}${help}</label><span class="preferences-setting-control setting-type-${esc(item.type)}">${control}${suffix}${extraControl}<button type="button" class="preferences-reset" data-setting-reset="${esc(item.path)}"${resetDisabled}>${esc(t('pref.reset.row'))}</button></span>${advisory}</div>`;
}

function preferenceNumberDisplayValue(item, value) {
  const scale = Number(item.scale) || 1;
  const raw = scale !== 1 ? Number(value) / scale : value;
  const clamped = Number(clampPreferenceNumber(item, raw));
  if (!Number.isFinite(clamped)) return clampPreferenceNumber(item, raw);
  if (Number.isFinite(Number(item.displayDecimals))) return clamped.toFixed(Number(item.displayDecimals));
  return clamped;
}

function uploadRsyncExampleCommand() {
  const host = serverHostname || '<host>';
  const destination = homePath || '~';
  return `rsync -avz <local-path> ${host}:${destination}/`;
}

function preferenceAdvisoryHtml(item, value) {
  if (item.path !== 'uploads.max_bytes' || Number(value) <= uploadRsyncRecommendationBytes) return '';
  const command = uploadRsyncExampleCommand();
  return `<div class="preferences-setting-advisory">
    <span>${esc(t('pref.advisory.upload', {size: formatFileSize(uploadRsyncRecommendationBytes)}))}</span>
    <code>${esc(command)}</code>
    <button type="button" class="preferences-inline-action" data-copy-text="${esc(command)}">${esc(t('pref.advisory.copyRsync'))}</button>
  </div>`;
}

function preferencesPanelHtml() {
  const query = preferenceSearchNeedle();
  const sections = preferenceSections()
    .filter(section => preferenceSectionMatches(section, query))
    .map(section => {
      const titleMatches = textMatchesPreferenceQuery(section.title, query);
      const visibleItems = section.items.filter(item => titleMatches || preferenceItemMatches(item, query));
      const collapsed = !query && collapsedPreferenceSections.has(section.title);
      const sectionIntro = section.id === 'yolo' && (!query || textMatchesPreferenceQuery('yolo rules rule file yaml auto approve approval', query))
        ? preferencesYoloRulesPathHtml()
        : '';
      const rows = `${sectionIntro}${visibleItems.map(item => preferenceControlHtml(item)).join('')}`;
      const count = visibleItems.length;
      return `
        <section class="preferences-section${collapsed ? ' collapsed' : ''}" data-preference-section="${esc(section.title)}">
          <button type="button" class="preferences-section-toggle" data-preference-section-toggle="${esc(section.title)}" aria-expanded="${collapsed ? 'false' : 'true'}">
            ${disclosureTriangleHtml(!collapsed, 'preferences-section-caret')}
            <span class="preferences-section-title">${esc(section.title)}</span>
            <span class="preferences-section-count">${count}</span>
          </button>
          <div class="preferences-settings"${collapsed ? ' hidden' : ''}>${rows}</div>
        </section>`;
    }).join('');
  const readonly = readOnlyMode && !shareViewMode ? `<span class="preferences-readonly">${esc(t('pref.readonly'))}</span>` : '';
  const resetDisabled = readOnlyMode ? ' disabled' : '';
  const resetTitle = preferencesResetConfirmVisible ? t('pref.reset.confirmTitle') : t('pref.reset.title');
  const resetWarning = preferencesResetConfirmVisible
    ? t('pref.reset.confirmWarning')
    : t('pref.reset.warning', {name: fileExplorerLabel()});
  const resetAction = preferencesResetConfirmVisible ? `
      <div class="preferences-reset-confirm">
        <button type="button" class="preferences-reset-continue" data-preferences-reset-confirm${resetDisabled}>${esc(t('pref.reset.continue'))}</button>
        <button type="button" class="preferences-reset-cancel" data-preferences-reset-cancel>${esc(t('pref.reset.cancel'))}</button>
      </div>` : `<button type="button" class="preferences-reset-all" data-preferences-reset-all${resetDisabled}>${esc(t('pref.reset.all'))}</button>`;
  const resetBlock = `
    <div class="preferences-global-reset${preferencesResetConfirmVisible ? ' confirming' : ''}" role="group" aria-label="${esc(t('pref.reset.aria'))}">
      <div>
        <div class="preferences-global-reset-title">${resetTitle}</div>
        <div class="preferences-global-reset-warning">${resetWarning}</div>
      </div>
      ${resetAction}
    </div>`;
  return `
    <div class="preferences-search-row">
      <input type="search" class="preferences-search" data-preferences-search value="${esc(preferencesSearchText)}" placeholder="${esc(t('pref.searchPlaceholder'))}" aria-label="${esc(t('pref.searchPlaceholder'))}">
      <button type="button" class="preferences-search-button" data-preferences-search-action>${esc(t('pref.searchButton'))}</button>
    </div>
    <div class="preferences-path-rows">${preferencesPathRowsHtml()}${readonly}</div>
    <div class="preferences-sections">${sections}</div>
    ${resetBlock}`;
}
function createPreferencesPanel() {
  const panel = document.createElement('article');
  panel.className = 'panel preferences-panel';
  panel.id = panelDomId(prefsItemId);
  panel.innerHTML = `
      <div class="panel-head preferences-panel-head">
        ${virtualPanelControlsHtml(prefsItemId)}
        <div class="pane-tabs" role="tablist" aria-label="${esc(t('pane.tabs.aria'))}"></div>
      </div>
      <div class="pane-info-bar panel-detail-row">
        <div class="pane-info-bar-copy panel-copy">
          <div id="panel-tab-${prefsItemId}" class="panel-session-label"><span class="session-button-dir">${esc(t('tab.preferences'))}</span></div>
          <div id="meta-${prefsItemId}" class="pane-info-bar-meta meta">${esc(preferenceStatusText())}</div>
        </div>
        <button type="button" class="panel-detail-close" data-detail-toggle="${esc(prefsItemId)}" title="${esc(t('pane.details.hide'))}" aria-label="${esc(t('pane.details.hide'))}"></button>
      </div>
      <div class="preferences-body panel-overlay-root">
        <div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div>
        <div class="preferences-scroll">${preferencesPanelHtml()}</div>
      </div>`;
  bindPanelShell(panel, prefsItemId);
  bindPreferencesPanel(panel);
  return panel;
}

function focusPreferencesSearch(panel = null) {
  // never steal focus into the search box while a tab is being dragged — focus() during a
  // drag (and the re-render it triggers) aborts the native drag.
  if (dragSession != null) return false;
  const root = panel && panel.isConnected !== false
    ? panel
    : (Array.from(document.querySelectorAll('.preferences-panel')).find(candidate => candidate.offsetParent !== null) || document.querySelector('.preferences-panel'));
  const search = root?.querySelector?.('[data-preferences-search]');
  if (!search) return false;
  search.focus?.({preventScroll: true});
  const position = String(search.value || '').length;
  search.setSelectionRange?.(position, position);
  return true;
}

function preferencesScrollIsActive(now = Date.now()) {
  return Number(now) < preferencesScrollActiveUntil;
}

function schedulePreferencesScrollFlush() {
  if (preferencesScrollFlushTimer) clearTimeout(preferencesScrollFlushTimer);
  preferencesScrollFlushTimer = setTimeout(() => {
    preferencesScrollFlushTimer = null;
    if (!pendingPreferencesRender) return;
    if (preferencesScrollIsActive()) {
      schedulePreferencesScrollFlush();
      return;
    }
    pendingPreferencesRender = false;
    renderPreferencesPanels();
  }, preferencesScrollRenderDeferMs);
}

function notePreferencesScrollActivity(now = Date.now()) {
  preferencesScrollActiveUntil = Math.max(preferencesScrollActiveUntil, Number(now) + preferencesScrollRenderDeferMs);
  schedulePreferencesScrollFlush();
}

function renderPreferencesPanels(options = {}) {
  // defer Preferences re-render while a tab drag is in flight; rebuilding the dragged tab
  // node aborts the native HTML5 drag.
  if (dragSession != null) { pendingPreferencesRender = true; return; }
  if (options.force !== true && preferencesScrollIsActive()) {
    pendingPreferencesRender = true;
    schedulePreferencesScrollFlush();
    return;
  }
  for (const panel of document.querySelectorAll('.preferences-panel')) {
    const body = panel.querySelector('.preferences-body');
    const meta = panel.querySelector(`#meta-${cssEscape(prefsItemId)}`);
    if (meta) meta.textContent = preferenceStatusText();
    if (body) {
      const activeControl = activePreferenceControl(panel);
      const shouldKeepDom = activeControl && options.force !== true;
      // the scroller is the inner .preferences-scroll, not the overlay-root body.
      const scroller = () => body.querySelector('.preferences-scroll') || body;
      const prevScroll = scroller();
      const scrollTop = prevScroll.scrollTop;
      const scrollLeft = prevScroll.scrollLeft;
      if (shouldKeepDom) {
        const pathRows = body.querySelector('.preferences-path-rows');
        if (pathRows) pathRows.innerHTML = `${preferencesPathRowsHtml()}${readOnlyMode && !shareViewMode ? `<span class="preferences-readonly">${esc(t('pref.readonly'))}</span>` : ''}`;
      } else {
        body.innerHTML = `<div id="panel-toasts-${prefsItemId}" class="panel-toast-stack"></div><div class="preferences-scroll">${preferencesPanelHtml()}</div>`;
      }
      if (options.focusSearch !== true) {
        const restore = () => { const s = scroller(); s.scrollTop = scrollTop; s.scrollLeft = scrollLeft; };
        restore();
        requestAnimationFrame(restore);
      }
    }
    bindPreferencesPanel(panel);
    autosizePreferenceTextareas(panel);
    if (options.focusSearch) focusPreferencesSearch(panel);
  }
  if (shareViewMode && typeof scheduleShareScrollRestoreByKey === 'function') {
    scheduleShareScrollRestoreByKey('preferences');
  }
}

function autosizePreferenceTextarea(textarea) {
  if (!textarea || textarea.dataset.settingAutosize !== 'true') return;
  const maxRows = Number(textarea.dataset.settingMaxItems || textarea.getAttribute('rows') || 0);
  textarea.style.height = 'auto';
  let height = textarea.scrollHeight;
  if (Number.isFinite(maxRows) && maxRows > 0) {
    const style = window.getComputedStyle?.(textarea);
    const lineHeight = Number.parseFloat(style?.lineHeight || '');
    const paddingTop = Number.parseFloat(style?.paddingTop || '0') || 0;
    const paddingBottom = Number.parseFloat(style?.paddingBottom || '0') || 0;
    const borderTop = Number.parseFloat(style?.borderTopWidth || '0') || 0;
    const borderBottom = Number.parseFloat(style?.borderBottomWidth || '0') || 0;
    if (Number.isFinite(lineHeight) && lineHeight > 0) {
      const maxHeight = Math.ceil((lineHeight * maxRows) + paddingTop + paddingBottom + borderTop + borderBottom);
      height = Math.min(height, maxHeight);
      textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : '';
    }
  }
  textarea.style.height = `${height}px`;
}

function clampPreferenceListControl(control) {
  const maxItems = Number(control?.dataset?.settingMaxItems || 0);
  if (!Number.isFinite(maxItems) || maxItems <= 0) return;
  const lines = String(control.value || '').split('\n');
  const kept = [];
  let used = 0;
  for (const line of lines) {
    if (line.trim()) {
      if (used >= maxItems) continue;
      used += 1;
    }
    kept.push(line);
  }
  const next = kept.join('\n');
  if (next !== control.value) control.value = next;
}

function autosizePreferenceTextareas(root) {
  root.querySelectorAll?.('textarea[data-setting-autosize="true"]').forEach(autosizePreferenceTextarea);
}

function bindPreferencesPanel(panel) {
  if (!panel || panel.dataset.preferencesBound === 'true') return;
  panel.dataset.preferencesBound = 'true';
  panel.addEventListener('input', event => {
    const search = event.target.closest('[data-preferences-search]');
    if (search && panel.contains(search)) {
      preferencesSearchText = search.value || '';
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true, focusSearch: true});
      scheduleShareUiStatePublish();
      return;
    }
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control)) return;
    if (control.dataset.settingAutosize === 'true') {
      if (control.dataset.settingType === 'list') clampPreferenceListControl(control);
      autosizePreferenceTextarea(control);
    }
    if (control.dataset.settingType === 'number') {
      validatePreferenceNumberControl(control);
      return;
    }
    if (control.dataset.settingType === 'range') {
      const value = valueFromPreferenceControl(control);
      const output = control.parentElement?.querySelector('.preferences-range-value');
      if (output) output.textContent = String(control.value);
      if (control.dataset.settingPath === 'appearance.inactive_pane_opacity') applyInactivePaneOpacity(value);
      if (control.dataset.settingPath === 'appearance.pane_ring_opacity') applyPaneRingOpacity(value);
    }
  });
  panel.addEventListener('change', event => {
    const control = event.target.closest('[data-setting-path]');
    if (!control || !panel.contains(control)) return;
    savePreferenceControl(control);
  });
  panel.addEventListener('wheel', event => {
    if (event.target.closest?.('.preferences-scroll')) notePreferencesScrollActivity();
  }, {passive: true});
  panel.addEventListener('touchmove', event => {
    if (event.target.closest?.('.preferences-scroll')) notePreferencesScrollActivity();
  }, {passive: true});
  panel.addEventListener('scroll', event => {
    if (event.target?.classList?.contains('preferences-scroll')) notePreferencesScrollActivity();
  }, true);
  panel.addEventListener('focusout', () => {
    setTimeout(() => {
      if (!activePreferenceControl(panel)) renderPreferencesPanels();
    }, 0);
  });
  panel.addEventListener('click', async event => {
    const searchAction = event.target.closest('[data-preferences-search-action]');
    if (searchAction && panel.contains(searchAction)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true});
      focusPreferencesSearch(panel);
      return;
    }
    const resetAll = event.target.closest('[data-preferences-reset-all]');
    if (resetAll && panel.contains(resetAll)) {
      event.preventDefault();
      preferencesResetConfirmVisible = true;
      renderPreferencesPanels({force: true});
      setTimeout(() => {
        const confirm = document.querySelector('[data-preferences-reset-confirm]');
        confirm?.scrollIntoView?.({block: 'nearest', inline: 'nearest'});
        confirm?.focus?.();
      }, 0);
      return;
    }
    const resetConfirm = event.target.closest('[data-preferences-reset-confirm]');
    if (resetConfirm && panel.contains(resetConfirm)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      resetAllPreferences();
      return;
    }
    const resetCancel = event.target.closest('[data-preferences-reset-cancel]');
    if (resetCancel && panel.contains(resetCancel)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      renderPreferencesPanels({force: true});
      return;
    }
    const copyText = event.target.closest('[data-copy-text]');
    if (copyText && panel.contains(copyText)) {
      event.preventDefault();
      copyTextToClipboard(copyText.dataset.copyText || '')
        .then(() => { statusEl.textContent = 'copied text'; })
        .catch(error => { statusErr(localizedHtml('status.copyFailed', {error})); });
      return;
    }
    const yoloRuleOpen = event.target.closest('[data-yolo-rule-open]');
    if (yoloRuleOpen && panel.contains(yoloRuleOpen)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      openYoloRuleFile();
      return;
    }
    const sectionToggle = event.target.closest('[data-preference-section-toggle]');
    if (sectionToggle && panel.contains(sectionToggle)) {
      event.preventDefault();
      preferencesResetConfirmVisible = false;
      const title = sectionToggle.dataset.preferenceSectionToggle || '';
      if (collapsedPreferenceSections.has(title)) collapsedPreferenceSections.delete(title);
      else collapsedPreferenceSections.add(title);
      writeStoredCollapsedPreferenceSections();
      const section = sectionToggle.closest('[data-preference-section]');
      const collapsed = collapsedPreferenceSections.has(title);
      if (section) {
        section.classList.toggle(CLS.collapsed, collapsed);
        sectionToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
        setDisclosureTriangleElement(sectionToggle.querySelector('.preferences-section-caret'), !collapsed);
        const settings = section.querySelector('.preferences-settings');
        if (settings) settings.hidden = collapsed;
      } else {
        renderPreferencesPanels({force: true});
      }
      scheduleShareUiStatePublish();
      return;
    }
    const reset = event.target.closest('[data-setting-reset]');
    if (!reset || !panel.contains(reset)) return;
    event.preventDefault();
    resetPreference(reset.dataset.settingReset || '');
  });
}
