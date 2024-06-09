from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from slixmpp import JID, Iq
from slixmpp.exceptions import XMPPError

from ...db import GatewayUser

if TYPE_CHECKING:
    from .base import BaseGateway


class Registration:
    def __init__(self, xmpp: "BaseGateway"):
        self.xmpp = xmpp
        xmpp["xep_0077"].api.register(
            self.xmpp.make_registration_form, "make_registration_form"
        )
        xmpp["xep_0077"].api.register(self._user_get, "user_get")
        xmpp["xep_0077"].api.register(self._user_validate, "user_validate")
        xmpp["xep_0077"].api.register(self._user_modify, "user_modify")
        # kept for slixmpp internal API compat
        # TODO: either fully use slixmpp internal API or rewrite registration without it at all
        xmpp["xep_0077"].api.register(lambda *a: None, "user_remove")

    def get_user(self, jid: JID) -> GatewayUser | None:
        return self.xmpp.store.users.get(jid)

    async def _user_get(
        self, _gateway_jid, _node, ifrom: JID, iq: Iq
    ) -> GatewayUser | None:
        if ifrom is None:
            ifrom = iq.get_from()
        return self.get_user(ifrom)

    async def _user_validate(self, _gateway_jid, _node, ifrom: JID, iq: Iq):
        xmpp = self.xmpp
        log.debug("User validate: %s", ifrom.bare)
        form_dict = {f.var: iq.get(f.var) for f in xmpp.REGISTRATION_FIELDS}
        xmpp.raise_if_not_allowed_jid(ifrom)
        legacy_module_data = await xmpp.user_prevalidate(ifrom, form_dict)
        if legacy_module_data is None:
            legacy_module_data = form_dict
        user = self.xmpp.store.users.new(
            jid=ifrom,
            legacy_module_data=legacy_module_data,  # type:ignore
        )
        log.info("New user: %s", user)

    async def _user_modify(
        self, _gateway_jid, _node, ifrom: JID, form_dict: dict[str, Optional[str]]
    ):
        await self.xmpp.user_prevalidate(ifrom, form_dict)
        log.debug("Modify user: %s", ifrom)
        user = self.xmpp.store.users.get(ifrom)
        if user is None:
            raise XMPPError("internal-server-error", "User not found")
        user.legacy_module_data.update(form_dict)
        self.xmpp.store.users.update(user)


log = logging.getLogger(__name__)
