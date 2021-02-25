from slixmpp import JID

from slidge.database import User
from slidge.session import sessions
from slidge.test import SlixGatewayTest


class TestSession(SlixGatewayTest):
    def setUp(self):
        self.stream_start()
        self.user = User(jid=JID("jabberuser@example.com"), legacy_id="legacy_id")
        self.user.commit()

    def test_get(self):
        session = sessions[self.user]
        assert (
            sessions.by_jid(self.user.jid)
            is sessions.by_legacy_id(self.user.legacy_id)
            is session
            is sessions[self.user]
        )

    def test_destroy_by_jid(self):
        assert len(sessions) == 0
        sessions[self.user]
        assert len(sessions) == 1
        sessions.destroy_by_jid(self.user.jid)
        assert len(sessions) == 0