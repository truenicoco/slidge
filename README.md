Slidge 🛷
========

[Home](https://sr.ht/~nicoco/slidge) |
[Source](https://sr.ht/~nicoco/slidge/sources) |
[Issues](https://sr.ht/~nicoco/slidge/trackers) |
[Patches](https://lists.sr.ht/~nicoco/public-inbox) |
[Chat](xmpp:slidge@conference.nicoco.fr?join)

Turn any XMPP client into that fancy multiprotocol chat app that every cool kid want.

[![Documentation status](https://readthedocs.org/projects/slidge/badge/?version=latest)](https://slidge.readthedocs.io/)
[![builds.sr.ht status](https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml?)
[![Debian package](https://builds.sr.ht/~nicoco/slidge/commits/master/debian.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/debian.yml?)
[![pypi](https://badge.fury.io/py/slidge.svg)](https://pypi.org/project/slidge/)

Slidge is a general purpose XMPP (puppeteer) gateway framework in python.
It's a work in progress, but it should make
[writing gateways to other chat networks](https://slidge.readthedocs.io/en/latest/dev/tutorial.html)
(*plugins*) as frictionless as possible.

It comes with a few plugins included, implementing at least basic direct messaging and often more "advanced"
instant messaging features:

|            | Presences[¹] | Typing[²] | Marks[³] | Upload[⁴] | Edit[⁵] | React[⁶] | Retract[⁷] | Reply[⁸] | 
|------------|--------------|-----------|----------|-----------|---------|----------|------------|----------|
| Signal     | N/A          | ✅         | ✅        | ✅         | N/A     | ✅        | ✅          | ✅        |
| Telegram   | ✅            | ✅         | ✅        | ✅         | ✅       | ✅        | ✅          | ✅        |
| Discord    | ❌            | ✅         | N/A      | ✅         | ✅       | ~        | ✅          | ✅        |
| Steam      | ✅            | ✅         | N/A      | ❌         | N/A     | ~        | N/A        | N/A      |
| Mattermost | ~            | ✅         | ~        | ✅         | ✅       | ✅        | ✅          | ❌        |
| Facebook   | ❌            | ✅         | ✅        | ✅         | ✅       | ✅        | ✅          | ✅        |
| Skype      | ❌            | ✅         | ❌        | ✅         | ❌       | ❌        | ❌          | ❌        |


[¹]: https://xmpp.org/rfcs/rfc6121.html#presence
[²]: https://xmpp.org/extensions/xep-0085.html
[³]: https://xmpp.org/extensions/xep-0333.html
[⁴]: https://xmpp.org/extensions/xep-0363.html
[⁵]: https://xmpp.org/extensions/xep-0308.html
[⁶]: https://xmpp.org/extensions/xep-0444.html
[⁷]: https://xmpp.org/extensions/xep-0424.html
[⁸]: https://xmpp.org/extensions/xep-0461.html


This table may not be entirely accurate, but **in theory**, stuff marked ✅ works.
N/A means that the legacy network does not have an equivalent of this XMPP feature
(because XMPP is better, what did you think?).

**WARNING**: you may break the terms of use of a legacy network and end up getting your account locked
by using slidge. Refer to the
[keeping a low profile](https://slidge.readthedocs.io/en/latest/user/low_profile.html)
documentation page for more info.

Status
------

Slidge is beta-grade software.
Right now, only direct messages are implemented, no group chat stuff at all.
Direct messaging does (more or less) work though.
Any contribution whatsoever (testing, patches, suggestions, beer, …) is more than welcome.

Try slidge and give us some
feedback, through the [MUC](xmpp:slidge@conference.nicoco.fr?join), the
[issue tracker](https://todo.sr.ht/~nicoco/slidge) or in the
[public inbox](https://lists.sr.ht/~nicoco/public-inbox).
Don't be shy!

Installation
------------

### containers

Containers are available on [docker hub](https://hub.docker.com/u/nicocool84).

### debian

Debian packages are built on each push to master as artifacts of
[this build job](https://builds.sr.ht/~nicoco/slidge/commits/master/debian.yml?)

### pip

Tagged releases are uploaded to [pypi](https://pypi.org/project/slidge/).

```sh
pip install slidge[signal]  # you can replace signal with any network listed in the table above
python -m slidge --legacy-module=slidge.plugins.signal
```

If you're looking for the bleeding edge, download an artifact
[here](https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml?).

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
