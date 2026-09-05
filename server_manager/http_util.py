"""HTTP 下载通用工具：代理、超时、UA、SHA1 校验、进度。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable

import requests

DEFAULT_TIMEOUT = 60
ProgressCb = Callable[[int, int], None]


class DownloadError(Exception):
    pass


class Http:
    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        proxy: str = "",
        user_agent: str = "mc-multiversion-deployer",
        extra_headers: dict | None = None,
        api_key: str = "",
        retries: int = 3,
    ):
        self.timeout = timeout
        session = requests.Session()
        # 不读系统/环境代理（Windows 系统代理常干扰 MC 相关下载）；
        # 仅当配置显式给了 proxy 才使用。trust_env=False 也避免读 HTTP(S)_PROXY。
        session.trust_env = False
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        # 连接层重试（对临时网络波动更稳）
        adapter = requests.adapters.HTTPAdapter(
            max_retries=requests.adapters.Retry(
                total=retries,
                backoff_factor=1.0,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=None,
            )
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        headers = {"User-Agent": user_agent}
        if extra_headers:
            headers.update(extra_headers)
        if api_key:
            headers.setdefault("x-api-key", api_key)
        session.headers.update(headers)
        self.session = session

    def get_json(self, url: str) -> dict | list:
        try:
            r = self.session.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise DownloadError(f"GET {url} 失败: {e}") from e

    def download(
        self,
        url: str,
        dest: Path,
        *,
        sha1: str = "",
        progress: ProgressCb | None = None,
        chunk_size: int = 256 * 1024,
    ) -> Path:
        """下载到 dest。若给了 sha1 则校验；失败抛 DownloadError。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        hasher = hashlib.sha1()
        try:
            with self.session.get(url, stream=True, timeout=self.timeout) as r:
                r.raise_for_status()
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        f.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
        except requests.RequestException as e:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"下载 {url} 失败: {e}") from e
        if sha1:
            actual = hasher.hexdigest()
            if actual.lower() != sha1.lower():
                tmp.unlink(missing_ok=True)
                raise DownloadError(
                    f"SHA1 校验失败 {dest.name}: 期望 {sha1} 实际 {actual}"
                )
        os.replace(tmp, dest)
        return dest


def build_modrinth_http(cfg_modrinth: dict, cfg_network: dict) -> Http:
    return Http(
        timeout=int(cfg_network.get("timeout", DEFAULT_TIMEOUT)),
        proxy=str(cfg_network.get("proxy", "")),
        user_agent=str(cfg_modrinth.get("user_agent", "") or "mc-multiversion-deployer"),
    )


def build_curseforge_http(cfg_curseforge: dict, cfg_network: dict) -> Http:
    """构造 CurseForge HTTP 客户端。key 从环境变量读取，缺失抛异常。"""
    env_name = str(cfg_curseforge.get("api_key_env") or "CURSEFORGE_API_KEY")
    key = os.environ.get(env_name, "")
    if not key:
        raise DownloadError(
            f"CurseForge 需要 API key：请设置环境变量 {env_name} "
            f"（参考 https://console.curseforge.com/ 申请）"
        )
    return Http(
        timeout=int(cfg_network.get("timeout", DEFAULT_TIMEOUT)),
        proxy=str(cfg_network.get("proxy", "")),
        user_agent=str(cfg_curseforge.get("user_agent", ""))
        or "mc-multiversion-deployer",
        api_key=key,
    )
