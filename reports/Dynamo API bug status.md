# Track Three Dynamo API Reports Clearly

DIS-3084 now contains the investigation, multiline repros, links to Dan’s Slack discussion, and identified PRs; its description was updated and read back on October 8, 2026 PT. Two defect mechanisms have fixes merged to Dynamo main: SGLang validation aborts propagate as request errors, and oversized stop lists receive a typed 4xx under a 32-ID cap. The top_logprobs=3 report remains supported by current source behavior, with no matching fix PR found and the count contract undecided. NVInfo supplied indexed NvBugs summaries, not full portal records. No live GLM-5.3 QA run or NvBug closure is established. DIS-3084 remains Triage and unassigned. ([DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports); [#15065](https://github.com/ai-dynamo/dynamo/pull/15065); [#14462](https://github.com/ai-dynamo/dynamo/pull/14462); [main SHA](https://github.com/ai-dynamo/dynamo/commit/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e))

## Two error paths have fixes; one output bound remains open

| NvBug | Done | Still open |
|---|---|---|
| [6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381) | PR #15065 by luckyq merged Sep 22, 2026, 1:41 PM PT. It maps SGLang HTTP 400 abort metadata to InvalidArgument before placeholder success; tests cover token and text paths. | Verify schema and regex cases on the intended release image. Check non-streaming HTTP status and streaming terminal SSE behavior. The mechanism match does not prove closure or a 1.5.1 backport. |
| [6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470) | PR #14462 by flpanbin merged Sep 14, 2026, 6:53 PM PT. Current main enforces one 32-ID cap and typed invalid-argument handling in validation and extraction. | Resolve whether 599 IDs are invalid and should receive 4xx (title) or valid and should be accepted and honored (expected result). Test the chosen contract on the intended image and /v1/completions route. |
| [6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379) | Source at main SHA f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e confirms the positive-count converter appends the selected token if absent from backend candidates. No matching fix PR found. | Decide whether top_logprobs is a strict maximum including the selected token. If yes, cap output and add a regression. Otherwise clarify the API contract and resolve against it. |

The inspected default branch is main at f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e. Source findings do not establish that the same code is in the reported 1.5.1 image. Merged PRs do not establish release backports or NvBug resolution. ([repository metadata](https://api.github.com/repos/ai-dynamo/dynamo); [commit](https://github.com/ai-dynamo/dynamo/commit/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e))

## NVInfo summaries supply symptoms, with evidence limits

All three NVInfo custom_nvbugs summaries name Rui Shao as author and show Dev - Open - To fix, last modified October 7, 2026 PT. They are not full NVBugsPro records: the portal pages were not opened, and summaries omit complete payloads, raw responses, attachments, request IDs, and fix owners. DIS-3084 was updated and read back to distinguish NVInfo indexed summaries from full portal records; full portal records remain unverified. ([6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381); [6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379); [6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470); [DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports))

### 6884381: invalid schema and malformed regex

The indexed summary reports HTTP 200 with content:null for both malformed cases on B200 Dynamo 1.5.1; native SGLang 0.5.18 did not reproduce under the stated matching configuration. The first request is exact:

<pre>
POST /v1/chat/completions
Content-Type: application/json
</pre>

<pre>
{
  "model": "zai-org/GLM-5.3",
  "messages": [
    {"role": "user", "content": "Return a JSON object."}
  ],
  "max_tokens": 512,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "invalid_schema",
      "schema": {"type": "invalid_type"}
    }
  }
}
</pre>

Expected: HTTP 400. Reported actual: HTTP 200 with null content. The second case is only partially recoverable: change the schema type to string and add pattern "[unclosed[" to a string property. The indexed summary omits the property name and full schema; it cannot support an exact original request. PR #15065’s worker regression covers abort status 400 and the invalid_type compiler message. Its documented transport behavior is HTTP 400 before streaming headers, or terminal SSE error after headers. ([NvBug 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381); [PR #15065](https://github.com/ai-dynamo/dynamo/pull/15065))

### 6884379: four alternatives for top_logprobs=3

The record title names Dynamo 1.5.0, while the summary says the issue was reconfirmed on 1.5.1. Preserve both test points. Three fresh B200 requests reportedly had 2, 14, and 3 token positions with four alternatives. Exact request:

<pre>
POST /v1/chat/completions
Content-Type: application/json
</pre>

<pre>
{
  "model": "zai-org/GLM-5.3",
  "messages": [
    {"role": "user", "content": "Say hello."}
  ],
  "max_tokens": 512,
  "logprobs": true,
  "top_logprobs": 3
}
</pre>

Expected check: every choices[0].logprobs.content[*].top_logprobs array has at most three entries. Raw responses, oversized positions, alternative values, and native-control count are absent from the summary. Source inspection gives a plausible mechanism, not a new inference repro: the Chat path calls convert_backend_top_logprobs for positive counts; it copies candidates then appends a missing selected token. PR #14510 by luckyq fixes explicit zero while preserving positive-count fallback. PR #15521 by neilkg changes raw Completions serialization, not this Chat count behavior. ([NvBug 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379); [converter](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai.rs#L299); [Chat caller](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/chat_completions/delta.rs#L130); [#14510](https://github.com/ai-dynamo/dynamo/pull/14510); [#15521](https://github.com/ai-dynamo/dynamo/pull/15521))

### 6880470: oversized stop_token_ids

The indexed report describes non-streaming /v1/completions on B200 and H200 Dynamo 1.5.1 disaggregated deployments. IDs 1 through 599 reportedly returned HTTP 500. Native SGLang 0.5.18 returned HTTP 200 and finish_reason stop in 3/3 attempts, stopping on listed IDs 421, 279, and 198. Request shape:

<pre>
POST /v1/completions
Content-Type: application/json
</pre>

<pre>
{
  "model": "zai-org/GLM-5.3",
  "prompt": "Write a short sentence about Paris.",
  "max_tokens": 512,
  "stream": false,
  "stop_token_ids": [
    1, 2, 3,
    "...",
    598, 599
  ]
}
</pre>

The excerpt above is abbreviated and not valid JSON; it illustrates the indexed-summary request shape only. DIS-3084 now contains the exact, fully expanded 1-through-599 request payload to use for reproduction. ([DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports)) Criteria conflict: title expects 4xx for an invalid oversized list, body expects acceptance and stopping on a listed token. Current main caps at 32 and returns typed invalid argument for 33 or more. Merged tests accept IDs 0–31 and reject 0–32 with “Maximum of 32 stop token IDs allowed, got 33.” DIS-3084’s five/33-ID checks test that boundary, not the original 599-ID Completions case. ([NvBug 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470); [validator](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/validate.rs#L169); [extractor](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai.rs#L224); [#14462 tests](https://github.com/ai-dynamo/dynamo/commit/9062a667024c88565c30066e7c48003a8c4e60a4))

## Linear and recipe notes record triage, not QA

DIS-3084 is a research/triage record, not an assigned implementation task. On October 8, 2026 PT it was Triage, unassigned, and labeled frontend, Bug, QA Bug. It links all three NvBugs, PRs/authors, multiline requests, and Slack context. Dan’s reply said the reports did not block the recipe; the QA release discussion called them known limitations. Neither establishes a final owner. The Linear issue has zero comments and attachments, so it contains no later assignment or QA sign-off. ([DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports); [Dan’s reply](https://nvidia.enterprise.slack.com/archives/C06850J381Y/p1791431480969229?thread_ts=1791431053.755549&cid=C06850J381Y); [QA discussion](https://nvidia.enterprise.slack.com/archives/C0ABS6Z8KHP/p1791431225747059?thread_ts=1791221998.745359&cid=C0ABS6Z8KHP))

Recipe PR #15590 by shugandhi merged October 8, 2026 at 9:21 AM PT. It updated recipes and documentation, not worker/API code for these reports. Its GLM-5.3 README records all three symptoms as known issues from the 1.5.1 recipe context. It is not proof those defects remain in main: #15065 and #14462 merged earlier. The README says more than 32 stop IDs while calling the request valid, conflicting with current source. PR #14462 description says limit four, but merged constant and tests say 32. Source and tests establish the current implementation. ([#15590 metadata](https://api.github.com/repos/ai-dynamo/dynamo/pulls/15590); [README](https://github.com/ai-dynamo/dynamo/blob/c9cc96dae64c04b3e33d3c97e1b3ed1f2bcd7c42/recipes/glm-5.3/README.md#L125); [#14462 source](https://github.com/ai-dynamo/dynamo/blob/9062a667024c88565c30066e7c48003a8c4e60a4/lib/llm/src/protocols/openai/validate.rs#L89))

## Assign QA and settle the API contracts

Name an owner for the count-contract decision and release verification. Preserve each QA request, image digest, route, status and headers, full response or SSE frames, request ID, and relevant logs in Linear. Run the two 6884381 schema variants separately; recover the missing full regex schema or label a new case as derived. For 6884379, record token-level array lengths across repeated requests and compare selected-token inclusion to the chosen contract. For 6880470, get the reporter/owner to choose the expected behavior, then test the full 599-ID Completions case and 32/33 boundary on the target image if the cap is retained. Distinguish current main from Dynamo 1.5.1; source and recipe documentation are not runtime results.

After QA, update DIS-3084 with measured image and disposition. Assign or split 6884379 implementation work if a strict maximum is confirmed. Correct recipe limitation text to match the stop-list decision. No evidence reviewed here establishes an NvBug as closed. ([DIS-3084](https://linear.app/nvidia/issue/DIS-3084/api-investigate-glm-53-dynamo-151-validation-and-logprobs-reports); [6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379); [6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470))
