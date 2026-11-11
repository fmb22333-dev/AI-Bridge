# AI Bridge

> **AI / Agent:** In a user Bus, read `PROJECT_STATE_INDEX.json` first. In this product repository, read `AGENTS.md` before modifying source.

AI Bridge is a distributable local Runtime for AI-assisted control of host applications. The shared product repository and each installation's Bus repository have deliberately different authority.

## 下载与 AI 安装：2026-11-11 v2 功能包

**同事安装本次远程指令、完整日志、进度、取消，以及全局 MCP 文件读写和命令登记功能，请使用下面这个日期版安装包。**

- [直接下载 Windows 安装包（约 0.5 MB）](https://github.com/fmb22333-dev/AI-Bridge/raw/refs/heads/main/downloads/remote-tools-2026-11-11/AI_Bridge_RemoteTools_2026-11-11_v2.zip)
- [安装 AI 必读：下载、校验和安装说明](docs/install/REMOTE_TOOLS_2026-11-11.md)
- [远程查询、日志分页和取消协议](docs/install/REMOTE_TOOLS_PROTOCOL_2026-11-11.md)
- [MCP 工作区文件读写与命令登记](docs/install/WORKSPACE_MCP_2026-11-11.md)

可以直接把安装说明链接交给同事的 AI。压缩包包含完整功能快照、全局 MCP 注册脚本、Houdini 适配器和 UE 0.6.0 插件源码；不带发送者的凭据、Bridge 身份、工程或历史记录。首次安装需要联网下载 Python 依赖，UE 插件需在接收者工程编译。

这是独立的固定版本快照：基础 Runtime 0.2.6.96 / 远程控制修订 2026-11-11.2。标准产品源代码和自动更新清单暂未迁移到该快照。**本次安装以新指南为准，不要走下方旧版标准发行下载流程。**

## Authority model

- **This repository** — Runtime, Supervisor, host adapters, installer, protocol/specifications, clean generic Knowledge and release artifacts.
- **Per-install GitHub Bus** — `PROJECT_STATE_INDEX.json`, durable Presence, project authority and GitHub V5 fallback transport.
- **Supabase** — primary realtime command/result transport when Presence reports it connected.
- **BridgeDB** — local command identity/idempotency authority keyed by canonical `command_id`.

A transport failover never creates a second execution identity. Preserve the same `command_id`.

## Normal command path

```text
GitHub PROJECT_STATE_INDEX / Presence
              |
              v
      discover current routing
              |
      +-------+--------+
      |                |
      v                v
Supabase PRIMARY   GitHub V5 FALLBACK
      \                /
       \              /
        v            v
          Bridge Runtime
               |
          Host Adapter
               |
        Houdini / Unreal
```

Blender remains scaffold-only.

## Standard-release first install

`download -> INSTALL_AI_BRIDGE.bat -> Setup UI -> connect/create GitHub Bus -> configure Supabase Primary -> install host plugins`

The installer must not import developer machine state, Bridge IDs, credentials, workspaces, sessions, command history or project documents.

See [docs/FIRST_INSTALL_FLOW.md](docs/FIRST_INSTALL_FLOW.md).

## AI integration

An authorized AI entering a generated Bus should:

1. read `PROJECT_STATE_INDEX.json`;
2. read the indexed durable Presence;
3. use `transport.ping` when current reachability matters;
4. follow `command_transport_policy` / `supabase_transport`;
5. read project authority only for the selected project;
6. read normative product specs from this repository.

Do not recover live state from chat memory or static release documents.

Normative integration contract: [specs/AI_AGENT_PROTOCOL.md](specs/AI_AGENT_PROTOCOL.md).

## Repository contents

This repository may contain Runtime/Core source, Supervisor bootstrap, Houdini/Unreal adapter payloads, installer/release tooling, normative specs, clean generic Knowledge and deterministic release artifacts.

It must not contain developer credentials, machine Bridge IDs, live workspaces/sessions/PIDs, command/recovery history, local paths, active project authority, project-specific HIP/FBX data or private project-family execution evidence.

## Main source / standard-release baseline

- Runtime **0.2.6.89**
- Supervisor **0.1.6**
- Houdini Adapter **0.5.27**
- AIBridgeUE **0.5.3**
- Blender Adapter **scaffold**

The Runtime bundle includes a pinned Windows wheelhouse for offline first-launch dependency bootstrap. Host Plugins supports one-click Houdini install/update and project-scoped Unreal installation.

## Canonical docs

- [specs/AI_BRIDGE_EXECUTION_SPEC.md](specs/AI_BRIDGE_EXECUTION_SPEC.md)
- [specs/AI_AGENT_PROTOCOL.md](specs/AI_AGENT_PROTOCOL.md)
- [specs/AI_BRIDGE_DEPLOYMENT_SPEC.md](specs/AI_BRIDGE_DEPLOYMENT_SPEC.md)
- [specs/AI_BRIDGE_LEARNING_POLICY.md](specs/AI_BRIDGE_LEARNING_POLICY.md)
- [docs/SUPABASE_PRIMARY_TRANSPORT.md](docs/SUPABASE_PRIMARY_TRANSPORT.md)
- [docs/UPDATE_WORKFLOW.md](docs/UPDATE_WORKFLOW.md)
- [docs/CHANGE_POLICY.md](docs/CHANGE_POLICY.md)
- [migration/BASELINE.json](migration/BASELINE.json)

Product history belongs in `CHANGELOG.md` and Git history; current live/project state belongs in the user's Bus.
