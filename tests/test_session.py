import unittest.mock

import pytest
from conftest import AvatarFixtureMixin
from slixmpp import JID, Iq, register_stanza_plugin
from slixmpp.plugins.xep_0060.stanza import EventItem
from slixmpp.plugins.xep_0084 import MetaData

from slidge import BaseGateway, BaseSession, user_store
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    pass


class Session(BaseSession):
    async def login(self):
        return "YUP"


@pytest.mark.usefixtures("avatar")
class TestSession(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()
    xmpp: Gateway

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        self.run_coro(self.xmpp._on_user_register(Iq(sfrom="romeo@montague.lit/gajim")))
        welcome = self.next_sent()
        assert welcome["body"]
        assert "logging in" in self.next_sent()["status"].lower()
        assert "syncing contacts" in self.next_sent()["status"].lower()
        assert "yup" in self.next_sent()["status"].lower()

        self.xmpp["xep_0060"].map_node_event(MetaData.namespace, "avatar_metadata")
        register_stanza_plugin(EventItem, MetaData)

        self.juliet = self.run_coro(
            self.get_romeo_session().contacts.by_legacy_id("juliet")
        )
        self.room = self.run_coro(
            self.get_romeo_session().bookmarks.by_legacy_id("room")
        )

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def test_gateway_receives_presence_probe(self):
        self.recv(  # language=XML
            f"""
            <presence from='romeo@montague.lit/dino'
                      to="{self.xmpp.boundjid.bare}"
                      type="probe" />
            """
        )
        self.send(  # language=XML
            f"""
            <presence to='romeo@montague.lit/dino'
                      from="{self.xmpp.boundjid.bare}">
              <status>YUP</status>
              <show>chat</show>
            </presence>
            """
        )
        assert self.next_sent() is None

    def test_avatar(self):
        with unittest.mock.patch("slidge.BaseSession.on_avatar") as on_avatar:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     type="headline"
                     to="{self.xmpp.boundjid.bare}"
                     id="mid">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}"
                        publisher="test@localhost">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{self.avatar_sha1}"
                            height="5"
                            width="5"
                            type="image/png"
                            bytes="{len(self.avatar_bytes)}" />
                    </metadata>
                  </item>
                </items>
              </event>
            </message>
            """
            )
            self.send(  # language=XML
                f"""
            <iq type="get"
                to="romeo@montague.lit"
                id="1">
              <pubsub xmlns="http://jabber.org/protocol/pubsub">
                <items node="urn:xmpp:avatar:data">
                  <item id="{self.avatar_sha1}" />
                </items>
              </pubsub>
            </iq>
            """
            )
            self.recv(  # language=XML
                f"""
            <iq type="result"
                from="romeo@montague.lit"
                id="1">
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='{self.avatar_sha1}'>
                    <data xmlns='urn:xmpp:avatar:data'>{self.avatar_base64}</data>
                  </item>
                </items>
              </pubsub>
            </iq>
            """
            )
            on_avatar.assert_awaited_with(
                self.avatar_bytes, self.avatar_sha1, "image/png", 5, 5
            )
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     type="headline"
                     to="{self.xmpp.boundjid.bare}"
                     id="mid">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}"
                        publisher="test@localhost">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{self.avatar_sha1}"
                            height="5"
                            width="5"
                            type="image/png"
                            bytes="{len(self.avatar_bytes)}" />
                    </metadata>
                  </item>
                </items>
              </event>
            </message>
            """
            )

    def test_avatar_unpublish(self):
        with unittest.mock.patch("slidge.BaseSession.on_avatar") as on_avatar:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     type="headline"
                     to="{self.xmpp.boundjid.bare}"
                     id="mid">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}"
                        publisher="test@localhost">
                    <metadata xmlns="urn:xmpp:avatar:metadata" />
                  </item>
                </items>
              </event>
            </message>
            """
            )
            on_avatar.assert_awaited_with(None, None, None, None, None)

    def test_user_send_invitation_to_standard_muc(self):
        self.recv(  # language=XML
            f"""
            <message from="romeo@montague.lit"
                     to="juliet@{self.xmpp.boundjid.bare}"
                     id="mid">
              <x xmlns='jabber:x:conference'
                 jid='darkcave@macbeth.shakespeare.lit'
                 password='cauldronburn'
                 reason='Hey Hecate, this is the place for all good witches!' />
            </message>
            """
        )
        msg = self.next_sent()
        assert msg["type"] == "error"
        assert msg["error"]["condition"] == "bad-request"

    def test_user_send_invitation(self):
        with unittest.mock.patch("slidge.BaseSession.on_invitation") as on_invitation:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     to="juliet@{self.xmpp.boundjid.bare}"
                     id="mid">
              <x xmlns='jabber:x:conference'
                 jid='room@{self.xmpp.boundjid.bare}'
                 reason='Hey Hecate, this is the place for all good witches!' />
            </message>
            """
            )
            on_invitation.assert_awaited_once_with(
                self.juliet,
                self.room,
                "Hey Hecate, this is the place for all good witches!",
            )
