from slixmpp import Presence
from slixmpp.xmlstream import ElementBase, register_stanza_plugin

NS = "urn:xmpp:hats:0"


class Hats(ElementBase):
    name = plugin_attrib = "hats"
    namespace = NS

    def add_hats(self, data: list[tuple[str, str]]):
        for uri, title in data:
            hat = Hat()
            hat["uri"] = uri
            hat["title"] = title
            self.append(hat)


class Hat(ElementBase):
    name = plugin_attrib = "hat"
    namespace = NS
    interfaces = {"uri", "title"}
    plugin_multi_attrib = "hats"


def register():
    register_stanza_plugin(Hats, Hat, iterable=True)
    register_stanza_plugin(Presence, Hats)
