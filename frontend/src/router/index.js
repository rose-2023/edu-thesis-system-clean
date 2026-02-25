import { createRouter, createWebHistory } from "vue-router";

import Login from "../pages/Login.vue";
import Entry from "../pages/Entry.vue";
import Quiz from "../pages/Quiz.vue";
import StudentHome from "../pages/StudentHome.vue";
import AdminUpload from "../pages/AdminUpload.vue";

const routes = [
  { path: "/", redirect: "/login" },

  // ✅ login 一定要有 name + public
  { path: "/login", name: "login", component: Login, meta: { public: true } },

  // ✅ 如果你的 entry/parsons 要免登入，就加 public
  { path: "/entry", name: "entry", component: Entry, meta: { public: true } },
  { path: "/parsons/:videoId", name: "parsons", component: () => import("../pages/parsons.vue"), meta: { public: true } },
  { path: "/learn/video/:videoId", name: "student-learning", component: () => import("../pages/StudentLearning.vue"), meta: { public: true } },

  // 其他頁面（需要登入就不要 public）
  { path: "/quiz", name: "quiz", component: Quiz },
  { path: "/home", name: "home", component: StudentHome },

  // 老師端
  { path: "/admin/upload", name: "adminUpload", component: AdminUpload },
  { path: "/admin/dashboard", name: "teacherDashboard", component: () => import("../pages/TeacherDashboard.vue") },
  { path: "/admin/agentlog", name: "teacherAgentLog", component: () => import("../pages/TeacherT5AgentLog.vue") },
  { path: "/admin/analyze", name: "teacherAnalyze", component: () => import("../pages/TeacherAnalyze.vue") },


  // ✅ 字幕校正（你要進的頁）
  { path: "/admin/subtitle", name: "teacherSubtitle", component: () => import("../pages/TeacherSubtitles.vue") },
  // { path: "/admin/videos", name: "teacherVideos", component: () => import("../pages/TeacherVideoManage.vue") },


  // 學生端
  { path: "/learn/:unit", name: "StudentLearning", component: () => import("../pages/StudentLearning.vue") },

  // ✅ catch-all 一定要放最後
  { path: "/:pathMatch(.*)*", redirect: "/login" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

// ✅ 路由守門
router.beforeEach((to, from, next) => {
  const token = localStorage.getItem("token");

  // 公開頁放行
  if (to.meta?.public) return next();

  // 有 token 放行
  if (token) return next();

  // 沒 token 回 login（保留原本要去的頁）
  return next({ name: "login", query: { redirect: to.fullPath } });
});

export default router;
export { router };
