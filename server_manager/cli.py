"""CLI 入口：mcserver <子命令>

子命令：
  deploy [--group ID] [--version V] [--dry-run] [--force]  部署（下载 fabric/mod/插件、生成配置）
                                                              --force: 强制重下全部（覆盖已有）
                                                              默认幂等/补齐：已有跳过、缺失下载（断点续部署）
  list                                            列出配置中的版本组与实例
  status [--version V]                            查看各实例部署状态与启动指引
  start  [--version V] [--update]                 前台透传运行 MCDR（等同进目录跑）；
                                                  --update: 先就地刷新启动命令/端口（不同端口，同步配置变更）再启动
  configure-server [--group ID] [--version V]     把 server 块属性（端口/游戏模式/RCON）配置到已部署实例（冷改）
                                                  （别名：set-ports / set-server，均可直接用）

启动服务器：工具不托管进程，deploy 后在实例目录生成 start.bat/start.sh，
双击或 `mcserver start --version X` 前台运行 MCDR 交互控制台。
"""
from __future__ import annotations

import argparse
import sys

from . import server as server_mgr
from .config import ConfigError, InstanceGroup, load_config
from .deploy import deploy_group
from .paths import DEFAULT_CONFIG_PATH

CONFIG_ARG = "--config"


def _load(args) -> object:
    cfg_path = getattr(args, "config", None) or getattr(args, "config_dup", None)
    return load_config(cfg_path or DEFAULT_CONFIG_PATH)


def _parse_target(args) -> tuple:
    """返回 (group_id, mc_version)。"""
    return getattr(args, "group", None), getattr(args, "version", None)


def cmd_deploy(cfg, args) -> int:
    from . import deploy as deploy_mod

    group_id, mc_version = _parse_target(args)
    if mc_version and not group_id:
        # 允许直接 --version 定位到唯一含该版本的组
        for g in cfg.groups:
            if mc_version in g.instances:
                group_id = g.id
                break
        else:
            raise ConfigError(f"配置里没有版本 {mc_version}")

    force = bool(getattr(args, "force", False))
    # 若指定了 --version 且 group 存在，部署单个；否则按组
    if group_id and mc_version:
        g = cfg.find_group(group_id)
        deploy_mod.deploy_instance(
            cfg, g, mc_version, dry_run=args.dry_run, force=force
        )
    else:
        deploy_group(cfg, group_id, dry_run=args.dry_run, force=force)
    return 0


def cmd_list(cfg, args) -> int:
    print("配置的版本组：")
    for g in cfg.groups:
        mods_n = len(g.mods)
        plg_n = len(g.plugins)
        print(f"  组 {g.id}: {', '.join(g.instances)}")
        print(f"      mods={mods_n} 插件={plg_n}  Java偏好={g.java.preferred_major}")
        for m in g.mods:
            print(f"        - [{m.source}] {m.id}")
        for p in g.plugins:
            print(f"        - 插件 {p.id}  {p.url}")
    return 0


def cmd_status(cfg, args) -> int:
    """展示各实例部署情况与启动指引（工具不托管进程，无法探测运行态）。"""
    from .paths import instance_paths

    target_list: list[tuple] = []
    if getattr(args, "version", None):
        gid = next(
            (g.id for g in cfg.groups if args.version in g.instances), None
        )
        if not gid:
            raise ConfigError(f"配置里没有版本 {args.version}")
        target_list = [(gid, args.version)]
    else:
        target_list = [(g.id, v) for g in cfg.groups for v in g.instances]

    for gid, ver in target_list:
        p = instance_paths(ver)
        deployed = p["jar"].exists() and p["mcdr_config"].exists()
        state = "已部署" if deployed else "未部署"
        print(f"  {ver:12} {state}")
        if deployed:
            print(f"             启动: 双击 {p['root'] / 'start.bat'} 或 "
                  f"`mcserver start --version {ver}`")
        else:
            print(f"             运行 deploy --version {ver} 部署")
    return 0


def cmd_start(cfg, args) -> int:
    """前台启动实例。--update 时先就地刷新启动命令/端口（同端口，同步配置变更）再启动。"""
    gid, ver = _parse_target(args)
    _, ver = _resolve(cfg, gid, ver)
    if getattr(args, "update", False):
        from .deploy import sync_instance_files

        # _resolve 已把 (gid, ver) 解出来，这里按组定位以取组级 server 覆盖
        g = cfg.find_group(gid) if gid else _find_group_by_version(cfg, ver)
        _require_deployed_files(cfg, ver)  # 明确提示缺 config.yml 时先 deploy
        print(f"[start] 就地同步 {ver} 启动配置（config.yml / 端口 / start.bat）...")
        sync_instance_files(cfg, g, ver)
        print(f"[start] 启动命令/端口已按当前配置更新完毕，开始启动")
    return server_mgr.run_foreground(ver)


def _find_group_by_version(cfg, ver) -> InstanceGroup:
    for g in cfg.groups:
        if ver in g.instances:
            return g
    raise ConfigError(f"配置里没有版本 {ver}")


def _require_deployed_files(cfg, ver) -> None:
    """--update 前检查实例是否已部署（缺 config.yml 提示先 deploy）。"""
    from .paths import instance_paths

    if not instance_paths(ver)["mcdr_config"].exists():
        raise FileNotFoundError(
            f"[{ver}] 尚未部署（缺 config.yml）。请先运行 deploy，或用 "
            f"`mcserver deploy --version {ver}` 完成首次部署后再 start --update。"
        )


def _iter_targets(cfg, args) -> list[tuple]:
    """解析目标实例列表（(group, mc_version)）。缺省 = 全部组全部实例。"""
    from .paths import instance_paths

    group_id, mc_version = _parse_target(args)
    if group_id:
        g = cfg.find_group(group_id)
        vers = [mc_version] if mc_version else g.instances
        return [(g, v) for v in vers]
    if mc_version:
        for g in cfg.groups:
            if mc_version in g.instances:
                return [(g, mc_version)]
        raise ConfigError(f"配置里没有版本 {mc_version}")
    return [(g, v) for g in cfg.groups for v in g.instances]


def cmd_configure_server(cfg, args) -> int:
    """把 server 块属性（端口 / 游戏模式 / RCON）配置到实例（部署后冷改）。

    子命令 configure-server（别名 set-ports / set-server）。
    数据来源：配置里的 server 块 + 命令行参数覆盖（命令行优先）。
    """
    from .config import RconSettings, ServerSettings
    from . import mcdr as mcdr_mod

    targets = _iter_targets(cfg, args)

    # 命令行覆盖 -> 构造一个“覆盖层” ServerSettings（未给的字段保持 None）
    over = ServerSettings()
    rcon_over = RconSettings()
    if args.port is not None:
        over.port = args.port
    if args.gamemode:
        over.gamemode = args.gamemode
    if args.rcon_port is not None:
        rcon_over.port = args.rcon_port
    if args.rcon_password:
        rcon_over.password = args.rcon_password
    if args.rcon_address:
        rcon_over.address = args.rcon_address
    if args.rcon_enable:
        rcon_over.enable = True
    elif args.rcon_disable:
        rcon_over.enable = False
    over.rcon = rcon_over

    changed = 0
    for g, ver in targets:
        base = cfg.effective_server(g)      # 配置值（顶层+组级合并）
        final = over.merged_with(base)      # 命令行覆盖配置
        # 应用到已部署实例（无 server.properties/config.yml 会提示跳过）
        mcdr_mod.write_server_settings(ver, final)
        changed += 1
    print(f"已对 {changed} 个实例应用端口/游戏模式/RCON 设置")
    return 0


def _resolve(cfg, group_id, mc_version):
    """解析 (组, 版本)。支持：只给版本时自动定位到含它的组。"""
    if mc_version and not group_id:
        for g in cfg.groups:
            if mc_version in g.instances:
                group_id = g.id
                break
        else:
            raise ConfigError(f"配置里没有版本 {mc_version}")
    return cfg.resolve_group(group_id, mc_version)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcserver",
        description="Minecraft 多版本测试服务器部署管理工具（fabric + MCDR）",
    )
    # 全局 --config：放在子命令前或后都可识别
    p.add_argument(CONFIG_ARG, help="配置文件路径（默认 ./config.yaml）")
    sub = p.add_subparsers(dest="command", required=True)

    def add_target(sp, need_version=False):
        sp.add_argument("--group", help="版本组 id")
        sp.add_argument("--version", help="MC 小版本，如 1.21.1")

    sp = sub.add_parser("deploy", help="部署版本实例（默认幂等补齐；--force 强制重下）")
    sp.add_argument("--config", dest="config_dup", help=argparse.SUPPRESS)
    add_target(sp)
    sp.add_argument("--dry-run", action="store_true", help="只解析，不实际下载/落盘")
    sp.add_argument("--force", action="store_true", help="强制重新部署：重下全部资源并覆盖已有文件")
    sp.set_defaults(func=cmd_deploy)

    sp = sub.add_parser("list", help="列出配置的版本组")
    sp.add_argument("--config", dest="config_dup", help=argparse.SUPPRESS)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("status", help="查询实例运行状态")
    sp.add_argument("--config", dest="config_dup", help=argparse.SUPPRESS)
    sp.add_argument("--version")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("start", help="前台启动实例（--update 先就地更新启动命令再启动）")
    sp.add_argument("--config", dest="config_dup", help=argparse.SUPPRESS)
    add_target(sp)
    sp.add_argument(
        "--update",
        action="store_true",
        help="启动前就地刷新启动配置（config.yml 启动命令/端口/RCON、start.bat），"
        "同步配置变更（同端口更新），不下载任何资源",
    )
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser(
        "configure-server",
        aliases=["set-ports", "set-server"],
        help="把 server 块属性（端口/游戏模式/RCON）配置到实例（冷改，部署后也可用）。"
        "值来自配置 server 块 + 命令行覆盖。别名 set-ports / set-server 同样可用",
    )
    sp.add_argument("--config", dest="config_dup", help=argparse.SUPPRESS)
    add_target(sp)
    sp.add_argument("--port", type=int, help="游戏端口（覆盖配置）")
    sp.add_argument(
        "--gamemode",
        help="游戏模式（覆盖配置）：survival/creative/adventure/spectator（或 0-3）",
    )
    sp.add_argument("--rcon-enable", dest="rcon_enable", action="store_true", help="启用 RCON")
    sp.add_argument("--rcon-disable", dest="rcon_disable", action="store_true", help="禁用 RCON")
    sp.add_argument("--rcon-port", dest="rcon_port", type=int, help="RCON 端口（覆盖配置）")
    sp.add_argument("--rcon-password", dest="rcon_password", help="RCON 密码（覆盖配置）")
    sp.add_argument("--rcon-address", dest="rcon_address", help="RCON 地址（默认 127.0.0.1）")
    sp.set_defaults(func=cmd_configure_server)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # 支持把 --config 放在子命令后：合并到全局
    if getattr(args, "config_dup", None) and not getattr(args, "config", None):
        args.config = args.config_dup
    try:
        cfg = _load(args)
        return args.func(cfg, args)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 2
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
