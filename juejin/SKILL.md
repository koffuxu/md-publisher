---
name: juejin-publish
description: 发布 Markdown 文章到掘金编辑器并正式发布。当用户说"发布到掘金"、"juejin publish"或要求把本地 Markdown 上传到 `https://juejin.cn/editor/drafts/new?v=2` 时触发。
---

# 掘金发布技能

将本地 Markdown 文章发布到掘金编辑器，正式发布（含分类、标签设置）。

## 使用方式

```bash
python3 juejin/juejin_publish.py "/path/to/article.md"
```

可选参数：

```bash
# 手动指定封面
python3 juejin/juejin_publish.py "/path/to/article.md" --cover "/path/to/cover.png"

# 无头模式（默认建议非无头）
python3 juejin/juejin_publish.py "/path/to/article.md" --headless
```

## 工作流

1. 读取 Markdown，提取标题和正文（正文保持 Markdown 原格式）
2. 复用本机 Chrome 的 `juejin.cn` Cookie 登录态
3. 打开 `https://juejin.cn/editor/drafts/new?v=2`
4. 填写标题
5. 将 Markdown 正文写入编辑器（优先 Monaco/CodeMirror API）
6. 尝试上传封面（若页面入口可见）
7. 打开发布面板，配置分类和标签后正式发布

## 依赖安装

```bash
pip install -r juejin/requirements.txt
playwright install chromium
```

## 认证方式

默认从本机 Chrome 提取 `juejin.cn` Cookie。

要求：
- 用户已在本机 Chrome 登录掘金
- 运行环境可访问浏览器 Cookie / Keychain

## 注意事项

1. 掘金编辑器支持 Markdown，正文不转 HTML
2. 若页面风控或登录失效，优先人工过验证后再继续自动化
3. 发布时需要至少一个标签，脚本会从标题自动提取
