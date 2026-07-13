"""Journal style preset tools."""
from ._common import as_json


def register(mcp, bridge):
    @mcp.tool()
    def chemdraw_apply_style_preset(preset: str = "ACS 1996") -> str:
        """Apply a journal style to the whole document — bond length, line
        widths, fonts — and restyle existing objects to match (fixes pasted-in
        structures with mismatched settings). Presets: ACS 1996 | RSC | Wiley |
        default. A document backup is saved first (backup_path)."""
        return as_json(bridge.apply_style_preset(preset))
