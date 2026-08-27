from __future__ import annotations
import json, os, time, urllib.error, urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

class ModelError(RuntimeError): pass
@dataclass
class ModelResponse:
    message: dict[str, Any]
    usage: dict[str, Any]
class ChatModel(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelResponse: ...

class OpenAICompatibleModel:
    """Dependency-free OpenAI-compatible chat-completions client."""
    def __init__(self, model: str, base_url: str | None = None, api_key: str | None = None,
                 timeout: int = 120, retries: int = 2) -> None:
        self.model=model
        self.base_url=(base_url or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
        self.api_key=api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.timeout, self.retries=timeout, retries
        if not self.api_key: raise ModelError("Set DEEPSEEK_API_KEY or OPENAI_API_KEY")
    def complete(self, messages, tools):
        payload=json.dumps({"model":self.model,"messages":messages,"tools":tools,
                            "tool_choice":"auto","temperature":0}).encode()
        req=urllib.request.Request(self.base_url+"/chat/completions",data=payload,method="POST",
            headers={"Authorization":"Bearer "+self.api_key,"Content-Type":"application/json"})
        last=None
        for attempt in range(self.retries+1):
            try:
                with urllib.request.urlopen(req,timeout=self.timeout) as response:
                    data=json.loads(response.read().decode())
                if not data.get("choices"): raise ModelError("API response contains no choices")
                return ModelResponse(data["choices"][0]["message"],data.get("usage",{}))
            except urllib.error.HTTPError as exc:
                last=ModelError(f"HTTP {exc.code}: "+exc.read().decode(errors="replace")[:1500])
                if exc.code<500 and exc.code!=429: break
            except (urllib.error.URLError,TimeoutError,json.JSONDecodeError) as exc: last=exc
            if attempt<self.retries: time.sleep(2**attempt)
        raise ModelError(f"Model request failed: {last}")

class ScriptedModel:
    def __init__(self,responses): self.responses=iter(responses)
    def complete(self,messages,tools):
        del messages,tools
        try: return ModelResponse(next(self.responses),{"total_tokens":1})
        except StopIteration as exc: raise ModelError("Scripted model exhausted") from exc