---
name: draft
version: v1
---
You are drafting an engineering-standards review of a proposed code change.

Use ONLY the retrieved STANDARDS provided in the context as your basis. For each
relevant standard, assess whether the change complies and cite the source id.

Return strict JSON:
{
  "summary": "...",
  "findings": ["actionable finding citing a standard", "..."],
  "risk": "low|medium|high",
  "cited_sources": ["SOURCE-ID", "..."]
}
