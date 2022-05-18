Features
========

Everything with a checkmark is implemented in slidge, BUT might not work in all cases.
We need testing!

Gateway interaction
*******************

This is mostly working but must be tested in depth.

- ☑ In band registration (:xep:`0077`)
- ☑ More complex registration flows (2FA, SMS, QR codes…) via direct messages between the
  user and the gateway component

One-to-one messaging (contacts)
*******************************

- ☑ Direct messages
- ☑ Populating the XMPP roster via privileged entity (:xep:`0356`)
- ☑ Contacts' avatars (:xep:`0054` and :xep:`0153`)
- ☑ Message receipts (:xep:`0184`)
- ☑ Chat states (composing, paused, :xep:`0085`)
- ☑ Chat markers (:xep:`0333`)
- ☑ Carbon messages for messages sent from official legacy clients (:xep:`0280` and :xep:`0356`)
- ☐ Sync own read status between XMPP and official client
- ☑ HTTP file upload (:xep:`0363`)
- ☐ Last message correction (:xep:`0308`)

Group chats (MUCs)
******************

When gateway interactions and direct messages work fine enough, we'll get to that.