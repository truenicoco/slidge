"""
Commit bot for the slidge MUC

Come say hi! xmpp:slidge@conference.nicoco.fr?join
"""

import os
import subprocess
from argparse import ArgumentParser

from slixmpp import ClientXMPP, ElementBase, register_stanza_plugin, JID
from slixmpp.plugins.xep_0049.stanza import PrivateXML


def get_storage(storage_name):
    class Storage(ElementBase):
        name = storage_name
        namespace = name
        plugin_attrib = name
        interfaces = {name}
        sub_interfaces = {name}

    register_stanza_plugin(PrivateXML, Storage)
    return Storage


def get_commits():
    return subprocess.check_output(["git", "rev-list", "master"]).decode().split()


def get_commit_msg(commit: str):
    return subprocess.check_output(
        ["git", "log", "--format=%B", "-n", "1", commit]
    ).decode()


class CommitBot(ClientXMPP):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.register_plugin("xep_0045")
        self.register_plugin("xep_0049")
        self.add_event_handler("session_start", self.session_start)

    async def session_start(self, _event):
        last_commit_published = await self.retrieve(REPO)
        await self["xep_0045"].join_muc(ROOM, BOT_JID.split("@")[0])

        commits = list(reversed(get_commits()))
        for i, commit in enumerate(commits):
            if commit == last_commit_published:
                break
        else:
            print("Apparently we don't have anything to publish")
            await self.disconnect()
            return
        for commit in commits[i + 1:]:
            print("Notifying about", commit)
            self.send_message(
                mto=JID(ROOM),
                mbody=f"{URL}{commit[:HASH_LEN]} - {get_commit_msg(commit.strip())}",
                mtype="groupchat",
            )

        await self.store(REPO, commits[-1])
        await self.disconnect()

    async def retrieve(self, key):
        get_storage(key)
        return (await self["xep_0049"].retrieve(key))["private"][key][key]

    async def store(self, key, value):
        cls = get_storage(key)
        x = cls()
        x[key] = value
        await self["xep_0049"].store(x)


def main():
    with open(os.path.expanduser(BOT_PASS_FILE)) as f:
        password = f.read().strip()

    bot = CommitBot(jid=BOT_JID, password=password)
    bot.connect()
    bot.loop.run_until_complete(bot.disconnected)


parser = ArgumentParser()
parser.add_argument("-m", "--muc")
args = parser.parse_args()

ROOM = "slidge@conference.nicoco.fr"
REPO = "slidge"
BOT_PASS_FILE = "~/.c3p0"
BOT_JID = "c3p0@slidge.im"
HASH_LEN = 5
URL = f"https://git.sr.ht/~nicoco/{REPO}/commit/"

main()
