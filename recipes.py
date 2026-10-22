"""Bounded CAD recipes. Geometry is executed through the real SolidCraft MCP."""
from __future__ import annotations

import math

STANDARD_SOURCES = {
    "nut_standard": "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=BDDE5AF77AC2FBC2D194289F10C69A4B",
    "nut_dimensions": "https://www.globalfastener.com/standards/detailn_4507.html",
    "gear_standard": "https://openstd.samr.gov.cn/bzgk/std/newGbInfo?hcno=7D44A15888B2E686063491408AB63CC8",
    "gear_formulas": "https://khkgears.net/gear-knowledge/gear-technical-reference/calculation-gear-dimensions/",
}


def positive(value, name, lower=0.1, upper=1000):
    value = float(value)
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} 必须在 {lower}–{upper} 范围内。")
    return value


def step(command, **params):
    return {"command": command, "params": params}


def parameters(**values):
    return [step("parameters.change", name=k, expression=v, unit="mm") for k, v in values.items()]


def solid_box(length=80, width=60, height=30):
    length, width, height = [positive(v,k) for k,v in zip(("长","宽","高"),(length,width,height))]
    steps=parameters(length=length,width=width,height=height)
    steps.append(step("solid.box",length="length",width="width",height="height",corner=[0,0,0],name="SolidBox",body_name="SolidBox"))
    return steps,{"part":"solid_box","length":length,"width":width,"height":height,"expected_volume":length*width*height,"assumptions":"实心长方体，不抽壳。"}


def open_box(length=80, width=60, height=30, wall=3, bottom=3):
    length, width, height, wall, bottom = [positive(v, k) for k, v in zip(("长", "宽", "高", "壁厚", "底厚"), (length, width, height, wall, bottom))]
    if 2 * wall >= min(length, width) or bottom >= height:
        raise ValueError("壁厚或底厚超过盒子尺寸。")
    steps = parameters(length=length, width=width, height=height, wall=wall, bottom=bottom)
    steps += [step("solid.box", length="length", width="width", height="height", corner=[0, 0, 0], body_name="OpenBox", name="OuterBox"),
              step("solid.box", length="length-2*wall", width="width-2*wall", height="height-bottom", corner=["wall", "wall", "bottom"], operation="cut", targets=["OpenBox"], name="OpenCavity")]
    expected = length * width * height - (length - 2 * wall) * (width - 2 * wall) * (height - bottom)
    return steps, {"part": "open_box", "length": length, "width": width, "height": height, "wall": wall, "bottom": bottom, "expected_volume": expected, "assumptions": "上方开口，壁厚和底厚为可编辑命名参数。"}


def m5_nut(modeled=True):
    """GB/T 6170-2015 M5: s=8, m=4.7, P=.8; a drawing model, not a material certificate."""
    across, height, drill = 8.0, 4.7, 4.2
    steps = parameters(across_flats=across, nut_height=height, thread_pitch=.8, major_diameter=5, drill_diameter=drill)
    steps += [step("sketch.create", plane="XY", name="Hexagon"),
              step("sketch.polygon.circumscribed", center=[0, 0], radius=across / 2, sides=6, angle=0),
              step("sketch.finish"),
              step("solid.extrude", sketch="Hexagon", distance="nut_height", operation="new", body_name="M5Nut", name="HexBody"),
              step("solid.hole", position=[0, 0, "nut_height"], diameter="drill_diameter")]
    # Circular conical nut chamfers, made by revolving an annular cutting section.
    # The mid-height hex and its wrenching flats remain full size.
    for name, z0, z1 in (("TopNutChamfer", height, height - .55), ("BottomNutChamfer", 0, .55)):
        steps += [step("sketch.create", plane={"origin": [0, 0, 0], "x_dir": [1, 0, 0], "y_dir": [0, 0, 1]}, name=name),
                  step("sketch.line", points=[[3.7, z0], [5.1, z0], [5.1, z1], [4.65, z1]], closed=True, infer=False),
                  step("sketch.finish"),
                  step("solid.revolve", sketch=name, axis="Z", angle=360, operation="cut", targets=["M5Nut"], name=name + "Cut")]
    steps += [step("solid.chamfer", edges=[[drill / 2, 0, height], [drill / 2, 0, 0]], distance=.5, body="M5Nut"),
              step("solid.thread", face=[drill / 2, 0, height / 2], designation="M5", length=height - 1, modeled=bool(modeled))]
    return steps, {"part": "M5_nut", "standard": "GB/T 6170-2015", "across_flats": across, "height": height, "thread": "M5×0.8", "modeled_requested": bool(modeled), "drill_diameter": drill, "nominal_thread_minor_diameter": 5 - 1.08253175473 * .8, "limits": {"height": [4.4, 4.7], "across_flats": [7.78, 8], "across_corners_min": 8.79}, "sources": STANDARD_SOURCES, "scope": "几何模型；不包含材料等级、表面处理或通止规检验。"}


def involute_outline(module, teeth, pressure_angle=20, samples=40):
    """Analytic involute flanks and a rolling-rack circular-tip root envelope.

    Cutter tip radius is 0.38m; the resulting trochoid joins the involute tangentially.
    Native sketch segments approximate both curves; sampling error is recorded.
    """
    m = positive(module, "模数", .25, 10)
    z = int(teeth)
    if z != teeth or not 18 <= z <= 100:
        raise ValueError("此无变位齿轮工具支持整数齿数 18–100。")
    alpha = math.radians(positive(pressure_angle, "压力角", 20, 20))
    rp, ra, rf = m * z / 2, m * (z + 2) / 2, m * (z - 2.5) / 2
    rb = rp * math.cos(alpha)
    inv_alpha = math.tan(alpha) - alpha
    half_base = math.pi / (2 * z) + inv_alpha
    ta = math.sqrt((ra / rb) ** 2 - 1)
    rho = .38 * m
    cutter_v = -1.25 * m + rho
    cutter_u = math.pi * m / 4 + cutter_v * math.tan(alpha) - rho / math.cos(alpha)
    phi0 = -cutter_u / rp
    phi1 = (-cutter_v / math.tan(alpha) - cutter_u) / rp
    root = []
    def root_point(phi):
        a = cutter_u + rp * phi
        norm = math.hypot(a, cutter_v)
        x, y = a + rho * a / norm, rp + cutter_v + rho * cutter_v / norm
        return math.hypot(x, y), phi + math.atan2(y, x) - (math.pi / 2 - math.pi / z)
    for i in range(samples + 1):
        root.append(root_point(phi0 + (phi1 - phi0) * i / samples))
    join_radius = root[-1][0]
    t0 = math.sqrt(max((join_radius / rb) ** 2 - 1, 0))
    flank = []
    for i in range(samples + 1):
        t = t0 + (ta - t0) * i / samples
        radius = rb * math.sqrt(1 + t * t)
        half_angle = half_base - (t - math.atan(t))
        flank.append((radius, half_angle))
    points = []
    max_error = 0.0
    polar = lambda radius, angle: [radius * math.cos(angle), radius * math.sin(angle)]
    for tooth in range(z):
        center = tooth * 2 * math.pi / z
        for radius, half_angle in root[:-1]:
            points.append(polar(radius, center - half_angle))
        for radius, half_angle in flank:
            points.append(polar(radius, center - half_angle))
        tip_half = flank[-1][1]
        for j in range(1, 13):
            points.append(polar(ra, center - tip_half + 2 * tip_half * j / 12))
        for radius, half_angle in reversed(flank[:-1]):
            points.append(polar(radius, center + half_angle))
        for radius, half_angle in reversed(root[:-1]):
            points.append(polar(radius, center + half_angle))
        root_half = root[0][1]
        gap = 2 * math.pi / z - 2 * root_half
        for j in range(1, 13):
            points.append(polar(rf, center + root_half + gap * j / 12))
    # Compute actual sampled flank chord deviation at interval midpoints.
    for i in range(samples):
        t = t0 + (ta - t0) * (i + .5) / samples
        radius = rb * math.sqrt(1 + t * t)
        point = polar(radius, half_base - t + math.atan(t))
        a = polar(*flank[i]); b = polar(*flank[i + 1])
        dx, dy = b[0] - a[0], b[1] - a[1]
        deviation = abs(dx * (a[1] - point[1]) - (a[0] - point[0]) * dy) / math.hypot(dx, dy)
        max_error = max(max_error, deviation)
    root_error = 0.0
    for i in range(samples):
        p = polar(*root_point(phi0 + (phi1 - phi0) * (i + .5) / samples))
        a, b = polar(*root[i]), polar(*root[i + 1])
        dx, dy = b[0] - a[0], b[1] - a[1]
        root_error = max(root_error, abs(dx * (a[1] - p[1]) - (a[0] - p[0]) * dy) / math.hypot(dx, dy))
    cleaned = []
    for point in points:
        if not cleaned or math.dist(point, cleaned[-1]) > 1e-8:
            cleaned.append(point)
    if math.dist(cleaned[0], cleaned[-1]) < 1e-8:
        cleaned.pop()
    return cleaned, {"module": m, "teeth": z, "pressure_angle": 20, "profile_shift": 0, "pitch_diameter": 2 * rp, "outside_diameter": 2 * ra, "root_diameter": 2 * rf, "base_diameter": 2 * rb, "sampled_involute_chord_error_mm": max_error, "sampled_root_chord_error_mm": root_error, "cutter_tip_radius": rho, "profile": "analytic involute sampled as native sketch line segments", "root_transition": "rolling rack circular-tip trochoid envelope, cutter tip radius 0.38m"}


def spur_gear(module=2, teeth=25, bore=20, width=10):
    width = positive(width, "齿宽", 1, 100)
    bore = positive(bore, "轴孔直径", 1, 500)
    points, info = involute_outline(module, teeth)
    if bore >= info["root_diameter"] - 2:
        raise ValueError("轴孔过大，齿根处没有足够实体。")
    steps = parameters(module=module, face_width=width, bore_diameter=bore)
    steps += [step("parameters.change", name="tooth_count", expression=teeth, unit=""),
              step("sketch.create", plane="XY", name="InvoluteGearProfile"),
              step("sketch.line", points=points, closed=True, infer=False, connect=False),
              step("sketch.finish"),
              step("solid.extrude", sketch="InvoluteGearProfile", distance="face_width", operation="new", body_name="SpurGear", name="GearExtrude"),
              step("solid.hole", position=[0, 0, "face_width"], diameter="bore_diameter")]
    outer_area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2
    info.update(part="spur_gear", width=width, bore=bore, outline_vertices=len(points), expected_volume=(outer_area - math.pi * (bore / 2) ** 2) * width, sources=STANDARD_SOURCES, scope="标准比例渐开线及滚动齿条刀尖齿根包络的离散几何模型；未声明齿轮制造精度等级。")
    return steps, info


CUSTOM_TOOLS = [
    {"type":"function","function":{"name":"build_solid_box","description":"在空白设计创建实心长方体，长宽高单位mm，不抽壳。","parameters":{"type":"object","properties":{k:{"type":"number"} for k in ("length","width","height")},"required":["length","width","height"]}}},
    {"type": "function", "function": {"name": "build_open_box", "description": "在空白设计中创建带命名参数的上方开口盒子。尺寸mm；用户未给壁/底厚时应说明采用的默认值。", "parameters": {"type": "object", "properties": {k: {"type": "number"} for k in ("length", "width", "height", "wall", "bottom")}, "required": ["length", "width", "height"]}}},
    {"type": "function", "function": {"name": "build_m5_nut", "description": "在空白设计创建GB/T6170-2015 M5六角螺母：对边8、高4.7、螺距0.8，圆周倒角和螺纹。返回实测及限制；不能声称材料/公差检验合格。", "parameters": {"type": "object", "properties": {"modeled": {"type": "boolean", "description": "要求实际螺旋牙型；默认true。失败必须报告，不得把光孔当实体螺纹。"}}}}},
    {"type": "function", "function": {"name": "build_spur_gear", "description": "在空白设计创建20度零变位渐开线直齿轮，模数mm、整数齿数18–100、轴孔、齿宽。齿面和滚动齿条刀尖齿根包络为解析曲线离散，非制造认证模型。", "parameters": {"type": "object", "properties": {"module": {"type": "number"}, "teeth": {"type": "integer"}, "bore": {"type": "number"}, "width": {"type": "number"}}, "required": ["module", "teeth", "bore"]}}},
]

BUILDERS = {"build_solid_box":solid_box, "build_open_box": open_box, "build_m5_nut": m5_nut, "build_spur_gear": spur_gear}
