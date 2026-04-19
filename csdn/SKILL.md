---
name: csdn-publish
description: 发布 Markdown 文章到 CSDN 创作中心并保存草稿。当用户说"发布到 CSDN"、"发到 CSDN 草稿箱"、"csdn publish"或要求把本地 Markdown 文章上传到 `mp.csdn.net/mp_blog/creation/editor` 时触发。
---

# CSDN 发布技能

将本地 Markdown 文章发布到 CSDN 创作中心，默认保存为草稿。

## 使用方式

```bash
python3 csdn/csdn_publish.py "/path/to/article.md"
```

可选参数：

```bash
# 手动指定封面
python3 csdn/csdn_publish.py "/path/to/article.md" --cover "/path/to/cover.png"

# 无头模式
python3 csdn/csdn_publish.py "/path/to/article.md" --headless

# 生成本地预览 HTML
python3 csdn/csdn_publish.py "/path/to/article.md" --preview
```

## 工作流

1. 读取 Markdown，解析标题、正文、内容图占位符
2. 查找文章目录里已有封面（无则跳过）
3. 打开 `https://mp.csdn.net/mp_blog/creation/editor`
4. 填写标题、标签、摘要
5. 将正文 HTML 写入 CSDN 的 CKEditor
6. 逐张替换正文图片占位符
7. 点击"保存草稿"

## 封面策略

封面优先级（只查找，不自动生成）：

1. `--cover` 手动指定
2. 文章同目录已有封面：`*-cover-csdn.png`、`cover-csdn.png`、`*-cover-16_9-bigtext.png`、`cover-16_9-bigtext.png`、`cover*.png/jpg/jpeg`

## 依赖安装

```bash
pip install -r csdn/requirements.txt
playwright install chromium
```

## 认证方式

默认从本机 Chrome 提取 `csdn.net` Cookie。

要求：
- 用户已在本机 Chrome 登录 CSDN
- 运行环境可访问浏览器 Cookie / Keychain

## 注意事项

1. 默认只保存草稿，不点击"发布博客"
2. 正文编辑器是 CSDN 的 CKEditor iframe，不是 `contenteditable`
3. 若草稿保存失败，停止并检查页面，不自动重试
