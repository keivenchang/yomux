// SPDX-FileCopyrightText: Copyright (c) 2026 Keiven Chang. All rights reserved.
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Authenticated raw-byte fetch and Blob URL lifecycle shared by file preview surfaces.

function rawFileUrl(path, params = {}) {
  const queryParts = [`path=${encodeURIComponent(path)}`];
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue;
    queryParts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return `/api/fs/raw?${queryParts.join('&')}`;
}

function rawFileMediaVersion(state) {
  const identity = physicalFileIdentityFromPayload(state);
  const mtime = String(state?.mtime_ns ?? state?.mtime ?? 0);
  const size = state?.size == null ? null : Number(state.size);
  return JSON.stringify([identity, mtime, size]);
}

function rawFileFailureFallback(status, path) {
  if (status === 401) return {key: 'auth.error.authenticationRequired', params: {}, fallback: 'Authentication required.'};
  if (status === 404) return {key: 'common.pathNotFound', params: {path}, fallback: `path not found: ${path}`};
  // Only claim the file is oversized when nothing more specific came back; the server's own message
  // wins otherwise, so a Git budget refusal is not relabelled as a size problem.
  if (status === 413) return {key: 'editor.fileTooLargeTitle', params: {}, fallback: 'File is too large to preview'};
  return {key: 'common.requestFailed', params: {}, fallback: 'request failed'};
}

async function rawFileFailureResult(response, path) {
  const status = Number(response?.status || 0);
  const payload = typeof response?.json === 'function' ? await response.json().catch(() => ({})) : {};
  return {ok: false, status, error: userMessageSnapshot(payload, rawFileFailureFallback(status, path))};
}

async function fetchRawFileBlob(path, options = {}) {
  try {
    const response = await apiFetch(rawFileUrl(path, options.params || {}), {
      cache: 'no-store',
      // Visible preview bytes are a user-facing point read. They must not sit behind long-poll
      // startup work in the browser-wide refresh coordinator.
      startupImmediate: true,
      deadlineMs: options.deadlineMs || apiFetchLongOperationDeadlineMs,
      ...(options.signal ? {signal: options.signal} : {}),
    }, {returnUnauthorizedResponse: true, abortRetirementReason: 'raw_file_media_replaced'});
    if (!response.ok) return rawFileFailureResult(response, path);
    const blob = await response.blob();
    return {
      ok: true,
      status: Number(response.status || 200),
      blob,
      contentType: String(response.headers?.get?.('Content-Type') || blob?.type || ''),
      contentDisposition: String(response.headers?.get?.('Content-Disposition') || ''),
    };
  } catch (error) {
    if (error?.name === 'AbortError') return {ok: false, aborted: true, status: 0, error: null};
    const status = Number(error?.status || 0);
    return {ok: false, status, error: userMessageSnapshot(error, rawFileFailureFallback(status, path))};
  }
}

function releaseRawFileMediaSource(media) {
  media?._rawFileAbortController?.abort?.();
  if (media) media._rawFileAbortController = null;
  media?._rawFileReadyCleanup?.();
  if (media) media._rawFileReadyCleanup = null;
  if (media?._rawFileErrorHandler) media.removeEventListener?.('error', media._rawFileErrorHandler);
  if (media) media._rawFileErrorHandler = null;
  const objectUrl = String(media?._rawFileObjectUrl || '');
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  if (media) media._rawFileObjectUrl = '';
}

function rawFileImageFailureResult(path, installed) {
  return {
    ...installed,
    ok: false,
    status: 0,
    decodeFailed: true,
    error: userMessageSnapshot({}, {key: 'preview.image.loadFailed', params: {}, fallback: `Image could not be loaded: ${path}`}),
  };
}

function rawFileImageReadiness(media, path, installed, options = {}) {
  let settle = null;
  const promise = new Promise((resolve, reject) => {
    let promiseSettled = false;
    let failureReported = false;
    const cleanup = () => {
      media.removeEventListener?.('load', handleLoad);
      media.removeEventListener?.('error', handleError);
      if (media._rawFileReadyCleanup === cancel) media._rawFileReadyCleanup = null;
    };
    const resolveOnce = value => {
      if (promiseSettled) return;
      promiseSettled = true;
      resolve(value);
    };
    const handleError = () => {
      if (failureReported) return;
      failureReported = true;
      cleanup();
      const failure = rawFileImageFailureResult(path, installed);
      releaseRawFileMediaSource(media);
      Promise.resolve(options.onDecodeFailure?.(failure.error, failure)).then(
        () => resolveOnce(failure),
        reject,
      );
    };
    const handleLoad = () => {
      if (Number(media.naturalWidth || 0) <= 0 || Number(media.naturalHeight || 0) <= 0) {
        handleError();
        return;
      }
      media.removeEventListener?.('load', handleLoad);
      resolveOnce(installed);
    };
    const cancel = () => {
      cleanup();
      resolveOnce({...installed, ok: false, status: 0, aborted: true, error: null});
    };
    settle = handleLoad;
    media._rawFileReadyCleanup = cancel;
    media.addEventListener('load', handleLoad, {once: true});
    media.addEventListener('error', handleError, {once: true});
  });
  return {promise, settle};
}

async function installRawFileMediaSource(media, path, options = {}) {
  if (!media || !path) return {ok: false, status: 0, error: null};
  releaseRawFileMediaSource(media);
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  media._rawFileAbortController = controller;
  const result = await fetchRawFileBlob(path, {params: options.params, signal: controller?.signal, deadlineMs: options.deadlineMs});
  if (media._rawFileAbortController !== controller || options.isCurrent?.() === false) return result;
  media._rawFileAbortController = null;
  if (!result.ok) {
    if (!result.aborted) await options.onFailure?.(result.error, result);
    return result;
  }
  const objectUrl = URL.createObjectURL(result.blob);
  if (options.isCurrent?.() === false) {
    URL.revokeObjectURL(objectUrl);
    return {...result, stale: true};
  }
  media._rawFileObjectUrl = objectUrl;
  const installed = {...result, objectUrl};
  if (media.tagName === 'IMG' && typeof media.addEventListener === 'function') {
    const readiness = rawFileImageReadiness(media, path, installed, options);
    media.src = objectUrl;
    if (media.complete && Number(media.naturalWidth || 0) > 0 && Number(media.naturalHeight || 0) > 0) readiness.settle();
    return readiness.promise;
  }
  if (typeof options.onDecodeFailure === 'function') {
    let decodeFailurePromise = null;
    const handleDecodeFailure = () => {
      if (media._rawFileErrorHandler !== handleDecodeFailure) return decodeFailurePromise;
      releaseRawFileMediaSource(media);
      decodeFailurePromise = Promise.resolve(options.onDecodeFailure());
      return decodeFailurePromise;
    };
    media._rawFileErrorHandler = handleDecodeFailure;
    media.addEventListener?.('error', handleDecodeFailure, {once: true});
  }
  media.src = objectUrl;
  if (typeof media.decode === 'function') {
    try {
      await media.decode();
    } catch (_) {
      await media._rawFileErrorHandler?.();
    }
  }
  return installed;
}

function releaseRawFileMediaSources(root) {
  for (const media of Array.from(root?.querySelectorAll?.('img, audio, video') || [])) releaseRawFileMediaSource(media);
}
