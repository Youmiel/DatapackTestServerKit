"""部署编排：把一个版本实例从零装到可运行。

步骤：
  1. 确保 MCDR 已安装（共享 venv）
  2. 探测 Java（固定）
  3. 共享 fabric/loader 依赖库（server/libraries -> .cache/libraries）
  4. 下载 fabric server jar（含 loader，可直接启动）
  5. 初始化 MCDR 实例目录（init）+ 生成 eula/server.properties
  6. 写入 MCDR config.yml（exec start_command）
  7. 安装 fabric mod（共享缓存 .cache/mods/ -> server/mods/）
  8. 安装 MCDR 插件（共享缓存 .cache/plugins/ -> plugins/）
  9. 记录部署元数据（loader 版本 / mod sha1 / 插件指纹）
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import fabric, libraries, mcdr, metadata, mods, mojang, server as server_mgr
from .config import InstanceGroup, RootConfig
from .http_util import build_modrinth_http
from .java import JavaRuntime, detect_java
from .paths import PLUGIN_CACHE_ROOT, instance_paths


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


def _jar_need(force: bool, jar_path: Path, prev: dict, artifact) -> tuple[bool, str]:
    """判断 fabric server jar 是否需重下。返回 (是否重下, 原因)。

    依据实例元数据里记录的 loader / installer 版本：版本变了就重下，
    不再像以前那样只能靠 --force 盲重下。无元数据的历史实例不重下（仅补记）。
    """
    if force:
        return True, "强制重下"
    if not (jar_path.exists() and jar_path.stat().st_size > 0):
        return True, "jar 缺失"
    if not prev:
        return False, "无版本记录，沿用现有 jar"
    if (
        prev.get("loader") != artifact.loader_version
        or prev.get("installer") != artifact.installer_version
    ):
        return (
            True,
            f"loader {prev.get('loader')} -> {artifact.loader_version}",
        )
    return False, "loader 版本与记录一致"


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
    默认（force=False）是幂等/补齐式：已有且“不下载即可判定未变”的资源跳过，
    缺失/变化的才下载 —— 天然支持“部署中断后重跑断点继续”。判定依据：
      - mod：本地重算 sha1 与 Modrinth/CurseForge 给出的 sha1 比对
      - 插件：HEAD 取 ETag/Last-Modified/Content-Length 与缓存记录比对
      - fabric jar：与元数据记录的 loader/installer 版本比对
    """
    paths = instance_paths(mc_version)
    verify = bool(cfg.network.get("verify_sha1", True))
    what = "重新部署" if force else "部署/补齐"

    # 0. Java 探测（固定路径）
    java = _probe_java(cfg, group, mc_version)
    print(f"[deploy {mc_version}] 使用 Java: {java.java_exe} (v{java.version})")

    # 1. MCDR 环境与本地生成文件（eula/properties/config.yml/start.bat）
    if not dry_run:
        mcdr.ensure_mcdr_installed(cfg)
        sync_instance_files(cfg, group, mc_version, java=java)

    # 2. fabric/loader 依赖库共享（junction/symlink；旧实例自动迁移为共享）
    libraries.ensure_shared(mc_version, dry_run=dry_run)

    # 3. fabric server jar（按元数据记录的 loader/installer 版本判定是否重下）
    http = build_modrinth_http(cfg.modrinth, cfg.network)
    artifact = fabric.resolve_server_jar_url(http, mc_version)
    jar_path = paths["jar"]
    meta = metadata.read_deploy_meta(mc_version)
    prev_fabric = meta.get("fabric") if isinstance(meta.get("fabric"), dict) else {}
    jar_needed, jar_why = _jar_need(force, jar_path, prev_fabric, artifact)
    if dry_run:
        print(
            f"[deploy {mc_version}] 解析 fabric server jar (loader {artifact.loader_version}, "
            f"installer {artifact.installer_version})"
        )
    elif jar_needed:
        paths["server_root"].mkdir(parents=True, exist_ok=True)
        print(
            f"[deploy {mc_version}] {what} fabric server jar "
            f"(loader {artifact.loader_version}；{jar_why})"
        )
        fabric.download_server_jar(http, artifact, jar_path, force=True)
        print(f"[deploy {mc_version}] 服务器 jar -> {jar_path}")
    else:
        print(f"[deploy {mc_version}] 服务器 jar 已存在，跳过（{jar_why}）")

    # 4. mod（共享缓存 .cache/mods/ -> 实例 server/mods/）
    resolved_mods = mods.resolve_mods(group.mods, mc_version, cfg, dry_run=dry_run)
    mod_files: list[Path] = []
    for rm in resolved_mods:
        if dry_run:
            print(f"[deploy {mc_version}] 解析 mod {rm.filename} ({rm.version_label})")
            continue
        target, status = mods.install_mod(
            rm, paths["mods"], cfg, force=force, verify=verify
        )
        print(f"[deploy {mc_version}] mod {rm.filename}: {status}")
        mod_files.append(target)

    # 5. MCDR 插件（顶层全局 plugins + 组级覆盖；共享缓存 -> 实例 plugins/）
    plugin_specs = cfg.effective_plugins(group)
    plugin_files: list[Path] = []
    if dry_run:
        for spec in plugin_specs:
            print(f"[deploy {mc_version}] 解析 插件 {spec.id} <- {spec.url}")
    else:
        plugin_files = mcdr.install_plugins(
            mc_version, plugin_specs, http, force=force, verify=verify
        )

    # 6. 记录部署元数据（loader 版本 / mod sha1 / 插件来源指纹）
    if not dry_run:
        meta["fabric"] = {
            "loader": artifact.loader_version,
            "installer": artifact.installer_version,
            "filename": artifact.filename,
        }
        meta["mods"] = {
            rm.filename: {"sha1": (rm.sha1 or "").lower(), "version": rm.version_label}
            for rm in resolved_mods
        }
        plugin_index = metadata.load_cache_index(PLUGIN_CACHE_ROOT)
        meta["plugins"] = {
            p.name: plugin_index[p.name]
            for p in plugin_files
            if isinstance(plugin_index.get(p.name), dict)
        }
        meta["libraries_shared"] = libraries.is_link_like(paths["libraries"])
        metadata.write_deploy_meta(mc_version, meta)

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
