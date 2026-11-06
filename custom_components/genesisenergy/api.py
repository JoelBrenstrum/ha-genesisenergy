"""Genesis Energy API."""

import json
import logging
import time
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs

import aiohttp
from homeassistant.util.ssl import get_default_context

from .usage import date_chunks, merge_usage

_LOGGER = logging.getLogger(__name__)

TOKEN_RENEWAL_THRESHOLD_SECONDS = timedelta(minutes=5).total_seconds()


class GenesisEnergyAuthError(Exception):
    """Raised when the Genesis Energy login flow fails."""


class GenesisEnergyApiError(Exception):
    """Raised when a Genesis Energy data request fails."""


def get_setting_json(page: str) -> Mapping[str, Any] | None:
    """Extract the B2C SETTINGS object from the login page."""
    for line in page.splitlines():
        if line.startswith("var SETTINGS = ") and line.endswith(";"):
            json_string = line.removeprefix("var SETTINGS = ").removesuffix(";")
            return json.loads(json_string)
    return None


def parse_auth_code(location: str) -> str:
    """Return the auth code from the B2C redirect Location header."""
    if "?" not in location:
        raise GenesisEnergyAuthError(
            f"Login did not redirect with an auth code (Location: {location!r}); check email/password"
        )
    query_params = parse_qs(location.split("?", 1)[1])
    if "error" in query_params:
        description = query_params.get("error_description", [""])[0]
        raise GenesisEnergyAuthError(f"{query_params['error'][0]}: {description}")
    if "code" not in query_params:
        raise GenesisEnergyAuthError(f"No auth code in redirect: {location!r}")
    return query_params["code"][0]


def expires_at(expires_in: Any, now: float) -> float:
    """Convert a relative expires_in value into an absolute timestamp."""
    try:
        return now + float(expires_in)
    except (TypeError, ValueError):
        return 0


def needs_renewal(expiry: float, now: float) -> bool:
    """Return True when a token expires within the renewal threshold."""
    return expiry - now <= TOKEN_RENEWAL_THRESHOLD_SECONDS


def _new_session() -> aiohttp.ClientSession:
    # B2C cookies contain characters newer aiohttp versions quote, which breaks the login flow
    return aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(quote_cookie=False),
        connector=aiohttp.TCPConnector(ssl=get_default_context()),
    )


class GenesisEnergyApi:
    """Define the Genesis Energy API."""

    def __init__(self, email, password):
        """Initialise the API."""
        self._client_id = "8e41676f-7601-4490-9786-85d74f387f47"
        self._redirect_uri = "https://myaccount.genesisenergy.co.nz/auth/redirect"
        self._url_token_base = "https://auth.genesisenergy.co.nz/auth.genesisenergy.co.nz"
        self._url_data_base = "https://web-api.genesisenergy.co.nz"
        self._p = "B2C_1A_signin"

        self._email = email
        self._password = password

        self._token = None
        self._refresh_token = None
        self._refresh_token_expires_at = 0.0
        self._access_token_expires_at = 0.0

    def _store_tokens(self, response_data: Mapping[str, Any]) -> None:
        now = time.time()
        self._token = response_data["access_token"]
        self._access_token_expires_at = expires_at(response_data.get("expires_in"), now)
        if response_data.get("refresh_token"):
            self._refresh_token = response_data["refresh_token"]
            self._refresh_token_expires_at = expires_at(
                response_data.get("refresh_token_expires_in"), now
            )

    async def get_refresh_token(self):
        """Log in with email/password and obtain access and refresh tokens."""
        _LOGGER.debug("API get_refresh_token")
        scope = f"openid offline_access {self._client_id}"

        async with _new_session() as session:
            _LOGGER.debug("Step 1: authorize")
            params = {
                "p": self._p,
                "client_id": self._client_id,
                "response_type": "code",
                "response_mode": "query",
                "scope": scope,
                "redirect_uri": self._redirect_uri,
            }
            async with session.get(
                f"{self._url_token_base}/oauth2/v2.0/authorize", params=params
            ) as response:
                response_text = await response.text()

            settings_json = get_setting_json(response_text)
            if settings_json is None:
                raise GenesisEnergyAuthError("Could not find SETTINGS on the login page")
            trans_id = settings_json.get("transId")
            csrf = settings_json.get("csrf")

            self_asserted_url = (
                f"{self._url_token_base}/{self._p}/SelfAsserted?tx={trans_id}&p={self._p}"
            )

            _LOGGER.debug("Step 2: submit email")
            async with session.post(
                self_asserted_url,
                headers={"X-CSRF-TOKEN": csrf},
                data={"request_type": "RESPONSE", "email": self._email},
            ) as response:
                _LOGGER.debug("Step 2 response: %s", await response.text())

            _LOGGER.debug("Step 3: confirm email")
            async with session.get(
                f"{self._url_token_base}/{self._p}/api/SelfAsserted/confirmed",
                params={"csrf_token": csrf, "tx": trans_id, "p": self._p},
            ) as response:
                await response.text()
                csrf_cookie = response.cookies.get("x-ms-cpim-csrf")
            if csrf_cookie is None:
                raise GenesisEnergyAuthError("Missing CSRF cookie during login flow")
            csrf = csrf_cookie.value

            _LOGGER.debug("Step 4: submit password")
            async with session.post(
                self_asserted_url,
                headers={"X-CSRF-TOKEN": csrf},
                data={
                    "request_type": "RESPONSE",
                    "signInName": self._email,
                    "password": self._password,
                },
            ) as response:
                _LOGGER.debug("Step 4 response: %s", await response.text())

            _LOGGER.debug("Step 5: confirm sign in")
            async with session.get(
                f"{self._url_token_base}/{self._p}/api/CombinedSigninAndSignup/confirmed",
                params={
                    "rememberMe": "false",
                    "csrf_token": csrf,
                    "tx": trans_id,
                    "p": self._p,
                },
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                code = parse_auth_code(response.headers.get("Location", ""))

            _LOGGER.debug("Step 6: exchange code for tokens")
            async with session.get(
                f"{self._url_token_base}/{self._p}/oauth2/v2.0/token",
                params={
                    "p": self._p,
                    "grant_type": "authorization_code",
                    "client_id": self._client_id,
                    "scope": scope,
                    "redirect_uri": self._redirect_uri,
                    "code": code,
                },
            ) as response:
                response_data = await response.json(content_type=None)

        if "access_token" not in response_data:
            raise GenesisEnergyAuthError(f"Token exchange failed: {response_data}")
        self._store_tokens(response_data)
        _LOGGER.debug("Refresh token retrieved successfully")

    async def get_api_token(self):
        """Renew the access token using the refresh token."""
        token_data = {
            "p": self._p,
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": self._refresh_token,
        }

        async with _new_session() as session, session.post(
            f"{self._url_token_base}/oauth2/v2.0/token", data=token_data
        ) as response:
            if response.status == 200:
                self._store_tokens(await response.json(content_type=None))
                return True
            _LOGGER.error(
                "Failed to renew access token (%s): %s",
                response.status,
                await response.text(),
            )
            return False

    async def _ensure_token(self):
        now = time.time()
        if self._refresh_token is None or needs_renewal(self._refresh_token_expires_at, now):
            _LOGGER.debug("Refresh token needs renewing")
            await self.get_refresh_token()
        elif needs_renewal(self._access_token_expires_at, now):
            _LOGGER.debug("Access token needs renewing")
            if not await self.get_api_token():
                await self.get_refresh_token()

    async def _fetch(self, session: aiohttp.ClientSession, method: str, path: str, **kwargs):
        await self._ensure_token()
        headers = {"authorization": f"Bearer {self._token}"}
        async with session.request(
            method, f"{self._url_data_base}{path}", headers=headers, **kwargs
        ) as response:
            if response.status == 200:
                return await response.json(content_type=None)
            raise GenesisEnergyApiError(
                f"{path} returned {response.status}: {await response.text()}"
            )

    async def _get_usage(self, method: str, path: str, body_key: str, start: date, end: date):
        chunks = []
        async with _new_session() as session:
            for chunk_start, chunk_end in date_chunks(start, end):
                request = {
                    "startDate": chunk_start.isoformat(),
                    "endDate": chunk_end.isoformat(),
                    "intervalType": "HOURLY",
                }
                try:
                    data = await self._fetch(session, method, path, **{body_key: request})
                except (GenesisEnergyApiError, aiohttp.ClientError) as err:
                    if chunks:
                        # Stop so the next refresh resumes here instead of leaving a permanent gap
                        _LOGGER.warning("Stopping usage fetch at %s: %s", chunk_start, err)
                        break
                    _LOGGER.debug("No usage available for %s: %s", chunk_start, err)
                    continue
                usage = data.get("usage") if isinstance(data, dict) else None
                if usage:
                    chunks.append(usage)
        return merge_usage(chunks)

    async def get_energy_usage(self, start: date, end: date):
        """Get hourly electricity usage between two dates (inclusive)."""
        return await self._get_usage(
            "POST", "/v2/private/electricity/site-usage", "json", start, end
        )

    async def get_gas_usage(self, start: date, end: date):
        """Get hourly gas usage between two dates (inclusive)."""
        return await self._get_usage(
            "GET", "/v2/private/naturalgas/advanced/usage", "params", start, end
        )
