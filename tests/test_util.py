from slidge.util import SubclassableOnce, ABCSubclassableOnceAtMost


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
