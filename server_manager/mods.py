"""Fabric mod 下载解析：Modrinth + CurseForge 统一入口。

配置项（config.yaml -> version_groups[].mods[]）：
  - id     : Modrinth slug 或 CurseForge 项目名（搜索用）
  - source : "modrinth"（默认）| "curseforge"
  - version: 可选锁定。Modrinth 填 version_id（8位）或精确 version_number；
             CurseForge 填 fileId（数字）。
  未锁定时自动选该 MC 版本 + fabric 下的最新 release。
下载走**共享缓存** `.cache/mods/`：同一文件名只联网下载一次，各实例从缓存复制
（刻意不做硬链接：避免删除/覆盖单实例文件时语义纠缠）。失效判定用 API 给出的
sha1 在本地重算比对 —— 不下载即可知道缓存/实例里的文件是否还是期望内容。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import metadata
from .config import ModSpec, RootConfig
from .http_util import (
    DownloadError,
    Http,
    build_curseforge_http,
    build_modrinth_http,
    sha1_file,
)
from .paths import MOD_CACHE_ROOT

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
        # 服务端过滤：只取该 MC 版本 + fabric 的版本（按发布时间倒序返回）
        params = {
            "game_versions": _json_arr([mc_version]),
            "loaders": _json_arr(["fabric"]),
        }
        # 用户锁定 version：两种写法
        #   - 8 位短 ID（如 uLkEd5dr） → 当作 version_id，直接取该版本
        #   - 其余（version_number，如 0.155.2+26.1.2） → 在过滤列表里精确匹配
        locked = (spec.version or "").strip()
        if locked and _looks_like_version_id(locked):
            data = http.get_json(f"{self.api}/version/{locked}")
            if not isinstance(data, dict) or not data.get("id"):
                raise DownloadError(
                    f"Modrinth 项目 {slug} 找不到版本 id {locked}"
                )
            return self._pick(data, spec, mc_version)

        data = http.get_json(
            f"{self.api}/project/{slug}/version", params=params
        )
        if not isinstance(data, list):
            raise DownloadError(f"Modrinth 项目 {slug} 不存在或不可用")
        if not data:
            raise DownloadError(
                f"Modrinth 项目 {slug} 没有 {mc_version} + fabric 的版本"
            )

        if locked:
            # version_number 精确匹配（忽略大小写），找不到明确报错、不静默降级
            for v in data:
                if v.get("version_number", "").lower() == locked.lower():
                    return self._pick(v, spec, mc_version)
            raise DownloadError(
                f"Modrinth 项目 {slug} 在 {mc_version} + fabric 下找不到版本 "
                f"{locked}。可用版本：\n"
                + "\n".join(f"  - {v.get('version_number')}" for v in data)
            )

        # 未锁定：服务端已按日期倒序 → 优先 release，其次 beta/alpha
        data.sort(
            key=lambda v: (
                v.get("version_type") != "release",
                v.get("date_published", ""),
            )
        )
        return self._pick(data[0], spec, mc_version)

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
            sha1=_cf_hash(chosen, 1),
            version_label=str(chosen.get("displayName", chosen.get("fileName", ""))),
        )


def _json_arr(arr: list[str]) -> str:
    import json

    return json.dumps(arr)


def _looks_like_version_id(s: str) -> bool:
    """Modrinth version_id 是 8 位字母数字短码（如 uLkEd5dr）。

    version_number 通常含 +、.、- 或更长，据此区分两种锁定写法。
    """
    return len(s) == 8 and s.isalnum()


def _is_fabric_cf(file: dict) -> bool:
    # CurseForge file 里 modLoader 相关在 gameVersions 中，如 "Fabric"
    return "Fabric" in file.get("gameVersions", [])


def _cf_hash(file: dict, algo: int) -> str:
    """取 CurseForge file 的指定算法哈希（1=SHA1, 2=MD5）；无则空串。"""
    for h in file.get("hashes") or []:
        if h.get("algo") == algo:
            return str(h.get("value", "")).lower()
    return ""


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


def _http_for(spec: ModSpec, cfg: RootConfig) -> Http:
    if spec.source == "curseforge":
        return build_curseforge_http(cfg.curseforge, cfg.network)
    return build_modrinth_http(cfg.modrinth, cfg.network)


def _record_index(name: str, resolved: ResolvedMod) -> None:
    """把共享缓存条目写入 .cache/mods/_index.json（来源 + 期望 sha1）。"""
    index = metadata.load_cache_index(MOD_CACHE_ROOT)
    entry: dict = {
        "url": resolved.url,
        "sha1": (resolved.sha1 or "").lower(),
    }
    if resolved.version_label:
        entry["version"] = resolved.version_label
    if resolved.spec.source != "modrinth":
        entry["source"] = resolved.spec.source
    index[name] = entry
    metadata.save_cache_index(MOD_CACHE_ROOT, index)


def install_mod(
    resolved: ResolvedMod,
    mods_dir: Path,
    cfg: RootConfig,
    force: bool = False,
    verify: bool = True,
) -> tuple[Path, str]:
    """确保 mod 就位：共享缓存 `.cache/mods/` -> 实例 `mods/`。

    返回 (实例内路径, 状态标签)。共享缓存是唯一联网下载点（同文件跨实例/跨组
    复用）；实例侧从缓存**复制**（不做硬链接，避免删除/覆盖语义纠缠）。

    失效判定均不依赖下载（verify=True 时生效）：
      - 缓存：本地重算 sha1 与 Modrinth/CurseForge 给出的 sha1 比对
      - 实例：同上；不符则用缓存重新复制覆盖（只覆盖同名文件，
        不同文件名的旧版本仍需手动清理）
    verify=False（network.verify_sha1: false）时退化为“存在且非空即复用”。
    """
    name = resolved.filename
    want = (resolved.sha1 or "").lower()
    mods_dir.mkdir(parents=True, exist_ok=True)
    MOD_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    cache_file = MOD_CACHE_ROOT / name
    dest = mods_dir / name

    # 1) 共享缓存
    cache_ok = (
        not force
        and cache_file.exists()
        and cache_file.stat().st_size > 0
        and (not verify or not want or sha1_file(cache_file) == want)
    )
    if cache_ok:
        status = "缓存命中"
    else:
        print(f"[mod] 下载 -> 共享缓存: {name}（{resolved.version_label}）")
        _http_for(resolved.spec, cfg).download(
            resolved.url, cache_file, sha1=want if verify else ""
        )
        _record_index(name, resolved)
        status = "已下载"

    # 2) 实例 mods/
    if not force and dest.exists() and dest.stat().st_size > 0:
        if not verify or not want or sha1_file(dest) == want:
            return dest, "已存在，跳过"
        print(
            f"[mod] {name} 实例内容与期望不一致，用缓存覆盖"
            f"（如同目录下还有该 mod 的其它版本文件，请手动删除）"
        )
    shutil.copyfile(cache_file, dest)
    return dest, f"{status} -> 复制"
