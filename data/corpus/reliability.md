---
id: REL-STD
title: Reliability and Change Management Standard
---
Any schema or data migration must include a documented rollback plan and be
reversible or forward-fixable. Changes must degrade gracefully and must not
introduce unbounded retries. Rate limits protect shared services from overload.
Deployments must support health checks and safe rollout.
