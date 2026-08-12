"""Greenfield preflight: prove the Showdown account logs in on the REAL ladder
server without starting a battle. Never prints the password.

Exit 0 + 'LOGIN_OK' on success; non-zero otherwise.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from fp.websocket_client import PSWebsocketClient

URI = "wss://sim3.psim.us/showdown/websocket"
USER = "DekuFoulerLab"
FMT = "gen9ou"


async def main() -> int:
    pw = os.getenv("PS_PASSWORD")
    print("password_present:", bool(pw), flush=True)
    if not pw:
        print("FAIL: PS_PASSWORD not found in environment/.env", flush=True)
        return 3
    client = await PSWebsocketClient.create(USER, pw, URI, expected_format=FMT)
    try:
        user_id = await client.login()
        print("LOGIN_OK user_id=", user_id, flush=True)
        try:
            await client.cancel_search()
        except Exception:
            pass
        return 0
    finally:
        try:
            await client.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        rc = 2
    sys.exit(rc)
