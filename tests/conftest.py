"""Shared fixtures."""

import pytest

from .test_light import FakeController, FakeDevice


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ in every test."""
    return


@pytest.fixture
def device() -> FakeDevice:
    """A fresh fake light that the fake controller reports."""
    FakeController.device = FakeDevice()
    return FakeController.device
