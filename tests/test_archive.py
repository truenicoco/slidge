import datetime
import random

import pytest
from slixmpp import Message, register_stanza_plugin
from slixmpp.plugins.xep_0203 import stanza
from slixmpp.plugins.xep_0359 import stanza as stanza_id

from slidge.core.muc.archive import MessageArchive


@pytest.fixture
def stanzas():
    register_stanza_plugin(Message, stanza.Delay)
    stanza_id.register_plugins()
    r = []
    for i in range(10):
        msg = Message()
        msg["body"] = str(i)
        msg["delay"]["stamp"] = datetime.datetime.now(
            tz=datetime.timezone.utc
        ) - datetime.timedelta(minutes=random.randint(0, 80))
        msg["stanza_id"]["id"] = str(i)
        r.append(msg)
    yield r


def test_insertion(stanzas):
    x = MessageArchive("1")
    assert len(list(x.get_all())) == 0
    x.add(stanzas[0])
    assert len(list(x.get_all())) == 1

    x = MessageArchive("2")
    assert len(list(x.get_all())) == 0
    x.add(stanzas[0])
    assert len(list(x.get_all())) == 1

    x = MessageArchive("3")
    assert len(list(x.get_all())) == 0
    x.add(stanzas[0])
    assert len(list(x.get_all())) == 1
    while stanzas:
        x.add(stanzas.pop())

    msgs = list(x.get_all())

    for m1, m2 in zip(msgs, msgs[1:]):
        assert m1.when <= m2.when

    assert msgs[-2].when <= msgs[-1].when
