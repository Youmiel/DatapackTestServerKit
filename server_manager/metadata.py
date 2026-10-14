"""部署元数据与缓存索引的读写（JSON，尽力而为）。

两类文件：
  - 缓存索引  .cache/<area>/_index.json
      记录共享缓存里每个文件来自哪个 URL、sha1 指纹，用于**不下载即可判定失效**。
  - 实例元数据 servers/<ver>/.deploy-meta.json
      记录该实例部署时用的 fabric loader/installer 版本、各 mod 的 sha1、
      各插件来源指纹、libraries 是否已共享。

均属加速信息而非必需品：任何读写失败都静默忽略，不阻塞部署。
"""
from __future__ import annotations

import json
from pathlib import Path

from .paths import MOD_CACHE_ROOT, PLUGIN_CACHE_ROOT, instance_paths


def read_json(path: Path) -> dict:
    """读 JSON 对象；不存在/损坏/非对象一律返回空 dict。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: dict) -> None:
    """写 JSON（UTF-8，2 空格缩进）。失败静默（元数据非必需）。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


# --------------------------------------------------------------------------
#  缓存索引（.cache/<area>/_index.json）
# --------------------------------------------------------------------------

# 文件名以下划线开头，与真实资源文件名区分（插件/mod 名不会这样开头）
INDEX_NAME = "_index.json"


def cache_index_path(cache_dir: Path) -> Path:
    return cache_dir / INDEX_NAME


def load_cache_index(cache_dir: Path) -> dict:
    """读某缓存目录的索引：{文件名: {url, sha1, fingerprint}}。"""
    return read_json(cache_index_path(cache_dir))


def save_cache_index(cache_dir: Path, index: dict) -> None:
    write_json(cache_index_path(cache_dir), index)


def plugin_cache_dir() -> Path:
    return PLUGIN_CACHE_ROOT


def mod_cache_dir() -> Path:
    return MOD_CACHE_ROOT


# --------------------------------------------------------------------------
#  实例部署元数据（servers/<ver>/.deploy-meta.json）
# --------------------------------------------------------------------------


def read_deploy_meta(mc_version: str) -> dict:
    return read_json(instance_paths(mc_version)["deploy_meta"])


def write_deploy_meta(mc_version: str, data: dict) -> None:
    write_json(instance_paths(mc_version)["deploy_meta"], data)
