Slidge 🛷
========

Pythonic XMPP gateways.

[![Documentation status](https://readthedocs.org/projects/slidge/badge/?version=latest)](https://slidge.readthedocs.io/)
[![builds.sr.ht status](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml?)
[![pypi](https://badge.fury.io/py/slidge.svg)](https://pypi.org/project/slidge/)

Slidge is a general purpose XMPP gateway framework in python

Homepage: [sourcehut](https://sr.ht/~nicoco/slidge)

Chat room:
[slidge\@conference.nicoco.fr](xmpp:slidge@conference.nicoco.fr?join)

Issue tracker: https://todo.sr.ht/~nicoco/slidge

Status
------

Slidge is alpha-grade software!
Right now, only direct messages are implemented, no group chat stuff at all.
Direct messaging does (more or less) work for the 5 plugins included in this repo though:
Telegram, Signal, Facebook messenger, Skype and Hackernews.

Testing locally should be fairly easy, so please go ahead and give me some
feedback, through the [MUC](xmpp:slidge@conference.nicoco.fr?join), the
[issue tracker](https://todo.sr.ht/~nicoco/slidge) or in my
[public inbox](https://lists.sr.ht/~nicoco/public-inbox).

Installation
------------

Clone the repo and turn it up using:

```bash
git clone https://git.sr.ht/~nicoco/slidge
cd slidge
docker-compose up
```

Open [gajim](https://gajim.org) and connect add an account ``test@localhost`` with the ``password``
password.
Go to "Accounts"→"Discover services".
You

You can also install slidge from [pypi](https://pypi.org/project/slidge/).

About privacy
-------------

Slidge (and most if not all XMPP gateway that I know of) will break
end-to-end encryption, or more precisely one of the \'ends\' become the
gateway itself. If privacy is a major concern for you, my advice would
be to:

-   use XMPP + OMEMO
-   self-host your gateways
-   have your gateways hosted by someone you know AFK

Related projects
----------------

-   [Spectrum](https://www.spectrum.im/)
-   [Bitfrost](https://github.com/matrix-org/matrix-bifrost)
-   [Mautrix](https://github.com/mautrix)
-   [matterbridge](https://github.com/42wim/matterbridge)
-   [XMPP-discord-bridge](https://git.polynom.me/PapaTutuWawa/xmpp-discord-bridge)

