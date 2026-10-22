"""A local-only web UI. All model, MCP and file operations run in Python."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import sys
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import CadAgent, ROOT
from vision import prepare_images, load_references
import credentials

TOKEN = secrets.token_urlsafe(32)
runtime = CadAgent()


@asynccontextmanager
async def lifespan(app):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="DeepSeek × SolidCraft", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def local_access(request: Request, call_next):
    host = request.url.hostname
    if host not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "仅允许本机访问。"}, status_code=403)
    if request.method not in {"GET", "HEAD"}:
        if not hmac.compare_digest(request.headers.get("x-agent-token", ""), TOKEN):
            return JSONResponse({"detail": "页面会话已失效，请刷新页面。"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "不允许跨站请求。"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    return response


@app.get("/", response_class=HTMLResponse)
async def index():
    return (ROOT / "index.html").read_text(encoding="utf-8").replace("__AGENT_TOKEN__", TOKEN)


@app.get("/api/status")
async def status():
    project_items = []
    for directory in sorted(runtime.projects.iterdir(), reverse=True):
        if not directory.is_dir() or not ((directory / "design.solidcraft").is_file() or (directory / "design-brief.json").is_file()):
            continue
        brief = {}
        try:
            brief_path = directory / "design-brief.json"
            if brief_path.is_file():
                brief = json.loads(brief_path.read_text(encoding="utf-8"))
                if not isinstance(brief, dict):
                    brief = {}
        except (OSError, ValueError):
            pass
        project_items.append({"id": directory.name, "title": brief.get("part_name") or "未命名设计",
                              "has_model": (directory / "design.solidcraft").is_file(),
                              "awaiting_parameters": bool(brief and not brief.get("ready_to_model"))})
    return {
        "ready": runtime.ready.is_set() and not runtime.error,
        "error": runtime.error,
        "version": runtime.server_version,
        "cli": str(runtime.settings.cli),
        "app_directory": str(ROOT),
        "mode": "桌面可见绘制" if runtime.desktop else ("桌面桥接" if runtime.settings.connect else "独立无界面会话"),
        "connection_saved": credentials.DEFAULT_PATH.is_file(),
        "saved_config_error": runtime.settings.saved_error,
        "key_configured": bool(runtime.settings.key()),
        "model": runtime.settings.model,
        "thinking": runtime.settings.thinking,
        "reasoning_effort": runtime.settings.reasoning_effort,
        "max_tokens": runtime.settings.max_tokens,
        "max_rounds": runtime.settings.max_rounds,
        "can_continue": runtime.can_continue,
        "awaiting_parameters": runtime.awaiting_parameters,
        "design_brief": runtime.design_brief,
        "references": load_references(runtime.directory),
        "active_job": runtime.active,
        "project": runtime.directory.name,
        "directory": str(runtime.directory),
        "snapshot": runtime.snapshot,
        "preview": f"/files/preview.png?v={runtime.preview_serial}" if (runtime.directory / "preview.png").is_file() else None,
        "files": [p.name for p in sorted(runtime.directory.iterdir()) if p.suffix.lower() in {".solidcraft", ".step", ".stl", ".obj", ".3mf", ".png", ".jpg", ".html", ".json", ".jsonl", ".md"} and not p.name.startswith("reference-")],
        "projects": [item["id"] for item in project_items],
        "project_items": project_items,
    }


class Configuration(BaseModel):
    api_key: str = Field(default="", max_length=512)
    model: str = Field(default="deepseek-flash", pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    thinking: bool = True
    reasoning_effort: Literal["low", "high", "max"] = "high"
    clear_key: bool = False
    remember: bool = False


@app.post("/api/config")
async def configure(body: Configuration):
    if runtime.active or not runtime.queue.empty():
        raise HTTPException(409, "任务执行期间不能修改配置。")
    if body.clear_key:
        runtime.settings.api_key = ""
        credentials.clear()
    elif body.api_key.strip():
        runtime.settings.api_key = body.api_key.strip()
    runtime.settings.model = body.model
    runtime.settings.thinking = body.thinking
    runtime.settings.reasoning_effort = body.reasoning_effort
    if body.remember and not body.clear_key:
        try:
            credentials.save(runtime.settings.key(), body.model, body.thinking, body.reasoning_effort)
        except Exception as exc:
            raise HTTPException(400, str(exc)) from None
    elif not body.remember:
        credentials.clear()
    return {"key_configured": bool(runtime.settings.key()), "model": body.model, "connection_saved": credentials.DEFAULT_PATH.is_file()}


class ReferenceImage(BaseModel):
    name: str = Field(default="参考图片", max_length=200)
    data_url: str = Field(max_length=12 * 1024 * 1024)


class ChatRequest(BaseModel):
    prompt: str = ""
    images: list[ReferenceImage] = Field(default_factory=list, max_length=4)


def submit(kind, prompt="", images=None):
    try:
        return {"job_id": runtime.submit(kind, prompt, images=images)}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from None


@app.post("/api/chat")
async def chat(body: ChatRequest):
    if not body.prompt.strip() and not body.images:
        raise HTTPException(422, "请输入尺寸与结构要求，或上传参考图片。")
    try:
        images = await asyncio.to_thread(prepare_images, [image.model_dump() for image in body.images])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    prompt = body.prompt.strip() or "请识别参考图中的零件结构与标注尺寸，补齐关键参数后建模并出图。"
    return submit("chat", prompt, images)


@app.post("/api/local")
async def local_build(body: ChatRequest):
    if body.images:
        raise HTTPException(422, "图片识别请使用 DeepSeek 智能体模式。")
    if not body.prompt.strip():
        raise HTTPException(422, "请输入建模需求。")
    return submit("local", body.prompt.strip())


@app.post("/api/continue")
async def continue_task():
    return submit("continue")


@app.post("/api/inspect")
async def inspect_model():
    return submit("inspect")


@app.post("/api/new")
async def new_project():
    return submit("new")


class OpenProject(BaseModel):
    project: str = Field(pattern=r"^\d{8}-\d{6}-[a-f0-9]{6}$")


@app.post("/api/open")
async def open_project(body: OpenProject):
    return submit("open", body.project)


@app.post("/api/stop")
async def stop():
    runtime.stopping.set()
    return {"message": "将在当前请求返回后停止后续模型/工具调用；不会中断正在执行的 CAD 操作。"}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    if job_id not in runtime.jobs:
        raise HTTPException(404, "找不到任务。")
    return runtime.jobs[job_id]


@app.get("/files/{filename}")
async def download(filename: str):
    allowed = {".solidcraft", ".step", ".stl", ".obj", ".3mf", ".png", ".jpg", ".html", ".json", ".jsonl", ".md"}
    path = (runtime.directory / filename).resolve()
    if path.parent != runtime.directory.resolve() or path.suffix.lower() not in allowed or not path.is_file():
        raise HTTPException(404, "文件不存在。")
    if path.suffix == ".png":
        return FileResponse(path, media_type="image/png")
    if path.suffix == ".jpg":
        return FileResponse(path, media_type="image/jpeg")
    return FileResponse(path, filename=path.name)


if __name__ == "__main__":
    import argparse
    import threading
    import uvicorn

    parser = argparse.ArgumentParser(description="DeepSeek SolidCraft CAD 智能体")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        timer = threading.Timer(2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}"))
        timer.daemon = True
        timer.start()
    # One worker: each MCP session and all its calls belong to the same asyncio task.
    # Use a Proactor event loop on Windows because stdio MCP needs subprocess pipes.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="info", access_log=False, loop="asyncio")
    asyncio.run(uvicorn.Server(config).serve())
