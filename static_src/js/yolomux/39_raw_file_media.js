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

function rawFileFailureFallback(status, path) {
  if (status === 401) return {key: 'auth.error.authenticationRequired', params: {}, fallback: 'Authentication required.'};
  if (status === 404) return {key: 'common.pathNotFound', params: {path}, fallback: `path not found: ${path}`};
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
      deadlineMs: options.deadlineMs || apiFetchLongOperationDeadlineMs,
      ...(options.signal ? {signal: options.signal} : {}),
    }, {returnUnauthorizedResponse: true});
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
  if (media?._rawFileErrorHandler) media.removeEventListener?.('error', media._rawFileErrorHandler);
  if (media) media._rawFileErrorHandler = null;
  const objectUrl = String(media?._rawFileObjectUrl || '');
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  if (media) media._rawFileObjectUrl = '';
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
    if (!result.aborted) options.onFailure?.(result.error, result);
    return result;
  }
  const objectUrl = URL.createObjectURL(result.blob);
  if (options.isCurrent?.() === false) {
    URL.revokeObjectURL(objectUrl);
    return {...result, stale: true};
  }
  media._rawFileObjectUrl = objectUrl;
  if (typeof options.onDecodeFailure === 'function') {
    const handleDecodeFailure = () => {
      if (media._rawFileErrorHandler !== handleDecodeFailure) return;
      releaseRawFileMediaSource(media);
      options.onDecodeFailure();
    };
    media._rawFileErrorHandler = handleDecodeFailure;
    media.addEventListener?.('error', handleDecodeFailure, {once: true});
  }
  media.src = objectUrl;
  if (typeof media.decode === 'function') {
    try {
      await media.decode();
    } catch (_) {
      media._rawFileErrorHandler?.();
    }
  }
  return {...result, objectUrl};
}

function releaseRawFileMediaSources(root) {
  for (const media of Array.from(root?.querySelectorAll?.('img, audio, video') || [])) releaseRawFileMediaSource(media);
}
