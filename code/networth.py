from datetime import datetime, timedelta, timezone

from sqlalchemy import asc

import providers
from models import NetWorthSnapshot, User


def capture(user: User, session) -> NetWorthSnapshot | None:
    # One row per UTC day; refreshing replaces today's row.
    if not user.items:
        return None
    data = providers.fetch_all(user, force_refresh=True)
    cash = providers.sum_balances(data["cash"])
    investments = providers.sum_balances(data["investment"])
    credit = providers.sum_balances(data["credit"])

    # UTC day boundary because taken_at is stored as naive UTC.
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
    return (
        session.query(NetWorthSnapshot)
        .filter_by(user_id=user.id)
        .order_by(asc(NetWorthSnapshot.taken_at))
        .all()
    )


def build_chart(
    snapshots,
    width: int = 1000,
    height: int = 150,
    range_start_ts: int | None = None,
    range_end_ts: int | None = None,
) -> dict | None:
    if not snapshots:
        return None

    pad_x = 4
    pad_y = 10
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    # Naive datetime.timestamp() treats as local time; force UTC to match browser.
    def _utc_ts(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    xs = [_utc_ts(s.taken_at) for s in snapshots]
    ys = [s.net_worth for s in snapshots]

    # Synthetic zero points create the flat-then-spike L-shape before first real data.
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
            return pad_y + plot_h / 2
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

    # Only real snapshots are hoverable; synthetic zero points are excluded.
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
