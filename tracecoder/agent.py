from __future__ import annotations
import json,subprocess,time
from dataclasses import asdict,dataclass,field
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
from .context import ContextManager
from .task_state import TaskState
from .instructions import AgentInstructions,InstructionsError
from .model import ChatModel,ModelError
from .safety import SafetyPolicy
from .tools import Tool,default_tools
from .workspace import diff_whitespace_errors,snapshot,snapshot_diff,workspace_digest
PROMPT="""You are TraceCoder, an autonomous coding agent in one local project directory.
Make the smallest correct change that satisfies the task.
Rules: inspect before editing; read focused project files; understand the requested structure and behavior;
modify only with apply_patch; keep producing complete project code as the primary objective; do not create
tests merely to satisfy the harness, and do not install packages, create environments, execute the project,
probe the runtime or run tests by default; when the user's task explicitly requests installation, environment
setup, execution, testing, validation, downloads or other command work, use run_command to perform the
requested work and report its actual output without turning it into a universal completion requirement;
declare Python dependencies in requirements.txt or the project's existing dependency manifest, and put
environment creation, dependency installation and run instructions in README; never access secrets, escape
the workspace, push, publish or deploy. Call finish as soon as the requested project files are complete."""
FINISH={"type":"function","function":{"name":"finish","description":"Complete delivery of the requested project files.","parameters":{"type":"object","properties":{"summary":{"type":"string"},"risks":{"type":"string"}},"required":["summary"]}}}
DEFAULT_STATE_DIR=Path(__file__).resolve().parent.parent/".tracecoder"
@dataclass
class Event:
    turn:int; tool:str; arguments:dict[str,Any]; ok:bool; output:str; duration_ms:int; metadata:dict[str,Any]=field(default_factory=dict)
@dataclass
class State:
    task:str; root:Path; max_turns:int; turn:int=0; messages:list=field(default_factory=list); events:list=field(default_factory=list)
    files_read:set=field(default_factory=set); files_modified:set=field(default_factory=set); tests:list=field(default_factory=list)
    usage:dict=field(default_factory=lambda:{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0})
    task_state:TaskState|None=None; last_prompt_tokens:int=0
    stop_reason:str=""; final_message:str=""
    def add_usage(self,raw):
        for key in self.usage: self.usage[key]+=int(raw.get(key,0) or 0)
        self.last_prompt_tokens=int(raw.get("prompt_tokens",0) or 0)
    def add_event(self,event):
        self.events.append(event)
        if event.tool=="read_file" and event.ok: self.files_read.add(str(event.arguments.get("path","")))
        if event.tool=="apply_patch" and event.ok: self.files_modified.update(event.metadata.get("modified_files",[]))
        if event.tool=="run_command" and event.metadata.get("is_test"):
            self.tests.append({"command":event.arguments.get("command",""),"ok":event.ok,"exit_code":event.metadata.get("exit_code"),
                               "workspace_digest":event.metadata.get("workspace_digest"),"turn":event.turn})
        if self.task_state is not None: self.task_state.record_event(event)
@dataclass
class AgentResult:
    success:bool; message:str; stop_reason:str; run_dir:Path; state:State
class RunLogger:
    def __init__(self,state_dir):
        stamp=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"); self.run_dir=state_dir/"runs"/stamp
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
                 require_tests=False,allow_network_commands=False,context_chars=80000,observer=None,state_dir:Path|None=None):
        self.model=model; self.root=root.resolve(); self.max_turns=max_turns; self.max_errors=max_errors
        self.require_tests=require_tests; self.context_chars=context_chars; self.observer=observer
        self.state_dir=(state_dir or DEFAULT_STATE_DIR).resolve()
        self.tools={t.name:t for t in (tools or default_tools())}
        self.policy=SafetyPolicy(self.root,allow_network_commands,self.state_dir/"command-home")
        self.git_env={"GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"safe.directory","GIT_CONFIG_VALUE_0":str(self.root)}
        self.instructions=AgentInstructions(self.root); self.exposed_instructions:set[str]=set()
        self.context=ContextManager(context_chars)
        self.git_mode=(self.root/".git").exists(); self.initial_snapshot=None
    def _emit(self,kind,**data):
        if self.observer is None: return
        try: self.observer({"type":kind,**data})
        except Exception: pass
    def schemas(self): return [t.schema() for t in self.tools.values()]+[FINISH]
    def run(self,task):
        if not task.strip() or not self.root.is_dir(): raise ValueError("Need a task and an existing project directory")
        if self._requests_network(task):
            self.policy.allow_network=True
        self.git_mode=(self.root/".git").exists()
        self.initial_snapshot=None if self.git_mode else snapshot(self.root)
        state=State(task.strip(),self.root,self.max_turns,task_state=TaskState(task.strip()))
        self.instructions.invalidate()
        root_documents=self.instructions.applicable(self.root)
        self.exposed_instructions={str(doc.path) for doc in root_documents}
        instruction_text=self.instructions.render(root_documents)
        state.messages=[{"role":"system","content":PROMPT+"\n\n"+instruction_text},{"role":"user","content":f"Workspace: {self.root}\nTask: {task.strip()}\nInspect first."}]
        logger=RunLogger(self.state_dir); logger.write({"type":"start","task":state.task,"workspace_mode":"git" if self.git_mode else "plain"}); errors=warnings=0; success=False
        self._emit("start",task=state.task,root=str(self.root),workspace_mode="git" if self.git_mode else "plain",run_dir=str(logger.run_dir))
        try:
            for turn in range(1,self.max_turns+1):
                state.turn=turn; self._compact(state)
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
                        self._emit("delivery_check",turn=turn,ok=accepted,output=text)
                        if accepted: success=True; state.stop_reason,state.final_message="verified_complete",text; break
                    else:
                        self._emit("tool_start",turn=turn,tool=name,arguments=args)
                        event=self._execute(turn,name,args); state.add_event(event); logger.write({"type":"tool",**asdict(event)})
                        self._emit("tool_end",turn=turn,tool=name,ok=event.ok,output=event.output,duration_ms=event.duration_ms,metadata=event.metadata)
                        state.messages.append(self._tool_message(call_id,name,event.ok,event.output,event.metadata))
                if success: break
                if state.events and state.events[-1].tool=="apply_patch" and state.events[-1].ok:
                    state.messages.append({"role":"user","content":"The project files were updated. Keep code delivery primary. If the user explicitly requested runtime, installation or test work, complete only that requested command work; otherwise call finish now."})
                if self._stagnating(state):
                    warnings+=1
                    if warnings>=2: state.stop_reason,state.final_message="stagnation","Repeated actions produced no evidence"; break
                    state.messages.append({"role":"user","content":"Three identical actions repeated. Choose a materially different action."})
                else: warnings=0
            else:
                accepted,text=self._verify(state,{"summary":"Project files completed at the turn limit","risks":"The model did not explicitly call finish"})
                self._emit("delivery_check",turn=state.turn,ok=accepted,output=text,automatic=True)
                if accepted: success=True; state.stop_reason,state.final_message="verified_complete_auto",text
                else: state.stop_reason,state.final_message="max_turns",f"Reached {self.max_turns} turns\n{text}"
        except KeyboardInterrupt: state.stop_reason,state.final_message="interrupted","Interrupted"
        except Exception as exc: state.stop_reason,state.final_message="internal_error",f"{type(exc).__name__}: {exc}"
        diff=self._diff(); logger.finish(state,diff)
        self._emit("complete",success=success,stop_reason=state.stop_reason,message=state.final_message,run_dir=str(logger.run_dir),usage=state.usage,
            tool_steps=len(state.events),files_modified=len(state.files_modified))
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
            instruction_result=self._instruction_preflight(name,args)
            if instruction_result is not None:
                ok,output,meta=False,instruction_result,{"instructions_required":True}
                elapsed=int((time.monotonic()-started)*1000); meta["duration_ms"]=elapsed
                return Event(turn,name,args,ok,output,elapsed,meta)
            result=(self.tools[name].execute(args,self.policy,on_output=lambda line: self._emit("command_output",turn=turn,output=line))
                    if name=="run_command" else self.tools[name].execute(args,self.policy)); ok,output,meta=result.ok,result.output,result.metadata
            if name=="run_command" and meta.get("is_test"):
                meta["workspace_digest"]=workspace_digest(self.root)
            if name=="apply_patch" and ok:
                changed=[Path(value) for value in meta.get("modified_files",[]) if Path(value).name=="AGENTS.md"]
                if changed:
                    self.instructions.invalidate()
                    self.exposed_instructions.difference_update(str((self.root/value).resolve()) for value in changed)
        except Exception as exc: ok,output,meta=False,f"{type(exc).__name__}: {exc}",{}
        elapsed=int((time.monotonic()-started)*1000); meta.setdefault("duration_ms",elapsed)
        return Event(turn,name,args,ok,output,elapsed,meta)
    def _instruction_preflight(self,name,args):
        targets=[]
        if name=="apply_patch":
            targets=[value.strip() for value in self.tools[name].PATH.findall(str(args.get("patch",""))) if value.strip()!="/dev/null"]
        elif name=="read_file": targets=[str(args.get("path","."))]
        if not targets: return None
        documents=[]
        try:
            for target in targets:
                for document in self.instructions.applicable(target):
                    if str(document.path) not in self.exposed_instructions and document not in documents:
                        documents.append(document)
        except InstructionsError as exc:
            return f"Cannot load project instructions: {exc}"
        if not documents: return None
        self.exposed_instructions.update(str(document.path) for document in documents)
        return self.instructions.render(documents)+"\n\nReview these newly discovered rules, then retry the tool call."
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
        if feedback: return False,"Completion rejected:\n"+"\n".join("- "+x for x in feedback)
        return True,str(args.get("summary","Completed"))+"\n\nNotes: "+str(args.get("risks","None stated"))
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
        documents=[]
        try:
            targets=[self.root]+[Path(value) for value in sorted(self.exposed_instructions)]
            for target in targets:
                for document in self.instructions.applicable(target):
                    if document not in documents: documents.append(document)
        except InstructionsError:
            documents=[]
        instruction_text=self.instructions.render(documents)
        state.messages,compacted=self.context.prepare(state.messages,base_prompt=PROMPT,
            task_state=state.task_state,instructions=instruction_text,last_prompt_tokens=state.last_prompt_tokens)
        if compacted:
            self._emit("context_compacted",turn=state.turn,messages=len(state.messages),
                compactions=state.task_state.compactions,last_prompt_tokens=state.last_prompt_tokens)
    @staticmethod
    def _requests_network(task):
        lowered=task.lower()
        return any(word in lowered for word in ("download","install","network","internet","git clone","curl","wget","下载","安装","联网","仓库克隆"))
    @staticmethod
    def _stagnating(state):
        if len(state.events)<3: return False
        keys={(e.tool,repr(sorted(e.arguments.items())),e.output[-1000:],e.ok) for e in state.events[-3:]}
        return len(keys)==1