#!/usr/bin/env bash
# =============================================================================
# 项目 bootstrap 脚本示例（运行模式入口，见 docker-compose.yaml 的 app service）
#
# 约定（详见 docs/development-and-run.md）:
#   1. set -euo pipefail：任一步骤失败立即退出，保留非零退出码供编排系统判断。
#   2. 用 exec 启动主进程：让 Python 成为 PID 1 下的主进程，正确接收 SIGTERM。
#   3. 脚本内不做依赖安装（镜像应在外网构建期固化依赖）；如必须，需容错网络。
# =============================================================================
set -euo pipefail

echo "[bootstrap] $(date -Is) 启动 demo 应用, 工作目录: $(pwd)"
exec python /workspace/app.py
