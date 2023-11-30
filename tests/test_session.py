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
            on_avatar.assert_awaited_once()
