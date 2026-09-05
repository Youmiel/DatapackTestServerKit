"""Fabric 服务器 jar 下载解析。

使用 Fabric meta API：https://meta.fabricmc.net/v2/
  - GET /v2/versions/loader/{game}         该 MC 版本可用的 loader 列表
  - GET /v2/versions/installer             可用 installer 版本（取最新）
  - GET /v2/versions/loader/{game}/{loader}/{installer}/server/jar
        直接返回"已装好 loader、可 java -jar 启动"的 fabric server jar。
        免去手动跑 fabric-installer。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .http_util import DownloadError, Http

META_BASE = "https://meta.fabricmc.net/v2"


@dataclass
class FabricArtifact:
    """一次解析出的 fabric 服务器下载参数。"""

    mc_version: str
    loader_version: str
    installer_version: str
    url: str
    filename: str  # 如 fabric-server-mc.1.21.1-loader.0.16.9-launcher.1.0.1.jar


def _stable(s: str) -> bool:
    """loader 版本号里含 - 的通常为 alpha/beta，优先稳定版。"""
    return "-" not in s


def pick_loader_version(http: Http, mc_version: str) -> str:
    """返回某 MC 版本下可用的最新稳定 fabric loader。"""
    data = http.get_json(f"{META_BASE}/versions/loader/{mc_version}")
    if not isinstance(data, list) or not data:
        raise DownloadError(f"Fabric 不支持 MC {mc_version}")
    # data[i].loader.version
    loaders = [d["loader"]["version"] for d in data if "loader" in d]
    stable = [v for v in loaders if _stable(v)]
    pool = stable or loaders
    return pool[0]  # API 已按时间倒序


def pick_installer_version(http: Http) -> str:
    """返回最新 installer 版本。"""
    data = http.get_json(f"{META_BASE}/versions/installer")
    if not isinstance(data, list) or not data:
        raise DownloadError("Fabric installer 版本列表为空")
    return data[0]["version"]


def resolve_server_jar_url(
    http: Http, mc_version: str, loader_version: str | None = None
) -> FabricArtifact:
    """解析出 fabric server jar 的下载 URL。loader 留空自动选最新稳定。"""
    lv = loader_version or pick_loader_version(http, mc_version)
    iv = pick_installer_version(http)
    url = f"{META_BASE}/versions/loader/{mc_version}/{lv}/{iv}/server/jar"
    filename = f"fabric-server-mc.{mc_version}-loader.{lv}-launcher.{iv}.jar"
    return FabricArtifact(
        mc_version=mc_version,
        loader_version=lv,
        installer_version=iv,
        url=url,
        filename=filename,
    )


def download_server_jar(
    http: Http, artifact: FabricArtifact, dest: Path
) -> Path:
    """下载 fabric server jar 到 dest（保存为统一的 minecraft_server.jar）。"""
    if dest.exists():
        # 简单存在性检查：非空即可复用
        if dest.stat().st_size > 0:
            return dest
    return http.download(artifact.url, dest)
