from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

from sqlalchemy import text

from app.core.database_pool import db_pool

CENT = Decimal("0.01")


def round_to_cents(amount) -> Decimal:
    """
    Rounds a money amount to whole cents (half-up), in Decimal.

    total_amount is stored with 3 decimals, so totals can carry sub-cent values.
    Rounding them once here - instead of after a float conversion in Python/JS -
    is what keeps reported totals from drifting by a cent.
    """
    return Decimal(str(amount or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


async def _fetch_revenue(query, params: Dict[str, Any]) -> Dict[str, Any]:
    await db_pool.initialize()
    if not db_pool.session_factory:
        raise RuntimeError("Database pool not available")

    async with db_pool.get_session() as session:
        result = await session.execute(query, params)
        row = result.fetchone()

    return {
        "property_id": params["property_id"],
        "tenant_id": params["tenant_id"],
        "total": str(round_to_cents(row.total_revenue)),
        "currency": "USD",
        "count": row.reservation_count,
    }


async def calculate_monthly_revenue(property_id: str, tenant_id: str, month: int, year: int) -> Dict[str, Any]:
    """
    Calculates revenue for a specific month.

    The month is the property's *local* calendar month: e.g. a check-in at
    2024-02-29 23:30 UTC is 2024-03-01 00:30 in Paris, so it is March revenue.
    """

    start_date = datetime(year, month, 1)
    if month < 12:
        end_date = datetime(year, month + 1, 1)
    else:
        end_date = datetime(year + 1, 1, 1)

    # start/end are local midnights; AT TIME ZONE turns them into the matching UTC instants
    # for each property's own timezone before comparing with check_in_date (timestamptz).
    query = text("""
        SELECT
            COALESCE(SUM(r.total_amount), 0) AS total_revenue,
            COUNT(r.id) AS reservation_count
        FROM reservations r
        JOIN properties p ON p.id = r.property_id AND p.tenant_id = r.tenant_id
        WHERE r.property_id = :property_id
        AND r.tenant_id = :tenant_id
        AND r.check_in_date >= (CAST(:start_date AS timestamp) AT TIME ZONE p.timezone)
        AND r.check_in_date < (CAST(:end_date AS timestamp) AT TIME ZONE p.timezone)
    """)

    return await _fetch_revenue(query, {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "start_date": start_date,
        "end_date": end_date,
    })


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    # No mock fallback on errors: returning made-up totals (that also ignored the tenant)
    # is worse than failing - the dashboard shows an error instead of wrong numbers.
    query = text("""
        SELECT
            COALESCE(SUM(total_amount), 0) AS total_revenue,
            COUNT(*) AS reservation_count
        FROM reservations
        WHERE property_id = :property_id AND tenant_id = :tenant_id
    """)

    return await _fetch_revenue(query, {
        "property_id": property_id,
        "tenant_id": tenant_id,
    })
