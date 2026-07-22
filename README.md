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
        │                                                      ▲
        ▼                                                      │ post-back
  ┌───────────────────────────── FastAPI (app/main.py) ───────┴──────────────┐
  │  POST /webhooks/github   HMAC-SHA256 verify + replay guard + rate limit   │
  │  POST /reviews/{id}/approve   human-in-the-loop decision (resume)         │
  │  GET  /reviews/{id}   status + trace     GET /healthz   GET /metrics       │
  └───────────────────────────────────┬──────────────────────────────────────┘
                                       │ start / resume
                                       ▼
        ┌──────────────── LangGraph StateGraph (app/graph.py) ───────────────┐
        │  plan ─▶ retrieve ─▶ draft ─▶ critique ─┐                           │
        │                        ▲                │ eval fails & attempts<max │
        │                        └────────────────┘  (retry / guardrail)      │
        │                                 │ eval ok / capped                   │
        │                                 ▼                                    │
        │                          await_approval                             │
        │                                 ┆  ⏸  interrupt_before=[finalize]    │
        │                                 ▼         (human-in-the-loop gate)   │
        │                            finalize ──▶ post-back + audit write      │
        └──────────┬───────────────────────────────────────────┬─────────────┘
                   │                                            │
                   ▼                                            ▼
   retrieval.py (TF-cosine over            audit.py (append-only JSONL,
   data/corpus/*.md, cited by id)          hash-chained → verify_chain())
                   │                                            
                   ▼                                            
   tracing.py (Langfuse spans when configured, else offline no-op with timing)
```

## What each capability maps to in this repo

| Capability | Where it lives | Backed by |
|---|---|---|
| LangGraph agentic workflow (state, retries, guardrails) | `app/graph.py` (`plan→retrieve→draft→critique→await_approval→finalize`, `interrupt_before`) | `tests/test_graph.py` |
| Output validation | Pydantic models validated at every node | `tests/test_output_validation.py`, `tests/test_graph.py::test_malformed_draft_fails_closed` |
| Retry / guardrail behavior | critique→draft loop capped at `MAX_ATTEMPTS` | `tests/test_graph.py::test_retry_path_recovers_a_weak_first_draft`, `::test_guardrail_caps_retries` |
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

**End-to-end demo** (signed webhook → workflow → HITL approval → audit record):

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

## Claims are backed by tests

Every capability the README claims is either exercised by a test in `tests/` or
labeled above as TODO / not-yet-wired. Two are worth calling out because they are
the easy ones to fake:

- **The eval gate is not vacuous.** `tests/test_evals.py` mutates the `draft`
  prompt asset (removing the instruction to use the retrieved standards), reruns
  the golden set, and asserts the pass rate drops below threshold, i.e. the gate
  *would reject* the regression. A gate that passes no matter what proves nothing.
- **The audit chain actually detects tampering.** `tests/test_audit.py` edits and
  reorders written records and asserts `verify_chain()` returns `False`.

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

## License

MIT. See [LICENSE](LICENSE).
