# Sample runs

This project is **provider-agnostic** -- the same workflow runs on a deterministic
mock, on Anthropic, or on OpenAI, with only the `app/llm.py` provider swapping.
This file collects a capture from each. Nothing about the orchestration, the HITL
gate, the eval gate, the traceability schema, or the audit log changes between
providers; only the drafted analysis and the LLM-judge verdict come from a live
model instead of the mock.

- **MOCK run** -- deterministic, offline, no API key.
- **Anthropic run** -- live `claude-opus-4-8`.
- **OpenAI run** -- live `gpt-4o`.
- **Langfuse-enabled run** -- live Anthropic with real LLM observability: each
  review exports as one trace to a Langfuse project, and the demo prints the
  trace URL.

Capturing all four is deliberate: it shows the same governed, auditable pipeline
producing a comparable, fully-traced result across two model vendors, and shipping
real observability spans when Langfuse is configured.

The MOCK capture below is produced by `python scripts/run_demo.py`. It exercises
the full path: a signed GitHub webhook, signature + replay validation, the
LangGraph workflow pausing at the human-in-the-loop gate, human approval, and the
tamper-evident audit record.

> The `record_hash` and `review_id` differ per run (random review id, timestamps
> in the hashed payload); everything else is stable.

## MOCK run -- `python scripts/run_demo.py`

```text
====================================================================
1. Signed GitHub webhook -> POST /webhooks/github
====================================================================
HTTP 202
{
  "review_id": "rev_ba8decbbac82",
  "status": "awaiting_approval",
  "message": "review started; awaiting human approval"
}

====================================================================
2. Review paused at HITL gate -> GET /reviews/{id}
====================================================================
status           : awaiting_approval
prompt versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
eval verdict     : passed=True score=0.9
trace spans      :
    - plan           0.024 ms
    - retrieve       0.504 ms
    - draft          0.087 ms
    - critique       0.039 ms
langfuse active  : False (offline no-op tracer records timing locally)

====================================================================
3. Human approves -> POST /reviews/{id}/approve
====================================================================
final status : approved
posted comment:
    [APPROVED] Automated standards review for acme/flight-controls#128

    The change is assessed against the retrieved standards. It is broadly compliant but has gaps that need follow-up before merge.

    Findings: 3; risk=medium; sources=['CMP-STD', 'SEC-STD', 'REL-STD']; approver=release-manager-1

====================================================================
4. Tamper-evident audit record (append-only, hash-chained)
====================================================================
records in log   : 1
review_id        : rev_ba8decbbac82
provider/model   : mock / mock-deterministic-v1
prompt_versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved_sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
approver         : release-manager-1 (approve)
status           : approved
prev_hash        : 0000000000000000...
record_hash      : daed928b85e81668...
chain verifies   : True

====================================================================
Done -- fully offline, deterministic mock provider.
====================================================================
```

## Anthropic run -- `AGENT_PROVIDER=anthropic python scripts/run_demo.py`

Verbatim capture against **`claude-opus-4-8`** (a live `ANTHROPIC_API_KEY`, a few
cents). Same orchestration, HITL gate, eval gate, and audit chain as the mock --
only the drafted analysis and the judge verdict are live-model text. The judge
scored the live draft 0.95; the `review_id` and `record_hash` differ per run.

```text
Provider: anthropic / claude-opus-4-8

====================================================================
1. Signed GitHub webhook -> POST /webhooks/github
====================================================================
HTTP 202
{
  "review_id": "rev_6d1553d443c6",
  "status": "awaiting_approval",
  "message": "review started; awaiting human approval"
}

====================================================================
2. Review paused at HITL gate -> GET /reviews/{id}
====================================================================
status           : awaiting_approval
prompt versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
eval verdict     : passed=True score=0.95
trace spans      :
    - plan           9217.218 ms
    - retrieve       0.623 ms
    - draft          6295.171 ms
    - critique       3031.081 ms
langfuse active  : False (offline no-op tracer records timing locally)

====================================================================
3. Human approves -> POST /reviews/{id}/approve
====================================================================
final status : approved
posted comment:
    [APPROVED] Automated standards review for acme/flight-controls#128

    The proposed change was assessed against the retrieved compliance, security, and reliability standards. No concrete change details or artifacts (approval records, tests, migration plans) were provided in the context, so compliance cannot be affirmatively verified against any standard. Findings below identify the mandatory controls each standard requires and flag them as unverified pending evidence.

    Findings: 3; risk=high; sources=['CMP-STD', 'SEC-STD', 'REL-STD']; approver=release-manager-1

====================================================================
4. Tamper-evident audit record (append-only, hash-chained)
====================================================================
records in log   : 1
review_id        : rev_6d1553d443c6
provider/model   : anthropic / claude-opus-4-8
prompt_versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved_sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
approver         : release-manager-1 (approve)
status           : approved
prev_hash        : 0000000000000000...
record_hash      : ff0ef75a84208aa5...
chain verifies   : True

====================================================================
Done -- provider: anthropic / claude-opus-4-8
====================================================================
```

## OpenAI run -- `AGENT_PROVIDER=openai python scripts/run_demo.py`

Verbatim capture against **`gpt-4o`**, with Langfuse also active. Identical
orchestration, HITL gate, eval gate, and audit chain to the Anthropic run -- only
the provider and the live-model text differ. The two vendors reach comparable but
not identical verdicts (exact findings/risk/score vary per run since the models
are non-deterministic), which is exactly the point of a provider-agnostic,
evaluated pipeline: the governance is constant, the model is swappable. The trace
URL is redacted below (see "A note on the trace URLs").

```text
Provider: openai / gpt-4o

====================================================================
1. Signed GitHub webhook -> POST /webhooks/github
====================================================================
HTTP 202
{
  "review_id": "rev_141eb8346e68",
  "status": "awaiting_approval",
  "message": "review started; awaiting human approval"
}

====================================================================
2. Review paused at HITL gate -> GET /reviews/{id}
====================================================================
status           : awaiting_approval
prompt versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
eval verdict     : passed=True score=1.0
trace spans      :
    - plan           4213.859 ms
    - retrieve       0.349 ms
    - draft          3632.422 ms
    - critique       1723.492 ms
langfuse active  : True (sending spans to Langfuse)

====================================================================
3. Human approves -> POST /reviews/{id}/approve
====================================================================
final status : approved
posted comment:
    [APPROVED] Automated standards review for acme/flight-controls#128

    The proposed code change complies with some standards but has notable deficiencies regarding security and reliability requirements.

    Findings: 3; risk=high; sources=['CMP-STD', 'SEC-STD', 'REL-STD']; approver=release-manager-1

====================================================================
4. Tamper-evident audit record (append-only, hash-chained)
====================================================================
records in log   : 1
review_id        : rev_141eb8346e68
provider/model   : openai / gpt-4o
prompt_versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved_sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
approver         : release-manager-1 (approve)
status           : approved
prev_hash        : 0000000000000000...
record_hash      : 4c8915dc98b8e363...
chain verifies   : True

====================================================================
5. Langfuse trace (LLM observability)
====================================================================
langfuse active  : True
trace url        : https://us.cloud.langfuse.com/project/<your-project>/traces/<trace-id>

====================================================================
Done -- provider: openai / gpt-4o
====================================================================
```

## Langfuse-enabled run -- live Anthropic + real observability

Same command, with `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` /
`LANGFUSE_HOST` set (US region). `langfuse active` is now **True**, and the run
prints a real trace URL. Each review exports as one Langfuse trace whose spans
are `plan -> retrieve -> draft -> critique -> finalize` (verified by fetching the
trace back from the Langfuse API). The trace URL below is redacted (see
"A note on the trace URLs").

```text
Provider: anthropic / claude-opus-4-8

====================================================================
1. Signed GitHub webhook -> POST /webhooks/github
====================================================================
HTTP 202
{
  "review_id": "rev_7488d62163af",
  "status": "awaiting_approval",
  "message": "review started; awaiting human approval"
}

====================================================================
2. Review paused at HITL gate -> GET /reviews/{id}
====================================================================
status           : awaiting_approval
prompt versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
eval verdict     : passed=True score=0.92
trace spans      :
    - plan           9978.689 ms
    - retrieve       0.988 ms
    - draft          6520.073 ms
    - critique       3750.109 ms
langfuse active  : True (sending spans to Langfuse)

====================================================================
3. Human approves -> POST /reviews/{id}/approve
====================================================================
final status : approved
posted comment:
    [APPROVED] Automated standards review for acme/flight-controls#128

    The proposed change was reviewed against the retrieved compliance, security, and reliability standards. However, no description of the actual code change or its implementation details was provided in the context, so compliance cannot be affirmatively verified. Findings below identify the mandatory controls each applicable standard requires and flag them as unverified.

    Findings: 4; risk=high; sources=['CMP-STD', 'SEC-STD', 'REL-STD']; approver=release-manager-1

====================================================================
4. Tamper-evident audit record (append-only, hash-chained)
====================================================================
records in log   : 1
review_id        : rev_7488d62163af
provider/model   : anthropic / claude-opus-4-8
prompt_versions  : {'plan': 'v1', 'draft': 'v1', 'critique': 'v1'}
retrieved_sources: ['CMP-STD', 'SEC-STD', 'REL-STD']
approver         : release-manager-1 (approve)
status           : approved
prev_hash        : 0000000000000000...
record_hash      : 8d482b6a48d6cce4...
chain verifies   : True

====================================================================
5. Langfuse trace (LLM observability)
====================================================================
langfuse active  : True
trace url        : https://us.cloud.langfuse.com/project/<your-project>/traces/<trace-id>

====================================================================
Done -- provider: anthropic / claude-opus-4-8
====================================================================
```

## A note on the trace URLs

The Langfuse trace URLs in this file are redacted to
`https://us.cloud.langfuse.com/project/<your-project>/traces/<trace-id>`. The
project id and per-run trace ids are not secrets (no API keys), but they point at
a private Langfuse project, so they are kept out of version control. The real
URLs from these captures are recorded in `SAMPLE_RUN.traces.local.md`, which is
gitignored. Every run prints its own live trace URL to the console.
