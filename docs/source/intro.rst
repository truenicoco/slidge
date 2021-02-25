.. include:: ../../README.rst

Warning: this is a very early pre-release and definitively not usable in a production
setting yet.

Features
--------

Gateway interaction
*******************

- ☑ In band registration (:xep:`0077`)
- ☑ Following good practices listed in :xep:`0100` (to be confirmed)
- ☑ More complex registration flows (2FA, SMS, QR codes…) via direct messages between the user and the gateway component
- ☐ Managing legacy network profile

One-to-one messaging (buddies)
******************************

- ☑ Messages
- ☑ Populating the XMPP roster via privileged entity (:xep:`0356`)
- ☑ Buddies' avatars (:xep:`0054` and :xep:`0153`)
- ☑ Message receipts (:xep:`0184`)
- ☑ Chat states (composing, paused, :xep:`0085`)
- ☑ Chat markers (:xep:`0333`)
- ☑ Carbon messages for messages sent from official legacy clients (:xep:`0280` and :xep:`0356`)
- ☐ Attachments (:xep:`0363`)

Group chats (mucs)
******************

- ☑ Messages (:xep:`0045`)
- ☑ "Invitations" to join legacy group chats you are already part of by the gateway component (:xep:`0249`)
- ☑ Buffering legacy group chat history before joining from the XMPP
- ☐ Message archive management
- ☐ Invitations to group chats from the legacy network
- ☐ *Really* leaving a legacy group chat
- ☐ Chat states
- ☐ Message markers
- ☐ Attachments

Easy-to-use implementation of new gateways
******************************************

Endgoal of this project, work in progress…
