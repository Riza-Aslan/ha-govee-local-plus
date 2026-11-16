"""User defined segment effects (static colour layouts, applied locally).

A strip only keeps the colours of segments that were addressed, everything
else falls back to stored values. So an effect always paints every segment:
first the background, then the fills.

Stored form (segments are numbered from 1, like in the Govee Home app)::

    {"Name": {"background": [0, 0, 0],
              "fills": [{"color": [255, 0, 0], "segments": [11, 12]}]}}
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
import re
from typing import Any

from .const import NONE_EFFECT
from .protocol import segment_color_packet

Rgb = tuple[int, int, int]

# Width of the segment bitmask in the packet.
MAX_SEGMENTS = 56


class EffectConfigError(ValueError):
    """The effects are not valid."""


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

    @property
    def highest_segment(self) -> int:
        """Return the highest 0-based segment used, -1 if none."""
        return max((s for fill in self.fills for s in fill.segments), default=-1)

    def fits(self, segment_count: int) -> bool:
        """Return True if a device with this many segments can show the effect."""
        return self.highest_segment < segment_count

    def packets(self, segment_count: int) -> list[bytes]:
        """Return the packets that paint the whole strip."""
        if not self.fits(segment_count):
            raise EffectConfigError(
                f"effect {self.name!r}: segment {self.highest_segment + 1} does not "
                f"exist (device has {segment_count})"
            )
        colors: dict[int, Rgb] = dict.fromkeys(range(segment_count), self.background)
        for fill in self.fills:
            for segment in fill.segments:
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
        or not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value)
    ):
        raise EffectConfigError(f"{where}: colour must be [r, g, b] with 0-255")
    return (value[0], value[1], value[2])


def parse_effects(
    raw: Mapping[str, Any] | None, *, base: int = 1
) -> dict[str, SegmentEffect]:
    """Validate the stored form and turn it into effects.

    ``base`` is the number of the first segment in ``raw`` (1 now, 0 in the
    v1 options).
    """
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise EffectConfigError("top level must be an object {name: effect}")

    effects: dict[str, SegmentEffect] = {}
    for name, body in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise EffectConfigError("effect names must be non-empty strings")
        if name == NONE_EFFECT:
            raise EffectConfigError(f"{NONE_EFFECT!r} is reserved")
        if not isinstance(body, Mapping):
            raise EffectConfigError(f"{name!r}: must be an object")
        fills = []
        for index, fill in enumerate(body.get("fills", [])):
            where = f"{name!r} fill {index + 1}"
            if not isinstance(fill, Mapping):
                raise EffectConfigError(f"{where}: must be an object")
            segs = fill.get("segments")
            if not isinstance(segs, list) or not all(
                isinstance(s, int)
                and not isinstance(s, bool)
                and base <= s < base + MAX_SEGMENTS
                for s in segs
            ):
                raise EffectConfigError(
                    f"{where}: segments must be a list of numbers from {base} "
                    f"to {base + MAX_SEGMENTS - 1}"
                )
            fills.append(
                Fill(_rgb(fill.get("color"), where), tuple(s - base for s in segs))
            )
        effects[name] = SegmentEffect(
            name=name,
            background=_rgb(body.get("background", [0, 0, 0]), f"{name!r} background"),
            fills=tuple(fills),
        )
    return effects


def effects_to_raw(effects: Mapping[str, SegmentEffect]) -> dict[str, Any]:
    """Return the canonical stored form (lists, 1-based, sorted segments)."""
    return {
        name: {
            "background": list(effect.background),
            "fills": [
                {
                    "color": list(fill.color),
                    "segments": sorted({s + 1 for s in fill.segments}),
                }
                for fill in effect.fills
            ],
        }
        for name, effect in effects.items()
    }


def load_effects_json(text: str | None) -> dict[str, Any]:
    """Parse and validate effects entered as JSON text; return the stored form."""
    if not text or not text.strip():
        return {}
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as err:
        raise EffectConfigError(f"not valid JSON: {err}") from err
    return effects_to_raw(parse_effects(raw))


def migrate_legacy_effects(text: str) -> dict[str, Any]:
    """Convert the v1 option (JSON text, segments from 0) to the stored form."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as err:
        raise EffectConfigError(f"not valid JSON: {err}") from err
    return effects_to_raw(parse_effects(raw, base=0))


def parse_segment_spec(text: str | None) -> list[int]:
    """Turn ``"1-3, 7"`` into ``[1, 2, 3, 7]`` (numbers as the user sees them)."""
    if not text or not text.strip():
        return []
    cleaned = re.sub(r"\s*[-\u2013]\s*", "-", text.strip())
    segments: set[int] = set()
    for part in re.split(r"[,;\s]+", cleaned):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not match:
            raise EffectConfigError(f"{part!r} is not a segment number or range")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start > end:
            raise EffectConfigError(f"range {part!r} runs backwards")
        if start < 1 or end > MAX_SEGMENTS:
            raise EffectConfigError(f"segments must be between 1 and {MAX_SEGMENTS}")
        segments.update(range(start, end + 1))
    return sorted(segments)


def format_segment_spec(segments: Iterable[int]) -> str:
    """Return the shortest ``"1-3, 7"`` form of a list of segment numbers."""
    numbers = sorted(set(segments))
    parts: list[str] = []
    index = 0
    while index < len(numbers):
        end = index
        while end + 1 < len(numbers) and numbers[end + 1] == numbers[end] + 1:
            end += 1
        if end > index:
            parts.append(f"{numbers[index]}-{numbers[end]}")
        else:
            parts.append(str(numbers[index]))
        index = end + 1
    return ", ".join(parts)
