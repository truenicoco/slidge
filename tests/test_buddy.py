import logging
import asyncio
import pytest
import hashlib
import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from slixmpp import JID

from slidge.test import SlixGatewayTest
from slidge.buddy import Buddies, Buddy
from slidge.database import User


class TestBuddy(SlixGatewayTest):
    def setUp(self):
        self.stream_start()
        self.buddies = Buddies()
        self.buddies.xmpp = self.xmpp
        self.buddies.user = User(
            jid=JID("gatewayuser@example.com"), legacy_id="jabber_user_legacy_id"
        )
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

    def add_buddy(self):
        buddy = Buddy("buddy_legacy_id")
        self.buddies.add(buddy)
        return buddy

    def test_slix_roster(self):
        buddy = self.add_buddy()
        buddy._make_roster_entry()
        assert self.xmpp.roster[buddy.jid][self.buddies.user.jid]['subscription'] == 'both'

    def test_add_buddy(self):
        buddy = self.add_buddy()
        assert buddy.xmpp is self.xmpp is self.buddies.xmpp
        assert buddy.user is self.buddies.user
        assert self.buddies.by_jid(buddy.jid) is buddy
        assert self.buddies.by_legacy_id(buddy.legacy_id) is buddy

    def test_fill_roster(self):
        self.buddies.add(Buddy("buddy_legacy_id1"))
        self.buddies.add(Buddy("buddy_legacy_id2"))
        self.xmpp.loop.create_task(self.buddies.fill_roster())
        _ = self.next_sent()  # handshake
        self.send(
            """
        <iq xmlns="jabber:component:accept"
            type="set"
            to="gatewayuser@example.com"
            from="gateway.example.com"
            id="1">
            <query xmlns="jabber:iq:roster">
                <item subscription="both" jid="buddy_legacy_id1@gateway.example.com">
                    <group>legacy</group>
                </item>
                <item subscription="both" jid="buddy_legacy_id2@gateway.example.com">
                    <group>legacy</group>
                </item>
            </query>
        </iq>
        """
        )

    def test_identity(self):
        buddy = Buddy("buddy_legacy_id")
        self.buddies.add(buddy)
        buddy._make_identity()
        info = self.xmpp.loop.run_until_complete(
            self.xmpp["xep_0030"].get_info(jid=buddy.jid, local=True)
        )
        self.check(
            info,
            f"""
            <iq id="1"
                to="{self.xmpp.config["component"]["jid"]}"
                from="buddy_legacy_id@{self.xmpp.config["component"]["jid"]}/{self.xmpp.config["buddies"]["resource"]}"
                type="result">
                <query xmlns="http://jabber.org/protocol/disco#info">
                    <identity category="{buddy.IDENTITY_CATEGORY}" type="{buddy.IDENTITY_TYPE}" />
                    <feature var="http://jabber.org/protocol/disco#info" />
                </query>
            </iq>
            """,
            "exact",
        )

    def test_caps(self):
        buddy = Buddy("buddy_legacy_id")
        self.buddies.add(buddy)
        buddy._make_identity()
        self.xmpp.loop.run_until_complete(buddy.update_caps())
        caps = self.xmpp.loop.run_until_complete(self.xmpp["xep_0115"].get_caps(jid=buddy.jid))
        self.check(
            caps,
            f"""
            <query xmlns="http://jabber.org/protocol/disco#info">
                <identity category="{buddy.IDENTITY_CATEGORY}" type="{buddy.IDENTITY_TYPE}" />
                <feature var="jabber:iq:oob" />
                <feature var="jabber:x:oob" />
                <feature var="jabber:x:data" />
                <feature var="http://jabber.org/protocol/chatstates" />
                <feature var="urn:xmpp:http:upload:0" />
                <feature var="vcard-temp" />
                <feature var="urn:xmpp:receipts" />
            </query>
            """,
            "exact",
        )
        buddy.send_xmpp_presence()
        _ = self.next_sent()
        verstring = self.xmpp.loop.run_until_complete(self.xmpp["xep_0115"].get_verstring(buddy.jid))
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to="{buddy.user.jid}"
                      from="{buddy.jid}">
                      <x xmlns="vcard-temp:x:update" />
                      <c xmlns="http://jabber.org/protocol/caps"
                         node="{self.xmpp["xep_0115"].caps_node}"
                         hash="sha-1"
                         ver="{verstring}" />
                      <priority>0</priority>
            </presence>
            """
        )

    def test_presence(self):
        buddy = Buddy("buddy_legacy_id")
        self.buddies.add(buddy)
        buddy.send_xmpp_presence()
        _ = self.next_sent()  # handshake
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to="{buddy.user.jid}"
                      from="{buddy.jid}">
                      <x xmlns="vcard-temp:x:update" />
                      <priority>0</priority>
            </presence>
            """
        )
        buddy.ptype = "away"
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to="{buddy.user.jid}"
                      from="{buddy.jid}">
                      <x xmlns="vcard-temp:x:update" />
                      <priority>0</priority>
                      <show>away</show>
            </presence>
            """
        )

    def test_avatar_hash_in_presence(self):
        buddy = Buddy("buddy_legacy_id")
        self.buddies.add(buddy)
        avatar_bytes = b"xxxxxxx"
        hash_ = hashlib.sha1(avatar_bytes).hexdigest()

        buddy.avatar_bytes = avatar_bytes
        buddy._make_identity()
        self.xmpp.loop.run_until_complete(buddy._make_vcard())
        buddy.send_xmpp_presence()
        _ = self.next_sent()  # handshake
        self.send(
            f"""
            <presence xmlns="jabber:component:accept"
                      to="{buddy.user.jid}"
                      from="{buddy.jid}">
                      <x xmlns="vcard-temp:x:update">
                      <photo>{hash_}</photo>
                      </x>
                      <priority>0</priority>
            </presence>
            """
        )
        assert self.next_sent() is None

    def test_send_xmpp_message(self):
        buddy = self.add_buddy()
        buddy.send_xmpp_message(body="the body")
        _ = self.next_sent()  # handshake
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
                     type="chat"
                     to="gatewayuser@example.com"
                     from="buddy_legacy_id@gateway.example.com/gateway"
                     id="1">
                <active xmlns="http://jabber.org/protocol/chatstates" />
                <body>the body</body>
                <request xmlns="urn:xmpp:receipts" />
                <origin-id xmlns="urn:xmpp:sid:0" id="1" />
            </message>
            """
        )

    def test_send_xmpp_carbon(self):
        buddy = self.add_buddy()
        stamp = datetime.datetime.now()
        buddy.send_xmpp_carbon(body="the body", timestamp=stamp)
        _ = self.next_sent()  # handshake
        self.send(
            f"""
            <message xmlns="jabber:component:accept"
                     to="example.com"
                     from="gateway.example.com">
                <privilege xmlns="urn:xmpp:privilege:1">
                    <forwarded xmlns="urn:xmpp:forward:0">
                        <message xmlns="jabber:client"
                                 from="gatewayuser@example.com"
                                 to="gatewayuser@example.com"
                                 type="chat">
                            <sent xmlns="urn:xmpp:carbons:2">
                                <forwarded xmlns="urn:xmpp:forward:0">
                                    <message xmlns="jabber:client"
                                            from="gatewayuser@example.com"
                                            to="buddy_legacy_id@gateway.example.com"
                                            type="chat">
                                        <body>the body</body>
                                        <delay xmlns="urn:xmpp:delay" stamp="{stamp.isoformat()[:19] + "Z"}" />
                                    </message>
                                </forwarded>
                            </sent>
                            <no-copy xmlns="urn:xmpp:hints" />
                        </message>
                    </forwarded>
                </privilege>
            </message>
            """,
        )

logging.basicConfig(level=logging.DEBUG)
