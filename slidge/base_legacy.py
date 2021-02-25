"""
"""

import typing
from typing import List, Dict
from configparser import SectionProxy

from slixmpp import Message, Iq

from slidge.database import User
from slidge.muc import LegacyMuc
from slidge.buddy import Buddy
from slidge.plugins.xep_0100 import LegacyError as BaseLegacyError


class LegacyError(BaseLegacyError):
    """
    Base exception that legacy clients should raise whenever something goes
    wrong. :class:`BaseGateway` can then forwards `self.msg` to the gateway
    user if appropriate, e.g., legacy_buddy_id does not exist.
    """

    def __init__(self, msg: str):
        """
        msg: str
            an error message, that can be transmitted to the gateway user
        """
        self.msg = msg


class BaseLegacyClient:
    """
    Subclass me to develop plugins for various legacy networks!

    Not everything has to be subclassed, it's OK to ignore some of these
    events.
    """

    def __init__(self, xmpp=None):
        from slidge.gateway import BaseGateway

        self.xmpp: typing.Optional[BaseGateway] = xmpp
        self.config: typing.Optional[SectionProxy] = None

    async def validate(self, registration: typing.Dict):
        """
        Validates a gateway subscription request.

        Should raise :class:`ValueError` (msg: str) in case there is anything wrong
        with the registration request. msg will be displayed to the gateway
        user's XMPP client.

        :param iq: the user registration request. iq["registration"] is dict-like
            and contains the field defined in BaseGateway.REGISTRATION_FIELDS
        """

    async def login(self, user: User):
        """
        Login a gateway user to the legacy network.

        Should raise a :class:`LegacyError` in case the login fails.
        """

    async def logout(self, user: User):
        """
        Logout the user from the legacy network.
        """

    async def get_buddies(self, user: User) -> List[Buddy]:
        """
        Is called by the gateway to retrieve the roster equivalent on the
        legacy network and sync it with the XMPP user's roster.
        """

    async def muc_list(self, user: User) -> List[LegacyMuc]:
        """
        Is called by the gateway to retrieve the list of legacy MUCs the XMPP user
        is part of on the legacy network.
        """

    async def muc_occupants(self, user: User, legacy_group_id: str) -> List[str]:
        """
        Returns the list of occupants of a legacy groups.
        """

    async def send_receipt(self, user: User, receipt: Message):
        """
        Ack a message on the legacy network.
        """

    async def send_message(self, user: User, legacy_buddy_id: str, msg: Message):
        """
        Sends an XMPP message to the legacy network.

        :param msg: The XMPP message. Must be saved somehow if we want to ack it later
            or mark it read.
        """

    async def send_muc_message(self, user: User, legacy_group_id: str, msg: Message):
        """
        Sends an XMPP message to a legacy MUC.

        :param msg: The XMPP message. Must be saved somehow if we want to ack it later
            or mark it read.
        """

    async def send_composing(self, user: User, legacy_buddy_id: str):
        """
        Sends an composing chat state or equivalent to the legacy network.
        """

    async def send_pause(self, user: User, legacy_buddy_id: str):
        """
        Sends an paused chat state or equivalent to the legacy network.
        """

    async def send_read_mark(self, user: User, legacy_buddy_id: str, msg_id: str):
        """
        Mark a message as read on the legacy network.
        """
