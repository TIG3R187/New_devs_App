"""
Regression tests for the three dashboard issues (cross-tenant cache, March totals, cents drift).

Unit tests run anywhere. The DB tests need the seeded Postgres from docker-compose
(DATABASE_URL, e.g. postgresql://postgres:postgres@localhost:5433/propertyflow) and are skipped otherwise.

    cd backend && uv run --with pytest pytest -q
"""
import asyncio
import json
import os
from decimal import Decimal

import pytest

from app.services import cache
from app.services.reservations import round_to_cents


# --- Finance: "slightly off by a few cents" ---------------------------------------------

def test_round_to_cents_is_half_up_in_decimal():
    # Binary floats store 1024.215 as 1024.2149999..., so Math.round(x * 100) / 100 gives 1024.21
    assert str(round_to_cents(Decimal("1024.215"))) == "1024.22"
    assert str(round_to_cents(Decimal("1.005"))) == "1.01"


def test_sub_cent_amounts_are_summed_before_rounding():
    # res-dec-1..3: rounding each booking first would report 999.99
    total = Decimal("333.333") + Decimal("333.333") + Decimal("333.334")
    assert str(round_to_cents(total)) == "1000.00"


# --- Client B: "numbers that belong to another company" ---------------------------------

class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


def test_revenue_cache_is_isolated_per_tenant(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache, "redis_client", fake)

    async def fake_total(property_id, tenant_id):
        return {"property_id": property_id, "tenant_id": tenant_id, "total": "1.00", "currency": "USD", "count": 1}

    monkeypatch.setattr("app.services.reservations.calculate_total_revenue", fake_total)

    async def scenario():
        a = await cache.get_revenue_summary("prop-001", "tenant-a")
        b = await cache.get_revenue_summary("prop-001", "tenant-b")
        return a, b

    a, b = asyncio.run(scenario())
    assert a["tenant_id"] == "tenant-a"
    assert b["tenant_id"] == "tenant-b"  # before the fix this was tenant-a's cached entry
    assert len(fake.store) == 2


# --- Client A: "different totals for March" (needs the seeded DB) -----------------------

needs_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="set DATABASE_URL to the seeded docker-compose Postgres",
)


def _run(coro_factory):
    async def wrapper():
        from app.core.database_pool import db_pool
        try:
            return await coro_factory()
        finally:
            await db_pool.close()
            db_pool.engine = None
            db_pool.session_factory = None
    return asyncio.run(wrapper())


@needs_db
def test_march_uses_property_local_time():
    from app.services.reservations import calculate_monthly_revenue

    march = _run(lambda: calculate_monthly_revenue("prop-001", "tenant-a", 3, 2024))
    feb = _run(lambda: calculate_monthly_revenue("prop-001", "tenant-a", 2, 2024))

    # res-tz-1 checks in 2024-02-29 23:30 UTC = 2024-03-01 00:30 Europe/Paris -> March
    assert (march["total"], march["count"]) == ("2250.00", 4)
    assert (feb["total"], feb["count"]) == ("0.00", 0)


@needs_db
def test_totals_come_from_db_and_respect_tenant():
    from app.services.reservations import calculate_total_revenue

    sunset = _run(lambda: calculate_total_revenue("prop-001", "tenant-a"))
    ocean = _run(lambda: calculate_total_revenue("prop-001", "tenant-b"))

    assert (sunset["total"], sunset["count"]) == ("2250.00", 4)
    assert (ocean["total"], ocean["count"]) == ("0.00", 0)  # Mountain Lodge Beta has no bookings
    json.dumps(sunset)  # must stay cacheable
