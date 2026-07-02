import axios from "axios";


const AUTH_STORAGE_KEYS = [
  "student_id",
  "studentId",
  "token",
  "access_token",
  "refresh_token",
  "participant_id",
  "role",
  "user",
];

let handlingExpiredSession = false;
let fetchInstalled = false;
const axiosClients = new WeakSet();


export function getAuthToken() {
  return localStorage.getItem("token") || "";
}


export function clearAuthState() {
  for (const key of AUTH_STORAGE_KEYS) localStorage.removeItem(key);
  sessionStorage.removeItem("token");
  sessionStorage.removeItem("access_token");
}


export async function logoutCurrentSession(apiBase = null) {
  const base = String(
    apiBase || import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000",
  ).replace(/\/$/, "");
  try {
    await window.fetch(`${base}/api/auth/logout`, { method: "POST" });
  } catch (_) {
    // Local state must still be cleared when the server is temporarily unavailable.
  } finally {
    clearAuthState();
  }
}


function isApiRequest(input) {
  const raw = typeof input === "string" ? input : input?.url;
  if (!raw) return false;
  try {
    const url = new URL(raw, window.location.origin);
    return url.pathname.startsWith("/api/") || url.pathname === "/api";
  } catch (_) {
    return String(raw).includes("/api/");
  }
}


function isBackendAxiosRequest(config) {
  try {
    const backendBase = new URL(
      import.meta.env.VITE_API_BASE || "http://127.0.0.1:5000",
      window.location.origin,
    );
    const target = new URL(config?.url || "", config?.baseURL || window.location.origin);
    return target.origin === backendBase.origin;
  } catch (_) {
    return String(config?.url || "").includes("/api/");
  }
}


export function handleSessionExpired(payload) {
  if (payload?.error !== "session_expired_due_to_new_login") return false;
  clearAuthState();
  if (handlingExpiredSession) return true;

  handlingExpiredSession = true;
  window.alert("此帳號已在其他裝置登入，您已被登出。");
  if (window.location.pathname !== "/login") {
    window.location.replace("/login");
  } else {
    handlingExpiredSession = false;
  }
  return true;
}


export function installFetchSessionHandling() {
  if (fetchInstalled || typeof window === "undefined") return;
  fetchInstalled = true;
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    const requestInit = { ...init };
    if (isApiRequest(input)) {
      const headers = new Headers(init.headers || input?.headers || {});
      const token = getAuthToken();
      if (token && !headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      requestInit.headers = headers;
    }

    const response = await originalFetch(input, requestInit);
    if (response.status === 401 && isApiRequest(input)) {
      const payload = await response.clone().json().catch(() => null);
      handleSessionExpired(payload);
    }
    return response;
  };
}


export function installAxiosSessionHandling(client = axios) {
  if (!client || axiosClients.has(client)) return;
  axiosClients.add(client);

  client.interceptors.request.use((config) => {
    const token = getAuthToken();
    if (token && isBackendAxiosRequest(config)) {
      config.headers = config.headers || {};
      if (!config.headers.Authorization) {
        config.headers.Authorization = `Bearer ${token}`;
      }
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error?.response?.status === 401 && isBackendAxiosRequest(error.config)) {
        handleSessionExpired(error.response.data);
      }
      return Promise.reject(error);
    },
  );
}
