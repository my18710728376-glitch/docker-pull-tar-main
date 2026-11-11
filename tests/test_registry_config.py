import json

import pytest

import registry_config
from registry_config import (
    PRESET_REGISTRIES,
    RegistryConfigError,
    list_registry_choices,
    load_registry_config,
    resolve_registry,
)

OFFICIAL_REGISTRY_HOSTS = [
    "ghcr.io",
    "quay.io",
    "registry.k8s.io",
    "k8s.gcr.io",
    "gcr.io",
    "registry.gitlab.com",
]


def test_preset_contains_all_official_registries():
    hosts = {option.host for option in PRESET_REGISTRIES.values()}
    for host in OFFICIAL_REGISTRY_HOSTS:
        assert host in hosts


def test_default_registry_is_docker_hub():
    config = load_registry_config()
    assert config.default_source == "builtin"
    assert config.default_option().host == "registry-1.docker.io"


@pytest.mark.parametrize("key,host", [
    ("ghcr", "ghcr.io"),
    ("quay", "quay.io"),
    ("k8s", "registry.k8s.io"),
    ("k8s-gcr", "k8s.gcr.io"),
    ("gcr", "gcr.io"),
    ("gitlab", "registry.gitlab.com"),
])
def test_resolve_registry_by_key(key, host):
    assert resolve_registry(key).host == host


@pytest.mark.parametrize("host", OFFICIAL_REGISTRY_HOSTS)
def test_resolve_registry_by_host(host):
    option = resolve_registry(host)
    assert option.host == host


@pytest.mark.parametrize("raw", ["https://ghcr.io", "ghcr.io/", " GHCR.io "])
def test_resolve_registry_normalizes_input(raw):
    assert resolve_registry(raw).host == "ghcr.io"


def test_resolve_custom_registry_host():
    option = resolve_registry("harbor.example.com")
    assert option.host == "harbor.example.com"
    assert option.key == "harbor.example.com"


def test_resolve_unknown_alias_raises():
    with pytest.raises(RegistryConfigError):
        resolve_registry("unknown-source")


def test_resolve_host_with_path_raises():
    with pytest.raises(RegistryConfigError):
        resolve_registry("ghcr.io/library")


def test_config_file_adds_custom_registry(tmp_path):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text(json.dumps({
        "default_registry": "company",
        "registries": {"company": {"name": "公司镜像站", "host": "mirror.example.com"}},
    }), encoding="utf-8")

    config = load_registry_config(str(config_file))
    assert config.config_path == str(config_file)
    assert config.default_source == "config"
    assert config.default_option().host == "mirror.example.com"
    assert "company" in config.registries


def test_config_file_can_override_preset(tmp_path):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text(json.dumps({
        "registries": {"ghcr": {"name": "GHCR 镜像", "host": "ghcr-mirror.example.com"}},
    }), encoding="utf-8")

    config = load_registry_config(str(config_file))
    assert resolve_registry("ghcr", config).host == "ghcr-mirror.example.com"


def test_config_file_accepts_plain_string_entry(tmp_path):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text(json.dumps({
        "registries": {"company": "mirror.example.com"},
    }), encoding="utf-8")

    option = resolve_registry("company", load_registry_config(str(config_file)))
    assert option.key == "company"
    assert option.host == "mirror.example.com"


def test_env_registry_overrides_config(tmp_path, monkeypatch):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text(json.dumps({"default_registry": "company",
                                       "registries": {"company": "mirror.example.com"}}), encoding="utf-8")

    config = load_registry_config(str(config_file))
    monkeypatch.setenv("DOCKER_PULL_REGISTRY", "ghcr")

    env_config = load_registry_config(str(config_file))
    assert env_config.default_source == "env"
    assert env_config.default_option().host == "ghcr.io"
    assert config.default_option().host == "mirror.example.com"


def test_config_path_from_env(tmp_path, monkeypatch):
    config_file = tmp_path / "custom_config.json"
    config_file.write_text(json.dumps({"default_registry": "quay"}), encoding="utf-8")
    monkeypatch.setenv("DOCKER_PULL_CONFIG_FILE", str(config_file))

    config = load_registry_config()
    assert config.default_option().host == "quay.io"


def test_missing_explicit_config_raises():
    with pytest.raises(RegistryConfigError):
        load_registry_config("not-exists.json")


def test_invalid_json_config_raises(tmp_path):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(RegistryConfigError):
        load_registry_config(str(config_file))


def test_invalid_config_falls_back_when_not_strict(tmp_path):
    config_file = tmp_path / "registry_config.json"
    config_file.write_text("{invalid json", encoding="utf-8")

    with pytest.warns(RuntimeWarning):
        config = load_registry_config(str(config_file), strict=False)

    assert config.default_source == "builtin"
    assert len(config.registries) == len(PRESET_REGISTRIES)


def test_list_registry_choices_deduplicates_hosts():
    choices = list_registry_choices()
    hosts = [host for _, host in choices]
    assert len(hosts) == len(set(hosts))
    assert any(host == "ghcr.io" for host in hosts)


def test_default_option_custom_host_from_env(monkeypatch):
    monkeypatch.setenv("DOCKER_PULL_REGISTRY", "https://harbor.example.com/")
    config = load_registry_config()
    assert config.default_option().host == "harbor.example.com"


def test_registry_config_module_constants():
    assert registry_config.DEFAULT_REGISTRY_HOST == "registry-1.docker.io"
    assert registry_config.ENV_REGISTRY_SELECTOR == "DOCKER_PULL_REGISTRY"
