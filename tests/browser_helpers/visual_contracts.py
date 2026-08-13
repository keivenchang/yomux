"""Pure browser visual-contract conversions shared by fixture facades."""

from __future__ import annotations

import re


def css_color_rgb(color):
    value = str(color or "").strip()
    if value.startswith("#"):
        digits = value[1:]
        if len(digits) == 3:
            digits = "".join(character * 2 for character in digits)
        if not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
            raise ValueError(f"unsupported CSS color: {color!r}")
        return tuple(float(int(digits[index:index + 2], 16)) for index in (0, 2, 4))
    srgb = re.fullmatch(
        r"color\(\s*srgb\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)\s+([-+]?[\d.]+)(?:\s*/\s*[^)]+)?\s*\)",
        value,
        flags=re.IGNORECASE,
    )
    if srgb:
        return tuple(max(0.0, min(255.0, float(srgb.group(index)) * 255.0)) for index in (1, 2, 3))
    match = re.match(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)", value)
    if not match:
        raise ValueError(f"unsupported CSS color: {color!r}")
    return tuple(max(0.0, min(255.0, float(match.group(index)))) for index in (1, 2, 3))


def wcag_contrast_ratio(first, second):
    def relative_luminance(color):
        channels = []
        for value in css_color_rgb(color):
            channel = value / 255.0
            channels.append(channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4)
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

    left = relative_luminance(first)
    right = relative_luminance(second)
    return (max(left, right) + 0.05) / (min(left, right) + 0.05)


def css_hex_to_rgb(value: str) -> str:
    """Convert a #rgb/#rrggbb pin into Selenium's computed `rgb(r, g, b)` spelling."""
    digits = str(value or "").strip().removeprefix("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", digits):
        raise ValueError(f"expected #rgb or #rrggbb, got {value!r}")
    red, green, blue = (int(digits[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgb({red}, {green}, {blue})"
