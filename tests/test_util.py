import cryptography.fernet

from slidge.util import SubclassableOnce, ABCSubclassableOnceAtMost, BiDict
from slidge.util.db import EncryptedShelf


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
