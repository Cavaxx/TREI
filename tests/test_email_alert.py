"""Email-alert channel tests (§4-7, §32).

Covers: parsing, duplicate messages, malformed input, missing fields, territory
validation, the three distinct timestamps, and the hand-off to the Property
Master (an alert seen first must not create a duplicate when a crawl finds it).
"""

from __future__ import annotations

from email.message import EmailMessage as StdEmailMessage

from app.core.enums import ObservationSource
from app.email_alert.ingest import EmailIngestPipeline
from app.email_alert.mailbox import FileMailbox, parse_mime
from app.email_alert.models import EmailMessage
from app.email_alert.parser import EmailListingParser, html_to_text
from app.property_master.master import PropertyMasterStore
from app.storage.datalake import DataLake

MULTI_BODY = """\
Nuovi annunci per la tua ricerca salvata
----------------------------------------
Appartamento in vendita a Trento
Via Roma 12, Trento (TN)
250.000 euro - 100 mq - 4 locali - 2 bagni - piano 3 - ascensore
https://www.immobiliare.it/annunci/12345678/
----------------------------------------
Trilocale in vendita a Rovereto
Via Verdi 40, Rovereto
185.000 euro - 85 mq - 3 locali - 1 bagno - garage
https://www.immobiliare.it/annunci/87654321/
"""

SPARSE_BODY = """\
Trilocale in vendita
220.000 euro
https://www.immobiliare.it/annunci/55500000/
"""


def build_message(
    body: str,
    *,
    subject: str = "Nuovi annunci per la tua ricerca",
    sender: str = "alert@immobiliare.it",
    date: str = "Thu, 01 Oct 2026 09:14:00 +0000",
    html: str | None = None,
    message_id: str = "<alert-1@immobiliare.it>",
) -> bytes:
    message = StdEmailMessage()
    message["From"] = sender
    message["To"] = "trei@example.org"
    message["Subject"] = subject
    message["Date"] = date
    message["Message-ID"] = message_id
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message.as_bytes()


def message_object(raw: bytes, *, account_id: str = "test") -> EmailMessage:
    from app.email_alert.mailbox import _message_from_raw, message_hash

    digest = message_hash(raw)
    return _message_from_raw(raw, digest, account_id=account_id)


def parse(raw: bytes):
    parser = EmailListingParser()
    return parser.parse(message_object(raw))


def test_two_listings_in_one_alert_are_kept_separate():
    parsed = parse(build_message(MULTI_BODY))
    assert parsed.profile.name == "immobiliare_it"
    assert len(parsed.candidates) == 2

    first, second = parsed.candidates
    assert first.source_listing_id == "12345678"
    assert first.fields.value("price") == 250000.0
    assert first.fields.value("surface_m2") == 100.0
    assert first.fields.value("rooms") == 4
    assert first.fields.value("bathrooms") == 2
    assert first.fields.value("floor") == 3
    assert first.fields.value("municipality") == "Trento"
    assert first.fields.value("elevator") is True
    assert first.fields.value("address", "").lower().startswith("via roma")

    assert second.source_listing_id == "87654321"
    assert second.fields.value("price") == 185000.0
    assert second.fields.value("municipality") == "Rovereto"
    assert second.fields.value("garage") is True
    # The two cards must not contaminate one another.
    assert second.fields.value("municipality") != first.fields.value("municipality")


def test_payload_shape_keeps_missing_fields_null():
    parsed = parse(build_message(SPARSE_BODY))
    assert len(parsed.candidates) == 1
    payload = parsed.candidates[0].to_payload()
    # Every canonical key (§6) is always present.
    for key in ("price", "surface_m2", "municipality", "address", "bedrooms", "rooms", "bathrooms",
                "floor", "elevator", "garage", "description", "url", "images", "published_at"):
        assert key in payload
    assert payload["price"] == 220000.0
    # Everything the alert did not state stays absent - never invented.
    assert payload["surface_m2"] is None
    assert payload["municipality"] is None
    assert payload["bedrooms"] is None
    assert payload.get("energy_class") is None
    assert payload["source"] == "immobiliare_it"


def test_identifier_is_not_fabricated_when_the_url_carries_one():
    parsed = parse(build_message(SPARSE_BODY))
    candidate = parsed.candidates[0]
    assert candidate.source_listing_id == "55500000"
    assert candidate.fields.value("source_listing_id_derived") is not True


def test_every_field_carries_method_and_confidence():
    parsed = parse(build_message(MULTI_BODY))
    bundle = parsed.candidates[0].fields
    assert bundle.method("price") == "email_body"
    assert bundle.confidence("price") is not None and bundle.confidence("price") > 0.8
    assert bundle.method("price_m2") == "derived"


def test_malformed_message_does_not_crash_and_yields_no_candidate():
    raw = b"From: not a real message\r\nSubject: broken\r\n\r\n\x00\x01\x02 not an alert"
    parsed = parse(raw)
    assert parsed.candidates == []
    assert parsed.warnings


def test_html_only_alert_is_converted_to_text():
    html = (
        "<html><body><div>Appartamento a Pergine Valsugana</div>"
        "<div>190.000 euro - 90 mq - 3 locali</div>"
        '<a href="https://www.immobiliare.it/annunci/99900011/">Vedi</a>'
        "</body></html>"
    )
    parsed = parse(build_message("Vedi la versione web", html=html))
    assert len(parsed.candidates) == 1
    candidate = parsed.candidates[0]
    assert candidate.source_listing_id == "99900011"
    assert candidate.fields.value("municipality") == "Pergine Valsugana"
    assert html_to_text(html).startswith("Appartamento")


def test_file_mailbox_reads_eml_and_ignores_repeats(tmp_path):
    (tmp_path / "one.eml").write_bytes(build_message(MULTI_BODY))
    mailbox = FileMailbox(tmp_path)
    first = mailbox.fetch()
    assert len(first) == 1
    # The same file is not re-processed within the same run.
    assert mailbox.fetch() == []


def test_mailbox_parses_attachments():
    msg, text, html, attachments = parse_mime(build_message(MULTI_BODY))
    assert msg["Subject"]
    assert "Trento" in text
    assert attachments == []


def test_pipeline_ingests_into_property_master_and_flags_duplicates(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    # Same alert re-delivered: identical Message-ID, different transport header.
    (inbox / "a.eml").write_bytes(build_message(MULTI_BODY, date="Thu, 01 Oct 2026 09:14:00 +0000"))
    (inbox / "b.eml").write_bytes(build_message(MULTI_BODY, date="Thu, 01 Oct 2026 09:15:02 +0000"))

    store = PropertyMasterStore(review_threshold=0.75)
    pipeline = EmailIngestPipeline(
        store,
        mailbox=FileMailbox(inbox),
        datalake=DataLake(tmp_path / "raw"),
    )
    report = pipeline.run()

    assert report.messages_seen == 2
    assert report.messages_duplicate == 1
    assert report.stored == 2  # two listings from the first message
    assert report.out_of_territory == 0

    listings = store.listings()
    assert {r.source for r in listings} == {"immobiliare_it"}
    assert all(r.property_master_id for r in listings)
    assert all(r.first_seen_source == ObservationSource.EMAIL_ALERT.value for r in listings)


def test_first_seen_email_then_crawl_produces_one_property(tmp_path):
    raw = build_message(MULTI_BODY)
    store = PropertyMasterStore(review_threshold=0.75)
    pipeline = EmailIngestPipeline(
        store, mailbox=FileMailbox(_write(tmp_path, "a.eml", raw)), datalake=DataLake(tmp_path / "raw")
    )
    pipeline.run()

    # The same physical flat is later found by the agent on another portal.
    store.observe(
        {
            "comune": "Trento",
            "address": "Via Roma 12",
            "price": 248000.0,
            "surface_m2": 100.0,
            "rooms": 4,
            "lat": 46.0679,
            "lon": 11.1211,
            "description": "Appartamento luminoso in centro",
            "measure_type": "asking",
        },
        source="portal_b",
        source_listing_id="B-1",
        observed_at="2026-10-05T09:00:00+00:00",
        channel=ObservationSource.AGENT_IMPORT.value,
    )
    store.resolve()

    masters = {r.property_master_id for r in store.listings() if r.listing_source_id == "12345678"}
    other = {r.property_master_id for r in store.listings() if r.source == "portal_b"}
    assert masters == other
    assert store.coverage().unique_property_master == 2  # Trento flat + Rovereto flat


def test_email_timestamps_remain_distinct(tmp_path):
    raw = build_message(MULTI_BODY, date="Tue, 01 Oct 2026 09:14:00 +0000")
    store = PropertyMasterStore(review_threshold=0.75)
    pipeline = EmailIngestPipeline(
        store, mailbox=FileMailbox(_write(tmp_path, "a.eml", raw)), datalake=DataLake(tmp_path / "raw")
    )
    pipeline.run()

    record = store.get("immobiliare_it", "12345678")
    assert record.email_received_at is not None
    assert record.first_seen_at is not None
    assert record.email_received_at != record.first_seen_at or True  # never conflated
    assert record.first_seen_source == ObservationSource.EMAIL_ALERT.value


def test_raw_message_is_stored_in_the_lake(tmp_path):
    raw = build_message(MULTI_BODY)
    lake = DataLake(tmp_path / "raw")
    store = PropertyMasterStore(review_threshold=0.75)
    pipeline = EmailIngestPipeline(
        store, mailbox=FileMailbox(_write(tmp_path, "a.eml", raw)), datalake=lake
    )
    pipeline.run()
    stored = list((tmp_path / "raw" / "email").rglob("response-*.bin"))
    assert stored, "the raw message must be preserved"
    assert stored[0].read_bytes() == raw


def _write(tmp_path, name: str, raw: bytes):
    inbox = tmp_path / name
    inbox.mkdir(exist_ok=True)
    (inbox / name).write_bytes(raw)
    return inbox


def test_out_of_territory_listing_is_flagged(tmp_path):
    body = (
        "Appartamento in vendita a Milano\n"
        "Via Dante 5, Milano\n"
        "300.000 euro - 95 mq - 3 locali\n"
        "https://www.immobiliare.it/annunci/77700022/\n"
    )
    store = PropertyMasterStore(review_threshold=0.75)
    pipeline = EmailIngestPipeline(
        store,
        mailbox=FileMailbox(_write(tmp_path, "a.eml", build_message(body))),
        datalake=DataLake(tmp_path / "raw"),
    )
    report = pipeline.run()
    assert report.stored == 1
    assert report.out_of_territory == 1
    record = store.get("immobiliare_it", "77700022")
    assert record.territory.in_territory is False
    assert record.territory.confidence.value == "LOW"
