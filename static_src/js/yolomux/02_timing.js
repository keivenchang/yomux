// Hardcoded frontend timing values live here. Settings-backed intervals stay in settings.py and are read through initialSetting/numberSetting.
const FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS = 15;
const uiDelayMs = Object.freeze({
  serverWatchDebounce: 300,
  tmuxWindowReadback: 120,
  tmuxWindowReadbackRetry: 80,
  tmuxWindowSwitchReveal: 4000,
  terminalRefreshAfterTabSelect: 120,
  fileQuickOpenDebounce: 160,
  commandPaletteMissingPathRetry: 1001,
  clientEventDemandDebounce: 30,
  fileExplorerTypeaheadClear: 700,
  mobileTerminalKeyRepeatDelay: 360,
  mobileTerminalKeyRepeatInterval: 68,
});

const yolomuxTiming = Object.freeze({
  // Non-settings fallback polls use odd cadences by preference; see docs/DEVELOPMENT.md.
  autoApproveDisconnectedPollMs: 5003,
  // A forced metadata read is answered from the server cache and names the generation of the build
  // that will observe the request. `transcripts_changed` carries that build, but only to a client
  // that demands the transcripts channel, which the default subscription does not. Converge on the
  // named generation with bounded cache reads so "force" means force for every client.
  forcedSessionMetadataSettleTimeoutMs: 8000,
  forcedSessionMetadataSettlePollMs: 151,
  serverWatchDebounceMs: uiDelayMs.serverWatchDebounce,
  serverWatchDebounceMaxDeferralMs: uiDelayMs.serverWatchDebounce * 4,
  tmuxWindowReadbackMs: uiDelayMs.tmuxWindowReadback,
  tmuxWindowReadbackRetryMs: uiDelayMs.tmuxWindowReadbackRetry,
  // Bounded UI wait for the post-confirmation refreshed frame before the explicit
  // `Still loading <target>` Retry/Cancel state (never a silent reveal).
  tmuxWindowSwitchRevealTimeoutMs: uiDelayMs.tmuxWindowSwitchReveal,
  // tmux switches instantly and repaints every attached client, so the new window's
  // bytes are usually ingested behind the mask BEFORE the select POST even resolves.
  // After confirmation, reveal on the next painted frame — or after this short cap when
  // the repaint already landed and no further frame is coming. Display cadence (round).
  tmuxWindowSwitchPaintCapMs: 250,
  terminalRefreshAfterTabSelectMs: uiDelayMs.terminalRefreshAfterTabSelect,
  fileQuickOpenDebounceMs: uiDelayMs.fileQuickOpenDebounce,
  commandPaletteMissingPathRetryMs: uiDelayMs.commandPaletteMissingPathRetry,
  clientEventDemandDebounceMs: uiDelayMs.clientEventDemandDebounce,
  fileExplorerTypeaheadClearMs: uiDelayMs.fileExplorerTypeaheadClear,
  mobileTerminalKeyRepeatDelayMs: uiDelayMs.mobileTerminalKeyRepeatDelay,
  mobileTerminalKeyRepeatIntervalMs: uiDelayMs.mobileTerminalKeyRepeatInterval,
  yolomuxFontReadyTimeoutMs: 2500,
});

const {
  autoApproveDisconnectedPollMs,
  forcedSessionMetadataSettleTimeoutMs,
  forcedSessionMetadataSettlePollMs,
  serverWatchDebounceMs,
  serverWatchDebounceMaxDeferralMs,
  tmuxWindowReadbackMs,
  tmuxWindowReadbackRetryMs,
  tmuxWindowSwitchRevealTimeoutMs,
  tmuxWindowSwitchPaintCapMs,
  terminalRefreshAfterTabSelectMs,
  fileQuickOpenDebounceMs,
  commandPaletteMissingPathRetryMs,
  clientEventDemandDebounceMs,
  fileExplorerTypeaheadClearMs,
  mobileTerminalKeyRepeatDelayMs,
  mobileTerminalKeyRepeatIntervalMs,
  yolomuxFontReadyTimeoutMs,
} = yolomuxTiming;
