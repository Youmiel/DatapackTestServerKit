"""部署编排：把一个版本实例从零装到可运行。

步骤：
  1. 确保 MCDR 已安装（共享 venv）
  2. 探测 Java（固定）
  3. 下载 fabric server jar（含 loader，可直接启动）
  4. 初始化 MCDR 实例目录（init）+ 生成 eula/server.properties
  5. 写入 MCDR config.yml（exec start_command）
  6. 下载并安装 fabric mod 到 server/mods/
  7. 下载 MCDR 插件到 plugins/
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import fabric, mcdr, mods, mojang, server as server_mgr
from .config import InstanceGroup, RootConfig
from .http_util import build_modrinth_http
from .java import JavaRuntime, detect_java
from .paths import instance_paths


@dataclass
class DeployResult:
    mc_version: str
    java: JavaRuntime
    jar_path: Path
    mod_files: list[Path]
    plugin_files: list[Path]


def _resolve_java_requirement(
    mc_version: str, eff_java
) -> tuple[int | None, str]:
    """确定该实例要用的 Java：优先用户显式配置，否则按 MC 版本查 Mojang 推断。

    返回 (preferred_major, preferred_component)。eff_java.preferred_major 非空
    表示用户显式指定过 → 用它；否则尝试从 Mojang 拿该版本官方 Java 要求。
    """
    if eff_java.preferred_major is not None:
        return eff_java.preferred_major, ""
    # 用户没指定 → 从 Mojang 推断该 MC 版本官方 Java
    try:
        req = mojang.fetch_java_requirement(mc_version)
        print(f"[deploy {mc_version}] 该版本官方要求 {req}（未在配置中指定，自动推断）")
        return req.major, req.component
    except Exception as e:  # noqa: BLE001 - 推断失败不阻塞，退回通用逻辑
        print(f"[deploy {mc_version}] 未能从 Mojang 推断 Java 要求（{e}），使用通用探测")
        return None, ""


def deploy_instance(
    cfg: RootConfig, group: InstanceGroup, mc_version: str, dry_run: bool = False
) -> DeployResult:
    """部署单个实例。dry_run=True 时只解析/打印要做什么，不落盘。"""
    paths = instance_paths(mc_version)

    # 0. Java 探测（固定路径）
    eff_java = cfg.effective_java(group)
    pref_major, pref_component = _resolve_java_requirement(mc_version, eff_java)
    java = detect_java(eff_java, pref_major, pref_component)
    print(f"[deploy {mc_version}] 使用 Java: {java.java_exe} (v{java.version})")

    # 1. MCDR 环境
    if not dry_run:
        mcdr.ensure_mcdr_installed(cfg)
        mcdr.init_instance(mc_version)
        mcdr.write_eula_and_properties(mc_version)
        eff_server = cfg.effective_server(group)
        mcdr.write_mcdr_config(mc_version, java.java_exe, cfg, eff_server)
        # 应用端口/RCON 设置（server.properties + MCDR config.yml 同步）
        mcdr.write_server_settings(mc_version, eff_server)

    # 2. fabric server jar
    http = build_modrinth_http(cfg.modrinth, cfg.network)
    artifact = fabric.resolve_server_jar_url(http, mc_version)
    print(
        f"[deploy {mc_version}] Fabric loader {artifact.loader_version} "
        f"installer {artifact.installer_version}"
    )
    jar_path = paths["jar"]
    if not dry_run:
        paths["server_root"].mkdir(parents=True, exist_ok=True)
        fabric.download_server_jar(http, artifact, jar_path)
        print(f"[deploy {mc_version}] 服务器 jar -> {jar_path}")

    # 3. mod
    resolved_mods = mods.resolve_mods(group.mods, mc_version, cfg, dry_run=dry_run)
    mod_files: list[Path] = []
    for rm in resolved_mods:
        tag = "解析" if dry_run else "下载"
        print(f"[deploy {mc_version}] {tag} mod {rm.filename} ({rm.version_label})")
        if not dry_run:
            p = mods.download_mod(rm, paths["mods"], cfg)
            mod_files.append(p)

    # 4. MCDR 插件
    plugin_files: list[Path] = []
    for spec in group.plugins:
        if dry_run:
            print(f"[deploy {mc_version}] 解析 插件 {spec.id} <- {spec.url}")
        else:
            installed = mcdr.install_plugins(mc_version, [spec], http)
            plugin_files.extend(installed)
            for p in installed:
                print(f"[deploy {mc_version}] 下载 插件 {p.name}")

    # 5. 生成前台启动脚本
    if not dry_run:
        scripts = server_mgr.write_start_scripts(mc_version)
        for s in scripts:
            print(f"[deploy {mc_version}] 启动脚本 -> {s}")

    return DeployResult(
        mc_version=mc_version,
        java=java,
        jar_path=jar_path,
        mod_files=mod_files,
        plugin_files=plugin_files,
    )


def deploy_group(cfg: RootConfig, group_id: str | None, dry_run: bool = False) -> list[DeployResult]:
    """部署某组全部实例；group_id 缺省时部署所有组。"""
    groups = [cfg.find_group(group_id)] if group_id else cfg.groups
    results: list[DeployResult] = []
    for g in groups:
        for v in g.instances:
            results.append(deploy_instance(cfg, g, v, dry_run=dry_run))
    return results
