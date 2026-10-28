"""Auction channel tests (§33-35): RSS parsing, normalization, deduplication."""

from __future__ import annotations

from app.agent_import.policy import PolicyResolver
from app.auction_fetcher.models import AuctionFeedItem
from app.auction_fetcher.parser import AuctionParser, to_iso_date
from app.auction_fetcher.pipeline import AuctionPipeline
from app.auction_fetcher.rss import parse_feed
from app.fetchers.audit import AccessLog
from app.fetchers.registry import SourceConfig, SourceRegistry

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Aste - Trentino</title>
    <link>https://www.astalegale.net/</link>
    <item>
      <title>Appartamento in Trento - Tribunale di Trento - Proc. n. 123/2025 - Lotto 2</title>
      <link>https://www.astalegale.net/annuncio/123</link>
      <guid>astalegale-123</guid>
      <pubDate>Tue, 30 Sep 2026 08:00:00 +0000</pubDate>
      <description>Appartamento in Trento, Via Roma 12, 95 mq. Prezzo base: 120.000 euro.
        Offerta minima: 90.000 euro. Cauzione: 12.000 euro. Libero. Data asta: 15/12/2026</description>
    </item>
    <item>
      <title>Villa - Tribunale di Rovereto - Proc. n. 45/2025 - Lotto 1</title>
      <link>https://www.astalegale.net/annuncio/456</link>
      <guid>astalegale-456</guid>
      <description>Villa a Rovereto, Via Verdi 40, 180 mq. Prezzo base: 260.000 euro.
        Offerta minima: 195.000 euro. Occupato. Data asta: 20/01/2027</description>
    </item>
    <item>
      <title>Appartamento in Milano - Tribunale di Milano - Proc. n. 9/2025 - Lotto 3</title>
      <link>https://www.astalegale.net/annuncio/789</link>
      <guid>astalegale-789</guid>
      <description>Appartamento a Milano, Via Dante 5, 90 mq. Prezzo base: 300.000 euro</description>
    </item>
  </channel>
</rss>
"""


def test_rss_feed_is_parsed_into_items():
    feed = parse_feed(RSS)
    assert feed.title == "Aste - Trentino"
    assert len(feed.items) == 3
    first = feed.items[0]
    assert "Tribunale di Trento" in first["title"]
    assert first["guid"] == "astalegale-123"
    assert "Prezzo base" in first["description"]


def test_auction_notice_fields_are_extracted():
    parser = AuctionParser()
    bundle = parser.parse_text(
        "Tribunale di Trento - Proc. n. 123/2025 - Lotto 2. Appartamento in Trento, Via Roma 12, "
        "95 mq. Prezzo base: 120.000 euro. Offerta minima: 90.000 euro. Cauzione: 12.000 euro. "
        "Libero. Data asta: 15/12/2026",
        url="https://www.astalegale.net/annuncio/123",
    )
    assert bundle.value("tribunal").startswith("Trento")
    assert bundle.value("procedure_number") == "123"
    assert bundle.value("procedure_year") == 2025
    assert bundle.value("lot_number") == "2"
    assert bundle.value("auction_date") == "2026-12-15"
    assert bundle.value("starting_price") == 120000
    assert bundle.value("minimum_offer") == 90000
    assert bundle.value("deposit") == 12000
    assert bundle.value("occupancy") == "free"
    assert bundle.value("municipality") == "Trento"
    assert bundle.value("address", "").lower().startswith("via roma")
    assert bundle.value("surface_m2") == 95
    assert bundle.method("starting_price") == "regex_text"


def test_missing_auction_fields_stay_absent():
    parser = AuctionParser()
    bundle = parser.parse_text("Tribunale di Trento - Proc. n. 5/2025")
    assert bundle.value("starting_price") is None
    assert bundle.value("minimum_offer") is None
    assert bundle.value("auction_date") is None
    assert bundle.value("occupancy") is None


def test_date_normalization():
    assert to_iso_date("asta il 15/12/2026") == "2026-12-15"
    assert to_iso_date("nessuna data") is None


def feed_items() -> list[AuctionFeedItem]:
    return [
        AuctionFeedItem(
            source="astalegale_net",
            title=str(item["title"]),
            link=str(item["link"]),
            guid=str(item["guid"]),
            description=str(item["description"]),
            published_at=str(item.get("pubdate", "")),
            raw=item,
        )
        for item in parse_feed(RSS).items
    ]


def test_pipeline_keeps_only_trentino_lots():
    pipeline = AuctionPipeline()
    report = pipeline.ingest_feed_items(feed_items(), source="astalegale_net")

    assert report.items_seen == 3
    assert report.records == 2
    assert report.out_of_territory == 1  # the Milano lot is excluded
    titles = {a.get("auction_url") for a in report.auctions}
    assert "https://www.astalegale.net/annuncio/789" not in titles


def test_same_lot_on_two_sources_collapses_to_one_master():
    pipeline = AuctionPipeline()
    pvp = AuctionFeedItem(
        source="pvp_giustizia",
        title="Tribunale di Trento - Proc. n. 123/2025 - Lotto 2",
        link="https://pvponline.giustizia.it/dettaglio/999",
        guid="pvp-999",
        description="Appartamento in Trento, Via Roma 12, 95 mq. Prezzo base: 118.000 euro",
        raw={"pvp_id": "PVP-999"},
    )
    asta = AuctionFeedItem(
        source="astalegale_net",
        title="Appartamento in Trento - Tribunale di Trento - Proc. n. 123/2025 - Lotto 2",
        link="https://www.astalegale.net/annuncio/123",
        guid="astalegale-123",
        description="Prezzo base: 120.000 euro. Offerta minima: 90.000 euro",
        raw={},
    )
    report = pipeline.ingest_feed_items([pvp, asta], source="astalegale_net")

    assert report.records == 1
    assert report.duplicates == 1
    merged = report.auctions[0]
    assert merged["master_auction_id"]
    assert set(merged["sources"]) == {"pvp", "astalegale"}
    assert merged["pvp_id"] == "PVP-999"
    # PVP is authoritative for the base price.
    assert merged["starting_price"] == 118000


def test_price_reduction_across_relistings_keeps_the_same_lot():
    pipeline = AuctionPipeline()
    first = AuctionFeedItem(
        source="astalegale_net",
        title="Tribunale di Trento - Proc. n. 200/2024 - Lotto 1",
        link="https://www.astalegale.net/annuncio/1",
        guid="1",
        description="Prezzo base: 150.000 euro. Data asta: 10/05/2026",
        raw={},
    )
    second = AuctionFeedItem(
        source="astalegale_net",
        title="Tribunale di Trento - Proc. n. 200/2024 - Lotto 1",
        link="https://www.astalegale.net/annuncio/2",
        guid="2",
        description="Prezzo base: 120.000 euro. Data asta: 22/11/2026",
        raw={},
    )
    report = pipeline.ingest_feed_items([first, second], source="astalegale_net")

    assert report.records == 1
    merged = report.auctions[0]
    assert merged["procedure_number"] == "200"
    assert merged["lot_number"] == "1"
    # Both observations survive as separate sources/urls on the same master lot.
    assert merged["master_auction_id"]


def test_new_auction_is_added_next_to_the_existing_ones():
    pipeline = AuctionPipeline()
    base = feed_items()[:1]
    report_one = pipeline.ingest_feed_items(base, source="astalegale_net")
    assert report_one.records == 1

    extra = AuctionFeedItem(
        source="astalegale_net",
        title="Rustico - Tribunale di Trento - Proc. n. 300/2026 - Lotto 4",
        link="https://www.astalegale.net/annuncio/300",
        guid="300",
        description="Rustico a Pergine Valsugana, 120 mq. Prezzo base: 88.000 euro",
        raw={},
    )
    report_two = pipeline.ingest_feed_items([*base, extra], source="astalegale_net")
    assert report_two.records == 2
    assert any(a.get("procedure_number") == "300" for a in report_two.auctions)


def test_licensed_pvp_export_is_read_from_a_drop_directory(tmp_path):
    (tmp_path / "pvp").mkdir()
    (tmp_path / "pvp" / "lotti.jsonl").write_text(
        '{"tribunal": "Trento", "procedure_number": "77", "procedure_year": 2026, "lot_number": "1",'
        ' "municipality": "Trento", "starting_price": 99000, "pvp_id": "PVP-77"}\n',
        encoding="utf-8",
    )
    report = AuctionPipeline().read_pvp_export(tmp_path / "pvp")

    assert report.records == 1
    merged = report.auctions[0]
    assert merged["procedure_number"] == "77"
    assert merged["starting_price"] == 99000
    assert merged["pvp_id"] == "PVP-77"


def test_rss_fetch_is_refused_without_a_policy_decision(tmp_path):
    from app.auction_fetcher.rss import RssAuctionFetcher

    unknown = SourceConfig(
        name="fallco_aste", domain="www.fallcoaste.it", kind="auction",
        tier="tier5_restricted", status="UNKNOWN", recommended_mode="verify first", feed=True,
    )
    resolver = PolicyResolver(SourceRegistry([unknown]))
    fetcher = RssAuctionFetcher(
        unknown, resolver=resolver, audit=AccessLog(tmp_path / "audit.jsonl")
    )
    items, decision = fetcher.fetch(["https://www.fallcoaste.it/rss.xml"])

    assert items == []
    assert decision.allowed is False
    assert fetcher.audit.entries()[0]["outcome"] == "refused_no_licence"
