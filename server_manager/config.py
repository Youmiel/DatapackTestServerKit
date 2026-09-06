"""配置加载与校验。

读取 config.yaml（或 --config 指定的文件），结构见 config.example.yaml。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(Exception):
    """配置错误。"""


# server.properties 的 gamemode 合法值（写文件用字符串）
GAMEMODES = ("survival", "creative", "adventure", "spectator")
# 默认游戏模式：测试服务器用创造（方便搭/查数据包）
DEFAULT_GAMEMODE = "creative"


@dataclass
class RconSettings:
    # None = 未显式指定（用于组级覆盖时表示“继承顶层”）
    enable: bool | None = None
    port: int | None = None
    password: str | None = None
    address: str | None = None


@dataclass
class ServerSettings:
    """服务器设置：端口、游戏模式、RCON、启动命令模板（MCDR start_command）。"""

    port: int | None = None  # None = 未指定
    # 游戏模式（server.properties gamemode）。None/空 = 未指定 → 用默认 creative
    gamemode: str | None = None
    rcon: RconSettings = field(default_factory=RconSettings)
    # MCDR 启动命令模板。None/空 = 用默认（java -Xms1G -Xmx2G ... -jar {jar} nogui）
    # 支持占位符：{java}（探测到的 java 绝对路径，自动加引号）、{jar}（server jar 文件名）
    start_command_template: str | None = None

    def effective(self) -> "ServerSettings":
        """解析成具体值：未指定的字段补默认值。"""
        port = 25565 if self.port is None else self.port
        gm = self._norm_gamemode(self.gamemode)
        r = RconSettings(
            enable=False if self.rcon.enable is None else self.rcon.enable,
            port=25575 if self.rcon.port is None else self.rcon.port,
            password=self.rcon.password if self.rcon.password else "password",
            address=self.rcon.address if self.rcon.address else "127.0.0.1",
        )
        return ServerSettings(
            port=port,
            gamemode=gm,
            rcon=r,
            start_command_template=self.start_command_template,
        )

    @staticmethod
    def _norm_gamemode(v: str | None) -> str | None:
        """规整 gamemode：数字转名、去空白小写；非法值抛 ConfigError。"""
        if v is None:
            return None
        s = str(v).strip().lower()
        # 兼容数字写法（0=survival 1=creative 2=adventure 3=spectator）
        num_map = {"0": "survival", "1": "creative", "2": "adventure", "3": "spectator"}
        s = num_map.get(s, s)
        if s not in GAMEMODES:
            raise ConfigError(
                f"无效的 gamemode: {v!r}（可选 {', '.join(GAMEMODES)}）"
            )
        return s

    def merged_with(self, base: "ServerSettings") -> "ServerSettings":
        """以 self 覆盖 base：self 未显式提供的字段沿用 base。"""
        port = self.port if self.port is not None else base.port
        gm = self._norm_gamemode(self.gamemode)
        if gm is None:
            gm = self._norm_gamemode(base.gamemode)
        r = RconSettings()
        r.enable = self.rcon.enable if self.rcon.enable is not None else base.rcon.enable
        r.port = self.rcon.port if self.rcon.port is not None else base.rcon.port
        r.password = self.rcon.password if self.rcon.password not in (None, "") else base.rcon.password
        r.address = self.rcon.address if self.rcon.address not in (None, "") else base.rcon.address
        tpl = self.start_command_template if self.start_command_template is not None else base.start_command_template
        return ServerSettings(port=port, gamemode=gm, rcon=r, start_command_template=tpl)


@dataclass
class JavaConfig:
    """Java 探测配置。None / 空 = 未显式指定（组级覆盖时表示继承顶层）。"""

    launcher_roots: list[str] | None = None
    use_env_fallback: bool | None = None
    preferred_major: int | None = None

    def effective(self) -> "JavaConfig":
        return JavaConfig(
            launcher_roots=list(self.launcher_roots or []),
            use_env_fallback=True if self.use_env_fallback is None else self.use_env_fallback,
            preferred_major=self.preferred_major,
        )

    def merged_with(self, base: "JavaConfig") -> "JavaConfig":
        """以 self 覆盖 base：self 未显式提供的字段沿用 base。"""
        roots = self.launcher_roots if self.launcher_roots is not None else base.launcher_roots
        fb = self.use_env_fallback if self.use_env_fallback is not None else base.use_env_fallback
        pm = self.preferred_major if self.preferred_major is not None else base.preferred_major
        return JavaConfig(
            launcher_roots=list(roots or []),
            use_env_fallback=fb,
            preferred_major=pm,
        )


@dataclass
class ModSpec:
    id: str
    source: str = "modrinth"  # modrinth | curseforge
    version: str = ""  # 可选锁定


@dataclass
class PluginSpec:
    id: str
    url: str = ""


@dataclass
class InstanceGroup:
    """一个大版本线（组），组下多个小版本实例共享 mod/插件/java 偏好。"""

    id: str
    instances: list[str]
    mods: list[ModSpec] = field(default_factory=list)
    plugins: list[PluginSpec] = field(default_factory=list)
    java: JavaConfig = field(default_factory=JavaConfig)
    server: ServerSettings | None = None  # 组级可选覆盖


@dataclass
class RootConfig:
    java: JavaConfig
    network: dict
    modrinth: dict
    curseforge: dict
    mcdr: dict
    server: ServerSettings
    groups: list[InstanceGroup]

    def find_group(self, group_id: str) -> InstanceGroup:
        for g in self.groups:
            if g.id == group_id:
                return g
        raise ConfigError(f"未找到版本组: {group_id}")

    def effective_java(self, group: InstanceGroup) -> JavaConfig:
        """合并顶层 java 默认与组级 java 覆盖。

        组级未显式给的字段继承顶层（组级全 None = 完全用顶层）。
        """
        return group.java.merged_with(self.java)

    def effective_server(self, group: InstanceGroup) -> ServerSettings:
        """合并顶层 server 默认与组级 server 覆盖（组级优先）。"""
        if group.server is None:
            return self.server
        return group.server.merged_with(self.server)

    def resolve_group(
        self, group_id: str | None, mc_version: str | None
    ) -> tuple[InstanceGroup, str]:
        """把命令行 group/mc 选择解析成 (组, 具体小版本)。"""
        if group_id:
            g = self.find_group(group_id)
            if mc_version:
                if mc_version not in g.instances:
                    raise ConfigError(
                        f"版本 {mc_version} 不在组 {g.id} 的 instances 中: {g.instances}"
                    )
                return g, mc_version
            if len(g.instances) == 1:
                return g, g.instances[0]
            raise ConfigError(
                f"组 {g.id} 有多个实例 {g.instances}，请用 --version 指定"
            )
        # 未指定组：若全配置只有一个实例，直接用；否则报错列出
        all_inst = [(g, v) for g in self.groups for v in g.instances]
        if len(all_inst) == 1:
            return all_inst[0]
        raise ConfigError(
            "请用 --group 或 --version 指定目标。可用：\n"
            + "\n".join(f"  --group {g.id} --version {v}" for g, v in all_inst)
        )


def _default_network() -> dict:
    return {"timeout": 60, "proxy": "", "verify_sha1": True, "download_retries": 3}


def _default_modrinth() -> dict:
    return {"user_agent": "datapack-tester/0.1 (mc-multiversion-deployer)"}


def _default_curseforge() -> dict:
    return {"api_key_env": "CURSEFORGE_API_KEY"}


def _default_mcdr() -> dict:
    return {"pip_spec": "mcdreforged>=2.0", "language": "zh_cn"}


def _parse_server(raw: Any) -> ServerSettings:
    """解析 server 配置块。未写的字段保持 None（表示未指定）。"""
    raw = raw or {}
    rcon_raw = raw.get("rcon") if isinstance(raw.get("rcon"), dict) else {}
    port = raw.get("port")
    rcon = RconSettings(
        enable=rcon_raw.get("enable") if "enable" in rcon_raw else None,
        port=rcon_raw.get("port") if "port" in rcon_raw else None,
        password=str(rcon_raw["password"]) if "password" in rcon_raw and rcon_raw["password"] not in (None, "") else None,
        address=str(rcon_raw["address"]) if "address" in rcon_raw and rcon_raw["address"] else None,
    )
    gm = None
    if "gamemode" in raw and raw["gamemode"] not in (None, ""):
        gm = ServerSettings._norm_gamemode(raw["gamemode"])
    return ServerSettings(
        port=int(port) if port is not None else None,
        gamemode=gm,
        rcon=rcon,
        start_command_template=(
            str(raw["start_command_template"])
            if "start_command_template" in raw
            and raw["start_command_template"] not in (None, "")
            else None
        ),
    )


def _parse_java(raw: Any) -> JavaConfig:
    """解析 java 块。仅显式出现的键被设置，缺失保持 None（表示“未指定”）。"""
    if not raw:
        return JavaConfig()  # 全 None
    jc = JavaConfig()
    if "launcher_roots" in raw and raw["launcher_roots"] is not None:
        jc.launcher_roots = [str(x) for x in raw["launcher_roots"]]
    if "use_env_fallback" in raw and raw["use_env_fallback"] is not None:
        jc.use_env_fallback = bool(raw["use_env_fallback"])
    pm = raw.get("preferred_major")
    if pm is not None:
        jc.preferred_major = int(pm)
    return jc


def _parse_mods(raw: Any) -> list[ModSpec]:
    out: list[ModSpec] = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(ModSpec(id=item))
            continue
        if isinstance(item, dict):
            src = item.get("source", "modrinth")
            if src not in ("modrinth", "curseforge"):
                raise ConfigError(f"未知 mod 源: {src}（mod {item.get('id')}）")
            out.append(
                ModSpec(
                    id=str(item["id"]),
                    source=src,
                    version=str(item.get("version", "")),
                )
            )
            continue
        raise ConfigError(f"mod 条目格式错误: {item!r}")
    return out


def _parse_plugins(raw: Any) -> list[PluginSpec]:
    out: list[PluginSpec] = []
    for item in raw or []:
        if isinstance(item, str):
            out.append(PluginSpec(id=item))
            continue
        if isinstance(item, dict):
            if "id" not in item:
                raise ConfigError(f"插件条目缺少 id: {item!r}")
            if not item.get("url"):
                raise ConfigError(
                    f"插件 {item.get('id')} 未提供 url（当前采用显式 URL 方式安装插件）"
                )
            out.append(PluginSpec(id=str(item["id"]), url=str(item["url"])))
            continue
        raise ConfigError(f"插件条目格式错误: {item!r}")
    return out


def _parse_groups(raw: Any) -> list[InstanceGroup]:
    groups: list[InstanceGroup] = []
    for item in raw or []:
        if not isinstance(item, dict) or "id" not in item:
            raise ConfigError(f"版本组缺少 id: {item!r}")
        gid = str(item["id"])
        inst = item.get("instances") or []
        if not inst:
            raise ConfigError(f"版本组 {gid} 的 instances 为空")
        groups.append(
            InstanceGroup(
                id=gid,
                instances=[str(x) for x in inst],
                mods=_parse_mods(item.get("mods")),
                plugins=_parse_plugins(item.get("plugins")),
                java=_parse_java(item.get("java")),
                server=_parse_server(item.get("server")),
            )
        )
    # 去重检查组 id
    seen = set()
    for g in groups:
        if g.id in seen:
            raise ConfigError(f"重复的版本组 id: {g.id}")
        seen.add(g.id)
    return groups


def load_config(path: str | Path | None = None) -> RootConfig:
    """加载配置。path 缺省时在当前目录找 config.yaml。"""
    p = Path(path) if path else Path(DEFAULT_CONFIG_NAME)
    if not p.exists():
        raise ConfigError(
            f"找不到配置文件 {p}。请先复制 config.example.yaml 为 {p} 并修改。"
        )
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # 允许顶层缩进写法：既可顶层 java:/network:... 也可包在 project: 下，做归一
    data = data.get("project", data) if isinstance(data, dict) else {}

    mcdr = dict(_default_mcdr())
    if isinstance(data.get("mcdr"), dict):
        mcdr.update(data["mcdr"])

    net = dict(_default_network())
    if isinstance(data.get("network"), dict):
        net.update(data["network"])

    modrinth = dict(_default_modrinth())
    if isinstance(data.get("modrinth"), dict):
        modrinth.update(data["modrinth"])

    curseforge = dict(_default_curseforge())
    if isinstance(data.get("curseforge"), dict):
        curseforge.update(data["curseforge"])

    root = RootConfig(
        java=_parse_java(data.get("java")),
        network=net,
        modrinth=modrinth,
        curseforge=curseforge,
        mcdr=mcdr,
        server=_parse_server(data.get("server")),
        groups=_parse_groups(data.get("version_groups")),
    )
    if not root.groups:
        raise ConfigError("version_groups 为空，请在配置里至少定义一个版本组")
    return root
