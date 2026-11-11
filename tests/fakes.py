"""测试用的轻量 requests 替身，避免单元测试访问真实网络。"""

import gzip
import hashlib
import json

import requests


class FakeResponse:
    """模拟 requests.Response 的常用接口。"""

    def __init__(self, status_code=200, json_data=None, content=b"", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self._content = content
        self.headers = headers or {}

    def json(self):
        if self._json_data is None:
            raise ValueError("响应中没有 JSON 内容")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        for index in range(0, len(self._content), chunk_size):
            yield self._content[index:index + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class FakeSession:
    """按 URL 路由到预设响应的假 Session，并记录所有请求。"""

    def __init__(self, handler):
        self._handler = handler
        self.requested_urls = []
        self.verify = True
        self.proxies = {}

    def get(self, url, headers=None, stream=False, timeout=None, **kwargs):
        self.requested_urls.append(url)
        return self._handler(url, headers, stream, timeout, **kwargs)


def make_digest(prefix: str) -> str:
    return "sha256:" + prefix * 64


def make_layer_blob(payload: bytes) -> bytes:
    """构造符合 gzip 格式的镜像层数据。"""
    return gzip.compress(payload)


def make_registry_handler(host: str, manifest: dict, blobs: dict):
    """构造模拟 Registry API（ping / manifests / blobs）的 URL 处理器。"""

    def handler(url, headers, stream, timeout):
        if not url.startswith(f"https://{host}/v2/"):
            raise AssertionError(f"请求了非目标仓库地址: {url}")

        path = url.split(f"https://{host}", 1)[1]
        if path.rstrip("/") == "/v2":
            return FakeResponse(200)
        if "/manifests/" in path:
            return FakeResponse(200, json_data=manifest)
        if "/blobs/" in path:
            digest = path.split("/blobs/", 1)[1]
            if digest not in blobs:
                raise AssertionError(f"未知的 blob: {digest}")
            return FakeResponse(200, content=blobs[digest])
        raise AssertionError(f"未预期的请求地址: {url}")

    return handler


def build_single_arch_manifest(layer_payloads):
    """构造单架构镜像清单与对应 blob 字典。"""
    config_bytes = json.dumps({"architecture": "amd64", "os": "linux"}).encode("utf-8")
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()

    layers = []
    blobs = {config_digest: config_bytes}
    for index, payload in enumerate(layer_payloads):
        blob = make_layer_blob(payload)
        digest = "sha256:" + hashlib.sha256(blob).hexdigest()
        layers.append({"digest": digest, "size": len(blob)})
        blobs[digest] = blob

    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
        "config": {"digest": config_digest, "size": len(config_bytes)},
        "layers": layers,
    }
    return manifest, blobs
