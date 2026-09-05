"""路径常量：定义工具根目录与每个服务器实例的目录布局。"""
from __future__ import annotations

from pathlib import Path

# 项目根 = 本文件所在目录的上一级（server_manager/..）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认配置文件
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

# 服务器实例根目录
SERVERS_ROOT = PROJECT_ROOT / "servers"

# 下载缓存目录（已下载的 jar/mod 二次部署可复用）
CACHE_ROOT = PROJECT_ROOT / ".cache"

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
        server/                       <- 服务器文件根（MCDR working_directory）
          minecraft_server.jar
          mods/                       <- fabric mod
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
        "plugins": root / "plugins",
        "mcdr_config": root / "config.yml",
        "eula": srv / "eula.txt",
        "server_properties": srv / "server.properties",
        "world": srv / "world",
        "datapacks": srv / "world" / "datapacks",
    }
