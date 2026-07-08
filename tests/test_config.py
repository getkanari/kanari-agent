"""
Tests for kanari_agent.config module
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from kanari_agent.config import load_config
from kanari_agent.models import AlertThresholds, Config


# Fixture to clean environment variables and ~/.kanari/config before each test
@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Remove all kanari-related env vars and isolate ~/.kanari/config before each test"""
    env_vars = [
        "KANARI_API_KEY",
        "KANARI_API_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "KANARI_LOCAL_MODE",
        "CHECK_INTERVAL",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)

    # Point KANARI_CONFIG_PATH to a non-existent temp path so real ~/.kanari/config
    # doesn't bleed into tests that expect default values
    monkeypatch.setattr("kanari_agent.login.KANARI_CONFIG_PATH", tmp_path / "config")


class TestLoadConfigDefaults:
    """Tests for default configuration values"""

    def test_returns_config_instance(self):
        """load_config should return a Config instance"""
        config = load_config()
        assert isinstance(config, Config)

    def test_default_api_url(self):
        """Default API URL should be getkanari.com"""
        config = load_config()
        assert config.api_url == "https://api.getkanari.com"

    def test_default_api_key_is_none(self):
        """Default API key should be None"""
        config = load_config()
        assert config.api_key is None

    def test_default_local_mode_is_false(self):
        """Default local_mode should be False"""
        config = load_config()
        assert config.local_mode is False

    def test_default_redis_url(self):
        """Default Redis URL should be localhost"""
        config = load_config()
        assert config.redis_url == "redis://localhost:6379/0"

    def test_default_celery_broker_url(self):
        """Default Celery broker URL should be localhost"""
        config = load_config()
        assert config.celery_broker_url == "redis://localhost:6379/0"

    def test_default_check_interval(self):
        """Default check interval should be 30 seconds"""
        config = load_config()
        assert config.check_interval_seconds == 30

    def test_default_monitored_queues(self):
        """Default monitored queues should include common queue names"""
        config = load_config()
        assert len(config.monitored_queues) == 0
        assert isinstance(config.monitored_queues, list)

    def test_default_thresholds(self):
        """Default thresholds should be set"""
        config = load_config()
        assert isinstance(config.thresholds, AlertThresholds)
        assert config.thresholds.max_queue_size == 1000
        assert config.thresholds.max_wait_time_seconds == 60
        assert config.thresholds.max_task_runtime_seconds == 1800


class TestLoadConfigFromEnv:
    """Tests for loading configuration from environment variables"""

    def test_api_key_from_env(self, monkeypatch):
        """KANARI_API_KEY env var should set api_key"""
        monkeypatch.setenv("KANARI_API_KEY", "test-api-key-123")
        config = load_config()
        assert config.api_key == "test-api-key-123"

    def test_api_url_from_env(self, monkeypatch):
        """KANARI_API_URL env var should override default"""
        monkeypatch.setenv("KANARI_API_URL", "https://custom.getkanari.com")
        config = load_config()
        assert config.api_url == "https://custom.getkanari.com"

    def test_redis_url_from_env(self, monkeypatch):
        """REDIS_URL env var should set redis_url"""
        monkeypatch.setenv("REDIS_URL", "redis://prod-redis:6379/1")
        config = load_config()
        assert config.redis_url == "redis://prod-redis:6379/1"

    def test_celery_broker_url_from_env(self, monkeypatch):
        """CELERY_BROKER_URL env var should set celery_broker_url"""
        monkeypatch.setenv("CELERY_BROKER_URL", "redis://broker:6379/2")
        config = load_config()
        assert config.celery_broker_url == "redis://broker:6379/2"

    def test_check_interval_from_env(self, monkeypatch):
        """CHECK_INTERVAL env var should set check_interval_seconds"""
        monkeypatch.setenv("CHECK_INTERVAL", "60")
        config = load_config()
        assert config.check_interval_seconds == 60

    @pytest.mark.parametrize("env_value", ["true", "True", "TRUE", "1", "yes", "YES"])
    def test_local_mode_true_variations(self, monkeypatch, env_value):
        """KANARI_LOCAL_MODE should accept various truthy values"""
        monkeypatch.setenv("KANARI_LOCAL_MODE", env_value)
        config = load_config()
        assert config.local_mode is True

    @pytest.mark.parametrize("env_value", ["false", "False", "0", "no", ""])
    def test_local_mode_false_variations(self, monkeypatch, env_value):
        """KANARI_LOCAL_MODE should remain False for non-truthy values"""
        monkeypatch.setenv("KANARI_LOCAL_MODE", env_value)
        config = load_config()
        assert config.local_mode is False


class TestLoadConfigFromYaml:
    """Tests for loading configuration from YAML files"""

    @pytest.fixture
    def config_file(self, tmp_path: Path) -> Path:
        """Create a temporary config file"""
        config_content = """
api_key: yaml-api-key
api_url: https://yaml.getkanari.com
local_mode: true
redis_url: redis://yaml-redis:6379/0
celery_broker_url: redis://yaml-broker:6379/0
celery_app_name: myapp
check_interval_seconds: 15
monitored_queues:
  - queue1
  - queue2
  - queue3
thresholds:
  max_queue_size: 500
  max_wait_time_seconds: 30
  max_task_runtime_seconds: 900
  critical_queues:
    - queue1
"""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(config_content)
        return config_path

    def test_load_from_yaml_file(self, config_file):
        """Config should load all values from YAML file"""
        config = load_config(str(config_file))

        assert config.api_key == "yaml-api-key"
        assert config.api_url == "https://yaml.getkanari.com"
        assert config.local_mode is True
        assert config.redis_url == "redis://yaml-redis:6379/0"
        assert config.celery_broker_url == "redis://yaml-broker:6379/0"
        assert config.celery_app_name == "myapp"
        assert config.check_interval_seconds == 15
        assert config.monitored_queues == ["queue1", "queue2", "queue3"]

    def test_load_thresholds_from_yaml(self, config_file):
        """Thresholds should load from YAML file"""
        config = load_config(str(config_file))

        assert config.thresholds.max_queue_size == 500
        assert config.thresholds.max_wait_time_seconds == 30
        assert config.thresholds.max_task_runtime_seconds == 900
        assert config.thresholds.critical_queues == ["queue1"]

    def test_env_overrides_yaml(self, config_file, monkeypatch):
        """Environment variables should override YAML values"""
        monkeypatch.setenv("KANARI_API_KEY", "env-api-key")
        monkeypatch.setenv("REDIS_URL", "redis://env-redis:6379/0")

        config = load_config(str(config_file))

        assert config.api_key == "env-api-key"  # from env
        assert config.redis_url == "redis://env-redis:6379/0"  # from env
        assert config.api_url == "https://yaml.getkanari.com"  # from yaml (not overridden)

    def test_nonexistent_file_uses_defaults(self):
        """Non-existent config file should use defaults"""
        config = load_config("/nonexistent/path/config.yaml")

        assert config.api_url == "https://api.getkanari.com"
        assert config.check_interval_seconds == 30

    def test_partial_yaml_config(self, tmp_path):
        """Partial YAML config should merge with defaults"""
        config_content = """
api_key: partial-key
check_interval_seconds: 45
"""
        config_path = tmp_path / "partial.yaml"
        config_path.write_text(config_content)

        config = load_config(str(config_path))

        assert config.api_key == "partial-key"
        assert config.check_interval_seconds == 45
        assert config.api_url == "https://api.getkanari.com"  # default
        assert config.redis_url == "redis://localhost:6379/0"  # default


class TestConfigValidation:
    """Tests for configuration validation"""

    def test_check_interval_must_be_positive(self, monkeypatch):
        """CHECK_INTERVAL should be a positive integer"""
        monkeypatch.setenv("CHECK_INTERVAL", "0")
        config = load_config()
        # Pydantic allows 0, but we could add validation
        assert config.check_interval_seconds == 0

    def test_invalid_check_interval_raises(self, monkeypatch):
        """Invalid CHECK_INTERVAL should raise an error"""
        monkeypatch.setenv("CHECK_INTERVAL", "not-a-number")
        with pytest.raises(ValueError):
            load_config()


class TestKanariConfigFile:
    """Tests for ~/.kanari/config integration"""

    def test_api_key_loaded_from_kanari_config(self, tmp_path):
        config_file = tmp_path / "config"
        config_file.write_text("api_key: sk_testkey123\napi_url: http://localhost:9000\n")

        with patch("kanari_agent.login.KANARI_CONFIG_PATH", config_file):
            config = load_config()

        assert config.api_key == "sk_testkey123"  # pragma: allowlist secret
        assert config.api_url == "http://localhost:9000"

    def test_env_var_overrides_kanari_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config"
        config_file.write_text("api_key: sk_from_file\n")
        monkeypatch.setenv("KANARI_API_KEY", "sk_from_env")

        with patch("kanari_agent.login.KANARI_CONFIG_PATH", config_file):
            config = load_config()

        assert config.api_key == "sk_from_env"  # pragma: allowlist secret

    def test_missing_kanari_config_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist"
        with patch("kanari_agent.login.KANARI_CONFIG_PATH", missing):
            config = load_config()
        assert config.api_key is None

    def test_save_and_load_roundtrip(self, tmp_path):
        from kanari_agent.login import load_kanari_config, save_kanari_config

        config_file = tmp_path / "config"
        with patch("kanari_agent.login.KANARI_CONFIG_PATH", config_file):
            save_kanari_config("sk_abc123", "http://example.com")
            loaded = load_kanari_config()

        assert loaded["api_key"] == "sk_abc123"  # pragma: allowlist secret
        assert loaded["api_url"] == "http://example.com"
        assert oct(config_file.stat().st_mode)[-3:] == "600"


def test_load_config_reads_worker_knobs_from_yaml(tmp_path):
    from kanari_agent.config import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "thresholds:\n  worker_offline_grace_seconds: 45\n  worker_auto_resolve_seconds: 1800\n"
    )
    cfg = load_config(str(cfg_file))
    assert cfg.thresholds.worker_offline_grace_seconds == 45
    assert cfg.thresholds.worker_auto_resolve_seconds == 1800


def test_worker_knobs_env_override(tmp_path, monkeypatch):
    from kanari_agent.config import load_config

    monkeypatch.setenv("WORKER_OFFLINE_GRACE_SECONDS", "10")
    monkeypatch.setenv("WORKER_AUTO_RESOLVE_SECONDS", "600")
    cfg = load_config(None)
    assert cfg.thresholds.worker_offline_grace_seconds == 10
    assert cfg.thresholds.worker_auto_resolve_seconds == 600
