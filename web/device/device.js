// Pantalla del dispositivo: personaje, llamada por botón o por nombre, y decisiones
// que siempre toma la persona (aprobar, recordar, compartir la cámara).

import { ApiError, createClient, normalize, readToken } from "/shared/api.js";
import { createCamera } from "/device/camera.js";
import { createVoice } from "/device/voice.js";

const LISTEN_WINDOW_MS = 9000;
const SESSION_POLL_MS = 10000;
const LANGUAGE = "es-CL";

const YES = /^(si|claro|bueno|ya|dale|ok|me interesa|aprobar|apruebo|recuerdalo|envialo|enviar)\b/;
const NO = /^(no|ahora no|mejor no|despues|cancelar)\b/;
const CAMERA_ON = /\b(compart\w*|enciend\w*|prend\w*|activ\w*) (la |mi )?camara\b/;
const CAMERA_OFF = /\b(apag\w*|paus\w*|desactiv\w*|deja de compartir) (la |mi )?camara\b/;
const RESUME = /\b(retom\w*|sigamos|volvamos|continuemos)\b/;

const $ = (id) => document.getElementById(id);
const ui = {
  device: $("device"),
  clock: $("clock"),
  caption: $("caption"),
  hint: $("hint"),
  card: $("card"),
  cardKicker: $("card-kicker"),
  cardTitle: $("card-title"),
  cardBody: $("card-body"),
  cardYes: $("card-yes"),
  cardNo: $("card-no"),
  support: $("support"),
  resources: $("support-resources"),
  supportContacts: $("support-contacts"),
  supportDraft: $("support-draft"),
  supportMessage: $("support-message"),
  supportCancel: $("support-cancel"),
  resume: $("resume"),
  power: $("power"),
  call: $("call"),
  cameraToggle: $("camera-toggle"),
  cameraIndicator: $("camera-indicator"),
  cameraText: $("camera-text"),
  preview: $("preview"),
  sayForm: $("say-form"),
  sayInput: $("say-input"),
  micStatus: $("mic-status"),
};

const token = readToken("silver-minds-device-token");
const api = token ? createClient("Device", token) : null;

const state = {
  name: "Silvia",
  personName: null,
  conversationId: null,
  contacts: new Map(),
  cards: [],
  listenTimer: null,
  draftRequestId: null,
};
let voice = null;
let camera = null;

// --- Presentación -------------------------------------------------------------------

function setState(next) {
  ui.device.dataset.state = next;
  if (next === "idle") {
    ui.caption.textContent = currentTime();
    ui.hint.textContent = `Di «${state.name}» o toca el botón`;
  }
}

function currentTime() {
  return new Date().toLocaleTimeString("es-CL", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function tickClock() {
  const now = new Date();
  ui.clock.textContent = now.toLocaleDateString("es-CL", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  if (ui.device.dataset.state === "idle") ui.caption.textContent = currentTime();
}

function say(text, hint = "") {
  ui.caption.textContent = text;
  ui.hint.textContent = hint;
}

function setMode(mode) {
  ui.device.dataset.mode = mode;
  if (mode === "normal") ui.support.hidden = true;
}

// --- Llamada y escucha --------------------------------------------------------------

function stopListening() {
  window.clearTimeout(state.listenTimer);
  state.listenTimer = null;
}

function listen(prompt = "Te escucho") {
  stopListening();
  setState("listening");
  say(prompt, "Habla cuando quieras");
  state.listenTimer = window.setTimeout(() => {
    if (ui.device.dataset.state === "listening" && !state.cards.length) goIdle();
  }, LISTEN_WINDOW_MS);
}

function goIdle() {
  stopListening();
  if (ui.device.dataset.mode !== "normal") {
    setState("attending");
    return; // en modo de apoyo la ayuda permanece en pantalla
  }
  state.conversationId = null; // cada llamada es una conversación nueva
  ui.card.hidden = true;
  setState("idle");
}

function wake(origin, rest = "") {
  voice.silence();
  api.post("/device/events", { kind: origin }).catch(() => {});
  if (rest) handleUtterance(rest);
  else listen(state.personName ? `Hola, ${state.personName}. Te escucho` : "Te escucho");
}

/** Minúsculas sin acentos conservando la posición de cada letra. */
const fold = (text) =>
  text
    .normalize("NFC")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "");

/** Decide qué hacer con una frase reconocida o escrita en el simulador. */
function hear(text) {
  if (!normalize(text)) return;
  const current = ui.device.dataset.state;
  if (current === "idle") {
    const original = text.normalize("NFC");
    const position = fold(original).indexOf(fold(state.name));
    if (position === -1) {
      ui.micStatus.textContent = `En reposo solo responde a su nombre («${state.name}») o al botón.`;
      return;
    }
    wake("wake_name", original.slice(position + state.name.normalize("NFC").length));
    return;
  }
  if (current === "listening" || current === "attending") handleUtterance(text);
}

async function handleUtterance(text) {
  const cleaned = text.replace(/^[\s,.:;¡!¿?]+/, "").trim();
  const spoken = normalize(cleaned);
  if (!spoken) {
    listen();
    return;
  }
  stopListening();
  if (CAMERA_OFF.test(spoken)) return shareCamera(false);
  if (CAMERA_ON.test(spoken)) return shareCamera(true);
  if (state.cards.length && YES.test(spoken)) return answerCard(true);
  if (state.cards.length && NO.test(spoken)) return answerCard(false);
  if (!ui.resume.hidden && RESUME.test(spoken)) return resumeNormal();
  await converse(cleaned);
}

async function converse(text) {
  setState("thinking");
  say(`«${text}»`, "Pensando…");
  try {
    if (!state.conversationId) {
      state.conversationId = (await api.post("/conversations")).id;
    }
    const reply = await api.post(`/conversations/${state.conversationId}/messages`, {
      content: text,
      client_message_id: crypto.randomUUID(),
    });
    await present(reply);
  } catch (error) {
    await speak(
      error instanceof ApiError && error.status === 429
        ? "Por hoy llegamos al límite de mensajes. Mañana seguimos."
        : "Tuve un problema para responder. Intentémoslo de nuevo en un momento.",
    );
    goIdle();
  }
}

async function speak(text, hint = "") {
  setState("speaking");
  say(text, hint);
  await voice.speak(text);
}

async function present(reply) {
  setMode(reply.mode);
  if (reply.mode !== "normal") {
    state.cards = [];
    ui.card.hidden = true;
    renderSupport(reply.support_options);
    await speak(reply.reply);
    setState("attending");
    return;
  }
  state.cards = [
    ...reply.proposals.map((proposal) => ({ type: "proposal", item: proposal })),
    ...reply.memory_candidates.map((candidate) => ({ type: "memory", item: candidate })),
  ];
  await speak(reply.reply);
  if (state.cards.length) showCard();
  else listen("¿Algo más?");
}

// --- Tarjetas: propuestas y memorias --------------------------------------------------

function showCard() {
  const card = state.cards[0];
  if (!card) {
    ui.card.hidden = true;
    listen("¿Algo más?");
    return;
  }
  if (card.type === "proposal") {
    const contact = state.contacts.get(card.item.contact_id);
    ui.cardKicker.textContent = card.item.is_generic ? "Una idea" : "Actividad del catálogo";
    ui.cardTitle.textContent = card.item.title;
    ui.cardBody.textContent = contact
      ? `${card.item.body}\nInvitación para: ${contact}`
      : card.item.body;
    ui.cardYes.textContent = contact ? `Sí, invitar a ${contact}` : "Sí, me interesa";
    ui.cardNo.textContent = "Ahora no";
  } else {
    ui.cardKicker.textContent = "¿Quieres que lo recuerde?";
    ui.cardTitle.textContent = card.item.content;
    ui.cardBody.textContent = "Solo lo guardo si tú lo confirmas. Puedes borrarlo cuando quieras.";
    ui.cardYes.textContent = "Sí, recuérdalo";
    ui.cardNo.textContent = "No";
  }
  ui.card.hidden = false;
  setState("attending");
  say("", "Puedes responder con la voz o tocando la pantalla");
}

async function answerCard(accepted) {
  const card = state.cards.shift();
  if (!card) return;
  ui.card.hidden = true;
  let message;
  try {
    if (card.type === "proposal") {
      const base = `/proposals/${card.item.id}`;
      if (accepted) {
        await api.post(`${base}/approve`, { version: card.item.version });
        const contact = state.contacts.get(card.item.contact_id);
        message = contact
          ? `Listo. Le envío la invitación a ${contact}.`
          : "Listo, quedó aprobada.";
      } else {
        await api.post(`${base}/reject`);
        message = "De acuerdo, la dejamos.";
      }
    } else {
      const base = `/memory-candidates/${card.item.id}`;
      await api.post(`${base}/${accepted ? "confirm" : "reject"}`);
      message = accepted ? "Lo voy a recordar." : "No lo guardo.";
    }
  } catch (error) {
    message =
      error instanceof ApiError && error.code === "actions_paused"
        ? "Por ahora las acciones están en pausa."
        : "No pude completar eso. Lo intentamos más tarde.";
  }
  await speak(message);
  showCard();
}

// --- Apoyo ----------------------------------------------------------------------------

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderSupport(options) {
  ui.resources.replaceChildren(
    ...options.resources.map((resource) => {
      const item = element("li");
      const link = element("a", "resource");
      link.href = `tel:${resource.phone.replace(/[^0-9*+#]/g, "")}`;
      link.append(
        element("span", "resource-phone", resource.phone),
        element("span", "resource-name", resource.name),
        element("span", "", `${resource.purpose} · ${resource.availability}`),
      );
      item.append(link);
      return item;
    }),
  );
  ui.supportContacts.replaceChildren(
    ...options.contacts.map((contact) => {
      const button = element("button", "choice choice-no", `Escribirle a ${contact.name}`);
      button.type = "button";
      button.addEventListener("click", () => draftSupportRequest(contact));
      return button;
    }),
  );
  ui.supportDraft.hidden = true;
  ui.resume.hidden = !options.can_resume;
  ui.support.hidden = false;
}

async function draftSupportRequest(contact) {
  const signature = state.personName ? ` — ${state.personName}` : "";
  const request = await api.post("/support-requests", {
    contact_id: contact.id,
    message: `Hola, ${contact.name}. ¿Puedes llamarme cuando puedas?${signature}`,
  });
  state.draftRequestId = request.id;
  ui.supportMessage.value = request.message;
  ui.supportDraft.hidden = false;
  ui.supportMessage.focus();
}

async function sendSupportRequest(event) {
  event.preventDefault();
  const base = `/support-requests/${state.draftRequestId}`;
  await api.patch(base, { message: ui.supportMessage.value });
  await api.post(`${base}/approve`);
  ui.supportDraft.hidden = true;
  await speak("Mensaje enviado. El correo puede tardar: si necesitas ayuda ahora, llama.");
  setState("attending");
}

async function cancelSupportRequest() {
  await api.post(`/support-requests/${state.draftRequestId}/cancel`).catch(() => {});
  ui.supportDraft.hidden = true;
}

async function resumeNormal() {
  try {
    await api.post("/support/resume", { confirm: true });
    setMode("normal");
    await speak("De acuerdo. Retomamos cuando quieras.");
    listen("¿En qué te ayudo?");
  } catch {
    await speak("Todavía no podemos retomar. Sigamos conversando.");
    setState("attending");
  }
}

// --- Cámara ---------------------------------------------------------------------------

function shareCamera(enabled) {
  camera.setSharing(enabled);
  speak(
    enabled
      ? "Cámara encendida. Tu familia puede verte mientras siga así."
      : "Cámara apagada. Nadie puede verte.",
  ).then(() => (state.cards.length ? showCard() : listen("¿Algo más?")));
}

function showCamera(sharing, viewers = []) {
  ui.device.dataset.camera = sharing ? "on" : "off";
  ui.cameraToggle.checked = sharing;
  ui.cameraIndicator.hidden = !sharing;
  ui.cameraText.textContent = viewers.length
    ? `${viewers.join(" y ")} ${viewers.length > 1 ? "están" : "está"} mirando`
    : "Cámara compartida";
}

// --- Arranque ---------------------------------------------------------------------------

async function refreshSession() {
  const session = await api.get("/device/session");
  state.name = session.device_name;
  state.personName = session.preferred_name;
  showCamera(session.camera_sharing, session.viewers);
  if (session.mode !== ui.device.dataset.mode && session.mode === "normal") setMode("normal");
  return session;
}

async function powerOn() {
  try {
    await refreshSession();
    const contacts = await api.get("/contacts");
    state.contacts = new Map(contacts.items.map((contact) => [contact.id, contact.name]));
  } catch {
    ui.power.textContent = "Este dispositivo no está autorizado. Abre el enlace de emparejamiento.";
    return;
  }
  voice = createVoice({
    language: LANGUAGE,
    onFinal: hear,
    onInterim: (text) => {
      if (ui.device.dataset.state === "listening") ui.hint.textContent = text;
    },
    onStatus: (text) => (ui.micStatus.textContent = text),
  });
  camera = createCamera({
    video: ui.preview,
    token,
    onViewers: (names) => showCamera(ui.device.dataset.camera === "on", names),
    onSharing: (sharing) => showCamera(sharing),
    onPresence: (value) => api.post("/device/events", { kind: "presence", value }).catch(() => {}),
    onProblem: (text) => (ui.micStatus.textContent = text),
    onReplaced: () => {
      voice.silence();
      ui.call.disabled = true;
      ui.cameraToggle.disabled = true;
      ui.power.textContent = "Este dispositivo se abrió en otra ventana. Recarga para usarlo aquí.";
      ui.device.dataset.state = "off";
    },
  });
  ui.call.disabled = false;
  ui.cameraToggle.disabled = false;
  setState("idle");
  voice.listen();
  window.setInterval(() => refreshSession().catch(() => {}), SESSION_POLL_MS);
}

if (!api) {
  ui.power.textContent = "Falta el enlace de emparejamiento de este dispositivo.";
  ui.power.disabled = true;
} else {
  ui.power.addEventListener("click", powerOn);
}

ui.call.addEventListener("click", () => wake("wake_button"));
ui.cameraToggle.addEventListener("change", () => camera.setSharing(ui.cameraToggle.checked));
ui.cardYes.addEventListener("click", () => answerCard(true));
ui.cardNo.addEventListener("click", () => answerCard(false));
ui.resume.addEventListener("click", resumeNormal);
ui.supportDraft.addEventListener("submit", sendSupportRequest);
ui.supportCancel.addEventListener("click", cancelSupportRequest);
ui.sayForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (ui.device.dataset.state === "off") return;
  hear(ui.sayInput.value);
  ui.sayInput.value = "";
});

tickClock();
window.setInterval(tickClock, 15000);
