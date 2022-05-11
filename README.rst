Slidge 🛷
=========

Pythonic XMPP gateways made easy.

An XMPP component that attempts to follow XEPs to provide an generic
way to write XMPP/*legacy network* gateways, leveraging on the power of
`SliXMPP <https://slixmpp.readthedocs.io>`_.

Status
------

Slidge is not ready for production yet! Right now, only direct messages are implemented,
for Telegram and Signal. Please test it locally and report bugs.

Installation
------------

The easiest way to try it out slidge is with docker-compose.
Clone the repo, run ``docker-compose up`` and you should have:

- an XMPP server (prosody) exposed on port 5222 with a registered user test@localhost (password: password)
- 3 gateway components (a dummy network, signal and telegram)
- hot reloading of gateways on code change
- signald running in a container (required for signal)

I recommend using gajim to test it. You can launch it with the -p option to use a clean
profile and not mess up your normal user settings and such.

It is definitely possible to set up everything without docker, but note that the
aiotdlib package needs to be manually built (wheels from pypi are incomplete unfortunately).

Related projects
----------------

Slidge is heavily inspired by `Spectrum <https://www.spectrum.im/>`_.
Spectrum "backends" (our *legacy clients*) can be written in any language,
but we target python specifically.

Another related project is `matterbridge <https://github.com/42wim/matterbridge>`_, but
this projects focuses on XMPP to take advantage of its amazing features when matterbridge
is geared towards mattermost.

`XMPP-discord-bridge <https://git.polynom.me/PapaTutuWawa/xmpp-discord-bridge>`_ also uses slixmpp,
but focuses on discord only on discord channels.

Homepage: `gitlab <https://gitlab.com/nicocool84/slidge/>`_

Chat room: `slidge@conference.nicoco.fr <xmpp:slidge@conference.nicoco.fr?join>`_

`Docs (WIP) are here <https://slidge.readthedocs.io>`_.
