import tarfile

import pytest

from fakes import FakeResponse, FakeSession, build_single_arch_manifest, make_registry_handler
from registry_config import PRESET_REGISTRIES, build_image_target

app = pytest.importorskip("app", reason="需要安装 gradio 才能测试 Web 拉取逻辑")

OFFICIAL_KEYS = ["docker", "ghcr", "quay", "k8s", "k8s-gcr", "gcr", "gitlab"]


def _install_fake_network(monkeypatch, session):
    monkeypatch.setattr(app, "get_session", lambda verify_ssl, proxies, **kwargs: session)
    monkeypatch.setattr(app, "get_auth_head", lambda session, registry, repository: {"Accept": "manifest"})


@pytest.mark.parametrize("key", OFFICIAL_KEYS)
def test_pull_flow_works_for_each_preset_registry(key, tmp_path, monkeypatch):
    host = PRESET_REGISTRIES[key].host
    target = build_image_target("library/nginx", "latest", host)

    manifest, blobs = build_single_arch_manifest([b"layer-one", b"layer-two"])
    session = FakeSession(make_registry_handler(host, manifest, blobs))
    _install_fake_network(monkeypatch, session)

    progress = app.WebProgressDisplay()
    app.pull_image_logic(
        progress, target.registry, target.repository, target.tag, "amd64",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert progress.error_msg == "", f"{host} 拉取失败: {progress.error_msg}"
    assert progress.is_done
    assert progress.target_reference == f"{host}/library/nginx:latest"

    tar_path = tmp_path / "library_nginx_latest_amd64.tar"
    assert tar_path.exists()
    with tarfile.open(tar_path) as tar:
        names = tar.getnames()
        assert any(name.endswith("manifest.json") for name in names)

    assert session.requested_urls
    assert all(url.startswith(f"https://{host}/v2/") for url in session.requested_urls)


def test_pull_flow_accepts_custom_registry(tmp_path, monkeypatch):
    host = "harbor.example.com"
    target = build_image_target("team/app", "1.0", host)

    manifest, blobs = build_single_arch_manifest([b"custom-layer"])
    session = FakeSession(make_registry_handler(host, manifest, blobs))
    _install_fake_network(monkeypatch, session)

    progress = app.WebProgressDisplay()
    app.pull_image_logic(
        progress, target.registry, target.repository, target.tag, "amd64",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert progress.error_msg == ""
    assert progress.is_done
    assert (tmp_path / "team_app_1.0_amd64.tar").exists()


def test_pull_selects_arm_v7_from_manifest_list(tmp_path, monkeypatch):
    """arm/v7 在清单中是 architecture=arm + variant=v7，需按 variant 精确匹配。"""
    host = "example.test"
    manifest, blobs = build_single_arch_manifest([b"arm-layer"])
    arm_digest = "sha256:" + "a" * 64
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.docker.distribution.manifest.list.v2+json",
        "manifests": [
            {"digest": "sha256:" + "b" * 64,
             "platform": {"architecture": "amd64", "os": "linux"}},
            {"digest": arm_digest,
             "platform": {"architecture": "arm", "os": "linux", "variant": "v7"}},
        ],
    }

    def handler(url, headers, stream, timeout):
        path = url.split(f"https://{host}", 1)[1]
        if path.rstrip("/") == "/v2":
            return FakeResponse(200)
        if path.endswith("/manifests/1.0"):
            return FakeResponse(200, json_data=index)
        if path.endswith(f"/manifests/{arm_digest}"):
            return FakeResponse(200, json_data=manifest)
        if "/blobs/" in path:
            return FakeResponse(200, content=blobs[path.split("/blobs/", 1)[1]])
        raise AssertionError(f"未预期的请求地址: {url}")

    session = FakeSession(handler)
    _install_fake_network(monkeypatch, session)

    progress = app.WebProgressDisplay()
    app.pull_image_logic(
        progress, host, "demo/app", "1.0", "arm/v7",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert progress.error_msg == "", progress.error_msg
    assert progress.is_done
    assert any(f"/manifests/{arm_digest}" in url for url in session.requested_urls)
    assert (tmp_path / "demo_app_1.0_arm-v7.tar").exists()


def test_single_segment_repository_on_non_hub_registry(tmp_path, monkeypatch):
    """非 Docker Hub 仓库的单段仓库名不应补全 library/ 前缀。"""
    host = "registry.k8s.io"
    manifest, blobs = build_single_arch_manifest([b"pause-layer"])
    session = FakeSession(make_registry_handler(host, manifest, blobs))
    _install_fake_network(monkeypatch, session)

    progress = app.WebProgressDisplay()
    app.pull_image_logic(
        progress, host, "pause", "3.9", "amd64",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert progress.error_msg == "", progress.error_msg
    assert any("/v2/pause/manifests/3.9" in url for url in session.requested_urls)
    assert not any("/v2/library/pause/" in url for url in session.requested_urls)


def test_pull_flow_reports_connectivity_error(tmp_path, monkeypatch):
    import requests

    host = "ghcr.io"
    target = build_image_target("library/nginx", "latest", host)

    def blocked(url, headers, stream, timeout):
        raise requests.exceptions.ConnectionError("DNS 解析失败")

    session = FakeSession(blocked)
    _install_fake_network(monkeypatch, session)

    progress = app.WebProgressDisplay()
    app.pull_image_logic(
        progress, target.registry, target.repository, target.tag, "amd64",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert not progress.is_done
    assert "无法连接镜像仓库" in progress.error_msg
    assert host in progress.error_msg
    assert not (tmp_path / "library_nginx_latest_amd64.tar").exists()
