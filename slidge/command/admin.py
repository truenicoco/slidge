# Commands only accessible for slidge admins
import functools
import logging
from typing import Any, Optional

from slixmpp import JID
from slixmpp.exceptions import XMPPError

from ..util.db import user_store
from ..util.types import AnyBaseSession
from .base import (
    Command,
    CommandAccess,
    Confirmation,
    Form,
    FormField,
    FormValues,
    TableResult,
)


class AdminCommand(Command):
    ACCESS = CommandAccess.ADMIN_ONLY


class Info(AdminCommand):
    # TODO: return uptime
    # TODO: return version
    NAME = "List registered users"
    HELP = "List the users registered to this gateway"
    NODE = CHAT_COMMAND = "info"

    async def run(self, _session, _ifrom, *_):
        items = []
        for u in user_store.get_all():
            d = u.registration_date
            if d is None:
                joined = ""
            else:
                joined = d.isoformat(timespec="seconds")
            items.append({"jid": u.bare_jid, "joined": joined})
        return TableResult(
            description="List of registered users",
            fields=[FormField("jid", type="jid-single"), FormField("joined")],
            items=items,  # type:ignore
        )


class DeleteUser(AdminCommand):
    NAME = "Delete a user"
    HELP = "Unregister a user from the gateway"
    NODE = CHAT_COMMAND = "delete_user"

    async def run(self, _session, _ifrom, *_):
        return Form(
            title="Remove a slidge user",
            instructions="Enter the bare JID of the user you want to delete",
            fields=[FormField("jid", type="jid-single", label="JID", required=True)],
            handler=self.delete,
        )

    async def delete(
        self, form_values: FormValues, _session: AnyBaseSession, _ifrom: JID
    ) -> Confirmation:
        jid: JID = form_values.get("jid")  # type:ignore
        user = user_store.get_by_jid(jid)
        if user is None:
            raise XMPPError("item-not-found", text=f"There is no user '{jid}'")

        return Confirmation(
            prompt=f"Are you sure you want to unregister '{jid}' from slidge?",
            success=f"User {jid} has been deleted",
            handler=functools.partial(self.finish, jid=jid),
        )

    async def finish(
        self, _session: Optional[AnyBaseSession], _ifrom: JID, jid: JID
    ) -> None:
        user = user_store.get_by_jid(jid)
        if user is None:
            raise XMPPError("bad-request", f"{jid} has no account here!")
        await self.xmpp.unregister_user(user)


class ChangeLoglevel(AdminCommand):
    NAME = "Change the verbosity of the logs"
    HELP = "Set the logging level"
    NODE = CHAT_COMMAND = "loglevel"

    async def run(self, _session, _ifrom, *_):
        return Form(
            title=self.NAME,
            instructions=self.HELP,
            fields=[
                FormField(
                    "level",
                    label="Log level",
                    required=True,
                    type="list-single",
                    options=[
                        {"label": "WARNING (quiet)", "value": str(logging.WARNING)},
                        {"label": "INFO (normal)", "value": str(logging.INFO)},
                        {"label": "DEBUG (verbose)", "value": str(logging.DEBUG)},
                    ],
                )
            ],
            handler=self.finish,
        )

    @staticmethod
    async def finish(
        form_values: FormValues, _session: AnyBaseSession, _ifrom: JID
    ) -> None:
        logging.getLogger().setLevel(int(form_values["level"]))  # type:ignore


class Exec(AdminCommand):
    NAME = HELP = "Exec arbitrary python code. SHOULD NEVER BE AVAILABLE IN PROD."
    CHAT_COMMAND = "!"
    NODE = "exec"
    ACCESS = CommandAccess.ADMIN_ONLY

    prev_snapshot = None

    context = dict[str, Any]()

    def __init__(self, xmpp):
        super().__init__(xmpp)

    async def run(self, session, ifrom: JID, *args):
        from contextlib import redirect_stdout
        from io import StringIO

        f = StringIO()
        with redirect_stdout(f):
            exec(" ".join(args), self.context)

        out = f.getvalue()
        if out:
            return f"```\n{out}\n```"
        else:
            return "No output"