---
id: OBS-STD
title: Observability Standard
---
Every request and background job must emit structured logs with a correlation
id so a single unit of work can be traced end to end. Long-running or multi-step
workflows must be observable via spans/traces. Metrics must cover request
counts, error rates, and latency. Audit-relevant actions must be recorded.
