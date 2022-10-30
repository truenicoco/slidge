daemonize = false;
admins = { }

modules_enabled = {
    "roster"; -- Allow users to have a roster. Recommended ;)
    "saslauth"; -- Authentication for clients and servers. Recommended if you want to log in.
    "tls"; -- Add support for secure TLS on c2s/s2s connections
    "dialback"; -- s2s dialback support
    "disco"; -- Service discovery
    "carbons"; -- Keep multiple clients in sync
    "pep"; -- Enables users to publish their avatar, mood, activity, playing music and more
    "private"; -- Private XML storage (for room bookmarks, etc.)
    "blocklist"; -- Allow users to block communications with other users
    "vcard4"; -- User profiles (stored in PEP)
    "vcard_legacy"; -- Conversion between legacy vCard and PEP Avatar, vcard
    "limits"; -- Enable bandwidth limiting for XMPP connections
    "version"; -- Replies to server version requests
    "uptime"; -- Report how long server has been running
    "time"; -- Let others know the time here on this server
    "ping"; -- Replies to XMPP pings with pongs
    "register"; -- Allow users to register on this server using a client and change passwords
    "mam"; -- Store messages in an archive and allow users to access it
    "admin_adhoc"; -- Allows administration via an XMPP client that supports ad-hoc commands
    "privilege";
}

allow_registration = true
c2s_require_encryption = false
s2s_require_encryption = false
s2s_secure_auth = true

limits = {
  c2s = {
    rate = "10kb/s";
  };
  s2sin = {
    rate = "30kb/s";
  };
}

pidfile = "/var/run/prosody/prosody.pid"

authentication = "internal_hashed"

archive_expires_after = "1w"

log = {
    {levels = {min = "debug"}, to = "console"};
}

certificates = "certs"

VirtualHost "localhost"
  privileged_entities = {
     ["dummy.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["telegram.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["signal.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["mattermost.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["facebook.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["skype.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["hackernews.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["steam.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["discord.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
     ["whatsapp.localhost"] = {
           roster = "both";
           message = "outgoing";
     },
  }

Component "muc.localhost" "muc"
  modules_enabled = { "muc_mam" }

Component "dummy.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "telegram.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "signal.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "mattermost.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "facebook.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "skype.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "hackernews.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "steam.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "discord.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "whatsapp.localhost"
  component_secret = "secret"
  modules_enabled = {"privilege"}

Component "upload.localhost" "http_file_share"
  http_host = "localhost"

  http_paths = {
    file_share = "/upload";
  }

plugin_server = "https://modules.prosody.im/rocks/"
installer_plugin_path = "/usr/local/lib/prosody/"
