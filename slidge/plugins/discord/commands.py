from typing import Optional

import discord as di
from slixmpp import JID

from slidge import FormField, XMPPError
from slidge.core.command import Command, CommandAccess, Form, TableResult

from .session import Session


class ListGuilds(Command):
    NAME = 'List your discord "servers"'
    HELP = "List your discord servers and their channels"
    CHAT_COMMAND = NODE = "servers"
    ACCESS = CommandAccess.USER_LOGGED

    async def run(self, session, ifrom: JID, *args):
        assert isinstance(session, Session)
        guilds = session.discord.guilds
        return Form(
            title="Your discord servers",
            instructions="Select a server to view its text channels",
            fields=[
                FormField(
                    "guild_id",
                    "Discord servers",
                    required=True,
                    type="list-single",
                    options=[
                        {"label": g.name, "value": str(i)} for i, g in enumerate(guilds)
                    ],
                )
            ],
            handler=self.list_guilds,
            handler_args=(guilds,),
        )

    @staticmethod
    async def list_guilds(
        form_values: dict[str, str], session: "Session", _ifrom, guilds: list[di.Guild]
    ):
        try:
            guild_id = int(form_values["guild_id"])
            guild = guilds[int(guild_id)]
        except (ValueError, IndexError, KeyError):
            raise XMPPError("bad-request")
        channels = [
            await session.bookmarks.by_legacy_id(c.id)
            for c in guild.channels
            if isinstance(c, di.TextChannel)
        ]
        return TableResult(
            fields=[
                FormField("name", "Name"),
                FormField("n_participants", "Number of participants"),
                FormField("jid", "JID", type="jid-single"),
            ],
            description=f"Text channels of server {guild}",
            items=[
                {"name": c.name, "n_participants": c.n_participants, "jid": c.jid}
                for c in channels
            ],
            jids_are_mucs=True,
        )
