import unittest.mock

import pytest
from conftest import AvatarFixtureMixin
from slixmpp import JID, Iq, register_stanza_plugin
from slixmpp.plugins.xep_0060.stanza import EventItem
from slixmpp.plugins.xep_0084 import MetaData

from slidge import BaseGateway, BaseSession, LegacyContact, user_store
from slidge.util.test import SlidgeTest
from slidge.util.types import LinkPreview


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

        self.juliet: LegacyContact = self.run_coro(
            self.get_romeo_session().contacts.by_legacy_id("juliet")
        )
        self.room = self.run_coro(
            self.get_romeo_session().bookmarks.by_legacy_id("room")
        )
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
                from="{self.xmpp.boundjid}"
                id="2">
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
                id="2">
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

    def test_link_preview(self):
        with unittest.mock.patch("slidge.BaseSession.on_text") as on_text:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     to="juliet@{self.xmpp.boundjid.bare}"
                     id="mid">
              <body>I wanted to mention https://the.link.example.com/what-was-linked-to</body>
              <rdf:Description xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                               xmlns:og="https://ogp.me/ns#"
                               rdf:about="https://the.link.example.com/what-was-linked-to">
                <og:title>Page Title</og:title>
                <og:description>Page Description</og:description>
                <og:url>Canonical URL</og:url>
                <og:image>https://link.to.example.com/image.png</og:image>
                <og:site_name>Some Website</og:site_name>
              </rdf:Description>
            </message>
            """
            )
            on_text.assert_awaited_once_with(
                self.juliet,
                "I wanted to mention https://the.link.example.com/what-was-linked-to",
                reply_to_msg_id=None,
                reply_to_fallback_text=None,
                reply_to=None,
                thread=None,
                link_previews=[
                    LinkPreview(
                        about="https://the.link.example.com/what-was-linked-to",
                        title="Page Title",
                        description="Page Description",
                        url="Canonical URL",
                        image="https://link.to.example.com/image.png",
                        type=None,
                        site_name="Some Website",
                    )
                ],
            )

    def test_juliet_sends_link_preview(self):
        self.juliet.send_text(
            "I wanted to mention https://the.link.example.com/what-was-linked-to",
            link_previews=[
                LinkPreview(
                    about="https://the.link.example.com/what-was-linked-to",
                    title="Page Title",
                    description="Page Description",
                    url="Canonical URL",
                    image="https://link.to.example.com/image.png",
                    type=None,
                    site_name="Some Website",
                )
            ],
        )
        self.send(  # language=XML
            """
            <message type="chat"
                     from="juliet@aim.shakespeare.lit/slidge"
                     to="romeo@montague.lit">
              <body>I wanted to mention https://the.link.example.com/what-was-linked-to</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <store xmlns="urn:xmpp:hints" />
              <Description xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                           about="https://the.link.example.com/what-was-linked-to">
                <title xmlns="https://ogp.me/ns#">Page Title</title>
                <description xmlns="https://ogp.me/ns#">Page Description</description>
                <url xmlns="https://ogp.me/ns#">Canonical URL</url>
                <image xmlns="https://ogp.me/ns#">https://link.to.example.com/image.png</image>
                <site_name xmlns="https://ogp.me/ns#">Some Website</site_name>
              </Description>
            </message>
            """
        )
