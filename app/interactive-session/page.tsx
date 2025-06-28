'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useUnmount } from 'ahooks';
import { useRouter, useSearchParams } from 'next/navigation';
import Cookies from 'js-cookie';

import {
  AvatarQuality,
  StreamingEvents,
  VoiceChatTransport,
  VoiceEmotion,
  StartAvatarRequest,
  STTProvider,
  ElevenLabsModel,
} from '@heygen/streaming-avatar';
import {
  StreamingAvatarProvider,
  StreamingAvatarSessionState,
  useStreamingAvatarSession,
  useVoiceChat,
  MessageSender,
} from '@/components/logic';
import { Button } from '@/components/Button';
import { AvatarConfig } from '@/components/AvatarConfig';
import { AvatarVideo } from '@/components/AvatarSession/AvatarVideo';
import { AvatarControls } from '@/components/AvatarSession/AvatarControls';
import { LoadingIcon } from '@/components/Icons';
import { MessageHistory } from '@/components/AvatarSession/MessageHistory';

const DEFAULT_CONFIG: StartAvatarRequest = {
  quality: AvatarQuality.Low,
  avatarName: 'Ann_Doctor_Standing2_public',
  knowledgeId: '13f254b102cf436d8c07b9fb617dbadf',
  language: 'es',
  voice: {
    rate: 1.5,
    emotion: VoiceEmotion.EXCITED,
    model: ElevenLabsModel.eleven_flash_v2_5,
  },
  voiceChatTransport: VoiceChatTransport.WEBSOCKET,
  sttSettings: { provider: STTProvider.DEEPGRAM },
};

function InteractiveSessionContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    initAvatar, startAvatar, stopAvatar, sessionState, stream, messages,
    handleUserTalkingMessage, handleStreamingTalkingMessage,
  } = useStreamingAvatarSession();
  const { startVoiceChat } = useVoiceChat();

  // --- ESTADO ---
  const [config, setConfig] = useState<StartAvatarRequest>(DEFAULT_CONFIG);
  const [sessionInfo, setSessionInfo] = useState<{ name: string; email: string; scenario: string; token: string } | null>(null);
  const [showAutoplayBlockedMessage, setShowAutoplayBlockedMessage] = useState(false);
  const [isAttemptingAutoStart, setIsAttemptingAutoStart] = useState(false);
  const [recordingTimer, setRecordingTimer] = useState(480);
  const [hasUserMediaPermission, setHasUserMediaPermission] = useState(false);

  // --- REFS ---
  const messagesRef = useRef<any[]>([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunks = useRef<Blob[]>([]);
  const localUserStreamRef = useRef<MediaStream | null>(null);
  const userCameraRef = useRef<HTMLVideoElement>(null);
  const avatarVideoRef = useRef<HTMLVideoElement>(null);
  const isFinalizingRef = useRef(false);

  // ========== CORRECCIÓN #1: LEER DATOS DE SESIÓN EN useEffect ==========
  // Esto soluciona el "React error #418" al asegurar que el código solo se ejecute en el navegador.
  useEffect(() => {
    const name = searchParams.get('name') || Cookies.get('user_name');
    const email = searchParams.get('email') || Cookies.get('user_email');
    const scenario = searchParams.get('scenario') || Cookies.get('user_scenario');
    const token = searchParams.get('token') || Cookies.get('user_token');

    if (name && email && scenario && token) {
      setSessionInfo({ name, email, scenario, token });
    } else {
      console.error("Faltan datos de sesión, redirigiendo al dashboard...");
      router.push('/dashboard');
    }
  }, [searchParams, router]);

  const stopUserCameraRecording = useCallback(() => {
    if (localUserStreamRef.current) {
      localUserStreamRef.current.getTracks().forEach(track => track.stop());
      localUserStreamRef.current = null;
    }
    if (userCameraRef.current) {
      userCameraRef.current.srcObject = null;
    }
  }, []);

  const startUserCameraRecording = useCallback(() => {
    if (!localUserStreamRef.current || mediaRecorderRef.current) return;
    try {
      const recorder = new MediaRecorder(localUserStreamRef.current, {
        mimeType: 'video/webm; codecs=vp8',
        videoBitsPerSecond: 2500000,
        audioBitsPerSecond: 128000,
      });
      recordedChunks.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) recordedChunks.current.push(e.data);
      };
      recorder.onerror = (ev: Event) => {
        const err = (ev as any).error;
        if (err) console.error("🎥 MediaRecorder ERROR:", err);
      };
      recorder.start();
      mediaRecorderRef.current = recorder;
    } catch (err) { console.error('Error al iniciar MediaRecorder:', err); }
  }, []);

  const stopAndFinalizeSession = useCallback(async (sessionMessages: any[]) => {
    if (isFinalizingRef.current || !sessionInfo) return;
    isFinalizingRef.current = true;
    console.log("🛑 Finalizando sesión...");
    stopAvatar();

    let finalVideoBlob: Blob | null = null;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        const recorder = mediaRecorderRef.current;
        await new Promise<void>(resolve => {
            recorder.onstop = () => {
                console.log("🎥 MediaRecorder: onstop event fired.");
                resolve();
            };
            recorder.stop();
        });
        if (recordedChunks.current.length > 0) {
            finalVideoBlob = new Blob(recordedChunks.current, { type: "video/webm" });
        }
    }
    stopUserCameraRecording();

    const userTranscript = sessionMessages.filter(m => m.sender === MessageSender.CLIENT).map(m => m.content).join('\n');
    const avatarTranscript = sessionMessages.filter(m => m.sender === MessageSender.AVATAR).map(m => m.content).join('\n');
    const duration = 480 - recordingTimer;

    const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL || '';
    let videoS3Key: string | null = null;

    try {
      if (finalVideoBlob) {
          const videoFormData = new FormData();
          videoFormData.append('video', finalVideoBlob, "user_recording.webm");
          videoFormData.append('name', sessionInfo.name);
          videoFormData.append('email', sessionInfo.email);

          // ========== CORRECCIÓN #2: AÑADIR TOKEN JWT AL SUBIR EL VIDEO ==========
          // Esto soluciona el error "401 Unauthorized" / "token faltante".
          const jwt = Cookies.get('jwt');
          const headers: HeadersInit = {};
          if (jwt) {
              headers['Authorization'] = `Bearer ${jwt}`;
          }

          const uploadRes = await fetch(`${flaskApiUrl}/upload_video`, {
              method: "POST",
              headers: headers, // Añadimos el header de autorización
              body: videoFormData,
          });

          if (!uploadRes.ok) {
              const errorText = await uploadRes.text();
              throw new Error(`Error al subir video: ${uploadRes.status} ${errorText}`);
          }
          const uploadData = await uploadRes.json();
          videoS3Key = uploadData.s3_object_key;
      }

      await fetch(`${flaskApiUrl}/log_full_session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: sessionInfo.name,
          email: sessionInfo.email,
          scenario: sessionInfo.scenario,
          conversation: userTranscript,
          avatar_transcript: avatarTranscript,
          duration: duration,
          video_object_key: videoS3Key
        })
      });

    } catch (err: any) {
        console.error("❌ Error en la finalización de la sesión:", err.message);
        alert(`⚠️ Ocurrió un error al guardar la sesión: ${err.message}`);
    } finally {
        router.push('/dashboard');
    }
  }, [stopAvatar, stopUserCameraRecording, recordingTimer, sessionInfo, router]);

  const fetchAccessToken = useCallback(async () => {
    try {
      const response = await fetch("/api/get-access-token", { method: "POST" });
      if (!response.ok) throw new Error(`Fallo al obtener token de acceso: ${response.status}`);
      return await response.text();
    } catch (error) {
      console.error("Error obteniendo token de acceso:", error);
      throw error;
    }
  }, []);

  const startHeyGenSession = useCallback(async (startWithVoice: boolean) => {
    if (!hasUserMediaPermission) {
      alert("Por favor, permite el acceso a la cámara y el micrófono.");
      return;
    }
    setIsAttemptingAutoStart(true);
    try {
      const heygenToken = await fetchAccessToken();
      const avatar = initAvatar(heygenToken);
      
      avatar.on(StreamingEvents.STREAM_DISCONNECTED, () => {
        if (!isFinalizingRef.current) stopAndFinalizeSession(messagesRef.current);
      });
      avatar.on(StreamingEvents.STREAM_READY, () => setIsAttemptingAutoStart(false));
      avatar.on(StreamingEvents.USER_TALKING_MESSAGE, (e) => handleUserTalkingMessage({ detail: e.detail }));
      avatar.on(StreamingEvents.AVATAR_TALKING_MESSAGE, (e) => handleStreamingTalkingMessage({ detail: e.detail }));

      await startAvatar(config);
      if (startWithVoice) await startVoiceChat();
    } catch (error: any) {
      console.error("Error iniciando sesión con HeyGen:", error);
      setShowAutoplayBlockedMessage(true);
    } finally {
      setIsAttemptingAutoStart(false);
    }
  }, [hasUserMediaPermission, fetchAccessToken, initAvatar, config, startAvatar, startVoiceChat, stopAndFinalizeSession, handleUserTalkingMessage, handleStreamingTalkingMessage, messagesRef]);

  useEffect(() => {
    if (!sessionInfo) return;
    const getUserMediaStream = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: { width: 640, height: 480, frameRate: 15 }});
        localUserStreamRef.current = stream;
        if (userCameraRef.current) userCameraRef.current.srcObject = stream;
        setHasUserMediaPermission(true);
      } catch (error) {
        console.error("❌ Error al obtener permisos de cámara/micrófono:", error);
        setHasUserMediaPermission(false);
        setShowAutoplayBlockedMessage(true);
      }
    };
    getUserMediaStream();
  }, [sessionInfo]);

  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && hasUserMediaPermission && !mediaRecorderRef.current) {
      startUserCameraRecording();
    }
  }, [sessionState, hasUserMediaPermission, startUserCameraRecording]);

  useEffect(() => {
    if (stream && avatarVideoRef.current) {
      avatarVideoRef.current.srcObject = stream;
      avatarVideoRef.current.onloadedmetadata = () => {
        avatarVideoRef.current!.play().catch((err) => {
          console.warn("Autoplay bloqueado:", err);
          setShowAutoplayBlockedMessage(true);
        });
      };
    }
  }, [stream]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (sessionState === StreamingAvatarSessionState.CONNECTED) {
      interval = setInterval(() => {
        setRecordingTimer(prev => {
          if (prev <= 1) {
            clearInterval(interval);
            stopAndFinalizeSession(messagesRef.current);
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => clearInterval(interval);
  }, [sessionState, stopAndFinalizeSession, messagesRef]);

  useUnmount(() => {
    if (!isFinalizingRef.current) {
        stopAndFinalizeSession(messagesRef.current);
    }
  });

  const formatTime = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
  };

  const handleAutoplayRetry = () => {
    if (hasUserMediaPermission) {
        setShowAutoplayBlockedMessage(false);
        startHeyGenSession(true);
    } else {
        alert("Por favor, permite el acceso a la cámara y el micrófono primero.");
    }
  }

  if (!sessionInfo) {
    return (
      <div className="w-screen h-screen flex flex-col items-center justify-center bg-zinc-900 text-white">
        <LoadingIcon className="w-10 h-10 animate-spin" />
        <p className="mt-4">Cargando datos de sesión...</p>
      </div>
    );
  }

  return (
    <div className="w-screen h-screen flex flex-col items-center bg-zinc-900 text-white relative">
      <h1 className="text-3xl font-bold text-blue-400 mt-6 mb-4">{`🧠 Leo – ${sessionInfo.scenario}`}</h1>
      
      {sessionState === StreamingAvatarSessionState.INACTIVE && !hasUserMediaPermission && !showAutoplayBlockedMessage && (
        <p className="text-zinc-300 mb-6">Solicitando permisos para cámara y micrófono...</p>
      )}
      {showAutoplayBlockedMessage && (
        <div className="text-red-400 mb-6">Error: Permisos de cámara/micrófono denegados o no disponibles.</div>
      )}

      <div className="relative w-full max-w-4xl h-auto flex flex-col md:flex-row items-center justify-center gap-5 p-4">
        <div className="relative w-full md:w-1/2 aspect-video min-h-[300px] flex items-center justify-center bg-zinc-800 rounded-lg shadow-lg overflow-hidden">
          {sessionState !== StreamingAvatarSessionState.INACTIVE ? (
            <AvatarVideo ref={avatarVideoRef} />
          ) : (
            !showAutoplayBlockedMessage && <AvatarConfig config={config} onConfigChange={setConfig} />
          )}
          {showAutoplayBlockedMessage && (
            <div className="absolute inset-0 bg-black bg-opacity-75 flex flex-col items-center justify-center text-center p-4 z-30">
              <p className="mb-4 text-lg font-semibold">Video y Audio Bloqueados</p>
              <p className="mb-6">Tu navegador ha bloqueado los permisos. Haz clic para reintentar.</p>
              <Button onClick={handleAutoplayRetry} className="bg-blue-600 hover:bg-blue-700">Habilitar Video y Audio</Button>
            </div>
          )}
          {sessionState === StreamingAvatarSessionState.CONNECTING && <div className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-50 z-20"><LoadingIcon className="w-10 h-10 animate-spin" /> <span className="ml-2">Conectando...</span></div>}
          {sessionState === StreamingAvatarSessionState.CONNECTED && <div className="absolute top-2 left-2 bg-black bg-opacity-70 text-white text-sm px-3 py-1 rounded-full z-10">Grabando: {formatTime(recordingTimer)}</div>}
        </div>
        <div className="w-full md:w-1/2">
          <video ref={userCameraRef} autoPlay muted playsInline className="rounded-lg border border-blue-500 w-full aspect-video object-cover bg-black" />
        </div>
      </div>

      <div className="flex flex-col gap-3 items-center justify-center p-4 border-t border-zinc-700 w-full mt-6">
        {sessionState === StreamingAvatarSessionState.INACTIVE && !showAutoplayBlockedMessage && (
          <div className="flex flex-row gap-4">
            <Button onClick={() => startHeyGenSession(true)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>Iniciar Chat de Voz</Button>
          </div>
        )}
        {sessionState === StreamingAvatarSessionState.CONNECTED && (
          <>
            <AvatarControls />
            <Button onClick={() => stopAndFinalizeSession(messagesRef.current)} className="bg-red-600 hover:bg-red-700">Finalizar Sesión</Button>
          </>
        )}
      </div>

      {sessionState === StreamingAvatarSessionState.CONNECTED && <MessageHistory />}

      <footer className="mt-auto mb-5 text-sm text-zinc-500 text-center w-full">
        <p>Desarrollado por <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">Teams</a> &copy; 2025</p>
      </footer>
    </div>
  );
}

// El Wrapper se mantiene igual
export default function InteractiveSessionWrapper() {
  return (
    <StreamingAvatarProvider basePath={process.env.NEXT_PUBLIC_BASE_API_URL || ''}>
      <InteractiveSessionContent />
    </StreamingAvatarProvider>
  );
}
```