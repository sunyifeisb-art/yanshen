function selectedIssueKey() {
  return document.querySelector("[data-mistake-select].is-selected")?.dataset.mistakeSelect || "";
}

function showIssue(issueKey) {
  if (!issueKey) return;
  document.querySelectorAll("[data-mistake-select]").forEach((card) => {
    const active = card.dataset.mistakeSelect === issueKey;
    card.classList.toggle("is-selected", active);
    card.setAttribute("aria-pressed", active ? "true" : "false");
  });
  document.querySelectorAll("[data-mistake-detail]").forEach((panel) => {
    panel.hidden = panel.dataset.mistakeDetail !== issueKey;
  });
  const url = new URL(window.location.href);
  url.searchParams.set("selected", issueKey);
  window.history.replaceState(window.history.state, "", url.href);
}

async function updateIssue(issueKey, action, note = "") {
  const body = new URLSearchParams({ issue_key: issueKey, action });
  if (action === "note") body.set("note", note);
  const response = await fetch("/mistakes/update", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
      Accept: "application/json",
    },
    body: body.toString(),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || "错题状态保存失败");
  }
  return payload;
}

function applyIssueState(issueKey, payload) {
  const detail = Array.from(document.querySelectorAll("[data-mistake-detail]"))
    .find((panel) => panel.dataset.mistakeDetail === issueKey);
  const card = Array.from(document.querySelectorAll("[data-mistake-select]"))
    .find((candidate) => candidate.dataset.mistakeSelect === issueKey);
  if (!detail) return;

  const statePill = detail.querySelector("[data-mistake-state]");
  if (statePill) {
    statePill.textContent = payload.state_label || "待复盘";
    statePill.classList.remove("active", "reviewing", "mastered");
    statePill.classList.add(payload.state || "active");
  }
  const count = detail.querySelector("[data-mistake-review-count]");
  if (count) count.textContent = String(payload.review_count ?? 0);
  const star = detail.querySelector('[data-mistake-action="toggle_star"]');
  if (star) star.classList.toggle("is-starred", Boolean(payload.is_starred));

  const masteryButton = detail.querySelector('[data-mistake-action="mastered"], [data-mistake-action="restore"]');
  if (masteryButton) {
    if (payload.state === "mastered") {
      masteryButton.dataset.mistakeAction = "restore";
      masteryButton.textContent = "重新加入复盘";
    } else {
      masteryButton.dataset.mistakeAction = "mastered";
      masteryButton.textContent = "标记已掌握";
    }
  }
  card?.classList.toggle("is-mastered", payload.state === "mastered");
  const footState = card?.querySelector(".mistake-card-foot > span:first-child");
  if (footState) footState.textContent = payload.state_label || "待复盘";
}

async function copyText(text) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

export function initializeMistakes(signal) {
  const book = document.querySelector("[data-mistake-book]");
  if (!book) return;

  const cards = Array.from(book.querySelectorAll("[data-mistake-select]"));
  cards.forEach((card, index) => {
    card.setAttribute("aria-pressed", card.classList.contains("is-selected") ? "true" : "false");
    card.addEventListener("click", () => showIssue(card.dataset.mistakeSelect), { signal });
    card.addEventListener("keydown", (event) => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      const offset = event.key === "ArrowDown" ? 1 : -1;
      const next = cards[(index + offset + cards.length) % cards.length];
      next?.focus();
      if (next) showIssue(next.dataset.mistakeSelect);
    }, { signal });
  });

  book.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-mistake-action]");
    if (actionButton) {
      const detail = actionButton.closest("[data-mistake-detail]");
      const issueKey = detail?.dataset.mistakeDetail || selectedIssueKey();
      if (!issueKey || actionButton.disabled) return;
      const action = actionButton.dataset.mistakeAction;
      const oldText = actionButton.textContent;
      actionButton.disabled = true;
      try {
        const payload = await updateIssue(issueKey, action);
        applyIssueState(issueKey, payload);
      } catch (error) {
        actionButton.textContent = "保存失败";
        window.setTimeout(() => { actionButton.textContent = oldText; }, 1400);
      } finally {
        actionButton.disabled = false;
      }
      return;
    }

    const saveNote = event.target.closest("[data-mistake-save-note]");
    if (saveNote) {
      const detail = saveNote.closest("[data-mistake-detail]");
      const issueKey = detail?.dataset.mistakeDetail || "";
      const textarea = detail?.querySelector("[data-mistake-note]");
      const status = detail?.querySelector("[data-mistake-note-status]");
      if (!issueKey || !textarea) return;
      saveNote.disabled = true;
      if (status) status.textContent = "保存中…";
      try {
        await updateIssue(issueKey, "note", textarea.value);
        if (status) status.textContent = "已保存";
      } catch (error) {
        if (status) status.textContent = "保存失败，请重试";
      } finally {
        saveNote.disabled = false;
      }
      return;
    }

    const copyButton = event.target.closest("[data-copy-mistake-answer]");
    if (copyButton) {
      const detail = copyButton.closest("[data-mistake-detail]");
      const text = detail?.querySelector("[data-mistake-revised-text]")?.textContent?.trim() || "";
      if (!text) return;
      const oldText = copyButton.textContent;
      try {
        await copyText(text);
        copyButton.textContent = "已复制";
      } catch (error) {
        copyButton.textContent = "复制失败";
      }
      window.setTimeout(() => { copyButton.textContent = oldText; }, 1200);
    }
  }, { signal });

  const filter = book.querySelector(".mistake-filter-bar");
  filter?.querySelectorAll("select").forEach((select) => {
    select.addEventListener("change", () => filter.requestSubmit(), { signal });
  });

  const initial = new URL(window.location.href).searchParams.get("selected") || cards[0]?.dataset.mistakeSelect;
  if (initial && cards.some((card) => card.dataset.mistakeSelect === initial)) showIssue(initial);
}
