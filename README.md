# 上财学业助手

面向上海财经大学 Windows 用户的本地学习工作台：把课表、日历、课程待办、Canvas 资料和空中课堂复盘放在一起。

**当前是开发预览版，不是已经完成全新电脑验收的正式安装版。** 安装包仅在完成发布验收后提供。项目为个人开源作品，与学校、Canvas、企业微信不存在官方合作或背书关系。

## 可以做什么

- 配置自己的学期和课表：手动录入、CSV 模板、课表截图识别，核对后生成日历。
- 使用独立浏览器登录上财 Canvas，同步大纲、课件、课程通知与作业。
- 从空中课堂“我参与的”回放提取平台语音文本，选择文本模式或下载视频的图文模式。
- 选择本地 Ollama 或自己的 OpenAI 兼容 API 生成课堂复盘、提取待确认要求；也可以不开启 AI。
- 无字幕时按需安装 Whisper 本地转写；无 Office 时可选安装 LibreOffice 转换课件。
- 读取本人 Windows 企微本地数据库副本；确认课程群绑定后导入课程消息，归档已缓存附件。

## 当前限制

- 首版目标为 Windows 11 x64、上财平台。校外用户需要贡献新的适配器。
- 企微采集使用非官方本地数据库方案；客户端升级可能导致不兼容。需选择本机账号并核验姓名、组织与 ID。不能读取未同步到本机的历史。
- **企微无界面附件下载尚未接入**，也不启用鼠标键盘自动操作。未缓存附件需先在企微下载，再重新检查。
- 课表识别需要看图模型；文本模型不能替代。AI 输出可能有遗漏，课表与课堂事务必须核对。
- 本地模型需要用户自行安装／选择。Whisper 默认 small，CPU 可用但较慢；CUDA 路径需匹配驱动和运行库。
- 当前课表导入支持预览和重复导入去重；已有课程的改课管理与跨学期体验仍需完善，请勿将改后的行直接当作覆盖操作。
- 独立打包、升级恢复、真实双账号及全新 Windows 用户验收见 [发布清单](docs/RELEASE_CHECKLIST.md)，未勾选项不代表已完成。

## 开发运行

需要 Python 3.12、Node.js 22 和 Git。普通用户安装版目标是不需要开发工具。

```powershell
git clone https://github.com/y09749204-gif/sufe-study-assistant.git
cd sufe-study-assistant
npm.cmd ci --prefix apps/web
npm.cmd ci --prefix apps/desktop
python scripts/prepare-runtime.py
./scripts/dev.ps1
```

`prepare-runtime.py` 从官方站点下载固定版本 Python、Node、PostgreSQL 并安装依赖，首次下载较大。可用 `SUFE_BUILD_CACHE` 指定构建缓存目录。运行环境不提交 Git。

桌面程序使用独立用户目录 `AppData/Roaming/SufeStudyAssistant`，首次创建空数据库。不会导入作者课表、聊天、登录态或其他 Personal OS 数据。开发测试可以通过 `SUFE_DATA_DIR` 指定单独目录；不要指向其他项目的数据。

AI API 地址填写服务商的兼容入口，例如以 `/v1` 结尾的地址。云端模式须明确启用，API Key 由 Windows DPAPI 保存。Ollama 模式只允许本机服务。

## 参与贡献

欢迎贡献课程平台适配、安装兼容性、测试、文档和问题反馈。请先阅读 [贡献指南](CONTRIBUTING.md)、[开发说明](docs/DEVELOPMENT.md)、[隐私说明](docs/PRIVACY.md)。Issue 中请勿上传 Cookie、密钥、学校账号、完整聊天或课程回放。

项目代码使用 **AGPL-3.0-only**。依赖和复用代码保留各自许可证，见 [第三方声明](THIRD_PARTY_NOTICES.md)。软件许可不授予学校课件、回放或聊天内容的再发布权利。
