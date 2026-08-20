# v0.7.9 unread-body HTTP response framing

Completed 2026-08-19. The queue closed at 5/5.

## Root cause and fix

The observed 414 was not a long URL. An old browser bundle POSTed the retired `/api/stats-history` route with `Content-Length: 98258`; the terminal response committed without closing while the request body remained unread. Those body bytes were then parsed as the next request line, producing a 414, while smaller unread bodies could hang. The capture had `request_line_complete=false`, so it did not establish a complete oversized request line. The shared `Handler.send_response` owner now checks for an unread body before headers commit and sends `Connection: close`; request-line and authentication limits remain unchanged.

## Evidence

- The focused HTTP selection collected 164 tests: 162 passed and 2 skipped.
- Red/green coverage proved the framing owner: seven positive regressions failed without the fix while the negative control survived; all eight passed with it.
- The final runtime candidate `71ef69fac` passed the unmodified canonical gate with 9/9 functional lanes and 7/7 certification units in 602.86 seconds, then fast-forwarded into clean local main.
- On a fresh authenticated server at that exact SHA, one 98,258-byte unread-body POST to retired `/api/stats-history` returned one 404 in 0.001650 seconds with `Connection: close`, reached EOF, left zero trailing bytes, produced zero 414 responses, and did not hang. The listener PID, CWD, SHA, and process-start identity were captured with the result.
- The same identity completed the 90.142-second settle and 603.504-second clean observation with 118 samples and no final integrity failures; its controlled negative phase proved one expected browser Error was accepted, rendered once, solely attributed, and redacted from all five retained/display channels.
- The earlier claim of 172 framing/fixture tests was stale and rejected; the measured focused selection collected 164 tests, and the live implementation owner is `Handler.send_response` and its unread-body close path.

## Closure reconciliation

The source queue still showed one unchecked criterion immediately before archival. It is closed by the retained seven-red/one-negative-control parser/producer result, the final 9/9 plus 7/7 gate, and the fresh authenticated final-SHA unread-body reproduction above; no credential value was retained in any release artifact.
