#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import browser_cookie3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.article_utils import copy_to_clipboard, parse_article  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("csdn_publish")

CSDN_COVER_SUFFIX = "-cover-csdn.png"


def _find_existing_cover(filepath: str) -> Optional[str]:
    article_path = Path(filepath).resolve()
    article_dir = article_path.parent
    stem = article_path.stem
    preferred = [
        article_dir / f"{stem}{CSDN_COVER_SUFFIX}",
        article_dir / "cover-csdn.png",
        article_dir / f"{stem}-cover-16_9-bigtext.png",
        article_dir / "cover-16_9-bigtext.png",
        article_dir / "cover.png",
    ]
    for candidate in preferred:
        if candidate.exists():
            return str(candidate)
    for pattern in ("*cover-csdn*.png", "*cover-16_9-bigtext*.png", "cover*.png", "cover*.jpg", "cover*.jpeg"):
        matches = sorted(article_dir.glob(pattern))
        for match in matches:
            name = match.name.lower()
            if any(token in name for token in ("wechat", "twitter", "xhs", "default")):
                continue
            return str(match)
    return None


def resolve_cover(filepath: str, explicit_cover: Optional[str] = None) -> Optional[str]:
    if explicit_cover:
        return explicit_cover
    return _find_existing_cover(filepath)


def load_csdn_cookies() -> List[Dict[str, Any]]:
    cookies: List[Dict[str, Any]] = []
    for cookie in browser_cookie3.chrome():
        domain = cookie.domain or ""
        if "csdn.net" not in domain:
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
        raise RuntimeError("No CSDN cookies found in local Chrome.")
    return cookies


def pick_tags(title: str) -> List[str]:
    tags: List[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+-]{1,}", title):
        cleaned = token.strip("+-_")
        if cleaned.lower() not in {"the", "and", "with"}:
            tags.append(cleaned)
    tags.extend(re.findall(r"[\u4e00-\u9fff]{2,6}", title))
    deduped: List[str] = []
    for tag in tags:
        if tag not in deduped:
            deduped.append(tag)
    return deduped[:3] or ["技术"]


def build_summary(markdown_path: str, title: str) -> str:
    raw = Path(markdown_path).read_text(encoding="utf-8")
    raw = re.sub(r"^# .*$", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", raw)
    raw = re.sub(r"`{3}.*?`{3}", "", raw, flags=re.DOTALL)
    raw = re.sub(r"[#>*`\\-]", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if raw.startswith(title):
        raw = raw[len(title):].strip()
    return raw[:120]


def _editor_frame(page: Any) -> Any:
    iframe = page.query_selector("#cke_1_contents iframe") or page.query_selector("iframe.cke_wysiwyg_frame")
    if not iframe:
        raise RuntimeError("CSDN CKEditor iframe not found.")
    frame = iframe.content_frame()
    if frame is None:
        raise RuntimeError("Unable to access CSDN CKEditor frame.")
    return frame


def _fill_title(page: Any, title: str) -> None:
    title_input = page.get_by_role("textbox", name="请输入文章标题（5～100个字）")
    if title_input.count():
        title_input.click()
        title_input.fill(title)
        return
    page.fill("#txtTitle", title)


def _upload_cover(page: Any, cover_path: str) -> None:
    upload_button = page.get_by_role("button", name="从本地上传")
    if upload_button.count():
        nested_input = upload_button.locator('input[type="file"]')
        if nested_input.count():
            nested_input.first.set_input_files(cover_path)
        else:
            page.locator('input[type="file"]').first.set_input_files(cover_path)
    else:
        page.set_input_files('input[type="file"][name="file"]', cover_path)
    page.wait_for_timeout(3000)
    _confirm_cover_dialog(page)
    page.wait_for_timeout(1000)


def _fill_tags(page: Any, tags: List[str]) -> None:
    tag_button = page.get_by_role("button", name="添加文章标签")
    if tag_button.count():
        logger.info("Opening tag dialog")
        tag_button.click()
        page.wait_for_timeout(800)
    elif page.locator('text=添加文章标签').count():
        logger.info("Opening tag dialog")
        page.locator('text=添加文章标签').first.click()
        page.wait_for_timeout(800)
    else:
        logger.warning("Tag dialog trigger not found; skipping tags.")
        return

    logger.info("Adding tags")
    for idx, tag in enumerate(tags):
        tag_input = page.get_by_role("textbox", name="文章标签*")
        if tag_input.count():
            try:
                tag_input.click()
                tag_input.fill(tag)
                tag_input.press("Enter")
                page.wait_for_timeout(400)
                continue
            except Exception:
                logger.warning("Tag textbox interaction failed for %s, trying fallback.", tag)

        visible_input = page.locator('input[placeholder*="请输入文字搜索"]:visible')
        if not visible_input.count():
            logger.warning("Tag input is no longer visible after adding %s tag(s); stopping tag fill.", idx)
            break
        visible_input.first.fill(tag)
        visible_input.first.press("Enter")
        page.wait_for_timeout(400)

    close_button = page.get_by_role("button", name="关闭")
    if close_button.count():
        close_button.click()
        page.wait_for_timeout(400)


def _fill_summary(page: Any, summary: str) -> None:
    summary_input = page.get_by_role("textbox", name="文章摘要")
    if summary_input.count():
        summary_input.click()
        summary_input.fill(summary[:256])
        return

    legacy_summary_input = page.locator("#txtSammary")
    if legacy_summary_input.count():
        try:
            if legacy_summary_input.first.is_visible():
                legacy_summary_input.first.fill(summary[:256])
            else:
                page.evaluate(
                    """(value) => {
                        const el = document.querySelector('#txtSammary');
                        if (!el) return;
                        el.value = value;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }""",
                    summary[:256],
                )
        except Exception:
            logger.warning("Skipping summary fill because CSDN summary field is not interactable.")


def _save_draft(page: Any) -> None:
    save_button = page.get_by_role("button", name="保存草稿")
    if save_button.count():
        save_button.click()
        return
    page.click('button:has-text("保存草稿")')


def _confirm_cover_dialog(page: Any) -> None:
    confirm_candidates = [
        'text=确认上传',
        'button:has-text("确定上传")',
        'button:has-text("确认上传")',
        'button:has-text("确定")',
        'button:has-text("确认")',
        'button:has-text("完成")',
        'button:has-text("应用")',
        'button:has-text("保存")',
        'button:has-text("裁剪")',
    ]
    clicked = False
    for selector in confirm_candidates:
        locator = page.locator(selector)
        count = locator.count()
        if count:
            logger.info("Confirming cover dialog with selector: %s", selector)
            try:
                target = locator.last
                target.click(timeout=5000, force=True)
            except Exception:
                target.evaluate("(el) => el.click()")
            clicked = True
            break
    if not clicked:
        return
    for selector in confirm_candidates:
        try:
            page.locator(selector).last.wait_for(state="hidden", timeout=15000)
            return
        except Exception:
            pass


def _editor_image_count(frame: Any) -> int:
    return int(frame.evaluate("() => document.querySelectorAll('img').length"))


def _editor_contains_marker(frame: Any, marker: str) -> bool:
    return bool(
        frame.evaluate(
            """(marker) => {
                const text = document.body ? (document.body.innerText || '') : '';
                return text.includes(marker);
            }""",
            marker,
        )
    )


def _insert_editor_image_via_toolbar(page: Any, image_path: str) -> bool:
    image_button = page.get_by_role("button", name="图像")
    if not image_button.count():
        return False
    image_button.click()
    page.wait_for_timeout(500)

    picker_button = page.get_by_role("button", name="选择图片")
    if not picker_button.count():
        return False

    nested_input = picker_button.locator('input[type="file"]')
    if nested_input.count():
        nested_input.first.set_input_files(image_path)
    else:
        file_inputs = page.locator('input[type="file"]')
        if not file_inputs.count():
            return False
        file_inputs.last.set_input_files(image_path)

    page.wait_for_timeout(1500)
    close_button = page.locator(".edit-title-close")
    if close_button.count():
        try:
            close_button.first.click(timeout=1000)
        except Exception:
            pass
    return True


def _select_marker(frame: Any, marker: str) -> bool:
    return bool(
        frame.evaluate(
            """(marker) => {
                const root = document.body;
                if (!root) return false;
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
                let node;
                while (node = walker.nextNode()) {
                    const idx = node.textContent.indexOf(marker);
                    if (idx !== -1) {
                        const range = document.createRange();
                        range.setStart(node, idx);
                        range.setEnd(node, idx + marker.length);
                        const sel = window.getSelection();
                        sel.removeAllRanges();
                        sel.addRange(range);
                        node.parentElement?.scrollIntoView({ block: 'center' });
                        return true;
                    }
                }
                return false;
            }""",
            marker,
        )
    )


def _cleanup_placeholders(frame: Any) -> None:
    for _ in range(25):
        cleanup_res = frame.evaluate(
            """() => {
                const root = document.body;
                if (!root) return { found: false, delWhole: false };
                const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
                let node;
                const pattern = /@@@IMG_\\d+@@@/;
                while (node = walker.nextNode()) {
                    const matched = node.textContent.match(pattern);
                    if (matched) {
                        const parentEl = node.parentElement;
                        const lineText = parentEl ? parentEl.innerText.trim() : node.textContent.trim();
                        const isOnlyPlaceholder = lineText === matched[0] || lineText.replace(/@@@IMG_\\d+@@@/g, '').trim() === '';
                        const range = document.createRange();
                        const sel = window.getSelection();
                        if (isOnlyPlaceholder && parentEl) {
                            range.selectNodeContents(parentEl);
                        } else {
                            range.setStart(node, node.textContent.indexOf(matched[0]));
                            range.setEnd(node, node.textContent.indexOf(matched[0]) + matched[0].length);
                        }
                        sel.removeAllRanges();
                        sel.addRange(range);
                        return { found: true, delWhole: isOnlyPlaceholder };
                    }
                }
                return { found: false, delWhole: false };
            }"""
        )
        if not cleanup_res["found"]:
            return
        frame.press("body", "Backspace")
        if cleanup_res.get("delWhole"):
            time.sleep(0.2)
            frame.press("body", "Backspace")
        time.sleep(0.3)


def _remove_marker_placeholder(frame: Any, marker: str) -> None:
    if not _select_marker(frame, marker):
        return
    frame.press("body", "Backspace")
    time.sleep(0.2)
    frame.press("body", "Backspace")
    time.sleep(0.2)


def build_preview_html(markdown_path: str, article: Dict[str, Any]) -> str:
    article_path = Path(markdown_path).resolve()
    body_html = article["html"]
    for img in article["content_images"]:
        img_path = Path(img["path"]).resolve()
        rel_path = img_path.relative_to(article_path.parent).as_posix()
        image_html = (
            f'<figure class="content-image">'
            f'<img src="{html.escape(rel_path, quote=True)}" alt="image-{img["index"] + 1}">'
            f"</figure>"
        )
        body_html = body_html.replace(f"@@@IMG_{img['index']}@@@", image_html)

    title = html.escape(article["title"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - CSDN Preview</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f6f7;
      --panel: #ffffff;
      --text: #222222;
      --muted: #666666;
      --line: #e6e8eb;
      --accent: #fc5531;
      --code: #f2f4f7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: linear-gradient(180deg, #f7f7f8 0%, #eef1f4 100%);
      color: var(--text);
      font: 16px/1.8 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    .page {{
      max-width: 860px;
      margin: 40px auto;
      padding: 0 20px 48px;
    }}
    .article {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 40px 44px;
      box-shadow: 0 18px 50px rgba(17, 24, 39, 0.06);
    }}
    .meta {{
      margin-bottom: 20px;
      color: var(--muted);
      font-size: 13px;
      letter-spacing: 0.02em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 0 0 28px;
      font-size: 34px;
      line-height: 1.25;
    }}
    h2, h3, h4 {{
      margin: 32px 0 14px;
      line-height: 1.35;
    }}
    h2 {{ font-size: 28px; }}
    h3 {{ font-size: 22px; }}
    h4 {{ font-size: 18px; }}
    p {{
      margin: 14px 0;
      word-break: break-word;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{ text-decoration: underline; }}
    ul {{
      margin: 16px 0;
      padding-left: 22px;
    }}
    li {{ margin: 8px 0; }}
    blockquote {{
      margin: 20px 0;
      padding: 14px 18px;
      border-left: 4px solid var(--accent);
      background: #fff7f5;
      color: #4b5563;
      border-radius: 10px;
    }}
    code {{
      padding: 0.1em 0.35em;
      border-radius: 6px;
      background: var(--code);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.92em;
    }}
    .content-image {{
      margin: 24px 0;
    }}
    .content-image img {{
      display: block;
      width: 100%;
      height: auto;
      border-radius: 14px;
      border: 1px solid var(--line);
    }}
    @media (max-width: 720px) {{
      .page {{ margin: 20px auto; padding: 0 12px 28px; }}
      .article {{ padding: 24px 18px; border-radius: 14px; }}
      h1 {{ font-size: 28px; }}
      h2 {{ font-size: 24px; }}
      h3 {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <article class="article">
      <div class="meta">CSDN Local Preview</div>
      <h1>{title}</h1>
      {body_html}
    </article>
  </main>
</body>
</html>
"""


def save_preview_html(markdown_path: str, article: Dict[str, Any]) -> str:
    article_path = Path(markdown_path).resolve()
    preview_path = article_path.with_name(f"{article_path.stem}.csdn.preview.html")
    preview_path.write_text(build_preview_html(markdown_path, article), encoding="utf-8")
    return str(preview_path)


def publish_to_csdn(
    markdown_path: str,
    cover_path: Optional[str] = None,
    headless: bool = False,
    preview: bool = False,
) -> Dict[str, str]:
    from playwright.sync_api import sync_playwright

    article = parse_article(markdown_path)
    final_cover = resolve_cover(markdown_path, explicit_cover=cover_path)
    cookies = load_csdn_cookies()
    title = article["title"]
    summary = build_summary(markdown_path, title)
    tags = pick_tags(title)
    logger.info("Preparing CSDN draft for %s", title)
    logger.info("Using cover: %s", final_cover or "<none>")
    logger.info("Using tags: %s", ", ".join(tags))
    preview_path = ""
    if preview:
        preview_path = save_preview_html(markdown_path, article)
        logger.info("Saved preview HTML to %s", preview_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            logger.info("Opening CSDN editor")
            page.goto("https://mp.csdn.net/mp_blog/creation/editor", wait_until="domcontentloaded", timeout=120000)
            page.wait_for_timeout(6000)

            logger.info("Filling title")
            _fill_title(page, title)
            page.wait_for_timeout(500)

            if final_cover and Path(final_cover).exists():
                logger.info("Uploading cover")
                _upload_cover(page, final_cover)

            _fill_tags(page, tags)

            if summary:
                logger.info("Filling summary")
                _fill_summary(page, summary)

            logger.info("Injecting article HTML")
            page.evaluate(
                """(html) => {
                    if (!window.CKEDITOR || !window.CKEDITOR.instances || !window.CKEDITOR.instances.editor) {
                        throw new Error('CKEditor instance not ready');
                    }
                    window.CKEDITOR.instances.editor.setData(html);
                }""",
                article["html"],
            )
            page.wait_for_timeout(3000)

            frame = _editor_frame(page)
            body_text_len = int(frame.evaluate("() => (document.body && document.body.innerText || '').trim().length"))
            if body_text_len < 20:
                raise RuntimeError(f"CSDN editor body looks empty after setData. textLen={body_text_len}")

            logger.info("Uploading %s inline images", len(article["content_images"]))
            for img in article["content_images"]:
                marker = f"@@@IMG_{img['index']}@@@"
                if not Path(img["path"]).exists():
                    logger.warning("Skipping missing image: %s", img["path"])
                    _remove_marker_placeholder(frame, marker)
                    continue
                if not _select_marker(frame, marker):
                    raise RuntimeError(f"Failed to select image marker: {marker}")
                logger.info("Uploading image for marker %s from %s", marker, img["path"])
                before = _editor_image_count(frame)
                if not _insert_editor_image_via_toolbar(page, img["path"]):
                    raise RuntimeError(f"Failed to trigger toolbar upload for marker {marker}")
                uploaded = False
                for _ in range(30):
                    time.sleep(1)
                    uploading_state = frame.evaluate(
                        """() => {
                            const text = document.body ? (document.body.innerText || '') : '';
                            return text.includes('正在上传') || text.includes('Uploading');
                        }"""
                    )
                    marker_still_present = _editor_contains_marker(frame, marker)
                    if not uploading_state and _editor_image_count(frame) > before and not marker_still_present:
                        uploaded = True
                        break
                if not uploaded:
                    raise RuntimeError(f"Failed to replace image marker in place: {marker}")

            final_stats = frame.evaluate(
                """() => {
                    const text = document.body ? (document.body.innerText || '') : '';
                    const placeholders = text.match(/@@@IMG_\\d+@@@/g) || [];
                    return { textLen: text.trim().length, placeholderCount: placeholders.length };
                }"""
            )
            if final_stats["placeholderCount"] > 0:
                raise RuntimeError(f"CSDN image placeholders remain before draft save: {final_stats['placeholderCount']}")

            logger.info("Saving draft")
            _save_draft(page)
            page.wait_for_timeout(5000)
            logger.info("Draft saved")
            return {
                "message": "CSDN draft saved successfully.",
                "title": title,
                "cover_path": final_cover or "",
                "preview_path": preview_path,
            }
        except Exception:
            screenshot_path = str(Path(markdown_path).with_suffix(".csdn-debug.png"))
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                logger.error("Saved debug screenshot to %s", screenshot_path)
            except Exception:
                logger.exception("Failed to save debug screenshot")
            raise
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish Markdown to CSDN draft")
    parser.add_argument("article", help="Markdown article path")
    parser.add_argument("--cover", default=None, help="Optional cover image path")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--preview", action="store_true", help="Save converted HTML preview next to the article")
    args = parser.parse_args()

    result = publish_to_csdn(args.article, cover_path=args.cover, headless=args.headless, preview=args.preview)
    print(result["message"])
    if result.get("cover_path"):
        print(f"cover: {result['cover_path']}")
    if result.get("preview_path"):
        print(f"preview: {result['preview_path']}")


if __name__ == "__main__":
    main()
