"""User defined segment effects (static colour layouts, applied locally).

A strip only keeps the colours of segments that were addressed, everything
else falls back to stored values. So an effect always paints every segment:
first the background, then the fills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from .protocol import segment_color_packet

Rgb = tuple[int, int, int]


class EffectConfigError(ValueError):
    """The custom effects option is not valid."""


@dataclass(frozen=True)
class Fill:
    """One colour on a set of segments (0-based)."""

    color: Rgb
    segments: tuple[int, ...]


@dataclass(frozen=True)
class SegmentEffect:
    """A named static layout."""

    name: str
    background: Rgb = (0, 0, 0)
    fills: tuple[Fill, ...] = field(default_factory=tuple)

    def packets(self, segment_count: int) -> list[bytes]:
        """Return the packets that paint the whole strip."""
        colors: dict[int, Rgb] = dict.fromkeys(range(segment_count), self.background)
        for fill in self.fills:
            for segment in fill.segments:
                if segment >= segment_count:
                    raise EffectConfigError(
                        f"effect {self.name!r}: segment {segment} does not exist "
                        f"(device has {segment_count})"
                    )
                colors[segment] = fill.color
        by_color: dict[Rgb, list[int]] = {}
        for segment, color in colors.items():
            by_color.setdefault(color, []).append(segment)
        # Background first, so a later colour never gets overwritten.
        ordered = sorted(by_color.items(), key=lambda item: item[0] != self.background)
        return [segment_color_packet(color, segs) for color, segs in ordered]


def _rgb(value: Any, where: str) -> Rgb:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or not all(isinstance(c, int) and 0 <= c <= 255 for c in value)
    ):
        raise EffectConfigError(f"{where}: colour must be [r, g, b] with 0-255")
    return (value[0], value[1], value[2])


def parse_effects(text: str | None) -> dict[str, SegmentEffect]:
    """Parse the JSON option.

    Format::

        {"Name": {"background": [0,0,0],
                  "fills": [{"color": [255,0,0], "segments": [10, 11]}]}}
    """
    if not text or not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as err:
        raise EffectConfigError(f"not valid JSON: {err}") from err
    if not isinstance(raw, dict):
        raise EffectConfigError("top level must be an object {name: effect}")

    effects: dict[str, SegmentEffect] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise EffectConfigError("effect names must be non-empty strings")
        if not isinstance(body, dict):
            raise EffectConfigError(f"{name!r}: must be an object")
        fills = []
        for index, fill in enumerate(body.get("fills", [])):
            where = f"{name!r} fill {index}"
            if not isinstance(fill, dict):
                raise EffectConfigError(f"{where}: must be an object")
            segs = fill.get("segments")
            if not isinstance(segs, list) or not all(
                isinstance(s, int) and 0 <= s < 56 for s in segs
            ):
                raise EffectConfigError(f"{where}: segments must be a list of 0-based ints")
            fills.append(Fill(_rgb(fill.get("color"), where), tuple(segs)))
        effects[name] = SegmentEffect(
            name=name,
            background=_rgb(body.get("background", [0, 0, 0]), f"{name!r} background"),
            fills=tuple(fills),
        )
    return effects
