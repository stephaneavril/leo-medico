// File: app/interactive-session/page.tsx
'use client';

/*
 ──────────────────────────────────────────────────────────────────────────────
  COMPONENT  : InteractiveSession (Next‑14 / React‑18, TSX)
  OBJETIVO   : Controlar toda la sesión HeyGen + grabación local del usuario  
               y registrar video + transcripciones en tu backend Flask. 
  NOTA       : 100 % autocontenida. Copia‑pega y guarda → `npm run dev`.
 ──────────────────────────────────────────────────────────────────────────────*/

import { useEffect, useRef, useState, useCallback } from 'react';
import { useMemoizedFn, useUnmount } from 'ahooks';
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

/*******************************
 * CONFIG POR DEFECTO DEL BOT  *
*******************************/
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

const isBrowser = typeof window !== 'undefined' && typeof navigator !== 'undefined';

function InteractiveSessionContent() {
  /********************* PARAMS / HOOK PRINCIPAL *********************/
  const router = useRouter();
  const search = useSearchParams();
    // Lee primero los parámetros y, si no llegan, usa las cookies que escribimos en Flask
  const name      = search.get('name')      || Cookies.get('user_name');
  const email     = search.get('email')     || Cookies.get('user_email');
  const scenario  = search.get('scenario')  || Cookies.get('user_scenario');
  const userToken = search.get('token')     || Cookies.get('user_token');

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

  /****************************** STATE ******************************/
  const [config, setConfig] = useState<StartAvatarRequest>(DEFAULT_CONFIG);
  const [showAutoplayBlockedMessage, setShowAutoplayBlockedMessage] = useState(false);
  const [isAttemptingAutoStart, setIsAttemptingAutoStart] = useState(false);
  const [recordingTimer, setRecordingTimer] = useState(480); // 8‑min límite
  const [showDocPanel, setShowDocPanel] = useState(false);
  const [hasUserMediaPermission, setHasUserMediaPermission] = useState(false);

  // NEW: State to track if component has mounted (client-side)
  const [mounted, setMounted] = useState(false);


  /****************************** REFS ******************************/
  const messagesRef = useRef<any[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunks = useRef<Blob[]>([]);
  const localUserStreamRef = useRef<MediaStream | null>(null);
  const userCameraRef = useRef<HTMLVideoElement>(null);
  const avatarVideoRef = useRef<HTMLVideoElement>(null);
  const isFinalizingRef = useRef(false);

  // NEW: Effect to set mounted to true after the first render (on client)
  useEffect(() => {
    setMounted(true);
  }, []);

 const startUserCameraRecording = useCallback(() => {
  if (!localUserStreamRef.current || mediaRecorderRef.current) return;
  try {
    const recorder = new MediaRecorder(localUserStreamRef.current, {
      mimeType: 'video/webm; codecs=vp8',
      // You can try adding videoBitsPerSecond and audioBitsPerSecond here
      // For example:
      // videoBitsPerSecond: 2500000, // 2.5 Mbps
      // audioBitsPerSecond: 128000   // 128 kbps
    });

    recordedChunks.current = [];

    recorder.ondataavailable = (e) => {
      console.log(`🎥 MediaRecorder: Received chunk. Size: ${e.data.size} bytes`); // <--- ADD/UPDATE THIS LINE
      if (e.data.size > 0) {
        recordedChunks.current.push(e.data);
        console.log(`🎥 MediaRecorder: Pushed chunk. Total chunks: ${recordedChunks.current.length}`); // <--- ADD THIS LINE
      } else {
        console.log(`🎥 MediaRecorder: 0-size chunk received. Not pushing.`); // <--- ADD THIS LINE
      }
    };

    recorder.onerror = (event) => { // <--- ADD THIS ERROR HANDLER
        console.error("🎥 MediaRecorder ERROR:", event.error); // Log any specific error details
    };

    recorder.start();
    mediaRecorderRef.current = recorder;
    console.log('🎥 MediaRecorder START');
    console.log(`🎥 MediaRecorder state after start: ${recorder.state}`); // <--- ADD THIS LINE to see state
  } catch (err) {
    console.error('MediaRecorder initialization error:', err); // Clarify initialization error
  }
}, []);

  /************************ FINALIZACIÓN DE SESIÓN ************************/
  const stopAndFinalizeSession = useMemoizedFn(async () => {
    if (isFinalizingRef.current) return;
    isFinalizingRef.current = true;

    const snapshot = [...messagesRef.current]; // antes de limpiar
    stopAvatar();
    stopUserCameraRecording();

    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      await new Promise<void>((res) => {
        mediaRecorderRef.current!.onstop = () => res();
        mediaRecorderRef.current!.stop();
      });
    }

    let videoBlob: Blob | null = null;
    if (recordedChunks.current.length) {
      videoBlob = new Blob(recordedChunks.current, { type: 'video/webm' });
      recordedChunks.current = [];
    }

    const userTranscript = snapshot
      .filter((m) => m.sender === MessageSender.CLIENT)
      .map((m) => m.content)
      .join('\n');
    const avatarTranscript = snapshot
      .filter((m) => m.sender === MessageSender.AVATAR)
      .map((m) => m.content)
      .join('\n');
    const duration = 480 - recordingTimer;

    const api = process.env.NEXT_PUBLIC_FLASK_API_URL;
    let videoKey: string | null = null;

    try {
      if (videoBlob) {
        const fd = new FormData();
        fd.append('video', videoBlob, 'user_recording.webm');
        fd.append('name', name ?? 'unknown');
        fd.append('email', email ?? 'unknown');
        const up = await fetch(`${api}/upload_video`, { method: 'POST', body: fd });
        if (!up.ok) throw new Error(await up.text());
        videoKey = (await up.json()).s3_object_key;
      }

      const res = await fetch(`${api}/log_full_session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          email,
          scenario,
          conversation: userTranscript,
          avatar_transcript: avatarTranscript,
          duration,
          video_object_key: videoKey,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      console.log('✅ Sesión registrada');
    } catch (err) {
      console.error('❌ Error registrando sesión', err);
      alert('Error registrando sesión. Revisa la consola');
    } finally {
      router.push('/dashboard');
    }
  });

  /************************ PERMISOS MEDIA ************************/
 useEffect(() => {
  let isMounted = true;

  async function enableMedia() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true,
      });

      if (!isMounted) return;               
      localUserStreamRef.current = stream;

      if (userCameraRef.current) {
        userCameraRef.current.srcObject = stream;
      }

      setHasUserMediaPermission(true);
    } catch (err) {
      console.error('getUserMedia error', err);
    }
  }

  enableMedia();

  return () => {           
    isMounted = false;
    localUserStreamRef.current?.getTracks().forEach(t => t.stop());
  };
}, []);

  /************ INICIAR GRABACIÓN CUANDO EL AVATAR CONECTA ***********/
  useEffect(() => {
    if (
      sessionState === StreamingAvatarSessionState.CONNECTED &&
      hasUserMediaPermission &&
      !mediaRecorderRef.current
    ) {
      startUserCameraRecording();
    }
  }, [sessionState, hasUserMediaPermission, startUserCameraRecording]);

  /************************ TOKEN HEYGEN ************************/
  const fetchAccessToken = useCallback(async () => {
    const res = await fetch('/api/get-access-token', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    return res.text();
  }, []);

  /************************ START HEYGEN ************************/
  const startHeyGenSession = useMemoizedFn(async (startWithVoice: boolean) => {
    if (!hasUserMediaPermission) {
      alert('Concede permisos de cámara y micrófono');
      return;
    }
    setIsAttemptingAutoStart(true);
    try {
      const token = await fetchAccessToken();
      const avatar = initAvatar(await token);

      /* Eventos clave */
      avatar.on(StreamingEvents.STREAM_DISCONNECTED, () => {
        if (!isFinalizingRef.current) stopAndFinalizeSession();
      });
      avatar.on(StreamingEvents.USER_TALKING_MESSAGE, (e: any) => {
        handleUserTalkingMessage({ detail: { message: e.message || e.detail?.message || '' } });
      });
      avatar.on(StreamingEvents.USER_END_MESSAGE, (e: any) => {
        handleUserTalkingMessage({ detail: { message: e.message || e.detail?.message || '' } });
      });
      avatar.on(StreamingEvents.AVATAR_TALKING_MESSAGE, (e: any) => {
        handleStreamingTalkingMessage({ detail: { message: e.message || e.detail?.message || '' } });
      });
      avatar.on(StreamingEvents.AVATAR_END_MESSAGE, (e: any) => {
        handleStreamingTalkingMessage({ detail: { message: e.message || e.detail?.message || '' } });
      });

      /* Arranque de video y opcionalmente voz */
      await startAvatar(config);
      if (startWithVoice) await startVoiceChat();
    } catch (err) {
      console.error('startHeyGenSession error', err);
      setShowAutoplayBlockedMessage(true);
      stopAvatar();
      stopUserCameraRecording();
    } finally {
      setIsAttemptingAutoStart(false);
    }
  });

  /*********************** LIMPIEZA AL DESMONTAR ***********************/
  useUnmount(() => {
    if (!isFinalizingRef.current && sessionState === StreamingAvatarSessionState.CONNECTED) {
      stopAndFinalizeSession();
    } else if (!isFinalizingRef.current) {
      stopUserCameraRecording();
      stopAvatar();
    }
  });

  /************************ TIMER GLOBAL 8 MIN ************************/
  useEffect(() => {
    let id: NodeJS.Timeout;
    if (sessionState === StreamingAvatarSessionState.CONNECTED) {
      id = setInterval(() => {
        setRecordingTimer((prev) => {
          if (prev <= 1) {
            clearInterval(id);
            stopAndFinalizeSession();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => clearInterval(id);
  }, [sessionState, stopAndFinalizeSession]);

  /************** AUTO‑REPLAY SI EL VIDEO PAUSA (CHROME) **************/
  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && stream && avatarVideoRef.current) {
      const videoEl = avatarVideoRef.current;
      videoEl.srcObject = stream;
      videoEl.onloadedmetadata = () => videoEl.play().catch(() => {});

      const id = setInterval(() => {
        if (videoEl.paused || videoEl.readyState < 3) {
          videoEl.play().catch(() => {});
        }
      }, 1000);
      return () => clearInterval(id);
    }
  }, [sessionState, stream]);

  /***************************** HELPERS UI *****************************/
  const formatTime = (s: number) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  const handleAutoplayRetry = useMemoizedFn(() => startHeyGenSession(true));
  const toggleDocPanel = () => setShowDocPanel((v) => !v);

  /***************************** RENDER *****************************/
  // Determine the scenario text for consistent rendering.
  // We use `mounted` to render the dynamic part only after hydration on the client.
  const scenarioText = mounted ? (scenario || "N/A") : "Cargando..."; 

  return (
    // Main application container - its classNames should match the conditional render above
    <div className="w-screen h-screen flex flex-col items-center bg-zinc-900 text-white relative">
        {/* The h1 will now always render a consistent string on the server. */}
        {/* suppressHydrationWarning tells React to ignore differences in this element after initial render. */}
        <h1 className="text-3xl font-bold text-blue-400 mt-6 mb-4" suppressHydrationWarning>
            🧠 Leo – {scenarioText}
        </h1>

        {/* Conditional content based on whether user data is available after mounting */}
        {(!mounted || !name || !email || !scenario || !userToken) ? (
            <p className="text-zinc-300">Verificando información de usuario y redirigiendo si es necesario.</p>
        ) : (
            <>
                {/* Mensaje de estado inicial de permisos */}
                {sessionState === StreamingAvatarSessionState.INACTIVE && !hasUserMediaPermission && !showAutoplayBlockedMessage && (
                  <p className="text-zinc-300 mb-6">Solicitando permisos para cámara y micrófono…</p>
                )}
                {showAutoplayBlockedMessage && (
                  <p className="text-red-400 mb-6">Permisos denegados o autoplay bloqueado.</p>
                )}

                {/* CONTENEDOR PRINCIPAL */}
                <div className="relative w-full max-w-4xl flex flex-col md:flex-row gap-5 p-4">
                  {/* VIDEO AVATAR */}
                  <div className="relative w-full md:w-1/2 aspect-video min-h-[300px] bg-zinc-800 rounded-lg overflow-hidden flex items-center justify-center">
                    {sessionState !== StreamingAvatarSessionState.INACTIVE ? (
                      <AvatarVideo ref={avatarVideoRef} />
                    ) : !showAutoplayBlockedMessage && (
                      <AvatarConfig config={config} onConfigChange={setConfig} />
                    )}

                    {showAutoplayBlockedMessage && (
                      <div className="absolute inset-0 bg-black bg-opacity-75 flex flex-col items-center justify-center text-center p-4">
                        <p className="mb-4 text-lg font-semibold">¡Video/audio bloqueados!</p>
                        <Button onClick={handleAutoplayRetry} className="bg-blue-600 hover:bg-blue-700">
                          Habilitar
                        </Button>
                      </div>
                    )}

                    {sessionState === StreamingAvatarSessionState.CONNECTED && (
                      <div className="absolute top-2 left-2 bg-black bg-opacity-70 px-3 py-1 rounded text-sm">
                        Grabando: {formatTime(recordingTimer)}
                      </div>
                    )}
                    {sessionState === StreamingAvatarSessionState.CONNECTING && !showAutoplayBlockedMessage && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-50">
                        <LoadingIcon className="w-10 h-10 animate-spin" />
                      </div>
                    )}
                  </div>

                  {/* VIDEO USUARIO */}
                  <div className="w-full md:w-1/2">
                    <video ref={userCameraRef} autoPlay muted playsInline className="rounded-lg border border-blue-500 w-full aspect-video object-cover bg-black" />
                  </div>
                </div>

                {/* CONTROLES */}
                <div className="flex flex-col items-center gap-4 mt-6 border-t border-zinc-700 w-full p-4">
                  {sessionState === StreamingAvatarSessionState.INACTIVE && !showAutoplayBlockedMessage && (
                    <div className="flex gap-4">
                      <Button onClick={() => startHeyGenSession(true)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
                        Iniciar voz
                      </Button>
                      <Button onClick={() => startHeyGenSession(false)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
                        Iniciar texto
                      </Button>
                    </div>
                  )}

                  {sessionState === StreamingAvatarSessionState.CONNECTING && !showAutoplayBlockedMessage && (
                    <div className="flex items-center gap-2 text-white">
                      <LoadingIcon className="w-6 h-6 animate-spin" />
                      <span>Conectando…</span>
                    </div>
                  )}

                  {sessionState === StreamingAvatarSessionState.CONNECTED && (
                    <>
                      <AvatarControls />
                      <Button onClick={stopAndFinalizeSession} className="bg-red-600 hover:bg-red-700">
                        Finalizar
                      </Button>
                    </>
                  )}
                </div>

                {sessionState === StreamingAvatarSessionState.CONNECTED && <MessageHistory />}

                {/* PANEL DOC */}
                <button onClick={toggleDocPanel} className="fixed top-5 left-1/2 -translate-x-1/2 bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded-lg shadow-lg">
                  📘 Ver documentación
                </button>
                <div className={`fixed top-0 right-0 w-80 h-full bg-zinc-950 border-l-2 border-blue-600 p-5 overflow-y-auto transition-transform duration-300 ${showDocPanel ? 'translate-x-0' : 'translate-x-full'}`}>
                  <h2 className="text-xl font-bold text-blue-400 mb-4">Documentación útil</h2>
                  <p className="text-sm text-zinc-300 mb-2">☑ Saludo ☑ Necesidad ☑ Propuesta ☑ Cierre</p>
                  <p className="text-sm text-zinc-300 mb-2">Objeciones: “Ya uso otro producto” → ¿Qué resultados ha observado?</p>
                  <p className="text-sm text-zinc-300 mb-2">Ética: permitido compartir evidencia válida. Prohibido usos off‑label.</p>
                </div>
            </>
        )} {/* End of conditional rendering based on user data */}

        <footer className="mt-auto mb-5 text-xs text-zinc-500 text-center w-full">
          Desarrollado por <a href="https://www.teams.com.mx" className="text-blue-400 hover:underline">Teams</a> © 2025
        </footer>
    </div>
  );
}

/**************************** WRAPPER PROVIDER ****************************/
export default function InteractiveSessionWrapper() {
  return (
    <StreamingAvatarProvider basePath={process.env.NEXT_PUBLIC_BASE_API_URL || ''}>
      <InteractiveSessionContent />
    </StreamingAvatarProvider>
  );
}