# tests/test_api_url_format.py
"""Which API path each Sonarr/Radarr version gets (no live server needed)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.api_client import ApiClient  # noqa: E402


def url_for(service, version):
    client = ApiClient.__new__(ApiClient)
    client.service_name = service
    client.version = version
    return client._get_api_url_format("http://host:8989", "KEY")


@pytest.mark.parametrize("version", ["3.0.10.1567", "4.0.15.2941", "5.0.0.2668", "10.1.0.1"])
def test_sonarr_3_and_newer_use_the_v3_api(version):
    assert url_for("sonarr", version) == "http://host:8989/api/v3/{endpoint}?apikey=KEY"


@pytest.mark.parametrize("version", ["2.0.0.5344", None])
def test_sonarr_2_uses_the_legacy_api(version):
    assert url_for("sonarr", version) == "http://host:8989/api/{endpoint}?apikey=KEY"


@pytest.mark.parametrize("version", ["3.2.2.5080", "5.26.2.10099", "6.4.4.10685"])
def test_radarr_3_and_newer_use_the_v3_api(version):
    assert url_for("radarr", version) == "http://host:8989/api/v3/{endpoint}?apikey=KEY"


def test_radarr_0_uses_the_legacy_api():
    assert url_for("radarr", "0.2.0.1504") == "http://host:8989/api/{endpoint}?apikey=KEY"
