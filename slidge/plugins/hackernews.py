"""
Hackernews slidge plugin

Will poll replies to items you've posted.
For every reply, the chat window '<REPLY_ITEM_ID>@<BRIDGE_JID>' should open.
It will contain your original post (as a carbon) and its reply as a normal chat message.
You can re-reply by replying in your XMPP client directly.
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Optional

import aiohttp
from slixmpp import JID
from slixmpp.exceptions import XMPPError

from slidge import *


class Gateway(BaseGateway):
    # FIXME: implement proper login process, but we might have to do something to handle captcha
    REGISTRATION_INSTRUCTIONS = (
        "Enter the hackernews cookie from your browser's dev console "
        "(something like your-user-name ampersand XXXXXXXXXXXXXXXXXXXXXXXXX)"
    )
    REGISTRATION_FIELDS = [
        FormField(var="cookie", label="'user' cookie", required=True),
    ]

    ROSTER_GROUP = "HN"  # Not used, we don't add anything to the roster

    COMPONENT_NAME = "Hackernews (slidge)"
    COMPONENT_TYPE = "hackernews"

    COMPONENT_AVATAR = "https://news.ycombinator.com/favicon.ico"

    async def validate(
        self, user_jid: JID, registration_form: dict[str, Optional[str]]
    ):
        if registration_form["cookie"] is None:
            raise ValueError("A cookie is required")
        async with aiohttp.ClientSession(
            cookies={"user": registration_form["cookie"]}
        ) as session:
            async with session.get(LOGIN_URL, allow_redirects=False) as r:
                log.debug("Login response: %s - %s", r.status, await r.text())
                if r.status != 302:
                    raise ValueError("Cookie does not seem valid")


class Session(BaseSession[LegacyContact, LegacyRoster, Gateway]):
    http_session: aiohttp.ClientSession
    highest_handled_submission_id: int
    hn_username: str

    def post_init(self):
        self.http_session = aiohttp.ClientSession(
            cookies={"user": self.user.registration_form["cookie"]}
        )
        self.highest_handled_submission_id = 0
        self.hn_username = self.user.registration_form["cookie"].split("&")[0]

    async def login(self):
        kid_ids: list[int] = []
        for submission_id in await self.get_user_submissions():
            user_submission = await self.get_item(submission_id)
            for kid_id in user_submission.get("kids", []):
                kid_ids.append(kid_id)

        if kid_ids:
            self.highest_handled_submission_id = max(kid_ids)

        self.xmpp.loop.create_task(self.main_loop())
        return f"Logged as {self.hn_username}"

    async def main_loop(self):
        kid_ids: list[int] = []
        while True:
            kid_ids.clear()
            for submission_id in await self.get_user_submissions():
                user_submission = await self.get_item(submission_id)
                for kid_id in user_submission.get("kids", []):
                    if kid_id <= self.highest_handled_submission_id:
                        continue
                    await self.send_own_and_reply(user_submission, kid_id)
                    kid_ids.append(kid_id)
            if kid_ids:
                self.highest_handled_submission_id = max(kid_ids)
            await asyncio.sleep(POLL_INTERVAL)

    async def get_user_submissions(self) -> list[int]:
        log.debug("Getting user subs: %s", self.hn_username)
        async with self.http_session.get(
            f"{API_URL}/user/{self.hn_username}.json"
        ) as r:
            if r.status != 200:
                log.warning("Bad response from API: %s", r)
                raise RuntimeError
            return (await r.json())["submitted"]

    async def send_own_and_reply(self, user_submission, reply_id):
        contact: LegacyContact = self.contacts.by_legacy_id(reply_id)
        date = datetime.fromtimestamp(user_submission["time"])
        contact.carbon(
            parse_comment_text(user_submission["text"]),
            date=date,
        )
        kid = await self.get_item(reply_id)
        contact.send_text(parse_comment_text(kid["text"]))

    async def get_item(self, item_id):
        async with self.http_session.get(f"{API_URL}/item/{item_id}.json") as r:
            return await r.json()

    async def logout(self):
        pass

    async def send_text(self, t: str, c: LegacyContact, *, reply_to_msg_id=None):
        goto = f"threads?id={self.hn_username}#{c.legacy_id}"
        url = f"{REPLY_URL}?id={c.legacy_id}&goto={goto}"
        async with self.http_session.get(url) as r:
            if r.status != 200:
                raise XMPPError(text="Couldn't get the post reply web page from HN")
            form_page_content = await r.text()
        match = re.search(HMAC_RE, form_page_content)

        if match is None:
            raise XMPPError(
                text="Couldn't find the HMAC hidden input on the comment reply thread"
            )

        await asyncio.sleep(SLEEP_BEFORE_POST)

        form_dict = {
            "hmac": match.group(1),
            "parent": c.legacy_id,
            "text": t,
            "goto": goto,
        }

        form = aiohttp.FormData(form_dict)
        async with self.http_session.post(REPLY_POST_URL, data=form) as r:
            first_attempt_html_content = await r.text()

        log.debug("Reply response #1: %s", r)
        if r.status != 200:
            raise XMPPError(text=f"Problem replying: {r}")

        if REPOST_TEXT in first_attempt_html_content:
            match = re.search(HMAC_RE, first_attempt_html_content)
            if match is None:
                raise XMPPError(
                    text="We should repost but haven't found any hmac field on the repost page"
                )

            await asyncio.sleep(SLEEP_BEFORE_POST2)
            form = aiohttp.FormData(form_dict | {"hmac": match.group(1)})
            async with self.http_session.post(REPLY_POST_URL, data=form) as r:
                log.debug("Reply response #2: %s", r)
                if r.status != 200:
                    raise XMPPError(text=f"Problem replying: {r}")

    # none of the following make sense in a HN context,
    # this is just to avoid raising NotImplementedErrors
    async def send_file(self, u: str, c: LegacyContact, *, reply_to_msg_id=None):
        pass

    async def active(self, c: LegacyContact):
        pass

    async def inactive(self, c: LegacyContact):
        pass

    async def composing(self, c: LegacyContact):
        pass

    async def paused(self, c: LegacyContact):
        pass

    async def displayed(self, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def correct(self, text: str, legacy_msg_id: Any, c: LegacyContact):
        pass

    async def search(self, form_values: dict[str, str]):
        pass


def parse_comment_text(text: str):
    # TODO: use regex or something more efficient here
    return text.replace("<p>", "\n").replace("&#x27;", "'").replace("&#x2F;", "/")


LOGIN_URL = "https://news.ycombinator.com/login"
REPLY_URL = "https://news.ycombinator.com/reply"
REPLY_POST_URL = "https://news.ycombinator.com/comment"
API_URL = "https://hacker-news.firebaseio.com/v0"
POLL_INTERVAL = 30  # seconds
SLEEP_BEFORE_POST = 30  # seconds
SLEEP_BEFORE_POST2 = 5  # seconds
REPOST_TEXT = "<tr><td>Please confirm that this is your comment by submitting it one"
HMAC_RE = re.compile(r'name="hmac" value="([a-zA-Z\d]*)"')

log = logging.getLogger(__name__)
