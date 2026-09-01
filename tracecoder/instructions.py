from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class InstructionsError(ValueError):
    pass


@dataclass(frozen=True)
class InstructionDocument:
    path: Path
    content: str


class AgentInstructions:
    """Load hierarchical AGENTS.md files without leaving the workspace."""

    filename = "AGENTS.md"

    def __init__(self, root: Path, *, max_file_chars: int = 32_000, max_total_chars: int = 64_000):
        self.root = root.resolve()
        self.max_file_chars = max_file_chars
        self.max_total_chars = max_total_chars
        self._cache: dict[Path, InstructionDocument | None] = {}

    def _load(self, directory: Path) -> InstructionDocument | None:
        directory = directory.resolve()
        if directory in self._cache:
            return self._cache[directory]
        path = directory / self.filename
        if path.is_symlink():
            raise InstructionsError(f"Symbolic-link instructions are not allowed: {path.relative_to(self.root)}")
        try: path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise InstructionsError("Instruction file escapes workspace") from exc
        if not path.is_file():
            self._cache[directory] = None
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > self.max_file_chars:
            raise InstructionsError(f"{path.relative_to(self.root)} exceeds {self.max_file_chars} characters")
        document = InstructionDocument(path, content)
        self._cache[directory] = document
        return document

    def applicable(self, target: str | Path = ".") -> list[InstructionDocument]:
        path = (self.root / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise InstructionsError("Instruction target escapes workspace") from exc
        directories = [self.root]
        current = self.root
        parts = relative.parts if path.is_dir() else relative.parts[:-1]
        for part in parts:
            current = current / part
            directories.append(current)
        documents = [doc for directory in directories if (doc := self._load(directory)) is not None]
        total = sum(len(doc.content) for doc in documents)
        if total > self.max_total_chars:
            raise InstructionsError(f"Applicable AGENTS.md files exceed {self.max_total_chars} characters")
        return documents

    def render(self, documents: list[InstructionDocument]) -> str:
        if not documents:
            return "No AGENTS.md instructions apply."
        parts = ["Project instructions (deeper files override conflicting parent rules):"]
        for document in documents:
            name = document.path.relative_to(self.root).as_posix()
            parts.append(f"\n## {name}\n{document.content.strip()}")
        return "\n".join(parts)

    def invalidate(self) -> None:
        self._cache.clear()
