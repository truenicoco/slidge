import unittest
from slixmpp.test import SlixTest

from slidge.util.xep_0356 import stanza, permissions


class TestPermissions(SlixTest):
    def setUp(self):
        stanza.register()

    def testAdvertisePermission(self):
        xmlstring = """
            <message from='capulet.lit' to='pubsub.capulet.lit'>
                <privilege xmlns='urn:xmpp:privilege:2'>
                    <perm access='roster' type='both'/>
                    <perm access='message' type='outgoing'/>
                    <perm access='presence' type='managed_entity'/>
                    <perm access='iq' type='both'/>
                </privilege>
            </message>
        """
        msg = self.Message()
        msg["from"] = "capulet.lit"
        msg["to"] = "pubsub.capulet.lit"

        for access, type_ in [
            ("roster", permissions.RosterAccess.BOTH),
            ("message", permissions.MessagePermission.OUTGOING),
            ("presence", permissions.PresencePermission.MANAGED_ENTITY),
            ("iq", permissions.IqPermission.BOTH),
        ]:
            msg["privilege"].add_perm(access, type_)

        self.check(msg, xmlstring)


suite = unittest.TestLoader().loadTestsFromTestCase(TestPermissions)
