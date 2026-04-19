---
name: zhihu-publish-v2
description: 通过 opencli 浏览器桥接发布 Markdown 文章到知乎专栏。当用户说"发布到知乎"、"发知乎"、"zhihu publish"时触发。
---

# 知乎发布技能 v2

通过 opencli 浏览器桥接将 Markdown 文章发布到知乎专栏，支持图片上传、草稿保存。

## 使用方式

```bash
python3 zhihu/zhihu_publish_v2.py "/path/to/article.md"
```

可选参数：

```bash
# 保存为草稿（不直接发布）
python3 zhihu/zhihu_publish_v2.py "/path/to/article.md" --draft

# 指定封面
python3 zhihu/zhihu_publish_v2.py "/path/to/article.md" --cover "/path/to/cover.png"

# 发布到指定专栏
python3 zhihu/zhihu_publish_v2.py "/path/to/article.md" --column "专栏ID"
```

## 工作流

1. 读取 Markdown，提取标题，将本地图片替换为 token 占位符
2. 将 Markdown 转换为知乎兼容 HTML
3. 查找现有 16:9 封面（无则不使用封面）
4. 通过 opencli daemon 在浏览器中执行发布脚本
5. 脚本在知乎页面上传图片、提交文章

## 封面策略

只查找现有封面，不自动生成：
- `*-cover-16_9-bigtext.png`、`cover-16_9-bigtext.png`
- `*-cover-zhihu.png`、`cover-zhihu.png`
- `*16_9*.png/jpg/jpeg`

## 依赖安装

```bash
pip install -r zhihu/requirements.txt
```

## 前置条件

1. 已安装并运行 [opencli](https://github.com/opencli/opencli)
2. 浏览器已安装 opencli Browser Bridge 扩展
3. 已在浏览器登录知乎

## 注意事项

1. 图片通过知乎图床 API 上传（需要登录态）
2. 若单次命令体积超过 900KB，自动降级（先去封面，再去图片）
3. opencli daemon 默认端口 19825，可通过 `OPENCLI_DAEMON_PORT` 环境变量覆盖
