"""Validated, secret-free model profiles and reproducible runtime options."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from jsonschema import Draft202012Validator


MODEL_SCHEMA={
    "type":"object","additionalProperties":False,"required":["model","base_url","api_key_env"],
    "properties":{
        "model":{"type":"string","minLength":1},
        "base_url":{"type":"string","minLength":1},
        "api_key_env":{"type":"string","pattern":"^[A-Za-z_][A-Za-z0-9_]*$"},
        "api_style":{"enum":["deepseek","openai-compatible"]},
        "use_saved_key":{"type":"boolean"},"thinking":{"type":"boolean"},
        "reasoning_effort":{"enum":["low","high","max"]},
        "max_tokens":{"type":["integer","null"],"minimum":1},
    },
}
SCHEMA={
    "type":"object","additionalProperties":False,"required":["models"],
    "properties":{
        "default_model":{"type":"string"},
        "models":{"type":"object","minProperties":1,"additionalProperties":MODEL_SCHEMA},
        "paths":{"type":"object","additionalProperties":False,"properties":{
            "solidcraft_cli":{"type":"string","minLength":1},"output_dir":{"type":"string","minLength":1}}},
        "cad":{"type":"object","additionalProperties":False,"properties":{
            "visible":{"type":"boolean"},"connect":{"type":"string"}}},
        "agent":{"type":"object","additionalProperties":False,"properties":{
            "max_rounds":{"type":"integer","minimum":0},"instructions_file":{"type":"string"}}},
        "workflow":{"type":"object","additionalProperties":False,"properties":{
            "export_formats":{"type":"array","uniqueItems":True,"items":{"enum":["step","stl","obj","3mf"]}},
            "measure":{"type":"boolean"},"preview":{"type":"boolean"},"stage_save":{"type":"boolean"},"drawings":{"type":"boolean"}}},
    },
}


def read_configuration(path):
    path=Path(path).expanduser().resolve()
    value=json.loads(path.read_text(encoding="utf-8-sig"))
    Draft202012Validator(SCHEMA).validate(value)
    for name,profile in value["models"].items():
        address=urlparse(profile["base_url"])
        if address.scheme not in {"http","https"} or not address.hostname or address.username or address.password or address.query or address.fragment:
            raise ValueError(f"模型 {name} 的 base_url 需要为不含凭证的 HTTP(S) API 地址。")
        if profile.get("use_saved_key") and address.hostname != "api.deepseek.com":
            raise ValueError("仅 DeepSeek 官方地址可以使用网页中保存的 DeepSeek Key；其他服务请使用独立环境变量。")
        if profile.get("api_style","deepseek") == "deepseek" and (address.hostname != "api.deepseek.com" or address.scheme != "https"):
            raise ValueError("DeepSeek 模式仅允许官方 HTTPS 地址；其他服务请设置 api_style=openai-compatible。")
        if profile.get("use_saved_key") and address.scheme != "https":
            raise ValueError("已保存 Key 只允许发送到官方 HTTPS 接口。")
    return path,value


def load_configuration(path,profile=None):
    path,value=read_configuration(path)
    name=profile or value.get("default_model") or next(iter(value["models"]))
    if name not in value["models"]:
        raise ValueError(f"找不到模型配置 {name}；可选：{', '.join(value['models'])}")
    result={"profile_name":name,"model":dict(value["models"][name]),"paths":dict(value.get("paths",{})),
            "cad":dict(value.get("cad",{})),"agent":dict(value.get("agent",{})),
            "workflow":dict(value.get("workflow",{})),"source":str(path)}
    for field in ("solidcraft_cli","output_dir"):
        if field in result["paths"]:
            target=Path(result["paths"][field]).expanduser()
            result["paths"][field]=str((target if target.is_absolute() else path.parent/target).resolve())
    instructions=result["agent"].get("instructions_file")
    if instructions:
        target=Path(instructions).expanduser()
        result["agent"]["instructions_file"]=str((target if target.is_absolute() else path.parent/target).resolve())
    return result
