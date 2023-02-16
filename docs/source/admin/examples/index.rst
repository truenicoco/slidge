==================================
Example XMPP server configurations
==================================

.. note::

  These examples are not meant to be complete, but rather show the relevant
  parts for slidge.

Example 1: prosody
==================

.. note::
    Uncomment/comment the relevant lines if you'd rather use
    :ref:`HTTP File Upload` for :ref:`Attachments`.

.. literalinclude:: prosody.cfg.lua
  :language: lua
  :linenos:

Example 2: ejabberd/upload-service
==================================

.. note::
    See additional notes in ``Example 2: ejabberd mod_http_upload``
    to get :ref:`Attachments` working.

.. note::
    This example does not cover the :ref:`No upload` option for attachments.
    For 'no upload' with ejabberd, you need an external HTTP server, eg
    :ref:`Example 2: nginx`.

.. literalinclude:: ejabberd.yaml
  :language: yaml
  :linenos:
