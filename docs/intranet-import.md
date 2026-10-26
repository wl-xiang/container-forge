# 内网离线导入与使用

本文说明「外网构建 -> 离线导入内网 -> 开发模式 -> 运行模式」闭环中的**导入**环节。前提：

- 已在外网通过 `build-image` Workflow 完成构建，且勾选了 `export_tar`（单平台，如 `linux/amd64`）。
- 内网机器已安装 Docker（20.10+ 建议）。
- 内网与外网无直连，通过 U 盘 / 移动硬盘 / 内部中转等介质传输文件。

## 1. 外网侧：获取离线包

Workflow 运行成功后，在运行详情页底部 **Artifacts** 下载：

| Artifact | 内容 |
|---|---|
| `offline-image-<tag>` | `<tag>-<platform>.tar.gz`（镜像离线包）、`*.tar.gz.sha256`（校验）、`build-metadata.json`（构建元数据）、`IMPORT-README.txt`（导入说明） |
| `dockerfile-and-metadata` | 本次生成的 Dockerfile 与构建元数据（审计用） |
| `build-info` | 镜像引用、平台、构建参数、运行链接汇总 |

## 2. 传输与校验

将 Artifact 压缩包解压后，整体拷贝进内网，在包所在目录执行：

```bash
# 1) 完整性校验（必须通过再导入）
sha256sum -c pydev-linux-amd64.tar.gz.sha256
# 预期输出: pydev-linux-amd64.tar.gz: OK
```

> 若不通过：说明传输损坏，重新拷贝；不要跳过校验直接导入。

## 3. 导入镜像

```bash
# 方式一：不解盘直接导入
gunzip -c pydev-linux-amd64.tar.gz | docker load

# 方式二：先解压再导入（磁盘充足时，之后可删除中间 tar）
gunzip -k pydev-linux-amd64.tar.gz
docker load -i pydev-linux-amd64.tar
```

导入完成后确认：

```bash
docker images | grep <镜像名>
# 镜像引用以 IMPORT-README.txt / build-metadata.json 中记录为准，
# 如 ghcr.io/your-org/pydev:<tag>
```

## 4. 验证镜像

```bash
# 直接进入 bash（默认 ENTRYPOINT 即 /bin/bash）
docker run -it --rm ghcr.io/your-org/pydev:<tag>

# 容器内检查必装工具与自定义依赖
python --version && git --version && curl --version | head -n1
dpkg -s vim >/dev/null && echo "vim OK"        # 自定义 apt 包按需检查
python -m pip show requests | head -n2          # 自定义 pip 包按需检查
```

## 5. （可选）转推内网私有 Registry

如果内网有私有 Registry，建议统一入库存档：

```bash
docker tag ghcr.io/your-org/pydev:<tag> registry.intranet.local/dev/pydev:<tag>
docker push registry.intranet.local/dev/pydev:<tag>
```

之后内网各机器直接从内网 Registry 拉取，无需重复导入 tar。

## 6. 内网开发模式

将本仓库的 [docker-compose.yaml](../docker-compose.yaml) 拷贝到你的项目目录（或按注释改写 `volumes` 指向现有源码），然后：

```bash
# 1) 修改 compose 中 image 为导入的镜像引用
# 2) 启动开发容器（volume 已把源码映射到 /workspace）
docker compose --profile dev up -d

# 3) 进入 bash 开始开发
docker compose --profile dev exec dev bash
```

容器内 `python app.py` 即可运行代码；宿主机改动即时生效。细节见 [development-and-run.md](development-and-run.md)。

## 7. 内网运行模式

确认源码目录中存在 `bootstrap.sh`（可参考 [examples/demo-app](../examples/demo-app/)），然后：

```bash
docker compose --profile run up app        # 前台运行，退出码直接可见
```

无需在内网宿主机安装 Python 或任何项目依赖——运行时完全在导入的镜像中完成。

## 8. 故障排查

| 现象 | 原因与处理 |
|---|---|
| `docker load` 报 `no space left` | 磁盘空间不足；清理 `docker system prune` 或换更大分区 |
| `docker run` 报 `exec format error` | 镜像架构与内网主机不符（如 arm64 镜像跑在 x86 上）；核对 build-metadata.json 的平台与 `uname -m` |
| 校验不通过 | 传输损坏或版本不一致；重新拷贝，确认 sha256 文件与 tar 同批 |
| `sha256sum -c` 找不到文件 | 需在 tar 与 sha256 文件所在目录执行；或用 `sha256sum -c <(cat xxx.sha256)` 指定路径 |
| 镜像 tag 与预期不符 | 以 `IMPORT-README.txt` 记录为准；`docker load` 会保留构建时的完整引用 |
| 内网 Docker 版本过老 | 20.10 以下对部分镜像格式兼容性差；升级 Docker 或改用 docker save 的纯 tar（不 gzip）格式 |
