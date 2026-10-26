#!/usr/bin/env python3
"""根据 workflow 输入参数动态生成 Dockerfile 与构建元数据。

用法示例:
  python3 scripts/generate_dockerfile.py \
    --output-dir build-context \
    --python-version 3.11 \
    --apt-packages "vim,procps" \
    --pip-packages "requests==2.32.3,fastapi" \
    --image-name pydev --image-tag abc1234 \
    --revision "<git sha>" --repo-url "https://github.com/org/repo"

规则:
  - apt/pip 列表支持逗号/分号/换行分隔，自动去空、去重、保序。
  - 所有注入 Dockerfile 的字符串均做白名单正则校验，防止破坏 Dockerfile 语法
    或注入 shell 命令；校验失败以退出码 2 结束并输出 [ERROR] 前缀日志，
    便于在 CI 中快速定位「Dockerfile 生成」阶段失败（AC-11）。
  - pip_requirements 支持两种形式：仓库内存在的文件路径，或直接粘贴的文本内容。
  - 输出: <output-dir>/Dockerfile、<output-dir>/requirements.txt（可选）、
          <output-dir>/build-metadata.json（审计用）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "templates" / "Dockerfile.template"

APT_PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")
PIP_PKG_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"            # 包名
    r"(\[[A-Za-z0-9,._-]*\])?"                 # 可选 extras，如 requests[security]
    r"([=<>!~][A-Za-z0-9._*+,!<>=~-]*)?$"      # 可选版本约束，如 ==2.32.3 / >=3.7,<4
)
BASE_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]*(:[0-9]+)?(:[A-Za-z0-9._-]+)?(@sha256:[a-f0-9]{64})?$"
)
LABEL_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
PY_VER_RE = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?$")

NON_ROOT_BLOCK = (
    "# 可选：非 root 用户（uid/gid 1000）。默认策略为 root，便于内网交互式开发；\n"
    "# 注意：非 root 模式下 pip 仅支持 --user 安装，挂载目录属主需与 uid 1000 匹配。\n"
    "RUN useradd --create-home --uid 1000 dev && mkdir -p /workspace && chown -R dev:dev /workspace\n"
    "USER dev"
)


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(2)


def parse_list(raw: str) -> list:
    """逗号/分号/换行分隔 -> 去空、去重、保序的列表。"""
    items = []
    for chunk in re.split(r"[,;\r\n]+", raw or ""):
        chunk = chunk.strip()
        if chunk and chunk not in items:
            items.append(chunk)
    return items


def resolve_requirements(raw: str):
    """pip_requirements：若为仓库内存在的文件路径则读取，否则视为文本内容。"""
    value = (raw or "").strip()
    if not value:
        return None, "无"
    candidate = Path(value)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8"), f"文件 {value}"
    return value + "\n", "内联文本"


def build_apt_block(required: list, extra: list) -> str:
    pkgs = required + [p for p in extra if p not in required]
    lines = [
        "# 系统工具与用户自定义 apt 包（bash/git/curl/ca-certificates 为必装基线）",
        "RUN apt-get update && \\",
        "    apt-get install -y --no-install-recommends \\",
    ]
    lines += [f"        {p} \\" for p in pkgs]
    lines.append("    && rm -rf /var/lib/apt/lists/*")
    return "\n".join(lines)


def build_pip_block(extra: list) -> str:
    if not extra:
        return ""
    body = " \\\n".join(f'        "{p}"' for p in extra)
    return "# 用户自定义 pip 包\nRUN pip install --no-cache-dir \\\n" + body


def build_requirements_block() -> str:
    return (
        "# requirements.txt 由 workflow 输入生成，用于锁定依赖版本\n"
        "COPY requirements.txt /tmp/requirements.txt\n"
        "RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm -f /tmp/requirements.txt"
    )


def build_labels_block(labels: dict) -> str:
    items = list(labels.items())
    lines = [f"LABEL {k}={json.dumps(v, ensure_ascii=True)}" for k, v in items[:1]]
    lines += [f"      {k}={json.dumps(v, ensure_ascii=True)}" for k, v in items[1:]]
    return "# OCI labels：镜像溯源信息\n" + " \\\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="动态生成 Dockerfile 与构建元数据")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--image-name", required=True)
    ap.add_argument("--image-tag", default="")
    ap.add_argument("--python-version", default="3.11")
    ap.add_argument("--base-image", default="", help="留空则使用 python:<python_version>-slim")
    ap.add_argument("--apt-packages", default="")
    ap.add_argument("--pip-packages", default="")
    ap.add_argument("--pip-requirements", default="", help="requirements 文件路径或文本内容")
    ap.add_argument("--extra-labels", default="", help="key=value，逗号分隔")
    ap.add_argument("--non-root", action="store_true", help="启用非 root 用户 uid 1000")
    ap.add_argument("--revision", default="")
    ap.add_argument("--repo-url", default="")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    args = ap.parse_args()

    image_name = args.image_name.strip()
    if not image_name:
        fail("image_name 不能为空")

    python_version = args.python_version.strip()
    if not PY_VER_RE.match(python_version):
        fail(f"python_version 格式非法: {python_version!r}（示例: 3.11 / 3.12.4）")

    base_image = args.base_image.strip() or f"python:{python_version}-slim"
    if not BASE_IMAGE_RE.match(base_image):
        fail(f"base_image 格式非法: {base_image!r}（示例: python:3.11-slim 或含 sha256 digest）")

    apt_packages = parse_list(args.apt_packages)
    for p in apt_packages:
        if not APT_PKG_RE.match(p):
            fail(f"apt 包名非法: {p!r}（仅允许字母数字与 . _ + -）")

    pip_packages = parse_list(args.pip_packages)
    for p in pip_packages:
        if not PIP_PKG_RE.match(p):
            fail(f"pip 包名/版本约束非法: {p!r}（示例: requests / requests==2.32.3 / 'fastapi>=0.110'）")

    requirements, req_source = resolve_requirements(args.pip_requirements)

    extra_labels = {}
    for item in parse_list(args.extra_labels):
        if "=" not in item:
            fail(f"extra_labels 需为 key=value 形式: {item!r}")
        k, _, v = item.partition("=")
        k, v = k.strip(), v.strip()
        if not LABEL_KEY_RE.match(k):
            fail(f"label key 非法: {k!r}")
        if "\n" in v or '"' in v:
            fail(f"label value 含非法字符: {k}")
        extra_labels[k] = v

    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    labels = {
        "org.opencontainers.image.title": image_name,
        "org.opencontainers.image.description": "Configurable Python dev/runtime image (entrypoint: /bin/bash)",
        "org.opencontainers.image.created": created,
        "org.opencontainers.image.revision": args.revision or "unknown",
        "org.opencontainers.image.version": args.image_tag,
        "org.opencontainers.image.source": args.repo_url,
        **extra_labels,
    }

    template = Path(args.template).read_text(encoding="utf-8")
    dockerfile = (
        template
        .replace("{{BASE_IMAGE}}", base_image)
        .replace("{{CREATED}}", created)
        .replace("{{REVISION}}", args.revision or "unknown")
        .replace("{{APT_INSTALL_BLOCK}}", build_apt_block(["bash", "ca-certificates", "curl", "git"], apt_packages))
        .replace("{{PIP_INSTALL_BLOCK}}", build_pip_block(pip_packages))
        .replace("{{REQUIREMENTS_BLOCK}}", build_requirements_block() if requirements is not None else "")
        .replace("{{LABELS_BLOCK}}", build_labels_block(labels))
        .replace("{{NON_ROOT_BLOCK}}", NON_ROOT_BLOCK if args.non_root else "")
    )
    dockerfile = re.sub(r"\n{3,}", "\n\n", dockerfile).strip() + "\n"

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if requirements is not None:
        (out_dir / "requirements.txt").write_text(requirements, encoding="utf-8")

    metadata = {
        "generated_at": created,
        "image_name": image_name,
        "image_tag": args.image_tag,
        "base_image": base_image,
        "python_version": python_version,
        "apt_packages": apt_packages,
        "pip_packages": pip_packages,
        "requirements": (
            {
                "source": req_source,
                "sha256": hashlib.sha256(requirements.encode()).hexdigest(),
                "lines": len(requirements.splitlines()),
            }
            if requirements is not None
            else None
        ),
        "non_root": args.non_root,
        "revision": args.revision,
        "repo_url": args.repo_url,
    }
    (out_dir / "build-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("[OK] Dockerfile 生成完成")
    print(f"     基础镜像    : {base_image}")
    print(f"     apt 追加    : {apt_packages or '(无)'}")
    print(f"     pip 追加    : {pip_packages or '(无)'}")
    print(f"     requirements: {req_source}")
    print(f"     非 root     : {args.non_root}")
    print(f"     输出        : {out_dir}/Dockerfile, build-metadata.json"
          + (", requirements.txt" if requirements is not None else ""))


if __name__ == "__main__":
    main()
