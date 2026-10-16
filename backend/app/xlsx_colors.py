"""Resolve .xlsx cell colours (explicit RGB or theme + tint) to '#RRGGBB'.

Google Sheets exports some fills as theme colours (e.g. black section rows as
theme 1, "dark 1"), so reading only explicit RGB values misses them.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any

# Excel's theme colour index -> slot in the theme's <a:clrScheme>.
_THEME_ORDER = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
                "hlink", "folHlink"]
_DEFAULTS = {"lt1": "FFFFFF", "dk1": "000000", "lt2": "E7E6E6", "dk2": "44546A"}


def theme_palette(workbook) -> list[str]:
    """Hex colours of the workbook theme, in Excel's theme index order."""
    xml = getattr(workbook, "loaded_theme", None)
    text = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else (xml or "")
    palette = []
    for slot in _THEME_ORDER:
        m = re.search(rf"<a:{slot}>\s*<a:(?:srgbClr val|sysClr[^>]*lastClr)=\"([0-9A-Fa-f]{{6}})\"", text)
        palette.append((m.group(1) if m else _DEFAULTS.get(slot, "000000")).upper())
    return palette


def _apply_tint(hex6: str, tint: float) -> str:
    if not tint:
        return hex6
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = l * (1 + tint) if tint < 0 else l * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), s)
    return "".join(f"{round(c * 255):02X}" for c in (r, g, b))


def resolve(color: Any, palette: list[str]) -> str | None:
    """'#RRGGBB' for an explicit or theme colour; None for 'no colour' or unsupported kinds."""
    if color is None:
        return None
    kind = getattr(color, "type", None)
    if kind == "rgb":
        rgb = color.rgb
        if not isinstance(rgb, str) or len(rgb) != 8 or rgb == "00000000":
            return None
        return "#" + rgb[2:].upper()
    if kind == "theme" and isinstance(color.theme, int) and 0 <= color.theme < len(palette):
        return "#" + _apply_tint(palette[color.theme], float(color.tint or 0))
    return None


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255
