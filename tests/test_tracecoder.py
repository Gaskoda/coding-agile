from __future__ import annotations
import json,os,shutil,subprocess,tempfile,unittest
from pathlib import Path
from tracecoder.agent import Agent
from tracecoder.context import ContextManager
from tracecoder.task_state import TaskState
from tracecoder.config import ConfigError, load_config
from tracecoder.instructions import AgentInstructions
from tracecoder.model import ScriptedModel
from tracecoder.safety import SafetyError,SafetyPolicy
from tracecoder.tools import ApplyPatch,ReadFile,RunCommand,SearchText
def call(i,name,args): return {"role":"assistant","content":"","tool_calls":[{"id":i,"type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}
class Case(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/mnt/82_store/mj"); self.root=Path(self.temp.name)
        self.git_env=os.environ.copy(); self.git_env.update({"GIT_CONFIG_COUNT":"1","GIT_CONFIG_KEY_0":"safe.directory","GIT_CONFIG_VALUE_0":str(self.root)})
        subprocess.run(["git","init","-q",str(self.root)],check=True,env=self.git_env)
        subprocess.run(["git","-C",str(self.root),"config","user.email","test@local"],check=True,env=self.git_env)
        subprocess.run(["git","-C",str(self.root),"config","user.name","Test"],check=True,env=self.git_env)
        (self.root/"calc.py").write_text("def add(a, b):\n    return a - b\n")
        (self.root/"test_calc.py").write_text("import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n")
        subprocess.run(["git","-C",str(self.root),"add","."],check=True,env=self.git_env); subprocess.run(["git","-C",str(self.root),"commit","-qm","fixture"],check=True,env=self.git_env)
        self.policy=SafetyPolicy(self.root)
    def tearDown(self): self.temp.cleanup()
    def patch(self): return """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
class ConfigTests(Case):
    def test_load_local_json(self):
        path=self.root/"config.json"
        path.write_text(json.dumps({"api_key":"secret","model":"demo","max_turns":7}))
        cfg=load_config(path)
        self.assertEqual(cfg["model"],"demo"); self.assertEqual(cfg["max_turns"],7)
    def test_missing_config_is_empty(self):
        self.assertEqual(load_config(self.root/"missing.json"),{})
    def test_unknown_key_is_rejected(self):
        path=self.root/"config.json"; path.write_text(json.dumps({"unexpected":True}))
        with self.assertRaises(ConfigError): load_config(path)

class SafetyTests(Case):
    def test_escape(self):
        with self.assertRaises(SafetyError): self.policy.path("../../etc/passwd")
    def test_secret(self):
        with self.assertRaises(SafetyError): self.policy.path(".env")
    def test_dependency_install_commands_are_policy_allowed(self):
        policy=SafetyPolicy(self.root,allow_network=True)
        commands=["pip install fastapi","python3 -m pip install httpx","uv pip install flask",
            "npm i react","yarn","go get example.com/x","cargo add serde"]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNone(policy.command(command))
    def test_existing_build_commands_remain_allowed(self):
        policy=SafetyPolicy(self.root,allow_network=True)
        for command in ["python3 -m compileall -q .","echo npm run build"]:
            with self.subTest(command=command):
                self.assertTrue(RunCommand().execute({"command":command},policy).ok)
    def test_danger(self): self.assertFalse(RunCommand().execute({"command":"rm -rf ."},self.policy).ok)
    def test_network(self): self.assertFalse(RunCommand().execute({"command":"curl https://example.com"},self.policy).ok)
    def test_plain_delete(self): self.assertFalse(RunCommand().execute({"command":"rm calc.py"},self.policy).ok)
    def test_secret_via_shell(self): self.assertFalse(RunCommand().execute({"command":"cat .env"},self.policy).ok)
class ToolTests(Case):
    def test_read_search(self):
        self.assertIn("return a - b",ReadFile().execute({"path":"calc.py"},self.policy).output)
        self.assertIn("calc.py:2",SearchText().execute({"query":"return","glob":"*.py"},self.policy).output)
    def test_test_detection_does_not_accept_echo(self):
        result=RunCommand().execute({"command":"echo pytest"},self.policy)
        self.assertTrue(result.ok); self.assertFalse(result.metadata["is_test"])
        self.assertTrue(RunCommand().execute({"command":"python3 -m unittest -q"},self.policy).metadata["is_test"])

    def test_patch_test(self):
        self.assertTrue(ApplyPatch().execute({"patch":self.patch()},self.policy).ok)
        result=RunCommand().execute({"command":"python3 -m unittest -q"},self.policy)
        self.assertTrue(result.ok,result.output); self.assertTrue(result.metadata["is_test"])
    def test_bad_patch_atomic(self):
        before=(self.root/"calc.py").read_text(); result=ApplyPatch().execute({"patch":"bad"},self.policy)
        self.assertFalse(result.ok); self.assertEqual(before,(self.root/"calc.py").read_text())
class AgentTests(Case):
    def test_e2e(self):
        model=ScriptedModel([call("1","read_file",{"path":"calc.py"}),call("2","read_file",{"path":"test_calc.py"}),
            call("3","apply_patch",{"patch":self.patch()}),call("4","run_command",{"command":"python3 -m unittest -q"}),
            call("5","finish",{"summary":"Fixed addition","tests":"unittest passed"})])
        result=Agent(model,self.root,max_turns=8).run("Fix add")
        self.assertTrue(result.success,result.message); self.assertEqual(result.stop_reason,"verified_complete")
        self.assertIn("return a + b",(self.root/"calc.py").read_text()); self.assertTrue((result.run_dir/"final.diff").exists())
    def test_auto_delivers_written_files_at_turn_limit(self):
        model=ScriptedModel([call("1","apply_patch",{"patch":self.patch()})])
        result=Agent(model,self.root,max_turns=1).run("Fix add")
        self.assertTrue(result.success,result.message)
        self.assertEqual(result.stop_reason,"verified_complete_auto")
        self.assertIn("Project files completed",result.message)

    def test_command_is_available_without_becoming_a_test_requirement(self):
        schemas=Agent(ScriptedModel([]),self.root).schemas()
        names=[schema["function"]["name"] for schema in schemas]
        self.assertIn("run_command",names)
        self.assertTrue(Agent._requests_network("请下载数据集并安装依赖"))
        self.assertTrue(Agent._requests_network("git clone the requested repository"))
        self.assertFalse(Agent._requests_network("write a todo application"))
        finish=next(schema for schema in schemas if schema["function"]["name"]=="finish")
        self.assertEqual(finish["function"]["parameters"]["required"],["summary"])
        self.assertNotIn("tests",finish["function"]["parameters"]["properties"])
    def test_run_artifacts_are_not_a_diff(self):
        model=ScriptedModel([call("1","run_command",{"command":"python3 -m unittest -q"}),
            call("2","finish",{"summary":"unchanged","tests":"passed"}),
            call("3","finish",{"summary":"unchanged","tests":"passed"})])
        result=Agent(model,self.root,max_turns=3).run("Do nothing")
        self.assertFalse(result.success)
        self.assertTrue(any("No changes detected" in str(m.get("content")) for m in result.state.messages))
    def test_default_completion_needs_no_test_and_keeps_target_clean(self):
        state_dir=self.root.parent/(self.root.name+"-tracecoder-state")
        try:
            model=ScriptedModel([call("1","apply_patch",{"patch":self.patch()}),
                call("2","finish",{"summary":"done","tests":"not requested"})])
            result=Agent(model,self.root,max_turns=2,state_dir=state_dir).run("Fix add")
            self.assertTrue(result.success,result.message)
            self.assertFalse((self.root/".runs").exists()); self.assertFalse((self.root/".agent_home").exists())
            self.assertTrue(result.run_dir.is_relative_to(state_dir))

        finally: shutil.rmtree(state_dir,ignore_errors=True)
class PlainDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir="/mnt/82_store/mj")
        self.root=Path(self.temp.name)
        (self.root/"calc.py").write_text("def add(a, b):\n    return a - b\n")
        (self.root/"test_calc.py").write_text("import unittest\nfrom calc import add\nclass T(unittest.TestCase):\n    def test_add(self): self.assertEqual(add(2,3),5)\n")
        self.policy=SafetyPolicy(self.root)
    def tearDown(self): self.temp.cleanup()
    def patch(self): return """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
    def test_patch_works_without_git(self):
        self.assertFalse((self.root/".git").exists())
        result=ApplyPatch().execute({"patch":self.patch()},self.policy)
        self.assertTrue(result.ok,result.output); self.assertEqual(result.metadata["mode"],"plain")
        self.assertIn("return a + b",(self.root/"calc.py").read_text())
    def test_plain_patch_can_create_file(self):
        patch="""diff --git a/new_file.py b/new_file.py
new file mode 100644
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1 @@
+value = 42
"""
        result=ApplyPatch().execute({"patch":patch},self.policy)
        self.assertTrue(result.ok,result.output)
        self.assertEqual((self.root/"new_file.py").read_text(),"value = 42\n")
    def test_agent_repairs_plain_directory(self):
        model=ScriptedModel([call("1","read_file",{"path":"calc.py"}),
            call("2","apply_patch",{"patch":self.patch()}),
            call("3","run_command",{"command":"python3 -m unittest -q"}),
            call("4","finish",{"summary":"Fixed addition","tests":"unittest passed"})])
        result=Agent(model,self.root,max_turns=6).run("Fix add")
        self.assertTrue(result.success,result.message)
        self.assertIn("return a + b",(self.root/"calc.py").read_text())
        diff=(result.run_dir/"final.diff").read_text()
        self.assertIn("-    return a - b",diff); self.assertIn("+    return a + b",diff)
        self.assertFalse((self.root/".git").exists())
    def test_run_artifacts_are_ignored_without_git(self):
        model=ScriptedModel([call("1","run_command",{"command":"python3 -m unittest -q"}),
            call("2","finish",{"summary":"unchanged","tests":"passed"}),
            call("3","finish",{"summary":"unchanged","tests":"passed"})])
        result=Agent(model,self.root,max_turns=3).run("Do nothing")
        self.assertFalse(result.success)
        self.assertTrue(any("No changes detected" in str(m.get("content")) for m in result.state.messages))

class InstructionTests(Case):
    def test_hierarchical_agents_md(self):
        (self.root/"AGENTS.md").write_text("Root rule")
        (self.root/"pkg").mkdir()
        (self.root/"pkg"/"AGENTS.md").write_text("Nested rule")
        (self.root/"pkg"/"code.py").write_text("value = 1\n")
        loader=AgentInstructions(self.root)
        rendered=loader.render(loader.applicable("pkg/code.py"))
        self.assertIn("Root rule",rendered); self.assertIn("Nested rule",rendered)
        self.assertLess(rendered.index("Root rule"),rendered.index("Nested rule"))

    def test_nested_rules_block_first_affected_edit(self):
        (self.root/"pkg").mkdir(); (self.root/"pkg"/"AGENTS.md").write_text("Nested rule")
        agent=Agent(ScriptedModel([]),self.root)
        patch="diff --git a/pkg/code.py b/pkg/code.py\n--- a/pkg/code.py\n+++ b/pkg/code.py\n"
        first=agent._instruction_preflight("apply_patch",{"patch":patch})
        self.assertIn("Nested rule",first)
        self.assertIsNone(agent._instruction_preflight("apply_patch",{"patch":patch}))

    def test_symlinked_agents_md_is_rejected(self):
        outside=self.root.parent/(self.root.name+"-outside-agents")
        outside.write_text("outside rule")
        try:
            (self.root/"AGENTS.md").symlink_to(outside)
            with self.assertRaises(ValueError): AgentInstructions(self.root).applicable(self.root)
        finally:
            outside.unlink(missing_ok=True)

class ContextTests(unittest.TestCase):
    def messages(self):
        messages=[{"role":"system","content":"old system"},{"role":"user","content":"old task"}]
        for number in range(8):
            messages.append({"role":"assistant","content":"","tool_calls":[{"id":str(number)}]})
            if number==0:
                payload={"ok":False,"output":"TRACEBACK_SENTINEL","metadata":{}}
            else:
                payload={"ok":True,"output":"x"*1000,"metadata":{}}
            messages.append({"role":"tool","name":"run_command","tool_call_id":str(number),"content":json.dumps(payload)})
        return messages

    def test_compaction_retains_objective_rules_failure_and_test(self):
        state=TaskState("OBJECTIVE_SENTINEL")
        state.latest_test={"command":"pytest","ok":True,"workspace_digest":"DIGEST_SENTINEL"}
        manager=ContextManager(char_limit=1,recent_messages=4)
        compacted,changed=manager.prepare(self.messages(),base_prompt="BASE",task_state=state,
            instructions="RULE_SENTINEL",last_prompt_tokens=0)
        text="\n".join(str(message.get("content","")) for message in compacted)
        self.assertTrue(changed); self.assertIn("OBJECTIVE_SENTINEL",text); self.assertIn("RULE_SENTINEL",text)
        self.assertIn("TRACEBACK_SENTINEL",text); self.assertIn("DIGEST_SENTINEL",text)
        self.assertEqual(state.compactions,1)

    def test_token_usage_can_trigger_compaction(self):
        state=TaskState("token task"); manager=ContextManager(char_limit=10**9,recent_messages=4)
        compacted,changed=manager.prepare(self.messages(),base_prompt="BASE",task_state=state,
            instructions="RULE",last_prompt_tokens=50_001)
        self.assertTrue(changed); self.assertLess(len(compacted),len(self.messages()))

if __name__=="__main__": unittest.main()
