import tempfile
import types
from pathlib import Path
from typing import Union

from slixmpp.test import SlixTest, TestTransport

from .. import BaseGateway, BaseSession, LegacyContact, LegacyRoster, user_store


class SlidgeTest(SlixTest):
    plugin: Union[types.ModuleType, dict]

    class Config:
        jid = "aim.shakespeare.lit"
        secret = "test"
        server = "shakespeare.lit"
        port = 5222
        upload_service = "upload.test"
        home_dir = Path(tempfile.mkdtemp())
        user_jid_validator = ".*@shakespeare.lit"
        admins: list[str] = []
        no_roster_push = False

    @classmethod
    def setUpClass(cls):
        user_store.set_file(Path(tempfile.mkdtemp()) / "test.db")

    def setUp(self):
        BaseGateway._subclass = find_subclass(self.plugin, BaseGateway)
        BaseSession._subclass = find_subclass(self.plugin, BaseSession)
        LegacyRoster._subclass = find_subclass(self.plugin, LegacyRoster, base_ok=True)
        LegacyContact._subclass = find_subclass(
            self.plugin, LegacyContact, base_ok=True
        )

        self.xmpp = BaseGateway.get_self_or_unique_subclass()(self.Config)

        self.xmpp._always_send_everything = True

        self.xmpp.connection_made(TestTransport(self.xmpp))
        self.xmpp.session_bind_event.set()
        # Remove unique ID prefix to make it easier to test
        self.xmpp._id_prefix = ""
        self.xmpp.default_lang = None
        self.xmpp.peer_default_lang = None

        def new_id():
            self.xmpp._id += 1
            return str(self.xmpp._id)

        self.xmpp._id = 0
        self.xmpp.new_id = new_id

        # Must have the stream header ready for xmpp.process() to work.
        header = self.xmpp.stream_header

        self.xmpp.data_received(header)
        self.wait_for_send_queue()

        self.xmpp.socket.next_sent()
        self.xmpp.socket.next_sent()

        # Some plugins require messages to have ID values. Set
        # this to True in tests related to those plugins.
        self.xmpp.use_message_ids = False
        self.xmpp.use_presence_ids = False

    @classmethod
    def tearDownClass(cls):
        BaseSession.reset_subclass()
        BaseGateway.reset_subclass()
        LegacyRoster.reset_subclass()
        LegacyContact.reset_subclass()
        user_store._users = None

    def next_sent(self):
        self.wait_for_send_queue()
        sent = self.xmpp.socket.next_sent(timeout=1)
        if sent is None:
            return None
        xml = self.parse_xml(sent)
        self.fix_namespaces(xml, "jabber:component:accept")
        sent = self.xmpp._build_stanza(xml, "jabber:component:accept")
        return sent


def find_subclass(o, parent, base_ok=False):
    try:
        vals = vars(o).values()
    except TypeError:
        vals = o.values()
    for x in vals:
        try:
            if issubclass(x, parent) and x is not parent:
                return x
        except TypeError:
            pass
    else:
        if base_ok:
            return parent
        else:
            raise RuntimeError
