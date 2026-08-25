"""agentic-review-gate: a webhook-triggered, LangGraph-based AI review agent.

A GitHub webhook triggers a multi-step agentic workflow that plans, retrieves
relevant engineering standards from a small local corpus, drafts an analysis,
self-critiques, PAUSES for human-in-the-loop approval, then posts a result and
writes a tamper-evident, fully traceable audit record.

Runs fully offline with a deterministic MockProvider. The real Anthropic path is
only exercised when ANTHROPIC_API_KEY is set.
"""

__version__ = "0.1.0"
