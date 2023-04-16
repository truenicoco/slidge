import tempfile
from pathlib import Path

from slidge import global_config
from slidge.util.test import reset_subclasses

global_config.HOME_DIR = Path(tempfile.gettempdir())  # needed to import whatsapp
from slidge.plugins.whatsapp.group import replace_mentions


def test_replace_mentions():
    text = "Hayo @1234, it's cool in here in with @5678!! @123333"

    assert (
        replace_mentions(
            text,
            {"+1234": "bibi", "+5678": "baba"},
        )
        == "Hayo bibi, it's cool in here in with baba!! @123333"
    )

    assert replace_mentions(text, {}) == text

    assert replace_mentions(text, {"+123333": "prout"}) == text.replace(
        "@123333", "prout"
    )

    assert replace_mentions("+1234", {"+1234": "bibi", "+5678": "baba"}) == "+1234"

    assert (
        replace_mentions("@1234@1234@123", {"+1234": "bibi", "+5678": "baba"})
        == "bibibibi@123"
    )


reset_subclasses()
