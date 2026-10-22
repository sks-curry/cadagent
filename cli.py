"""Terminal and embeddable entry point; no browser or FastAPI process required."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agent import CadAgent, Settings
from configuration import load_configuration, read_configuration
from vision import prepare_image_files


async def run_task(settings, prompt="", *, projects=None, kind="chat", project=None, new=False, on_event=None, images=None):
    """Run one serial CAD task and always close its MCP session. Returns JSON-safe data."""
    runtime=CadAgent(settings,Path(projects) if projects else None)
    runtime.on_event=on_event
    async def wait_job(identity):
        while runtime.active==identity or runtime.jobs[identity]["status"] in {"queued","running"}:
            if runtime.error:
                raise RuntimeError(runtime.error)
            await asyncio.sleep(.1)
        return runtime.jobs[identity]
    try:
        await runtime.start()
        if runtime.error:
            raise RuntimeError(runtime.error)
        if project:
            target=Path(project)
            if target.is_absolute():
                target=target.resolve()
                if target.parent!=runtime.projects:
                    raise ValueError("--project 必须是当前 output_dir 下的项目目录或项目编号。")
                project=target.name
            opened=await wait_job(runtime.submit("open",str(project)))
            if opened["status"]!="completed":
                raise RuntimeError(opened["error"])
        elif new:
            created=await wait_job(runtime.submit("new"))
            if created["status"]!="completed":
                raise RuntimeError(created["error"])
        prepared = prepare_image_files(images) if images else None
        job=await wait_job(runtime.submit(kind,prompt,images=prepared))
        return {"job_id":job["id"],"status":job["status"],"profile":getattr(settings,"profile_name","default"),
                "model":settings.model,"directory":str(runtime.directory),"answer":job["answer"],
                "error":job["error"],"can_continue":runtime.can_continue,
                "design_brief":runtime.design_brief,"awaiting_parameters":runtime.awaiting_parameters,
                "files":[p.name for p in sorted(runtime.directory.iterdir()) if p.is_file()]}
    finally:
        await runtime.close()


def parser():
    value=argparse.ArgumentParser(description="SolidCraft CAD 智能体终端入口（无需网页）")
    value.add_argument("command",choices=["run","inspect","profiles"])
    value.add_argument("--config",type=Path,help="JSON 配置文件；相对路径按配置文件目录解析")
    value.add_argument("--profile",help="选择 models 中的配置名称")
    inputs=value.add_mutually_exclusive_group()
    inputs.add_argument("--prompt",help="本次需求")
    inputs.add_argument("--prompt-file",type=Path,help="UTF-8 需求文件")
    value.add_argument("--output-dir",type=Path,help="覆盖本次零件根目录")
    value.add_argument("--cli",type=Path,help="覆盖 SolidCraft CLI 路径")
    project=value.add_mutually_exclusive_group()
    project.add_argument("--project",help="已有项目编号或完整目录")
    project.add_argument("--new",action="store_true",help="显式创建空白设计（桌面模式会切换文档）")
    value.add_argument("--continue",dest="resume",action="store_true",help="读取项目中的待续任务")
    value.add_argument("--local",action="store_true",help="只使用已定义的标准件规则，不调用模型")
    mode=value.add_mutually_exclusive_group()
    mode.add_argument("--headless",action="store_true",help="独立 CAD 会话，不连接桌面")
    mode.add_argument("--visible",action="store_true",help="连接或打开 SolidCraft 桌面")
    value.add_argument("--exports",help="本次导出格式，逗号分隔；如 step,stl；空串关闭自动导出")
    value.add_argument("--no-preview",action="store_true")
    value.add_argument("--no-measure",action="store_true")
    value.add_argument("--image",type=Path,action="append",default=[],help="参考图，可重复指定最多4张 PNG/JPEG/WebP")
    value.add_argument("--no-drawings",action="store_true",help="关闭自动三视图与可打印尺寸说明")
    value.add_argument("--quiet",action="store_true",help="关闭 stderr 进度；stdout 始终为最终 JSON")
    return value


def main(argv=None):
    arguments=parser()
    args=arguments.parse_args(argv)
    try:
        if args.command=="profiles":
            if not args.config:
                arguments.error("profiles 需要 --config")
            _,value=read_configuration(args.config)
            print(json.dumps({"default_model":value.get("default_model"),"models":[{"profile":name,"model":model["model"],"base_url":model["base_url"]} for name,model in value["models"].items()]},ensure_ascii=False,indent=2))
            return 0
        if args.profile and not args.config:
            arguments.error("--profile 需要 --config")
        if args.resume and (args.local or args.prompt or args.prompt_file or args.image or args.command!="run"):
            arguments.error("--continue 仅用于 run，不能同时指定需求或 --local")
        configuration=load_configuration(args.config,args.profile) if args.config else None
        settings=Settings(configuration)
        # Terminal runs are isolated unless the user/config explicitly chooses the desktop.
        if configuration is None or args.headless:
            settings.visible=False
            settings.connect=""
        if args.visible:
            settings.visible=True
            settings.connect=settings.connect or "127.0.0.1:17878"
        if args.cli:
            settings.cli=args.cli.expanduser().resolve()
        if args.exports is not None:
            formats=[part.strip() for part in args.exports.split(",") if part.strip()]
            if not set(formats)<= {"step","stl","obj","3mf"}:
                arguments.error("--exports 支持 step,stl,obj,3mf")
            settings.export_formats=list(dict.fromkeys(formats))
        if args.no_preview:
            settings.preview_enabled=False
        if args.no_measure:
            settings.measure_enabled=False
        if args.no_drawings:
            settings.drawings_enabled=False
        if args.image and (args.local or args.command != "run"):
            arguments.error("--image 仅用于模型 run，不能与 --local 同用")
        prompt=args.prompt or (args.prompt_file.read_text(encoding="utf-8-sig") if args.prompt_file else "")
        if args.command=="run" and not args.resume and not prompt.strip() and not args.image and not sys.stdin.isatty():
            prompt=sys.stdin.read()
        if args.image and not prompt.strip():
            prompt="请识别参考图中的零件结构与标注尺寸，补齐关键参数后建模并出图。"
        if args.command=="run" and not args.resume and not prompt.strip():
            arguments.error("请提供 --prompt、--prompt-file 或管道输入")
        kind="inspect" if args.command=="inspect" else ("continue" if args.resume else ("local" if args.local else "chat"))
        if kind in {"chat","continue"} and not settings.key():
            raise ValueError(f"尚未配置环境变量 {settings.api_key_env} 或可用的加密连接。")
        progress=None if args.quiet else lambda event:print(f"[{event['time']}] {event['text']}",file=sys.stderr,flush=True)
        result=asyncio.run(run_task(settings,prompt,projects=args.output_dir,kind=kind,project=args.project,new=args.new,on_event=progress,images=args.image))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return {"completed":0,"paused":2,"awaiting_parameters":3,"failed":1,"stopped":130}.get(result["status"],1)
    except KeyboardInterrupt:
        print(json.dumps({"error":"用户中断；请检查当前 CAD 和最后保存文件。"},ensure_ascii=False),file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error":str(exc)},ensure_ascii=False),file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
