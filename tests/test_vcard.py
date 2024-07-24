import pytest
from conftest import AvatarFixtureMixin

from slidge import BaseGateway, BaseSession
from slidge.contact import LegacyContact
from slidge.core.session import _sessions
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    COMPONENT_NAME = "A test"


class Session(BaseSession):
    async def login(self):
        return "YUP"


class Contact(LegacyContact):
    async def update_info(self):
        if self.legacy_id == "has-vcard":
            self.set_vcard(full_name="A full name")

    async def fetch_vcard(self):
        if self.legacy_id == "on-demand":
            self.set_vcard(full_name="Lazy")


@pytest.mark.usefixtures("avatar")
class TestSession(AvatarFixtureMixin, SlidgeTest):
    plugin = globals()
    xmpp: Gateway

    def setUp(self):
        super().setUp()
        self.setup_logged_session()

    def tearDown(self):
        super().tearDown()
        _sessions.clear()

    def _assert_broadcast_on_demand(self):
        self.send(  # language=XML
            """
            <message type="headline"
                     from="on-demand@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:vcard4">
                  <item id="current"
                        node="urn:xmpp:vcard4">
                    <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
                      <impp>
                        <uri>xmpp:on-demand@aim.shakespeare.lit</uri>
                      </impp>
                      <fn>
                        <text>Lazy</text>
                      </fn>
                    </vcard>
                  </item>
                </items>
              </event>
            </message>
            """
        )

    def test_vcard_in_update_info(self):
        self.run_coro(self.romeo.contacts.by_legacy_id("has-vcard"))
        self.send(  # language=XML
            """
            <message type="headline"
                     from="has-vcard@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:vcard4">
                  <item id="current"
                        node="urn:xmpp:vcard4">
                    <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
                      <impp>
                        <uri>xmpp:has-vcard@aim.shakespeare.lit</uri>
                      </impp>
                      <fn>
                        <text>A full name</text>
                      </fn>
                    </vcard>
                  </item>
                </items>
              </event>
            </message>
            """
        )
        assert self.next_sent() is None

    def test_vcard_outside_update_info(self):
        self.juliet.set_vcard("Another full name")
        self.send(  # language=XML
            """
            <message type="headline"
                     from="juliet@aim.shakespeare.lit"
                     to="romeo@montague.lit">
              <event xmlns="http://jabber.org/protocol/pubsub#event">
                <items node="urn:xmpp:vcard4">
                  <item id="current"
                        node="urn:xmpp:vcard4">
                    <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
                      <impp>
                        <uri>xmpp:juliet@aim.shakespeare.lit</uri>
                      </impp>
                      <fn>
                        <text>Another full name</text>
                      </fn>
                    </vcard>
                  </item>
                </items>
              </event>
            </message>
            """
        )
        assert self.next_sent() is None

    def test_fetch_raw_iq(self):
        self.run_coro(self.romeo.contacts.by_legacy_id("on-demand"))
        assert self.next_sent() is None
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit'
                id='fetch'
                to='on-demand@aim.shakespeare.lit'
                type='get'>
              <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="on-demand@aim.shakespeare.lit"
                id="fetch"
                to="romeo@montague.lit"
                type="result">
              <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
                <impp>
                  <uri>xmpp:on-demand@aim.shakespeare.lit</uri>
                </impp>
                <fn>
                  <text>Lazy</text>
                </fn>
              </vcard>
            </iq>
            """,
            use_values=False,
        )
        self._assert_broadcast_on_demand()
        assert self.next_sent() is None

    def test_fetch_raw_iq_empty(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit'
                id='fetch'
                to='juliet@aim.shakespeare.lit'
                type='get'>
              <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="juliet@aim.shakespeare.lit"
                id="fetch"
                to="romeo@montague.lit"
                type="result">
              <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0" />
            </iq>
            """,
            use_values=False,
        )
        assert self.next_sent() is None

    def test_fetch_pubsub(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit'
                id='fetch'
                to='on-demand@aim.shakespeare.lit'
                type='get'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:vcard4' />
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="on-demand@aim.shakespeare.lit"
                id="fetch"
                to="romeo@montague.lit"
                type="result">
              <pubsub xmlns="http://jabber.org/protocol/pubsub">
                <items node="urn:xmpp:vcard4">
                  <item id="current">
                    <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
                      <impp>
                        <uri>xmpp:on-demand@aim.shakespeare.lit</uri>
                      </impp>
                      <fn>
                        <text>Lazy</text>
                      </fn>
                    </vcard>
                  </item>
                </items>
              </pubsub>
            </iq>
            """
        )
        self._assert_broadcast_on_demand()
        assert self.next_sent() is None

    def test_fetch_pubsub_empty(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit'
                id='fetch'
                to='juliet@aim.shakespeare.lit'
                type='get'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:vcard4' />
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="juliet@aim.shakespeare.lit"
                id="fetch"
                to="romeo@montague.lit"
                type="result">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """
        )
        assert self.next_sent() is None
