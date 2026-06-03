"""FX conversion with daily caching.

The conversion helper is the public surface: `convert(amount, from_ccy,
to_ccy, db, as_of=None)`. It returns the converted amount and a
`{rate, source}` envelope so callers (the ETL, the budget pacing
calculator, the dashboard) can show users which day's rate was used.

Source order:
1. Same currency → no-op, rate = 1.0, source = "identity".
2. Cached `FxRate` row for `(as_of_date, from, to)` → source = "cached".
3. Frankfurter open API (`https://api.frankfurter.app/latest`) →
   row cached and returned. Source = "frankfurter".
4. On any failure → rate = 1.0, source = "fallback". A warning is
   logged. Critical: never block the report on FX unavailability — we'd
   rather over/underpay on the conversion (caller's problem) than fail
   the whole sync.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from app import models

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return date.today().isoformat()


def _read_cached_rate(
    db: Session, as_of_date: str, from_ccy: str, to_ccy: str
) -> Optional[float]:
    row = (
        db.query(models.FxRate)
        .filter(
            models.FxRate.as_of_date == as_of_date,
            models.FxRate.from_currency == from_ccy,
            models.FxRate.to_currency == to_ccy,
        )
        .first()
    )
    if not row:
        return None
    try:
        return float(row.rate)
    except (TypeError, ValueError):
        return None


def _persist_rate(
    db: Session, as_of_date: str, from_ccy: str, to_ccy: str, rate: float
) -> None:
    db.add(models.FxRate(
        as_of_date=as_of_date,
        from_currency=from_ccy,
        to_currency=to_ccy,
        rate=str(rate),
    ))
    try:
        db.commit()
    except Exception:
        # Race: another worker just persisted the same pair. Roll back
        # and re-read; the caller doesn't care which one wins.
        db.rollback()


def _fetch_from_frankfurter(from_ccy: str, to_ccy: str) -> Optional[float]:
    """Free open-data FX API (https://www.frankfurter.app/). No auth."""
    try:
        r = httpx.get(
            "https://api.frankfurter.app/latest",
            params={"from": from_ccy, "to": to_ccy},
            timeout=5.0,
        )
        if r.status_code >= 300:
            logger.warning("Frankfurter returned %s for %s→%s", r.status_code, from_ccy, to_ccy)
            return None
        body = r.json()
        rate = body.get("rates", {}).get(to_ccy)
        if rate is None:
            return None
        return float(rate)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Frankfurter fetch failed (%s→%s): %s", from_ccy, to_ccy, exc)
        return None


def convert(
    amount: float,
    *,
    from_currency: str,
    to_currency: str,
    db: Session,
    as_of_date: Optional[str] = None,
) -> Tuple[float, dict]:
    """Convert `amount` from `from_currency` to `to_currency`.

    Returns `(converted_amount, envelope)` where envelope contains the
    rate and the source (`identity` / `cached` / `frankfurter` /
    `fallback`). Caller decides whether to surface the envelope.
    """
    from_ccy = (from_currency or "USD").upper()
    to_ccy = (to_currency or "USD").upper()
    if from_ccy == to_ccy:
        return amount, {"rate": 1.0, "source": "identity"}

    as_of = as_of_date or _today_iso()

    cached = _read_cached_rate(db, as_of, from_ccy, to_ccy)
    if cached is not None:
        return amount * cached, {"rate": cached, "source": "cached", "as_of_date": as_of}

    fresh = _fetch_from_frankfurter(from_ccy, to_ccy)
    if fresh is not None:
        _persist_rate(db, as_of, from_ccy, to_ccy, fresh)
        return amount * fresh, {"rate": fresh, "source": "frankfurter", "as_of_date": as_of}

    logger.warning(
        "FX rate unavailable for %s→%s on %s; falling back to identity (no conversion).",
        from_ccy, to_ccy, as_of,
    )
    return amount, {"rate": 1.0, "source": "fallback", "as_of_date": as_of}
