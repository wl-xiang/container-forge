#!/usr/bin/env bash
# =============================================================================
# 镜像冒烟测试：验证默认入口、工作目录、基础工具、自定义 apt/pip 依赖。
#
# 用法:
#   APT_PACKAGES="vim,procps" PIP_PACKAGES="requests==2.32.3" \
#     bash tests/smoke-test.sh <image-ref>
#
# 依赖: 本机 docker；仅测试已 --load 到本地的单平台镜像
#       （arm64 镜像需先注册 QEMU binfmt，workflow 中已通过 setup-qemu 处理）。
# =============================================================================
set -euo pipefail

IMAGE="${1:?用法: smoke-test.sh <image-ref>}"
APT_PACKAGES="${APT_PACKAGES:-}"
PIP_PACKAGES="${PIP_PACKAGES:-}"

pass()      { printf 'OK   %s\n' "$1"; }
fail_exit() { printf 'FAIL %s\n' "$1" >&2; exit 1; }

echo "==> 冒烟测试目标镜像: ${IMAGE}"

# 1) 默认入口为 /bin/bash（不覆盖 entrypoint，直接附加命令，等价 docker run -it 场景）
if docker run --rm "${IMAGE}" -c 'echo entrypoint-bash-ok' | grep -q 'entrypoint-bash-ok'; then
  pass "默认 ENTRYPOINT 为 /bin/bash"
else
  fail_exit "默认入口不是可用的 /bin/bash"
fi

# 2) 工作目录为 /workspace
WD="$(docker run --rm "${IMAGE}" -c 'pwd' | tail -n1)"
if [ "${WD}" = "/workspace" ]; then
  pass "WORKDIR=/workspace"
else
  fail_exit "工作目录异常: ${WD}"
fi

# 3) 基础工具: python / git / curl / bash
if docker run --rm "${IMAGE}" -lc 'set -e; python --version; git --version; curl --version | head -n1; test -x /bin/bash'; then
  pass "python / git / curl / bash 可用"
else
  fail_exit "基础工具(python/git/curl/bash)校验失败"
fi

# 4) 自定义 apt/pip 依赖检查：
#    在宿主机把包名展开为字面检查命令写入临时脚本，挂载进容器执行，
#    避免把宿主机变量/引号拼接进容器 shell 造成注入。
CHECK_SCRIPT="$(mktemp)"
trap 'rm -f "${CHECK_SCRIPT}"' EXIT
{
  echo 'set -euo pipefail'

  if [ -n "${APT_PACKAGES}" ]; then
    printf '%s' "${APT_PACKAGES}" | tr ',;\r' '\n' \
      | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' \
      | while IFS= read -r pkg; do
          if [[ ! "${pkg}" =~ ^[A-Za-z0-9][A-Za-z0-9+._-]*$ ]]; then
            echo "SKIP 非法 apt 包名: ${pkg}"
            continue
          fi
          printf "dpkg -s %s >/dev/null 2>&1 || { echo 'FAIL apt %s 未安装'; exit 1; }\necho 'OK   apt %s'\n" \
            "${pkg}" "${pkg}" "${pkg}"
        done
  fi

  if [ -n "${PIP_PACKAGES}" ]; then
    printf '%s' "${PIP_PACKAGES}" | tr ',;\r' '\n' \
      | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' \
      | while IFS= read -r raw; do
          pkg="$(printf '%s' "${raw}" | sed -E 's/\[[^]]*\]//g; s/[=<>!~].*$//')"
          if [[ ! "${pkg}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
            echo "SKIP 非法 pip 包名: ${raw}"
            continue
          fi
          printf "python -m pip show %s >/dev/null 2>&1 || { echo 'FAIL pip %s 未安装'; exit 1; }\necho 'OK   pip %s'\n" \
            "${pkg}" "${pkg}" "${pkg}"
        done
  fi
} > "${CHECK_SCRIPT}"

if grep -qE '^(dpkg -s|python -m pip show)' "${CHECK_SCRIPT}"; then
  echo "==> 检查自定义依赖（apt/pip）"
  if docker run --rm -v "${CHECK_SCRIPT}:/tmp/check-deps.sh:ro" "${IMAGE}" bash /tmp/check-deps.sh; then
    pass "自定义 apt/pip 依赖已安装"
  else
    fail_exit "自定义依赖校验失败（详见上方输出）"
  fi
else
  echo "==> 未指定自定义依赖，跳过依赖检查"
fi

echo "==> 冒烟测试全部通过: ${IMAGE}"
