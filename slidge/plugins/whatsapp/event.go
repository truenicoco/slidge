package whatsapp

import (
	// Standard library.
	"context"
	"fmt"
	"mime"

	// Third-party libraries.
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

// EventKind represents all event types recognized by the Python session adapter, as emitted by the
// Go session adapter.
type EventKind int

// The event types handled by the overarching session adapter handler.
const (
	EventUnknown EventKind = iota
	EventQRCode
	EventPairSuccess
	EventConnected
	EventLoggedOut
	EventContactSync
	EventPresence
	EventMessage
	EventChatState
	EventReceipt
)

// EventPayload represents the collected payloads for all event types handled by the overarching
// session adapter handler. Only specific fields will be populated in events emitted by internal
// handlers, see documentation for specific types for more information.
type EventPayload struct {
	QRCode       string
	PairDeviceID string
	Contact      Contact
	Presence     Presence
	Message      Message
	ChatState    ChatState
	Receipt      Receipt
}

// A Contact represents any entity that be communicated with directly in WhatsApp. This typically
// represents people, but may represent a business or bot as well, but not a group-chat.
type Contact struct {
	JID       string
	Name      string
	AvatarURL string
}

// NewContactSyncEvent returns event data meant for [Session.propagateEvent] for the contact information
// given. Unknown or invalid contact information will return an [EventUnknown] event with nil data.
func newContactSyncEvent(c *whatsmeow.Client, jid types.JID, info types.ContactInfo) (EventKind, *EventPayload) {
	var contact = Contact{
		JID: jid.ToNonAD().String(),
	}

	for _, n := range []string{info.FullName, info.FirstName, info.BusinessName, info.PushName} {
		if n != "" {
			contact.Name = n
			break
		}
	}

	// Don't attempt to synchronize contacts with no user-readable name.
	if contact.Name == "" {
		return EventUnknown, nil
	}

	if p, _ := c.GetProfilePictureInfo(jid, false, ""); p != nil {
		contact.AvatarURL = p.URL
	}

	return EventContactSync, &EventPayload{Contact: contact}
}

// Precence represents a contact's general state of activity, and is periodically updated as
// contacts start or stop paying attention to their client of choice.
type Presence struct {
	JID      string
	Away     bool
	LastSeen int64
}

// NewPresenceEvent returns event data meant for [Session.propagateEvent] for the primitive presence
// event given.
func newPresenceEvent(evt *events.Presence) (EventKind, *EventPayload) {
	return EventPresence, &EventPayload{Presence: Presence{
		JID:      evt.From.ToNonAD().String(),
		Away:     evt.Unavailable,
		LastSeen: evt.LastSeen.Unix(),
	}}
}

// MessageKind represents all concrete message types (plain-text messages, edit messages, reactions)
// recognized by the Python session adapter.
type MessageKind int

// The message types handled by the overarching session event handler.
const (
	MessagePlain MessageKind = 1 + iota
	MessageRevoke
	MessageReaction
	MessageAttachment
)

// A Message represents one of many kinds of bidirectional communication payloads, for example, a
// text message, a file (image, video) attachment, an emoji reaction, etc. Messages of different
// kinds are denoted as such, and re-use fields where the semantics overlap.
type Message struct {
	Kind        MessageKind  // The concrete message kind being sent or received.
	ID          string       // The unique message ID, used for referring to a specific Message instance.
	JID         string       // The JID this message concerns, semantics can change based on IsCarbon.
	Body        string       // The plain-text message body. For attachment messages, this can be a caption.
	Timestamp   int64        // The Unix timestamp denoting when this message was created.
	IsCarbon    bool         // Whether or not this message concerns the gateway user themselves.
	ReplyID     string       // The unique message ID this message is in reply to, if any.
	ReplyBody   string       // The full body of the message this message is in reply to, if any.
	Attachments []Attachment // The list of file (image, video, etc.) attachments contained in this message.
}

// A Attachment represents additional binary data (e.g. images, videos, documents) provided alongside
// a message, for display or storage on the recepient client.
type Attachment struct {
	MIME     string // The MIME type for attachment.
	Filename string // The recommended file name for this attachment. May be an auto-generated name.
	Caption  string // The user-provided caption, provided alongside this attachment.
	Data     []byte // The raw binary data for this attachment. Mutually exclusive with [.URL].
	URL      string // The URL to download attachment data from. Mutually exclusive with [.Data].
}

// GenerateMessageID returns a valid, pseudo-random message ID for use in outgoing messages. This
// function will panic if there is no entropy available for random ID generation.
func GenerateMessageID() string {
	return whatsmeow.GenerateMessageID()
}

// NewMessageEvent returns event data meant for [Session.propagateEvent] for the primive message
// event given. Unknown or invalid messages will return an [EventUnknown] event with nil data.
func newMessageEvent(client *whatsmeow.Client, evt *events.Message) (EventKind, *EventPayload) {
	// Ignore incoming messages sent or received over group-chats until proper support is implemented.
	if evt.Info.IsGroup {
		return EventUnknown, nil
	}

	// Set basic data for message, to be potentially amended depending on the concrete version of
	// the underlying message.
	var message = Message{
		Kind:      MessagePlain,
		ID:        evt.Info.ID,
		Body:      evt.Message.GetConversation(),
		Timestamp: evt.Info.Timestamp.Unix(),
		IsCarbon:  evt.Info.IsFromMe,
	}

	// Set message JID based on whether a message is originating from ourselves or someone else.
	if message.IsCarbon {
		message.JID = evt.Info.MessageSource.Chat.ToNonAD().String()
	} else {
		message.JID = evt.Info.MessageSource.Sender.ToNonAD().String()
	}

	// Handle handle protocol messages (such as message deletion or editing).
	if p := evt.Message.GetProtocolMessage(); p != nil {
		switch p.GetType() {
		case proto.ProtocolMessage_REVOKE:
			message.Kind = MessageRevoke
			message.ID = p.Key.GetId()
			return EventMessage, &EventPayload{Message: message}
		}
	}

	// Handle emoji reaction to existing message.
	if r := evt.Message.GetReactionMessage(); r != nil {
		message.Kind = MessageReaction
		message.ID = r.Key.GetId()
		message.Body = r.GetText()
		return EventMessage, &EventPayload{Message: message}
	}

	// Handle message attachments, if any.
	if attach, err := getMessageAttachments(client, evt.Message); err != nil {
		client.Log.Errorf("Failed getting message attachments: %s", err)
		return EventUnknown, nil
	} else if len(attach) > 0 {
		message.Attachments = append(message.Attachments, attach...)
		message.Kind = MessageAttachment
	}

	// Get extended information from message, if available. Extended messages typically represent
	// messages with additional context, such as replies, forwards, etc.
	if e := evt.Message.GetExtendedTextMessage(); e != nil {
		if message.Body == "" {
			message.Body = e.GetText()
		}
		if c := e.GetContextInfo(); c != nil {
			message.ReplyID = c.GetStanzaId()
			if q := c.GetQuotedMessage(); q != nil {
				message.ReplyBody = q.GetConversation()
			}
		}
	}

	// Ignore obviously invalid messages.
	if message.Kind == MessagePlain && message.Body == "" {
		return EventUnknown, nil
	}

	return EventMessage, &EventPayload{Message: message}
}

// GetMessageAttachments fetches and decrypts attachments (images, audio, video, or documents) sent
// via WhatsApp. Any failures in retrieving any attachment will return an error immediately.
func getMessageAttachments(client *whatsmeow.Client, message *proto.Message) ([]Attachment, error) {
	var result []Attachment
	var kinds = []whatsmeow.DownloadableMessage{
		message.GetImageMessage(),
		message.GetAudioMessage(),
		message.GetVideoMessage(),
		message.GetDocumentMessage(),
	}

	for _, msg := range kinds {
		// Handle data for specific attachment type.
		var a Attachment
		switch msg := msg.(type) {
		case *proto.ImageMessage:
			a.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
		case *proto.AudioMessage:
			a.MIME = msg.GetMimetype()
		case *proto.VideoMessage:
			a.MIME, a.Caption = msg.GetMimetype(), msg.GetCaption()
		case *proto.DocumentMessage:
			a.MIME, a.Caption, a.Filename = msg.GetMimetype(), msg.GetCaption(), msg.GetFileName()
		}

		// Ignore attachments with empty or unknown MIME types.
		if a.MIME == "" {
			continue
		}

		// Set filename from SHA256 checksum and MIME type, if none is already set.
		if a.Filename == "" {
			a.Filename = fmt.Sprintf("%x%s", msg.GetFileSha256(), extensionByType(a.MIME))
		}

		// Attempt to download and decrypt raw attachment data, if any.
		data, err := client.Download(msg)
		if err != nil {
			return nil, err
		}

		a.Data = data
		result = append(result, a)
	}

	return result, nil
}

// KnownMediaTypes represents MIME type to WhatsApp media types known to be handled by WhatsApp in a
// special way (that is, not as generic file uploads).
var knownMediaTypes = map[string]whatsmeow.MediaType{
	"image/jpeg":      whatsmeow.MediaImage,
	"audio/ogg":       whatsmeow.MediaAudio,
	"application/ogg": whatsmeow.MediaAudio,
	"video/mp4":       whatsmeow.MediaVideo,
}

// UploadAttachment attempts to push the given attachment data to WhatsApp according to the MIME type
// specified within. Attachments are handled as generic file uploads unless they're of a specific
// format, see [knownMediaTypes] for more information.
func uploadAttachment(client *whatsmeow.Client, attach Attachment) (*proto.Message, error) {
	mediaType := knownMediaTypes[attach.MIME]
	if mediaType == "" {
		mediaType = whatsmeow.MediaDocument
	}

	upload, err := client.Upload(context.Background(), attach.Data, mediaType)
	if err != nil {
		return nil, err
	}

	var message *proto.Message
	switch mediaType {
	case whatsmeow.MediaImage:
		message = &proto.Message{
			ImageMessage: &proto.ImageMessage{
				Url:           &upload.URL,
				DirectPath:    &upload.DirectPath,
				MediaKey:      upload.MediaKey,
				Mimetype:      &attach.MIME,
				FileEncSha256: upload.FileEncSHA256,
				FileSha256:    upload.FileSHA256,
				FileLength:    ptrTo(uint64(len(attach.Data))),
			},
		}
	case whatsmeow.MediaAudio:
		message = &proto.Message{
			AudioMessage: &proto.AudioMessage{
				Url:           &upload.URL,
				DirectPath:    &upload.DirectPath,
				MediaKey:      upload.MediaKey,
				Mimetype:      &attach.MIME,
				FileEncSha256: upload.FileEncSHA256,
				FileSha256:    upload.FileSHA256,
				FileLength:    ptrTo(uint64(len(attach.Data))),
			},
		}
	case whatsmeow.MediaVideo:
		message = &proto.Message{
			VideoMessage: &proto.VideoMessage{
				Url:           &upload.URL,
				DirectPath:    &upload.DirectPath,
				MediaKey:      upload.MediaKey,
				Mimetype:      &attach.MIME,
				FileEncSha256: upload.FileEncSHA256,
				FileSha256:    upload.FileSHA256,
				FileLength:    ptrTo(uint64(len(attach.Data))),
			}}
	case whatsmeow.MediaDocument:
		message = &proto.Message{
			DocumentMessage: &proto.DocumentMessage{
				Url:           &upload.URL,
				DirectPath:    &upload.DirectPath,
				MediaKey:      upload.MediaKey,
				Mimetype:      &attach.MIME,
				FileEncSha256: upload.FileEncSHA256,
				FileSha256:    upload.FileSHA256,
				FileLength:    ptrTo(uint64(len(attach.Data))),
				FileName:      &attach.Filename,
			}}
	}

	return message, nil
}

// ExtensionByType returns the file extension for the given MIME type, or a generic extension if the
// MIME type is unknown.
func extensionByType(typ string) string {
	if ext, _ := mime.ExtensionsByType(typ); len(ext) > 0 {
		return ext[0]
	}
	return ".bin"
}

// ChatStateKind represents the different kinds of chat-states possible in WhatsApp.
type ChatStateKind int

// The chat states handled by the overarching session event handler.
const (
	ChatStateComposing ChatStateKind = 1 + iota
	ChatStatePaused
)

// A ChatState represents the activity of a contact within a certain discussion, for instance,
// whether the contact is currently composing a message. This is separate to the concept of a
// Presence, which is the contact's general state across all discussions.
type ChatState struct {
	JID  string
	Kind ChatStateKind
}

// NewChatStateEvent returns event data meant for [Session.propagateEvent] for the primitive
// chat-state event given.
func newChatStateEvent(evt *events.ChatPresence) (EventKind, *EventPayload) {
	var state = ChatState{JID: evt.MessageSource.Sender.ToNonAD().String()}
	switch evt.State {
	case types.ChatPresenceComposing:
		state.Kind = ChatStateComposing
	case types.ChatPresencePaused:
		state.Kind = ChatStatePaused
	}
	return EventChatState, &EventPayload{ChatState: state}
}

// ReceiptKind represents the different types of delivery receipts possible in WhatsApp.
type ReceiptKind int

// The delivery receipts handled by the overarching session event handler.
const (
	ReceiptDelivered ReceiptKind = 1 + iota
	ReceiptRead
)

// A Receipt represents a notice of delivery or presentation for [Message] instances sent or
// received. Receipts can be delivered for many messages at once, but are generally all delivered
// under one specific state at a time.
type Receipt struct {
	Kind       ReceiptKind
	MessageIDs []string
	JID        string
	Timestamp  int64
	IsCarbon   bool
}

// NewReceiptEvent returns event data meant for [Session.propagateEvent] for the primive receipt
// event given. Unknown or invalid receipts will return an [EventUnknown] event with nil data.
func newReceiptEvent(evt *events.Receipt) (EventKind, *EventPayload) {
	var receipt = Receipt{
		MessageIDs: append([]string{}, evt.MessageIDs...),
		Timestamp:  evt.Timestamp.Unix(),
		IsCarbon:   evt.MessageSource.IsFromMe,
	}

	if len(receipt.MessageIDs) == 0 {
		return EventUnknown, nil
	}

	if receipt.IsCarbon {
		receipt.JID = evt.MessageSource.Chat.ToNonAD().String()
	} else {
		receipt.JID = evt.MessageSource.Sender.ToNonAD().String()
	}

	switch evt.Type {
	case events.ReceiptTypeDelivered:
		receipt.Kind = ReceiptDelivered
	case events.ReceiptTypeRead:
		receipt.Kind = ReceiptRead
	}

	return EventReceipt, &EventPayload{Receipt: receipt}
}
