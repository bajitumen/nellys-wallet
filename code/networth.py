"""Net-worth snapshots: capture on /sync, render as a server-side SVG line."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import asc

import providers
from models import NetWorthSnapshot, User


def capture(user: User, session) -> NetWorthSnapshot | None:
    """Snapshot the user's current cash / investment / credit totals.
    Replaces any prior same-day snapshot — only the most recent value per
    calendar day is kept (refresh many times, only the latest survives).
    Skips when the user has no linked items."""
    if not user.items:
        return None
    data = providers.fetch_all(user, force_refresh=True)
    cash = providers.sum_balances(data["cash"])
    investments = providers.sum_balances(data["investment"])
    credit = providers.sum_balances(data["credit"])

    # Drop today's existing snapshots so this one is the single row for today.
    # Use UTC consistently — `taken_at` is stored as naive UTC, so the day
    # boundary must be in UTC too, otherwise around midnight UTC the
    # local-date boundary misses the existing row and we end up with two.
    today = datetime.now(timezone.utc).date()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = start_of_day + timedelta(days=1)
    session.query(NetWorthSnapshot).filter(
        NetWorthSnapshot.user_id == user.id,
        NetWorthSnapshot.taken_at >= start_of_day,
        NetWorthSnapshot.taken_at < end_of_day,
    ).delete(synchronize_session=False)

    snapshot = NetWorthSnapshot(
        user_id=user.id,
        cash_total=cash,
        investment_total=investments,
        credit_total=credit,
        net_worth=cash + investments - credit,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def get_snapshots(user: User, session) -> list[NetWorthSnapshot]:
    """One snapshot per calendar day, latest wins. New captures already
    dedupe today; this guards against legacy rows where the same-day cleanup
    hadn't been applied yet."""
    rows = (
        session.query(NetWorthSnapshot)
        .filter_by(user_id=user.id)
        .order_by(asc(NetWorthSnapshot.taken_at))
        .all()
    )
    by_day: dict = {}
    for r in rows:
        by_day[r.taken_at.date()] = r  # later iterations overwrite earlier
    return sorted(by_day.values(), key=lambda r: r.taken_at)


def build_chart(
    snapshots,
    width: int = 1000,
    height: int = 150,
    range_start_ts: int | None = None,
    range_end_ts: int | None = None,
) -> dict | None:
    """SVG path data for the net-worth line + filled area.

    When `range_start_ts`/`range_end_ts` are given, the X axis is anchored
    to that window regardless of where the data sits — so a single point
    near the right edge of a 30-day window stays at the right edge instead
    of being centered. When omitted, the axis spans the data itself."""
    if not snapshots:
        return None

    pad_x = 4
    pad_y = 10
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    # Snapshots are stored as naive datetimes in UTC (utcnow default).
    # `.timestamp()` on a naive datetime interprets it as LOCAL time —
    # which gives a unix ts that's off by the server's offset from UTC.
    # Force UTC so the resulting unix ts matches the browser's clock.
    def _utc_ts(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    xs = [_utc_ts(s.taken_at) for s in snapshots]
    ys = [s.net_worth for s in snapshots]

    # Backfill the pre-data portion of the range with a flat zero so the
    # chart shows "no data → zero" until the first real snapshot, then
    # spikes up. Two synthetic points create the L-shape: flat across the
    # empty days, then vertical jump at the first real timestamp.
    if range_start_ts is not None and xs[0] > range_start_ts:
        path_xs = [float(range_start_ts), xs[0]] + xs
        path_ys = [0.0, 0.0] + ys
    else:
        path_xs = list(xs)
        path_ys = list(ys)

    if range_start_ts is not None and range_end_ts is not None:
        x_min, x_max = float(range_start_ts), float(range_end_ts)
    else:
        x_min, x_max = path_xs[0], path_xs[-1]
    x_span = max(1.0, x_max - x_min)
    y_min, y_max = min(path_ys), max(path_ys)
    y_span = max(1.0, y_max - y_min)

    def to_x(t: float) -> float:
        return pad_x + (t - x_min) / x_span * plot_w

    def to_y(v: float) -> float:
        if y_max == y_min:
            return pad_y + plot_h / 2  # center a single value vertically
        return pad_y + (y_max - v) / y_span * plot_h

    rendered = [(to_x(t), to_y(v)) for t, v in zip(path_xs, path_ys)]
    baseline_y = pad_y + plot_h

    if len(rendered) >= 2:
        line_path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in rendered)
        area_path = " ".join(
            [f"M {rendered[0][0]:.2f},{baseline_y:.2f}"]
            + [f"L {x:.2f},{y:.2f}" for x, y in rendered]
            + [f"L {rendered[-1][0]:.2f},{baseline_y:.2f}", "Z"]
        )
    else:
        line_path = ""
        area_path = ""

    # Hover snaps to real snapshots only — the synthetic zero points aren't
    # actual data points the user can click through to.
    point_data = [
        {
            "x": round(to_x(t), 2),
            "y": round(to_y(v), 2),
            "ts": int(t),
            "label": s.taken_at.strftime("%b %d, %Y"),
            "value": v,
        }
        for s, t, v in zip(snapshots, xs, ys)
    ]

    return {
        "width": width,
        "height": height,
        "line_path": line_path,
        "area_path": area_path,
        "trend": "up" if ys[-1] >= ys[0] else "down",
        "first_value": ys[0],
        "last_value": ys[-1],
        "points": point_data,
    }
