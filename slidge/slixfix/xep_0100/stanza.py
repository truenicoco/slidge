from slixmpp.xmlstream import ElementBase


class Gateway(ElementBase):
    namespace = "jabber:iq:gateway"
    name = "query"
    plugin_attrib = "gateway"
    interfaces = {"desc", "prompt", "jid"}
    sub_interfaces = interfaces
