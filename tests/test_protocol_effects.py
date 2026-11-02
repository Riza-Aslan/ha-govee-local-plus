"""Packets and effects. Hex values are the ones that worked on Riza's H61E5."""

import json

import pytest

from custom_components.govee_local_plus.catalog import parse_library
from custom_components.govee_local_plus.effects import (
    EffectConfigError,
    Fill,
    SegmentEffect,
    parse_effects,
)
from custom_components.govee_local_plus.protocol import (
    finish_packet,
    segment_color_packet,
    segment_mask,
    xor_checksum,
)

GREEN = (117, 251, 76)
RED = (234, 51, 35)
WARM = (252, 237, 198)

STAIRS = {
    "Signal-Rot": {"fills": [{"color": list(RED), "segments": [10, 11]}]},
    "Signal-Rot-Hell": {
        "background": list(WARM),
        "fills": [{"color": list(RED), "segments": [10, 11]}],
    },
    "Grün-20": {"fills": [{"color": list(GREEN), "segments": [9, 10]}]},
}


def test_checksum_and_padding():
    packet = finish_packet(bytes([0x33, 0x05, 0x04]))
    assert len(packet) == 20
    assert packet[-1] == xor_checksum(packet[:19])
    with pytest.raises(ValueError):
        finish_packet(bytes(20))


def test_segment_mask_bits():
    assert segment_mask([0]) == bytes([1, 0, 0, 0, 0, 0, 0])
    assert segment_mask([8, 11]) == bytes([0, 0b1001, 0, 0, 0, 0, 0])
    with pytest.raises(ValueError):
        segment_mask([56])


def test_gruen_20_matches_the_packets_seen_working():
    effect = parse_effects(json.dumps(STAIRS))["Grün-20"]
    packets = effect.packets(12)
    assert [p.hex() for p in packets] == [
        "330515010000000000000000ff090000000000d4",  # background: black on 0-8, 11
        "3305150175fb4c000000000000060000000000e6",  # green on 9, 10
    ]


def test_signal_rot_hell_paints_background_first():
    packets = parse_effects(json.dumps(STAIRS))["Signal-Rot-Hell"].packets(12)
    assert len(packets) == 2
    assert packets[0][4:7] == bytes(WARM)  # background colour comes first
    assert packets[1][4:7] == bytes(RED)
    assert packets[0][12:19] == segment_mask(range(10))
    assert packets[1][12:19] == segment_mask([10, 11])


def test_every_segment_is_painted_exactly_once():
    for effect in parse_effects(json.dumps(STAIRS)).values():
        painted: list[int] = []
        for packet in effect.packets(12):
            mask = packet[12:19]
            painted += [i for i in range(56) if mask[i // 8] >> (i % 8) & 1]
        assert sorted(painted) == list(range(12)), effect.name


def test_later_fill_wins_over_earlier():
    effect = SegmentEffect(
        "x", fills=(Fill((1, 1, 1), (0, 1)), Fill((2, 2, 2), (1,)))
    )
    by_color = {p[4:7]: p[12:19] for p in effect.packets(3)}
    assert by_color[bytes((1, 1, 1))] == segment_mask([0])
    assert by_color[bytes((2, 2, 2))] == segment_mask([1])


def test_effect_beyond_device_segments_is_rejected():
    effect = parse_effects(json.dumps(STAIRS))["Signal-Rot"]
    with pytest.raises(EffectConfigError):
        effect.packets(10)


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[]",
        '{"a": 1}',
        '{"a": {"fills": [{"color": [1,2], "segments": [0]}]}}',
        '{"a": {"fills": [{"color": [1,2,300], "segments": [0]}]}}',
        '{"a": {"fills": [{"color": [1,2,3], "segments": ["x"]}]}}',
        '{"a": {"background": "red"}}',
    ],
)
def test_invalid_effect_options(text):
    with pytest.raises(EffectConfigError):
        parse_effects(text)


def test_empty_option_means_no_effects():
    assert parse_effects(None) == {}
    assert parse_effects("  ") == {}


def test_parse_library_cleans_names_and_dedupes():
    payload = {
        "data": {
            "categories": [
                {
                    "scenes": [
                        {"sceneName": "Beer\xa0Festival", "lightEffects": [{"sceneCode": 5, "scenceParam": ""}]},
                        {"sceneName": "Aurora", "lightEffects": [{"sceneCode": 1, "scenceParam": "AQID"}]},
                        {"sceneName": "Aurora", "lightEffects": [{"sceneCode": 2, "scenceParam": ""}]},
                        {"sceneName": "Broken", "lightEffects": []},
                    ]
                }
            ]
        }
    }
    scenes = parse_library(payload)
    assert [(s.name, s.code, s.param) for s in scenes] == [
        ("Beer Festival", 5, b""),
        ("Aurora", 1, b"\x01\x02\x03"),
        ("Aurora 2", 2, b""),
    ]


def test_segment_color_packet_clamps():
    assert segment_color_packet((300, -5, 7), [0])[4:7] == bytes((255, 0, 7))
