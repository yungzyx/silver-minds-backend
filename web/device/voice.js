// Voz del dispositivo: reconocimiento continuo para oír su nombre y síntesis para hablar.
//
// Usa la Web Speech API del navegador. En Chrome el reconocimiento envía el audio a un
// servicio del proveedor del navegador: en un dispositivo real se reemplaza por un
// detector local de la palabra de activación y por el endpoint de audio del backend.

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const RESTART_DELAY_MS = 400;

export function createVoice({ language, onFinal, onInterim, onStatus }) {
  let recognition = null;
  let wanted = false; // el dispositivo está encendido y debe escuchar
  let paused = false; // en pausa mientras el dispositivo habla, para no oírse a sí mismo

  function start() {
    if (!recognition || !wanted || paused) return;
    try {
      recognition.start();
    } catch {
      // Ya estaba escuchando.
    }
  }

  if (Recognition) {
    recognition = new Recognition();
    recognition.lang = language;
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onstart = () => onStatus("Micrófono encendido: te escucho.");
    recognition.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const text = event.results[i][0].transcript.trim();
        if (!text) continue;
        if (event.results[i].isFinal) onFinal(text);
        else onInterim(text);
      }
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        wanted = false;
        onStatus("Sin permiso de micrófono. Usa el campo de texto para simular la voz.");
      }
    };
    // El navegador corta el reconocimiento cada cierto tiempo: se retoma solo.
    recognition.onend = () => window.setTimeout(start, RESTART_DELAY_MS);
  }

  function pickVoice() {
    const voices = window.speechSynthesis?.getVoices() ?? [];
    const prefix = language.slice(0, 2);
    return (
      voices.find((voice) => voice.lang === language) ||
      voices.find((voice) => voice.lang.startsWith(prefix)) ||
      null
    );
  }

  return {
    supported: Boolean(recognition),

    listen() {
      wanted = true;
      if (!recognition) {
        onStatus("Este navegador no reconoce voz. Usa el campo de texto para simularla.");
        return;
      }
      start();
    },

    /** Dice el texto en voz alta. La promesa se cumple al terminar (o si no hay voz). */
    speak(text) {
      return new Promise((resolve) => {
        if (!window.speechSynthesis || !text) {
          resolve();
          return;
        }
        paused = true;
        recognition?.abort();
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = language;
        utterance.rate = 0.95;
        const voice = pickVoice();
        if (voice) utterance.voice = voice;
        const finish = () => {
          paused = false;
          start();
          resolve();
        };
        utterance.onend = finish;
        utterance.onerror = finish;
        window.speechSynthesis.speak(utterance);
      });
    },

    silence() {
      window.speechSynthesis?.cancel();
    },
  };
}
