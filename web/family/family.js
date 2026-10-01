// Panel familiar: señales de actividad y, si la persona mayor la enciende, la cámara en vivo.

import { ApiError, createClient, readToken, streamUrl } from "/shared/api.js";

const POLL_MS = 5000;
const RECONNECT_MS = 4000;
const SVG = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const token = readToken("silver-minds-family-token");
const api = token ? createClient("Viewer", token) : null;

let personName = "";
let frameUrl = null;
let lastFrameAt = 0;
let streamAllowed = false;

// --- Formato --------------------------------------------------------------------------

function relative(iso) {
  if (!iso) return "sin registro";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "recién";
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.round(hours / 24);
  return `hace ${days} ${days === 1 ? "día" : "días"}`;
}

function clockTime(iso) {
  return new Date(iso).toLocaleString("es-CL", {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function svg(tag, attributes, text) {
  const node = document.createElementNS(SVG, tag);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  if (text !== undefined) node.textContent = text;
  return node;
}

// --- Representación ---------------------------------------------------------------------

function renderChart(series) {
  const width = 420;
  const height = 180;
  const top = 24;
  const bottom = 28;
  const slot = width / series.length;
  const barWidth = slot * 0.56;
  const peak = Math.max(1, ...series.map((point) => point.interactions));
  const usable = height - top - bottom;
  const chart = $("chart");
  chart.replaceChildren(
    svg("line", { class: "baseline", x1: 0, x2: width, y1: height - bottom, y2: height - bottom }),
  );
  series.forEach((point, index) => {
    const isToday = index === series.length - 1;
    const barHeight = point.interactions ? Math.max(4, (point.interactions / peak) * usable) : 3;
    const x = index * slot + (slot - barWidth) / 2;
    const y = height - bottom - barHeight;
    const kind = point.interactions ? (isToday ? "bar bar-today" : "bar") : "bar bar-empty";
    const bar = svg("rect", { class: kind, x, y, width: barWidth, height: barHeight, rx: 4 });
    bar.style.animationDelay = `${index * 40}ms`;
    const day = new Date(`${point.day}T12:00:00`);
    const label = isToday ? "hoy" : day.toLocaleDateString("es-CL", { weekday: "short" });
    chart.append(bar, svg("text", { x: x + barWidth / 2, y: height - 8 }, label.replace(".", "")));
    if (point.interactions) {
      const value = String(point.interactions);
      chart.append(svg("text", { class: "value", x: x + barWidth / 2, y: y - 6 }, value));
    }
  });
  const total = series.reduce((sum, point) => sum + point.interactions, 0);
  $("chart-summary").textContent =
    `Conversaciones con el asistente por día. Total de la semana: ${total}.`;
}

function renderTimeline(entries) {
  const list = $("timeline-list");
  if (!entries.length) {
    const empty = document.createElement("li");
    empty.className = "timeline-empty";
    empty.textContent = "Todavía no hay actividad esta semana.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(
    ...entries.map((entry) => {
      const item = document.createElement("li");
      const time = document.createElement("time");
      time.dateTime = entry.at;
      time.textContent = clockTime(entry.at);
      const label = document.createElement("span");
      label.textContent = entry.label;
      if (entry.kind === "invitation") label.className = "highlight";
      item.append(time, label);
      return item;
    }),
  );
}

function renderCamera(camera, device) {
  streamAllowed = camera.allowed;
  const frame = $("frame");
  const live = camera.allowed && camera.sharing && Date.now() - lastFrameAt < 4000;
  frame.dataset.live = String(live);
  $("live-image").hidden = !live;
  let message;
  if (!camera.allowed) message = `${personName} no compartió la cámara contigo.`;
  else if (!device.online) message = "El dispositivo está apagado o sin conexión.";
  else if (!camera.sharing) {
    message = `${personName} tiene la cámara apagada. Solo se comparte cuando ${personName} la enciende.`;
  } else message = "Conectando con la cámara…";
  $("live-message").textContent = message;
}

function render(overview) {
  personName = overview.person_name;
  $("viewer").textContent = `Hola, ${overview.viewer_name}`;
  $("person-name").textContent = personName;
  document.querySelectorAll(".person-inline").forEach((node) => (node.textContent = personName));

  const { device, today, week } = overview;
  $("device-dot").dataset.online = String(device.online);
  $("device-status").textContent = device.online
    ? `${device.name ?? "El dispositivo"} está encendido`
    : `Dispositivo sin conexión${device.last_seen_at ? ` · visto ${relative(device.last_seen_at)}` : ""}`;
  $("last-interaction").textContent = `Última conversación: ${relative(today.last_interaction_at)}`;

  $("interactions").textContent = today.interactions;
  $("interactions-label").textContent =
    today.interactions === 1 ? "vez conversó con el asistente" : "veces conversó con el asistente";
  $("calls-button").textContent = today.calls_by_button;
  $("calls-name").textContent = today.calls_by_name;
  $("presence").textContent = today.presence_minutes;
  $("presence-note").textContent = today.last_presence_at
    ? `Último movimiento registrado ${relative(today.last_presence_at)}. Solo se mide con la cámara encendida.`
    : "El movimiento solo se mide con la cámara encendida.";

  $("approved").textContent = week.activities_approved;
  $("done").textContent = week.activities_done;
  $("sent").textContent = week.invitations_sent;
  $("accepted").textContent = week.invitations_accepted;

  renderChart(overview.series);
  renderTimeline(overview.timeline);
  renderCamera(overview.camera, device);
}

// --- Datos ------------------------------------------------------------------------------

function showGate(message) {
  $("panel").hidden = true;
  $("gate").hidden = false;
  $("gate").textContent = message;
}

async function refresh() {
  try {
    render(await api.get("/family/overview"));
    $("gate").hidden = true;
    $("panel").hidden = false;
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showGate("Este acceso ya no está disponible. Pídele a tu familiar que te lo comparta de nuevo.");
      return false;
    }
    return true; // un fallo de red no cierra el panel; se reintenta
  }
}

function connectStream() {
  const socket = new WebSocket(streamUrl("/family/stream"));
  socket.binaryType = "blob";
  socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token }));
  socket.onmessage = (event) => {
    if (typeof event.data === "string") {
      const message = JSON.parse(event.data);
      if (message.type === "status" && !message.sharing) lastFrameAt = 0;
      return;
    }
    const image = $("live-image");
    const next = URL.createObjectURL(event.data);
    image.onload = () => {
      if (frameUrl) URL.revokeObjectURL(frameUrl);
      frameUrl = next;
    };
    image.src = next;
    image.hidden = false;
    lastFrameAt = Date.now();
    $("frame").dataset.live = "true";
  };
  socket.onclose = (event) => {
    $("frame").dataset.live = "false";
    if (event.code === 4401 || event.code === 4403) return; // sin permiso: no se reintenta
    window.setTimeout(connectStream, RECONNECT_MS);
  };
}

async function start() {
  if (!api) {
    showGate("Falta el enlace personal que te envió tu familiar.");
    return;
  }
  if (!(await refresh())) return;
  if (streamAllowed) connectStream();
  window.setInterval(async () => {
    await refresh();
  }, POLL_MS);
}

start();
