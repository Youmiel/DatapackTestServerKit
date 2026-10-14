"""路径常量：定义工具根目录与每个服务器实例的目录布局。"""
from __future__ import annotations

from pathlib import Path

# 项目根 = 本文件所在目录的上一级（server_manager/..）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认配置文件
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# 服务器实例根目录
SERVERS_ROOT = PROJECT_ROOT / "servers"

# 下载缓存目录
#   .cache/mojang_java/  Mojang Java 要求（24h TTL）
#   .cache/plugins/      MCDR 插件（跨 MC 版本通用，一次下载多实例复制复用）
#   .cache/mods/         fabric mod（按文件名共享，跨实例/跨组复用同一文件）
#   .cache/libraries/    fabric/loader 依赖库（Maven 布局，各实例 server/libraries 链接至此）
#   （fabric server jar 仍是每实例一份：其内容与 MC 版本绑定，跨实例无复用价值）
CACHE_ROOT = PROJECT_ROOT / ".cache"
MOJANG_CACHE_ROOT = CACHE_ROOT / "mojang_java"
PLUGIN_CACHE_ROOT = CACHE_ROOT / "plugins"
MOD_CACHE_ROOT = CACHE_ROOT / "mods"
LIBS_CACHE_ROOT = CACHE_ROOT / "libraries"

# MCDR 共享 venv 里的可执行文件（Windows: Scripts, 其他: bin）
def venv_bin(venv_root: Path) -> Path:
    return venv_root / ("Scripts" if Path(venv_root / "Scripts").exists() else "bin")


PROJECT_VENV = PROJECT_ROOT / "venv"


def server_dir(mc_version: str) -> Path:
    """单个 MC 小版本对应的服务器目录（平铺，版本号全局唯一）。"""
    return SERVERS_ROOT / mc_version


def instance_paths(mc_version: str) -> dict[str, Path]:
    """返回某实例的关键路径集合。

    每个实例是一个自包含的 MCDR 根：
      servers/<mc-version>/           <- MCDR 工作根（config.yml / plugins/ / logs/）
        .deploy-meta.json             <- 工具记录的部署元数据（jar 版本 / mod sha1）
        server/                       <- 服务器文件根（MCDR working_directory）
          minecraft_server.jar
          mods/                       <- fabric mod
          libraries/                  <- fabric/loader 依赖库（通常链接到 .cache/libraries）
          eula.txt / server.properties / world/
            world/datapacks/          <- 用户手动放数据包
        plugins/                      <- MCDR 插件（用户显式 URL 配置）
    """
    root = server_dir(mc_version)
    srv = root / "server"
    return {
        "root": root,  # MCDR 根
        "server_root": srv,
        "jar": srv / "minecraft_server.jar",
        "mods": srv / "mods",
        "libraries": srv / "libraries",
        "plugins": root / "plugins",
        "mcdr_config": root / "config.yml",
        "deploy_meta": root / ".deploy-meta.json",
        "eula": srv / "eula.txt",
        "server_properties": srv / "server.properties",
        "world": srv / "world",
        "datapacks": srv / "world" / "datapacks",
    }
