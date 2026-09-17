# 第三方许可与来源

本项目自己的代码：AGPL-3.0-only。分发时同时保留以下依赖的许可证、版权声明及适用的对应源码。

| 组件 | 许可／来源 | 用途 |
| --- | --- | --- |
| PyMuPDF / MuPDF | AGPL-3.0，https://github.com/pymupdf/PyMuPDF | PDF 渲染 |
| wxwork_crypto.py | MIT，tzwkb/wecom-agent，commit `437d69653d1bbbb12e976b3aa78ba464d3d2bb30` | 企微本地数据库解密原语 |
| discover-wecom-key.ps1 | 改编自上述 MIT 项目 | 本机只读密钥发现 |
| Electron / Next.js / React / Playwright | MIT | 桌面、Web 和浏览器适配 |
| PostgreSQL | PostgreSQL License；Windows 发行包保留 EDB 附带声明 | 独立数据库 |
| Python | PSF License；嵌入发行版附带许可 | API 运行环境 |
| Node.js | MIT 及其发行版附带第三方许可 | 浏览器脚本 |
| FastAPI / SQLAlchemy / Alembic / Pydantic / HTTPX | MIT | API 与数据模型 |
| psycopg | LGPL-3.0 系列，具体以所分发版本为准 | 数据库驱动 |
| pypdf | BSD-3-Clause | PDF 文本 |
| OpenCV | Apache-2.0；发行包另含第三方组件 | 视频画面 |
| Pillow | HPND 系列许可 | 图像处理 |
| RapidOCR / ONNX Runtime | Apache-2.0 / MIT，模型许可随上游版本 | OCR |
| faster-whisper / CTranslate2 | MIT | 可选本地转写 |
| LibreOffice | MPL-2.0 等上游许可，本项目不捆绑，用户单独安装 | 可选 Office 文档转换 |
| Ollama 与模型 | 用户单独安装；模型按各自许可使用 | 可选本地 AI |

企微复用代码的原始 MIT 全文在 `apps/api/personal_os_api/vendor/wecom/LICENSE`。模型权重、课件、回放和用户数据不进入源码仓库。

构建包内 Python wheel 的 `.dist-info`、运行时 license 文件必须保留。发布安装包时应一并提供本项目对应提交源码，并核对完整传递依赖许可清单；此摘要不替代依赖自身的许可全文。
