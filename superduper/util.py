import asyncio
from pathlib import Path

ASSETS_DIR = Path(__file__).parent / "assets"
if not ASSETS_DIR.exists():
    ASSETS_DIR = Path(__file__).parent.parent / "dev" / "assets"
    if not ASSETS_DIR.exists():
        raise FileNotFoundError


def later(awaitable):
    asyncio.create_task(_later(awaitable))


async def _later(awaitable):
    await asyncio.sleep(0.5)
    await awaitable
