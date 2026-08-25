"""PromptRegistry: prompts are versioned assets, not string literals.

Each prompt lives in app/prompts/<name>.<version>.md with a small frontmatter
header (name/version). The registry loads them, tracks the current version per
name, and can list/diff versions. This is the "prompt as a versioned, auditable
artifact" story: the exact version used is recorded in every TraceRecord.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    body: str


def _parse(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    name = version = None
    body = text
    if text.startswith("---"):
        _, header, body = text.split("---", 2)
        for line in header.strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if key == "name":
                    name = val
                elif key == "version":
                    version = val
    # Fall back to filename (<name>.<version>.md) if header is incomplete.
    stem = path.name[: -len(".md")] if path.name.endswith(".md") else path.stem
    file_name, _, file_version = stem.partition(".")
    return Prompt(
        name=name or file_name,
        version=version or file_version,
        body=body.strip(),
    )


class PromptRegistry:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR) -> None:
        self.dir = prompts_dir
        self._prompts: dict[tuple[str, str], Prompt] = {}
        self._load()

    def _load(self) -> None:
        for path in sorted(self.dir.glob("*.md")):
            p = _parse(path)
            self._prompts[(p.name, p.version)] = p

    def list_versions(self, name: str) -> list[str]:
        return sorted(v for (n, v) in self._prompts if n == name)

    def current_version(self, name: str) -> str:
        versions = self.list_versions(name)
        if not versions:
            raise KeyError(f"no prompt named {name!r}")
        return versions[-1]  # lexical max; v1 < v2 < ... for this scheme

    def get(self, name: str, version: str | None = None) -> Prompt:
        version = version or self.current_version(name)
        try:
            return self._prompts[(name, version)]
        except KeyError as exc:
            raise KeyError(f"no prompt {name!r} version {version!r}") from exc

    def render(self, name: str, version: str | None = None) -> str:
        return self.get(name, version).body

    def diff(self, name: str, v_from: str, v_to: str) -> str:
        a = self.get(name, v_from).body.splitlines()
        b = self.get(name, v_to).body.splitlines()
        return "\n".join(
            difflib.unified_diff(a, b, fromfile=f"{name}.{v_from}", tofile=f"{name}.{v_to}", lineterm="")
        )

    def all_current_versions(self) -> dict[str, str]:
        names = {n for (n, _) in self._prompts}
        return {n: self.current_version(n) for n in sorted(names)}
