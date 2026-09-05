"""实例启动辅助（可选）。

设计决策：工具**不**接管 MCDR 进程（daemon 重定向会触发 MCDR→服务器
编码/回显异常）。主路径是：
  - 工具在 deploy 时生成 start.bat / start.sh，用户进实例目录双击/运行即
    前台启动 MCDR 交互控制台；
  - 若仍想由本工具前台启动，可运行 `mcserver start --version X`，它会
    以透传方式（继承当前终端 stdin/stdout）拉起 MCDR，等同自己进目录跑。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import mcdr
from .paths import instance_paths


def _root(mc_version: str) -> Path:
    return instance_paths(mc_version)["root"]


def _require_deployed(mc_version: str) -> Path:
    paths = instance_paths(mc_version)
    if not paths["mcdr_config"].exists():
        raise FileNotFoundError(
            f"[{mc_version}] 尚未部署（缺 config.yml）。请先运行 deploy。"
        )
    return paths["root"]


def write_start_scripts(mc_version: str) -> list[Path]:
    """在实例根生成 start.bat / start.sh，供用户前台启动 MCDR。"""
    root = _root(mc_version)
    py = mcdr.python_exe()
    written: list[Path] = []
    if os.name == "nt":
        bat = root / "start.bat"
        # 用项目 venv 的 python -m mcdreforged；cwd 自动为脚本所在目录
        bat.write_text(
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            f'cd /d "%~dp0"\r\n'
            f'"{py}" -m mcdreforged\r\n'
            "pause\r\n",
            encoding="utf-8",
        )
        written.append(bat)
    else:
        sh = root / "start.sh"
        sh.write_text(
            "#!/usr/bin/env bash\n"
            f'cd "$(dirname "$0")"\n'
            f'exec "{py}" -m mcdreforged\n',
            encoding="utf-8",
        )
        sh.chmod(0o755)
        written.append(sh)
    return written


def run_foreground(mc_version: str) -> int:
    """透传前台运行 MCDR（继承当前终端）。退出返回码。"""
    root = _require_deployed(mc_version)
    py = mcdr.python_exe()
    print(
        f"[{mc_version}] 前台启动 MCDR（等同在 {root} 下运行）。"
        f" 退出 MCDR: 控制台输入 !!MCDR exit 或 Ctrl+C"
    )
    return subprocess.call(
        [str(py), "-m", "mcdreforged"],
        cwd=str(root),
    )
