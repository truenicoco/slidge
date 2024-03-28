from slixmpp import Iq
from slixmpp.plugins import BasePlugin
from slixmpp.plugins.xep_0004 import Form
from slixmpp.types import JidStr

from . import stanza


class XEP_0490(BasePlugin):
    """
    XEP-0490: Message Displayed Synchronization
    """

    name = "xep_0490"
    description = "XEP-0490: Message Displayed Synchronization"
    dependencies = {"xep_0060", "xep_0163", "xep_0359"}
    stanza = stanza

    def plugin_init(self):
        stanza.register_plugin()
        self.xmpp.plugin["xep_0163"].register_pep(
            "message_displayed_synchronization",
            stanza.Displayed,
        )

    def flag_chat(self, chat: JidStr, stanza_id: str, **kwargs) -> Iq:
        displayed = stanza.Displayed()
        displayed["stanza_id"]["id"] = stanza_id
        return self.xmpp.plugin["xep_0163"].publish(
            displayed, node=stanza.NS, options=PUBLISH_OPTIONS, id=str(chat), **kwargs
        )

    def catch_up(self, **kwargs):
        return self.xmpp.plugin["xep_0060"].get_items(
            self.xmpp.boundjid.bare, stanza.NS, **kwargs
        )


PUBLISH_OPTIONS = Form()
PUBLISH_OPTIONS["type"] = "submit"
PUBLISH_OPTIONS.add_field(
    "FORM_TYPE", "hidden", value="http://jabber.org/protocol/pubsub#publish-options"
)
PUBLISH_OPTIONS.add_field("pubsub#persist_items", value="true")
PUBLISH_OPTIONS.add_field("pubsub#max_items", value="max")
PUBLISH_OPTIONS.add_field("pubsub#send_last_published_item", value="never")
PUBLISH_OPTIONS.add_field("pubsub#access_model", value="whitelist")
