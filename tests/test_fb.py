import pytest


@pytest.fixture
def facebook():
    import slidge.plugins.facebook
    import slidge

    yield slidge.plugins.facebook.util

    # https://stackoverflow.com/a/14422979/5902284

    slidge.BaseGateway.reset_subclass()
    slidge.BaseSession.reset_subclass()
    slidge.LegacyRoster.reset_subclass()
    slidge.LegacyContact.reset_subclass()


def test_find_closest_timestamp(facebook):
    sent = facebook.Messages()
    sent.add(facebook.FacebookMessage("a", 2))
    sent.add(facebook.FacebookMessage("b", 5))
    sent.add(facebook.FacebookMessage("c", 10))
    sent.add(facebook.FacebookMessage("d", 23))
    sent.add(facebook.FacebookMessage("e", 45))
    m = sent.pop_up_to(15)
    assert m.timestamp_ms == 10
    assert m.mid == "c"
    assert len(sent) == 2
    assert "d" in sent.by_mid
    assert "e" in sent.by_mid

    m = sent.pop_up_to(500)
    assert m.timestamp_ms == 45
    assert m.mid == "e"
    assert len(sent) == 0

    try:
        sent.pop_up_to(5)
    except KeyError as e:
        assert e.args == (5,)

    assert len(sent.by_timestamp_ms) == 0
