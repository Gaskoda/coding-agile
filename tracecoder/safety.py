from __future__ import annotations
import re, shlex
from pathlib import Path
class SafetyError(PermissionError): pass
class SafetyPolicy:
    BLOCKED_PARTS={".git",".ssh",".venv","node_modules"}
    SENSITIVE={".env",".netrc","id_rsa","id_ed25519","credentials.json"}
    BLOCKED=(r"(^|\s)rm\s+.*-[a-z]*r[a-z]*f",r"(^|\s)rm\s+.*-[a-z]*f[a-z]*r",
      r"(^|\s)(sudo|su|mkfs|fdisk|parted|shutdown|reboot)(\s|$)",
      r"(^|\s)git\s+(push|reset\s+--hard|clean\s+-[a-z]*f)",r"(^|\s)(env|printenv)(\s|$)",
      r"(^|\s)(chown|chmod\s+.*-[a-z]*r)(\s|$)",r"(^|[;&|]\s*|\s)rm(\s|$)",r"(^|\s)(curl|wget).*(\||&&|;)\s*(sh|bash|python)")
    def __init__(self,root:Path,allow_network=False,runtime_root:Path|None=None):
        self.root=root.resolve(); self.allow_network=allow_network
        self.runtime_root=(runtime_root or self.root/".agent_home").resolve()
    def path(self,value:str,write=False):
        if not value or "\0" in value: raise SafetyError("Invalid empty path")
        raw=Path(value); candidate=raw.resolve() if raw.is_absolute() else (self.root/raw).resolve()
        try: rel=candidate.relative_to(self.root)
        except ValueError as exc: raise SafetyError(f"Path escapes workspace: {value}") from exc
        if any(p in self.BLOCKED_PARTS for p in rel.parts): raise SafetyError(f"Protected path: {value}")
        if candidate.name.lower() in self.SENSITIVE: raise SafetyError(f"Sensitive file blocked: {value}")
        if write and candidate==self.root: raise SafetyError("Cannot overwrite workspace root")
        return candidate
    def command(self,value:str):
        if not value.strip(): raise SafetyError("Empty command")
        lower=value.lower()
        for pattern in self.BLOCKED:
            if re.search(pattern,lower): raise SafetyError(f"Blocked command pattern: {pattern}")
        if not self.allow_network and re.search(r"(^|\s)(curl|wget|ssh|scp|rsync|nc)(\s|$)",lower):
            raise SafetyError("Network commands disabled")
        try: tokens=shlex.split(value)
        except ValueError as exc: raise SafetyError(f"Invalid quoting: {exc}") from exc
        for token in tokens:
            if Path(token.strip(";|&")).name.lower() in self.SENSITIVE: raise SafetyError("Sensitive file in command")
            if token.startswith("/"):
                try: Path(token.split(":",1)[0]).resolve().relative_to(self.root)
                except ValueError as exc: raise SafetyError(f"Path outside workspace: {token}") from exc