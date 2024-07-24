import re
from datetime import datetime, timedelta

import cryptography.fernet
from slixmpp import JID

from slidge.contact import LegacyContact
from slidge.core import config
from slidge.util import (
    ABCSubclassableOnceAtMost,
    SubclassableOnce,
    is_valid_phone_number,
)
from slidge.util.types import Mention
from slidge.util.util import merge_resources, replace_mentions


def test_subclass():
    SubclassableOnce.TEST_MODE = False
    # fmt: off
    class A(metaclass=SubclassableOnce): pass
    assert A.get_self_or_unique_subclass() is A

    class B(A): pass
    assert A.get_self_or_unique_subclass() is B

    try:
        class C(A): pass
    except RuntimeError:
        pass
    else:
        raise AssertionError("RuntimeError should have been raised")

    A.reset_subclass()

    class C(A): pass
    assert A.get_self_or_unique_subclass() is C

    A.reset_subclass()
    class D(metaclass=ABCSubclassableOnceAtMost): pass

    # fmt: on
    SubclassableOnce.TEST_MODE = True


def test_phone_validation():
    assert is_valid_phone_number("+33")
    assert not is_valid_phone_number("+")
    assert not is_valid_phone_number("+asdfsadfa48919sadf")
    assert not is_valid_phone_number("12597891")


def test_strip_delay(monkeypatch):
    monkeypatch.setattr(config, "IGNORE_DELAY_THRESHOLD", timedelta(seconds=300))

    class MockDelay:
        @staticmethod
        def set_stamp(x):
            pass

        @staticmethod
        def set_from(x):
            pass

    class MockC:
        STRIP_SHORT_DELAY = True

        class xmpp:
            boundjid = JID("test")

    class MockMsg:
        delay_added = None

        def __getitem__(self, key):
            if key == "delay":
                self.delay_added = True
            return MockDelay

    msg = MockMsg()
    LegacyContact._add_delay(MockC, msg, datetime.now())
    assert not msg.delay_added

    monkeypatch.setattr(config, "IGNORE_DELAY_THRESHOLD", timedelta(seconds=0))

    msg = MockMsg()
    LegacyContact._add_delay(MockC, msg, datetime.now())
    assert msg.delay_added


def test_merge_presence():
    assert merge_resources(
        {
            "1": {
                "show": "",
                "status": "",
                "priority": 0,
            }
        }
    ) == {
        "show": "",
        "status": "",
        "priority": 0,
    }

    assert merge_resources(
        {
            "1": {
                "show": "dnd",
                "status": "X",
                "priority": -10,
            },
            "2": {
                "show": "dnd",
                "status": "",
                "priority": 0,
            },
        }
    ) == {
        "show": "dnd",
        "status": "X",
        "priority": 0,
    }

    assert merge_resources(
        {
            "1": {
                "show": "",
                "status": "",
                "priority": 0,
            },
            "2": {
                "show": "away",
                "status": "",
                "priority": 0,
            },
            "3": {
                "show": "dnd",
                "status": "",
                "priority": 0,
            },
        }
    ) == {
        "show": "",
        "status": "",
        "priority": 0,
    }

    assert merge_resources(
        {
            "1": {
                "show": "",
                "status": "",
                "priority": 0,
            },
            "2": {
                "show": "away",
                "status": "",
                "priority": 0,
            },
            "3": {
                "show": "dnd",
                "status": "Blah blah",
                "priority": 0,
            },
        }
    ) == {
        "show": "",
        "status": "Blah blah",
        "priority": 0,
    }

    assert merge_resources(
        {
            "1": {
                "show": "",
                "status": "",
                "priority": 0,
            },
            "2": {
                "show": "away",
                "status": "Blah",
                "priority": 0,
            },
            "3": {
                "show": "dnd",
                "status": "Blah blah",
                "priority": 10,
            },
        }
    ) == {
        "show": "",
        "status": "Blah blah",
        "priority": 0,
    }

    assert merge_resources(
        {
            "1": {
                "show": "",
                "status": "",
                "priority": 0,
            },
            "2": {
                "show": "away",
                "status": "Blah",
                "priority": 0,
            },
            "3": {
                "show": "dnd",
                "status": "",
                "priority": 10,
            },
        }
    ) == {
        "show": "",
        "status": "Blah",
        "priority": 0,
    }


def test_replace_mentions():
    mentions = []
    text = "Text Mention 1 and Mention 2, and Mention 3"
    for match in re.finditer("Mention 1|Mention 2|Mention 3", text):
        span = match.span()
        nick = match.group()
        mentions.append(
            Mention(contact=f"Contact{nick[-1]}", start=span[0], end=span[1])
        )
    assert (
        replace_mentions(text, mentions, lambda c: "@" + c[-1])
        == "Text @1 and @2, and @3"
    )

    mentions = []
    text = "Text Mention 1 and Mention 2, and Mention 3 blabla"
    for match in re.finditer("Mention 1|Mention 2|Mention 3", text):
        span = match.span()
        nick = match.group()
        mentions.append(
            Mention(contact=f"Contact{nick[-1]}", start=span[0], end=span[1])
        )
    assert (
        replace_mentions(text, mentions, lambda c: "@" + c[-1])
        == "Text @1 and @2, and @3 blabla"
    )
