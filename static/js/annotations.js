import { clampNumber, showConfirmModal, showUndoToast, viewState } from "./core.js";

async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (_) { /* fall through to legacy path */ }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    return ok;
  } catch (_) {
    return false;
  }
}

function showCopyToast(message) {
  document.querySelector("[data-copy-toast]")?.remove();
  const toast = document.createElement("div");
  toast.className = "copy-toast";
  toast.dataset.copyToast = "1";
  toast.setAttribute("role", "status");
  toast.textContent = message;
  document.body.append(toast);
  window.setTimeout(() => toast.remove(), 1500);
}

function floatingToolbarPosition(anchorRect, toolbarRect, readingGap = 12) {
  const margin = 8;
  const sideGap = 12;
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const maxTop = Math.max(margin, viewportHeight - toolbarRect.height - margin);
  const centeredTop = clampNumber(
    anchorRect.top + (anchorRect.height / 2) - (toolbarRect.height / 2),
    margin,
    maxTop,
  );

  // Prefer the horizontal gutter. This keeps the toolbar completely off the
  // reading line instead of merely moving it to another line of material.
  const rightLeft = anchorRect.right + sideGap;
  if (rightLeft + toolbarRect.width <= viewportWidth - margin) {
    return { top: centeredTop, left: rightLeft, placement: "right" };
  }
  const leftLeft = anchorRect.left - toolbarRect.width - sideGap;
  if (leftLeft >= margin) {
    return { top: centeredTop, left: leftLeft, placement: "left" };
  }

  const topCandidate = anchorRect.top - toolbarRect.height - readingGap;
  const bottomCandidate = anchorRect.bottom + readingGap;
  const top = topCandidate >= margin
    ? topCandidate
    : clampNumber(bottomCandidate, margin, maxTop);
  const left = clampNumber(
    anchorRect.left + (anchorRect.width / 2) - (toolbarRect.width / 2),
    margin,
    Math.max(margin, viewportWidth - toolbarRect.width - margin),
  );
  return { top, left, placement: topCandidate >= margin ? "above" : "below" };
}

export function editableValue(element) {
  if (!element) return "";
  if ("value" in element) return element.value;
  if (
    element.isContentEditable
    || element.hasAttribute?.("contenteditable")
    || element.matches?.("[data-material-highlight], [data-text-annotation]")
  ) {
    return editablePlainText(element);
  }
  return element.textContent || "";
}

export function editablePlainText(element) {
  const blockTags = new Set(["DIV", "P", "LI", "SECTION", "ARTICLE", "HEADER", "FOOTER"]);
  const parts = [];
  const endsWithNewline = () => parts.length > 0 && parts[parts.length - 1].text.endsWith("\n");
  const pushText = (value) => {
    if (value) parts.push({ text: value.replace(/\r\n/g, "\n").replace(/\r/g, "\n"), synthetic: false });
  };
  const pushLineBreak = (synthetic = false) => {
    parts.push({ text: "\n", synthetic });
  };
  const pushSyntheticNewline = () => {
    if (parts.length && !endsWithNewline()) pushLineBreak(true);
  };
  const walk = (node) => {
    node.childNodes.forEach((child, index) => {
      if (child.nodeType === Node.TEXT_NODE) {
        pushText(child.nodeValue || "");
        return;
      }
      if (child.nodeType !== Node.ELEMENT_NODE) return;
      if (child.matches("[data-editor-decoration]")) return;
      const tagName = child.tagName;
      if (tagName === "BR") {
        pushLineBreak(false);
        return;
      }
      const isBlock = blockTags.has(tagName);
      if (isBlock && index > 0) {
        pushSyntheticNewline();
      }
      const lengthBeforeChildren = parts.length;
      walk(child);
      if (isBlock && parts.length === lengthBeforeChildren) {
        pushSyntheticNewline();
      }
    });
  };
  const editorLines = Array.from(element.children || []);
  if (
    editorLines.length
    && editorLines.length === element.childNodes.length
    && editorLines.every((line) => line.matches("[data-editor-line]"))
  ) {
    editorLines.forEach((line, index) => {
      if (index) pushLineBreak(false);
      const meaningfulChildren = Array.from(line.childNodes).filter((child) => (
        child.nodeType !== Node.ELEMENT_NODE
        || !child.matches("[data-editor-decoration]")
      ));
      const isBrowserBlankLine = (
        meaningfulChildren.length === 1
        && meaningfulChildren[0].nodeType === Node.ELEMENT_NODE
        && meaningfulChildren[0].tagName === "BR"
      );
      // Chromium keeps a lone <br> inside an empty contenteditable line as a
      // caret placeholder. The line boundary above already represents its
      // newline, so treating that placeholder as content creates a second
      // blank line whenever an annotation is rendered.
      if (!isBrowserBlankLine) walk(line);
    });
    return parts.map((part) => part.text).join("");
  }
  walk(element);
  while (parts.length && parts[parts.length - 1].synthetic) {
    parts.pop();
  }
  return parts.map((part) => part.text).join("");
}

export function renderEditorParagraphs(container) {
  const alignments = container.__paragraphAlignments || [];
  if (!container.childNodes.length) {
    container.replaceChildren();
    return;
  }
  const lines = [document.createDocumentFragment()];
  const nextLine = () => lines.push(document.createDocumentFragment());

  Array.from(container.childNodes).forEach((node) => {
    if (node.nodeType !== Node.TEXT_NODE) {
      lines[lines.length - 1].append(node);
      return;
    }
    const parts = String(node.nodeValue || "").split("\n");
    parts.forEach((part, index) => {
      if (part) lines[lines.length - 1].append(document.createTextNode(part));
      if (index < parts.length - 1) nextLine();
    });
  });

  container.replaceChildren();
  lines.forEach((content, index) => {
    const line = document.createElement("div");
    line.dataset.editorLine = "";
    const align = alignments[index] || "left";
    line.style.textAlign = align === "left" ? "" : align;
    line.append(content);
    container.append(line);
  });
}

export function setEditableValue(element, value) {
  if (!element) return;
  if ("value" in element) {
    element.value = value;
  } else {
    element.textContent = value;
  }
}

export function bindPlainTextPaste(element) {
  if (!element || "value" in element) return;
  element.addEventListener("paste", (event) => {
    event.preventDefault();
    const text = event.clipboardData?.getData("text/plain") || "";
    document.execCommand("insertText", false, text);
  });
}

export function insertEditorText(element, text) {
  if ("value" in element) {
    const start = element.selectionStart ?? element.value.length;
    const end = element.selectionEnd ?? start;
    element.setRangeText(text, start, end, "end");
    element.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: text,
    }));
    return;
  }

  element.focus();
  if (document.execCommand("insertText", false, text)) return;

  const selection = window.getSelection();
  if (!selection || !selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  element.dispatchEvent(new InputEvent("input", {
    bubbles: true,
    inputType: "insertText",
    data: text,
  }));
}

export function bindChineseAnswerPunctuation(element) {
  if (!element || element.dataset.chinesePunctuationBound === "1") return;
  element.dataset.chinesePunctuationBound = "1";
  element.addEventListener("beforeinput", (event) => {
    if (event.isComposing || event.inputType !== "insertText") return;
    let replacement = "";
    if (event.data === "\\") replacement = "、";
    if (event.data === ",") replacement = "，";
    if (event.data === " ") replacement = "　";
    if (!replacement) return;
    event.preventDefault();
    insertEditorText(element, replacement);
  });
}

export function bindPlainTextEditing(element) {
  if (!element || "value" in element) return;
  bindPlainTextPaste(element);
  element.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    document.execCommand("insertText", false, "\n");
  });
}

export const materialHighlightColors = ["yellow", "orange", "pink", "purple", "blue", "green"];
export const textAnnotationStoragePrefix = "gongkao.textAnnotations:v1";
export const textAnnotationStyles = ["strike", "underline"];
export const annotationSaveTimers = new WeakMap();
export const annotationSaveVersions = new WeakMap();
export const annotationStates = new WeakMap();
export const annotationTextSnapshots = new WeakMap();

export function materialHighlightKey(container) {
  return `gongkao.materialHighlights:v1:${container.dataset.highlightScope}:material:${container.dataset.materialId}`;
}

export function legacyMaterialHighlightKey(container) {
  return `gongkao.materialHighlights:v1:question-${container.dataset.questionId}:material:${container.dataset.materialId}`;
}

export function textAnnotationKey(container) {
  if (container.hasAttribute("data-material-highlight")) {
    return materialHighlightKey(container);
  }
  return [
    textAnnotationStoragePrefix,
    container.dataset.highlightScope || "global",
    container.dataset.annotationType || "text",
    container.dataset.annotationId || "default",
  ].join(":");
}

export function parseTextAnnotations(raw) {
  try {
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => ({
        start: Number(item.start),
        end: Number(item.end),
        color: String(item.color || ""),
        style: String(item.style || ""),
        note: String(item.note || ""),
        quote: String(item.quote || ""),
        prefix: String(item.prefix || ""),
        suffix: String(item.suffix || ""),
        anchorVersion: Number(item.anchor_version || item.anchorVersion || 0),
      }))
      .filter((item) => (
        Number.isInteger(item.start)
        && Number.isInteger(item.end)
        && item.start >= 0
        && item.end > item.start
        && (materialHighlightColors.includes(item.color) || textAnnotationStyles.includes(item.style))
      ));
  } catch (error) {
    return [];
  }
}

export function localTextAnnotations(container) {
  try {
    const raw = viewState.persistentGet(textAnnotationKey(container));
    if (raw !== null) return parseTextAnnotations(raw);
    if (container.hasAttribute("data-material-highlight") && container.dataset.questionId) {
      // Retain an unsaved mark from the old per-question cache during upgrade.
      if (serverTextAnnotations(container).length) return null;
      const legacy = viewState.persistentGet(legacyMaterialHighlightKey(container));
      if (legacy !== null) return parseTextAnnotations(legacy);
    }
    return null;
  } catch (error) {
    return null;
  }
}

export function textFingerprint(text) {
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${text.length}:${(hash >>> 0).toString(16)}`;
}

export function serverTextAnnotations(container) {
  return parseTextAnnotations(container.dataset.savedAnnotations || "[]");
}

export function readTextAnnotations(container) {
  if (annotationStates.has(container)) return annotationStates.get(container) || [];
  const local = localTextAnnotations(container);
  const resolved = resolveTextAnnotations(
    local === null ? serverTextAnnotations(container) : local,
    editableValue(container),
  );
  annotationStates.set(container, resolved);
  return resolved;
}

export function annotationContext(text, start, end) {
  return {
    quote: text.slice(start, end),
    prefix: text.slice(Math.max(0, start - 32), start),
    suffix: text.slice(end, Math.min(text.length, end + 32)),
    anchor_version: 1,
  };
}

export function anchorTextAnnotations(highlights, text) {
  return normalizeTextAnnotations(highlights, text.length).map((item) => ({
    start: item.start,
    end: item.end,
    color: item.color || "",
    style: item.style || "",
    note: item.note || "",
    ...annotationContext(text, item.start, item.end),
  }));
}

export function anchoredTextRange(item, text) {
  const quote = String(item.quote || "");
  if (!quote) return null;
  const candidates = [];
  let offset = text.indexOf(quote);
  while (offset >= 0) {
    const prefix = String(item.prefix || "");
    const suffix = String(item.suffix || "");
    let score = 0;
    if (prefix && text.slice(Math.max(0, offset - prefix.length), offset) === prefix) score += 4;
    if (suffix && text.slice(offset + quote.length, offset + quote.length + suffix.length) === suffix) score += 4;
    score -= Math.min(3, Math.abs(offset - Number(item.start || 0)) / 1000);
    candidates.push({ start: offset, end: offset + quote.length, score });
    offset = text.indexOf(quote, offset + 1);
  }
  if (!candidates.length) return null;
  candidates.sort((a, b) => b.score - a.score || Math.abs(a.start - item.start) - Math.abs(b.start - item.start));
  return candidates[0];
}

export function resolveTextAnnotations(highlights, text) {
  const resolved = parseTextAnnotations(JSON.stringify(highlights)).map((item) => {
    const anchored = anchoredTextRange(item, text);
    if (anchored) return { ...item, start: anchored.start, end: anchored.end };
    return item;
  });
  return anchorTextAnnotations(resolved, text);
}

export function annotationSavePayload(container, highlights) {
  const targetType = container.dataset.annotationTarget;
  if (!targetType || !container.dataset.annotationSaveUrl) return null;
  return {
    target_type: targetType,
    question_id: Number(container.dataset.questionId) || null,
    material_number: Number(container.dataset.materialNumber) || null,
    attempt_id: Number(container.dataset.attemptId) || null,
    text_hash: textFingerprint(editableValue(container)),
    annotations: highlights,
  };
}

export function persistTextAnnotations(container, highlights) {
  const payload = annotationSavePayload(container, highlights);
  if (!payload) return;
  const serialized = JSON.stringify(highlights);
  const version = (annotationSaveVersions.get(container) || 0) + 1;
  annotationSaveVersions.set(container, version);
  window.clearTimeout(annotationSaveTimers.get(container));
  const timer = window.setTimeout(async () => {
    try {
      const response = await fetch(container.dataset.annotationSaveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok || annotationSaveVersions.get(container) !== version) return;
      container.dataset.savedAnnotations = serialized;
      container.dataset.savedTextHash = payload.text_hash;
      try {
        if (viewState.persistentGet(textAnnotationKey(container)) === serialized) {
          viewState.persistentRemove(textAnnotationKey(container));
        }
      } catch (error) {
        // The database save succeeded; inaccessible browser storage can be ignored.
      }
    } catch (error) {
      // Keep the local copy so a later page load can retry the database save.
    }
  }, 250);
  annotationSaveTimers.set(container, timer);
}

export function writeTextAnnotations(container, highlights) {
  const anchored = anchorTextAnnotations(highlights, editableValue(container));
  annotationStates.set(container, anchored);
  const key = textAnnotationKey(container);
  const serialized = JSON.stringify(anchored);
  try {
    if (container.dataset.annotationSaveUrl) {
      viewState.persistentSet(key, serialized);
      if (container.hasAttribute("data-material-highlight") && container.dataset.questionId) {
        viewState.persistentRemove(legacyMaterialHighlightKey(container));
      }
    } else if (!anchored.length) {
      viewState.persistentRemove(key);
    } else {
      viewState.persistentSet(key, serialized);
    }
  } catch (error) {
    // Database persistence below remains available when browser storage is unavailable.
  }
  persistTextAnnotations(container, anchored);
  return anchored;
}

export function normalizeTextAnnotations(highlights, textLength) {
  const normalized = highlights
    .map((item) => ({
      start: clampNumber(Math.round(item.start), 0, textLength),
      end: clampNumber(Math.round(item.end), 0, textLength),
      color: materialHighlightColors.includes(item.color) ? item.color : "",
      style: textAnnotationStyles.includes(item.style) ? item.style : "",
      note: String(item.note || ""),
      quote: String(item.quote || ""),
      prefix: String(item.prefix || ""),
      suffix: String(item.suffix || ""),
      anchor_version: Number(item.anchor_version || item.anchorVersion || 0),
    }))
    .filter((item) => item.end > item.start && (item.color || item.style))
    .sort((a, b) => a.start - b.start || a.end - b.end);
  const merged = [];
  normalized.forEach((item) => {
    const previous = merged[merged.length - 1];
    if (
      previous
      && previous.color === item.color
      && previous.style === item.style
      && previous.note === item.note
      && item.start <= previous.end
    ) {
      previous.end = Math.max(previous.end, item.end);
    } else {
      merged.push({ ...item });
    }
  });
  return merged;
}

export function splitAnnotationItem(item, start, end, patch) {
  const next = [];
  if (item.start < start) {
    next.push({ ...item, end: start });
  }
  const overlapStart = Math.max(item.start, start);
  const overlapEnd = Math.min(item.end, end);
  if (overlapEnd > overlapStart && patch) {
    const patched = { ...item, start: overlapStart, end: overlapEnd };
    if (Object.prototype.hasOwnProperty.call(patch, "color")) patched.color = patch.color;
    if (Object.prototype.hasOwnProperty.call(patch, "style")) patched.style = patch.style;
    if (Object.prototype.hasOwnProperty.call(patch, "note")) patched.note = patch.note;
    if (patched.color || patched.style) next.push(patched);
  }
  if (item.end > end) {
    next.push({ ...item, start: end });
  }
  return next;
}

export function applyTextAnnotation(highlights, start, end, patch, textLength) {
  const current = normalizeTextAnnotations(highlights, textLength);
  const next = [];
  const covered = [];
  current.forEach((item) => {
    if (item.end <= start || item.start >= end) {
      next.push(item);
      return;
    }
    covered.push([Math.max(item.start, start), Math.min(item.end, end)]);
    next.push(...splitAnnotationItem(item, start, end, patch));
  });
  if (patch) {
    let cursor = start;
    covered.sort((a, b) => a[0] - b[0]).forEach((range) => {
      if (range[0] > cursor) {
        next.push({
          start: cursor,
          end: range[0],
          color: patch.color || "",
          style: patch.style || "",
          note: patch.note || ""
        });
      }
      cursor = Math.max(cursor, range[1]);
    });
    if (cursor < end) {
      next.push({
        start: cursor,
        end,
        color: patch.color || "",
        style: patch.style || "",
        note: patch.note || ""
      });
    }
  }
  return normalizeTextAnnotations(next, textLength);
}

export function textEditRange(beforeText, afterText) {
  let start = 0;
  while (
    start < beforeText.length
    && start < afterText.length
    && beforeText[start] === afterText[start]
  ) {
    start += 1;
  }
  let beforeEnd = beforeText.length;
  let afterEnd = afterText.length;
  while (
    beforeEnd > start
    && afterEnd > start
    && beforeText[beforeEnd - 1] === afterText[afterEnd - 1]
  ) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }
  return { start, beforeEnd, afterEnd, delta: afterEnd - beforeEnd };
}

export function syncTextAnnotationsForEdit(highlights, beforeText, afterText) {
  if (beforeText === afterText) {
    return normalizeTextAnnotations(highlights, afterText.length);
  }
  const edit = textEditRange(beforeText, afterText);
  const next = [];
  normalizeTextAnnotations(highlights, beforeText.length).forEach((item) => {
    if (item.end <= edit.start) {
      next.push(item);
      return;
    }
    if (item.start >= edit.beforeEnd) {
      next.push({ ...item, start: item.start + edit.delta, end: item.end + edit.delta });
      return;
    }
    const start = item.start < edit.start ? item.start : edit.start;
    const end = item.end > edit.beforeEnd ? item.end + edit.delta : edit.afterEnd;
    if (end > start) next.push({ ...item, start, end });
  });
  return normalizeTextAnnotations(next, afterText.length);
}

function createTextAnnotationMark(container, item, text, start, end) {
  const mark = document.createElement("span");
  mark.className = container.hasAttribute("data-material-highlight")
    ? "material-highlight text-annotation-highlight"
    : "text-annotation-highlight";
  if (item.color) mark.dataset.highlightColor = item.color;
  if (item.style) mark.dataset.annotationStyle = item.style;
  mark.dataset.highlightStart = String(item.start);
  mark.dataset.highlightEnd = String(item.end);
  mark.textContent = text.slice(start, end);
  if (item.note) {
    mark.classList.add("has-note");
    mark.title = item.note;
    mark.dataset.annotationNote = item.note;
  }
  return mark;
}

function createAnnotationNoteBadge(item) {
  const badge = document.createElement("span");
  badge.className = "highlight-note-indicator has-note";
  badge.dataset.editorDecoration = "";
  badge.contentEditable = "false";
  badge.setAttribute("aria-label", "查看笔记");
  badge.title = item.note;
  badge.dataset.annotationNote = item.note;
  badge.dataset.highlightStart = String(item.start);
  badge.dataset.highlightEnd = String(item.end);
  badge.innerHTML = `
    <svg class="annotation-note-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3.5h9l4 4v13H6z"></path>
      <path d="M15 3.5v4h4M9 12h7M9 16h5"></path>
    </svg>`;
  return badge;
}

export function renderTextAnnotations(container) {
  const text = editableValue(container);
  const highlights = normalizeTextAnnotations(readTextAnnotations(container), text.length);
  container.textContent = "";
  let cursor = 0;
  highlights.forEach((item) => {
    if (item.start > cursor) {
      container.append(document.createTextNode(text.slice(cursor, item.start)));
    }
    let segmentStart = item.start;
    let lastMark = null;
    const segments = text.slice(item.start, item.end).split(/(\n+)/);
    segments.forEach((segment) => {
      if (!segment) return;
      const segmentEnd = segmentStart + segment.length;
      if (segment.includes("\n") || !segment.trim()) {
        container.append(document.createTextNode(segment));
      } else {
        lastMark = createTextAnnotationMark(container, item, text, segmentStart, segmentEnd);
        container.append(lastMark);
      }
      segmentStart = segmentEnd;
    });
    if (item.note && lastMark) {
      lastMark.after(createAnnotationNoteBadge(item));
    }
    cursor = item.end;
  });
  if (cursor < text.length) {
    container.append(document.createTextNode(text.slice(cursor)));
  }
  if (container.__usesParagraphAlignment) renderEditorParagraphs(container);
}

export function readMaterialHighlights(container) {
  return readTextAnnotations(container);
}

export function writeMaterialHighlights(container, highlights) {
  writeTextAnnotations(container, highlights);
}

export function normalizeMaterialHighlights(highlights, textLength) {
  return normalizeTextAnnotations(highlights, textLength);
}

export function removeHighlightOverlap(highlights, start, end) {
  return applyTextAnnotation(highlights, start, end, null, Number.MAX_SAFE_INTEGER);
}

export function renderMaterialHighlights(container) {
  renderTextAnnotations(container);
}

export function applyMaterialHighlight(highlights, start, end, color, textLength) {
  return applyTextAnnotation(highlights, start, end, color ? { color } : null, textLength);
}

export function textOffsetInContainer(container, targetNode, targetOffset) {
  const editorLines = Array.from(container.children || []);
  const usesEditorLines = (
    editorLines.length
    && editorLines.length === container.childNodes.length
    && editorLines.every((line) => line.matches("[data-editor-line]"))
  );
  if (usesEditorLines) {
    const targetElement = targetNode.nodeType === Node.TEXT_NODE
      ? targetNode.parentElement
      : targetNode;
    const targetLine = targetElement?.closest?.("[data-editor-line]");
    if (targetLine?.parentElement === container) {
      let lineOffset = 0;
      for (const line of editorLines) {
        if (line === targetLine) break;
        // renderEditorParagraphs represents every paragraph boundary with one
        // newline in editableValue, including boundaries around empty lines.
        lineOffset += editablePlainText(line).length + 1;
      }
      const offsetInLine = textOffsetInContainer(targetLine, targetNode, targetOffset);
      return offsetInLine < 0 ? -1 : lineOffset + offsetInLine;
    }
  }

  if (targetNode === container && Number.isInteger(targetOffset)) {
    const boundary = Math.max(0, Math.min(targetOffset, container.childNodes.length));
    const prefix = document.createElement("div");
    Array.from(container.childNodes).slice(0, boundary).forEach((node) => {
      prefix.append(node.cloneNode(true));
    });
    return editablePlainText(prefix).length;
  }

  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      return node.parentElement?.closest("[data-editor-decoration]")
        ? NodeFilter.FILTER_REJECT
        : NodeFilter.FILTER_ACCEPT;
    },
  });
  let offset = 0;
  let node = walker.nextNode();
  while (node) {
    if (node === targetNode) return offset + targetOffset;
    offset += node.nodeValue.length;
    node = walker.nextNode();
  }
  return -1;
}

export function selectedTextAnnotationRange() {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount !== 1 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  const startElement = range.startContainer.nodeType === Node.TEXT_NODE
    ? range.startContainer.parentElement
    : range.startContainer;
  const endElement = range.endContainer.nodeType === Node.TEXT_NODE
    ? range.endContainer.parentElement
    : range.endContainer;
  const selector = "[data-material-highlight], [data-text-annotation]";
  const startContainer = startElement ? startElement.closest(selector) : null;
  const endContainer = endElement ? endElement.closest(selector) : null;
  if (!startContainer || startContainer !== endContainer) return null;
  const start = textOffsetInContainer(startContainer, range.startContainer, range.startOffset);
  const end = textOffsetInContainer(startContainer, range.endContainer, range.endOffset);
  if (start < 0 || end < 0 || start === end) return null;
  const text = editableValue(startContainer);
  let trimmedStart = Math.min(start, end);
  let trimmedEnd = Math.max(start, end);
  while (trimmedStart < trimmedEnd && /\s/.test(text[trimmedStart])) trimmedStart += 1;
  while (trimmedEnd > trimmedStart && /\s/.test(text[trimmedEnd - 1])) trimmedEnd -= 1;
  if (trimmedStart === trimmedEnd) return null;
  return {
    container: startContainer,
    start: trimmedStart,
    end: trimmedEnd,
    rect: range.getBoundingClientRect(),
  };
}

export function selectedMaterialRange() {
  return selectedTextAnnotationRange();
}



export function initializeSelectionMenu(signal) {
  // 通用"选中文字 → 高亮/复制"浮动菜单。
  // 作用于任何内容文本（材料、题目、作答要求、参考答案等），
  // 标注容器（data-material-highlight / data-text-annotation）与输入框由
  // 标注工具栏/编辑器接管，不重复弹出。
  document.querySelectorAll(".selection-menu").forEach((el) => el.remove());
  const menu = document.createElement("div");
  menu.className = "selection-menu";
  menu.hidden = true;
  menu.innerHTML = `
    <button type="button" class="selection-menu-copy" data-selection-copy aria-label="复制选中文字" title="复制选中文字">复制</button>
    <span class="selection-menu-divider" aria-hidden="true"></span>
    <span class="selection-menu-colors" role="group" aria-label="高亮颜色">
      ${materialHighlightColors.map((color) => (
        `<button type="button" class="selection-menu-color" data-selection-color="${color}" data-highlight-color="${color}" aria-label="${color}高亮" title="${color}高亮"></button>`
      )).join("")}
    </span>
  `;
  document.body.append(menu);

  let resetTimer = null;
  const hide = () => { menu.hidden = true; };

  const isFunctionalSelection = (node) => {
    if (!node || !node.closest) return false;
    // 侧边栏 / 导航 / 功能键 / 表单控件：不弹选中菜单
    if (node.closest("aside, nav, .sidebar")) return true;
    if (node.closest("button, input, select, textarea, label")) return true;
    if (node.closest("[role='button'], [role='tab'], [role='menuitem'], [role='navigation']")) return true;
    // 答题编辑器（contenteditable）由标注工具栏接管
    if (node.closest("[contenteditable]")) return true;
    return false;
  };

  const updateFromSelection = () => {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount !== 1) {
      hide();
      return;
    }
    const range = selection.getRangeAt(0);
    if (!range.toString().trim()) { hide(); return; }
    const startNode = range.startContainer.nodeType === Node.TEXT_NODE
      ? range.startContainer.parentElement
      : range.startContainer;
    if (isFunctionalSelection(startNode)) { hide(); return; }
    // 标注容器（答题页材料等）：只有专用工具栏已经实际显示时才让给它；
    // WKWebView 某些选择事件不会触发 mouseup，或 offset 计算会短暂失败，
    // 此时通用菜单继续兜底，避免两个菜单同时都隐藏。
    if (startNode.closest("[data-material-highlight], [data-text-annotation]")) {
      const annotationToolbar = document.querySelector("[data-highlight-toolbar]");
      if (selectedTextAnnotationRange() && annotationToolbar && !annotationToolbar.hidden) {
        hide();
        return;
      }
    }
    const rect = range.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) { hide(); return; }
    menu.hidden = false;
    const menuRect = menu.getBoundingClientRect();
    const position = floatingToolbarPosition(rect, menuRect, 18);
    menu.dataset.placement = position.placement;
    menu.style.top = `${Math.round(position.top)}px`;
    menu.style.left = `${Math.round(position.left)}px`;
  };

  let selectionUpdateTimer = null;
  const scheduleSelectionUpdate = () => {
    window.clearTimeout(selectionUpdateTimer);
    selectionUpdateTimer = window.setTimeout(updateFromSelection, 0);
  };

  document.addEventListener("selectionchange", scheduleSelectionUpdate, { signal: signal });
  document.addEventListener("mouseup", scheduleSelectionUpdate, { signal: signal });
  document.addEventListener("keyup", (event) => {
    if (event.key === "Shift" || event.key.startsWith("Arrow")) scheduleSelectionUpdate();
  }, { signal: signal });
  document.addEventListener("scroll", hide, { passive: true, capture: true, signal: signal });
  document.addEventListener("mousedown", (event) => {
    if (menu.contains(event.target)) return;
    window.setTimeout(hide, 0);
  }, { signal: signal });

  menu.querySelector("[data-selection-copy]").addEventListener("click", async () => {
    const text = window.getSelection()?.toString() || "";
    if (!text) { hide(); return; }
    const ok = await copyText(text);
    showCopyToast(ok ? "已复制选中文字" : "复制失败，请手动复制");
    window.clearTimeout(resetTimer);
    resetTimer = window.setTimeout(hide, 1200);
  });

  menu.querySelectorAll("[data-selection-color]").forEach((button) => {
    button.addEventListener("click", () => {
      const ok = applySelectionHighlight(button.dataset.selectionColor);
      hide();
      showCopyToast(ok ? "已高亮" : "高亮失败，请重新选择文字");
    });
  });
}

function applySelectionHighlight(color) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return false;
  const range = selection.getRangeAt(0);
  if (range.collapsed || !range.toString().trim()) return false;
  const colors = {
    yellow: "rgba(245, 196, 78, .38)",
    orange: "rgba(235, 139, 61, .3)",
    pink: "rgba(224, 116, 148, .28)",
    purple: "rgba(145, 105, 190, .27)",
    blue: "rgba(79, 143, 207, .28)",
    green: "rgba(86, 171, 133, .28)",
  };
  const bg = colors[color] || colors.yellow;
  const textNodes = [];
  const root = range.commonAncestorContainer;
  if (root.nodeType === Node.TEXT_NODE) {
    textNodes.push(root);
  } else {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      if (range.intersectsNode(walker.currentNode)) textNodes.push(walker.currentNode);
    }
  }
  if (!textNodes.length) return false;
  for (const tn of textNodes) {
    const start = tn === range.startContainer ? range.startOffset : 0;
    const end = tn === range.endContainer ? range.endOffset : tn.nodeValue.length;
    if (start >= end || end > tn.nodeValue.length) continue;
    const mark = document.createElement("mark");
    mark.style.backgroundColor = bg;
    mark.style.borderRadius = "3px";
    mark.style.boxDecorationBreak = "clone";
    mark.style.webkitBoxDecorationBreak = "clone";
    const frag = document.createDocumentFragment();
    if (start > 0) frag.appendChild(document.createTextNode(tn.nodeValue.slice(0, start)));
    mark.appendChild(document.createTextNode(tn.nodeValue.slice(start, end)));
    frag.appendChild(mark);
    if (end < tn.nodeValue.length) frag.appendChild(document.createTextNode(tn.nodeValue.slice(end)));
    tn.parentNode.replaceChild(frag, tn);
  }
  selection.removeAllRanges();
  return true;
}

export function initializeAnnotations(signal) {
  const annotationContainers = Array.from(new Set([
    ...document.querySelectorAll("[data-material-highlight]"),
    ...document.querySelectorAll("[data-text-annotation]"),
  ]));
  if (annotationContainers.length) {
    document.querySelectorAll("[data-highlight-toolbar]").forEach((btn) => btn.remove());
    const toolbar = document.createElement("div");
    toolbar.className = "material-highlight-toolbar";
    toolbar.hidden = true;
    toolbar.setAttribute("data-highlight-toolbar", "");
    toolbar.innerHTML = `
      <div class="highlight-color-group" role="group" aria-label="高亮颜色">
        ${materialHighlightColors.map((color) => (
          `<button type="button" class="highlight-color ${color}" data-highlight-color="${color}" aria-label="${color}高亮"></button>`
        )).join("")}
      </div>
      <span class="toolbar-divider" aria-hidden="true"></span>
      <div class="highlight-action-group" role="group" aria-label="标注操作">
        <button type="button" class="highlight-note" data-highlight-note aria-label="添加笔记" title="添加笔记">
          <svg class="annotation-note-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M6 3.5h9l4 4v13H6z"></path>
            <path d="M15 3.5v4h4M9 12h7M9 16h5"></path>
          </svg>
        </button>
        <button type="button" class="highlight-style strike" data-highlight-style="strike" aria-label="划掉" title="划掉"><span>划</span></button>
        <button type="button" class="highlight-clear" data-highlight-clear aria-label="清除标注" title="清除标注"><span aria-hidden="true">×</span></button>
        <button type="button" class="highlight-copy" data-highlight-copy aria-label="复制选中文字" title="复制选中文字">
          <svg class="annotation-copy-icon" viewBox="0 0 24 24" aria-hidden="true">
            <rect x="9" y="9" width="11" height="11" rx="2"></rect>
            <path d="M5 15V5a2 2 0 0 1 2-2h10"></path>
          </svg>
        </button>
      </div>
    `;
    document.body.append(toolbar);

    let activeRange = null;
    let activeRangeFromHighlight = false;

    const isModalActive = () => {
      const annotModal = document.getElementById("custom-annotation-modal");
      const confirmModal = document.getElementById("custom-confirm-modal");
      return (annotModal && annotModal.classList.contains("active")) ||
             (confirmModal && confirmModal.classList.contains("active"));
    };

    let currentSaveCallback = null;
    const getOrCreateModal = () => {
      let modal = document.getElementById("custom-annotation-modal");
      if (!modal) {
        modal = document.createElement("div");
        modal.className = "annotation-modal-overlay";
        modal.id = "custom-annotation-modal";
        modal.innerHTML = `
          <div class="annotation-modal-card">
            <div class="annotation-modal-header">编辑材料批注（感想和注意事项）</div>
            <textarea class="annotation-modal-textarea" maxlength="2000" placeholder="写下你的感想或注意事项..."></textarea>
            <div class="annotation-modal-footer">
              <span class="annotation-modal-counter">0 / 2000</span>
              <div class="annotation-modal-actions">
                <button type="button" class="annotation-modal-btn cancel">取消</button>
                <button type="button" class="annotation-modal-btn primary save">保存</button>
              </div>
            </div>
          </div>
        `;
        document.body.append(modal);

        const textarea = modal.querySelector(".annotation-modal-textarea");
        const counter = modal.querySelector(".annotation-modal-counter");
        const updateCounter = () => {
          counter.textContent = `${textarea.value.length} / 2000`;
        };
        textarea.addEventListener("input", updateCounter);

        const closeModal = () => {
          modal.classList.remove("active");
          currentSaveCallback = null;
        };

        modal.querySelector(".cancel").addEventListener("click", closeModal);
        modal.addEventListener("click", (event) => {
          if (event.target === modal) closeModal();
        });

        modal.querySelector(".save").addEventListener("click", () => {
          if (currentSaveCallback) {
            currentSaveCallback(textarea.value.trim());
          }
          closeModal();
        });

        textarea.addEventListener("keydown", (event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            modal.querySelector(".save").click();
          } else if (event.key === "Escape") {
            closeModal();
          }
        });
      }
      return modal;
    };

    const showAnnotationModal = (initialText, onSave) => {
      const modal = getOrCreateModal();
      const textarea = modal.querySelector(".annotation-modal-textarea");
      textarea.value = initialText || "";
      modal.querySelector(".annotation-modal-counter").textContent = `${textarea.value.length} / 2000`;
      currentSaveCallback = onSave;
      modal.classList.add("active");
      window.setTimeout(() => {
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
      }, 50);
    };

    let currentPopoverEditCallback = null;
    let currentPopoverDeleteCallback = null;
    const getOrCreatePopover = () => {
      let popover = document.getElementById("annotation-popover-card");
      if (!popover) {
        popover = document.createElement("div");
        popover.className = "annotation-popover-card";
        popover.id = "annotation-popover-card";
        popover.innerHTML = `
          <button type="button" class="annotation-popover-content" aria-label="编辑批注"></button>
          <button type="button" class="annotation-popover-delete" aria-label="删除批注" title="删除批注">×</button>
        `;
        document.body.append(popover);

        popover.addEventListener("mouseenter", () => {
          window.clearTimeout(hoverTimeout);
        });

        popover.addEventListener("mouseleave", (e) => {
          const toElement = e.relatedTarget;
          if (toElement && toElement.closest(".has-note")) {
            return;
          }
          window.clearTimeout(hoverTimeout);
          hoverTimeout = window.setTimeout(() => {
            hidePopover();
          }, 300);
        });

        popover.querySelector(".annotation-popover-content").addEventListener("click", (e) => {
          e.stopPropagation();
          const cb = currentPopoverEditCallback;
          hidePopover();
          if (cb) cb();
        });

        popover.querySelector(".annotation-popover-delete").addEventListener("click", (e) => {
          e.stopPropagation();
          const cb = currentPopoverDeleteCallback;
          hidePopover();
          if (cb) cb();
        });
      }
      return popover;
    };

    const hidePopover = () => {
      const popover = document.getElementById("annotation-popover-card");
      if (popover) {
        popover.classList.remove("active");
      }
      currentPopoverEditCallback = null;
      currentPopoverDeleteCallback = null;
    };

    const showAnnotationPopover = (element, noteText, onEdit, onDelete) => {
      const popover = getOrCreatePopover();
      popover.querySelector(".annotation-popover-content").textContent = noteText;
      currentPopoverEditCallback = onEdit;
      currentPopoverDeleteCallback = onDelete;
      popover.classList.add("active");

      // Position the popover relative to the note indicator badge if present
      const badge = element.classList.contains("highlight-note-indicator")
        ? element
        : (element.nextElementSibling && element.nextElementSibling.classList.contains("highlight-note-indicator")
           ? element.nextElementSibling
           : element);
      const rect = badge.getBoundingClientRect();
      const popoverWidth = popover.offsetWidth || 200;
      const popoverHeight = popover.offsetHeight || 80;

      // 与标注工具栏一致：position:fixed + 纯视口坐标定位
      const x = rect.left + (rect.width - popoverWidth) / 2;
      let y = rect.top - popoverHeight - 8;

      if (y < 8) {
        y = rect.bottom + 8;
      }

      popover.style.left = `${Math.round(Math.max(8, x))}px`;
      popover.style.top = `${Math.round(y)}px`;
    };

    const clearSelectedHighlight = () => {
      document.querySelectorAll(".material-highlight.is-selected-highlight, .text-annotation-highlight.is-selected-highlight").forEach((item) => {
        item.classList.remove("is-selected-highlight");
      });
    };
    const hideToolbar = () => {
      toolbar.hidden = true;
      activeRange = null;
      activeRangeFromHighlight = false;
      clearSelectedHighlight();
    };
    const syncToolbarSelection = (mark = null) => {
      const activeColor = mark?.dataset.highlightColor || "";
      const activeStyle = mark?.dataset.annotationStyle || "";
      toolbar.querySelectorAll("[data-highlight-color]").forEach((button) => {
        const active = Boolean(activeColor) && button.dataset.highlightColor === activeColor;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      toolbar.querySelectorAll("[data-highlight-style]").forEach((button) => {
        const active = Boolean(activeStyle) && button.dataset.highlightStyle === activeStyle;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-pressed", String(active));
      });
    };
    const showToolbar = (range, mark = null) => {
      hidePopover();
      activeRange = range;
      syncToolbarSelection(mark);
      toolbar.hidden = false;
      const toolbarRect = toolbar.getBoundingClientRect();
      const computedLineHeight = Number.parseFloat(window.getComputedStyle(range.container).lineHeight);
      const lineHeight = Number.isFinite(computedLineHeight) && computedLineHeight > 0
        ? computedLineHeight
        : Math.max(18, Number(range.rect.height) || 18);
      // Leave one text line between the floating toolbar and the selection.
      // With only an 8px gap the toolbar can cover the neighbouring line in
      // dense material text, which also makes pointer entry feel unstable.
      const readingGap = Math.min(44, Math.max(18, Math.ceil(lineHeight) + 6));
      const position = floatingToolbarPosition(range.rect, toolbarRect, readingGap);
      toolbar.dataset.placement = position.placement;
      toolbar.style.top = `${Math.round(position.top)}px`;
      toolbar.style.left = `${Math.round(position.left)}px`;
    };
    const showToolbarForHighlight = (mark) => {
      const container = mark.closest("[data-material-highlight], [data-text-annotation]");
      if (!container) return;
      const start = Number(mark.dataset.highlightStart);
      const end = Number(mark.dataset.highlightEnd);
      if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) return;
      window.getSelection()?.removeAllRanges();
      clearSelectedHighlight();
      mark.classList.add("is-selected-highlight");
      activeRangeFromHighlight = true;
      showToolbar({
        container,
        start,
        end,
        rect: mark.getBoundingClientRect(),
        note: mark.dataset.annotationNote || "",
      }, mark);
    };
    const updateToolbarFromSelection = () => {
      if (isModalActive()) return;
      if (activeRangeFromHighlight) return;
      const range = selectedTextAnnotationRange();
      if (!range) {
        hideToolbar();
        return;
      }
      clearSelectedHighlight();
      activeRangeFromHighlight = false;

      let note = "";
      const current = readTextAnnotations(range.container);
      current.forEach((item) => {
        if (item.start < range.end && item.end > range.start) {
          if (item.note) note = item.note;
        }
      });
      range.note = note;

      showToolbar(range);
    };
    let toolbarSelectionUpdateTimer = null;
    const scheduleToolbarSelectionUpdate = () => {
      window.clearTimeout(toolbarSelectionUpdateTimer);
      toolbarSelectionUpdateTimer = window.setTimeout(() => {
        if (isModalActive()) return;
        if (activeRangeFromHighlight) return;
        updateToolbarFromSelection();
      }, 0);
    };
    const applyActiveAnnotation = (patch) => {
      if (!activeRange) return;
      const textLength = editableValue(activeRange.container).length;
      const current = readTextAnnotations(activeRange.container);
      const next = applyTextAnnotation(current, activeRange.start, activeRange.end, patch, textLength);
      writeTextAnnotations(activeRange.container, next);
      renderTextAnnotations(activeRange.container);
      window.getSelection()?.removeAllRanges();
      hideToolbar();
    };

    annotationContainers.forEach((container) => {
      const sourceName = container.dataset.annotationFor;
      const source = sourceName
        ? container.closest(".text-block, .attempt-note-panel, .answer-editor")?.querySelector(`[name="${sourceName}"]`)
        : null;
      const renderFromSource = () => {
        if (source) container.textContent = source.value || "";
        renderTextAnnotations(container);
        annotationTextSnapshots.set(container, editableValue(container));
      };
      renderFromSource();
      const pendingLocalAnnotations = localTextAnnotations(container);
      if (pendingLocalAnnotations !== null && container.dataset.annotationSaveUrl) {
        persistTextAnnotations(container, readTextAnnotations(container));
      }
      if (source) {
        source.addEventListener("input", () => {
          const beforeText = annotationTextSnapshots.get(container) || "";
          const afterText = source.value || "";
          const next = syncTextAnnotationsForEdit(readTextAnnotations(container), beforeText, afterText);
          container.textContent = afterText;
          writeTextAnnotations(container, next);
          renderTextAnnotations(container);
          annotationTextSnapshots.set(container, afterText);
        });
      }
      if (container.isContentEditable) {
        container.addEventListener("input", () => {
          const beforeText = annotationTextSnapshots.get(container) || "";
          const afterText = editableValue(container);
          const next = syncTextAnnotationsForEdit(readTextAnnotations(container), beforeText, afterText);
          writeTextAnnotations(container, next);
          annotationTextSnapshots.set(container, afterText);
        });
        container.addEventListener("blur", () => {
          renderTextAnnotations(container);
          annotationTextSnapshots.set(container, editableValue(container));
        });
      }
      container.addEventListener("click", (event) => {
        const mark = event.target instanceof Element
          ? event.target.closest(
            ".material-highlight, .text-annotation-highlight, .highlight-note-indicator",
          )
          : null;
        if (mark && container.contains(mark)) {
          const selection = window.getSelection();
          if (selection && !selection.isCollapsed) return;
          event.stopPropagation();
          if (mark.classList.contains("has-note")) {
            const start = Number(mark.dataset.highlightStart);
            const end = Number(mark.dataset.highlightEnd);
            const noteText = mark.dataset.annotationNote || "";
            showAnnotationPopover(
              mark,
              noteText,
              () => {
                showAnnotationModal(noteText, (newNote) => {
                  const currentAnnotations = readTextAnnotations(container);
                  const patch = { note: newNote };
                  let hasColorOrStyle = false;
                  currentAnnotations.forEach((item) => {
                    if (item.start <= start && item.end >= end) {
                      if (item.color || item.style) hasColorOrStyle = true;
                    }
                  });
                  if (!hasColorOrStyle) patch.color = "yellow";
                  activeRange = { container, start, end };
                  applyActiveAnnotation(patch);
                });
              },
              () => {
                showConfirmModal("确定要删除这条批注吗？", () => {
                  const currentAnnotations = readTextAnnotations(container);
                  let hasColorOrStyle = false;
                  currentAnnotations.forEach((item) => {
                    if (item.start <= start && item.end >= end) {
                      if (item.color || item.style) hasColorOrStyle = true;
                    }
                  });
                  activeRange = { container, start, end };
                  if (hasColorOrStyle) {
                    applyActiveAnnotation({ note: "" });
                  } else {
                    applyActiveAnnotation(null);
                  }
                });
              }
            );
          } else {
            showToolbarForHighlight(mark);
          }
        }
      });
    });
    document.querySelectorAll("[data-clear-active-material-highlights]").forEach((button) => {
      button.addEventListener("click", () => {
        const tabset = button.closest("[data-tabs]");
        const activePanel = tabset?.querySelector(".tab-panel.active-panel");
        const container = activePanel?.querySelector("[data-material-highlight]");
        if (!container) return;
        const previousAnnotations = readTextAnnotations(container).map((item) => ({ ...item }));
        if (!previousAnnotations.length) return;
        writeTextAnnotations(container, []);
        renderTextAnnotations(container);
        hideToolbar();
        showUndoToast("已清除本材料批注", () => {
          writeTextAnnotations(container, previousAnnotations);
          renderTextAnnotations(container);
        });
      });
    });
    document.querySelectorAll("[data-clear-text-annotations]").forEach((button) => {
      button.addEventListener("click", () => {
        const panel = button.closest(".text-block, .attempt-note-panel, .answer-editor, form");
        const container = panel?.querySelector("[data-text-annotation]");
        if (!container) return;
        const previousAnnotations = readTextAnnotations(container).map((item) => ({ ...item }));
        if (!previousAnnotations.length) return;
        writeTextAnnotations(container, []);
        renderTextAnnotations(container);
        hideToolbar();
        showUndoToast("已清除本区域批注", () => {
          writeTextAnnotations(container, previousAnnotations);
          renderTextAnnotations(container);
        });
      });
    });
    document.addEventListener("selectionchange", scheduleToolbarSelectionUpdate, { signal: signal });
    document.addEventListener("mouseup", scheduleToolbarSelectionUpdate, { signal: signal });
    document.addEventListener("keyup", (event) => {
      if (!event.key) return;
      if (event.key.startsWith("Arrow") || event.key === "Shift") {
        scheduleToolbarSelectionUpdate();
      }
    }, { signal: signal });
    document.addEventListener("mousedown", (event) => {
      if (isModalActive()) return;
      const materialFontControls = event.target instanceof Element
        ? event.target.closest("[data-material-font-controls]")
        : null;
      if (!toolbar.hidden && !toolbar.contains(event.target) && !materialFontControls) {
        hideToolbar();
      }
      const popover = document.getElementById("annotation-popover-card");
      if (popover && popover.classList.contains("active") && !popover.contains(event.target) && !event.target.closest(".has-note")) {
        hidePopover();
      }
    }, { signal: signal });

    let hoverTimeout = null;
    document.addEventListener("mouseover", (event) => {
      const mark = event.target.closest(".has-note");
      const popover = document.getElementById("annotation-popover-card");
      if (mark) {
        window.clearTimeout(hoverTimeout);
        const noteText = mark.dataset.annotationNote || "";
        const start = Number(mark.dataset.highlightStart);
        const end = Number(mark.dataset.highlightEnd);
        const container = mark.closest("[data-material-highlight], [data-text-annotation]");
        if (!container) return;
        showAnnotationPopover(
          mark,
          noteText,
          () => {
            showAnnotationModal(noteText, (newNote) => {
              const currentAnnotations = readTextAnnotations(container);
              const patch = { note: newNote };
              let hasColorOrStyle = false;
              currentAnnotations.forEach((item) => {
                if (item.start <= start && item.end >= end) {
                  if (item.color || item.style) hasColorOrStyle = true;
                }
              });
              if (!hasColorOrStyle) patch.color = "yellow";
              activeRange = { container, start, end };
              applyActiveAnnotation(patch);
            });
          },
          () => {
            showConfirmModal("确定要删除这条批注吗？", () => {
              const currentAnnotations = readTextAnnotations(container);
              let hasColorOrStyle = false;
              currentAnnotations.forEach((item) => {
                if (item.start <= start && item.end >= end) {
                  if (item.color || item.style) hasColorOrStyle = true;
                }
              });
              activeRange = { container, start, end };
              if (hasColorOrStyle) {
                applyActiveAnnotation({ note: "" });
              } else {
                applyActiveAnnotation(null);
              }
            });
          }
        );
      } else if (popover && popover.contains(event.target)) {
        window.clearTimeout(hoverTimeout);
      }
    }, { signal: signal });

    document.addEventListener("mouseout", (event) => {
      const popover = document.getElementById("annotation-popover-card");
      if (!popover || !popover.classList.contains("active")) return;
      const toElement = event.relatedTarget;
      if (toElement && (toElement.closest(".has-note") || toElement.closest("#annotation-popover-card"))) {
        return;
      }
      window.clearTimeout(hoverTimeout);
      hoverTimeout = window.setTimeout(() => {
        const popoverEl = document.getElementById("annotation-popover-card");
        if (popoverEl && popoverEl.matches(":hover")) {
          return;
        }
        let hasNoteHovered = false;
        document.querySelectorAll(".has-note").forEach((el) => {
          if (el.matches(":hover")) hasNoteHovered = true;
        });
        if (hasNoteHovered) {
          return;
        }
        hidePopover();
      }, 300);
    }, { signal: signal });
    toolbar.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    toolbar.querySelectorAll("[data-highlight-color]").forEach((button) => {
      button.addEventListener("click", () => applyActiveAnnotation({ color: button.dataset.highlightColor || "" }));
    });
    toolbar.querySelectorAll("[data-highlight-style]").forEach((button) => {
      button.addEventListener("click", () => applyActiveAnnotation({ style: button.dataset.highlightStyle || "" }));
    });
    toolbar.querySelector("[data-highlight-note]")?.addEventListener("click", () => {
      if (!activeRange) return;
      const container = activeRange.container;
      const currentAnnotations = readTextAnnotations(container);
      let currentNote = "";
      currentAnnotations.forEach((item) => {
        if (item.start < activeRange.end && item.end > activeRange.start) {
          if (item.note) currentNote = item.note;
        }
      });
      showAnnotationModal(currentNote, (note) => {
        let hasColorOrStyle = false;
        currentAnnotations.forEach((item) => {
          if (item.start < activeRange.end && item.end > activeRange.start) {
            if (item.color || item.style) {
              hasColorOrStyle = true;
            }
          }
        });
        const patch = { note };
        if (!hasColorOrStyle) {
          patch.color = "yellow";
        }
        applyActiveAnnotation(patch);
      });
    });
    toolbar.querySelector("[data-highlight-clear]")?.addEventListener("click", () => applyActiveAnnotation(null));
    toolbar.querySelector("[data-highlight-copy]")?.addEventListener("click", async () => {
      if (!activeRange) return;
      const text = editableValue(activeRange.container).slice(activeRange.start, activeRange.end);
      if (!text) return;
      const ok = await copyText(text);
      showCopyToast(ok ? "已复制选中文字" : "复制失败，请手动复制");
    });
    window.addEventListener("scroll", hideToolbar, { passive: true, signal: signal });
  }

  initializeSelectionMenu(signal);
}
