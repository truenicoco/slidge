from slixmpp.test import SlixTest
from slidge.util.xep_0292 import stanza, vcard4


class TestVcard(SlixTest):
    def setUp(self):
        self.stream_start(
            mode="component",
            plugins=["xep_0292_provider"],
            jid="vcard.jabber.org",
            server="jabber.org",
        )

    def testNoVcard(self):
        self.recv(
            """
            <iq from='samizzi@cisco.com/foo'
                id='bx81v356'
                to='stpeter@vcard.jabber.org'
                type='get'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
            </iq>
            """
        )
        self.send(
            """
              <iq from='stpeter@vcard.jabber.org'
                  id='bx81v356'
                  to='samizzi@cisco.com/foo'
                  type='result'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
              </iq>
            """,
            use_values=False,
        )

    def testBasicVCard(self):
        vcard = stanza.VCard4()
        vcard["full_name"] = "Peter Saint-Andre"
        vcard["given"] = "Peter"
        vcard["surname"] = "Saint-Andre"
        vcard.add_tel("+1-303-308-3282", "work")

        self.xmpp["xep_0292_provider"].set_vcard("stpeter@vcard.jabber.org", vcard)
        self.recv(
            """
            <iq from='samizzi@cisco.com/foo'
                id='bx81v356'
                to='stpeter@vcard.jabber.org'
                type='get'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
            </iq>
            """
        )
        self.send(
            """
            <iq from='stpeter@vcard.jabber.org'
              id='bx81v356'
              to='samizzi@cisco.com/foo'
              type='result'>
            <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
              <fn><text>Peter Saint-Andre</text></fn>
              <n><surname>Saint-Andre</surname><given>Peter</given></n>
              <tel>
                <parameters>
                  <type><text>work</text></type>
                </parameters>
                <uri>tel:+1-303-308-3282</uri>
              </tel>
            </vcard>
            </iq>
            """,
            use_values=False,
        )

    def testAccess(self):
        vcard = stanza.VCard4()
        vcard["full_name"] = "Peter Saint-Andre"
        vcard["given"] = "Peter"
        vcard["surname"] = "Saint-Andre"
        vcard.add_tel("+1-303-308-3282", "work")

        self.xmpp["xep_0292_provider"].set_vcard(
            "stpeter@vcard.jabber.org", vcard, authorized_jids={"samizzi@cisco.com"}
        )
        self.recv(
            """
            <iq from='prout@cisco.com/foo'
                id='xx'
                to='stpeter@vcard.jabber.org'
                type='get'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
            </iq>
            """
        )
        self.send(
            """
              <iq from='stpeter@vcard.jabber.org'
                  id='xx'
                  to='prout@cisco.com/foo'
                  type='result'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
              </iq>
            """,
            use_values=False,
        )

        self.recv(
            """
            <iq from='samizzi@cisco.com/foo'
                id='bx81v356'
                to='stpeter@vcard.jabber.org'
                type='get'>
                <vcard xmlns='urn:ietf:params:xml:ns:vcard-4.0'/>
            </iq>
            """
        )
        self.send(
            """
            <iq from='stpeter@vcard.jabber.org'
              id='bx81v356'
              to='samizzi@cisco.com/foo'
              type='result'>
            <vcard xmlns="urn:ietf:params:xml:ns:vcard-4.0">
              <fn><text>Peter Saint-Andre</text></fn>
              <n><surname>Saint-Andre</surname><given>Peter</given></n>
              <tel>
                <parameters>
                  <type><text>work</text></type>
                </parameters>
                <uri>tel:+1-303-308-3282</uri>
              </tel>
            </vcard>
            </iq>
            """,
            use_values=False,
        )
