"""
XEP-0184 Delivery Receipts

The corresponding slixmpp module is a bit too rigid, this is our implementation
to selectively choose when we send delivery receipts
"""

from typing import TYPE_CHECKING

from slixmpp import JID, Message
from slixmpp.types import MessageTypes

if TYPE_CHECKING:
    from slidge.core.gateway import BaseGateway


class DeliveryReceipt:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp

    def ack(self, msg: Message):
        """
        Send a XEP-0184 (delivery receipt) in response to a message,
        if appropriate.

        :param msg:
        """
        if not self.requires_receipt(msg):
            return
        ack = self.make_ack(msg["id"], msg["to"], msg["from"].bare, msg["type"])
        ack.send()

    def make_ack(self, msg_id: str, mfrom: JID, mto: JID, mtype: MessageTypes = "chat"):
        ack = self.xmpp.Message()
        ack["type"] = mtype
        ack["to"] = mto
        ack["from"] = mfrom
        ack["receipt"] = msg_id
        return ack

    def requires_receipt(self, msg: Message):
        """
        Check if a message is eligible for a delivery receipt.

        :param msg:
        :return:
        """
        return (
            msg["request_receipt"]
            and msg["type"] in self.xmpp.plugin["xep_0184"].ack_types
            and not msg["receipt"]
        )
