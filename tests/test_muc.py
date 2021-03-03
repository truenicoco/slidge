import asyncio

from slixmpp import JID, Presence

from slidge.test import SlixGatewayTest
from slidge.muc import LegacyMucList, LegacyMuc, Occupant
from slidge.database import User


class TestMuc(SlixGatewayTest):
    def setUp(self):
        self.stream_start(gateway_jid="chat.shakespeare.lit", server="shakespeare.lit")
        self.mucs = LegacyMucList()
        self.mucs.xmpp = self.xmpp
        self.user = self.mucs.user = User(
            jid=JID("hag66@shakespeare.lit/pda"), legacy_id="hag66_legacy_id"
        )
        self.next_sent()  # handshake

    def add_muc(self):
        muc = LegacyMuc(legacy_id="coven")
        muc.subject = "A Dark Cave"
        self.mucs.add(muc)
        return muc

    def test_add_muc(self):
        muc = self.add_muc()
        muc.subject = "Spells"
        assert muc.xmpp is self.xmpp
        assert muc.legacy is self.xmpp.legacy_client

    def test_room_info(self):
        muc = self.add_muc()
        muc.make_disco()
        self.recv(
            """
            <iq from='hag66@shakespeare.lit/pda'
                id='ik3vs715'
                to='coven@chat.shakespeare.lit'
                type='get'>
            <query xmlns='http://jabber.org/protocol/disco#info'/>
            </iq>
            """
        )
        self.send(
            f"""
            <iq from='coven@chat.shakespeare.lit'
                id='ik3vs715'
                to='hag66@shakespeare.lit/pda'
                type='result'>
            <query xmlns='http://jabber.org/protocol/disco#info'>
                <identity
                    category='conference'
                    name='A Dark Cave'
                    type='text'/>
                <feature var='http://jabber.org/protocol/muc'/>
                <feature var='http://jabber.org/protocol/muc#stable_id'/>
                <feature var='muc_hidden'/>
                <feature var='muc_open'/>
                <feature var='muc_unmoderated'/>
                <x xmlns='jabber:x:data' type='result'>
                    <field var='FORM_TYPE' type='hidden'>
                        <value>http://jabber.org/protocol/muc#roominfo</value>
                    </field>
                    <field var='muc#roominfo_subject'
                           label='Current Discussion Topic'>
                        <value>{muc.subject}</value>
                    </field>
                    <field var="muc#maxhistoryfetch"
                           label="Maximum Number of History Messages Returned by Room">
                        <value>{muc.history.max_history_fetch}</value>
                    </field>
                </x>
            </query>
            </iq>
            """
        )

    def test_join_muc(self):
        muc = self.add_muc()
        muc.occupants.add(
            Occupant(nick=f"firstwitch", role="moderator", affiliation="owner")
        )
        muc.occupants.add(
            Occupant(nick=f"secondwitch", role="moderator", affiliation="admin")
        )
        muc.subject_changer = "secondwitch"

        presence = Presence()
        presence["from"] = self.user.jid
        presence["to"] = f"{muc.jid}/thirdwitch"
        self.xmpp.loop.run_until_complete(muc.user_join(presence, sync_occupants=False))
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/firstwitch'
                to='hag66@shakespeare.lit/pda'>
                <x xmlns='http://jabber.org/protocol/muc#user'>
                    <item affiliation='owner' role='moderator'/>
                </x>
                <x xmlns="vcard-temp:x:update" />
            </presence>
            """
        )
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/secondwitch'
                to='hag66@shakespeare.lit/pda'>
                <x xmlns='http://jabber.org/protocol/muc#user'>
                    <item affiliation='admin' role='moderator'/>
                </x>
                <x xmlns="vcard-temp:x:update" />
            </presence>
            """
        )
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/thirdwitch'
                to='hag66@shakespeare.lit/pda'>
            <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
                <status code='110'/>
                <status code='210'/>
            </x>
            <x xmlns="vcard-temp:x:update" />
            </presence>
            """,
            use_values=False,
        )
        self.send(
            """
            <message xmlns="jabber:component:accept"
                     type="groupchat"
                     from="coven@chat.shakespeare.lit/secondwitch"
                     to="hag66@shakespeare.lit/pda">
                <subject>A Dark Cave</subject>
            </message>
            """,
        )
        assert self.next_sent() is None
        assert "pda" in muc.user_resources

    def test_leave_muc(self):
        muc = self.add_muc()
        muc.user_resources = ["pda"]
        muc.user_nickname = "thirdwitch"
        presence = Presence()
        presence["from"] = self.user.jid
        presence["to"] = f"{muc.jid}/thirdwitch"
        presence["type"] = "unavailable"
        self.xmpp.loop.run_until_complete(muc.user_leaves(presence))
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/thirdwitch'
                to='hag66@shakespeare.lit/pda'
                type='unavailable'>
            <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member'
                    jid='hag66@shakespeare.lit/pda'
                    role='none'/>
                <status code='110'/>
            </x>
            </presence>
            """,
            use_values=False,
        )
        assert self.next_sent() is None

    def test_shutdown(self):
        muc = self.add_muc()
        muc.user_resources = ["pda"]
        muc.user_nickname = "thirdwitch"
        self.xmpp.loop.run_until_complete(muc.shutdown())
        self.send(
            """
            <presence
                        from='coven@chat.shakespeare.lit/thirdwitch'
                        to='hag66@shakespeare.lit/pda'
                        type='unavailable'>
                <x xmlns='http://jabber.org/protocol/muc#user'>
                    <item affiliation='none' role='none' />
                    <status code='110'/>
                    <status code='332'/>
                </x>
            </presence>
            """,
            use_values=False,
        )
        assert self.next_sent() is None

    def test_participant_enters(self):
        muc = self.add_muc()
        muc.user_resources = ["pda"]
        occ = Occupant(nick=f"thirdwitch", role="participant", affiliation="member")
        muc.occupants.add(occ)
        # occ.make_join_presence().send()
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/thirdwitch'
                to='hag66@shakespeare.lit/pda'>
            <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
            </x>
            <x xmlns="vcard-temp:x:update" />
            </presence>
            """
        )

    def test_participant_exits(self):
        muc = self.add_muc()
        occ = Occupant(nick=f"thirdwitch", role="participant", affiliation="member")
        muc.occupants.add(occ)

        muc.user_resources = ["pda"]
        muc.occupants.remove(occ)
        self.send(
            """
            <presence
                from='coven@chat.shakespeare.lit/thirdwitch'
                to='hag66@shakespeare.lit/pda'
                type='unavailable'>
            <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
            </x>
            </presence>
            """
        )

    def test_change_subject(self):
        muc = self.add_muc()
        muc.user_resources = ["pda"]

        muc.change_subject("Fire Burn and Cauldron Bubble!", "secondwitch")
        self.send(
            """
        <message
            from='coven@chat.shakespeare.lit/secondwitch'
            to='hag66@shakespeare.lit/pda'
            type='groupchat'>
          <subject>Fire Burn and Cauldron Bubble!</subject>
        </message>
        """
        )

    # def test_disco_features(self):
    #     pass
    #     # Identity category = gateway so this cannot (?) work
    #     # https://xmpp.org/extensions/xep-0045.html#example-3
    #     # self.recv(
    #     #     """
    #     #     <iq from='hag66@shakespeare.lit/pda'
    #     #         id='lx09df27'
    #     #         to='chat.shakespeare.lit'
    #     #         type='get'>
    #     #     <query xmlns='http://jabber.org/protocol/disco#info'/>
    #     #     </iq>
    #     #     """
    #     # )
    #     # self.send(
    #     #     """
    #     #     <iq from='chat.shakespeare.lit'
    #     #         id='lx09df27'
    #     #         to='hag66@shakespeare.lit/pda'
    #     #         type='result'>
    #     #     <query xmlns='http://jabber.org/protocol/disco#info'>
    #     #         <identity
    #     #             category='conference'
    #     #             name='Shakespearean Chat Service'
    #     #             type='text'/>
    #     #         <feature var='http://jabber.org/protocol/muc'/>
    #     #     </query>
    #     #     </iq>
    #     #     """
    #     # )

    # def test_disco_rooms_by_user(self):
    #     pass
    #     # without any access control on disco service, it's probably best to
    #     # avoid making listing rooms possible