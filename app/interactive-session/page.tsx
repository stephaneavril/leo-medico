// File: app/interactive-session/page.tsx

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
  // const [showDocPanel, setShowDocPanel] = useState(false); // Removed: No longer needed
  const [hasUserMediaPermission, setHasUserMediaPermission] = useState(false);

  // NEW: State to track if component has mounted (client-side) for hydration
  const [mounted, setMounted] = useState(false);


  /****************************** REFS ******************************/
  const messagesRef = useRef<any[]>([]);
  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const recordedChunks = useRef<Blob[]>([]); // This stores the chunks. It needs to be read AFTER the recorder stops.
  const localUserStreamRef = useRef<MediaStream | null>(null);
  const userCameraRef = useRef<HTMLVideoElement>(null);
  const avatarVideoRef = useRef<HTMLVideoElement>(null);
  const isFinalizingRef = useRef(false);

  // NEW: Effect to set mounted to true after the first render (on client)
  useEffect(() => {
    setMounted(true);
  }, []);

  const stopUserCameraRecording = useCallback(() => {
    // This function's role is ONLY to stop the stream tracks and clear refs
    // It should NOT stop mediaRecorderRef.current directly here, as we need to wait for onstop event.
    // The MediaRecorder stop logic is now handled in stopAndFinalizeSession.

    if (localUserStreamRef.current) {
        localUserStreamRef.current.getTracks().forEach(track => track.stop());
        localUserStreamRef.current = null;
        console.log("🎥 User camera stream tracks stopped.");
    }
    if (userCameraRef.current) {
        userCameraRef.current.srcObject = null;
    }
    // Do NOT set mediaRecorderRef.current to null here, it needs to be accessible in stopAndFinalizeSession
  }, []);

 const startUserCameraRecording = useCallback(() => {
  if (!localUserStreamRef.current || mediaRecorderRef.current) return;
  try {
    const recorder = new MediaRecorder(localUserStreamRef.current, {
      mimeType: 'video/webm; codecs=vp8',
      videoBitsPerSecond: 2500000, // 2.5 Mbps - UNCOMMENTED AND NOW ACTIVE
      audioBitsPerSecond: 128000   // 128 kbps - UNCOMMENTED AND NOW ACTIVE
    });

    recordedChunks.current = []; // Clear chunks array when starting a new recording

    recorder.ondataavailable = (e) => {
      console.log(`🎥 MediaRecorder: Received chunk. Size: ${e.data.size} bytes`);
      if (e.data.size > 0) {
        recordedChunks.current.push(e.data);
        console.log(`🎥 MediaRecorder: Pushed chunk. Total chunks: ${recordedChunks.current.length}`);
      } else {
        console.log(`🎥 MediaRecorder: 0-size chunk received. Not pushing.`);
      }
    };

    recorder.onerror = (event: MediaRecorderErrorEvent) => { // <-- Add MediaRecorderErrorEvent type
        console.error("🎥 MediaRecorder ERROR:", event.error);
    };

    recorder.start();
    mediaRecorderRef.current = recorder; // Store the recorder instance
    console.log('🎥 MediaRecorder START');
    console.log(`🎥 MediaRecorder state after start: ${recorder.state}`);
  } catch (err) {
    console.error('MediaRecorder initialization error:', err);
  }
}, []);

  // ************************ FINALIZACIÓN DE SESIÓN ************************
  const stopAndFinalizeSession = async (sessionMessages: any[]) => {
    if (isFinalizingRef.current) {
      console.log("🛑 Finalización ya en progreso o ya completada. Abortando llamada redundante.");
      return;
    }
    isFinalizingRef.current = true;

    console.log("🛑 Deteniendo grabación y sesión...");

    stopAvatar(); // Stop HeyGen avatar streaming

    const flaskApiUrl = process.env.NEXT_PUBLIC_FLASK_API_URL; // Declare flaskApiUrl once here
    const trimmedFlaskApiUrl = flaskApiUrl ? flaskApiUrl.trim() : ''; // Add this line to trim
    console.log("DEBUG: Original flaskApiUrl:", flaskApiUrl); // Keep this for raw value
    console.log("DEBUG: Trimmed flaskApiUrl:", trimmedFlaskApiUrl); // Debugging: Check the trimmed URL


    // --- Video Recording Finalization ---
    let finalVideoBlob: Blob | null = null;
    if (mediaRecorderRef.current) { // Check if recorder instance exists
        console.log(`🎥 MediaRecorder exists with state: '${mediaRecorderRef.current.state}'`);
        if (mediaRecorderRef.current.state === 'recording' || mediaRecorderRef.current.state === 'paused') {
            const recorderStopPromise = new Promise<void>((resolve) => {
                mediaRecorderRef.current!.onstop = () => {
                    console.log("🎥 MediaRecorder: onstop event fired. All chunks collected.");
                    resolve();
                };
            });

            mediaRecorderRef.current.stop();
            console.log("🎥 MediaRecorder: Sent stop signal.");
            // Await the onstop event only if it was recording.
            // For paused state, onstop might not fire immediately, but data will be available.
            if (mediaRecorderRef.current.state === 'recording') {
                await recorderStopPromise;
            } else {
                // For paused, wait a small moment to ensure last data is processed
                await new Promise(r => setTimeout(r, 100));
            }

        } else {
            console.warn(`MediaRecorder state was '${mediaRecorderRef.current.state}'. No active recording to stop.`);
        }

        if (recordedChunks.current.length > 0) {
            finalVideoBlob = new Blob(recordedChunks.current, { type: "video/webm" });
            console.log(`✅ Video Blob created. Size: ${finalVideoBlob.size} bytes`);
        } else {
            console.warn("No recorded video chunks available. Video Blob will be null.");
        }

        recordedChunks.current = []; // Clear chunks array
        mediaRecorderRef.current = null; // Clear recorder ref
    } else {
        console.warn("MediaRecorder instance was null when stopAndFinalizeSession called. No video captured.");
    }

    // Always stop user camera stream tracks, regardless of recorder state or if recording was active
    stopUserCameraRecording(); // Call this function to handle tracks and camera ref cleanup

    // --- End Video Recording Finalization ---


    const userTranscript = (Array.isArray(sessionMessages) ? sessionMessages : [])
                            .filter(msg => msg && msg.sender === MessageSender.CLIENT)
                            .map(msg => msg.content || "")
                            .join('\n');
    const avatarTranscript = (Array.isArray(sessionMessages) ? sessionMessages : [])
                              .filter(msg => msg && msg.sender === MessageSender.AVATAR)
                              .map(msg => msg.content || "")
                              .join('\n');
    const duration = 480 - recordingTimer;

    console.log(`📊 Transcripción del Usuario (longitud: ${userTranscript.length}): '${userTranscript.substring(0, Math.min(userTranscript.length, 100))}'`);
    console.log(`📊 Transcripción del Avatar (longitud: ${avatarTranscript.length}): '${avatarTranscript.substring(0, Math.min(avatarTranscript.length, 100))}'`);

    const simpleProcessingDiv = document.createElement("div");
    simpleProcessingDiv.id = "simple-processing-overlay";
    simpleProcessingDiv.style.cssText = `
      position: fixed; top: 0; left: 0; width: 100%; height: 100%;
      background-color: rgba(0,0,0,0.9); display: flex; flex-direction: column;
      align-items: center; justify-content: center; z-index: 10000; color: white;
      text-align: center; font-size: 1.5em;
    `;
    simpleProcessingDiv.innerHTML = `
      <div class="loader" style="border: 6px solid #f3f3f3; border-top: 6px solid #00e0ff; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin-bottom: 20px;"></div>
      <span>Registrando sesión...</span>
    `;
    document.body.appendChild(simpleProcessingDiv);


    let videoS3Key: string | null = null;
    try {
      if (finalVideoBlob) { // Use finalVideoBlob here
        console.log("Attempting to upload recording to Flask...");
        const videoFormData = new FormData(); // Define videoFormData here, inside the if block
        videoFormData.append('video', finalVideoBlob, "user_recording.webm");
        videoFormData.append('name', name || 'unknown');
        videoFormData.append('email', email || 'unknown');

        const uploadRes = await fetch(`${trimmedFlaskApiUrl}/upload_video`, { // Use trimmedFlaskApiUrl
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
          isFinalizingRef.current = false;
          return;
        }
      } else {
        console.warn("No final video blob to upload, skipping /upload_video call.");
      }

      console.log("Attempting to send session log to Flask /log_full_session...");
      const sessionLogRes = await fetch(`${trimmedFlaskApiUrl}/log_full_session`, { // Use trimmedFlaskApiUrl
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: name,
          email: email,
          scenario: scenario,
          conversation: userTranscript,
          avatar_transcript: avatarTranscript,
          duration: duration,
          video_object_key: videoS3Key
        })
      });

      if (sessionLogRes.ok) {
        const sessionLogData = await sessionLogRes.json();
        console.log("✅ Flask /log_full_session success. Response:", sessionLogData);
      } else {
        const errorText = await sessionLogRes.text();
        console.error("❌ Error al registrar sesión a Flask /log_full_session:", sessionLogRes.status, errorText);
        alert("⚠️ Error al registrar la sesión para análisis. Consulta la consola para más detalles.");
      }

    } catch (err) {
      console.error("❌ Error general en la solicitud de subida o registro:", err);
      alert("❌ Error de red durante el proceso de finalización de la sesión.");
    } finally {
      document.getElementById("simple-processing-overlay")?.remove();
      router.push('/dashboard');
    }
  };

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
        localUserStreamRef.current = stream;
        if (userCameraRef.current) {
          userCameraRef.current.srcObject = stream;
        }
        setHasUserMediaPermission(true);
        setShowAutoplayBlockedMessage(false);
        console.log("🎥 User camera preview stream acquired and permissions granted.");
      } catch (error: any) {
        console.error("❌ No se pudo acceder a la cámara del usuario para la vista previa o grabación:", error);
        setHasUserMediaPermission(false);
        setShowAutoplayBlockedMessage(true);
      }
    };

    getUserMediaStream();

    return () => {
        // This cleanup is for when the component unmounts unexpectedly, not normal session finalization.
        if (!isFinalizingRef.current) {
            console.log("useEffect cleanup: Deteniendo medios locales (no finalizando).");
            // Stop media recorder directly here if it's still running and not part of finalization flow
            if(mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
                mediaRecorderRef.current.stop();
                mediaRecorderRef.current = null;
            }
            stopUserCameraRecording(); // Stop tracks and clear camera ref
            stopAvatar(); // Stop HeyGen avatar
        }
    };
  }, [stopUserCameraRecording, isFinalizingRef, stopAvatar]); // Added stopAvatar to dependencies

  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && hasUserMediaPermission && !mediaRecorderRef.current) {
        console.log("HeyGen Session CONNECTED. Attempting to start user recording.");
        startUserCameraRecording();
    }
  }, [sessionState, hasUserMediaPermission, mediaRecorderRef, startUserCameraRecording]);

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

  const startHeyGenSession = useCallback(async (startWithVoice: boolean) => {
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
      avatar.on(StreamingEvents.STREAM_DISCONNECTED, (event) => { // Added 'event' parameter
        console.log("HeyGen Stream disconnected.", event); // Log event for more detail
        if (!isFinalizingRef.current) {
            console.log("Stream desconectado inesperadamente. Disparando finalización.");
            stopAndFinalizeSession(messagesRef.current); // Pass current messages to stopAndFinalizeSession
        }
      });
      avatar.on(StreamingEvents.STREAM_READY, (event) => {
        console.log(">>>>> HeyGen Stream ready:", event.detail);
        setShowAutoplayBlockedMessage(false);
        setIsAttemptingAutoStart(false);
      });
      avatar.on(StreamingEvents.USER_START, (event) => console.log(">>>>> User started talking:", event));
      avatar.on(StreamingEvents.USER_STOP, (event) => console.log(">>>>> User stopped talking.", event)); // Added 'event' parameter

      // Crucial: Pass the entire 'detail' object from the SDK event.
      avatar.on(StreamingEvents.USER_END_MESSAGE, (event) => {
        console.log("HeyGen: USER_END_MESSAGE event received. Detail:", event.detail);
        handleUserTalkingMessage({ detail: event.detail });
      });
      avatar.on(StreamingEvents.USER_TALKING_MESSAGE, (event) => {
        console.log("HeyGen: USER_TALKING_MESSAGE event received. Detail:", event.detail);
        handleUserTalkingMessage({ detail: event.detail });
      });
      avatar.on(StreamingEvents.AVATAR_TALKING_MESSAGE, (event) => {
        console.log("HeyGen: AVATAR_TALKING_MESSAGE event received. Detail:", event.detail);
        handleStreamingTalkingMessage({ detail: event.detail });
      });
      avatar.on(StreamingEvents.AVATAR_END_MESSAGE, (event) => {
        console.log("HeyGen: AVATAR_END_MESSAGE event received. Detail:", event.detail);
        handleStreamingTalkingMessage({ detail: event.detail });
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
  }, [hasUserMediaPermission, fetchAccessToken, initAvatar, stopAndFinalizeSession, messagesRef, startAvatar, startVoiceChat, config, stopAvatar, stopUserCameraRecording, handleUserTalkingMessage, handleStreamingTalkingMessage, sessionState, setIsAttemptingAutoStart, setShowAutoplayBlockedMessage]);


 useUnmount(() => {
  console.log("Component unmounting. Ensuring all streams/recorders are stopped.");
  if (!isFinalizingRef.current && sessionState === StreamingAvatarSessionState.CONNECTED) {
      // Pass the current messages from the ref during unmount cleanup
      console.log("useUnmount: Sesión CONECTADA y no finalizada explícitamente. Disparando FINALIZACIÓN GRACIAS A UNMOUNT.");
      stopAndFinalizeSession(messagesRef.current); // Pass current messages to stopAndFinalizeSession
  } else if (!isFinalizingRef.current) {
      console.log("useUnmount: Sesión NO CONECTADA o ya finalizando. Solo deteniendo medios locales y avatar.");
      // Stop media recorder directly here if it's still running and not part of finalization flow
      if(mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
          mediaRecorderRef.current.stop();
          mediaRecorderRef.current = null;
      }
      stopUserCameraRecording(); // Stop tracks and clear camera ref
      stopAvatar(); // Stop HeyGen avatar
  } else {
      console.log("useUnmount: Finalización ya en curso, el desmontaje es parte del proceso.");
  }
});

  useEffect(() => {
    if (stream && avatarVideoRef.current) {
      avatarVideoRef.current.srcObject = stream;
      avatarVideoRef.current.onloadedmetadata = () => {
        avatarVideoRef.current!.play()
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
  }, [avatarVideoRef, stream, stopAvatar]);

  useEffect(() => {
    if (sessionState === StreamingAvatarSessionState.CONNECTED && stream && avatarVideoRef.current) {
      const videoElement = avatarVideoRef.current;
      const checkAndPlay = setTimeout(() => {
        if (videoElement.paused || videoElement.ended || videoElement.readyState < 3) {
          console.log("El video del avatar no se está reproduciendo, intentando reproducir de nuevo...");
          videoElement.play().catch(e => console.error("Error al reproducir el video de nuevo:", e));
        }
      }, 1000);
      return () => clearTimeout(checkAndPlay);
    }
  }, [sessionState, stream, avatarVideoRef]);

  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (sessionState === StreamingAvatarSessionState.CONNECTED) {
      interval = setInterval(() => {
        setRecordingTimer((prev) => {
          if (prev <= 1) {
            clearInterval(interval);
            console.log("⏰ Tiempo agotado. Deteniendo y finalizando sesión.");
            stopAndFinalizeSession(messagesRef.current); // Pass messages here
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

  const handleAutoplayRetry = useCallback(async () => {
    console.log("handleAutoplayRetry triggered by user click.");
    setShowAutoplayBlockedMessage(false);

    if (!hasUserMediaPermission) {
        alert("Por favor, permite el acceso a la cámara y el micrófono cuando se te solicite para habilitar la sesión.");
        return;
    }
    // Attempt to restart session only if permissions are already granted
    await startHeyGenSession(true);
  }, [hasUserMediaPermission, startHeyGenSession, setShowAutoplayBlockedMessage]);

  const formatTime = (seconds: number) => {
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = seconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
  };

  // Removed: toggleDocPanel function is no longer needed
  // const toggleDocPanel = () => {
  //   setShowDocPanel(prev => !prev);
  // };

  // REFINED HYDRATION FIX: Render the main structure consistently,
  // then conditionally render dynamic content within it.
  // The 'Error: Faltan datos de usuario.' message moves inside the conditional rendering.
  const hasUserData = name && email && scenario && userToken;

  return (
    <div className="w-screen h-screen flex flex-col items-center bg-zinc-900 text-white relative">
      {/* Suppress hydration warning on H1 as its content might slightly differ initially on server */}
      <h1 className="text-3xl font-bold text-blue-400 mt-6 mb-4" suppressHydrationWarning>
        🧠 Leo – {mounted ? (scenario || "Cargando...") : "Cargando..."}
      </h1>

      {/* Conditional content based on whether user data is available after mounted,
          preventing hydration mismatches by keeping main structure consistent. */}
      {(!mounted || !hasUserData) ? (
          <div className="flex flex-col items-center justify-center p-4 min-h-[calc(100vh-150px)]"> {/* Added min-height to prevent layout shift */}
              <LoadingIcon className="w-10 h-10 animate-spin text-blue-400" />
              <p className="mt-4 text-zinc-300">Verificando información de usuario y redirigiendo si es necesario.</p>
          </div>
      ) : (
          <>
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
                    <AvatarVideo ref={avatarVideoRef} />
                  ) : (
                    !showAutoplayBlockedMessage && (
                        sessionState === StreamingAvatarSessionState.INACTIVE && (
                            <AvatarConfig config={config} onConfigChange={setConfig} readOnly />
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
                  <div className="flex items-center space-x-2 text-white">
                    <LoadingIcon className="w-6 h-6 animate-spin" />
                    <span>Conectando...</span>
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

              {sessionState === StreamingAvatarSessionState.CONNECTED && (
                <MessageHistory />
              )}

              {/* Removed: Doc Panel Toggle and Panel */}
              {/*
              <button onClick={toggleDocPanel} className="fixed top-5 left-1/2 -translate-x-1/2 bg-blue-600 hover:bg-blue-700 text-white py-2 px-4 rounded-lg shadow-lg z-50 transition duration-300">
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
              */}
          </>
      )} {/* End of conditional rendering based on user data */}

      <footer className="mt-auto mb-5 text-sm text-zinc-500 text-center w-full">
        <p>Desarrollado por <a href="https://www.teams.com.mx" target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">Teams</a> &copy; 2025</p>
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