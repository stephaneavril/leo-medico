'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useUnmount } from 'ahooks';
import { useRouter, useSearchParams } from 'next/navigation';

import {
  AvatarQuality,
  StreamingEvents,
  VoiceChatTransport,
  VoiceEmotion,
  StartAvatarRequest,
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
import { LoaderCircle } from 'lucide-react';

// ───────────────────────────────────────────────────────────────
// Default HeyGen avatar config
// ───────────────────────────────────────────────────────────────
const DEFAULT_CONFIG: StartAvatarRequest = {
  quality: AvatarQuality.Low,
  avatarName: 'Ann_Doctor_Standing2_public',
  knowledgeId: '13f254b102cf436d8c07b9fb617dbadf',
  language: 'es',
  voice: {
    voiceId: '1edf8ae6571d46c8b7e719eaa91f93c6', // tu voz
    model: ElevenLabsModel.eleven_multilingual_v2,
    rate: 1.15,
    emotion: VoiceEmotion.FRIENDLY,
  },
  // Mantén WEBSOCKET (tu SDK no exporta WEBRTC)
  voiceChatTransport: VoiceChatTransport.WEBSOCKET,

  // ⚠️ Quitar STT forzado por ahora (dejamos el default de HeyGen)
  // sttSettings: { provider: STTProvider.DEEPGRAM },
};

function InteractiveSessionContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const {
    initAvatar,
    startAvatar,
    stopAvatar,
    sessionState,
    stream,
    messages,
    handleUserTalkingMessage,
    handleStreamingTalkingMessage,
  } = useStreamingAvatarSession();

  const { startVoiceChat } = useVoiceChat();

  // ── Local UI State ────────────────────────────────────────────
  const [config, setConfig] = useState<StartAvatarRequest>(DEFAULT_CONFIG);
  const [sessionInfo, setSessionInfo] = useState<{
    name: string;
    email: string;
    scenario: string;
    token: string;
  } | null>(null);
  const [isReady, setIsReady] = useState(false);
  const [showAutoplayBlockedMessage, setShowAutoplayBlockedMessage] = useState(false);
  const [isAttemptingAutoStart, setIsAttemptingAutoStart] = useState(false);
  const [hasUserMediaPermission, setHasUserMediaPermission] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  // ── Refs ─────────────────────────────────────────────────────
  const recordingTimerRef = useRef<number>(480);
  const [timerDisplay, setTimerDisplay] = useState('08:00');
  const messagesRef = useRef<any[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunks = useRef<Blob[]>([]);
  const localUserStreamRef = useRef<MediaStream | null>(null);
  const userCameraRef = useRef<HTMLVideoElement>(null);
  const avatarVideoRef = useRef<HTMLVideoElement>(null);
  const isFinalizingRef = useRef(false);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  // ── Parse URL params & JWT ───────────────────────────────────
  useEffect(() => {
    const name = searchParams.get('name') || '';
    const email = searchParams.get('email') || '';
    const scenario = searchParams.get('scenario') || '';
    const urlToken = searchParams.get('token') || '';
    const cookieToken = (() => {
      if (typeof document !== 'undefined') {
        const m = document.cookie.match(/(?:^|; )jwt=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
      }
      return '';
    })();
    const token = urlToken || cookieToken;

    if (name && email && scenario && token) {
      setSessionInfo({ name, email, scenario, token });
      setIsReady(true);
    } else {
      console.error('Faltan parámetros en la URL');
    }
  }, [searchParams]);

  // ── Camera helpers ───────────────────────────────────────────
  const stopUserCameraRecording = useCallback(() => {
    if (localUserStreamRef.current) {
      localUserStreamRef.current.getTracks().forEach((t) => t.stop());
      localUserStreamRef.current = null;
    }
    if (userCameraRef.current) userCameraRef.current.srcObject = null;
    if (mediaRecorderRef.current?.state === 'recording') mediaRecorderRef.current.stop();
  }, []);

  const startUserCameraRecording = useCallback(() => {
    if (!localUserStreamRef.current || mediaRecorderRef.current?.state === 'recording') return;
    try {
      const recorder = new MediaRecorder(localUserStreamRef.current, {
        mimeType: 'video/webm; codecs=vp8',
        videoBitsPerSecond: 2_500_000,
        audioBitsPerSecond: 128_000,
      });
      recordedChunks.current = [];
      recorder.ondataavailable = (e) => e.data.size && recordedChunks.current.push(e.data);
      recorder.start();
      mediaRecorderRef.current = recorder;
    } catch (err) {
      console.error('Error al iniciar MediaRecorder:', err);
    }
  }, []);

  // ── Finalize & Upload ────────────────────────────────────────
  const stopAndFinalizeSession = useCallback(
    async (sessionMessages: any[]) => {
      if (isFinalizingRef.current || !sessionInfo) return;
      isFinalizingRef.current = true;
      setIsUploading(true);

      stopAvatar();

      const finalize = async () => {
        stopUserCameraRecording();

        const { name, email, scenario, token } = sessionInfo;
        const userTranscript = sessionMessages
          .filter((m) => m.sender === MessageSender.CLIENT)
          .map((m) => m.content)
          .join('\n');
        const avatarTranscript = sessionMessages
          .filter((m) => m.sender === MessageSender.AVATAR)
          .map((m) => m.content)
          .join('\n');
        const duration = 480 - recordingTimerRef.current;
        const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL || '';

        try {
          let videoS3Key: string | null = null;
          if (recordedChunks.current.length) {
            const videoBlob = new Blob(recordedChunks.current, { type: 'video/webm' });
            if (videoBlob.size) {
              const form = new FormData();
              form.append('video', videoBlob, 'user_recording.webm');
              const uploadRes = await fetch(`${flaskApiUrl}/upload_video`, {
                method: 'POST',
                headers: { Authorization: `Bearer ${token}` },
                body: form,
              });
              const uploadJson = await uploadRes.json();
              if (!uploadRes.ok) throw new Error(uploadJson.message || 'Error desconocido');
              videoS3Key = uploadJson.s3_object_key;
            }
          }

          await fetch(`${flaskApiUrl}/log_full_session`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              name,
              email,
              scenario,
              conversation: userTranscript,
              avatar_transcript: avatarTranscript,
              duration,
              s3_object_key: videoS3Key,
            }),
          });
        } catch (err: any) {
          console.error('❌ Error finalizando sesión:', err);
          alert(`⚠️ ${err.message}`);
          setIsUploading(false);
        } finally {
          router.push('/dashboard');
        }
      };

      if (mediaRecorderRef.current?.state === 'recording') {
        mediaRecorderRef.current.onstop = finalize;
        mediaRecorderRef.current.stop();
      } else {
        finalize();
      }
    },
    [stopAvatar, stopUserCameraRecording, router, sessionInfo]
  );

  // ── Access token helper ─────────────────────────────────────
  const fetchAccessToken = useCallback(async () => {
    const res = await fetch('/api/get-access-token', { method: 'POST' });
    if (!res.ok) throw new Error(`Fallo al obtener token de acceso: ${res.status}`);
    return res.text();
  }, []);

  // ── Start session with HeyGen ────────────────────────────────
  const startHeyGenSession = useCallback(
    async (withVoice: boolean) => {
      if (!hasUserMediaPermission) {
        alert('Por favor, permite el acceso a la cámara y el micrófono.');
        return;
      }
      setIsAttemptingAutoStart(true);
      try {
        const heygenToken = await fetchAccessToken();
        const avatar = initAvatar(heygenToken);

        // Logs para verificar ciclo Voz→Texto y TTS
        avatar.on(StreamingEvents.USER_TALKING_MESSAGE, (e) => {
          console.log('USER_STT:', e.detail);
          handleUserTalkingMessage({ detail: e.detail });
        });
        avatar.on(StreamingEvents.AVATAR_TALKING_MESSAGE, (e) => {
          console.log('AVATAR_TTS:', e.detail);
          handleStreamingTalkingMessage({ detail: e.detail });
        });

        avatar.on(StreamingEvents.STREAM_DISCONNECTED, () => {
          if (!isFinalizingRef.current) stopAndFinalizeSession(messagesRef.current);
        });

        // Arrancar la VOZ cuando el stream ya está listo (evita carrera de autoplay)
        avatar.on(StreamingEvents.STREAM_READY, async () => {
          setIsAttemptingAutoStart(false);

          // TTS corto de confirmación (opcional pero útil)
          try {
            await (avatar as any)?.speakText?.('Hola, ya te escucho. Cuando quieras, empezamos.');
          } catch {}

          if (withVoice) {
            try {
              await startVoiceChat(); // ← aquí se enciende STT
              console.log('startVoiceChat OK');
            } catch (e) {
              console.error('startVoiceChat falló:', e);
            }
          }
        });

        await startAvatar(config); // levanta el stream del avatar
        // NOTA: no llames startVoiceChat aquí; ya lo hacemos en STREAM_READY
      } catch (err: any) {
        console.error('Error iniciando sesión con HeyGen:', err);
        setShowAutoplayBlockedMessage(true);
      } finally {
        setIsAttemptingAutoStart(false);
      }
    },
    [
      hasUserMediaPermission,
      fetchAccessToken,
      initAvatar,
      config,
      startAvatar,
      startVoiceChat,
      stopAndFinalizeSession,
      handleUserTalkingMessage,
      handleStreamingTalkingMessage,
    ]
  );

  // ── Get user media on mount ─────────────────────────────────
  useEffect(() => {
    const getUserMediaStream = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: true,
          video: { width: 640, height: 480, frameRate: 15 },
        });
        localUserStreamRef.current = stream;
        if (userCameraRef.current) userCameraRef.current.srcObject = stream;
        setHasUserMediaPermission(true);
      } catch (err) {
        console.error('❌ Error al obtener permisos:', err);
        setShowAutoplayBlockedMessage(true);
      }
    };
    if (isReady) getUserMediaStream();
  }, [isReady]);

  // ── Start recording when avatar connects ────────────────────
  useEffect(() => {
    if (
      sessionState === StreamingAvatarSessionState.CONNECTED &&
      hasUserMediaPermission &&
      !mediaRecorderRef.current
    ) {
      startUserCameraRecording();
    }
  }, [sessionState, hasUserMediaPermission, startUserCameraRecording]);

  // ── Bind remote stream to video tag ─────────────────────────
  useEffect(() => {
    if (stream && avatarVideoRef.current) {
      avatarVideoRef.current.srcObject = stream;
      avatarVideoRef.current.onloadedmetadata = () => {
        avatarVideoRef.current!.play().catch(() => {
          setShowAutoplayBlockedMessage(true);
        });
      };
    }
  }, [stream]);

  // ── Timer countdown ─────────────────────────────────────────
  useEffect(() => {
    let id: NodeJS.Timeout | undefined;
    if (sessionState === StreamingAvatarSessionState.CONNECTED) {
      id = setInterval(() => {
        recordingTimerRef.current -= 1;
        const m = Math.floor(recordingTimerRef.current / 60).toString().padStart(2, '0');
        const s = (recordingTimerRef.current % 60).toString().padStart(2, '0');
        setTimerDisplay(`${m}:${s}`);
        if (recordingTimerRef.current <= 0) {
          clearInterval(id);
          stopAndFinalizeSession(messagesRef.current);
        }
      }, 1000);
    }
    return () => clearInterval(id);
  }, [sessionState, stopAndFinalizeSession]);

  // ── Clean up on unmount ─────────────────────────────────────
  useUnmount(() => {
    if (!isFinalizingRef.current) stopAndFinalizeSession(messagesRef.current);
  });

  const handleAutoplayRetry = () => {
    if (hasUserMediaPermission) {
      setShowAutoplayBlockedMessage(false);
      startHeyGenSession(true);
    } else {
      alert('Por favor, permite el acceso a la cámara y el micrófono primero.');
    }
  };

  // ── Loading guard ------------------------------------------------
  if (!isReady) {
    return (
      <div className="w-screen h-screen flex flex-col items-center justify-center bg-zinc-900 text-white">
        <LoadingIcon className="w-10 h-10 animate-spin" />
        <p className="mt-4">Verificando datos de sesión...</p>
      </div>
    );
  }

  // ── Main UI -----------------------------------------------------
  return (
    <div className="w-screen h-screen flex flex-col items-center bg-zinc-900 text-white relative">
      <h1 className="text-3xl font-bold text-blue-400 mt-6 mb-4" suppressHydrationWarning>
        {`🧠 Leo – ${sessionInfo?.scenario || ''}`}
      </h1>

      {sessionState === StreamingAvatarSessionState.INACTIVE && !hasUserMediaPermission && !showAutoplayBlockedMessage && (
        <p className="text-zinc-300 mb-6">Solicitando permisos...</p>
      )}
      {showAutoplayBlockedMessage && (
        <div className="text-red-400 mb-6 text-center">Permisos de cámara/micrófono denegados o no disponibles.</div>
      )}

      {/* Video area */}
      <div className="relative w-full max-w-4xl flex flex-col md:flex-row items-center justify-center gap-5 p-4">
        {/* Avatar video */}
        <div className="relative w-full md:w-1/2 aspect-video min-h-[300px] flex items-center justify-center bg-zinc-800 rounded-lg shadow-lg overflow-hidden">
          {sessionState !== StreamingAvatarSessionState.INACTIVE ? (
            <AvatarVideo ref={avatarVideoRef} />
          ) : (
            !showAutoplayBlockedMessage && <AvatarConfig config={config} onConfigChange={setConfig} />
          )}

          {showAutoplayBlockedMessage && (
            <div className="absolute inset-0 bg-black/75 flex flex-col items-center justify-center text-center p-4 z-30">
              <p className="mb-4 text-lg font-semibold">Video y Audio Bloqueados</p>
              <p className="mb-6">Haz clic para reintentar.</p>
              <Button onClick={handleAutoplayRetry} className="bg-blue-600 hover:bg-blue-700">
                Habilitar
              </Button>
            </div>
          )}

          {sessionState === StreamingAvatarSessionState.CONNECTING && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-20">
              <LoadingIcon className="w-10 h-10 animate-spin" />
              <span className="ml-2">Conectando…</span>
            </div>
          )}

          {sessionState === StreamingAvatarSessionState.CONNECTED && (
            <div className="absolute top-2 left-2 bg-black/70 text-white text-sm px-3 py-1 rounded-full z-10">
              Grabando: {timerDisplay}
            </div>
          )}
        </div>

        {/* User camera */}
        <div className="w-full md:w-1/2">
          <video
            ref={userCameraRef}
            autoPlay
            muted
            playsInline
            className="rounded-lg border border-blue-500 w-full aspect-video object-cover bg-black"
          />
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-col gap-3 items-center justify-center p-4 border-t border-zinc-700 w-full mt-6">
        {sessionState === StreamingAvatarSessionState.INACTIVE && !showAutoplayBlockedMessage && (
          <div className="flex flex-row gap-4">
            <Button onClick={() => startHeyGenSession(true)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
              Iniciar Chat de Voz
            </Button>
            <Button onClick={() => startHeyGenSession(false)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
              Iniciar Chat de Texto
            </Button>
          </div>
        )}
        {sessionState === StreamingAvatarSessionState.CONNECTED && (
          <>
            <AvatarControls />
            <Button onClick={() => stopAndFinalizeSession(messagesRef.current)} className="bg-red-600 hover:bg-red-700">
              Finalizar Sesión
            </Button>
          </>
        )}
      </div>

      {sessionState === StreamingAvatarSessionState.CONNECTED && <MessageHistory />}

      <footer className="mt-auto mb-5 text-sm text-zinc-500 text-center w-full">
        <p>
          Desarrollado por{' '}
          <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
            Teams
          </a>{' '}
          © 2025
        </p>
      </footer>

      {/* Overlay de subida / análisis IA */}
      {isUploading && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 text-white backdrop-blur-sm">
          <LoaderCircle className="h-12 w-12 animate-spin mb-6" />
          <p className="text-lg text-center px-4">Subiendo a servidores para&nbsp;análisis&nbsp;IA…</p>
        </div>
      )}
    </div>
  );
}

export default function InteractiveSessionWrapper() {
  return (
    <StreamingAvatarProvider>
      <InteractiveSessionContent />
    </StreamingAvatarProvider>
  );
}
