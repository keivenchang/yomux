# Dynamo API bug PR status

## Which merged PRs address the three reported behaviors?

### Takeaway

The SGLang grammar-error propagation and stop-list error-mapping fixes are merged. The positive-count chat logprob converter still appends the selected token when it is absent from backend candidates. [SGLang fix](https://github.com/ai-dynamo/dynamo/pull/15065), [stop-list fix](https://github.com/ai-dynamo/dynamo/pull/14462), [current converter](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai.rs#L299).

### Cited Findings

| Reported behavior | PR and author | Verified merged time in PT | Merge commit |
|---|---|---|---|
| 6884381: malformed schema or regex receives success | [#15065: fix(sglang): propagate validation aborts as request errors](https://github.com/ai-dynamo/dynamo/pull/15065), luckyq | September 22, 2026, 1:41:15 PM PT | [70e094f650ab89eb81d359825adcb78fc9256aba](https://github.com/ai-dynamo/dynamo/commit/70e094f650ab89eb81d359825adcb78fc9256aba) |
| 6880470: oversized stop list produces 500 | [#14462: fix(llm): align stop-sequence limits and return 400](https://github.com/ai-dynamo/dynamo/pull/14462), flpanbin | September 14, 2026, 6:53:19 PM PT | [9062a667024c88565c30066e7c48003a8c4e60a4](https://github.com/ai-dynamo/dynamo/commit/9062a667024c88565c30066e7c48003a8c4e60a4) |

- PR #15065 states that non-streaming requests using schema type `invalid_type` or pattern `[unclosed[` change from HTTP 200 with a successful stop and unrelated token to HTTP 400 with the compiler message. Once streaming headers have been sent, HTTP stays 200 and the error is sent as a terminal SSE event. It explicitly adds no semantic frontend schema precheck. [PR description](https://github.com/ai-dynamo/dynamo/pull/15065).
- PR #14462 explains that early stop validation and later extraction had inconsistent limits, and a generic extraction failure became HTTP 500. Its final diff adds a shared limit and typed invalid-argument errors. [PR diff](https://github.com/ai-dynamo/dynamo/pull/14462/files).
- The #14462 description and generated summary still say the limit is four. The merged implementation and its boundary tests use the shared constant 32. Use the source for the actual limit. [Merged constant](https://github.com/ai-dynamo/dynamo/blob/9062a667024c88565c30066e7c48003a8c4e60a4/lib/llm/src/protocols/openai/validate.rs#L89), [PR description](https://github.com/ai-dynamo/dynamo/pull/14462).
- PR #14510, authored by luckyq and merged September 9, 2026, 10:02:07 PM PT, fixes explicit `top_logprobs: 0`. It expressly preserves sampled-token fallback for positive or omitted counts, so it does not remove the positive-count three-to-four behavior. [PR #14510](https://github.com/ai-dynamo/dynamo/pull/14510).
- PR #15521, authored by neilkg and merged October 5, 2026, 11:19:53 PM PT, changes raw Completions top-logprob serialization into token maps and preserves selected-token inclusion. Its scope is not the Chat alternatives-count report. [PR #15521](https://github.com/ai-dynamo/dynamo/pull/15521).

- PR [#15590](https://github.com/ai-dynamo/dynamo/pull/15590), authored by `shugandhi`, is closed and merged October 8, 2026, 9:21:20 AM PT. Its current title is `fix(recipes): glm-5.2: move SGLang B200/H200 recipes to stock Dynamo 1.5.1 (SGLang 0.5.18, Mooncake PD, H200 decode KV fix)`; merge commit is [c9cc96dae64c04b3e33d3c97e1b3ed1f2bcd7c42](https://github.com/ai-dynamo/dynamo/commit/c9cc96dae64c04b3e33d3c97e1b3ed1f2bcd7c42). [PR metadata](https://api.github.com/repos/ai-dynamo/dynamo/pulls/15590).
- #15590 updates recipe runtime images, deployment configuration, performance documentation, catalog tests, and recipe paths. Its changed files do not alter the frontend or worker API implementation. It documents these API limitations; it does not implement their fixes. [Final changed-file list](https://github.com/ai-dynamo/dynamo/pull/15590/files).
- The merged recipe README lists invalid schema/regex HTTP 200 with null content, three-to-four alternatives, and HTTP 500 for more than 32 `stop_token_ids`. Its assertion that the last request is valid conflicts with the inspected current-main limit of 32. This documentation is a record of the recipe limitation, not proof that main still has the same error-mapping defect. [Merged known issues, lines 125-127](https://github.com/ai-dynamo/dynamo/blob/c9cc96dae64c04b3e33d3c97e1b3ed1f2bcd7c42/recipes/glm-5.3/README.md#L125), [current validator](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/validate.rs#L169).

### Inferences

- #15065 and #14462 address the corresponding defect mechanisms. Their public descriptions do not directly identify the three NVBug numbers, so this is a mechanism match rather than a verified NVBug closure. [#15065](https://github.com/ai-dynamo/dynamo/pull/15065), [#14462](https://github.com/ai-dynamo/dynamo/pull/14462).

### Gaps

- GitHub searches for each exact NVBug number returned no matching public issue or PR. No NVBug closure state or release backport is established by these PRs alone.
- These checks read GitHub metadata and source; they do not rerun a model or verify the deployed release image.

## What does the current default branch implement?

### Takeaway

The default branch is `main`; the inspected immutable head is `f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e`. Both error fixes are present, and the chat converter retains selected-token fallback for positive counts. [Repository metadata](https://api.github.com/repos/ai-dynamo/dynamo), [inspected commit](https://github.com/ai-dynamo/dynamo/commit/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e).

### Cited Findings

- `MAX_STOP_SEQUENCES` is 32. The early extra-field validator rejects a `stop_token_ids` array longer than 32 with a typed invalid-argument error, and the shared extractor enforces the same limit. [Constant and validator](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/validate.rs#L169), [extractor](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai.rs#L224).
- `_raise_if_sglang_error` raises `InvalidArgument(message)` for status-400 aborts before placeholder output; both token and text response paths call it. Ordinary aborts without error metadata retain cancellation behavior. [Helper](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/components/src/dynamo/sglang/request_handlers/llm/decode_handler.py#L75), [token path](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/components/src/dynamo/sglang/request_handlers/llm/decode_handler.py#L1039), [text path](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/components/src/dynamo/sglang/request_handlers/llm/decode_handler.py#L1204).
- `convert_backend_top_logprobs` copies all backend candidates and appends the selected token when its token ID was absent. The Chat caller bypasses this only for explicit zero; positive and omitted counts still call it. [Converter](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai.rs#L299), [Chat caller](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/chat_completions/delta.rs#L130).

### Inferences

- Three backend candidates whose IDs exclude the selected token still become four public alternatives. This follows from the inspected converter and positive-count call path; it is not fresh GPU inference. [Converter and caller](https://github.com/ai-dynamo/dynamo/blob/f7ada8d74e4ee5b4429e476b06ad89a9d5e0680e/lib/llm/src/protocols/openai/chat_completions/delta.rs#L130).

### Gaps

- Presence on main does not prove inclusion in the deployed 1.5.1 image. Image provenance and release-branch status need a separate check.

## What exact cases are already recorded in the fixes?

### Takeaway

The merged tests cover 32/33 stop-ID boundaries and preservation of typed grammar-abort errors. They provide precise regression inputs without claiming a new live reproduction. [Stop tests](https://github.com/ai-dynamo/dynamo/commit/9062a667024c88565c30066e7c48003a8c4e60a4), [abort tests](https://github.com/ai-dynamo/dynamo/commit/70e094f650ab89eb81d359825adcb78fc9256aba).

### Cited Findings

- The stop validator accepts IDs `0` through `31` and rejects IDs `0` through `32`. The exact asserted error is `InvalidArgument: Maximum of 32 stop token IDs allowed, got 33`; both legacy integer `stop` and passthrough `stop_token_ids` are covered. [Tests in merged commit](https://github.com/ai-dynamo/dynamo/commit/9062a667024c88565c30066e7c48003a8c4e60a4).
- The grammar-abort fixture has placeholder `output_ids: [101]`, text `_color`, and `finish_reason` containing type `abort`, status code 400, error type `BadRequestError`, and the compiler message identifying unsupported type `invalid_type`. Both token and text iterators must raise `InvalidArgument` before emitting the placeholder or notifying first-token timing. [Merged worker regression](https://github.com/ai-dynamo/dynamo/commit/70e094f650ab89eb81d359825adcb78fc9256aba).
- A neighboring case emits token `42` or text `hello`, then the abort. It preserves the earlier delta, raises the validation error, and ends the iterator without a successful terminal chunk. [Merged worker regression](https://github.com/ai-dynamo/dynamo/commit/70e094f650ab89eb81d359825adcb78fc9256aba).

### Inferences

- Acceptance should distinguish pre-header non-streaming rejection from post-header streaming error delivery. An HTTP-200 assertion alone cannot decide whether streaming error handling is correct. [Documented streaming behavior](https://github.com/ai-dynamo/dynamo/pull/15065).

### Gaps

- No upstream tests were executed during this PR-status research. The cited test inputs and assertions were inspected directly.
