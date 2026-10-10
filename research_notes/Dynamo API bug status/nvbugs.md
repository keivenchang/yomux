# NVBugs research: GLM-5.3 Dynamo API reports

Findings below come from authenticated `nvinfo search --query <bug-id> --json` results in the `custom_nvbugs` source. The linked records are NVBugs redirect URLs; the full NVBugsPro pages were not opened in this pass.

## 6884381 — invalid JSON Schema and malformed regex return HTTP 200

### Takeaway
The indexed report confirms that both malformed structured-output cases returned HTTP 200 with `content: null` on a B200 Dynamo 1.5.1 deployment, while the expected result is HTTP 400; the issue remains open for a fix. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)

### Cited Findings
- Record title: `[LLM][GLM-5.3][Dynamo 1.5.1][API] Chat accepts invalid JSON Schema type and malformed regex with HTTP 200`; author Rui Shao; action `Dev - Open - To fix`; last modified Oct 7, 2026, 9:07 AM PDT. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The issue summary says Dynamo Chat Completions accepts an invalid JSON Schema type or an unclosed regex pattern and returns HTTP 200 with null content. It says the behavior was reconfirmed on Dynamo 1.5.1 and does not reproduce on native SGLang 0.5.18 with the same runtime image and model configuration. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The first reproduction sends `POST /v1/chat/completions` with model `zai-org/GLM-5.3`, user content `Return a JSON object.`, `max_tokens: 512`, and `response_format.type: json_schema`; its named schema is `invalid_schema` and its schema body is `{"type":"invalid_type"}`. The expected result is HTTP 400. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The second reproduction is described as replacing the schema type with `string` and adding `pattern: "[unclosed["` to a string property. The actual result says the verified Dynamo 1.5.1 B200 deployment returned HTTP 200 with `content: null` for both malformed schemas. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The report lists NVIDIA B200 and H200 reference configurations with driver 615.71.09 and 183359 MiB per B200 GPU / 143771 MiB per H200 GPU; the recorded runtime image is `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.5.1@sha256:37badffd83eb334aee9d06fb442e36fc3c963314336c92622c78ac5c037d514d`. The actual-result sentence specifically identifies a B200 Dynamo deployment. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The stated impact is that invalid structured-output requests are reported as successful instead of being rejected at the API boundary. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)

### Inferences
- The source gives two distinct validation regressions under one report: invalid schema type and invalid regex syntax. The malformed regex case is not merely another spelling of the invalid-type case because the steps explicitly change the type to `string` and add a `pattern`. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)

### Gaps
- The indexed result does not preserve the full second JSON Schema object or the string property name, so the `pattern` fragment is source-confirmed but a fully exact second JSON request cannot be reconstructed from this result alone. The result also does not expose the complete HTTP response body, an error payload, or request IDs. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)
- The available indexed fields do not give a fix owner, target release, or resolution date; the action remains `Dev - Open - To fix` as of the record’s stated last-modified time. [NVBugs 6884381](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884381)

## 6884379 — `top_logprobs=3` returns four alternatives

### Takeaway
The report provides a compact, exact Chat Completions reproducer and repeated 1.5.1 evidence: three fresh B200 requests had 2, 14, and 3 token positions with four alternatives despite a requested maximum of three; the record remains open. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)

### Cited Findings
- Record title names Dynamo 1.5.0; author Rui Shao; action `Dev - Open - To fix`; last modified Oct 7, 2026, 9:09 AM PDT. The description says the issue was reconfirmed on Dynamo 1.5.1 and does not reproduce on native SGLang 0.5.18 with the same runtime image and model configuration. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)
- Exact reproduction: `POST /v1/chat/completions`, `Content-Type: application/json`, body with model `zai-org/GLM-5.3`, messages `[ {"role":"user","content":"Say hello."} ]`, `max_tokens: 512`, `logprobs: true`, and `top_logprobs: 3`. The report says to count each element in `choices[0].logprobs.content[*].top_logprobs`; expected: every such array has no more than three entries. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)
- Actual results: three fresh B200 Dynamo 1.5.1 requests each had four-entry positions; respectively, 2, 14, and 3 token positions exceeded the requested limit. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)
- The report says this violates the requested upper bound and can break clients that validate the OpenAI-compatible response. Its environment section records NVIDIA B200/H200 reference configurations, driver 615.71.09, and runtime image digest `sha256:37badffd83eb334aee9d06fb442e36fc3c963314336c92622c78ac5c037d514d`. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)

### Inferences
- The evidence points to a per-token output-bound violation rather than a request-level rejection: the request succeeds far enough to return token-level logprob arrays, and only some positions exceed the requested count. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)

### Gaps
- The indexed summary does not include the raw responses, which token positions were oversized, exact alternative values, or native-control counts. It says native SGLang does not reproduce, but does not state a number of control requests. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)
- The title’s 1.5.0 version and body’s 1.5.1 confirmation describe different test points; the indexed record does not say which release first introduced the behavior or identify a fix owner/target release. [NVBugs 6884379](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6884379)

## 6880470 — oversized `stop_token_ids` produces HTTP 500

### Takeaway
The report says Dynamo 1.5.1 returns HTTP 500 for a Completions request carrying every integer stop token ID from 1 through 599 on both B200 and H200 disaggregated deployments. Its expected-results text conflicts with the title: the title says the malformed input should produce 4xx, while the body expects it to be accepted and stop generation like native SGLang. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)

### Cited Findings
- Record title: `[LLM][GLM-5.3][Dynamo 1.5.1][API] Invalid oversized stop_token_ids returns HTTP 500 instead of 4xx`; author Rui Shao; action `Dev - Open - To fix`; last modified Oct 7, 2026, 9:11 AM PDT. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- Reproduction steps specify a non-streaming `POST /v1/completions` request with model `zai-org/GLM-5.3`, prompt `Write a short sentence about Paris.`, `max_tokens: 512`, `stream: false`, and `stop_token_ids` set to the integer sequence 1 through 599 inclusive. The record’s displayed JSON excerpt abbreviates the middle of that array as `"..."`; it is not a paste-ready JSON literal. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- Actual result: both B200 and H200 Dynamo 1.5.1 disaggregated deployments returned HTTP 500 through the same internal-error response path. Native SGLang 0.5.18 returned HTTP 200 with `finish_reason=stop` in 3/3 attempts and stopped on listed IDs 421, 279, and 198. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- Expected-results text says the request is accepted and generation stops when a listed token is produced, matching native SGLang. The issue summary likewise says native SGLang 0.5.18 accepts the same request and stops on a listed token. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- The report lists B200 and H200 reference configurations with driver 615.71.09 and 183359 MiB per B200 GPU / 143771 MiB per H200 GPU; its hardware excerpt mentions an 8-GPU configuration without clearly tying the count to each GPU type. The runtime image is `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.5.1@sha256:37badffd83eb334aee9d06fb442e36fc3c963314336c92622c78ac5c037d514d`. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)

### Inferences
- Before implementation or acceptance criteria are finalized, the reporter or owner needs to resolve whether the contract is “reject this oversized list with a 4xx” or “accept the list and stop on a listed token”; the title and expected-results section cannot both define the same desired behavior. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)

### Gaps
- The available record excerpt does not include the full 599-element JSON body, the exact HTTP 500 response body, or the underlying stack/error detail; it only says the same internal-error response path occurred on both GPU deployments. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
- The indexed fields do not identify a fix owner or target release; the action remains `Dev - Open - To fix` as of the recorded update. [NVBugs 6880470](https://nvbugswb.nvidia.com/NVBugs5/redir.aspx?url=/6880470)
