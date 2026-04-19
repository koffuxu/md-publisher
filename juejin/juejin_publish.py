#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import io
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import browser_cookie3
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.article_utils import copy_to_clipboard, resolve_image_path  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("juejin_publish")

JUEJIN_EDITOR_URL = "https://juejin.cn/editor/drafts/new?v=2"


def extract_title_and_body(markdown_path: str) -> Tuple[str, str, List[Dict[str, Any]]]:
    content = Path(markdown_path).read_text(encoding="utf-8")
    article_path = Path(markdown_path).resolve()
    lines = content.splitlines()

    title = "Untitled"
    title_index: Optional[int] = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            title = stripped[2:].strip()[:100]
            title_index = idx
            break
        if not stripped.startswith("!["):
            title = stripped[:100]
            title_index = idx
            break

    if title_index is not None:
        body_lines = lines[:title_index] + lines[title_index + 1:]
    else:
        body_lines = lines

    body = "\n".join(body_lines).strip()
    images: List[Dict[str, Any]] = []

    def repl(match: re.Match[str]) -> str:
        idx = len(images)
        raw_path = match.group(2).strip()
        marker = f"[[IMG_{idx}]]"
        images.append(
            {
                "index": idx,
                "path": resolve_image_path(raw_path, article_path.parent),
                "alt": match.group(1).strip(),
                "marker": marker,
            }
        )
        return marker

    body_with_markers = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, body)
    return title, body_with_markers, images


def _find_existing_cover(markdown_path: str) -> Optional[str]:
    article_path = Path(markdown_path).resolve()
    article_dir = article_path.parent
    stem = article_path.stem

    preferred = [
        article_dir / f"{stem}-cover-juejin.png",
        article_dir / "cover-juejin.png",
        article_dir / f"{stem}-cover-16_9-bigtext.png",
        article_dir / "cover-16_9-bigtext.png",
        article_dir / f"{stem}-cover-csdn.png",
        article_dir / "cover-csdn.png",
        article_dir / f"{stem}-cover-zhihu.png",
        article_dir / "cover-zhihu.png",
        article_dir / "cover.png",
    ]

    candidates: List[Path] = [p for p in preferred if p.exists()]
    for pattern in ("*cover*.png", "*cover*.jpg", "*cover*.jpeg"):
        for matched in sorted(article_dir.glob(pattern)):
            name = matched.name.lower()
            if any(token in name for token in ("debug", "preview")):
                continue
            candidates.append(matched)

    deduped: List[Path] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    if not deduped:
        return None

    for candidate in deduped:
        name = candidate.name.lower()
        if any(token in name for token in ("16_9", "16-9", "juejin", "zhihu", "csdn")):
            return str(candidate.resolve())

    for candidate in deduped:
        try:
            with Image.open(candidate) as img:
                w, h = img.size
            if h > 0 and abs((w / h) - (16 / 9)) <= 0.08:
                return str(candidate.resolve())
        except Exception:
            continue

    for candidate in deduped:
        name = candidate.name.lower()
        if "default" in name:
            continue
        return str(candidate.resolve())
    return str(deduped[0].resolve())


def resolve_cover(markdown_path: str, explicit_cover: Optional[str]) -> Optional[str]:
    if explicit_cover:
        candidate = Path(explicit_cover).expanduser().resolve()
        if candidate.exists():
            return str(candidate)
        logger.warning("Explicit cover does not exist: %s", explicit_cover)
    return _find_existing_cover(markdown_path)


def load_juejin_cookies() -> List[Dict[str, Any]]:
    cookies: List[Dict[str, Any]] = []
    for cookie in browser_cookie3.chrome():
        domain = cookie.domain or ""
        if "juejin.cn" not in domain:
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": domain if domain.startswith(".") else "." + domain,
                "path": cookie.path or "/",
                "secure": bool(cookie.secure),
                "httpOnly": False,
                "sameSite": "Lax",
            }
        )
    if not cookies:
        raise RuntimeError("No juejin.cn cookies found in local Chrome.")
    return cookies


def _fill_title(page: Any, title: str) -> None:
    candidates = [
        page.get_by_role("textbox", name="输入文章标题"),
        page.get_by_placeholder("输入文章标题"),
        page.get_by_placeholder("请输入标题"),
        page.get_by_role("textbox", name=re.compile("标题|文章标题")),
        page.locator('input[placeholder*="标题"]'),
        page.locator('textarea[placeholder*="标题"]'),
    ]
    for locator in candidates:
        if locator.count():
            locator.first.click()
            locator.first.fill(title)
            return
    raise RuntimeError("Juejin title input not found.")


def _fill_markdown_with_editor_api(page: Any, markdown_text: str) -> bool:
    result = page.evaluate(
        """(markdown) => {
            try {
                if (window.monaco && window.monaco.editor && window.monaco.editor.getModels) {
                    const models = window.monaco.editor.getModels();
                    if (models && models.length > 0) {
                        models[0].setValue(markdown);
                        return { ok: true, via: 'monaco' };
                    }
                }
            } catch (_) {}

            try {
                const cms = Array.from(document.querySelectorAll('.CodeMirror'));
                for (const el of cms) {
                    if (el && el.CodeMirror && el.CodeMirror.setValue) {
                        el.CodeMirror.setValue(markdown);
                        return { ok: true, via: 'codemirror' };
                    }
                }
            } catch (_) {}

            return { ok: false, via: '' };
        }""",
        markdown_text,
    )
    if result.get("ok"):
        logger.info("Markdown body filled via %s API", result.get("via"))
        return True
    return False


def _locate_editor_target(page: Any) -> Any:
    selector = page.evaluate(
        """() => {
            const candidates = [];
            const push = (el, score) => {
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return;
                if (rect.width < 200 || rect.height < 60) return;
                candidates.push({ el, score: score + rect.width * rect.height });
            };

            document.querySelectorAll('textarea').forEach((el) => push(el, 2000000));
            document.querySelectorAll('.monaco-editor').forEach((el) => push(el, 1500000));
            document.querySelectorAll('.CodeMirror').forEach((el) => push(el, 1400000));
            document.querySelectorAll('[contenteditable="true"]').forEach((el) => push(el, 800000));

            if (!candidates.length) return null;
            candidates.sort((a, b) => b.score - a.score);
            const top = candidates[0].el;
            if (!top.id) top.id = 'juejin-editor-target';
            return '#' + top.id;
        }"""
    )
    if not selector:
        raise RuntimeError("Juejin markdown editor area not found.")
    return page.locator(selector)


def _fill_markdown_by_paste(page: Any, markdown_text: str) -> None:
    codemirror_scroll = page.locator(".CodeMirror-scroll")
    if codemirror_scroll.count():
        codemirror_scroll.first.click()
    else:
        target = _locate_editor_target(page)
        target.first.click()
    page.wait_for_timeout(200)

    if copy_to_clipboard(markdown_text):
        page.keyboard.press("Meta+a")
        page.keyboard.press("Backspace")
        page.keyboard.press("Meta+v")
        page.wait_for_timeout(1200)
        return

    page.keyboard.press("Meta+a")
    page.keyboard.press("Backspace")
    page.keyboard.type(markdown_text, delay=1)
    page.wait_for_timeout(1200)


def _read_markdown_from_editor(page: Any) -> str:
    return str(
        page.evaluate(
            """() => {
                try {
                    if (window.monaco && window.monaco.editor && window.monaco.editor.getModels) {
                        const models = window.monaco.editor.getModels();
                        if (models && models.length > 0) return models[0].getValue() || '';
                    }
                } catch (_) {}

                try {
                    const cm = document.querySelector('.CodeMirror');
                    if (cm && cm.CodeMirror && cm.CodeMirror.getValue) {
                        return cm.CodeMirror.getValue() || '';
                    }
                } catch (_) {}

                const textareas = Array.from(document.querySelectorAll('textarea'));
                for (const ta of textareas) {
                    if (ta.value && ta.value.trim()) return ta.value;
                }
                return '';
            }"""
        )
    )


def _set_marker_selection(page: Any, marker: str) -> bool:
    return bool(
        page.evaluate(
            """(marker) => {
                try {
                    if (window.monaco && window.monaco.editor && window.monaco.editor.getEditors) {
                        const editors = window.monaco.editor.getEditors();
                        for (const ed of editors) {
                            const model = ed && ed.getModel ? ed.getModel() : null;
                            if (!model || !model.getValue) continue;
                            const value = model.getValue() || '';
                            const idx = value.indexOf(marker);
                            if (idx < 0) continue;
                            const start = model.getPositionAt(idx);
                            const end = model.getPositionAt(idx + marker.length);
                            if (!start || !end) continue;
                            ed.focus();
                            ed.setSelection({
                                startLineNumber: start.lineNumber,
                                startColumn: start.column,
                                endLineNumber: end.lineNumber,
                                endColumn: end.column,
                            });
                            ed.revealLineInCenter(start.lineNumber);
                            return true;
                        }
                    }
                } catch (_) {}

                try {
                    const cms = Array.from(document.querySelectorAll('.CodeMirror'));
                    for (const el of cms) {
                        const cm = el && el.CodeMirror ? el.CodeMirror : null;
                        if (!cm || !cm.getValue) continue;
                        const value = cm.getValue() || '';
                        const idx = value.indexOf(marker);
                        if (idx < 0) continue;
                        const from = cm.posFromIndex(idx);
                        const to = cm.posFromIndex(idx + marker.length);
                        cm.focus();
                        cm.setSelection(from, to);
                        return true;
                    }
                } catch (_) {}
                return false;
            }""",
            marker,
        )
    )


def _replace_marker_text(page: Any, marker: str, new_text: str) -> bool:
    return bool(
        page.evaluate(
            """({ marker, newText }) => {
                try {
                    if (window.monaco && window.monaco.editor && window.monaco.editor.getEditors) {
                        const editors = window.monaco.editor.getEditors();
                        for (const ed of editors) {
                            const model = ed && ed.getModel ? ed.getModel() : null;
                            if (!model || !model.getValue) continue;
                            const value = model.getValue() || '';
                            const idx = value.indexOf(marker);
                            if (idx < 0) continue;
                            const start = model.getPositionAt(idx);
                            const end = model.getPositionAt(idx + marker.length);
                            if (!start || !end) continue;
                            const range = {
                                startLineNumber: start.lineNumber,
                                startColumn: start.column,
                                endLineNumber: end.lineNumber,
                                endColumn: end.column,
                            };
                            if (ed.executeEdits) {
                                ed.executeEdits('juejin-inline-image-fallback', [{ range, text: newText }]);
                                return true;
                            }
                            if (model.pushEditOperations) {
                                model.pushEditOperations([], [{ range, text: newText }], () => null);
                                return true;
                            }
                        }
                    }
                } catch (_) {}

                try {
                    const cms = Array.from(document.querySelectorAll('.CodeMirror'));
                    for (const el of cms) {
                        const cm = el && el.CodeMirror ? el.CodeMirror : null;
                        if (!cm || !cm.getValue) continue;
                        const value = cm.getValue() || '';
                        const idx = value.indexOf(marker);
                        if (idx < 0) continue;
                        const from = cm.posFromIndex(idx);
                        const to = cm.posFromIndex(idx + marker.length);
                        cm.replaceRange(newText, from, to);
                        return true;
                    }
                } catch (_) {}
                return false;
            }""",
            {"marker": marker, "newText": new_text},
        )
    )


def _markdown_image_count(text: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text))


def _markdown_data_uri_for_image(image_path: Path, alt: str) -> str:
    suffix = image_path.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    mime = mime_map.get(suffix, "application/octet-stream")
    payload = base64.b64encode(image_path.read_bytes()).decode("ascii")
    safe_alt = alt.replace("]", "").strip() or "image"
    return f"![{safe_alt}](data:{mime};base64,{payload})"


def _wait_editor_text_stable(page: Any, min_stable_rounds: int = 3, max_rounds: int = 24, interval_ms: int = 500) -> bool:
    stable_rounds = 0
    last_text: Optional[str] = None
    for _ in range(max_rounds):
        page.wait_for_timeout(interval_ms)
        current = _read_markdown_from_editor(page)
        if current == last_text and current.strip():
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_text = current
        if stable_rounds >= min_stable_rounds:
            return True
    return False


def _copy_image_to_clipboard_for_juejin(image_path: str) -> bool:
    if sys.platform != "darwin":
        return copy_to_clipboard(image_path, is_image=True)
    try:
        from AppKit import NSPasteboard, NSPasteboardTypePNG, NSPasteboardTypeTIFF
        from Foundation import NSData

        img = Image.open(image_path)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            white_bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            img = Image.alpha_composite(white_bg, rgba).convert("RGB")
        else:
            img = img.convert("RGB")

        png_buf = io.BytesIO()
        tiff_buf = io.BytesIO()
        img.save(png_buf, format="PNG")
        img.save(tiff_buf, format="TIFF")

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        png_data = NSData.dataWithBytes_length_(png_buf.getvalue(), len(png_buf.getvalue()))
        tiff_data = NSData.dataWithBytes_length_(tiff_buf.getvalue(), len(tiff_buf.getvalue()))
        pb.setData_forType_(png_data, NSPasteboardTypePNG)
        pb.setData_forType_(tiff_data, NSPasteboardTypeTIFF)
        return True
    except Exception:
        return copy_to_clipboard(image_path, is_image=True)


def _replace_inline_images(page: Any, images: List[Dict[str, Any]]) -> None:
    if not images:
        return
    logger.info("Replacing %s inline images", len(images))

    for image in images:
        marker = str(image.get("marker") or f"[[IMG_{image['index']}]]")
        img_path = Path(str(image["path"])).resolve()
        if not img_path.exists():
            raise RuntimeError(f"Inline image not found: {img_path}")

        before_text = _read_markdown_from_editor(page)
        before_count = _markdown_image_count(before_text)
        if marker not in before_text:
            raise RuntimeError(f"Inline image marker missing before replacement: {marker}")

        if not _set_marker_selection(page, marker):
            raise RuntimeError(f"Failed to select inline image marker: {marker}")
        if not _copy_image_to_clipboard_for_juejin(str(img_path)):
            raise RuntimeError(f"Failed to copy image to clipboard: {img_path}")

        selected_text = str(
            page.evaluate(
                """() => {
                    try {
                        const sel = window.getSelection();
                        if (sel && sel.rangeCount) {
                            return (sel.toString && sel.toString()) || '';
                        }
                    } catch (_) {}
                    try {
                        if (window.monaco && window.monaco.editor && window.monaco.editor.getEditors) {
                            const editors = window.monaco.editor.getEditors();
                            for (const ed of editors) {
                                const s = ed && ed.getSelection ? ed.getSelection() : null;
                                const model = ed && ed.getModel ? ed.getModel() : null;
                                if (!s || !model || !model.getValueInRange) continue;
                                const t = model.getValueInRange(s) || '';
                                if (t) return t;
                            }
                        }
                    } catch (_) {}
                    return '';
                }"""
            )
        )
        if selected_text.strip() != marker:
            logger.warning("Selection mismatch before paste for %s; attempting paste anyway.", marker)

        page.keyboard.press("Meta+v")

        replaced = False
        for _ in range(45):
            page.wait_for_timeout(1000)
            current_text = _read_markdown_from_editor(page)
            if marker not in current_text:
                if current_text != before_text:
                    _wait_editor_text_stable(page)
                    replaced = True
                    break
                continue
            current_count = _markdown_image_count(current_text)
            if current_count > before_count or "http" in current_text:
                if marker in current_text:
                    _replace_marker_text(page, marker, "")
                _wait_editor_text_stable(page)
                replaced = True
                break
        if not replaced:
            logger.warning("Inline upload timed out for %s, using data URI fallback.", marker)
            current_text = _read_markdown_from_editor(page)
            if marker in current_text:
                data_uri_markdown = _markdown_data_uri_for_image(img_path, str(image.get("alt", "")))
                if not _replace_marker_text(page, marker, data_uri_markdown):
                    raise RuntimeError(f"Inline image replacement timed out: {marker}")
            elif current_text != before_text:
                logger.info("Inline marker %s disappeared after paste; treating as replaced.", marker)
            else:
                raise RuntimeError(f"Inline image replacement timed out: {marker}")

    remaining = _read_markdown_from_editor(page)
    unresolved = re.findall(r"\[\[IMG_\d+\]\]", remaining)
    if unresolved:
        logger.warning("Inline image markers remain (%s); applying final data-URI fallback.", len(unresolved))
        patched = remaining
        for image in images:
            marker = str(image.get("marker") or f"[[IMG_{image['index']}]]")
            if marker not in patched:
                continue
            img_path = Path(str(image["path"])).resolve()
            if not img_path.exists():
                continue
            data_uri_markdown = _markdown_data_uri_for_image(img_path, str(image.get("alt", "")))
            patched = patched.replace(marker, data_uri_markdown)

        if patched != remaining:
            if not _fill_markdown_with_editor_api(page, patched):
                _fill_markdown_by_paste(page, patched)
            page.wait_for_timeout(600)
            remaining = _read_markdown_from_editor(page)
            unresolved = re.findall(r"\[\[IMG_\d+\]\]", remaining)

    if unresolved:
        raise RuntimeError(f"Inline image markers remain before draft save: {len(unresolved)}")


def _fill_markdown(page: Any, markdown_text: str) -> None:
    if not _fill_markdown_with_editor_api(page, markdown_text):
        _fill_markdown_by_paste(page, markdown_text)

    expected_line = ""
    for line in markdown_text.splitlines():
        line = line.strip()
        if line:
            expected_line = line[:30]
            break

    editor_value = _read_markdown_from_editor(page)
    if len(editor_value.strip()) < max(10, min(80, len(markdown_text.strip()) // 20)):
        raise RuntimeError("Juejin markdown editor content seems empty after fill.")
    if expected_line and expected_line not in editor_value:
        logger.warning("Editor content check did not find expected line; continuing.")


def _upload_cover(page: Any, cover_path: str) -> bool:
    inputs = page.locator('input[type="file"]')
    uploaded = False
    if inputs.count():
        for idx in range(min(inputs.count(), 8)):
            try:
                inputs.nth(idx).set_input_files(cover_path)
                uploaded = True
                break
            except Exception:
                continue

    if not uploaded:
        trigger_candidates = [
            page.get_by_role("button", name=re.compile("add_cover\\s*上传封面")),
            page.get_by_role("button", name=re.compile("封面|设置封面|上传封面")),
            page.locator('text=添加封面'),
            page.locator('text=设置封面'),
            page.locator('button:has-text("封面")'),
        ]
        for trigger in trigger_candidates:
            if not trigger.count():
                continue
            try:
                with page.expect_file_chooser(timeout=2500) as chooser_info:
                    trigger.first.click(timeout=2000)
                chooser = chooser_info.value
                chooser.set_files(cover_path)
                uploaded = True
                break
            except Exception:
                continue

    if not uploaded:
        logger.info("Cover upload input/chooser not found; skipping cover")
        return False

    page.wait_for_timeout(2000)
    confirm_buttons = [
        page.get_by_role("button", name=re.compile("确定|确认|完成|保存|应用")),
        page.locator('button:has-text("确定")'),
        page.locator('button:has-text("确认")'),
    ]
    for button in confirm_buttons:
        if button.count():
            try:
                button.first.click(timeout=1000)
                page.wait_for_timeout(600)
                break
            except Exception:
                continue
    return True


def _open_publish_panel(page: Any) -> bool:
    publish_btn = page.get_by_role("button", name="发布")
    if publish_btn.count():
        publish_btn.first.click()
        page.wait_for_timeout(600)
        return True
    return False


def _close_publish_panel(page: Any) -> None:
    close_candidates = [
        page.locator("i").nth(3),
        page.get_by_role("button", name=re.compile("关闭|取消")),
        page.locator('[aria-label*="close"], [aria-label*="关闭"]'),
    ]
    for candidate in close_candidates:
        try:
            if candidate.count():
                candidate.first.click(timeout=800)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)


def _save_draft(page: Any) -> None:
    save_candidates = [
        page.get_by_role("button", name=re.compile("保存草稿|存草稿|草稿")),
        page.locator('button:has-text("保存草稿")'),
        page.locator('button:has-text("存草稿")'),
        page.locator('text=保存草稿'),
    ]
    for button in save_candidates:
        if button.count():
            button.first.click()
            page.wait_for_timeout(1200)
            return

    page.keyboard.press("Meta+s")
    page.wait_for_timeout(1200)
    signal = page.locator("text=草稿").first
    if signal.count():
        return
    raise RuntimeError("Juejin save draft action not found.")


def _publish_now(page: Any) -> None:
    confirm = page.locator('div:has-text("发布文章") button:has-text("确定并发布")').first
    if not confirm.count():
        confirm = page.get_by_role("button", name="确定并发布").first
    if not confirm.count():
        raise RuntimeError('Publish confirm button "确定并发布" not found.')

    try:
        if not confirm.is_enabled():
            raise RuntimeError('Publish confirm button is disabled. Required fields may be missing.')
    except Exception:
        pass

    confirm.click(timeout=3000, force=True)
    page.wait_for_timeout(1200)

    success_tokens = ("发布成功", "文章已发布", "发布完成")
    for _ in range(120):
        page.wait_for_timeout(1000)
        url = page.url
        body = page.locator("body").inner_text()[:1200]
        if any(token in body for token in success_tokens):
            return
        if re.search(r"juejin\.cn/(post|p)/", url):
            return
        panel_visible = page.locator("text=发布文章").count() > 0
        if "/editor/drafts/new" not in url and "/editor/" not in url:
            return
        if not panel_visible:
            return

    raise RuntimeError("Juejin publish action did not finish within expected time.")


def _first_publish_tag_from_title(title: str) -> str:
    zh = re.findall(r"[\u4e00-\u9fff]{2,6}", title)
    if zh:
        return zh[0]
    en = re.findall(r"[A-Za-z][A-Za-z0-9+-]{1,}", title)
    if en:
        return en[0]
    return "技术"


def _extract_publish_tags(title: str, body_markdown: str, limit: int = 3) -> List[str]:
    tokens: List[str] = []
    tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9+-]{1,}", title))
    tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", title))
    tokens.extend(re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", body_markdown[:1200]))
    tokens.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", body_markdown[:1200]))

    blocked = {"the", "and", "with", "this", "that", "image", "markdown"}
    normalized: List[str] = []
    for token in tokens:
        t = token.strip().strip("+-_")
        if not t:
            continue
        if t.lower() in blocked:
            continue
        if t not in normalized:
            normalized.append(t)
        if len(normalized) >= limit:
            break
    if not normalized:
        normalized = [_first_publish_tag_from_title(title)]
    return normalized[:limit]


def _configure_publish_panel(page: Any, title: str, body_markdown: str) -> None:
    category_candidates = [
        page.get_by_role("button", name="人工智能"),
        page.get_by_text("人工智能"),
    ]
    for category in category_candidates:
        if category.count():
            try:
                category.first.click(timeout=1500)
                page.wait_for_timeout(300)
                break
            except Exception:
                continue

    tags = _extract_publish_tags(title, body_markdown)

    def _fill_tag(tag: str) -> bool:
        try:
            tag_field = page.locator('div:has-text("添加标签")').locator('input[placeholder*="请搜索添加标签"], input[placeholder*="搜索添加标签"]').first
            if not tag_field.count():
                tag_field = page.get_by_placeholder("请搜索添加标签").first
            if not tag_field.count():
                boxes = page.get_by_role("textbox")
                if boxes.count() >= 2:
                    tag_field = boxes.nth(1)
            if not tag_field.count():
                return False

            tag_field.click(timeout=1200)
            tag_field.fill(tag)
            page.wait_for_timeout(350)
            tag_field.press("Enter")
            page.wait_for_timeout(350)
            pick = page.get_by_role("button", name=tag)
            if pick.count():
                pick.first.click(timeout=800)
                page.wait_for_timeout(250)
            return True
        except Exception:
            return False

    for tag in tags[:3]:
        _fill_tag(tag)

    tag_ok = bool(
        page.evaluate(
            """() => {
                const panel = Array.from(document.querySelectorAll('div')).find(el => (el.textContent || '').includes('发布文章'));
                if (!panel) return false;
                const txt = panel.innerText || '';
                if (/请搜索添加标签/.test(txt)) {
                    const chips = panel.querySelectorAll('button,span,div');
                    for (const c of chips) {
                        const t = (c.textContent || '').trim();
                        if (!t) continue;
                        if (t === '人工智能') continue;
                        if (/AI|编程|开发|Code|测试|流程/.test(t)) return true;
                    }
                    return false;
                }
                return /AI|编程|开发|Code|测试|流程/.test(txt);
            }"""
        )
    )
    if not tag_ok:
        raise RuntimeError("Publish tags not added successfully.")


def _detect_blocked_state(page: Any, headless: bool) -> None:
    body_text = page.locator("body").inner_text()[:1000]
    url = page.url

    login_required = any(key in url for key in ("/login", "passport")) or "登录掘金" in body_text
    if login_required:
        raise RuntimeError("Juejin login required. Please login in Chrome first, then rerun.")

    risk_keywords = ("安全验证", "异常", "请先验证")
    need_verify = any(token in body_text for token in risk_keywords)
    if not need_verify:
        return

    if headless:
        raise RuntimeError("Juejin verification detected. Please run without --headless and complete verification.")

    logger.warning("Juejin verification detected; waiting up to 180s for manual completion.")
    for _ in range(36):
        page.wait_for_timeout(5000)
        body_text = page.locator("body").inner_text()[:1000]
        if not any(token in body_text for token in risk_keywords):
            logger.info("Verification appears completed; continuing.")
            return
    raise RuntimeError("Juejin verification not completed within 180s.")


def publish_to_juejin(
    markdown_path: str,
    cover_path: Optional[str] = None,
    headless: bool = False,
) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    title, body_markdown, inline_images = extract_title_and_body(markdown_path)
    final_cover = resolve_cover(markdown_path, explicit_cover=cover_path)
    cookies = load_juejin_cookies()

    logger.info("Preparing Juejin publish for %s", title)
    logger.info("Using cover: %s", final_cover or "<none>")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            page.goto(JUEJIN_EDITOR_URL, wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(4500)
            _detect_blocked_state(page, headless=headless)

            logger.info("Filling title")
            _fill_title(page, title)
            page.wait_for_timeout(300)

            logger.info("Filling markdown body")
            _fill_markdown(page, body_markdown)
            page.wait_for_timeout(800)
            _replace_inline_images(page, inline_images)
            page.wait_for_timeout(800)

            if not _open_publish_panel(page):
                raise RuntimeError("Publish panel not found.")

            if final_cover and Path(final_cover).exists():
                logger.info("Uploading cover")
                _upload_cover(page, str(Path(final_cover).resolve()))

            _configure_publish_panel(page, title, body_markdown)
            logger.info("Publishing article")
            _publish_now(page)

            return {
                "message": "Juejin article published successfully.",
                "title": title,
                "cover_path": final_cover or "",
            }
        except Exception:
            screenshot_path = str(Path(markdown_path).with_suffix(".juejin-debug.png"))
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                logger.error("Saved debug screenshot to %s", screenshot_path)
            except Exception:
                logger.exception("Failed to save debug screenshot")
            raise
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Markdown to Juejin")
    parser.add_argument("article", help="Markdown article path")
    parser.add_argument("--cover", default=None, help="Optional cover image path")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    args = parser.parse_args()

    result = publish_to_juejin(args.article, cover_path=args.cover, headless=args.headless)
    print(result["message"])
    if result.get("cover_path"):
        print(f"cover: {result['cover_path']}")


if __name__ == "__main__":
    main()
