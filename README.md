# container-forge

可配置 Python 开发/运行镜像构建与内网离线交付模板仓库。

通过 GitHub Actions `workflow_dispatch` 参数化动态生成 Dockerfile 并构建镜像：默认入口 `ENTRYPOINT ["/bin/bash"]`，预装 Python / Git / CURL / ca-certificates，支持自定义 apt/pip 依赖与 `linux/amd64`、`linux/arm64` 平台；产物可推送到镜像仓库，也可导出为离线 tar 包供内网 `docker load`。

## 闭环流程

```
        外网（GitHub 仓库）                          内网（隔离环境）
┌────────────────────────────────┐          ┌────────────────────────────────┐
│ 1. workflow_dispatch 填参数     │          │ 4. 下载 Artifact (tar.gz+sha256) │
│ 2. 动态生成 Dockerfile (Artifact)│   拷贝   │ 5. sha256 校验 -> docker load   │
│ 3. buildx 构建 -> push / 导出tar │ ───────> │                                │
└────────────────────────────────┘  介质/U盘  │ 6. 开发模式: volume 源码 + bash  │
                                             │ 7. 运行模式: entrypoint=bootstrap│
                                             └────────────────────────────────┘
```

## 仓库结构

```
container-forge/
├── .github/workflows/build-image.yml   # 核心构建 Workflow（workflow_dispatch）
├── scripts/generate_dockerfile.py      # Dockerfile 动态生成脚本（含输入校验）
├── templates/Dockerfile.template       # Dockerfile 模板（占位符）
├── docker-compose.yaml                 # 开发/运行双模式 Compose 模板（仓库根目录）
├── docs/
│   ├── intranet-import.md              # 内网离线导入流程
│   └── development-and-run.md          # 开发模式与运行模式详解
├── examples/
│   ├── demo-app/                       # 运行模式演示项目（bootstrap.sh + app.py）
│   └── requirements.lock.txt           # 依赖锁定示例（pip_requirements 参数用）
├── tests/smoke-test.sh                 # 镜像冒烟测试（入口/工具/依赖校验）
└── .dockerignore
```

## 快速开始（外网构建）

1. GitHub 仓库 → **Actions** → **build-image** → **Run workflow**，按参数表填写。
2. 运行完成后按需取产物：
   - 推送模式：镜像已在 `registry/image_name:image_tag`（或 `ghcr.io/<owner>/<image_name>:<tag>`）。
   - 离线模式：下载 Artifact `offline-image-<tag>`（含 `*.tar.gz`、`*.sha256`、`build-metadata.json`、`IMPORT-README.txt`）。
   - 审计产物：Artifact `dockerfile-and-metadata`（生成的 Dockerfile 与元数据）、`build-info`。

CLI 触发示例：

```bash
gh workflow run build-image \
  -f image_name=pydev \
  -f python_version=3.11 \
  -f apt_packages="vim,procps" \
  -f pip_packages="requests==2.32.3" \
  -f platforms=linux/amd64 \
  -f export_tar=true
```

## Workflow 参数说明

| 参数 | 说明 | 必填 | 默认值 |
|---|---|---|---|
| image_name | 镜像名称（不含 tag）；registry 留空且不含 `/` 时推送目标为 `ghcr.io/<owner>/<image_name>` | 是 | 无 |
| image_tag | 镜像标签；推荐富信息标签如 `py3.11-<sha>` | 否 | git short sha |
| python_version | Python 版本（决定默认基础镜像） | 否 | 3.11 |
| base_image | 基础镜像，必须含 `/bin/bash`；建议可固定 digest | 否 | `python:<version>-slim` |
| apt_packages | 额外 apt 包，逗号/换行分隔 | 否 | 空 |
| pip_packages | 额外 pip 包，可带版本约束（`requests==2.32.3`） | 否 | 空 |
| pip_requirements | requirements：仓库内文件路径（如 `examples/requirements.lock.txt`）或粘贴内容 | 否 | 空 |
| platforms | 目标平台，逗号分隔（`linux/amd64`, `linux/arm64`） | 否 | linux/amd64 |
| push_enabled | 构建后推送到镜像仓库 | 否 | false |
| registry | 镜像仓库地址；留空使用 ghcr.io | 否 | 空 |
| export_tar | 导出离线 tar 包（**仅支持单平台**） | 否 | false |
| non_root_user | 使用非 root 用户（uid 1000） | 否 | false |
| extra_labels | 额外 OCI label，`key=value` 逗号分隔 | 否 | 空 |

## Dockerfile 动态生成规则

由 [generate_dockerfile.py](file:///workspace/scripts/generate_dockerfile.py) 按 [Dockerfile.template](file:///workspace/templates/Dockerfile.template) 生成，规则要点：

1. 基础镜像：`python:<python_version>-slim` 或用户指定（白名单正则校验格式）。
2. 必装基线：`bash / ca-certificates / curl / git`，Python 由基础镜像保证。
3. 追加用户 apt 包（`--no-install-recommends`，清理 apt 缓存）与 pip 包（`--no-cache-dir`）。
4. `pip_requirements` 支持仓库文件路径或粘贴内容，生成 `requirements.txt` 并 `COPY` 后安装，锁定版本。
5. 环境变量：`PYTHONUNBUFFERED=1`、`PYTHONDONTWRITEBYTECODE=1`、`PIP_NO_CACHE_DIR=1`。
6. `WORKDIR /workspace`；`ENTRYPOINT ["/bin/bash"]`、`CMD ["-l"]`。
7. OCI labels：`title/created/revision/version/source` + 用户自定义 labels。
8. 可选非 root 用户（uid 1000）；末尾自检 `/bin/bash` 存在性。
9. 所有输入经白名单校验（包名、镜像引用、label 键值），防止注入；生成的 Dockerfile 与 `build-metadata.json` 上传为 Artifact 供审计与复现。

## 镜像规范

- 默认入口：`ENTRYPOINT ["/bin/bash"]`，`CMD ["-l"]`；`docker run -it --rm IMAGE` 直接进入 bash。
- 工作目录：`/workspace`；镜像内不含业务源码（源码通过 volume 或后续构建阶段注入）。
- 标签策略：默认 `git short sha`；建议手填 `image_tag=py<版本>-<短sha>` 富信息标签。
- 溯源：每次构建的 Dockerfile、依赖列表、requirements sha256、Git revision 均记录在 Artifact 中。

## 开发模式与运行模式（Compose 模板）

仓库根目录 [docker-compose.yaml](file:///workspace/docker-compose.yaml) 提供双模式模板（通过 profiles 隔离）：

- **开发模式**：`docker compose --profile dev up -d` 后 `exec` 进入 bash；volume 把源码映射到 `/workspace`，改代码即时生效。
- **运行模式**：`docker compose --profile run up app`；entrypoint 覆盖为 `/workspace/bootstrap.sh` 自动启动项目，`init: true` 保证信号与退出码正确。

详细说明见 [docs/development-and-run.md](file:///workspace/docs/development-and-run.md)。

## 内网离线导入（摘要）

1. 从 Actions 运行页下载 `offline-image-<tag>` Artifact 并拷贝入内网。
2. `sha256sum -c xxx.tar.gz.sha256` 校验。
3. `gunzip -c xxx.tar.gz | docker load` 导入。
4. `docker run -it --rm <image>` 验证可进入 bash。

完整流程与故障排查见 [docs/intranet-import.md](file:///workspace/docs/intranet-import.md)。

## 安全要求

- **不硬编码密钥**：私有 Registry 凭据仅存于 GitHub Secrets（`REGISTRY_USERNAME` / `REGISTRY_PASSWORD`）；GHCR 使用内置 `GITHUB_TOKEN`；日志自动脱敏。
- **最小权限**：Workflow 仅 `contents: read` + `packages: write`，按需可再收敛。
- **输入安全**：自由文本参数经环境变量传入 shell（防注入）；包名/镜像引用/label 经白名单正则校验后才写入 Dockerfile。
- **依赖可信可锁定**：pip 包支持版本约束或 requirements 锁定（metadata 记录 sha256）；基础镜像可固定 digest；镜像构建期即暴露依赖冲突。
- **可选非 root**：`non_root_user=true` 时容器以 uid 1000 运行。
- **制品保留期**：离线镜像 Artifact 14 天、Dockerfile 30 天，减少敏感制品暴露面。

## 常见问题

| 问题 | 说明与处理 |
|---|---|
| 多架构 tar 无法 `docker load` | `docker save` 格式限制；`export_tar` 仅允许单平台（Workflow 会快速失败并提示），多架构请走 Registry 推送 |
| QEMU 构建 arm64 慢 | 已启用 `type=gha` 构建缓存；仍慢则拆分为 amd64/arm64 两次单平台构建 |
| 基础镜像不可达 | 构建在外网执行，检查 tag 拼写与网络限流；可在 `base_image` 固定 digest 保证可复现 |
| 依赖版本冲突 | 构建期即失败；改用 requirements 锁定文件并重跑 |
| Artifact 过大 | 离线 tar 受 GitHub Artifact 体积限制；超大镜像建议推 Registry 后在离线侧自建导入，或分卷 |
| 内网无 Compose v2 | 用 `docker run` 等价命令，见 docs/development-and-run.md 附录 |

## 验收标准对照

| AC | 验证方法 |
|---|---|
| AC-01 | Actions 页面触发 build-image，参数表单与说明可见 |
| AC-02 | 「阶段1」日志输出 Dockerfile 全文；Artifact `dockerfile-and-metadata` 可下载 |
| AC-03 | 默认 `linux/amd64` 构建成功；改 `linux/arm64` 或双平台重跑成功（自动配置 QEMU） |
| AC-04 | `docker run -it --rm IMAGE` 进入 bash（冒烟测试第 1 项自动覆盖） |
| AC-05 | 容器内 `python/git/curl --version` 正常；冒烟测试自动校验自定义 apt/pip 包 |
| AC-06 | `export_tar=true` + 单平台；Artifact 内 `sha256sum -c` 通过；`docker load` 成功 |
| AC-07 | dev 模式编辑 `examples/demo-app` 内文件，容器内立即生效 |
| AC-08 | `docker compose --profile run up app` 自动执行 bootstrap.sh，退出码 0 |
| AC-09 | 本 README 与 docs/ 两篇文档覆盖四个环节 |
| AC-10 | 仓库检索无任何密钥/Token 明文 |
| AC-11 | 步骤按「阶段N」命名，失败可定位到生成/构建测试/推送/导出环节 |

## 本地调试生成器

```bash
python3 scripts/generate_dockerfile.py \
  --output-dir /tmp/build-context \
  --image-name pydev --image-tag test \
  --apt-packages "vim,procps" \
  --pip-packages "requests==2.32.3" \
  --pip-requirements "examples/requirements.lock.txt" \
  --revision "$(git rev-parse HEAD)"
```

## 扩展路线（P2）

- 构建缓存已内置（`cache-from/cache-to type=gha`）。
- SBOM/镜像扫描：Buildx 支持 `--sbom=true --provenance=true`，或追加 Trivy/Grype 扫描步骤。
- 多架构离线分发：推 Registry 后用 `docker buildx imagetools create` 汇聚 manifest，内网侧自建 Registry 同步。
