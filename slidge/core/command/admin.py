"""
Commands only accessible for slidge admins
"""
import logging

from slixmpp import JID
from slixmpp.exceptions import XMPPError

from ...util.db import user_store
from .base import Command, CommandAccess, Confirmation, Form, FormField, TableResult


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
            items=items,
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

    @staticmethod
    async def delete(form_values: dict[str, str], _session, _ifrom):
        jid: JID = form_values.get("jid")  # type:ignore
        user = user_store.get_by_jid(jid)
        if user is None:
            raise XMPPError("item-not-found", text=f"There is no user '{jid}'")

        return Confirmation(
            prompt=f"Are you sure you want to unregister '{jid}' from slidge?",
            success=f"User {jid} has been deleted",
            handler=lambda *_: user_store.remove_by_jid(jid),
            handler_args=(jid,),
        )


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
    async def finish(form_values: dict[str, str], _session, _ifrom):
        logging.getLogger().setLevel(int(form_values["level"]))
