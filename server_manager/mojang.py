"""Mojang 官方数据：查询某 MC 版本所需的 Java 运行时。

数据源：Mojang version manifest (piston-meta.mojang.com)
每个版本的 version json 里带：
  javaVersion: { component: "java-runtime-delta", majorVersion: 21 }

用途：不依赖人工映射表，deploy 时自动推断该 MC 版本需要哪个 Java，
并在官方启动器 runtime 目录里精确定位对应 java-runtime-<component>。

缓存：写入 .cache/mojang_java/<version>.json，24h 内复用；失败时若已有
缓存则降级用缓存（避免网络问题阻塞部署），否则抛错。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .http_util import DownloadError
from .paths import MOJANG_CACHE_ROOT

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
CACHE_TTL = 24 * 3600  # 秒


@dataclass
class JavaRequirement:
    major: int
    component: str  # 如 java-runtime-delta

    def __str__(self) -> str:
        return f"Java {self.major} ({self.component})"


def _cache_path(mc_version: str) -> Path:
    return MOJANG_CACHE_ROOT / f"{mc_version}.json"


def _read_cache(mc_version: str) -> JavaRequirement | None:
    p = _cache_path(mc_version)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        return JavaRequirement(
            major=int(data["major"]), component=str(data["component"])
        )
    except (OSError, ValueError, KeyError):
        return None


def _write_cache(mc_version: str, req: JavaRequirement) -> None:
    try:
        p = _cache_path(mc_version)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "version": mc_version,
                    "major": req.major,
                    "component": req.component,
                    "fetched_at": int(time.time()),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _cache_fresh(mc_version: str) -> JavaRequirement | None:
    """返回缓存中未过期的要求；过期/缺失返回 None。"""
    p = _cache_path(mc_version)
    try:
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if int(data.get("fetched_at", 0)) + CACHE_TTL < time.time():
            return None  # 过期
        return JavaRequirement(
            major=int(data["major"]), component=str(data["component"])
        )
    except (OSError, ValueError, KeyError):
        return None


def fetch_java_requirement(mc_version: str, timeout: int = 30) -> JavaRequirement:
    """查询某 MC 版本官方要求的 Java。带 24h 缓存；网络失败降级旧缓存。

    抛 DownloadError 仅在无任何缓存且网络失败时。
    """
    # 1) 先看新鲜缓存
    cached = _cache_fresh(mc_version)
    if cached:
        return cached

    import requests

    session = requests.Session()
    session.trust_env = False  # 与 http_util 一致：不读系统代理
    try:
        r = session.get(MANIFEST_URL, timeout=timeout)
        r.raise_for_status()
        manifest = r.json()
        url = None
        for v in manifest.get("versions", []):
            if v.get("id") == mc_version:
                url = v.get("url")
                break
        if not url:
            raise DownloadError(f"Mojang manifest 中找不到版本 {mc_version}")
        r2 = session.get(url, timeout=timeout)
        r2.raise_for_status()
        jv = r2.json().get("javaVersion")
        if not jv or "majorVersion" not in jv:
            raise DownloadError(f"版本 {mc_version} 的 json 缺少 javaVersion 字段")
        req = JavaRequirement(
            major=int(jv["majorVersion"]),
            component=str(jv.get("component", "")),
        )
        _write_cache(mc_version, req)
        return req
    except (requests.RequestException, ValueError) as e:
        # 网络失败：降级到任意旧缓存（若存在）
        old = _read_cache(mc_version)
        if old:
            return old
        raise DownloadError(
            f"无法从 Mojang 获取 {mc_version} 的 Java 要求: {e}"
        ) from e
