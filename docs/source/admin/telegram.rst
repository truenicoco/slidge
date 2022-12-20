Telegram
--------

Additional requirement: ``libc++1``.

Slidge uses the official telegram's library: `tdlib <https://tdlib.github.io/td/>`_.

If you set ``--api-id`` and ``--api-hash`` the users won't have to register an app
and use their own. Visit https://my.telegram.org/apps for more info.

You can customize tdlib's root dir and (local) encryption key with ``--tdlib-path``
and ``--tdlib-key``.
