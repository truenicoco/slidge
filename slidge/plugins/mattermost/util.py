from typing import Optional

from slidge.plugins.mattermost.api import MattermostClient


def get_client_from_registration_form(f: dict[str, Optional[str]]):
    url = (f.get("url") or "") + (f.get("basepath") or "")
    return MattermostClient(
        url,
        verify_ssl=f["strict_ssl"],
        timeout=5,
        token=f["token"],
    )
