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


def sync_instance_files(
    cfg: RootConfig, group: InstanceGroup, mc_version: str, java=None
) -> None:
    """同步某实例的“本地生成文件”到当前配置（就地更新/补齐，不下载网络资源）。

    供 `start --update`（启动前刷新启动命令）与 `deploy`（部署时写入）复用：
      - MCDR 初始化（config.yml 缺失时 init）
      - eula.txt / server.properties（缺失时生成）
      - MCDR config.yml（language / start_command / encoding）——含启动命令模板变更
      - server.properties + config.yml 的端口 / RCON（与配置保持一致）
      - start.bat / start.sh
    """
    mcdr.init_instance(mc_version)
    mcdr.write_eula_and_properties(mc_version)
    eff_server = cfg.effective_server(group)
    if java is None:
        java = _probe_java(cfg, group, mc_version)
    mcdr.write_mcdr_config(mc_version, java.java_exe, cfg, eff_server)
    mcdr.write_server_settings(mc_version, eff_server)
    for s in server_mgr.write_start_scripts(mc_version):
        print(f"[deploy {mc_version}] 启动脚本 -> {s}")


def _probe_java(cfg: RootConfig, group: InstanceGroup, mc_version: str):
    """探测实例所用 Java（自动推断失败时从 config.yml 读回已固定路径）。"""
    eff_java = cfg.effective_java(group)
    pref_major, pref_component = _resolve_java_requirement(mc_version, eff_java)
    try:
        return detect_java(eff_java, pref_major, pref_component)
    except RuntimeError:
        # 兼容“部署后 launcher_roots 失效”等场景：读回 config.yml 里固定过的 java
        import yaml

        cfg_path = instance_paths(mc_version)["mcdr_config"]
        if cfg_path.exists():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            sc = data.get("start_command") or []
            if isinstance(sc, list) and sc:
                return type("_StubJava", (), {"java_exe": Path(sc[0])})()
        raise


def deploy_instance(
    cfg: RootConfig,
    group: InstanceGroup,
    mc_version: str,
    dry_run: bool = False,
    force: bool = False,
) -> DeployResult:
    """部署单个实例。

    dry_run=True：只解析/打印要做什么，不落盘。
    force=True：无条件重下 jar/mod/插件（覆盖已有），用于 `--force` 重新部署。
    默认（force=False）是幂等/补齐式：已有非空文件跳过、缺失才下载 —— 天然
    支持“部署中断后重跑断点继续”，也适合配置更新后补齐新 mod/插件。
    """
    paths = instance_paths(mc_version)
    deploy_tag = f"强制" if force else "补齐"
    what = "重新部署" if force else "部署/补齐"

    # 0. Java 探测（固定路径）
    java = _probe_java(cfg, group, mc_version)
    print(f"[deploy {mc_version}] 使用 Java: {java.java_exe} (v{java.version})")

    # 1. MCDR 环境与本地生成文件（eula/properties/config.yml/start.bat）
    if not dry_run:
        mcdr.ensure_mcdr_installed(cfg)
        sync_instance_files(cfg, group, mc_version, java=java)

    # 2. fabric server jar
    http = build_modrinth_http(cfg.modrinth, cfg.network)
    artifact = fabric.resolve_server_jar_url(http, mc_version)
    jar_path = paths["jar"]
    jar_needed = force or not (
        jar_path.exists() and jar_path.stat().st_size > 0
    )
    if dry_run:
        print(
            f"[deploy {mc_version}] 解析 fabric server jar (loader {artifact.loader_version}, "
            f"installer {artifact.installer_version})"
        )
    elif jar_needed:
        paths["server_root"].mkdir(parents=True, exist_ok=True)
        print(
            f"[deploy {mc_version}] {what} fabric server jar "
            f"(loader {artifact.loader_version})"
        )
        fabric.download_server_jar(http, artifact, jar_path, force=force)
        print(f"[deploy {mc_version}] 服务器 jar -> {jar_path}")
    else:
        print(f"[deploy {mc_version}] 服务器 jar 已存在，跳过")

    # 3. mod
    resolved_mods = mods.resolve_mods(group.mods, mc_version, cfg, dry_run=dry_run)
    mod_files: list[Path] = []
    for rm in resolved_mods:
        if dry_run:
            print(f"[deploy {mc_version}] 解析 mod {rm.filename} ({rm.version_label})")
            continue
        target = paths["mods"] / rm.filename
        if not force and target.exists() and target.stat().st_size > 0:
            print(f"[deploy {mc_version}] mod 已存在，跳过: {rm.filename}")
            mod_files.append(target)
            continue
        print(f"[deploy {mc_version}] {deploy_tag}下载 mod {rm.filename} ({rm.version_label})")
        mod_files.append(mods.download_mod(rm, paths["mods"], cfg, force=force))

    # 4. MCDR 插件
    plugin_files: list[Path] = []
    for spec in group.plugins:
        if dry_run:
            print(f"[deploy {mc_version}] 解析 插件 {spec.id} <- {spec.url}")
            continue
        installed = mcdr.install_plugins(mc_version, [spec], http, force=force)
        for p in installed:
            plugin_files.append(p)
            if force or not (p.exists() and p.stat().st_size > 0):
                print(f"[deploy {mc_version}] {deploy_tag}下载 插件 {p.name}")
            else:
                print(f"[deploy {mc_version}] 插件已存在，跳过: {p.name}")

    return DeployResult(
        mc_version=mc_version,
        java=java,
        jar_path=jar_path,
        mod_files=mod_files,
        plugin_files=plugin_files,
    )


def deploy_group(
    cfg: RootConfig,
    group_id: str | None,
    dry_run: bool = False,
    force: bool = False,
) -> list[DeployResult]:
    """部署某组全部实例；group_id 缺省时部署所有组。"""
    groups = [cfg.find_group(group_id)] if group_id else cfg.groups
    results: list[DeployResult] = []
    for g in groups:
        for v in g.instances:
            results.append(
                deploy_instance(cfg, g, v, dry_run=dry_run, force=force)
            )
    return results
