CREATE TABLE user(
  id INTEGER PRIMARY KEY,
  jid TEXT UNIQUE
);

CREATE TABLE muc(
  id INTEGER PRIMARY KEY,
  jid TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id),
  UNIQUE(user_id, jid)
);

CREATE TABLE mam_message(
  id INTEGER PRIMARY KEY,
  message_id TEXT,
  sent_on INTEGER,
  sender_jid TEXT,
  xml TEXT,
  muc_id INTEGER,
  user_id INTEGER,
  FOREIGN KEY(muc_id) REFERENCES muc(id),
  FOREIGN KEY(user_id) REFERENCES user(id),
  UNIQUE(user_id, muc_id, message_id)
);

CREATE INDEX mam_sent_on ON mam_message(sent_on);
CREATE INDEX muc_jid ON muc(jid);

CREATE TABLE session_message_sent(
  id INTEGER PRIMARY KEY,
  legacy_id UNIQUE,
  xmpp_id TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id)
);

CREATE INDEX session_message_sent_legacy_id
    ON session_message_sent(legacy_id);
CREATE INDEX session_message_sent_xmpp_id
    ON session_message_sent(xmpp_id);

CREATE TABLE session_message_sent_muc(
  id INTEGER PRIMARY KEY,
  legacy_id UNIQUE,
  xmpp_id TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id)
);

CREATE INDEX session_message_sent_muc_legacy_id
    ON session_message_sent_muc(legacy_id);
CREATE INDEX session_message_sent_muc_xmpp_id
    ON session_message_sent_muc(xmpp_id);

CREATE TABLE session_thread_sent_muc(
  id INTEGER PRIMARY KEY,
  legacy_id UNIQUE,
  xmpp_id TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id)
);

CREATE INDEX session_thread_sent_muc_legacy_id
    ON session_thread_sent_muc(legacy_id);
CREATE INDEX session_thread_sent_muc_xmpp_id
    ON session_thread_sent_muc(xmpp_id);


CREATE TABLE attachment(
  id INTEGER PRIMARY KEY,
  legacy_id UNIQUE,
  url TEXT UNIQUE,
  sims TEXT,
  sfs TEXT
);

CREATE INDEX attachment_legacy_id ON attachment(legacy_id);
CREATE INDEX attachment_url ON attachment(url);

CREATE TABLE attachment_legacy_msg_id(
  id INTEGER PRIMARY KEY,
  legacy_id UNIQUE
);

CREATE TABLE attachment_xmpp_ids(
  id INTEGER PRIMARY KEY,
  legacy_msg_id INTEGER,
  xmpp_id TEXT,
  FOREIGN KEY(legacy_msg_id) REFERENCES attachment_legacy_msg_id(id)
);

CREATE TABLE nick(
  id INTEGER PRIMARY KEY,
  jid UNIQUE,
  nick TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id),
  UNIQUE(jid, user_id)
);

CREATE INDEX nick_jid ON nick(jid);


CREATE TABLE avatar(
  id INTEGER PRIMARY KEY,
  jid TEXT UNIQUE,
  cached_id TEXT
);

CREATE INDEX avatar_jid ON avatar(jid);


CREATE TABLE presence(
  id INTEGER PRIMARY KEY,
  jid TEXT,
  last_seen INTEGER,
  ptype TEXT,
  pstatus TEXT,
  pshow TEXT,
  user_id INTEGER,
  FOREIGN KEY(user_id) REFERENCES user(id),
  UNIQUE(jid, user_id)
);

CREATE INDEX presence_jid ON presence(jid);
