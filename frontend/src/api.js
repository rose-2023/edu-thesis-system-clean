import axios from "axios";
import { installAxiosSessionHandling } from "./sessionAuth";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || "", 
  timeout: 15000
  
});
console.log(
  "[api.js] VITE_API_BASE:",
  JSON.stringify(import.meta.env.VITE_API_BASE)
);

console.log(
  "[api.js] axios baseURL:",
  JSON.stringify(api.defaults.baseURL)
);
export default api;

// 攔截器：只為 JSON 請求設定 Content-Type，FormData 請求則跳過
api.interceptors.request.use(
  (config) => { 
    console.log("[Axios request]", {
    baseURL: config.baseURL,
    url: config.url,
  });
    // 如果不是 FormData，則設定 JSON content-type
    if (!(config.data instanceof FormData)) {
      config.headers["Content-Type"] = "application/json";
    }
    return config;
  },
  (error) => Promise.reject(error)
);

installAxiosSessionHandling(api);
