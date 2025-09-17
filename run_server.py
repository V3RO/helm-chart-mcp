import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "src" / "chart_tools"))

from helm_chart_mcp.server import main

if __name__ == "__main__":
    asyncio.run(main())
