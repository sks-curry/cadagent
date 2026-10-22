"""Image/clarification regressions. Mock model and CAD; no paid API requests."""
import base64
import copy
import importlib
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
from PIL import Image

from agent import CadAgent, REVIEW_TOOL
from design_brief import BRIEF_TOOL, save_brief
from drawings import write_drawing
from recipes import CUSTOM_TOOLS
from test_model_limits import completion, project_temporary
from vision import MAX_IMAGE_BYTES, image_blocks, load_references, normalize_image, prepare_images, save_references


def png():
    stream = io.BytesIO()
    Image.new("RGB", (100, 80), "white").save(stream, format="PNG")
    return stream.getvalue()


def brief(ready=False):
    return {"part_name": "实心长方体", "structure": "长方体，无孔，无空腔。", "parameters": [
        {"name": "length", "value": "80", "unit": "mm", "source": "image_annotation", "required": True},
        {"name": "width", "value": "60", "unit": "mm", "source": "image_annotation", "required": True},
        {"name": "height", "value": "30" if ready else None, "unit": "mm", "source": "user_text" if ready else "unknown", "required": True},
    ], "missing_parameters": [] if ready else ["高度是多少毫米？"], "assumptions": [], "ready_to_model": ready}


def tool_call(name, arguments, identity):
    return {"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class ImageTests(unittest.TestCase):
    def test_normalizes_actual_format_and_flattens_transparency(self):
        image = normalize_image(png(), "../../图.png")
        self.assertEqual(image.name, "图.png")
        self.assertEqual(image.mime, "image/png")
        self.assertEqual((image.width, image.height), (100, 80))
        with Image.open(io.BytesIO(image.data)) as pixels:
            self.assertEqual(pixels.mode, "RGB")

    def test_rejects_invalid_data_urls_and_corrupt_images(self):
        for value in ("https://example.com/image.png", "data:image/png;base64,not-an-image", "data:image/png;base64," + base64.b64encode(b"not png").decode()):
            with self.subTest(value=value[:40]), self.assertRaises(ValueError):
                prepare_images([{"name": "test.png", "data_url": value}])
        with self.assertRaises(ValueError):
            normalize_image(b"x" * (MAX_IMAGE_BYTES + 1), "large.png")

    def test_references_persist_and_cannot_read_external_paths(self):
        with project_temporary() as temporary:
            directory = Path(temporary)
            refs = save_references(directory, [normalize_image(png(), "草图.png")])
            self.assertEqual(load_references(directory), refs)
            self.assertEqual(image_blocks(directory, refs)[1]["type"], "image_url")
            with self.assertRaises(ValueError):
                image_blocks(directory, [{"filename": "../credentials.py", "name": "bad"}])
            (directory / "references.json").write_text(json.dumps([{"filename": "../secret"}]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_references(directory)

    def test_inferred_critical_dimensions_cannot_open_modeling_gate(self):
        with project_temporary() as temporary:
            value = brief(True)
            value["parameters"][0]["source"] = "inferred"
            saved = save_brief(Path(temporary), value)
            self.assertFalse(saved["ready_to_model"])
            self.assertTrue(saved["missing_parameters"])

    def test_standard_and_derived_parameters_with_basis_can_proceed(self):
        with project_temporary() as temporary:
            value = brief(True)
            value["parameters"][0].update(source="standard", basis="测试明确指定的规格长80mm")
            value["parameters"][1].update(source="derived", basis="已知长度80mm × 0.75 = 60mm")
            saved = save_brief(Path(temporary), value)
            self.assertTrue(saved["ready_to_model"])
            self.assertEqual(saved["missing_parameters"], [])

    def test_standard_source_requires_explicit_basis(self):
        with project_temporary() as temporary:
            value = brief(True)
            value["parameters"][0]["source"] = "standard"
            with self.assertRaisesRegex(ValueError, "basis"):
                save_brief(Path(temporary), value)

    def test_printable_drawing_embeds_views_and_escapes_content(self):
        with project_temporary() as temporary:
            directory = Path(temporary)
            path = directory / "preview.png"
            path.write_bytes(png())
            value = brief(True)
            value["part_name"] = "<script>bad</script>"
            result = write_drawing(directory, value, {"bodies": [{"bbox": {"min": [0, 0, 0], "max": [80, 60, 30]}, "volume_mm3": 144000}]}, {"iso": path})
            html = result.read_text(encoding="utf-8")
            self.assertIn("80 × 60 × 30", html)
            self.assertIn("data:image/png;base64,", html)
            self.assertNotIn("<script>bad", html)
            self.assertIn("此视图未生成", html)


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = project_temporary()
        self.directory = Path(self.temporary.__enter__())
        settings = SimpleNamespace(api_key="offline-test", key=lambda: "offline-test", cli=Path("unused.exe"), visible=False, connect="", model="deepseek-flash", thinking=True, reasoning_effort="high", base_url="https://offline.invalid", max_tokens=4096, max_rounds=0)
        self.agent = CadAgent(settings, self.directory)
        for tool in [*CUSTOM_TOOLS, BRIEF_TOOL, REVIEW_TOOL]:
            self.agent.schemas[tool["function"]["name"]] = tool["function"]["parameters"]
            self.agent.tools.append(tool)
        self.agent.schemas.update({name: {"type": "object"} for name in ("batch", "save", "measure", "screenshot", "inspect_design")})
        self.calls = []
        self.requests = []
        async def raw(name, args):
            self.calls.append((name, args))
            if name == "inspect_design":
                return {"bodies": []}
            if name in {"save", "screenshot"}:
                Path(args["path"]).write_bytes(png() if name == "screenshot" else b"native")
            return {"ok": True}
        self.agent.raw = raw

    def tearDown(self):
        self.temporary.__exit__(None, None, None)

    def responses(self, *responses):
        replies = iter(responses)
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200, json=next(replies))
        self.agent.client_factory = lambda **kwargs: AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_image_question_followup_builds_only_after_dimensions_complete(self):
        refs = save_references(self.agent.directory, [normalize_image(png(), "草图.png")])
        self.responses(completion(calls=[tool_call("record_design_brief", brief(), "brief-1"), tool_call("build_solid_box", {"length": 80, "width": 60, "height": 30}, "premature")], finish="tool_calls"))
        job = {"id": "first", "events": [], "attachments": refs}
        answer = await self.agent.chat(job, "根据草图创建实心长方体")
        self.assertTrue(job["needs_parameters"])
        self.assertIn("高度", answer)
        self.assertFalse(any(name == "batch" for name, _ in self.calls))
        content = self.requests[0]["messages"][-1]["content"]
        self.assertTrue(any(block["type"] == "image_url" for block in content))
        for filename in ("requests.jsonl", "task-checkpoint.json", "design-brief.json"):
            self.assertNotIn("base64,", (self.agent.directory / filename).read_text(encoding="utf-8"))
        self.responses(completion(calls=[tool_call("record_design_brief", brief(True), "brief-2")], finish="tool_calls"), completion(calls=[tool_call("build_solid_box", {"length": 80, "width": 60, "height": 30}, "build")], finish="tool_calls"), completion("已完成模型。"))
        await self.agent.chat({"id": "second", "events": []}, "高度30mm")
        self.assertTrue(any(name == "batch" for name, _ in self.calls))
        self.assertIn("根据草图创建实心长方体", self.agent.pending_prompt)
        self.assertIn("高度30mm", self.agent.pending_prompt)
        self.assertFalse(self.agent.awaiting_parameters)
        self.assertTrue((self.agent.directory / "design.solidcraft").is_file())

    async def test_mutation_requires_current_turn_brief_even_if_previous_is_complete(self):
        self.agent.brief_required = True
        self.agent.design_brief = brief(True)
        with self.assertRaisesRegex(ValueError, "record_design_brief"):
            await self.agent.tool("build_solid_box", {"length": 80, "width": 60, "height": 30}, {"id": "blocked", "events": []})
        self.assertEqual(self.calls, [])

    async def test_text_completion_without_actual_geometry_is_paused(self):
        self.responses(completion(calls=[tool_call("record_design_brief", brief(True), "brief")], finish="tool_calls"), completion("模型已完成"))
        job = {"id": "empty", "events": []}
        answer = await self.agent.chat(job, "创建长方体")
        self.assertTrue(job["needs_continue"])
        self.assertIn("尚未生成实体", answer)

    async def test_restart_recovers_pending_requirements_and_reference_image(self):
        save_references(self.agent.directory, [normalize_image(png(), "草图.png")])
        save_brief(self.agent.directory, brief())
        self.agent.pending_prompt = "原始目标"
        self.agent.checkpoint(False)
        self.agent.history.clear()
        self.agent.pending_prompt = ""
        self.agent.restore_checkpoint()
        self.assertTrue(self.agent.awaiting_parameters)
        self.responses(completion(calls=[tool_call("record_design_brief", brief(), "again")], finish="tool_calls"))
        await self.agent.chat({"id": "restart", "events": []}, "补充要求")
        content = self.requests[0]["messages"][-1]["content"]
        self.assertIn("原始目标", content[0]["text"])
        self.assertTrue(any(block["type"] == "image_url" for block in content))

    async def test_review_image_is_sent_as_user_after_all_tool_results(self):
        self.responses(completion(calls=[tool_call("record_design_brief", brief(True), "brief")], finish="tool_calls"), completion(calls=[tool_call("review_model_view", {"view": "iso"}, "review")], finish="tool_calls"), completion("已检查可见外形。"))
        await self.agent.chat({"id": "review", "events": []}, "查看当前模型")
        messages = self.requests[-1]["messages"]
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-2]["role"], "tool")
        self.assertTrue(any(block["type"] == "image_url" for block in messages[-1]["content"]))


class UploadTests(unittest.TestCase):
    def test_static_interface_and_readable_project_metadata(self):
        from fastapi.testclient import TestClient
        from agent import Settings
        with project_temporary() as temporary:
            with patch("credentials.load", return_value={}), patch.dict("os.environ", {"SOLIDCRAFT_PARTS": temporary, "SOLIDCRAFT_VISIBLE": "0"}):
                settings = Settings()
                settings.api_key = "offline-test"
                agent = CadAgent(settings, Path(temporary))
                module = importlib.import_module("app")
            save_brief(agent.directory, brief(True))
            with patch.object(module, "runtime", agent):
                client = TestClient(module.app)
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn('name="agent-token"', page.text)
                self.assertIn('/static/app.js', page.text)
                self.assertNotIn("offline-test", page.text)
                for name in ("styles.css", "app.js", "markdown.js"):
                    response = client.get("/static/" + name)
                    self.assertEqual(response.status_code, 200, name)
                    self.assertGreater(len(response.content), 100)
                state = client.get("/api/status").json()
                self.assertEqual(state["project_items"][0]["title"], "实心长方体")
                self.assertEqual(state["project_items"][0]["id"], agent.directory.name)

    def test_image_only_request_and_local_rejection(self):
        from fastapi.testclient import TestClient
        with project_temporary() as temporary:
            settings = SimpleNamespace(api_key="offline-test", key=lambda: "offline-test", cli=Path("unused.exe"), visible=False, connect="")
            agent = CadAgent(settings, Path(temporary))
            with patch("credentials.load", return_value={}), patch.dict("os.environ", {"SOLIDCRAFT_PARTS": temporary, "SOLIDCRAFT_VISIBLE": "0"}):
                module = importlib.import_module("app")
            with patch.object(module, "runtime", agent):
                client = TestClient(module.app)
                body = {"images": [{"name": "草图.png", "data_url": "data:image/png;base64," + base64.b64encode(png()).decode()}]}
                headers = {"X-Agent-Token": module.TOKEN}
                self.assertEqual(client.post("/api/chat", json=body).status_code, 403)
                result = client.post("/api/chat", json=body, headers=headers)
                self.assertEqual(result.status_code, 200, result.text)
                job, prompt = agent.queue.get_nowait()
                agent.queue.task_done()
                self.assertEqual(len(job["attachments"]), 1)
                self.assertIn("参考图", prompt)
                self.assertEqual(client.post("/api/local", json=body, headers=headers).status_code, 422)
                body["images"][0]["data_url"] = "data:image/png;base64,bm90LXBpY3R1cmU="
                self.assertEqual(client.post("/api/chat", json=body, headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
