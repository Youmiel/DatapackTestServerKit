"""fabric/loader 依赖库（`server/libraries`）的跨实例共享。

做法：各实例的 `server/libraries` 做成指向共享缓存 `.cache/libraries` 的
**目录链接**（Windows junction / POSIX symlink）。各 MC 版本的库按 Maven 布局
（`group/artifact/version/artifact-version.jar`）叠加共存，互不冲突 —— 实测
20 个实例合计约 790MB 可压到约 243MB。

为什么只能用链接：fabric 的 server jar 实为 installer bootstrap，其“已安装”
判定只检查 `.fabric/server/fabric-loader-*.jar` 的 manifest Class-Path 所列文件
**是否存在**（不校验），且库目录路径在 ServerInstaller 里硬编码为 `cwd/libraries`，
没有任何可配置入口。因此无法像 mod / 插件那样“下载到缓存再复制”，只能让实例
目录本身指向共享存储。

附带收益：fabric 一旦判定“安装不完整”而全量重装，写入会经链接落到共享目录，
其它实例随后即可直接复用。

注意：Windows junction 在 `shutil.rmtree` 下会**跟随删除目标内容**，所以删除
实例目录前必须先 `remove_link()`（本模块提供）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .paths import LIBS_CACHE_ROOT, instance_paths

# Windows reparse point 类型：junction（目录挂载点）
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003


def is_link_like(path: Path) -> bool:
    """path 是否为目录链接（POSIX symlink 或 Windows junction/symlink）。"""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return False
    if os.name != "nt":
        return False
    try:
        # 必须用 lstat：stat 会跟随链接，拿到的就不是 reparse 信息了
        return os.lstat(path).st_reparse_tag == IO_REPARSE_TAG_MOUNT_POINT
    except (OSError, AttributeError):
        return False


def link_target(path: Path) -> Path | None:
    """返回链接指向的绝对路径；非链接或读取失败返回 None。"""
    if not is_link_like(path):
        return None
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    # Windows 下 readlink 可能带 \\?\ 或 \??\ 前缀（NT 对象路径）
    text = raw
    for prefix in ("\\\\?\\", "\\??\\"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    p = Path(text)
    return p if p.is_absolute() else (path.parent / p).resolve()


def remove_link(path: Path) -> None:
    """只移除链接本身，不触碰目标内容。"""
    if os.name == "nt":
        os.rmdir(path)  # junction / 目录符号链接用 rmdir 移除
    else:
        os.unlink(path)


def create_link(link: Path, target: Path) -> None:
    """创建目录链接：Windows 用 junction（无需管理员权限），其它平台用 symlink。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
    else:
        os.symlink(target, link, target_is_directory=True)


def _merge_into_cache(src: Path, cache: Path) -> tuple[int, int]:
    """把 src 目录树合并进共享缓存：同相对路径且大小相同视为已有（跳过），

    其余**移动**过去（覆盖同名不同大小的文件）。返回 (移动数, 复用数)。
    """
    moved = skipped = 0
    files = [f for f in src.rglob("*") if f.is_file()]
    for f in files:
        rel = f.relative_to(src)
        target = cache / rel
        if target.exists() and target.stat().st_size == f.stat().st_size:
            skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(f, target)
        except OSError:
            # 跨盘等场景退回“复制 + 删除”
            shutil.copyfile(f, target)
            f.unlink()
        moved += 1
    return moved, skipped


def ensure_shared(
    mc_version: str, migrate: bool = True, dry_run: bool = False
) -> str:
    """确保实例 `server/libraries` 指向共享缓存。返回状态说明字符串。

    migrate=True：遇到真实目录（旧实例）时把内容**移动合并**进共享缓存，
    再替换为链接；migrate=False 则原样跳过。
    """
    libs: Path = instance_paths(mc_version)["libraries"]
    cache: Path = LIBS_CACHE_ROOT

    if is_link_like(libs):
        current = link_target(libs)
        if current is not None and current == cache.resolve():
            return "已共享"
        print(f"[{mc_version}] libraries 链接指向异常（{current}），重建")
        if not dry_run:
            remove_link(libs)
    elif libs.exists():
        if not migrate:
            return "跳过（本地目录，未迁移）"
        if dry_run:
            n = sum(1 for f in libs.rglob("*") if f.is_file())
            print(
                f"[{mc_version}] dry-run：将把 libraries/ 中 {n} 个文件合并进"
                f"共享缓存（{cache}）并改为链接"
            )
            return "待迁移"
        moved, skipped = _merge_into_cache(libs, cache)
        print(
            f"[{mc_version}] libraries 合并入共享缓存：移动 {moved}、复用已有 {skipped}"
        )
        shutil.rmtree(libs)

    if dry_run:
        print(f"[{mc_version}] dry-run：将创建 libraries -> {cache} 链接")
        return "待创建链接"
    create_link(libs, cache)
    print(f"[{mc_version}] libraries 已共享（链接 -> {cache}）")
    return "已共享"
