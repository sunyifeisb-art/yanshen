import { adaptivePageSize, rememberFilterState, showConfirmModal, submitForm, viewState } from "./core.js";
import { fetchLibraryListUpdate } from "./navigation.js";



export function initializeGlobalForms(signal) {
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (form.dataset.confirmed === "1") {
      return;
    }
    const confirmMessage = form.dataset.confirm;
    if (confirmMessage) {
      event.preventDefault();
      showConfirmModal(confirmMessage, () => {
        form.dataset.confirmed = "1";
        form.requestSubmit(event.submitter || undefined);
      });
      return;
    }
    const onsubmitAttr = form.getAttribute("onsubmit") || "";
    const match = onsubmitAttr.match(/confirm\(['"](.*?)['"]\)/);
    if (match) {
      event.preventDefault();
      const message = match[1];
      showConfirmModal(message, () => {
        form.dataset.confirmed = "1";
        form.requestSubmit(event.submitter || undefined);
      });
    }
  }, {
    signal,
    capture: true,
  });
}




export function initializeShellControls(signal) {
  const shell = document.querySelector(".shell");
  const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
  if (shell && sidebarToggle) {
    const storageKey = "gongkao.sidebarMode";

    const storedMode = () => {
      try {
        return viewState.persistentGet(storageKey) === "top" ? "top" : "side";
      } catch (error) {
        return "side";
      }
    };

    const saveMode = (mode) => {
      try {
        viewState.persistentSet(storageKey, mode);
      } catch (error) {
        // Sidebar placement is a preference; failing to persist should not break the page.
      }
    };

    const isDesktopSidebar = () => window.matchMedia("(min-width: 1121px)").matches;

    const applySidebarMode = (mode, persist = false) => {
      const topMode = mode === "top";
      shell.classList.toggle("sidebar-top", topMode && isDesktopSidebar());
      const label = topMode ? "Switch navigation to left" : "Switch navigation to top";
      sidebarToggle.setAttribute("aria-label", label);
      sidebarToggle.setAttribute("title", label);
      if (persist) saveMode(topMode ? "top" : "side");
    };

    const syncSidebarMode = () => {
      if (isDesktopSidebar()) {
        applySidebarMode(storedMode());
      } else {
        shell.classList.remove("sidebar-top");
      }
    };

    sidebarToggle.addEventListener("click", () => {
      if (!isDesktopSidebar()) return;
      applySidebarMode(shell.classList.contains("sidebar-top") ? "side" : "top", true);
    });

    syncSidebarMode();
    window.addEventListener("resize", syncSidebarMode, { signal: signal });
  }

  document.querySelectorAll("[data-filter-panel-toggle]").forEach((toggle) => {
    const panelId = toggle.getAttribute("aria-controls");
    const panel = panelId ? document.getElementById(panelId) : null;
    if (!panel) return;
    const openStateKey = `filter-panel-open:${window.location.pathname}`;
    const syncFilterPanel = (expanded) => {
      panel.hidden = !expanded;
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      toggle.classList.toggle("is-expanded", expanded);
    };
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") !== "true";
      syncFilterPanel(expanded);
      viewState.sessionSet(openStateKey, expanded ? "1" : "0");
    });
    const sessionOpen = viewState.sessionGet(openStateKey);
    if (sessionOpen === "1") {
      syncFilterPanel(true);
    } else {
      syncFilterPanel(!panel.hidden);
    }
  });

  // Actively opened filter panels stay open across same-route refreshes, but
  // collapse again when the user leaves the page (or restarts the app).
  window.addEventListener("gongkao:before-navigation", (event) => {
    const detail = event?.detail;
    const nextPathname = detail?.url ? new URL(detail.url, window.location.href).pathname : null;
    if (nextPathname && nextPathname !== window.location.pathname) {
      viewState.sessionRemove(`filter-panel-open:${window.location.pathname}`);
    }
  }, signal ? { signal } : undefined);

  document.querySelectorAll("[data-year-range]").forEach((range) => {
    const minYear = Number(range.dataset.minYear || 2020);
    const maxYear = Number(range.dataset.maxYear || new Date().getFullYear());
    const fromSlider = range.querySelector("[data-year-from]");
    const toSlider = range.querySelector("[data-year-to]");
    const fromHidden = range.querySelector("[data-year-from-hidden]");
    const toHidden = range.querySelector("[data-year-to-hidden]");
    const label = range.querySelector("[data-year-range-label]");
    if (!fromSlider || !toSlider || !fromHidden || !toHidden || !label) return;

    const clamp = (value) => Math.min(Math.max(Number(value) || minYear, minYear), maxYear);
    const syncYears = (changed) => {
      let from = clamp(fromSlider.value);
      let to = clamp(toSlider.value);
      if (from > to) {
        if (changed === "from") {
          to = from;
          toSlider.value = String(to);
        } else {
          from = to;
          fromSlider.value = String(from);
        }
      }
      fromHidden.value = String(from);
      toHidden.value = to >= maxYear ? "" : String(to);
      label.textContent = `${from} - ${to}`;
      const lower = ((from - minYear) / (maxYear - minYear || 1)) * 100;
      const upper = ((to - minYear) / (maxYear - minYear || 1)) * 100;
      range.style.setProperty("--range-left", `${lower}%`);
      range.style.setProperty("--range-right", `${100 - upper}%`);
    };

    fromSlider.addEventListener("input", () => syncYears("from"));
    toSlider.addEventListener("input", () => syncYears("to"));
    syncYears();
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => syncYears()).observe(range);
    }
  });

  document.querySelectorAll("[data-file-input]").forEach((input) => {
    const label = input.closest("label");
    const name = label ? label.querySelector("[data-file-name]") : null;
    input.addEventListener("change", () => {
      if (name) {
        name.textContent = input.files && input.files.length ? input.files[0].name : "未选择文件";
      }
    });
  });

}


function showNativeDownloadToast(message) {
  document.querySelector("[data-native-download-toast]")?.remove();
  const toast = document.createElement("div");
  toast.className = "native-download-toast";
  toast.dataset.nativeDownloadToast = "1";
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function nativeDownloadApi() {
  const api = window.pywebview?.api;
  return api && typeof api.download_file === "function" ? api : null;
}

function waitForNativeDownloadApi(timeoutMs = 1800) {
  const ready = nativeDownloadApi();
  if (ready) return Promise.resolve(ready);

  return new Promise((resolve) => {
    let settled = false;
    let timer = 0;
    const finish = (api = null) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener("pywebviewready", onReady);
      resolve(api);
    };
    const onReady = () => finish(nativeDownloadApi());
    window.addEventListener("pywebviewready", onReady, { once: true });
    timer = window.setTimeout(() => finish(nativeDownloadApi()), timeoutMs);
  });
}

export function initializeNativeDownloads(signal) {
  document.querySelectorAll("[data-native-download]").forEach((link) => {
    link.addEventListener("click", async (event) => {
      // WKWebView treats a PDF href as a normal navigation even when the
      // anchor has `download`, which replaces the app page with a blank PDF
      // view. Always keep the current page in place and hand the request to
      // the native host once pywebview finishes injecting its API.
      event.preventDefault();
      if (link.getAttribute("aria-busy") === "true") return;
      const href = link.getAttribute("href") || "";
      const originalText = link.textContent;
      const label = link.dataset.downloadLabel || "PDF";
      link.setAttribute("aria-busy", "true");
      link.classList.add("is-loading");
      link.textContent = "生成中…";
      try {
        const api = await waitForNativeDownloadApi();
        if (!api) {
          showNativeDownloadToast("导出接口尚未就绪，请重试");
          return;
        }
        const result = await api.download_file(href);
        showNativeDownloadToast(
          result?.ok ? `${label} 已导出：${result.path}` : `导出失败：${result?.error || "未知错误"}`,
        );
      } catch (error) {
        showNativeDownloadToast(`导出失败：${error?.message || error}`);
      } finally {
        link.removeAttribute("aria-busy");
        link.classList.remove("is-loading");
        link.textContent = originalText;
      }
    }, { signal });
  });
}

function pageFindTextIndex(root) {
  const entries = [];
  let text = "";
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
      const parent = node.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (parent.closest(
        ".page-find-bar, .sidebar, button, input, select, textarea, label, script, style, "
        + "[role='button'], [role='tab'], [role='menuitem'], [role='navigation']",
      )) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node = walker.nextNode();
  while (node) {
    const start = text.length;
    text += node.nodeValue;
    entries.push({ node, start, end: text.length });
    node = walker.nextNode();
  }
  return { text, entries };
}

function pageFindRange(index, length, entries) {
  const endIndex = index + length;
  const startEntry = entries.find((entry) => index >= entry.start && index < entry.end);
  const endEntry = entries.find((entry) => endIndex > entry.start && endIndex <= entry.end);
  if (!startEntry || !endEntry) return null;
  const range = document.createRange();
  range.setStart(startEntry.node, index - startEntry.start);
  range.setEnd(endEntry.node, endIndex - endEntry.start);
  return range;
}

function revealPageFindRange(range) {
  const startElement = range.startContainer.parentElement;
  if (!startElement) return;

  let details = startElement.closest("details");
  while (details) {
    details.open = true;
    details = details.parentElement?.closest("details") || null;
  }

  const panel = startElement.closest(".tab-panel");
  if (panel?.hidden && panel.id) {
    const tabset = panel.closest("[data-tabs]");
    tabset?.querySelector(`[data-tab-target="${panel.id}"]`)?.click();
  }

  startElement.scrollIntoView?.({ behavior: "smooth", block: "center", inline: "nearest" });
}

export function initializePageFind(signal) {
  const root = document.querySelector(".main");
  if (!root) return;

  const bar = document.createElement("div");
  bar.className = "page-find-bar";
  bar.dataset.pageFind = "1";
  bar.hidden = true;
  bar.innerHTML = `
    <label class="page-find-input-wrap">
      <span class="sr-only">查找本页文字</span>
      <input type="search" data-page-find-input autocomplete="off" spellcheck="false" placeholder="查找本页文字…" aria-label="查找本页文字">
    </label>
    <span class="page-find-count" data-page-find-count aria-live="polite">0 / 0</span>
    <button type="button" class="page-find-step" data-page-find-prev aria-label="上一个匹配" title="上一个匹配（Shift+Enter / ⌘⇧G）">↑</button>
    <button type="button" class="page-find-step" data-page-find-next aria-label="下一个匹配" title="下一个匹配（Enter / ⌘G）">↓</button>
    <button type="button" class="page-find-close" data-page-find-close aria-label="关闭查找" title="关闭（Esc）">×</button>
  `;
  document.body.append(bar);

  const input = bar.querySelector("[data-page-find-input]");
  const count = bar.querySelector("[data-page-find-count]");
  const previous = bar.querySelector("[data-page-find-prev]");
  const next = bar.querySelector("[data-page-find-next]");
  const close = bar.querySelector("[data-page-find-close]");
  let matches = [];
  let current = -1;
  let currentRange = null;

  const customHighlightsAvailable = Boolean(window.CSS?.highlights && window.Highlight);
  const clearHighlights = () => {
    if (customHighlightsAvailable) {
      window.CSS.highlights.delete("page-find-all");
      window.CSS.highlights.delete("page-find-current");
    } else if (currentRange) {
      const selection = window.getSelection();
      if (selection?.rangeCount) selection.removeAllRanges();
    }
    currentRange = null;
  };

  const showCurrent = () => {
    clearHighlights();
    if (!matches.length || current < 0) {
      count.textContent = "0 / 0";
      previous.disabled = true;
      next.disabled = true;
      return;
    }
    previous.disabled = false;
    next.disabled = false;
    current = (current + matches.length) % matches.length;
    currentRange = matches[current];
    count.textContent = `${current + 1} / ${matches.length}`;
    revealPageFindRange(currentRange);
    if (customHighlightsAvailable) {
      window.CSS.highlights.set("page-find-all", new window.Highlight(...matches));
      window.CSS.highlights.set("page-find-current", new window.Highlight(currentRange));
    } else {
      const selection = window.getSelection();
      selection?.removeAllRanges();
      selection?.addRange(currentRange);
    }
  };

  const refreshMatches = () => {
    clearHighlights();
    matches = [];
    current = -1;
    const query = input.value;
    if (!query) {
      count.textContent = "0 / 0";
      previous.disabled = true;
      next.disabled = true;
      return;
    }
    const index = pageFindTextIndex(root);
    const haystack = index.text.toLocaleLowerCase();
    const needle = query.toLocaleLowerCase();
    let offset = 0;
    while (offset <= haystack.length - needle.length) {
      const found = haystack.indexOf(needle, offset);
      if (found < 0) break;
      const range = pageFindRange(found, query.length, index.entries);
      if (range) matches.push(range);
      offset = found + Math.max(1, needle.length);
    }
    if (matches.length) current = 0;
    showCurrent();
  };

  const move = (delta) => {
    if (!input.value) return;
    if (!matches.length) {
      refreshMatches();
      return;
    }
    current = (current + delta + matches.length) % matches.length;
    showCurrent();
  };

  const openFind = () => {
    bar.hidden = false;
    refreshMatches();
    input.focus({ preventScroll: true });
    input.select();
  };

  const closeFind = () => {
    clearHighlights();
    matches = [];
    current = -1;
    bar.hidden = true;
  };

  input.addEventListener("input", refreshMatches, { signal });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      move(event.shiftKey ? -1 : 1);
    }
  }, { signal });
  previous.addEventListener("click", () => move(-1), { signal });
  next.addEventListener("click", () => move(1), { signal });
  close.addEventListener("click", closeFind, { signal });

  document.addEventListener("keydown", (event) => {
    const key = event.key.toLocaleLowerCase();
    const command = event.metaKey || event.ctrlKey;
    if (command && !event.altKey && key === "f") {
      event.preventDefault();
      openFind();
      return;
    }
    if (command && !event.altKey && key === "g") {
      if (!input.value) return;
      event.preventDefault();
      if (bar.hidden) bar.hidden = false;
      move(event.shiftKey ? -1 : 1);
      input.focus({ preventScroll: true });
      return;
    }
    if (event.key === "Escape" && !bar.hidden) {
      event.preventDefault();
      closeFind();
    }
  }, { signal, capture: true });

  signal.addEventListener("abort", () => {
    clearHighlights();
    bar.remove();
  }, { once: true });
}


export function initializeFilters(_signal, navigatePartial) {
  document.querySelectorAll("form.auto-filter").forEach((form) => {
    form.querySelector("[data-filter-reset]")?.addEventListener("click", () => {
      viewState.clearFilterState(form);
    });
    const autoApply = () => {
      rememberFilterState(form);
      const url = new URL(form.action || window.location.href, window.location.href);
      url.search = new URLSearchParams(new FormData(form)).toString();
      if (
        url.pathname === window.location.pathname
        && (url.pathname === "/" || url.pathname === "/papers")
      ) {
        fetchLibraryListUpdate(url);
      } else if (navigatePartial) {
        navigatePartial(url);
      } else {
        submitForm(form);
      }
    };
    // Filtering stays automatic (selecting a condition immediately updates the
    // list), but only the list region changes in place - the detail panel and
    // the rest of the page are never rebuilt or hidden.
    form.querySelectorAll("select").forEach((field) => {
      field.addEventListener("change", autoApply);
    });
    let timer = 0;
    form.querySelectorAll("input").forEach((field) => {
      field.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(autoApply, 400);
      });
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      autoApply();
    });
  });
}




export function initializeTabsAndPagination(signal, _navigatePartial) {
  const materialFontStorageKey = "gongkao.materialFontSize:v1";
  const materialFontMin = 14;
  const materialFontMax = 24;
  const materialFontStep = 2;
  let savedMaterialFontSize = 16;
  try {
    const stored = Number(window.localStorage.getItem(materialFontStorageKey));
    if (Number.isFinite(stored) && stored >= materialFontMin && stored <= materialFontMax) {
      savedMaterialFontSize = stored;
    }
  } catch (_error) {
    // localStorage can be unavailable in restricted webviews; keep the default.
  }

  document.querySelectorAll("[data-material-font-controls]").forEach((controls) => {
    const tabset = controls.closest("[data-tabs]");
    const down = controls.querySelector("[data-material-font-size-down]");
    const up = controls.querySelector("[data-material-font-size-up]");
    const value = controls.querySelector("[data-material-font-size-value]");
    if (!tabset || !down || !up || !value) return;

    let currentSize = savedMaterialFontSize;
    const applyMaterialFontSize = (nextSize, persist = true) => {
      currentSize = Math.max(materialFontMin, Math.min(materialFontMax, nextSize));
      tabset.style.setProperty("--material-font-size", `${currentSize}px`);
      value.textContent = String(currentSize);
      down.disabled = currentSize <= materialFontMin;
      up.disabled = currentSize >= materialFontMax;
      if (!persist) return;
      try {
        window.localStorage.setItem(materialFontStorageKey, String(currentSize));
      } catch (_error) {
        // The size still applies for the current page when storage is unavailable.
      }
    };

    applyMaterialFontSize(currentSize, false);
    // Preserve an active text selection on a normal mouse press, but always keep
    // click as a fallback. WKWebView/AX activation can emit click without first
    // delivering mousedown; filtering click by event.detail made those presses a
    // no-op. A per-button flag only suppresses the click that follows a handled
    // mousedown, so both native pointer and accessibility activation work.
    const bindMaterialFontButton = (button, delta) => {
      let suppressFollowingClick = false;
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
        suppressFollowingClick = true;
        applyMaterialFontSize(currentSize + delta);
      }, { signal });
      button.addEventListener("click", () => {
        if (suppressFollowingClick) {
          suppressFollowingClick = false;
          return;
        }
        applyMaterialFontSize(currentSize + delta);
      }, { signal });
    };
    bindMaterialFontButton(down, -materialFontStep);
    bindMaterialFontButton(up, materialFontStep);
  });

  document.querySelectorAll("[data-tabs]").forEach((tabset) => {
    const tabs = Array.from(tabset.querySelectorAll("[role='tab']"));
    const activate = (tab) => {
      tabs.forEach((candidate) => {
        const selected = candidate === tab;
        candidate.classList.toggle("active-tab", selected);
        candidate.setAttribute("aria-selected", selected ? "true" : "false");
        const panel = document.getElementById(candidate.dataset.tabTarget);
        if (panel) {
          panel.hidden = !selected;
          panel.classList.toggle("active-panel", selected);
        }
      });
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activate(tab));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        let nextIndex = index;
        if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
        if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
        if (event.key === "Home") nextIndex = 0;
        if (event.key === "End") nextIndex = tabs.length - 1;
        tabs[nextIndex].focus();
        activate(tabs[nextIndex]);
      });
    });
  });

  if (window.location.hash) {
    const hash = window.location.hash;
    let targetPanel = null;
    if (hash.startsWith("#material-")) {
      const num = hash.substring("#material-".length);
      targetPanel = document.querySelector(`[data-material-number="${num}"]`)?.closest('.tab-panel');
    } else if (hash.startsWith("#reference-")) {
      const idx = parseInt(hash.substring("#reference-".length)) - 1;
      targetPanel = document.querySelector(`[id$="-reference-panel-${idx}"]`);
    } else {
      targetPanel = document.getElementById(hash.substring(1));
    }

    if (targetPanel) {
      const tabset = targetPanel.closest('[data-tabs]');
      if (tabset) {
        const tabTargetId = targetPanel.id;
        const tab = tabset.querySelector(`[data-tab-target="${tabTargetId}"]`);
        if (tab) {
          setTimeout(() => {
            tab.click();
            const details = tab.closest('details');
            if (details) details.open = true;
            tab.scrollIntoView({ behavior: "smooth", block: "center" });
          }, 100);
        }
      }
    }
  }

  const paginatedGrid = document.querySelector("[data-adaptive-pagination]");
  if (paginatedGrid) {
    let resizeTimer = 0;
    const syncPagination = () => {
      const grid = document.querySelector("[data-adaptive-pagination]");
      if (!grid) return;
      const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").filter(Boolean).length;
      const desiredSize = adaptivePageSize(columns);
      const currentSize = Number(grid.dataset.pageSize || 0);
      if (!desiredSize || desiredSize === currentSize) return;

      const currentPage = Number(grid.dataset.page || 1);
      const firstItemIndex = Math.max(0, (currentPage - 1) * currentSize);
      const nextPage = Math.floor(firstItemIndex / desiredSize) + 1;
      const url = new URL(window.location.href);
      url.searchParams.set("per_page", String(desiredSize));
      if (nextPage > 1) {
        url.searchParams.set("page", String(nextPage));
      } else {
        url.searchParams.delete("page");
      }
      fetchLibraryListUpdate(url);
    };

    syncPagination();
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(syncPagination, 250);
    }, { signal: signal });
  }
}
