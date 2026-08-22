# Agentic Review Gate

**Portfolio project:** a self-contained demo of governed, auditable, human-in-the-loop AI workflow orchestration.

_By James Kelly._ MIT licensed. Runs fully offline; no API key needed to try it.

A GitHub webhook (PR or issue) triggers a **LangGraph** agentic workflow that
**plans** a review, **retrieves** the relevant standards from a small local
corpus, **drafts** an analysis, **self-critiques** it with an LLM-as-judge, then
**pauses for human-in-the-loop approval** before it **posts a result** and writes
a **tamper-evident, fully traceable audit record** (prompt versions + model id +
inputs + retrieved sources + eval verdict + approver + timestamp).

The pattern applies anywhere an AI decision has to be trustworthy: regulated or
safety-critical engineering, finance and compliance, healthcare, legal, content
moderation, or any workflow where "an AI approved it" is not good enough and you
need to reconstruct exactly what happened and prove the record was not altered.
The demo uses code review as a concrete, familiar example; the corpus, prompts,
and post-back target are swappable seams. Everything runs **fully offline** on a
deterministic mock provider; the real Anthropic and OpenAI paths are used only
when the matching API key is set.

## Architecture

```
   GitHub PR/issue                                          GitHub
   webhook (signed)                                         comment
        |                                                      ^
        v                                                      | post-back
  +----------------------------- FastAPI (app/main.py) -------+--------------+
  |  POST /webhooks/github   HMAC-SHA256 verify + replay guard + rate limit   |
  |  POST /reviews/{id}/approve   human-in-the-loop decision (resume)         |
  |  GET  /reviews/{id}   status + trace     GET /healthz   GET /metrics       |
  +-----------------------------------+--------------------------------------+
                                       | start / resume
                                       v
        +---------------- LangGraph StateGraph (app/graph.py) ---------------+
        |  plan -> retrieve -> draft -> critique -+                           |
        |                        ^                | eval fails & attempts<max |
        |                        +----------------+  (retry / guardrail)      |
        |                                 | eval ok / capped                   |
        |                                 v                                    |
        |                          await_approval                             |
        |                                 :  [||] interrupt_before=[finalize]   |
        |                                 v         (human-in-the-loop gate)   |
        |                            finalize --> post-back + audit write      |
        +----------+-------------------------------------------+-------------+
                   |                                            |
                   v                                            v
   retrieval.py (TF-cosine over            audit.py (append-only JSONL,
   data/corpus/*.md, cited by id)          hash-chained -> verify_chain())
                   |                                            
                   v                                            
   tracing.py (Langfuse spans when configured, else offline no-op with timing)
```

## What each capability maps to in this repo

| Capability | Where it lives | Backed by |
|---|---|---|
| LangGraph agentic workflow (state, retries, guardrails) | `app/graph.py` (`plan->retrieve->draft->critique->await_approval->finalize`, `interrupt_before`) | `tests/test_graph.py` |
| Output validation | Pydantic models validated at every node | `tests/test_output_validation.py`, `tests/test_graph.py::test_malformed_draft_fails_closed` |
| Retry / guardrail behavior | critique->draft loop capped at `MAX_ATTEMPTS` | `tests/test_graph.py::test_retry_path_recovers_a_weak_first_draft`, `::test_guardrail_caps_retries` |
| Human-in-the-loop (regulated approval) | `interrupt_before=["finalize"]` + `/reviews/{id}/approve` | `tests/test_graph.py::test_resume_approve_finalizes`, `::test_reject_stops` |
| Secure GitHub webhook (HMAC + replay) | `app/security.py` | `tests/test_security.py` |
| Prompt versioning (prompt-as-asset) | `app/prompts/*.md` + `app/prompts.py` `PromptRegistry` | recorded in every `TraceRecord`; used by evals |
| Evaluation + eval-gated CI | `app/evals/{runner,gate}.py`, `golden.jsonl` | `tests/test_evals.py` (incl. a mutation proof) |
| LLM observability | `app/tracing.py` (Langfuse wrapper, offline no-op) | spans asserted in `GET /reviews/{id}` / demo |
| Retrieval + provenance | `app/retrieval.py`, `data/corpus/` | citations flow into the draft and audit record |
| Traceability / audit (tamper-evident) | `app/audit.py` hash-chained JSONL | `tests/test_audit.py` |
| Provider seam (mock/real) | `app/llm.py` `LLMProvider` + `get_provider()` | mock is default; Anthropic and OpenAI imported lazily behind one Protocol |
| Rate limiting | in-memory limiter in `app/main.py` | applied to webhook + approval routes |
| Production hardening | `Dockerfile` (non-root, healthcheck), `.github/workflows/ci.yml`, `infra/main.bicep` | CI runs tests + eval gate |

## Implemented (runs and is tested, fully offline)

- LangGraph state machine with retry/guardrail edges and Pydantic output validation.
- HMAC-SHA256 webhook signature verification (constant-time) + delivery-id replay guard.
- Human-in-the-loop pause/resume via a real LangGraph checkpointer interrupt.
- Prompt registry over versioned `.md` assets; versions recorded per review.
- Local TF-cosine retriever over an in-repo standards corpus, with cited source ids.
- Deterministic mock LLM provider + LLM-as-judge self-critique.
- Eval harness + **eval gate** that fails CI below a pass-rate threshold, proven
  non-vacuous by mutating a prompt and watching the gate fail.
- Append-only, hash-chained audit log with `verify_chain()` tamper detection.
- FastAPI service (`/webhooks/github`, `/reviews/{id}/approve`, `/reviews/{id}`,
  `/healthz`, `/metrics`) with in-memory rate limiting.
- Offline no-op tracer that still records per-step timing; Docker + CI + Bicep.
- **Live model runs on two vendors:** the same workflow has been run against
  Anthropic (`claude-opus-4-8`) and OpenAI (`gpt-4o`); both verbatim captures are
  in `SAMPLE_RUN.md` alongside the mock, demonstrating the provider-agnostic seam.
- **Live Langfuse tracing:** with `LANGFUSE_*` set, each review exports as one
  trace (spans: plan, retrieve, draft, critique, finalize) to a real Langfuse
  project; verified end to end against Langfuse Cloud (US), and the demo prints
  the trace URL. Offline, it degrades to the local no-op tracer with timing.

## TODO / not yet wired (honest scope)
- **Live Azure deploy:** `infra/main.bicep` is IaC demonstrating the Azure
  Container Apps pattern; it has **not** been deployed to a subscription (see
  `infra/README.md`).
- **Real GitHub post-back:** `finalize` calls a `poster` stub; the record and
  comment are produced but not sent to the GitHub API.
- **Durable/multi-replica state:** reviews and the replay guard are in-memory
  (single process); production would use a shared store (documented as swappable).
- Retrieval is a TF-cosine scorer over a tiny corpus, deliberately swappable
  for an embedding/vector store without touching the graph.

## Run it

Python 3.11+.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

> **Every command below assumes the virtualenv is active.** If a command errors
> with `ModuleNotFoundError: No module named 'fastapi'`, the venv isn't active;
> run `source .venv/bin/activate` first, or prefix each command with
> `./.venv/bin/python` (e.g. `./.venv/bin/python scripts/run_demo.py`).

**Tests** (all offline, no API key, no Docker):

```bash
pytest -q          # 34 tests
```

**Eval gate** (what CI runs after the tests):

```bash
python -m app.evals.gate          # exits non-zero if pass rate < threshold
```

**End-to-end demo** (signed webhook -> workflow -> HITL approval -> audit record):

```bash
python scripts/run_demo.py
```

**Serve the API** (optional):

```bash
uvicorn app.main:app --reload      # http://127.0.0.1:8000/healthz
```

**Use a real model** (optional). The same workflow runs on either provider;
only the provider and key change:

```bash
# Anthropic
pip install anthropic
AGENT_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-ant-... python scripts/run_demo.py

# OpenAI
pip install openai
AGENT_PROVIDER=openai OPENAI_API_KEY=sk-... python scripts/run_demo.py
```

Models default per provider (`claude-opus-4-8`, `gpt-4o`) and are overridable
via `ANTHROPIC_MODEL` / `OPENAI_MODEL`, or `AGENT_MODEL` for a universal
override.

## Adapt it to your domain

Code review is just the worked example. The governance skeleton (signed-webhook
intake, plan/retrieve/draft/critique, the human-in-the-loop gate, the eval gate,
and the tamper-evident audit trail) stays the same for any high-stakes review.
Three seams are meant to be swapped:

- **Corpus:** replace `data/corpus/*.md` with your standards, policies, or
  contracts (retrieval and per-source citation carry over unchanged).
- **Prompts:** edit the versioned assets in `app/prompts/` (bump the version so
  every decision stays traceable to the exact prompt that produced it).
- **Post-back target:** the `finalize` node writes back to a GitHub comment;
  point it at Jira, a ticketing system, or a system of record instead.

The webhook source and the model provider are already pluggable.

## Claims backed by tests

Run `pytest -q`. The suite is offline: it needs no API key and no network, and
the deterministic mock provider stands in for a model everywhere.

| Claim | Test |
| --- | --- |
| The eval gate is not vacuous: mutate the draft prompt to drop the retrieved standards and the pass rate falls below threshold | `tests/test_evals.py::test_gate_fails_on_regressed_prompt` |
| The same gate passes on the unmutated prompts, so the failure above is the mutation and not the harness | `tests/test_evals.py::test_gate_passes_on_good_prompts` |
| Editing a written record breaks the audit chain | `tests/test_audit.py::test_tampering_breaks_the_chain` |
| Reordering records breaks it too, which a per-record hash alone would miss | `tests/test_audit.py::test_reordering_breaks_the_chain` |
| The chain verifies across many records, so the check above is not failing for an unrelated reason | `tests/test_audit.py::test_chain_verifies_for_multiple_records` |
| The graph pauses for human approval instead of finalizing | `tests/test_graph.py::test_happy_path_pauses_at_approval` |
| Approving resumes and finalizes; rejecting stops | `tests/test_graph.py::test_resume_approve_finalizes` |
| A malformed draft fails CLOSED rather than passing something through | `tests/test_graph.py::test_malformed_draft_fails_closed` |
| A weak first draft is retried and recovered | `tests/test_graph.py::test_retry_path_recovers_a_weak_first_draft` |
| Retries are capped, so a model that never improves cannot loop forever | `tests/test_graph.py::test_guardrail_caps_retries` |
| A real model wraps JSON in fences or prose, and the parser survives all of it | `tests/test_parse_json.py::test_fenced_json_with_language_tag_parses` |
| Prose around the JSON parses; braces inside string values are preserved | `tests/test_parse_json.py::test_prose_wrapped_json_parses` |
| A reply with no JSON object at all raises rather than returning a default | `tests/test_parse_json.py::test_no_json_object_raises` |
| An empty, placeholder or invalid-risk draft is rejected by output validation | `tests/test_output_validation.py::test_draft_rejects_placeholder_summary` |
| A webhook with a valid signature passes and a wrong secret fails | `tests/test_security.py::test_valid_signature_passes` |
| A tampered body fails, and a non-sha256 scheme is refused | `tests/test_security.py::test_tampered_body_fails` |
| A replayed delivery id is rejected, so a captured webhook cannot be resent | `tests/test_security.py::test_replayed_delivery_id_is_rejected` |

THE TWO WORTH CALLING OUT are the first three rows, because they are the easy
ones to fake. A gate that passes no matter what proves nothing, so the eval
test mutates the prompt asset and requires the gate to REJECT; and an audit
chain that only hashes each record individually would not notice records being
reordered, so that is asserted separately.

## Layout

```
app/
  main.py         FastAPI routes + in-memory rate limiter
  graph.py        LangGraph StateGraph, HITL interrupt, run/resume helpers
  models.py       Pydantic v2 models (state, draft, eval, trace record, ...)
  security.py     HMAC signature verify + replay guard
  llm.py          LLMProvider protocol, MockProvider, lazy Anthropic + OpenAI
  prompts.py      PromptRegistry over versioned prompt assets
  prompts/        plan.v1.md, draft.v1.md, critique.v1.md
  retrieval.py    TF-cosine retriever over the standards corpus
  tracing.py      Langfuse wrapper / offline no-op tracer
  audit.py        append-only, hash-chained audit log + verify_chain()
  evals/          golden.jsonl, runner.py, gate.py (CI eval gate)
data/corpus/      tiny fake "engineering standards" docs (SEC/REL/OBS/CMP)
scripts/run_demo.py   offline end-to-end demo
tests/            pytest suite (security, graph, evals, audit, output validation)
infra/main.bicep  Azure Container Apps IaC (not deployed; see infra/README.md)
```

## Sibling projects

One of several small projects on the theme of AI systems you can trust and
prove, all following the same discipline of claims mapped to tests, mutation
checks on the tests that matter, and behavior verified before publishing:

- [prompt-injection-benchmark](https://github.com/jkelly-dev1/prompt-injection-benchmark) measures
  defenses against a synthetic attack corpus rather than asserting them.
- [ai-data-boundary-proxy](https://github.com/jkelly-dev1/ai-data-boundary-proxy) enforces PII
  egress policy and measures what a right-to-erasure operation misses.
- [llm-eval-gate](https://github.com/jkelly-dev1/llm-eval-gate) measures the
  judges rather than trusting them.
- [federated-retrieval-router](https://github.com/jkelly-dev1/federated-retrieval-router)
  measures whether a routing decision was right and what it cost, and then
  measures its own hand-rolled stores against real ones.
- [hardened-mcp-server](https://github.com/jkelly-dev1/hardened-mcp-server)
  measures which bytes a tool-definition pin has to cover, and finds the
  intuitive policy catching 1 of 8 rug pulls with a perfectly clean
  false-alarm record: a quiet gate that has stopped protecting anything.
- [vlm-extraction-integrity](https://github.com/jkelly-dev1/vlm-extraction-integrity)
  is the opposite failure of the same gate. Its pixel-grounding rung rejects
  0 of 40 correct extractions on three models and 31 of 36 on a fourth, which
  cites a field's caption instead of its value. Catch rate is the number
  people quote; the false-rejection rate is what decides whether it ships.
- [ai-compliance-checker](https://github.com/jkelly-dev1/ai-compliance-checker)
  builds the gate this repo argues for and then measures its price across five
  unrelated regimes: a checker that always answers is wrong 15-36% of the
  time, and one that refuses when the evidence cannot carry a decision is
  never wrong and still decides 13-50%. Those are not two points on one scale,
  so it publishes no accuracy figure at all.
- [least-privilege-agent](https://github.com/jkelly-dev1/least-privilege-agent)
- [citation-abstention-rag](https://github.com/jkelly-dev1/citation-abstention-rag)
- [typed-agent-service](https://github.com/jkelly-dev1/typed-agent-service)
- [temporal-multi-agent](https://github.com/jkelly-dev1/temporal-multi-agent)
- [llm-observability-stack](https://github.com/jkelly-dev1/llm-observability-stack)
- [airgapped-ai-bundle](https://github.com/jkelly-dev1/airgapped-ai-bundle)
- [agent-sandbox-escape](https://github.com/jkelly-dev1/agent-sandbox-escape)
- [parser-eval](https://github.com/jkelly-dev1/parser-eval)

## License

MIT. See [LICENSE](LICENSE).
