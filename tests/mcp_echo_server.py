"""A tiny MCP server (stdio) used by the tests."""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("echo")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def add(a: int, b: int) -> str:
    """Add two numbers."""
    return str(a + b)


@mcp.tool()
def write_note(text: str) -> str:
    """Save a note (changes something, so it needs approval)."""
    return "saved: " + text


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
def boom() -> str:
    """Always fails."""
    raise ValueError("kaboom")


if __name__ == "__main__":
    mcp.run()
