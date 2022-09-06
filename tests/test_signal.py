from slixmpp import JID, Presence

from aiosignald import exc

import slidge.plugins.signal as plugin
import slidge.plugins.signal.gateway
from slidge.util.test import SlidgeTest
from slidge import *
from aiosignald import generated as sigapi


class MockSignal:
    def __init__(self):
        self.calls = []

    def __getattr__(self, item):
        async def mock_coroutine(*args, **kwargs):
            self.calls.append([item, locals()])
            print(f"{item} has been called with {args} and {kwargs}")

        return mock_coroutine

    @staticmethod
    async def list_contacts(account=None):
        return sigapi.ProfileListv1(profiles=[])


class TestSignalBase(SlidgeTest):
    plugin = plugin

    class Config(SlidgeTest.Config):
        jid = "signal.test"
        user_jid_validator = ".*"

    async def test_roster(self):
        class MockGateway:
            boundjid = JID("test")

            async def set_vcard_avatar(*a, **kw):
                pass

        class MockUser:
            pass

        class MockSession:
            xmpp = MockGateway
            user = MockUser

        phone = "+xxx"
        uuid = "UUID"

        roster = slidge.plugins.signal.Roster(MockSession)
        MockSession.contacts = roster
        c = roster.by_phone(phone)
        assert c.uuid is None
        assert c.phone == phone

        c2 = await roster.by_json_address(sigapi.JsonAddressv1(number=phone, uuid=uuid))

        assert c is c2
        assert c2.uuid == uuid

        c7 = await roster.by_json_address(sigapi.JsonAddressv1(number=phone, uuid=uuid))
        c4 = roster.by_phone(phone)
        c5 = roster.by_legacy_id(phone)
        c6 = await roster.by_uuid(uuid)
        c3 = roster.by_jid(JID(phone + "@" + MockGateway.boundjid.bare))
        assert c3 is c4 is c5 is c6 is c7
        assert c6.signal_address == sigapi.JsonAddressv1(number=phone, uuid=uuid)


class TestSignalUnregistered(TestSignalBase):
    def setUp(self):
        super(TestSignalUnregistered, self).setUp()
        self.signal = slidge.plugins.signal.gateway.signal = MockSignal()
        self.xmpp["xep_0356"].granted_privileges["test"].message = "outgoing"
        self.xmpp["xep_0356"].granted_privileges["test"].roster = "both"

    def test_registration_primary_device_missing_name(self):
        self.recv(
            """
            <iq type='set'
                to="signal.test"
                from='romeo@test'>
                <query xmlns='jabber:iq:register'>
                    <x xmlns="jabber:x:data" type="form">
                        <field var="phone"><value>+123</value></field>
                        <field var="device"><value>primary</value></field>
                    </x>
                </query>
            </iq>
            """
        )
        s = self.next_sent()
        assert s["error"]["condition"] == "not-acceptable"

    def test_registration_primary_device(self):
        self.recv(
            """
            <iq type='set'
                to="signal.test"
                from='romeo@test'
                id="123">
                <query xmlns='jabber:iq:register'>
                    <x xmlns="jabber:x:data" type="form">
                        <field var="phone"><value>+123</value></field>
                        <field var="device"><value>primary</value></field>
                        <field var="name"><value>Romeo</value></field>
                    </x>
                </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type="result" to="romeo@test" from="signal.test" id="123"/>
            """
        )
        # self.send(
        #     """
        #     <presence to="romeo@signal.test" type="subscribe" from="signal.test" />
        #     """
        # )
        assert (
            user_store.get(
                None, None, JID("romeo@test"), None
            ).registration_form["name"]
            == "Romeo"
        )
        user_store.remove(None, None, JID("romeo@test"), None)


class TestSignalFinalizePrimaryDeviceRegistration(TestSignalBase):
    def setUp(self):

        super().setUp()
        jid = JID("romeo@signal.test")
        user_store.add(jid, {"phone": "+123", "device": "primary", "name": "Romeo"})
        self.romeo = user_store.get_by_jid(jid)
        self.signal = slidge.plugins.signal.gateway.signal = MockSignal()

    def tearDown(self):
        user_store.remove(None, None, self.romeo.jid, None)
        slidge.plugins.signal.gateway.signal = None
        super().tearDown()

    def test_register_primary_device(self):
        subscribe_calls = []
        register_calls = []

        async def subscribe(account):
            subscribe_calls.append(account)
            if len(subscribe_calls) == 1:
                raise exc.NoSuchAccountError(payload={})

        async def register(account, captcha=None):
            register_calls.append([account, captcha])
            if captcha is None:
                raise exc.CaptchaRequiredError(payload={})

        self.signal.subscribe = subscribe
        self.signal.register = register
        # login is not triggered by presence available anymore
        # self.recv(
        #     f"""
        #     <presence from='{self.romeo.jid}' to='{self.xmpp.boundjid.bare}' />
        #     """
        # )
        # assert isinstance(self.next_sent(), Presence)  # available
        # assert isinstance(self.next_sent(), Presence)  # connecting
        # assert isinstance(self.next_sent(), Presence)  # registering
        # assert isinstance(self.next_sent(), Presence)  # captcha required
        # assert "captcha" in self.next_sent()["body"]
        # assert subscribe_calls[0] == self.romeo.registration_form["phone"]
        # assert register_calls[0][0] == self.romeo.registration_form["phone"]
        # assert register_calls[0][1] is None
        # assert len(self.signal.calls) == 0
        #
        # self.recv(
        #     f"""
        #     <message from='{self.romeo.jid}' to='{self.xmpp.boundjid.bare}'>
        #         <body>TOKEN</body>
        #     </message>
        #     """
        # )
        # self.next_sent()
        # assert register_calls[1][0] == self.romeo.registration_form["phone"]
        # assert register_calls[1][1] == "TOKEN"
        # self.recv(
        #     f"""
        #     <message from='{self.romeo.jid}' to='{self.xmpp.boundjid.bare}'>
        #         <body>CODE</body>
        #     </message>
        #     """
        # )
        # self.next_sent()
        # assert self.signal.calls[0][0] == "verify"
        # assert (
        #     self.signal.calls[0][1]["kwargs"]["account"]
        #     == self.romeo.registration_form["phone"]
        # )
        # assert self.signal.calls[0][1]["kwargs"]["code"] == "CODE"
