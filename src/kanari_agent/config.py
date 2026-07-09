"""
Configuration loading for Kanari Agent
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from typing import Optional

from kanari_agent.models import AlertThresholds, Config, PrivacyConfig

try:
    AGENT_VERSION = version("kanari")
except PackageNotFoundError:
    AGENT_VERSION = "0.1.0"  # fallback for dev installs

# Optional YAML support
try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    yaml = None  # type: ignore[assignment]
    YAML_AVAILABLE = False


def load_config(config_path: Optional[str] = None) -> Config:
    """Loads configuration from YAML file or environment variables"""
    config_data: dict = {}

    # Load from YAML file if it exists
    if config_path and os.path.exists(config_path):
        if not YAML_AVAILABLE:
            print("⚠️  PyYAML not installed. Install with: pip install pyyaml")
        else:
            with open(config_path) as f:
                yaml_config = yaml.safe_load(f) or {}

            # Map YAML to config dict
            config_data = {
                "api_key": yaml_config.get("api_key"),
                "api_url": yaml_config.get("api_url", ""),
                "local_mode": yaml_config.get("local_mode", False),
                "redis_url": yaml_config.get("redis_url", "redis://localhost:6379/0"),
                "celery_broker_url": yaml_config.get(
                    "celery_broker_url", "redis://localhost:6379/0"
                ),
                "celery_app_name": yaml_config.get("celery_app_name", "tasks"),
                "check_interval_seconds": yaml_config.get("check_interval_seconds", 30),
            }

            # Only set monitored_queues if explicitly configured
            if "monitored_queues" in yaml_config:
                config_data["monitored_queues"] = yaml_config["monitored_queues"]

            # Handle thresholds
            if "thresholds" in yaml_config:
                t = yaml_config["thresholds"]
                threshold_data = {
                    "max_queue_size": t.get("max_queue_size", 1000),
                    "max_wait_time_seconds": t.get("max_wait_time_seconds", 60),
                    "max_task_runtime_seconds": t.get("max_task_runtime_seconds", 1800),
                    "worker_offline_grace_seconds": t.get("worker_offline_grace_seconds", 90),
                    "worker_auto_resolve_seconds": t.get("worker_auto_resolve_seconds"),
                }
                # Only set critical_queues if explicitly configured
                if "critical_queues" in t:
                    threshold_data["critical_queues"] = t["critical_queues"]
                config_data["thresholds"] = AlertThresholds(**threshold_data)

            # Handle privacy settings
            if "privacy" in yaml_config:
                p = yaml_config["privacy"]
                privacy_data = {}
                if "sanitize_task_signatures" in p:
                    privacy_data["sanitize_task_signatures"] = p["sanitize_task_signatures"]
                config_data["privacy"] = PrivacyConfig(**privacy_data)

    # ~/.kanari/config (written by kanari login) overrides config.yaml
    from kanari_agent.login import load_kanari_config

    kanari_cfg = load_kanari_config()
    if kanari_cfg.get("api_key") and not config_data.get("api_key"):
        config_data["api_key"] = kanari_cfg["api_key"]
    if kanari_cfg.get("api_url") and not config_data.get("api_url"):
        config_data["api_url"] = kanari_cfg["api_url"]

    # Environment variables override everything
    if os.environ.get("KANARI_API_KEY"):
        config_data["api_key"] = os.environ["KANARI_API_KEY"]
    if os.environ.get("KANARI_API_URL"):
        config_data["api_url"] = os.environ["KANARI_API_URL"]

    # Final fallback: hardcoded default if no source provided a value
    if not config_data.get("api_url"):
        config_data["api_url"] = "https://api.getkanari.com"

    if os.environ.get("REDIS_URL"):
        config_data["redis_url"] = os.environ["REDIS_URL"]
    if os.environ.get("CELERY_BROKER_URL"):
        config_data["celery_broker_url"] = os.environ["CELERY_BROKER_URL"]

    # Local mode from env
    local_mode_env = os.environ.get("KANARI_LOCAL_MODE", "").lower()
    if local_mode_env in ("true", "1", "yes"):
        config_data["local_mode"] = True

    if os.environ.get("CHECK_INTERVAL"):
        config_data["check_interval_seconds"] = int(os.environ["CHECK_INTERVAL"])

    grace_env = os.environ.get("WORKER_OFFLINE_GRACE_SECONDS")
    auto_env = os.environ.get("WORKER_AUTO_RESOLVE_SECONDS")
    if grace_env is not None or auto_env is not None:
        existing = config_data.get("thresholds")
        base = existing.model_dump() if isinstance(existing, AlertThresholds) else {}
        if grace_env is not None:
            base["worker_offline_grace_seconds"] = int(grace_env)
        if auto_env is not None:
            base["worker_auto_resolve_seconds"] = int(auto_env)
        config_data["thresholds"] = AlertThresholds(**base)

    # Privacy settings from env
    sanitize_env = os.environ.get("KANARI_SANITIZE_TASK_SIGNATURES", "").lower()
    if sanitize_env in ("false", "0", "no"):
        config_data["privacy"] = PrivacyConfig(sanitize_task_signatures=False)

    # Create and validate config with Pydantic
    return Config(**{k: v for k, v in config_data.items() if v is not None})
