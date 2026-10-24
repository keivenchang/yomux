function activePreferenceControl(panel) {
  const active = document.activeElement;
  if (!active || !panel?.contains(active)) return null;
  return active.closest?.('[data-setting-path], [data-preferences-search], [data-preferences-search-action], [data-preference-section-toggle], [data-preferences-reset-all], [data-preferences-reset-confirm], [data-preferences-reset-cancel]') || null;
}

function clampPreferenceNumber(item, value) {
  // item.scale lets a field display a human unit (e.g. MB) while storing raw (bytes); min/max are in
  // display units, so the stored default is divided by scale to compare in the same space.
  const scale = Number(item.scale) || 1;
  const fallback = Number(preferenceDefault(item.path)) / scale;
  const parsed = Number(value);
  let number = Number.isFinite(parsed) ? parsed : fallback;
  if (Number.isFinite(Number(item.min))) number = Math.max(Number(item.min), number);
  if (Number.isFinite(Number(item.max))) number = Math.min(Number(item.max), number);
  if (!Number.isFinite(number)) return '';
  return Number.isInteger(number) ? String(number) : String(number);
}

function validatePreferenceNumberControl(control) {
  const path = control.dataset.settingPath || '';
  const item = preferenceItemByPath(path);
  if (!item || item.type !== 'number') return true;
  const value = Number(control.value);
  const min = Number(item.min);
  const max = Number(item.max);
  let message = '';
  if (!Number.isFinite(value)) message = t('validation.enterNumber');
  else if (Number.isFinite(min) && value < min) message = t('validation.minimum', {min});
  else if (Number.isFinite(max) && value > max) message = t('validation.maximum', {max});
  control.setCustomValidity(message);
  return !message;
}

function settingPatch(path, value) {
  const parts = String(path || '').split('.').filter(Boolean);
  const root = {};
  let current = root;
  parts.forEach((part, index) => {
    if (index === parts.length - 1) current[part] = value;
    else {
      current[part] = {};
      current = current[part];
    }
  });
  return root;
}

function codexModelDefaultEffort(model) {
  const metadata = typeof settingChoiceMetadata === 'function' ? settingChoiceMetadata('yoagent.codex_model') : {};
  const entry = metadata[String(model || '')] || {};
  const effort = String(entry.default_effort || '').trim();
  return effort || '';
}

function settingPatchForPath(path, value) {
  if (path === 'file_explorer.index_exclude_paths') return quickOpenExclusionSettingPatch(value);
  const patch = settingPatch(path, value);
  if (path === 'appearance.global_font_size' && boolSetting('appearance.sync_font_sizes', true)) {
    patch.appearance = {
      ...(patch.appearance || {}),
      terminal_font_size: value,
      editor_font_size: value,
      preview_font_size: value,
      file_explorer_font_size: syncedFinderFontSize(value),
      ui_font_size: value,
    };
  }
  if (path === 'appearance.sync_font_sizes' && value === true) {
    const size = preferenceValue('appearance.global_font_size');
    patch.appearance = {
      ...(patch.appearance || {}),
      terminal_font_size: size,
      editor_font_size: size,
      preview_font_size: size,
      file_explorer_font_size: syncedFinderFontSize(size),
      ui_font_size: size,
    };
  }
  if (path === 'yoagent.codex_model') {
    const defaultEffort = codexModelDefaultEffort(value);
    if (defaultEffort) patch.yoagent = {...(patch.yoagent || {}), codex_effort: defaultEffort};
  }
  return patch;
}

function valueFromPreferenceControl(control) {
  const type = control.dataset.settingType || 'text';
  if (type === 'boolean') return control.checked === true;
  if (type === 'number' || type === 'range') {
    const item = preferenceItemByPath(control.dataset.settingPath || '');
    if (!item) return Number(control.value);
    const clamped = clampPreferenceNumber(item, control.value);
    control.value = clamped;
    validatePreferenceNumberControl(control);
    const scale = Number(item.scale) || 1;
    return Number(clamped) * scale;
  }
  if (type === 'list') {
    const maxItems = Number(control.dataset.settingMaxItems || 0);
    const items = String(control.value || '').split('\n').map(line => line.trim()).filter(Boolean);
    return Number.isFinite(maxItems) && maxItems > 0 ? items.slice(0, maxItems) : items;
  }
  return control.value;
}

async function saveSettingsPatch(patch, options = {}) {
  if (readOnlyMode) return;
  const payload = await apiFetchJson('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({settings: patch}),
  });
  // Preserve navigation made while the save was in flight when applying settings changes the layout.
  const preservedLayout = options.preserveLayout === false ? null : cloneLayoutSlots();
  const preservedLayoutSignature = preservedLayout ? layoutSlotsSignature(preservedLayout) : '';
  const preservedFocus = focusedPanelItem;
   applySettingsPayload(payload, {
     force: true,
     applyEditorDefaults: options.applyEditorDefaults === true,
     forcePreferences: options.forcePreferences === true,
   });
  if (preservedLayout && layoutSlotsSignature() !== preservedLayoutSignature) {
    applyLayoutSlots(preservedLayout, {
      focusSession: preservedFocus && itemInLayout(preservedFocus, preservedLayout) ? preservedFocus : undefined,
      prune: false,
    });
  }
  refreshYoloRulesStatus({silent: true});
}

function showUploadRsyncRecommendation(options = {}) {
  const command = uploadRsyncExampleCommand();
  const action = makeButton({
    label: t('pref.advisory.copyRsync'),
    onClick: event => {
      event.stopPropagation();
      void copyTextWithFeedback(command, {button: event.currentTarget, statusText: t('upload.copiedRsync')});
    },
  });
  const sizeText = options.sizeBytes ? t('upload.sizeText', {size: formatFileSize(options.sizeBytes)}) : '';
  return emitNotification('fileTransfer', {
    session: options.session, item: options.item || focusedPanelItem || fileExplorerItemId,
    title: t('common.rsyncLargeFiles'), lines: [
      t('upload.toastBody', {sizeText, cap: formatFileSize(uploadMaxBytes)}), command,
    ],
    actions: [action],
    countdownMs: 20000,
  }).inApp;
}

function savePreferenceControl(control) {
  const path = control.dataset.settingPath || '';
  if (control.dataset.settingType === 'number' && !validatePreferenceNumberControl(control)) {
    control.reportValidity?.();
    return;
  }
  const previousValue = preferenceValue(path);
  const value = valueFromPreferenceControl(control);
  // #50: switch the UI language OPTIMISTICALLY on the select change — don't wait for the settings-poll
  // round-trip (same lesson as the theme toggle). applyLocale is async + re-renders every surface.
  if (path === 'general.language') applyLocale(resolveLocalePref(value));
  // #260: apply the global theme live the moment the radio changes (the old theme-cards relied on a
  // re-render; the radio routes through here, so flip body.theme-* + the editor/terminal palettes now).
  if (path === 'appearance.theme') {
    globalThemeMode = normalizeGlobalThemeMode(value);
    applyGlobalThemeMode({updateEditor: true, updateTerminals: true});
    renderSessionButtons();  // keep the View -> Theme active marker in sync with the Preferences radio
  }
  if (path === 'appearance.inactive_pane_opacity') {
    applyInactivePaneOpacity(value);
  }
  if (path === 'appearance.pane_ring_opacity') {
    applyPaneRingOpacity(value);
  }
  if (path === 'appearance.active_color') {
    applyActiveColor(value);
  }
  saveSettingsPatch(settingPatchForPath(path, value), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
    forcePreferences: path === 'appearance.sync_font_sizes',
  })
    .then(() => {
      const scheme = activeEditorScheme();
      if ((path === 'appearance.editor_dark_color_scheme' && scheme.dark)
        || (path === 'appearance.editor_light_color_scheme' && !scheme.dark)) {
        setFileEditorThemeMode(value);
      }
      if (path === 'uploads.max_bytes' && Number(value) > uploadRsyncRecommendationBytes && Number(previousValue) <= uploadRsyncRecommendationBytes) {
        showUploadRsyncRecommendation({sizeBytes: Number(value)});
      }
      showLayoutStatus(t('status.settingSaved', {path}));
    })
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
}

function resetPreference(path) {
  const item = preferenceItemByPath(path);
  if (!item) return;
  saveSettingsPatch(settingPatchForPath(path, preferenceDefault(path)), {
    applyEditorDefaults: path === 'terminal_editor.word_wrap' || path === 'terminal_editor.line_numbers',
  })
    .then(() => { showLayoutStatus(t('status.settingReset', {path})); })
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error: userMessageText(error, t('common.requestFailed'))})); });
}

function resetAllPreferences() {
  if (readOnlyMode) return;
  saveSettingsPatch(mergeSettingObjects({}, clientSettingsDefaults), {applyEditorDefaults: true})
    .then(() => {
      preferencesSearchText = '';
      preferencesResetConfirmVisible = false;
      collapsedPreferenceSections = defaultCollapsedPreferenceSections();
      writeStoredCollapsedPreferenceSections();
      setFileEditorThemeMode(editorThemeInheritMode);
      renderPreferencesPanels({force: true});
      showLayoutStatus(t('status.preferencesResetAll'));
    })
    .catch(error => { statusErr(localizedHtml('status.settingsResetFailed', {error: userMessageText(error, t('common.requestFailed'))})); });
}

function clampEditorPreviewFontSize(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return Math.max(8, Math.min(32, editorFontSize + 1));
  return Math.max(8, Math.min(32, Math.round(number)));
}

function updateEditorPreviewFontControls(scope = document) {
  const value = clampEditorPreviewFontSize(editorPreviewFontSize);
  scope.querySelectorAll?.('.file-editor-preview-font-value')?.forEach(node => {
    node.textContent = `${value}`;
  });
  scope.querySelectorAll?.('[data-editor-preview-font-step]')?.forEach(button => {
    const step = Number(button.dataset.editorPreviewFontStep || 0);
    button.disabled = readOnlyMode || !step || (step < 0 && value <= 8) || (step > 0 && value >= 32);
    button.setAttribute('aria-disabled', button.disabled ? 'true' : 'false');
  });
}

function setEditorPreviewFontSize(value) {
  if (readOnlyMode) return;
  const next = clampEditorPreviewFontSize(value);
  if (next === editorPreviewFontSize) {
    updateEditorPreviewFontControls();
    return;
  }
  editorPreviewFontSize = next;
  applyCssSettings();
  updateEditorPreviewFontControls();
  refreshFilePreviewPopouts();
  saveSettingsPatch(settingPatch('appearance.preview_font_size', next))
    .then(() => { showLayoutStatus(t('status.previewFontSizeSaved')); })
    .catch(error => { statusErr(localizedHtml('status.settingsSaveFailed', {error: userMessageText(error, t('common.requestFailed'))})); refreshSettings({force: true}); });
}

// Finder, Differ, and Tabber share this one panel builder. The item determines the fixed view so
// they may coexist in separate panes without a global mode switch rebuilding one another.
