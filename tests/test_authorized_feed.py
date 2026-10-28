"""Tests for the authorized-feed layer: licence gate, credential store, audit log.

The point of these tests is the *refusals*: a source that is not covered by a
signed licence, or whose endpoint is not named in it, must never be requested.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
import yaml

from app.fetchers.adapters.feed import AuthorizedFeedAdapter, FeedScopeError
from app.fetchers.adapters.parsing import parse_payload
from app.fetchers.audit import (
    AccessEvent,
    AccessLog,
    AccessOutcome,
    is_secret_key,
    redact,
    scrub_url,
)
from app.fetchers.base import RobotsDisallowed
from app.fetchers.credentials import (
    CredentialKind,
    Credentials,
    CredentialStore,
    MissingCredentials,
    OAuth2ClientCredentials,
)
from app.fetchers.licence import LicenceRecord, LicenceRegistry, NotLicensed
from app.fetchers.registry import SourceConfig, SourceRegistry

API_BASE = "https://api.partner.example/v2"


def make_config(
    name: str = "partner_portal",
    tier: str = "tier2_feed",
    status: str = "LICENSED",
    domain: str = "api.partner.example",
) -> SourceConfig:
    return SourceConfig(
        name=name,
        domain=domain,
        kind="portal",
        tier=tier,
        status=status,
        recommended_mode="licensed API / authorized export only",
        requires_auth=True,
    )


def make_licence(**overrides) -> LicenceRecord:
    today = date.today()
    base = dict(
        source="partner_portal",
        grantor="Partner Portal S.p.A.",
        contract_ref="TREI-LIC-001",
        valid_from=(today - timedelta(days=10)).isoformat(),
        valid_to=(today + timedelta(days=355)).isoformat(),
        endpoints=[f"{API_BASE}/listings"],
        scope=["read_listings"],
        permitted_formats=["json", "csv", "xml", "jsonl"],
        attribution="Dati forniti da Partner Portal",
    )
    base.update(overrides)
    return LicenceRecord(**base)


def write_licence_file(tmp_path, records: list[dict]):
    path = tmp_path / "source_licences.yaml"
    path.write_text(yaml.safe_dump({"licences": records}, allow_unicode=True), encoding="utf-8")
    return path


@pytest.fixture
def robots_allow(monkeypatch):
    """Make RobotsCache see a 404 (no rules => allowed) without touching network."""
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(404, request=httpx.Request("GET", url)),
    )


@pytest.fixture
def audit(tmp_path) -> AccessLog:
    return AccessLog(tmp_path / "access.jsonl", actor="tester")


# --- licence register -------------------------------------------------------
def test_active_licence_covers_named_endpoint_only():
    registry = LicenceRegistry([make_licence()])
    assert registry.status("partner_portal") == "active"
    licence = registry.assert_licensed("partner_portal")
    assert licence.contract_ref == "TREI-LIC-001"
    assert licence.covers_url(f"{API_BASE}/listings?page=2") is True
    # Same host, different resource: not named by the contract.
    assert licence.covers_url(f"{API_BASE}/agencies") is False


def test_source_without_licence_is_refused():
    registry = LicenceRegistry([make_licence()])
    assert registry.status("subito_it") == "missing"
    with pytest.raises(NotLicensed, match="no licence recorded"):
        registry.assert_licensed("subito_it")


def test_expired_licence_is_refused():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    registry = LicenceRegistry([make_licence(valid_to=yesterday)])
    assert registry.status("partner_portal") == "expired"
    with pytest.raises(NotLicensed, match="expired"):
        registry.assert_licensed("partner_portal")


def test_licence_not_yet_in_force_is_refused():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    registry = LicenceRegistry([make_licence(valid_from=tomorrow)])
    assert registry.status("partner_portal") == "not_yet_valid"
    with pytest.raises(NotLicensed, match="not in force"):
        registry.assert_licensed("partner_portal")


def test_licence_without_endpoints_covers_nothing():
    registry = LicenceRegistry([make_licence(endpoints=[])])
    assert registry.status("partner_portal") == "no_endpoints"
    with pytest.raises(NotLicensed, match="names no endpoints"):
        registry.assert_licensed("partner_portal")


def test_missing_register_file_means_nothing_is_licensed(tmp_path):
    registry = LicenceRegistry.load(tmp_path / "does-not-exist.yaml")
    assert registry.all() == []
    assert registry.status("partner_portal") == "missing"


def test_register_loads_from_yaml(tmp_path):
    path = write_licence_file(
        tmp_path,
        [
            {
                "source": "partner_portal",
                "grantor": "Partner Portal S.p.A.",
                "contract_ref": "TREI-LIC-002",
                "valid_from": (date.today() - timedelta(days=1)).isoformat(),
                "endpoints": [f"{API_BASE}/listings"],
                "scope": ["read_listings"],
                "max_requests_per_minute": 30,
            }
        ],
    )
    registry = LicenceRegistry.load(path)
    licence = registry.assert_licensed("partner_portal")
    assert licence.licence_id == "partner_portal:Partner Portal S.p.A.:TREI-LIC-002"
    assert licence.max_requests_per_minute == 30
    assert "robots_applies" in licence.to_dict()


# --- credentials ------------------------------------------------------------
def test_credentials_resolved_from_env_and_never_echoed():
    store = CredentialStore({"TREI_PARTNER_PORTAL_API_KEY": "top-secret-value"})
    creds = store.resolve("partner_portal")
    assert creds.kind is CredentialKind.API_KEY
    assert creds.headers() == {"X-API-Key": "top-secret-value"}
    assert "top-secret-value" not in json.dumps(creds.redacted())


def test_missing_credentials_names_env_vars_not_values():
    with pytest.raises(MissingCredentials) as exc:
        CredentialStore({}).resolve("partner_portal")
    message = str(exc.value)
    assert "TREI_PARTNER_PORTAL_API_KEY" in message
    assert "TREI_PARTNER_PORTAL_BEARER_TOKEN" in message


def test_raw_credentials_are_inert_by_default():
    blank = Credentials()
    assert blank.kind is CredentialKind.NONE
    assert blank.headers() == {}
    assert blank.query() == {}
    assert blank.auth() is None


def test_bearer_basic_and_oauth2_are_recognised():
    bearer = CredentialStore({"TREI_PARTNER_PORTAL_BEARER_TOKEN": "tok"}).resolve("partner_portal")
    assert bearer.kind is CredentialKind.BEARER
    assert bearer.headers() == {"Authorization": "Bearer tok"}

    basic = CredentialStore(
        {"TREI_PARTNER_PORTAL_USERNAME": "u", "TREI_PARTNER_PORTAL_PASSWORD": "p"}
    ).resolve("partner_portal")
    assert basic.kind is CredentialKind.BASIC
    assert isinstance(basic.auth(), httpx.BasicAuth)

    oauth = CredentialStore(
        {
            "TREI_PARTNER_PORTAL_CLIENT_ID": "cid",
            "TREI_PARTNER_PORTAL_CLIENT_SECRET": "sec",
            "TREI_PARTNER_PORTAL_TOKEN_URL": "https://auth.partner.example/token",
        }
    ).resolve("partner_portal")
    assert oauth.kind is CredentialKind.OAUTH2_CLIENT_CREDENTIALS
    assert isinstance(oauth.auth(), OAuth2ClientCredentials)


def test_explicit_credential_kind_overrides_inference():
    creds = CredentialStore(
        {
            "TREI_PARTNER_PORTAL_API_KEY": "k",
            "TREI_PARTNER_PORTAL_CREDENTIAL_KIND": "bearer",
            "TREI_PARTNER_PORTAL_BEARER_TOKEN": "tok",
        }
    ).resolve("partner_portal")
    assert creds.kind is CredentialKind.BEARER


def test_api_key_in_query_is_opt_in():
    creds = CredentialStore(
        {
            "TREI_PARTNER_PORTAL_API_KEY": "k",
            "TREI_PARTNER_PORTAL_API_KEY_QUERY_PARAM": "apikey",
        }
    ).resolve("partner_portal")
    assert creds.query() == {"apikey": "k"}
    assert creds.headers() == {}


def test_optional_credentials_allow_credentialless_channels():
    creds = CredentialStore({}).optional("partner_portal")
    assert creds.kind is CredentialKind.NONE


def test_oauth2_token_is_exchanged_once_and_cached(monkeypatch):
    calls = {"count": 0}

    def fake_post(url, **kwargs):
        calls["count"] += 1
        return httpx.Response(
            200,
            json={"access_token": "abc", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    auth = OAuth2ClientCredentials("https://auth.partner.example/token", "cid", "sec", "read")
    assert auth.token() == "abc"
    assert auth.token() == "abc"
    assert calls["count"] == 1

    request = httpx.Request("GET", f"{API_BASE}/listings")
    next(auth.auth_flow(request))
    assert request.headers["Authorization"] == "Bearer abc"


# --- audit log --------------------------------------------------------------
def test_audit_log_appends_and_never_stores_secrets(tmp_path):
    log = AccessLog(tmp_path / "audit.jsonl", actor="tester")
    written = log.record(
        AccessEvent(
            source="partner_portal",
            outcome=AccessOutcome.ALLOWED,
            records=2,
            credentials={"api_key": "top-secret", "api_key_header": "X-API-Key"},
        )
    )
    assert written["credentials"]["api_key"] == "***redacted***"
    assert written["credentials"]["api_key_header"] == "X-API-Key"
    assert written["actor"] == "tester"
    assert "top-secret" not in (tmp_path / "audit.jsonl").read_text(encoding="utf-8")


def test_audit_log_records_refusals_alongside_successes(tmp_path):
    log = AccessLog(tmp_path / "audit.jsonl")
    log.record(
        AccessEvent(
            source="subito_it",
            outcome=AccessOutcome.REFUSED_NO_LICENCE,
            reason="no licence recorded",
        )
    )
    log.record(AccessEvent(source="subito_it", outcome=AccessOutcome.ALLOWED))
    assert len(log.refused()) == 1
    assert len(log.for_source("subito_it")) == 2
    assert log.entries()[0]["outcome"] == "refused_no_licence"


def test_redact_masks_nested_credential_keys():
    cleaned = redact(
        {
            "headers": {"Authorization": "Bearer x", "Accept": "application/json"},
            "token": "abc",
            "records": 3,
        }
    )
    assert cleaned["headers"]["Authorization"] == "***redacted***"
    assert cleaned["headers"]["Accept"] == "application/json"
    assert cleaned["token"] == "***redacted***"
    assert cleaned["records"] == 3


def test_is_secret_key_keeps_descriptive_names_readable():
    assert is_secret_key("api_key") is True
    assert is_secret_key("Authorization") is True
    assert is_secret_key("bearer_token") is True
    # A header *name* and a boolean presence flag are not themselves secrets.
    assert is_secret_key("api_key_header") is False
    assert is_secret_key("has_api_key") is False
    assert is_secret_key("page") is False


def test_scrub_url_masks_credential_query_values():
    scrubbed = scrub_url(f"{API_BASE}/listings?page=2&apikey=SUPERSECRET&sig=abc")
    assert "SUPERSECRET" not in scrubbed
    assert "page=2" in scrubbed
    assert scrub_url(f"{API_BASE}/listings") == f"{API_BASE}/listings"
    assert scrub_url("file:///srv/drop/immobili.csv") == "file:///srv/drop/immobili.csv"


# --- authorized feed adapter ------------------------------------------------
def build_http_adapter(
    tmp_path,
    *,
    urls,
    licences,
    env,
    fmt="json",
    record_mapper=None,
    next_field=None,
    max_pages=1,
    config=None,
    handler=None,
) -> AuthorizedFeedAdapter:
    config = config or make_config()
    # Keep the politeness delay negligible in tests (real default is 2s).
    config.extra = config.extra or {"min_delay_s": 0.001}
    client = httpx.Client(
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json={"data": []}))
        )
    )
    return AuthorizedFeedAdapter(
        config,
        licences=licences,
        credentials=CredentialStore(env),
        audit=AccessLog(tmp_path / "access.jsonl", actor="tester"),
        urls=urls,
        fmt=fmt,
        record_mapper=record_mapper,
        next_field=next_field,
        max_pages=max_pages,
        client=client,
    )


def test_feed_refuses_without_licence_and_makes_no_request(tmp_path, robots_allow):
    def must_not_run(request):  # pragma: no cover - must never be reached
        raise AssertionError("a request was issued without a licence")

    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
        handler=must_not_run,
    )
    with pytest.raises(NotLicensed):
        adapter.run()
    refusals = adapter.audit.refused()
    assert len(refusals) == 1
    assert refusals[0]["outcome"] == "refused_no_licence"


def test_feed_refuses_without_credentials(tmp_path, robots_allow):
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([make_licence()]),
        env={},
    )
    with pytest.raises(MissingCredentials):
        adapter.run()
    assert adapter.audit.refused()[0]["outcome"] == "refused_no_credentials"


def test_feed_refuses_endpoint_not_named_in_the_licence(tmp_path, robots_allow):
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/agencies"],  # same host, different resource
        licences=LicenceRegistry([make_licence()]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
    )
    with pytest.raises(FeedScopeError):
        adapter.run()
    assert adapter.audit.refused()[0]["outcome"] == "refused_out_of_scope"


def test_feed_respects_robots_even_when_a_licence_exists(tmp_path, monkeypatch):
    """robots.txt still wins unless BOTH the contract and the env waive it."""
    monkeypatch.setattr(
        httpx,
        "get",
        lambda url, **kwargs: httpx.Response(
            200,
            text="User-agent: *\nDisallow: /",
            request=httpx.Request("GET", url),
        ),
    )
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([make_licence()]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
        handler=lambda request: httpx.Response(200, json={"data": [{"id": 1}]}),
    )
    with pytest.raises(RobotsDisallowed):
        adapter.run()
    assert adapter.audit.refused()[0]["outcome"] == "refused_robots"
    assert adapter.health.blocked_requests == 1


def test_feed_format_must_be_permitted_by_the_licence(tmp_path):
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([make_licence(permitted_formats=["json"])]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
        fmt="csv",
    )
    with pytest.raises(FeedScopeError, match="format"):
        adapter.run()


def test_contractual_rate_limit_raises_the_politeness_delay(tmp_path):
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([make_licence(max_requests_per_minute=6)]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
    )
    adapter.assert_allowed()
    assert adapter._min_delay >= 10.0 - 1e-9  # 60s / 6 requests


def test_feed_http_success_sends_key_and_stamps_lineage(tmp_path, robots_allow):
    seen: dict = {}

    def handler(request):
        seen["api_key"] = request.headers.get("x-api-key")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": 7, "price": 250000}]})

    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings?page=1"],
        licences=LicenceRegistry([make_licence()]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "secret-api-key"},
        record_mapper=lambda row: {"listing_source_id": str(row["id"]), "price": row["price"]},
        handler=handler,
    )
    result = adapter.run()

    assert seen["api_key"] == "secret-api-key"
    assert "page=1" in seen["url"]
    assert result.status.value == "OK"
    assert result.records[0]["listing_source_id"] == "7"
    assert result.records[0]["source"] == "partner_portal"
    assert result.records[0]["lineage"]["source"] == "partner_portal"
    assert result.records[0]["lineage"]["content_hash"] == result.content_hash

    allowed = [e for e in adapter.audit.entries() if e["outcome"] == "allowed"]
    assert allowed[0]["licence_id"] == "partner_portal:Partner Portal S.p.A.:TREI-LIC-001"
    assert allowed[0]["records"] == 1
    # The credential value must never reach the evidence log.
    assert "secret-api-key" not in json.dumps(adapter.audit.entries())


def test_feed_follows_the_licence_covered_next_page(tmp_path, robots_allow):
    def handler(request):
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(
                200,
                json={"data": [{"id": 1}], "meta": {"next": f"{API_BASE}/listings?page=2"}},
            )
        return httpx.Response(200, json={"data": [{"id": 2}], "meta": {}})

    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings?page=1"],
        licences=LicenceRegistry([make_licence()]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
        record_mapper=lambda row: {"listing_source_id": str(row["id"])},
        next_field="meta.next",
        max_pages=2,
        handler=handler,
    )
    result = adapter.run()
    assert [r["listing_source_id"] for r in result.records] == ["1", "2"]


# --- licensed export drop (no credentials involved) -------------------------
def test_export_drop_reads_licensed_folder_without_credentials(tmp_path, audit):
    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "immobili.csv").write_text(
        "id,comune,prezzo\n1,Trento,250000\n2,Rovereto,180000\n", encoding="utf-8"
    )
    adapter = AuthorizedFeedAdapter(
        make_config(),
        licences=LicenceRegistry([make_licence(endpoints=[f"file://{drop.resolve()}"])]),
        credentials=CredentialStore({}),  # a received export needs no credential
        audit=audit,
        directory=drop,
        fmt="csv",
        record_mapper=lambda row: {
            "listing_source_id": row["id"],
            "comune": row["comune"],
            "price": float(row["prezzo"]),
        },
    )
    result = adapter.run()
    assert result.status.value == "OK"
    assert len(result.records) == 2
    assert result.records[0]["comune"] == "Trento"
    assert result.records[0]["source"] == "partner_portal"
    assert result.records[0]["lineage"]["source_url"].startswith("file://")
    assert adapter.audit.entries()[-1]["outcome"] == "allowed"


def test_export_drop_outside_the_licensed_folder_is_refused(tmp_path, audit):
    licensed = tmp_path / "licensed"
    licensed.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "rogue.csv").write_text("id\n1\n", encoding="utf-8")

    adapter = AuthorizedFeedAdapter(
        make_config(),
        licences=LicenceRegistry([make_licence(endpoints=[f"file://{licensed.resolve()}"])]),
        credentials=CredentialStore({}),
        audit=audit,
        directory=elsewhere,
        fmt="csv",
    )
    with pytest.raises(FeedScopeError):
        adapter.run()
    assert adapter.audit.refused()[0]["outcome"] == "refused_out_of_scope"


# --- the registry posture can never be widened silently ---------------------
def test_registry_flag_alone_never_enables_a_feed(tmp_path, robots_allow):
    """An explicitly restricted source stays refused while the register is empty."""
    config = SourceRegistry.load().get("subito_it")
    assert config is not None
    assert config.auto_acquisition_allowed is False

    adapter = build_http_adapter(
        tmp_path,
        urls=["https://api.subito.example/v1/listings"],
        licences=LicenceRegistry.load(),  # the real, empty register
        env={"TREI_SUBITO_IT_API_KEY": "k"},
        config=config,
    )
    with pytest.raises(NotLicensed):
        adapter.run()


# --- shared payload parsers -------------------------------------------------
def test_parse_payload_supports_every_feed_format():
    assert parse_payload(
        "json", json.dumps({"payload": {"items": [{"a": 1}]}}), records_path="payload.items"
    ) == [{"a": 1}]
    assert parse_payload("jsonl", '{"a": 1}\n\n{"a": 2}\n') == [{"a": 1}, {"a": 2}]
    assert parse_payload("csv", "a,b\n1,2\n") == [{"a": "1", "b": "2"}]

    xml = (
        "<annunci><annuncio id='7'><comune>Trento</comune>"
        "<prezzo>250000</prezzo></annuncio></annunci>"
    )
    assert parse_payload("xml", xml, record_tag="annuncio") == [
        {"id": "7", "comune": "Trento", "prezzo": "250000"}
    ]
    # Repeated child tags collapse into a list rather than overwriting.
    assert parse_payload("xml", "<r><a><b>1</b><b>2</b></a></r>", record_tag="a") == [
        {"b": ["1", "2"]}
    ]

    with pytest.raises(ValueError, match="unsupported"):
        parse_payload("xlsx", "")


def test_parse_payload_autodetects_common_api_envelopes():
    assert parse_payload("json", json.dumps({"results": [{"a": 1}]})) == [{"a": 1}]
    assert parse_payload("json", json.dumps({"dataset": {"data": [{"a": 2}]}})) == [{"a": 2}]
    assert parse_payload("json", json.dumps({"unexpected": "shape"})) == []


# --- credentials must never reach disk --------------------------------------
def test_query_param_credentials_never_reach_the_audit_log(tmp_path, robots_allow):
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"id": 1}]})

    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings"],
        licences=LicenceRegistry([make_licence()]),
        env={
            "TREI_PARTNER_PORTAL_API_KEY": "SUPER-SECRET-KEY",
            "TREI_PARTNER_PORTAL_API_KEY_QUERY_PARAM": "apikey",
        },
        handler=handler,
    )
    assert adapter.run().status.value == "OK"
    # The request legitimately carried the key...
    assert "apikey=SUPER-SECRET-KEY" in seen["url"]
    # ...but the evidence log must not.
    assert "SUPER-SECRET-KEY" not in (tmp_path / "access.jsonl").read_text(encoding="utf-8")


def test_inline_credential_in_a_seeded_url_is_scrubbed(tmp_path, robots_allow):
    adapter = build_http_adapter(
        tmp_path,
        urls=[f"{API_BASE}/listings?apikey=INLINE-SECRET&page=1"],
        licences=LicenceRegistry([make_licence()]),
        env={"TREI_PARTNER_PORTAL_API_KEY": "k"},
        handler=lambda request: httpx.Response(200, json={"data": [{"id": 1}]}),
    )
    result = adapter.run()

    assert result.status.value == "OK"
    log_text = (tmp_path / "access.jsonl").read_text(encoding="utf-8")
    assert "INLINE-SECRET" not in log_text
    assert "page=1" in log_text
    # Lineage is stored too, so it must be scrubbed as well.
    assert "INLINE-SECRET" not in json.dumps(result.records)



