# 开发与架构

前端为 Next.js 静态导出，由 FastAPI 同源提供，Electron 只作为桌面宿主。每次启动生成随机本机 API token，通过 URL fragment 完成一次会话交换；API 使用 HttpOnly、SameSite=Strict cookie 或内部 token 鉴权。

PostgreSQL、Python 和 Node 由桌面宿主在独立目录启动。端口动态分配，不连接默认 8000 或现有 Personal OS 数据库。进程退出只处理自己创建的子进程和指定应用数据库目录。

源码内保留原始 Python 包名 `personal_os_api` 以降低成熟采集／阅读代码的迁移风险；这不是对私人项目的运行时依赖。无 OpenClaw、个人微信或 Honcho 启动依赖。

## 测试

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r apps/api/requirements-dev.txt
# 准备独立 PostgreSQL 数据库 sufe_test，绝不能使用个人数据库。
$env:DATABASE_URL='postgresql+psycopg://sufe:YOUR_TEST_PASSWORD@127.0.0.1:55439/sufe_test'
./.venv/Scripts/python.exe -m pytest -q
npm.cmd ci --prefix integrations/kzkt-browser
npx.cmd --prefix integrations/kzkt-browser playwright-core install chromium
node --test integrations/kzkt-browser/*.test.mjs
npm.cmd run typecheck --prefix apps/web
npm.cmd run build --prefix apps/web
python scripts/check-publication.py
```

数据库测试只接受名称为 `sufe_test` 的数据库，以事务回滚隔离；没有配置时明确跳过，CI 必须配置。数据迁移脚本仅创建分享版空 schema，并拒绝未知版本；后续 schema 修改必须增加真实迁移与恢复测试。

## 接口

- `/api/setup`：用户目录、学期与默认回放模式；节次使用内置上财时间表。
- `/api/setup/timetable/{preview,csv,recognize,confirm}`：导入草稿与确认。预览及识别不写课程数据库。
- `/api/setup/ai`、`/api/setup/ai/test`：模型配置与实际连接测试；秘密不回显。
- `/api/connections/*`：独立登录、识别账号、绑定与企微只读配置。
- `/api/academics/*`、`/api/calendar`、`/api/tasks`：课程、资料、复盘、日历及待办。
- `/api/components/*`：可选组件检测与明确触发的转写安装。

回放文字先于媒体提取。文本模式不调用视频下载；图文模式随后获取媒体。队列分别记录发现、平台文本、媒体下载、本地转写、总结、画面和课堂要求提取阶段；AI 未配置仍保存平台原文。

## 打包

```powershell
python scripts/prepare-runtime.py
npm.cmd run build --prefix apps/web
npm.cmd ci --prefix apps/desktop
npm.cmd run pack --prefix apps/desktop
```

首次运行 `prepare-runtime.py` 需要网络。固定下载源与构建校验值写入运行时 manifest。安装包不能包含测试数据、开发机设置、学校登录态或个人模型缓存。签名证书不是开发版前置条件；未签名必须注明，不能引导关闭系统保护。

打包后的冒烟测试与产物检查：

```powershell
python scripts/smoke-desktop.py dist/win-unpacked/resources
python scripts/check-artifact.py dist/win-unpacked
```

冒烟测试使用新建的临时用户目录；它验证运行时与 API，不替代全新 Windows 的安装、卸载及真实账号验收。

Windows 已准备 runtime 和 Web 构建后，可运行 `node scripts/test-onboarding.mjs` 验证完整向导。测试使用新临时目录与虚构课程，结束后停止自建服务。用 `SUFE_TEST_BROWSER` 指定本机 Chrome／Edge 路径，否则使用 Playwright 已安装的 Chromium。可用 `SUFE_TEST_SCREENSHOTS` 指定界面截图输出目录。
