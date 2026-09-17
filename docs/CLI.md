# CLI 与 Personal OS 接入

学业助手独立拥有课程、资料、学校登录态及数据库。Personal OS 或其他工具通过 CLI 使用它，不直接访问数据库，不需要共享学校账号或复制登录态。桌面程序使用 Electron + NSIS 用户级安装；CLI 与桌面程序共用同一个本机后台。

## Windows 安装版

先启动桌面的“上财学业助手”。PowerShell 中指定安装目录下的命令：

```powershell
$sufe = 'E:\Apps\SufeStudyAssistant\resources\cli\sufe.cmd'
& $sufe status --json
& $sufe courses list --json
& $sufe calendar list --start 2026-09-01T00:00:00+08:00 --end 2026-10-01T00:00:00+08:00 --json
& $sufe tasks list --json
& $sufe tasks complete <任务UUID> --json
& $sufe tasks reopen <任务UUID> --json
& $sufe sync canvas --json
& $sufe sync kzkt --json
& $sufe jobs status <同步返回的job_id> --json
& $sufe open
```

将示例路径改成自己的安装位置。无需把 API Key 或本机认证令牌写入命令行。CLI 默认读取当前 Windows 用户 `%APPDATA%\SufeStudyAssistant` 下的服务发现文件，令牌用 Windows DPAPI 加密；不同数据目录可用 `--data-dir` 显式选择。后台未启动时明确返回不可用，不偷偷连接 Personal OS 或启动另一份数据库。

开发版可用 `runtime/python/python.exe scripts/sufe.py --data-dir <独立用户目录> status --json`。

## 稳定输出

stdout 始终输出 UTF-8 JSON。成功为 `{"ok":true,"protocol":1,"data":...}`；失败为 `{"ok":false,"protocol":1,"error":{"code":"unavailable","message":"..."}}`。退出码 0 表示成功、2 表示命令参数错误、3 表示服务或操作失败；`--help` 显示帮助。

同步返回 `started` 和 `job_id` 仅表示已启动，必须查询任务，`success` 才表示同步进程成功退出；重启前未完成的任务标记 `interrupted`。该成功不代表后续所有回放总结已经处理完成。网络中断后写入结果可能不确定，先查询状态，勿盲目重试同步。

## 集成方式

用进程参数数组调用随包 Python 和 `scripts/sufe.py`（不经 shell），解析 JSON 与退出码。公共入口底层调用本机 HTTP API；`/api/integration/v1/status` 返回协议和能力。访问凭据仅限同一 Windows 用户，服务只监听 loopback，CLI 拒绝重定向和代理。

Personal OS 保存连接器配置和展示缓存，学业助手是课程、待办状态的来源。完成待办直接回写助手，课程详情和资料在助手中打开。卸载助手后底座其他功能仍可运行，只显示连接不可用。CLI 不是远程多用户授权接口；不要把发现文件和数据库备份提交到 GitHub。
