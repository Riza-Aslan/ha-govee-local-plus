"""Parsing of the optional light addresses."""

import pytest

from custom_components.govee_local_plus import parse_device_ips


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, []),
        ("", []),
        ("10.0.0.5", ["10.0.0.5"]),
        ("10.0.0.5, 10.0.0.6;10.0.0.5\n10.0.0.7", ["10.0.0.5", "10.0.0.6", "10.0.0.7"]),
    ],
)
def test_parse_ok(text, expected):
    assert parse_device_ips(text) == expected


@pytest.mark.parametrize("text", ["10.0.0", "abc", "10.0.0.256", "::1"])
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        parse_device_ips(text)
