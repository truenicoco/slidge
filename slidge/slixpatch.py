from slixmpp import Iq

import slixmpp.plugins.xep_0077
import slixmpp.plugins.xep_0333


# patch for https://lab.louiz.org/poezio/slixmpp/-/issues/3469
def send_marker(self, mto, id: str, marker: str, thread=None, *, mfrom=None):
    if marker not in ("displayed", "received", "acknowledged"):
        raise ValueError("Invalid marker: %s" % marker)
    msg = self.xmpp.make_message(mto=mto, mfrom=mfrom)
    if thread:
        msg["thread"] = thread
    msg[marker]["id"] = id
    msg.send()


# patch to allow data forms instead of the more limited jabber:iq:register protocol
async def _handle_registration(self, iq: Iq):
    if iq["type"] == "get":
        await self._send_form(iq)
    elif iq["type"] == "set":
        form = iq["register"]["form"]
        if form:
            await self.handle_data_form(iq)
            return

        if iq["register"]["remove"]:
            try:
                await self.api["user_remove"](None, None, iq["from"], iq)
            except KeyError:
                slixmpp.plugins.xep_0077.register._send_error(
                    iq,
                    "404",
                    "cancel",
                    "item-not-found",
                    "User not found",
                )
            else:
                reply = iq.reply()
                reply.send()
                self.xmpp.event("user_unregister", iq)
            return

        for field in self.form_fields:
            if not iq["register"][field]:
                # Incomplete Registration
                slixmpp.plugins.xep_0077.register._send_error(
                    iq,
                    "406",
                    "modify",
                    "not-acceptable",
                    "Please fill in all fields.",
                )
                return

        try:
            await self.api["user_validate"](None, None, iq["from"], iq["register"])
        except ValueError as e:
            slixmpp.plugins.xep_0077.register._send_error(
                iq,
                "406",
                "modify",
                "not-acceptable",
                e.args,
            )
        else:
            reply = iq.reply()
            reply.send()
            self.xmpp.event("user_register", iq)


async def handle_data_form(self, iq: Iq):
    form = iq["register"]["form"].get_values()
    try:
        remove = form["remove"]
    except KeyError:
        pass
    else:
        if remove:
            try:
                await self.api["user_remove"](None, None, iq["from"], iq)
            except KeyError:
                slixmpp.plugins.xep_0077.register._send_error(
                    iq,
                    "404",
                    "cancel",
                    "item-not-found",
                    "User not found",
                )
            else:
                reply = iq.reply()
                reply.send()
                self.xmpp.event("user_unregister", iq)
            return

    try:
        await self.api["user_validate"](None, None, iq["from"], iq)
    except ValueError as e:
        slixmpp.plugins.xep_0077.register._send_error(
            iq,
            "406",
            "modify",
            "not-acceptable",
            e.args,
        )
    else:
        reply = iq.reply()
        reply.send()
        self.xmpp.event("user_register", iq)


slixmpp.plugins.xep_0333.XEP_0333.send_marker = send_marker
slixmpp.plugins.xep_0077.XEP_0077._handle_registration = _handle_registration
slixmpp.plugins.xep_0077.XEP_0077.handle_data_form = handle_data_form
