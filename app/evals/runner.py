"""Offline eval harness.

Runs the review graph (mock provider, auto-approve) over the golden set and
scores each case with a rubric PLUS the graph's own mock LLM-judge verdict
(`eval_result.passed`). Returns per-case results and an aggregate pass rate.

The registry is injectable so the gate test can point the runner at a *mutated*
prompt directory and prove the eval gate actually catches a regression.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.audit import AuditLog
from app.graph import build_graph, run_review_auto
from app.llm import MockProvider
from app.models import ApprovalDecision, GitHubWebhookEvent, ReviewState
from app.prompts import PromptRegistry

GOLDEN_PATH = Path(__file__).parent / "golden.jsonl"


@dataclass
class CaseResult:
    id: str
    passed: bool
    checks: dict[str, bool]
    score: float


@dataclass
class EvalReport:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if not self.cases:
            return 0.0
        return round(sum(c.passed for c in self.cases) / len(self.cases), 4)

    def summary(self) -> str:
        passed = sum(c.passed for c in self.cases)
        return f"{passed}/{len(self.cases)} cases passed (pass_rate={self.pass_rate})"


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    cases = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _score_case(case: dict, state) -> CaseResult:
    expect = case.get("expect", {})
    draft = state.draft
    evalr = state.eval_result
    checks: dict[str, bool] = {}

    # Rubric checks.
    if "min_findings" in expect:
        checks["min_findings"] = bool(draft and len(draft.findings) >= expect["min_findings"])
    if "must_cite" in expect:
        cited = set(draft.cited_sources) if draft else set()
        retrieved = {c.source_id for c in state.retrieved}
        checks["must_cite"] = expect["must_cite"] in cited or expect["must_cite"] in retrieved
    # Mock LLM-judge verdict (from the graph's critique node).
    if "eval_passed" in expect:
        checks["judge_passed"] = bool(evalr and evalr.passed) == expect["eval_passed"]

    passed = all(checks.values()) if checks else False
    score = round(sum(checks.values()) / len(checks), 4) if checks else 0.0
    return CaseResult(id=case["id"], passed=passed, checks=checks, score=score)


def run_golden(
    registry: PromptRegistry | None = None,
    golden_path: Path = GOLDEN_PATH,
    audit_dir: str | None = None,
) -> EvalReport:
    """Run every golden case through the graph and score it."""
    audit_dir = audit_dir or tempfile.mkdtemp(prefix="eval-audit-")
    report = EvalReport()
    for case in load_golden(golden_path):
        audit = AuditLog(os.path.join(audit_dir, f"{case['id']}.jsonl"))
        graph = build_graph(provider=MockProvider(), audit_log=audit, registry=registry)
        event = GitHubWebhookEvent(
            delivery_id=f"eval-{case['id']}-{uuid.uuid4().hex[:6]}",
            repo="eval/corpus",
            number=1,
            title=case["title"],
            body=case.get("body", ""),
            sender="eval-harness",
        )
        state = ReviewState(review_id=case["id"], event=event)
        decision = ApprovalDecision(decision="approve", approver="eval-harness")
        final = run_review_auto(graph, state, thread_id=case["id"], decision=decision)
        report.cases.append(_score_case(case, final))
    return report
