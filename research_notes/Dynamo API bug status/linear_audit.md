# Linear audit: DIS-3084

## Issue state and tracked artifacts

### Takeaway
DIS-3084 is a substantial research and triage record, not an implementation task with an owner: it is in **Triage**, unassigned, labeled `frontend`, `Bug`, and `QA Bug`. It already contains the investigation, multiline proposed repros, PR authors, and Slack references, but no comments or separate attachments. — [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)

### Cited Findings
- Current title: “[API] Investigate GLM-5.3 / Dynamo 1.5.1 validation and logprobs reports”; last updated October 8, 2026 10:59 AM PT; no assignee; state `Triage`; labels `frontend`, `Bug`, `QA Bug`; zero comments and zero attachments. — [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)
- The description links all three NvBugs and includes source references, proposed multiline requests, PR #14462 and #15065 with author names, PR #15590, and Slack links to Dan’s triage reply and the QA release discussion. — [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)
- The issue itself records that Dan said the reports were not blocking the recipe, Ryan expected work distribution the next morning, and the thread did not establish a final assignee. The QA thread called them known limitations. — [Dan’s Slack reply](https://nvidia.enterprise.slack.com/archives/C06850J381Y/p1791431480969229?thread_ts=1791431053.755549&cid=C06850J381Y); [QA release discussion](https://nvidia.enterprise.slack.com/archives/C0ABS6Z8KHP/p1791431225747059?thread_ts=1791221998.745359&cid=C0ABS6Z8KHP)

### Inferences
- The Slack link requested earlier is present and points to Dan’s reply; the Linear issue has not been formally assigned or advanced out of triage based on its current metadata.
- “Research written up” is done. “All reported behavior fixed and validated” is not established by this issue: it explicitly says no live GLM-5.3 request was run.

### Gaps
- The issue has no comments, so there is no later disposition, assignee decision, or QA sign-off recorded there.
- PR statuses/authors and the referenced current-main SHA were not refreshed as part of this Linear metadata audit; the statuses below are those stated in the issue description.

## Comparison with refreshed NVInfo NvBugs summaries

### Takeaway
NVInfo now returns indexed `custom_nvbugs` summaries for all three records, so the Linear scope statement that NvBugs records “were not available” is stale if read as “no NvBugs evidence was accessible.” Full portal records remain distinct from these short indexed summaries. The summaries broadly support the issue descriptions but expose one title/version mismatch and make the original `stop_token_ids` route and size more specific.

### Cited Findings
- NVInfo indexed 6884381 as accepting an invalid JSON Schema type or unclosed regex in Dynamo Chat Completions, returning HTTP 200 with null content; it says the behavior was reconfirmed on Dynamo 1.5.1 and did not reproduce on native. The indexed record author is Rui Shao, last modified October 7, 2026. — [NvBug 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- NVInfo’s title for 6884379 says **Dynamo 1.5.0**, while its indexed summary says the four-alternative result was reconfirmed on Dynamo 1.5.1 and did not reproduce on native SGLang 0.5.18 with the same runtime image and model. The issue was authored by Rui Shao and last modified October 7, 2026. — [NvBug 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)
- NVInfo indexed 6880470 as a `/v1/completions` report: a `stop_token_ids` list containing IDs 1 through 599 returned HTTP 500 on Dynamo, while native SGLang 0.5.18 accepted the request and stopped on a listed token. The record was authored by Rui Shao and last modified October 7, 2026. — [NvBug 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- Linear already distinguishes the proposed five-ID `/v1/chat/completions` boundary probe from the release README’s `/v1/completions` report using IDs 1 through 599, and labels the proposed JSON requests as not the unavailable original QA bodies. — [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)

### Inferences
- Update the evidence boundary in Linear to say: the direct portal/full records were not available during the earlier investigation, but NVInfo’s indexed summaries are now accessible. Do not describe the summaries as full NvBugs records.
- Preserve the 6884379 filed-title version (1.5.0) separately from the later 1.5.1 reconfirmation; don’t silently rewrite one as the other.
- For 6880470, keep the five-ID Chat Completions request as an isolated source-derived threshold test, not as the original bug repro. The indexed report is specifically a Completions request with 599 IDs.

### Gaps
- NVInfo search returns indexed summaries, not the complete NvBug fields, attachments, QA request bodies, or correlated logs. The portal link itself was not verified as accessible from this audit.
- Exact original requests remain unavailable: 6884381 does not identify whether the regex case used JSON Schema `pattern` or top-level `guided_regex`; 6884379 has no original prompt/response; 6880470 has no complete 599-ID request body in the indexed summary.

## Completion and remaining work

### Takeaway
Two reported classes have merged fixes recorded in Linear; the logprob-count report remains source-confirmed and unresolved in the issue write-up. None of the three has live GLM-5.3 QA verification recorded. The immediate tracking decision is whether to keep this combined research issue in triage, assign the remaining 6884379 work, and create or link implementation/QA follow-ups for the two merged fixes.

### Cited Findings
- **6884381 — implementation recorded done; runtime verification open.** Linear says merged PR #15065 propagates SGLang validation aborts as request errors and covers non-streaming invalid schema-type and malformed-pattern cases; streaming after headers requires an SSE error. Linear says live GLM-5.3 validation was not run. — [PR #15065](https://github.com/ai-dynamo/dynamo/pull/15065); [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)
- **6880470 — implementation recorded done; original-case verification open.** Linear says merged PR #14462 returns `InvalidArgument` for over-limit stop-token lists; the current-main limit is 32, so five IDs are accepted and 33 rejected with HTTP 400. The indexed NvBug summary describes the original `/v1/completions` case with 599 IDs. — [PR #14462](https://github.com/ai-dynamo/dynamo/pull/14462); [NvBug 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470); [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)
- **6884379 — open.** Linear reports the shared converter can append the selected token after forwarding `k` backend alternatives, causing four returned entries for `top_logprobs=3`; it says this remains in current main and records no fix PR. The indexed NvBug summary independently confirms four alternatives and 1.5.1 reconfirmation. — [NvBug 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379); [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)
- **PR #15590 is report/documentation context, not a fix.** Linear describes it as open and says its recipe README documents the symptoms; the issue’s suggested tracking split is to follow up 6884381 and 6880470 if QA still sees them, and separately track 6884379 for a behavior/contract decision. — [PR #15590](https://github.com/ai-dynamo/dynamo/pull/15590); [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)

### Inferences
- To close the research loop, obtain a QA run against the intended current build/image for each original route and preserve the request, status/headers, response or SSE frames, request ID, and relevant logs. For 6880470, include the reported 599-ID Completions case; the 5/33-ID probes answer the limit boundary on a different API contract.
- The remaining code question is the API contract for `top_logprobs`: whether selected-token inclusion counts toward the requested maximum. Once decided, 6884379 needs an implementation and regression test if the maximum is intended to be strict.
- After QA evidence and an owner are recorded, update issue state/disposition and split follow-up work if implementation or validation should be tracked independently.

### Gaps
- No live endpoint results, original full NvBug payloads, or QA logs are recorded in Linear or in the NVInfo indexed summaries reviewed here.
- This audit did not establish the current GitHub state beyond what DIS-3084 records; merged/open status should be refreshed before claiming a present-day PR state.
