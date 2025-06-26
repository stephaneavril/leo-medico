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
  // Este ref rastrea el ID del mensaje actual para concatenar segmentos.
  const currentMessageIdRef = useRef<string | null>(null);

  const handleNewSegment = useCallback((
    newSegment: string,
    sender: MessageSender,
    messageId: string // Unique ID for this utterance/segment
  ) => {
    const trimmedNewSegment = newSegment.trim();
    if (!trimmedNewSegment) {
        console.log(`Context: handleNewSegment - IGNORANDO SEGMENTO VACÍO de ${sender}.`);
        return;
    }

    setMessages((prev) => {
      const safePrev = Array.isArray(prev) ? prev : [];
      const lastMessage = safePrev.length > 0 ? safePrev[safePrev.length - 1] : null;

      // Concatenar al último mensaje si es del mismo hablante Y el mismo ID de mensaje
      if (lastMessage && lastMessage.sender === sender && lastMessage.id === messageId) {
        const lastContent = typeof lastMessage.content === 'string' ? lastMessage.content : '';

        // Si el nuevo segmento ya es parte del contenido final o es idéntico, ignorar para evitar duplicados exactos
        if (lastContent.endsWith(trimmedNewSegment) || lastContent.trim() === trimmedNewSegment) {
            console.log(`Context: handleNewSegment - SKIPPING DUPLICADO/FINALIZADO de ${sender} (ID: ${messageId}): "${trimmedNewSegment}" (contenido previo: "${lastContent}")`);
            return prev;
        }

        // Añadir espacio si es necesario
        const contentToAdd = (lastContent.length > 0 && ![',', '.', '?', '!'].includes(lastContent.slice(-1)))
                             ? ' ' + trimmedNewSegment
                             : trimmedNewSegment;

        console.log(`Context: handleNewSegment - CONCATENANDO a ${sender} (ID: ${messageId}): "${trimmedNewSegment}" (nuevo contenido: "${lastContent + contentToAdd}")`);
        return [
          ...safePrev.slice(0, -1),
          {
            ...lastMessage,
            content: lastContent + contentToAdd,
          },
        ];
      } else {
        // Iniciar un nuevo mensaje
        console.log(`Context: handleNewSegment - INICIANDO NUEVO mensaje para ${sender} (ID: ${messageId}): "${trimmedNewSegment}"`);
        // Actualizar el ref del ID del mensaje actual para la próxima concatenación
        currentMessageIdRef.current = messageId;
        return [
          ...safePrev,
          {
            id: messageId, // Usamos el messageId de HeyGen como el ID de la entrada
            sender: sender,
            content: trimmedNewSegment,
          },
        ];
      }
    });
  }, []); // Dependencias: ninguna, useCallback es estable

  // handleUserTalkingMessage y handleStreamingTalkingMessage se mantienen igual
  const handleUserTalkingMessage = useCallback(({ detail }: { detail: UserTalkingMessageEvent; }) => {
    const messageContent = typeof detail.message === 'string' ? detail.message : '';
    const messageId = detail.task_id || `user_${Date.now().toString()}`; // Fallback ID

    console.log(`Context: handleUserTalkingMessage - RECIBIDO: "${messageContent}" (ID: ${messageId}, Hablante: CLIENT)`);
    handleNewSegment(messageContent, MessageSender.CLIENT, messageId);
  }, [handleNewSegment]);

  const handleStreamingTalkingMessage = useCallback(({ detail }: { detail: StreamingTalkingMessageEvent; }) => {
    const messageContent = typeof detail.message === 'string' ? detail.message : '';
    const messageId = detail.task_id || `avatar_${Date.now().toString()}`; // Fallback ID

    console.log(`Context: handleStreamingTalkingMessage - RECIBIDO: "${messageContent}" (ID: ${messageId}, Hablante: AVATAR)`);
    handleNewSegment(messageContent, MessageSender.AVATAR, messageId);
  }, [handleNewSegment]);

  const handleEndMessage = useCallback(() => {
    // Cuando una locución termina, finaliza el mensaje actual.
    // Esto asegura que la próxima locución empiece un NUEVO mensaje en la historia.
    currentMessageIdRef.current = null; // Reseteamos el ID del mensaje para forzar un nuevo mensaje.
    console.log('Context: handleEndMessage llamado. Mensaje finalizado para el próximo turno.');
  }, []);

  return {
    messages,
    clearMessages: useCallback(() => {
      console.log('Context: clearMessages llamado.');
      setMessages([]);
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