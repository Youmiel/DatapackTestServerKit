"""HTTP 下载通用工具：代理、超时、UA、SHA1 校验、进度、失败重试。"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Callable

import requests

DEFAULT_TIMEOUT = 60
DEFAULT_ATTEMPTS = 3
ProgressCb = Callable[[int, int], None]


class DownloadError(Exception):
    pass


def sha1_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """计算本地文件 SHA1（小写十六进制）。读取失败返回空串。

    用途：不联网即可判定已有缓存/实例文件是否与解析出的 sha1 一致。
    """
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk_size)
                if not block:
                    break
                h.update(block)
    except OSError:
        return ""
    return h.hexdigest()


def _fingerprint_from_headers(headers) -> dict[str, str]:
    """从响应头提取缓存指纹（ETag / Last-Modified / Content-Length）。"""
    return {
        "etag": headers.get("ETag", ""),
        "last_modified": headers.get("Last-Modified", ""),
        "content_length": headers.get("Content-Length", ""),
    }


def fingerprint_usable(fp: dict | None) -> bool:
    """指纹是否可用于比对（三项至少一项非空）。"""
    return bool(fp) and any(fp.values())


class Http:
    """HTTP 客户端。

    attempts: 每个请求/下载的**整体**尝试次数（含首次），>1 时失败会间隔退避重试。
    配合连接层 urllib3 Retry 处理瞬时错误；attempts 兜底 5xx/超时/断流等整体失败。
    """

    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        proxy: str = "",
        user_agent: str = "mc-multiversion-deployer",
        extra_headers: dict | None = None,
        api_key: str = "",
        retries: int = 0,  # 连接层 urllib3 重试（向后兼容，默认关闭）
        attempts: int = DEFAULT_ATTEMPTS,  # 整体重试次数（含首次）
        backoff: float = 2.0,
    ):
        self.timeout = timeout
        self.attempts = max(1, int(attempts))
        self.backoff = backoff
        session = requests.Session()
        # 不读系统/环境代理（Windows 系统代理常干扰 MC 相关下载）；
        # 仅当配置显式给了 proxy 才使用。trust_env=False 也避免读 HTTP(S)_PROXY。
        session.trust_env = False
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        # 连接层重试（对临时网络波动更稳）
        if retries:
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

    def get_json(self, url: str, params: dict | None = None) -> dict | list:
        """GET JSON；失败按 attempts 重试。"""
        last_err: Exception | None = None
        for i in range(self.attempts):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                last_err = e
                self._warn(url, i)
        raise DownloadError(f"GET {url} 失败（已重试 {self.attempts} 次）: {last_err}") from last_err

    def download(
        self,
        url: str,
        dest: Path,
        *,
        sha1: str = "",
        progress: ProgressCb | None = None,
        chunk_size: int = 256 * 1024,
        fingerprint_out: dict | None = None,
    ) -> Path:
        """下载到 dest。若给了 sha1 则校验；失败按 attempts 重试后抛 DownloadError。

        fingerprint_out：传入 dict 时，成功后会填入本次响应的缓存指纹
        （etag / last_modified / content_length），供下次部署比对判定失效。
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        last_err: Exception | None = None
        for i in range(self.attempts):
            tmp.unlink(missing_ok=True)
            hasher = hashlib.sha1()
            headers: dict[str, str] = {}
            try:
                with self.session.get(url, stream=True, timeout=self.timeout) as r:
                    r.raise_for_status()
                    headers = _fingerprint_from_headers(r.headers)
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
                last_err = e
                tmp.unlink(missing_ok=True)
                self._warn(url, i)
                continue
            if sha1:
                actual = hasher.hexdigest()
                if actual.lower() != sha1.lower():
                    last_err = DownloadError(
                        f"SHA1 校验失败 {dest.name}: 期望 {sha1} 实际 {actual}"
                    )
                    tmp.unlink(missing_ok=True)
                    self._warn(url, i)
                    continue
            if progress:
                print()  # 结束进度行
            if fingerprint_out is not None:
                fingerprint_out.update(headers)
            os.replace(tmp, dest)
            return dest
        raise DownloadError(
            f"下载 {dest.name} 失败（已重试 {self.attempts} 次）: {last_err}"
        ) from last_err

    def head_fingerprint(self, url: str) -> dict | None:
        """HEAD 探测远端资源指纹，不下载内容。

        返回 etag / last_modified / content_length 三项；无网、服务端不支持
        HEAD、或三项均为空时返回 None（调用方应降级为“沿用缓存”）。
        """
        try:
            r = self.session.head(url, timeout=self.timeout, allow_redirects=True)
            r.raise_for_status()
        except requests.RequestException:
            return None
        fp = _fingerprint_from_headers(r.headers)
        return fp if fingerprint_usable(fp) else None

    def _warn(self, url: str, attempt_index: int) -> None:
        """第 attempt_index 次（0 起）失败且还有下次时打印提示。"""
        if attempt_index + 1 >= self.attempts:
            return
        wait = self.backoff * (2 ** attempt_index)
        print(f"[网络] 尝试 {attempt_index + 1}/{self.attempts} 失败：{url}（{wait:.0f}s 后重试）")
        time.sleep(wait)


def _build_http(
    cfg_modrinth: dict, cfg_curseforge: dict, cfg_network: dict, api_key: str
) -> Http:
    return Http(
        timeout=int(cfg_network.get("timeout", DEFAULT_TIMEOUT)),
        proxy=str(cfg_network.get("proxy", "")),
        user_agent=str(cfg_modrinth.get("user_agent", "")
                          or cfg_curseforge.get("user_agent", "")
                          or "mc-multiversion-deployer"),
        api_key=api_key,
        attempts=int(cfg_network.get("download_retries", DEFAULT_ATTEMPTS)),
    )


def build_modrinth_http(cfg_modrinth: dict, cfg_network: dict) -> Http:
    return _build_http(cfg_modrinth, {}, cfg_network, "")


def build_curseforge_http(cfg_curseforge: dict, cfg_network: dict) -> Http:
    """构造 CurseForge HTTP 客户端。key 从环境变量读取，缺失抛异常。"""
    env_name = str(cfg_curseforge.get("api_key_env") or "CURSEFORGE_API_KEY")
    key = os.environ.get(env_name, "")
    if not key:
        raise DownloadError(
            f"CurseForge 需要 API key：请设置环境变量 {env_name} "
            f"（参考 https://console.curseforge.com/ 申请）"
        )
    return _build_http({}, cfg_curseforge, cfg_network, key)
