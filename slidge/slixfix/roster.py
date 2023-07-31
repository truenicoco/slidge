from slixmpp import JID

from ..util.db import log, user_store


class YesSet(set):
    """
    A pseudo-set which always test True for membership
    """

    def __contains__(self, item):
        log.debug("Test in")
        return True


class RosterBackend:
    """
    A pseudo-roster for the gateway component.

    If a user is in the user store, this will behave as if the user is part of the
    roster with subscription "both", and "none" otherwise.

    This is rudimentary but the only sane way I could come up with so far.
    """

    @staticmethod
    def entries(_owner_jid, _default=None):
        return YesSet()

    @staticmethod
    def save(_owner_jid, _jid, _item_state, _db_state):
        pass

    @staticmethod
    def load(_owner_jid, jid, _db_state):
        log.debug("Load %s", jid)
        user = user_store.get_by_jid(JID(jid))
        log.debug("User %s", user)
        if user is None:
            return {
                "name": "",
                "groups": [],
                "from": False,
                "to": False,
                "pending_in": False,
                "pending_out": False,
                "whitelisted": False,
                "subscription": "both",
            }
        else:
            return {
                "name": "",
                "groups": [],
                "from": True,
                "to": True,
                "pending_in": False,
                "pending_out": False,
                "whitelisted": False,
                "subscription": "none",
            }