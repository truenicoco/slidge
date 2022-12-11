# type:ignore
import tempfile
import types
from pathlib import Path
from typing import Union
from xml.dom.minidom import parseString

from slixmpp import (
    ElementBase,
    MatcherId,
    MatchXMLMask,
    MatchXPath,
    Message,
    Presence,
    StanzaPath,
)
from slixmpp.test import SlixTest, TestTransport
from slixmpp.xmlstream import highlight, tostring
from slixmpp.xmlstream.matcher import MatchIDSender

from slidge import *

from ..core import config


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
        upload_requester = None
        ignore_delay_threshold = 300

    @classmethod
    def setUpClass(cls):
        user_store.set_file(Path(tempfile.mkdtemp()) / "test.db")
        for k, v in vars(cls.Config).items():
            setattr(config, k.upper(), v)

    def setUp(self):
        BaseGateway._subclass = find_subclass(self.plugin, BaseGateway)
        BaseSession._subclass = find_subclass(self.plugin, BaseSession)
        LegacyRoster._subclass = find_subclass(self.plugin, LegacyRoster, base_ok=True)
        LegacyContact._subclass = find_subclass(
            self.plugin, LegacyContact, base_ok=True
        )
        LegacyMUC._subclass = find_subclass(self.plugin, LegacyMUC, base_ok=True)
        LegacyBookmarks._subclass = find_subclass(
            self.plugin, LegacyBookmarks, base_ok=True
        )

        self.xmpp = BaseGateway.get_self_or_unique_subclass()()

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
        LegacyMUC.reset_subclass()
        LegacyBookmarks.reset_subclass()
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

    def check(self, stanza, criteria, method="exact", defaults=None, use_values=True):
        """
        Create and compare several stanza objects to a correct XML string.

        If use_values is False, tests using stanza.values will not be used.

        Some stanzas provide default values for some interfaces, but
        these defaults can be problematic for testing since they can easily
        be forgotten when supplying the XML string. A list of interfaces that
        use defaults may be provided and the generated stanzas will use the
        default values for those interfaces if needed.

        However, correcting the supplied XML is not possible for interfaces
        that add or remove XML elements. Only interfaces that map to XML
        attributes may be set using the defaults parameter. The supplied XML
        must take into account any extra elements that are included by default.

        Arguments:
            stanza       -- The stanza object to test.
            criteria     -- An expression the stanza must match against.
            method       -- The type of matching to use; one of:
                            'exact', 'mask', 'id', 'xpath', and 'stanzapath'.
                            Defaults to the value of self.match_method.
            defaults     -- A list of stanza interfaces that have default
                            values. These interfaces will be set to their
                            defaults for the given and generated stanzas to
                            prevent unexpected test failures.
            use_values   -- Indicates if testing using stanza.values should
                            be used. Defaults to True.
        """
        if method is None and hasattr(self, "match_method"):
            method = getattr(self, "match_method")

        if method != "exact":
            matchers = {
                "stanzapath": StanzaPath,
                "xpath": MatchXPath,
                "mask": MatchXMLMask,
                "idsender": MatchIDSender,
                "id": MatcherId,
            }
            Matcher = matchers.get(method, None)
            if Matcher is None:
                raise ValueError("Unknown matching method.")
            test = Matcher(criteria)
            self.assertTrue(
                test.match(stanza),
                "Stanza did not match using %s method:\n" % method
                + "Criteria:\n%s\n" % str(criteria)
                + "Stanza:\n%s" % str(stanza),
            )
        else:
            stanza_class = stanza.__class__
            # Hack to preserve namespaces instead of having jabber:client
            # everywhere.
            old_ns = stanza_class.namespace
            stanza_class.namespace = stanza.namespace
            if not isinstance(criteria, ElementBase):
                xml = self.parse_xml(criteria)
            else:
                xml = criteria.xml

            # Ensure that top level namespaces are used, even if they
            # were not provided.
            self.fix_namespaces(stanza.xml)
            self.fix_namespaces(xml)

            stanza2 = stanza_class(xml=xml)

            if use_values:
                # Using stanza.values will add XML for any interface that
                # has a default value. We need to set those defaults on
                # the existing stanzas and XML so that they will compare
                # correctly.
                default_stanza = stanza_class()
                if defaults is None:
                    known_defaults = {Message: ["type"], Presence: ["priority"]}
                    defaults = known_defaults.get(stanza_class, [])
                for interface in defaults:
                    stanza[interface] = stanza[interface]
                    stanza2[interface] = stanza2[interface]
                    # Can really only automatically add defaults for top
                    # level attribute values. Anything else must be accounted
                    # for in the provided XML string.
                    if interface not in xml.attrib:
                        if interface in default_stanza.xml.attrib:
                            value = default_stanza.xml.attrib[interface]
                            xml.attrib[interface] = value

                values = stanza2.values
                stanza3 = stanza_class()
                stanza3.values = values

                debug = "Three methods for creating stanzas do not match.\n"
                debug += "Given XML:\n%s\n" % highlight(tostring(xml))
                debug += "Given stanza:\n%s\n" % format_stanza(stanza)
                debug += "Generated stanza:\n%s\n" % highlight(tostring(stanza2.xml))
                debug += "Second generated stanza:\n%s\n" % highlight(
                    tostring(stanza3.xml)
                )
                result = self.compare(xml, stanza.xml, stanza2.xml, stanza3.xml)
            else:
                debug = "Two methods for creating stanzas do not match.\n"
                debug += "Given XML:\n%s\n" % highlight(tostring(xml))
                debug += "Given stanza:\n%s\n" % format_stanza(stanza)
                debug += "Generated stanza:\n%s\n" % highlight(tostring(stanza2.xml))
                result = self.compare(xml, stanza.xml, stanza2.xml)
            stanza_class.namespace = old_ns

            self.assertTrue(result, debug)


def format_stanza(stanza):
    return highlight(
        "\n".join(parseString(tostring(stanza.xml)).toprettyxml().split("\n")[1:])
    )


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
