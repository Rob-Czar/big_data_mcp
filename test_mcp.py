
import asyncio
import json
from mcp import Client

MCP_URL = "http://127.0.0.1:5000/mcp"


async def main():
    async with Client(MCP_URL) as client:

        # Discover available MCP tools
        response = await client.list_tools()

        print("\nAvailable MCP tools:")
        tool_names = set()

        for tool in response.tools:
            print("-", tool.name)
            tool_names.add(tool.name)

        # Tests for both MongoDB and Redis
        tests = [
            (
                "search_restaurants",
                {
                    "cuisine": "Italian",
                    "borough": "Brooklyn",
                    "limit": 3
                }
            ),
            (
                "top_cuisines",
                {
                    "borough": "Queens",
                    "limit": 5
                }
            ),
            (
                "inspection_grades_by_borough",
                {}
            ),
            (
                "get_restaurant",
                {
                    "restaurant_id": "40356018"
                }
            )
        ]

        for tool_name, arguments in tests:
            print(f"\n--- Testing {tool_name} ---")

            if tool_name not in tool_names:
                print("FAILED: Tool not registered")
                continue

            try:
                result = await client.call_tool(
                    tool_name,
                    arguments
                )

                if result.is_error:
                    print("FAILED: MCP tool error")
                else:
                    print("SUCCESS: MCP tool executed")

                for item in result.content:
                    content = getattr(item, "text", str(item))

                    try:
                        data = json.loads(content)
                        print(json.dumps(data, indent=2)[:2000])
                    except (ValueError, TypeError):
                        print(content[:2000])

            except Exception as e:
                print(
                    "FAILED:",
                    type(e).__name__,
                    str(e)
                )


if __name__ == "__main__":
    asyncio.run(main())

