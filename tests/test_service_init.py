# tests/test_service_init.py
"""A service that isn't up yet at boot must be retried, never silently disabled."""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

if "settings" not in sys.modules:
    # The sample settings stand in for a real settings.py (which holds secrets).
    sample = os.path.join(os.path.dirname(__file__), "..", "settings-sample.py")
    module = types.ModuleType("settings")
    with open(sample) as f:
        exec(f.read(), module.__dict__)
    sys.modules["settings"] = module
import settings  # noqa: E402

from bot import searcharr_bot  # noqa: E402
from bot.searcharr_bot import SearcharrBot  # noqa: E402


class FlakySonarr:
    """Fails `failures` times, then connects."""
    calls = 0
    failures = 0

    def __init__(self, url, key, verbose=False):
        FlakySonarr.calls += 1
        if FlakySonarr.calls <= FlakySonarr.failures:
            raise ConnectionError("Connection refused")


@pytest.fixture
def bot(monkeypatch):
    settings.sonarr_enabled = True
    settings.sonarr_url = "http://sonarr.invalid"
    settings.sonarr_api_key = "x"
    FlakySonarr.calls = 0
    monkeypatch.setattr(searcharr_bot, "Sonarr", FlakySonarr)
    monkeypatch.setattr("bot.services.sonarr_service.configure_sonarr", lambda c: c)
    sleeps = []
    b = SearcharrBot.__new__(SearcharrBot)
    b.logger = searcharr_bot.set_up_logger("test")
    b._sleep = sleeps.append
    b.sleeps = sleeps
    return b


def test_a_service_still_starting_is_retried_until_it_answers(bot):
    FlakySonarr.failures = 3
    client = bot._init_service("sonarr", False)
    assert isinstance(client, FlakySonarr)
    assert FlakySonarr.calls == 4
    assert settings.sonarr_enabled is True
    assert bot.sleeps == [5, 10, 20]


def test_the_wait_between_tries_is_capped(bot):
    FlakySonarr.failures = 7
    bot._init_service("sonarr", False)
    assert bot.sleeps == [5, 10, 20, 40, 60, 60, 60]


def test_a_service_that_never_answers_exits_so_docker_restarts_it(bot):
    FlakySonarr.failures = 10**6
    with pytest.raises(SystemExit) as exit_info:
        bot._init_service("sonarr", False)
    assert exit_info.value.code == 75
    assert sum(bot.sleeps) >= 600, "keeps trying for at least ten minutes first"
    assert settings.sonarr_enabled is True, "never silently turned off"
