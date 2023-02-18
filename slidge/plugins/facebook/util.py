import shelve
from collections import OrderedDict
from dataclasses import dataclass

from maufbapi import AndroidState
from maufbapi.types import mqtt as mqtt_t

from slidge import global_config


def get_shelf_path(user_bare_jid):
    return str(global_config.HOME_DIR / user_bare_jid)


def save_state(user_bare_jid: str, state: AndroidState):
    shelf_path = get_shelf_path(user_bare_jid)
    with shelve.open(shelf_path) as shelf:
        shelf["state"] = state


@dataclass
class FacebookMessage:
    mid: str
    timestamp_ms: int


class Messages:
    def __init__(self):
        self.by_mid: OrderedDict[str, FacebookMessage] = OrderedDict()
        self.by_timestamp_ms: OrderedDict[int, FacebookMessage] = OrderedDict()

    def __len__(self):
        return len(self.by_mid)

    def add(self, m: FacebookMessage):
        self.by_mid[m.mid] = m
        self.by_timestamp_ms[m.timestamp_ms] = m

    def pop_up_to(self, approx_t: int) -> FacebookMessage:
        i = 0
        for i, t in enumerate(self.by_timestamp_ms.keys()):
            if t > approx_t:
                i -= 1
                break
        for j, t in enumerate(list(self.by_timestamp_ms.keys())):
            msg = self.by_timestamp_ms.pop(t)
            self.by_mid.pop(msg.mid)
            if j == i:
                return msg
        else:
            raise KeyError(approx_t)


def is_group_thread(t: mqtt_t.ThreadKey):
    return t.other_user_id is None and t.thread_fbid is not None
