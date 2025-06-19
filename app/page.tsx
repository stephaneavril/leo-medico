// File: stephaneavril/leo_api/LEO_API-b913b081323a85b5938124f7a062b68789831888/app/interactive-session/page.tsx
'use client';

import { useEffect, useRef, useState, useCallback } from "react";
import { useMemoizedFn, useUnmount } from "ahooks";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AvatarQuality,
  StreamingEvents,
  VoiceChatTransport,
  VoiceEmotion,
  StartAvatarRequest,
  STTProvider,
  ElevenLabsModel,
} from "@heygen/streaming-avatar";
import { StreamingAvatarProvider, StreamingAvatarSessionState, useStreamingAvatarSession, useVoiceChat, MessageSender, useMessageHistory } from "@/components/logic";
import { Button } from "@/components/Button";
import { AvatarConfig } from "@/components/AvatarConfig";
import { AvatarVideo } from "@/components/AvatarSession/AvatarVideo";
import { AvatarControls } from "@/components/AvatarSession/AvatarControls";
import { LoadingIcon } from "@/components/Icons";
import { MessageHistory } from "@/components/AvatarSession/MessageHistory";

// Define DEFAULT_CONFIG outside the component to prevent re-creation on re-renders
const DEFAULT_CONFIG: StartAvatarRequest = {
  quality: AvatarQuality.Low,
  avatarName: "Ann_Doctor_Standing2_public",
  knowledgeId: "13f254b102cf436d8c07b9fb617dbadf", // Asegúrate de que este ID sea válido o configúralo
  voice: {
    rate: 1.5,
    emotion: VoiceEmotion.EXCITED,
    model: ElevenLabsModel.eleven_flash_v2_5,
  },
  language: "es", // Idioma por defecto en español
  voiceChatTransport: VoiceChatTransport.WEBSOCKET,
  sttSettings: {
    provider: STTProvider.DEEPGRAM,
  },
};

// Helper for browser check
const isBrowser = typeof window !== "undefined" && typeof navigator !== "undefined";

function InteractiveSessionContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const name = searchParams.get('name');
  const email = searchParams.get('email');
  const scenario = searchParams.get('scenario');
  const userToken = searchParams.get('token'); // This is your Flask user token, not HeyGen's

  // HeyGen Streaming Avatar Session Hooks
  const { initAvatar, startAvatar, stopAvatar, sessionState, stream, messages, handleUserTalkingMessage, handleStreamingTalkingMessage, handleEndMessage } = useStreamingAvatarSession(); // <<-- messages y handlers del context
  const { startVoiceChat, isVoiceChatActive } = useVoiceChat();

  // Component State
  const [config, setConfig] = useState<StartAvatarRequest>(DEFAULT_CONFIG);
  const [showAutoplayBlockedMessage, setShowAutoplayBlockedMessage] = useState(false);
  const [isAttemptingAutoStart, setIsAttemptingAutoStart] = useState(false);
  const [recordingTimer, setRecordingTimer] = useState<number>(480); // 480s = 8min session as per chat.html
  const [showDocPanel, setShowDocPanel] = useState(false);


  // Video Recording State
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunks = useRef<Blob[]>([]); // Se mantiene para recolectar chunks

  // Refs for Video Elements
  const userCameraRef = useRef<HTMLVideoElement>(null);
  const mediaStreamRef = useRef<HTMLVideoElement>(null); // Ref for HeyGen avatar video

  // --- VARIABLES DE ESTADO/REF PARA CONTROL DE FINALIZACIÓN Y LIMPIEZA ---
  const isFinalizingRef = useRef(false); // Para asegurar que stopAndFinalizeSession no se llame varias veces
  const localUserStreamRef = useRef<MediaStream | null>(null); // Referencia al stream de la cámara del usuario
  const [hasUserMediaPermission, setHasUserMediaPermission] = useState(false); // Nuevo: para habilitar botones de inicio

  // Ref para `messages` (anteriormente `messageHistory`) - para acceder al valor más reciente
  const messagesRef = useRef(messages);
  useEffect(() => {
    messagesRef.current = messages; // Mantiene la ref actualizada con el último `messages` del context
  }, [messages]);
  // --- FIN NUEVAS VARIABLES ---

  // Function to stop local recording (user camera)
  const stopUserCameraRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop(); // Detener el grabador
      mediaRecorderRef.current = null;
      console.log("🎥 MediaRecorder for user stopped.");
    }
    if (localUserStreamRef.current) {
        localUserStreamRef.current.getTracks().forEach(track => track.stop());
        localUserStreamRef.current = null;
        console.log("🎥 User camera stream tracks stopped.");
    }
    if (userCameraRef.current) {
        userCameraRef.current.srcObject = null;
    }
  }, []);

  // Function to start user camera recording (called once HeyGen session is ready)
  const startUserCameraRecording = useCallback(() => {
    if (localUserStreamRef.current && !mediaRecorderRef.current) {
      const streamToRecord = localUserStreamRef.current;
      if (streamToRecord.getVideoTracks().length === 0 && streamToRecord.getAudioTracks().length === 0) {
        console.warn("No video or audio tracks available in user stream for recording.");
        return;
      }
      try {
        const recorder = new MediaRecorder(streamToRecord, { mimeType: 'video/webm; codecs=vp8' });
        recordedChunks.current = []; // Siempre vaciar al inicio de una nueva grabación
        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) {
            recordedChunks.current.push(event.data);
            console.log(`🎥 MediaRecorder: ondataavailable - chunk size: ${event.data.size}`);
          }
        };
        recorder.onerror = (event) => {
            console.error("MediaRecorder error:", event);
        };
        recorder.start(1000); // Grabar chunks cada segundo para evitar problemas con buffers pequeños
        mediaRecorderRef.current = recorder;
        console.log("🎥 Grabación iniciada del usuario (MediaRecorder).");
        console.log(`🎥 MediaRecorder state after start: ${recorder.state}`);
      } catch (error) {
          console.error("Failed to start MediaRecorder:", error);
      }
    } else {
      console.warn("Cannot start recording: User camera stream not available or recorder already exists.");
    }
  }, []);

  const stopAndFinalizeSession = useMemoizedFn(async () => {
    if (isFinalizingRef.current) {
      console.log("🛑 Finalización ya en progreso o ya completada. Abortando llamada redundante.");
      return;
    }
    isFinalizingRef.current = true;

    console.log("🛑 Deteniendo grabación y sesión...");

    stopAvatar(); // Stop HeyGen avatar session
    stopUserCameraRecording(); // Stop user camera and local recorder (already handled in stopUserCameraRecording)

    // Asegurarse de que el MediaRecorder se detenga y genere el blob ANTES de continuar
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        await new Promise<void>(resolve => {
            mediaRecorderRef.current!.onstop = () => {
                console.log("MediaRecorder was forced stopped, onstop callback fired.");
                resolve();
            };
            mediaRecorderRef.current!.stop();
        });
    }

    let videoBlob: Blob | null = null;
    if (recordedChunks.current.length > 0) {
        videoBlob = new Blob(recordedChunks.current, { type: "video/webm" });
        console.log(`✅ Video Blob created. Size: ${videoBlob.size} bytes`);
        recordedChunks.current = []; // Limpiar chunks después de usar
    } else {
        console.warn("No recorded video chunks available to finalize. Video Blob will be null.");
    }

    // --- USANDO messagesRef.current PARA ACCEDER AL VALOR MÁS RECIENTE DE LA TRANSCRIPCIÓN ---
    // Asegurarse de que messagesRef.current sea un array. El `?? []` ayuda si es null/undefined.
    const currentMessages = messagesRef.current || [];
    const userTranscript = currentMessages.filter(msg => msg.sender === MessageSender.CLIENT).map(msg => msg.content).join('\n');
    const avatarTranscript = currentMessages.filter(msg => msg.sender === MessageSender.AVATAR).map(msg => msg.content).join('\n');
    const duration = 480 - recordingTimer;

    console.log(`📊 Transcripción del Usuario (longitud: ${userTranscript.length}): '${userTranscript.substring(0, Math.min(userTranscript.length, 100))}'`);
    console.log(`📊 Transcripción del Avatar (longitud: ${avatarTranscript.length}): '${avatarTranscript.substring(0, Math.min(avatarTranscript.length, 100))}'`);
    // --- FIN LOG ---

    const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL;

    const processingDiv = document.createElement("div");
    processingDiv.id = "ia-waiting-overlay";
    processingDiv.style.cssText = `
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background-color: rgba(0,0,0,0.9); display: flex; flex-direction: column;
      align-items: center; justify-content: center; z-index: 10000; color: white;
      text-align: center;
    `;
    document.body.appendChild(processingDiv);

    const iaBar = document.getElementById("ia-bar");
    const iaText = document.getElementById("ia-progress-text");
    let currentProgress = 0;

    const updateSimulatedProgress = (stepName: string, targetPercentage: number) => {
      return new Promise<void>(resolve => {
        const stepIncrement = (targetPercentage - currentProgress) / 10;
        const intervalDuration = 200;
        const simInterval = setInterval(() => {
          currentProgress += stepIncrement;
          if (currentProgress >= targetPercentage) {
            currentProgress = targetPercentage;
            clearInterval(simInterval);
          }
          if (iaBar) iaBar.style.width = `${Math.round(currentProgress)}%`;
          if (iaText) iaText.textContent = `${stepName} ${Math.round(currentProgress)}%`;
          if (currentProgress === targetPercentage) resolve();
        }, intervalDuration);
      });
    };

    let videoS3Key: string | null = null;
    try {
      if (videoBlob) {
        await updateSimulatedProgress("Subiendo video...", 30);
        const videoFormData = new FormData();
        videoFormData.append('video', videoBlob, "user_recording.webm");
        videoFormData.append('name', name || 'unknown');
        videoFormData.append('email', email || 'unknown');

        console.log("Attempting to upload recording to Flask /upload_video...");
        const uploadRes = await fetch(`${flaskApiUrl}/upload_video`, {
          method: "POST",
          body: videoFormData,
        });

        if (uploadRes.ok) {
          const uploadData = await uploadRes.json();
          videoS3Key = uploadData.s3_object_key;
          console.log("✅ Flask /upload_video success. S3 Key returned:", videoS3Key);
        } else {
          const errorText = await uploadRes.text();
          console.error("❌ Error al subir grabación a Flask /upload_video:", uploadRes.status, errorText);
          alert("⚠️ Problema al subir el video. Consulta la consola para más detalles.");
          // Si falla la subida de video, es mejor no intentar loguear una sesión sin video.
          isFinalizingRef.current = false; // Reset para permitir reintento
          return;
        }
      } else {
        console.warn("No video blob to upload, skipping /upload_video call.");
        await updateSimulatedProgress("Saltando subida de video (sin video)...", 30);
      }

      await updateSimulatedProgress("Enviando registro de sesión para análisis...", 60);

      console.log("Attempting to send session log to Flask /log_full_session...");
      const sessionLogRes = await fetch(`${flaskApiUrl}/log_full_session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          email: email,
          scenario: scenario,
          conversation: userTranscript, // Enviar transcripción real del usuario
          avatar_transcript: avatarTranscript, // Enviar transcripción real del avatar
          duration: duration,
          video_object_key: videoS3Key // Pass the S3 key obtained from /upload_video
        })
      });

      if (sessionLogRes.ok) {
        const sessionLogData = await sessionLogRes.json();
        console.log("✅ Flask /log_full_session success. Response:", sessionLogData);
        await updateSimulatedProgress("Análisis en curso...", 90);
      } else {
        const errorText = await sessionLogRes.text();
        console.error("❌ Error al registrar sesión a Flask /log_full_session:", sessionLogRes.status, errorText);
        alert("⚠️ Error al registrar la sesión para análisis. Consulta la consola para más detalles.");
      }

    } catch (err) {
      console.error("❌ Error general en la solicitud de subida o registro:", err);
      alert("❌ Error de red durante el proceso de finalización de la sesión.");
    } finally {
      await updateSimulatedProgress("Redirigiendo al Dashboard...", 100);
      document.getElementById("ia-waiting-overlay")?.remove();
      router.push('/dashboard');
    }
  });


  // --- EFECTO PARA ACCEDER A LA CÁMARA DEL USUARIO Y CONFIGURAR EL PREVIEW ---
  useEffect(() => {
    if (!isBrowser || !navigator.mediaDevices?.getUserMedia) {
      console.warn("Browser does not support getUserMedia or is not a browser environment.");
      return;
    }

    const getUserMediaStream = async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: true,
            video: {
                width: { ideal: 640, max: 640 },
                height: { ideal: 480, max: 480 },
                frameRate: { ideal: 15, max: 15 }
            }
        });
        localUserStreamRef.current = stream; // Guardar el stream en la referencia
        if (userCameraRef.current) {
          userCameraRef.current.srcObject = stream;
        }
        setHasUserMediaPermission(true); // Habilitar los botones de inicio
        setShowAutoplayBlockedMessage(false); // Limpiar cualquier mensaje de bloqueo previo
        console.log("🎥 User camera preview stream acquired and permissions granted.");
      } catch (error: any) {
        console.error("❌ No se pudo acceder a la cámara del usuario para la vista previa o grabación:", error);
        setHasUserMediaPermission(false); // Deshabilitar botones
        setShowAutoplayBlockedMessage(true); // Mostrar mensaje de bloqueo
      }
    };

    getUserMediaStream();

    return () => {
        if (!isFinalizingRef.current) { // Solo limpiar si no estamos ya en el proceso de finalización
            console.log("useEffect cleanup: Deteniendo medios locales (no finalizando).");
            stopUserCameraRecording();
        }
    };
  }, [stopUserCameraRecording, isFinalizingRef]); // Se ejecuta solo una vez al montar

  // --- EFECTO PARA INICIAR LA GRABACIÓN DEL USUARIO CUANDO LA SESIÓN DE HEYGEN ESTÁ CONECTADA ---
  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && hasUserMediaPermission && !mediaRecorderRef.current) {
        console.log("HeyGen Session CONNECTED. Attempting to start user recording.");
        startUserCameraRecording();
    }
  }, [sessionState, hasUserMediaPermission, mediaRecorderRef, startUserCameraRecording]);

  // Function to fetch HeyGen access token
  const fetchAccessToken = useCallback(async () => {
    try {
      console.log("Fetching access token...");
      const response = await fetch("/api/get-access-token", {
        method: "POST",
      });
      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Failed to fetch access token: ${response.status} ${errorText}`);
      }
      const token = await response.text();
      console.log("Access Token received.");
      return token;
    } catch (error) {
      console.error("Error fetching access token:", error);
      throw error;
    }
  }, []);

  // Memoized function to start the HeyGen avatar session (user-triggered)
  const startHeyGenSession = useMemoizedFn(async (startWithVoice: boolean) => {
    console.log(`startHeyGenSession called. startWithVoice: ${startWithVoice}. Current sessionState: ${sessionState}`);
    setIsAttemptingAutoStart(true);
    setShowAutoplayBlockedMessage(false);

    if (!hasUserMediaPermission) {
        alert("Por favor, permite el acceso a la cámara y el micrófono antes de iniciar la sesión.");
        setIsAttemptingAutoStart(false);
        return;
    }

    try {
      const heygenToken = await fetchAccessToken();
      const avatar = initAvatar(heygenToken);
      console.log("Avatar initialized with HeyGen token.");

      avatar.on(StreamingEvents.AVATAR_START_TALKING, () => console.log("Avatar started talking"));
      avatar.on(StreamingEvents.AVATAR_STOP_TALKING, () => console.log("Avatar stopped talking"));
      avatar.on(StreamingEvents.STREAM_DISCONNECTED, () => {
        console.log("HeyGen Stream disconnected.");
        if (!isFinalizingRef.current) {
            console.log("Stream desconectado inesperadamente. Disparando finalización.");
            stopAndFinalizeSession();
        }
      });
      avatar.on(StreamingEvents.STREAM_READY, (event) => {
        console.log(">>>>> HeyGen Stream ready:", event.detail);
        setShowAutoplayBlockedMessage(false);
        setIsAttemptingAutoStart(false);
      });
      avatar.on(StreamingEvents.USER_START, (event) => console.log(">>>>> User started talking:", event));
      avatar.on(StreamingEvents.USER_STOP, () => console.log(">>>>> User stopped talking."));
      avatar.on(StreamingEvents.USER_END_MESSAGE, (event) => {
        // --- LOG DE DIAGNÓSTICO ---
        console.log("HeyGen: USER_END_MESSAGE event received. Current messagesRef.current (User):", (messagesRef.current || []).filter(msg => msg.sender === MessageSender.CLIENT).map(msg => msg.content));
        // --- FIN LOG ---
      });
      avatar.on(StreamingEvents.USER_TALKING_MESSAGE, (event) => {
        // --- LOG DE DIAGNÓSTICO ---
        console.log("HeyGen: USER_TALKING_MESSAGE event received. Message:", event.message, typeof event.message);
        // --- FIN LOG ---
      });
      avatar.on(StreamingEvents.AVATAR_TALKING_MESSAGE, (event) => {
        // --- LOG DE DIAGNÓSTICO ---
        console.log("HeyGen: AVATAR_TALKING_MESSAGE event received. Message:", event.message, typeof event.message);
        // --- FIN LOG ---
      });
      avatar.on(StreamingEvents.AVATAR_END_MESSAGE, (event) => {
        // --- LOG DE DIAGNÓSTICO ---
        console.log("HeyGen: AVATAR_END_MESSAGE event received. Current messagesRef.current (Avatar):", (messagesRef.current || []).filter(msg => msg.sender === MessageSender.AVATAR).map(msg => msg.content));
        // --- FIN LOG ---
      });
      avatar.on(
        StreamingEvents.CONNECTION_QUALITY_CHANGED,
        ({ detail }) => {
          console.log("Connection quality changed:", detail);
        }
      );

      console.log("Attempting to start Avatar video with config:", config);
      await startAvatar(config);

      if (startWithVoice) {
        console.log("Attempting to start voice chat (after avatar video started)...");
        await startVoiceChat();
        console.log("Voice chat start call completed.");
      }

    } catch (error: any) {
      console.error("Error starting HeyGen avatar session:", error);
      if (error instanceof DOMException && error.name === 'NotAllowedError') {
        console.log("Detected NotAllowedError (Autoplay/Permissions blocked, e.g., video or mic).");
        setShowAutoplayBlockedMessage(true);
      } else if (error.message && error.message.includes("Microphone access denied")) {
        console.log("Microphone access specifically denied. Showing autoplay blocked message.");
        setShowAutoplayBlockedMessage(true);
      } else {
        console.error("General error during session start:", error);
      }
      stopAvatar();
      stopUserCameraRecording();
    } finally {
      setIsAttemptingAutoStart(false);
    }
  });

 useUnmount(() => {
  console.log("Component unmounting. Ensuring all streams/recorders are stopped.");
  if (!isFinalizingRef.current && sessionState === StreamingAvatarSessionState.CONNECTED) {
      console.log("useUnmount: Sesión CONECTADA y no finalizada explícitamente. Disparando FINALIZACIÓN GRACIAS A UNMOUNT.");
      stopAndFinalizeSession();
  } else if (!isFinalizingRef.current) {
      console.log("useUnmount: Sesión NO CONECTADA o ya finalizando. Solo deteniendo medios locales y avatar.");
      stopUserCameraRecording();
      stopAvatar();
  } else {
      console.log("useUnmount: Finalización ya en curso, el desmontaje es parte del proceso.");
  }
});

  // Effect to handle HeyGen avatar stream video playback (Autoplay handling)
  useEffect(() => {
    if (stream && mediaStreamRef.current) {
      mediaStreamRef.current.srcObject = stream;
      mediaStreamRef.current.onloadedmetadata = () => {
        mediaStreamRef.current!.play()
          .then(() => {
            console.log("Stream Effect: HeyGen Video played successfully.");
            setShowAutoplayBlockedMessage(false);
          })
          .catch((error) => {
            console.warn("Stream Effect: Autoplay bloqueado (video playback failed):", error);
            setShowAutoplayBlockedMessage(true);
            stopAvatar();
          });
      };
    }
  }, [mediaStreamRef, stream, stopAvatar]);

  // Effect to re-attempt playback if avatar video is paused/stuck after connection
  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && stream && mediaStreamRef.current) {
      const videoElement = mediaStreamRef.current;
      const checkAndPlay = setTimeout(() => {
        if (videoElement.paused || videoElement.ended || videoElement.readyState < 3) {
          console.log("El video del avatar no se está reproduciendo, intentando reproducir de nuevo...");
          videoElement.play().catch(e => console.error("Error al reproducir el video de nuevo:", e));
        }
      }, 1000);
      return () => clearTimeout(checkAndPlay);
    }
  }, [sessionState, stream]);

  // Effect for recording timer
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (sessionState === StreamingAvatarSessionState.CONNECTED) {
      interval = setInterval(() => {
        setRecordingTimer((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            console.log("⏰ Tiempo agotado. Deteniendo y finalizando sesión.");
            stopAndFinalizeSession();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }

    return () => {
      if (interval) clearInterval(interval);
    };
  }, [sessionState, stopAndFinalizeSession]);

  // Function for the user to retry the session start or activate voice if autoplay was blocked
  const handleAutoplayRetry = useMemoizedFn(async () => {
    console.log("handleAutoplayRetry triggered by user click.");
    setShowAutoplayBlockedMessage(false);

    if (hasUserMediaPermission) {
        await startHeyGenSession(true);
    } else {
        alert("Por favor, permite el acceso a la cámara y el micrófono cuando se te solicite para habilitar la sesión.");
    }
  });

  // Format timer for display
  const formatTime = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
  };

  const toggleDocPanel = () => {
    setShowDocPanel(prev => !prev);
  };

  if (!name || !email || !scenario || !userToken) {
    return <div className="min-h-screen flex items-center justify-center bg-zinc-900 text-white">Error: Faltan datos de usuario. Redirigiendo...</div>;
  }

  return (
    <div className="w-screen h-screen flex flex-col items-center bg-zinc-900 text-white relative">
      <h1 className="text-3xl font-bold text-blue-400 mt-6 mb-4">🧠 Leo - {scenario}</h1>
      {/* Mensaje de estado inicial de permisos */}
      {sessionState === StreamingAvatarSessionState.INACTIVE && !hasUserMediaPermission && !showAutoplayBlockedMessage && (
        <p id="status" className="text-zinc-300 mb-6">Solicitando permisos para cámara y micrófono...</p>
      )}
      {showAutoplayBlockedMessage && (
          <p id="status" className="text-red-400 mb-6">Error: Permisos de cámara/micrófono denegados o no disponibles.</p>
      )}


      <div className="relative w-full max-w-4xl h-auto flex flex-col md:flex-row items-center justify-center gap-5 p-4">
        {/* Avatar de HeyGen */}
        <div className="relative w-full md:w-1/2 aspect-video min-h-[300px] flex items-center justify-center bg-zinc-800 rounded-lg shadow-lg overflow-hidden">
          {sessionState !== StreamingAvatarSessionState.INACTIVE ? (
            <AvatarVideo ref={mediaStreamRef} />
          ) : (
            // Mostrar AvatarConfig solo si no hay mensaje de bloqueo de autoplay y está inactivo
            !showAutoplayBlockedMessage && (
                sessionState === StreamingAvatarSessionState.INACTIVE && (
                    <AvatarConfig config={config} onConfigChange={setConfig} />
                )
            )
          )}

          {showAutoplayBlockedMessage && (
            <div className="absolute inset-0 bg-black bg-opacity-75 flex flex-col items-center justify-center text-white text-center p-4 rounded-lg z-30">
              <p className="mb-4 text-lg font-semibold">
                ¡El video y el audio están bloqueados!
              </p>
              <p className="mb-6">
                Tu navegador bloqueó la reproducción automática o el acceso al micrófono.
                Haz clic en "Habilitar Video y Audio" y asegúrate de dar permiso.
              </p>
              <Button onClick={handleAutoplayRetry} className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded">
                Habilitar Video y Audio
              </Button>
            </div>
          )}
          {sessionState === StreamingAvatarSessionState.CONNECTING && !showAutoplayBlockedMessage && (
            <div className="absolute inset-0 flex items-center justify-center bg-black bg-opacity-50 text-white rounded-lg z-20">
                <LoadingIcon className="w-10 h-10 animate-spin" />
                <span className="ml-2 text-lg">Cargando Avatar...</span>
            </div>
          )}
          {sessionState === StreamingAvatarSessionState.CONNECTED && (
                <div className="absolute top-2 left-2 bg-black bg-opacity-70 text-white text-sm px-3 py-1 rounded-full z-10">
                    Grabando: {formatTime(recordingTimer)}
                </div>
            )}
        </div>

        {/* Cámara del usuario */}
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

      {/* Controles de la sesión */}
      <div className="flex flex-col gap-3 items-center justify-center p-4 border-t border-zinc-700 w-full mt-6">
        {sessionState === StreamingAvatarSessionState.INACTIVE && !showAutoplayBlockedMessage && (
          // Mostrar botones de INICIO si está inactivo, sin bloqueo y con permisos
          <div className="flex flex-row gap-4">
            <Button onClick={() => startHeyGenSession(true)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
              Iniciar Chat de Voz
            </Button>
            <Button onClick={() => startHeyGenSession(false)} disabled={isAttemptingAutoStart || !hasUserMediaPermission}>
              Iniciar Chat de Texto
            </Button>
          </div>
        )}

        {sessionState === StreamingAvatarSessionState.CONNECTING && !showAutoplayBlockedMessage && (
          // Mostrar mensaje de CONECTANDO
          <div className="flex items-center space-x-2 text-white">
            <LoadingIcon className="w-6 h-6 animate-spin" />
            <span>Conectando...</span>
          </div>
        )}

        {sessionState === StreamingAvatarSessionState.CONNECTED && (
          // Mostrar AvatarControls y el botón de Finalizar Sesión cuando está CONECTADO
          <>
            <AvatarControls />
            <Button onClick={stopAndFinalizeSession} className="bg-red-600 hover:bg-red-700">
              Finalizar Sesión
            </Button>
          </>
        )}
      </div>

      {sessionState === StreamingAvatarSessionState.CONNECTED && (
        <MessageHistory />
      )}

      {/* Doc Panel Toggle and Panel */}
      <button onClick={toggleDocPanel} className="fixed top-5 left-1/2 -translate-x-1/2 bg-blue-600 hover:bg-blue-700 text-white py-2 px-4 rounded-lg shadow-lg z-50 transition duration-200">
        📘 Ver Documentación
      </button>
      <div className={`fixed top-0 right-0 w-80 h-full bg-zinc-950 text-white p-5 border-l-2 border-blue-600 overflow-y-auto transition-transform duration-300 ease-in-out ${showDocPanel ? 'translate-x-0' : 'translate-x-full'} z-40`}>
        <h2 className="text-xl font-bold text-blue-400 mb-4">📋 Documentación útil</h2>
        <hr className="border-blue-600 mb-4" />
        <h3 className="text-lg font-semibold text-blue-300 mb-2">🧠 Presentación Efectiva</h3>
        <p className="text-zinc-300 text-sm mb-4">Una presentación efectiva combina saludo profesional, identificación de necesidad clínica y una pregunta abierta que involucre al médico.</p>
        <h3 className="text-lg font-semibold text-blue-300 mb-2">🎯 Objeciones Médicas</h3>
        <p className="text-zinc-300 text-sm mb-4">“Ya uso otro producto” → ¿Qué resultados ha observado?</p>
        <h3 className="text-lg font-semibold text-blue-300 mb-2">📊 Pasos de Visita</h3>
        <p className="text-zinc-300 text-sm mb-4">☑ Saludo ☑ Necesidad ☑ Propuesta ☑ Cierre</p>
        <h3 className="text-lg font-semibold text-blue-300 mb-2">⚖ Ética y Regulación</h3>
        <p className="text-zinc-300 text-sm">✅ Está permitido compartir evidencia válida.<br/>⛔ Está prohibido comparar sin estudios o sugerir usos fuera de indicación.</p>
      </div>

      <footer className="mt-auto mb-5 text-sm text-zinc-500 text-center w-full">
        <p>Desarrollado por <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">Teams</a> &copy; 2025</p>
      </footer>
    </div>
  );
}

// Wrapper para StreamingAvatarProvider
export default function InteractiveSessionWrapper() {
  return (
    <StreamingAvatarProvider basePath={process.env.NEXT_PUBLIC_BASE_API_URL || ""}>
      <InteractiveSessionContent />
    </StreamingAvatarProvider>
  );
}