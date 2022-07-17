General information
===================

Getting started
---------------

The easiest way to develop using slidge is with docker-compose. Clone the
repo, run `docker-compose up` and you should have:

-   an XMPP server (prosody) exposed on port 5222 with a registered user
    <test@localhost> (password: password)
-   3 gateway components (a dummy network, signal and telegram)
-   hot reloading of gateways on code change
-   signald running in a container (required for signal)

I recommend using gajim to test it. You can launch it with the -p option
to use a clean profile and not mess up your normal user settings and
such.

Contributing
------------

Mypy

Black

pytest