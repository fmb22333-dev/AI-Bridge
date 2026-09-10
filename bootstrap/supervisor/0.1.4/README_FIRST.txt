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

产品更新模型：

Runtime / Supervisor 的默认更新源是共享产品仓库：
  fmb22333-dev/AI-Bridge : main

每台机器自己的 GitHub Bus 只负责：
  command / result / status / PROJECT_STATE_INDEX / 项目权威状态

用户 Bus 永远不会被自动创建 bridge-runtime 发布分支，也不会被当成 Runtime
更新源。若配置了显式镜像仓库，Supervisor 可以读取该镜像，但仍禁止
bootstrap_from_bus。

Bridge 内置受限 bridge_admin 维护能力。Runtime 更新会先进入 Staging，
执行 compile / pytest 验证，通过后才切换；Supervisor 负责健康检查与失败回滚。

首次安装完成后，正常 Runtime 更新不要求重复下载安装包。
