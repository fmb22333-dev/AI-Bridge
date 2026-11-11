# AI Bridge：远程执行与日志功能包

将整个压缩包交给同事的 AI，并让它先读 `AI_INSTALL_GUIDE.md`。

包含：远程指令目录、UE/Houdini 会话查询、异步提交、增量进度、完整日志分页、任务进程树取消、可选正常关闭原宿主，以及全局 Codex MCP 注册脚本。UE 插件源码 0.6.0 和 Houdini 适配器已包含。

v2 增加全局 MCP 工作区文本读写和执行命令登记：目录列表、单文件和批量读取、创建、修改、带旧文件哈希检查的批量变更，以及通过结构化项目配置登记命令。仍用原有五个工具，参数可通过 `ta_bridge_context(operation="workspace.patchset.apply")` 或 `operation="workspace.project.configure"` 查询。详见 `WORKSPACE.md`。

Windows 10/11、Python 3.11+；首次安装需要联网下载依赖。解压后运行 `INSTALL_AI_BRIDGE.bat` 准备，再用 `START_AI_BRIDGE.bat` 启动。接收者需要配置自己的工作区；远程使用另需自己的 GitHub/Supabase 环境。

这是一份干净的功能快照，不带发送者的账号、凭据、Bridge 身份、工程、历史任务、Codex 偏好或技能。未打包本机虚拟环境和 UE 编译产物。

`FILE_MANIFEST.json` 记录包内文件哈希；`REMOTE.md` 是远程协议说明。默认固定使用包内 Runtime，防止自动更新覆盖此次功能；启用更新源的步骤见安装指南。
