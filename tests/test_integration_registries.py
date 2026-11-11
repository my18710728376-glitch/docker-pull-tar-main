import os

import pytest
import requests

from registry_config import check_registry_connectivity, build_image_target, PRESET_REGISTRIES

pytestmark = pytest.mark.integration

OFFICIAL_HOSTS = [
    "ghcr.io",
    "quay.io",
    "registry.k8s.io",
    "k8s.gcr.io",
    "gcr.io",
    "registry.gitlab.com",
]

requires_network = pytest.mark.skipif(
    not os.environ.get("RUN_REGISTRY_INTEGRATION_TESTS"),
    reason="设置 RUN_REGISTRY_INTEGRATION_TESTS=1 后运行真实仓库连通性测试",
)

requires_pull = pytest.mark.skipif(
    not os.environ.get("RUN_REGISTRY_PULL_TESTS"),
    reason="设置 RUN_REGISTRY_PULL_TESTS=1 后运行真实镜像拉取测试",
)


@requires_network
@pytest.mark.parametrize("host", OFFICIAL_HOSTS)
def test_preset_registry_reachable(host):
    session = requests.Session()
    session.verify = False

    message = check_registry_connectivity(session, host, timeout=20)

    assert host in message
    assert "可访问" in message


@requires_network
@pytest.mark.parametrize("key", ["ghcr", "quay", "k8s", "k8s-gcr", "gcr", "gitlab"])
def test_registry_token_endpoint_responds(key):
    """验证预置仓库的认证端点可访问（401 表示仓库在线）。"""
    host = PRESET_REGISTRIES[key].host
    session = requests.Session()
    session.verify = False

    response = session.get(f"https://{host}/v2/", timeout=20)

    assert response.status_code in (200, 401, 403)


@requires_pull
def test_real_pull_from_docker_hub(tmp_path):
    app = pytest.importorskip("app", reason="需要安装 gradio")

    target = build_image_target("library/alpine", "3.20", "registry-1.docker.io")
    progress = app.WebProgressDisplay()

    app.pull_image_logic(
        progress, target.registry, target.repository, target.tag, "amd64",
        str(tmp_path), ("无代理", "", "", "", True)
    )

    assert progress.is_done, progress.error_msg
    assert (tmp_path / "library_alpine_3.20_amd64.tar").exists()
