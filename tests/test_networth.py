"""Net-worth snapshot capture + chart-building."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch


def _utcnow_naive():
    # Match the naive UTC datetimes the models store.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fetch_all_with(cash=0.0, investment=0.0, credit=0.0):
    """Build a providers.fetch_all return value with single-account totals."""
    def acct(b):
        return {"balance": b}
    return {
        "cash": [acct(cash)] if cash else [],
        "investment": [acct(investment)] if investment else [],
        "credit": [acct(credit)] if credit else [],
        "other": [],
        "errors": [],
    }


def test_capture_inserts_snapshot(user_with_item, db_session):
    from models import NetWorthSnapshot
    import networth
    with patch("networth.providers.fetch_all",
               return_value=_fetch_all_with(cash=500.0, investment=1000.0, credit=200.0)):
        networth.capture(user_with_item, db_session)
    rows = db_session.query(NetWorthSnapshot).all()
    assert len(rows) == 1
    assert rows[0].cash_total == 500.0
    assert rows[0].investment_total == 1000.0
    assert rows[0].credit_total == 200.0
    assert rows[0].net_worth == 1300.0


def test_capture_replaces_existing_same_day_snapshot(user_with_item, db_session):
    """A second capture on the same day overwrites the first one — one row per day."""
    from models import NetWorthSnapshot
    import networth
    with patch("networth.providers.fetch_all",
               return_value=_fetch_all_with(cash=100.0)):
        networth.capture(user_with_item, db_session)
    with patch("networth.providers.fetch_all",
               return_value=_fetch_all_with(cash=250.0)):
        networth.capture(user_with_item, db_session)
    rows = db_session.query(NetWorthSnapshot).all()
    assert len(rows) == 1
    assert rows[0].net_worth == 250.0


def test_get_snapshots_returns_rows_in_chronological_order(user_with_item, db_session):
    """Same-day uniqueness is enforced by capture(); get_snapshots just
    returns every persisted row oldest-first."""
    from models import NetWorthSnapshot
    import networth
    base = datetime(2026, 5, 1, 12, 0, 0)
    db_session.add_all([
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=base.replace(day=3),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=300.0),
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=base,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=100.0),
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=base.replace(day=2),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=200.0),
    ])
    db_session.commit()
    snaps = networth.get_snapshots(user_with_item, db_session)
    assert [s.net_worth for s in snaps] == [100.0, 200.0, 300.0]


def test_capture_skips_when_no_items(user, db_session):
    """A user with no linked institutions doesn't get a useless zero snapshot."""
    from models import NetWorthSnapshot
    import networth
    with patch("networth.providers.fetch_all") as mock_fetch:
        result = networth.capture(user, db_session)
    assert result is None
    assert mock_fetch.call_count == 0
    assert db_session.query(NetWorthSnapshot).count() == 0


def test_build_chart_returns_none_for_empty():
    import networth
    assert networth.build_chart([]) is None


def test_build_chart_single_snapshot_renders_no_line_path(user_with_item):
    """One snapshot: no line/area path (need at least 2 points), but the
    chart object still exists so the page can show the dot at hover time."""
    from models import NetWorthSnapshot
    import networth
    snap = NetWorthSnapshot(
        user_id=user_with_item.id, taken_at=_utcnow_naive(),
        cash_total=0, investment_total=0, credit_total=0, net_worth=100.0,
    )
    chart = networth.build_chart([snap])
    assert chart is not None
    assert chart["line_path"] == ""
    assert chart["area_path"] == ""
    assert len(chart["points"]) == 1


def test_build_chart_with_range_bounds_anchors_axis():
    """range_start_ts/range_end_ts pin the X axis even when data sits at one end."""
    from models import NetWorthSnapshot
    import networth
    now = _utcnow_naive()
    snaps = [
        NetWorthSnapshot(user_id=1, taken_at=now - timedelta(days=1),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=100.0),
        NetWorthSnapshot(user_id=1, taken_at=now,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=200.0),
    ]
    range_end = int(now.timestamp())
    range_start = range_end - 30 * 86400
    chart = networth.build_chart(snaps, range_start_ts=range_start, range_end_ts=range_end)
    # Both data points fall near the right edge of a 30-day window.
    width = chart["width"]
    for p in chart["points"]:
        assert p["x"] > width * 0.9  # rightmost ~10% of the chart


def test_build_chart_trend_up_when_last_exceeds_first():
    from models import NetWorthSnapshot
    import networth
    now = _utcnow_naive()
    snaps = [
        NetWorthSnapshot(user_id=1, taken_at=now - timedelta(days=2),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=100.0),
        NetWorthSnapshot(user_id=1, taken_at=now,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=250.0),
    ]
    chart = networth.build_chart(snaps)
    assert chart["trend"] == "up"
    assert chart["first_value"] == 100.0
    assert chart["last_value"] == 250.0
    assert "M " in chart["line_path"]
    assert "Z" in chart["area_path"]
    # Points list for client-side hover snapping.
    assert len(chart["points"]) == 2
    assert chart["points"][0]["value"] == 100.0
    assert chart["points"][1]["value"] == 250.0
    for p in chart["points"]:
        assert "x" in p and "y" in p and "label" in p and "ts" in p


def test_build_chart_trend_compares_last_to_first():
    """Red on a falling balance, green on a rising one — regardless of sign.
    A positive balance trending down still shows down/red."""
    from models import NetWorthSnapshot
    import networth
    now = _utcnow_naive()

    snaps_down_positive = [
        NetWorthSnapshot(user_id=1, taken_at=now - timedelta(days=2),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=500.0),
        NetWorthSnapshot(user_id=1, taken_at=now,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=300.0),
    ]
    assert networth.build_chart(snaps_down_positive)["trend"] == "down"

    snaps_up_negative = [
        NetWorthSnapshot(user_id=1, taken_at=now - timedelta(days=2),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=-100.0),
        NetWorthSnapshot(user_id=1, taken_at=now,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=-50.0),
    ]
    assert networth.build_chart(snaps_up_negative)["trend"] == "up"


def test_get_snapshots_orders_by_taken_at_asc(user_with_item, db_session):
    from models import NetWorthSnapshot
    import networth
    now = _utcnow_naive()
    db_session.add_all([
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=now,
                         cash_total=0, investment_total=0, credit_total=0, net_worth=300.0),
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=now - timedelta(days=1),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=200.0),
        NetWorthSnapshot(user_id=user_with_item.id, taken_at=now - timedelta(days=2),
                         cash_total=0, investment_total=0, credit_total=0, net_worth=100.0),
    ])
    db_session.commit()
    snaps = networth.get_snapshots(user_with_item, db_session)
    assert [s.net_worth for s in snaps] == [100.0, 200.0, 300.0]
