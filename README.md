Slidge 🛷
========

[Home](https://sr.ht/~nicoco/slidge) |
[Source](https://sr.ht/~nicoco/slidge/sources) |
[Issues](https://sr.ht/~nicoco/slidge/trackers) |
[Patches](https://lists.sr.ht/~nicoco/public-inbox) |
[Chat](xmpp:slidge@conference.nicoco.fr?join)

Turn any XMPP client into that fancy multiprotocol chat app that every cool kid want.

[![Documentation status](https://readthedocs.org/projects/slidge/badge/?version=latest)](https://slidge.readthedocs.io/)
[![builds.sr.ht status](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml?)
[![pypi](https://badge.fury.io/py/slidge.svg)](https://pypi.org/project/slidge/)

Slidge is a general purpose XMPP (puppeteer) gateway framework in python.
It's a work in progress, but it should make
[writing gateways to other chat networks](https://slidge.readthedocs.io/en/latest/dev/tutorial.html)
(*plugins*) as frictionless as possible.

It comes with a few plugins included, implementing at least basic direct messaging and often more "advanced"
instant messaging features:

|            | ⏻[¹] | …[²] | ✓[³] | 🗎[⁴] | ✎[⁵] | ☺[⁶] | 🗑[⁷]  | ↵[⁸] | 
|------------|------|------|------|-------|------|------|--------|------|
| Signal     | -    | ✓    | ✓    | ✓     | -    | ✓    | ✓      | ✓    |
| Telegram   | ✓    | ✓    | ✓    | ✓     | ✓    | ✓    | ✓      | ✓    |
| Discord    | ✗    | ✓    | -    | ✓     | ✓    | ~    | ✓      | ✓    |
| Steam      | ✓    | ✓    | -    | ✗     | -    | ~    | -      | -    |
| Mattermost | ~    | ✓    | -    | ✓     | ✓    | ✓    | ✓      | ✗    |
| Facebook   | ✗    | ✓    | ✓    | ✓     | ✓    | ✓    | ✓      | ✓    |
| Skype      | ✗    | ✗    | ✗    | ~     | ✗    | ✗    | ✗      | ✗    |


[¹]: https://xmpp.org/rfcs/rfc6121.html#presence
[²]: https://xmpp.org/extensions/xep-0085.html
[³]: https://xmpp.org/extensions/xep-0333.html
[⁴]: https://xmpp.org/extensions/xep-0363.html
[⁵]: https://xmpp.org/extensions/xep-0308.html
[⁶]: https://xmpp.org/extensions/xep-0444.html
[⁷]: https://xmpp.org/extensions/xep-0424.html
[⁸]: https://xmpp.org/extensions/xep-0461.html


(this table may not be entirely accurate, but **in theory**, stuff marked ✓ works)

NB: - means that the legacy network does not have an equivalent of this XMPP feature
    (because XMPP is better, what did you think?)

**WARNING**: you may break the terms of use of a legacy network and end up getting your account locked
by using slidge. Refer to the [keeping a low profile](https://slidge.readthedocs.io/en/latest/user/low_profile.html)
documentation page for more info.

Status
------

Slidge is alpha-grade software.
Right now, only direct messages are implemented, no group chat stuff at all.
Direct messaging does (more or less) work though.
Any contribution whatsoever (testing, patches, suggestions, beer, …) is more than welcome.
Don't be shy!

Testing locally should be fairly easy, so please go ahead and give me some
feedback, through the [MUC](xmpp:slidge@conference.nicoco.fr?join), the
[issue tracker](https://todo.sr.ht/~nicoco/slidge) or in my
[public inbox](https://lists.sr.ht/~nicoco/public-inbox).

Installation
------------

#### docker-compose

Docker-compose spins up a local XMPP server preconfigured for you., with a ``test@localhost`` / ``password``
account

```sh
docker-compose up
```

For the other options, you need a
[configured](https://slidge.readthedocs.io/en/latest/admin/general.html#configure-the-xmpp-server)
XMPP server.

#### poetry

```sh
poetry install --extras signal  # you can replace signal with any network listed in the table above
poetry run python -m slidge --legacy-module=slidge.plugins.signal
```

#### pip

```sh
pip install slidge[signal]  # you can replace signal with any network listed in the table above
python -m slidge --legacy-module=slidge.plugins.signal
```

### XMPP client

#### movim

If you used docker-compose, you should be able to use the [movim](https://movim.eu) client
from your browser at http://localhost:8888

Unfortunately, the movim UI thinks that ``test@localhost`` is not a valid JID and does not let you click
on the "Connect" button.
As a workaround, use your browser dev tools to inspect and modify the ``<input id="username"`` in order to
remove the ``pattern="^[^...`` attribute.

Then go to the Configuration/Account tab. You should be able to register to the slidge gateways from here.

#### Gajim

Install and launch [gajim](https://gajim.org) and add your XMPP account.
Go to "Accounts"→"Discover services".
You should see the slidge gateways as server components.

About privacy
-------------

Slidge (and most if not all XMPP gateway that I know of) will break
end-to-end encryption, or more precisely one of the 'ends' become the
gateway itself. If privacy is a major concern for you, my advice would
be to:

-   use XMPP + OMEMO
-   self-host your gateways
-   have your gateways hosted by someone you know AFK and trust

Related projects
----------------

-   [Spectrum](https://www.spectrum.im/)
-   [Bitfrost](https://github.com/matrix-org/matrix-bifrost)
-   [Mautrix](https://github.com/mautrix)
-   [matterbridge](https://github.com/42wim/matterbridge)
-   [XMPP-discord-bridge](https://git.polynom.me/PapaTutuWawa/xmpp-discord-bridge)
