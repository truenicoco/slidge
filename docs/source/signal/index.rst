User doc: a signald gateway
===========================

Requirements
------------

- Python 3.7+
- `Poetry <https://python-poetry.org/>`_
- `Signald <https://gitlab.com/signald/signald>`_
- A phone number or an existing official signal client installation
- A running `prosody XMPP server <https://prosody.im/>`_
- (optional) component access to mod_http_upload to receive QR code as an image in case of linking

Installation
------------

Prosody config
**************

.. code-block:: lua

   VirtualHost "example.com"
      privileged_entities = {
         ["signald.example.com"] = {
               roster = "both";
               message = "outgoing";
         },
      }

   Component "signald.example.com"
         component_secret = "password"
         modules_enabled = {"privilege"}


SliXMPP gateway
***************

- Clone the repo
- `poetry install`
- `poetry run python -m slidge signald.ini` as a user which has full access to the signald UNIX socket and data dirs.

.. _configuration example:

Configuration: content of signald.ini
-------------------------------------

.. literalinclude :: ../../../confs/signald.ini
