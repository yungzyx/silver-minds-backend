// Utilidades compartidas: token del enlace y llamadas autenticadas a la API.

const API_BASE = "/api/v1";

/** Lee el token del fragmento (#token=...), lo guarda y limpia la barra de direcciones.
 *  El fragmento nunca se envía al servidor, así que el token no queda en sus registros. */
export function readToken(storageKey) {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const fromLink = params.get("token");
  if (fromLink) {
    window.localStorage.setItem(storageKey, fromLink);
    window.history.replaceState(null, "", window.location.pathname);
  }
  return fromLink || window.localStorage.getItem(storageKey);
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
  return {
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
