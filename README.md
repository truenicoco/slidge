Slidge 🛷
========

[![Documentation status](https://readthedocs.org/projects/slidge/badge/?version=latest)](https://slidge.readthedocs.io/)
[![builds.sr.ht status](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/.build.yml?)

Pythonic XMPP gateways.

An XMPP component that provides a generic way to write XMPP ⟷ **legacy network**
gateways, based on [SliXMPP](https://slixmpp.readthedocs.io).

Status
------

Slidge is not ready for production yet! Right now, only direct messages
are implemented, for Telegram, Signal and Facebook messenger. Please
test it locally and report bugs.

Installation
------------

The easiest way to try out slidge is with docker-compose. Clone the
repo, run `docker-compose up` and you should have:

-   an XMPP server (prosody) exposed on port 5222 with a registered user
    <test@localhost> (password: password)
-   3 gateway components (a dummy network, signal and telegram)
-   hot reloading of gateways on code change
-   signald running in a container (required for signal)

I recommend using gajim to test it. You can launch it with the -p option
to use a clean profile and not mess up your normal user settings and
such.

It is definitely possible to set up everything without docker, but note
that the aiotdlib package needs to be manually built (wheels from pypi
are incomplete unfortunately).

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

Homepage: [sourcehut](https://sr.hr/~nicoco/slidge)

Chat room:
[slidge\@conference.nicoco.fr](xmpp:slidge@conference.nicoco.fr?join)
