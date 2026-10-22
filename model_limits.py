"""Bounded model-facing CAD data. Never truncates a JSON string mid-argument."""
from __future__ import annotations

import json


def encoded_size(value):
    return len(json.dumps(value,ensure_ascii=False,default=str))


def preview(value,depth=0):
    if depth>6:
        return {"summary":"嵌套数据，请按字段分页读取","type":type(value).__name__}
    if isinstance(value,dict):
        items=list(value.items())
        result={str(k):preview(v,depth+1) for k,v in items[:24]}
        if len(items)>24:
            result["_more_keys"]=len(items)-24
        return result
    if isinstance(value,list):
        if len(value)<=8:
            return [preview(v,depth+1) for v in value]
        return {"items":[preview(v,depth+1) for v in value[:8]],"total":len(value),"truncated":True}
    if isinstance(value,str) and len(value)>1500:
        return value[:1500]+"…（仅预览，完整内容请分页读取）"
    return value


def result_page(value,path,offset=0,limit=25):
    for key in path:
        if isinstance(value,dict):
            value=value[str(key)]
        elif isinstance(value,list):
            value=value[int(key)]
        else:
            raise ValueError("字段路径不能继续进入该值。")
    if isinstance(value,list):
        return {"items":value[offset:offset+limit],"offset":offset,"total":len(value),"next_offset":offset+limit if offset+limit<len(value) else None}
    if isinstance(value,str):
        # Text pages are characters. The transport itself is always valid JSON.
        span=min(limit*100,5000)
        return {"text":value[offset:offset+span],"offset":offset,"total_chars":len(value),"next_offset":offset+span if offset+span<len(value) else None}
    if isinstance(value,dict):
        items=list(value.items())
        return {"fields":dict(items[offset:offset+limit]),"offset":offset,"total_fields":len(items),"next_offset":offset+limit if offset+limit<len(items) else None}
    return {"value":value}


PAGE_TOOL={"type":"function","function":{"name":"read_tool_result_page","description":"按 result_ref 和字段路径读取大型工具结果的指定一页。几何修改后旧ref失效，需重新检查模型获得新ref。数组页最多50项。","parameters":{"type":"object","properties":{"result_ref":{"type":"string"},"path":{"type":"array","items":{"type":"string"},"maxItems":12},"offset":{"type":"integer","minimum":0},"limit":{"type":"integer","minimum":1,"maximum":50}},"required":["result_ref","path"],"additionalProperties":False}}}
