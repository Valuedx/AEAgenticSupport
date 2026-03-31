"""Quick test — verify ticket_create response format."""
import asyncio
import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from mcp_server.tools.ticket_tools import ticket_create


async def test():
    print("=" * 50)
    print("Test: ticket_create response check")
    print("=" * 50)

    result = await ticket_create(
        process_name="Demat Process",
        description="Test ticket - Process Failed",
        request_type="Request",
    )

    print(f"\nRaw response:\n{result}")

    parsed = json.loads(result)
    print(f"\nsuccess: {parsed.get('success')}")
    print(f"ticket_id: {parsed.get('ticket_id')}")
    print(f"message: {parsed.get('message')}")
    print(f"error: {parsed.get('error')}")

    if parsed.get("success"):
        print("\n✅ Ticket created successfully!")
    else:
        print(f"\n⚠️ Failed: {parsed.get('error')}")


if __name__ == "__main__":
    asyncio.run(test())
