from __future__ import annotations
import difflib, hashlib
from dataclasses import dataclass
from pathlib import Path

IGNORED_PARTS = {".git", ".runs", ".agent_home", "__pycache__", ".pytest_cache", ".venv", "node_modules", "dist", "build"}
IGNORED_NAMES = {".env", ".netrc", "id_rsa", "id_ed25519", "credentials.json"}
IGNORED_SUFFIXES = {".pyc", ".pyo", ".coverage"}
MAX_TEXT_FILE = 1_000_000
MAX_SNAPSHOT_TEXT = 50_000_000
MAX_FILES = 20_000

@dataclass(frozen=True)
class FileState:
    digest: str
    content: bytes | None

def ignored(relative: Path) -> bool:
    return (any(part in IGNORED_PARTS for part in relative.parts)
            or relative.name.lower() in IGNORED_NAMES
            or relative.name.endswith(tuple(IGNORED_SUFFIXES)))

def snapshot(root: Path) -> dict[str, FileState]:
    result: dict[str, FileState] = {}
    retained = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if ignored(relative):
            continue
        if len(result) >= MAX_FILES:
            raise ValueError(f"Workspace contains more than {MAX_FILES} files")
        size=path.stat().st_size
        content=None
        digest=hashlib.sha256()
        if size <= MAX_TEXT_FILE:
            raw=path.read_bytes(); digest.update(raw)
            if b"\0" not in raw[:8192] and retained + size <= MAX_SNAPSHOT_TEXT:
                content=raw; retained += size
        else:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024*1024),b""):
                    digest.update(chunk)
        result[relative.as_posix()] = FileState(digest.hexdigest(), content)
    return result
def workspace_digest(root: Path) -> str:
    """Hash the observable workspace state while excluding runtime artifacts."""
    digest = hashlib.sha256()
    for name, state in sorted(snapshot(root).items()):
        digest.update(name.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(state.digest.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()

def snapshot_diff(root: Path, before: dict[str, FileState]) -> str:
    after = snapshot(root)
    pieces: list[str] = []
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old == new or (old and new and old.digest == new.digest):
            continue
        if old is not None and old.content is None or new is not None and new.content is None:
            pieces.append(f"Binary or oversized file changed: {name}\n")
            continue
        old_text = (old.content.decode("utf-8", errors="replace").splitlines(True) if old else [])
        new_text = (new.content.decode("utf-8", errors="replace").splitlines(True) if new else [])
        pieces.extend(difflib.unified_diff(old_text, new_text,
            fromfile=(f"a/{name}" if old else "/dev/null"),
            tofile=(f"b/{name}" if new else "/dev/null")))
    return "".join(pieces)

def diff_whitespace_errors(diff: str) -> list[str]:
    errors=[]
    for number,line in enumerate(diff.splitlines(),1):
        if line.startswith("+") and not line.startswith("+++") and line.rstrip(" \t") != line:
            errors.append(f"added line {number} has trailing whitespace")
    return errors