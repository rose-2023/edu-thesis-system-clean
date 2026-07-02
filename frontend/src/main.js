import { createApp } from "vue";
import axios from "axios";
import App from "./App.vue";
import { router } from "./router";
import {
  installAxiosSessionHandling,
  installFetchSessionHandling,
} from "./sessionAuth";

installFetchSessionHandling();
installAxiosSessionHandling(axios);

const app = createApp(App);

app.use(router);
app.mount("#app");
