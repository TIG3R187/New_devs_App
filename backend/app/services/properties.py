from typing import Dict, Any, List

from sqlalchemy import text

from app.core.database_pool import db_pool


async def list_properties(tenant_id: str) -> List[Dict[str, Any]]:
    """
    Properties owned by one tenant, for the dashboard property selector.
    """
    await db_pool.initialize()
    if not db_pool.session_factory:
        raise RuntimeError("Database pool not available")

    query = text("""
        SELECT id, name, timezone
        FROM properties
        WHERE tenant_id = :tenant_id
        ORDER BY id
    """)

    async with db_pool.get_session() as session:
        result = await session.execute(query, {"tenant_id": tenant_id})
        return [dict(row._mapping) for row in result]
