Contributing
============

Development setup
-----------------

With containers
---------------

The easiest way to develop using slidge is by using docker-compose.
Clone the repo, run `docker-compose up` and you should have:

-   an XMPP server (prosody) exposed on port 5222 with a registered user
    <test@localhost> (password: password)
-   an XMPP component, the "super duper" gateway, a fake component that can be
    used to try stuff directly in an XMPP client, with code hot-reload.
-   the in-browser Movim client running on http://localhost:8888

NB: it's possible to select which containers you want to run, you don't have to
launch everything.

You can login with the JID ``test@localhost`` and ``password`` as the password.

Without containers
------------------

To install outside of a container, use `poetry <https://python-poetry.org/>`_.

If you don't like containers, set up a virtual environment with
``poetry install`` and refer to :ref:`XMPP server config`.

Using another XMPP client
-------------------------

`Gajim <https://gajim.org>`_
is also a good choice to test stuff during development, since it implement a lot
of XEPs, especially when it comes to components.
You can launch it with ``-p slidge -c ~/.local/share/slidge-test -v`` to use a
clean profile and not mess up your usual gajim config, db, cache, etc.

Unlike gajim, some clients will not accept self signed certificates, a possible
workaround using debian and docker is

.. code-block::

   docker cp slidge_prosody_1:/etc/prosody/certs/localhost.crt \
      /tmp/localhost.crt
   sudo /tmp/localhost.crt /usr/local/share/ca-certificates
   sudo update-ca-certificates

Guidelines
----------

Tests should be written with the `pytest <https://pytest.org>`_ framework.
For complex tests involving mocking data through the XMPP stream, the
:class:`slidge.util.test.SlidgeTest` class can come in handy.

The code should pass
`black <https://black.readthedocs.io/en/stable/>`_,
`mypy <https://mypy-lang.org/>`_ and
`ruff <https://ruff.rs>`_
with the settings defined in ``pyproject.toml``.
