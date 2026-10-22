"""Validated local reference images; only normalized pixels go to the model."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import uuid
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_SIDE = 3000
FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass
class PreparedImage:
    name: str
    data: bytes
    mime: str
    width: int
    height: int


def normalize_image(data: bytes, name: str) -> PreparedImage:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("每张图片需要为非空文件，且不超过 8 MB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in FORMATS:
                    raise ValueError("仅支持实际内容为 PNG、JPEG 或 WebP 的图片。")
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("图片像素过大，请缩小到 4000 万像素以内。")
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
                # Flatten transparency on white and strip EXIF/other metadata.
                rgba = image.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                stream = io.BytesIO()
                clean.save(stream, format="PNG", optimize=True)
                normalized = stream.getvalue()
                mime = "image/png"
                if len(normalized) > 4 * 1024 * 1024:
                    stream = io.BytesIO()
                    clean.save(stream, format="JPEG", quality=92, optimize=True)
                    normalized, mime = stream.getvalue(), "image/jpeg"
                safe_name = re.sub(r"[\x00-\x1f\x7f]", "", name.replace("\\", "/").split("/")[-1])[:160] or "参考图片"
                return PreparedImage(safe_name, normalized, mime, clean.width, clean.height)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无法解码或像素过大，请重新导出为 PNG/JPEG/WebP。") from exc


def prepare_images(items: list[dict]) -> list[PreparedImage]:
    if len(items) > MAX_IMAGES:
        raise ValueError("每个项目最多使用 4 张参考图，可上传不同视角或局部细节。")
    prepared, total = [], 0
    for item in items:
        value = item["data_url"]
        if not isinstance(value, str) or not re.fullmatch(r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=\r\n]+", value):
            raise ValueError("图片需要为 PNG/JPEG/WebP 的 Base64 数据，不能使用外部地址。")
        encoded = value.split(",", 1)[1]
        if len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4 + 1024:
            raise ValueError("每张图片不得超过 8 MB。")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("图片 Base64 数据不完整。") from exc
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("一次上传的图片总大小不得超过 16 MB。")
        prepared.append(normalize_image(data, item.get("name", "参考图片")))
    return prepared


def prepare_image_files(paths) -> list[PreparedImage]:
    if len(paths) > MAX_IMAGES:
        raise ValueError("最多指定 4 张参考图片。")
    total, images = 0, []
    for value in paths:
        path = Path(value)
        total += path.stat().st_size
        if path.stat().st_size > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
            raise ValueError("每张图片最多 8 MB，合计最多 16 MB。")
        images.append(normalize_image(path.read_bytes(), path.name))
    return images


def load_references(directory: Path) -> list[dict]:
    path = directory / "references.json"
    if not path.is_file():
        return []
    references = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(references, list) or len(references) > MAX_IMAGES:
        raise ValueError("项目参考图记录无效。")
    for item in references:
        filename = item.get("filename", "")
        if not re.fullmatch(r"reference-[a-f0-9]{32}\.(?:png|jpg)", filename):
            raise ValueError("项目参考图文件名无效。")
        if not (directory / filename).is_file():
            raise ValueError("项目参考图已丢失，请恢复图片或新建项目重新上传。")
    return references


def save_references(directory: Path, images: list[PreparedImage]) -> list[dict]:
    previous = load_references(directory)
    if len(previous) + len(images) > MAX_IMAGES:
        raise ValueError("当前项目最多保存 4 张参考图；更多图片请在新项目中使用。")
    added = []
    for image in images:
        filename = "reference-" + uuid.uuid4().hex + (".png" if image.mime == "image/png" else ".jpg")
        (directory / filename).write_bytes(image.data)
        added.append({"filename": filename, "name": image.name, "mime": image.mime, "width": image.width, "height": image.height})
    if images:
        (directory / "references.json").write_text(json.dumps([*previous, *added], ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def image_blocks(directory: Path, references: list[dict]) -> list[dict]:
    # Revalidate persisted names before reading; never accept arbitrary model paths.
    allowed = {entry["filename"] for entry in load_references(directory)}
    blocks = []
    for item in references:
        if item["filename"] not in allowed:
            raise ValueError("参考图不属于当前项目。")
        data = (directory / item["filename"]).read_bytes()
        mime = "image/png" if item["filename"].endswith(".png") else "image/jpeg"
        blocks.extend([
            {"type": "text", "text": "参考图：" + item["name"]},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64," + base64.b64encode(data).decode("ascii"), "detail": "high"}},
        ])
    return blocks
