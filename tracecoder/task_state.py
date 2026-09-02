from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskState:
    """Durable facts that must survive conversation compaction."""

    objective: str
    completed: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    files_read: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    latest_test: dict[str, Any] | None = None
    important_evidence: list[str] = field(default_factory=list)
    compactions: int = 0

    @staticmethod
    def _add_unique(items: list[str], value: str, limit: int = 50) -> None:
        if value and value not in items:
            items.append(value)
            del items[:-limit]

    def record_event(self, event: Any) -> None:
        if event.tool == "read_file" and event.ok:
            self._add_unique(self.files_read, str(event.arguments.get("path", "")))
        if event.tool == "apply_patch" and event.ok:
            for path in event.metadata.get("modified_files", []):
                self._add_unique(self.files_modified, str(path))
            self._add_unique(self.completed, "Applied patch: " + ", ".join(event.metadata.get("modified_files", [])), 20)
        if event.tool == "run_command" and event.metadata.get("is_test"):
            self.latest_test = {
                "command": event.arguments.get("command", ""),
                "ok": event.ok,
                "exit_code": event.metadata.get("exit_code"),
                "workspace_digest": event.metadata.get("workspace_digest"),
                "turn": event.turn,
            }
            if event.ok:
                self._add_unique(self.completed, "Passed test: " + str(event.arguments.get("command", "")), 20)
        if not event.ok:
            detail = f"{event.tool} failed: {event.output[-2000:]}"
            self._add_unique(self.unresolved, detail, 10)
            self._add_unique(self.important_evidence, detail, 15)
        else:
            self.unresolved = [item for item in self.unresolved if not item.startswith(event.tool + " failed:")]

    def render(self) -> str:
        test = self.latest_test or {}
        sections = [
            "# Durable task state",
            f"Objective: {self.objective}",
            "Completed:\n" + ("\n".join("- " + item for item in self.completed[-10:]) or "- none recorded"),
            "Unresolved:\n" + ("\n".join("- " + item for item in self.unresolved[-5:]) or "- none recorded"),
            "Files read: " + (", ".join(self.files_read[-30:]) or "none"),
            "Files modified: " + (", ".join(self.files_modified[-30:]) or "none"),
            "Important evidence:\n" + ("\n".join("- " + item for item in self.important_evidence[-5:]) or "- none recorded"),
            "Latest test: " + (f"{test.get('command')} | ok={test.get('ok')} | digest={test.get('workspace_digest')}" if test else "none"),
            f"Context compactions: {self.compactions}",
        ]
        return "\n\n".join(sections)
