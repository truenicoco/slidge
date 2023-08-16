from datetime import datetime, timedelta

import cryptography.fernet
from slixmpp import JID

from slidge.core import config
from slidge.core.contact import LegacyContact
from slidge.util import (
    ABCSubclassableOnceAtMost,
    BiDict,
    SubclassableOnce,
    is_valid_phone_number,
)
from slidge.util.db import EncryptedShelf
from slidge.util.sql import SQLBiDict


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


def test_bidict():
    d: BiDict[int, str] = BiDict()
    d[1] = "a"
    d[2] = "b"

    assert d.inverse["a"] == 1
    assert d.inverse["b"] == 2
    assert 1 in d
    assert 2 in d

    d[2] = "c"
    assert d[2] == "c"
    assert d.inverse["c"] == 2
    assert "b" not in d
    assert len(d.inverse.values()) == 2


def test_bidict_sql(user):
    d = SQLBiDict("test", "key1", "key2", user, create_table=True)
    d[1] = "a"
    d[2] = "b"

    assert d.inverse.get("a") == 1
    assert d.inverse.get("b") == 2
    assert 1 in d
    assert 2 in d

    d[2] = "c"
    assert d[2] == "c"
    assert d.inverse.get("c") == 2
    assert "b" not in d

    user.bare_jid = "test2@test.fr"
    d2 = SQLBiDict("test", "key1", "key2", user)
    assert d2.get(1) is None
    assert d2.inverse.get("a") is None


def test_encrypted_shelf(tmp_path):
    key = "test_key"
    s = EncryptedShelf(tmp_path / "test.db", key)
    s["x"] = 123
    s["y"] = 777
    s.close()

    s = EncryptedShelf(tmp_path / "test.db", key)
    assert s["x"] == 123
    assert s["y"] == 777.0
    s.close()

    s = EncryptedShelf(tmp_path / "test.db", "WRONG_KEY")
    try:
        s["x"]
    except Exception as e:
        assert isinstance(e, cryptography.fernet.InvalidToken), e
    else:
        assert False


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
