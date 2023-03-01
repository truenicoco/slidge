import pytest

from slidge.core.mixins.base import ReactionRecipientMixin


@pytest.mark.asyncio
async def test_no_restriction():
    x = ReactionRecipientMixin()
    assert await x.restricted_emoji_extended_feature() is None


@pytest.mark.asyncio
async def test_single_reaction_any_emoji():
    class X(ReactionRecipientMixin):
        REACTIONS_SINGLE_EMOJI = True

    x = X()
    form = await x.restricted_emoji_extended_feature()
    values = form.get_values()
    assert values["max_reactions_per_user"] == "1"
    assert values.get("allowlist") is None


@pytest.mark.asyncio
async def test_single_emoji():
    class X(ReactionRecipientMixin):
        async def available_emojis(self, legacy_msg_id=None):
            return "♥"

    x = X()
    form = await x.restricted_emoji_extended_feature()
    values = form.get_values()
    assert values.get("max_reactions_per_user") is None
    assert values.get("allowlist") == "♥"


@pytest.mark.asyncio
async def test_two_emojis():
    class X(ReactionRecipientMixin):
        async def available_emojis(self, legacy_msg_id=None):
            return "♥", "😛"

    x = X()
    form = await x.restricted_emoji_extended_feature()
    values = form.get_values()
    assert values.get("max_reactions_per_user") is None
    assert values.get("allowlist") == ["♥", "😛"]


@pytest.mark.asyncio
async def test_two_emojis_single_reaction():
    class X(ReactionRecipientMixin):
        REACTIONS_SINGLE_EMOJI = True

        async def available_emojis(self, legacy_msg_id=None):
            return "♥", "😛"

    x = X()
    form = await x.restricted_emoji_extended_feature()
    values = form.get_values()
    assert values.get("max_reactions_per_user") == "1"
    assert values.get("allowlist") == ["♥", "😛"]
