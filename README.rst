Slidge 🛷
=========

Pythonic XMPP gateways made easy.

An XMPP component that attempts to follow XEP-0100 and provide an generic
way to write XMPP/*legacy network* gateways, leveraging on the power of
`SliXMPP <https://slixmpp.readthedocs.io>`_.

It is heavily inspired by `Spectrum <https://www.spectrum.im/>`_.
Spectrum "backends" (our *legacy clients*) can be written in any language,
but we target python specifically.
At this point, only a `signald <https://gitlab.com/signald>`_-based gateway is
available as a proof of concept.

Another related project is `matterbridge <https://github.com/42wim/matterbridge>`_, but
this projects focuses on XMPP to take advantage of its amazing features when matterbridge
is geared towards mattermost.

`Docs (WIP) are here <https://slidge.readthedocs.io>`_.
