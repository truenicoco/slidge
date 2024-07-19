from datetime import datetime, timezone

import pytest
from conftest import AvatarFixtureMixin
from slixmpp import JID, Iq

from slidge import BaseGateway, BaseSession, MucType
from slidge.contact import LegacyContact
from slidge.core.session import _sessions
from slidge.group import LegacyBookmarks, LegacyMUC
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"
    GROUPS = True


class Session(BaseSession):
    async def login(self):
        return "YUP"


class Contact(LegacyContact):
    async def update_info(self):
        self.name = "A name"
        self.is_friend = True
        self.online("status msg")
        await self.set_avatar("AVATAR_URL")


class MUC(LegacyMUC):
    async def update_info(self):
        self.name = "Cool name"
        self.description = "Cool description"
        self.type = MucType.CHANNEL_NON_ANONYMOUS
        self.subject = "Cool subject"
        self.subject_setter = await self.get_participant_by_legacy_id("juliet")
        self.subject_date = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.n_participants = 666
        self.user_nick = "Cool nick"
        await self.set_avatar("AVATAR_URL")


class Bookmarks(LegacyBookmarks):
    async def fill(self):
        return


@pytest.mark.usefixtures("avatar")
class TestSession(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()
    xmpp: Gateway

    def setUp(self):
        super().setUp()
        self.db_engine.echo = False
        user = self.xmpp.store.users.new(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        user.preferences = {"sync_avatar": True, "sync_presence": True}
        self.xmpp.store.users.update(user)
        self.run_coro(self.xmpp._on_user_register(Iq(sfrom="romeo@montague.lit/gajim")))
        welcome = self.next_sent()
        assert welcome["body"]
        stanza = self.next_sent()
        assert "logging in" in stanza["status"].lower(), stanza
        stanza = self.next_sent()
        assert "syncing contacts" in stanza["status"].lower(), stanza
        stanza = self.next_sent()
        assert "syncing groups" in stanza["status"].lower(), stanza
        stanza = self.next_sent()
        assert "yup" in stanza["status"].lower(), stanza

        self.send(  # language=XML
            """
            <iq type="get"
                to="romeo@montague.lit"
                id="1"
                from="aim.shakespeare.lit">
              <pubsub xmlns="http://jabber.org/protocol/pubsub">
                <items node="urn:xmpp:avatar:metadata" />
              </pubsub>
            </iq>
            """
        )
        self.db_engine.echo = True

    def tearDown(self):
        super().tearDown()
        _sessions.clear()

    @property
    def romeo_session(self) -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def test_contact_init(self):
        self.run_coro(self.romeo_session.contacts.by_legacy_id("juliet"))
        self.send(  # language=XML
            """
            <presence from="juliet@aim.shakespeare.lit/slidge"
                      to="romeo@montague.lit">
              <c xmlns="http://jabber.org/protocol/caps"
                 node="http://slixmpp.com/ver/1.8.5"
                 hash="sha-1"
                 ver="OErK4nBtx6JV2uK05xyCf47ioT0=" />
              <status>status msg</status>
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <message type="headline"
                     from="juliet@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="http://jabber.org/protocol/nick">
                  <item>
                    <nick xmlns="http://jabber.org/protocol/nick">A name</nick>
                  </item>
                </items>
              </event>
            </message>
            """,
            use_values=False,
        )
        self.send(  # language=XML
            """
            <message type="headline"
                     from="juliet@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="630e98ce280a370dd1c7933289ce7a0338b8b3f1">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="630e98ce280a370dd1c7933289ce7a0338b8b3f1"
                            type="image/png"
                            bytes="470"
                            height="5"
                            width="5" />
                    </metadata>
                  </item>
                </items>
              </event>
            </message>
            """
        )
        assert self.next_sent() is None
        juliet: Contact = self.run_coro(
            self.romeo_session.contacts.by_legacy_id("juliet")
        )
        assert juliet.name == "A name"
        assert juliet.is_friend
        cached_presence = juliet._get_last_presence()
        assert cached_presence is not None
        assert cached_presence.pstatus == "status msg"
        assert juliet.avatar is not None

    def test_group_init(self):
        self.run_coro(self.romeo_session.bookmarks.by_legacy_id("room"))
        self.next_sent()  # juliet presence
        self.next_sent()  # juliet nick
        self.next_sent()  # juliet avatar
        muc = self.run_coro(self.romeo_session.bookmarks.by_legacy_id("room"))
        assert self.next_sent() is None
        # self.run_coro(muc._set)
        assert muc.name == "Cool name"
        assert muc.description == "Cool description"
        assert muc.type == MucType.CHANNEL_NON_ANONYMOUS
        assert muc.n_participants == 666
        assert muc.user_nick == "Cool nick"
        assert muc.avatar is not None
        assert muc.subject == "Cool subject"
        assert muc.subject_date == datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert (
            muc.subject_setter
            == self.run_coro(self.romeo_session.contacts.by_legacy_id("juliet")).name
        )
