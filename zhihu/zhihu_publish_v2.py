#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


OPENCLI_DAEMON_PORT = int(os.environ.get("OPENCLI_DAEMON_PORT", "19825"))
OPENCLI_DAEMON_URL = f"http://127.0.0.1:{OPENCLI_DAEMON_PORT}"
WORKSPACE = "site:zhihu-publish-v2"


def _zhihu_markdown_to_html(content: str) -> str:
    """Convert Markdown to Zhihu-compatible HTML (no image uploading)."""
    content = re.sub(
        r"```(\w*)\n(.*?)```",
        r'<pre><code class="\1">\2</code></pre>',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(r"`([^`]+)`", r"<code>\1</code>", content)
    content = re.sub(r"^### (.+)$", r"<h3>\1</h3>", content, flags=re.MULTILINE)
    content = re.sub(r"^## (.+)$", r"<h2>\1</h2>", content, flags=re.MULTILINE)
    content = re.sub(r"^# (.+)$", r"<h1>\1</h1>", content, flags=re.MULTILINE)
    content = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", content)
    content = re.sub(r"\*(.+?)\*", r"<em>\1</em>", content)
    content = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', content)
    content = re.sub(r"^- (.+)$", r"<li>\1</li>", content, flags=re.MULTILINE)
    content = re.sub(r"^\d+\. (.+)$", r"<li>\1</li>", content, flags=re.MULTILINE)

    lines = content.split("\n")
    result = []
    in_list = False
    for line in lines:
        if "<li>" in line:
            if not in_list:
                result.append("<ul>")
                in_list = True
            result.append(line)
        else:
            if in_list:
                result.append("</ul>")
                in_list = False
            result.append(line)
    if in_list:
        result.append("</ul>")
    content = "\n".join(result)

    lines = content.split("\n")
    processed_lines = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("<h1", "<h2", "<h3", "<ul", "<ol", "<pre", "<img", "<li", "<p")):
            in_block = True
            processed_lines.append(line)
        elif stripped.endswith(("</h1>", "</h2>", "</h3>", "</ul>", "</ol>", "</pre>", "</li>", "</p>")):
            in_block = False
            processed_lines.append(line)
        elif stripped == "":
            processed_lines.append("")
        elif not in_block:
            processed_lines.append(f"<p>{stripped}</p>")
        else:
            processed_lines.append(line)
    return "\n".join(processed_lines)


@dataclass
class LocalImage:
    image_id: str
    alt: str
    abs_path: Path
    token: str


def extract_title(markdown: str) -> str:
    lines = markdown.splitlines()
    for line in lines:
        text = line.strip()
        if not text:
            continue
        if text.startswith("# "):
            return text[2:].strip() or "未命名文章"
        return re.sub(r"^#+\s*", "", text).strip() or "未命名文章"
    return "未命名文章"


def remove_first_h1(markdown: str) -> str:
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().startswith("# "):
            return "\n".join(lines[:idx] + lines[idx + 1:]).strip()
    return markdown


def find_existing_16_9_cover(article_path: Path) -> str | None:
    article_dir = article_path.parent
    stem = article_path.stem

    preferred = [
        article_dir / f"{stem}-cover-16_9-bigtext.png",
        article_dir / "cover-16_9-bigtext.png",
        article_dir / f"{stem}-cover-zhihu.png",
        article_dir / "cover-zhihu.png",
    ]
    for candidate in preferred:
        if candidate.exists():
            return str(candidate)

    patterns = [
        "*16_9*.png",
        "*16_9*.jpg",
        "*16_9*.jpeg",
        "*1920x1080*.png",
        "*1920x1080*.jpg",
        "*1920x1080*.jpeg",
    ]
    for pattern in patterns:
        for candidate in sorted(article_dir.glob(pattern)):
            if candidate.is_file():
                return str(candidate)
    return None


def resolve_cover(article_path: Path, explicit_cover: str | None) -> str | None:
    if explicit_cover:
        cover_path = Path(explicit_cover).expanduser().resolve()
        if cover_path.exists():
            return str(cover_path)
        print(f"⚠️ 指定封面不存在: {cover_path}，将尝试自动选择封面")

    existing_cover = find_existing_16_9_cover(article_path)
    if existing_cover:
        print(f"🖼️ 使用现有 16:9 封面: {existing_cover}")
        return existing_cover

    return None


def _sanitize_rel_path(raw: str) -> str:
    return raw.strip().replace("\\", "/")


def _is_local_image(path: str) -> bool:
    return not re.match(r"^(https?://|data:)", path, re.I)


def preprocess_markdown(article_path: Path, markdown: str) -> tuple[str, list[LocalImage]]:
    article_dir = article_path.parent
    image_refs: list[LocalImage] = []

    def replace_image(match: re.Match[str]) -> str:
        alt = match.group(1) or ""
        raw_path = _sanitize_rel_path(match.group(2).strip().strip("<>"))
        if not _is_local_image(raw_path):
            return match.group(0)

        abs_path = (article_dir / raw_path).resolve()
        image_id = f"img_{len(image_refs)}"
        token = f"__ZHIHU_LOCAL_IMAGE_{image_id}__"
        image_refs.append(LocalImage(image_id=image_id, alt=alt, abs_path=abs_path, token=token))
        return f"![{alt}]({token})"

    replaced_markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image, markdown)
    body = remove_first_h1(replaced_markdown)
    html = _zhihu_markdown_to_html(body)

    for ref in image_refs:
        html = html.replace(
            f"![{ref.alt}]({ref.token})",
            f'<img src="{ref.token}" alt="{ref.alt}" data-local-src="{ref.abs_path.as_posix()}">',
        )
        html = html.replace(ref.token, ref.token)
    return html, image_refs


def image_to_inline_blob(path: Path) -> tuple[str, str]:
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return mime, b64


class OpenCLIDaemonClient:
    def __init__(self, daemon_url: str = OPENCLI_DAEMON_URL):
        self.daemon_url = daemon_url

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers["X-OpenCLI"] = "1"
        return requests.request(method, f"{self.daemon_url}{path}", headers=headers, timeout=30, **kwargs)

    def ensure_ready(self) -> None:
        try:
            status = self.status()
        except Exception:
            status = None

        if not status:
            print("🔌 opencli daemon 未运行，尝试自动拉起...")
            import subprocess
            subprocess.run(["opencli", "doctor"], check=False, capture_output=True, text=True)
            status = self.status()

        if not status.get("ok"):
            raise RuntimeError("opencli daemon 不可用")
        if not status.get("extensionConnected"):
            raise RuntimeError("opencli 扩展未连接，请先安装并启用 Browser Bridge 扩展")

    def status(self) -> dict[str, Any]:
        resp = self._request("GET", "/status")
        resp.raise_for_status()
        return resp.json()

    def send_command(self, action: str, **params: Any) -> dict[str, Any]:
        cmd_id = f"cmd_{uuid.uuid4().hex}"
        payload = {"id": cmd_id, "action": action, **params}
        resp = self._request("POST", "/command", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("error", "opencli daemon command failed"))
        return data


def build_browser_exec_script(payload: dict[str, Any]) -> str:
    return f"""
(() => {{
  const payload = {json.dumps(payload, ensure_ascii=False)};

  const getCookie = (name) => {{
    const kv = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith(name + '='));
    return kv ? decodeURIComponent(kv.slice(name.length + 1)) : '';
  }};
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const uploadImage = async (asset) => {{
    const b64 = asset.dataBase64 || '';
    if (!b64) throw new Error(`图片数据为空: ${{asset.imageId}}`);
    const bin = atob(b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) u8[i] = bin.charCodeAt(i);
    const blob = new Blob([u8], {{ type: asset.mime || 'image/png' }});
    const filename = asset.filename || `${{asset.imageId}}.png`;
    const file = new File([blob], filename, {{ type: blob.type || 'image/png' }});
    const fd = new FormData();
    fd.append('image', file, filename);

    const xsrf = getCookie('_xsrf') || getCookie('xsrf_token') || '';
    const uploadEndpoints = [
      'https://zhuanlan.zhihu.com/api/images',
      '/api/images',
      '/api/v4/images'
    ];
    let lastErr = '';
    for (const endpoint of uploadEndpoints) {{
      try {{
        const resp = await fetch(endpoint, {{
          method: 'POST',
          body: fd,
          credentials: 'include',
          headers: xsrf ? {{ 'x-xsrftoken': xsrf }} : {{}},
        }});
        if (!resp.ok) {{
          lastErr = `${{endpoint}}: HTTP ${{resp.status}}`;
          continue;
        }}
        const data = await resp.json();
        const url = data?.src || data?.url || data?.data?.url || '';
        if (url) return url;
      }} catch (err) {{
        lastErr = `${{endpoint}}: ${{String(err)}}`;
      }}
    }}
    throw new Error(`上传图片失败: ${{asset.imageId}} (${{lastErr}})`);
  }};

  return (async () => {{
    const result = {{
      uploaded: [],
      skipped: [],
      endpoint: '',
      publishResponse: null,
    }};

    let html = payload.html;
    for (const asset of payload.images || []) {{
      const escaped = asset.token.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
      try {{
        const remoteUrl = await uploadImage(asset);
        html = html.replace(new RegExp(escaped, 'g'), remoteUrl);
        result.uploaded.push({{ imageId: asset.imageId, remoteUrl }});
      }} catch (err) {{
        html = html.replace(new RegExp(escaped, 'g'), '');
        result.skipped.push({{ imageId: asset.imageId, error: String(err) }});
      }}
      await sleep(120);
    }}

    let coverUrl = '';
    if (payload.cover && payload.cover.localUrl) {{
      try {{
        coverUrl = await uploadImage(payload.cover);
      }} catch (_) {{
        coverUrl = '';
      }}
    }}

    const xsrf = getCookie('_xsrf') || getCookie('xsrf_token') || '';
    const postData = {{
      title: payload.title,
      content: html,
      draft: !!payload.draft,
      can_comment: true,
    }};
    if (coverUrl) postData.image_url = coverUrl;
    if (payload.columnId) postData.column = payload.columnId;

    const endpoints = [
      'https://zhuanlan.zhihu.com/api/articles',
      '/api/articles',
      '/api/v4/articles'
    ];
    let lastErr = '';
    const publishErrors = [];
    for (const endpoint of endpoints) {{
      try {{
        const resp = await fetch(endpoint, {{
          method: 'POST',
          credentials: 'include',
          headers: {{
            'Content-Type': 'application/json',
            ...(xsrf ? {{ 'x-xsrftoken': xsrf }} : {{}})
          }},
          body: JSON.stringify(postData),
        }});
        const text = await resp.text();
        let data = null;
        try {{ data = JSON.parse(text); }} catch (_) {{ data = {{ raw: text }}; }}
        if (!resp.ok) {{
          lastErr = `${{endpoint}}: HTTP ${{resp.status}}`;
          publishErrors.push(lastErr);
          continue;
        }}
        result.endpoint = endpoint;
        result.publishResponse = data;
        const articleId = data?.id || data?.data?.id || '';
        const articleUrl = articleId ? `https://zhuanlan.zhihu.com/p/${{articleId}}` : '';
        return {{
          ok: true,
          title: payload.title,
          draft: !!payload.draft,
          articleId,
          articleUrl,
          result,
        }};
      }} catch (err) {{
        lastErr = `${{endpoint}}: ${{String(err)}}`;
        publishErrors.push(lastErr);
      }}
    }}

    const setInputLikeValue = (el, value) => {{
      if (!el) return false;
      el.focus();
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {{
        el.value = value;
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
        return true;
      }}
      if (el.isContentEditable) {{
        el.innerHTML = value;
        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
        return true;
      }}
      return false;
    }};
    const isVisible = (el) => {{
      if (!el) return false;
      const s = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
    }};
    const pickTitle = () => {{
      const sels = ['textarea[placeholder*="标题"]', 'input[placeholder*="标题"]', '[contenteditable="true"][data-placeholder*="标题"]'];
      for (const sel of sels) {{
        const el = document.querySelector(sel);
        if (isVisible(el)) return el;
      }}
      return null;
    }};
    const pickEditor = () => {{
      const cands = Array.from(document.querySelectorAll('[contenteditable="true"], .ProseMirror'));
      const visible = cands.filter((el) => isVisible(el));
      visible.sort((a, b) => (b.getBoundingClientRect().width * b.getBoundingClientRect().height) - (a.getBoundingClientRect().width * a.getBoundingClientRect().height));
      return visible[0] || null;
    }};
    const clickByText = (texts) => {{
      const targets = Array.isArray(texts) ? texts : [texts];
      const nodes = Array.from(document.querySelectorAll('button, [role="button"], div, span, a'));
      const target = nodes.find((n) => isVisible(n) && targets.some((t) => (n.textContent || '').includes(t)));
      if (!target) return false;
      target.click();
      return true;
    }};

    const titleEl = pickTitle();
    const editorEl = pickEditor();
    const titleOk = setInputLikeValue(titleEl, payload.title);
    const editorOk = setInputLikeValue(editorEl, html);
    await sleep(800);
    const saved = clickByText(['保存草稿', '存为草稿', '草稿']);
    if (titleOk && editorOk) {{
      return {{
        ok: true,
        title: payload.title,
        draft: true,
        articleId: '',
        articleUrl: '',
        result: {{
          ...result,
          endpoint: 'dom_fallback',
          publishErrors,
          domSaved: saved,
        }},
      }};
    }}

    return {{
      ok: false,
      error: `发布失败: ${{lastErr || '未知错误'}}`,
      result: {{ ...result, publishErrors }},
    }};
  }})()
}})()
"""


def choose_payload_with_size_budget(payload: dict[str, Any], budget_bytes: int = 900_000) -> tuple[dict[str, Any], str]:
    candidates: list[tuple[str, dict[str, Any]]] = []

    full = dict(payload)
    candidates.append(("full", full))

    no_cover = dict(payload)
    no_cover["cover"] = None
    candidates.append(("no_cover", no_cover))

    text_only = dict(no_cover)
    text_only["images"] = []
    html = str(text_only.get("html", ""))
    html = re.sub(r'<img[^>]*src="__ZHIHU_LOCAL_IMAGE_[^"]+"[^>]*>\s*', "", html)
    text_only["html"] = html
    candidates.append(("text_only", text_only))

    for mode, candidate in candidates:
        code = build_browser_exec_script(candidate)
        if len(code.encode("utf-8")) <= budget_bytes:
            return candidate, mode

    raise RuntimeError("文章内容与图片体积过大，超过 opencli daemon 单次命令大小限制（约 1MB）")


def prepare_payload(
    article_path: Path,
    title: str,
    html: str,
    image_refs: list[LocalImage],
    cover_path: str | None,
    draft: bool,
    column_id: str | None,
) -> dict[str, Any]:
    images_payload = []
    for ref in image_refs:
        mime, b64 = image_to_inline_blob(ref.abs_path)
        images_payload.append(
            {
                "imageId": ref.image_id,
                "filename": ref.abs_path.name,
                "mime": mime,
                "dataBase64": b64,
                "token": ref.token,
            }
        )

    cover_payload = None
    if cover_path:
        cover_mime, cover_b64 = image_to_inline_blob(Path(cover_path))
        cover_payload = {
            "imageId": "cover",
            "filename": Path(cover_path).name,
            "mime": cover_mime,
            "dataBase64": cover_b64,
            "token": "__ZHIHU_LOCAL_COVER__",
        }

    payload = {
        "title": title,
        "html": html,
        "images": images_payload,
        "cover": cover_payload,
        "draft": draft,
        "columnId": column_id or "",
        "articlePath": str(article_path),
    }
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 opencli 浏览器桥接发布 Markdown 到知乎")
    parser.add_argument("article", help="Markdown 文件路径")
    parser.add_argument("--title", help="文章标题（默认从文件第一行提取）")
    parser.add_argument("--cover", help="封面图路径（默认自动选 16:9）")
    parser.add_argument("--column", help="专栏 ID")
    parser.add_argument("--draft", action="store_true", help="保存为草稿")
    parser.add_argument("--opencli-daemon-url", default=OPENCLI_DAEMON_URL, help="opencli daemon 地址")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    article_path = Path(args.article).expanduser().resolve()
    if not article_path.exists():
        print(f"❌ 文件不存在: {article_path}")
        return 1

    markdown = article_path.read_text(encoding="utf-8")
    title = args.title or extract_title(markdown)
    cover_path = resolve_cover(article_path, args.cover)
    html, image_refs = preprocess_markdown(article_path, markdown)

    payload = prepare_payload(
        article_path=article_path,
        title=title,
        html=html,
        image_refs=image_refs,
        cover_path=cover_path,
        draft=bool(args.draft),
        column_id=args.column,
    )

    try:
        client = OpenCLIDaemonClient(daemon_url=args.opencli_daemon_url)
        client.ensure_ready()

        nav = client.send_command(
            "navigate",
            workspace=WORKSPACE,
            url="https://zhuanlan.zhihu.com/write",
        )
        tab_id = ((nav.get("data") or {}).get("tabId")) if isinstance(nav, dict) else None

        effective_payload, mode = choose_payload_with_size_budget(payload, budget_bytes=900_000)
        if mode != "full":
            print(f"⚠️ 命令体积过大，已自动降级发布模式: {mode}")
        code = build_browser_exec_script(effective_payload)

        exec_result = client.send_command(
            "exec",
            workspace=WORKSPACE,
            tabId=tab_id,
            code=code,
        )
        data = exec_result.get("data") if isinstance(exec_result, dict) else None
        if not isinstance(data, dict):
            print("❌ 浏览器执行结果异常")
            return 1
        if not data.get("ok"):
            print(f"❌ 发布失败: {data.get('error', '未知错误')}")
            return 1

        print("✅ 发布成功")
        print(f"标题: {data.get('title')}")
        print(f"链接: {data.get('articleUrl') or '未返回文章链接'}")
        if data.get("draft"):
            print("状态: 草稿")
        result_meta = data.get("result") or {}
        uploaded = result_meta.get("uploaded") or []
        endpoint = result_meta.get("endpoint") or "unknown"
        if endpoint == "dom_fallback":
            print("发布通道: DOM fallback（页面草稿保存）")
        else:
            print(f"发布通道: {endpoint}")
        print(f"上传图片: {len(uploaded)} 张")
        return 0
    except Exception as err:
        print(f"❌ 发布异常: {err}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
