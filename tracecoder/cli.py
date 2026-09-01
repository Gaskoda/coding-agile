from __future__ import annotations
import argparse,os,sys
from pathlib import Path
from .agent import Agent
from .config import ConfigError, DEFAULT_CONFIG, load_config
from .model import ModelError,OpenAICompatibleModel

def parser():
    p=argparse.ArgumentParser(description="Small, test-driven and auditable coding agent")
    p.add_argument("task",nargs="?"); p.add_argument("--cwd",type=Path,default=Path.cwd())
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG,
                   help=f"Local JSON config (default: {DEFAULT_CONFIG})")
    p.add_argument("--model",default=None); p.add_argument("--base-url",default=None)
    p.add_argument("--max-turns",type=int,default=None); p.add_argument("--context-chars",type=int,default=None)
    p.add_argument("--no-require-tests",action="store_true"); p.add_argument("--allow-network-commands",action="store_true")
    return p

def main(argv=None):
    args=parser().parse_args(argv); task=args.task or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    try:
        cfg=load_config(args.config)
    except ConfigError as exc:
        print(f"error: {exc}",file=sys.stderr); return 2
    model_name=args.model or cfg.get("model") or os.getenv("TRACECODER_MODEL") or "deepseek-v4-pro"
    base_url=args.base_url or cfg.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
    api_key=cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    max_turns=args.max_turns if args.max_turns is not None else cfg.get("max_turns",30)
    context_chars=args.context_chars if args.context_chars is not None else cfg.get("context_chars",80000)
    if not task or not 1<=max_turns<=200: print("Provide a task; max-turns must be 1..200",file=sys.stderr); return 2
    try:
        result=Agent(OpenAICompatibleModel(model_name,base_url,api_key=api_key),args.cwd,max_turns=max_turns,
            context_chars=context_chars,require_tests=not args.no_require_tests,
            allow_network_commands=args.allow_network_commands).run(task)
    except (ValueError,ModelError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
    print(result.message); print(f"\nStop reason: {result.stop_reason}"); print(f"Run artifacts: {result.run_dir}")
    print(f"Tokens: {result.state.usage['total_tokens']}"); return 0 if result.success else 1
if __name__=="__main__": raise SystemExit(main())