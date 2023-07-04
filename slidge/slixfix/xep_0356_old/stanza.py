from slixmpp.plugins.xep_0297 import Forwarded
from slixmpp.stanza import Message
from slixmpp.xmlstream import ElementBase, register_stanza_plugin


class PrivilegeOld(ElementBase):
    namespace = "urn:xmpp:privilege:1"
    name = "privilege"
    plugin_attrib = "privilege_old"

    def permission(self, access):
        for perm in self["perms"]:
            if perm["access"] == access:
                return perm["type"]

    def roster(self):
        return self.permission("roster")

    def message(self):
        return self.permission("message")

    def presence(self):
        return self.permission("presence")

    def add_perm(self, access, type):
        # This should only be needed for servers, so maybe out of scope for slixmpp
        perm = PermOld()
        perm["type"] = type
        perm["access"] = access
        self.append(perm)


class PermOld(ElementBase):
    namespace = "urn:xmpp:privilege:1"
    name = "perm"
    plugin_attrib = "perm"
    plugin_multi_attrib = "perms"
    interfaces = {"type", "access"}


def register():
    register_stanza_plugin(Message, PrivilegeOld)
    register_stanza_plugin(PrivilegeOld, Forwarded)
    register_stanza_plugin(PrivilegeOld, PermOld, iterable=True)
