CREATE TABLE muc(
  id INTEGER PRIMARY KEY,
  jid TEXT
);

CREATE TABLE mam_message(
  id INTEGER PRIMARY KEY,
  message_id TEXT,
  sent_on INTEGER,
  sender_jid TEXT,
  xml TEXT,
  muc_id INTEGER,
  FOREIGN KEY(muc_id) REFERENCES muc(id)
);

CREATE INDEX mam_sent_on ON mam_message(sent_on);
CREATE INDEX muc_jid ON muc(jid);
