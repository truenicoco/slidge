import pytest

from slixmpp import JID
from slidge.database import User, init_session, RosterBackend


@pytest.fixture(autouse=True)
def init_db():
    init_session("sqlite://", echo=False)


def test_user():
    user = User(jid=jid, legacy_id=legacy_id, legacy_password=legacy_id)
    assert User.by_jid(jid) is None
    user.commit()
    assert User.by_jid(jid) is user
    assert User.by_legacy_id(legacy_id) is user
    assert User.all()[0] is user
    user.delete()
    assert User.by_jid(jid) is None


def test_roster_backend():
    db_state = {}
    db = RosterBackend(owner_jid)
    db.save(owner_jid, jid, item_state, db_state)
    assert isinstance(db_state["id"], int)
    item_state2 = db.load(owner_jid, jid, db_state)
    assert item_state2 == item_state
    item_state2["subscription"] = "from"
    db.save(owner_jid, jid, item_state2, db_state)
    item_state3 = db.load(owner_jid, jid, db_state)
    assert db_state["id"] == 1
    assert item_state3 == item_state2


item_state = {
    "from": True,
    "to": True,
    "pending_in": False,
    "pending_out": False,
    "subscription": "both",
    "whitelisted": True,
    "name": "",
    "groups": [],
    "removed": False
}
owner_jid = JID("owner@titi.com/resource")
jid = JID("prout@prout.fr/resource")
legacy_id = "legacy_prout"
legacy_password = "toto"