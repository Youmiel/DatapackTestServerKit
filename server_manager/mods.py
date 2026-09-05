"""Fabric mod 下载解析：Modrinth + CurseForge 统一入口。

配置项（config.yaml -> version_groups[].mods[]）：
  - id     : Modrinth slug 或 CurseForge 项目名（搜索用）
  - source : "modrinth"（默认）| "curseforge"
  - version: 可选锁定。Modrinth 填 version_id（8位）或精确 version_number；
             CurseForge 填 fileId（数字）。
  未锁定时自动选该 MC 版本 + fabric 下的最新 release。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import ModSpec, RootConfig
from .http_util import DownloadError, Http, build_curseforge_http, build_modrinth_http

GAME_ID = 432  # Minecraft
CLASS_MODS = 6  # CurseForge "Minecraft Mods" class
MODLOADER_FABRIC_CF = 4  # CurseForge modLoaderType Fabric = 4


@dataclass
class ResolvedMod:
    """解析出的一个待下载 mod 文件。"""

    spec: ModSpec
    filename: str
    url: str
    sha1: str = ""
    # 用于展示
    version_label: str = ""


class ModSource:
    name = "base"

    def resolve(
        self, spec: ModSpec, mc_version: str, http: Http
    ) -> ResolvedMod:
        raise NotImplementedError


class ModrinthSource(ModSource):
    name = "modrinth"
    api = "https://api.modrinth.com/v2"

    def resolve(self, spec, mc_version, http):
        slug = spec.id
        params = {
            "game_versions": _json_arr([mc_version]),
            "loaders": _json_arr(["fabric"]),
        }
        # 若用户给了精确 version_id / version_number，先尝试直接取该版本
        if spec.version and len(spec.version) >= 8:
            try:
                data = http.get_json(f"{self.api}/version/{spec.version}")
                if isinstance(data, dict) and data.get("id"):
                    return self._pick(data, spec, mc_version)
            except DownloadError:
                pass  # 回退到列表查询
        data = http.get_json(f"{self.api}/project/{slug}/version")
        if not isinstance(data, list):
            raise DownloadError(f"Modrinth 项目 {slug} 不存在或不可用")
        # 服务端过滤：仅保留匹配 mc 版本 + fabric + release 优先
        cands = [
            v
            for v in data
            if _match_game(v, mc_version)
            and _match_loader(v, "fabric")
        ]
        if not cands:
            raise DownloadError(
                f"Modrinth 项目 {slug} 没有 {mc_version} + fabric 的版本"
            )
        # 选稳定版优先，其次 beta/alpha
        cands.sort(key=lambda v: (v.get("version_type") != "release", v.get("date_published", "")))
        chosen = cands[0]
        return self._pick(chosen, spec, mc_version)

    def _pick(self, version: dict, spec: ModSpec, mc_version: str) -> ResolvedMod:
        # 优先选主文件（primary），且文件名不能是 sources jar
        files = [f for f in version.get("files", []) if not f["filename"].endswith("-sources.jar")]
        if not files:
            raise DownloadError(f"Modrinth 版本 {version.get('version_number')} 无可用文件")
        f = files[0]
        return ResolvedMod(
            spec=spec,
            filename=f["filename"],
            url=f["url"],
            sha1=f.get("hashes", {}).get("sha1", ""),
            version_label=version.get("version_number", ""),
        )


class CurseForgeSource(ModSource):
    name = "curseforge"
    api = "https://api.curseforge.com/v1"

    def resolve(self, spec, mc_version, http):
        slug = spec.id
        # 1) search 找 modId
        data = http.get_json(
            f"{self.api}/mods/search",
            params={
                "gameId": GAME_ID,
                "classId": CLASS_MODS,
                "slug": slug,
                "sortField": 1,  # 按受欢迎度
            },
        )
        items = (data or {}).get("data", [])
        if not items:
            raise DownloadError(f"CurseForge 搜不到项目 {slug}")
        mod_id = items[0]["id"]
        # 2) 拿该 mod 的全部文件
        files_data = http.get_json(f"{self.api}/mods/{mod_id}/files")
        files = (files_data or {}).get("data", [])
        # 3) 过滤 mc 版本 + fabric
        cands = [
            f
            for f in files
            if mc_version in f.get("gameVersions", [])
            and _is_fabric_cf(f)
        ]
        if not cands:
            raise DownloadError(
                f"CurseForge mod {slug}(id={mod_id}) 没有 {mc_version}+fabric 的文件"
            )
        # files 端点按日期倒序返回（最新在前）
        chosen = cands[0]
        return ResolvedMod(
            spec=spec,
            filename=chosen["fileName"],
            url=chosen["downloadUrl"]
            or f"https://www.curseforge.com/minecraft/mc-mods/{slug}/download/{chosen['id']}/file",
            sha1=str(chosen.get("hashes", [{}])[0].get("value", "")) if chosen.get("hashes") else "",
            version_label=str(chosen.get("displayName", chosen.get("fileName", ""))),
        )


def _json_arr(arr: list[str]) -> str:
    import json

    return json.dumps(arr)


def _match_game(version: dict, mc_version: str) -> bool:
    return mc_version in version.get("game_versions", [])


def _match_loader(version: dict, loader: str) -> bool:
    return loader in version.get("loaders", [])


def _is_fabric_cf(file: dict) -> bool:
    # CurseForge file 里 modLoader 相关在 gameVersions 中，如 "Fabric"
    return "Fabric" in file.get("gameVersions", [])


_SOURCES: dict[str, ModSource] = {
    "modrinth": ModrinthSource(),
    "curseforge": CurseForgeSource(),
}


def resolve_mods(
    specs: list[ModSpec],
    mc_version: str,
    cfg: RootConfig,
    dry_run: bool = False,
) -> list[ResolvedMod]:
    """解析一组 mod 配置为具体下载文件。网络只读查询，不落盘。"""
    modrinth_http = build_modrinth_http(cfg.modrinth, cfg.network)
    cf_http = None
    out: list[ResolvedMod] = []
    for spec in specs:
        if spec.source == "curseforge":
            if cf_http is None:
                cf_http = build_curseforge_http(cfg.curseforge, cfg.network)
            http = cf_http
        else:
            http = modrinth_http
        src = _SOURCES.get(spec.source)
        if src is None:
            raise DownloadError(f"未知 mod 源 {spec.source}")
        out.append(src.resolve(spec, mc_version, http))
    return out


def download_mod(resolved: ResolvedMod, mods_dir: Path, cfg: RootConfig) -> Path:
    """把已解析的 mod 下载到 mods 目录（带 sha1 校验）。"""
    mods_dir.mkdir(parents=True, exist_ok=True)
    dest = mods_dir / resolved.filename
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    http = (
        build_curseforge_http(cfg.curseforge, cfg.network)
        if resolved.spec.source == "curseforge"
        else build_modrinth_http(cfg.modrinth, cfg.network)
    )
    return http.download(resolved.url, dest, sha1=resolved.sha1)
