"""Structured requirements and a deterministic gate before model-driven CAD writes."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator

BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "part_name": {"type": "string", "minLength": 1, "maxLength": 200},
        "structure": {"type": "string", "minLength": 1, "maxLength": 6000},
        "parameters": {"type": "array", "maxItems": 100, "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "name": {"type": "string", "minLength": 1, "maxLength": 120},
                "value": {"type": ["string", "null"], "maxLength": 500},
                "unit": {"type": "string", "maxLength": 40},
                "source": {"enum": ["user_text", "image_annotation", "standard", "derived", "inferred", "default", "unknown", "cad_measurement"]},
                "basis": {"type": "string", "minLength": 1, "maxLength": 500, "description": "standard/derived 来源必填：已指定的标准/工具规格，或基于已知参数的推导公式；不得以图片比例估计充当推导。"},
                "required": {"type": "boolean"},
            }, "required": ["name", "value", "unit", "source", "required"],
        }},
        "missing_parameters": {"type": "array", "maxItems": 30, "items": {"type": "string", "minLength": 1, "maxLength": 500}},
        "assumptions": {"type": "array", "maxItems": 30, "items": {"type": "string", "maxLength": 500}},
        "ready_to_model": {"type": "boolean"},
    }, "required": ["part_name", "structure", "parameters", "missing_parameters", "assumptions", "ready_to_model"],
}

BRIEF_TOOL = {"type": "function", "function": {
    "name": "record_design_brief",
    "description": "在任何 CAD 修改前记录从文字/参考图提取的结构、尺寸、来源、默认假设与缺失问题。已指定标准唯一确定的值用 standard，基于已知参数的公式计算用 derived，两者填写 basis，不重复问用户标准常数。缺失或仅从图片比例猜测的关键尺寸不能建模；齐全后 ready_to_model=true 自动继续。",
    "parameters": BRIEF_SCHEMA,
}}

READ_ONLY = {"list_commands", "inspect_design", "measure", "body_topology", "screenshot", "save", "export", "read_tool_result_page", "record_design_brief", "review_model_view"}


def save_brief(directory: Path, arguments: dict) -> dict:
    Draft202012Validator(BRIEF_SCHEMA).validate(arguments)
    brief = copy.deepcopy(arguments)
    missing = list(brief["missing_parameters"])
    if not brief["parameters"]:
        missing.append("请提供决定模型尺寸的关键参数或适用的标准规格。")
    for parameter in brief["parameters"]:
        if parameter["source"] in {"standard", "derived"} and not parameter.get("basis", "").strip():
            raise ValueError(f"{parameter['name']} 的 {parameter['source']} 来源需要在 basis 填写所选标准/工具规格或已知参数的公式；如果只是图片比例估计，请使用 inferred 并向用户补问。")
        if parameter["required"] and (
            not (parameter["value"] or "").strip()
            or parameter["source"] in {"unknown", "inferred", "default"}
        ):
            words = [word for word in re.findall(r"[A-Za-z_]+|[\u4e00-\u9fff]+", parameter["name"]) if len(word) > 1]
            if not any(any(word.casefold() in question.casefold() for word in words) for question in missing):
                missing.append(f"请补充或确认{parameter['name']}的实际数值（{parameter['unit'] or '规格'}）。")
    brief["missing_parameters"] = list(dict.fromkeys(missing))
    brief["ready_to_model"] = bool(brief["ready_to_model"] and not missing)
    brief["updated"] = datetime.now().isoformat(timespec="seconds")
    (directory / "design-brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# " + brief["part_name"], "", brief["structure"], "", "## 尺寸与来源", ""]
    for parameter in brief["parameters"]:
        lines.append(f"- {parameter['name']}：{parameter['value'] or '待补充'} {parameter['unit']}（{parameter['source']}）" + (f"；依据：{parameter['basis']}" if parameter.get("basis") else ""))
    if brief["assumptions"]:
        lines.extend(["", "## 假设", "", *["- " + value for value in brief["assumptions"]]])
    if brief["missing_parameters"]:
        lines.extend(["", "## 待补齐参数", "", *["- " + value for value in brief["missing_parameters"]]])
    (directory / "design-brief.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return brief


def load_brief(directory: Path) -> dict:
    path = directory / "design-brief.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def clarification(brief: dict) -> str:
    lines = [f"已整理：{brief['part_name']}。", brief["structure"], "", "已知尺寸／规格："]
    for item in brief["parameters"]:
        if item["value"] is not None:
            lines.append(f"- {item['name']}：{item['value']} {item['unit']}")
    lines.extend(["", "建模前还需要补充："])
    lines.extend(f"{i}. {value}" for i, value in enumerate(brief["missing_parameters"], 1))
    if not brief["missing_parameters"]:
        lines.append("请补充或确认关键尺寸与结构要求。")
    lines.append("\n直接在下方回复这些参数即可，补齐后会自动继续建模。")
    return "\n".join(lines)
