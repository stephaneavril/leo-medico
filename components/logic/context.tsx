// File: components/logic/context.tsx

import StreamingAvatar, {
  ConnectionQuality,
  StreamingTalkingMessageEvent,
  UserTalkingMessageEvent,
} from "@heygen/streaming-avatar";
import React, { useRef, useState, useCallback } from "react";

export enum StreamingAvatarSessionState {
  INACTIVE = "inactive",
  CONNECTING = "connecting",
  CONNECTED = "connected",
}

export enum MessageSender {
  CLIENT = "CLIENT",
  AVATAR = "AVATAR",
}

export interface Message {
  id: string;
  sender: MessageSender;
  content: string;
}

type StreamingAvatarContextProps = {
  avatarRef: React.MutableRefObject<StreamingAvatar | null>;
  basePath?: string;

  isMuted: boolean;
  setIsMuted: (isMuted: boolean) => void;
  isVoiceChatLoading: boolean;
  setIsVoiceChatLoading: (isVoiceChatLoading: boolean) => void;
  isVoiceChatActive: boolean;
  setIsVoiceChatActive: (isVoiceChatActive: boolean) => void;

  sessionState: StreamingAvatarSessionState;
  setSessionState: (sessionState: StreamingAvatarSessionState) => void;
  stream: MediaStream | null;
  setStream: (stream: MediaStream | null) => void;

  messages: Message[];
  clearMessages: () => void;
  handleUserTalkingMessage: ({
    detail,
  }: {
    detail: UserTalkingMessageEvent;
  }) => void;
  handleStreamingTalkingMessage: ({
    detail,
  }: {
    detail: StreamingTalkingMessageEvent;
  }) => void;
  handleEndMessage: () => void;

  isListening: boolean;
  setIsListening: (isListening: boolean) => void;
  isUserTalking: boolean;
  setIsUserTalking: (isUserTalking: boolean) => void;
  isAvatarTalking: boolean;
  setIsAvatarTalking: (isAvatarTalking: boolean) => void;

  connectionQuality: ConnectionQuality;
  setConnectionQuality: (connectionQuality: ConnectionQuality) => void;
};

const StreamingAvatarContext = React.createContext<StreamingAvatarContextProps>(
  {
    avatarRef: { current: null },
    isMuted: true,
    setIsMuted: () => {},
    isVoiceChatLoading: false,
    setIsVoiceChatLoading: () => {},
    sessionState: StreamingAvatarSessionState.INACTIVE,
    setSessionState: () => {},
    isVoiceChatActive: false,
    setIsVoiceChatActive: () => {},
    stream: null,
    setStream: () => {},
    messages: [],
    clearMessages: () => {},
    handleUserTalkingMessage: () => {},
    handleStreamingTalkingMessage: () => {},
    handleEndMessage: () => {},
    isListening: false,
    setIsListening: () => {},
    isUserTalking: false,
    setIsUserTalking: () => {},
    isAvatarTalking: false,
    setIsAvatarTalking: () => {},
    connectionQuality: ConnectionQuality.UNKNOWN,
    setConnectionQuality: () => {},
  },
);

const useStreamingAvatarSessionState = () => {
  const [sessionState, setSessionState] = useState(
    StreamingAvatarSessionState.INACTIVE,
  );
  const [stream, setStream] = useState<MediaStream | null>(null);

  return {
    sessionState,
    setSessionState,
    stream,
    setStream,
  };
};

const useStreamingAvatarVoiceChatState = () => {
  const [isMuted, setIsMuted] = useState(true);
  const [isVoiceChatLoading, setIsVoiceChatLoading] = useState(false);
  const [isVoiceChatActive, setIsVoiceChatActive] = useState(false);

  return {
    isMuted,
    setIsMuted,
    isVoiceChatLoading,
    setIsVoiceChatLoading,
    isVoiceChatActive,
    setIsVoiceChatActive,
  };
};

// ADDED: Definition for useStreamingAvatarMessageState (already present, but confirming its placement)
const useStreamingAvatarMessageState = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  // This ref tracks the sender of the *current, ongoing* message.
  const currentSenderRef = useRef<MessageSender | null>(null);
  // This ref stores the message ID of the *current, ongoing* message.
  const currentMessageIdRef = useRef<string | null>(null);

  const handleNewSegment = useCallback((
    newSegment: string,
    sender: MessageSender,
    messageId: string // Unique ID for this utterance/segment
  ) => {
    if (!newSegment.trim()) { // Ignore empty or whitespace-only segments
        return;
    }

    setMessages((prev) => {
      const safePrev = Array.isArray(prev) ? prev : [];

      // If the sender is the same AND it's the same logical message (same ID)
      // then we append to the last message.
      if (currentSenderRef.current === sender && currentMessageIdRef.current === messageId && safePrev.length > 0) {
        const lastMessage = safePrev[safePrev.length - 1];
        const lastContent = typeof lastMessage?.content === 'string' ? lastMessage.content : '';

        // Add a space if the last character wasn't already a space/punctuation
        const contentToAdd = (lastContent.endsWith(' ') || lastContent.endsWith('.') || lastContent.endsWith('?') || lastContent.endsWith('!'))
                             ? newSegment.trim()
                             : (lastContent ? ' ' : '') + newSegment.trim();

        // Prevent adding completely identical consecutive words/phrases if already joined
        // This is a basic de-duplication, more advanced might use diffing
        if (lastContent.endsWith(newSegment.trim())) { // If the new segment is identical to the end of the last, skip
            return prev; 
        }
        if (lastContent.includes(newSegment.trim()) && lastContent.slice(-newSegment.trim().length * 2).includes(newSegment.trim())) {
            // Heuristic: if newSegment is a repetition of the last part of lastContent
            return prev;
        }


        return [
          ...safePrev.slice(0, -1),
          {
            ...lastMessage,
            content: lastContent + contentToAdd, // Concatenate carefully
          },
        ];
      } else {
        // Start a new message
        currentSenderRef.current = sender;
        currentMessageIdRef.current = messageId; // Update message ID for new logical message
        return [
          ...safePrev,
          {
            id: Date.now().toString() + '_' + sender + '_' + messageId, // Ensure unique ID for React keys
            sender: sender,
            content: newSegment.trim(),
          },
        ];
      }
    });
  }, []);


  const handleUserTalkingMessage = useCallback(({ detail }: { detail: UserTalkingMessageEvent; }) => {
    // HeyGen's USER_TALKING_MESSAGE and AVATAR_TALKING_MESSAGE events have a `message` property directly.
    // The `detail` property often contains additional metadata like `task_id`.
    const messageContent = typeof detail.message === 'string' ? detail.message : '';
    const messageId = detail.task_id || Date.now().toString(); // Use task_id for message ID if available

    // Log the incoming message to debug the frontend events
    console.log('Context: handleUserTalkingMessage - Incoming:', { message: messageContent, id: messageId, sender: MessageSender.CLIENT });

    // IMPORTANT: Ensure the sender is correctly identified before calling handleNewSegment
    // If this handler is being called with Avatar's speech, there's a problem upstream in HeyGen event routing or your listeners.
    // For now, we'll process it as User, but be aware this is the source of misattribution if it happens frequently.
    handleNewSegment(messageContent, MessageSender.CLIENT, messageId);
  }, [handleNewSegment]);


  const handleStreamingTalkingMessage = useCallback(({ detail }: { detail: StreamingTalkingMessageEvent; }) => {
    // HeyGen's USER_TALKING_MESSAGE and AVATAR_TALKING_MESSAGE events have a `message` property directly.
    const messageContent = typeof detail.message === 'string' ? detail.message : '';
    const messageId = detail.task_id || Date.now().toString(); // Use task_id for message ID if available

    // Log the incoming message to debug the frontend events
    console.log('Context: handleStreamingTalkingMessage - Incoming:', { message: messageContent, id: messageId, sender: MessageSender.AVATAR });

    // IMPORTANT: Ensure the sender is correctly identified before calling handleNewSegment
    // If this handler is being called with User's speech, there's a problem upstream in HeyGen event routing or your listeners.
    handleNewSegment(messageContent, MessageSender.AVATAR, messageId);
  }, [handleNewSegment]);


  const handleEndMessage = useCallback(() => {
    // When an utterance ends, finalize the current message.
    // Reset sender and message ID so next segment starts a new message.
    currentSenderRef.current = null;
    currentMessageIdRef.current = null;
    console.log('Context: handleEndMessage called. Message finalized for next turn.');
  }, []);

  return {
    messages,
    clearMessages: useCallback(() => {
      console.log('Context: clearMessages called.');
      setMessages([]);
      currentSenderRef.current = null;
      currentMessageIdRef.current = null;
    }, []),
    handleUserTalkingMessage,
    handleStreamingTalkingMessage,
    handleEndMessage,
  };
};

// ADDED: Definitions for the missing state hooks
const useStreamingAvatarListeningState = () => {
  const [isListening, setIsListening] = useState(false);
  return { isListening, setIsListening };
};

const useStreamingAvatarTalkingState = () => {
  const [isUserTalking, setIsUserTalking] = useState(false);
  const [isAvatarTalking, setIsAvatarTalking] = useState(false);
  return { isUserTalking, setIsUserTalking, isAvatarTalking, setIsAvatarTalking };
};

const useStreamingAvatarConnectionQualityState = () => {
  const [connectionQuality, setConnectionQuality] = useState(ConnectionQuality.UNKNOWN);
  return { connectionQuality, setConnectionQuality };
};


export const StreamingAvatarProvider = ({
  children,
  basePath,
}: {
  children: React.ReactNode;
  basePath?: string;
}) => {
  const avatarRef = React.useRef<StreamingAvatar | null>(null); 
  const voiceChatState = useStreamingAvatarVoiceChatState();
  const sessionState = useStreamingAvatarSessionState();
  const messageState = useStreamingAvatarMessageState(); 
  const listeningState = useStreamingAvatarListeningState(); 
  const talkingState = useStreamingAvatarTalkingState();
  const connectionQualityState = useStreamingAvatarConnectionQualityState();

  return (
    <StreamingAvatarContext.Provider
      value={{
        avatarRef,
        basePath,
        ...voiceChatState,
        ...sessionState,
        ...messageState, 
        ...listeningState,
        ...talkingState,
        ...connectionQualityState,
      }}
    >
      {children}
    </StreamingAvatarContext.Provider>
  );
};

export const useStreamingAvatarContext = () => {
  return React.useContext(StreamingAvatarContext);
};