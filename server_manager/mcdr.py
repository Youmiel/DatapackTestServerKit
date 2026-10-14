"""MCDR (MCDReforged) 相关：环境初始化、配置写入、插件安装。

MCDR 通过 pip 装进共享 venv（项目 venv），一次即可；每个服务器实例是
一个自包含 MCDR 根目录（由 `python -m mcdreforged init` 生成），彼此隔离。

插件策略（用户已拍板）：配置里给显式 URL，工具下载后放入实例的 plugins/。
MCDR 启动时会加载 plugins/ 下的插件；也可在运行中 !!MCDR plg reloadall。

插件跨 MC 版本通用：下载统一进共享缓存 .cache/plugins/（一次网络下载），
各实例 deploy 时从缓存复制到自身 plugins/，多版本不重复下载。
失效判定不需要下载：HEAD 探测 ETag/Last-Modified/Content-Length 与缓存记录
比对，指纹变化（或 URL 改变）才重下；不能探测时沿用缓存，不阻塞部署。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import metadata
from .config import PluginSpec, RootConfig
from .http_util import Http, fingerprint_usable, sha1_file
from .paths import PLUGIN_CACHE_ROOT, instance_paths, venv_bin

DEFAULT_JDK_ARGS = [
    "-Xms1G",
    "-Xmx2G",
    "-Dfile.encoding=UTF-8",
    "-Dstdout.encoding=UTF-8",
    "-Dstderr.encoding=UTF-8",
]


def render_start_command(
    java_exe: Path, server_settings=None
) -> str | list[str]:
    """根据 server 配置（含可选模板）生成 MCDR start_command。

    - 未提供模板：返回 exec 模式 list（java + 默认 JVM 参数 + -jar {jar} nogui），
      不经 shell，含空格路径安全（保持原有行为）。
    - 提供模板：渲染占位符 {java}/{jar} 后返回 shell 字符串。
      模板示例：'"{java}" -Xms2G -Xmx4G -XX:+UseG1GC -jar {jar} nogui'
    """
    from .config import ServerSettings

    template = None
    if server_settings is not None:
        template = server_settings.start_command_template

    if template:
        # {java} -> 裸 java 路径（不加引号）。含空格路径请在模板里写成 "{java}"。
        return template.replace("{java}", str(java_exe)).replace(
            "{jar}", "minecraft_server.jar"
        )

    return [
        str(java_exe),
        *DEFAULT_JDK_ARGS,
        "-jar",
        "minecraft_server.jar",
        "nogui",
    ]


def python_exe() -> Path:
    """返回项目 venv 的 python 可执行文件。"""
    return venv_bin(Path(__file__).resolve().parent.parent / "venv") / (
        "python.exe" if __import__("os").name == "nt" else "python"
    )


def ensure_mcdr_installed(cfg: RootConfig) -> None:
    """确保 mcdreforged 已装进共享 venv。幂等。"""
    py = python_exe()
    try:
        out = subprocess.run(
            [str(py), "-m", "mcdreforged", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    spec = cfg.mcdr.get("pip_spec") or "mcdreforged>=2.0"
    subprocess.run(
        [str(py), "-m", "pip", "install", "--upgrade", spec],
        check=True,
    )


def init_instance(mc_version: str) -> Path:
    """在实例根目录生成 MCDR 环境（init）。已存在则跳过。返回实例根。"""
    paths = instance_paths(mc_version)
    root = paths["root"]
    root.mkdir(parents=True, exist_ok=True)
    if not paths["mcdr_config"].exists():
        py = python_exe()
        subprocess.run(
            [str(py), "-m", "mcdreforged", "init"],
            cwd=str(root),
            check=True,
        )
    return root


def write_eula_and_properties(mc_version: str) -> None:
    """预先生成 eula.txt=true 与最小 server.properties（若缺失）。

    端口/RCON 等实际运行设置不在这里写，统一由 write_server_settings 负责
    （该函数可随时冷调用）。
    """
    paths = instance_paths(mc_version)
    srv = paths["server_root"]
    srv.mkdir(parents=True, exist_ok=True)

    eula = paths["eula"]
    if not eula.exists():
        eula.write_text("eula=true\n", encoding="utf-8")

    sp = paths["server_properties"]
    if not sp.exists():
        # 最小属性；fabric server 首次运行会自己补全其余默认值
        sp.write_text(
            "enable-rcon=false\n"
            "level-type=minecraft\\:normal\n"
            "spawn-protection=0\n"
            "online-mode=false\n"
            "view-distance=10\n"
            "motd=Datapack Test Server (" + mc_version + ")\n",
            encoding="utf-8",
        )


def _set_properties_values(lines: list[str], updates: dict[str, str]) -> list[str]:
    """在 server.properties 文本行上应用键值更新：已有键原位改，缺失追加。

    保留注释、顺序及其它行。
    """
    out: list[str] = []
    remaining = dict(updates)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    # 追加缺失键
    for k, v in remaining.items():
        out.append(f"{k}={v}")
    return out


def write_server_settings(
    mc_version: str, server_cfg: "ServerSettings", mcdr_cfg: dict | None = None
) -> None:
    """把端口 / 游戏模式 / RCON 设置写入 server.properties，并同步 MCDR config.yml 的 rcon 段。

    - server.properties：server-port、gamemode、enable-rcon、rcon.port、rcon.password（读改写）
    - MCDR config.yml   ：rcon.enable / rcon.port / rcon.password / rcon.address
      （MCDR 作为 RCON 客户端，需与 server.properties 一致）

    无论实例是否已部署/启动过都可调用（冷修改）。若对应文件不存在则跳过并提示。
    """
    from .config import ServerSettings

    from .paths import instance_paths as _ip

    paths = _ip(mc_version)
    eff = server_cfg.effective()
    r = eff.rcon

    # 1) server.properties
    sp = paths["server_properties"]
    if sp.exists():
        updates = {
            "server-port": str(eff.port),
            "gamemode": str(eff.gamemode),
            "enable-rcon": "true" if r.enable else "false",
            "rcon.port": str(r.port),
        }
        if r.enable:
            updates["rcon.password"] = r.password
        lines = sp.read_text(encoding="utf-8").splitlines()
        new_lines = _set_properties_values(lines, updates)
        sp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(
            f"[{mc_version}] server.properties: port={eff.port}, "
            f"gamemode={eff.gamemode}, rcon={r.enable}"
        )
    else:
        print(f"[{mc_version}] 跳过：server.properties 不存在（先 deploy）")

    # 2) MCDR config.yml 的 rcon 段
    cfg_path = paths["mcdr_config"]
    if cfg_path.exists():
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        rcon = data.get("rcon") if isinstance(data.get("rcon"), dict) else {}
        rcon["enable"] = bool(r.enable)
        rcon["port"] = int(r.port)
        rcon["password"] = r.password
        rcon["address"] = r.address if r.address else "127.0.0.1"
        data["rcon"] = rcon
        cfg_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"[{mc_version}] MCDR config.yml rcon 已同步")
    else:
        print(f"[{mc_version}] 跳过：config.yml 不存在（先 deploy）")


def write_mcdr_config(
    mc_version: str,
    java_exe: Path,
    cfg: RootConfig,
    server_settings=None,
) -> None:
    """改写实例 config.yml：language、start_command、encoding。

    start_command 由 render_start_command 生成：
      - 未配模板：exec mode（list）直接 exec java，不经 shell，含空格路径安全。
      - 配了模板：按模板渲染为 shell 字符串。

    用 PyYAML 读改写（会丢失 MCDR 的注释，但保证字段结构绝对正确；
    缺失的其它字段 MCDR 启动时会自动补默认值）。
    """
    import yaml

    paths = instance_paths(mc_version)
    cfg_path = paths["mcdr_config"]
    lang = cfg.mcdr.get("language", "zh_cn")

    start_command = render_start_command(java_exe, server_settings)

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    data["language"] = lang
    data["working_directory"] = "server"
    data["start_command"] = start_command
    data["handler"] = "vanilla_handler"
    data.setdefault("encoding", "utf8")
    data.setdefault("decoding", "utf8")

    header = (
        "# MCDReforged 配置文件（由 mc-multiversion-deployer 生成）\n"
        "# 缺省字段由 MCDR 首次启动自动补齐。\n"
    )
    cfg_path.write_text(
        header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _plugin_filename(spec: PluginSpec) -> str:
    """从 URL 末段推断插件文件名，失败时退化为 <id>.py。"""
    name = spec.url.rstrip("/").split("/")[-1]
    if not name or "." not in name:
        name = f"{spec.id}.py"
    return name


def _cache_stale(
    cache_file: Path,
    url: str,
    entry: dict | None,
    http: Http,
    verify: bool,
    force: bool,
) -> tuple[bool, str]:
    """判断插件共享缓存是否需要重下。返回 (是否失效, 原因说明)。

    判定不依赖下载：HEAD 取远端指纹（ETag/Last-Modified/Content-Length）
    与缓存索引记录比对；无法探测（无网/服务端不支持 HEAD）时**沿用缓存**，
    避免因网络问题阻塞部署。
    """
    if force:
        return True, "强制重下"
    if not (cache_file.exists() and cache_file.stat().st_size > 0):
        return True, "缓存缺失"
    if not isinstance(entry, dict):
        return True, "无缓存记录"
    if entry.get("url") != url:
        return True, "来源 URL 变化"
    if not verify:
        return False, "校验关闭，沿用缓存"
    local = entry.get("fingerprint") or {}
    remote = http.head_fingerprint(url)
    if remote is None:
        print(f"[网络] 无法探测远端指纹（无网/不支持 HEAD），沿用缓存: {url}")
        return False, "远端指纹不可用"
    if not fingerprint_usable(local):
        return False, "本地无指纹记录"
    if local != remote:
        return True, "远端指纹变化"
    return False, "远端指纹一致"


def install_plugins(
    mc_version: str,
    plugins: list[PluginSpec],
    http: Http,
    force: bool = False,
    verify: bool = True,
) -> list[Path]:
    """把配置的 MCDR 插件（显式 URL）装进实例 plugins/。返回落盘到实例的文件。

    插件跨 MC 版本通用，故统一走**共享缓存** `.cache/plugins/`：
      1) 确保缓存有该文件 —— 已缓存且“不下载即可判定未变”时直接复用；
      2) 从缓存复制到实例 plugins/（实例已有且内容一致时跳过）。
    返回的是实例 plugins/ 下的路径（保持原调用方语义）。
    """
    paths = instance_paths(mc_version)
    plugins_dir = paths["plugins"]
    plugins_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = PLUGIN_CACHE_ROOT
    cache_dir.mkdir(parents=True, exist_ok=True)
    index = metadata.load_cache_index(cache_dir)
    installed: list[Path] = []
    for spec in plugins:
        if not spec.url:
            continue
        name = _plugin_filename(spec)
        cache_file = cache_dir / name
        dest = plugins_dir / name
        entry = index.get(name) if isinstance(index.get(name), dict) else None

        # 1) 共享缓存：不下载即可判定失效
        stale, why = _cache_stale(
            cache_file, spec.url, entry, http, verify=verify, force=force
        )
        if stale:
            print(f"[{mc_version}] 下载插件 -> 共享缓存: {name}（{why}）")
            fingerprint: dict = {}
            http.download(spec.url, cache_file, fingerprint_out=fingerprint)
            entry = {"url": spec.url, "fingerprint": fingerprint}
            index[name] = entry
            metadata.save_cache_index(cache_dir, index)
        else:
            print(f"[{mc_version}] 插件缓存命中: {name}（{why}）")

        # 2) 实例 plugins/：与缓存比对内容（本地重算 sha1，不联网）
        cache_sha1 = sha1_file(cache_file)
        if not force and dest.exists() and dest.stat().st_size > 0:
            if not verify or sha1_file(dest) == cache_sha1:
                print(f"[{mc_version}] 插件已存在，跳过: {name}")
                installed.append(dest)
                continue
            print(f"[{mc_version}] 插件实例内容与缓存不符，重新复制: {name}")
        print(f"[{mc_version}] 插件缓存命中，复制: {name}")
        shutil.copyfile(cache_file, dest)
        installed.append(dest)
    return installed
