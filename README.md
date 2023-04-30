![Slidge logo](./dev/assets/slidge-color-small.png)

[Home](https://sr.ht/~nicoco/slidge) |
[Docs](https://slidge.im) |
[Source](https://sr.ht/~nicoco/slidge/sources) |
[Issues](https://sr.ht/~nicoco/slidge/trackers) |
[Patches](https://lists.sr.ht/~nicoco/public-inbox) |
[Chat](xmpp:slidge@conference.nicoco.fr?join)

[![builds.sr.ht status](https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml.svg)](https://builds.sr.ht/~nicoco/slidge/commits/master/ci.yml?)
[![pypi](https://badge.fury.io/py/slidge.svg)](https://pypi.org/project/slidge/)
[![debian version](https://slidge.im/debian-release.svg)](https://slidge.im/core/admin/install.html#debian)
[![debian nightly package](https://slidge.im/debian-nightly.svg)](https://slidge.im/core/admin/install.html#debian)

Slidge is a general purpose XMPP (puppeteer) gateway framework in python.
It's a work in progress, but it should make
[writing gateways to other chat networks](https://slidge.im/core/dev/tutorial.html)
(*legacy modules*) as frictionless as possible.
It supports fancy IM features, such as
[(emoji) reactions](https://xmpp.org/extensions/xep-0444.html),
[replies](https://xmpp.org/extensions/xep-0461.html), and
[retractions](https://xmpp.org/extensions/xep-0424.html).
The full list of supported XEPs in on [xmpp.org](https://xmpp.org/software/slidge/).

Slidge is meant for gateway developers, if you are an XMPP server admin and
want to install gateways on your server, you are looking for one of these projects:

- [slidgnal](https://git.sr.ht/~nicoco/slidgnal) ([Signal](https://signal.org))
- [slidge-whatsapp](https://git.sr.ht/~nicoco/slidge-whatsapp) ([Whatsapp](https://whatsapp.com))
- [slidgram](https://git.sr.ht/~nicoco/slidgram) ([Telegram](https://telegram.org))
- [slidcord](https://git.sr.ht/~nicoco/slidcord) ([Discord](https://discord.com))
- [matteridge](https://git.sr.ht/~nicoco/matteridge) ([Mattermost](https://mattermost.com))
- [sleamdge](https://git.sr.ht/~nicoco/sleamdge) ([Steam](https://steamcommunity.com/))
- [skidge](https://git.sr.ht/~nicoco/skidge) ([Skype](https://skype.com/))
- [messlidger](https://git.sr.ht/~nicoco/messlidger) ([Facebook Messenger](https://messenger.com/))

If you use debian, you might also be interested in the
[slidge-debian](https://git.sr.ht/~nicoco/slidge-debian)
bundle.

Status
------

Slidge is **beta**-grade software for 1:1 chats.
Group chat support is **experimental**.

Try slidge and give us some
feedback, through the [MUC](xmpp:slidge@conference.nicoco.fr?join), the
[issue tracker](https://todo.sr.ht/~nicoco/slidge) or in the
[public inbox](https://lists.sr.ht/~nicoco/public-inbox).
Don't be shy!

Installation
------------

Slidge is available on
[docker.io](https://hub.docker.com/u/nicocool84),
[pypi](https://pypi.org/project/slidge/) and as
[debian packages](https://slidge.im/core/admin/install.html#debian).
Refer to [the docs](https://slidge.im/core/admin/install.html) for details.

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
-   [telegabber](https://dev.narayana.im/narayana/telegabber)
-   [biboumi](https://biboumi.louiz.org/)
-   [Bifröst](https://github.com/matrix-org/matrix-bifrost)
-   [Mautrix](https://github.com/mautrix)
-   [matterbridge](https://github.com/42wim/matterbridge)

Thank you, [Trung](https://trung.fun/), for the slidge logo!
