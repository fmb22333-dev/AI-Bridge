AI Bridge - 从这里开始
====================

最简单：双击根目录的 AI_Bridge.bat

根目录入口：

AI_Bridge.bat
  推荐入口。等同于 START_AI_BRIDGE.bat。

START_AI_BRIDGE.bat
  启动 Supervisor + 当前 Runtime。
  第一次运行会自动准备 Python 环境并同步 Houdini Adapter。

OPEN_AI_BRIDGE.bat
  Bridge 已运行时重新打开控制页面。

STOP_AI_BRIDGE.bat
  停止 Supervisor 和 Runtime。

你通常不需要进入下面目录：

Runtime\Current   当前运行的可替换 Runtime
Runtime\Previous  自动回滚备份
Runtime\Staging   AI/更新校验区
_System           稳定 Supervisor 与本地 Python 环境
Docs              技术文档

自更新说明：

首次使用这个 Supervisor 版后，它会复用现有 GitHub Bus 配置，在同一仓库建立
bridge-runtime 更新分支。main 仍然只用于 command/result/status。

从这一版开始，Bridge 内置受限 bridge_admin 维护适配器：AI 可以通过现有 GitHub Bus
复制 Runtime 到 Staging、修改 Bridge 文件、运行 compile/pytest、发布新版本；Supervisor
负责停止旧 Runtime、切换、健康检查和失败回滚。

因此这次手动换到 Supervisor 版属于一次性的 bootstrap。后续正常 Bridge Runtime
迭代不再要求你下载 ZIP / 覆盖文件 / 手工重启。
