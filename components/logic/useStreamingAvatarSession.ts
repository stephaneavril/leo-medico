// File: C:\Users\avril\OneDrive\Escritorio\LEO_API\InteractiveAvatarNextJSDemo-main\components\logic\useStreamingAvatarSession.ts
import StreamingAvatar, {
  ConnectionQuality,
  StartAvatarRequest,
  StreamingEvents,
} from "@heygen/streaming-avatar";
import { useCallback } from "react";

import {
  StreamingAvatarSessionState,
  useStreamingAvatarContext,
} from "./context";
import { useVoiceChat } from "./useVoiceChat";
// Si usas useMessageHistory en otros lugares, asegúrate de que no haya importaciones duplicadas.
// En page.tsx, messages viene directamente de useStreamingAvatarSession,
// por lo que no necesitas importar useMessageHistory allí.

export const useStreamingAvatarSession = () => {
  const {
    avatarRef,
    basePath,
    sessionState,
    setSessionState,
    stream,
    setStream,
    setIsListening,
    setIsUserTalking,
    setIsAvatarTalking,
    setConnectionQuality,
    // <<<< AHORA SÍ DESTRUCTRURAMOS LAS FUNCIONES Y EL ESTADO 'messages' >>>
    handleUserTalkingMessage, 
    handleStreamingTalkingMessage, 
    handleEndMessage, 
    clearMessages,
    messages // También obtenemos el estado 'messages' directamente del contexto
  } = useStreamingAvatarContext();
  const { stopVoiceChat } = useVoiceChat();

  const init = useCallback(
    (token: string) => {
      avatarRef.current = new StreamingAvatar({
        token,
        basePath: basePath,
      });

      return avatarRef.current;
    },
    [basePath, avatarRef],
  );

  const handleStream = useCallback(
    ({ detail }: { detail: MediaStream }) => {
      setStream(detail);
      setSessionState(StreamingAvatarSessionState.CONNECTED);
    },
    [setSessionState, setStream],
  );

  const stop = useCallback(async () => {
    avatarRef.current?.off(StreamingEvents.STREAM_READY, handleStream);
    avatarRef.current?.off(StreamingEvents.STREAM_DISCONNECTED, stop);
    clearMessages();
    stopVoiceChat();
    setIsListening(false);
    setIsUserTalking(false);
    setIsAvatarTalking(false);
    setStream(null);
    await avatarRef.current?.stopAvatar();
    setSessionState(StreamingAvatarSessionState.INACTIVE);
  }, [
    handleStream,
    setSessionState,
    setStream,
    avatarRef,
    setIsListening,
    stopVoiceChat,
    clearMessages,
    setIsUserTalking,
    setIsAvatarTalking,
  ]);

  const start = useCallback(
    async (config: StartAvatarRequest, token?: string) => {
      if (sessionState !== StreamingAvatarSessionState.INACTIVE) {
        throw new Error("There is already an active session");
      }

      if (!avatarRef.current) {
        if (!token) {
          throw new Error("Token is required");
        }
        init(token);
      }

      if (!avatarRef.current) {
        throw new Error("Avatar is not initialized");
      }

      setSessionState(StreamingAvatarSessionState.CONNECTING);
      avatarRef.current.on(StreamingEvents.STREAM_READY, handleStream);
      avatarRef.current.on(StreamingEvents.STREAM_DISCONNECTED, stop);
      avatarRef.current.on(
        StreamingEvents.CONNECTION_QUALITY_CHANGED,
        ({ detail }: { detail: ConnectionQuality }) =>
          setConnectionQuality(detail),
      );
      avatarRef.current.on(StreamingEvents.USER_START, () => {
        setIsUserTalking(true);
      });
      avatarRef.current.on(StreamingEvents.USER_STOP, () => {
        setIsUserTalking(false);
      });
      avatarRef.current.on(StreamingEvents.AVATAR_START_TALKING, () => {
        setIsAvatarTalking(true);
      });
      avatarRef.current.on(StreamingEvents.AVATAR_STOP_TALKING, () => {
        setIsAvatarTalking(false);
      });
      avatarRef.current.on(
        StreamingEvents.USER_TALKING_MESSAGE,
        handleUserTalkingMessage, // <<< USANDO LA FUNCIÓN DEL CONTEXTO DIRECTAMENTE
      );
      avatarRef.current.on(
        StreamingEvents.AVATAR_TALKING_MESSAGE,
        handleStreamingTalkingMessage, // <<< USANDO LA FUNCIÓN DEL CONTEXTO DIRECTAMENTE
      );
      avatarRef.current.on(StreamingEvents.USER_END_MESSAGE, handleEndMessage); // <<< USANDO LA FUNCIÓN DEL CONTEXTO DIRECTAMENTE
      avatarRef.current.on(
        StreamingEvents.AVATAR_END_MESSAGE,
        handleEndMessage, // <<< USANDO LA FUNCIÓN DEL CONTEXTO DIRECTAMENTE
      );

      await avatarRef.current.createStartAvatar(config);

      return avatarRef.current;
    },
    [
      init,
      handleStream,
      stop,
      setSessionState,
      avatarRef,
      sessionState,
      setConnectionQuality,
      setIsUserTalking,
      handleUserTalkingMessage, // Añadido a las dependencias
      handleStreamingTalkingMessage, // Añadido a las dependencias
      handleEndMessage, // Añadido a las dependencias
      setIsAvatarTalking,
    ],
  );

  return {
    avatarRef,
    sessionState,
    stream,
    initAvatar: init,
    startAvatar: start,
    stopAvatar: stop,
    messages, // <<< EXPORTAMOS EL ESTADO 'messages'
    handleUserTalkingMessage, // <<< EXPORTAMOS LA FUNCIÓN
    handleStreamingTalkingMessage, // <<< EXPORTAMOS LA FUNCIÓN
    handleEndMessage, // <<< EXPORTAMOS LA FUNCIÓN
  };
};