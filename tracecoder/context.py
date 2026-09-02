from __future__ import annotations

import json
from typing import Any

from .task_state import TaskState


class ContextManager:
    """Deterministically compact model context while retaining durable facts."""

    def __init__(self, char_limit: int = 80_000, token_limit: int = 50_000, recent_messages: int = 12):
        self.char_limit = char_limit
        self.token_limit = token_limit
        self.recent_messages = recent_messages

    def needs_compaction(self, messages: list[dict[str, Any]], last_prompt_tokens: int = 0) -> bool:
        return (sum(len(str(message)) for message in messages) > self.char_limit
                or last_prompt_tokens > self.token_limit) and len(messages) > self.recent_messages + 2

    def prepare(self, messages: list[dict[str, Any]], *, base_prompt: str, task_state: TaskState,
                instructions: str, last_prompt_tokens: int = 0) -> tuple[list[dict[str, Any]], bool]:
        if not self.needs_compaction(messages, last_prompt_tokens):
            return messages, False
        cutoff = max(2, len(messages) - self.recent_messages)
        while cutoff > 2 and messages[cutoff].get("role") == "tool":
            cutoff -= 1
        evidence = self._evidence(messages[2:cutoff])
        task_state.compactions += 1
        system = base_prompt + "\n\n" + instructions + "\n\n" + task_state.render()
        compacted = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Workspace task: {task_state.objective}"},
        ]
        if evidence:
            compacted.append({"role": "system", "content": "# Earlier evidence\n" + "\n".join(evidence[-20:])})
        compacted.extend(messages[cutoff:])
        return compacted, True

    def _evidence(self, messages: list[dict[str, Any]]) -> list[str]:
        evidence: list[str] = []
        for message in messages:
            if message.get("role") != "tool":
                continue
            name = str(message.get("name", "tool"))
            raw = str(message.get("content", ""))
            try:
                value = json.loads(raw); ok = bool(value.get("ok")); output = str(value.get("output", "")); metadata = value.get("metadata") or {}
            except (json.JSONDecodeError, AttributeError):
                ok = False; output = raw; metadata = {}
            if not ok:
                evidence.append(f"- {name} failed: {self._bounded_failure(output)}")
            elif name == "run_command":
                evidence.append(f"- command succeeded: exit={metadata.get('exit_code')} test={metadata.get('is_test')} digest={metadata.get('workspace_digest')}")
            elif name == "apply_patch":
                evidence.append("- patch applied: " + ", ".join(metadata.get("modified_files", [])))
            else:
                evidence.append(f"- {name} succeeded: {output[:600]}")
        return evidence

    @staticmethod
    def _bounded_failure(output: str) -> str:
        if len(output) <= 4_000:
            return output
        return output[:1_000] + "\n... omitted ...\n" + output[-3_000:]
