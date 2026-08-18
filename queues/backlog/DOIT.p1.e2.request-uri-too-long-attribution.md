# DOIT.p1.e2.request-uri-too-long-attribution.md - Attribute JSON-Fragment 414 Requests

## Goal

Identify the client, method, framing, and complete request line responsible for observed `414 Request-URI Too Long` responses whose retained request-line fragments included `":0,"` and `":1,"`.

## Plan

- [ ] Reproduce or capture one live 414 with timestamp, peer, user agent, method, raw bounded request-line bytes, route/parser state, and browser/network initiator without retaining credentials.
- [ ] Prove or disprove each producer from current evidence. `jsDebugStatsSampleQuery()` is seven scalar parameters and about 160 characters, so do not attribute the event to `/api/stats-sample` without the framing join.
- [ ] Add one exact regression at the proven owner and preserve normal HTTP parser/authentication limits; do not widen request-line limits or change parsing from fragments alone.

## Done Criteria

- [ ] The DONE record contains one joined failing request and names the exact producer and framing defect, or records a bounded no-product-defect conclusion after every plausible owner is eliminated.
- [ ] Any fix has red/green parser/producer tests, the canonical gate, and a restarted authenticated reproduction with no credential retention.
