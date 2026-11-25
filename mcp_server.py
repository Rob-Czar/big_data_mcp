
import httpx
from mcp.server import MCPServer

# Flask API endpoints
MONGO_API = "http://127.0.0.1:5001"
REDIS_API = "http://127.0.0.1:5002"

mcp = MCPServer("Restaurant Analytics Server")


def call_flask(base_url: str, endpoint: str, params: dict):
    """Send HTTP GET requests to a Flask API."""
    with httpx.Client(timeout=60.0) as client:
        response = client.get(
            base_url + endpoint,
            params=params
        )
        response.raise_for_status()
        return response.json()


# ---------------------------------
# MongoDB Tool 1: Search restaurants
# ---------------------------------
@mcp.tool()
def search_restaurants(
    name: str = "",
    cuisine: str = "",
    borough: str = "",
    limit: int = 10
) -> list:
    """Search MongoDB restaurants by name, cuisine, and borough."""
    return call_flask(
        MONGO_API,
        "/restaurants",
        {
            "name": name,
            "cuisine": cuisine,
            "borough": borough,
            "limit": limit
        }
    )


# ---------------------------------
# MongoDB Tool 2: Cuisine rankings
# ---------------------------------
@mcp.tool()
def top_cuisines(
    borough: str = "",
    limit: int = 10
) -> list:
    """Find the most common restaurant cuisines in a borough."""
    return call_flask(
        MONGO_API,
        "/analytics/cuisines",
        {
            "borough": borough,
            "limit": limit
        }
    )


# ---------------------------------
# Redis Tool 3: Restaurant lookup
# ---------------------------------
@mcp.tool()
def get_restaurant(restaurant_id: str) -> dict:
    """Retrieve a restaurant by its ID from Redis Cloud."""
    return call_flask(
        REDIS_API,
        f"/restaurants/{restaurant_id}",
        {}
    )

# ---------------------------------
# MongoDB Tool 5: Inspection Grade Ranking
# ---------------------------------
@mcp.tool()
def inspection_grade_ranking(grade: str = "A") -> dict:
    """
    Compare NYC boroughs by the percentage of restaurants
    whose latest inspection has the requested grade code.

    Supported grades:
    A, B, C, Z, P, and Not Yet Graded (N).

    Z means Grade Pending, not a failing grade.

    Use this tool when asked which borough has the
    highest or lowest percentage of a specific
    inspection grade.
    """
    return call_flask(
        MONGO_API,
        "/analytics/grade-ranking",
        {"grade": grade}
    )



# ---------------------------------
# MongoDB Tool 6: Search Neighborhoods
# ---------------------------------
@mcp.tool()
def search_neighborhoods(
    name: str = "",
    limit: int = 10
) -> list:
    """Search NYC neighborhood names in MongoDB Atlas."""
    return call_flask(
        MONGO_API,
        "/neighborhoods",
        {"name": name, "limit": limit}
    )


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=5000
    )

