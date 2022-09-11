General information
===================

Getting started
---------------

The easiest way to develop using slidge is by using docker-compose.
Clone the repo, run `docker-compose up` and you should have:

-   an XMPP server (prosody) exposed on port 5222 with a registered user
    <test@localhost> (password: password)
-   all plugins as XMPP components, including a "dummy" plugin which does not
    connect to any external service (useful for trying out new core stuff)
-   hot reloading of gateways on code change
-   signald running in a container (required for signal)
-   the in-browser Movim client running on http://localhost:8888
-   a mattermost test instance on http://localhost:8065

NB: it's possible to select which containers you want to run, you don't have to
launch everything.

`Gajim <https://gajim.org>`_
is also a good choice to test stuff during development, since it implement a lot
of XEPs, especially when it comes to components.
You can launch it with the ``-p`` option to use a clean profile and not mess up
your normal user settings and such.

Contributing
------------

The plan is to make the slidge codebase as slim and maintainable as possible.

Most of the ``util/xep_xxxx`` modules are supposed to be here temporarily and should be
submitted upstream to the `slixmpp <https://slixmpp.readthedocs.io/en/latest/>`_ library.

If possible, plugins should use maintained external libraries for legacy network specificities.

Code style should follow `black conventions <https://black.readthedocs.io/en/stable/>`_.

Tests should be written with the `pytest <https://pytest.org>`_ framework.
For complex tests, especially plugins tests, the :class:`slidge.util.test.SlidgeTest` class
can come in handy.

As much as possible, code should be
`mypy <https://http://mypy-lang.org/>`_-compliant.
