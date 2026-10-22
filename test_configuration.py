"""Regression tests for configurable reproduction and terminal integration."""
import asyncio
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent import Settings
from configuration import load_configuration
import cli
from test_model_limits import project_temporary

SAMPLE=Path(__file__).with_name("agent.example.json")


class ConfigurationTests(unittest.TestCase):
    def write_config(self,directory,value):
        target=Path(directory)/"agent.json"
        target.write_text(json.dumps(value),encoding="utf-8")
        return target

    def test_profiles_paths_and_workflow_override(self):
        value=json.loads(SAMPLE.read_text(encoding="utf-8"))
        value["paths"]["output_dir"]="parts"
        value["agent"]["instructions_file"]="rules.md"
        value["workflow"]={"export_formats":["step"],"measure":False,"preview":False,"stage_save":False}
        with project_temporary() as directory:
            (Path(directory)/"rules.md").write_text("安装孔按参数命名",encoding="utf-8")
            selected=load_configuration(self.write_config(directory,value),"deepseek-pro")
            with patch("credentials.load",return_value={"api_key":"offline-saved-key"}):
                settings=Settings(selected)
            self.assertEqual(settings.model,"deepseek-v4-pro")
            self.assertEqual(settings.output_dir,Path(directory)/"parts")
            self.assertFalse(settings.visible)
            self.assertFalse(settings.preview_enabled)
            self.assertFalse(settings.measure_enabled)
            self.assertFalse(settings.stage_save)
            self.assertEqual(settings.export_formats,["step"])
            self.assertIn("安装孔",settings.extra_instructions)

    def test_foreign_profile_never_reuses_saved_deepseek_key(self):
        selected=load_configuration(SAMPLE,"custom-compatible")
        with patch("credentials.load",return_value={"api_key":"offline-saved-key"}), patch.dict(os.environ,{"CUSTOM_MODEL_API_KEY":"offline-custom-key"}):
            settings=Settings(selected)
            self.assertEqual(settings.key(),"offline-custom-key")
            self.assertEqual(settings.api_key,"")
            self.assertFalse(settings.use_registry_key)

    def test_foreign_profile_cannot_request_saved_key(self):
        value=json.loads(SAMPLE.read_text(encoding="utf-8"))
        value["models"]["custom-compatible"]["use_saved_key"]=True
        with project_temporary() as directory:
            with self.assertRaisesRegex(ValueError,"官方地址"):
                load_configuration(self.write_config(directory,value))

    def test_cli_profiles_does_not_start_cad(self):
        stream=io.StringIO()
        with patch("cli.CadAgent") as factory, redirect_stdout(stream):
            self.assertEqual(cli.main(["profiles","--config",str(SAMPLE)]),0)
        factory.assert_not_called()
        self.assertEqual(len(json.loads(stream.getvalue())["models"]),3)

    def test_cli_prompt_file_and_overrides_reach_embedded_runner(self):
        captured={}
        async def fake_runner(settings,prompt,**kwargs):
            captured.update(settings=settings,prompt=prompt,options=kwargs)
            return {"status":"completed","answer":"offline"}
        with project_temporary() as directory:
            source=Path(directory)/"input.md"
            source.write_text("80×60×30实心长方体",encoding="utf-8")
            with patch("cli.run_task",fake_runner),patch("credentials.load",return_value={}),redirect_stdout(io.StringIO()):
                status=cli.main(["run","--config",str(SAMPLE),"--local","--prompt-file",str(source),"--output-dir",directory,"--exports","step","--no-preview","--no-measure","--quiet"])
            self.assertEqual(status,0)
            self.assertEqual(captured["prompt"],"80×60×30实心长方体")
            self.assertEqual(captured["settings"].export_formats,["step"])
            self.assertFalse(captured["settings"].preview_enabled)
            self.assertEqual(captured["options"]["kind"],"local")

    def test_embedded_runner_closes_session_on_error(self):
        closed=[]
        class FakeAgent:
            def __init__(self,*args):
                self.error="offline startup failure"
            async def start(self):
                pass
            async def close(self):
                closed.append(True)
        with patch("cli.CadAgent",FakeAgent):
            with self.assertRaisesRegex(RuntimeError,"startup"):
                asyncio.run(cli.run_task(None,"sample"))
        self.assertEqual(closed,[True])


if __name__=="__main__":
    unittest.main(verbosity=2)
