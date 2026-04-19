#!/usr/bin/env python3
"""Shared utilities for Markdown article parsing and clipboard operations."""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def resolve_image_path(img_path: str, base_path: Path) -> str:
    if os.path.isabs(img_path):
        return img_path
    img_filename = Path(img_path).name
    for candidate in [base_path / img_path, base_path / "assets" / img_filename]:
        if candidate.exists():
            return str(candidate.resolve())
    for assets_dir in base_path.glob("*.assets"):
        candidate = assets_dir / img_filename
        if candidate.exists():
            return str(candidate.resolve())
    return str((base_path / img_path).resolve())


def split_into_blocks(markdown: str) -> List[str]:
    blocks = []
    current_block: List[str] = []
    in_code_block = False
    code_block_lines: List[str] = []
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                if code_block_lines:
                    blocks.append("___CODE_BLOCK_START___" + "\n".join(code_block_lines) + "___CODE_BLOCK_END___")
                code_block_lines = []
            else:
                if current_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                in_code_block = True
            continue
        if in_code_block:
            code_block_lines.append(line)
            continue
        if not stripped:
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
            continue
        if stripped.startswith(("#", ">")):
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
            blocks.append(stripped)
            continue
        if re.match(r"^!\[.*\]\(.*\)$", stripped) or re.match(r"^!\[\[.+\]\]$", stripped):
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
            blocks.append(stripped)
            continue
        current_block.append(line)
    if current_block:
        blocks.append("\n".join(current_block))
    if code_block_lines:
        blocks.append("___CODE_BLOCK_START___" + "\n".join(code_block_lines) + "___CODE_BLOCK_END___")
    return blocks


def extract_title_and_cover(markdown: str, base_path: Path) -> Tuple[str, str, str, Optional[str]]:
    lines = markdown.strip().split("\n")
    title = "Untitled"
    title_line_idx = None
    title_source = "none"
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            title = stripped[2:].strip()
            title_line_idx = idx
            title_source = "h1"
            break
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            title_source = "h2"
            break
        if not stripped.startswith("!["):
            title = stripped[:100]
            title_source = "first_line"
            break
    if title_line_idx is not None:
        lines.pop(title_line_idx)

    cover_image_path = None
    cover_line_idx = None
    img_pattern = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        match = img_pattern.match(stripped)
        if match:
            cover_image_path = resolve_image_path(match.group(2), base_path)
            cover_line_idx = idx
            break
        break
    if cover_line_idx is not None:
        lines.pop(cover_line_idx)
    return title, "\n".join(lines), title_source, cover_image_path


def markdown_to_html(markdown: str) -> str:
    html = markdown
    html = re.sub(
        r"___CODE_BLOCK_START___(.*?)___CODE_BLOCK_END___",
        lambda match: (
            "<blockquote>"
            + "<br>".join(line for line in match.group(1).strip().splitlines() if line.strip())
            + "</blockquote>"
        ),
        html,
        flags=re.DOTALL,
    )
    html = re.sub(
        r"^>\s?(.*)$",
        lambda match: f"<blockquote>{match.group(1).strip()}</blockquote>",
        html,
        flags=re.MULTILINE,
    )
    html = re.sub(r"___IMG_PLACEHOLDER_(\d+)___", r"<p>@@@IMG_\1@@@</p>", html)
    html = re.sub(r"^# (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h4>\1</h4>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", html)
    html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
    html = re.sub(r"^\s*[-*] (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
    html = re.sub(r"((?:<li>.*?</li>\n?)+)", r"<ul>\1</ul>", html)
    parts = html.split("\n\n")
    processed = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith(("<h2>", "<h3>", "<h4>", "<blockquote>", "<ul>", "<li>", "<p>@@@IMG_")):
            processed.append(part)
        else:
            processed.append(f"<p>{part.replace(chr(10), '<br>')}</p>")
    return "".join(processed)


def parse_article(filepath: str) -> Dict[str, Any]:
    path = Path(filepath)
    content = path.read_text(encoding="utf-8")
    title, body, _, cover = extract_title_and_cover(content, path.parent)
    blocks = split_into_blocks(body)
    images: List[Dict[str, Any]] = []
    result_blocks = []
    img_idx = 0
    img_pattern = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
    for block in blocks:
        match = img_pattern.match(block.strip())
        if match:
            placeholder = f"IMG_PLACEHOLDER_{img_idx}"
            images.append({
                "path": resolve_image_path(match.group(2), path.parent),
                "index": img_idx,
                "placeholder_id": placeholder,
            })
            result_blocks.append(f"___{placeholder}___")
            img_idx += 1
        else:
            result_blocks.append(block)
    return {
        "title": title,
        "cover_image": cover,
        "content_images": images,
        "html": markdown_to_html("\n\n".join(result_blocks)),
    }


def copy_to_clipboard(content: str, is_html: bool = False, is_image: bool = False) -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import io

        from AppKit import NSPasteboard, NSPasteboardTypeHTML, NSPasteboardTypeString, NSPasteboardTypeTIFF
        from Foundation import NSData
        from PIL import Image

        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        if is_image:
            with open(content, "rb") as handle:
                img = Image.open(io.BytesIO(handle.read()))
                if img.mode in ("RGBA", "LA", "P"):
                    rgba = img.convert("RGBA")
                    white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                    img = Image.alpha_composite(white_bg, rgba).convert("RGB")
                else:
                    img = img.convert("RGB")
                tiff_buffer = io.BytesIO()
                img.save(tiff_buffer, format="TIFF")
                ns_data = NSData.dataWithBytes_length_(tiff_buffer.getvalue(), len(tiff_buffer.getvalue()))
                pasteboard.setData_forType_(ns_data, NSPasteboardTypeTIFF)
        elif is_html:
            html_data = content.encode("utf-8")
            ns_data = NSData.dataWithBytes_length_(html_data, len(html_data))
            pasteboard.setData_forType_(ns_data, NSPasteboardTypeHTML)
            pasteboard.setString_forType_(content, NSPasteboardTypeString)
        else:
            pasteboard.setString_forType_(content, NSPasteboardTypeString)
        return True
    except Exception as exc:
        logger.warning("Clipboard error: %s", exc)
        return False
