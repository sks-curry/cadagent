"""Offline regressions. No real model request or SolidCraft mutation is made."""
import importlib
import json
import os
import shutil
import uuid
from contextlib import contextmanager
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from openai import AsyncOpenAI
from agent import CadAgent
from model_limits import PAGE_TOOL, encoded_size

TEST_WORK=Path(__file__).resolve().parent/".private"/"test-work"
TEST_WORK.mkdir(parents=True,exist_ok=True)

@contextmanager
def project_temporary():
    # Inherit application-folder ACLs; avoid platform-specific tempfile permissions.
    target=(TEST_WORK/uuid.uuid4().hex).resolve()
    assert target.is_relative_to(TEST_WORK.resolve())
    target.mkdir()
    try:
        yield str(target)
    finally:
        assert target.is_relative_to(TEST_WORK.resolve())
        shutil.rmtree(target)


def completion(content=None, calls=None, finish="stop", reasoning="test reasoning"):
    return {"id":"mock", "object":"chat.completion", "created":0, "model":"deepseek-flash",
            "choices":[{"index":0, "finish_reason":finish,
                        "message":{"role":"assistant", "content":content,
                                   "reasoning_content":reasoning, "tool_calls":calls}}]}


def call(identity="one", arguments='{"amount":1}'):
    return {"id":identity,"type":"function","function":{"name":"mutate","arguments":arguments}}


class LoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=project_temporary()
        temporary=self.temporary.__enter__()
        settings=SimpleNamespace(api_key="offline-test",key=lambda:"offline-test", cli=Path("unused.exe"),
                                 visible=False,connect="",model="deepseek-flash",thinking=True,
                                 reasoning_effort="high",base_url="https://offline.invalid",max_tokens=393216,max_rounds=0)
        self.agent=CadAgent(settings,Path(temporary))
        self.executed=[]
        self.requests=[]
        self.agent.schemas["read_tool_result_page"]=PAGE_TOOL["function"]["parameters"]
        async def raw(name,args):
            return {"bodies":[],"timeline":[],"path":None}
        async def tool(name,args,job):
            self.executed.append(name)
            if name=="mutate":
                self.agent.geometry_generation+=1
            return {"ok":True}
        self.agent.raw=raw
        self.fake_tool=tool

    def tearDown(self):
        self.temporary.__exit__(None,None,None)

    def responses(self,*responses):
        replies=iter(responses)
        def handler(request):
            self.requests.append(json.loads(request.content))
            return httpx.Response(200,json=next(replies))
        self.agent.client_factory=lambda **kwargs:AsyncOpenAI(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    def job(self):
        return {"id":"offline","events":[]}

    async def test_truncated_call_set_never_executes_valid_prefix(self):
        self.agent.tool=self.fake_tool
        self.responses(completion(calls=[call("discard-good"),call("discard-bad",'{"amount":')],finish="length"),
                       completion(calls=[call("accepted")],finish="tool_calls"), completion("完成"))
        answer=await self.agent.chat(self.job(),"create")
        self.assertEqual(answer,"完成")
        self.assertEqual(self.executed,["mutate","save"])
        history=json.dumps(self.agent.history)
        self.assertNotIn("discard-good",history)
        self.assertNotIn("discard-bad",history)
        accepted=[m for m in self.agent.history if m.get("tool_calls")][0]
        self.assertEqual(accepted["reasoning_content"],"test reasoning")
        self.assertEqual(self.requests[0]["max_tokens"],393216)

    async def test_malformed_arguments_with_stop_also_not_executed(self):
        self.agent.tool=self.fake_tool
        self.responses(completion(calls=[call(arguments="[]")],finish="tool_calls"),completion("已恢复"))
        self.assertEqual(await self.agent.chat(self.job(),"create"),"已恢复")
        self.assertEqual(self.executed,[])

    async def test_unlimited_loop_passes_old_24_round_limit(self):
        self.agent.tool=self.fake_tool
        replies=[completion(calls=[call(str(i))],finish="tool_calls") for i in range(26)]
        self.responses(*replies,completion("全部完成"))
        self.assertEqual(await self.agent.chat(self.job(),"long task"),"全部完成")
        self.assertEqual(self.executed.count("mutate"),26)
        self.assertEqual(self.executed.count("save"),26)
        self.assertEqual(len(self.requests),27)
        self.assertFalse(self.agent.can_continue)

    async def test_text_is_preserved_and_continued(self):
        self.responses(completion("第一部分",finish="length"),completion("第二部分"))
        self.assertEqual(await self.agent.chat(self.job(),"explain"),"第一部分\n第二部分")
        self.assertIn("第一部分",json.dumps(self.agent.history,ensure_ascii=False))
        self.assertEqual(self.executed,[])

    async def test_compatible_api_omits_deepseek_fields_and_optional_budget(self):
        self.agent.settings.api_style="openai-compatible"
        self.agent.settings.max_tokens=None
        self.responses(completion("兼容接口完成"))
        await self.agent.chat(self.job(),"create")
        self.assertNotIn("thinking",self.requests[0])
        self.assertNotIn("reasoning_effort",self.requests[0])
        self.assertNotIn("max_tokens",self.requests[0])

    async def test_workflow_can_disable_exports_measure_and_preview(self):
        self.agent.settings.export_formats=[]
        self.agent.settings.measure_enabled=False
        self.agent.settings.preview_enabled=False
        async def raw(name,args):
            return {"bodies":[{"id":"one"}]}
        self.agent.raw=raw
        self.agent.tool=self.fake_tool
        await self.agent.refresh(self.job())
        self.assertEqual(self.executed,["save"])

    async def test_repeated_truncation_checkpoint_restores_after_restart(self):
        self.responses(*[completion(calls=[call(arguments="{")],finish="length") for _ in range(3)])
        job=self.job()
        answer=await self.agent.chat(job,"目标文本")
        self.assertTrue(job["needs_continue"])
        self.assertTrue(self.agent.can_continue)
        self.assertIn("继续任务",answer)
        checkpoint=json.loads((self.agent.directory/"task-checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["goal"],"目标文本")
        self.assertNotIn("offline-test",json.dumps(checkpoint))
        self.agent.pending_prompt=""
        self.agent.restore_checkpoint()
        self.assertEqual(self.agent.pending_prompt,"目标文本")
        self.responses(completion("继续后完成"))
        self.assertEqual(await self.agent.chat(self.job(),"",resume=True),"继续后完成")
        self.assertFalse(self.agent.can_continue)

    async def test_pagination_and_stale_reference(self):
        value={"curves":[{"id":i,"data":"coordinate"*200} for i in range(200)]}
        view=self.agent.model_view(value)
        self.assertLess(encoded_size(view),24000)
        page=await self.agent.tool("read_tool_result_page",{"result_ref":view["result_ref"],"path":["curves"],"offset":81,"limit":2},self.job())
        self.assertEqual([p["id"] for p in page["items"]],[81,82])
        self.agent.geometry_generation+=1
        with self.assertRaisesRegex(ValueError,"过期"):
            await self.agent.tool("read_tool_result_page",{"result_ref":view["result_ref"],"path":[]},self.job())

    async def test_provider_context_error_preserves_progress(self):
        def handler(request):
            return httpx.Response(400,json={"error":{"message":"maximum context length exceeded","type":"invalid_request_error"}})
        self.agent.client_factory=lambda **kwargs:AsyncOpenAI(**kwargs,http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        job=self.job()
        answer=await self.agent.chat(job,"keep my complete request")
        self.assertTrue(job["needs_continue"])
        self.assertIn("上下文",answer)
        self.assertEqual(self.executed,[])


class RequestTests(unittest.TestCase):
    def test_long_input_and_continue_endpoint(self):
        from fastapi.testclient import TestClient
        with project_temporary() as temporary:
            assert Path(temporary).resolve().is_relative_to(TEST_WORK.resolve())
            with patch.dict(os.environ,{"SOLIDCRAFT_PARTS":temporary,"SOLIDCRAFT_VISIBLE":"0"}), patch("credentials.load",return_value={}):
                module=importlib.import_module("app")
            module.runtime.settings.api_key="offline-test"
            client=TestClient(module.app)
            text="完整需求"*75000
            response=client.post("/api/chat",json={"prompt":text},headers={"X-Agent-Token":module.TOKEN})
            self.assertEqual(response.status_code,200)
            job,prompt=module.runtime.queue.get_nowait()
            module.runtime.queue.task_done()
            self.assertEqual(prompt,text)
            response=client.post("/api/continue",json={},headers={"X-Agent-Token":module.TOKEN})
            self.assertEqual(response.status_code,409)
            module.runtime.pending_prompt="remaining task"
            module.runtime.can_continue=True
            response=client.post("/api/continue",json={},headers={"X-Agent-Token":module.TOKEN})
            self.assertEqual(response.status_code,200)


if __name__=="__main__":
    unittest.main(verbosity=2)
