"""Backfill platform_event_id on lead_touchpoints where it is NULL.

Correlates via intake_event_logs: if a log row has status='duplicate'
and its lead has a touchpoint with a matching created_at window,
set the touchpoint's platform_event_id from the log.
"""

import asyncio

from sqlalchemy import text

from core.db import get_session


async def main() -> None:
    async for session in get_session():
        result = await session.execute(
            text("""
            UPDATE lead_touchpoints lt
            SET platform_event_id = iel.platform_event_id
            FROM intake_event_logs iel
            JOIN leads l ON iel.lead_id = l.id
            WHERE lt.lead_id = l.id
              AND lt.platform_event_id IS NULL
              AND iel.status = 'duplicate'
              AND ABS(EXTRACT(EPOCH FROM (lt.created_at - iel.created_at))) < 10
            RETURNING lt.id, iel.platform_event_id
        """)
        )
        await session.commit()
        rows = result.fetchall()
        for row in rows:
            print(f"updated touchpoint {row[0]} -> platform_event_id={row[1]}")
        if not rows:
            print("nothing to backfill")


asyncio.run(main())
