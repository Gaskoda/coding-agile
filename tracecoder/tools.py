from __future__ import annotations
import os,re,selectors,signal,subprocess,time
from dataclasses import dataclass,field
from pathlib import Path
from typing import Any
from .safety import SafetyPolicy
IGNORED={".git",".venv","node_modules","__pycache__","dist","build",".runs"}
@dataclass
class ToolResult:
    ok:bool; output:str; metadata:dict[str,Any]=field(default_factory=dict)
class Tool:
    name=""; description=""; parameters={"type":"object","properties":{}}
    def schema(self): return {"type":"function","function":{"name":self.name,"description":self.description,"parameters":self.parameters}}
    def execute(self,args,policy): raise NotImplementedError
    @staticmethod
    def text(args,key,default=None):
        value=args.get(key,default)
        if not isinstance(value,str) or not value: raise ValueError(f"{key} must be a non-empty string")
        return value
    @staticmethod
    def integer(args,key,default,low,high):
        value=args.get(key,default)
        if not isinstance(value,int) or isinstance(value,bool) or not low<=value<=high: raise ValueError(f"{key} must be {low}..{high}")
        return value
class ListFiles(Tool):
    name="list_files"; description="List workspace files with bounded depth and output."
    parameters={"type":"object","properties":{"path":{"type":"string"},"max_depth":{"type":"integer"},"max_entries":{"type":"integer"}}}
    def execute(self,args,policy):
        root=policy.path(str(args.get("path","."))); depth=self.integer(args,"max_depth",3,1,5); limit=self.integer(args,"max_entries",200,1,500)
        if not root.is_dir(): return ToolResult(False,"Not a directory")
        out=[]
        for current,dirs,files in os.walk(root):
            here=Path(current); dirs[:]=sorted(d for d in dirs if d not in IGNORED)
            if len(here.relative_to(root).parts)>=depth: dirs[:]=[]
            for name in sorted(files):
                out.append((here/name).relative_to(policy.root).as_posix())
                if len(out)==limit: return ToolResult(True,"\n".join(out)+"\n... truncated")
        return ToolResult(True,"\n".join(out) or "(empty)")
class ReadFile(Tool):
    name="read_file"; description="Read at most 400 numbered lines from a UTF-8 file."
    parameters={"type":"object","properties":{"path":{"type":"string"},"start_line":{"type":"integer"},"end_line":{"type":"integer"}},"required":["path"]}
    def execute(self,args,policy):
        value=self.text(args,"path"); start=self.integer(args,"start_line",1,1,1000000); end=self.integer(args,"end_line",start+199,start,start+399)
        path=policy.path(value)
        if not path.is_file(): return ToolResult(False,f"File not found: {value}")
        if path.stat().st_size>2000000: return ToolResult(False,"File exceeds 2 MB")
        raw=path.read_bytes()
        if b"\0" in raw[:8192]: return ToolResult(False,"Binary file")
        lines=raw.decode(errors="replace").splitlines(); body="\n".join(f"{n:>6} | {line}" for n,line in enumerate(lines[start-1:end],start))
        return ToolResult(True,body or "(empty range)",{"total_lines":len(lines)})
class SearchText(Tool):
    name="search_text"; description="Recursively search text with a regular expression."
    parameters={"type":"object","properties":{"query":{"type":"string"},"path":{"type":"string"},"glob":{"type":"string"},"max_results":{"type":"integer"}},"required":["query"]}
    def execute(self,args,policy):
        try: regex=re.compile(self.text(args,"query"))
        except re.error as exc: return ToolResult(False,f"Invalid regex: {exc}")
        root=policy.path(str(args.get("path","."))); glob=str(args.get("glob","*")); limit=self.integer(args,"max_results",50,1,200); out=[]
        paths=[root] if root.is_file() else root.rglob(glob)
        for path in paths:
            if not path.is_file() or any(p in IGNORED for p in path.parts): continue
            try:
                if path.stat().st_size>1000000: continue
                lines=path.read_text().splitlines()
            except (OSError,UnicodeError): continue
            for number,line in enumerate(lines,1):
                if regex.search(line):
                    out.append(f"{path.relative_to(policy.root).as_posix()}:{number}:{line[:300]}")
                    if len(out)==limit: return ToolResult(True,"\n".join(out)+"\n... truncated")
        return ToolResult(True,"\n".join(out) or "No matches")
class ApplyPatch(Tool):
    name="apply_patch"; description="Apply a unified diff after validating target paths."
    parameters={"type":"object","properties":{"patch":{"type":"string"}},"required":["patch"]}
    PATH=re.compile(r"^(?:---|\+\+\+)\s+(?:[ab]/)?([^\t\n]+)",re.MULTILINE)
    def execute(self,args,policy):
        patch=self.text(args,"patch")
        if len(patch.encode())>500000: return ToolResult(False,"Patch exceeds 500 KB")
        paths=[]
        for value in self.PATH.findall(patch):
            value=value.strip()
            if value!="/dev/null" and value not in paths: paths.append(value)
        if not paths or len(paths)>10: return ToolResult(False,"Patch must modify 1..10 files")
        try:
            for value in paths: policy.path(value,write=True)
        except Exception as exc: return ToolResult(False,str(exc))
        if (policy.root/".git").exists():
            git_env=os.environ.copy(); git_env.update({"GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"safe.directory","GIT_CONFIG_VALUE_0":str(policy.root)})
            check_cmd=["git","apply","--check","--whitespace=nowarn","-"]
            apply_cmd=["git","apply","--whitespace=nowarn","-"]
            env=git_env
        else:
            check_cmd=["patch","--dry-run","--batch","--forward","--reject-file=-","-p1"]
            apply_cmd=["patch","--batch","--forward","--reject-file=-","-p1"]
            env=None
        check=subprocess.run(check_cmd,input=patch,text=True,cwd=policy.root,capture_output=True,timeout=30,env=env)
        if check.returncode: return ToolResult(False,"Patch check failed:\n"+(check.stdout+check.stderr)[-4000:])
        result=subprocess.run(apply_cmd,input=patch,text=True,cwd=policy.root,capture_output=True,timeout=30,env=env)
        if result.returncode: return ToolResult(False,"Patch failed:\n"+(result.stdout+result.stderr)[-4000:])
        mode="git" if (policy.root/".git").exists() else "plain"
        return ToolResult(True,"Patch applied: "+", ".join(paths),{"modified_files":paths,"mode":mode})
class RunCommand(Tool):
    name="run_command"; description="Run one non-interactive workspace command with timeout."
    parameters={"type":"object","properties":{"command":{"type":"string"},"timeout":{"type":"integer"}},"required":["command"]}
    TESTS=("pytest","unittest","npm test","pnpm test","cargo test","go test")
    def execute(self,args,policy,on_output=None):
        command=self.text(args,"command"); timeout=self.integer(args,"timeout",60,1,300)
        try: policy.command(command)
        except Exception as exc: return ToolResult(False,str(exc),{"blocked":True})
        started=time.monotonic(); chunks=[]; timed_out=False
        env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":str(policy.root/".agent_home"),"PYTHONPATH":str(policy.root),"LANG":"C.UTF-8","PYTHONUNBUFFERED":"1"}
        process=subprocess.Popen(command,cwd=policy.root,shell=True,executable="/bin/bash",stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,bufsize=1,env=env,start_new_session=True)
        selector=selectors.DefaultSelector(); selector.register(process.stdout,selectors.EVENT_READ)
        try:
            while True:
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0:
                    timed_out=True; os.killpg(process.pid,signal.SIGKILL); break
                ready=selector.select(min(.2,remaining))
                for key,_ in ready:
                    line=key.fileobj.readline()
                    if line:
                        chunks.append(line)
                        if on_output: on_output(line.rstrip("\n"))
                if process.poll() is not None:
                    tail=process.stdout.read()
                    if tail:
                        chunks.append(tail)
                        if on_output:
                            for line in tail.splitlines(): on_output(line)
                    break
            process.wait()
        finally:
            selector.close(); process.stdout.close()
        output="".join(chunks)
        if len(output)>12000: output=output[:4000]+"\n... truncated ...\n"+output[-8000:]
        meta={"exit_code":process.returncode,"duration_ms":int((time.monotonic()-started)*1000),
              "is_test":any(x in command.lower() for x in self.TESTS)}
        if timed_out:
            meta["timeout"]=True; return ToolResult(False,f"Timed out after {timeout}s\n"+output[-8000:],meta)
        return ToolResult(process.returncode==0,output or "(no output)",meta)
def default_tools(): return [ListFiles(),SearchText(),ReadFile(),ApplyPatch(),RunCommand()]