// Cámara del dispositivo. La persona mayor decide cuándo se comparte.
//
// - Solo se enciende cuando ella lo pide; al apagarla se detiene la captura.
// - Los cuadros se envían en vivo y el servidor no los guarda.
// - La presencia es un número (cuánto cambió la imagen): no sale ninguna imagen por
//   esa vía, y solo se mide con la cámara encendida.

import { streamUrl } from "/shared/api.js";

const FRAME_INTERVAL_MS = 250; // 4 cuadros por segundo
const FRAME_WIDTH = 480;
const JPEG_QUALITY = 0.6;
const MOTION_SAMPLE_MS = 1000;
const MOTION_REPORT_MS = 15000;
const MOTION_THRESHOLD = 0.02;
const MOTION_WIDTH = 32;
const MOTION_HEIGHT = 24;
const RECONNECT_MS = 3000;

export function createCamera({ video, token, onViewers, onSharing, onPresence, onProblem }) {
  let socket = null;
  let media = null;
  let sharing = false;
  let timers = [];
  let previousPixels = null;
  let peakMotion = 0;

  const frameCanvas = document.createElement("canvas");
  const motionCanvas = document.createElement("canvas");
  motionCanvas.width = MOTION_WIDTH;
  motionCanvas.height = MOTION_HEIGHT;

  function connect() {
    socket = new WebSocket(streamUrl("/device/stream"));
    socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token }));
    socket.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const message = JSON.parse(event.data);
      if (message.type === "viewers") onViewers(message.names);
      if (message.type === "ready") applySharing(message.camera_sharing);
      if (message.type === "camera") applySharing(message.enabled);
    };
    socket.onclose = (event) => {
      if (event.code === 4401 || event.code === 4403) return; // token inválido o revocado
      window.setTimeout(connect, RECONNECT_MS);
    };
  }

  async function applySharing(enabled) {
    sharing = enabled;
    if (enabled) {
      const started = await startCapture();
      if (!started) {
        // Sin acceso a la cámara no se puede compartir: se deja apagada en el servidor.
        setSharing(false);
        return;
      }
    } else {
      stopCapture();
    }
    onSharing(sharing);
  }

  async function startCapture() {
    if (media) return true;
    try {
      media = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480, facingMode: "user" },
        audio: false,
      });
    } catch {
      onProblem("No se pudo usar la cámara. Revisa el permiso del navegador.");
      return false;
    }
    video.srcObject = media;
    video.hidden = false;
    await video.play().catch(() => {});
    timers = [
      window.setInterval(sendFrame, FRAME_INTERVAL_MS),
      window.setInterval(sampleMotion, MOTION_SAMPLE_MS),
      window.setInterval(reportMotion, MOTION_REPORT_MS),
    ];
    return true;
  }

  function stopCapture() {
    timers.forEach((timer) => window.clearInterval(timer));
    timers = [];
    media?.getTracks().forEach((track) => track.stop());
    media = null;
    video.srcObject = null;
    video.hidden = true;
    previousPixels = null;
    peakMotion = 0;
  }

  function sendFrame() {
    if (!sharing || socket?.readyState !== WebSocket.OPEN || !video.videoWidth) return;
    frameCanvas.width = FRAME_WIDTH;
    frameCanvas.height = Math.round((FRAME_WIDTH * video.videoHeight) / video.videoWidth);
    frameCanvas.getContext("2d").drawImage(video, 0, 0, frameCanvas.width, frameCanvas.height);
    frameCanvas.toBlob(
      (blob) => {
        if (blob && sharing && socket?.readyState === WebSocket.OPEN) socket.send(blob);
      },
      "image/jpeg",
      JPEG_QUALITY,
    );
  }

  /** Cambio promedio de brillo entre dos muestras pequeñas, entre 0 y 1. */
  function sampleMotion() {
    if (!video.videoWidth) return;
    const context = motionCanvas.getContext("2d", { willReadFrequently: true });
    context.drawImage(video, 0, 0, MOTION_WIDTH, MOTION_HEIGHT);
    const { data } = context.getImageData(0, 0, MOTION_WIDTH, MOTION_HEIGHT);
    const pixels = new Uint8Array(MOTION_WIDTH * MOTION_HEIGHT);
    for (let i = 0; i < pixels.length; i += 1) {
      pixels[i] = (data[i * 4] + data[i * 4 + 1] + data[i * 4 + 2]) / 3;
    }
    if (previousPixels) {
      let difference = 0;
      for (let i = 0; i < pixels.length; i += 1) {
        difference += Math.abs(pixels[i] - previousPixels[i]);
      }
      peakMotion = Math.max(peakMotion, difference / pixels.length / 255);
    }
    previousPixels = pixels;
  }

  function reportMotion() {
    if (peakMotion >= MOTION_THRESHOLD) onPresence(Math.min(peakMotion, 1));
    peakMotion = 0;
  }

  function setSharing(enabled) {
    if (socket?.readyState !== WebSocket.OPEN) {
      onProblem("Sin conexión con el servidor. Inténtalo de nuevo en un momento.");
      onSharing(sharing);
      return;
    }
    socket.send(JSON.stringify({ type: "camera", enabled }));
  }

  connect();
  return { setSharing };
}
