# 开发模式与运行模式

本仓库镜像默认 `ENTRYPOINT ["/bin/bash"]`、`WORKDIR /workspace`，通过 [docker-compose.yaml](../docker-compose.yaml) 模板支持两种使用模式。两模式通过 Compose `profiles` 隔离，可共存于同一文件。

## 模式对照

| 维度 | 开发模式（dev） | 运行模式（run） |
|---|---|---|
| 目的 | 交互式开发、调试 | 容器启动即自动运行项目 |
| 源码进入方式 | volume 映射宿主机源码到 `/workspace` | 同左（也可改为构建期 COPY） |
| entrypoint | `/bin/bash`（镜像默认） | `/bin/bash /workspace/bootstrap.sh` |
| 典型命令 | `docker compose --profile dev up -d` + `exec` | `docker compose --profile run up app` |
| 生命周期 | 常驻，手动进入/退出 | 跑完退出或常驻服务 |

## 开发模式详解

```bash
# 启动（-d 后台常驻）
docker compose --profile dev up -d

# 进入交互式 bash
docker compose --profile dev exec dev bash

# 或一次性创建并进入（退出即销毁容器）
docker compose run --rm dev
```

要点：

1. **volume 映射是关键**：`./your-src:/workspace` 把源码映射进容器，宿主机改代码容器内立即可见，无需重建镜像。把模板中 `./examples/demo-app` 换成你的项目目录即可。
2. **`stdin_open` + `tty`**：等价于 `docker run -it`，保证 bash 交互体验。
3. **临时依赖注意事项**：容器内 `pip install xxx` 只写入容器可写层，`docker compose down` / 容器重建后**即失效**。沉淀依赖的正确做法：
   - 把包名/版本回填到 Workflow 参数（`pip_packages` 或 requirements 锁定文件）重新构建镜像；
   - 或在项目 requirements 中锁定，走一次新的镜像构建。
4. **端口/调试**：需要 Web 调试时在 dev service 增加 `ports: ["8000:8000"]`；调试器映射按需增加。

## 运行模式详解

运行模式的核心是**覆盖 entrypoint 为项目 bootstrap 脚本**：

```yaml
entrypoint: ["/bin/bash", "/workspace/bootstrap.sh"]
init: true
```

bootstrap 脚本约定（参考 [examples/demo-app/bootstrap.sh](../examples/demo-app/bootstrap.sh)）：

1. `set -euo pipefail`：任一步骤失败立即退出，编排系统能拿到非零退出码。
2. 用 `exec` 启动主进程（如 `exec python app.py`）：主进程正确接收 `SIGTERM`，配合 `init: true` 实现优雅停止。
3. 不在 bootstrap 中做依赖安装：依赖应在外网构建期固化进镜像；内网运行期不访问公网。

常用操作：

```bash
# 前台运行（退出码直接可见，便于验收）
docker compose --profile run up app
echo $?                                   # 0 表示成功

# 后台运行 + 日志
docker compose --profile run up -d app
docker compose --profile run logs -f app

# 查询上次容器退出码（排查崩溃）
docker inspect --format '{{.State.ExitCode}}' <container>

# 优雅停止（发送 SIGTERM，init 进程转发给主进程）
docker compose --profile run stop app
```

从开发切换到运行：不需要换镜像——同一镜像既有 bash 入口（默认）又能被覆盖 entrypoint；只需把 compose 中 entrypoint/ command 按运行模式改写。反之亦然。

## 附录：不使用 Compose 的等价 docker run 命令

内网若只有老版 docker-compose 或习惯裸 `docker run`：

```bash
# 开发模式（volume + 交互 bash）
docker run -it --rm \
  -v "$(pwd)/your-src:/workspace" \
  -w /workspace \
  -e PYTHONUNBUFFERED=1 \
  ghcr.io/your-org/pydev:<tag>

# 运行模式（覆盖 entrypoint 执行 bootstrap）
docker run --rm --init \
  -v "$(pwd)/your-src:/workspace" \
  -w /workspace \
  -e PYTHONUNBUFFERED=1 \
  --entrypoint /bin/bash \
  ghcr.io/your-org/pydev:<tag> /workspace/bootstrap.sh
```

## 常见问题

| 问题 | 处理 |
|---|---|
| `docker compose up` 提示 no service selected | 两 service 均声明了 profiles，必须带 `--profile dev` 或 `--profile run` |
| 老版本 docker-compose 不认识 profiles | compose v1 需 >=1.28；或把不需要的 service 整段注释 |
| 挂载后容器内无法写文件（非 root 镜像） | 非 root 模式 uid 1000，需宿主机目录属主匹配；或构建镜像时不启用 non_root |
| bootstrap 报 `No such file` | volume 路径映射错误，`docker compose run --rm dev ls /workspace` 排查 |
| 运行模式立刻退出 | 查看 `docker compose logs app` 与退出码；demo 应用本身跑完即退出属正常 |
