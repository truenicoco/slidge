from typing import Hashable, Optional, Dict, Any

from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *
from slidge.core.muc import MucType

from slidge.util.test import SlidgeTest
from slidge.core.contact import LegacyContactType
from slidge.util.types import LegacyMessageType


class Gateway(BaseGateway):
    COMPONENT_NAME = "SLIDGE TEST"
    GROUPS = True


class Session(BaseSession):
    SENT_TEXT = []
    REACTED = []

    def __init__(self, user):
        super().__init__(user)

    @staticmethod
    def xmpp_msg_id_to_legacy_msg_id(i: str) -> str:
        return "legacy-" + i

    @staticmethod
    def legacy_msg_id_to_xmpp_msg_id(i: str) -> str:
        return i[7:]

    async def paused(self, c: LegacyContactType):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContactType):
        pass

    async def search(self, form_values: Dict[str, str]):
        pass

    async def login(self):
        pass

    async def logout(self):
        pass

    async def send_text(
        self,
        text: str,
        chat: LegacyContact,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to=None,
    ):
        self.SENT_TEXT.append(locals())
        return "legacy-id"

    async def send_file(self, url: str, c: LegacyContact, **kwargs):
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        pass

    async def displayed(self, legacy_msg_id: Hashable, c: LegacyContact):
        pass

    async def react(
        self, legacy_msg_id: LegacyMessageType, emojis: list[str], c: LegacyContact
    ):
        self.REACTED.append(locals())


jids = {123: "juliet", 111: "firstwitch", 222: "secondwitch"}
legacy = {v: k for k, v in jids.items()}


class Roster(LegacyRoster):
    async def jid_username_to_legacy_id(self, jid_username: str) -> int:
        try:
            return legacy[jid_username]
        except KeyError:
            raise XMPPError(text="Only juliet", condition="item-not-found")

    async def legacy_id_to_jid_username(self, legacy_id: int) -> str:
        try:
            return jids[legacy_id]
        except KeyError:
            raise XMPPError(text="Only juliet", condition="item-not-found")


class Participant(LegacyParticipant):
    pass


class MUC(LegacyMUC[Session, str, Participant, str]):
    user_nick = "thirdwitch"

    async def get_participants(self):
        first = Participant(self, "firstwitch")
        first.affiliation = "owner"
        first.role = "moderator"
        if "private" in str(self.legacy_id):
            first.contact = await self.session.contacts.by_legacy_id(111)
        yield first
        second = Participant(self, "secondwitch")
        second.affiliation = "admin"
        second.role = "moderator"
        if "private" in str(self.legacy_id):
            second.contact = await self.session.contacts.by_legacy_id(222)
        yield second

    async def fill_history(self, full_jid: JID, **kwargs):
        pass


class Bookmarks(LegacyBookmarks[Session, MUC, str]):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part: str):
        if not local_part.startswith("room") and local_part != "coven":
            raise XMPPError("item-not-found")
        else:
            return local_part

    async def by_jid(self, jid: JID):
        muc = await super().by_jid(jid)
        if "private" in muc.legacy_id:
            muc.type = MucType.GROUP
        elif "public" in muc.legacy_id:
            muc.type = MucType.CHANNEL
        elif muc.legacy_id != "coven":
            raise XMPPError("item-not-found")
        return muc


class TestMuc(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def get_private_muc(self) -> MUC:
        return self.xmpp.loop.run_until_complete(
            self.get_romeo_session().bookmarks.by_jid(
                JID("room-private@aim.shakespeare.lit")
            )
        )

    def test_disco_non_existing_room(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="non-room@{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept"
                type="error" from="non-room@aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                id="123">
              <error xmlns="jabber:client" type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
            </error></iq>
            """,
        )

    def test_disco_group(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="room-private@{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="result"
                from="room-private@{self.xmpp.boundjid.bare}" to="romeo@montague.lit/gajim" id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference" type="text" name="room-private" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent"/>
                <feature var="muc_membersonly"/>
                <feature var="muc_nonanonymous"/>
                <feature var="muc_hidden"/>
                <feature var="urn:xmpp:sid:0" />
                <x xmlns="jabber:x:data" type="result">
                    <field var="FORM_TYPE" type="hidden">
                        <value>http://jabber.org/protocol/muc#roominfo</value>
                    </field>
                    <field var="muc#maxhistoryfetch">
                        <value>100</value>
                    </field>
                    <field var="muc#roominfo_subjectmod" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_persistentroom" type="boolean">
                        <value>1</value>
                    </field>
                    <field var="muc#roomconfig_changesubject" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_membersonly" type="boolean">
                        <value>1</value>
                    </field>
                    <field var="muc#roomconfig_whois" type="boolean">
                        <value>1</value>
                    </field>
                    <field var="muc#roomconfig_publicroom" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_allowpm" type="boolean">
                        <value>0</value>
                    </field>
                </x>
              </query>
            </iq>
            """,
        )

    def test_disco_channel(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="room-public@{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="result"
                from="room-public@{self.xmpp.boundjid.bare}" to="romeo@montague.lit/gajim" id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference" type="text" name="room-public" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent"/>
                <feature var="muc_public"/>
                <feature var="muc_open"/>
                <feature var="muc_semianonymous"/>
                <x xmlns="jabber:x:data" type="result">
                    <field var="FORM_TYPE" type="hidden">
                        <value>http://jabber.org/protocol/muc#roominfo</value>
                    </field>
                    <field var="muc#maxhistoryfetch">
                        <value>100</value>
                    </field>
                    <field var="muc#roominfo_subjectmod" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_persistentroom" type="boolean">
                        <value>1</value>
                    </field>
                    <field var="muc#roomconfig_changesubject" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_membersonly" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_whois" type="boolean">
                        <value>0</value>
                    </field>
                    <field var="muc#roomconfig_publicroom" type="boolean">
                        <value>1</value>
                    </field>
                    <field var="muc#roomconfig_allowpm" type="boolean">
                        <value>1</value>
                    </field>
                </x>
              </query>
            </iq>
            """,
        )

    def test_disco_participant(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="room-public@{self.xmpp.boundjid.bare}/firstwitch" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq type="result" from="room-public@aim.shakespeare.lit/firstwitch"
                to="romeo@montague.lit/gajim" id="123">
              <query xmlns='http://jabber.org/protocol/disco#info'>
                <identity category="client" type="pc" name="firstwitch"/>
                <feature var="http://jabber.org/protocol/disco#info"/>
              </query>
            </iq>
            """
        )

    def test_join_muc_no_nick(self):
        self.recv(
            """
            <presence
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='coven@aim.shakespeare.lit'>
              <x xmlns='http://jabber.org/protocol/muc'/>
            </presence>
            """
        )
        self.send(
            """
            <iq id="1"
                from="aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                type="get">
                <query xmlns="http://jabber.org/protocol/disco#info"/>
            </iq>
            """
        )
        self.send(
            """
            <presence
                from='coven@aim.shakespeare.lit'
                id='n13mt3l'
                to='romeo@montague.lit/gajim'
                type='error'>
              <error by='coven@aim.shakespeare.lit' type='modify'>
                <jid-malformed xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
              </error>
            </presence>
            """,
            use_values=False,  # the error element does not appear for some reason
        )

    def test_join_group(self):
        self.recv(
            """
            <presence
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='coven@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc'/>
            </presence>
            """
        )
        self.send(
            """
            <iq id="1"
                from="aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                type="get">
                <query xmlns="http://jabber.org/protocol/disco#info"/>
            </iq>
            """
        )
        self.send(
            """
            <presence
                from='coven@aim.shakespeare.lit/firstwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='owner' role='moderator'/>
              </x>
            </presence>
            """,
        )
        self.send(
            """
            <presence
                from='coven@aim.shakespeare.lit/secondwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='admin' role='moderator'/>
              </x>
            </presence>
            """,
        )
        self.send(
            """
            <presence
                id='n13mt3l'
                from='coven@aim.shakespeare.lit/thirdwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
                <status code='110'/>
              </x>
            </presence>
            """,
        )

    def test_join_channel(self):
        self.recv(
            """
            <presence
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='room-private@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc'/>
            </presence>
            """
        )
        self.send(
            """
            <iq id="1"
                from="aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                type="get">
                <query xmlns="http://jabber.org/protocol/disco#info"/>
            </iq>
            """
        )
        self.send(
            """
            <presence
                from='room-private@aim.shakespeare.lit/firstwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='owner' role='moderator' jid='firstwitch@aim.shakespeare.lit/slidge'/>
              </x>
            </presence>
            """,
        )
        self.send(
            """
            <presence
                from='room-private@aim.shakespeare.lit/secondwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='admin' role='moderator' jid='secondwitch@aim.shakespeare.lit/slidge'/>
              </x>
            </presence>
            """,
        )
        self.send(
            """
            <presence
                id='n13mt3l'
                from='room-private@aim.shakespeare.lit/thirdwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
                <status code='110'/>
              </x>
            </presence>
            """,
        )

    def test_self_ping_disconnected(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' id='s2c1' type='get'
                to='room-private@aim.shakespeare.lit/SlidgeUser'>
                <ping xmlns='urn:xmpp:ping'/>
            </iq>
            """
        )
        self.send(
            """
            <iq from='room-private@aim.shakespeare.lit/SlidgeUser' id='s2c1' type='error'
                  to='romeo@montague.lit/gajim' >
              <error type="cancel" by="room-private@aim.shakespeare.lit">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_self_ping_connected(self):
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' id='s2c1' type='get'
                to='room-private@aim.shakespeare.lit/SlidgeUser'>
                <ping xmlns='urn:xmpp:ping'/>
            </iq>
            """
        )
        self.send(
            """
            <iq xmlns="jabber:component:accept"
                from="room-private@aim.shakespeare.lit/SlidgeUser"
                id="s2c1"
                type="result"
                to="romeo@montague.lit/gajim">
            </iq>
            """,
            use_values=False,
        )

    # def test_origin_id(self):
    #     """
    #     this test is broken because of slixtest magic, but the behavior is actually good
    #     in real conditions
    #     """
    #     session = BaseSession.get_self_or_unique_subclass().from_jid(
    #         JID("romeo@montague.lit")
    #     )
    #     muc = self.xmpp.loop.run_until_complete(
    #         session.bookmarks.by_jid(JID("room-private@aim.shakespeare.lit"))
    #     )
    #     muc.user_resources.add("gajim")
    #     self.recv(
    #         """
    #         <message from='romeo@montague.lit/gajim' type='groupchat'
    #                  to='room-private@aim.shakespeare.lit'>
    #             <body>body</body>
    #             <origin-id xmlns="urn:xmpp:sid:0" id="origin" />
    #         </message>
    #         """
    #     )
    #     self.send(
    #         """
    #         <message to='romeo@montague.lit/gajim' type='get'
    #                  from='room-private@aim.shakespeare.lit/SlideUser'>
    #             <body>body</body>
    #             <origin-id xmlns="urn:xmpp:sid:0" id="origin" />
    #             <stanza-id xmlns="urn:xmpp:sid:0" id="muc-id" by="room-private@aim.shakespeare.lit" />
    #         </message>
    #         """,
    #     )

    def test_msg_from_xmpp(self):
        muc = self.get_private_muc()
        muc.user_resources = ["gajim", "movim"]
        self.recv(
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
                <body>BODY</body>
                <origin-id xmlns="urn:xmpp:sid:0" id="xmpp-id"/>
            </message>
            """
        )
        for r in muc.user_resources:
            self.send(
                f"""
                <message from='{muc.jid}/{muc.user_nick}'
                         id='origin'
                         to='romeo@montague.lit/{r}'
                         type='groupchat'>
                    <body>BODY</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                         id="legacy-id"
                         by="room-private@aim.shakespeare.lit"/>
                    <origin-id xmlns="urn:xmpp:sid:0"
                         id="xmpp-id"/>
                </message>
                """,
                use_values=False,
            )
        assert self.next_sent() is None
        sent = Session.SENT_TEXT.pop()
        assert sent["text"] == "BODY", sent
        assert sent["chat"].is_group, sent
        assert sent["reply_to_msg_id"] is None, sent
        assert sent["reply_to_fallback_text"] is None, sent
        assert sent["reply_to"] is None, sent

    def test_msg_reply_from_xmpp(self):
        Session.SENT_TEXT = []
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        fallback = "> Anna wrote:\n> We should bake a cake\n"
        stripped_body = "Great idea!"
        self.recv(
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
                <body>{fallback}{stripped_body}</body>
                <reply to='room-private@aim.shakespeare.lit/Anna' id='message-id1' xmlns='urn:xmpp:reply:0' />
                <fallback xmlns='urn:xmpp:feature-fallback:0' for='urn:xmpp:reply:0'>
                  <body start="0" end="{len(fallback)}" />
                </fallback>
            </message>
            """
        )
        self.next_sent()
        sent = Session.SENT_TEXT.pop()
        assert sent["reply_to"].nickname == "Anna"
        assert sent["reply_to_msg_id"] == "legacy-message-id1"
        assert sent["reply_to_fallback_text"] == fallback
        assert sent["text"] == stripped_body

    def test_msg_from_legacy(self):
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        participant: LegacyParticipant = self.xmpp.loop.run_until_complete(
            muc.get_participant("firstwitch")
        )
        participant.send_text("the body", legacy_msg_id="legacy-XXX")
        self.send(
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
                <body>the body</body>
                <markable xmlns="urn:xmpp:chat-markers:0"/>
                <stanza-id xmlns="urn:xmpp:sid:0"
                     id="XXX"
                     by="room-private@aim.shakespeare.lit"/>
            </message>
            """,
            use_values=False,
        )

    def test_msg_reply_self_from_legacy(self):
        Session.SENT_TEXT = []
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        participant: LegacyParticipant = self.xmpp.loop.run_until_complete(
            muc.get_participant("firstwitch")
        )
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to_msg_id="legacy-REPLY-TO",
            reply_self=True,
        )
        self.send(
            f"""
                <message from='{muc.jid}/firstwitch'
                         id='XXX'
                         to='romeo@montague.lit/gajim'
                         type='groupchat'>
                    <body>the body</body>
                    <markable xmlns="urn:xmpp:chat-markers:0"/>
                    <reply xmlns="urn:xmpp:reply:0" id="REPLY-TO" to="room-private@aim.shakespeare.lit/firstwitch"/>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit"/>
                </message>
                """,
            use_values=False,
        )

    def test_msg_reply_from_legacy(self):
        Session.SENT_TEXT = []
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        participant: LegacyParticipant = self.xmpp.loop.run_until_complete(
            muc.get_participant("firstwitch")
        )
        second_witch = self.xmpp.loop.run_until_complete(
            muc.get_participant("secondwitch")
        )
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to_msg_id="legacy-REPLY-TO",
            reply_to_author=second_witch,
        )
        self.send(
            f"""
                <message from='{muc.jid}/firstwitch'
                         id='XXX'
                         to='romeo@montague.lit/gajim'
                         type='groupchat'>
                    <body>the body</body>
                    <markable xmlns="urn:xmpp:chat-markers:0"/>
                    <reply xmlns="urn:xmpp:reply:0" id="REPLY-TO" to="room-private@aim.shakespeare.lit/secondwitch"/>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit"/>
                </message>
                """,
            use_values=False,
        )

    def test_msg_reply_from_legacy_fallback(self):
        Session.SENT_TEXT = []
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        participant: LegacyParticipant = self.xmpp.loop.run_until_complete(
            muc.get_participant("firstwitch")
        )
        second_witch = self.xmpp.loop.run_until_complete(
            muc.get_participant("secondwitch")
        )
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to_msg_id="legacy-REPLY-TO",
            reply_to_author=second_witch,
            reply_to_fallback_text="Blabla"
        )
        self.send(
            f"""
                <message from='{muc.jid}/firstwitch'
                         id='XXX'
                         to='romeo@montague.lit/gajim'
                         type='groupchat'>
                    <body>&gt; Blabla\nthe body</body>
                    <markable xmlns="urn:xmpp:chat-markers:0"/>
                    <reply xmlns="urn:xmpp:reply:0" id="REPLY-TO" to="room-private@aim.shakespeare.lit/secondwitch"/>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit"/>
                    <fallback xmlns="urn:xmpp:feature-fallback:0" for="urn:xmpp:reply:0">
                  		<body start="0" end="8"/>
                   	</fallback>
                </message>
                """,
            use_values=False,
        )

    def test_react_from_xmpp(self):
        muc = self.get_private_muc()
        muc.user_resources = ["gajim", "movim"]
        self.recv(
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
              <reactions id='SOME-ID' xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
              </reactions>
            </message>
            """
        )
        for r in muc.user_resources:
            self.send(
                f"""
                <message from='{muc.jid}/{muc.user_nick}'
                         id='origin'
                         to='romeo@montague.lit/{r}'
                         type='groupchat'>
                    <reactions id='SOME-ID' xmlns='urn:xmpp:reactions:0'>
                      <reaction>👋</reaction>
                    </reactions>
                </message>
                """,
                use_values=False
            )
        assert self.next_sent() is None
        sent = Session.REACTED.pop()
        assert sent["c"].is_group
        assert tuple(sent["emojis"]) == ("👋",)
        assert sent["legacy_msg_id"] == "legacy-SOME-ID"

    def test_react_from_legacy(self):
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        participant: LegacyParticipant = self.xmpp.loop.run_until_complete(
            muc.get_participant("firstwitch")
        )
        participant.react(legacy_msg_id="legacy-XXX", emojis="👋")
        self.send(
            f"""
            <message from='{muc.jid}/firstwitch'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <store xmlns="urn:xmpp:hints"/>
              <reactions id='XXX' xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
              </reactions>
            </message>
            """,
            use_values=False,
        )