import asyncio

from app.db import init_db
from app.jobs import scheduler_loop


if __name__ == "__main__":
    init_db()
    asyncio.run(scheduler_loop())

