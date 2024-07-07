import pytest
from slixmpp import JID
from sqlalchemy import create_engine

from slidge.db.meta import Base
from slidge.db.store import UserStore


@pytest.fixture
def store():
    engine = create_engine("sqlite+pysqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    yield UserStore(engine)


def test_user(store: UserStore):
    user1 = store.new(JID("test-user@test-host"), {})

    user1.preferences = {"section": {"do_xxx": True}}
    assert user1.jid == JID("test-user@test-host")
    store.update(user1)

    del user1
    user2 = store.get(JID("test-user@test-host"))
    assert user2.preferences == {"section": {"do_xxx": True}}

    user2.preferences["section"]["do_xxx"] = False
    store.update(user2)
    del user2

    user3 = store.get(JID("test-user@test-host"))
    assert not user3.preferences["section"]["do_xxx"]
