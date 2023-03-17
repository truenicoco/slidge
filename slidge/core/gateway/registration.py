import logging
from typing import TYPE_CHECKING, Optional

from slixmpp import JID, Iq

from ...util.db import user_store

if TYPE_CHECKING:
    from .base import BaseGateway


class Registration:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        xmpp["xep_0077"].api.register(
            user_store.get,
            "user_get",
        )
        xmpp["xep_0077"].api.register(
            user_store.remove,
            "user_remove",
        )
        xmpp["xep_0077"].api.register(
            self.xmpp.make_registration_form, "make_registration_form"
        )
        xmpp["xep_0077"].api.register(self._user_validate, "user_validate")
        xmpp["xep_0077"].api.register(self._user_modify, "user_modify")

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        """
        SliXMPP internal API stuff
        """
        xmpp = self.xmpp
        log.debug("User validate: %s", ifrom.bare)
        form_dict = {f.var: iq.get(f.var) for f in xmpp.REGISTRATION_FIELDS}
        xmpp.raise_if_not_allowed_jid(ifrom)
        await xmpp.user_prevalidate(ifrom, form_dict)
        log.info("New user: %s", ifrom.bare)
        user_store.add(ifrom, form_dict)

    async def _user_modify(
        self, _gateway_jid, _node, ifrom: JID, form_dict: dict[str, Optional[str]]
    ):
        """
        SliXMPP internal API stuff
        """
        user = user_store.get_by_jid(ifrom)
        log.debug("Modify user: %s", user)
        await self.xmpp.user_prevalidate(ifrom, form_dict)
        user_store.add(ifrom, form_dict)


log = logging.getLogger(__name__)
