// File: stephaneavril/leo_api/LEO_API-41312fbadb4af8d7b7904b3ffb825896429b306c/components/logic/context.tsx
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

const useStreamingAvatarMessageState = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const currentSenderRef = useRef<MessageSender | null>(null);

  const handleUserTalkingMessage = useCallback(({
    detail,
  }: {
    detail: UserTalkingMessageEvent;
  }) => {
    console.log('Context: handleUserTalkingMessage - Raw detail:', detail); // NEW DEBUG LOG
    const messageContent = typeof detail.message === 'string' ? detail.message : ''; // Ensure string or empty
    console.log('Context: handleUserTalkingMessage - Processed messageContent:', messageContent); // NEW DEBUG LOG
    
    setMessages((prev) => {
      const safePrev = Array.isArray(prev) ? prev : [];
      
      if (currentSenderRef.current === MessageSender.CLIENT && safePrev.length > 0) {
        const lastMessage = safePrev[safePrev.length - 1];
        const lastContent = typeof lastMessage?.content === 'string' ? lastMessage.content : ''; // Safely get content as string
        console.log('Context: Appending user message:', messageContent, 'to last:', lastContent); // DEBUG LOG
        return [
          ...safePrev.slice(0, -1),
          {
            ...lastMessage, // Keep other properties of the last message
            content: [lastContent, messageContent].join(""), // Concatenate safely
          },
        ];
      } else {
        console.log('Context: Adding new user message:', messageContent); // DEBUG LOG
        currentSenderRef.current = MessageSender.CLIENT;
        return [
          ...safePrev,
          {
            id: Date.now().toString(),
            sender: MessageSender.CLIENT,
            content: messageContent,
          },
        ];
      }
    });
  }, []);

  const handleStreamingTalkingMessage = useCallback(({
    detail,
  }: {
    detail: StreamingTalkingMessageEvent;
  }) => {
    console.log('Context: handleStreamingTalkingMessage - Raw detail:', detail); // NEW DEBUG LOG
    const messageContent = typeof detail.message === 'string' ? detail.message : ''; // Ensure string or empty
    console.log('Context: handleStreamingTalkingMessage - Processed messageContent:', messageContent); // NEW DEBUG LOG

    setMessages((prev) => {
      const safePrev = Array.isArray(prev) ? prev : [];

      if (currentSenderRef.current === MessageSender.AVATAR && safePrev.length > 0) {
        const lastMessage = safePrev[safePrev.length - 1];
        const lastContent = typeof lastMessage?.content === 'string' ? lastMessage.content : ''; // Safely get content as string
        console.log('Context: Appending avatar message:', messageContent, 'to last:', lastContent); // DEBUG LOG
        return [
          ...safePrev.slice(0, -1),
          {
            ...lastMessage, // Keep other properties of the last message
            content: [lastContent, messageContent].join(""), // Concatenate safely
          },
        ];
      } else {
        console.log('Context: Adding new avatar message:', messageContent); // DEBUG LOG
        currentSenderRef.current = MessageSender.AVATAR;
        return [
          ...safePrev,
          {
            id: Date.now().toString(),
            sender: MessageSender.AVATAR,
            content: messageContent,
          },
        ];
      }
    });
  }, []);

  const handleEndMessage = useCallback(() => {
    console.log('Context: handleEndMessage called.');
    currentSenderRef.current = null;
  }, []);

  return {
    messages,
    clearMessages: useCallback(() => {
      console.log('Context: clearMessages called.');
      setMessages([]);
      currentSenderRef.current = null;
    }, []),
    handleUserTalkingMessage,
    handleStreamingTalkingMessage,
    handleEndMessage,
  };
};

const useStreamingAvatarListeningState = () => {
  const [isListening, setIsListening] = useState(false);

  return { isListening, setIsListening };
};

const useStreamingAvatarTalkingState = () => {
  const [isUserTalking, setIsUserTalking] = useState(false);
  const [isAvatarTalking, setIsAvatarTalking] = useState(false);

  return {
    isUserTalking,
    setIsUserTalking,
    isAvatarTalking,
    setIsAvatarTalking,
  };
};

const useStreamingAvatarConnectionQualityState = () => {
  const [connectionQuality, setConnectionQuality] = useState(
    ConnectionQuality.UNKNOWN,
  );

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