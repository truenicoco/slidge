import asyncio
import tempfile
from base64 import b64encode
from pathlib import Path

import pytest
from slixmpp.exceptions import XMPPError
from slixmpp.stanza import Error
from slixmpp.test import SlixTest

from slidge.core.cache import avatar_cache
from slidge.core.pubsub import PubSubComponent
from slidge.util.test import SlixTestPlus


class TestPubSubDisco(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            jid="pubsub.south.park",
            plugins={"pubsub"},
        )
        self.pubsub: PubSubComponent = self.xmpp["pubsub"]
        Error.namespace = "jabber:component:accept"

    def test_disco(self):
        self.recv(  # language=XML
            """
            <iq type='get'
                from='stan@south.park/phone'
                to='pubsub.south.park'
                id='disco'>
              <query xmlns="http://jabber.org/protocol/disco#info" />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="pubsub.south.park"
                to="stan@south.park/phone"
                id="disco">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="account"
                          type="registered" />
                <identity category="pubsub"
                          type="pep" />
                <feature var="http://jabber.org/protocol/shim" />
                <feature var="http://jabber.org/protocol/shim#SubID" />
                <feature var="jabber:x:data" />
                <feature var="http://jabber.org/protocol/caps" />
                <feature var="http://jabber.org/protocol/pubsub#event" />
                <feature var="http://jabber.org/protocol/pubsub#retrieve-items" />
                <feature var="http://jabber.org/protocol/pubsub#persistent-items" />
              </query>
            </iq>
            """
        )


class MockSession:
    logged = True
    ready = asyncio.Future()
    ready.set_result(True)

    @staticmethod
    async def get_contact_or_group_or_participant(j):
        return

    class contacts:
        @staticmethod
        async def by_jid(sto):
            if sto != "stan@pubsub.south.park":
                raise XMPPError("item-not-found")


class TestPubSubNickname(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            jid="pubsub.south.park",
            plugins={"pubsub"},
        )
        self.pubsub: PubSubComponent = self.xmpp["pubsub"]
        self.xmpp.get_session_from_jid = lambda j: MockSession

    def test_new_nick(self):
        self.pubsub.set_nick("stan@pubsub.south.park", "BUBU", "kenny@south.park")
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     type="headline"
                     from="stan@pubsub.south.park"
                     to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="http://jabber.org/protocol/nick">
                  <item>
                    <nick xmlns="http://jabber.org/protocol/nick">BUBU</nick>
                  </item>
                </items>
              </event>
            </message>
            """,
            use_values=False,
        )

    def test_no_nick(self):
        self.pubsub.set_nick("stan@pubsub.south.park", None, "kenny@south.park")
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     type="headline"
                     from="stan@pubsub.south.park"
                     to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="http://jabber.org/protocol/nick">
                  <item>
                    <nick xmlns="http://jabber.org/protocol/nick" />
                  </item>
                </items>
              </event>
            </message>
            """
        )


@pytest.mark.usefixtures("avatar")
class TestPubSubAvatar(SlixTestPlus):
    def setUp(self):
        super().setUp()
        self.stream_start(
            mode="component",
            jid="pubsub.south.park",
            plugins={"pubsub"},
        )
        self.pubsub: PubSubComponent = self.xmpp["pubsub"]
        self.xmpp.get_session_from_jid = lambda j: MockSession
        self.xmpp.get_session_from_stanza = lambda j: MockSession
        self.temp_dir = tempfile.TemporaryDirectory()
        avatar_cache.dir = Path(self.temp_dir.name)

    def advertise_avatar(self):
        # img = Path(__file__).parent.parent / "dev" / "assets" / "5x5.png"
        self.run_coro(
            self.pubsub.set_avatar(
                "stan@pubsub.south.park",
                self.avatar_path,
                "kenny@south.park",
            )
        )
        self.send(  # language=XML
            f"""
            <message xmlns="jabber:component:accept"
                     type="headline"
                     from="stan@pubsub.south.park"
                     to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{self.avatar_sha1}"
                            type="image/png"
                            bytes="{len(self.avatar_bytes)}"
                            height="5"
                            width="5" />
                    </metadata>
                  </item>
                </items>
              </event>
            </message>
            """,
            use_values=False,
        )
        v = b64encode(self.avatar_bytes).decode()
        return v

    def test_advertise_avatar(self):
        self.advertise_avatar()

    def test_single_avatar_retrieval(self):
        v = self.advertise_avatar()
        self.recv(  # language=XML
            f"""
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='{self.avatar_sha1}' />
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='{self.avatar_sha1}'>
                    <data xmlns='urn:xmpp:avatar:data'>{v}</data>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
            use_values=False,
        )

    def test_all_avatars_retrieval(self):
        v = self.advertise_avatar()
        self.recv(  # language=XML
            """
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data' />
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='{self.avatar_sha1}'>
                    <data xmlns='urn:xmpp:avatar:data'>{v}</data>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
            use_values=False,
        )

    def test_unauthorized_retrieval(self):
        self.advertise_avatar()
        self.recv(  # language=XML
            """
            <iq type='get'
                from='kyle@south.park'
                to='stan@pubsub.south.park'
                id='retrieve2'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66' />
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="error"
                from="stan@pubsub.south.park"
                to="kyle@south.park"
                id="retrieve2">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_single_metadata_retrieval(self):
        self.advertise_avatar()
        self.recv(  # language=XML
            f"""
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:metadata'>
                  <item id='{self.avatar_sha1}' />
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{self.avatar_sha1}"
                            type="image/png"
                            bytes="{len(self.avatar_bytes)}"
                            height="5"
                            width="5" />
                    </metadata>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
        )

    def test_all_metadata_retrieval(self):
        self.advertise_avatar()
        self.recv(  # language=XML
            """
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:metadata' />
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="{self.avatar_sha1}"
                            type="image/png"
                            bytes="{len(self.avatar_bytes)}"
                            height="5"
                            width="5" />
                    </metadata>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
        )

    def test_no_avatar(self):
        self.run_coro(
            self.pubsub.set_avatar(
                "stan@pubsub.south.park",
                None,
                "kenny@south.park",
            )
        )
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     type="headline"
                     from="stan@pubsub.south.park"
                     to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item>
                    <metadata xmlns="urn:xmpp:avatar:metadata" />
                  </item>
                </items>
              </event>
            </message>
            """
        )
