// Utilidades compartidas: token del enlace y llamadas autenticadas a la API.

const API_BASE = "/api/v1";
const REPLACE_PAIRING =
  "Esta pantalla ya está vinculada a una cuenta. El enlace que abriste la vincularía a " +
  "otra. ¿Quieres reemplazar el vínculo actual? Si no reconoces el enlace, elige Cancelar.";

/** Lee el token del fragmento (#token=...), lo guarda y limpia la barra de direcciones.
 *  El fragmento nunca se envía al servidor, así que el token no queda en sus registros. */
export function readToken(storageKey) {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const fromLink = params.get("token");
  const stored = window.localStorage.getItem(storageKey);
  if (!fromLink) return stored;
  window.history.replaceState(null, "", window.location.pathname);
  // Un enlace ajeno podría vincular esta pantalla a la cuenta de otra persona: si ya
  // hay un emparejamiento distinto, solo se reemplaza con una confirmación explícita.
  const replaces = stored && stored !== fromLink;
  if (replaces && !window.confirm(REPLACE_PAIRING)) return stored;
  window.localStorage.setItem(storageKey, fromLink);
  return fromLink;
}

// Si se pega un enlace nuevo con la página ya abierta, solo cambia el fragmento y el
// navegador no recarga: se recarga aquí para tomar el token nuevo.
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(window.location.hash.slice(1)).has("token")) window.location.reload();
});

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function createClient(scheme, token) {
  async function request(method, path, body) {
    const response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        Authorization: `${scheme} ${token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const error = payload?.error ?? {};
      throw new ApiError(response.status, error.code ?? "error", error.message ?? "Error");
    }
    return payload;
  }
  /** POST que devuelve un archivo (por ejemplo, audio). */
  async function postForBlob(path, body) {
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { Authorization: `${scheme} ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new ApiError(response.status, "error", "Error");
    return response.blob();
  }
  return {
    postForBlob,
    get: (path) => request("GET", path),
    post: (path, body = {}) => request("POST", path, body),
    patch: (path, body) => request("PATCH", path, body),
  };
}

export function streamUrl(path) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${API_BASE}${path}`;
}

/** Texto en minúsculas y sin acentos, para comparar lo que se dijo. */
export function normalize(text) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-zñ0-9 ]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
