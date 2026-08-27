from __future__ import annotations
import argparse,os,sys
from pathlib import Path
from .agent import Agent
from .model import ModelError,OpenAICompatibleModel
def parser():
    p=argparse.ArgumentParser(description="Small, test-driven and auditable coding agent")
    p.add_argument("task",nargs="?"); p.add_argument("--cwd",type=Path,default=Path.cwd())
    p.add_argument("--model",default=os.getenv("TRACECODER_MODEL","deepseek-v4-pro"))
    p.add_argument("--base-url",default=os.getenv("OPENAI_BASE_URL","https://api.deepseek.com/v1"))
    p.add_argument("--max-turns",type=int,default=30); p.add_argument("--context-chars",type=int,default=80000)
    p.add_argument("--no-require-tests",action="store_true"); p.add_argument("--allow-network-commands",action="store_true")
    return p
def main(argv=None):
    args=parser().parse_args(argv); task=args.task or (sys.stdin.read().strip() if not sys.stdin.isatty() else "")
    if not task or not 1<=args.max_turns<=200: print("Provide a task; max-turns must be 1..200",file=sys.stderr); return 2
    try:
        result=Agent(OpenAICompatibleModel(args.model,args.base_url),args.cwd,max_turns=args.max_turns,
            context_chars=args.context_chars,require_tests=not args.no_require_tests,
            allow_network_commands=args.allow_network_commands).run(task)
    except (ValueError,ModelError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
    print(result.message); print(f"\nStop reason: {result.stop_reason}"); print(f"Run artifacts: {result.run_dir}")
    print(f"Tokens: {result.state.usage['total_tokens']}"); return 0 if result.success else 1
if __name__=="__main__": raise SystemExit(main())