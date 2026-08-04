"""Reverse-engineered Perfect World Arena request and response protocol."""

from __future__ import annotations

import base64
import ipaddress
import json
import random
import time
from collections.abc import Mapping

import requests

from .native_signer import generate_signature


WEB_API_APP_ID = "20000"
WEB_API_BASE = "https://pwaweblogin.wmpvp.com"
CLIENT_REFERER = "https://client.wmpvp.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) perfectworldarena/1.0.26073111 "
    "Chrome/80.0.3987.163 Electron/8.5.5 Safari/537.36"
)

# Reconstructed from the current desktop client's AES module. The six-character
# response token is appended to this 26-byte prefix to form an AES-256 key.
RESPONSE_KEY_PREFIX = "G#r%*VCDYj6P5$mny0838MhH8d"
PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org/",
    "https://ifconfig.me/ip",
)
_PUBLIC_IPV4_CACHE: str | None = None


class PerfectWorldProtocolError(RuntimeError):
    pass


def _aes_primitives():
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise PerfectWorldProtocolError(
            "缺少隔离版依赖 cryptography，请安装 perfectworld_experiment/requirements.txt"
        ) from exc
    return padding, Cipher, algorithms, modes


def canonical_query(params: Mapping[str, object]) -> str:
    """Match the client: make raw key=value pairs and sort the full strings."""
    return "&".join(sorted(f"{key}={value}" for key, value in params.items()))


def build_signed_params(
    params: Mapping[str, object],
    *,
    randnum: int | str | None = None,
    timestamp: int | str | None = None,
) -> dict[str, str]:
    normalized = {str(key): str(value) for key, value in params.items()}
    signature_fields = build_signature_params(
        canonical_query(normalized),
        randnum=randnum,
        timestamp=timestamp,
    )
    return {**signature_fields, **normalized}


def build_signature_params(
    signing_data: str,
    *,
    randnum: int | str | None = None,
    timestamp: int | str | None = None,
) -> dict[str, str]:
    """Build a/r/s/t for an already-canonicalized GET query or POST body."""
    rand_value = str(randnum if randnum is not None else random.randrange(100000, 1000000))
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    signature = generate_signature(
        rand_value,
        timestamp_value,
        str(signing_data),
    )
    return {
        "a": WEB_API_APP_ID,
        "r": rand_value,
        "s": signature,
        "t": timestamp_value,
    }


def decrypt_response(ciphertext: str, token: str) -> str:
    """Decrypt response data.e using data.t exactly as the desktop client does."""
    key = (RESPONSE_KEY_PREFIX + str(token)).encode("utf-8")
    if len(key) != 32:
        raise PerfectWorldProtocolError("完美平台响应解密 token 长度异常")
    try:
        encrypted = base64.b64decode(ciphertext, validate=True)
    except (ValueError, TypeError) as exc:
        raise PerfectWorldProtocolError("完美平台响应不是有效 Base64") from exc
    padding, Cipher, algorithms, modes = _aes_primitives()
    try:
        decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise PerfectWorldProtocolError("完美平台响应解密失败") from exc


def decode_api_payload(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return payload
    data = payload.get("data")
    if not isinstance(data, Mapping) or not data.get("e") or not data.get("t"):
        return data
    try:
        return json.loads(decrypt_response(str(data["e"]), str(data["t"])))
    except json.JSONDecodeError as exc:
        raise PerfectWorldProtocolError("完美平台解密响应不是有效 JSON") from exc


def get_public_ipv4(session: requests.Session | None = None) -> str:
    global _PUBLIC_IPV4_CACHE
    if _PUBLIC_IPV4_CACHE:
        return _PUBLIC_IPV4_CACHE
    client = session or requests.Session()
    for endpoint in PUBLIC_IP_ENDPOINTS:
        try:
            response = client.get(endpoint, timeout=(5, 10))
            response.raise_for_status()
            value = response.text.strip()
            if ipaddress.ip_address(value).version == 4:
                _PUBLIC_IPV4_CACHE = value
                return value
        except (requests.RequestException, ValueError):
            continue
    raise PerfectWorldProtocolError("无法自动确定下载签名所需的公网 IPv4")


def build_x_pwa_signature(
    steamid: str,
    public_ip: str,
    timestamp: int | str | None = None,
) -> str:
    """Build timestamp-AES-CBC signature used by the OSS download request."""
    timestamp_value = str(timestamp if timestamp is not None else int(time.time()))
    if len(timestamp_value) != 10 or not timestamp_value.isdigit():
        raise ValueError("下载签名时间戳必须是 10 位 Unix 秒")
    try:
        address = ipaddress.ip_address(public_ip)
    except ValueError as exc:
        raise ValueError("下载签名公网 IP 无效") from exc
    if address.version != 4:
        raise ValueError("下载签名目前只支持公网 IPv4")

    key = (timestamp_value + steamid[len(timestamp_value) - 16 :]).encode("ascii")
    iv = steamid[-16:].encode("ascii")
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("SteamID64 无法组成下载签名密钥")
    padding, Cipher, algorithms, modes = _aes_primitives()
    padder = padding.PKCS7(128).padder()
    padded = padder.update(str(address).encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return f"{timestamp_value}-{encrypted.hex()}"


def build_download_headers(
    steamid: str,
    *,
    session: requests.Session | None = None,
    public_ip: str | None = None,
    timestamp: int | str | None = None,
) -> dict[str, str]:
    address = public_ip or get_public_ipv4(session)
    return {
        "User-Agent": USER_AGENT,
        "Referer": CLIENT_REFERER,
        "X-PWA-SteamId": steamid,
        "X-PWA-Signature": build_x_pwa_signature(steamid, address, timestamp),
        "PwaSteamId": steamid,
        "x-pwa-steamid": steamid,
        "pwasteamid": steamid,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN",
    }


def build_api_headers(steamid: str, access_token: str) -> dict[str, str]:
    return {
        "pwasteamid": steamid,
        "PwaSteamId": steamid,
        "x-pwa-steamid": steamid,
        "Referer": CLIENT_REFERER + "/",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN",
        "Cookie": f"steam_cn_token={access_token}",
    }


def build_demo_url(
    match_id: str,
    cup_id: int | str,
    access_token: str,
) -> str:
    params = build_signed_params(
        {
            "access_token": access_token,
            "cup_id": str(cup_id),
            "match_id": str(match_id),
        }
    )
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"{WEB_API_BASE}/csgo/demo/{match_id}_{cup_id}.dem?{query}"
