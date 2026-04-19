# md-publisher

一组用于将 Markdown 文章自动发布到中文内容平台的工具，支持 CSDN、掘金、知乎。

## 结构

```
md-publisher/
├── csdn/           # CSDN 发布（Playwright 浏览器自动化）
├── juejin/         # 掘金发布（Playwright 浏览器自动化）
├── zhihu/          # 知乎发布（opencli 浏览器桥接）
└── shared/         # 公共工具（Markdown 解析、剪贴板）
```

## 快速开始

### CSDN

```bash
pip install -r csdn/requirements.txt
playwright install chromium
python3 csdn/csdn_publish.py "/path/to/article.md"
```

### 掘金

```bash
pip install -r juejin/requirements.txt
playwright install chromium
python3 juejin/juejin_publish.py "/path/to/article.md"
```

### 知乎

```bash
pip install -r zhihu/requirements.txt
# 需要提前安装并运行 opencli + Browser Bridge 扩展
python3 zhihu/zhihu_publish_v2.py "/path/to/article.md"
```

## 认证

- **CSDN / 掘金**：使用 `browser-cookie3` 从本机 Chrome 自动提取 Cookie，无需手动配置。在 Chrome 登录对应平台后即可使用。
- **知乎**：通过 opencli Browser Bridge 扩展在已登录的浏览器中执行 API 调用，无需提取 Cookie。

## 封面

各发布器会自动在文章同目录查找封面图，命名规则：

| 平台 | 推荐文件名 | 比例 |
|------|-----------|------|
| CSDN | `*-cover-csdn.png` 或 `*-cover-16_9-bigtext.png` | 16:9 |
| 掘金 | `*-cover-juejin.png` 或 `*-cover-16_9-bigtext.png` | 16:9 |
| 知乎 | `*-cover-zhihu.png` 或 `*-cover-16_9-bigtext.png` | 16:9 |

也可通过 `--cover` 参数手动指定封面路径。

## 系统要求

- Python 3.10+
- macOS（剪贴板功能依赖 pyobjc）
- Chrome 浏览器（已登录对应平台）

## 作者

| 平台 | 链接 |
|------|------|
| X（Twitter） | [@koffuxu](https://x.com/koffuxu) |
| 微信公众号 | 可夫小子 |

## License

MIT
