"""Printable CAD views with explicit parameter/measurement provenance."""
from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

VIEWS = {"front": "前视图", "top": "俯视图", "right": "右视图", "iso": "等轴测图"}
SOURCES = {"user_text": "用户描述", "image_annotation": "图片标注", "standard": "标准规格", "derived": "已知参数推导", "inferred": "推测待确认", "default": "默认假设", "unknown": "待补充", "cad_measurement": "CAD 实测"}


def write_drawing(directory: Path, brief: dict, measurement: dict, available: dict) -> Path:
    esc = lambda value: html.escape(str(value))
    title = brief.get("part_name") or "CAD 模型"
    figures = []
    for view, label in VIEWS.items():
        path = available.get(view)
        if path and path.is_file():
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content = f'<img src="data:image/png;base64,{encoded}" alt="{label}">'
        else:
            content = '<p class="missing">此视图未生成，请查看执行记录。</p>'
        figures.append(f"<figure><figcaption>{label}</figcaption>{content}</figure>")
    rows = []
    for item in brief.get("parameters", []):
        source = SOURCES.get(item["source"], item["source"]) + ("：" + item["basis"] if item.get("basis") else "")
        rows.append("<tr>" + "".join(f"<td>{esc(value)}</td>" for value in (item["name"], item["value"] or "待补充", item["unit"], source)) + "</tr>")
    facts = []
    for index, body in enumerate(measurement.get("bodies", []), 1):
        bbox = body.get("bbox", {})
        if "min" in bbox and "max" in bbox:
            sizes = [f"{b-a:.4g}" for a, b in zip(bbox["min"], bbox["max"])]
            facts.append(f"实体 {index} 包围盒 X×Y×Z：{' × '.join(sizes)} mm")
        if body.get("volume_mm3") is not None:
            facts.append(f"实体 {index} 体积：{body['volume_mm3']:.6g} mm³")
    if not facts:
        facts.append("本次未获得完整 CAD 测量数据。")
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{esc(title)} · 视图与尺寸说明</title>
<style>*{{box-sizing:border-box}}body{{font:14px/1.6 "Microsoft YaHei",sans-serif;color:#172b35;max-width:1120px;margin:28px auto;padding:20px}}h1{{margin:0}}.meta{{color:#627481}}.views{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}figure{{margin:0;border:1px solid #cbd5dc;padding:10px;break-inside:avoid}}figcaption{{font-weight:bold}}img{{width:100%;height:250px;object-fit:contain}}table{{width:100%;border-collapse:collapse}}td,th{{padding:7px;border:1px solid #cbd5dc;text-align:left}}.missing{{height:220px;color:#97713f}}button{{float:right;padding:8px 15px}}@media print{{body{{margin:0;padding:0;font-size:11px}}button{{display:none}}img{{height:200px}}}}@page{{size:A4 landscape;margin:12mm}}</style>
<button onclick="window.print()">打印 / 保存为 PDF</button><h1>{esc(title)}</h1><p class="meta">模型视图与尺寸说明 · 单位 mm · {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>{esc(brief.get('structure', ''))}</p><div class="views">{''.join(figures)}</div>
<h2>设计参数</h2><table><thead><tr><th>参数</th><th>值</th><th>单位</th><th>依据</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>CAD 实测</h2><ul>{''.join('<li>'+esc(value)+'</li>' for value in facts)}</ul>
<h2>设计假设</h2><ul>{''.join('<li>'+esc(value)+'</li>' for value in brief.get('assumptions', [])) or '<li>无额外假设记录。</li>'}</ul>
<p class="meta">本页为模型视图与参数说明，视图非等比例；尚非带完整尺寸线、公差和技术要求的制造工程图。孔位、壁厚等以设计参数与 CAD 实测为准。</p></html>'''
    target = directory / "drawing.html"
    target.write_text(document, encoding="utf-8")
    return target
