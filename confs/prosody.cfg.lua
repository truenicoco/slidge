daemonize = false;
admins = { }

modules_enabled = {

        -- Generally required
                "roster"; -- Allow users to have a roster. Recommended ;)
                "saslauth"; -- Authentication for clients and servers. Recommended if you want to log in.
                "tls"; -- Add support for secure TLS on c2s/s2s connections
                "dialback"; -- s2s dialback support
                "disco"; -- Service discovery

        -- Not essential, but recommended
                "carbons"; -- Keep multiple clients in sync
                "pep"; -- Enables users to publish their avatar, mood, activity, playing music and more
                "private"; -- Private XML storage (for room bookmarks, etc.)
                "blocklist"; -- Allow users to block communications with other users
                "vcard4"; -- User profiles (stored in PEP)
                "vcard_legacy"; -- Conversion between legacy vCard and PEP Avatar, vcard
                "limits"; -- Enable bandwidth limiting for XMPP connections

        -- Nice to have
                "version"; -- Replies to server version requests
                "uptime"; -- Report how long server has been running
                "time"; -- Let others know the time here on this server
                "ping"; -- Replies to XMPP pings with pongs
                "register"; -- Allow users to register on this server using a client and change passwords
                "mam"; -- Store messages in an archive and allow users to access it
                --"csi_simple"; -- Simple Mobile optimizations

        -- Admin interfaces
                "admin_adhoc"; -- Allows administration via an XMPP client that supports ad-hoc commands
                --"admin_telnet"; -- Opens telnet console interface on localhost port 5582

        -- HTTP modules
                --"bosh"; -- Enable BOSH clients, aka "Jabber over HTTP"
                --"websocket"; -- XMPP over WebSockets
                --"http_files"; -- Serve static files from a directory over HTTP

        -- Other specific functionality
                --"groups"; -- Shared roster support
                --"server_contact_info"; -- Publish contact information for this service
                --"announce"; -- Send announcement to all online users
                --"welcome"; -- Welcome users who register accounts
                --"watchregistrations"; -- Alert admins of registrations
                --"motd"; -- Send a message to users when they log in
                --"legacyauth"; -- Legacy authentication. Only used by some old clients and bots.
                --"proxy65"; -- Enables a file transfer proxy service which clients behind NAT can use
                "privilege";
--                 "http";
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

component_interfaces = { "prosody" }

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
  }

VirtualHost "prosody"
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

Component "upload.localhost" "http_file_share"
  http_host = "localhost"

  http_paths = {
    file_share = "/upload";
  }

plugin_server = "https://modules.prosody.im/rocks/"
installer_plugin_path = "/usr/local/lib/prosody/"
