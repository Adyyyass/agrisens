import sys
import os
import json
import asyncio
from pathlib import Path

# Add backend to path
sys.path.append(os.getcwd())

from backend.api.app import get_statistics

async def test():
    try:
        stats = await get_statistics()
        print(json.dumps(stats, indent=2, default=str))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test())
