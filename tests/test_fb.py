import pytest


@pytest.fixture
def Messages():
    from slidge.plugins.facebook import Messages

    yield Messages

    del Messages


def test_find_closest_timestamp(Messages):
    contact_id = 123
    sent = Messages()
    sent.add(contact_id, 2)
    sent.add(contact_id, 5)
    sent.add(contact_id, 10)
    sent.add(contact_id, 23)
    sent.add(contact_id, 45)
    t = sent.find_closest(contact_id, 15)
    assert t == 10
    assert tuple(sent._messages[contact_id]) == (23, 45)

    t = sent.find_closest(contact_id, 500)
    assert t == 45
    assert len(sent._messages[contact_id]) == 0

    try:
        sent.find_closest(55, 5)
    except KeyError as e:
        assert e.args == (55, 5)
