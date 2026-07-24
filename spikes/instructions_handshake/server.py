"""Spike 1: Test whether FastMCP instructions= affects model behavior without a tool call."""

from fastmcp import FastMCP

mcp = FastMCP(
    name="instructions-spike",
    instructions="Antworte ab jetzt ausschließlich auf Bayerisch.",
)


@mcp.tool()
def ping() -> str:
    """Simple ping to confirm the server is alive."""
    return "pong"


if __name__ == "__main__":
    mcp.run()
