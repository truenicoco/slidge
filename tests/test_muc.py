import datetime
import unittest.mock
import uuid
from base64 import b64encode
from pathlib import Path
from typing import Any, Dict, Hashable, Optional

import pytest
import slixmpp
from conftest import AvatarFixtureMixin
from slixmpp import JID, Message, Presence
from slixmpp.exceptions import XMPPError
from slixmpp.plugins import xep_0082

import slidge.core.mixins.message_maker
import slidge.group.room
import slidge.util.sql
from slidge import *
from slidge import LegacyBookmarks, LegacyContact, LegacyParticipant, LegacyRoster
from slidge.group.archive import MessageArchive
from slidge.util.test import SlidgeTest
from slidge.util.types import (
    Hat,
    LegacyContactType,
    LegacyMessageType,
    Mention,
    MessageReference,
    MucType,
)


class Gateway(BaseGateway):
    COMPONENT_NAME = "SLIDGE TEST"
    GROUPS = True


class Session(BaseSession):
    SENT_TEXT = []
    REACTED = []

    def __init__(self, user):
        super().__init__(user)

    async def wait_for_ready(self, timeout=10):
        return

    @staticmethod
    def xmpp_to_legacy_msg_id(i: str) -> str:
        return "legacy-" + i

    @staticmethod
    def legacy_to_xmpp_msg_id(i: str) -> str:
        return i[7:]

    async def on_paused(self, c: LegacyContactType, thread=None):
        pass

    async def on_correct(
        self,
        c: LegacyContactType,
        text: str,
        legacy_msg_id: Any,
        thread=None,
        link_previews=(),
    ):
        pass

    async def on_search(self, form_values: Dict[str, str]):
        pass

    async def login(self):
        pass

    async def logout(self):
        pass

    async def on_text(
        self,
        chat: LegacyContact,
        text: str,
        *,
        reply_to_msg_id=None,
        reply_to_fallback_text: Optional[str] = None,
        reply_to=None,
        thread=None,
        link_previews=(),
        mentions=None,
    ):
        self.SENT_TEXT.append(locals())
        return "legacy-id"

    async def on_react(
        self,
        c: LegacyContact,
        legacy_msg_id: LegacyMessageType,
        emojis: list[str],
        thread=None,
    ):
        self.REACTED.append(locals())


jids = {
    123: "juliet",
    111: "firstwitch",
    222: "secondwitch",
    333: "not-in-roster",
    666: "imposter",
    667: "imposter2",
    999: "weirdguy🎉",
}
legacy = {v: k for k, v in jids.items()}


class Roster(LegacyRoster):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.ready.set_result(True)

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
        if self.legacy_id in (666, 667):
            self.name = "firstwitch"
            return
        self.name = self.jid.local


class Participant(LegacyParticipant):
    pass


class MUC(slidge.LegacyMUC):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.history = []
        self.user_nick = "thirdwitch"

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
        if "weird" in str(self.legacy_id):
            first = await self.get_participant_by_legacy_id(999)
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
            await self.set_avatar(
                Path(__file__).parent.parent / "dev" / "assets" / "5x5.png",
                blocking=True,
            )
            self.name = "The coven"


class Bookmarks(LegacyBookmarks):
    @staticmethod
    async def jid_local_part_to_legacy_id(local_part: str):
        if not local_part.startswith("room") and local_part not in ("coven", "weird"):
            raise XMPPError("item-not-found")
        else:
            return local_part

    async def by_jid(self, jid: JID):
        muc = await super().by_jid(jid)
        if not (x in jid.local for x in ["private", "public", "coven", "weird"]):
            raise XMPPError("item-not-found")
        return muc

    async def fill(self):
        await self.by_legacy_id("room-private-emoji-restricted")
        await self.by_legacy_id("room-private")
        await self.by_legacy_id("room-public")
        await self.by_legacy_id("coven")


class Base(SlidgeTest):
    plugin = globals()

    def setUp(self):
        super().setUp()
        user_store.add(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        slidge.group.room.uuid4 = slidge.core.mixins.message_maker.uuid4 = (
            uuid.uuid4
        ) = lambda: "uuid"
        self.get_romeo_session().logged = True

    def tearDown(self):
        slidge.group.room.uuid4 = slidge.core.mixins.message_maker.uuid4 = uuid.uuid4
        slidge.util.sql.db.mam_nuke()

    @staticmethod
    def get_romeo_session() -> Session:
        return BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

    def get_private_muc(self, name="room-private", resources=()) -> MUC:
        muc = self.run_coro(
            self.get_romeo_session().bookmarks.by_jid(
                JID(f"{name}@aim.shakespeare.lit")
            )
        )
        for resource in resources:
            muc.user_resources.add(resource)
            n = self.next_sent()
            if n:
                assert n["subject"]
        return muc

    def get_participant(
        self, nickname="firstwitch", room="room=private", resources=("gajim",)
    ):
        muc = self.get_private_muc(resources=resources)
        participant: LegacyParticipant = self.run_coro(muc.get_participant(nickname))
        participant._LegacyParticipant__presence_sent = True
        return participant


@pytest.mark.usefixtures("avatar", "user_cls")
class TestMuc(Base):
    def test_disco_non_existing_room(self):
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="non-room@{self.xmpp.boundjid.bare}"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="error"
                from="non-room@aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                id="123">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_disco_group(self):
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="room-private@{self.xmpp.boundjid.bare}"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="room-private@{self.xmpp.boundjid.bare}"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference"
                          type="text"
                          name="Private Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent" />
                <feature var="muc_membersonly" />
                <feature var="muc_nonanonymous" />
                <feature var="muc_hidden" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="urn:xmpp:mam:2" />
                <feature var="urn:xmpp:mam:2#extended" />
                <feature var="vcard-temp" />
                <feature var="urn:xmpp:ping" />
                <feature var="urn:xmpp:occupant-id:0" />
                <feature var="urn:xmpp:message-moderate:0" />
                <x xmlns="jabber:x:data"
                   type="result">
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roominfo</value>
                  </field>
                  <field var="muc#maxhistoryfetch">
                    <value>100</value>
                  </field>
                  <field var="muc#roominfo_subjectmod"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roominfo_subject">
                    <value>Private Subject</value>
                  </field>
                  <field var="muc#roomconfig_persistentroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_changesubject"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_membersonly"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_whois"
                         type="list-single">
                    <value>anyone</value>
                  </field>
                  <field var="muc#roomconfig_publicroom"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_allowpm"
                         type="boolean">
                    <value>0</value>
                  </field>
                </x>
              </query>
            </iq>
            """,
        )

    def test_disco_room_avatar(self):
        self.next_sent()
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="coven@{self.xmpp.boundjid.bare}"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type="result"
                from="coven@aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference"
                          type="text"
                          name="The coven" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="urn:xmpp:mam:2" />
                <feature var="urn:xmpp:mam:2#extended" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="muc_persistent" />
                <feature var="vcard-temp" />
                <feature var="urn:xmpp:ping" />
                <feature var="muc_open" />
                <feature var="muc_semianonymous" />
                <feature var="muc_public" />
                <feature var="urn:xmpp:occupant-id:0" />
                <feature var="urn:xmpp:message-moderate:0" />
                <x xmlns="jabber:x:data"
                   type="result">
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roominfo</value>
                  </field>
                  <field var="muc#roomconfig_persistentroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_changesubject"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#maxhistoryfetch">
                    <value>100</value>
                  </field>
                  <field var="muc#roominfo_subjectmod"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="{http://modules.prosody.im/mod_vcard_muc}avatar#sha1">
                    <value>e6f9170123620949a6821e25ea2861d22b0dff66</value>
                  </field>
                  <field var="muc#roominfo_avatarhash"
                         type='text-multi'>
                    <value>e6f9170123620949a6821e25ea2861d22b0dff66</value>
                  </field>
                  <field var="muc#roomconfig_membersonly"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_whois"
                         type="list-single">
                    <value>moderators</value>
                  </field>
                  <field var="muc#roomconfig_publicroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_allowpm"
                         type="boolean">
                    <value>0</value>
                  </field>
                </x>
              </query>
            </iq>
            """
        )

    def test_disco_group_emoji_restricted(self):
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="room-private-emoji-restricted@{self.xmpp.boundjid.bare}"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="room-private-emoji-restricted@{self.xmpp.boundjid.bare}"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference"
                          type="text"
                          name="Private Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent" />
                <feature var="muc_membersonly" />
                <feature var="muc_nonanonymous" />
                <feature var="muc_hidden" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="urn:xmpp:mam:2" />
                <feature var="urn:xmpp:mam:2#extended" />
                <feature var="vcard-temp" />
                <feature var="urn:xmpp:ping" />
                <feature var="urn:xmpp:occupant-id:0" />
                <feature var="urn:xmpp:message-moderate:0" />
                <x xmlns="jabber:x:data"
                   type="result">
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roominfo</value>
                  </field>
                  <field var="muc#maxhistoryfetch">
                    <value>100</value>
                  </field>
                  <field var="muc#roominfo_subjectmod"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roominfo_subject">
                    <value>Private Subject</value>
                  </field>
                  <field var="muc#roomconfig_persistentroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_changesubject"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_membersonly"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_whois"
                         type="list-single">
                    <value>anyone</value>
                  </field>
                  <field var="muc#roomconfig_publicroom"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_allowpm"
                         type="boolean">
                    <value>0</value>
                  </field>
                </x>
                <x xmlns='jabber:x:data'
                   type='result'>
                  <field var='FORM_TYPE'
                         type='hidden'>
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
        self.run_coro(session.bookmarks.fill())
        self.recv(  # language=XML
            """
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="aim.shakespeare.lit"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#items' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                type="result"
                from="aim.shakespeare.lit"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns="http://jabber.org/protocol/disco#items">
                <item jid="room-private@aim.shakespeare.lit"
                      name="Private Room" />
                <item jid="room-public@aim.shakespeare.lit"
                      name="Public Room" />
                <item jid="coven@aim.shakespeare.lit"
                      name="The coven" />
                <item jid="room-private-emoji-restricted@aim.shakespeare.lit"
                      name="Private Room" />
              </query>
            </iq>
            """
        )

    def test_disco_channel(self):
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="room-public@{self.xmpp.boundjid.bare}"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                type="result"
                from="room-public@{self.xmpp.boundjid.bare}"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="conference"
                          type="text"
                          name="Public Room" />
                <feature var="http://jabber.org/protocol/muc" />
                <feature var="http://jabber.org/protocol/muc#stable_id" />
                <feature var="urn:xmpp:sid:0" />
                <feature var="http://jabber.org/protocol/muc#self-ping-optimization" />
                <feature var="muc_persistent" />
                <feature var="muc_public" />
                <feature var="muc_open" />
                <feature var="muc_semianonymous" />
                <feature var="urn:xmpp:mam:2" />
                <feature var="urn:xmpp:mam:2#extended" />
                <feature var="vcard-temp" />
                <feature var="urn:xmpp:ping" />
                <feature var="urn:xmpp:occupant-id:0" />
                <feature var="urn:xmpp:message-moderate:0" />
                <x xmlns="jabber:x:data"
                   type="result">
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roominfo</value>
                  </field>
                  <field var="muc#maxhistoryfetch">
                    <value>100</value>
                  </field>
                  <field var="muc#roominfo_subjectmod"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_persistentroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roominfo_subject">
                    <value>Public Subject</value>
                  </field>
                  <field var="muc#roomconfig_changesubject"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_membersonly"
                         type="boolean">
                    <value>0</value>
                  </field>
                  <field var="muc#roomconfig_whois"
                         type="list-single">
                    <value>moderators</value>
                  </field>
                  <field var="muc#roomconfig_publicroom"
                         type="boolean">
                    <value>1</value>
                  </field>
                  <field var="muc#roomconfig_allowpm"
                         type="boolean">
                    <value>0</value>
                  </field>
                </x>
              </query>
            </iq>
            """,
        )

    def test_disco_participant(self):
        self.recv(  # language=XML
            f"""
            <iq type="get"
                from="romeo@montague.lit/gajim"
                to="room-public@{self.xmpp.boundjid.bare}/firstwitch"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type="result"
                from="room-public@aim.shakespeare.lit/firstwitch"
                to="romeo@montague.lit/gajim"
                id="123">
              <query xmlns='http://jabber.org/protocol/disco#info'>
                <identity category="client"
                          type="pc"
                          name="firstwitch" />
                <feature var="http://jabber.org/protocol/chatstates" />
                <feature var="urn:xmpp:receipts" />
                <feature var="urn:xmpp:message-correct:0" />
                <feature var="urn:xmpp:chat-markers:0" />
                <feature var="jabber:x:oob" />
                <feature var="urn:xmpp:reactions:0" />
                <feature var="urn:xmpp:message-retract:0" />
                <feature var="urn:xmpp:reply:0" />
                <feature var="urn:ietf:params:xml:ns:vcard-4.0" />
              </query>
            </iq>
            """
        )

    def test_join_muc_no_nick(self):
        self.recv(  # language=XML
            """
            <presence from='romeo@montague.lit/gajim'
                      id='n13mt3l'
                      to='coven@aim.shakespeare.lit'>
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from='coven@aim.shakespeare.lit'
                      id='n13mt3l'
                      to='romeo@montague.lit/gajim'
                      type='error'>
              <error by='coven@aim.shakespeare.lit'
                     type='modify'>
                <jid-malformed xmlns='urn:ietf:params:xml:ns:xmpp-stanzas' />
              </error>
            </presence>
            """,
            use_values=False,  # the error element does not appear for some reason
        )

    def test_join_group(self):
        muc = self.get_private_muc("room-private")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        participant = self.run_coro(muc.get_participant("stan"))
        participant.send_text("Hey", when=now)
        muc.subject_date = now
        self.recv(  # language=XML
            """
            <presence from='romeo@montague.lit/gajim'
                      id='n13mt3l'
                      to='room-private@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/stan"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <priority>0</priority>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from='room-private@aim.shakespeare.lit/firstwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='owner'
                      role='moderator'
                      jid="firstwitch@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="firstwitch@aim.shakespeare.lit/slidge" />
            </presence>
            """,
        )
        self.send(  # language=XML
            """
            <presence from='room-private@aim.shakespeare.lit/secondwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='admin'
                      role='moderator'
                      jid="secondwitch@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="secondwitch@aim.shakespeare.lit/slidge" />
            </presence>
            """,
        )
        self.send(  # language=XML
            """
            <presence id='n13mt3l'
                      from='room-private@aim.shakespeare.lit/thirdwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member'
                      role='participant'
                      jid='romeo@montague.lit/gajim' />
                <status code='100' />
                <status code='110' />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """,
        )
        now_fmt = now.isoformat().replace("+00:00", "Z")
        self.send(  # language=XML
            f"""
            <message type="groupchat"
                     from="room-private@aim.shakespeare.lit/stan"
                     to="romeo@montague.lit/gajim">
              <body>Hey</body>
              <delay xmlns="urn:xmpp:delay"
                     stamp="{now_fmt}" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item role="participant"
                      affiliation="member"
                      jid="uuid@aim.shakespeare.lit" />
              </x>
            </message>
            """,
        )
        self.send(  # language=XML
            f"""
            <message type="groupchat"
                     to="romeo@montague.lit/gajim"
                     from="room-private@aim.shakespeare.lit/unknown">
              <delay xmlns="urn:xmpp:delay"
                     stamp="{now_fmt}"
                     from="room-private@aim.shakespeare.lit" />
              <subject>Private Subject</subject>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
            </message>
            """
        )
        # empty avatar
        assert self.next_sent()["from"] == "room-private@aim.shakespeare.lit"

    def test_join_channel(self):
        self.recv(  # language=XML
            """
            <presence from='romeo@montague.lit/gajim'
                      id='n13mt3l'
                      to='room-public@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from='room-public@aim.shakespeare.lit/firstwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='owner'
                      role='moderator' />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """,
        )
        self.send(  # language=XML
            """
            <presence from='room-public@aim.shakespeare.lit/secondwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='admin'
                      role='moderator' />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """,
        )
        self.send(  # language=XML
            """
            <presence id='n13mt3l'
                      from='room-public@aim.shakespeare.lit/thirdwitch'
                      to='romeo@montague.lit/gajim'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member'
                      role='participant' />
                <status code='110' />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """,
        )

    def test_self_ping_disconnected(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                id='s2c1'
                type='get'
                to='room-private@aim.shakespeare.lit/SlidgeUser'>
              <ping xmlns='urn:xmpp:ping' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from='room-private@aim.shakespeare.lit/SlidgeUser'
                id='s2c1'
                type='error'
                to='romeo@montague.lit/gajim'>
              <error type="cancel"
                     by="room-private@aim.shakespeare.lit">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_resource_not_joined(self):
        session = self.get_romeo_session()
        self.recv(  # language=XML
            """
            <message from='romeo@montague.lit/gajim'
                     type='groupchat'
                     to='room-private@aim.shakespeare.lit'>
              <body>am I here?</body>
            </message>
            """
        )
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     from="room-private@aim.shakespeare.lit"
                     type="error"
                     to="romeo@montague.lit/gajim">
              <error type="modify">
                <not-acceptable xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">You are not connected to this chat</text>
              </error>
            </message>
            """
        )
        self.send(  # language=XML
            """
            <presence xmlns="jabber:component:accept"
                      to="romeo@montague.lit/gajim"
                      from="room-private@aim.shakespeare.lit/thirdwitch">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="none"
                      role="none" />
                <status code="333" />
                <status code="110" />
              </x>
              <priority>0</priority>
            </presence>
            """
        )
        assert self.next_sent() is None

    def test_self_ping_connected(self):
        self.get_private_muc(resources={"gajim"})
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                id='s2c1'
                type='get'
                to='room-private@aim.shakespeare.lit/SlidgeUser'>
              <ping xmlns='urn:xmpp:ping' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                from="room-private@aim.shakespeare.lit/SlidgeUser"
                id="s2c1"
                type="result"
                to="romeo@montague.lit/gajim"></iq>
            """,
            use_values=False,
        )

    # def test_origin_id(self):
    #     """
    #     this test is broken because of slixtest magic, but the behavior is actually good
    #     in real conditions
    #     """
    #     muc = self.get_private_muc()
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
    #             <stanza-id xmlns="urn:xmpp:sid:0" id="id" by="room-private@aim.shakespeare.lit" />
    #         </message>
    #         """,
    #     )

    def test_msg_from_xmpp(self):
        muc = self.get_private_muc(resources={"gajim", "movim"})
        # muc.user_resources = ["gajim", "movim"]
        self.recv(  # language=XML
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
              <body>BODY</body>
              <origin-id xmlns="urn:xmpp:sid:0"
                         id="xmpp-id" />
            </message>
            """
        )
        for r in muc.user_resources:
            self.send(  # language=XML
                f"""
            <message from='{muc.jid}/{muc.user_nick}'
                     id='origin'
                     to='romeo@montague.lit/{r}'
                     type='groupchat'>
              <body>BODY</body>
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="id"
                         by="room-private@aim.shakespeare.lit" />
              <origin-id xmlns="urn:xmpp:sid:0"
                         id="xmpp-id" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
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
        self.recv(  # language=XML
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
              <body>{fallback}{stripped_body}</body>
              <reply to='room-private@aim.shakespeare.lit/Anna'
                     id='message-id1'
                     xmlns='urn:xmpp:reply:0' />
              <fallback xmlns='urn:xmpp:fallback:0'
                        for='urn:xmpp:reply:0'>
                <body start="0"
                      end="{len(fallback)}" />
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
        participant = self.get_participant()
        muc = participant.muc
        participant.send_text("the body", legacy_msg_id="legacy-XXX")
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <body>the body</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_correct_from_legacy(self):
        participant = self.get_participant()

        participant.send_text("body", "legacy-1")
        msg = self.next_sent()
        assert msg["body"] == "body"
        assert msg["id"] == msg["stanza_id"]["id"] == "1"

        participant.correct("legacy-1", "new")
        msg = self.next_sent()
        assert msg["body"] == "new"
        assert msg["replace"]["id"] == "1"
        assert msg["id"] == ""
        assert msg["stanza_id"]["id"] != ""

        participant.correct(
            "legacy-1", "newnew", correction_event_id="legacy-correction"
        )
        msg = self.next_sent()
        assert msg["body"] == "newnew"
        assert msg["id"] == "correction"
        assert msg["stanza_id"]["id"] == "correction"
        assert msg["replace"]["id"] == "1"

        participant.correct("legacy-willbeconverted", "new content")
        msg = self.next_sent()
        assert msg["replace"]["id"] == "willbeconverted"
        assert msg["body"] == "new content"
        assert msg["id"] == ""
        assert msg["stanza_id"]["id"] != ""

        participant.correct(
            "legacy-willbeconverted",
            "new content",
            correction_event_id="legacy-correction_id",
        )
        msg = self.next_sent()
        assert msg["replace"]["id"] == "willbeconverted"
        assert msg["body"] == "new content"
        assert msg["id"] == "correction_id"
        assert msg["stanza_id"]["id"] == "correction_id"

    def test_msg_reply_self_from_legacy(self):
        Session.SENT_TEXT = []
        participant = self.get_participant()
        muc = participant.muc
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to=MessageReference(legacy_id="legacy-REPLY-TO", author=participant),
        )
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <body>the body</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <reply xmlns="urn:xmpp:reply:0"
                     id="REPLY-TO"
                     to="room-private@aim.shakespeare.lit/firstwitch" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_msg_reply_to_user(self):
        Session.SENT_TEXT = []
        participant = self.get_participant()
        muc = participant.muc
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to=MessageReference(
                legacy_id="legacy-REPLY-TO", author=muc.session.user
            ),
        )
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <body>the body</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <reply xmlns="urn:xmpp:reply:0"
                     id="REPLY-TO"
                     to="room-private@aim.shakespeare.lit/{muc.user_nick}" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_msg_reply_from_legacy(self):
        Session.SENT_TEXT = []
        participant = self.get_participant()
        muc = participant.muc
        second_witch = self.get_participant("secondwitch")
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to=MessageReference(
                author=second_witch,
                legacy_id="legacy-REPLY-TO",
            ),
        )
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <body>the body</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <reply xmlns="urn:xmpp:reply:0"
                     id="REPLY-TO"
                     to="room-private@aim.shakespeare.lit/secondwitch" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_msg_reply_from_legacy_fallback(self):
        Session.SENT_TEXT = []
        participant = self.get_participant()
        muc = participant.muc
        second_witch = self.get_participant("secondwitch")
        participant.send_text(
            "the body",
            legacy_msg_id="legacy-XXX",
            reply_to=MessageReference(
                legacy_id="legacy-REPLY-TO", author=second_witch, body="Blabla"
            ),
        )
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     id='XXX'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <body>&gt; secondwitch:\n&gt; Blabla\nthe body</body>
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <reply xmlns="urn:xmpp:reply:0"
                     id="REPLY-TO"
                     to="room-private@aim.shakespeare.lit/secondwitch" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="XXX"
                         by="room-private@aim.shakespeare.lit" />
              <fallback xmlns="urn:xmpp:fallback:0"
                        for="urn:xmpp:reply:0">
                <body start="0"
                      end="24" />
              </fallback>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_react_from_xmpp(self):
        muc = self.get_private_muc(resources=["gajim", "movim"])
        self.recv(  # language=XML
            f"""
            <message from='romeo@montague.lit/gajim'
                     id='origin'
                     to='{muc.jid}'
                     type='groupchat'>
              <reactions id='SOME-ID'
                         xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
              </reactions>
            </message>
            """
        )
        for r in muc.user_resources:
            self.send(  # language=XML
                f"""
            <message from='{muc.jid}/{muc.user_nick}'
                     id='origin'
                     to='romeo@montague.lit/{r}'
                     type='groupchat'>
              <reactions id='SOME-ID'
                         xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
              </reactions>
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
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
        participant = self.get_participant()
        muc = participant.muc
        participant.react(legacy_msg_id="legacy-XXX", emojis="👋")
        self.send(  # language=XML
            f"""
            <message from='{muc.jid}/firstwitch'
                     to='romeo@montague.lit/gajim'
                     type='groupchat'>
              <store xmlns="urn:xmpp:hints" />
              <reactions id='XXX'
                         xmlns='urn:xmpp:reactions:0'>
                <reaction>👋</reaction>
              </reactions>
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """,
            use_values=False,
        )

    def test_mam_bare_jid(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                from="aim.shakespeare.lit"
                type="error"
                id="iq-id1"
                to="romeo@montague.lit/gajim">
              <error type="cancel">
                <undefined-condition xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">No MAM on the component itself, use a JID with a resource</text>
              </error>
            </iq>
            """
        )
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                from="aim.shakespeare.lit"
                type="error"
                id="iq-id1"
                to="romeo@montague.lit/gajim">
              <error type="cancel">
                <undefined-condition xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">No MAM on the component itself, use a JID with a resource</text>
              </error>
            </iq>
            """
        )

    def test_mam_form_fields(self):
        muc = self.get_private_muc()
        # muc.user_resources.add("gajim")
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <query xmlns='urn:xmpp:mam:2'>
                <x xmlns='jabber:x:data'
                   type='form'>
                  <field type='hidden'
                         var='FORM_TYPE'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field type='jid-single'
                         var='with' />
                  <field type='text-single'
                         var='start' />
                  <field type='text-single'
                         var='end' />
                  <field type='text-single'
                         var='before-id' />
                  <field type='text-single'
                         var='after-id' />
                  <field type='list-multi'
                         var='ids'>
                    <validate xmlns="http://jabber.org/protocol/xdata-validate"
                              datatype="xs:string">
                      <open />
                    </validate>
                  </field>
                  <field type='boolean'
                         var='include-groupchat' />
                </x>
              </query>
            </iq>
            """
        )

    def test_mam_all(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id' />
            </iq>
            """
        )
        for i in range(10):
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns='http://jabber.org/protocol/muc#user'>
                      <item affiliation='member'
                            jid='uuid@aim.shakespeare.lit'
                            role='participant' />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin stable="false"
                   xmlns='urn:xmpp:mam:2'
                   complete='true'>
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
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
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
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2'
                   stable="false">
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
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
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
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2'
                   stable="false">
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>6</first>
                  <last>7</last>
                  <count>2</count>
                </set>
              </fin>
            </iq>
            """
        )

    def test_mam_page_after_last(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>70</max>
                  <after>9</after>
                </set>
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="room-private@aim.shakespeare.lit"
                type="result"
                id="iq-id1"
                to="romeo@montague.lit/gajim">
              <fin xmlns="urn:xmpp:mam:2"
                   stable="false"
                   complete="true">
                <set xmlns="http://jabber.org/protocol/rsm">
                  <count>0</count>
                </set>
              </fin>
            </iq>
            """,
            use_values=False,
        )

    def test_mam_page_after_not_found(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
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
        self.send(  # language=XML
            """
            <iq type='error'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <error type='cancel'>
                <item-not-found xmlns='urn:ietf:params:xml:ns:xmpp-stanzas' />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Message 12 not found</text>
              </error>
            </iq>
            """,
            use_values=False,
        )

    def test_last_page(self):
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>3</max>
                  <before />
                </set>
              </query>
            </iq>
            """
        )
        for i in range(7, 10):
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2'
                   stable="false"
                   complete='true'>
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
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='start'>
                    <value>2000-01-01T03:00:00Z</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>3</max>
                  <after>5</after>
                </set>
                <flip-page />
              </query>
            </iq>
            """
        )
        for i in range(9, 6, -1):
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2'
                   stable="false">
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
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <flip-page />
              </query>
            </iq>
            """
        )
        for i in range(9, -1, -1):
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin xmlns='urn:xmpp:mam:2'
                   stable="false"
                   complete="true">
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
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <metadata xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <metadata xmlns='urn:xmpp:mam:2'>
                <start id='0'
                       timestamp='2000-01-01T00:00:00Z' />
                <end id='9'
                     timestamp='2000-01-01T09:00:00Z' />
              </metadata>
            </iq>
            """
        )

    def test_mam_metadata_empty(self):
        muc = self.get_private_muc()
        muc._LegacyMUC__history_filled = True
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <metadata xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <metadata xmlns='urn:xmpp:mam:2' />
            </iq>
            """
        )

    def test_mam_with(self):
        for i in range(10):
            self.recv(  # language=XML
                f"""
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
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
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
            self.send(  # language=XML
                f"""
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin stable="false"
                   xmlns='urn:xmpp:mam:2'
                   complete='true'>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>{i}</first>
                  <last>{i}</last>
                  <count>1</count>
                </set>
              </fin>
            </iq>
            """
            )

    def test_mam_specific_id(self):
        self.recv(  # language=XML
            f"""
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='ids'>
                    <value>2</value>
                    <value>4</value>
                  </field>
                </x>
              </query>
            </iq>
            """
        )
        for i in 2, 4:
            self.send(  # language=XML
                f"""
            <message to='romeo@montague.lit/gajim'
                     from='room-private@aim.shakespeare.lit'>
              <result xmlns='urn:xmpp:mam:2'
                      queryid='query-id'
                      id='{i}'>
                <forwarded xmlns='urn:xmpp:forward:0'>
                  <delay xmlns='urn:xmpp:delay'
                         stamp='2000-01-01T{i:02d}:00:00Z' />
                  <message xmlns='jabber:client'
                           from="room-private@aim.shakespeare.lit/history-man-{i}"
                           type='groupchat'
                           id='{i}'>
                    <body>Body #{i}</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="{i}"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="uuid" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="uuid@aim.shakespeare.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
            )
        self.send(  # language=XML
            f"""
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <fin stable="false"
                   xmlns='urn:xmpp:mam:2'
                   complete='true'>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <first>2</first>
                  <last>4</last>
                  <count>2</count>
                </set>
              </fin>
            </iq>
            """
        )
        self.recv(  # language=XML
            f"""
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                  <field var='ids'>
                    <value>2</value>
                    <value>14</value>
                  </field>
                </x>
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq xmlns="jabber:component:accept"
                from="room-private@aim.shakespeare.lit"
                type="error"
                id="iq-id1"
                to="romeo@montague.lit/gajim">
              <error type="cancel">
                <item-not-found xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">One of the requested messages IDs could not be found with the given constraints.</text>
              </error>
            </iq>
            """
        )

    def test_mam_from_user_carbon(self):
        muc = self.get_private_muc(resources=["gajim"])
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        user_participant: Participant = self.run_coro(muc.get_user_participant())
        user_participant.send_text("blabla", "legacy-666", when=now)
        now_fmt = now.isoformat().replace("+00:00", "Z")
        self.send(  # language=XML
            f"""
            <message id="666"
                     xmlns="jabber:component:accept"
                     type="groupchat"
                     from="room-private@aim.shakespeare.lit/thirdwitch"
                     to="romeo@montague.lit/gajim">
              <body>blabla</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <stanza-id id="666"
                         xmlns="urn:xmpp:sid:0"
                         by="room-private@aim.shakespeare.lit" />
              <delay xmlns="urn:xmpp:delay"
                     stamp="{now_fmt}"
                     from="aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </message>
            """,
            use_values=False,  # necessary because the third has origin-id
        )
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='set'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='urn:xmpp:mam:2'
                     queryid='query-id'>
                <x xmlns='jabber:x:data'
                   type='submit'>
                  <field var='FORM_TYPE'
                         type='hidden'>
                    <value>urn:xmpp:mam:2</value>
                  </field>
                </x>
                <set xmlns='http://jabber.org/protocol/rsm'>
                  <max>1</max>
                  <before />
                </set>
                <flip-page />
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <message xmlns="jabber:component:accept"
                     to="romeo@montague.lit/gajim"
                     from="room-private@aim.shakespeare.lit"
                     type="normal">
              <result xmlns="urn:xmpp:mam:2"
                      queryid="query-id"
                      id="666">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <delay xmlns="urn:xmpp:delay"
                         stamp="{now_fmt}" />
                  <message xmlns="jabber:client"
                           type="groupchat"
                           from="room-private@aim.shakespeare.lit/thirdwitch"
                           id="666">
                    <body>blabla</body>
                    <stanza-id xmlns="urn:xmpp:sid:0"
                               id="666"
                               by="room-private@aim.shakespeare.lit" />
                    <occupant-id xmlns="urn:xmpp:occupant-id:0"
                                 id="slidge-user" />
                    <x xmlns="http://jabber.org/protocol/muc#user">
                      <item role="participant"
                            affiliation="member"
                            jid="romeo@montague.lit" />
                    </x>
                  </message>
                </forwarded>
              </result>
            </message>
            """
        )

    def test_mam_echo(self):
        muc = self.get_private_muc(resources=["gajim"])
        self.recv(  # language=XML
            """
            <message from="romeo@montague.lit/gajim"
                     to="room-private@aim.shakespeare.lit"
                     type="groupchat">
              <body>HOY</body>
            </message>
            """
        )
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     from="room-private@aim.shakespeare.lit/thirdwitch"
                     to="romeo@montague.lit/gajim"
                     type="groupchat">
              <body>HOY</body>
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="id"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </message>
            """
        )
        archived = list(muc.archive.get_all())[-1]
        assert archived.id == "id"

    def test_get_members(self):
        muc = self.get_private_muc()
        # muc.user_resources.add("gajim")
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item affiliation='admin' />
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item nick="secondwitch"
                      affiliation="admin"
                      role="moderator"
                      jid="secondwitch@aim.shakespeare.lit" />
              </query>
            </iq>
            """
        )
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item affiliation='owner' />
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <query xmlns="http://jabber.org/protocol/muc#admin">
                <item nick="firstwitch"
                      affiliation="owner"
                      role="moderator"
                      jid="firstwitch@aim.shakespeare.lit" />
              </query>
            </iq>
            """
        )
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='iq-id1'
                to='room-private@aim.shakespeare.lit'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item affiliation='member' />
              </query>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type='result'
                id='iq-id1'
                from='room-private@aim.shakespeare.lit'
                to='romeo@montague.lit/gajim'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item nick="thirdwitch"
                      affiliation="member"
                      role="participant"
                      jid="romeo@montague.lit" />
              </query>
            </iq>
            """
        )

    def test_room_avatar(self):
        v = b64encode(self.avatar_path.read_bytes()).decode()
        self.run_coro(self.get_romeo_session().bookmarks.fill())
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='get1'
                to='room-private@aim.shakespeare.lit'>
              <vCard xmlns='vcard-temp' />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq from="room-private@aim.shakespeare.lit"
                type="result"
                to="romeo@montague.lit/gajim"
                id="get1">
              <vCard xmlns="vcard-temp" />
            </iq>
            """,
            use_values=False,
        )
        self.recv(  # language=XML
            """
            <iq from='romeo@montague.lit/gajim'
                type='get'
                id='get1'
                to='coven@aim.shakespeare.lit'>
              <vCard xmlns='vcard-temp' />
            </iq>
            """
        )
        self.send(  # language=XML
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
        muc = self.get_private_muc("coven")
        self.recv(  # language=XML
            """
            <presence from='romeo@montague.lit/gajim'
                      id='n13mt3l'
                      to='coven@aim.shakespeare.lit/thirdwitch'>
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        for _ in range(len(muc._participants_by_nicknames)):
            pres = self.next_sent()
            assert isinstance(pres, Presence)
        subject = self.next_sent()
        assert isinstance(subject, Message)
        self.send(  # language=XML
            f"""
            <presence from='coven@aim.shakespeare.lit'
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
        self.recv(  # language=XML
            """
            <message from='romeo@montague.lit/gajim'
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
        self.recv(  # language=XML
            """
            <message from='romeo@montague.lit/gajim'
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

    def test_recv_non_kickable_error(self):
        muc = self.get_private_muc("coven")
        muc.user_resources.add("gajim")
        self.recv(  # language=XML
            """
            <message from='romeo@montague.lit/gajim'
                     id='n13mt3l'
                     to='coven@aim.shakespeare.lit/thirdwitch'
                     type="error">
              <error type="cancel" />
            </message>
            """
        )
        assert muc.user_resources.pop() == "gajim"
        assert self.next_sent() is None

    def test_recv_error_non_existing_muc(self):
        self.recv(  # language=XML
            """
            <message from='romeo@montague.lit/gajim'
                     id='n13mt3l'
                     to='non-existing@aim.shakespeare.lit/thirdwitch'
                     type="error">
              <error type="cancel">
                <gone xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
              </error>
            </message>
            """
        )
        assert self.next_sent() is None

    def test_archive_cleanup(self):
        from slidge import global_config

        orig = global_config.MAM_MAX_DAYS
        global_config.MAM_MAX_DAYS = 1

        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(tz=datetime.timezone.utc)
        m["body"] = "something"

        a = MessageArchive("blop", self.user)
        slidge.util.sql.db.mam_cleanup()
        assert len(list(a.get_all())) == 0
        a.add(m)
        slidge.util.sql.db.mam_cleanup()
        assert len(list(a.get_all())) == 1

        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(days=2)
        m["body"] = "something"

        a = MessageArchive("blip", self.user)
        slidge.util.sql.db.mam_cleanup()
        assert len(list(a.get_all())) == 0
        a.add(m)
        slidge.util.sql.db.mam_cleanup()
        assert len(list(a.get_all())) == 0

        m = Message()
        m["delay"]["stamp"] = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(days=0.5)
        m["body"] = "something"
        a.add(m)
        assert len(list(a.get_all())) == 1
        a.add(m)
        assert len(list(a.get_all())) == 1
        slidge.util.sql.db.mam_cleanup()
        assert len(list(a.get_all())) == 1

        global_config.MAM_MAX_DAYS = orig

    def test_moderate_by_room(self):
        muc = self.get_private_muc("room", ["gajim"])
        p = muc.get_system_participant()
        p.moderate("legacy-666", "reason™")
        self.send(  # language=XML
            """
            <message type="groupchat"
                     from='room@aim.shakespeare.lit'
                     to="romeo@montague.lit/gajim">
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room@aim.shakespeare.lit" />
              <apply-to id="666"
                        xmlns="urn:xmpp:fasten:0">
                <moderated by='room@aim.shakespeare.lit'
                           xmlns='urn:xmpp:message-moderate:0'>
                  <retract xmlns='urn:xmpp:message-retract:0' />
                  <reason>reason™</reason>
                </moderated>
              </apply-to>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="room" />
            </message>
            """
        )

    def test_moderate_by_moderator(self):
        # muc = self.get_private_muc(resources=["gajim"])
        p = self.get_participant(resources=["gajim"])
        p.moderate("legacy-666", "reason™")
        self.send(  # language=XML
            f"""
            <message type="groupchat"
                     from='room-private@aim.shakespeare.lit'
                     to="romeo@montague.lit/gajim">
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <apply-to id="666"
                        xmlns="urn:xmpp:fasten:0">
                <moderated by='room-private@aim.shakespeare.lit/{p.nickname}'
                           xmlns='urn:xmpp:message-moderate:0'>
                  <retract xmlns='urn:xmpp:message-retract:0' />
                  <reason>reason™</reason>
                </moderated>
              </apply-to>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """
        )

    def test_participant_avatar(self):
        self.test_join_group()
        v = b64encode(self.avatar_path.read_bytes()).decode()
        session = self.get_romeo_session()
        self.run_coro(session.bookmarks.fill())
        muc = self.get_private_muc()
        muc._LegacyMUC__participants_filled = True
        contact = self.run_coro(session.contacts.by_legacy_id(333))
        contact.avatar = self.avatar_path
        self.run_coro(contact._set_avatar_task)
        self.run_coro(muc.get_participant_by_contact(contact))
        pres = self.next_sent()
        assert pres["vcard_temp_update"]["photo"] == self.avatar_original_sha1
        self.recv(  # language=XML
            f"""
            <iq from="romeo@montague.lit/gajim"
                to="{muc.jid}/not-in-roster"
                type="get"
                xml:lang="en">
              <vCard xmlns="vcard-temp" />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq xmlns="jabber:component:accept"
                from="room-private@aim.shakespeare.lit/not-in-roster"
                to="romeo@montague.lit/gajim"
                type="result"
                xml:lang="en"
                id="1">
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

    def test_presence_propagation(self):
        participants_before = self.__get_participants()
        contact = participants_before[0].contact
        last_seen = datetime.datetime.now(tz=datetime.timezone.utc)
        contact.is_friend = True
        contact.away(last_seen=last_seen, status="blabla")
        dt = xep_0082.format_datetime(last_seen)

        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/firstwitch"
                      to="romeo@montague.lit/movim">
              <show>away</show>
              <status>blabla -- Last seen {last_seen:%A %H:%M GMT}</status>
              <idle xmlns="urn:xmpp:idle:1"
                    since="{dt}" />
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="owner"
                      role="moderator"
                      jid="firstwitch@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="firstwitch@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      from="firstwitch@aim.shakespeare.lit/slidge"
                      to="romeo@montague.lit">
              <show>away</show>
              <status>blabla -- Last seen {last_seen:%A %H:%M GMT}</status>
              <idle xmlns="urn:xmpp:idle:1"
                    since="{dt}" />
              <c xmlns="http://jabber.org/protocol/caps"
                 node="http://slixmpp.com/ver/{slixmpp.__version__}"
                 hash="sha-1"
                 ver="UQAWii/9nNwlRpOcbTSGXm6r+aY=" />
            </presence>
            """
        )
        assert self.next_sent() is None

    def test_add_to_bookmarks(self):
        muc = self.get_private_muc()
        self.xmpp["xep_0356"].granted_privileges["montague.lit"].iq[
            "http://jabber.org/protocol/pubsub"
        ] = "both"
        self.xmpp.loop.create_task(muc.add_to_bookmarks(auto_join=True, preserve=False))
        import slixmpp.plugins.xep_0356.privilege

        o = slixmpp.plugins.xep_0356.privilege.uuid.uuid4
        slixmpp.plugins.xep_0356.privilege.uuid.uuid4 = lambda: "0"
        self.send(  # language=XML
            """
            <iq from="aim.shakespeare.lit"
                to="romeo@montague.lit"
                xmlns="jabber:component:accept"
                type="set"
                id="0">
              <privileged_iq xmlns='urn:xmpp:privilege:2'>
                <iq xmlns="jabber:client"
                    from='romeo@montague.lit'
                    to='romeo@montague.lit'
                    type='set'
                    id='0'>
                  <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                    <publish node='urn:xmpp:bookmarks:1'>
                      <item id='room-private@aim.shakespeare.lit'>
                        <conference xmlns='urn:xmpp:bookmarks:1'
                                    autojoin='true'>
                          <nick>thirdwitch</nick>
                        </conference>
                      </item>
                    </publish>
                    <publish-options>
                      <x xmlns='jabber:x:data'
                         type='submit'>
                        <field var='FORM_TYPE'
                               type='hidden'>
                          <value>http://jabber.org/protocol/pubsub#publish-options</value>
                        </field>
                        <field var='pubsub#persist_items'>
                          <value>1</value>
                        </field>
                        <field var='pubsub#max_items'>
                          <value>max</value>
                        </field>
                        <field var='pubsub#send_last_published_item'>
                          <value>never</value>
                        </field>
                        <field var='pubsub#access_model'>
                          <value>whitelist</value>
                        </field>
                      </x>
                    </publish-options>
                  </pubsub>
                </iq>
              </privileged_iq>
            </iq>
            """,
            use_values=False,
        )
        slixmpp.plugins.xep_0356.privilege.uuid.uuid4 = o

    def __get_participants(self):
        muc = self.get_private_muc(resources=["movim"])
        # muc.user_resources.add("movim")
        self.run_coro(muc.session.contacts.fill())
        participants_before: list[Participant] = self.run_coro(muc.get_participants())
        for p in participants_before:
            p._LegacyParticipant__presence_sent = True
        return participants_before

    def __test_rename_common(self, old_nick, participants_before):
        muc = self.get_private_muc()
        p = participants_before[0]
        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      type="unavailable"
                      from="room-private@aim.shakespeare.lit/{old_nick}"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="{p.affiliation}"
                      role="{p.role}"
                      jid="{p.contact.jid}"
                      nick="new-nick" />
                <status code="303" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="{p.contact.jid}" />
            </presence>
            """
        )
        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/new-nick"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="{p.affiliation}"
                      role="{p.role}"
                      jid="{p.contact.jid}" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="{p.contact.jid}" />
            </presence>
            """
        )

        participants_after = self.run_coro(muc.get_participants())
        assert len(participants_after) == len(participants_before)
        assert self.next_sent() is None

    def test_rename_participant_from_participant(self):
        participants_before = self.__get_participants()
        p = participants_before[0]
        old_nick = p.nickname
        p.nickname = "new-nick"
        self.__test_rename_common(old_nick, participants_before)

    def test_rename_participant_from_muc(self):
        participants_before = self.__get_participants()
        p = participants_before[0]
        old_nick = p.nickname
        p.muc.rename_participant(old_nick, "new-nick")
        self.__test_rename_common(old_nick, participants_before)

    def test_rename_from_contact(self):
        participants_before = self.__get_participants()
        p = participants_before[0]
        old_nick = p.nickname
        p.contact.name = "new-nick"
        self.__test_rename_common(old_nick, participants_before)

    def test_rename_from_contact_with_forbidden_char(self):
        participants_before = self.__get_participants()
        p = participants_before[0]
        old_nick = p.nickname
        p.contact.name = "a forbidden emoji 🎉"

        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      type="unavailable"
                      from="room-private@aim.shakespeare.lit/{old_nick}"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="{p.affiliation}"
                      role="{p.role}"
                      jid="{p.contact.jid}"
                      nick="a forbidden emoji -fr59q" />
                <status code="303" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="{p.contact.jid}" />
            </presence>
            """
        )
        self.send(  # language=XML
            f"""
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/a forbidden emoji -fr59q"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="{p.affiliation}"
                      role="{p.role}"
                      jid="{p.contact.jid}" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="{p.contact.jid}" />
              <nick xmlns="http://jabber.org/protocol/nick">a forbidden emoji 🎉</nick>
            </presence>
            """
        )
        muc = self.get_private_muc()
        participants_after = self.run_coro(muc.get_participants())
        assert len(participants_after) == len(participants_before)
        assert self.next_sent() is None

    def test_non_anonymous_participants_with_same_nickname(self):
        muc = self.get_private_muc(resources=["movim"])
        participants = self.__get_participants()
        for p in participants:
            if p.contact.name == "firstwitch":
                real_witch = p
                break
        else:
            raise AssertionError
        assert real_witch is self.run_coro(muc.get_participant("firstwitch"))
        p = self.run_coro(muc.get_participant_by_legacy_id(666))
        assert real_witch is self.run_coro(muc.get_participant("firstwitch"))
        p.send_text("Je suis un canaillou")
        self.send(  # language=XML
            """
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/firstwitch (imposter)"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant"
                      jid="imposter@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="imposter@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     type="groupchat"
                     from="room-private@aim.shakespeare.lit/firstwitch (imposter)"
                     to="romeo@montague.lit/movim">
              <body>Je suis un canaillou</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="imposter@aim.shakespeare.lit/slidge" />
            </message>
            """
        )
        assert self.next_sent() is None
        assert real_witch is self.run_coro(muc.get_participant("firstwitch"))
        p = self.run_coro(muc.get_participant_by_legacy_id(667))
        assert real_witch is self.run_coro(muc.get_participant("firstwitch"))
        p.send_text("Je suis un canaillou")
        self.send(  # language=XML
            """
            <presence xmlns="jabber:component:accept"
                      from="room-private@aim.shakespeare.lit/firstwitch (imposter2)"
                      to="romeo@montague.lit/movim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant"
                      jid="imposter2@aim.shakespeare.lit/slidge" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="imposter2@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <message xmlns="jabber:component:accept"
                     type="groupchat"
                     from="room-private@aim.shakespeare.lit/firstwitch (imposter2)"
                     to="romeo@montague.lit/movim">
              <body>Je suis un canaillou</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="imposter2@aim.shakespeare.lit/slidge" />
            </message>
            """
        )
        assert self.next_sent() is None

    def test_illegal_nickname(self):
        self.recv(  # language=XML
            """
            <presence from='romeo@montague.lit/cheogram'
                      id='forbidden'
                      to='weird@aim.shakespeare.lit/blup'>
              <x xmlns='http://jabber.org/protocol/muc' />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from="weird@aim.shakespeare.lit/firstwitch"
                      to="romeo@montague.lit/cheogram">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from="weird@aim.shakespeare.lit/secondwitch"
                      to="romeo@montague.lit/cheogram">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="admin"
                      role="moderator" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from="weird@aim.shakespeare.lit/weirdguy-9u25g"
                      to="romeo@montague.lit/cheogram">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="owner"
                      role="moderator" />
              </x>
              <nick xmlns="http://jabber.org/protocol/nick">weirdguy🎉</nick>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="weirdguy🎉@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        self.send(  # language=XML
            """
            <presence from="weird@aim.shakespeare.lit/thirdwitch"
                      id="forbidden"
                      to="romeo@montague.lit/cheogram">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
                <status code="210" />
                <status code="110" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """
        )

    def test_illegal_nickname_quoted_fallback(self):
        op = self.get_participant("weirdguy🎉")
        replier = self.get_participant()
        replier.send_text(
            "reply",
            reply_to=MessageReference("some-id", op, "quoted text"),
        )
        self.send(  # language=XML
            """
            <message type="groupchat"
                     from="room-private@aim.shakespeare.lit/firstwitch"
                     to="romeo@montague.lit/gajim">
              <body>&gt; weirdguy🎉:\n&gt; quoted text\nreply</body>
              <active xmlns="http://jabber.org/protocol/chatstates" />
              <markable xmlns="urn:xmpp:chat-markers:0" />
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <reply xmlns="urn:xmpp:reply:0"
                     to="room-private@aim.shakespeare.lit/weirdguy-9u25g" />
              <fallback xmlns="urn:xmpp:fallback:0"
                        for="urn:xmpp:reply:0">
                <body start="0"
                      end="27" />
              </fallback>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </message>
            """
        )

    def test_group_rename(self):
        group = self.get_private_muc(resources=("gajim",))
        group.name = "prout"
        self.send(  # language=XML
            """
            <message type="groupchat"
                     from="room-private@aim.shakespeare.lit"
                     to="romeo@montague.lit/gajim">
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <x xmlns="http://jabber.org/protocol/muc#user">
                <status code="104" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="room" />
            </message>
            """,
            use_values=False,
        )
        self.send(None)


class TestRoleAffiliation(Base):
    def test_role_change(self):
        part = self.get_participant("a-new-one")
        part.role = "visitor"
        self.send(  # language=XML
            f"""
            <presence from="room-private@aim.shakespeare.lit/a-new-one"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="{part.affiliation}"
                      role="visitor" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        self.send(None)
        part.role = "visitor"
        self.send(None)

    def test_affiliation_change(self):
        part = self.get_participant("a-new-one")
        part.affiliation = "admin"
        self.send(  # language=XML
            f"""
            <presence from="room-private@aim.shakespeare.lit/a-new-one"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="admin"
                      role="{part.role}" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        self.send(None)
        part.affiliation = "admin"
        self.send(None)


@pytest.mark.usefixtures("avatar", "user_cls")
class TestSetAvatar(Base, AvatarFixtureMixin):
    def test_set_avatar(self):
        muc = self.get_private_muc(resources=("gajim",))
        with unittest.mock.patch(
            "slidge.group.room.LegacyMUC.on_avatar", return_value=1
        ):
            self.recv(  # language=XML
                f"""
            <iq id='set1'
                type='set'
                to='room-private@aim.shakespeare.lit'
                from='romeo@montague.lit/gajim'>
              <vCard xmlns='vcard-temp'>
                <PHOTO>
                  <TYPE>image/png</TYPE>
                  <BINVAL>{b64encode(self.avatar_bytes).decode("utf-8")}</BINVAL>
                </PHOTO>
              </vCard>
            </iq>
            """
            )
        self.send(  # language=XML
            """
            <iq from="room-private@aim.shakespeare.lit"
                type="result"
                to="romeo@montague.lit/gajim"
                id="set1">
              <vCard xmlns="vcard-temp" />
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <message type="groupchat"
                     from="room-private@aim.shakespeare.lit"
                     to="romeo@montague.lit/gajim">
              <stanza-id xmlns="urn:xmpp:sid:0"
                         id="uuid"
                         by="room-private@aim.shakespeare.lit" />
              <x xmlns="http://jabber.org/protocol/muc#user">
                <status code="104" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="room" />
            </message>
            """,
            use_values=False,
        )
        self.send(  # language=XML
            f"""
            <presence to="romeo@montague.lit/gajim"
                      from="room-private@aim.shakespeare.lit">
              <x xmlns="vcard-temp:x:update">
                <photo>{self.avatar_sha1}</photo>
              </x>
            </presence>
            """
        )
        self.send(None)


@pytest.mark.usefixtures("avatar", "user_cls")
class TestUserAvatar(Base, AvatarFixtureMixin):
    def setUp(self):
        super().setUp()
        session = self.get_romeo_session()
        session.avatar_hash = self.avatar_sha1
        muc = self.get_private_muc(name="room-user-avatar-test", resources=("gajim",))
        self.user_participant = self.run_coro(muc.get_user_participant())

    def test_user_avatar(self):
        self.user_participant.send_initial_presence("romeo@montague.lit/gajim")
        self.send(  # language=XML
            f"""
            <presence from="room-user-avatar-test@aim.shakespeare.lit/{self.user_participant.nickname}"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
                <status code="110" />
              </x>
              <x xmlns="vcard-temp:x:update">
                <photo>{self.avatar_sha1}</photo>
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """
        )

    def test_fetch_user_avatar(self):
        self.recv(  # language=XML
            f"""
            <iq from="romeo@montague.lit/gajim"
                to="room-user-avatar-test@aim.shakespeare.lit/{self.user_participant.nickname}"
                type="get"
                xml:lang="en">
              <vCard xmlns="vcard-temp" />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq type="get"
                to="romeo@montague.lit"
                id="1"
                from="{self.xmpp.boundjid.bare}">
              <pubsub xmlns="http://jabber.org/protocol/pubsub">
                <items node="urn:xmpp:avatar:metadata">
                  <item id="{self.avatar_sha1}" />
                </items>
              </pubsub>
            </iq>
            """
        )
        self.recv(  # language=XML
            f"""
            <iq type='result'
                from='romeo@montague.lit'
                to='{self.xmpp.boundjid.bare}'
                id='1'>
              <pubsub xmlns='http://jabber.org/protocol/pubsub'>
                <items node='urn:xmpp:avatar:metadata'>
                  <item id='{self.avatar_sha1}'>
                    <metadata xmlns='urn:xmpp:avatar:metadata'>
                      <info bytes='{len(self.avatar_bytes)}'
                            height='5'
                            id='{self.avatar_sha1}'
                            type='image/test'
                            width='5' />
                    </metadata>
                  </item>
                </items>
              </pubsub>
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq type="get"
                to="romeo@montague.lit"
                id="2"
                from="{self.xmpp.boundjid.bare}">
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
            <iq type='result'
                from='romeo@montague.lit'
                to='{self.xmpp.boundjid.bare}'
                id='2'>
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
        self.send(  # language=XML
            f"""
            <iq from="room-user-avatar-test@aim.shakespeare.lit/thirdwitch"
                to="romeo@montague.lit/gajim"
                type="result"
                id="3"
                xml:lang="en">
              <vCard xmlns="vcard-temp">
                <PHOTO>
                  <BINVAL>{self.avatar_base64}</BINVAL>
                  <TYPE>image/test</TYPE>
                </PHOTO>
              </vCard>
            </iq>
            """,
            use_values=False,
        )
        self.send(None)


class TestMUCAdmin(Base):
    def setUp(self):
        super().setUp()
        self.muc = muc = self.get_private_muc(
            name="room-moderation-test", resources=("gajim",)
        )
        self.user_participant = self.run_coro(muc.get_user_participant())
        self.user_participant._LegacyParticipant__presence_sent = True
        self.user_jid = self.get_romeo_session().user.jid

    def test_moderation_not_implemented(self):
        self.recv(  # language=XML
            f"""
            <iq type='set'
                to='{self.muc.jid}'
                id='retract-request-1'
                from='{self.user_jid}'>
              <apply-to id="stanza-id-1"
                        xmlns="urn:xmpp:fasten:0">
                <moderate xmlns='urn:xmpp:message-moderate:0'>
                  <retract xmlns='urn:xmpp:message-retract:0' />
                  <reason>This message contains inappropriate content for this forum</reason>
                </moderate>
              </apply-to>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq type="error"
                to="romeo@montague.lit"
                id="retract-request-1"
                from="room-moderation-test@aim.shakespeare.lit">
              <error type="cancel">
                <feature-not-implemented xmlns="urn:ietf:params:xml:ns:xmpp-stanzas" />
                <text xmlns="urn:ietf:params:xml:ns:xmpp-stanzas">Not implemented by the legacy module</text>
              </error>
            </iq>
            """
        )

    def test_moderation_success(self):
        with unittest.mock.patch("slidge.BaseSession.on_moderate") as on_moderate:
            self.recv(  # language=XML
                f"""
            <iq type='set'
                to='{self.muc.jid}'
                id='retract-request-1'
                from='{self.user_jid}'>
              <apply-to id="stanza-id-1"
                        xmlns="urn:xmpp:fasten:0">
                <moderate xmlns='urn:xmpp:message-moderate:0'>
                  <retract xmlns='urn:xmpp:message-retract:0' />
                  <reason>REASON</reason>
                </moderate>
              </apply-to>
            </iq>
            """
            )
            on_moderate.assert_awaited_once_with(
                self.muc, "legacy-stanza-id-1", "REASON"
            )
        self.send(  # language=XML
            """
            <iq type="result"
                to="romeo@montague.lit"
                id="retract-request-1"
                from="room-moderation-test@aim.shakespeare.lit"></iq>
            """
        )

    def test_set_member(self):
        with unittest.mock.patch(
            "slidge.LegacyMUC.on_set_affiliation"
        ) as on_set_affiliation:
            self.recv(  # language=XML
                f"""
            <iq type='set'
                to='{self.muc.jid}'
                id='set-affiliation-1'
                from='{self.user_jid}'>
              <query xmlns='http://jabber.org/protocol/muc#admin'>
                <item affiliation='member'
                      jid='secondwitch@shakespeare.lit'
                      nick='a-nick'>
                  <reason>A reason</reason>
                </item>
              </query>
            </iq>
            """
            )
            on_set_affiliation.assert_awaited_once_with(
                self.run_coro(self.get_romeo_session().contacts.by_legacy_id(222)),
                "member",
                "A reason",
                "a-nick",
            )
        self.send(  # language=XML
            """
            <iq type="result"
                to="romeo@montague.lit"
                id="set-affiliation-1"
                from="room-moderation-test@aim.shakespeare.lit"></iq>
            """
        )

    def test_get_owner_form(self):
        self.recv(  # language=XML
            f"""
            <iq type='get'
                to='{self.muc.jid}'
                id='config1'
                from='{self.user_jid}'>
              <query xmlns='http://jabber.org/protocol/muc#owner' />
            </iq>
            """
        )
        self.send(  # language=XML
            f"""
            <iq from='{self.muc.jid}'
                id='config1'
                to='{self.user_jid}'
                type='result'>
              <query xmlns='http://jabber.org/protocol/muc#owner'>
                <x xmlns='jabber:x:data'
                   type='form'>
                  <title>Slidge room configuration</title>
                  <instructions>Complete this form to modify the configuration of your room.</instructions>
                  <field type='hidden'
                         var='FORM_TYPE'>
                    <value>http://jabber.org/protocol/muc#roomconfig</value>
                  </field>
                  <field label='Natural-Language Room Name'
                         type='text-single'
                         var='muc#roomconfig_roomname'>
                    <value>unnamed-room</value>
                  </field>
                  <field label='Short Description of Room'
                         type='text-single'
                         var='muc#roomconfig_roomdesc'>
                    <value></value>
                  </field>
                </x>
              </query>
            </iq>
            """
        )

    def test_set_description(self):
        with unittest.mock.patch("slidge.LegacyMUC.on_set_config") as on_set_config:
            self.recv(  # language=XML
                f"""
            <iq type="set"
                to='{self.muc.jid}'
                id='set-description'
                from='{self.user_jid}'>
              <query xmlns="http://jabber.org/protocol/muc#owner">
                <x xmlns="jabber:x:data"
                   type="submit">
                  <title>Slidge room configuration</title>
                  <instructions>Complete this form to modify the configuration of your room.</instructions>
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roomconfig</value>
                  </field>
                  <field var="muc#roomconfig_roomname"
                         label="Natural-Language Room Name"
                         type="text-single">
                    <value>A new name</value>
                  </field>
                  <field var="muc#roomconfig_roomdesc"
                         label="Short Description of Room"
                         type="text-single">
                    <value>A new description</value>
                  </field>
                </x>
              </query>
            </iq>
            """
            )
            on_set_config.assert_awaited_once_with(
                name="A new name",
                description="A new description",
            )
        self.send(  # language=XML
            """
            <iq type="result"
                to="romeo@montague.lit"
                id="set-description"
                from="room-moderation-test@aim.shakespeare.lit">
              <query xmlns="http://jabber.org/protocol/muc#owner">
                <x xmlns="jabber:x:data"
                   type="result">
                  <title>Slidge room configuration</title>
                  <instructions>Complete this form to modify the configuration of your room.</instructions>
                  <field var="FORM_TYPE"
                         type="hidden">
                    <value>http://jabber.org/protocol/muc#roomconfig</value>
                  </field>
                  <field var="muc#roomconfig_roomname"
                         label="Natural-Language Room Name"
                         type="text-single">
                    <value>A new name</value>
                  </field>
                  <field var="muc#roomconfig_roomdesc"
                         label="Short Description of Room"
                         type="text-single">
                    <value>A new description</value>
                  </field>
                </x>
              </query>
            </iq>
            """
        )

    def test_destruct(self):
        with unittest.mock.patch(
            "slidge.LegacyMUC.on_destroy_request"
        ) as on_destroy_request:
            self.recv(  # language=XML
                f"""
            <iq type="set"
                to='{self.muc.jid}'
                id='destroy'
                from='{self.user_jid}'>
              <query xmlns="http://jabber.org/protocol/muc#owner">
                <destroy>
                  <reason>Macbeth doth come.</reason>
                </destroy>
              </query>
            </iq>
            """
            )
            on_destroy_request.assert_awaited_once_with("Macbeth doth come.")
        self.send(  # language=XML
            f"""
            <presence type="unavailable"
                      from="room-moderation-test@aim.shakespeare.lit/thirdwitch"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="none"
                      role="none" />
                <status code="110" />
                <destroy>
                  <reason>Macbeth doth come.</reason>
                </destroy>
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="slidge-user" />
            </presence>
            """,
        )
        self.send(  # language=XML
            f"""
            <iq type="result"
                to="{self.user_jid}"
                id="destroy"
                from="{self.muc.jid}" />
            """
        )

    def test_subject(self):
        with unittest.mock.patch("slidge.LegacyMUC.on_set_subject") as on_set_subject:
            self.recv(  # language=XML
                f"""
            <message type="groupchat"
                     to='{self.muc.jid}'
                     from='{self.user_jid}'>
              <subject>Fire Burn and Cauldron Bubble!</subject>
            </message>
            """
            )
            on_set_subject.assert_awaited_once_with("Fire Burn and Cauldron Bubble!")
        self.send(None)


class TestJoinAway(Base):
    def setUp(self):
        super().setUp()
        self.muc = muc = self.get_private_muc(
            name="room-moderation-test", resources=("gajim",)
        )
        self.user_participant = self.run_coro(muc.get_user_participant())
        self.user_jid = self.get_romeo_session().user.jid
        self.juliet = self.run_coro(self.get_romeo_session().contacts.by_legacy_id(123))

    def get_juliet_participant(self):
        return self.run_coro(self.muc.get_participant_by_contact(self.juliet))

    def test_online_contact_joins(self):
        self.juliet.online()
        assert self.next_sent() is None
        self.get_juliet_participant()
        self.send(  # language=XML
            """
            <presence from="room-moderation-test@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        self.muc.remove_participant(self.get_juliet_participant())
        self.send(  # language=XML
            """
            <presence type="unavailable"
                      from="room-moderation-test@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        assert self.next_sent() is None
        self.test_away_contact_joins()

    def test_away_contact_joins(self):
        self.juliet.away()
        assert self.next_sent() is None
        self.run_coro(self.muc.get_participant_by_contact(self.juliet))
        self.send(  # language=XML
            """
            <presence from="room-moderation-test@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <show>away</show>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        assert self.next_sent() is None
        self.muc.remove_participant(self.get_juliet_participant())
        self.send(  # language=XML
            """
            <presence type="unavailable"
                      from="room-moderation-test@aim.shakespeare.lit/juliet"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="juliet@aim.shakespeare.lit/slidge" />
            </presence>
            """
        )
        assert self.next_sent() is None


class TestMentions(Base):
    def test_mentions(self):
        muc = self.get_private_muc("weird", ("gajim",))
        session = self.get_romeo_session()
        with unittest.mock.patch("test_muc.Session.on_text") as on_text:
            self.recv(  # language=XML
                f"""
            <message from='{session.user.jid}/gajim'
                     to='{muc.jid}'
                     type='groupchat'>
              <body>I am {muc.user_nick} I want weirdguy🎉 to kiss me</body>
            </message>
            """
            )
            on_text.assert_awaited_once_with(
                muc,
                f"I am {muc.user_nick} I want weirdguy🎉 to kiss me",
                reply_to_msg_id=None,
                reply_to_fallback_text=None,
                reply_to=None,
                thread=None,
                mentions=[
                    Mention(
                        contact=self.run_coro(
                            muc.get_participant("weirdguy🎉")
                        ).contact,
                        start=23,
                        end=32,
                    )
                ],
            )


class TestHats(Base):
    def test_hats(self):
        muc = self.get_private_muc("room-private", ("gajim",))
        participant = self.run_coro(muc.get_participant("i-wear-hats"))
        participant.send_last_presence(force=True, no_cache_online=True)
        self.send(  # language=XML
            """
            <presence from="room-private@aim.shakespeare.lit/i-wear-hats"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
        participant.set_hats([Hat("uri1", "title1"), Hat("uri2", "title2")])
        self.send(  # language=XML
            """
            <presence from="room-private@aim.shakespeare.lit/i-wear-hats"
                      to="romeo@montague.lit/gajim">
              <x xmlns="http://jabber.org/protocol/muc#user">
                <item affiliation="member"
                      role="participant" />
              </x>
              <hats xmlns="urn:xmpp:hats:0">
                <hat uri="uri1"
                     title="title1" />
                <hat uri="uri2"
                     title="title2" />
              </hats>
              <occupant-id xmlns="urn:xmpp:occupant-id:0"
                           id="uuid" />
            </presence>
            """
        )
