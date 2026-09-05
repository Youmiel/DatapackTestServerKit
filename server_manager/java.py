"""Java 运行时探测。

候选来源（deploy 时探测一次，固定路径）：
  1. config.java.launcher_roots 下各 MC 官方启动器 runtime 目录（java-runtime-*）
  2. 环境变量 JAVA_HOME（use_env_fallback=true 时）
  3. 系统 PATH 中的 java（use_env_fallback=true 时）

选择逻辑：
  - 若给定 component（如 java-runtime-delta，来自 Mojang version json），
    在 launcher_roots 中优先精确定位同名官方运行时；
  - 否则按 major 匹配（配置里显式 preferred_major 或 Mojang 推断的 major）；
  - 找不到匹配 major 时抛错列出候选，不默默用错版本。
"""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import JavaConfig


@dataclass
class JavaRuntime:
    java_exe: Path  # 可执行文件绝对路径
    version: str  # 如 "21.0.3"
    major: int  # 如 21
    component: str = ""  # 官方运行时组件名（如 java-runtime-delta），非官启来源为空


def _find_in_launcher_root(root: Path) -> list[tuple[Path, str]]:
    """在 MC 启动器 runtime 目录里找**官方运行时** java。

    返回 [(java_exe, 组件名), ...]。只收集名字以 java-runtime- 开头的官方
    运行时（其它 jre/jdk 目录是遗留，不用）。兼容布局：
      A) 平铺：runtime/java-runtime-xxx/bin/java(.exe)
      B) 嵌套：runtime/java-runtime-xxx/<os>-<arch>/java-runtime-xxx/bin/java(.exe)
    root 参数：可直接是 runtime 目录，或含 runtime/ 子目录的启动器根。
    """
    found: list[tuple[Path, str]] = []
    exe = "java.exe" if os.name == "nt" else "java"
    if (root / "runtime").is_dir():
        base = root / "runtime"
    else:
        base = root

    try:
        entries = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return found
    for entry in entries:
        if not entry.name.startswith("java-runtime-"):
            continue
        component = entry.name  # 如 java-runtime-delta
        patterns = [
            entry / "bin" / exe,
            entry / "*" / "*" / "bin" / exe,  # <os>-<arch>/java-runtime-xxx/bin
            entry / "*" / "*" / "*" / "bin" / exe,
        ]
        for pattern in patterns:
            for p in glob.glob(str(pattern)):
                p = Path(p)
                if not any(existing == p for existing, _ in found):
                    found.append((p, component))
    return found


def _detect_version(java_exe: Path) -> tuple[str, int] | None:
    try:
        out = subprocess.run(
            [str(java_exe), "-version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        text = (out.stderr or "") + (out.stdout or "")
        m = re.search(r'(?:version|openjdk version) "([^"]+)"', text)
        if not m:
            return None
        ver = m.group(1)
        major_s = re.match(r"(\d+)", ver)
        major = int(major_s.group(1)) if major_s else 0
        return ver, major
    except (OSError, subprocess.TimeoutExpired):
        return None


def _candidates(java: JavaConfig) -> list[JavaRuntime]:
    """收集所有候选 Java 并探测版本，返回 JavaRuntime 列表（含 component）。"""
    java = java.effective()  # 补默认（None -> []/True）
    raw: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def add(p: Path, component: str = ""):
        p = p.resolve()
        if p not in seen:
            seen.add(p)
            raw.append((p, component))

    # 1) 启动器目录（官方运行时带 component 名）
    for root_str in java.launcher_roots:
        root = Path(root_str)
        if root.exists():
            for p, component in _find_in_launcher_root(root):
                add(p, component)
    # 2) JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    if jh and java.use_env_fallback:
        jh_path = Path(jh)
        if jh_path.exists():
            add(jh_path / "bin" / ("java.exe" if os.name == "nt" else "java"))
    # 3) PATH
    if java.use_env_fallback:
        which = shutil.which("java")
        if which:
            add(Path(which))

    runtimes: list[JavaRuntime] = []
    for exe, component in raw:
        ver = _detect_version(exe)
        if ver:
            runtimes.append(JavaRuntime(exe, ver[0], ver[1], component))
    return runtimes


def _fmt_candidates(runtimes: list[JavaRuntime]) -> str:
    return "\n".join(
        f"    - {r.java_exe}  (Java {r.major}"
        + (f", {r.component})" if r.component else ")")
        for r in sorted(runtimes, key=lambda r: (r.major, r.version))
    )


def detect_java(
    java: JavaConfig,
    preferred_major: int | None = None,
    preferred_component: str = "",
) -> JavaRuntime:
    """探测可用 Java，返回最合适的一个。

    选择优先级（在 launcher_roots 的官方运行时里）：
      1. 与 preferred_component 完全同名（如 java-runtime-delta）→ 最精确
      2. 与 preferred_major 匹配
    然后才考虑 JAVA_HOME / PATH（fallback）。
    - 无任何候选：抛 RuntimeError，提示来源。
    - 给了 major/component 但都匹配不到：抛错列出候选，不默默用错版本。
    - 全都没给：选 major 最大的。
    """
    java = java.effective()
    if preferred_major is None:
        preferred_major = java.preferred_major

    runtimes = _candidates(java)
    if not runtimes:
        raise RuntimeError(
            "未探测到可用 Java。已尝试以下来源均无可用 java：\n"
            + "  - java.launcher_roots 指定的 MC 官方启动器目录"
            + ("（当前为空）" if not java.launcher_roots else "")
            + "\n"
            + "  - 环境变量 JAVA_HOME\n"
            + "  - 系统 PATH 中的 java\n"
            + "\n"
            + "请任选其一提供 Java：\n"
            + "  1) 在 config.yaml 的 java.launcher_roots 填入 MC 官方启动器的 "
            + "runtime 目录（推荐，含全版本运行时）\n"
            + "  2) 安装 JDK 并设置 JAVA_HOME\n"
            + "  3) 把 java 加入系统 PATH\n"
            + "（可关闭 java.use_env_fallback 仅用 launcher_roots）"
        )

    # 1) component 精确定位（最优先，限 launcher_roots 来源）
    if preferred_component:
        matched = [r for r in runtimes if r.component == preferred_component]
        if matched:
            return max(matched, key=lambda r: r.version)
        # 有 component 但本机没有对应官方运行时：若还能按 major 匹配到非官启的，
        # 也不该擅自用（版本可能不符），交给 major 逻辑统一处理/报错。

    # 2) major 匹配
    if preferred_major is not None:
        matched = [r for r in runtimes if r.major == preferred_major]
        if not matched:
            avail = sorted({r.major for r in runtimes})
            hint = f"（需要官方运行时 {preferred_component}）" if preferred_component else ""
            raise RuntimeError(
                f"要求 Java {preferred_major} {hint}，但本机可用的是: {avail}。\n"
                f"可用 Java 列表:\n{_fmt_candidates(runtimes)}\n\n"
                f"请安装/提供 Java {preferred_major}，或在配置中调整该组的 "
                f"preferred_major（或填入含对应版本的 launcher_roots）。"
            )
        return max(matched, key=lambda r: r.version)

    # 3) 都没给：选 major 最大
    return max(runtimes, key=lambda r: (r.major, r.version))
