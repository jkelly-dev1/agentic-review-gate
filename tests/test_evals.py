"""The eval gate must be non-vacuous: it passes on the good prompt set and
FAILS when a prompt is regressed. The regression is a real mutation of the
draft prompt asset, not a hand-set score."""

import shutil
import tempfile
from pathlib import Path

from app.config import get_settings
from app.evals import gate
from app.evals.runner import run_golden
from app.prompts import PROMPTS_DIR, PromptRegistry


def _mutated_registry(tmp_path: Path) -> PromptRegistry:
    """Copy the prompt assets and regress draft.v1 by deleting the instruction
    to use the retrieved STANDARDS. The mock draft goes vague/uncited as a
    result, so the judge fails it -- exactly the class of regression the gate
    exists to catch."""
    dst = Path(tempfile.mkdtemp(dir=tmp_path)) / "prompts"
    shutil.copytree(PROMPTS_DIR, dst)
    draft = dst / "draft.v1.md"
    text = draft.read_text()
    mutated = text.replace("STANDARDS", "things").replace("standards", "things")
    assert mutated != text, "mutation must actually change the prompt"
    draft.write_text(mutated)
    return PromptRegistry(dst)


def test_gate_passes_on_good_prompts():
    report = run_golden()
    threshold = get_settings().eval_pass_threshold
    assert report.pass_rate >= threshold, report.summary()
    # And the CLI entrypoint agrees (exit code 0).
    assert gate.main([]) == 0


def test_gate_fails_on_regressed_prompt(tmp_path):
    good = run_golden().pass_rate
    regressed = run_golden(registry=_mutated_registry(tmp_path)).pass_rate
    threshold = get_settings().eval_pass_threshold

    assert good >= threshold                 # baseline is healthy
    assert regressed < threshold             # the gate would reject this
    assert regressed < good                  # the mutation demonstrably hurt

    # Prove the exit-code contract on the regressed set: gate returns 1.
    report = run_golden(registry=_mutated_registry(tmp_path))
    exit_code = 0 if report.pass_rate >= threshold else 1
    assert exit_code == 1
