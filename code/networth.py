import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import asc

import providers
from models import AccountBalanceSnapshot, NetWorthSnapshot, User

log = logging.getLogger(__name__)


def capture(user: User, session) -> NetWorthSnapshot | None:
    if not user.items:
        return None
    data = providers.fetch_all(user, force_refresh=True)

    # Healthy item ids appear in returned account rows; missing ones either
    # errored or hit reauth. Carry forward the most recent same-bucket balance
    # for each missing item so one broken bank doesn't blank net worth.
    healthy_item_ids: set[int] = set()
    for bucket in ("cash", "credit", "investment", "other"):
        for acct in data[bucket]:
            if "item_id" in acct:
                healthy_item_ids.add(acct["item_id"])
    degraded_item_ids = {it.id for it in user.items} - healthy_item_ids
    if degraded_item_ids:
        carried = _carry_forward(degraded_item_ids, session, user.id)
        if carried is None:
            log.warning(
                "networth.capture skipped for user_id=%s: degraded items %s have no prior snapshot",
                user.id, degraded_item_ids,
            )
            return None
        for bucket, accts in carried.items():
            data[bucket].extend(accts)

    cash = providers.sum_balances(data["cash"])
    investments = providers.sum_balances(data["investment"])
    credit = providers.sum_balances(data["credit"])

    today = datetime.now(timezone.utc).date()
    start_of_day = datetime.combine(today, datetime.min.time())
    end_of_day = start_of_day + timedelta(days=1)
    session.query(NetWorthSnapshot).filter(
        NetWorthSnapshot.user_id == user.id,
        NetWorthSnapshot.taken_at >= start_of_day,
        NetWorthSnapshot.taken_at < end_of_day,
    ).delete(synchronize_session=False)
    session.query(AccountBalanceSnapshot).filter(
        AccountBalanceSnapshot.user_id == user.id,
        AccountBalanceSnapshot.taken_at >= start_of_day,
        AccountBalanceSnapshot.taken_at < end_of_day,
    ).delete(synchronize_session=False)

    snapshot = NetWorthSnapshot(
        user_id=user.id,
        cash_total=cash,
        investment_total=investments,
        credit_total=credit,
        net_worth=cash + investments - credit,
    )
    session.add(snapshot)

    for bucket in ("cash", "credit", "investment", "other"):
        for acct in data[bucket]:
            if acct.get("balance") is None or "item_id" not in acct:
                continue
            session.add(AccountBalanceSnapshot(
                user_id=user.id,
                item_id=acct["item_id"],
                plaid_account_id=acct["plaid_account_id"],
                account_name=acct.get("name"),
                institution_name=acct.get("institution"),
                bucket=bucket,
                balance=float(acct["balance"]),
            ))

    session.commit()
    return snapshot


def _carry_forward(
    item_ids: set[int], session, user_id: int,
) -> dict[str, list[dict]] | None:
    out: dict[str, list[dict]] = {"cash": [], "credit": [], "investment": [], "other": []}
    found_any = False
    for item_id in item_ids:
        rows = (
            session.query(AccountBalanceSnapshot)
            .filter_by(user_id=user_id, item_id=item_id)
            .order_by(AccountBalanceSnapshot.taken_at.desc())
            .all()
        )
        latest_by_acct: dict[str, AccountBalanceSnapshot] = {}
        for r in rows:
            if r.plaid_account_id not in latest_by_acct:
                latest_by_acct[r.plaid_account_id] = r
        if not latest_by_acct:
            continue
        found_any = True
        for r in latest_by_acct.values():
            bucket = r.bucket if r.bucket in out else "other"
            out[bucket].append({
                "institution": r.institution_name or "Unknown",
                "name": r.account_name or "",
                "plaid_account_id": r.plaid_account_id,
                "balance": float(r.balance),
                "item_id": r.item_id,
            })
    return out if found_any or not item_ids else None


def get_snapshots(user: User, session) -> list[NetWorthSnapshot]:
    return (
        session.query(NetWorthSnapshot)
        .filter_by(user_id=user.id)
        .order_by(asc(NetWorthSnapshot.taken_at))
        .all()
    )


def get_account_snapshots(user: User, session) -> list[AccountBalanceSnapshot]:
    return (
        session.query(AccountBalanceSnapshot)
        .filter_by(user_id=user.id)
        .order_by(asc(AccountBalanceSnapshot.taken_at))
        .all()
    )


def _utc_ts_for_day(d) -> int:
    return int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def build_series_data(snapshots, account_snaps) -> dict:
    """Return {series_key: [{ts, value}]} for net worth, per-institution, per-account."""
    out: dict = {}

    out["net"] = [
        {
            "ts": int(s.taken_at.replace(tzinfo=timezone.utc).timestamp()),
            "value": s.net_worth,
        }
        for s in snapshots
    ]

    by_inst: dict = defaultdict(lambda: defaultdict(float))
    by_acct: dict = defaultdict(lambda: {})
    for s in account_snaps:
        # Mirror net definition (cash + investments - credit); excluding
        # credit/other keeps stacked series summing to the net line.
        if s.bucket in ("credit", "other"):
            continue
        d = s.taken_at.date()
        inst = s.institution_name or "Unknown"
        by_inst[inst][d] += s.balance
        by_acct[s.plaid_account_id][d] = s.balance

    for inst, day_map in by_inst.items():
        out["inst:" + inst] = [
            {"ts": _utc_ts_for_day(d), "value": v}
            for d, v in sorted(day_map.items())
        ]
    for acct_id, day_map in by_acct.items():
        out["acct:" + acct_id] = [
            {"ts": _utc_ts_for_day(d), "value": v}
            for d, v in sorted(day_map.items())
        ]

    return out


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

    # Naive datetime.timestamp() treats as local; force UTC for browser parity.
    def _utc_ts(dt):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc).timestamp()
        return dt.timestamp()
    xs = [_utc_ts(s.taken_at) for s in snapshots]
    ys = [s.net_worth for s in snapshots]

    # Synthetic zero prefix draws a flat-then-spike L before first real data.
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

    has_synthetic_prefix = (
        range_start_ts is not None and xs[0] > range_start_ts
    )
    baseline = 0.0 if has_synthetic_prefix else ys[0]
    return {
        "width": width,
        "height": height,
        "line_path": line_path,
        "area_path": area_path,
        "trend": "down" if ys[-1] < baseline else "up",
        "first_value": baseline,
        "last_value": ys[-1],
        "points": point_data,
    }
