import { initializeAgent } from "./js/agent.js";
import { initializeAnnotations } from "./js/annotations.js";
import {
  clearBrowserRecordState,
  configureStateNavigation,
  initializeDisplaySettings,
  initializeSessionNavigation,
  initializeSessionScrollState,
  initializeStartupRestoreSettings,
  initializeWorkflowMenus,
  viewState,
} from "./js/core.js";
import { initializeGrading } from "./js/grading.js";
import { initializeHome } from "./js/home.js";
import { initializeIdioms } from "./js/idioms.js";
import { initializeMistakes } from "./js/mistakes.js";
import {
  configureNavigation,
  fetchLibraryListUpdate,
  initializePartialNavigation,
  navigatePartial,
  playPartialViewTransition,
} from "./js/navigation.js";
import { initializePractice } from "./js/practice.js";
import { initializePoliticalQuiz } from "./js/political-quiz.js";
import {
  initializeFilters,
  initializeGlobalForms,
  initializeNativeDownloads,
  initializePageFind,
  initializeShellControls,
  initializeTabsAndPagination,
} from "./js/shell.js";

export function mountPage() {
  const isPartialMount = window.__gongkaoPartialMount === true;
  window.__gongkaoPartialMount = false;
  if (!isPartialMount) {
    // A fresh page load (first entry / app restart) always starts with the
    // detail filter panel collapsed; only the active same-route filter
    // session keeps it open.
    viewState.sessionRemove(`filter-panel-open:${window.location.pathname}`);
  }
  window.__gongkaoPageAbortController?.abort();
  (window.__gongkaoPageIntervals || []).forEach((timer) => window.clearInterval(timer));
  window.__gongkaoPageIntervals = [];
  const pageAbortController = new AbortController();
  const signal = pageAbortController.signal;
  window.__gongkaoPageAbortController = pageAbortController;

  viewState.section = document.body?.dataset.activeSection || "";
  initializeSessionNavigation();
  initializeStartupRestoreSettings(signal);
  initializeSessionScrollState(signal);
  viewState.initializeDisclosureState();
  initializeWorkflowMenus(signal);
  initializeDisplaySettings();

  const clearLocalRecordMarker = document.querySelector("[data-clear-local-record-state]");
  if (clearLocalRecordMarker) {
    clearBrowserRecordState();
    clearLocalRecordMarker.remove();
  }

  const runInitializer = (name, initialize) => {
    try {
      initialize();
    } catch (error) {
      console.error(`[研申] ${name} 初始化失败`, error);
    }
  };

  runInitializer("全局表单", () => initializeGlobalForms(signal));
  runInitializer("智能批改", () => initializeAgent(signal, navigatePartial));
  runInitializer("页面控件", () => initializeShellControls(signal));
  runInitializer("作答区", () => initializePractice(signal));
  runInitializer("文本标注", () => initializeAnnotations(signal));
  runInitializer("筛选", () => initializeFilters(signal, navigatePartial));
  runInitializer("批改报告", () => initializeGrading(signal, navigatePartial));
  runInitializer("材料与分页", () => initializeTabsAndPagination(signal, navigatePartial));
  runInitializer("文件导出", () => initializeNativeDownloads(signal));
  runInitializer("页内搜索", () => initializePageFind(signal));
  runInitializer("首页", () => initializeHome(signal));
  runInitializer("成语学习", () => initializeIdioms(signal));
  runInitializer("错题本", () => initializeMistakes(signal));
  runInitializer("时政测验", () => initializePoliticalQuiz(signal));
}

configureNavigation(mountPage);
configureStateNavigation((url, options = {}) => {
  const pathname = url.pathname;
  if (
    (pathname === "/" || pathname === "/papers")
    && pathname === window.location.pathname
  ) {
    // Restoring saved filter state only changes the list, so update it in
    // place instead of replaying the whole page entrance animation.
    fetchLibraryListUpdate(url);
    return;
  }
  navigatePartial(url, options);
});
document.addEventListener("DOMContentLoaded", () => {
  mountPage();
  initializePartialNavigation();
  playPartialViewTransition(document.querySelector(".main"));
}, { once: true });
