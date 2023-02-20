from typing import Optional

import emoji

from slidge.plugins.mattermost.api import MattermostClient


def get_client_from_registration_form(f: dict[str, Optional[str]]):
    url = (f.get("url") or "") + (f.get("basepath") or "")
    return MattermostClient(
        url,
        verify_ssl=f["strict_ssl"],
        timeout=5,
        token=f["token"],
    )


def emojize(x: str):
    return emoji.emojize(x.replace("_3_", "_three_"), language="alias")
