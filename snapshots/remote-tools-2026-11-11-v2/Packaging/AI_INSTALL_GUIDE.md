# 给安装 AI：先读这里

这是 Windows 版 AI Bridge 完整安装包，包含 2026-11-11 的远程指令发现、进度事件、完整日志分页、任务进程树取消，以及全局 ta_bridge MCP。UE 插件源码为 0.6.0；Houdini 适配器随 Runtime 提供。

## 安装目标与边界

- 在接收者自己的电脑安装。压缩包没有发送者的凭据、Bridge ID、工作区、历史任务或工程文件。
- 不要连接发送者的私有仓库、Supabase 或机器。远程账号、Bus 仓库、Supabase 项目与权限由接收者提供。
- 先检查该机器是否已有 Bridge、Codex MCP 或活动 UE/Houdini。已有安装先备份，再决定使用原数据升级还是新目录安装；不要关闭宿主、覆盖原配置或复制本机状态。
- 默认仅准备文件、依赖和可选 Codex 注册，不替用户建立云账号、开防火墙、共享凭据、安装开机启动或发起 UE 编译。

## 干净安装

1. 解压到本机可写目录，例如 `D:/Tools/AI_Bridge`。不要放在临时解压目录后直接删除。
2. 确认 Python 3.11+ 可用，运行 `INSTALL_AI_BRIDGE.bat`。它创建包内 `_System/.venv` 并安装 Python 依赖，因此首次安装需要联网。
3. 如果检测到 Codex 配置，安装器只增加 `mcp_servers.ta_bridge`。已有不同路径的同名服务会拒绝覆盖。检查后才使用 `Tools/register_codex.py --replace-existing`；该操作会备份原配置。其它模型、审批、技能和工具配置保留。
4. 运行 `START_AI_BRIDGE.bat`。首次启动生成接收者自己的 `%USERPROFILE%/.ai_bridge` 配置，并打开设置页面。默认本机地址由 `connection.json` 确定，不要假定固定端口。
   Supervisor 沿用原有行为，会先把 Houdini 适配器部署到识别到的默认用户偏好目录；非 21.0 版本应先按下文指定实际目录。
5. 本地使用不需要配置 Supabase 或 GitHub。通过本地 context 确认服务可用，再注册接收者自己的工作区。
6. 网页/远程使用时，在设置页面选择**手动连接已有的接收者 Bus 仓库**，再配置其 Supabase 通道。仓库必须已有相应文档/权限，Supabase 表结构需与 `REMOTE.md` 描述的命令行结构一致。没有远程基础设施时先完成本地安装，再讨论部署。包里不包含云端表初始化器或服务器账号。

该包是本次功能快照。为防自动下载旧发行版覆盖更新，包内 Supervisor 默认不自动更新 Runtime。用户选择可信更新源并验证版本后，才在启动环境设置 `AI_BRIDGE_ALLOW_AUTOMATIC_UPDATES=1`。不要使用“一键创建 Bus”里未配置的更新源占位值。

## Host 插件

Houdini：默认安装器识别 21.0 用户目录；其它版本先找实际偏好目录，通过 `Runtime/Current/install_houdini_adapter.py --houdini-user-dir <实际目录>` 指定。保护正在运行的 HIP；需要重新加载插件时与用户安排重启。

UE：源码位于 `Runtime/Current/unreal_adapter/AIBridgeUE`。新项目复制到 `<工程>/Plugins/AIBridgeUE`；已有插件先比较和备份，保护其自定义改动。不要在活动编辑器中替换已加载 DLL。使用该工程真实 UE 版本和 Editor target 编译，按需安排重开编辑器。包内无编译好的 UE DLL，不能保证跨 UE 版本直接编译；首次接入以该版本真实编译/会话为准。

## 验收

- v2 工作区文件能力：`ta_bridge_context()` 应返回 `workspace_access`；用 `ta_bridge_context(operation="workspace.patchset.apply")` 查看参数，再按 `WORKSPACE.md` 在接收者授权的测试目录验证。单文件和批量读取会返回 SHA-256；批量修改已有文件必须提供该哈希。更新已安装的同一路径 helper 后需重载 MCP，现有 UE/Houdini 无需重启。
- 登记执行命令：查询 `workspace.project.configure` 的参数，先读 `.ai-bridge/project.json`，保留原命令和服务，提交完整 manifest 与读取时的哈希；新建 manifest 才省略哈希。登记不会执行命令，执行另用 `workspace.command.run`。这是整体替换，不要遗漏其它条目。

- Codex：重新加载后检查 `codex mcp get ta_bridge --json` 为 enabled；不要把配置文件存在当作工具已经加载。
- `ta_bridge_context()` 能返回本机工作区/会话；无宿主时 sessions 为空是正常现象。
- 远程：先 `transport.ping`，根据 `remote_controls` 查询 `transport.capabilities`；现有能力搜索也能找到命令。用接收者授权的简单输出进程验证进度、日志和取消，禁止拿实际工程写入做连通测试。
- 完整日志用 `command.log` 分页，拼接并核对哈希。取消后查原任务回执；Supabase 行 state=failed 可能是取消，精确原因看 `result.failure.code=TASK_CANCELLED`。
- `close_host` 默认 false；用户明确要求关闭原宿主时才设 true。该参数仅请求正常关闭，未保存提示可能需要用户处理。

任务进程归属在等待超时后仍保留，Runtime 本身重启后当前版本会失去进程句柄；不能据此猜 PID 并结束其它程序。原始任务未知时查原 ID，不要盲目重放。

## 回滚

Codex 配置修改前会创建带时间戳的备份；恢复前核对后续人工改动。Bridge 原数据保留在接收者自己的 `.ai_bridge`，不要删除。该安装包不包含发送者的项目级 ta_accelerator、工程构建动作、全局 AGENTS 或技能。
