import axios from "axios";

export const api = axios.create({
  baseURL: "http://127.0.0.1:5000",
  timeout: 15000
});
export default api;

// 攔截器：只為 JSON 請求設定 Content-Type，FormData 請求則跳過
api.interceptors.request.use(
  (config) => {
    // 如果不是 FormData，則設定 JSON content-type
    if (!(config.data instanceof FormData)) {
      config.headers["Content-Type"] = "application/json";
    }
    return config;
  },
  (error) => Promise.reject(error)
);
