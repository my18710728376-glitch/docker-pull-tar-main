import pytest
import requests

from fakes import FakeResponse, FakeSession
from registry_config import RegistryConnectivityError, RegistryConfigError, check_registry_connectivity


def make_session(ping_response=None, exc=None, urls=None):
    def handler(url, headers, stream, timeout):
        if urls is not None:
            urls.append(url)
        if exc is not None:
            raise exc
        return ping_response

    session = FakeSession(handler)
    return session


def test_ping_200_is_reachable():
    session = make_session(FakeResponse(200))
    message = check_registry_connectivity(session, "ghcr.io")
    assert "ghcr.io" in message
    assert "HTTP 200" in message


def test_ping_401_is_reachable_with_auth_required():
    session = make_session(FakeResponse(401))
    message = check_registry_connectivity(session, "quay.io")
    assert "HTTP 401" in message
    assert "需要认证" in message


def test_ping_403_is_reachable_but_restricted():
    session = make_session(FakeResponse(403))
    message = check_registry_connectivity(session, "registry.gitlab.com")
    assert "HTTP 403" in message


@pytest.mark.parametrize("registry", ["ghcr.io", "quay.io", "registry.k8s.io", "k8s.gcr.io", "gcr.io",
                                     "registry.gitlab.com", "harbor.example.com"])
def test_request_url_uses_v2_endpoint(registry):
    urls = []
    session = make_session(FakeResponse(200), urls=urls)
    check_registry_connectivity(session, registry)
    assert urls == [f"https://{registry}/v2/"]


def test_timeout_raises_connectivity_error():
    session = make_session(exc=requests.exceptions.Timeout("timeout"))
    with pytest.raises(RegistryConnectivityError) as exc_info:
        check_registry_connectivity(session, "ghcr.io", timeout=3)

    message = str(exc_info.value)
    assert "ghcr.io" in message
    assert "超时" in message
    assert "3 秒" in message


def test_connection_error_raises_connectivity_error():
    session = make_session(exc=requests.exceptions.ConnectionError("DNS failed"))
    with pytest.raises(RegistryConnectivityError) as exc_info:
        check_registry_connectivity(session, "k8s.gcr.io")

    message = str(exc_info.value)
    assert "k8s.gcr.io" in message
    assert "网络不可达" in message


def test_ssl_error_raises_connectivity_error():
    session = make_session(exc=requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(RegistryConnectivityError) as exc_info:
        check_registry_connectivity(session, "gcr.io")

    message = str(exc_info.value)
    assert "SSL" in message
    assert "gcr.io" in message


def test_404_reports_not_a_registry():
    session = make_session(FakeResponse(404))
    with pytest.raises(RegistryConnectivityError) as exc_info:
        check_registry_connectivity(session, "example.com")

    assert "404" in str(exc_info.value)
    assert "Docker Registry" in str(exc_info.value)


def test_unexpected_status_raises():
    session = make_session(FakeResponse(500))
    with pytest.raises(RegistryConnectivityError) as exc_info:
        check_registry_connectivity(session, "registry.k8s.io")

    assert "HTTP 500" in str(exc_info.value)


def test_invalid_registry_host_raises_config_error():
    session = make_session(FakeResponse(200))
    with pytest.raises(RegistryConfigError):
        check_registry_connectivity(session, "ghcr.io/library")
