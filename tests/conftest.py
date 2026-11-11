import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def clean_registry_env(monkeypatch):
    """隔离外部环境变量，避免宿主机配置影响测试结果。"""
    monkeypatch.delenv("DOCKER_PULL_REGISTRY", raising=False)
    monkeypatch.delenv("DOCKER_PULL_CONFIG_FILE", raising=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: 需要访问真实网络的集成测试")
