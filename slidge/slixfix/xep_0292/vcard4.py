from slixmpp.plugins.base import BasePlugin, register_plugin
from slixmpp.plugins.xep_0292.stanza import NS


class VCard4Provider(BasePlugin):
    name = "xep_0292_provider"
    description = "VCard4 Provider"
    dependencies = {"xep_0030"}

    def plugin_init(self):
        self.xmpp.plugin["xep_0030"].add_feature(NS)


register_plugin(VCard4Provider)
