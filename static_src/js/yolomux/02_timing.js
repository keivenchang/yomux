// Hardcoded frontend timing values live here. Settings-backed intervals stay in settings.py and are read through initialSetting/numberSetting.
const FILE_TREE_RECENCY_JUST_UPDATED_MAX_AGE_SECONDS = 15;
const uiDelayMs = Object.freeze({
  shareViewerStatusBackupRefresh: 30001,
  shareHostStatusBackupRefresh: 3001,
  shareRemoteResizeAfterSocketOpen: 50,
  serverWatchRenew: 60001,
  serverWatchDebounce: 300,
  tmuxWindowReadback: 120,
  tmuxWindowReadbackRetry: 80,
  tmuxWindowSwitchReveal: 4000,
  terminalRefreshAfterTabSelect: 120,
  fileQuickOpenDebounce: 160,
  commandPaletteMissingPathRetry: 1001,
  clientEventDemandDebounce: 30,
  fileExplorerTypeaheadClear: 700,
  shareGeometryDigestPublish: 2001,
  mobileTerminalKeyRepeatDelay: 360,
  mobileTerminalKeyRepeatInterval: 68,
});

const yolomuxTiming = Object.freeze({
  shareDebugProfileUploadMinIntervalMs: 5000,
  // Non-settings fallback polls use odd cadences by preference; see docs/DEVELOPMENT.md.
  autoApproveDisconnectedPollMs: 5003,
  shareViewerStatusBackupRefreshMs: uiDelayMs.shareViewerStatusBackupRefresh,
  shareHostStatusBackupRefreshMs: uiDelayMs.shareHostStatusBackupRefresh,
  shareRemoteResizeAfterSocketOpenMs: uiDelayMs.shareRemoteResizeAfterSocketOpen,
  serverWatchRenewMs: uiDelayMs.serverWatchRenew,
  serverWatchDebounceMs: uiDelayMs.serverWatchDebounce,
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
  shareGeometryDigestPublishMs: uiDelayMs.shareGeometryDigestPublish,
  mobileTerminalKeyRepeatDelayMs: uiDelayMs.mobileTerminalKeyRepeatDelay,
  mobileTerminalKeyRepeatIntervalMs: uiDelayMs.mobileTerminalKeyRepeatInterval,
  yolomuxFontReadyTimeoutMs: 2500,
  shareReplayKeyframeRequestInitialBackoffMs: 5000,
  shareReplayKeyframeRequestMinIntervalMs: 5000,
  shareReplayKeyframeRequestMaxBackoffMs: 5000,
  shareGeometryResyncMinIntervalMs: 10000,
  shareReplayPostTopologyKeyframeQuietExtraMs: 1000,
  shareTopologyKeyframePointerQuietMs: 500,
});

const {
  shareDebugProfileUploadMinIntervalMs,
  autoApproveDisconnectedPollMs,
  shareViewerStatusBackupRefreshMs,
  shareHostStatusBackupRefreshMs,
  shareRemoteResizeAfterSocketOpenMs,
  serverWatchRenewMs,
  serverWatchDebounceMs,
  tmuxWindowReadbackMs,
  tmuxWindowReadbackRetryMs,
  tmuxWindowSwitchRevealTimeoutMs,
  tmuxWindowSwitchPaintCapMs,
  terminalRefreshAfterTabSelectMs,
  fileQuickOpenDebounceMs,
  commandPaletteMissingPathRetryMs,
  clientEventDemandDebounceMs,
  fileExplorerTypeaheadClearMs,
  shareGeometryDigestPublishMs,
  mobileTerminalKeyRepeatDelayMs,
  mobileTerminalKeyRepeatIntervalMs,
  yolomuxFontReadyTimeoutMs,
  shareReplayKeyframeRequestInitialBackoffMs,
  shareReplayKeyframeRequestMinIntervalMs,
  shareReplayKeyframeRequestMaxBackoffMs,
  shareGeometryResyncMinIntervalMs,
  shareTopologyKeyframePointerQuietMs,
} = yolomuxTiming;
const shareReplayHostKeyframeMinIntervalMs = shareReplayKeyframeRequestMinIntervalMs;
const shareReplayPostTopologyKeyframeQuietMs = shareReplayHostKeyframeMinIntervalMs + yolomuxTiming.shareReplayPostTopologyKeyframeQuietExtraMs;
const shareTopologyKeyframeMaxDeferralMs = shareReplayHostKeyframeMinIntervalMs;
