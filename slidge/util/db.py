"""
This module covers a backend for storing user data persistently and managing a
pseudo-roster for the gateway component.
"""

import dataclasses
import datetime
import logging
import os.path
import shelve
from io import BytesIO
from os import PathLike
from typing import Iterable, Optional, Union

from pickle_secure import Pickler, Unpickler
from slixmpp import JID, Iq, Message, Presence


# noinspection PyUnresolvedReferences
class EncryptedShelf(shelve.DbfilenameShelf):
    cache: dict
    dict: dict
    writeback: bool
    keyencoding: str
    _protocol: int

    def __init__(
        self, filename: PathLike, key: str, flag="c", protocol=None, writeback=False
    ):
        super().__init__(str(filename), flag, protocol, writeback)
        self.secret_key = key

    def __getitem__(self, key):
        try:
            value = self.cache[key]
        except KeyError:
            f = BytesIO(self.dict[key.encode(self.keyencoding)])
            value = Unpickler(f, key=self.secret_key).load()
            if self.writeback:
                self.cache[key] = value
        return value

    def __setitem__(self, key, value):
        if self.writeback:
            self.cache[key] = value
        f = BytesIO()
        p = Pickler(f, self._protocol, key=self.secret_key)
        p.dump(value)
        self.dict[key.encode(self.keyencoding)] = f.getvalue()


@dataclasses.dataclass
class GatewayUser:
    """
    A dataclass representing a gateway user
    """

    bare_jid: str
    """Bare JID of the user"""
    registration_form: dict[str, Optional[str]]
    """Content of the registration form, as a dict"""
    plugin_data: Optional[dict] = None
    registration_date: Optional[datetime.datetime] = None

    def __hash__(self):
        return hash(self.bare_jid)

    def __repr__(self):
        return f"<User {self.bare_jid}>"

    def __post_init__(self):
        if self.registration_date is None:
            self.registration_date = datetime.datetime.now()

    @property
    def jid(self) -> JID:
        """
        The user's (bare) JID

        :return:
        """
        return JID(self.bare_jid)

    def get(self, field: str, default: str = "") -> Optional[str]:
        """
        Get fields from the registration form (required to comply with slixmpp backend protocol)

        :param field: Name of the field
        :param default: Default value to return if the field is not present

        :return: Value of the field
        """
        return self.registration_form.get(field, default)

    def commit(self):
        user_store.commit(self)


class UserStore:
    """
    Basic user store implementation using shelve from the python standard library

    Set_file must be called before it is usable
    """

    def __init__(self):
        self._users: shelve.Shelf[GatewayUser] = None  # type: ignore

    def set_file(self, filename: PathLike, secret_key: Optional[str] = None):
        """
        Set the file to use to store user data

        :param filename: Path to the shelf file
        :param secret_key: Secret key to store files encrypted on disk
        """
        if self._users is not None:
            raise RuntimeError("Shelf file already set!")
        if os.path.exists(filename):
            log.info("Using existing slidge DB: %s", filename)
        else:
            log.info("Creating a new slidge DB: %s", filename)
        if secret_key:
            self._users = EncryptedShelf(filename, key=secret_key)
        else:
            self._users = shelve.open(str(filename))
        log.info("Registered users in the DB: %s", list(self._users.keys()))

    def get_all(self) -> Iterable[GatewayUser]:
        """
        Get all users in the store

        :return: An iterable of GatewayUsers
        """
        return self._users.values()

    def add(self, jid: JID, registration_form: dict[str, Optional[str]]):
        """
        Add a user to the store.

        NB: there is no reason to call this manually, as this should be covered
        by slixmpp XEP-0077 and XEP-0100 plugins

        :param jid: JID of the gateway user
        :param registration_form: Content of the registration form (:xep:`0077`)
        """
        log.debug("Adding user %s", jid)
        self._users[jid.bare] = GatewayUser(
            bare_jid=jid.bare,
            registration_form=registration_form,
            registration_date=datetime.datetime.now(),
        )
        self._users.sync()
        log.debug("Store: %s", self._users)

    def commit(self, user: GatewayUser):
        self._users[user.bare_jid] = user
        self._users.sync()

    def get(self, _gateway_jid, _node, ifrom: JID, iq) -> Optional[GatewayUser]:
        """
        Get a user from the store

        NB: there is no reason to call this, it is used by SliXMPP internal API

        :param _gateway_jid:
        :param _node:
        :param ifrom:
        :param iq:
        :return:
        """
        if ifrom is None:  # bug in SliXMPP's XEP_0100 plugin
            ifrom = iq["from"]
        log.debug("Getting user %s", ifrom.bare)
        return self._users.get(ifrom.bare)

    def remove(self, _gateway_jid, _node, ifrom: JID, _iq):
        """
        Remove a user from the store

        NB: there is no reason to call this, it is used by SliXMPP internal API
        """
        self.remove_by_jid(ifrom)

    def remove_by_jid(self, jid: JID):
        """
        Remove a user from the store, by JID
        """
        j = jid.bare
        log.debug("Removing user %s", j)
        del self._users[j]
        self._users.sync()

    def get_by_jid(self, jid: JID) -> Optional[GatewayUser]:
        """
        Convenience function to get a user from their JID.

        :param jid: JID of the gateway user
        :return:
        """
        return self._users.get(jid.bare)

    def get_by_stanza(self, s: Union[Presence, Message, Iq]) -> Optional[GatewayUser]:
        """
        Convenience function to get a user from a stanza they sent.

        :param s: A stanza sent by the gateway user
        :return:
        """
        return self.get_by_jid(s.get_from())

    def close(self):
        self._users.sync()
        self._users.close()


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


user_store = UserStore()

log = logging.getLogger(__name__)
