import { createRouter, createWebHistory } from "vue-router";

import Login from "../pages/Login.vue";
import Entry from "../pages/Entry.vue";
import Quiz from "../pages/Quiz.vue";
import PreCheck from "../pages/PreCheck.vue";
import StudentHome from "../pages/StudentHome.vue";
import AdminUpload from "../pages/AdminUpload.vue";


const routes = [
  { path: "/", redirect: "/login" },
  { path: "/login", name: "login", component: Login, meta: { public: true } },
  { path: "/entry", name: "entry", component: Entry },
  { path: "/parsons/:videoId", name: "parsons", component: () => import("../pages/parsons.vue") },
  { path: "/posttest/parsons", name: "posttest_parsons", component: () => import("../pages/parsons.vue") },
  { path: "/learn/video/:videoId", name: "student-learning", component: () => import("../pages/StudentLearning.vue") },
  { path: "/quiz", name: "quiz", component: Quiz },
  { path: "/precheck", name: "precheck", component: PreCheck },
  { path: "/home", name: "home", component: StudentHome },
  { path: "/admin/upload", name: "adminUpload", component: AdminUpload },
  { path: "/admin/dashboard", name: "teacherDashboard", component: () => import("../pages/TeacherDashboard.vue") },
  { path: "/admin/agentlog", name: "teacherAgentLog", component: () => import("../pages/TeacherT5AgentLog.vue") },
  { path: "/admin/analyze", name: "teacherAnalyze", component: () => import("../pages/TeacherAnalyze.vue") },
  { path: "/admin/subtitle", name: "teacherSubtitle", component: () => import("../pages/TeacherSubtitles.vue") },
  { path: "/learn/:unit", name: "StudentLearning", component: () => import("../pages/StudentLearning.vue") },
  { path: "/:pathMatch(.*)*", redirect: "/login" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem("token");
  const role = localStorage.getItem("role") || "student";

  if (to.meta?.public) return next();
  if (!token) return next({ name: "login", query: { redirect: to.fullPath } });
  if (to.path.startsWith("/admin/") && !["teacher", "admin"].includes(role)) {
    return next({ name: "home" });
  }
  return next();
});

export default router;
export { router };
