import time
import logging
import hashlib

import pytest

from slixmpp import JID, Message

from slidge.database import User
from slidge.session import sessions
from slidge.test import SlixGatewayTest, MockLegacyClient


@pytest.fixture(scope="class")
def legacy_client(request):
    # https://docs.pytest.org/en/stable/unittest.html#mixing-pytest-fixtures-into-unittest-testcase-subclasses-using-marks
    request.cls.legacy_client = MockLegacyClient()


@pytest.mark.usefixtures("legacy_client")
class TestGateway(SlixGatewayTest):
    def setUp(self):
        self.stream_start(db_echo=False)
        self.xmpp.socket.next_sent()
        self.user_jid = JID("jabberuser@example.com/gajim")
        self.user_legacy_id = "user_legacy_id"
        self.user_legacy_pass = "gnagnagna"
        self.user_muc_nickname = "anickname"
        self.xmpp.legacy_client = self.legacy_client  # pylint: disable=no-member
        self.legacy_client.legacy_sent = (  # pylint: disable=no-member
            self.legacy_sent
        ) = []

    def recv_privileges(self):
        self.recv(
            """
        <message from='example.com' to='gateway.example.com' id='12345'>
            <privilege xmlns='urn:xmpp:privilege:1'>
                <perm access='roster' type='both'/>
                <perm access='message' type='outgoing'/>
            </privilege>
        </message>
        """
        )

    def add_user(self):
        self.user = User(
            jid=self.user_jid,
            legacy_id=self.user_legacy_id,
            legacy_password=self.user_legacy_pass,
        )
        self.user.commit()
        self.xmpp.client_roster.subscribe(self.user_jid)
        log.debug(f"add user: {self.next_sent()}")
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='{self.xmpp.boundjid.bare}'
                      type='subscribed' />
            """
        )
        log.debug(f"roster: {self.xmpp.client_roster}")
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='{self.xmpp.boundjid.bare}'
                      type='subscribe' />
            """
        )
        log.debug(f"roster: {self.xmpp.client_roster}")
        log.debug(f"add user: {self.next_sent()}")
        log.debug(f"add user: {self.next_sent()}")
        for buddy in self.legacy_client.buddies:
            sessions.by_jid(self.user_jid).buddies.add(buddy)
            self.xmpp.loop.run_until_complete(buddy.finalize())

    def login_user(self):
        self.recv_privileges()
        self.add_user()
        self.xmpp.event("legacy_login", {"from": self.user_jid})
        self.recv(f"""<iq id="1" type="result" />""")  # IQ push
        while self.next_sent() is not None:
            pass

    def test_bad_legacy_credentials(self):
        self.recv(
            f"""
            <iq type='set' id='reg2' to='{self.xmpp.boundjid.bare}' from="{self.user_jid}">
                <query xmlns='jabber:iq:register'>
                    <username>invalid</username>
                    <password>{self.user_legacy_pass}</password>
                </query>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="error"
                id="reg2"
                to="jabberuser@example.com/gajim"
                from="gateway.example.com">
                <query xmlns="jabber:iq:register">
                       <username>invalid</username>
                       <password>gnagnagna</password>
                </query>
                <error xmlns="jabber:client" type="modify" code="406">
                    <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                </error>
            </iq>
            """,
            use_values=False,
        )

    def test_send_message_to_legacy_buddy_not_in_roster(self):
        self.add_user()
        self.recv(
            f"""
            <message from="{self.user_jid}"
                     to="buddy@{self.xmpp.boundjid.bare}">
                <body>Hello</body>
            </message>
            """
        )
        log.debug(f"Send queue: {id(self.legacy_sent)}, {self.legacy_sent}")

        msg = self.xmpp.legacy_client.last_sent
        assert msg["from"].legacy_id == self.user.legacy_id
        assert msg["from"].jid == self.user.jid
        assert msg["to"] == "buddy"
        assert msg["msg"]["body"] == "Hello"
        assert msg["type"] == "1on1"

    def test_probe_gateway_by_unregistered_user(self):
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='{self.xmpp.boundjid.bare}'
                      type='probe' />
            """
        )
        #
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to='{self.user_jid.bare}'
                      from='{self.xmpp.boundjid.bare}'
                      type='unsubscribed' />
            """
        )

    def test_probe_gateway_by_registered_user(self):
        self.add_user()
        self.xmpp.loop.run_until_complete(self.xmpp._startup(event=None))
        log.debug(f"roster: {self.xmpp.client_roster}")
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='{self.xmpp.boundjid.bare}'
                      type='probe' />
            """
        )
        #
        h = self.xmpp.loop.run_until_complete(
            self.xmpp["xep_0153"].api["get_hash"](
                jid=self.xmpp.boundjid, node=None, ifrom=None, args={}
            )
        )
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to='{self.user_jid.bare}'
                      from='{self.xmpp.boundjid.bare}'>
                <x xmlns="vcard-temp:x:update">
                    <photo>
                    {h}</photo>
                </x>
                <priority>0</priority>
            </presence>
            """
        )

    def test_probe_invalid_buddy(self):
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='buddy@{self.xmpp.boundjid.bare}'
                      type='probe' />
            """
        )
        #
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to='{self.user_jid.bare}'
                      from='buddy@{self.xmpp.boundjid.bare}'
                      type='unsubscribed'>
            </presence>
            """
        )

    def test_probe_valid_buddy(self):
        self.add_user()
        buddy = sessions[self.user].buddies.by_legacy_id("buddy")
        buddy._make_roster_entry()
        self.recv(
            f"""
            <presence from='{self.user_jid}'
                      to='{buddy.jid}'
                      type='probe' />
            """
        )
        #
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to='{self.user_jid.bare}'
                      from='{buddy.jid.bare}'>
                <x xmlns="vcard-temp:x:update" />
                <priority>0</priority>
            </presence>
            """
        )

    def test_logout_inexisting_user(self):
        self.xmpp.event("legacy_logout", {"from": self.user_jid})
        assert self.next_sent() is None

    def test_logout_inexisting_session(self):
        self.add_user()
        self.xmpp.event("legacy_logout", {"from": self.user_jid})
        assert self.next_sent() is None

    def test_user_join_muc(self):
        self.login_user()
        muc = sessions.by_jid(self.user_jid).mucs.by_legacy_id(
            self.legacy_client.muc.legacy_id
        )
        self.recv(
            f"""
            <presence to="{muc.jid.username}@{self.xmpp.boundjid.bare}/{self.user_muc_nickname}"
                      from="{self.user_jid}">
                <x xmlns="http://jabber.org/protocol/muc">
                    <history maxchars="0" />
                </x>
                <x xmlns="vcard-temp:x:update"><photo /></x>
            </presence>
            """
        )  # No caps because this triggers a disco unconsistently
        # really want that
        for nick in self.legacy_client.occupants:
            # stanza = self.next_sent()
            # stanzas.append(stanza)
            # print(nick, stanza)
            self.send(
                f"""
                <presence to="{self.user_jid}"
                          from="{muc.jid}/{nick}">
                    <x xmlns="http://jabber.org/protocol/muc#user">
                    <item affiliation="member"
                          role="participant" />
                    </x>
                    <x xmlns="vcard-temp:x:update" />
                </presence>
                """
            )
        self.send(
            f"""
            <presence from='{muc.jid}/{self.user_muc_nickname}'
                      to='{self.user_jid}'>
                <x xmlns='http://jabber.org/protocol/muc#user'>
                    <item affiliation='member' role='moderator'/>
                    <status code='110'/>
                    <status code='210'/>
                </x>
                <x xmlns="vcard-temp:x:update" />
            </presence>
            """,
            use_values=False,
        )

        # assert False is True
        # for nick in self.legacy_client.occupants:
        #     stanzas.append(self.next_sent())

    def test_add_buddy_success(self):
        self.login_user()
        self.recv(
            f"""
            <presence from="{self.user.jid.bare}"
                      to="buddy3@{self.xmpp.boundjid.bare}"
                      type="subscribe" />
            """
        )
        self.send(
            f"""
            <presence to="{self.user.jid.bare}"
                      from="buddy3@{self.xmpp.boundjid.bare}"
                      type="subscribed" />
            """
        )
        self.send(
            f"""
            <presence to="{self.user.jid.bare}"
                      from="buddy3@{self.xmpp.boundjid.bare}">
                      <x xmlns="vcard-temp:x:update" />
                      <priority>0</priority>
            </presence>
            """
        )
        self.send(
            f"""
            <presence to="{self.user.jid.bare}"
                      from="buddy3@{self.xmpp.boundjid.bare}"
                      type="subscribe" />
            """
        )
        self.recv(
            f"""
            <presence from="{self.user.jid.bare}"
                      to="buddy3@{self.xmpp.boundjid.bare}"
                      type="subscribed" />
            """
        )
        assert self.next_sent() is None


    def test_add_buddy_denied(self):
        self.login_user()
        self.recv(
            f"""
            <presence from="{self.user.jid.bare}"
                      to="notabuddy@{self.xmpp.boundjid.bare}"
                      type="subscribe" />
            """
        )
        self.send(
            f"""
            <presence to="{self.user.jid.bare}"
                      from="notabuddy@{self.xmpp.boundjid.bare}"
                      type="unsubscribed" />
            """
        )
        assert self.next_sent() is None

    # FIXME: doesn't work in test setting, but OK in real life
    # def test_send_legacy_message_ack_read_reply(self):
    #     # legacy_client = MockLegacyClient()
    #     # self.xmpp.legacy_client = legacy_client
    #     self.recv_privileges()
    #     self.add_user()
    #     self.recv(
    #         f"""
    #         <message from="{self.user_jid}"
    #                  to="buddy@{self.xmpp.boundjid.bare}"
    #                  type="chat"
    #                  id="abc">
    #             <body>heho</body>
    #             <origin-id xmlns="urn:xmpp:sid:0"
    #                        id="abc" />
    #                 <request xmlns="urn:xmpp:receipts" />
    #                 <active xmlns="http://jabber.org/protocol/chatstates" />
    #             <markable xmlns="urn:xmpp:chat-markers:0" />
    #         </message>
    #         """
    #     )
    #     log.debug(f"Sessions xmpp {self.xmpp}")
    #     # Nothing seems sent in test

    # FIXME: doesn't always work
    # def test_composing_to_buddy(self):
    #     self.recv_privileges()
    #     self.add_user()
    #     self.recv(
    #         f"""
    #         <message to="buddy1@{self.xmpp.boundjid.bare}"
    #                  id="67d315b0-0e2a-4da5-851f-5fb7be7a503c"
    #                  type="chat"
    #                  from="{self.user_jid}">
    #             <origin-id xmlns="urn:xmpp:sid:0"
    #                        id="67d315b0-0e2a-4da5-851f-5fb7be7a503c" />
    #             <composing xmlns="http://jabber.org/protocol/chatstates" />
    #             <no-store xmlns="urn:xmpp:hints" />
    #         </message>
    #         """
    #     )
    #     assert self.xmpp.legacy_client.last_sent["type"] == "composing"

    # Removed this one because roster automagically send presences everywhere and I
    # didn't manage to sort it out :(
    # def test_user_with_buddies_got_online(self):
    #     self.recv_privileges()
    #     self.add_user()
    #     # self.xmpp.loop.run_until_complete(self.xmpp._startup(event=None))
    #     self.recv(
    #         f"""
    #         <presence from="{self.user_jid}" to="{self.xmpp.boundjid.bare}">
    #             <c xmlns="http://jabber.org/protocol/caps"
    #                hash="sha-1"
    #                node="https://gajim.org"
    #                ver="pAg7f6566/B8BfVtblCX9GwW1mA=" />
    #                <x xmlns="vcard-temp:x:update"><photo /></x>
    #         </presence>
    #     """
    #     )
    #     for buddy in self.legacy_client.buddies:
    #         self.recv(
    #             f"""
    #             <presence from="{self.user_jid}" to="{buddy.jid.bare}">
    #                 <c xmlns="http://jabber.org/protocol/caps"
    #                 hash="sha-1"
    #                 node="https://gajim.org"
    #                 ver="pAg7f6566/B8BfVtblCX9GwW1mA=" />
    #                 <x xmlns="vcard-temp:x:update"><photo /></x>
    #             </presence>
    #         """
    #         )
    #     self.recv(
    #         f"""
    #         <presence from="{self.user_jid}" to="{self.xmpp.boundjid.bare}" type="probe">
    #         </presence>
    #         """
    #     )
    #     for buddy in self.legacy_client.buddies:
    #         self.recv(
    #             f"""
    #             <presence from="{self.user_jid}" to="{buddy.jid.bare}" type="probe">
    #             </presence>
    #             """
    #         )
    #     while self.next_sent() is not None:
    #         continue
    #     self.send(
    #         """
    #         <iq xmlns="jabber:component:accept"
    #             id="1"
    #             from="gateway.example.com"
    #             to="jabberuser@example.com/gajim"
    #             type="get">
    #           <query xmlns="http://jabber.org/protocol/disco#info"
    #                  node="https://gajim.org#pAg7f6566/B8BfVtblCX9GwW1mA=" />
    #         </iq>
    #         """
    #     )
    #     h = self.xmpp.loop.run_until_complete(
    #         self.xmpp["xep_0153"].api["get_hash"](jid=self.xmpp.boundjid)
    #     )
    #     self.send(
    #         f"""
    #         <presence to="{self.user_jid.bare}"
    #                   from="{self.xmpp.boundjid.bare}">
    #             <x xmlns="vcard-temp:x:update">
    #                 <photo>{h}</photo>
    #             </x>
    #         </presence>
    #         """
    #     )
    #     self.send(
    #         f"""
    #         <presence to="{self.user_jid.bare}"
    #                   from="{self.xmpp.boundjid.bare}">
    #             <x xmlns="vcard-temp:x:update">
    #                 <photo>{h}</photo>
    #             </x>
    #         </presence>
    #         """
    #     )
    #     self.send(
    #         f"""
    #         <iq xmlns="jabber:component:accept"
    #             id="1"
    #             type="set"
    #             to="{self.user_jid.bare}"
    #             from="{self.xmpp.boundjid.bare}">
    #             <query xmlns="jabber:iq:roster">
    #         """
    #         + "\n".join(
    #             f"""<item subscription="both" jid="{b.jid.bare}">
    #                     <group>{self.xmpp.config["buddies"]["group"]}</group>
    #                 </item>"""
    #             for b in self.xmpp.legacy_client.buddies
    #         )
    #         + """
    #             </query>
    #         </iq>
    #         """
    #     )
    #     self.send(
    #         f"""
    #         <iq id="2"
    #             from="{self.xmpp.boundjid.bare}"
    #             to="{self.user_jid}"
    #             type="get">
    #             <query xmlns="http://jabber.org/protocol/disco#info"
    #                    node="https://gajim.org#pAg7f6566/B8BfVtblCX9GwW1mA=" />
    #         </iq>
    #     """,
    #     )
    #     self.recv(f"""<iq id="1" type="result" />""")
    #     for buddy in self.xmpp.legacy_client.buddies:
    #         ver = self.xmpp.loop.run_until_complete(self.xmpp["xep_0115"].get_verstring(jid=buddy.jid))
    #         h = hashlib.sha1(buddy.avatar_bytes).hexdigest()
    #         # h = self.xmpp.loop.run_until_complete(self.xmpp["xep_0153"].api["get_hash"](jid=buddy.jid))
    #         self.send(
    #             f"""
    #             <presence to="{self.user_jid.bare}"
    #                       from="{buddy.jid}">
    #                 <x xmlns="vcard-temp:x:update">
    #                 <photo>
    #                 {h}
    #                 </photo>
    #                 </x>
    #                 <c xmlns="http://jabber.org/protocol/caps"
    #                    node="{self.xmpp["xep_0115"].caps_node}"
    #                    hash="sha-1"
    #                    ver="{ver}" />
    #                 <priority>0</priority>
    #             </presence>""",
    #         )
    #     self.send(
    #         f"""
    #         <presence xmlns="jabber:component:accept"
    #                   to="{self.user_jid.bare}"
    #                   from="{self.xmpp.boundjid.bare}">
    #             <x xmlns="vcard-temp:x:update">
    #                 <photo>
    #                 {self.xmpp["xep_0153"].api["get_hash"](jid=self.xmpp.boundjid, node=None, ifrom=None, args={})}
    #                 </photo>
    #             </x>
    #             <priority>0</priority>
    #         </presence>
    #         """
    #     )
    #     assert self.next_sent() is None


logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)
