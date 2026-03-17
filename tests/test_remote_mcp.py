
import asyncio
import os
import sys

# Add current dir to path if needed for local mcp imports
sys.path.insert(0, os.getcwd())

from mcp import ClientSession
try:
    from mcp.client.streamable_http import streamable_http_client as client_factory
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client as client_factory

async def test_remote_mcp():
    url = "http://127.0.0.1:8000/mcp"
    print(f"Connecting to remote MCP server at {url}...")
    
    try:
        async with client_factory(url) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                
                print("\nDiscovering tools...")
                result = await session.list_tools()
                tools = result.tools
                print(f"Found {len(tools)} tools.")
                
                # List first 5 tools
                for tool in tools[:5]:
                    print(f" - {tool.name}: {tool.description[:50]}...")
                
                if tools:
                    test_tool = "ae.request.list_recent"
                    print(f"\nCalling tool {test_tool}...")
                    call_result = await session.call_tool(test_tool, arguments={"limit": 1})
                    print("Result received successfully!")
                    # print(f"Output: {call_result}")
                
    except Exception as e:
        print(f"FAILED to connect or execute: {e}")

if __name__ == "__main__":
    asyncio.run(test_remote_mcp())
