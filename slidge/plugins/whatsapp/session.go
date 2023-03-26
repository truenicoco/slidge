package whatsapp

import (
	// Standard library.
	"context"
	"fmt"
	"io"
	"net/http"
	"runtime"
	"time"

	// Third-party libraries.
	_ "github.com/mattn/go-sqlite3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/appstate"
	"go.mau.fi/whatsmeow/binary/proto"
	"go.mau.fi/whatsmeow/store"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
)

const (
	// The default host part for user JIDs on WhatsApp.
	DefaultUserServer = types.DefaultUserServer

	// The default host part for group JIDs on WhatsApp.
	DefaultGroupServer = types.GroupServer

	// The number of times keep-alive checks can fail before attempting to re-connect the session.
	keepAliveFailureThreshold = 3

	// The minimum and maximum wait interval between connection retries after keep-alive check failure.
	keepAliveMinRetryInterval = 5 * time.Second
	keepAliveMaxRetryInterval = 5 * time.Minute
)

// HandleEventFunc represents a handler for incoming events sent to the Python Session, accepting an
// event type and payload. Note that this is distinct to the [Session.handleEvent] function, which
// may emit events into the Python Session event handler but which otherwise does not process across
// Python/Go boundaries.
type HandleEventFunc func(EventKind, *EventPayload)

// A Session represents a connection (active or not) between a linked device and WhatsApp. Active
// sessions need to be established by logging in, after which incoming events will be forwarded to
// the adapter event handler, and outgoing events will be forwarded to WhatsApp.
type Session struct {
	device       LinkedDevice      // The linked device this session corresponds to.
	eventHandler HandleEventFunc   // The event handler for the overarching Session.
	client       *whatsmeow.Client // The concrete client connection to WhatsApp for this session.
	gateway      *Gateway          // The Gateway this Session is attached to.
}

// Login attempts to authenticate the given [Session], either by re-using the [LinkedDevice] attached
// or by initiating a pairing session for a new linked device. Callers are expected to have set an
// event handler in order to receive any incoming events from the underlying WhatsApp session.
func (s *Session) Login() error {
	var err error
	var store *store.Device

	// Try to fetch existing device from given device JID.
	if s.device.ID != "" {
		store, err = s.gateway.container.GetDevice(s.device.JID())
		if err != nil {
			return err
		}
	}

	if store == nil {
		store = s.gateway.container.NewDevice()
	}

	s.client = whatsmeow.NewClient(store, s.gateway.logger)
	s.client.AddEventHandler(s.handleEvent)

	// Simply connect our client if already registered.
	if s.client.Store.ID != nil {
		return s.client.Connect()
	}

	// Attempt out-of-band registration of client via QR code.
	qrChan, _ := s.client.GetQRChannel(context.Background())
	if err = s.client.Connect(); err != nil {
		return err
	}

	go func() {
		for e := range qrChan {
			if !s.client.IsConnected() {
				return
			}
			switch e.Event {
			case "code":
				s.propagateEvent(EventQRCode, &EventPayload{QRCode: e.Code})
			}
		}
	}()

	return nil
}

// Logout disconnects and removes the current linked device locally and initiates a logout remotely.
func (s *Session) Logout() error {
	if s.client == nil || s.client.Store.ID == nil {
		return nil
	}

	err := s.client.Logout()
	s.client = nil

	return err
}

// Disconnects detaches the current connection to WhatsApp without removing any linked device state.
func (s *Session) Disconnect() error {
	if s.client != nil {
		s.client.Disconnect()
		s.client = nil
	}

	return nil
}

// SendMessage processes the given Message and sends a WhatsApp message for the kind and contact JID
// specified within. In general, different message kinds require different fields to be set; see the
// documentation for the [Message] type for more information.
func (s *Session) SendMessage(message Message) error {
	if s.client == nil || s.client.Store.ID == nil {
		return fmt.Errorf("Cannot send message for unauthenticated session")
	}

	jid, err := types.ParseJID(message.JID)
	if err != nil {
		return fmt.Errorf("Could not parse sender JID for message: %s", err)
	}

	var payload *proto.Message
	var extra whatsmeow.SendRequestExtra

	switch message.Kind {
	case MessageAttachment:
		// Handle message with attachment, if any.
		if len(message.Attachments) == 0 {
			return nil
		}

		// Attempt to download attachment data if URL is set.
		if url := message.Attachments[0].URL; url != "" {
			if buf, err := getFromURL(s.gateway.httpClient, url); err != nil {
				return fmt.Errorf("Failed downloading attachment: %s", err)
			} else {
				message.Attachments[0].Data = buf
			}
		}

		// Ignore attachments with no data set or downloaded.
		if len(message.Attachments[0].Data) == 0 {
			return nil
		}

		// Upload attachment into WhatsApp before sending message.
		if payload, err = uploadAttachment(s.client, message.Attachments[0]); err != nil {
			return fmt.Errorf("Failed uploading attachment: %s", err)
		}
		extra.ID = message.ID
	case MessageRevoke:
		// Don't send message, but revoke existing message by ID.
		payload = s.client.BuildRevoke(s.device.JID().ToNonAD(), types.EmptyJID, message.ID)
	case MessageReaction:
		// Send message as emoji reaction to a given message.
		payload = &proto.Message{
			ReactionMessage: &proto.ReactionMessage{
				Key: &proto.MessageKey{
					RemoteJid:   &message.JID,
					FromMe:      &message.IsCarbon,
					Id:          &message.ID,
					Participant: &message.OriginJID,
				},
				Text:              &message.Body,
				SenderTimestampMs: ptrTo(time.Now().UnixMilli()),
			},
		}
	default:
		// Compose extended message when made as a reply to a different message, otherwise compose
		// plain-text message for body given for all other message kinds.
		if message.ReplyID != "" {
			// Fall back to our own JID if no origin JID has been specified, in which case we assume
			// we're replying to our own messages.
			if message.OriginJID == "" {
				message.OriginJID = s.device.JID().ToNonAD().String()
			}
			payload = &proto.Message{
				ExtendedTextMessage: &proto.ExtendedTextMessage{
					Text: &message.Body,
					ContextInfo: &proto.ContextInfo{
						StanzaId:      &message.ReplyID,
						QuotedMessage: &proto.Message{Conversation: ptrTo(message.ReplyBody)},
						Participant:   &message.OriginJID,
					},
				},
			}
		}
		// Add URL preview, if any was given in message.
		if message.Preview.URL != "" {
			if payload == nil {
				payload = &proto.Message{
					ExtendedTextMessage: &proto.ExtendedTextMessage{Text: &message.Body},
				}
			}
			payload.ExtendedTextMessage.MatchedText = &message.Preview.URL
			payload.ExtendedTextMessage.Title = &message.Preview.Title
			if url := message.Preview.ImageURL; url != "" {
				if buf, err := getFromURL(s.gateway.httpClient, url); err == nil {
					payload.ExtendedTextMessage.JpegThumbnail = buf
				}
			} else if len(message.Preview.ImageData) > 0 {
				payload.ExtendedTextMessage.JpegThumbnail = message.Preview.ImageData
			}
		}
		if payload == nil {
			payload = &proto.Message{Conversation: &message.Body}
		}
		extra.ID = message.ID
	}

	s.gateway.logger.Debugf("Sending message to JID '%s': %+v", jid, payload)
	_, err = s.client.SendMessage(context.Background(), jid, payload, extra)
	return err
}

// SendChatState sends the given chat state notification (e.g. composing message) to WhatsApp for the
// contact specified within.
func (s *Session) SendChatState(state ChatState) error {
	if s.client == nil || s.client.Store.ID == nil {
		return fmt.Errorf("Cannot send chat state for unauthenticated session")
	}

	jid, err := types.ParseJID(state.JID)
	if err != nil {
		return fmt.Errorf("Could not parse sender JID for chat state: %s", err)
	}

	var presence types.ChatPresence
	switch state.Kind {
	case ChatStateComposing:
		presence = types.ChatPresenceComposing
	case ChatStatePaused:
		presence = types.ChatPresencePaused
	}

	return s.client.SendChatPresence(jid, presence, "")
}

// SendReceipt sends a read receipt to WhatsApp for the message IDs specified within.
func (s *Session) SendReceipt(receipt Receipt) error {
	if s.client == nil || s.client.Store.ID == nil {
		return fmt.Errorf("Cannot send receipt for unauthenticated session")
	}

	var jid, senderJID types.JID
	var err error

	if receipt.GroupJID != "" {
		if senderJID, err = types.ParseJID(receipt.JID); err != nil {
			return fmt.Errorf("Could not parse sender JID for receipt: %s", err)
		} else if jid, err = types.ParseJID(receipt.GroupJID); err != nil {
			return fmt.Errorf("Could not parse group JID for receipt: %s", err)
		}
	} else {
		if jid, err = types.ParseJID(receipt.JID); err != nil {
			return fmt.Errorf("Could not parse sender JID for receipt: %s", err)
		}
	}

	ids := append([]types.MessageID{}, receipt.MessageIDs...)
	return s.client.MarkRead(ids, time.Unix(receipt.Timestamp, 0), jid, senderJID)
}

// GetContacts subscribes to the WhatsApp roster currently stored in the Session's internal state.
// If `refresh` is `true`, FetchRoster will pull application state from the remote service and
// synchronize any contacts found with the adapter.
func (s *Session) GetContacts(refresh bool) ([]Contact, error) {
	if s.client == nil || s.client.Store.ID == nil {
		return nil, fmt.Errorf("Cannot get contacts for unauthenticated session")
	}

	// Synchronize remote application state with local state if requested.
	if refresh {
		err := s.client.FetchAppState(appstate.WAPatchCriticalUnblockLow, false, false)
		if err != nil {
			s.gateway.logger.Warnf("Could not get app state from server: %s", err)
		}
	}

	// Synchronize local contact state with overarching gateway for all local contacts.
	data, err := s.client.Store.Contacts.GetAllContacts()
	if err != nil {
		return nil, fmt.Errorf("Failed getting local contacts: %s", err)
	}

	var contacts []Contact
	for jid, info := range data {
		if err = s.client.SubscribePresence(jid); err != nil {
			s.gateway.logger.Warnf("Failed to subscribe to presence for %s", jid)
		}

		_, c := newContactEvent(s.client, jid, info)
		contacts = append(contacts, c.Contact)
	}

	return contacts, nil
}

// GetGroups returns a list of all group-chats currently joined in WhatsApp, along with additional
// information on present participants.
func (s *Session) GetGroups() ([]Group, error) {
	if s.client == nil || s.client.Store.ID == nil {
		return nil, fmt.Errorf("Cannot get groups for unauthenticated session")
	}

	data, err := s.client.GetJoinedGroups()
	if err != nil {
		return nil, fmt.Errorf("Failed getting groups: %s", err)
	}

	var groups []Group
	for _, info := range data {
		groups = append(groups, newGroup(s.client, info))
	}

	return groups, nil
}

// GetAvatar fetches a profile picture for the Contact or Group JID given. If a non-empty `avatarID`
// is also given, GetAvatar will return an empty [Avatar] instance with no error if the remote state
// for the given ID has not changed.
func (s *Session) GetAvatar(resourceID, avatarID string) (Avatar, error) {
	jid, err := types.ParseJID(resourceID)
	if err != nil {
		return Avatar{}, fmt.Errorf("Could not parse JID for avatar: %s", err)
	}

	p, err := s.client.GetProfilePictureInfo(jid, &whatsmeow.GetProfilePictureParams{ExistingID: avatarID})
	if err != nil {
		return Avatar{}, fmt.Errorf("Could not get avatar: %s", err)
	} else if p != nil {
		return Avatar{ID: p.ID, URL: p.URL}, nil
	}

	return Avatar{}, nil
}

// SetEventHandler assigns the given handler function for propagating internal events into the Python
// gateway. Note that the event handler function is not entirely safe to use directly, and all calls
// should instead be made via the [propagateEvent] function.
func (s *Session) SetEventHandler(h HandleEventFunc) {
	s.eventHandler = h
}

// PropagateEvent handles the given event kind and payload with the adapter event handler defined in
// SetEventHandler. If no event handler is set, this function will return early with no error.
func (s *Session) propagateEvent(kind EventKind, payload *EventPayload) {
	if s.eventHandler == nil {
		s.gateway.logger.Errorf("Event handler not set when propagating event %d with payload %v", kind, payload)
		return
	} else if kind == EventUnknown {
		return
	}

	// Send empty payload instead of a nil pointer, as Python has trouble handling the latter.
	if payload == nil {
		payload = &EventPayload{}
	}

	// Don't allow other Goroutines from using this thread, as this might lead to concurrent use of
	// the GIL, which can lead to crashes.
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	s.eventHandler(kind, payload)
}

// HandleEvent processes the given incoming WhatsApp event, checking its concrete type and
// propagating it to the adapter event handler. Unknown or unhandled events are ignored, and any
// errors that occur during processing are logged.
func (s *Session) handleEvent(evt interface{}) {
	s.gateway.logger.Debugf("Handling event '%T': %+v", evt, evt)

	switch evt := evt.(type) {
	case *events.AppStateSyncComplete:
		if len(s.client.Store.PushName) > 0 && evt.Name == appstate.WAPatchCriticalBlock {
			s.propagateEvent(EventConnected, &EventPayload{ConnectedJID: s.device.JID().ToNonAD().String()})
			if err := s.client.SendPresence(types.PresenceAvailable); err != nil {
				s.gateway.logger.Warnf("Failed to send available presence: %s", err)
			}
		}
	case *events.Connected, *events.PushNameSetting:
		if len(s.client.Store.PushName) == 0 {
			return
		}
		s.propagateEvent(EventConnected, &EventPayload{ConnectedJID: s.device.JID().ToNonAD().String()})
		if err := s.client.SendPresence(types.PresenceAvailable); err != nil {
			s.gateway.logger.Warnf("Failed to send available presence: %s", err)
		}
	case *events.HistorySync:
		switch evt.Data.GetSyncType() {
		case proto.HistorySync_PUSH_NAME:
			for _, n := range evt.Data.Pushnames {
				jid, err := types.ParseJID(n.GetId())
				if err != nil {
					continue
				}
				s.propagateEvent(newContactEvent(s.client, jid, types.ContactInfo{FullName: n.GetPushname()}))
				if err = s.client.SubscribePresence(jid); err != nil {
					s.gateway.logger.Warnf("Failed to subscribe to presence for %s", jid)
				}
			}
		}
	case *events.Message:
		s.propagateEvent(newMessageEvent(s.client, evt))
	case *events.Receipt:
		s.propagateEvent(newReceiptEvent(evt))
	case *events.Presence:
		s.propagateEvent(newPresenceEvent(evt))
	case *events.PushName:
		s.propagateEvent(newContactEvent(s.client, evt.JID, types.ContactInfo{FullName: evt.NewPushName}))
	case *events.JoinedGroup:
		s.propagateEvent(EventGroup, &EventPayload{Group: newGroup(s.client, &evt.GroupInfo)})
	case *events.GroupInfo:
		s.propagateEvent(newGroupEvent(evt))
	case *events.ChatPresence:
		s.propagateEvent(newChatStateEvent(evt))
	case *events.CallTerminate:
		if evt.Reason == "timeout" {
			s.propagateEvent(newCallEvent(CallMissed, evt.BasicCallMeta))
		}
	case *events.LoggedOut:
		s.client.Disconnect()
		if err := s.client.Store.Delete(); err != nil {
			s.gateway.logger.Warnf("Unable to delete local device state on logout: %s", err)
		}
		s.client = nil
		s.propagateEvent(EventLoggedOut, nil)
	case *events.PairSuccess:
		if s.client.Store.ID == nil {
			s.gateway.logger.Errorf("Pairing succeeded, but device ID is missing")
			return
		}
		deviceID := s.client.Store.ID.String()
		s.propagateEvent(EventPair, &EventPayload{PairDeviceID: deviceID})
		if err := s.gateway.CleanupSession(LinkedDevice{ID: deviceID}); err != nil {
			s.gateway.logger.Warnf("Failed to clean up devices after pair: %s", err)
		}
	case *events.KeepAliveTimeout:
		if evt.ErrorCount > keepAliveFailureThreshold {
			s.gateway.logger.Debugf("Forcing reconnection after keep-alive timeouts...")
			go func() {
				s.client.Disconnect()

				var interval = keepAliveMinRetryInterval
				for {
					err := s.client.Connect()
					if err == nil || err == whatsmeow.ErrAlreadyConnected {
						break
					}

					s.gateway.logger.Errorf("Error reconnecting after keep-alive timeouts, retrying in %s: %s", interval, err)
					time.Sleep(interval)

					if interval > keepAliveMaxRetryInterval {
						interval = keepAliveMaxRetryInterval
					} else if interval < keepAliveMaxRetryInterval {
						interval *= 2
					}
				}
			}()
		}
	}
}

// GetFromURL is a convienience function for fetching the raw response body from the URL given, for
// the provided HTTP client.
func getFromURL(client *http.Client, url string) ([]byte, error) {
	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}

	defer resp.Body.Close()

	buf, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	return buf, nil
}

// PtrTo returns a pointer to the given value, and is used for convenience when converting between
// concrete and pointer values without assigning to a variable.
func ptrTo[T any](t T) *T {
	return &t
}
