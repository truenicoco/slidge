from unittest import mock

import pytest
from slixmpp import JID

from slidge.group import LegacyParticipant


@pytest.fixture
def muc():
    muc = mock.MagicMock()
    muc.jid = JID("room@component")
    return muc


def test_unassigned_code_points(muc):
    part = LegacyParticipant(muc, "fiesta! 🎉")
    assert "🎉" not in part.jid.resource


def test_control_chars(muc):
    part = LegacyParticipant(muc, "leet hackk\ber and I have control chars in my nick")
    assert "\b" not in part.jid.resource


def test_control_chars_and_unassigned_code_points(muc):
    part = LegacyParticipant(
        muc,
        "I'm a leet hackk\ber"
        + "🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉" * 10
        + ", I have control chars, emojis in my nick and a ridiculously long nickname",
    )
    assert "\b" not in part.jid.resource
    assert "🎉" not in part.jid.resource
