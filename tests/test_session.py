import unittest.mock

import pytest
from conftest import AvatarFixtureMixin
from slixmpp import JID, register_stanza_plugin
from slixmpp.plugins.xep_0060.stanza import EventItem
from slixmpp.plugins.xep_0084 import MetaData

from slidge import BaseGateway, BaseSession
from slidge.core.session import _sessions
from slidge.util.test import SlidgeTest
from slidge.util.types import LinkPreview


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"


class Session(BaseSession):
    async def login(self):
        return "YUP"


@pytest.mark.usefixtures("avatar")
class TestSession(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()
    xmpp: Gateway

    def setUp(self):
        super().setUp()
        self.setup_logged_session()
        self.xmpp["xep_0060"].map_node_event(MetaData.namespace, "avatar_metadata")
        register_stanza_plugin(EventItem, MetaData)

    def tearDown(self):
        super().tearDown()
        _sessions.clear()

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
              <c xmlns="http://jabber.org/protocol/caps"
                 node="http://slixmpp.com/ver/1.8.5"
                 hash="sha-1"
                 ver="AuL8MdHJviOT17Bh1mfkW7IM7NU=" />
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
            on_invitation.assert_awaited_once()
            assert on_invitation.call_args[0][0].jid == self.juliet.jid
            assert on_invitation.call_args[0][1].jid == self.room.jid
            assert (
                on_invitation.call_args[0][2]
                == "Hey Hecate, this is the place for all good witches!"
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
            on_text.assert_awaited_once()
            args, kwargs = on_text.call_args
            assert args[0].jid == self.juliet.jid
            assert (
                args[1]
                == "I wanted to mention https://the.link.example.com/what-was-linked-to"
            )
            # kwargs = on_text.c
            assert kwargs == dict(
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

    def test_mark_all_messages(self):
        self.xmpp.MARK_ALL_MESSAGES = True
        self.juliet.send_text("whatever", "msg_00")
        self.juliet.send_text("whatever", "msg_01")
        self.juliet.send_text("whatever", "msg_02")
        with unittest.mock.patch(
            "slidge.core.session.BaseSession.on_displayed"
        ) as on_displayed:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit"
                     to="{self.juliet.jid.bare}">
              <displayed xmlns='urn:xmpp:chat-markers:0'
                         id='msg_03' />
            </message>
            """
            )
        assert on_displayed.await_count == 3
        for i in range(3):
            assert on_displayed.call_args_list[i][0][1] == f"msg_0{i}"

    def test_movim_sticker(self):
        sticker_stanza = f"""
            <message from="romeo@montague.lit/movim"
                     to="{self.juliet.jid.bare}">
              <body>Un autocollant a été envoyé via Movim</body>
              <html xmlns="http://jabber.org/protocol/xhtml-im">
                <body xmlns="http://www.w3.org/1999/xhtml">
                  <p>
                    <img src="cid:sha1+4b97ce7f0f06a0e05999f3c719cd5b4f3da992a7@bob.xmpp.org"
                         alt="Sticker" />
                  </p>
                </body>
              </html>
            </message>
            """
        self.recv(sticker_stanza)
        self.send(  # language=XML
            """
            <iq id="2"
                type="get"
                to="romeo@montague.lit/movim">
              <data xmlns="urn:xmpp:bob"
                    cid="sha1+4b97ce7f0f06a0e05999f3c719cd5b4f3da992a7@bob.xmpp.org" />
            </iq>
            """
        )
        with unittest.mock.patch(
            "slidge.core.session.BaseSession.on_sticker"
        ) as on_sticker:
            self.recv(  # language=XML
                f"""
            <iq from='romeo@montague.lit/movim'
                id='2'
                to='{self.xmpp.boundjid.bare}'
                type='result'>
              <data xmlns='urn:xmpp:bob'
                    cid='sha-1+4b97ce7f0f06a0e05999f3c719cd5b4f3da992a7@bob.xmpp.org'
                    max-age='86400'
                    type='image/png'>iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAABGdBTUEAALGPC/xhBQAAAAlwSFlzAAALEwAACxMBAJqcGAAAAAd0SU1FB9YGARc5KB0XV+IAAAAddEVYdENvbW1lbnQAQ3JlYXRlZCB3aXRoIFRoZSBHSU1Q72QlbgAAAF1JREFUGNO9zL0NglAAxPEfdLTs4BZM4DIO4C7OwQg2JoQ9LE1exdlYvBBeZ7jqch9//q1uH4TLzw4d6+ErXMMcXuHWxId3KOETnnXXV6MJpcq2MLaI97CER3N0vr4MkhoXe0rZigAAAABJRU5ErkJggg==</data>
            </iq>
            """
            )
            on_sticker.assert_awaited_once()
            args, kwargs = on_sticker.call_args
            chat, sticker = args
            assert chat.legacy_id == self.juliet.legacy_id
            assert sticker.hashes["sha_1"] == "4b97ce7f0f06a0e05999f3c719cd5b4f3da992a7"
            assert sticker.path.exists()
            assert sticker.content_type == "image/png"
        # this time slidge must have cached the BoBd ata, so no bob-fetching IQ
        with unittest.mock.patch(
            "slidge.core.session.BaseSession.on_sticker"
        ) as on_sticker:
            self.recv(sticker_stanza)
            on_sticker.assert_awaited_once()
            args, kwargs = on_sticker.call_args
            chat, sticker = args
            assert chat.legacy_id == self.juliet.legacy_id
            assert sticker.hashes["sha_1"] == "4b97ce7f0f06a0e05999f3c719cd5b4f3da992a7"
            assert sticker.path.exists()
        assert self.next_sent() is None

    def test_movim_custom_emoji(self):
        with unittest.mock.patch("slidge.core.session.BaseSession.on_text") as on_text:
            self.recv(  # language=XML
                f"""
            <message from="romeo@montague.lit/movim"
                     to="{self.juliet.jid.bare}">
              <body>fdsf :amogus:</body>
              <html xmlns="http://jabber.org/protocol/xhtml-im">
                <body xmlns="http://www.w3.org/1999/xhtml">
                  <p>fdsf
                  <img src="cid:sha-256+583ca9a99f6cd8454c24d81a43d913a98dd80f282ce5c8f0f8ede418990134af@bob.xmpp.org"
                       alt=":amogus:" /></p>
                </body>
              </html>
            </message>
            """
            )
        on_text.assert_awaited_once()
        args, kwargs = on_text.call_args
        assert args[1] == "fdsf :amogus:"

    def test_carbon_retract(self):
        with (
            unittest.mock.patch(
                "slidge.core.session.BaseSession.on_retract"
            ) as on_retract,
            unittest.mock.patch(
                "slidge.core.session.BaseSession.on_correct"
            ) as on_correct,
        ):
            self.juliet.retract("some-id", carbon=True)
            self.recv(  # language=XML
                f"""
            <message type="chat"
                     to="{self.juliet.jid.bare}"
                     from="romeo@montague.lit/movim"
                     id="slidge-carbon-whatever">
              <body>/me retracted the message 1269564719166132224</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <store xmlns="urn:xmpp:hints" />
              <fallback xmlns="urn:xmpp:fallback:0"
                        for="urn:xmpp:message-retract:1" />
              <retract xmlns="urn:xmpp:message-retract:1"
                       id="some-id" />
              <replace xmlns="urn:xmpp:message-correct:0"
                       id="some-id" />
            </message>
            """
            )
            on_correct.assert_not_awaited()
            on_retract.assert_not_awaited()

    def test_new_thread_from_xmpp(self):
        with (
            unittest.mock.patch("slidge.core.session.BaseSession.on_text") as on_text,
            unittest.mock.patch(
                "slidge.contact.contact.LegacyContact.create_thread",
                return_value="legacy-thread-id",
            ),
        ):
            self.recv(  # language=XML
                f"""
            <message type="chat"
                     to="{self.juliet.jid.bare}"
                     from="romeo@montague.lit/movim"
                     id="xmpp-msg-id">
              <body>I start a new thread</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <thread>xmpp-thread-id</thread>
            </message>
            """
            )
            on_text.assert_awaited_once()
            args, kwargs = on_text.call_args
            assert kwargs["thread"] == "legacy-thread-id"
        with unittest.mock.patch("slidge.core.session.BaseSession.on_text") as on_text:
            self.recv(  # language=XML
                f"""
            <message type="chat"
                     to="{self.juliet.jid.bare}"
                     from="romeo@montague.lit/movim"
                     id="xmpp-msg-id-2">
              <body>I send a new message in the new thread</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <thread>xmpp-thread-id</thread>
            </message>
            """
            )
            on_text.assert_awaited_once()
            args, kwargs = on_text.call_args
            assert kwargs["thread"] == "legacy-thread-id"
