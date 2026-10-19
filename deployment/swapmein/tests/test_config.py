import pytest

from swapmein.config import Settings


def test_gpu_region_can_move_without_relocating_gateway_and_queues(monkeypatch):
    monkeypatch.setenv("REGION", "us-east4")
    monkeypatch.setenv("GPU_REGION", "us-central1")
    settings = Settings.from_env()
    assert settings.region == "us-east4"
    assert settings.gpu_region == "us-central1"


@pytest.fixture
def clean_preparation_env(monkeypatch):
    monkeypatch.delenv("DAILY_PREPARATIONS", raising=False)
    monkeypatch.delenv("MONTHLY_PREPARATIONS", raising=False)


def test_default_limits_support_100_attempts_on_every_day_of_a_31_day_month(clean_preparation_env):
    settings = Settings.from_env()
    assert settings.daily_preparations == 100
    assert settings.monthly_preparations == 31 * settings.daily_preparations


def test_preparation_limits_can_be_configured_independently(clean_preparation_env, monkeypatch):
    monkeypatch.setenv("DAILY_PREPARATIONS", "17")
    monkeypatch.setenv("MONTHLY_PREPARATIONS", "527")
    settings = Settings.from_env()
    assert settings.daily_preparations == 17 and settings.monthly_preparations == 527


@pytest.mark.parametrize("name", ["DAILY_PREPARATIONS", "MONTHLY_PREPARATIONS"])
@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "true", " 100 ", "1e2", "１００"])
def test_preparation_limit_env_rejects_non_positive_or_non_integer_values(
        clean_preparation_env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name + " must be a positive whole number"):
        Settings.from_env()
