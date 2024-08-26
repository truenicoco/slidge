import unittest.mock

from slixmpp import JID
from slixmpp.plugins.xep_0356.permissions import IqPermission
from test_muc import Base as BaseMUC

# from test_shakespeare import Base as BaseNoMUC


class MDSMixin:
    def setUp(self):
        super().setUp()
        for domain in "test.com", "montague.lit":
            self.xmpp["xep_0356"].granted_privileges[domain].iq[
                "http://jabber.org/protocol/pubsub"
            ] = IqPermission.BOTH
            self.xmpp["xep_0356"].granted_privileges[domain].iq[
                "http://jabber.org/protocol/pubsub#owner"
            ] = IqPermission.BOTH
        self.patch_uuid = unittest.mock.patch("uuid.uuid4", return_value="uuid")
        self.patch_uuid.start()

    def tearDown(self):
        super().tearDown()
        self.patch_uuid.stop()


class TestMDS(MDSMixin, BaseMUC):
    def test_add_to_whitelist(self):
        task = self.xmpp.loop.create_task(
            self.xmpp._BaseGateway__add_component_to_mds_whitelist(JID("test@test.com"))
        )
        self.send(  # language=XML
            """
            <iq id="uuid"
                to="test@test.com"
                from="aim.shakespeare.lit"
                type="set">
              <privileged_iq xmlns="urn:xmpp:privilege:2">
                <iq xmlns="jabber:client"
                    type="set"
                    to="test@test.com"
                    from="test@test.com"
                    id="uuid">
                  <pubsub xmlns="http://jabber.org/protocol/pubsub">
                    <create node="urn:xmpp:mds:displayed:0" />
                  </pubsub>
                </iq>
              </privileged_iq>
            </iq>
            """,
            use_values=False,
        )
        self.recv(  # language=XML
            """
            <iq id="uuid"
                type="result"
                to="test@test.com"
                from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:2">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <iq xmlns="jabber:client"
                      id="uuid"
                      type="result"
                      to="test@localhost" />
                </forwarded>
              </privilege>
            </iq>
            """
        )
        self.send(  # language=XML
            """
            <iq id="uuid"
                to="test@test.com"
                from="aim.shakespeare.lit"
                type="set">
              <privileged_iq xmlns="urn:xmpp:privilege:2">
                <iq xmlns="jabber:client"
                    type="set"
                    to="test@test.com"
                    from="test@test.com"
                    id="uuid">
                  <pubsub xmlns="http://jabber.org/protocol/pubsub#owner">
                    <affiliations node="urn:xmpp:mds:displayed:0">
                      <affiliation jid="aim.shakespeare.lit"
                                   affiliation="member" />
                    </affiliations>
                  </pubsub>
                </iq>
              </privileged_iq>
            </iq>
            """,
            use_values=False,
        )
        self.recv(  # language=XML
            """
            <iq id="uuid"
                type="result"
                to="test@test.com"
                from="aim.shakespeare.lit">
              <privilege xmlns="urn:xmpp:privilege:2">
                <forwarded xmlns="urn:xmpp:forward:0">
                  <iq xmlns="jabber:client"
                      id="uuid"
                      type="result"
                      to="test@localhost" />
                </forwarded>
              </privilege>
            </iq>
            """
        )
        assert task.done()

    def test_receive_event(self):
        session = self.get_romeo_session()
        # juliet = self.juliet
        muc = self.get_private_muc()

        with unittest.mock.patch("test_muc.Session.on_displayed") as on_displayed:
            self.recv(  # language=XML
                f"""
            <message from='{session.user_jid}'
                     to='{self.xmpp.boundjid.bare}'
                     type='headline'
                     id='new-displayed-pep-event'>
              <event xmlns='http://jabber.org/protocol/pubsub#event'>
                <items node='urn:xmpp:mds:displayed:0'>
                  <item id='{muc.jid}'>
                    <displayed xmlns='urn:xmpp:mds:displayed:0'>
                      <stanza-id xmlns='urn:xmpp:sid:0'
                                 by='what@ev.er'
                                 id='1337' />
                    </displayed>
                  </item>
                </items>
              </event>
            </message>
            """
            )
            on_displayed.assert_awaited_once()
            assert on_displayed.call_args[0][0].jid == muc.jid
            assert on_displayed.call_args[0][1] == "legacy-1337"

    def test_send_mds(self):
        muc = self.get_private_muc()
        participant = self.run_coro(muc.get_user_participant())
        participant.displayed("legacy-msg-id")
        self.send(  # language=XML
            """
            <iq id="uuid"
                to="romeo@montague.lit"
                from="aim.shakespeare.lit"
                type="set">
              <privileged_iq xmlns="urn:xmpp:privilege:2">
                <iq xmlns="jabber:client"
                    to="romeo@montague.lit"
                    from="romeo@montague.lit"
                    id="uuid"
                    type="set">
                  <pubsub xmlns="http://jabber.org/protocol/pubsub">
                    <publish node="urn:xmpp:mds:displayed:0">
                      <item id="room-private@aim.shakespeare.lit">
                        <displayed xmlns="urn:xmpp:mds:displayed:0">
                          <stanza-id xmlns="urn:xmpp:sid:0"
                                     id="msg-id"
                                     by="room-private@aim.shakespeare.lit" />
                        </displayed>
                      </item>
                    </publish>
                    <publish-options>
                      <x xmlns="jabber:x:data"
                         type="submit">
                        <field var="FORM_TYPE"
                               type="hidden">
                          <value>http://jabber.org/protocol/pubsub#publish-options</value>
                        </field>
                        <field var="pubsub#persist_items">
                          <value>1</value>
                        </field>
                        <field var="pubsub#max_items">
                          <value>max</value>
                        </field>
                        <field var="pubsub#send_last_published_item">
                          <value>never</value>
                        </field>
                        <field var="pubsub#access_model">
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
        self.recv(  # language=XML
            """
            <iq id="uuid"
                from="romeo@montague.lit"
                to="aim.shakespeare.lit"
                type="result" />
            """
        )
