import tempfile
from base64 import b64encode
from pathlib import Path

from slixmpp.test import SlixTest

from slidge.core.cache import avatar_cache
from slidge.core.pubsub import PubSubComponent
from slidge.core import config


class TestPubSubDisco(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            jid="pubsub.south.park",
            plugins={"pubsub"},
        )
        self.pubsub: PubSubComponent = self.xmpp["pubsub"]

    def test_disco(self):
        self.recv(
            """
            <iq type='get'
                from='stan@south.park/phone'
                to='pubsub.south.park'
                id='disco'>
               <query xmlns="http://jabber.org/protocol/disco#info" />
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="pubsub.south.park"
                to="stan@south.park/phone"
                id="disco">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="account" type="registered" />
                <identity category="pubsub" type="pep" />
                <feature var="http://jabber.org/protocol/shim" />
                <feature var="http://jabber.org/protocol/shim#SubID" />
                <feature var="jabber:x:data" />
                <feature var="http://jabber.org/protocol/caps" />
                <feature var="http://jabber.org/protocol/pubsub#event" />
                <feature var="http://jabber.org/protocol/pubsub#retrieve-items" />
                <feature var="http://jabber.org/protocol/pubsub#persistent-items" />
            </query></iq>
            """
        )


class MockSession:
    logged = True

    @staticmethod
    async def get_contact_or_group_or_participant(j):
        return


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
        self.send(
            """
            <message xmlns="jabber:component:accept"
                type="headline"
                from="stan@pubsub.south.park"
                to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="http://jabber.org/protocol/nick">
                  <item>
                    <nick xmlns="http://jabber.org/protocol/nick">BUBU</nick>
            </item></items></event></message>
            """
        )

    def test_no_nick(self):
        self.pubsub.set_nick("stan@pubsub.south.park", None, "kenny@south.park")
        self.send(
            """
            <message xmlns="jabber:component:accept"
                type="headline"
                from="stan@pubsub.south.park"
                to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="http://jabber.org/protocol/nick">
                  <item>
                    <nick xmlns="http://jabber.org/protocol/nick" />
            </item></items></event></message>
            """
        )


class TestPubSubAvatar(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            jid="pubsub.south.park",
            plugins={"pubsub"},
        )
        self.pubsub: PubSubComponent = self.xmpp["pubsub"]
        self.xmpp.get_session_from_jid = lambda j: MockSession
        self.temp_dir = tempfile.TemporaryDirectory()
        avatar_cache.dir = Path(self.temp_dir.name)

    def advertise_avatar(self):
        img = Path(__file__).parent.parent / "dev" / "assets" / "5x5.png"
        self.xmpp.loop.run_until_complete(
            self.pubsub.set_avatar(
                "stan@pubsub.south.park",
                img,
                "kenny@south.park",
            )
        )
        self.send(
            """
            <message xmlns="jabber:component:accept"
                type="headline"
                from="stan@pubsub.south.park"
                to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="e6f9170123620949a6821e25ea2861d22b0dff66">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="e6f9170123620949a6821e25ea2861d22b0dff66"
                          type="image/png"
                          bytes="547"
                          height="5" width="5" />
            </metadata></item></items></event></message>
            """,
            use_values=False,
        )
        v = b64encode(img.open("rb").read()).decode()
        return v

    def test_advertise_avatar(self):
        self.advertise_avatar()

    def test_single_avatar_retrieval(self):
        v = self.advertise_avatar()
        self.recv(
            """
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66'/>
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" 
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66'>
                    <data xmlns='urn:xmpp:avatar:data'>
                      {v}
                    </data>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
            use_values=False,
        )

    def test_all_avatars_retrieval(self):
        v = self.advertise_avatar()
        self.recv(
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
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" 
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66'>
                    <data xmlns='urn:xmpp:avatar:data'>
                      {v}
                    </data>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
            use_values=False,
        )

    def test_unauthorized_retrieval(self):
        self.advertise_avatar()
        self.recv(
            """
            <iq type='get'
                from='kyle@south.park'
                to='stan@pubsub.south.park'
                id='retrieve2'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:data'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66'/>
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="error"
                from="stan@pubsub.south.park"
                to="kyle@south.park"
                id="retrieve2">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
            </error></iq>
            """,
            use_values=False,
        )

    def test_single_metadata_retrieval(self):
        self.advertise_avatar()
        self.recv(
            """
            <iq type='get'
                from='kenny@south.park'
                to='stan@pubsub.south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:metadata'>
                  <item id='e6f9170123620949a6821e25ea2861d22b0dff66'/>
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" 
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node="urn:xmpp:avatar:metadata">
                  <item id="e6f9170123620949a6821e25ea2861d22b0dff66">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="e6f9170123620949a6821e25ea2861d22b0dff66"
                          type="image/png"
                          bytes="547"
                          height="5" width="5" />
                    </metadata>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
        )

    def test_all_metadata_retrieval(self):
        self.advertise_avatar()
        self.recv(
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
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" 
                type='result'
                from='stan@pubsub.south.park'
                to='kenny@south.park'
                id='retrieve4'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node="urn:xmpp:avatar:metadata">
                  <item id="e6f9170123620949a6821e25ea2861d22b0dff66">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="e6f9170123620949a6821e25ea2861d22b0dff66"
                          type="image/png"
                          bytes="547"
                          height="5" width="5" />
                    </metadata>
                  </item>
                </items>
              </pubsub>
            </iq>
            """,
        )

    def test_no_avatar(self):
        self.xmpp.loop.run_until_complete(
            self.pubsub.set_avatar(
                "stan@pubsub.south.park",
                None,
                "kenny@south.park",
            )
        )
        self.send(
            """
            <message xmlns="jabber:component:accept"
                type="headline"
                from="stan@pubsub.south.park"
                to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item>
                    <metadata xmlns="urn:xmpp:avatar:metadata" />
            </item></items></event></message>
            """
        )

    def test_avatar_http(self):
        config.HTTP_AVATARS_BASE_URL = "https://something/"
        img = Path(__file__).parent.parent / "dev" / "assets" / "5x5.png"
        self.xmpp.loop.run_until_complete(
            self.pubsub.set_avatar(
                "stan@pubsub.south.park",
                img,
                "kenny@south.park",
            )
        )
        fname = (next(avatar_cache.dir.glob("*.png"))).name
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
                type="headline"
                from="stan@pubsub.south.park"
                to="kenny@south.park">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="e6f9170123620949a6821e25ea2861d22b0dff66">
                    <metadata xmlns="urn:xmpp:avatar:metadata">
                      <info id="e6f9170123620949a6821e25ea2861d22b0dff66"
                          type="image/png"
                          bytes="547"
                          height="5" width="5" 
                          url="https://something/{fname}" />
            </metadata></item></items></event></message>
            """,
            use_values=False,
        )
        config.HTTP_AVATARS_BASE_URL = None