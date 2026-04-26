import asyncio

from app.db import init_db
from app.jobs import worker_loop


if __name__ == "__main__":
    init_db()
    asyncio.run(worker_loop())

