import datetime
import tempfile
import uuid
from base64 import b64encode
from pathlib import Path
from typing import Hashable, Optional, Dict, Any

import pytest
from slixmpp import JID, Message
from slixmpp.exceptions import XMPPError

import slidge.core.muc.room
import slidge.core.mixins.message
from slidge import *
from slidge.core.muc import MucType
from slidge.core.muc.archive import MessageArchive
from slidge.util.test import SlidgeTest
from slidge.util.types import LegacyContactType, LegacyMessageType
from slidge.core.cache import avatar_cache


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

    async def paused(self, c: LegacyContactType, thread=None):
        pass

    async def correct(
        self, c: LegacyContactType, text: str, legacy_msg_id: Any, thread=None
    ):
        pass

    async def search(self, form_values: Dict[str, str]):
        pass

    async def login(self):
        pass

    async def logout(self):
        pass

    async def send_text(
        self,
        chat: LegacyContact,
        text: str,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to=None,
        thread=None,
    ):
        self.SENT_TEXT.append(locals())
        return "legacy-id"

    async def send_file(self, c: LegacyContact, url: str, **kwargs):
        pass

    async def active(self, c: LegacyContact, thread=None):
        pass

    async def inactive(self, c: LegacyContact, thread=None):
        pass

    async def composing(self, c: LegacyContact, thread=None):
        pass

    async def displayed(self, c: LegacyContact, legacy_msg_id: Hashable, thread=None):
        pass

    async def react(
        self,
        c: LegacyContact,
        legacy_msg_id: LegacyMessageType,
        emojis: list[str],
        thread=None,
    ):
        self.REACTED.append(locals())


jids = {123: "juliet", 111: "firstwitch", 222: "secondwitch", 333: "not-in-roster"}
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


class Contact(LegacyContact):
    async def update_info(self):
        self.name = self.jid.local


class Participant(LegacyParticipant):
    pass


class MUC(LegacyMUC):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.history = []
        self.user_nick = "thirdwitch"
        self.archive = MessageArchive(10e7)

    async def available_emojis(self, legacy_msg_id=None):
        if self.jid.local != "room-private-emoji-restricted":
            return
        return {"💘", "❤️", "💜"}

    async def backfill(self, _id=None, _when=None):
        for hour in range(10):
            sender = await self.get_participant(f"history-man-{hour}")
            sender.send_text(
                body=f"Body #{hour}",
                legacy_msg_id=f"legacy-{hour}",
                when=datetime.datetime(
                    2000, 1, 1, hour, 0, 0, tzinfo=datetime.timezone.utc
                ),
                archive_only=True,
            )

    async def fill_participants(self):
        if "private" in str(self.legacy_id):
            first = await self.get_participant_by_contact(
                await self.session.contacts.by_legacy_id(111)
            )
            # first.nickname = "firstwitch"
            second = await self.get_participant_by_contact(
                await self.session.contacts.by_legacy_id(222)
            )
            # second.nickname = "secondwitch"
        else:
            first = await self.get_participant("firstwitch")
            second = await self.get_participant("secondwitch")
        first.affiliation = "owner"
        first.role = "moderator"

        second.affiliation = "admin"
        second.role = "moderator"
        await self.get_user_participant()

    async def update_info(self):
        if self.jid.local == "room-private":
            self.name = "Private Room"
            self.subject = "Private Subject"
            self.type = MucType.GROUP
            return

        if self.jid.local == "room-private-emoji-restricted":
            self.name = "Private Room"
            self.subject = "Private Subject"
            self.type = MucType.GROUP
            self.REACTIONS_SINGLE_EMOJI = True
            return

        if self.jid.local == "room-public":
            self.name = "Public Room"
            self.subject = "Public Subject"
            self.type = MucType.CHANNEL
            return

        if self.jid.local == "coven":
            self.avatar = Path(__file__).parent.parent / "dev" / "assets" / "5x5.png"
            self.name = "The coven"


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part: str):
        if not local_part.startswith("room") and local_part != "coven":
            raise XMPPError("item-not-found")
        else:
            return local_part

    async def by_jid(self, jid: JID):
        muc = await super().by_jid(jid)
        if not (x in jid.local for x in ["private", "public", "coven"]):
            raise XMPPError("item-not-found")
        return muc

    async def fill(self):
        await self.by_legacy_id("room-private-emoji-restricted")
        await self.by_legacy_id("room-private")
        await self.by_legacy_id("room-public")
        await self.by_legacy_id("coven")


@pytest.mark.usefixtures("avatar")
class TestMuc(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        slidge.core.muc.room.uuid4 = slidge.core.mixins.message.uuid4 = lambda: "uuid"
        self.get_romeo_session().logged = True

    def tearDown(self):
        slidge.core.muc.room.uuid4 = slidge.core.mixins.message.uuid4 = uuid.uuid4

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def get_private_muc(self, name="room-private") -> MUC:
        return self.xmpp.loop.run_until_complete(
            self.get_romeo_session().bookmarks.by_jid(
                JID(f"{name}@aim.shakespeare.lit")
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
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
            </error></iq>
            """,
            use_values=False,
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
                <identity category="conference" type="text" name="Private Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent"/>
                <feature var="muc_membersonly"/>
                <feature var="muc_nonanonymous"/>
                <feature var="muc_hidden"/>
                <feature var="urn:xmpp:sid:0" />
                <feature var="urn:xmpp:mam:2"/>
           		<feature var="urn:xmpp:mam:2#extended"/>
           		<feature var="vcard-temp"/>
           		<feature var="urn:xmpp:ping"/>
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
                    <field var="muc#roominfo_subject">
   		    		 <value>Private Subject</value>
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

    def test_disco_group_emoji_restricted(self):
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="room-private-emoji-restricted@{self.xmpp.boundjid.bare}" id="123">
                <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq xmlns="jabber:component:accept" type="result"
                from="room-private-emoji-restricted@{self.xmpp.boundjid.bare}" to="romeo@montague.lit/gajim" id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference" type="text" name="Private Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent"/>
                <feature var="muc_membersonly"/>
                <feature var="muc_nonanonymous"/>
                <feature var="muc_hidden"/>
                <feature var="urn:xmpp:sid:0" />
                <feature var="urn:xmpp:mam:2"/>
                <feature var="urn:xmpp:mam:2#extended"/>
                <feature var="vcard-temp"/>
                <feature var="urn:xmpp:ping"/>
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
                    <field var="muc#roominfo_subject">
                     <value>Private Subject</value>
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
                <x xmlns='jabber:x:data' type='result'>
                  <field var='FORM_TYPE' type='hidden'>
                    <value>urn:xmpp:reactions:0:restrictions</value>
                  </field>
                  <field var='max_reactions_per_user'>
                    <value>1</value>
                  </field>
                  <field var='allowlist'>
                    <value>💘</value>
                    <value>❤️</value>
                    <value>💜</value>
                  </field>
                </x> 
              </query>
            </iq>
            """,
        )

    def test_disco_items(self):
        session = self.get_romeo_session()
        self.xmpp.loop.run_until_complete(session.bookmarks.fill())
        self.recv(
            f"""
            <iq type="get" from="romeo@montague.lit/gajim" to="aim.shakespeare.lit" id="123">
                <query xmlns='http://jabber.org/protocol/disco#items'/>
            </iq>
            """
        )
        self.send(
            """
           <iq xmlns="jabber:component:accept" type="result" from="aim.shakespeare.lit" to="romeo@montague.lit/gajim" id="123">   	
            <query xmlns="http://jabber.org/protocol/disco#items">
                <item jid="room-private@aim.shakespeare.lit" name="Private Room"/>
                <item jid="room-public@aim.shakespeare.lit" name="Public Room"/>
                <item jid="coven@aim.shakespeare.lit" name="The coven"/>
                <item jid="room-private-emoji-restricted@aim.shakespeare.lit" name="Private Room"/>
            </query>
           </iq>
            """
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
                <identity category="conference" type="text" name="Public Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent"/>
                <feature var="muc_public"/>
                <feature var="muc_open"/>
                <feature var="muc_semianonymous"/>
        		<feature var="urn:xmpp:mam:2"/>
           		<feature var="urn:xmpp:mam:2#extended"/>
           		<feature var="vcard-temp"/>
           		<feature var="urn:xmpp:ping"/>
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
                    <field var="muc#roominfo_subject">
   		    		 <value>Public Subject</value>
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
                        <value>0</value>
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
                <feature var="http://jabber.org/protocol/chatstates"/>
           		<feature var="urn:xmpp:receipts"/>
           		<feature var="urn:xmpp:message-correct:0"/>
           		<feature var="urn:xmpp:chat-markers:0"/>
           		<feature var="jabber:x:oob"/>
           		<feature var="urn:xmpp:reactions:0"/>
          		<feature var="urn:xmpp:message-retract:0"/>
           		<feature var="urn:xmpp:reply:0"/>
           		<feature var="urn:ietf:params:xml:ns:vcard-4.0"/>
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
        muc = self.get_private_muc("room-private")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        muc.session.contacts.ready.set_result(True)
        participant = self.xmpp.loop.run_until_complete(muc.get_participant("stan"))
        participant.send_text("Hey", when=now)
        muc.subject_date = now
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
           <presence xmlns="jabber:component:accept" from="room-private@aim.shakespeare.lit/stan" to="romeo@montague.lit/gajim">
            <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member" role="participant"/>
            </x>
            <priority>0</priority>
           </presence>
            """
        )
        self.send(
            """
            <presence
                from='room-private@aim.shakespeare.lit/firstwitch'
                to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='owner' role='moderator' jid="firstwitch@aim.shakespeare.lit/slidge"/>
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
                <item affiliation='admin' role='moderator' jid="secondwitch@aim.shakespeare.lit/slidge"/>
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
                <item affiliation='member' role='participant' jid='romeo@montague.lit/gajim'/>
                <status code='100'/>
                <status code='110'/>
              </x>
            </presence>
            """,
        )
        now_fmt = now.isoformat().replace("+00:00", "Z")
        self.send(
            f"""
            <message type="groupchat" from="room-private@aim.shakespeare.lit/stan" to="romeo@montague.lit/gajim">
                <body>Hey</body>
                <delay xmlns="urn:xmpp:delay" stamp="{now_fmt}" />
                <stanza-id xmlns="urn:xmpp:sid:0" id="uuid" by="room-private@aim.shakespeare.lit"/>
            </message>
            """,
            use_values=False,
        )
        self.send(
            f"""
            <message type="groupchat" to="romeo@montague.lit/gajim" from="room-private@aim.shakespeare.lit/unknown">
                <delay xmlns="urn:xmpp:delay" stamp="{now_fmt}" />
                <subject>Private Subject</subject>
            </message>
            """
        )

    def test_join_channel(self):
        self.recv(
            """
            <presence
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='room-public@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc'/>
            </presence>
            """
        )
        self.send(
            """
            <presence
                from='room-public@aim.shakespeare.lit/firstwitch'
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
                from='room-public@aim.shakespeare.lit/secondwitch'
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
                from='room-public@aim.shakespeare.lit/thirdwitch'
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

    def test_resource_not_joined(self):
        session = self.get_romeo_session()
        session.contacts.ready.set_result(True)
        self.recv(
            """
            <message from='romeo@montague.lit/gajim' type='groupchat'
                to='room-private@aim.shakespeare.lit'>
                <body>am I here?</body>
            </message>
            """
        )
        self.send(
            """
           <message xmlns="jabber:component:accept" from="room-private@aim.shakespeare.lit" type="error" to="romeo@montague.lit/gajim">              
            <error type="modify">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">You are not connected to this chat</text>
            </error>
           </message>
            """
        )
        self.send(
            """
           <presence xmlns="jabber:component:accept" to="romeo@montague.lit/gajim" from="room-private@aim.shakespeare.lit/thirdwitch">
            <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="none" role="none"/>
                <status code="333"/>
                <status code="110"/>
            </x>
            <priority>0</priority>
           </presence>
            """
        )
        assert self.next_sent() is None

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
                <fallback xmlns='urn:xmpp:fallback:0' for='urn:xmpp:reply:0'>
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
        participant._LegacyParticipant__presence_sent = True
        participant.send_text("the body", legacy_msg_id="legacy-XXX")
        self.send(
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
                <body>the body</body>
                <active xmlns="http://jabber.org/protocol/chatstates"/>
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
        participant._LegacyParticipant__presence_sent = True
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
                    <active xmlns="http://jabber.org/protocol/chatstates"/>
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
        participant._LegacyParticipant__presence_sent = True
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
                    <active xmlns="http://jabber.org/protocol/chatstates"/>
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
        participant._LegacyParticipant__presence_sent = True
        second_witch = self.xmpp.loop.run_until_complete(
            muc.get_participant("secondwitch")
        )
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to_msg_id="legacy-REPLY-TO",
            reply_to_author=second_witch,
            reply_to_fallback_text="Blabla",
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
                    <fallback xmlns="urn:xmpp:fallback:0" for="urn:xmpp:reply:0">
                  		<body start="0" end="9"/>
                   	</fallback>
                   	<active xmlns="http://jabber.org/protocol/chatstates"/>
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
                    <stanza-id xmlns="urn:xmpp:sid:0" id="uuid" by="room-private@aim.shakespeare.lit"/>
                </message>
                """,
                use_values=False,
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
        participant._LegacyParticipant__presence_sent = True
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
              <stanza-id xmlns="urn:xmpp:sid:0" id="uuid" by="room-private@aim.shakespeare.lit"/>
            </message>
            """,
            use_values=False,
        )

    def test_mam_form_fields(self):
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <query xmlns='urn:xmpp:mam:2'>
                <x xmlns='jabber:x:data' type='form'>
                  <field type='hidden' var='FORM_TYPE'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field type='jid-single' var='with'/>
                  <field type='text-single' var='start'/>
                  <field type='text-single' var='end'/>
                  <field type='text-single' var='before-id'/>
                  <field type='text-single' var='after-id'/>
                  <field type='list-multi' var='ids'>
                    <validate xmlns="http://jabber.org/protocol/xdata-validate" datatype="xs:string">
                      <open/>
                    </validate>
                  </field>
                  <field type='boolean' var='include-groupchat'/>
                </x>
              </query>
            </iq>
            """
        )

    def test_mam_all(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id' />
            </iq>
            """
        )
        for i in range(10):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin stable="false" xmlns='urn:xmpp:mam:2' complete='true'>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>0</first>
                  <last>9</last>
                  <count>10</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_page_limit(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='FORM_TYPE' type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>2</max>
                </set>
              </query>
            </iq>
            """
        )
        for i in range(3, 5):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2' stable="false">
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>3</first>
                  <last>4</last>
                  <count>2</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_page_after(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='FORM_TYPE' type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>2</max>
                  <after>5</after>
                </set>
              </query>
            </iq>
            """
        )
        for i in range(6, 8):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2' stable="false">
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>6</first>
                  <last>7</last>
                  <count>2</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_page_after_not_found(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                <x xmlns='jabber:x:data' type='submit'>
                  <field var='FORM_TYPE' type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>2</max>
                  <after>12</after>
                </set>
              </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='error' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <error type='cancel'>
                <item-not-found xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_last_page(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                  <x xmlns='jabber:x:data' type='submit'>
                    <field var='FORM_TYPE' type='hidden'><value>urn:xmpp:mam:2</value></field>
                    <field var='start'><value>2000-01-01T03:00:00Z</value></field>
                  </x>
                  <set xmlns='http://jabber.org/protocol/rsm'>
                     <max>3</max>
                     <before/>
                  </set>
              </query>
            </iq>
            """
        )
        for i in range(7, 10):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2' stable="false" complete='true'>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>7</first>
                  <last>9</last>
                  <count>3</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_flip(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                  <x xmlns='jabber:x:data' type='submit'>
                    <field var='FORM_TYPE' type='hidden'><value>urn:xmpp:mam:2</value></field>
                    <field var='start'><value>2000-01-01T03:00:00Z</value></field>
                  </x>
                  <set xmlns='http://jabber.org/protocol/rsm'>
                     <max>3</max>
                     <after>5</after>
                  </set>
                  <flip-page/>
              </query>
            </iq>
            """
        )
        for i in range(9, 6, -1):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2' stable="false">
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>9</first>
                  <last>7</last>
                  <count>3</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_flip_no_max(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                  <flip-page/>
              </query>
            </iq>
            """
        )
        for i in range(9, -1, -1):
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2' stable="false" complete="true">
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>9</first>
                  <last>0</last>
                  <count>10</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_metadata(self):
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <metadata xmlns='urn:xmpp:mam:2'/>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <metadata xmlns='urn:xmpp:mam:2'>
                <start id='0' timestamp='2000-01-01T00:00:00Z' />
                <end id='9' timestamp='2000-01-01T09:00:00Z' />
              </metadata>
            </iq>
            """
        )

    def test_mam_metadata_empty(self):
        muc = self.get_private_muc()
        muc._LegacyMUC__history_filled = True
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
              <metadata xmlns='urn:xmpp:mam:2'/>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
                <metadata xmlns='urn:xmpp:mam:2'/>
            </iq>
            """
        )

    def test_mam_with(self):
        for i in range(10):
            self.recv(
                f"""
                <iq from='romeo@montague.lit/gajim' type='set' id='iq-id1' to='room-private@aim.shakespeare.lit'>
                  <query xmlns='urn:xmpp:mam:2' queryid='query-id'>
                      <x xmlns='jabber:x:data' type='submit'>
                      <field var='FORM_TYPE' type='hidden'>
                        <value>urn:xmpp:mam:2</value>
                      </field>
                      <field var='with'>
                        <value>room-private@aim.shakespeare.lit/history-man-{i}</value>
                      </field>
                    </x>
                  </query>
                </iq>
                """
            )
            self.send(
                f"""
                <message to='romeo@montague.lit/gajim' from='room-private@aim.shakespeare.lit'>
                  <result xmlns='urn:xmpp:mam:2' queryid='query-id' id='{i}'>
                    <forwarded xmlns='urn:xmpp:forward:0'>
                      <delay xmlns='urn:xmpp:delay' stamp='2000-01-01T{i:02d}:00:00Z'/>
                      <message xmlns='jabber:client'
                               from="room-private@aim.shakespeare.lit/history-man-{i}"
                               type='groupchat'
                               id='{i}'>
                        <body>Body #{i}</body>
                        <stanza-id xmlns="urn:xmpp:sid:0" id="{i}" by="room-private@aim.shakespeare.lit"/>
                      </message>
                    </forwarded>
                  </result>
                </message>
                """
            )
            self.send(
                f"""
                <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
                  <fin stable="false" xmlns='urn:xmpp:mam:2' complete='true'>
                    <set xmlns='http://jabber.org/protocol/rsm'>
                      <first>{i}</first>
                      <last>{i}</last>
                      <count>1</count>
                    </set>
                  </fin>
                </iq>
                """
            )

    def test_get_members(self):
        muc = self.get_private_muc()
        muc.user_resources.add("gajim")
        muc.session.contacts.ready.set_result(True)
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
                <query xmlns='http://jabber.org/protocol/muc#admin'>
                    <item affiliation='admin' />
                </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item nick="secondwitch" affiliation="admin" role="moderator" jid="secondwitch@aim.shakespeare.lit"/>
              </query>
            </iq>
            """
        )
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
                <query xmlns='http://jabber.org/protocol/muc#admin'>
                    <item affiliation='owner' />
                </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
                <query xmlns="http://jabber.org/protocol/muc#admin">
                    <item nick="firstwitch" affiliation="owner" role="moderator" jid="firstwitch@aim.shakespeare.lit"/>
                </query>
            </iq>
            """
        )
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='iq-id1' to='room-private@aim.shakespeare.lit'>
                <query xmlns='http://jabber.org/protocol/muc#admin'>
                    <item affiliation='member' />
                </query>
            </iq>
            """
        )
        self.send(
            """
            <iq type='result' id='iq-id1' from='room-private@aim.shakespeare.lit' to='romeo@montague.lit/gajim'>
                <query xmlns='http://jabber.org/protocol/muc#admin'>
           	    	<item nick="thirdwitch" affiliation="member" role="participant" jid="romeo@montague.lit"/>
                </query>
            </iq>
            """
        )

    def test_room_avatar(self):
        v = b64encode(self.avatar_path.read_bytes()).decode()
        self.xmpp.loop.run_until_complete(self.get_romeo_session().bookmarks.fill())
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='get1' to='room-private@aim.shakespeare.lit'>
                <vCard xmlns='vcard-temp'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq from="room-private@aim.shakespeare.lit"
                type="error"
                to="romeo@montague.lit/gajim"
                id="get1">
             	<error type="cancel">
               		<item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas"/>
               	</error>
            </iq>
            """,
            use_values=False,
        )
        self.recv(
            """
            <iq from='romeo@montague.lit/gajim' type='get' id='get1' to='coven@aim.shakespeare.lit'>
                <vCard xmlns='vcard-temp'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq from="coven@aim.shakespeare.lit"
                type="result"
                to="romeo@montague.lit/gajim"
                id="get1">
             <vCard xmlns="vcard-temp">
                <PHOTO>
                  <TYPE>image/png</TYPE>
                  <BINVAL>{v}</BINVAL>
                </PHOTO>
             </vCard>
            </iq>
            """
        )

    def test_join_room_avatar(self):
        self.get_private_muc("coven")
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
            f"""
            <presence
                from='coven@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
                <x xmlns='vcard-temp:x:update'>
                    <photo>{self.avatar_original_sha1}</photo>
                  </x>
            </presence>
            """,
        )

    def test_send_to_bad_resource(self):
        muc = self.get_private_muc("coven")
        muc.user_resources.add("gajim")
        self.recv(
            """
            <message
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='coven@aim.shakespeare.lit/thirdwitch'
                type="error">
                <error type="cancel">
                    <gone xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                </error>
            </message>
            """
        )
        assert not muc.user_resources
        self.recv(
            """
            <message
                from='romeo@montague.lit/gajim'
                id='n13mt3l'
                to='coven@aim.shakespeare.lit/thirdwitch'
                type="error">
                <error type="cancel">
                    <gone xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                </error>
            </message>
            """
        )
        assert not muc.user_resources

    def test_archive_cleanup(self):
        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(tz=datetime.timezone.utc)
        m["body"] = "something"

        a = MessageArchive(1)
        assert len(list(a.get_all())) == 0
        a.add(m)
        assert len(list(a.get_all())) == 1

        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(days=2)
        m["body"] = "something"

        a = MessageArchive(1)
        assert len(list(a.get_all())) == 0
        a.add(m)
        assert len(list(a.get_all())) == 0

        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(days=0.5)
        m["body"] = "something"
        a.add(m)
        a.add(m)
        assert len(list(a.get_all())) == 2

    def test_moderate(self):
        muc = self.get_private_muc("room")
        muc.user_resources.add("gajim")
        p = muc.get_system_participant()
        p.moderate("legacy-666", "reason™")
        self.send(
            """
            <message type="groupchat" from='room@aim.shakespeare.lit' to="romeo@montague.lit/gajim">
              <stanza-id xmlns="urn:xmpp:sid:0" id="uuid" by="room@aim.shakespeare.lit"/>
              <apply-to id="666" xmlns="urn:xmpp:fasten:0">
                <moderated by='room@aim.shakespeare.lit' xmlns='urn:xmpp:message-moderate:0'>
                  <retract xmlns='urn:xmpp:message-retract:0' />
                  <reason>reason™</reason>
                </moderated>
              </apply-to>
            </message>
            """
        )

    def test_participant_avatar(self):
        self.test_join_group()
        v = b64encode(self.avatar_bytes).decode()
        with tempfile.TemporaryDirectory() as d:
            avatar_cache.dir = Path(d)
            session = self.get_romeo_session()
            self.xmpp.loop.run_until_complete(session.bookmarks.fill())
            muc = self.get_private_muc()
            # self.xmpp.loop.run_until_complete(muc.fill_participants())
            muc._LegacyMUC__participants_filled = True
            contact = self.xmpp.loop.run_until_complete(
                session.contacts.by_legacy_id(333)
            )
            contact.avatar = self.avatar_path
            self.xmpp.loop.run_until_complete(muc.get_participant_by_contact(contact))
            self.recv(
                f"""
                <iq from="romeo@montague.lit/gajim"
                    to="{muc.jid}/not-in-roster"
                    type="get" xml:lang="en">
                  <vCard xmlns="vcard-temp" />
                </iq>
                """
            )
            self.send(
                f"""
               <iq xmlns="jabber:component:accept" from="room-private@aim.shakespeare.lit/not-in-roster" to="romeo@montague.lit/gajim" type="result" xml:lang="en" id="1">
                
                                 
                <vCard xmlns="vcard-temp">
                    <PHOTO>
                        <BINVAL>{v}</BINVAL>
                        <TYPE>image/png</TYPE>
                    </PHOTO>
                </vCard>
               </iq>
                """,
                use_values=False,
            )
