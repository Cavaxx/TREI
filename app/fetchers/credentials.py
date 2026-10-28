"""Credential resolution for authorized feeds.

Secrets are supplied by the operator through the environment (12-factor) and are
never written into the repository, the licence register, the audit log, or any
exception message. This module therefore only ever *names* what is missing.

Env var convention, per source (``TREI_<SOURCE>_...``)::

    TREI_IMMOBILIARE_IT_CREDENTIAL_KIND      # api_key | bearer | basic | oauth2_client_credentials
    TREI_IMMOBILIARE_IT_API_KEY
    TREI_IMMOBILIARE_IT_API_KEY_HEADER       # default X-API-Key
    TREI_IMMOBILIARE_IT_BEARER_TOKEN
    TREI_IMMOBILIARE_IT_USERNAME / _PASSWORD
    TREI_IMMOBILIARE_IT_CLIENT_ID / _CLIENT_SECRET / _TOKEN_URL / _SCOPE

If the grantor issues a static access token instead of a refresh grant, it is
passed as ``_BEARER_TOKEN`` and no token exchange is performed.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

import httpx


class CredentialKind(str, Enum):
    NONE = "none"
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"
    OAUTH2_CLIENT_CREDENTIALS = "oauth2_client_credentials"


class MissingCredentials(Exception):
    """Raised when an authorized feed needs credentials that are not present.

    The message lists environment variable *names* only - never values.
    """

    def __init__(self, source: str, expected: list[str]) -> None:
        self.source = source
        self.expected = expected
        super().__init__(
            f"{source}: no usable credential found. Set one of: {', '.join(expected)}"
        )


def prefix_for(source: str) -> str:
    """Return the env-var prefix for a registry source name."""
    return "TREI_" + re.sub(r"[^A-Z0-9]+", "_", source.upper()).strip("_")


@dataclass(frozen=True)
class Credentials:
    kind: CredentialKind = CredentialKind.NONE
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    api_key_query_param: str | None = None
    bearer_token: str | None = None
    username: str | None = None
    password: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    token_url: str | None = None
    scope: str | None = None

    def headers(self) -> dict[str, str]:
        # A grantor that expects the key in the query must not also receive it
        # as a header (some gateways reject unknown headers).
        if self.kind is CredentialKind.API_KEY and self.api_key and not self.api_key_query_param:
            return {self.api_key_header: self.api_key}
        if self.kind in (CredentialKind.BEARER, CredentialKind.OAUTH2_CLIENT_CREDENTIALS):
            if self.bearer_token:
                return {"Authorization": f"Bearer {self.bearer_token}"}
        return {}

    def query(self) -> dict[str, str]:
        if self.kind is CredentialKind.API_KEY and self.api_key_query_param and self.api_key:
            return {self.api_key_query_param: self.api_key}
        return {}

    def auth(self) -> httpx.Auth | None:
        if self.kind is CredentialKind.BASIC and self.username is not None:
            return httpx.BasicAuth(self.username, self.password or "")
        if self.kind is CredentialKind.OAUTH2_CLIENT_CREDENTIALS and self.token_url:
            return OAuth2ClientCredentials(
                self.token_url, self.client_id or "", self.client_secret or "", self.scope
            )
        return None

    def redacted(self) -> dict:
        """Safe-to-log description. Never contains secret material."""
        return {
            "kind": self.kind.value,
            "api_key_header": self.api_key_header if self.api_key else None,
            "has_api_key": bool(self.api_key),
            "has_bearer_token": bool(self.bearer_token),
            "has_basic_auth": bool(self.username),
            "has_client_credentials": bool(self.client_id and self.client_secret),
            "token_url": self.token_url,
            "scope": self.scope,
        }


class OAuth2ClientCredentials(httpx.Auth):
    """RFC 6749 client-credentials grant for httpx, with an in-memory token cache."""

    def __init__(
        self, token_url: str, client_id: str, client_secret: str, scope: str | None = None
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self._token: str | None = None
        self._expires_at: float = 0.0

    def _fetch_token(self) -> str:
        data = {"grant_type": "client_credentials"}
        if self.scope:
            data["scope"] = self.scope
        resp = httpx.post(
            self.token_url,
            data=data,
            auth=httpx.BasicAuth(self.client_id, self.client_secret),
            timeout=20.0,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = str(body["access_token"])
        # Refresh 30s early so a slow request never races the expiry.
        self._expires_at = time.monotonic() + float(body.get("expires_in", 3600)) - 30.0
        return self._token

    def token(self) -> str:
        if self._token is None or time.monotonic() >= self._expires_at:
            return self._fetch_token()
        return self._token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {self.token()}"
        yield request


class CredentialStore:
    """Resolves credentials for a source from the process environment."""

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env: Mapping[str, str] = env if env is not None else os.environ

    def env_names(self, source: str) -> dict[str, str]:
        p = prefix_for(source)
        return {
            "kind": f"{p}_CREDENTIAL_KIND",
            "api_key": f"{p}_API_KEY",
            "bearer_token": f"{p}_BEARER_TOKEN",
            "username": f"{p}_USERNAME",
            "password": f"{p}_PASSWORD",
            "client_id": f"{p}_CLIENT_ID",
            "client_secret": f"{p}_CLIENT_SECRET",
            "token_url": f"{p}_TOKEN_URL",
        }

    def expected(self, source: str) -> list[str]:
        n = self.env_names(source)
        return [
            n["api_key"],
            n["bearer_token"],
            f"{n['username']}+{n['password']}",
            n["client_id"],
        ]

    def resolve(self, source: str, kind: CredentialKind | None = None) -> Credentials:
        """Return credentials for ``source``, or raise :class:`MissingCredentials`."""
        creds = self.optional(source, kind)
        if creds.kind is CredentialKind.NONE:
            raise MissingCredentials(source, self.expected(source))
        return creds

    def optional(self, source: str, kind: CredentialKind | None = None) -> Credentials:
        """As :meth:`resolve`, but ``NONE`` is a valid answer.

        Used for channels that need no credential - e.g. an export file the
        operator received from the portal and dropped on disk.
        """
        return self._build(source, kind)

    def _build(self, source: str, kind: CredentialKind | None) -> Credentials:
        names = self.env_names(source)
        p = prefix_for(source)

        def get(key: str) -> str | None:
            return (self._env.get(key) or "").strip() or None

        api_key = get(names["api_key"])
        bearer = get(names["bearer_token"])
        username, password = get(names["username"]), get(names["password"])
        client_id, client_secret = get(names["client_id"]), get(names["client_secret"])
        token_url = get(names["token_url"])

        explicit = kind or get(f"{p}_CREDENTIAL_KIND")
        if explicit:
            try:
                chosen = CredentialKind(str(explicit).strip().lower())
            except ValueError as exc:
                raise MissingCredentials(source, self.expected(source)) from exc
        elif client_id and client_secret and token_url:
            chosen = CredentialKind.OAUTH2_CLIENT_CREDENTIALS
        elif bearer:
            chosen = CredentialKind.BEARER
        elif api_key:
            chosen = CredentialKind.API_KEY
        elif username and password:
            chosen = CredentialKind.BASIC
        else:
            chosen = CredentialKind.NONE

        return Credentials(
            kind=chosen,
            api_key=api_key,
            api_key_header=get(f"{p}_API_KEY_HEADER") or "X-API-Key",
            api_key_query_param=get(f"{p}_API_KEY_QUERY_PARAM"),
            bearer_token=bearer,
            username=username,
            password=password,
            client_id=client_id,
            client_secret=client_secret,
            token_url=token_url,
            scope=get(f"{p}_SCOPE"),
        )
