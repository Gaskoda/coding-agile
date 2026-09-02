from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
from .agent import Agent
from .config import ConfigError,DEFAULT_CONFIG,load_config
from .model import ModelError,OpenAICompatibleModel

class LiveConsole:
    """Dependency-free renderer for observable agent runs."""
    def __init__(self,stream=None,enabled=True):
        self.stream=stream or sys.stderr; self.enabled=enabled
        self.color=bool(getattr(self.stream,"isatty",lambda:False)()) and not os.getenv("NO_COLOR")
    def _paint(self,text,code): return f"\033[{code}m{text}\033[0m" if self.color else text
    def _line(self,text=""):
        if self.enabled: print(text,file=self.stream,flush=True)
    @staticmethod
    def _args(tool,args):
        if tool=="apply_patch":
            patch=str(args.get("patch","")); files=[x[4:].strip() for x in patch.splitlines() if x.startswith("+++ ")]
            return "files="+(", ".join(files) or "unknown")
        if tool=="run_command": return str(args.get("command",""))
        return json.dumps(args,ensure_ascii=False)[:300]
    def __call__(self,event):
        kind=event["type"]
        if kind=="start":
            self._line(self._paint(f"TraceCoder · {event['workspace_mode']} · {event['root']}","1;36"))
            self._line(f"实时轨迹已开启 · 运行记录: {event['run_dir']}")
        elif kind=="model_wait": self._line(self._paint(f"\n[{event['turn']}] 等待模型…","36"))
        elif kind=="model_response":
            content=event.get("content","").strip()
            if content: self._line("模型: "+content[:1200])
            self._line(f"计划调用 {event['tool_count']} 个工具")
        elif kind=="model_error": self._line(self._paint("模型请求失败: "+event["error"],"31"))
        elif kind=="tool_start": self._line(self._paint(f"→ {event['tool']}","33")+"  "+self._args(event["tool"],event["arguments"]))
        elif kind=="command_output": self._line("  | "+event.get("output",""))
        elif kind=="tool_end":
            mark=self._paint("✓","32") if event["ok"] else self._paint("✗","31"); self._line(f"{mark} {event['tool']} ({event['duration_ms']} ms)")
            output=str(event.get("output","")).strip()
            if output and event["tool"]!="run_command":
                if len(output)>6000: output=output[:2000]+"\n… 输出已截断 …\n"+output[-4000:]
                self._line(output)
        elif kind=="delivery_check":
            mark=self._paint("交付检查通过","1;32") if event["ok"] else self._paint("交付检查未通过","1;31"); self._line(f"{mark}\n{event['output']}")
        elif kind=="context_compacted":
            self._line(self._paint(f"上下文已压缩 · 第 {event['compactions']} 次 · 保留 {event['messages']} 条消息","35"))
        elif kind=="complete":
            detail=f"{event.get('tool_steps',0)} 个工具步骤 · 修改 {event.get('files_modified',0)} 个文件"
            self._line(self._paint(f"\n结束: {event['stop_reason']} · {detail}","1;32" if event["success"] else "1;31"))

def parser():
    p=argparse.ArgumentParser(description="Small, observable and auditable coding agent")
    p.add_argument("task",nargs="?"); p.add_argument("--cwd",type=Path,default=Path.cwd())
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG,help=f"Local JSON config (default: {DEFAULT_CONFIG})")
    p.add_argument("--model",default=None); p.add_argument("--base-url",default=None)
    p.add_argument("--max-turns",type=int,default=None); p.add_argument("--context-chars",type=int,default=None)
    p.add_argument("--state-dir",type=Path,default=None,help="TraceCoder state directory; kept outside the target by default")
    p.add_argument("-i","--interactive",action="store_true",help="Start an interactive task prompt")
    p.add_argument("--allow-network-commands",action="store_true",help="Allow network CLIs even when task wording is ambiguous")
    p.add_argument("--quiet",action="store_true",help="Hide live progress and print only the result")
    return p

def _settings(args):
    try: cfg=load_config(args.config)
    except ConfigError as exc: raise ModelError(str(exc)) from exc
    model_name=args.model or cfg.get("model") or os.getenv("TRACECODER_MODEL") or "deepseek-v4-pro"
    base_url=args.base_url or cfg.get("base_url") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
    api_key=cfg.get("api_key") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    max_turns=args.max_turns if args.max_turns is not None else cfg.get("max_turns",30)
    context_chars=args.context_chars if args.context_chars is not None else cfg.get("context_chars",80000)
    if not 1<=max_turns<=200: raise ModelError("max-turns must be 1..200")
    return model_name,base_url,api_key,max_turns,context_chars

def _run(task,args,settings,console):
    model_name,base_url,api_key,max_turns,context_chars=settings
    model=OpenAICompatibleModel(model_name,base_url,api_key=api_key)
    result=Agent(model,args.cwd,max_turns=max_turns,context_chars=context_chars,allow_network_commands=args.allow_network_commands,
        observer=console,state_dir=args.state_dir).run(task)
    print(result.message); print(f"\nStop reason: {result.stop_reason}"); print(f"Run artifacts: {result.run_dir}"); print(f"Tokens: {result.state.usage['total_tokens']}")
    return 0 if result.success else 1

def _interactive(args,settings,console):
    print(f"TraceCoder interactive · workspace: {args.cwd.resolve()}"); print("输入任务并回车；/help 查看命令，/quit 退出。")
    last=0
    while True:
        try: task=input("\ntracecoder> ").strip()
        except (EOFError,KeyboardInterrupt): print(); return last
        if not task: continue
        if task in {"/quit","/exit","quit","exit"}: return last
        if task=="/help": print("/help  显示帮助\n/quit  退出\n其余输入会作为新的编码任务执行"); continue
        try: last=_run(task,args,settings,console)
        except (ValueError,ModelError) as exc: print(f"error: {exc}",file=sys.stderr); last=2

def main(argv=None):
    args=parser().parse_args(argv); task=args.task or (sys.stdin.read().strip() if not args.interactive and not sys.stdin.isatty() else "")
    try:
        settings=_settings(args); console=LiveConsole(enabled=not args.quiet)
        if args.interactive or (not task and sys.stdin.isatty()): return _interactive(args,settings,console)
        if not task: raise ModelError("Provide a task or use --interactive")
        return _run(task,args,settings,console)
    except (ValueError,ModelError) as exc: print(f"error: {exc}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
