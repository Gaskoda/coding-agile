from __future__ import annotations
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from tracecoder.agent import Agent
from tracecoder.config import ConfigError, load_config
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
    def test_danger(self): self.assertFalse(RunCommand().execute({"command":"rm -rf ."},self.policy).ok)
    def test_network(self): self.assertFalse(RunCommand().execute({"command":"curl https://example.com"},self.policy).ok)
    def test_plain_delete(self): self.assertFalse(RunCommand().execute({"command":"rm calc.py"},self.policy).ok)
    def test_secret_via_shell(self): self.assertFalse(RunCommand().execute({"command":"cat .env"},self.policy).ok)
class ToolTests(Case):
    def test_read_search(self):
        self.assertIn("return a - b",ReadFile().execute({"path":"calc.py"},self.policy).output)
        self.assertIn("calc.py:2",SearchText().execute({"query":"return","glob":"*.py"},self.policy).output)
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
    def test_finish_requires_test(self):
        model=ScriptedModel([call("1","apply_patch",{"patch":self.patch()}),call("2","finish",{"summary":"done","tests":"none"}),
            call("3","run_command",{"command":"python3 -m unittest -q"}),call("4","finish",{"summary":"done","tests":"passed"})])
        result=Agent(model,self.root,max_turns=6).run("Fix add")
        self.assertTrue(result.success); self.assertTrue(any("Completion rejected" in str(m.get("content")) for m in result.state.messages))
    def test_run_artifacts_are_not_a_diff(self):
        model=ScriptedModel([call("1","run_command",{"command":"python3 -m unittest -q"}),
            call("2","finish",{"summary":"unchanged","tests":"passed"}),
            call("3","finish",{"summary":"unchanged","tests":"passed"})])
        result=Agent(model,self.root,max_turns=3).run("Do nothing")
        self.assertFalse(result.success)
        self.assertTrue(any("No changes detected" in str(m.get("content")) for m in result.state.messages))
if __name__=="__main__": unittest.main()