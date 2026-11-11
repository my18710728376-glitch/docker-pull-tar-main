import tarfile

import pytest

from fakes import FakeSession, build_single_arch_manifest, make_registry_handler
from registry_config import PRESET_REGISTRIES, build_image_target

app = pytest.importorskip("app", reason="需要安装 gradio 才能测试 Web 拉取逻辑")

OFFICIAL_KEYS = ["docker", "ghcr", "quay", "k8s", "k8s-gcr", "gcr", "gitlab"]


def _install_fake_network(monkeypatch, session):
    monkeypatch.setattr(app, "get_session", lambda verify_ssl, proxies: session)
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
