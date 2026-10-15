<div align="center">

<img src="docs/gifs/hero.gif" width="880" alt="PropertyFlow revenue dashboard: three client complaints, all fixed" />

<br/>

<img src="https://img.shields.io/badge/FastAPI-0f172a?style=for-the-badge&logo=fastapi&logoColor=34d399" />
<img src="https://img.shields.io/badge/React_18-0f172a?style=for-the-badge&logo=react&logoColor=38bdf8" />
<img src="https://img.shields.io/badge/Tailwind_CSS-0f172a?style=for-the-badge&logo=tailwindcss&logoColor=38bdf8" />
<img src="https://img.shields.io/badge/PostgreSQL_15-0f172a?style=for-the-badge&logo=postgresql&logoColor=818cf8" />
<img src="https://img.shields.io/badge/Redis-0f172a?style=for-the-badge&logo=redis&logoColor=fb7185" />
<img src="https://img.shields.io/badge/Docker-0f172a?style=for-the-badge&logo=docker&logoColor=38bdf8" />

<img src="https://img.shields.io/badge/client_issues-3%2F3_fixed-34d399?style=flat-square&labelColor=1e293b" />
<img src="https://img.shields.io/badge/bugs_found-7-fbbf24?style=flat-square&labelColor=1e293b" />
<img src="https://img.shields.io/badge/tests-5_passing-34d399?style=flat-square&labelColor=1e293b" />

<br/><br/>

<a href="https://drive.google.com/file/d/1o9PfoPIL9S7lAZAoPjoe7yNY22_gGgD3/view?usp=drive_link">
  <img src="https://img.shields.io/badge/%E2%96%B6%20Watch%20the%20video%20walkthrough-0f172a?style=for-the-badge&logo=googledrive&logoColor=38bdf8" alt="Watch the video walkthrough" />
</a>

</div>

---

## 🎥 Video walkthrough

**[Watch on Google Drive →](https://drive.google.com/file/d/1o9PfoPIL9S7lAZAoPjoe7yNY22_gGgD3/view?usp=drive_link)**

A ~9 minute walkthrough covering what bugs I found and how, the root cause of each, the fixes, and a live demo
with both client accounts. (Hosted on Google Drive in place of Loom.)

---

## The situation

| Who | What they said | What was actually wrong |
| :-- | :-- | :-- |
| 🟥 **Ocean Rentals** (tenant-b) | *"Sometimes on refresh we see revenue that looks like another company's."* | Redis cache key had no tenant in it **and** the mock fallback ignored the tenant |
| 🟧 **Sunset Properties** (tenant-a) | *"March totals don't match our internal records."* | DB pool never connected (served hardcoded mock data) **and** months were cut in UTC, not the property's timezone |
| 🟨 **Finance team** | *"Totals are slightly off by a few cents."* | Sub-cent money (`NUMERIC(10,3)`) rounded as binary floats |

## TL;DR: bugs and fixes

| # | Bug | Root cause | Fix | Files |
| :-: | :-- | :-- | :-- | :-- |
| 1 | **Cross-tenant cache leak** | Cache key was `revenue:{property_id}`. Both tenants own a `prop-001`, so whoever loaded first got cached and the other tenant was served those numbers | Key is now `revenue:{tenant_id}:{property_id}:{period}` | [`services/cache.py`](backend/app/services/cache.py) |
| 2 | **Dashboard never read the database** | `DatabasePool` built its URL from `settings.supabase_db_*` (fields that don't exist), used `QueuePool` (invalid for async engines), and `get_session()` was `async def` but used with `async with`. Every request hit the `except` and returned **hardcoded mock totals that ignored the tenant** | Build URL from `DATABASE_URL`, default async pool, plain `get_session()`, one shared pool; mock fallback removed (an error beats fake financial data) | [`core/database_pool.py`](backend/app/core/database_pool.py), [`services/reservations.py`](backend/app/services/reservations.py) |
| 3 | **Missing async driver dependency** | `sqlalchemy>=2.0.0` now resolves to 2.1, where `greenlet` (needed by the async engine) is an optional extra, so a fresh install can't run async queries at all | `sqlalchemy[asyncio]>=2.0.0` | [`requirements.txt`](backend/requirements.txt) |
| 4 | **March cut in UTC** | Month boundaries were naive `datetime(year, month, 1)`. `res-tz-1` checks in `2024-02-29 23:30 UTC` = **`2024-03-01 00:30` Paris**, so it fell into February | Month boundaries are local midnight in the property's timezone (`AT TIME ZONE p.timezone`); `?month=&year=` on the summary endpoint | [`services/reservations.py`](backend/app/services/reservations.py), [`api/v1/dashboard.py`](backend/app/api/v1/dashboard.py) |
| 5 | **Cents drift** | Totals with sub-cent values were passed as floats and rounded with `Math.round(x*100)/100`. `1024.215` is stored as `1024.2149999…`, so it rounds to `1024.21` | Sum in SQL, round **once** in `Decimal` with `ROUND_HALF_UP`, send a clean 2-decimal amount | [`services/reservations.py`](backend/app/services/reservations.py) |
| 6 | **Other clients' properties in the dropdown** | Frontend hardcoded all 5 properties (with tenant-a's name for `prop-001`) for every user | New tenant-scoped `GET /api/v1/dashboard/properties`; dropdown loads from it | [`Dashboard.tsx`](frontend/src/components/Dashboard.tsx), [`services/properties.py`](backend/app/services/properties.py) |
| 7 | **Unknown users defaulted to Sunset's tenant** | `TenantResolver` returned `"tenant-a"` for any unmapped user; dashboard fell back to `"default_tenant"` | Resolver returns `None`; dashboard answers **403** without a tenant | [`core/tenant_resolver.py`](backend/app/core/tenant_resolver.py), [`api/v1/dashboard.py`](backend/app/api/v1/dashboard.py) |

---

## 🟥 Client B: someone else's revenue

<img src="docs/gifs/tenant-leak.gif" width="880" alt="Before: Ocean gets Sunset's cached revenue. After: tenant-scoped keys." />

```diff
- cache_key = f"revenue:{property_id}"
+ period = f"{year}-{month:02d}" if month and year else "all"
+ cache_key = f"revenue:{tenant_id}:{property_id}:{period}"
```

The database already scopes queries by tenant; the cache sat in front of it and threw that scope away.
On top of that, because the DB pool was broken (bug 2), the mock fallback returned the same totals to **every** tenant for **every** property ID.

## 🟧 Client A: March doesn't match

<img src="docs/gifs/march-timezone.gif" width="880" alt="res-tz-1 lands in February in UTC but in March in Paris." />

```sql
-- month boundaries are local midnights, converted per property
AND r.check_in_date >= (CAST(:start_date AS timestamp) AT TIME ZONE p.timezone)
AND r.check_in_date <  (CAST(:end_date   AS timestamp) AT TIME ZONE p.timezone)
```

| Beach House Alpha (prop-001) | Before | After |
| :-- | --: | --: |
| Dashboard total | $1,000.00 · 3 bookings *(mock)* | **$2,250.00 · 4 bookings** |
| March 2024 (`?month=3&year=2024`) | n/a | **$2,250.00 · 4 bookings** |
| February 2024 | n/a | $0.00 · 0 bookings |

## 🟨 Finance: a few cents off

<img src="docs/gifs/cents-rounding.gif" width="880" alt="1024.215 rounds to 1024.21 as a float, 1024.22 as a Decimal." />

```python
def round_to_cents(amount) -> Decimal:
    return Decimal(str(amount or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

Sub-cent amounts are **summed first, rounded once** (`333.333 + 333.333 + 333.334 = 1000.00`, not `999.99`).

---

## Request flow after the fix

```mermaid
flowchart LR
  UI[React dashboard] -->|JWT| API["GET /dashboard/summary"]
  API -->|tenant from token, 403 if none| C{Redis<br/>revenue:tenant:property:period}
  C -- hit --> API
  C -- miss --> S[reservations service]
  S -->|tenant-scoped SQL<br/>local-time months| PG[(Postgres)]
  S -->|Decimal, half-up to cents| C
```

## Run it

```bash
docker-compose up --build
# Frontend  http://localhost:3000
# API docs  http://localhost:8000/docs
```

<details>
<summary><b>Without building the images</b> (slow network / low RAM)</summary>

```bash
docker compose up -d db redis                     # Postgres :5433, Redis :6380

cd backend
uv venv --python 3.11 && uv pip install -r requirements.txt
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/propertyflow \
REDIS_URL=redis://localhost:6380/0 \
uv run uvicorn app.main:app --port 8000

cd ../frontend && npm install && npm run dev       # http://localhost:5173
```
</details>

| Client | Email | Password | Should see |
| :-- | :-- | :-- | :-- |
| Sunset Properties | `sunset@propertyflow.com` | `client_a_2024` | Beach House Alpha **$2,250.00**, City Apartment **$4,975.50**, Country Villa **$6,100.50** |
| Ocean Rentals | `ocean@propertyflow.com` | `client_b_2024` | Mountain Lodge Beta **$0.00**, Lakeside Cottage **$1,776.50**, Urban Loft **$3,256.00** |

### Tests

```bash
cd backend
DATABASE_URL=postgresql://postgres:postgres@localhost:5433/propertyflow uv run --with pytest pytest -q
# 5 passed
```

[`tests/test_revenue_fixes.py`](backend/tests/test_revenue_fixes.py) covers tenant-isolated caching, half-up cent rounding,
sum-then-round, Paris-local March totals, and DB-backed tenant-scoped totals.

## Noticed, deliberately left alone

- `currency` is hard-coded to `USD`; all seed data is USD, but mixed currencies would need a per-currency sum.
- The frontend sends an `X-Simulated-Tenant` header; the backend ignores it and always takes the tenant from the signed JWT.
- 256 pre-existing TypeScript errors in the frontend; none were introduced here.

<div align="center">
<sub>Explainer GIFs drawn with the Tailwind slate / sky / emerald / rose palette to match the dashboard.</sub>
</div>
