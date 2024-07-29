import pytest
import sqlalchemy as sa
from slixmpp import JID

import slidge.db.store
from slidge.db.meta import Base
from slidge.db.models import Avatar, Contact
from slidge.db.store import SlidgeStore


@pytest.fixture
def slidge_store():
    engine = sa.create_engine("sqlite+pysqlite:///:memory:", echo=True)
    Base.metadata.create_all(engine)
    yield SlidgeStore(engine)


def test_user(slidge_store):
    assert slidge.db.store._session is None
    with slidge_store.session() as s1:
        assert slidge.db.store._session is s1
        with slidge_store.session() as s2:
            assert slidge.db.store._session is s2
            assert s1 is s2
            with slidge_store.session() as s3:
                assert slidge.db.store._session is s3
                assert s1 is s2 is s3
        assert slidge.db.store._session is s1
    assert slidge.db.store._session is None


def test_delete_avatar(slidge_store):
    user = slidge_store.users.new(JID("x@x.com"), {})

    with slidge_store.session() as orm:
        avatar = Avatar(
            filename="",
            hash="hash",
            height=0,
            width=0,
        )

        contact = Contact(
            jid=JID("xxx@xxx.com"), legacy_id="prout", user_account_id=user.id
        )
        orm.add(contact)
        orm.commit()
        contact_pk = contact.id
        contact = slidge_store.contacts.get_by_pk(contact_pk)
        contact.avatar = avatar
        orm.add(contact)

        orm.commit()

        avatar_pk = avatar.id

    with slidge_store.session() as orm:
        contact = slidge_store.contacts.get_by_pk(contact_pk)
        assert contact.avatar is not None
        slidge_store.avatars.delete_by_pk(avatar_pk)
        contact = slidge_store.contacts.get_by_pk(contact_pk)
        assert contact.avatar is None
