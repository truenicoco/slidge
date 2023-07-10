from slixmpp import JID, Iq

from slidge import BaseGateway, BaseSession, user_store
from slidge.util.test import SlidgeTest


class Gateway(BaseGateway):
    pass


class Session(BaseSession):
    async def login(self):
        return "YUP"


class TestSession(SlidgeTest):
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
        assert self.next_sent() is None

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
