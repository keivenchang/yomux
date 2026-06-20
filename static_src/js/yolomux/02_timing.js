// Hardcoded frontend timing values live here. Settings-backed intervals stay in settings.py and are read through initialSetting/numberSetting.
const uiDelayMs = Object.freeze({
  shareViewerStatusBackupRefresh: 30000,
  shareHostStatusBackupRefresh: 3000,
  shareRemoteResizeAfterSocketOpen: 50,
});

const yolomuxTiming = Object.freeze({
  shareDebugProfileUploadMinIntervalMs: 5000,
  // Non-settings fallback polls use odd cadences by preference; see docs/DEVELOPMENT.md.
  autoApproveDisconnectedPollMs: 5003,
  shareViewerStatusBackupRefreshMs: uiDelayMs.shareViewerStatusBackupRefresh,
  shareHostStatusBackupRefreshMs: uiDelayMs.shareHostStatusBackupRefresh,
  shareRemoteResizeAfterSocketOpenMs: uiDelayMs.shareRemoteResizeAfterSocketOpen,
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
