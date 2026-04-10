import asyncio
from app.database import SessionLocal
from app.models import Connection
from app.services import connectors

async def test():
    db = SessionLocal()
    conns = db.query(Connection).filter(Connection.platform == 'google').all()
    print(f"Found {len(conns)} google connections.")
    for i, c in enumerate(conns):
        print(f"Conn {i}: account={c.account_id}, selected={c.selected_account_ids}")
        df = await connectors.fetch_platform_data(
            c.platform, c.account_id, refresh_token=c.refresh_token, 
            start_date="2026-02-01", end_date="2026-03-31"
        )
        print(f"Conn {i} spend: {df['spend'].sum()}")
        import pandas as pd
        print(f"Conn {i} row count: {len(df)}")
        # let's find dupes
        dupes = df.duplicated(subset=['date', 'campaign'])
        if dupes.any():
            print(f"Conn {i} HAS DUPES!")
            print(df[dupes].head())

asyncio.run(test())
