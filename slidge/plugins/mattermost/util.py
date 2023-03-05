from typing import Optional

import emoji as emoji_lib

from slidge.plugins.mattermost.api import MattermostClient


def get_client_from_registration_form(f: dict[str, Optional[str]]):
    url = (f.get("url") or "") + (f.get("basepath") or "")
    return MattermostClient(
        url,
        verify_ssl=f["strict_ssl"],
        timeout=5,
        token=f["token"],
    )


def _emoji_name_conversion(x: str):
    return x.replace("_3_", "_three_").replace("thumbsup", "+1")


def emojize(x: str):
    return emoji_lib.emojize(f":{_emoji_name_conversion(x)}:", language="alias")


def demojize(emoji_char: str):
    return _emoji_name_conversion(
        emoji_lib.demojize(emoji_char, delimiters=("", ""), language="alias")
    )
