# type:ignore
import tempfile
import types
from pathlib import Path
from typing import Optional, Union
from xml.dom.minidom import parseString

import xmldiff.main
from slixmpp import (
    JID,
    ElementBase,
    Iq,
    MatcherId,
    MatchXMLMask,
    MatchXPath,
    Message,
    Presence,
    StanzaPath,
)
from slixmpp.stanza.error import Error
from slixmpp.test import SlixTest, TestTransport
from slixmpp.xmlstream import highlight, tostring
from slixmpp.xmlstream.matcher import MatchIDSender
from sqlalchemy import create_engine, delete

from slidge import (
    BaseGateway,
    BaseSession,
    LegacyBookmarks,
    LegacyContact,
    LegacyMUC,
    LegacyParticipant,
    LegacyRoster,
)

from ..command import Command
from ..core import config
from ..core.config import _TimedeltaSeconds
from ..core.pubsub import PepAvatar, PepNick
from ..db import SlidgeStore
from ..db.avatar import avatar_cache
from ..db.meta import Base
from ..db.models import Contact


class SlixTestPlus(SlixTest):
    def setUp(self):
        super().setUp()
        Error.namespace = "jabber:component:accept"

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

            if not result:
                debug += str(
                    xmldiff.main.diff_texts(tostring(xml), tostring(stanza.xml))
                )
                if use_values:
                    debug += str(
                        xmldiff.main.diff_texts(tostring(xml), tostring(stanza2.xml))
                    )
            self.assertTrue(result, debug)

    def next_sent(self, timeout=0.05) -> Optional[Union[Message, Iq, Presence]]:
        self.wait_for_send_queue()
        sent = self.xmpp.socket.next_sent(timeout=timeout)
        if sent is None:
            return None
        xml = self.parse_xml(sent)
        self.fix_namespaces(xml, "jabber:component:accept")
        sent = self.xmpp._build_stanza(xml, "jabber:component:accept")
        return sent


class SlidgeTest(SlixTestPlus):
    plugin: Union[types.ModuleType, dict]

    class Config:
        jid = "aim.shakespeare.lit"
        secret = "test"
        server = "shakespeare.lit"
        port = 5222
        upload_service = "upload.test"
        home_dir = Path(tempfile.mkdtemp())
        user_jid_validator = ".*"
        admins: list[str] = []
        no_roster_push = False
        upload_requester = None
        ignore_delay_threshold = _TimedeltaSeconds("300")
        last_seen_fallback = True

    @classmethod
    def setUpClass(cls):
        for k, v in vars(cls.Config).items():
            setattr(config, k.upper(), v)

    def setUp(self):
        if hasattr(self, "plugin"):
            BaseGateway._subclass = find_subclass(self.plugin, BaseGateway)
            BaseSession._subclass = find_subclass(self.plugin, BaseSession)
            LegacyRoster._subclass = find_subclass(
                self.plugin, LegacyRoster, base_ok=True
            )
            LegacyContact._subclass = find_subclass(
                self.plugin, LegacyContact, base_ok=True
            )
            LegacyMUC._subclass = find_subclass(self.plugin, LegacyMUC, base_ok=True)
            LegacyBookmarks._subclass = find_subclass(
                self.plugin, LegacyBookmarks, base_ok=True
            )

        # workaround for duplicate output of sql alchemy's log, cf
        # https://stackoverflow.com/a/76498428/5902284
        from sqlalchemy import log as sqlalchemy_log

        sqlalchemy_log._add_default_handler = lambda x: None

        engine = self.db_engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        BaseGateway.store = SlidgeStore(engine)
        BaseGateway._test_mode = True
        try:
            self.xmpp = BaseGateway.get_self_or_unique_subclass()()
        except Exception:
            raise
        self.xmpp.TEST_MODE = True
        PepNick.contact_store = self.xmpp.store.contacts
        PepAvatar.store = self.xmpp.store
        avatar_cache.store = self.xmpp.store.avatars
        avatar_cache.set_dir(Path(tempfile.mkdtemp()))
        self.xmpp._always_send_everything = True
        engine.echo = True

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
        Error.namespace = "jabber:component:accept"

    def tearDown(self):
        self.db_engine.echo = False
        super().tearDown()
        import slidge.db.store

        if slidge.db.store._session is not None:
            slidge.db.store._session.commit()
            slidge.db.store._session = None
        Base.metadata.drop_all(self.xmpp.store._engine)

    def setup_logged_session(self, n_contacts=0):
        user = self.xmpp.store.users.new(
            JID("romeo@montague.lit/gajim"), {"username": "romeo", "city": ""}
        )
        user.preferences = {"sync_avatar": True, "sync_presence": True}
        self.xmpp.store.users.update(user)

        with self.xmpp.store.session() as session:
            session.execute(delete(Contact))
            session.commit()

        self.run_coro(
            self.xmpp._BaseGateway__dispatcher._on_user_register(
                Iq(sfrom="romeo@montague.lit/gajim")
            )
        )
        welcome = self.next_sent()
        assert welcome["body"], welcome
        stanza = self.next_sent()
        assert "logging in" in stanza["status"].lower(), stanza
        stanza = self.next_sent()
        assert "syncing contacts" in stanza["status"].lower(), stanza
        if BaseGateway.get_self_or_unique_subclass().GROUPS:
            stanza = self.next_sent()
            assert "syncing groups" in stanza["status"].lower(), stanza
        for _ in range(n_contacts):
            probe = self.next_sent()
            assert probe.get_type() == "probe"
        stanza = self.next_sent()
        assert "yup" in stanza["status"].lower(), stanza
        self.romeo: BaseSession = BaseSession.get_self_or_unique_subclass().from_jid(
            JID("romeo@montague.lit")
        )

        self.juliet: LegacyContact = self.run_coro(
            self.romeo.contacts.by_legacy_id("juliet")
        )
        self.room: LegacyMUC = self.run_coro(self.romeo.bookmarks.by_legacy_id("room"))
        self.first_witch: LegacyParticipant = self.run_coro(
            self.room.get_participant("firstwitch")
        )
        self.send(  # language=XML
            """
            <iq type="get"
                to="romeo@montague.lit"
                id="1"
                from="aim.shakespeare.lit">
              <pubsub xmlns="http://jabber.org/protocol/pubsub">
                <items node="urn:xmpp:avatar:metadata" />
              </pubsub>
            </iq>
            """
        )

    @classmethod
    def tearDownClass(cls):
        reset_subclasses()


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


def reset_subclasses():
    """
    Reset registered subclasses between test classes.

    Needed because these classes are meant to only be subclassed once and raise
    exceptions otherwise.
    """
    BaseSession.reset_subclass()
    BaseGateway.reset_subclass()
    LegacyRoster.reset_subclass()
    LegacyContact.reset_subclass()
    LegacyMUC.reset_subclass()
    LegacyBookmarks.reset_subclass()
    LegacyParticipant.reset_subclass()
    # reset_commands()


def reset_commands():
    Command.subclasses = [
        c for c in Command.subclasses if str(c).startswith("<class 'slidge.core")
    ]
