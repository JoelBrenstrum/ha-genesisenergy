import pytest

from custom_components.genesisenergy.api import (
    GenesisEnergyAuthError,
    expires_at,
    get_setting_json,
    needs_renewal,
    parse_auth_code,
)


def test_get_setting_json_extracts_settings():
    page = 'foo\nvar SETTINGS = {"transId": "abc", "csrf": "xyz"};\nbar'
    assert get_setting_json(page) == {"transId": "abc", "csrf": "xyz"}


def test_get_setting_json_missing_returns_none():
    assert get_setting_json("<html></html>") is None


def test_parse_auth_code_returns_code():
    assert parse_auth_code("https://x/auth/redirect?code=CODE123&state=s") == "CODE123"


def test_parse_auth_code_raises_on_error():
    with pytest.raises(GenesisEnergyAuthError, match="access_denied: bad password"):
        parse_auth_code("https://x/redirect?error=access_denied&error_description=bad+password")


def test_parse_auth_code_raises_without_query():
    with pytest.raises(GenesisEnergyAuthError, match="check email/password"):
        parse_auth_code("")


def test_parse_auth_code_raises_without_code():
    with pytest.raises(GenesisEnergyAuthError, match="No auth code"):
        parse_auth_code("https://x/redirect?state=s")


def test_expires_at_is_absolute():
    assert expires_at(3600, now=1000) == 4600
    assert expires_at("60", now=1000) == 1060
    assert expires_at(None, now=1000) == 0


def test_needs_renewal():
    assert needs_renewal(0, now=1000)
    assert needs_renewal(1000 + 299, now=1000)
    assert not needs_renewal(1000 + 3600, now=1000)
