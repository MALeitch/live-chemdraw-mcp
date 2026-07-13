"""ChemDraw MCP server — drives a live ChemDraw window over COM automation."""
from mcp.server.fastmcp import FastMCP

from chemdraw_connector.bridge import ChemDrawBridge
from tools import register_all

mcp = FastMCP(
    "chemdraw",
    instructions=(
        "Tools for drawing, editing, and organizing chemical structures in a "
        "live ChemDraw window. The user works in ChemDraw alongside you: "
        "before manipulating anything you didn't just create this turn, call "
        "chemdraw_get_document_state or chemdraw_diff_since_last_check to see "
        "the current canvas (the user may have edited by hand). Layout tools "
        "return preview images — inspect them and iterate rather than "
        "trusting the first arrangement."
    ),
)

bridge = ChemDrawBridge()
register_all(mcp, bridge)

if __name__ == "__main__":
    mcp.run()
