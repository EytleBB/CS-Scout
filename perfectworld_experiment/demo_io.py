"""Bounded, isolated Perfect World demo download and extraction."""

from __future__ import annotations

import bz2
import ipaddress
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import tempfile
from urllib.parse import urljoin, urlsplit, urlunsplit
import zipfile

import requests

from .pwa_client import validate_match_id


MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_MEMBERS = 32
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
PROXY_FAKE_IP_NETWORKS = (
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("fdfe:dcba:9876::/48"),
)
TRUSTED_DEMO_HOSTS = {"pwaweblogin.wmpvp.com"}
TRUSTED_DEMO_HOST_SUFFIXES = (".aliyuncs.com",)


class UnsafePerfectWorldUrl(ValueError):
    pass


class PerfectWorldDownloadError(RuntimeError):
    pass


def _resolve_public_host(hostname: str) -> None:
    try:
        answers = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise UnsafePerfectWorldUrl("Demo 主机无法解析") from exc
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses:
        raise UnsafePerfectWorldUrl("Demo 主机没有有效地址")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise UnsafePerfectWorldUrl("Demo 主机返回了非法地址") from exc
        trusted_fake_ip = (
            any(address in network for network in PROXY_FAKE_IP_NETWORKS)
            and (
                hostname in TRUSTED_DEMO_HOSTS
                or hostname.endswith(TRUSTED_DEMO_HOST_SUFFIXES)
            )
        )
        if not address.is_global and not trusted_fake_ip:
            raise UnsafePerfectWorldUrl("Demo 主机解析到了非公网地址")


def normalize_download_url(
    url: str,
    *,
    base_url: str | None = None,
    require_public_dns: bool = True,
) -> str:
    if not isinstance(url, str):
        raise UnsafePerfectWorldUrl("Demo URL 不是文本")
    value = url.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UnsafePerfectWorldUrl("Demo URL 为空或含控制字符")
    if base_url is not None:
        value = urljoin(base_url, value)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise UnsafePerfectWorldUrl("Demo URL 端口非法") from exc
    if parsed.scheme.casefold() != "https":
        raise UnsafePerfectWorldUrl("Demo URL 必须使用 HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePerfectWorldUrl("Demo URL 不得包含账号信息")
    if port not in (None, 443) or parsed.fragment:
        raise UnsafePerfectWorldUrl("Demo URL 端口或片段非法")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if not hostname:
        raise UnsafePerfectWorldUrl("Demo URL 缺少主机名")
    if require_public_dns:
        _resolve_public_host(hostname)
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafePerfectWorldUrl("Demo 主机名非法") from exc
    return urlunsplit(("https", hostname, parsed.path or "/", parsed.query, ""))


def _copy_bounded(source, target, maximum: int) -> int:
    total = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise PerfectWorldDownloadError("Demo 文件超过大小限制")
        target.write(chunk)
    return total


def _safe_demo_name(match_id: str, index: int, member_name: str = "") -> str:
    suffix = Path(PurePosixPath(member_name.replace("\\", "/")).name).name
    if not suffix.casefold().endswith(".dem"):
        suffix = f"{match_id}.dem"
    return f"{match_id}_{index}_{suffix}"


def _extract_zip(archive_path: Path, destination: Path, match_id: str) -> list[Path]:
    extracted: list[Path] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) > MAX_ZIP_MEMBERS:
            raise PerfectWorldDownloadError("Demo 压缩包文件数量异常")
        demo_members = []
        total_size = 0
        for item in members:
            pure = PurePosixPath(item.filename.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                raise PerfectWorldDownloadError("Demo 压缩包包含越界路径")
            total_size += max(0, int(item.file_size))
            if total_size > MAX_EXTRACTED_BYTES:
                raise PerfectWorldDownloadError("Demo 解压后超过大小限制")
            if pure.name.casefold().endswith(".dem"):
                demo_members.append(item)
        if not demo_members:
            raise PerfectWorldDownloadError("压缩包中没有 .dem 文件")
        for index, item in enumerate(demo_members):
            target = destination / _safe_demo_name(match_id, index, item.filename)
            temporary = target.with_suffix(target.suffix + ".part")
            try:
                with archive.open(item) as source, temporary.open("wb") as output:
                    _copy_bounded(source, output, MAX_EXTRACTED_BYTES)
                os.replace(temporary, target)
                extracted.append(target)
            finally:
                temporary.unlink(missing_ok=True)
    return extracted


def _extract_bz2(archive_path: Path, destination: Path, match_id: str) -> list[Path]:
    target = destination / f"{match_id}.dem"
    temporary = target.with_suffix(".dem.part")
    try:
        with bz2.open(archive_path, "rb") as source, temporary.open("wb") as output:
            _copy_bounded(source, output, MAX_EXTRACTED_BYTES)
        os.replace(temporary, target)
        return [target]
    except OSError as exc:
        raise PerfectWorldDownloadError("Demo BZ2 解压失败") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _store_raw_demo(archive_path: Path, destination: Path, match_id: str) -> list[Path]:
    target = destination / f"{match_id}.dem"
    os.replace(archive_path, target)
    return [target]


def download_and_extract(
    match_id: str,
    demo_url: str,
    destination: str | os.PathLike[str],
    *,
    headers: dict[str, str],
    session: requests.Session | None = None,
    require_public_dns: bool = True,
) -> list[str]:
    """Download one signed PWA URL without leaking it or leaving partial files."""
    safe_match_id = validate_match_id(match_id)
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cached = []
    exact_cache = root / f"{safe_match_id}.dem"
    if exact_cache.is_file():
        cached.append(exact_cache)
    cached.extend(sorted(root.glob(f"{safe_match_id}_*.dem")))
    if cached:
        return [str(path) for path in cached]

    client = session or requests.Session()
    current_url = normalize_download_url(
        demo_url, require_public_dns=require_public_dns
    )
    response = None
    archive_path: Path | None = None
    try:
        for _ in range(MAX_REDIRECTS + 1):
            response = client.get(
                current_url,
                headers=headers,
                stream=True,
                timeout=(15, 120),
                allow_redirects=False,
            )
            if response.status_code not in REDIRECT_STATUSES:
                break
            location = response.headers.get("Location")
            response.close()
            response = None
            if not location:
                raise PerfectWorldDownloadError("Demo 重定向缺少 Location")
            current_url = normalize_download_url(
                location,
                base_url=current_url,
                require_public_dns=require_public_dns,
            )
        else:
            raise PerfectWorldDownloadError("Demo 重定向次数过多")

        if response is None:
            raise PerfectWorldDownloadError("Demo 下载没有响应")
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").casefold()
        if "text/html" in content_type or "application/json" in content_type:
            raise PerfectWorldDownloadError("Demo 地址返回的不是二进制文件")
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > MAX_DOWNLOAD_BYTES:
                    raise PerfectWorldDownloadError("Demo 下载超过大小限制")
            except ValueError as exc:
                raise PerfectWorldDownloadError("Demo 响应大小非法") from exc

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{safe_match_id}-", suffix=".download", dir=root
        )
        os.close(fd)
        archive_path = Path(temporary_name)
        response.raw.decode_content = True
        with archive_path.open("wb") as output:
            _copy_bounded(response.raw, output, MAX_DOWNLOAD_BYTES)

        if zipfile.is_zipfile(archive_path):
            paths = _extract_zip(archive_path, root, safe_match_id)
        else:
            with archive_path.open("rb") as source:
                magic = source.read(3)
            paths = (
                _extract_bz2(archive_path, root, safe_match_id)
                if magic == b"BZh"
                else _store_raw_demo(archive_path, root, safe_match_id)
            )
            if paths and paths[0] == archive_path:
                archive_path = None
        return [str(path) for path in paths]
    except requests.RequestException as exc:
        raise PerfectWorldDownloadError("Demo HTTPS 下载失败") from exc
    finally:
        if response is not None:
            response.close()
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)
