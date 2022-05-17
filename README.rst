Slidge 🛷
=========

.. image:: https://readthedocs.org/projects/slidge/badge/?version=latest
    :target: https://slidge.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation status

.. image:: https://gitlab.com/nicocool84/slidge/badges/master/pipeline.svg
    :target: https://gitlab.com/group-name/project-name/commits/master
    :alt: Pipeline status


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

About privacy
-------------

Slidge (and most if not all XMPP gateway that I know of) will break end-to-end encryption,
or more precisely one of the 'ends' become the gateway itself.
If privacy is a major concern for you, my advice would be to:

- use XMPP + OMEMO
- self-host your gateways
- have your gateways hosted by someone you know AFK


Related projects
----------------

- `Spectrum <https://www.spectrum.im/>`_
- `Bitfrost <https://github.com/matrix-org/matrix-bifrost>`_
- `Mautrix <https://github.com/mautrix>`_
- `matterbridge <https://github.com/42wim/matterbridge>`_
- `XMPP-discord-bridge <https://git.polynom.me/PapaTutuWawa/xmpp-discord-bridge>`_

Homepage: `gitlab <https://gitlab.com/nicocool84/slidge/>`_

Chat room: `slidge@conference.nicoco.fr <xmpp:slidge@conference.nicoco.fr?join>`_
