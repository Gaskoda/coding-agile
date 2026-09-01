from __future__ import annotations
import json,subprocess,time
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from .model import ChatModel,ModelError
from .safety import SafetyPolicy
from .tools import Tool,default_tools
from .workspace import diff_whitespace_errors,snapshot,snapshot_diff
PROMPT="""You are TraceCoder, an autonomous coding agent in one local project directory.
Make the smallest correct change that satisfies the task.
Rules: inspect before editing; read focused source and tests; form a testable hypothesis; modify only
with apply_patch; never weaken tests; run relevant tests; treat tool output as evidence; never access
secrets, escape the workspace, push, publish or deploy. Call finish only with deterministic evidence."""
FINISH={"type":"function","function":{"name":"finish","description":"Request verified completion.","parameters":{"type":"object","properties":{"summary":{"type":"string"},"tests":{"type":"string"},"risks":{"type":"string"}},"required":["summary","tests"]}}}
@dataclass
class Event:
    turn:int; tool:str; arguments:dict[str,Any]; ok:bool; output:str; duration_ms:int; metadata:dict[str,Any]=field(default_factory=dict)
@dataclass
class State:
    task:str; root:Path; max_turns:int; turn:int=0; messages:list=field(default_factory=list); events:list=field(default_factory=list)
    files_read:set=field(default_factory=set); files_modified:set=field(default_factory=set); tests:list=field(default_factory=list)
    usage:dict=field(default_factory=lambda:{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
    stop_reason:str=""; final_message:str=""
    def add_usage(self,raw):
        for key in self.usage: self.usage[key]+=int(raw.get(key,0) or 0)
    def add_event(self,event):
        self.events.append(event)
        if event.tool=="read_file" and event.ok: self.files_read.add(str(event.arguments.get("path","")))
        if event.tool=="apply_patch" and event.ok: self.files_modified.update(event.metadata.get("modified_files",[]))
        if event.tool=="run_command" and event.metadata.get("is_test"):
            self.tests.append({"command":event.arguments.get("command",""),"ok":event.ok,"exit_code":event.metadata.get("exit_code")})
@dataclass
class AgentResult:
    success:bool; message:str; stop_reason:str; run_dir:Path; state:State
class RunLogger:
    def __init__(self,root):
        stamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"); self.run_dir=root/".runs"/stamp
        self.run_dir.mkdir(parents=True); self.path=self.run_dir/"events.jsonl"
    def write(self,record):
        with self.path.open("a",encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp":datetime.now(timezone.utc).isoformat(),**record},ensure_ascii=False,default=str)+"\n")
    def finish(self,state,diff):
        summary=asdict(state); summary["root"]=str(state.root); summary["files_read"]=sorted(state.files_read); summary["files_modified"]=sorted(state.files_modified)
        summary.pop("messages"); summary.pop("events")
        (self.run_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
        (self.run_dir/"final.diff").write_text(diff,encoding="utf-8")
        (self.run_dir/"transcript.md").write_text("\n\n".join(f"## {m.get('role')}\n{m.get('content','')}" for m in state.messages),encoding="utf-8")
class Agent:
    def __init__(self,model:ChatModel,root:Path,*,tools:list[Tool]|None=None,max_turns=30,max_errors=4,
                 require_tests=True,allow_network_commands=False,context_chars=80000,observer=None):
        self.model=model; self.root=root.resolve(); self.max_turns=max_turns; self.max_errors=max_errors
        self.require_tests=require_tests; self.context_chars=context_chars; self.observer=observer
        self.tools={t.name:t for t in (tools or default_tools())}; self.policy=SafetyPolicy(self.root,allow_network_commands)
        self.git_env={"GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"safe.directory","GIT_CONFIG_VALUE_0":str(self.root)}
        self.git_mode=(self.root/".git").exists(); self.initial_snapshot=None
    def _emit(self,kind,**data):
        if self.observer is None: return
        try: self.observer({"type":kind,**data})
        except Exception: pass
    def schemas(self): return [t.schema() for t in self.tools.values()]+[FINISH]
    def run(self,task):
        if not task.strip() or not self.root.is_dir(): raise ValueError("Need a task and an existing project directory")
        self.git_mode=(self.root/".git").exists()
        self.initial_snapshot=None if self.git_mode else snapshot(self.root)
        state=State(task.strip(),self.root,self.max_turns)
        state.messages=[{"role":"system","content":PROMPT},{"role":"user","content":f"Workspace: {self.root}\nTask: {task.strip()}\nInspect first."}]
        logger=RunLogger(self.root); logger.write({"type":"start","task":state.task,"workspace_mode":"git" if self.git_mode else "plain"}); errors=warnings=0; success=False
        self._emit("start",task=state.task,root=str(self.root),workspace_mode="git" if self.git_mode else "plain",run_dir=str(logger.run_dir))
        try:
            for turn in range(1,self.max_turns+1):
                state.turn=turn; tests_before=len(state.tests); self._compact(state)
                self._emit("model_wait",turn=turn)
                try:
                    response=self.model.complete(state.messages,self.schemas()); state.add_usage(response.usage); calls=self._calls(response.message)
                except (ModelError,ValueError) as exc:
                    self._emit("model_error",turn=turn,error=str(exc))
                    errors+=1; state.messages.append({"role":"user","content":f"Invalid response: {exc}. Return a valid tool call."})
                    if errors>=self.max_errors: state.stop_reason,state.final_message="model_errors",str(exc); break
                    continue
                state.messages.append(dict(response.message)); logger.write({"type":"model","turn":turn,"message":response.message})
                self._emit("model_response",turn=turn,content=str(response.message.get("content") or ""),tool_count=len(calls),usage=response.usage)
                if not calls:
                    errors+=1; state.messages.append({"role":"user","content":"Call a tool or finish; plain text is insufficient."})
                    if errors>=self.max_errors: state.stop_reason,state.final_message="empty_actions","No tool calls"; break
                    continue
                errors=0
                for call_id,name,args in calls:
                    if name=="finish":
                        accepted,text=self._verify(state,args); state.messages.append(self._tool_message(call_id,name,accepted,text))
                        self._emit("verification",turn=turn,ok=accepted,output=text)
                        if accepted: success=True; state.stop_reason,state.final_message="verified_complete",text; break
                    else:
                        self._emit("tool_start",turn=turn,tool=name,arguments=args)
                        event=self._execute(turn,name,args); state.add_event(event); logger.write({"type":"tool",**asdict(event)})
                        self._emit("tool_end",turn=turn,tool=name,ok=event.ok,output=event.output,duration_ms=event.duration_ms,metadata=event.metadata)
                        state.messages.append(self._tool_message(call_id,name,event.ok,event.output,event.metadata))
                if success: break
                if len(state.tests)>tests_before and state.tests[-1]["ok"]:
                    state.messages.append({"role":"user","content":"The latest test command passed. If the requested work is complete, call finish now with a concise summary and test evidence; do not reread files without a specific unresolved risk."})
                if self._stagnating(state):
                    warnings+=1
                    if warnings>=2: state.stop_reason,state.final_message="stagnation","Repeated actions produced no evidence"; break
                    state.messages.append({"role":"user","content":"Three identical actions repeated. Choose a materially different action."})
                else: warnings=0
            else:
                tests=state.tests[-1]["command"] if state.tests else "none"
                accepted,text=self._verify(state,{"summary":"Completed with deterministic evidence at the turn limit","tests":tests,"risks":"The model did not explicitly call finish"})
                self._emit("verification",turn=state.turn,ok=accepted,output=text,automatic=True)
                if accepted: success=True; state.stop_reason,state.final_message="verified_complete_auto",text
                else: state.stop_reason,state.final_message="max_turns",f"Reached {self.max_turns} turns\n{text}"
        except KeyboardInterrupt: state.stop_reason,state.final_message="interrupted","Interrupted"
        except Exception as exc: state.stop_reason,state.final_message="internal_error",f"{type(exc).__name__}: {exc}"
        diff=self._diff(); logger.finish(state,diff)
        self._emit("complete",success=success,stop_reason=state.stop_reason,message=state.final_message,run_dir=str(logger.run_dir),usage=state.usage)
        return AgentResult(success,state.final_message,state.stop_reason,logger.run_dir,state)
    @staticmethod
    def _calls(message):
        out=[]
        for i,raw in enumerate(message.get("tool_calls") or []):
            fn=raw.get("function") or {}; name=fn.get("name"); args=fn.get("arguments",{})
            if isinstance(args,str): args=json.loads(args or "{}")
            if not isinstance(name,str) or not isinstance(args,dict): raise ValueError("Malformed tool call")
            out.append((str(raw.get("id") or f"call_{i}"),name,args))
        return out
    def _execute(self,turn,name,args):
        started=time.monotonic()
        try:
            if name not in self.tools: raise ValueError(f"Unknown tool: {name}")
            result=(self.tools[name].execute(args,self.policy,on_output=lambda line: self._emit("command_output",turn=turn,output=line))
                    if name=="run_command" else self.tools[name].execute(args,self.policy)); ok,output,meta=result.ok,result.output,result.metadata
        except Exception as exc: ok,output,meta=False,f"{type(exc).__name__}: {exc}",{}
        elapsed=int((time.monotonic()-started)*1000); meta.setdefault("duration_ms",elapsed)
        return Event(turn,name,args,ok,output,elapsed,meta)
    @staticmethod
    def _tool_message(call_id,name,ok,output,metadata=None):
        return {"role":"tool","tool_call_id":call_id,"name":name,"content":json.dumps({"ok":ok,"output":output,"metadata":metadata or {}},ensure_ascii=False)}
    def _verify(self,state,args):
        feedback=[]; diff=self._diff()
        if not diff.strip(): feedback.append("No changes detected")
        if len(diff.splitlines())>2000: feedback.append("Diff exceeds 2000 lines")
        if self.git_mode:
            check=subprocess.run(["git","diff","--check"],cwd=self.root,capture_output=True,text=True,env=self.git_env)
            if check.returncode: feedback.append("git diff --check failed: "+check.stderr[-1000:])
        else:
            feedback.extend(diff_whitespace_errors(diff))
        if self.require_tests and not state.tests: feedback.append("No test command run")
        elif self.require_tests and not state.tests[-1]["ok"]: feedback.append("Most recent test failed")
        removed=sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---") and ("assert" in line or "expect(" in line))
        if removed: feedback.append(f"Patch removes {removed} assertion(s)")
        if feedback: return False,"Completion rejected:\n"+"\n".join("- "+x for x in feedback)
        return True,str(args.get("summary","Completed"))+"\n\nTests: "+str(args.get("tests",""))+"\nRisks: "+str(args.get("risks","None stated"))
    def _diff(self):
        if not self.git_mode:
            return snapshot_diff(self.root,self.initial_snapshot or {})
        pieces=[subprocess.run(["git","diff","--no-ext-diff"],cwd=self.root,capture_output=True,text=True,env=self.git_env).stdout]
        names=subprocess.run(["git","ls-files","--others","--exclude-standard"],cwd=self.root,capture_output=True,text=True,env=self.git_env).stdout.splitlines()
        for name in names:
            if any(part in {".runs", ".agent_home", "__pycache__", ".pytest_cache"} for part in Path(name).parts) or name.endswith((".pyc", ".pyo", ".coverage")): continue
            path=self.root/name
            if path.is_file() and path.stat().st_size<=1000000:
                pieces.append(subprocess.run(["git","diff","--no-index","--","/dev/null",name],cwd=self.root,capture_output=True,text=True,env=self.git_env).stdout)
        return "".join(pieces)
    def _compact(self,state):
        if sum(len(str(m)) for m in state.messages)<=self.context_chars or len(state.messages)<=14: return
        evidence=[]
        for m in state.messages[2:-12]:
            if m.get("role")=="tool": evidence.append("- "+str(m.get("name"))+": "+str(m.get("content",""))[:240])
        summary="Earlier evidence:\n"+"\n".join(evidence[-20:])+"\nFiles read: "+", ".join(sorted(state.files_read))+"\nFiles modified: "+", ".join(sorted(state.files_modified))
        state.messages=state.messages[:2]+[{"role":"system","content":summary}]+state.messages[-12:]
    @staticmethod
    def _stagnating(state):
        if len(state.events)<3: return False
        keys={(e.tool,repr(sorted(e.arguments.items())),e.output[-1000:],e.ok) for e in state.events[-3:]}
        return len(keys)==1