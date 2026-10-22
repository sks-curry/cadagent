"""Independent checks against the exported STL and native CAD measurements."""
import json
import math
import statistics
import struct
from pathlib import Path


def triangles(path):
    raw = Path(path).read_bytes()
    count = struct.unpack_from("<I", raw, 80)[0]
    if len(raw) != 84 + count * 50:
        raise ValueError("STL 格式或三角形数量不符。")
    return [tuple(struct.unpack_from("<3f", raw, 84 + i * 50 + 12 + j * 12) for j in range(3)) for i in range(count)]


def ray_radii(mesh, angle, z, offset=0.0):
    c, s = math.cos(angle), math.sin(angle)
    hits = []
    for triangle in mesh:
        (x0,y0,z0),(x1,y1,z1),(x2,y2,z2) = [(x*c+y*s, -x*s+y*c, zz) for x,y,zz in triangle]
        determinant = (y1-y0)*(z2-z0)-(y2-y0)*(z1-z0)
        if abs(determinant) < 1e-12:
            continue
        a=((offset-y0)*(z2-z0)-(y2-y0)*(z-z0))/determinant
        b=((y1-y0)*(z-z0)-(offset-y0)*(z1-z0))/determinant
        if a >= -1e-8 and b >= -1e-8 and a+b <= 1+1e-8:
            x=x0+a*(x1-x0)+b*(x2-x0)
            if x > 0:
                hits.append(x)
    return sorted(hits)


def section_hull(mesh, z):
    """Convex hull of an XY section; internal threads do not alter outer flats."""
    points = set()
    for triangle in mesh:
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            if abs(a[2] - z) < 1e-8:
                points.add((round(a[0], 7), round(a[1], 7)))
            if (a[2] - z) * (b[2] - z) < 0:
                t = (z - a[2]) / (b[2] - a[2])
                points.add((round(a[0] + t * (b[0] - a[0]), 7), round(a[1] + t * (b[1] - a[1]), 7)))
    points = sorted(points)
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 1e-6:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 1e-6:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def thread_period(samples, spacing=.02):
    """Compare full radial profiles; do not count floating-point plateau peaks."""
    if len(samples) < 80 or max(samples)-min(samples) < .1:
        return None, []
    errors = []
    for lag in range(20, min(61, len(samples)//2)):
        rmse = math.sqrt(sum((a-b)**2 for a, b in zip(samples, samples[lag:])) / (len(samples)-lag))
        errors.append({"pitch_mm": lag*spacing, "radial_rmse_mm": rmse})
    minimum = min(item["radial_rmse_mm"] for item in errors)
    # Multiples of a true period can tie within floating-point error. Choose the
    # fundamental period, rather than declaring 0.6 mm to be 1.2 mm.
    best = min((item for item in errors if item["radial_rmse_mm"] <= minimum + 1e-8), key=lambda item: item["pitch_mm"])
    return best["pitch_mm"], sorted(errors, key=lambda item: item["radial_rmse_mm"])[:5]


def verify(directory, expected_spec=None):
    directory = Path(directory)
    # A test can supply independently fixed acceptance dimensions without replacing
    # the model/recipe's original part-spec.json evidence.
    spec = expected_spec if expected_spec is not None else json.loads((directory / "part-spec.json").read_text(encoding="utf-8"))
    measured = json.loads((directory / "measurements.json").read_text(encoding="utf-8"))
    mesh = triangles(directory / "model.stl")
    checks = []
    details = {}
    def check(name, actual, expected, tolerance):
        ok = abs(actual-expected) <= tolerance
        checks.append({"name": name, "actual": actual, "expected": expected, "tolerance": tolerance, "passed": ok})
    check("实体数", measured["body_count"], 1, 0)
    body = measured["bodies"][0]
    bbox = body["bbox"]
    dimensions = [b-a for a,b in zip(bbox["min"],bbox["max"])]
    if spec["part"] == "solid_box":
        for axis, actual, expected in zip("XYZ", dimensions, [spec["length"],spec["width"],spec["height"]]):
            check("外形"+axis, actual, expected, 1e-6)
        check("实体体积", body["volume_mm3"], spec["length"]*spec["width"]*spec["height"], .001)
        check("六个平面", body.get("face_types",{}).get("plane",0), 6, 0)
    elif spec["part"] == "open_box":
        for axis, actual, expected in zip("XYZ", dimensions, [spec["length"],spec["width"],spec["height"]]):
            check("外形"+axis, actual, expected, 1e-6)
        check("实体体积", body["volume_mm3"], spec["expected_volume"], 1e-5)
        # A ray through the interior first meets the far wall; bottom is separately present.
        bottom_rays = ray_radii(mesh, 0, spec["bottom"] / 2, spec["width"] / 2)
        wall_rays = ray_radii(mesh, 0, spec["bottom"] + 1, spec["width"] / 2)
        inner_span = wall_rays[-1] - wall_rays[-2]
        check("侧壁厚度（导出网格）", inner_span, spec["wall"], .001)
        details["bottom_solid_ray_hits"] = bottom_rays
    elif spec["part"] == "M5_nut":
        check("高度", dimensions[2], 4.7, 1e-5)
        # The request does not prescribe a global XY orientation or a particular
        # chamfer radius. Measure the actual flats, independent of rotation.
        cx, cy = [(a+b)/2 for a, b in zip(bbox["min"][:2], bbox["max"][:2])]
        zmin = bbox["min"][2]
        mesh = [tuple((x-cx, y-cy, z-zmin) for x, y, z in triangle) for triangle in mesh]
        hull = section_hull(mesh, dimensions[2]/2)
        check("六角外轮廓边数", len(hull), 6, 0)
        flat_distances = [abs(a[0]*b[1]-a[1]*b[0])/math.dist(a,b) for a,b in zip(hull,hull[1:]+hull[:1]) if math.dist(a,b)>1e-8]
        for i, distance in enumerate(flat_distances,1):
            check(f"第{i}个扳手平面的轴线距离", distance, 4, .002)
        details["across_flats_from_section_mm"] = [2*distance for distance in flat_distances]
        chamfer_radii = []
        for face_z in (.01, dimensions[2]-.01):
            hits = [ray_radii(mesh, angle, face_z) for angle in (0,math.pi/6,math.pi/3)]
            if any(not hit for hit in hits):
                check("端面倒角截面存在 z="+str(face_z),0,1,0)
                continue
            radii = [max(hit) for hit in hits]
            check("端面圆周一致性 z="+str(face_z), max(radii)-min(radii), 0, .035)
            check("端面外圈小于六角扳手面 z="+str(face_z),int(2.5<max(radii)<3.97),1,0)
            chamfer_radii.append(statistics.mean(radii))
        if len(chamfer_radii)==2:
            check("上下圆周倒角对称性",abs(chamfer_radii[0]-chamfer_radii[1]),0,.035)
        details["chamfer_end_radii_mm"] = chamfer_radii
        samples=[]
        for i in range(160):
            z=.75+i*.02
            radii=ray_radii(mesh,0,z,.013)
            if radii:
                samples.append((z,radii[0]))
        peaks=[p for i,p in enumerate(samples[1:-1],1) if p[1] > 2.3 and p[1]>samples[i-1][1] and p[1]>=samples[i+1][1]]
        spacings=[peaks[i+1][0]-peaks[i][0] for i in range(len(peaks)-1)]
        pitch, candidates = thread_period([radius for z,radius in samples])
        if pitch is None:
            check("实际螺旋牙型", 0, 1, 0)
        else:
            check("实际牙距（径向轮廓周期）", pitch, .8, .025)
        if samples:
            check("螺纹牙底大径", 2*max(r for z,r in samples), 5, .015)
            actual_minor = 2*min(r for z,r in samples)
            lower = spec.get("nominal_thread_minor_diameter",5-1.08253175473*.8)
            upper = spec.get("drill_diameter",4.2)
            checks.append({"name":"螺纹牙顶小径允许范围","actual":actual_minor,"expected":[lower,upper],"tolerance":.015,"passed":lower-.015<=actual_minor<=upper+.015})
        details["thread_peaks"] = peaks
        details["thread_pitch_samples"] = spacings
        details["thread_period_candidates"] = candidates
        details["acceptance_basis"] = "M5×0.8、对边8、高4.7、实际牙型及对称圆周倒角；未要求固定XY朝向、倒角半径或仅限4.2mm底孔。小径在理论基本值与既有配方底孔范围内做几何检查，不声明公差合格。"
        details["standard"] = "GB/T 6170-2015 nominal geometry; no manufacturing certification"
    elif spec["part"] == "spur_gear":
        check("齿宽", dimensions[2], spec["width"], 1e-5)
        bottom = {(round(x,6),round(y,6)) for triangle in mesh for x,y,z in triangle if abs(z)<1e-6}
        radii=[math.hypot(x,y) for x,y in bottom]
        outer=[r for r in radii if r>spec["bore"]/2+1]
        check("齿顶圆直径", 2*max(outer), spec["outside_diameter"], .0001)
        check("齿根圆直径", 2*min(outer), spec["root_diameter"], .0001)
        inner=[r for r in radii if r<spec["bore"]/2+1]
        check("轴孔直径（圆周顶点）", 2*statistics.median(inner), spec["bore"], .0001)
        tip_angles=sorted(math.atan2(y,x)%(2*math.pi) for x,y in bottom if math.hypot(x,y)>spec["outside_diameter"]/2-.0001)
        angular_gaps=[(tip_angles[(i+1)%len(tip_angles)]-tip_angles[i])%(2*math.pi) for i in range(len(tip_angles))]
        check("实际独立齿顶数", sum(g>math.pi/spec["teeth"] for g in angular_gaps), spec["teeth"], 0)
        rb=spec["base_diameter"]/2
        inv=math.tan(math.radians(20))-math.radians(20)
        errors=[]
        for x,y in bottom:
            r=math.hypot(x,y)
            if r<rb+.25 or r>spec["outside_diameter"]/2-.02:
                continue
            theta=abs((math.atan2(y,x)+math.pi/spec["teeth"])%(2*math.pi/spec["teeth"])-math.pi/spec["teeth"])
            t=math.sqrt((r/rb)**2-1)
            expected=math.pi/(2*spec["teeth"])+inv-(t-math.atan(t))
            errors.append(abs(theta-expected)*r)
        if errors:
            check("齿面采样点渐开线偏差", max(errors), 0, .003)
        else:
            check("有可验证渐开线采样点", 0, 1, 0)
        relative=abs(body["volume_mm3"]-spec["expected_volume"])/spec["expected_volume"]
        check("体积相对误差", relative, 0, .0001)
        details["pitch_diameter_from_module_and_teeth"] = spec["pitch_diameter"]
        details["flank_samples_checked"] = len(errors)
        details["curve_discretization"] = {k:v for k,v in spec.items() if "chord_error" in k}
    for name in ("design.solidcraft", "model.step", "model.stl", "preview.png"):
        check("文件存在且非空："+name, int((directory/name).is_file() and (directory/name).stat().st_size>50), 1, 0)
    report = {"passed": all(c["passed"] for c in checks), "part": spec["part"], "checks": checks, "details": details, "scope": "几何与文件检查；不代替制造公差、材料或精度等级认证。"}
    (directory / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
