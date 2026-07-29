(function () {
  const RAW_CONTAINER_ID = "the-list-proposal-markdown-input";
  const PREVIEW_CONTAINER_ID = "the-list-proposal-preview";
  const MODE_CONTAINER_ID = "the-list-proposal-view-mode";
  const VISUAL_EDITOR_CLASS = "the-list-visual-editor";
  const VISUAL_EDITOR_ACTIVE_CLASS = "the-list-visual-editor--active";
  const CARD_TITLE_ACTIONS_SLOT_ID = "person-detail-card-title-actions-slot";
  const PAGE_TITLE_ROW_ID = "people-title-row";
  const CARD_EDIT_BUTTON_ID = "the-list-card-edit-btn";
  const DOWNLOAD_MENU_ID = "recipe-download-menu";
  const DELETE_BUTTON_ID = "the-list-delete-btn";
  const MARKDOWN_EDIT_BUTTON_ID = "the-list-markdown-edit-btn";
  const REVIEW_LINK_ID = "the-list-review-link";
  const MARKDOWN_CONTAINER_ID = "person-detail-markdown";
  const PROPOSAL_CITE_BUTTON_ID = "the-list-proposal-cite-btn";
  const PROPOSAL_BIB_BUTTON_ID = "the-list-proposal-bib-btn";
  const PROPOSAL_ACTIONS_ID = "the-list-proposal-actions";
  const SOURCE_CITATION_OPTIONS_ID = "the-list-source-citation-options";
  const PROPOSAL_STATUS_IDS = ["the-list-proposal-status", "the-list-card-proposal-status", "recipe-page-status"];
  const VERIFY_STATUS_PATTERN = /receta marcada como (verificada|sin verificar)/i;
  const CREATE_RECIPE_STATUS_PATTERN = /receta creada/i;
  const DELETE_RECIPE_STATUS_PATTERN = /receta .*eliminad[ao]/i;
  const RECETAS_LIST_CREATED_URL = "/recetas/?created_recipe=1";
  const RECETAS_LIST_DELETED_URL = "/recetas/?deleted_recipe=1";
  const RECETAS_LIST_TOAST_SESSION_KEY = "recetas_list_success_toast";
  const TOAST_ROOT_ID = "the-list-toast-root";
  const TOAST_HIDE_DELAY_MS = 4200;
  const TOAST_REMOVE_DELAY_MS = 4700;
  const CARD_PROPOSAL_SHELL_ID = "the-list-card-proposal-shell";
  const CARD_PROPOSAL_NAME_ID = "the-list-card-proposal-name";
  const CARD_PROPOSAL_BUCKET_ID = "the-list-card-proposal-bucket";
  const CARD_PROPOSAL_TAGS_ID = "the-list-card-proposal-tags";
  const CARD_PROPOSAL_TOTAL_TIME_ID = "the-list-card-proposal-total-time";
  const CARD_PROPOSAL_PERSONS_ID = "the-list-card-proposal-persons";
  const CARD_PROPOSAL_IMAGE_DATA_ID = "the-list-card-image-data";
  const CARD_IMAGE_UPLOAD_BUTTON_ID = "the-list-card-image-plus-btn";
  const CARD_PROPOSAL_ACTIONS_ID = "the-list-card-proposal-actions";
  const CURRENT_SLUG_ID = "the-list-current-slug";
  const CURRENT_NAME_ID = "the-list-current-name";
  const CURRENT_BUCKET_ID = "the-list-current-bucket";
  const CURRENT_TAGS_ID = "the-list-current-tags";
  const CURRENT_TOTAL_TIME_ID = "the-list-current-total-time";
  const CURRENT_PERSONS_ID = "the-list-current-persons";
  const DETAIL_CARD_SELECTOR = "#person-detail-hero .person-detail-card";
  const DETAIL_INLINE_ACTIONS_SLOT_ID = "person-detail-card-inline-actions-slot";
  const DETAIL_TITLE_SELECTOR = ".person-detail-card__title";
  const DETAIL_BUCKET_SELECTOR = ".person-detail-card__bucket";
  const DETAIL_TAGS_SELECTOR = ".person-detail-card__tags";
  const DETAIL_TOTAL_TIME_VALUE_SELECTOR = ".recipe-meta-item__value--total-time";
  const DETAIL_PERSONS_VALUE_SELECTOR = ".recipe-meta-item__value--persons";
  const DETAIL_MEDIA_SELECTOR = ".person-detail-card__media";
  const DETAIL_IMAGE_SELECTOR = ".person-detail-card__media img";
  const DETAIL_VERIFY_BADGE_SELECTOR = ".recipe-detail-card__verified";
  const DETAIL_VERIFY_PAYLOAD_ID = "recipe-detail-verify-payload";
  const DETAIL_VERIFY_TRIGGER_ID = "recipe-detail-verify-trigger";
  const DETAIL_VERIFY_STATE_ID = "recipe-verified-state";
  const RECIPE_DOWNLOAD_PAYLOAD_ID = "recipe-download-payload";
  const RECIPE_DELETE_BLOCKED_MESSAGE =
    "No puedes eliminar una receta verificada. Primero márcala como sin verificar.";
  const RECIPE_DELETE_CONFIRM_MESSAGE =
    "Esta acción eliminará la receta de forma permanente y no se puede deshacer.\n\n¿Quieres continuar?";
  const RECIPE_IMPORT_FILE_LIST_ID = "recipe-import-file-list";
  const RECIPE_IMPORT_REMOVE_INDEX_ID = "recipe-import-remove-index";
  const RECIPE_IMPORT_REMOVE_TRIGGER_ID = "recipe-import-remove-trigger";
  const CARD_IMAGE_CROP_MODAL_ID = "the-list-card-image-crop-modal";
  const CARD_IMAGE_CROP_VIEW_WIDTH = 360;
  const CARD_IMAGE_CROP_VIEW_HEIGHT = 270;
  const CARD_IMAGE_OUTPUT_WIDTH = 360;
  const CARD_IMAGE_OUTPUT_HEIGHT = 270;
  const CARD_IMAGE_CROP_MAX_ZOOM_MULTIPLIER = 6;
  const CARD_IMAGE_CROP_ZOOM_STEPS = 1000;
  const CARD_IMAGE_CROP_WHEEL_SENSITIVITY = 0.0018;
  const CROP_DEBUG_ENABLED = false;
  const CROP_DEBUG_MAX_ENTRIES = 220;
  const CROP_DEBUG_VISIBLE_LINES = 18;
  const CROP_DEBUG_PANEL_ID = "the-list-crop-debug-panel";
  const CITATION_TOOLTIP_ID = "the-list-citation-tooltip";
  const IMAGE_RESIZE_DIRECTIONS = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  const IMAGE_RESIZE_ACTIVE_CLASS = "the-list-image-resize-target";
  const IMAGE_RESIZE_MIN_WIDTH = 56;
  const IMAGE_RESIZE_MAX_MULTIPLIER = 3;
  const TAG_SUGGESTION_LIMIT = 8;
  const RECIPE_INGREDIENTS_INPUT_ID = "recipe-editor-ingredients-input";
  const RECIPE_INGREDIENTS_EDITOR_CLASS = "recipe-ingredients-editor";
  const RECIPE_INGREDIENT_ITEM_SELECTOR = ".recipe-ingredient-item";
  const RECIPE_STEPS_INPUT_ID = "recipe-editor-steps-input";
  const RECIPE_STEPS_EDITOR_CLASS = "recipe-steps-editor";
  const RECIPE_STEP_ITEM_SELECTOR = ".recipe-step-item";
  const COMPILED_PREVIEW_DEBOUNCE_MS = 1000;
  const COMPILED_PREVIEW_RESTORE_RETRY_MAX = 16;
  const COMPILED_PREVIEW_RESTORE_RETRY_DELAY_MS = 50;

  const boundEditors = new WeakSet();
  const boundStatusNodes = new WeakSet();
  const handledCardImageChangeEvents = new WeakSet();
  let syncScheduled = false;
  let cardInlineWasActive = false;
  let inlinePreviewUrl = "";
  let imagePreviewBindingReady = false;
  let cardImageNativePicker = null;
  let cardImageCropBypassOnce = false;
  let cardImageCropUi = null;
  let cardImageCropState = null;
  let cardImageCropSessionToken = 0;
  let cropDebugPanelBody = null;
  let cropDebugPanelStatus = null;
  let lastInlineEditorStateKey = "";
  let lastCropModalVisibilityKey = "";
  let imageResizeOverlay = null;
  let imageResizeTarget = null;
  let imageResizeDragState = null;
  let imageResizePositionScheduled = false;
  let imageResizeContainer = null;
  let imageResizeOutsideClickBound = false;
  let citationTooltipNode = null;
  let citationTooltipAnchor = null;
  let citationTooltipGlobalBound = false;
  let compiledPreviewSyncTimerId = 0;
  let lastCompiledPreviewMarkdown = "";
  let cardProposalActionsRowCache = null;
  let recipeIngredientItemCounter = 0;
  let recipeStepItemCounter = 0;
  let createdRecipeRedirectTriggered = false;
  let deletedRecipeRedirectTriggered = false;

  const getCropDebugStore = () => {
    const key = "__theListCropDebugLogs";
    const root = window;
    if (!Array.isArray(root[key])) {
      root[key] = [];
    }
    return root[key];
  };

  const cropDebugStringify = (value) => {
    if (value === undefined) return "";
    if (typeof value === "string") return value;
    try {
      return JSON.stringify(value);
    } catch (error) {
      void error;
      return String(value);
    }
  };

  const updateCropDebugPanel = () => {
    if (!CROP_DEBUG_ENABLED) return;
    const logs = getCropDebugStore();
    if (cropDebugPanelBody instanceof HTMLElement) {
      const lines = logs.slice(-CROP_DEBUG_VISIBLE_LINES).map((entry) => {
        const details = cropDebugStringify(entry.details);
        return details ? `${entry.at} ${entry.event} ${details}` : `${entry.at} ${entry.event}`;
      });
      cropDebugPanelBody.textContent = lines.join("\n");
    }
    if (cropDebugPanelStatus instanceof HTMLElement) {
      cropDebugPanelStatus.textContent = `logs: ${logs.length}`;
    }
  };

  const ensureCropDebugPanel = () => {
    if (!CROP_DEBUG_ENABLED) return;
    let root = document.getElementById(CROP_DEBUG_PANEL_ID);
    if (!(root instanceof HTMLElement)) {
      root = document.createElement("section");
      root.id = CROP_DEBUG_PANEL_ID;
      root.style.position = "fixed";
      root.style.right = "8px";
      root.style.bottom = "8px";
      root.style.width = "min(520px, calc(100vw - 16px))";
      root.style.maxHeight = "40vh";
      root.style.display = "grid";
      root.style.gridTemplateRows = "auto 1fr";
      root.style.gap = "6px";
      root.style.padding = "8px";
      root.style.border = "1px solid #bfdbfe";
      root.style.borderRadius = "10px";
      root.style.background = "rgba(15, 23, 42, 0.94)";
      root.style.color = "#e2e8f0";
      root.style.zIndex = "3200";
      root.style.fontFamily = "ui-monospace, SFMono-Regular, Menlo, monospace";
      root.style.fontSize = "11px";
      root.style.lineHeight = "1.35";

      const header = document.createElement("div");
      header.style.display = "flex";
      header.style.justifyContent = "space-between";
      header.style.alignItems = "center";
      header.style.gap = "8px";

      const title = document.createElement("strong");
      title.textContent = "Depuración de recorte";

      cropDebugPanelStatus = document.createElement("span");
      cropDebugPanelStatus.style.opacity = "0.8";
      cropDebugPanelStatus.textContent = "logs: 0";

      const controls = document.createElement("div");
      controls.style.display = "flex";
      controls.style.gap = "6px";

      const clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.textContent = "Limpiar";
      clearBtn.style.border = "1px solid #475569";
      clearBtn.style.background = "#1e293b";
      clearBtn.style.color = "#e2e8f0";
      clearBtn.style.borderRadius = "6px";
      clearBtn.style.padding = "2px 6px";
      clearBtn.addEventListener("click", () => {
        const logs = getCropDebugStore();
        logs.length = 0;
        updateCropDebugPanel();
      });

      controls.appendChild(cropDebugPanelStatus);
      controls.appendChild(clearBtn);
      header.appendChild(title);
      header.appendChild(controls);

      cropDebugPanelBody = document.createElement("pre");
      cropDebugPanelBody.style.margin = "0";
      cropDebugPanelBody.style.whiteSpace = "pre-wrap";
      cropDebugPanelBody.style.overflow = "auto";
      cropDebugPanelBody.style.maxHeight = "calc(40vh - 40px)";
      cropDebugPanelBody.style.background = "rgba(15, 23, 42, 0.35)";
      cropDebugPanelBody.style.border = "1px solid rgba(148, 163, 184, 0.45)";
      cropDebugPanelBody.style.borderRadius = "8px";
      cropDebugPanelBody.style.padding = "6px";
      cropDebugPanelBody.style.userSelect = "text";

      root.appendChild(header);
      root.appendChild(cropDebugPanelBody);
      document.body.appendChild(root);
    }

    if (!window.__theListCropDebug) {
      window.__theListCropDebug = {
        getLogs: () => getCropDebugStore().slice(),
        clear: () => {
          const logs = getCropDebugStore();
          logs.length = 0;
          updateCropDebugPanel();
        },
      };
    }
    updateCropDebugPanel();
  };

  const cropDebugLog = (event, details) => {
    if (!CROP_DEBUG_ENABLED) return;
    const logs = getCropDebugStore();
    const entry = {
      at: new Date().toISOString().slice(11, 23),
      event: String(event || "unknown"),
      details: details ?? null,
    };
    logs.push(entry);
    if (logs.length > CROP_DEBUG_MAX_ENTRIES) {
      logs.splice(0, logs.length - CROP_DEBUG_MAX_ENTRIES);
    }
    try {
      console.log("[the-list-crop]", entry.event, entry.details);
    } catch (error) {
      void error;
    }
    ensureCropDebugPanel();
    updateCropDebugPanel();
  };

  const fileToDataUrl = (file) =>
    new Promise((resolve, reject) => {
      if (!(file instanceof Blob)) {
        reject(new Error("El archivo recibido no es válido."));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        resolve(String(reader.result || ""));
      };
      reader.onerror = () => {
        reject(new Error("No se pudo codificar la imagen recortada."));
      };
      reader.readAsDataURL(file);
    });

  const getRawTextarea = () =>
    document.querySelector(`#${RAW_CONTAINER_ID} textarea`);

  const getPreviewContainer = () =>
    document.getElementById(PREVIEW_CONTAINER_ID);

  const getPreviewEditor = () => {
    const container = getPreviewContainer();
    if (!container) return null;
    return (
      container.querySelector(".prose") ||
      container.querySelector(".md") ||
      container.querySelector(".markdown-body") ||
      container
    );
  };

  const getModeValue = () => {
    const checked = document.querySelector(
      `#${MODE_CONTAINER_ID} input[type="radio"]:checked`,
    );
    if (!checked) return "";
    const value = (checked.value || "").trim().toLowerCase();
    if (value) return value;
    const label = (checked.closest("label")?.textContent || "").trim().toLowerCase();
    if (label.includes("compiled") || label.includes("preview")) return "preview";
    if (label.includes("raw")) return "raw";
    return "";
  };

  const resolveModeInput = (targetMode) => {
    const normalizedTarget = String(targetMode || "").trim().toLowerCase();
    const inputs = Array.from(
      document.querySelectorAll(`#${MODE_CONTAINER_ID} input[type="radio"]`),
    ).filter((node) => node instanceof HTMLInputElement);
    for (const input of inputs) {
      const value = String(input.value || "").trim().toLowerCase();
      const label = String(input.closest("label")?.textContent || "").trim().toLowerCase();
      if (normalizedTarget === "raw") {
        if (value === "raw" || label.includes("raw")) return input;
        continue;
      }
      if (value === "preview" || value === "compiled" || label.includes("compiled") || label.includes("preview")) {
        return input;
      }
    }
    return null;
  };

  const activateModeInput = (input) => {
    if (!(input instanceof HTMLInputElement)) return;
    const clickable = input.closest("label");
    if (clickable instanceof HTMLElement) {
      clickable.click();
      return;
    }
    input.click();
  };

  const isCompiledMode = () => {
    const mode = getModeValue();
    return mode === "preview" || mode === "compiled";
  };

  const rerenderCompiledPreviewFromRaw = ({ selectionState = null } = {}) => {
    if (!selectionState) return;
    let attempts = 0;
    const restoreSelection = () => {
      const editor = getPreviewEditor();
      if (!(editor instanceof HTMLElement) || !isCompiledMode()) {
        if (attempts < COMPILED_PREVIEW_RESTORE_RETRY_MAX) {
          attempts += 1;
          window.setTimeout(restoreSelection, COMPILED_PREVIEW_RESTORE_RETRY_DELAY_MS);
        }
        return;
      }
      const restored = restorePreviewTextSelectionState(editor, selectionState);
      if (!restored && attempts < COMPILED_PREVIEW_RESTORE_RETRY_MAX) {
        attempts += 1;
        window.setTimeout(restoreSelection, COMPILED_PREVIEW_RESTORE_RETRY_DELAY_MS);
      }
    };
    window.setTimeout(restoreSelection, 40);
  };

  const isElementVisible = (node) => {
    if (!(node instanceof HTMLElement)) return false;
    const computed = window.getComputedStyle(node);
    if (computed.display === "none" || computed.visibility === "hidden") return false;
    return node.getClientRects().length > 0;
  };

  const getTextOffsetWithin = (root, node, offset) => {
    if (!(root instanceof HTMLElement) || !(node instanceof Node)) return null;
    try {
      const range = document.createRange();
      range.selectNodeContents(root);
      range.setEnd(node, offset);
      return range.toString().length;
    } catch (error) {
      void error;
      return null;
    }
  };

  const capturePreviewTextSelectionState = (editor) => {
    if (!(editor instanceof HTMLElement)) return null;
    const selection = window.getSelection();
    if (!selection || selection.rangeCount < 1) return null;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null;
    const start = getTextOffsetWithin(editor, range.startContainer, range.startOffset);
    const end = getTextOffsetWithin(editor, range.endContainer, range.endOffset);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return null;
    return {
      start: Math.max(0, Number(start)),
      end: Math.max(0, Number(end)),
      collapsed: range.collapsed,
    };
  };

  const resolveTextOffsetWithin = (root, targetOffset) => {
    if (!(root instanceof HTMLElement)) return null;
    const safeTarget = Number.isFinite(targetOffset) ? Math.max(0, Number(targetOffset)) : 0;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let current = walker.nextNode();
    if (!(current instanceof Text)) {
      return { node: root, offset: 0 };
    }
    let remaining = safeTarget;
    let lastTextNode = current;
    while (current instanceof Text) {
      lastTextNode = current;
      const textValue = String(current.textContent || "");
      if (remaining <= textValue.length) {
        return { node: current, offset: Math.max(0, Math.min(textValue.length, remaining)) };
      }
      remaining -= textValue.length;
      current = walker.nextNode();
    }
    return {
      node: lastTextNode,
      offset: String(lastTextNode.textContent || "").length,
    };
  };

  const restorePreviewTextSelectionState = (editor, state) => {
    if (!(editor instanceof HTMLElement) || !state) return false;
    const selection = window.getSelection();
    if (!selection) return false;
    const startPosition = resolveTextOffsetWithin(editor, state.start);
    const endPosition = state.collapsed ? startPosition : resolveTextOffsetWithin(editor, state.end);
    if (!startPosition || !endPosition) return false;
    try {
      editor.focus();
      const range = document.createRange();
      range.setStart(startPosition.node, startPosition.offset);
      range.setEnd(endPosition.node, endPosition.offset);
      selection.removeAllRanges();
      selection.addRange(range);
      return true;
    } catch (error) {
      void error;
      return false;
    }
  };

  const getComponentInput = (componentId) =>
    document.querySelector(
      `#${componentId} textarea, #${componentId} input[type="text"], #${componentId} input[type="hidden"], #${componentId} input:not([type])`,
    );

  const getComponentValue = (componentId) => {
    const input = getComponentInput(componentId);
    return input ? String(input.value || "") : "";
  };

  const dispatchComponentEvents = (input) => {
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const setComponentValue = (componentId, value) => {
    const input = getComponentInput(componentId);
    if (!input) return;
    const next = String(value ?? "");
    if ((input.value || "") === next) return;
    input.value = next;
    dispatchComponentEvents(input);
  };

  const decodeBase64ToBytes = (value) => {
    const raw = String(value || "").replace(/\s+/g, "");
    if (!raw) return new Uint8Array(0);
    const binary = window.atob(raw);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  };

  const stopEvent = (event) => {
    if (!(event instanceof Event)) return;
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === "function") {
      event.stopImmediatePropagation();
    }
  };

  const isTrueish = (value) => {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "1" || normalized === "true" || normalized === "yes" || normalized === "on" || normalized === "si" || normalized === "sí";
  };

  const normalizeMarkdownLineEndings = (value) =>
    String(value || "").replace(/\r\n/g, "\n");

  const setRawTextareaValue = (textarea, value, { emitEvents = true, forceEmit = false } = {}) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    const nextValue = String(value ?? "");
    const changed = (textarea.value || "") !== nextValue;
    if (changed) {
      textarea.value = nextValue;
    }
    if (emitEvents && (changed || forceEmit)) {
      textarea.dispatchEvent(new Event("input", { bubbles: true }));
      textarea.dispatchEvent(new Event("change", { bubbles: true }));
      textarea.dispatchEvent(new Event("blur"));
    }
  };

  const forceEmitRawTextarea = () => {
    const textarea = getRawTextarea();
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    setRawTextareaValue(textarea, textarea.value || "", { emitEvents: true, forceEmit: true });
  };

  const insertTextAtCursor = (text) => {
    const value = String(text || "");
    if (!value) return;
    if (document.queryCommandSupported?.("insertText")) {
      document.execCommand("insertText", false, value);
      return;
    }
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return;
    selection.deleteFromDocument();
    selection.getRangeAt(0).insertNode(document.createTextNode(value));
  };

  const insertTextIntoTextarea = (textarea, text) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return false;
    const insertValue = String(text || "");
    if (!insertValue) return false;
    const start = Number.isFinite(textarea.selectionStart)
      ? textarea.selectionStart
      : textarea.value.length;
    const end = Number.isFinite(textarea.selectionEnd)
      ? textarea.selectionEnd
      : start;
    const nextValue = `${textarea.value.slice(0, start)}${insertValue}${textarea.value.slice(end)}`;
    setRawTextareaValue(textarea, nextValue);
    const nextCursor = start + insertValue.length;
    try {
      textarea.focus();
      textarea.setSelectionRange(nextCursor, nextCursor);
    } catch (error) {
      void error;
    }
    return true;
  };

  const appendTextToTextarea = (textarea, text) => {
    if (!(textarea instanceof HTMLTextAreaElement)) return false;
    const appendValue = String(text || "");
    if (!appendValue) return false;
    const prefix = textarea.value && !/\s$/.test(textarea.value) ? " " : "";
    setRawTextareaValue(textarea, `${textarea.value}${prefix}${appendValue}`);
    return true;
  };

  const normalizeCitationKey = (value) =>
    String(value || "")
      .trim()
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/-{2,}/g, "-")
      .replace(/^[-.]+|[-.]+$/g, "")
      .slice(0, 80);

  const looksLikeReferenceTarget = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) return false;
    return /^(?:source|source-id|theory|theory-id)\s*:/i.test(normalized) || /^https?:\/\//i.test(normalized);
  };

  const splitDefinitionBody = (rawValue) => {
    const normalized = String(rawValue || "").trim();
    if (!normalized) return { target: "", label: "" };
    const pipeIndex = normalized.indexOf("|");
    if (pipeIndex < 0) return { target: normalized, label: "" };
    return {
      target: normalized.slice(0, pipeIndex).trim(),
      label: normalized.slice(pipeIndex + 1).trim(),
    };
  };

  const parseBibMacroPayload = (rawPayload) => {
    const payload = String(rawPayload || "").trim();
    if (!payload) return null;

    let key = "";
    let referenceValue = "";

    if (payload.includes("=>")) {
      const parts = payload.split("=>");
      const left = parts.shift() || "";
      const right = parts.join("=>");
      key = normalizeCitationKey(left);
      referenceValue = String(right || "").trim();
    } else if (payload.includes("|")) {
      const parts = payload.split("|").map((part) => String(part || "").trim());
      const first = parts[0] || "";
      const second = parts[1] || "";
      const trailing = parts.length > 2 ? parts.slice(2).join("|").trim() : "";
      if (looksLikeReferenceTarget(first) && parts.length === 2) {
        key = "";
        referenceValue = `${first} | ${second}`.trim();
      } else {
        key = normalizeCitationKey(first);
        referenceValue = trailing ? `${second} | ${trailing}`.trim() : second;
      }
    } else {
      const keyMatch = payload.match(/^([A-Za-z0-9._-]+)\s*:\s*(.+)$/);
      if (keyMatch && !looksLikeReferenceTarget(payload)) {
        key = normalizeCitationKey(keyMatch[1] || "");
        referenceValue = String(keyMatch[2] || "").trim();
      } else {
        key = "";
        referenceValue = payload;
      }
    }

    const parsedBody = splitDefinitionBody(referenceValue);
    let target = String(parsedBody.target || "").trim();
    let label = String(parsedBody.label || "").trim();
    if (!target && label) {
      target = label;
      label = "";
    }
    if (!target) return null;
    return { key, target, label };
  };

  const suggestCitationKey = (seedValue, usedKeys, preserveKey = "") => {
    const base = normalizeCitationKey(seedValue) || "ref";
    if (!usedKeys.has(base) || base === preserveKey) return base;
    let suffix = 2;
    while (usedKeys.has(`${base}-${suffix}`)) {
      suffix += 1;
    }
    return `${base}-${suffix}`;
  };

  const collectBibDefinitions = (markdown) => {
    const definitions = [];
    const seen = new Set();
    const normalized = normalizeMarkdownLineEndings(markdown);
    normalized.replace(/\\bib\{([^{}\n]+)\}/gi, (_match, rawPayload) => {
      const parsed = parseBibMacroPayload(rawPayload);
      if (!parsed) return _match;
      const definitionKey = suggestCitationKey(parsed.key || parsed.label || parsed.target, seen);
      seen.add(definitionKey);
      definitions.push({
        key: definitionKey,
        target: parsed.target,
        label: parsed.label,
        preview: parsed.label || parsed.target,
      });
      return _match;
    });
    return definitions;
  };

  const normalizeCitationPreview = (value) =>
    String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const normalizeReferenceTargetFromHref = (rawHref) => {
    let href = String(rawHref || "").trim();
    if (!href) return "";
    if (href.startsWith("<") && href.endsWith(">") && href.length > 2) {
      href = href.slice(1, -1).trim();
    }
    if (!href) return "";

    let parsed = null;
    if (href.startsWith("/") || /^https?:\/\//i.test(href)) {
      try {
        parsed = new URL(href, window.location.origin);
      } catch (error) {
        void error;
        parsed = null;
      }
    }
    if (!(parsed instanceof URL)) {
      return href;
    }

    return href;
  };

  const referencePayloadFromListItem = (itemValue) => {
    const text = normalizeCitationPreview(itemValue);
    if (!text) return "";
    const markdownLinkMatch = text.match(/^\[(.+?)\]\(([^)]+)\)$/);
    if (!markdownLinkMatch) return text;
    const label = normalizeCitationPreview(markdownLinkMatch[1]);
    const hrefRaw = normalizeCitationPreview(markdownLinkMatch[2]);
    const hrefValue = hrefRaw.split(/\s+/, 1)[0] || "";
    const target = normalizeReferenceTargetFromHref(hrefValue);
    if (target && label && target !== label) return `${target} | ${label}`;
    return target || label;
  };

  const collectCitationDefinitions = (markdown) => {
    const normalized = normalizeMarkdownLineEndings(markdown);
    const definitions = [];
    const seenKeys = new Set();
    const seenNumbers = new Set();

    const addKeyDefinition = (seedKey, target, label) => {
      const nextKey = suggestCitationKey(seedKey || label || target, seenKeys);
      if (!nextKey || seenKeys.has(nextKey)) return;
      seenKeys.add(nextKey);
      const preview = normalizeCitationPreview(label || target || nextKey) || nextKey;
      definitions.push({
        type: "key",
        key: nextKey,
        number: 0,
        preview,
        marker: `\\cite{${nextKey}}`,
      });
    };

    const addNumberDefinition = (numberValue, payload) => {
      const parsedNumber = Number.parseInt(String(numberValue || ""), 10);
      if (!Number.isFinite(parsedNumber) || parsedNumber <= 0 || seenNumbers.has(parsedNumber)) return;
      const parsedBody = splitDefinitionBody(payload);
      const target = normalizeCitationPreview(parsedBody.target || "");
      const label = normalizeCitationPreview(parsedBody.label || "");
      const preview = label || target || `Referencia ${parsedNumber}`;
      seenNumbers.add(parsedNumber);
      definitions.push({
        type: "number",
        key: "",
        number: parsedNumber,
        preview,
        marker: `\\cite{${parsedNumber}}`,
      });
    };

    normalized.replace(/\\bib\{([^{}\n]+)\}/gi, (_match, rawPayload) => {
      const parsed = parseBibMacroPayload(rawPayload);
      if (!parsed) return _match;
      addKeyDefinition(parsed.key, parsed.target, parsed.label);
      return _match;
    });

    normalized.split("\n").forEach((line) => {
      const match = String(line || "").match(/^\s*\[(\d{1,4})\]\s*:\s*(.+?)\s*$/);
      if (!match) return;
      addNumberDefinition(match[1], match[2]);
    });

    const lines = normalized.split("\n");
    let inReferences = false;
    let nextAutoNumber = 1;
    for (const line of lines) {
      if (/^\s*#{1,6}\s+references\s*$/i.test(line)) {
        inReferences = true;
        continue;
      }
      if (inReferences && /^\s*#{1,6}\s+\S+/.test(line)) {
        inReferences = false;
      }
      if (!inReferences) continue;

      const trimmed = String(line || "").trim();
      if (!trimmed) continue;
      if (/^\s*\[(\d{1,4})\]\s*:/.test(trimmed) || /^\s*\\bib\{[^{}\n]+\}\s*$/i.test(trimmed)) {
        continue;
      }

      const orderedMatch = trimmed.match(/^(\d{1,4})[.)]\s+(.+?)$/);
      if (orderedMatch) {
        addNumberDefinition(orderedMatch[1], referencePayloadFromListItem(orderedMatch[2]));
        continue;
      }

      const bulletMatch = trimmed.match(/^[-*+]\s+(.+?)$/);
      if (!bulletMatch) continue;
      while (seenNumbers.has(nextAutoNumber)) {
        nextAutoNumber += 1;
      }
      addNumberDefinition(nextAutoNumber, referencePayloadFromListItem(bulletMatch[1]));
      nextAutoNumber += 1;
    }

    return definitions;
  };

  const getSourceCitationOptions = () => {
    const rawValue = getComponentValue(SOURCE_CITATION_OPTIONS_ID);
    if (!rawValue) return [];
    try {
      const parsed = JSON.parse(rawValue);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .map((entry) => {
          const slug = String(entry?.slug || "").trim().toLowerCase();
          const rawKind = String(entry?.kind || "").trim().toLowerCase();
          const kind = rawKind === "theory" ? "theory" : "source";
          const target = String(entry?.target || `${kind}:${slug}`)
            .trim()
            .toLowerCase();
          return {
            id: Number.parseInt(String(entry?.id ?? "0"), 10) || 0,
            slug,
            name: String(entry?.name || "").trim(),
            kind,
            target,
          };
        })
        .filter((entry) => entry.slug && /^(?:source|theory):/.test(entry.target));
    } catch (error) {
      void error;
      return [];
    }
  };

  const buildBibDefinitionBody = (targetValue, labelValue) => {
    const target = String(targetValue || "").trim();
    const label = String(labelValue || "").trim();
    if (!target) return "";
    if (!label) return target;
    return `${target} | ${label}`;
  };

  const buildBibMacroLine = (entry) => {
    const key = normalizeCitationKey(entry?.key || "");
    const definitionBody = buildBibDefinitionBody(entry?.target || "", entry?.label || "");
    if (!key || !definitionBody) return "";
    return `\\bib{${key} | ${definitionBody}}`;
  };

  const ensureReferencesHeading = (lines) => {
    let headingIndex = lines.findIndex((line) => /^\s*#{1,6}\s+references\s*$/i.test(line));
    if (headingIndex >= 0) return headingIndex;
    while (lines.length && !String(lines[lines.length - 1] || "").trim()) {
      lines.pop();
    }
    if (lines.length) {
      lines.push("");
    }
    lines.push("## References");
    return lines.length - 1;
  };

  const upsertBibDefinition = (markdown, entry) => {
    const nextLine = buildBibMacroLine(entry);
    if (!nextLine) return normalizeMarkdownLineEndings(markdown);
    const key = normalizeCitationKey(entry?.key || "");
    if (!key) return normalizeMarkdownLineEndings(markdown);

    const lines = normalizeMarkdownLineEndings(markdown).split("\n");
    const existingLineIndex = lines.findIndex((line) => {
      const match = String(line || "").match(/^\s*\\bib\{([^{}\n]+)\}\s*$/i);
      if (!match) return false;
      const parsed = parseBibMacroPayload(match[1]);
      const parsedKey = normalizeCitationKey(parsed?.key || "");
      return parsedKey === key;
    });
    if (existingLineIndex >= 0) {
      lines[existingLineIndex] = nextLine;
      return lines.join("\n");
    }

    const headingIndex = ensureReferencesHeading(lines);
    let lastReferenceIndex = headingIndex;
    for (let idx = headingIndex + 1; idx < lines.length; idx += 1) {
      const row = String(lines[idx] || "");
      if (/^\s*\[\d{1,4}\]\s*:/.test(row) || /^\s*\\bib\{[^{}\n]+\}\s*$/i.test(row)) {
        lastReferenceIndex = idx;
      }
    }

    const insertIndex = lastReferenceIndex > headingIndex ? lastReferenceIndex + 1 : headingIndex + 1;
    lines.splice(insertIndex, 0, nextLine);
    return lines.join("\n");
  };

  const capturePreviewSelectionRange = () => {
    if (!isCompiledMode()) return null;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement) || !isElementVisible(editor)) return null;
    const selection = window.getSelection();
    if (!selection || selection.rangeCount < 1) return null;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) {
      return null;
    }
    return range.cloneRange();
  };

  const insertCitationMarkerIntoPreview = (marker, preferredRange = null) => {
    if (!isCompiledMode()) return false;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement) || !isElementVisible(editor)) return false;

    const selection = window.getSelection();
    const selectionInsideEditor =
      selection &&
      selection.rangeCount > 0 &&
      editor.contains(selection.anchorNode) &&
      editor.contains(selection.focusNode);
    if (!selectionInsideEditor) {
      const preferredInsideEditor =
        preferredRange instanceof Range &&
        editor.contains(preferredRange.startContainer) &&
        editor.contains(preferredRange.endContainer);
      if (preferredInsideEditor) {
        selection?.removeAllRanges();
        selection?.addRange(preferredRange);
        editor.focus();
      } else {
        const range = document.createRange();
        range.selectNodeContents(editor);
        range.collapse(false);
        selection?.removeAllRanges();
        selection?.addRange(range);
        editor.focus();
      }
    }

    insertTextAtCursor(marker);
    scheduleSyncRawFromPreview();
    return true;
  };

  const resolveButtonClickHost = (host) => {
    if (!(host instanceof HTMLElement)) return null;
    const nestedButton =
      host.querySelector("button") ||
      host.shadowRoot?.querySelector("button");
    if (nestedButton instanceof HTMLElement) return nestedButton;
    return host;
  };

  const openSelectionDialog = ({
    title,
    options,
    getOptionLabel,
    searchPlaceholder = "Buscar...",
    confirmLabel = "Seleccionar",
  }) =>
    new Promise((resolve) => {
      const source = Array.isArray(options) ? options : [];
      if (!source.length) {
        resolve(null);
        return;
      }
      const optionLabel = typeof getOptionLabel === "function"
        ? getOptionLabel
        : (entry) => String(entry?.label || entry?.name || entry || "");

      const overlay = document.createElement("div");
      overlay.style.position = "fixed";
      overlay.style.inset = "0";
      overlay.style.background = "rgba(15, 23, 42, 0.45)";
      overlay.style.display = "flex";
      overlay.style.alignItems = "center";
      overlay.style.justifyContent = "center";
      overlay.style.padding = "1rem";
      overlay.style.zIndex = "3000";

      const dialog = document.createElement("div");
      dialog.style.width = "min(620px, 96vw)";
      dialog.style.maxHeight = "86vh";
      dialog.style.background = "#ffffff";
      dialog.style.borderRadius = "12px";
      dialog.style.border = "1px solid #cbd5e1";
      dialog.style.boxShadow = "0 24px 40px rgba(15, 23, 42, 0.3)";
      dialog.style.display = "grid";
      dialog.style.gridTemplateRows = "auto auto 1fr auto";
      dialog.style.gap = "0.55rem";
      dialog.style.padding = "0.85rem";

      const heading = document.createElement("h3");
      heading.textContent = String(title || "Seleccionar");
      heading.style.margin = "0";
      heading.style.fontSize = "1.03rem";
      heading.style.color = "#0f172a";

      const searchInput = document.createElement("input");
      searchInput.type = "search";
      searchInput.placeholder = searchPlaceholder;
      searchInput.style.border = "1px solid #cbd5e1";
      searchInput.style.borderRadius = "9px";
      searchInput.style.padding = "0.5rem 0.62rem";

      const select = document.createElement("select");
      select.size = 12;
      select.style.width = "100%";
      select.style.minHeight = "240px";
      select.style.border = "1px solid #cbd5e1";
      select.style.borderRadius = "10px";
      select.style.padding = "0.35rem";
      select.style.fontSize = "0.95rem";

      const footer = document.createElement("div");
      footer.style.display = "flex";
      footer.style.justifyContent = "flex-end";
      footer.style.gap = "0.5rem";

      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.textContent = "Cancelar";
      cancelBtn.style.border = "1px solid #cbd5e1";
      cancelBtn.style.background = "#ffffff";
      cancelBtn.style.borderRadius = "9px";
      cancelBtn.style.padding = "0.45rem 0.75rem";
      cancelBtn.style.cursor = "pointer";

      const confirmBtn = document.createElement("button");
      confirmBtn.type = "button";
      confirmBtn.textContent = confirmLabel;
      confirmBtn.style.border = "1px solid #1d4ed8";
      confirmBtn.style.background = "#2563eb";
      confirmBtn.style.color = "#ffffff";
      confirmBtn.style.borderRadius = "9px";
      confirmBtn.style.padding = "0.45rem 0.85rem";
      confirmBtn.style.cursor = "pointer";

      footer.appendChild(cancelBtn);
      footer.appendChild(confirmBtn);

      dialog.appendChild(heading);
      dialog.appendChild(searchInput);
      dialog.appendChild(select);
      dialog.appendChild(footer);
      overlay.appendChild(dialog);
      document.body.appendChild(overlay);

      let filteredIndexes = [];
      const teardown = (result) => {
        overlay.remove();
        resolve(result);
      };

      const renderOptions = () => {
        const needle = String(searchInput.value || "").trim().toLowerCase();
        filteredIndexes = [];
        select.innerHTML = "";
        source.forEach((entry, idx) => {
          const label = String(optionLabel(entry) || "").trim();
          if (!label) return;
          if (needle && !label.toLowerCase().includes(needle)) return;
          filteredIndexes.push(idx);
          const optionNode = document.createElement("option");
          optionNode.value = String(idx);
          optionNode.textContent = label;
          select.appendChild(optionNode);
        });
        if (select.options.length) {
          select.selectedIndex = 0;
          return;
        }
        const emptyNode = document.createElement("option");
        emptyNode.value = "";
        emptyNode.textContent = "Sin coincidencias.";
        emptyNode.disabled = true;
        select.appendChild(emptyNode);
      };

      const confirmSelection = () => {
        const selectedValue = String(select.value || "").trim();
        if (!selectedValue) {
          teardown(null);
          return;
        }
        const sourceIndex = Number.parseInt(selectedValue, 10);
        if (!Number.isFinite(sourceIndex) || sourceIndex < 0 || sourceIndex >= source.length) {
          teardown(null);
          return;
        }
        teardown(source[sourceIndex]);
      };

      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) {
          teardown(null);
        }
      });
      cancelBtn.addEventListener("click", () => teardown(null));
      confirmBtn.addEventListener("click", confirmSelection);
      select.addEventListener("dblclick", confirmSelection);
      select.addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        confirmSelection();
      });
      searchInput.addEventListener("input", renderOptions);
      searchInput.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          teardown(null);
          return;
        }
        if (event.key !== "Enter") return;
        event.preventDefault();
        if (filteredIndexes.length === 1) {
          const onlyIndex = filteredIndexes[0];
          if (Number.isFinite(onlyIndex)) {
            teardown(source[onlyIndex] || null);
            return;
          }
        }
        confirmSelection();
      });

      renderOptions();
      window.setTimeout(() => searchInput.focus(), 0);
    });

  const requestBibliographyEntry = async (markdown) => {
    const existingEntries = collectBibDefinitions(markdown);
    const existingKeys = new Set(existingEntries.map((entry) => entry.key));

    const mode = await openSelectionDialog({
      title: "Crear entrada bibliográfica",
      options: [
        { id: "source", label: "Fuente interna (de la lista de fuentes)" },
        { id: "theory", label: "Teoría interna (de la lista de teorías)" },
        { id: "url", label: "URL externa" },
        { id: "text", label: "Referencia de texto manual" },
      ],
      getOptionLabel: (entry) => entry.label,
      searchPlaceholder: "Filtrar modo...",
      confirmLabel: "Continuar",
    });
    if (!mode || !mode.id) return null;

    if (mode.id === "source" || mode.id === "theory") {
      const referenceKind = mode.id === "theory" ? "theory" : "source";
      const sourceOptions = getSourceCitationOptions().filter(
        (entry) => String(entry?.kind || "source").trim().toLowerCase() === referenceKind,
      );
      if (!sourceOptions.length) {
        if (referenceKind === "theory") {
          window.alert("No se encontraron tarjetas de teoría. Crea una en Teorías o usa el modo URL/Texto.");
        } else {
          window.alert("No se encontraron tarjetas de fuente. Crea una en Fuentes o usa el modo URL/Texto.");
        }
        return null;
      }
      const selectedSource = await openSelectionDialog({
        title: referenceKind === "theory" ? "Seleccionar tarjeta de teoría" : "Seleccionar tarjeta de fuente",
        options: sourceOptions,
        getOptionLabel: (entry) => `${entry.name || entry.slug} (${entry.slug})`,
        searchPlaceholder:
          referenceKind === "theory"
            ? "Buscar teoría por nombre o slug..."
            : "Buscar fuente por nombre o slug...",
        confirmLabel: referenceKind === "theory" ? "Usar teoría" : "Usar fuente",
      });
      if (!selectedSource || !selectedSource.slug) return null;

      const labelInput = window.prompt(
        referenceKind === "theory"
          ? "Etiqueta opcional (déjalo vacío para usar el nombre de la teoría)."
          : "Etiqueta opcional (déjalo vacío para usar el nombre de la fuente).",
        String(selectedSource.name || ""),
      );
      if (labelInput === null) return null;
      const label = String(labelInput || "").trim();
      const suggestedKey = suggestCitationKey(selectedSource.slug || label || "ref", existingKeys);
      const keyInput = window.prompt("Clave de cita para \\cite{clave}:", suggestedKey);
      if (keyInput === null) return null;
      const key = normalizeCitationKey(keyInput);
      if (!key) {
        window.alert("La clave de cita debe contener letras o números.");
        return null;
      }
      return {
        key,
        target: String(selectedSource.target || `${referenceKind}:${selectedSource.slug}`)
          .trim()
          .toLowerCase(),
        label,
      };
    }

    if (mode.id === "url") {
      const urlInput = window.prompt("URL de referencia (https://...):", "");
      if (urlInput === null) return null;
      const target = String(urlInput || "").trim();
      if (!/^https?:\/\//i.test(target)) {
        window.alert("Introduce una URL http(s) válida.");
        return null;
      }
      const labelInput = window.prompt("Etiqueta opcional (ejemplo: Wikipedia):", "");
      if (labelInput === null) return null;
      const label = String(labelInput || "").trim();
      const suggestedKey = suggestCitationKey(label || target, existingKeys);
      const keyInput = window.prompt("Clave de cita para \\cite{clave}:", suggestedKey);
      if (keyInput === null) return null;
      const key = normalizeCitationKey(keyInput);
      if (!key) {
        window.alert("La clave de cita debe contener letras o números.");
        return null;
      }
      return { key, target, label };
    }

    const textInput = window.prompt("Texto de referencia (ejemplo: notas de entrevista, 2025):", "");
    if (textInput === null) return null;
    const target = String(textInput || "").trim();
    if (!target) {
      window.alert("El texto de referencia no puede estar vacío.");
      return null;
    }
    const suggestedKey = suggestCitationKey(target, existingKeys);
    const keyInput = window.prompt("Clave de cita para \\cite{clave}:", suggestedKey);
    if (keyInput === null) return null;
    const key = normalizeCitationKey(keyInput);
    if (!key) {
      window.alert("La clave de cita debe contener letras o números.");
      return null;
    }
    return { key, target, label: "" };
  };

  const bindBibliographyInsertButton = () => {
    const host = document.getElementById(PROPOSAL_BIB_BUTTON_ID);
    if (!(host instanceof HTMLElement) || host.dataset.bibBound === "1") return;
    const buttonHost = resolveButtonClickHost(host);
    if (!(buttonHost instanceof HTMLElement)) return;

    host.dataset.bibBound = "1";
    let swallowNextClick = false;
    const activate = async (event) => {
      event.preventDefault();
      event.stopPropagation();

      const rawTextarea = getRawTextarea();
      if (!(rawTextarea instanceof HTMLTextAreaElement)) return;

      if (isCompiledMode()) {
        syncRawFromPreview({ emitEvents: false });
      }
      const bibliographyEntry = await requestBibliographyEntry(rawTextarea.value);
      if (!bibliographyEntry) return;

      const nextMarkdown = upsertBibDefinition(rawTextarea.value, bibliographyEntry);
      setRawTextareaValue(rawTextarea, nextMarkdown);
      if (isCompiledMode()) {
        lastCompiledPreviewMarkdown = "";
        scheduleCompiledPreviewRerender({ immediate: true, preserveSelection: false });
      }
      showSuccessToast(`Guardado \\bib{${bibliographyEntry.key}}.`);
    };

    buttonHost.addEventListener("pointerdown", (event) => {
      if ("button" in event && event.button !== 0) return;
      swallowNextClick = true;
      void activate(event);
    });

    buttonHost.addEventListener("click", (event) => {
      if (swallowNextClick) {
        swallowNextClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      void activate(event);
    });
  };

  const bindCitationInsertButton = () => {
    const host = document.getElementById(PROPOSAL_CITE_BUTTON_ID);
    if (!(host instanceof HTMLElement) || host.dataset.citeBound === "1") return;
    const buttonHost = resolveButtonClickHost(host);
    if (!(buttonHost instanceof HTMLElement)) return;

    host.dataset.citeBound = "1";
    let swallowNextClick = false;
    const activate = async (event) => {
      event.preventDefault();
      event.stopPropagation();

      const textarea = getRawTextarea();
      const markdownValue = textarea instanceof HTMLTextAreaElement ? textarea.value : "";
      const citationEntries = collectCitationDefinitions(markdownValue);
      const keyCitationEntries = citationEntries.filter((entry) => entry?.type === "key");
      const selectableEntries = keyCitationEntries.length ? keyCitationEntries : citationEntries;
      if (!selectableEntries.length) {
        window.alert("Create at least one reference first (Bib button or References section).");
        return;
      }
      const preferredPreviewRange = capturePreviewSelectionRange();
      const selectedEntry = await openSelectionDialog({
        title: "Insert citation",
        options: selectableEntries,
        getOptionLabel: (entry) => `${entry.marker} - ${entry.preview}`,
        searchPlaceholder: "Buscar cita...",
        confirmLabel: "Insert",
      });
      if (!selectedEntry || !selectedEntry.marker) return;

      const marker = String(selectedEntry.marker || "");
      const insertedAtCursor =
        (textarea instanceof HTMLTextAreaElement &&
          document.activeElement === textarea &&
          insertTextIntoTextarea(textarea, marker)) ||
        insertCitationMarkerIntoPreview(marker, preferredPreviewRange) ||
        (textarea instanceof HTMLTextAreaElement && appendTextToTextarea(textarea, marker));
      if (!insertedAtCursor) {
        window.alert("No se pudo insertar el marcador de cita.");
        return;
      }

      if (isCompiledMode()) {
        lastCompiledPreviewMarkdown = "";
        scheduleCompiledPreviewRerender({ immediate: true, preserveSelection: true });
      }
      showSuccessToast(`Inserted ${marker}.`);
    };

    buttonHost.addEventListener("pointerdown", (event) => {
      if ("button" in event && event.button !== 0) return;
      swallowNextClick = true;
      void activate(event);
    });

    buttonHost.addEventListener("click", (event) => {
      if (swallowNextClick) {
        swallowNextClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      void activate(event);
    });
  };

  const normalizeSingleLineText = (value) =>
    String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const normalizeTagValue = (value) => normalizeSingleLineText(value).toLowerCase();

  const parseTagValues = (rawValue) => {
    const parsed = [];
    const seen = new Set();
    String(rawValue || "")
      .split(/[\n,]+/)
      .forEach((part) => {
        const cleaned = normalizeTagValue(part);
        if (!cleaned || seen.has(cleaned)) return;
        seen.add(cleaned);
        parsed.push(cleaned);
      });
    return parsed;
  };

  const serializeTagValues = (tags) => tags.join(", ");

  const getDetailCard = () => document.querySelector(DETAIL_CARD_SELECTOR);

  const getCardTitleNode = (card) => card?.querySelector(DETAIL_TITLE_SELECTOR) || null;

  const getCardBucketNode = (card) => card?.querySelector(DETAIL_BUCKET_SELECTOR) || null;

  const getCardTagsHost = (card) => card?.querySelector(DETAIL_TAGS_SELECTOR) || null;

  const getCardTotalTimeNode = (card) => card?.querySelector(DETAIL_TOTAL_TIME_VALUE_SELECTOR) || null;

  const getCardPersonsNode = (card) => card?.querySelector(DETAIL_PERSONS_VALUE_SELECTOR) || null;

  const getCardMediaNode = (card) => card?.querySelector(DETAIL_MEDIA_SELECTOR) || null;

  const getCardImageNode = (card) => card?.querySelector(DETAIL_IMAGE_SELECTOR) || null;

  const getCardInlineActionsSlot = () => document.getElementById(DETAIL_INLINE_ACTIONS_SLOT_ID);

  const getCardProposalActionsRow = () => {
    const row = document.getElementById(CARD_PROPOSAL_ACTIONS_ID);
    if (row instanceof HTMLElement) {
      cardProposalActionsRowCache = row;
      return row;
    }
    return cardProposalActionsRowCache instanceof HTMLElement ? cardProposalActionsRowCache : null;
  };

  const ensureCardProposalActionsMounted = () => {
    const shell = document.getElementById(CARD_PROPOSAL_SHELL_ID);
    const row = getCardProposalActionsRow();
    if (!(shell instanceof HTMLElement) || !(row instanceof HTMLElement)) {
      return { shell: null, row: null };
    }

    if (!row.isConnected || !document.body.contains(row) || !(row.parentElement instanceof HTMLElement)) {
      shell.appendChild(row);
      row.classList.remove("the-list-card-actions--inline");
    }

    return { shell, row };
  };

  const getCardImageUploadHost = () => document.getElementById(CARD_IMAGE_UPLOAD_BUTTON_ID);

  const getCardImageFileInput = () => {
    const host = getCardImageUploadHost();
    if (!(host instanceof HTMLElement)) return null;
    return (
      host.querySelector("input[type='file']") ||
      host.shadowRoot?.querySelector("input[type='file']") ||
      null
    );
  };

  const clickElement = (node) => {
    if (!(node instanceof HTMLElement)) return false;
    try {
      node.click();
      return true;
    } catch (error) {
      void error;
      return false;
    }
  };

  const triggerCardImagePicker = () => {
    cropDebugLog("gradio_picker.open.start");
    const host = getCardImageUploadHost();
    if (!(host instanceof HTMLElement)) {
      cropDebugLog("gradio_picker.open.host_missing");
      return false;
    }

    const input =
      host.querySelector("input[type='file']") ||
      host.shadowRoot?.querySelector("input[type='file']");
    if (input instanceof HTMLInputElement) {
      if (typeof input.showPicker === "function") {
        try {
          input.showPicker();
          cropDebugLog("gradio_picker.open.showPicker_ok");
          return true;
        } catch (error) {
          cropDebugLog("gradio_picker.open.showPicker_error", {
            message: String(error?.message || error || "unknown"),
          });
          void error;
        }
      }
      if (clickElement(input)) {
        cropDebugLog("gradio_picker.open.input_click_ok");
        return true;
      }
    }

    const button = host.querySelector("button") || host.shadowRoot?.querySelector("button");
    if (button instanceof HTMLButtonElement && clickElement(button)) {
      cropDebugLog("gradio_picker.open.button_click_ok");
      return true;
    }
    const hostClicked = clickElement(host);
    cropDebugLog("gradio_picker.open.host_click_fallback", { hostClicked });
    return hostClicked;
  };

  const ensureCardImageNativePicker = () => {
    if (
      cardImageNativePicker instanceof HTMLInputElement &&
      document.body.contains(cardImageNativePicker)
    ) {
      cropDebugLog("native_picker.reuse");
      return cardImageNativePicker;
    }

    cropDebugLog("native_picker.create");
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.tabIndex = -1;
    input.className = "the-list-card-image-native-picker";
    input.setAttribute("aria-hidden", "true");
    input.style.position = "fixed";
    input.style.left = "-99999px";
    input.style.width = "1px";
    input.style.height = "1px";
    input.style.opacity = "0";
    input.style.pointerEvents = "none";
    input.addEventListener("change", () => {
      cropDebugLog("native_picker.change.start");
      const file = readFirstFileFromNode(input);
      input.value = "";
      if (!(file instanceof File)) {
        cropDebugLog("native_picker.change.no_file");
        return;
      }
      if (!isElementVisible(document.getElementById(CARD_PROPOSAL_SHELL_ID))) {
        cropDebugLog("native_picker.change.shell_hidden");
        return;
      }

      const targetInput = getCardImageFileInput();
      if (!(targetInput instanceof HTMLInputElement)) {
        cropDebugLog("native_picker.change.target_missing");
        void openCardImageCropModal(null, file);
        return;
      }
      cropDebugLog("native_picker.change.file_selected", {
        name: file.name,
        size: file.size,
        type: file.type,
      });
      void openCardImageCropModal(targetInput, file);
    });
    document.body.appendChild(input);
    cardImageNativePicker = input;
    return input;
  };

  const openCardImageNativePicker = () => {
    cropDebugLog("native_picker.open.start");
    const picker = ensureCardImageNativePicker();
    if (!(picker instanceof HTMLInputElement)) {
      cropDebugLog("native_picker.open.missing_picker");
      return false;
    }
    picker.value = "";
    if (typeof picker.showPicker === "function") {
      try {
        picker.showPicker();
        cropDebugLog("native_picker.open.showPicker_ok");
        return true;
      } catch (error) {
        cropDebugLog("native_picker.open.showPicker_error", {
          message: String(error?.message || error || "unknown"),
        });
        void error;
      }
    }
    const clicked = clickElement(picker);
    cropDebugLog("native_picker.open.click_fallback", { clicked });
    return clicked;
  };

  const readFirstFileFromNode = (node) => {
    if (!(node instanceof HTMLInputElement)) return null;
    if (node.type !== "file") return null;
    return node.files && node.files[0] ? node.files[0] : null;
  };

  const readFileInputNode = (node) => {
    if (!(node instanceof HTMLInputElement)) return null;
    if (node.type !== "file") return null;
    return node;
  };

  const getCardImageFileInputCandidates = () => {
    const host = getCardImageUploadHost();
    if (!(host instanceof HTMLElement)) return [];
    const direct = readFileInputNode(host.querySelector("input[type='file']"));
    const shadow = readFileInputNode(host.shadowRoot?.querySelector("input[type='file']"));
    return [direct, shadow].filter((node) => node instanceof HTMLInputElement);
  };

  const isCardImageFileInput = (node) => {
    if (!(node instanceof HTMLInputElement)) return false;
    return getCardImageFileInputCandidates().some((candidate) => candidate === node);
  };

  const extractFileInputFromChangeEvent = (event) => {
    const direct = readFileInputNode(event.target);
    if (direct) return direct;

    if (typeof event.composedPath === "function") {
      const path = event.composedPath();
      for (const node of path) {
        const inputNode = readFileInputNode(node);
        if (inputNode) return inputNode;
      }
    }

    return readFileInputNode(getCardImageFileInput());
  };

  const applyInlineCardImagePreview = (file) => {
    if (!(file instanceof File)) return;
    if (!String(file.type || "").toLowerCase().startsWith("image/")) return;
    const card = getDetailCard();
    const image = getCardImageNode(card);
    if (!(image instanceof HTMLImageElement)) return;
    if (inlinePreviewUrl) {
      URL.revokeObjectURL(inlinePreviewUrl);
    }
    inlinePreviewUrl = URL.createObjectURL(file);
    image.src = inlinePreviewUrl;
    cropDebugLog("inline_preview.updated", {
      name: file.name,
      size: file.size,
      type: file.type,
    });
  };

  const dockCardProposalActions = () => {
    const { shell, row } = ensureCardProposalActionsMounted();
    if (!(shell instanceof HTMLElement) || !(row instanceof HTMLElement)) return;
    const slot = getCardInlineActionsSlot();
    const editing = isElementVisible(shell);

    if (editing && slot instanceof HTMLElement) {
      if (row.parentElement !== slot) {
        slot.appendChild(row);
      }
      row.classList.add("the-list-card-actions--inline");
      return;
    }

    if (row.parentElement !== shell) {
      shell.appendChild(row);
    }
    row.classList.remove("the-list-card-actions--inline");
  };

  const setInlineFieldMode = (node, active) => {
    if (!(node instanceof HTMLElement)) return;
    node.setAttribute("contenteditable", active ? "true" : "false");
    node.setAttribute("spellcheck", "true");
    node.classList.add("person-detail-card__field");
    node.classList.toggle("person-detail-card__field--active", active);
    if (!active) {
      node.removeAttribute("role");
      return;
    }
    node.setAttribute("role", "textbox");
  };

  const bindInlineSingleLineField = (node, componentId) => {
    if (!(node instanceof HTMLElement) || node.dataset.inlineFieldBound === "1") return;
    node.dataset.inlineFieldBound = "1";

    node.addEventListener("input", () => {
      setComponentValue(componentId, normalizeSingleLineText(node.textContent || ""));
    });
    node.addEventListener("blur", () => {
      const cleaned = normalizeSingleLineText(node.textContent || "");
      node.textContent = cleaned;
      setComponentValue(componentId, cleaned);
    });
    node.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      node.blur();
    });
    node.addEventListener("paste", (event) => {
      const text = (event.clipboardData || window.clipboardData)?.getData("text/plain");
      if (!text) return;
      event.preventDefault();
      if (document.queryCommandSupported?.("insertText")) {
        document.execCommand("insertText", false, text);
        return;
      }
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) return;
      selection.deleteFromDocument();
      selection.getRangeAt(0).insertNode(document.createTextNode(text));
    });
  };

  const resolveChipFieldId = (host, explicitFieldId) =>
    normalizeSingleLineText(explicitFieldId || host?.dataset?.inlineFieldId || CARD_PROPOSAL_TAGS_ID);

  const setTagRemovalState = (tagNode, removed, chipLabel = "tag") => {
    if (!(tagNode instanceof HTMLElement)) return;
    tagNode.dataset.removed = removed ? "1" : "0";
    tagNode.classList.toggle("person-tag--pending-remove", removed);
    const button = tagNode.querySelector(".person-tag__remove-btn");
    if (button instanceof HTMLButtonElement) {
      const tagLabel = tagNode.dataset.tagValue || "tag";
      button.setAttribute(
        "aria-label",
        removed ? `Restore ${chipLabel} ${tagLabel}` : `Mark ${chipLabel} ${tagLabel} for removal`,
      );
      button.title = removed ? `Restore "${tagLabel}"` : `Remove "${tagLabel}"`;
    }
  };

  const collectEditableTagNodes = (host) =>
    Array.from(host.querySelectorAll(".person-tag--editable"));

  const collectActiveTagValues = (host) =>
    collectEditableTagNodes(host)
      .filter((node) => node.dataset.removed !== "1")
      .map((node) => normalizeTagValue(node.dataset.tagValue || ""))
      .filter(Boolean);

  const syncCardTagFieldFromHost = (host, fieldId = "") => {
    const tags = collectActiveTagValues(host);
    setComponentValue(resolveChipFieldId(host, fieldId), serializeTagValues(tags));
  };

  const findEditableTagNode = (host, tagValue) =>
    collectEditableTagNodes(host).find(
      (node) => normalizeTagValue(node.dataset.tagValue || "") === normalizeTagValue(tagValue),
    );

  const mergeUniqueTagValues = (...sources) => {
    const merged = [];
    const seen = new Set();
    sources.forEach((source) => {
      const values = Array.isArray(source) ? source : [source];
      values.forEach((value) => {
        const normalized = normalizeTagValue(value);
        if (!normalized || seen.has(normalized)) return;
        seen.add(normalized);
        merged.push(normalized);
      });
    });
    return merged;
  };

  const readTagCatalogFromHost = (host) => {
    if (!(host instanceof HTMLElement)) return [];
    const rawCatalog = String(host.dataset.tagCatalog || "").trim();
    if (!rawCatalog) return [];
    try {
      const parsed = JSON.parse(rawCatalog);
      if (!Array.isArray(parsed)) return [];
      return mergeUniqueTagValues(parsed);
    } catch (error) {
      void error;
      return [];
    }
  };

  const writeTagCatalogToHost = (host, tagValues) => {
    if (!(host instanceof HTMLElement)) return;
    const normalized = mergeUniqueTagValues(tagValues);
    host.dataset.tagCatalog = JSON.stringify(normalized);
  };

  const ensureTagCatalogIncludes = (host, tagValues) => {
    const merged = mergeUniqueTagValues(readTagCatalogFromHost(host), tagValues);
    writeTagCatalogToHost(host, merged);
    return merged;
  };

  const addOrRestoreEditableTag = (host, insertBeforeNode, tagValue, options = {}) => {
    const chipLabel = normalizeSingleLineText(options.chipLabel || "tag") || "tag";
    const fieldId = resolveChipFieldId(host, options.fieldId);
    const normalizedTag = normalizeTagValue(tagValue);
    if (!normalizedTag) return false;
    const existing = findEditableTagNode(host, normalizedTag);
    if (existing) {
      setTagRemovalState(existing, false, chipLabel);
      syncCardTagFieldFromHost(host, fieldId);
      ensureTagCatalogIncludes(host, [normalizedTag]);
      return true;
    }
    host.insertBefore(buildEditableTagNode(normalizedTag, host, false, options), insertBeforeNode);
    syncCardTagFieldFromHost(host, fieldId);
    ensureTagCatalogIncludes(host, [normalizedTag]);
    return true;
  };

  const buildEditableTagNode = (tagValue, host, removed = false, options = {}) => {
    const chipLabel = normalizeSingleLineText(options.chipLabel || "tag") || "tag";
    const fieldId = resolveChipFieldId(host, options.fieldId);
    const node = document.createElement("span");
    node.className = "person-tag person-tag--editable";
    node.dataset.tagValue = normalizeTagValue(tagValue);

    const label = document.createElement("span");
    label.className = "person-tag__label";
    label.textContent = normalizeTagValue(tagValue);
    node.appendChild(label);

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "person-tag__remove-btn";
    removeButton.textContent = "x";
    node.appendChild(removeButton);

    removeButton.addEventListener("click", (event) => {
      event.preventDefault();
      const currentlyRemoved = node.dataset.removed === "1";
      setTagRemovalState(node, !currentlyRemoved, chipLabel);
      syncCardTagFieldFromHost(host, fieldId);
    });

    setTagRemovalState(node, removed, chipLabel);
    return node;
  };

  const buildTagAddEditor = (host, addButton, options = {}) => {
    const chipLabel = normalizeSingleLineText(options.chipLabel || "tag") || "tag";
    const editor = document.createElement("span");
    editor.className = "person-detail-card__tag-add-editor";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "person-detail-card__tag-add-input";
    input.placeholder = `nuevo ${chipLabel}`;
    input.autocomplete = "off";
    input.setAttribute("aria-label", `Nuevo ${chipLabel}`);
    editor.appendChild(input);

    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "person-detail-card__tag-add-cancel-btn";
    cancelButton.textContent = "x";
    cancelButton.title = `Cancelar añadir ${chipLabel}`;
    cancelButton.setAttribute("aria-label", `Cancelar añadir ${chipLabel}`);
    editor.appendChild(cancelButton);

    const suggestions = document.createElement("div");
    suggestions.className = "person-detail-card__tag-suggestions";
    suggestions.hidden = true;
    editor.appendChild(suggestions);

    let closed = false;
    const closeEditor = ({ focusButton = false } = {}) => {
      if (closed) return;
      closed = true;
      document.removeEventListener("pointerdown", closeOnOutsidePointerDown, true);
      editor.remove();
      addButton.hidden = false;
      addButton.disabled = false;
      if (focusButton) addButton.focus();
    };

    const renderSuggestions = () => {
      const query = normalizeTagValue(input.value || "");
      suggestions.replaceChildren();
      if (!query) {
        suggestions.hidden = true;
        return;
      }

      const catalog = ensureTagCatalogIncludes(host, collectEditableTagNodes(host).map((node) => node.dataset.tagValue || ""));
      const matches = catalog.filter((tag) => tag.includes(query)).slice(0, TAG_SUGGESTION_LIMIT);
      if (!matches.length) {
        suggestions.hidden = true;
        return;
      }

      matches.forEach((tag) => {
        const optionButton = document.createElement("button");
        optionButton.type = "button";
        optionButton.className = "person-detail-card__tag-suggestion-btn";
        optionButton.textContent = tag;
        optionButton.setAttribute("aria-label", `Usar ${chipLabel} ${tag}`);
        optionButton.addEventListener("mousedown", (event) => {
          event.preventDefault();
        });
        optionButton.addEventListener("click", (event) => {
          event.preventDefault();
          addOrRestoreEditableTag(host, addButton, tag, options);
          input.value = "";
          renderSuggestions();
          input.focus();
        });
        suggestions.appendChild(optionButton);
      });

      suggestions.hidden = false;
    };

    const commitInputValue = () => {
      const normalizedTag = normalizeTagValue(input.value || "");
      if (!normalizedTag) return false;
      addOrRestoreEditableTag(host, addButton, normalizedTag, options);
      input.value = "";
      renderSuggestions();
      input.focus();
      return true;
    };

    const closeOnOutsidePointerDown = (event) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (editor.contains(target) || target === addButton) return;
      closeEditor();
    };

    input.addEventListener("input", () => {
      renderSuggestions();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === ",") {
        event.preventDefault();
        commitInputValue();
        return;
      }
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeEditor({ focusButton: true });
    });

    cancelButton.addEventListener("mousedown", (event) => {
      event.preventDefault();
    });
    cancelButton.addEventListener("click", (event) => {
      event.preventDefault();
      closeEditor({ focusButton: true });
    });

    document.addEventListener("pointerdown", closeOnOutsidePointerDown, true);
    return editor;
  };

  const buildTagAddButton = (host, options = {}) => {
    const chipLabel = normalizeSingleLineText(options.chipLabel || "tag") || "tag";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "person-detail-card__tags-add-btn";
    button.textContent = "+";
    button.title = `Añadir ${chipLabel}`;
    button.setAttribute("aria-label", `Añadir ${chipLabel}`);
    let swallowNextClick = false;

    const activate = (event) => {
      event.preventDefault();
      const existingEditor = host.querySelector(".person-detail-card__tag-add-editor");
      if (existingEditor instanceof HTMLElement) {
        const existingInput = existingEditor.querySelector("input");
        if (existingInput instanceof HTMLInputElement) existingInput.focus();
        return;
      }

      button.hidden = true;
      button.disabled = true;
      const editor = buildTagAddEditor(host, button, options);
      host.insertBefore(editor, button);
      const input = editor.querySelector("input");
      if (input instanceof HTMLInputElement) input.focus();
    };

    button.addEventListener("pointerdown", (event) => {
      if ("button" in event && event.button !== 0) return;
      swallowNextClick = true;
      activate(event);
    });

    button.addEventListener("click", (event) => {
      if (swallowNextClick) {
        swallowNextClick = false;
        event.preventDefault();
        return;
      }
      activate(event);
    });
    return button;
  };

  const renderEditableTags = (host, tags, options = {}) => {
    if (!(host instanceof HTMLElement)) return;
    const fieldId = resolveChipFieldId(host, options.fieldId);
    const normalizedTags = parseTagValues(Array.isArray(tags) ? tags.join(",") : String(tags || ""));
    ensureTagCatalogIncludes(host, normalizedTags);
    host.replaceChildren();
    normalizedTags.forEach((tag) => {
      host.appendChild(buildEditableTagNode(tag, host, false, options));
    });
    host.appendChild(buildTagAddButton(host, options));
    host.classList.add("person-detail-card__tags--editing");
    syncCardTagFieldFromHost(host, fieldId);
  };

  const renderReadonlyTags = (host, tags, options = {}) => {
    if (!(host instanceof HTMLElement)) return;
    const emptyLabel = normalizeSingleLineText(options.emptyLabel || "no-tags") || "no-tags";
    const normalizedTags = parseTagValues(Array.isArray(tags) ? tags.join(",") : String(tags || ""));
    host.replaceChildren();
    host.classList.remove("person-detail-card__tags--editing");
    if (!normalizedTags.length) {
      const muted = document.createElement("span");
      muted.className = "person-tag person-tag--muted";
      muted.textContent = emptyLabel;
      host.appendChild(muted);
      return;
    }
    normalizedTags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = "person-tag";
      chip.textContent = tag;
      host.appendChild(chip);
    });
  };

  const clampValue = (value, min, max) => Math.min(max, Math.max(min, value));

  const markCardImageChangeHandled = (event) => {
    if (!(event instanceof Event)) return;
    handledCardImageChangeEvents.add(event);
  };

  const isCardImageChangeHandled = (event) =>
    event instanceof Event && handledCardImageChangeEvents.has(event);

  const sliderToZoom = (sliderValue, maxZoom) => {
    const parsed = Number.parseFloat(String(sliderValue || 0));
    const ratio = clampValue(parsed / CARD_IMAGE_CROP_ZOOM_STEPS, 0, 1);
    return 1 + (maxZoom - 1) * ratio;
  };

  const zoomToSlider = (zoom, maxZoom) => {
    if (!Number.isFinite(maxZoom) || maxZoom <= 1) return 0;
    const ratio = clampValue((zoom - 1) / (maxZoom - 1), 0, 1);
    return Math.round(ratio * CARD_IMAGE_CROP_ZOOM_STEPS);
  };

  const getCardImageCropScale = (state) => {
    if (!state) return 1;
    const baseScale = Math.max(
      CARD_IMAGE_CROP_VIEW_WIDTH / Math.max(1, state.imageWidth),
      CARD_IMAGE_CROP_VIEW_HEIGHT / Math.max(1, state.imageHeight),
    );
    return baseScale * Math.max(1, state.zoom);
  };

  const clampCardImageCropOffsets = (state) => {
    if (!state) return;
    const scale = getCardImageCropScale(state);
    const drawWidth = state.imageWidth * scale;
    const drawHeight = state.imageHeight * scale;
    const minOffsetX = Math.min(0, CARD_IMAGE_CROP_VIEW_WIDTH - drawWidth);
    const minOffsetY = Math.min(0, CARD_IMAGE_CROP_VIEW_HEIGHT - drawHeight);
    state.scale = scale;
    state.drawWidth = drawWidth;
    state.drawHeight = drawHeight;
    state.offsetX = clampValue(state.offsetX, minOffsetX, 0);
    state.offsetY = clampValue(state.offsetY, minOffsetY, 0);
  };

  const getCardImageCropCanvasPoint = (clientX, clientY) => {
    const canvas = cardImageCropUi?.canvas;
    if (!(canvas instanceof HTMLCanvasElement)) {
      return {
        x: CARD_IMAGE_CROP_VIEW_WIDTH / 2,
        y: CARD_IMAGE_CROP_VIEW_HEIGHT / 2,
        scaleX: 1,
        scaleY: 1,
      };
    }
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) {
      return {
        x: CARD_IMAGE_CROP_VIEW_WIDTH / 2,
        y: CARD_IMAGE_CROP_VIEW_HEIGHT / 2,
        scaleX: 1,
        scaleY: 1,
      };
    }
    const scaleX = CARD_IMAGE_CROP_VIEW_WIDTH / rect.width;
    const scaleY = CARD_IMAGE_CROP_VIEW_HEIGHT / rect.height;
    return {
      x: clampValue((clientX - rect.left) * scaleX, 0, CARD_IMAGE_CROP_VIEW_WIDTH),
      y: clampValue((clientY - rect.top) * scaleY, 0, CARD_IMAGE_CROP_VIEW_HEIGHT),
      scaleX,
      scaleY,
    };
  };

  const teardownCardImageCropState = ({ clearInput = false, restoreFocus = false } = {}) => {
    const state = cardImageCropState;
    if (!state) return;
    cropDebugLog("crop_state.teardown", {
      clearInput,
      restoreFocus,
      hadDragState: Boolean(state.dragState),
      sourceName: state.sourceFile?.name || "",
    });
    if (clearInput && state.fileInput instanceof HTMLInputElement) {
      state.fileInput.value = "";
    }
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
    }
    if (cardImageCropUi?.canvas instanceof HTMLCanvasElement) {
      cardImageCropUi.canvas.classList.remove("is-dragging");
    }
    if (restoreFocus && state.restoreFocus instanceof HTMLElement) {
      try {
        state.restoreFocus.focus();
      } catch (error) {
        void error;
      }
    }
    cardImageCropState = null;
  };

  const syncCardImageCropUi = () => {
    const state = cardImageCropState;
    if (!state || !cardImageCropUi) return;
    if (cardImageCropUi.zoomRange instanceof HTMLInputElement) {
      cardImageCropUi.zoomRange.value = String(zoomToSlider(state.zoom, state.maxZoom));
    }
    if (cardImageCropUi.zoomValue instanceof HTMLElement) {
      cardImageCropUi.zoomValue.textContent = `${Math.round(state.zoom * 100)}%`;
    }
  };

  const renderCardImageCrop = () => {
    const state = cardImageCropState;
    const ui = cardImageCropUi;
    if (!state || !ui) return;
    const canvas = ui.canvas;
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    clampCardImageCropOffsets(state);
    context.clearRect(0, 0, CARD_IMAGE_CROP_VIEW_WIDTH, CARD_IMAGE_CROP_VIEW_HEIGHT);
    context.fillStyle = "#f1f5f9";
    context.fillRect(0, 0, CARD_IMAGE_CROP_VIEW_WIDTH, CARD_IMAGE_CROP_VIEW_HEIGHT);
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(state.image, state.offsetX, state.offsetY, state.drawWidth, state.drawHeight);
    syncCardImageCropUi();
  };

  const setCardImageCropZoom = (
    nextZoom,
    focusX = CARD_IMAGE_CROP_VIEW_WIDTH / 2,
    focusY = CARD_IMAGE_CROP_VIEW_HEIGHT / 2,
  ) => {
    const state = cardImageCropState;
    if (!state) return;

    clampCardImageCropOffsets(state);
    const safeZoom = clampValue(nextZoom, 1, state.maxZoom);
    if (Math.abs(safeZoom - state.zoom) < 0.0001) return;

    const previousScale = Math.max(0.0001, state.scale || getCardImageCropScale(state));
    const imageX = (focusX - state.offsetX) / previousScale;
    const imageY = (focusY - state.offsetY) / previousScale;

    state.zoom = safeZoom;
    const nextScale = getCardImageCropScale(state);
    state.offsetX = focusX - imageX * nextScale;
    state.offsetY = focusY - imageY * nextScale;
    renderCardImageCrop();
  };

  const closeCardImageCropModal = ({ clearInput = false, reason = "unspecified" } = {}) => {
    cropDebugLog("crop_modal.close", {
      reason,
      clearInput,
      hasState: Boolean(cardImageCropState),
    });
    cardImageCropSessionToken += 1;
    const ui = cardImageCropUi;
    if (ui?.modal instanceof HTMLElement) {
      ui.modal.hidden = true;
      ui.modal.classList.remove("is-open");
    }
    document.body.classList.remove("the-list-card-image-crop-open");
    teardownCardImageCropState({ clearInput, restoreFocus: true });
  };

  const cancelCardImageCrop = () => {
    closeCardImageCropModal({
      clearInput: true,
      reason: "cancel_button_or_close",
    });
  };

  const isCardImageCropFreshlyOpened = () => {
    if (!cardImageCropState) return false;
    return Date.now() - Number(cardImageCropState.openedAt || 0) < 1800;
  };

  const onCardImageCropPointerDown = (event) => {
    const state = cardImageCropState;
    const canvas = cardImageCropUi?.canvas;
    if (!state || !(canvas instanceof HTMLCanvasElement)) return;
    event.preventDefault();
    const point = getCardImageCropCanvasPoint(event.clientX, event.clientY);
    state.dragState = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startOffsetX: state.offsetX,
      startOffsetY: state.offsetY,
      scaleX: point.scaleX,
      scaleY: point.scaleY,
    };
    canvas.classList.add("is-dragging");
    if (typeof canvas.setPointerCapture === "function") {
      try {
        canvas.setPointerCapture(event.pointerId);
      } catch (error) {
        void error;
      }
    }
  };

  const onCardImageCropPointerMove = (event) => {
    const state = cardImageCropState;
    if (!state || !state.dragState) return;
    if (event.pointerId !== state.dragState.pointerId) return;
    event.preventDefault();
    const dx = (event.clientX - state.dragState.startClientX) * state.dragState.scaleX;
    const dy = (event.clientY - state.dragState.startClientY) * state.dragState.scaleY;
    state.offsetX = state.dragState.startOffsetX + dx;
    state.offsetY = state.dragState.startOffsetY + dy;
    renderCardImageCrop();
  };

  const onCardImageCropPointerUp = (event) => {
    const state = cardImageCropState;
    const canvas = cardImageCropUi?.canvas;
    if (!state || !state.dragState) return;
    if (event.pointerId !== state.dragState.pointerId) return;
    state.dragState = null;
    if (canvas instanceof HTMLCanvasElement) {
      canvas.classList.remove("is-dragging");
    }
  };

  const onCardImageCropWheel = (event) => {
    if (!cardImageCropState) return;
    event.preventDefault();
    const point = getCardImageCropCanvasPoint(event.clientX, event.clientY);
    const factor = Math.exp(-event.deltaY * CARD_IMAGE_CROP_WHEEL_SENSITIVITY);
    setCardImageCropZoom(cardImageCropState.zoom * factor, point.x, point.y);
  };

  const onCardImageCropZoomInput = (event) => {
    const state = cardImageCropState;
    if (!state) return;
    const sliderValue = event.target instanceof HTMLInputElement ? event.target.value : "0";
    setCardImageCropZoom(sliderToZoom(sliderValue, state.maxZoom));
  };

  const buildCroppedCardImageFile = async () => {
    const state = cardImageCropState;
    if (!state) return null;
    clampCardImageCropOffsets(state);

    const cropWidthInSource = CARD_IMAGE_CROP_VIEW_WIDTH / Math.max(0.0001, state.scale);
    const cropHeightInSource = CARD_IMAGE_CROP_VIEW_HEIGHT / Math.max(0.0001, state.scale);
    const rawCropX = -state.offsetX / Math.max(0.0001, state.scale);
    const rawCropY = -state.offsetY / Math.max(0.0001, state.scale);
    const cropX = clampValue(rawCropX, 0, Math.max(0, state.imageWidth - cropWidthInSource));
    const cropY = clampValue(rawCropY, 0, Math.max(0, state.imageHeight - cropHeightInSource));

    const outputCanvas = document.createElement("canvas");
    outputCanvas.width = CARD_IMAGE_OUTPUT_WIDTH;
    outputCanvas.height = CARD_IMAGE_OUTPUT_HEIGHT;
    const outputContext = outputCanvas.getContext("2d");
    if (!outputContext) return null;
    outputContext.imageSmoothingEnabled = true;
    outputContext.imageSmoothingQuality = "high";
    outputContext.drawImage(
      state.image,
      cropX,
      cropY,
      cropWidthInSource,
      cropHeightInSource,
      0,
      0,
      CARD_IMAGE_OUTPUT_WIDTH,
      CARD_IMAGE_OUTPUT_HEIGHT,
    );

    const blob = await new Promise((resolve) => {
      outputCanvas.toBlob(resolve, "image/png", 0.95);
    });
    if (!(blob instanceof Blob)) return null;

    const sourceName = String(state.sourceFile?.name || "card-image");
    const baseName = sourceName.replace(/\.[a-z0-9]+$/i, "") || "card-image";
    return new File([blob], `${baseName}-360x270.png`, {
      type: "image/png",
      lastModified: Date.now(),
    });
  };

  const commitCardImageCrop = async () => {
    const state = cardImageCropState;
    const ui = cardImageCropUi;
    if (!state || !ui || state.isApplying) {
      cropDebugLog("crop_apply.skip", {
        hasState: Boolean(state),
        hasUi: Boolean(ui),
        isApplying: Boolean(state?.isApplying),
      });
      return;
    }
    cropDebugLog("crop_apply.start", {
      sourceName: state.sourceFile?.name || "",
      sourceSize: state.sourceFile?.size || 0,
    });

    state.isApplying = true;
    if (ui.applyButton instanceof HTMLButtonElement) {
      ui.applyButton.disabled = true;
    }
    try {
      const croppedFile = await buildCroppedCardImageFile();
      if (!(croppedFile instanceof File)) {
        throw new Error("No se pudo crear la imagen recortada.");
      }
      const croppedDataUrl = await fileToDataUrl(croppedFile);
      setComponentValue(CARD_PROPOSAL_IMAGE_DATA_ID, croppedDataUrl);
      applyInlineCardImagePreview(croppedFile);

      cropDebugLog("crop_apply.data_prepared", {
        outputName: croppedFile.name,
        outputSize: croppedFile.size,
        dataLen: croppedDataUrl.length,
      });
      if (state.fileInput instanceof HTMLInputElement && typeof DataTransfer === "function") {
        const transfer = new DataTransfer();
        transfer.items.add(croppedFile);
        state.fileInput.files = transfer.files;
        cardImageCropBypassOnce = true;
        cropDebugLog("crop_apply.dispatch_change");
        state.fileInput.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
      } else {
        cropDebugLog("crop_apply.skip_input_dispatch", {
          hasInput: state.fileInput instanceof HTMLInputElement,
          hasDataTransfer: typeof DataTransfer === "function",
        });
      }
      closeCardImageCropModal({
        clearInput: false,
        reason: "apply_success",
      });
    } catch (error) {
      console.error("Card image crop failed", error);
      cropDebugLog("crop_apply.error", {
        message: String(error?.message || error || "unknown"),
      });
      closeCardImageCropModal({
        clearInput: true,
        reason: "apply_exception",
      });
    } finally {
      if (ui.applyButton instanceof HTMLButtonElement) {
        ui.applyButton.disabled = false;
      }
      if (cardImageCropState) {
        cardImageCropState.isApplying = false;
      }
    }
  };

  const loadImageForCrop = (objectUrl) =>
    new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("No se pudo cargar la imagen para recortarla."));
      image.src = objectUrl;
    });

  const ensureCardImageCropUi = () => {
    if (cardImageCropUi) {
      cropDebugLog("crop_modal.ui_reuse");
      return cardImageCropUi;
    }
    cropDebugLog("crop_modal.ui_create");

    const modal = document.createElement("div");
    modal.id = CARD_IMAGE_CROP_MODAL_ID;
    modal.className = "the-list-card-image-crop-modal";
    modal.hidden = true;
    modal.innerHTML = `
      <button type="button" class="the-list-card-image-crop-modal__backdrop" data-action="backdrop" aria-label="Fondo del cuadro de recorte"></button>
      <section class="the-list-card-image-crop-modal__dialog" role="dialog" aria-modal="true" aria-labelledby="the-list-card-image-crop-title">
        <header class="the-list-card-image-crop-modal__header">
          <h3 id="the-list-card-image-crop-title">Recortar imagen de la tarjeta</h3>
          <button type="button" class="the-list-card-image-crop-modal__close" data-action="cancel" aria-label="Cerrar cuadro de recorte">x</button>
        </header>
        <div class="the-list-card-image-crop-modal__body">
          <div class="the-list-card-image-crop-modal__viewport">
            <canvas
              class="the-list-card-image-crop-modal__canvas"
              width="${CARD_IMAGE_CROP_VIEW_WIDTH}"
              height="${CARD_IMAGE_CROP_VIEW_HEIGHT}"
              aria-label="Vista previa de recorte 4:3"
            ></canvas>
          </div>
          <p class="the-list-card-image-crop-modal__hint">Arrastra para mover. Desplázate para acercar.</p>
          <div class="the-list-card-image-crop-modal__controls">
            <label for="the-list-card-image-crop-zoom">Zoom</label>
            <input
              id="the-list-card-image-crop-zoom"
              class="the-list-card-image-crop-modal__zoom"
              type="range"
              min="0"
              max="${CARD_IMAGE_CROP_ZOOM_STEPS}"
              step="1"
              value="0"
            />
            <span class="the-list-card-image-crop-modal__zoom-value">100%</span>
            <span class="the-list-card-image-crop-modal__output">Salida: ${CARD_IMAGE_OUTPUT_WIDTH} x ${CARD_IMAGE_OUTPUT_HEIGHT}</span>
          </div>
        </div>
        <div class="the-list-card-image-crop-modal__actions">
          <button type="button" class="the-list-card-image-crop-modal__btn the-list-card-image-crop-modal__btn--secondary" data-action="cancel">Cancelar</button>
          <button type="button" class="the-list-card-image-crop-modal__btn the-list-card-image-crop-modal__btn--primary" data-action="apply">Aplicar recorte</button>
        </div>
      </section>
    `;
    document.body.appendChild(modal);

    const canvas = modal.querySelector(".the-list-card-image-crop-modal__canvas");
    const zoomRange = modal.querySelector(".the-list-card-image-crop-modal__zoom");
    const zoomValue = modal.querySelector(".the-list-card-image-crop-modal__zoom-value");
    const applyButton = modal.querySelector("[data-action='apply']");
    if (
      !(canvas instanceof HTMLCanvasElement) ||
      !(zoomRange instanceof HTMLInputElement) ||
      !(zoomValue instanceof HTMLElement) ||
      !(applyButton instanceof HTMLButtonElement)
    ) {
      cropDebugLog("crop_modal.ui_create_failed");
      modal.remove();
      return null;
    }

    modal.querySelectorAll("[data-action='cancel']").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (isCardImageCropFreshlyOpened()) {
          cropDebugLog("crop_modal.cancel_ignored_fresh_open");
          return;
        }
        event.preventDefault();
        cropDebugLog("crop_modal.cancel_clicked");
        cancelCardImageCrop();
      });
    });
    applyButton.addEventListener("click", (event) => {
      event.preventDefault();
      cropDebugLog("crop_modal.apply_clicked");
      void commitCardImageCrop();
    });
    canvas.addEventListener("pointerdown", onCardImageCropPointerDown);
    window.addEventListener("pointermove", onCardImageCropPointerMove);
    window.addEventListener("pointerup", onCardImageCropPointerUp);
    window.addEventListener("pointercancel", onCardImageCropPointerUp);
    canvas.addEventListener("wheel", onCardImageCropWheel, { passive: false });
    zoomRange.addEventListener("input", onCardImageCropZoomInput);
    cardImageCropUi = {
      modal,
      canvas,
      zoomRange,
      zoomValue,
      applyButton,
    };
    cropDebugLog("crop_modal.ui_ready");
    return cardImageCropUi;
  };

  const openCardImageCropModal = async (fileInput, file) => {
    cropDebugLog("crop_modal.open.start", {
      hasInput: fileInput instanceof HTMLInputElement,
      hasFile: file instanceof File,
      type: file?.type || "",
      name: file?.name || "",
      size: file?.size || 0,
    });
    if (!(file instanceof File)) {
      cropDebugLog("crop_modal.open.invalid_input_or_file");
      return false;
    }
    if (!String(file.type || "").toLowerCase().startsWith("image/")) {
      cropDebugLog("crop_modal.open.invalid_type", { type: file.type || "" });
      return false;
    }
    const ui = ensureCardImageCropUi();
    if (!ui) {
      cropDebugLog("crop_modal.open.ui_missing");
      return false;
    }

    const token = ++cardImageCropSessionToken;
    cropDebugLog("crop_modal.open.token", { token });
    const objectUrl = URL.createObjectURL(file);
    let image;
    try {
      image = await loadImageForCrop(objectUrl);
      cropDebugLog("crop_modal.open.image_loaded", {
        width: Number(image?.naturalWidth || image?.width || 0),
        height: Number(image?.naturalHeight || image?.height || 0),
      });
    } catch (error) {
      URL.revokeObjectURL(objectUrl);
      console.error("Card image load failed", error);
      cropDebugLog("crop_modal.open.image_error", {
        message: String(error?.message || error || "unknown"),
      });
      return false;
    }

    if (token !== cardImageCropSessionToken) {
      URL.revokeObjectURL(objectUrl);
      cropDebugLog("crop_modal.open.token_mismatch", {
        token,
        activeToken: cardImageCropSessionToken,
      });
      return true;
    }

    teardownCardImageCropState({ clearInput: false, restoreFocus: false });
    cardImageCropState = {
      fileInput: fileInput instanceof HTMLInputElement ? fileInput : null,
      sourceFile: file,
      objectUrl,
      image,
      imageWidth: Number(image.naturalWidth || image.width || 0),
      imageHeight: Number(image.naturalHeight || image.height || 0),
      zoom: 1,
      maxZoom: Math.max(1.2, CARD_IMAGE_CROP_MAX_ZOOM_MULTIPLIER),
      offsetX: 0,
      offsetY: 0,
      scale: 1,
      drawWidth: 0,
      drawHeight: 0,
      dragState: null,
      isApplying: false,
      openedAt: Date.now(),
      restoreFocus: document.activeElement instanceof HTMLElement ? document.activeElement : null,
    };

    clampCardImageCropOffsets(cardImageCropState);
    cardImageCropState.offsetX = (CARD_IMAGE_CROP_VIEW_WIDTH - cardImageCropState.drawWidth) / 2;
    cardImageCropState.offsetY = (CARD_IMAGE_CROP_VIEW_HEIGHT - cardImageCropState.drawHeight) / 2;
    renderCardImageCrop();

    ui.modal.hidden = false;
    ui.modal.classList.add("is-open");
    document.body.classList.add("the-list-card-image-crop-open");
    if (ui.zoomRange instanceof HTMLInputElement) {
      ui.zoomRange.focus();
    }
    cropDebugLog("crop_modal.open.visible");
    return true;
  };

  const applyCardImagePreview = () => {
    const host = getCardImageUploadHost();
    if (!(host instanceof HTMLElement)) {
      cropDebugLog("image_change.bind.host_missing");
      return;
    }

    const handleImageSelection = (event) => {
      cropDebugLog("image_change.event", {
        type: event?.type || "",
        targetTag:
          event?.target instanceof Element ? event.target.tagName.toLowerCase() : "unknown",
      });
      if (!isElementVisible(document.getElementById(CARD_PROPOSAL_SHELL_ID))) {
        cropDebugLog("image_change.ignored_shell_hidden");
        return;
      }
      const inputNode = extractFileInputFromChangeEvent(event);
      if (!isCardImageFileInput(inputNode)) {
        cropDebugLog("image_change.ignored_not_card_input");
        return;
      }
      const file = readFirstFileFromNode(inputNode);
      if (!file) {
        cropDebugLog("image_change.ignored_no_file");
        return;
      }

      if (isCardImageChangeHandled(event)) {
        cropDebugLog("image_change.ignored_already_handled");
        return;
      }
      markCardImageChangeHandled(event);
      cropDebugLog("image_change.handling_file", {
        name: file.name,
        size: file.size,
        type: file.type,
      });

      if (cardImageCropBypassOnce) {
        cardImageCropBypassOnce = false;
        cropDebugLog("image_change.bypass_preview_only");
        applyInlineCardImagePreview(file);
        return;
      }

      event.preventDefault();
      event.stopImmediatePropagation();
      cropDebugLog("image_change.open_crop_modal");
      void openCardImageCropModal(inputNode, file);
    };

    if (!imagePreviewBindingReady) {
      document.addEventListener("change", handleImageSelection, true);
      imagePreviewBindingReady = true;
      cropDebugLog("image_change.bind.document");
    }

    if (host.dataset.inlineImagePreviewBound !== "1") {
      host.addEventListener("change", handleImageSelection, true);
      if (host.shadowRoot) {
        host.shadowRoot.addEventListener("change", handleImageSelection, true);
      }
      host.dataset.inlineImagePreviewBound = "1";
      cropDebugLog("image_change.bind.host");
    }
  };

  const clearInlinePreviewUrl = () => {
    if (!inlinePreviewUrl) return;
    URL.revokeObjectURL(inlinePreviewUrl);
    inlinePreviewUrl = "";
  };

  const updateCardImagePickerState = (card, active) => {
    const media = getCardMediaNode(card);
    if (!(media instanceof HTMLElement)) return;
    media.classList.toggle("person-detail-card__media--editable", active);
    if (active) {
      media.setAttribute("role", "button");
      media.setAttribute("tabindex", "0");
      media.setAttribute("aria-label", "Cambiar imagen de la tarjeta");
      return;
    }
    media.removeAttribute("role");
    media.removeAttribute("tabindex");
    media.removeAttribute("aria-label");
  };

  const bindCardImagePicker = (card) => {
    const media = getCardMediaNode(card);
    if (!(media instanceof HTMLElement) || media.dataset.inlineImagePickerBound === "1") return;
    media.dataset.inlineImagePickerBound = "1";
    const openPicker = () => {
      if (!isElementVisible(document.getElementById(CARD_PROPOSAL_SHELL_ID))) {
        cropDebugLog("picker_open.blocked_shell_hidden");
        return;
      }
      cropDebugLog("picker_open.requested");
      if (openCardImageNativePicker()) return;
      cropDebugLog("picker_open.native_failed_fallback_gradio");
      triggerCardImagePicker();
    };
    media.addEventListener("click", (event) => {
      event.preventDefault();
      openPicker();
    });
    media.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openPicker();
    });
  };

  const handleDetailVerifiedActivation = (badge, event) => {
    if (!(badge instanceof HTMLElement)) return;
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const slug = String(badge.getAttribute("data-slug") || "").trim().toLowerCase();
    const currentState = isTrueish(badge.getAttribute("data-state"));
    const nextState = !currentState;
    badge.setAttribute("data-state", nextState ? "true" : "false");
    badge.classList.toggle("is-verified", nextState);
    badge.classList.toggle("is-unverified", !nextState);
    const badgeLabel = nextState ? "Receta verificada" : "Receta sin verificar";
    badge.setAttribute("aria-label", badgeLabel);
    badge.setAttribute("title", badgeLabel);
    const payload = JSON.stringify({ slug, nextState });
    setComponentValue(DETAIL_VERIFY_PAYLOAD_ID, payload);
    setComponentValue(DETAIL_VERIFY_STATE_ID, nextState ? "true" : "false");

    const shell = document.getElementById(CARD_PROPOSAL_SHELL_ID);
    const editing = isElementVisible(shell);
    if (editing) {
      // In edit mode this acts as a staged field value. Persist only on Guardar.
      return;
    }

    const row = getCardProposalActionsRow();
    const slot = getCardInlineActionsSlot();
    if (
      row instanceof HTMLElement &&
      shell instanceof HTMLElement &&
      slot instanceof HTMLElement &&
      row.parentElement === slot
    ) {
      // Keep action buttons mounted while the hero HTML gets replaced.
      shell.appendChild(row);
      row.classList.remove("the-list-card-actions--inline");
    }

    const triggerHost = document.getElementById(DETAIL_VERIFY_TRIGGER_ID);
    const trigger = resolveButtonClickHost(triggerHost);
    if (!(trigger instanceof HTMLElement)) return;
    trigger.click();
  };

  const bindDetailVerifiedToggle = () => {
    const heroHost = document.getElementById("person-detail-hero");
    if (!(heroHost instanceof HTMLElement) || heroHost.dataset.detailVerifiedBound === "1") return;
    heroHost.dataset.detailVerifiedBound = "1";

    heroHost.addEventListener("click", (event) => {
      const target = event.target;
      const badge = target instanceof Element ? target.closest(DETAIL_VERIFY_BADGE_SELECTOR) : null;
      if (!(badge instanceof HTMLElement) || !heroHost.contains(badge)) return;
      handleDetailVerifiedActivation(badge, event);
    });

    heroHost.addEventListener("keydown", (event) => {
      if (!(event instanceof KeyboardEvent)) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      const target = event.target;
      const badge = target instanceof Element ? target.closest(DETAIL_VERIFY_BADGE_SELECTOR) : null;
      if (!(badge instanceof HTMLElement) || !heroHost.contains(badge)) return;
      handleDetailVerifiedActivation(badge, event);
    });
  };

  const bindRecipeDeleteGuard = () => {
    if (document.body.dataset.recipeDeleteGuardBound === "1") return;
    document.body.dataset.recipeDeleteGuardBound = "1";

    document.addEventListener(
      "click",
      (event) => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const deleteTarget = target.closest(
          `#${DELETE_BUTTON_ID}, #${DELETE_BUTTON_ID} button, button#${DELETE_BUTTON_ID}`,
        );
        if (!(deleteTarget instanceof HTMLElement)) return;

        const deleteHost = document.getElementById(DELETE_BUTTON_ID);
        if (
          deleteHost instanceof HTMLElement &&
          deleteTarget !== deleteHost &&
          !deleteHost.contains(deleteTarget)
        ) {
          return;
        }

        if (isTrueish(getComponentValue(DETAIL_VERIFY_STATE_ID))) {
          stopEvent(event);
          window.alert(RECIPE_DELETE_BLOCKED_MESSAGE);
          return;
        }

        const confirmed = window.confirm(RECIPE_DELETE_CONFIRM_MESSAGE);
        if (!confirmed) {
          stopEvent(event);
        }
      },
      true,
    );
  };

  const bindRecipeDownloadPayload = () => {
    const host = document.getElementById(RECIPE_DOWNLOAD_PAYLOAD_ID);
    const input = getComponentInput(RECIPE_DOWNLOAD_PAYLOAD_ID);
    if (!(host instanceof HTMLElement) || !(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement)) {
      return;
    }
    if (host.dataset.recipeDownloadPayloadBound === "1") return;
    host.dataset.recipeDownloadPayloadBound = "1";

    let lastPayload = "";
    const consumePayload = () => {
      const rawPayload = String(input.value || "").trim();
      if (!rawPayload || rawPayload === lastPayload) return;
      lastPayload = rawPayload;

      try {
        const payload = JSON.parse(rawPayload);
        const filename = String(payload?.filename || "receta");
        const mimeType = String(payload?.mime_type || "application/octet-stream");
        const bytes = decodeBase64ToBytes(payload?.data_base64 || "");
        if (!bytes.length) return;

        const blob = new Blob([bytes], { type: mimeType });
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filename;
        anchor.style.display = "none";
        document.body.appendChild(anchor);
        anchor.click();
        window.setTimeout(() => {
          URL.revokeObjectURL(objectUrl);
          anchor.remove();
        }, 0);
      } catch (error) {
        void error;
      } finally {
        setComponentValue(RECIPE_DOWNLOAD_PAYLOAD_ID, "");
        lastPayload = "";
      }
    };

    input.addEventListener("input", consumePayload);
    input.addEventListener("change", consumePayload);
    const observer = new MutationObserver(consumePayload);
    observer.observe(input, { attributes: true, childList: true, characterData: true, subtree: true });
    consumePayload();
  };

  const bindRecipeImportFileChipRemovers = () => {
    if (document.body.dataset.recipeImportFileChipRemoversBound === "1") return;
    document.body.dataset.recipeImportFileChipRemoversBound = "1";

    document.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const removeButton = target.closest(".recipe-import-file-chip__remove");
      if (!(removeButton instanceof HTMLElement)) return;

      const fileListHost = document.getElementById(RECIPE_IMPORT_FILE_LIST_ID);
      if (!(fileListHost instanceof HTMLElement) || !fileListHost.contains(removeButton)) return;

      const rawIndex = String(removeButton.dataset.recipeImportRemoveIndex || "").trim();
      if (!/^\d+$/.test(rawIndex)) return;

      event.preventDefault();
      event.stopPropagation();

      setComponentValue(RECIPE_IMPORT_REMOVE_INDEX_ID, rawIndex);
      const triggerHost = document.getElementById(RECIPE_IMPORT_REMOVE_TRIGGER_ID);
      const trigger = resolveButtonClickHost(triggerHost);
      if (trigger instanceof HTMLElement) {
        trigger.click();
      }
    }, true);
  };

  const setNodeText = (node, value) => {
    if (!(node instanceof HTMLElement)) return;
    node.textContent = String(value || "");
  };

  const currentSlugValue = () => normalizeSingleLineText(getComponentValue(CURRENT_SLUG_ID)).toLowerCase();

  const enterInlineCardEdit = (card) => {
    if (!(card instanceof HTMLElement)) return;
    const titleNode = getCardTitleNode(card);
    const bucketNode = getCardBucketNode(card);
    const tagsHost = getCardTagsHost(card);
    const totalTimeNode = getCardTotalTimeNode(card);
    const personsNode = getCardPersonsNode(card);
    const imageNode = getCardImageNode(card);
    const slug = currentSlugValue();

    if (titleNode instanceof HTMLElement) {
      bindInlineSingleLineField(titleNode, CARD_PROPOSAL_NAME_ID);
      setInlineFieldMode(titleNode, true);
    }
    if (bucketNode instanceof HTMLElement) {
      bindInlineSingleLineField(bucketNode, CARD_PROPOSAL_BUCKET_ID);
      setInlineFieldMode(bucketNode, true);
    }
    if (totalTimeNode instanceof HTMLElement) {
      bindInlineSingleLineField(totalTimeNode, CARD_PROPOSAL_TOTAL_TIME_ID);
      setInlineFieldMode(totalTimeNode, true);
    }
    if (personsNode instanceof HTMLElement) {
      bindInlineSingleLineField(personsNode, CARD_PROPOSAL_PERSONS_ID);
      setInlineFieldMode(personsNode, true);
    }

    bindCardImagePicker(card);
    applyCardImagePreview();
    updateCardImagePickerState(card, true);

    const shouldInitialize =
      card.dataset.inlineCardMode !== "1" || card.dataset.inlineCardSlug !== slug;
    if (!shouldInitialize) return;

    const currentName = normalizeSingleLineText(getComponentValue(CURRENT_NAME_ID));
    const currentBucket = normalizeSingleLineText(getComponentValue(CURRENT_BUCKET_ID));
    const currentTags = parseTagValues(getComponentValue(CURRENT_TAGS_ID));
    const currentTotalTime = normalizeSingleLineText(getComponentValue(CURRENT_TOTAL_TIME_ID));
    const currentPersons = normalizeSingleLineText(getComponentValue(CURRENT_PERSONS_ID));

    const proposalName = normalizeSingleLineText(getComponentValue(CARD_PROPOSAL_NAME_ID)) || currentName;
    const proposalBucket =
      normalizeSingleLineText(getComponentValue(CARD_PROPOSAL_BUCKET_ID)) || currentBucket;
    const proposalTags =
      parseTagValues(getComponentValue(CARD_PROPOSAL_TAGS_ID)).length > 0
        ? parseTagValues(getComponentValue(CARD_PROPOSAL_TAGS_ID))
        : currentTags;
    const proposalTotalTime =
      normalizeSingleLineText(getComponentValue(CARD_PROPOSAL_TOTAL_TIME_ID)) || currentTotalTime;
    const proposalPersons =
      normalizeSingleLineText(getComponentValue(CARD_PROPOSAL_PERSONS_ID)) || currentPersons;

    if (titleNode instanceof HTMLElement) {
      setNodeText(titleNode, proposalName || titleNode.textContent || "");
      setComponentValue(CARD_PROPOSAL_NAME_ID, normalizeSingleLineText(titleNode.textContent || ""));
    }
    if (bucketNode instanceof HTMLElement) {
      setNodeText(bucketNode, proposalBucket || bucketNode.textContent || "");
      setComponentValue(CARD_PROPOSAL_BUCKET_ID, normalizeSingleLineText(bucketNode.textContent || ""));
    }
    if (tagsHost instanceof HTMLElement) {
      renderEditableTags(tagsHost, proposalTags, {
        fieldId: CARD_PROPOSAL_TAGS_ID,
        chipLabel: "tag",
      });
    }
    if (totalTimeNode instanceof HTMLElement) {
      setNodeText(totalTimeNode, proposalTotalTime || totalTimeNode.textContent || "");
      setComponentValue(
        CARD_PROPOSAL_TOTAL_TIME_ID,
        normalizeSingleLineText(totalTimeNode.textContent || ""),
      );
    }
    if (personsNode instanceof HTMLElement) {
      setNodeText(personsNode, proposalPersons || personsNode.textContent || "");
      setComponentValue(CARD_PROPOSAL_PERSONS_ID, normalizeSingleLineText(personsNode.textContent || ""));
    }
    if (imageNode instanceof HTMLImageElement) {
      imageNode.dataset.inlineBaseSrc = imageNode.getAttribute("src") || "";
    }
    setComponentValue(CARD_PROPOSAL_IMAGE_DATA_ID, "");

    card.dataset.inlineCardMode = "1";
    card.dataset.inlineCardSlug = slug;
  };

  const leaveInlineCardEdit = (card) => {
    if (!(card instanceof HTMLElement)) return;
    const titleNode = getCardTitleNode(card);
    const bucketNode = getCardBucketNode(card);
    const tagsHost = getCardTagsHost(card);
    const totalTimeNode = getCardTotalTimeNode(card);
    const personsNode = getCardPersonsNode(card);
    const imageNode = getCardImageNode(card);

    if (titleNode instanceof HTMLElement) {
      setInlineFieldMode(titleNode, false);
      const currentName = normalizeSingleLineText(getComponentValue(CURRENT_NAME_ID));
      if (currentName) setNodeText(titleNode, currentName);
      setComponentValue(CARD_PROPOSAL_NAME_ID, currentName);
    }
    if (bucketNode instanceof HTMLElement) {
      setInlineFieldMode(bucketNode, false);
      const currentBucket = normalizeSingleLineText(getComponentValue(CURRENT_BUCKET_ID));
      if (currentBucket) setNodeText(bucketNode, currentBucket);
      setComponentValue(CARD_PROPOSAL_BUCKET_ID, currentBucket);
    }
    if (tagsHost instanceof HTMLElement) {
      const currentTags = parseTagValues(getComponentValue(CURRENT_TAGS_ID));
      renderReadonlyTags(tagsHost, currentTags, { emptyLabel: "no-tags" });
      setComponentValue(CARD_PROPOSAL_TAGS_ID, serializeTagValues(currentTags));
    }
    if (totalTimeNode instanceof HTMLElement) {
      setInlineFieldMode(totalTimeNode, false);
      const currentTotalTime = normalizeSingleLineText(getComponentValue(CURRENT_TOTAL_TIME_ID));
      setNodeText(totalTimeNode, currentTotalTime || totalTimeNode.textContent || "");
      setComponentValue(CARD_PROPOSAL_TOTAL_TIME_ID, currentTotalTime);
    }
    if (personsNode instanceof HTMLElement) {
      setInlineFieldMode(personsNode, false);
      const currentPersons = normalizeSingleLineText(getComponentValue(CURRENT_PERSONS_ID));
      setNodeText(personsNode, currentPersons || personsNode.textContent || "");
      setComponentValue(CARD_PROPOSAL_PERSONS_ID, currentPersons);
    }

    updateCardImagePickerState(card, false);
    if (imageNode instanceof HTMLImageElement) {
      const baseSrc = String(imageNode.dataset.inlineBaseSrc || "").trim();
      if (baseSrc) imageNode.src = baseSrc;
    }
    setComponentValue(CARD_PROPOSAL_IMAGE_DATA_ID, "");
    clearInlinePreviewUrl();
    card.dataset.inlineCardMode = "0";
    delete card.dataset.inlineCardSlug;
  };

  const refreshInlineCardEditor = () => {
    const card = getDetailCard();
    const shell = document.getElementById(CARD_PROPOSAL_SHELL_ID);
    const active = isElementVisible(shell);
    const stateKey = [
      card instanceof HTMLElement ? "card:1" : "card:0",
      active ? "active:1" : "active:0",
      card instanceof HTMLElement ? `mode:${card.dataset.inlineCardMode || "0"}` : "mode:-",
      cardImageCropState ? "crop:1" : "crop:0",
    ].join("|");
    if (stateKey !== lastInlineEditorStateKey) {
      lastInlineEditorStateKey = stateKey;
      cropDebugLog("inline_editor.state", { state: stateKey });
    }

    if (!(card instanceof HTMLElement)) {
      cardInlineWasActive = false;
      clearInlinePreviewUrl();
      return;
    }
    if (active) {
      enterInlineCardEdit(card);
      cardInlineWasActive = true;
      return;
    }
    if (cardInlineWasActive || card.dataset.inlineCardMode === "1") {
      leaveInlineCardEdit(card);
    } else {
      updateCardImagePickerState(card, false);
      setInlineFieldMode(getCardTitleNode(card), false);
      setInlineFieldMode(getCardBucketNode(card), false);
      setInlineFieldMode(getCardTotalTimeNode(card), false);
      setInlineFieldMode(getCardPersonsNode(card), false);
    }
    cardInlineWasActive = false;
  };

  const getRecipeIngredientsHost = () => document.getElementById(RECIPE_INGREDIENTS_INPUT_ID);

  const getRecipeIngredientsTextarea = (host) => {
    const resolvedHost = host instanceof HTMLElement ? host : getRecipeIngredientsHost();
    if (!(resolvedHost instanceof HTMLElement)) return null;
    const textarea = resolvedHost.querySelector("textarea");
    return textarea instanceof HTMLTextAreaElement ? textarea : null;
  };

  const normalizeRecipeIngredientPart = (value) =>
    String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const parseRecipeIngredientsFromTextareaValue = (rawValue) => {
    const rows = [];
    String(rawValue || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n")
      .forEach((line) => {
        const rawLine = String(line || "").trim();
        if (!rawLine) return;

        let amount = "";
        let ingredient = rawLine;
        const pipeIndex = rawLine.indexOf("|");
        const colonIndex = rawLine.indexOf(":");
        if (pipeIndex >= 0) {
          amount = normalizeRecipeIngredientPart(rawLine.slice(0, pipeIndex));
          ingredient = normalizeRecipeIngredientPart(rawLine.slice(pipeIndex + 1));
        } else if (colonIndex >= 0) {
          amount = normalizeRecipeIngredientPart(rawLine.slice(0, colonIndex));
          ingredient = normalizeRecipeIngredientPart(rawLine.slice(colonIndex + 1));
        } else {
          ingredient = normalizeRecipeIngredientPart(rawLine);
        }

        if (!amount && !ingredient) return;
        rows.push({ amount, ingredient });
      });

    if (!rows.length) {
      return [{ amount: "", ingredient: "" }];
    }
    return rows;
  };

  const serializeRecipeIngredients = (rows) => {
    const lines = (Array.isArray(rows) ? rows : [rows]).map((row) => {
      const amount = normalizeRecipeIngredientPart(row?.amount);
      const ingredient = normalizeRecipeIngredientPart(row?.ingredient);
      if (amount && ingredient) return `${amount} | ${ingredient}`;
      if (ingredient) return ingredient;
      if (amount) return `${amount} |`;
      return "";
    });
    while (lines.length > 1 && !lines[lines.length - 1]) {
      lines.pop();
    }
    return lines.join("\n");
  };

  const getRecipeIngredientsEditor = (host) =>
    host instanceof HTMLElement ? host.querySelector(`.${RECIPE_INGREDIENTS_EDITOR_CLASS}`) : null;

  const getRecipeIngredientsList = (editor) =>
    editor instanceof HTMLElement ? editor.querySelector(".recipe-ingredients-editor__list") : null;

  const getRecipeIngredientItems = (list) =>
    list instanceof HTMLElement ? Array.from(list.querySelectorAll(RECIPE_INGREDIENT_ITEM_SELECTOR)) : [];

  const collectRecipeIngredientRowsFromList = (list) =>
    getRecipeIngredientItems(list).map((item) => {
      const amountInput = item.querySelector(".recipe-ingredient-item__amount");
      const ingredientInput = item.querySelector(".recipe-ingredient-item__name");
      const amount =
        amountInput instanceof HTMLInputElement ? normalizeRecipeIngredientPart(amountInput.value) : "";
      const ingredient =
        ingredientInput instanceof HTMLInputElement ? normalizeRecipeIngredientPart(ingredientInput.value) : "";
      return { amount, ingredient };
    });

  const syncRecipeIngredientsTextareaFromList = (host, textarea, list, { forceDispatch = false } = {}) => {
    if (!(host instanceof HTMLElement) || !(textarea instanceof HTMLTextAreaElement)) return;
    const serialized = serializeRecipeIngredients(collectRecipeIngredientRowsFromList(list));
    const changed = String(textarea.value || "") !== serialized;
    if (changed) {
      textarea.value = serialized;
      dispatchComponentEvents(textarea);
    } else if (forceDispatch) {
      dispatchComponentEvents(textarea);
    }
    host.dataset.recipeIngredientsValue = serialized;
  };

  const getRecipeIngredientDragAfterElement = (list, clientY) => {
    const candidates = getRecipeIngredientItems(list).filter(
      (item) => !item.classList.contains("recipe-ingredient-item--dragging"),
    );
    let closest = { offset: Number.NEGATIVE_INFINITY, element: null };
    candidates.forEach((item) => {
      const box = item.getBoundingClientRect();
      const offset = clientY - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        closest = { offset, element: item };
      }
    });
    return closest.element;
  };

  const clearRecipeIngredientDragging = (list) => {
    if (!(list instanceof HTMLElement)) return;
    list.classList.remove("recipe-ingredients-editor__list--dragging");
    getRecipeIngredientItems(list).forEach((item) => {
      item.classList.remove("recipe-ingredient-item--dragging");
    });
  };

  const buildRecipeIngredientItem = (
    host,
    textarea,
    list,
    initialRow = { amount: "", ingredient: "" },
  ) => {
    const item = document.createElement("div");
    item.className = "recipe-ingredient-item";
    item.draggable = true;
    item.dataset.ingredientId = String(++recipeIngredientItemCounter);

    const dragHandle = document.createElement("button");
    dragHandle.type = "button";
    dragHandle.className = "recipe-ingredient-item__drag";
    dragHandle.title = "Arrastrar para reordenar ingrediente";
    dragHandle.setAttribute("aria-label", "Arrastrar para reordenar ingrediente");
    dragHandle.textContent = "";

    const amountInput = document.createElement("input");
    amountInput.type = "text";
    amountInput.className = "recipe-ingredient-item__amount";
    amountInput.value = normalizeRecipeIngredientPart(initialRow.amount);
    amountInput.placeholder = "cantidad";
    amountInput.setAttribute("aria-label", "Cantidad del ingrediente");

    const ingredientInput = document.createElement("input");
    ingredientInput.type = "text";
    ingredientInput.className = "recipe-ingredient-item__name";
    ingredientInput.value = normalizeRecipeIngredientPart(initialRow.ingredient);
    ingredientInput.placeholder = "ingrediente";
    ingredientInput.setAttribute("aria-label", "Nombre del ingrediente");

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "recipe-ingredient-item__remove";
    removeButton.title = "Eliminar fila de ingrediente";
    removeButton.setAttribute("aria-label", "Eliminar fila de ingrediente");
    removeButton.textContent = "x";

    item.appendChild(dragHandle);
    item.appendChild(ingredientInput);
    item.appendChild(amountInput);
    item.appendChild(removeButton);

    const sync = () => {
      syncRecipeIngredientsTextareaFromList(host, textarea, list);
    };

    amountInput.addEventListener("input", sync);
    ingredientInput.addEventListener("input", sync);
    amountInput.addEventListener("blur", () => {
      const cleaned = normalizeRecipeIngredientPart(amountInput.value);
      if (cleaned !== amountInput.value) amountInput.value = cleaned;
      sync();
    });
    ingredientInput.addEventListener("blur", () => {
      const cleaned = normalizeRecipeIngredientPart(ingredientInput.value);
      if (cleaned !== ingredientInput.value) ingredientInput.value = cleaned;
      sync();
    });

    removeButton.addEventListener("click", (event) => {
      event.preventDefault();
      const items = getRecipeIngredientItems(list);
      if (items.length <= 1) {
        amountInput.value = "";
        ingredientInput.value = "";
        sync();
        ingredientInput.focus();
        return;
      }
      item.remove();
      sync();
    });

    item.addEventListener("dragstart", (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && target.closest(".recipe-ingredient-item__remove")) {
        event.preventDefault();
        return;
      }
      if (
        target instanceof HTMLElement &&
        (target.closest(".recipe-ingredient-item__amount") ||
          target.closest(".recipe-ingredient-item__name"))
      ) {
        event.preventDefault();
        return;
      }
      item.classList.add("recipe-ingredient-item--dragging");
      list.classList.add("recipe-ingredients-editor__list--dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.dataset.ingredientId || "");
      }
    });

    item.addEventListener("dragend", () => {
      clearRecipeIngredientDragging(list);
      sync();
    });

    return item;
  };

  const appendRecipeIngredientItem = (host, textarea, list, row = {}, { focus = false } = {}) => {
    if (!(list instanceof HTMLElement)) return null;
    const item = buildRecipeIngredientItem(host, textarea, list, row);
    list.appendChild(item);
    syncRecipeIngredientsTextareaFromList(host, textarea, list);
    if (focus) {
      const input = item.querySelector(".recipe-ingredient-item__name");
      if (input instanceof HTMLInputElement) {
        window.setTimeout(() => input.focus(), 0);
      }
    }
    return item;
  };

  const bindRecipeIngredientsDnD = (host, textarea, list) => {
    if (!(list instanceof HTMLElement) || list.dataset.recipeIngredientsDragBound === "1") return;
    list.dataset.recipeIngredientsDragBound = "1";

    list.addEventListener("dragover", (event) => {
      const dragging = list.querySelector(".recipe-ingredient-item--dragging");
      if (!(dragging instanceof HTMLElement)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      const afterElement = getRecipeIngredientDragAfterElement(list, event.clientY);
      if (!(afterElement instanceof HTMLElement)) {
        list.appendChild(dragging);
      } else if (afterElement !== dragging) {
        list.insertBefore(dragging, afterElement);
      }
    });

    list.addEventListener("drop", (event) => {
      const dragging = list.querySelector(".recipe-ingredient-item--dragging");
      if (!(dragging instanceof HTMLElement)) return;
      event.preventDefault();
      clearRecipeIngredientDragging(list);
      syncRecipeIngredientsTextareaFromList(host, textarea, list);
    });
  };

  const renderRecipeIngredientsFromTextarea = (host, textarea, list) => {
    if (!(host instanceof HTMLElement) || !(textarea instanceof HTMLTextAreaElement) || !(list instanceof HTMLElement)) {
      return;
    }
    const parsedRows = parseRecipeIngredientsFromTextareaValue(textarea.value);
    const serialized = serializeRecipeIngredients(parsedRows);
    const previousSerialized = String(host.dataset.recipeIngredientsValue || "");
    if (serialized === previousSerialized) return;

    list.replaceChildren();
    parsedRows.forEach((row) => {
      list.appendChild(buildRecipeIngredientItem(host, textarea, list, row));
    });
    host.dataset.recipeIngredientsValue = serialized;

    if (String(textarea.value || "") !== serialized) {
      textarea.value = serialized;
      dispatchComponentEvents(textarea);
    }
  };

  const ensureRecipeIngredientsEditor = () => {
    const host = getRecipeIngredientsHost();
    if (!(host instanceof HTMLElement)) return;
    const textarea = getRecipeIngredientsTextarea(host);
    if (!(textarea instanceof HTMLTextAreaElement)) return;

    let editor = getRecipeIngredientsEditor(host);
    if (!(editor instanceof HTMLElement)) {
      editor = document.createElement("section");
      editor.className = RECIPE_INGREDIENTS_EDITOR_CLASS;

      const header = document.createElement("header");
      header.className = "recipe-ingredients-editor__header";

      const title = document.createElement("strong");
      title.className = "recipe-ingredients-editor__title";
      title.textContent = "Ingredientes";

      const addButton = document.createElement("button");
      addButton.type = "button";
      addButton.className = "recipe-ingredients-editor__add-btn";
      addButton.title = "Añadir fila de ingrediente";
      addButton.setAttribute("aria-label", "Añadir fila de ingrediente");
      addButton.textContent = "+";

      const columns = document.createElement("div");
      columns.className = "recipe-ingredients-editor__columns";
      columns.innerHTML = "<span>Ingrediente</span><span>Cantidad</span>";

      const list = document.createElement("div");
      list.className = "recipe-ingredients-editor__list";

      header.appendChild(title);
      header.appendChild(addButton);
      editor.appendChild(header);
      editor.appendChild(columns);
      editor.appendChild(list);
      host.appendChild(editor);

      bindRecipeIngredientsDnD(host, textarea, list);
      addButton.addEventListener("click", (event) => {
        event.preventDefault();
        appendRecipeIngredientItem(host, textarea, list, { amount: "", ingredient: "" }, { focus: true });
      });
    }

    host.classList.add("recipe-ingredients-input--enhanced");
    const list = getRecipeIngredientsList(editor);
    if (!(list instanceof HTMLElement)) return;
    bindRecipeIngredientsDnD(host, textarea, list);
    renderRecipeIngredientsFromTextarea(host, textarea, list);
  };

  const getRecipeStepsHost = () => document.getElementById(RECIPE_STEPS_INPUT_ID);

  const getRecipeStepsTextarea = (host) => {
    const resolvedHost = host instanceof HTMLElement ? host : getRecipeStepsHost();
    if (!(resolvedHost instanceof HTMLElement)) return null;
    const textarea = resolvedHost.querySelector("textarea");
    return textarea instanceof HTMLTextAreaElement ? textarea : null;
  };

  const normalizeRecipeStepValue = (value) =>
    String(value || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const parseRecipeStepsFromTextareaValue = (rawValue) => {
    const lines = String(rawValue || "")
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .split("\n")
      .map((line) => normalizeRecipeStepValue(String(line || "").replace(/^\s*\d+[.)]\s*/, "")));
    return lines.length ? lines : [""];
  };

  const serializeRecipeSteps = (steps) =>
    (Array.isArray(steps) ? steps : [steps]).map((step) => normalizeRecipeStepValue(step)).join("\n");

  const getRecipeStepsEditor = (host) =>
    host instanceof HTMLElement ? host.querySelector(`.${RECIPE_STEPS_EDITOR_CLASS}`) : null;

  const getRecipeStepsList = (editor) =>
    editor instanceof HTMLElement ? editor.querySelector(".recipe-steps-editor__list") : null;

  const getRecipeStepItems = (list) =>
    list instanceof HTMLElement ? Array.from(list.querySelectorAll(RECIPE_STEP_ITEM_SELECTOR)) : [];

  const getRecipeStepInputControl = (item) => {
    if (!(item instanceof HTMLElement)) return null;
    const input = item.querySelector(".recipe-step-item__input");
    if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) return input;
    return null;
  };

  const resizeRecipeStepInputControl = (input) => {
    if (!(input instanceof HTMLTextAreaElement)) return;
    input.style.height = "0";
    input.style.height = `${Math.max(input.scrollHeight, 32)}px`;
  };

  const updateRecipeStepIndices = (list) => {
    getRecipeStepItems(list).forEach((item, index) => {
      const indexNode = item.querySelector(".recipe-step-item__index");
      if (!(indexNode instanceof HTMLElement)) return;
      indexNode.textContent = String(index + 1);
    });
  };

  const collectRecipeStepValuesFromList = (list) =>
    getRecipeStepItems(list).map((item) => {
      const input = getRecipeStepInputControl(item);
      if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement)) return "";
      return normalizeRecipeStepValue(input.value);
    });

  const syncRecipeStepsTextareaFromList = (host, textarea, list, { forceDispatch = false } = {}) => {
    if (!(host instanceof HTMLElement) || !(textarea instanceof HTMLTextAreaElement)) return;
    const serialized = serializeRecipeSteps(collectRecipeStepValuesFromList(list));
    const changed = String(textarea.value || "") !== serialized;
    if (changed) {
      textarea.value = serialized;
      dispatchComponentEvents(textarea);
    } else if (forceDispatch) {
      dispatchComponentEvents(textarea);
    }
    host.dataset.recipeStepsValue = serialized;
  };

  const getRecipeStepDragAfterElement = (list, clientY) => {
    const candidates = getRecipeStepItems(list).filter(
      (item) => !item.classList.contains("recipe-step-item--dragging"),
    );
    let closest = { offset: Number.NEGATIVE_INFINITY, element: null };
    candidates.forEach((item) => {
      const box = item.getBoundingClientRect();
      const offset = clientY - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        closest = { offset, element: item };
      }
    });
    return closest.element;
  };

  const clearRecipeStepDragging = (list) => {
    if (!(list instanceof HTMLElement)) return;
    list.classList.remove("recipe-steps-editor__list--dragging");
    getRecipeStepItems(list).forEach((item) => {
      item.classList.remove("recipe-step-item--dragging");
    });
  };

  const buildRecipeStepItem = (host, textarea, list, initialValue = "") => {
    const item = document.createElement("div");
    item.className = "recipe-step-item";
    item.draggable = true;
    item.dataset.stepId = String(++recipeStepItemCounter);

    const dragHandle = document.createElement("button");
    dragHandle.type = "button";
    dragHandle.className = "recipe-step-item__drag";
    dragHandle.title = "Arrastrar para reordenar paso";
    dragHandle.setAttribute("aria-label", "Arrastrar para reordenar paso");
    dragHandle.textContent = "";

    const indexNode = document.createElement("span");
    indexNode.className = "recipe-step-item__index";
    indexNode.textContent = "1";

    const input = document.createElement("textarea");
    input.className = "recipe-step-item__input";
    input.value = normalizeRecipeStepValue(initialValue);
    input.placeholder = "Describe este paso...";
    input.setAttribute("aria-label", "Paso de preparación");

    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.className = "recipe-step-item__remove";
    removeButton.title = "Eliminar paso";
    removeButton.setAttribute("aria-label", "Eliminar paso");
    removeButton.textContent = "x";

    item.appendChild(dragHandle);
    item.appendChild(indexNode);
    item.appendChild(input);
    item.appendChild(removeButton);

    input.addEventListener("input", () => {
      resizeRecipeStepInputControl(input);
      syncRecipeStepsTextareaFromList(host, textarea, list);
    });
    input.addEventListener("blur", () => {
      const cleaned = normalizeRecipeStepValue(input.value);
      if (cleaned !== input.value) input.value = cleaned;
      resizeRecipeStepInputControl(input);
      syncRecipeStepsTextareaFromList(host, textarea, list);
    });

    removeButton.addEventListener("click", (event) => {
      event.preventDefault();
      const items = getRecipeStepItems(list);
      if (items.length <= 1) {
        input.value = "";
        syncRecipeStepsTextareaFromList(host, textarea, list);
        input.focus();
        return;
      }
      item.remove();
      updateRecipeStepIndices(list);
      syncRecipeStepsTextareaFromList(host, textarea, list);
    });

    item.addEventListener("dragstart", (event) => {
      const target = event.target;
      if (target instanceof HTMLElement && target.closest(".recipe-step-item__remove")) {
        event.preventDefault();
        return;
      }
      if (target instanceof HTMLElement && target.closest(".recipe-step-item__input")) {
        event.preventDefault();
        return;
      }
      item.classList.add("recipe-step-item--dragging");
      list.classList.add("recipe-steps-editor__list--dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", item.dataset.stepId || "");
      }
    });

    item.addEventListener("dragend", () => {
      clearRecipeStepDragging(list);
      updateRecipeStepIndices(list);
      syncRecipeStepsTextareaFromList(host, textarea, list);
    });

    window.requestAnimationFrame(() => {
      resizeRecipeStepInputControl(input);
    });

    return item;
  };

  const appendRecipeStepItem = (host, textarea, list, value = "", { focus = false } = {}) => {
    if (!(list instanceof HTMLElement)) return null;
    const item = buildRecipeStepItem(host, textarea, list, value);
    list.appendChild(item);
    updateRecipeStepIndices(list);
    syncRecipeStepsTextareaFromList(host, textarea, list);
    if (focus) {
      const input = item.querySelector(".recipe-step-item__input");
      if (input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) {
        window.setTimeout(() => input.focus(), 0);
      }
    }
    return item;
  };

  const bindRecipeStepsDnD = (host, textarea, list) => {
    if (!(list instanceof HTMLElement) || list.dataset.recipeStepsDragBound === "1") return;
    list.dataset.recipeStepsDragBound = "1";

    list.addEventListener("dragover", (event) => {
      const dragging = list.querySelector(".recipe-step-item--dragging");
      if (!(dragging instanceof HTMLElement)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      const afterElement = getRecipeStepDragAfterElement(list, event.clientY);
      if (!(afterElement instanceof HTMLElement)) {
        list.appendChild(dragging);
      } else if (afterElement !== dragging) {
        list.insertBefore(dragging, afterElement);
      }
      updateRecipeStepIndices(list);
    });

    list.addEventListener("drop", (event) => {
      const dragging = list.querySelector(".recipe-step-item--dragging");
      if (!(dragging instanceof HTMLElement)) return;
      event.preventDefault();
      clearRecipeStepDragging(list);
      updateRecipeStepIndices(list);
      syncRecipeStepsTextareaFromList(host, textarea, list);
    });
  };

  const resizeAllRecipeStepInputs = (list) => {
    getRecipeStepItems(list).forEach((item) => {
      const input = getRecipeStepInputControl(item);
      if (input instanceof HTMLTextAreaElement) {
        resizeRecipeStepInputControl(input);
      }
    });
  };

  const renderRecipeStepsFromTextarea = (host, textarea, list) => {
    if (!(host instanceof HTMLElement) || !(textarea instanceof HTMLTextAreaElement) || !(list instanceof HTMLElement)) {
      return;
    }
    const parsedSteps = parseRecipeStepsFromTextareaValue(textarea.value);
    const serialized = serializeRecipeSteps(parsedSteps);
    const previousSerialized = String(host.dataset.recipeStepsValue || "");
    const needsRebuild = serialized !== previousSerialized;

    if (needsRebuild) {
      list.replaceChildren();
      parsedSteps.forEach((step) => {
        const item = buildRecipeStepItem(host, textarea, list, step);
        list.appendChild(item);
      });
      updateRecipeStepIndices(list);
      host.dataset.recipeStepsValue = serialized;

      if (String(textarea.value || "") !== serialized) {
        textarea.value = serialized;
        dispatchComponentEvents(textarea);
      }
    }

    // Double requestAnimationFrame + setTimeout ensures layout is complete before measuring
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.setTimeout(() => {
          resizeAllRecipeStepInputs(list);
        }, 50);
      });
    });
  };

  const ensureRecipeStepsEditor = () => {
    const host = getRecipeStepsHost();
    if (!(host instanceof HTMLElement)) return;
    const textarea = getRecipeStepsTextarea(host);
    if (!(textarea instanceof HTMLTextAreaElement)) return;
    textarea.classList.add("recipe-steps-editor__source");

    let editor = getRecipeStepsEditor(host);
    if (!(editor instanceof HTMLElement)) {
      editor = document.createElement("section");
      editor.className = RECIPE_STEPS_EDITOR_CLASS;

      const header = document.createElement("header");
      header.className = "recipe-steps-editor__header";

      const title = document.createElement("strong");
      title.className = "recipe-steps-editor__title";
      title.textContent = "Pasos";

      const addButton = document.createElement("button");
      addButton.type = "button";
      addButton.className = "recipe-steps-editor__add-btn";
      addButton.title = "Añadir paso";
      addButton.setAttribute("aria-label", "Añadir paso");
      addButton.textContent = "+";

      header.appendChild(title);
      header.appendChild(addButton);

      const list = document.createElement("div");
      list.className = "recipe-steps-editor__list";
      editor.appendChild(header);
      editor.appendChild(list);
      host.appendChild(editor);

      bindRecipeStepsDnD(host, textarea, list);
      addButton.addEventListener("click", (event) => {
        event.preventDefault();
        appendRecipeStepItem(host, textarea, list, "", { focus: true });
      });
    }

    host.classList.add("recipe-steps-input--enhanced");
    const list = getRecipeStepsList(editor);
    if (!(list instanceof HTMLElement)) return;
    bindRecipeStepsDnD(host, textarea, list);
    renderRecipeStepsFromTextarea(host, textarea, list);

    // Additional resize pass after everything is set up to handle initial load
    window.setTimeout(() => {
      resizeAllRecipeStepInputs(list);
    }, 200);
  };

  const inlineChildren = (node) =>
    Array.from(node.childNodes)
      .map((child) => nodeToMarkdown(child, true, 0))
      .join("");

  const collapseInline = (value) =>
    (value || "").replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();

  const escapePipes = (value) => (value || "").replace(/\|/g, "\\|");
  let markdownConversionContext = null;

  const withMarkdownConversionContext = (context, callback) => {
    const previousContext = markdownConversionContext;
    markdownConversionContext = context || null;
    try {
      return callback();
    } finally {
      markdownConversionContext = previousContext;
    }
  };

  const getMarkdownConversionContext = () => markdownConversionContext || null;

  const buildBibFallbackByNumber = (markdown) => {
    const fallbackMap = new Map();
    const entries = collectBibDefinitions(markdown);
    entries.forEach((entry, index) => {
      if (!entry || !entry.key) return;
      const number = index + 1;
      fallbackMap.set(number, {
        key: normalizeCitationKey(entry.key || ""),
        target: String(entry.target || "").trim(),
        label: String(entry.label || "").trim(),
      });
    });
    return fallbackMap;
  };

  const buildBibFallbackByNumberFromPreview = () => {
    const fallbackMap = new Map();
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement)) return fallbackMap;
    const referenceNodes = Array.from(
      editor.querySelectorAll(
        "li.person-article-reference-item[data-reference-number], li.person-article-reference-item[id^='person-article-reference-']",
      ),
    );
    referenceNodes.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      let number = Number.parseInt(String(node.getAttribute("data-reference-number") || "").trim(), 10);
      if (!Number.isFinite(number) || number <= 0) {
        const idMatch = String(node.id || "").match(/person-article-reference-(\d{1,4})$/);
        if (idMatch) {
          number = Number.parseInt(idMatch[1], 10);
        }
      }
      if (!Number.isFinite(number) || number <= 0) return;
      const key = normalizeCitationKey(node.getAttribute("data-reference-key") || "");
      if (!key) return;
      fallbackMap.set(number, {
        key,
        target: String(node.getAttribute("data-reference-target") || "").trim(),
        label: String(node.getAttribute("data-reference-label") || "").trim(),
      });
    });
    return fallbackMap;
  };

  const buildCitationFallbackByNumber = (markdown) => {
    const mergedMap = buildBibFallbackByNumber(markdown);
    const previewMap = buildBibFallbackByNumberFromPreview();
    previewMap.forEach((value, number) => {
      if (!Number.isFinite(number) || number <= 0) return;
      const key = normalizeCitationKey(String(value?.key || ""));
      if (!key) return;
      mergedMap.set(number, value);
    });
    return mergedMap;
  };

  const rewriteBracketCitationsToMacros = (rawText, markdown) => {
    const source = String(rawText || "");
    if (!source) return "";
    const fallbackBibByNumber = buildCitationFallbackByNumber(markdown);
    if (!(fallbackBibByNumber instanceof Map) || !fallbackBibByNumber.size) {
      return source;
    }

    return source.replace(/\[(\d{1,4})\]/g, (match, rawNumber, offset, whole) => {
      const index = Number.parseInt(rawNumber, 10);
      if (!Number.isFinite(index) || index <= 0) return match;
      const fullText = String(whole || "");
      const previousChar = offset > 0 ? fullText.charAt(offset - 1) : "";
      const nextChar = fullText.charAt(offset + match.length) || "";
      if (previousChar === "[" || nextChar === "(") return match;

      const fallback = fallbackBibByNumber.get(index);
      const fallbackKey = normalizeCitationKey(String(fallback?.key || ""));
      if (!fallbackKey) return match;
      return `\\cite{${fallbackKey}}`;
    });
  };

  const getCitationAnchorForNode = (node) => {
    if (node instanceof HTMLElement) {
      const closest = node.closest("a.person-article-citation");
      return closest instanceof HTMLAnchorElement ? closest : null;
    }
    if (node instanceof Text) {
      const parent = node.parentElement;
      if (!(parent instanceof HTMLElement)) return null;
      const closest = parent.closest("a.person-article-citation");
      return closest instanceof HTMLAnchorElement ? closest : null;
    }
    return null;
  };

  const extractReferencesSectionMarkdown = (markdown) => {
    const lines = normalizeMarkdownLineEndings(markdown).split("\n");
    const headingIndex = lines.findIndex((line) => /^\s*#{1,6}\s+references\s*$/i.test(line));
    if (headingIndex < 0) return "";
    let endIndex = lines.length;
    for (let idx = headingIndex + 1; idx < lines.length; idx += 1) {
      if (/^\s*#{1,6}\s+\S+/.test(lines[idx])) {
        endIndex = idx;
        break;
      }
    }
    return lines.slice(headingIndex, endIndex).join("\n").trim();
  };

  const listToMarkdown = (listNode, ordered, depth) => {
    const items = Array.from(listNode.children).filter(
      (child) => child.tagName && child.tagName.toLowerCase() === "li",
    );
    if (!items.length) return "";

    let output = "";
    items.forEach((item, index) => {
      const marker = ordered ? `${index + 1}.` : "-";
      const indent = "  ".repeat(depth);
      const textParts = [];
      const nestedParts = [];
      Array.from(item.childNodes).forEach((child) => {
        if (child.nodeType === Node.ELEMENT_NODE) {
          const tag = child.tagName.toLowerCase();
          if (tag === "ul" || tag === "ol") {
            nestedParts.push(listToMarkdown(child, tag === "ol", depth + 1));
            return;
          }
          if (tag === "p") {
            textParts.push(collapseInline(inlineChildren(child)));
            return;
          }
        }
        textParts.push(nodeToMarkdown(child, true, depth));
      });
      const line = collapseInline(textParts.join(""));
      output += `${indent}${marker} ${line}`.trimEnd() + "\n";
      nestedParts.forEach((nested) => {
        if (!nested.trim()) return;
        output += `${nested.trimEnd()}\n`;
      });
    });
    return `${output}\n`;
  };

  const tableToMarkdown = (tableNode) => {
    const rows = Array.from(tableNode.querySelectorAll("tr")).map((row) =>
      Array.from(row.querySelectorAll("th, td")).map((cell) =>
        escapePipes(collapseInline(inlineChildren(cell))),
      ),
    );
    if (!rows.length) return "";
    const columnCount = rows.reduce((max, row) => Math.max(max, row.length), 0);
    if (!columnCount) return "";

    const normalizeRow = (row) =>
      Array.from({ length: columnCount }, (_, index) => row[index] || "");

    const header = normalizeRow(rows[0]);
    const divider = Array.from({ length: columnCount }, () => "---");
    const body = rows.slice(1).map(normalizeRow);

    const lines = [
      `| ${header.join(" | ")} |`,
      `| ${divider.join(" | ")} |`,
      ...body.map((row) => `| ${row.join(" | ")} |`),
    ];
    return `${lines.join("\n")}\n\n`;
  };

  const referencesNodeToMarkdown = (referencesNode) => {
    if (!(referencesNode instanceof HTMLElement)) return "";
    const preservedSection = getMarkdownConversionContext()?.previousReferencesSection;
    if (typeof preservedSection === "string") {
      const trimmedSection = preservedSection.trim();
      if (!trimmedSection) return "";
      return `${trimmedSection}\n\n`;
    }
    const referenceItems = Array.from(
      referencesNode.querySelectorAll(
        "li.person-article-reference-item, ol > li, ul > li",
      ),
    );
    if (!referenceItems.length) return "";

    const fallbackBibByNumber = getMarkdownConversionContext()?.fallbackBibByNumber;
    const lines = [];
    const seenKeys = new Set();
    const seenNumbers = new Set();

    referenceItems.forEach((item) => {
      if (!(item instanceof HTMLElement)) return;
      let key = normalizeCitationKey(item.getAttribute("data-reference-key") || "");
      let target = String(item.getAttribute("data-reference-target") || "").trim();
      let label = String(item.getAttribute("data-reference-label") || "").trim();
      let number = Number.parseInt(
        String(item.getAttribute("data-reference-number") || "").trim(),
        10,
      );
      if (!Number.isFinite(number) || number <= 0) {
        const idMatch = String(item.id || "").match(/person-article-reference-(\d{1,4})$/);
        if (idMatch) {
          const parsedFromId = Number.parseInt(idMatch[1], 10);
          if (Number.isFinite(parsedFromId) && parsedFromId > 0) {
            number = parsedFromId;
          }
        }
      }
      const linkNode = item.querySelector("a.person-article-reference-link");
      const textNode = item.querySelector(".person-article-reference-text");
      const visibleText = collapseInline(item.textContent || "");
      const linkText = linkNode ? collapseInline(linkNode.textContent || "") : "";
      const textValue = textNode ? collapseInline(textNode.textContent || "") : "";

      if (!target && linkNode instanceof HTMLAnchorElement) {
        const href = String(linkNode.getAttribute("href") || "").trim();
        if (href) {
          const normalizedTarget = normalizeReferenceTargetFromHref(href);
          if (normalizedTarget && normalizedTarget !== href) {
            target = normalizedTarget;
          } else if (/^https?:\/\//i.test(href)) {
            target = href;
          } else if (!href.startsWith("#")) {
            target = href;
          }
        }
      }
      if (!target && textValue) {
        target = textValue;
      }
      if (!target && linkText) {
        target = linkText;
      }
      if (!target && visibleText) {
        target = visibleText;
      }

      if (!label && linkText) {
        if (!target) {
          label = linkText;
        } else if (String(target).trim() !== linkText) {
          label = linkText;
        }
      }
      if (!target && label) {
        target = label;
        label = "";
      }
      if (Number.isFinite(number) && number > 0 && fallbackBibByNumber instanceof Map) {
        const fallback = fallbackBibByNumber.get(number);
        if (fallback && typeof fallback === "object") {
          if (!key) {
            key = normalizeCitationKey(fallback.key || "");
          }
          if (!target) {
            target = String(fallback.target || "").trim();
          }
          if (!label) {
            label = String(fallback.label || "").trim();
          }
        }
      }

      const definitionBody = buildBibDefinitionBody(target, label);
      if (key && definitionBody && !seenKeys.has(key)) {
        seenKeys.add(key);
        lines.push(`\\bib{${key} | ${definitionBody}}`);
        return;
      }
      if (!Number.isFinite(number) || number <= 0 || !definitionBody || seenNumbers.has(number)) {
        return;
      }
      seenNumbers.add(number);
      lines.push(`[${number}]: ${definitionBody}`);
    });

    if (!lines.length) return "";
    return `## References\n${lines.join("\n")}\n\n`;
  };

  const escapeHtmlAttribute = (value) =>
    String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");

  const readImageDimension = (node, axis) => {
    if (!(node instanceof HTMLImageElement)) return 0;
    const attrValue = Number.parseFloat(String(node.getAttribute(axis) || "").trim());
    if (Number.isFinite(attrValue) && attrValue > 0) {
      return Math.round(attrValue);
    }
    const styleValue = String(node.style?.[axis] || "").trim();
    const match = styleValue.match(/^([0-9]+(?:\.[0-9]+)?)px$/i);
    if (!match) return 0;
    const parsed = Number.parseFloat(match[1]);
    if (!Number.isFinite(parsed) || parsed <= 0) return 0;
    return Math.round(parsed);
  };

  const imageNodeToMarkdown = (node, inline) => {
    if (!(node instanceof HTMLImageElement)) return "";
    const alt = (node.getAttribute("alt") || "").trim();
    const src = (node.getAttribute("src") || "").trim();
    if (!src) return "";

    const width = readImageDimension(node, "width");
    const height = readImageDimension(node, "height");
    if (!width && !height) {
      return inline ? `![${alt}](${src})` : `![${alt}](${src})\n\n`;
    }

    const attrs = [`src="${escapeHtmlAttribute(src)}"`];
    if (alt) attrs.push(`alt="${escapeHtmlAttribute(alt)}"`);
    if (width) attrs.push(`width="${width}"`);
    if (height) attrs.push(`height="${height}"`);
    return inline ? `<img ${attrs.join(" ")} />` : `<img ${attrs.join(" ")} />\n\n`;
  };

  const setImageResizeContainer = (container) => {
    if (imageResizeContainer === container) return;
    if (imageResizeContainer instanceof HTMLElement) {
      imageResizeContainer.removeEventListener("scroll", scheduleImageResizeOverlayPosition);
    }
    imageResizeContainer = container instanceof HTMLElement ? container : null;
    if (imageResizeContainer instanceof HTMLElement) {
      imageResizeContainer.addEventListener("scroll", scheduleImageResizeOverlayPosition, {
        passive: true,
      });
    }
  };

  const getImageResizeMaxWidth = () => {
    const previewContainer = getPreviewContainer();
    if (!(previewContainer instanceof HTMLElement)) return 1800;
    return Math.max(
      IMAGE_RESIZE_MIN_WIDTH,
      Math.round(previewContainer.clientWidth * IMAGE_RESIZE_MAX_MULTIPLIER),
    );
  };

  const stopImageResizeDrag = (syncPreview = true) => {
    if (imageResizeDragState) {
      window.removeEventListener("pointermove", onImageResizeDragMove);
      window.removeEventListener("pointerup", onImageResizeDragEnd);
      window.removeEventListener("pointercancel", onImageResizeDragEnd);
      imageResizeDragState = null;
    }
    document.body.classList.remove("the-list-image-resizing");
    if (syncPreview) syncRawFromPreview();
  };

  const clearImageResizeOverlaySelection = () => {
    stopImageResizeDrag(false);
    if (imageResizeTarget instanceof HTMLImageElement) {
      imageResizeTarget.classList.remove(IMAGE_RESIZE_ACTIVE_CLASS);
    }
    imageResizeTarget = null;
    if (imageResizeOverlay instanceof HTMLElement) {
      imageResizeOverlay.hidden = true;
    }
  };

  const positionImageResizeOverlay = () => {
    if (!(imageResizeTarget instanceof HTMLImageElement)) return;
    if (!isCompiledMode()) {
      clearImageResizeOverlaySelection();
      return;
    }

    const previewContainer = getPreviewContainer();
    const previewEditor = getPreviewEditor();
    if (!(previewContainer instanceof HTMLElement) || !(previewEditor instanceof HTMLElement)) {
      clearImageResizeOverlaySelection();
      return;
    }
    if (!previewEditor.contains(imageResizeTarget)) {
      clearImageResizeOverlaySelection();
      return;
    }

    const overlay = ensureImageResizeOverlay();
    if (!(overlay instanceof HTMLElement)) return;
    if (overlay.parentElement !== previewContainer) {
      previewContainer.appendChild(overlay);
    }

    const imageRect = imageResizeTarget.getBoundingClientRect();
    if (imageRect.width <= 0 || imageRect.height <= 0) {
      overlay.hidden = true;
      return;
    }
    const containerRect = previewContainer.getBoundingClientRect();
    const left = imageRect.left - containerRect.left + previewContainer.scrollLeft;
    const top = imageRect.top - containerRect.top + previewContainer.scrollTop;

    overlay.style.left = `${left}px`;
    overlay.style.top = `${top}px`;
    overlay.style.width = `${imageRect.width}px`;
    overlay.style.height = `${imageRect.height}px`;
    overlay.hidden = false;
  };

  const scheduleImageResizeOverlayPosition = () => {
    if (imageResizePositionScheduled) return;
    imageResizePositionScheduled = true;
    window.requestAnimationFrame(() => {
      imageResizePositionScheduled = false;
      positionImageResizeOverlay();
    });
  };

  const computeImageResizeDelta = (direction, dx, dy) => {
    const horizontal = direction.includes("e") ? 1 : direction.includes("w") ? -1 : 0;
    const vertical = direction.includes("s") ? 1 : direction.includes("n") ? -1 : 0;
    if (horizontal && vertical) {
      const deltaX = dx * horizontal;
      const deltaY = dy * vertical;
      return Math.abs(deltaX) >= Math.abs(deltaY) ? deltaX : deltaY;
    }
    if (horizontal) return dx * horizontal;
    if (vertical) return dy * vertical;
    return 0;
  };

  const applyResizableImageWidth = (imageNode, widthValue) => {
    if (!(imageNode instanceof HTMLImageElement)) return;
    const safeWidth = Math.round(
      Math.max(IMAGE_RESIZE_MIN_WIDTH, Math.min(getImageResizeMaxWidth(), widthValue)),
    );
    imageNode.style.width = `${safeWidth}px`;
    imageNode.style.height = "auto";
    imageNode.setAttribute("width", String(safeWidth));
    imageNode.removeAttribute("height");
  };

  const onImageResizeDragMove = (event) => {
    if (!imageResizeDragState || !(imageResizeDragState.image instanceof HTMLImageElement)) return;
    if (event.pointerId !== imageResizeDragState.pointerId) return;
    const dx = event.clientX - imageResizeDragState.startX;
    const dy = event.clientY - imageResizeDragState.startY;
    const delta = computeImageResizeDelta(imageResizeDragState.direction, dx, dy);
    applyResizableImageWidth(imageResizeDragState.image, imageResizeDragState.startWidth + delta);
    scheduleImageResizeOverlayPosition();
  };

  const onImageResizeDragEnd = (event) => {
    if (!imageResizeDragState) return;
    if (event.pointerId !== imageResizeDragState.pointerId) return;
    stopImageResizeDrag(true);
  };

  const startImageResizeDrag = (event, direction) => {
    if (!(imageResizeTarget instanceof HTMLImageElement)) return;
    if (!isCompiledMode()) return;

    event.preventDefault();
    event.stopPropagation();

    stopImageResizeDrag(false);
    const imageRect = imageResizeTarget.getBoundingClientRect();
    const startWidth = readImageDimension(imageResizeTarget, "width") || imageRect.width;
    if (!startWidth) return;

    imageResizeDragState = {
      pointerId: event.pointerId,
      direction,
      image: imageResizeTarget,
      startX: event.clientX,
      startY: event.clientY,
      startWidth,
    };
    document.body.classList.add("the-list-image-resizing");
    window.addEventListener("pointermove", onImageResizeDragMove);
    window.addEventListener("pointerup", onImageResizeDragEnd);
    window.addEventListener("pointercancel", onImageResizeDragEnd);
  };

  const ensureImageResizeOverlay = () => {
    const previewContainer = getPreviewContainer();
    if (!(previewContainer instanceof HTMLElement)) return null;
    setImageResizeContainer(previewContainer);

    if (imageResizeOverlay instanceof HTMLElement) {
      if (imageResizeOverlay.parentElement !== previewContainer) {
        previewContainer.appendChild(imageResizeOverlay);
      }
      return imageResizeOverlay;
    }

    const overlay = document.createElement("div");
    overlay.className = "the-list-image-resize-overlay";
    overlay.hidden = true;
    IMAGE_RESIZE_DIRECTIONS.forEach((direction) => {
      const handle = document.createElement("button");
      handle.type = "button";
      handle.className = `the-list-image-resize-handle the-list-image-resize-handle--${direction}`;
      handle.setAttribute("aria-label", `Resize image (${direction})`);
      handle.dataset.direction = direction;
      handle.addEventListener("pointerdown", (event) => {
        startImageResizeDrag(event, direction);
      });
      overlay.appendChild(handle);
    });

    previewContainer.appendChild(overlay);
    imageResizeOverlay = overlay;
    return overlay;
  };

  const selectImageResizeTarget = (imageNode) => {
    if (!(imageNode instanceof HTMLImageElement)) return;
    if (!isCompiledMode()) return;
    const previewEditor = getPreviewEditor();
    if (!(previewEditor instanceof HTMLElement) || !previewEditor.contains(imageNode)) return;

    bindImageResizeOutsideClick();
    ensureImageResizeOverlay();
    if (imageResizeTarget === imageNode) {
      scheduleImageResizeOverlayPosition();
      return;
    }

    if (imageResizeTarget instanceof HTMLImageElement) {
      imageResizeTarget.classList.remove(IMAGE_RESIZE_ACTIVE_CLASS);
    }
    imageResizeTarget = imageNode;
    imageResizeTarget.classList.add(IMAGE_RESIZE_ACTIVE_CLASS);
    scheduleImageResizeOverlayPosition();
  };

  const syncImageResizeState = () => {
    if (!isCompiledMode()) {
      clearImageResizeOverlaySelection();
      return;
    }
    const previewEditor = getPreviewEditor();
    if (!(previewEditor instanceof HTMLElement)) {
      clearImageResizeOverlaySelection();
      return;
    }
    if (!(imageResizeTarget instanceof HTMLImageElement) || !previewEditor.contains(imageResizeTarget)) {
      clearImageResizeOverlaySelection();
      return;
    }
    ensureImageResizeOverlay();
    scheduleImageResizeOverlayPosition();
  };

  const bindImageResizeOutsideClick = () => {
    if (imageResizeOutsideClickBound) return;
    imageResizeOutsideClickBound = true;

    document.addEventListener(
      "pointerdown",
      (event) => {
        if (!(imageResizeTarget instanceof HTMLImageElement)) return;
        const target = event.target;
        if (!(target instanceof Element)) return;
        if (imageResizeOverlay instanceof HTMLElement && imageResizeOverlay.contains(target)) return;

        const previewEditor = getPreviewEditor();
        if (previewEditor instanceof HTMLElement && previewEditor.contains(target)) {
          const clickedImage = target.closest("img");
          if (clickedImage instanceof HTMLImageElement && previewEditor.contains(clickedImage)) return;
        }
        clearImageResizeOverlaySelection();
      },
      true,
    );

    window.addEventListener("resize", scheduleImageResizeOverlayPosition);
  };

  const handlePreviewEditorClick = (event) => {
    if (!isCompiledMode()) {
      clearImageResizeOverlaySelection();
      return;
    }
    const target = event.target;
    if (!(target instanceof Element)) return;
    const previewEditor = getPreviewEditor();
    if (!(previewEditor instanceof HTMLElement)) return;

    const imageNode = target.closest("img");
    if (imageNode instanceof HTMLImageElement && previewEditor.contains(imageNode)) {
      selectImageResizeTarget(imageNode);
      return;
    }
    clearImageResizeOverlaySelection();
  };

  const nodeToMarkdown = (node, inline, depth) => {
    if (!node) return "";
    if (node.nodeType === Node.TEXT_NODE) {
      const text = (node.textContent || "").replace(/\u00a0/g, " ");
      return inline ? text.replace(/\s+/g, " ") : text;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return "";

    const tag = node.tagName.toLowerCase();

      if (inline) {
      if (tag === "br") return "\n";
      if (tag === "strong" || tag === "b") return `**${inlineChildren(node)}**`;
      if (tag === "em" || tag === "i") return `*${inlineChildren(node)}*`;
      if (tag === "code") {
        const code = (node.textContent || "").replace(/`/g, "\\`");
        return `\`${code}\``;
      }
      if (tag === "a") {
        if (node.classList?.contains("person-article-citation")) {
          const citationKey = normalizeCitationKey(node.getAttribute("data-cite-key") || "");
          if (citationKey) return `\\cite{${citationKey}}`;
          const numericMatch = String(node.textContent || "").trim().match(/\[(\d{1,4})\]/);
          if (numericMatch) {
            const parsedNumber = Number.parseInt(numericMatch[1], 10);
            if (Number.isFinite(parsedNumber) && parsedNumber > 0) {
              const fallbackBibByNumber = getMarkdownConversionContext()?.fallbackBibByNumber;
              if (fallbackBibByNumber instanceof Map) {
                const fallback = fallbackBibByNumber.get(parsedNumber);
                const fallbackKey = normalizeCitationKey(String(fallback?.key || ""));
                if (fallbackKey) {
                  return `\\cite{${fallbackKey}}`;
                }
              }
              return `[${parsedNumber}]`;
            }
          }
          // Never degrade a citation anchor into a plain URL/link.
          return "";
        }
        const href = (node.getAttribute("href") || "").trim();
        const label = collapseInline(inlineChildren(node)) || href;
        return href ? `[${label}](${href})` : label;
      }
      if (tag === "img") return imageNodeToMarkdown(node, true);
      return inlineChildren(node);
    }

    if (/^h[1-6]$/.test(tag)) {
      const level = Number.parseInt(tag.slice(1), 10) || 1;
      const heading = collapseInline(inlineChildren(node));
      return heading ? `${"#".repeat(level)} ${heading}\n\n` : "";
    }
    if (tag === "p") {
      const text = collapseInline(inlineChildren(node));
      return text ? `${text}\n\n` : "\n";
    }
    if (tag === "ul") return listToMarkdown(node, false, depth);
    if (tag === "ol") return listToMarkdown(node, true, depth);
    if (tag === "pre") {
      const text = (node.textContent || "").replace(/\r\n/g, "\n").replace(/\n+$/, "");
      return text ? `\`\`\`\n${text}\n\`\`\`\n\n` : "";
    }
    if (tag === "blockquote") {
      const body = Array.from(node.childNodes)
        .map((child) => nodeToMarkdown(child, false, depth))
        .join("")
        .trim();
      if (!body) return "";
      return (
        body
          .split("\n")
          .map((line) => (line ? `> ${line}` : ">"))
          .join("\n") + "\n\n"
      );
    }
    if (tag === "table") return tableToMarkdown(node);
    if (tag === "div" && node.classList?.contains("person-article-references")) {
      return referencesNodeToMarkdown(node);
    }
    if (tag === "hr") return "---\n\n";
    if (tag === "br") return "\n";
    if (tag === "img") return imageNodeToMarkdown(node, false);

    return Array.from(node.childNodes)
      .map((child) => nodeToMarkdown(child, false, depth))
      .join("");
  };

  const htmlToMarkdown = (root, previousMarkdown = "") => {
    const context = {
      fallbackBibByNumber: buildBibFallbackByNumber(previousMarkdown),
      previousReferencesSection: extractReferencesSectionMarkdown(previousMarkdown),
    };
    const raw = withMarkdownConversionContext(context, () =>
      Array.from(root.childNodes)
        .map((child) => nodeToMarkdown(child, false, 0))
        .join(""),
    );
    return raw
      .replace(/\r\n/g, "\n")
      .replace(/[ \t]+\n/g, "\n");
  };

  const syncRawFromPreview = ({ emitEvents = false, forceEmit = false } = {}) => {
    if (!isCompiledMode()) return;
    const editor = getPreviewEditor();
    const textarea = getRawTextarea();
    if (!editor || !textarea) return;
    const markdown = htmlToMarkdown(editor, textarea.value || "");
    setRawTextareaValue(textarea, markdown, { emitEvents, forceEmit });
    return markdown;
  };

  const scheduleSyncRawFromPreview = () => {
    if (syncScheduled) return;
    syncScheduled = true;
    window.requestAnimationFrame(() => {
      syncScheduled = false;
      syncRawFromPreview({ emitEvents: false });
    });
  };

  const scheduleCompiledPreviewRerender = ({ immediate = false, preserveSelection = true } = {}) => {
    if (compiledPreviewSyncTimerId) {
      window.clearTimeout(compiledPreviewSyncTimerId);
      compiledPreviewSyncTimerId = 0;
    }
    const run = () => {
      compiledPreviewSyncTimerId = 0;
      if (!isCompiledMode()) return;
      const editor = getPreviewEditor();
      if (!(editor instanceof HTMLElement)) return;
      const hadFocus =
        preserveSelection &&
        document.activeElement instanceof Node &&
        editor.contains(document.activeElement);
      const selectionState = hadFocus ? capturePreviewTextSelectionState(editor) : null;
      const markdown = String(syncRawFromPreview({ emitEvents: false }) || "");
      if (markdown === lastCompiledPreviewMarkdown) return;
      lastCompiledPreviewMarkdown = markdown;
      syncRawFromPreview({ emitEvents: true, forceEmit: true });
      rerenderCompiledPreviewFromRaw({ selectionState });
    };
    if (!immediate) return;
    run();
  };

  const applyPasteAsPlainText = (event) => {
    if (!isCompiledMode()) return;
    const text = (event.clipboardData || window.clipboardData)?.getData("text/plain");
    if (!text) return;
    const textarea = getRawTextarea();
    const previousMarkdown = textarea instanceof HTMLTextAreaElement ? textarea.value || "" : "";
    const transformedText = rewriteBracketCitationsToMacros(text, previousMarkdown);
    event.preventDefault();
    let inserted = false;
    if (document.queryCommandSupported?.("insertText")) {
      document.execCommand("insertText", false, transformedText);
      inserted = true;
    } else {
      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) return;
      selection.deleteFromDocument();
      selection.getRangeAt(0).insertNode(document.createTextNode(transformedText));
      inserted = true;
    }
    if (!inserted) return;
    scheduleSyncRawFromPreview();
    scheduleCompiledPreviewRerender({ immediate: false, preserveSelection: true });
    if (/\\(?:cite|bib)\{[^{}\n]+\}/i.test(transformedText)) {
      window.setTimeout(() => {
        lastCompiledPreviewMarkdown = "";
        scheduleCompiledPreviewRerender({ immediate: true, preserveSelection: true });
      }, 0);
    }
  };

  const handleCompiledClipboardEvent = (event) => {
    if (!isCompiledMode()) return;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement)) return;

    const selection = window.getSelection();
    if (!selection || selection.rangeCount < 1) return;
    const range = selection.getRangeAt(0);
    if (range.collapsed) return;
    if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return;

    const wrapper = document.createElement("div");
    wrapper.appendChild(range.cloneContents());
    const hasWrappedCitation = wrapper.querySelector("a.person-article-citation");
    const startCitation = getCitationAnchorForNode(range.startContainer);
    const endCitation = getCitationAnchorForNode(range.endContainer);
    if (
      !(hasWrappedCitation instanceof HTMLElement) &&
      !(startCitation instanceof HTMLAnchorElement) &&
      !(endCitation instanceof HTMLAnchorElement)
    ) {
      return;
    }

    const textarea = getRawTextarea();
    const previousMarkdown = textarea instanceof HTMLTextAreaElement ? textarea.value || "" : "";
    const snippet = rewriteBracketCitationsToMacros(
      htmlToMarkdown(wrapper, previousMarkdown),
      previousMarkdown,
    ).trim();
    if (!snippet) return;

    let clipboardHandled = false;
    if (event.clipboardData) {
      event.clipboardData.setData("text/plain", snippet);
      event.preventDefault();
      clipboardHandled = true;
    } else if (navigator?.clipboard && typeof navigator.clipboard.writeText === "function") {
      event.preventDefault();
      void navigator.clipboard.writeText(snippet).catch(() => {});
      clipboardHandled = true;
    }
    if (!clipboardHandled) return;

    if (event.type !== "cut") return;
    const citationNodesToRemove = new Set();
    if (startCitation instanceof HTMLAnchorElement) {
      citationNodesToRemove.add(startCitation);
    }
    if (endCitation instanceof HTMLAnchorElement) {
      citationNodesToRemove.add(endCitation);
    }
    if (citationNodesToRemove.size > 0 && !(hasWrappedCitation instanceof HTMLElement)) {
      citationNodesToRemove.forEach((node) => {
        if (node instanceof HTMLElement) node.remove();
      });
    } else {
      selection.deleteFromDocument();
    }
    scheduleSyncRawFromPreview();
    scheduleCompiledPreviewRerender({ immediate: false, preserveSelection: true });
    window.setTimeout(() => {
      lastCompiledPreviewMarkdown = "";
      scheduleCompiledPreviewRerender({ immediate: true, preserveSelection: true });
    }, 0);
  };

  const bindPreviewEditor = () => {
    const editor = getPreviewEditor();
    if (!editor || boundEditors.has(editor)) return;
    boundEditors.add(editor);
    editor.addEventListener("input", () => {
      scheduleSyncRawFromPreview();
    });
    editor.addEventListener("input", scheduleImageResizeOverlayPosition);
    editor.addEventListener("blur", () => {
      scheduleCompiledPreviewRerender({ immediate: true, preserveSelection: false });
    });
    editor.addEventListener("paste", applyPasteAsPlainText);
    editor.addEventListener("copy", handleCompiledClipboardEvent);
    editor.addEventListener("cut", handleCompiledClipboardEvent);
    editor.addEventListener("click", handlePreviewEditorClick);
    editor.addEventListener("pointerover", handleCitationTooltipPointerOver);
    editor.addEventListener("pointerout", handleCitationTooltipPointerOut);
    editor.addEventListener("focusin", handleCitationTooltipFocusIn);
    editor.addEventListener("focusout", handleCitationTooltipFocusOut);
    bindCitationTooltipGlobalEvents();
  };

  const neutralizeCompiledCitationAnchors = () => {
    if (!isCompiledMode()) return;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement)) return;
    const citationNodes = Array.from(editor.querySelectorAll("a"));
    citationNodes.forEach((node) => {
      if (!(node instanceof HTMLAnchorElement)) return;
      const text = collapseInline(node.textContent || "");
      const isCitationClass = node.classList.contains("person-article-citation");
      const looksLikeNumericCitation = /^\[(\d{1,4})\]$/.test(text);
      if (!isCitationClass && !looksLikeNumericCitation) return;
      node.classList.add("person-article-citation");
      const href = String(node.getAttribute("href") || "").trim();
      if (href && !String(node.dataset.citeHref || "").trim()) {
        node.dataset.citeHref = href;
      }
      node.removeAttribute("href");
      node.removeAttribute("target");
      node.removeAttribute("rel");
    });
  };

  const buildReferenceInfoByNumber = (editor) => {
    const referenceInfoByNumber = new Map();
    if (!(editor instanceof HTMLElement)) return referenceInfoByNumber;
    const referenceNodes = Array.from(
      editor.querySelectorAll(
        "li.person-article-reference-item[data-reference-number], li.person-article-reference-item[id^='person-article-reference-']",
      ),
    );
    referenceNodes.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      let number = Number.parseInt(String(node.getAttribute("data-reference-number") || "").trim(), 10);
      if (!Number.isFinite(number) || number <= 0) {
        const idMatch = String(node.id || "").match(/person-article-reference-(\d{1,4})$/);
        if (idMatch) {
          number = Number.parseInt(idMatch[1], 10);
        }
      }
      if (!Number.isFinite(number) || number <= 0) return;

      const targetValue = String(node.getAttribute("data-reference-target") || "").trim();
      const definitionLabel = String(node.getAttribute("data-reference-label") || "").trim();
      const linkNode = node.querySelector("a.person-article-reference-link");
      const linkHref = linkNode instanceof HTMLAnchorElement ? String(linkNode.getAttribute("href") || "").trim() : "";
      const linkText = linkNode instanceof HTMLAnchorElement ? collapseInline(linkNode.textContent || "") : "";
      const textNode = node.querySelector(".person-article-reference-text");
      const textValue = textNode instanceof HTMLElement ? collapseInline(textNode.textContent || "") : "";

      const labelValue = definitionLabel || linkText || textValue;
      const targetForPreview = targetValue || linkHref;
      let preview = labelValue || targetForPreview;
      if (labelValue && targetForPreview && labelValue !== targetForPreview) {
        preview = `${labelValue} (${targetForPreview})`;
      }
      preview = String(preview || "").trim();
      const citeKey = normalizeCitationKey(node.getAttribute("data-reference-key") || "");
      if (!preview && !citeKey) return;
      referenceInfoByNumber.set(number, {
        preview,
        key: citeKey,
      });
    });
    return referenceInfoByNumber;
  };

  const hydrateCompiledCitationPreviews = () => {
    if (!isCompiledMode()) return;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement)) return;

    const referenceInfoByNumber = buildReferenceInfoByNumber(editor);

    const citationNodes = Array.from(editor.querySelectorAll("a"));
    citationNodes.forEach((node) => {
      if (!(node instanceof HTMLAnchorElement)) return;
      const text = collapseInline(node.textContent || "");
      const isCitationClass = node.classList.contains("person-article-citation");
      const numberMatch = text.match(/^\[(\d{1,4})\]$/);
      if (!isCitationClass && !numberMatch) return;

      node.classList.add("person-article-citation");
      const existingPreview = String(node.getAttribute("data-cite-preview") || "").trim();
      const match = numberMatch || text.match(/\[(\d{1,4})\]/);
      if (!match) return;
      const number = Number.parseInt(match[1], 10);
      if (!Number.isFinite(number) || number <= 0) return;
      const referenceInfo = referenceInfoByNumber.get(number) || {};
      const resolvedPreview = String(referenceInfo.preview || existingPreview || "").trim();
      const resolvedKey = normalizeCitationKey(String(referenceInfo.key || node.getAttribute("data-cite-key") || ""));

      if (resolvedPreview) {
        node.setAttribute("data-cite-preview", resolvedPreview);
        node.setAttribute("aria-label", resolvedPreview);
      } else {
        node.removeAttribute("data-cite-preview");
        node.removeAttribute("aria-label");
      }
      if (resolvedKey) {
        node.setAttribute("data-cite-key", resolvedKey);
      }
    });
  };

  const getCitationAnchorFromTarget = (target) => {
    if (!(target instanceof Element)) return null;
    const anchor = target.closest("a.person-article-citation");
    return anchor instanceof HTMLAnchorElement ? anchor : null;
  };

  const ensureCitationTooltipNode = () => {
    if (citationTooltipNode instanceof HTMLElement && document.body.contains(citationTooltipNode)) {
      return citationTooltipNode;
    }
    const node = document.createElement("div");
    node.id = CITATION_TOOLTIP_ID;
    node.style.position = "fixed";
    node.style.left = "0";
    node.style.top = "0";
    node.style.maxWidth = "min(340px, 72vw)";
    node.style.padding = "0.45rem 0.55rem";
    node.style.borderRadius = "8px";
    node.style.border = "1px solid #cbd5e1";
    node.style.background = "#0f172a";
    node.style.color = "#f8fafc";
    node.style.boxShadow = "0 14px 24px rgba(15, 23, 42, 0.3)";
    node.style.fontSize = "0.78rem";
    node.style.lineHeight = "1.35";
    node.style.textAlign = "left";
    node.style.whiteSpace = "normal";
    node.style.pointerEvents = "none";
    node.style.zIndex = "5000";
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    node.style.transition = "opacity 0.16s ease, transform 0.16s ease";
    node.hidden = true;
    document.body.appendChild(node);
    citationTooltipNode = node;
    return node;
  };

  const hideCitationTooltip = () => {
    citationTooltipAnchor = null;
    const node = citationTooltipNode;
    if (!(node instanceof HTMLElement)) return;
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    window.setTimeout(() => {
      if (citationTooltipAnchor) return;
      node.hidden = true;
    }, 170);
  };

  const resolveCitationPreviewFromAnchor = (anchor, editor) => {
    if (!(anchor instanceof HTMLAnchorElement)) return "";
    const directPreview = String(anchor.getAttribute("data-cite-preview") || anchor.getAttribute("aria-label") || "").trim();
    if (directPreview) return directPreview;

    const text = collapseInline(anchor.textContent || "");
    const match = text.match(/\[(\d{1,4})\]/);
    if (!match) return "";
    const number = Number.parseInt(match[1], 10);
    if (!Number.isFinite(number) || number <= 0) return "";

    const referenceInfo = buildReferenceInfoByNumber(editor).get(number);
    const preview = String(referenceInfo?.preview || "").trim();
    if (preview) {
      anchor.setAttribute("data-cite-preview", preview);
      anchor.setAttribute("aria-label", preview);
    }
    return preview;
  };

  const positionCitationTooltip = () => {
    const node = citationTooltipNode;
    const anchor = citationTooltipAnchor;
    if (!(node instanceof HTMLElement) || !(anchor instanceof HTMLElement)) {
      hideCitationTooltip();
      return;
    }
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement) || !editor.contains(anchor) || !isCompiledMode()) {
      hideCitationTooltip();
      return;
    }

    const rect = anchor.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      hideCitationTooltip();
      return;
    }
    const spacing = 10;
    const viewportPadding = 8;
    const tooltipWidth = node.offsetWidth || 240;
    const tooltipHeight = node.offsetHeight || 52;

    let left = rect.left + rect.width / 2 - tooltipWidth / 2;
    left = Math.max(viewportPadding, Math.min(left, window.innerWidth - tooltipWidth - viewportPadding));

    let top = rect.top - tooltipHeight - spacing;
    if (top < viewportPadding) {
      top = rect.bottom + spacing;
    }

    node.style.left = `${Math.round(left)}px`;
    node.style.top = `${Math.round(top)}px`;
  };

  const showCitationTooltipForAnchor = (anchor) => {
    if (!isCompiledMode()) return;
    const editor = getPreviewEditor();
    if (!(editor instanceof HTMLElement) || !(anchor instanceof HTMLAnchorElement) || !editor.contains(anchor)) {
      hideCitationTooltip();
      return;
    }
    const preview = resolveCitationPreviewFromAnchor(anchor, editor);
    if (!preview) {
      hideCitationTooltip();
      return;
    }

    const node = ensureCitationTooltipNode();
    node.textContent = preview;
    node.hidden = false;
    citationTooltipAnchor = anchor;
    positionCitationTooltip();
    window.requestAnimationFrame(() => {
      if (citationTooltipAnchor !== anchor) return;
      node.style.opacity = "1";
      node.style.transform = "translateY(0)";
      positionCitationTooltip();
    });
  };

  const handleCitationTooltipPointerOver = (event) => {
    if (!isCompiledMode()) return;
    const anchor = getCitationAnchorFromTarget(event.target);
    if (!(anchor instanceof HTMLAnchorElement)) return;
    showCitationTooltipForAnchor(anchor);
  };

  const handleCitationTooltipPointerOut = (event) => {
    const leavingAnchor = getCitationAnchorFromTarget(event.target);
    if (!(leavingAnchor instanceof HTMLAnchorElement)) return;
    const nextAnchor = getCitationAnchorFromTarget(event.relatedTarget);
    if (nextAnchor === leavingAnchor) return;
    hideCitationTooltip();
  };

  const handleCitationTooltipFocusIn = (event) => {
    if (!isCompiledMode()) return;
    const anchor = getCitationAnchorFromTarget(event.target);
    if (!(anchor instanceof HTMLAnchorElement)) return;
    showCitationTooltipForAnchor(anchor);
  };

  const handleCitationTooltipFocusOut = (event) => {
    const leavingAnchor = getCitationAnchorFromTarget(event.target);
    if (!(leavingAnchor instanceof HTMLAnchorElement)) return;
    const nextAnchor = getCitationAnchorFromTarget(event.relatedTarget);
    if (nextAnchor === leavingAnchor) return;
    hideCitationTooltip();
  };

  const bindCitationTooltipGlobalEvents = () => {
    if (citationTooltipGlobalBound) return;
    citationTooltipGlobalBound = true;
    window.addEventListener("resize", positionCitationTooltip);
    window.addEventListener("scroll", positionCitationTooltip, true);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) hideCitationTooltip();
    });
  };

  const updateEditorMode = () => {
    const editor = getPreviewEditor();
    if (!editor) {
      clearImageResizeOverlaySelection();
      return;
    }
    const editable = isCompiledMode();
    editor.setAttribute("contenteditable", editable ? "true" : "false");
    editor.setAttribute("spellcheck", "true");
    editor.classList.add(VISUAL_EDITOR_CLASS);
    editor.classList.toggle(VISUAL_EDITOR_ACTIVE_CLASS, editable);
    if (!editable) {
      hideCitationTooltip();
    }
    neutralizeCompiledCitationAnchors();
    hydrateCompiledCitationPreviews();
    syncImageResizeState();
  };

  const dockDetailActions = () => {
    const titleSlot = document.getElementById(CARD_TITLE_ACTIONS_SLOT_ID);
    const pageTitleRow = document.getElementById(PAGE_TITLE_ROW_ID);
    const cardEditButton = document.getElementById(CARD_EDIT_BUTTON_ID);
    const downloadMenu = document.getElementById(DOWNLOAD_MENU_ID);
    const deleteButton = document.getElementById(DELETE_BUTTON_ID);
    const reviewLink = document.getElementById(REVIEW_LINK_ID);
    if (pageTitleRow) {
      if (cardEditButton && cardEditButton.parentElement !== pageTitleRow) {
        pageTitleRow.appendChild(cardEditButton);
      }
      if (downloadMenu && downloadMenu.parentElement !== pageTitleRow) {
        pageTitleRow.appendChild(downloadMenu);
      }
      if (deleteButton && deleteButton.parentElement !== pageTitleRow) {
        pageTitleRow.appendChild(deleteButton);
      }
      if (reviewLink && reviewLink.parentElement !== pageTitleRow) {
        pageTitleRow.appendChild(reviewLink);
      }
    } else if (titleSlot) {
      if (cardEditButton && cardEditButton.parentElement !== titleSlot) {
        titleSlot.appendChild(cardEditButton);
      }
      if (downloadMenu && downloadMenu.parentElement !== titleSlot) {
        titleSlot.appendChild(downloadMenu);
      }
      if (deleteButton && deleteButton.parentElement !== titleSlot) {
        titleSlot.appendChild(deleteButton);
      }
    }

    const markdownHost = document.getElementById(MARKDOWN_CONTAINER_ID);
    const markdownEditButton = document.getElementById(MARKDOWN_EDIT_BUTTON_ID);
    if (markdownHost && markdownEditButton && markdownEditButton.parentElement !== markdownHost) {
      markdownHost.appendChild(markdownEditButton);
    }
  };

  const ensureToastRoot = () => {
    let root = document.getElementById(TOAST_ROOT_ID);
    if (root) return root;
    root = document.createElement("div");
    root.id = TOAST_ROOT_ID;
    document.body.appendChild(root);
    return root;
  };

  const showSuccessToast = (message) => {
    const root = ensureToastRoot();
    const toast = document.createElement("div");
    toast.className = "the-list-toast the-list-toast--success";
    toast.textContent = String(message || "").replace(/^\s*✅\s*/, "").trim();
    root.appendChild(toast);
    window.setTimeout(() => {
      toast.classList.add("is-hiding");
    }, TOAST_HIDE_DELAY_MS);
    window.setTimeout(() => {
      toast.remove();
    }, TOAST_REMOVE_DELAY_MS);
  };

  const scrollToTop = () => {
    const options = { top: 0, left: 0, behavior: "smooth" };
    try {
      window.scrollTo(options);
    } catch (error) {
      window.scrollTo(0, 0);
      void error;
    }
    const targets = [
      document.documentElement,
      document.body,
      document.querySelector(".gradio-container > .main"),
      document.querySelector(".gradio-container > .main > .wrap"),
      document.querySelector(".gradio-container > .main > .wrap > .contain"),
    ].filter(Boolean);
    targets.forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      try {
        node.scrollTo(options);
      } catch (error) {
        node.scrollTop = 0;
        void error;
      }
    });
  };

  const getStatusMessage = (statusNode) => {
    if (!statusNode) return "";
    return String(statusNode.textContent || "")
      .replace(/\s+/g, " ")
      .trim();
  };

  const clearStatusMessage = (statusNode) => {
    if (!statusNode) return;
    statusNode.textContent = "";
  };

  const bindProposalStatusToasts = () => {
    PROPOSAL_STATUS_IDS.forEach((statusId) => {
      const statusNode = document.getElementById(statusId);
      if (!statusNode || boundStatusNodes.has(statusNode)) return;
      boundStatusNodes.add(statusNode);
      let lastMessage = "";
      const syncStatus = () => {
        const message = getStatusMessage(statusNode);
        if (!message || message === lastMessage) return;
        lastMessage = message;
        if (!message.startsWith("✅")) return;
        if (statusId === "recipe-page-status") {
          if (CREATE_RECIPE_STATUS_PATTERN.test(message)) {
            clearStatusMessage(statusNode);
            if (!createdRecipeRedirectTriggered) {
              createdRecipeRedirectTriggered = true;
              try {
                window.sessionStorage.setItem(RECETAS_LIST_TOAST_SESSION_KEY, "created");
              } catch (error) {
                void error;
              }
              window.location.assign(RECETAS_LIST_CREATED_URL);
            }
            return;
          }
          if (DELETE_RECIPE_STATUS_PATTERN.test(message)) {
            clearStatusMessage(statusNode);
            if (!deletedRecipeRedirectTriggered) {
              deletedRecipeRedirectTriggered = true;
              try {
                window.sessionStorage.setItem(RECETAS_LIST_TOAST_SESSION_KEY, "deleted");
              } catch (error) {
                void error;
              }
              window.location.assign(RECETAS_LIST_DELETED_URL);
            }
            return;
          }
          if (!VERIFY_STATUS_PATTERN.test(message)) return;
        }
        showSuccessToast(message);
        clearStatusMessage(statusNode);
        if (statusId !== "recipe-page-status") {
          scrollToTop();
        }
      };
      const statusObserver = new MutationObserver(syncStatus);
      statusObserver.observe(statusNode, {
        childList: true,
        subtree: true,
        characterData: true,
      });
      syncStatus();
    });
  };

  const refreshVisualEditor = () => {
    dockDetailActions();
    dockCardProposalActions();
    bindPreviewEditor();
    updateEditorMode();
    bindCitationInsertButton();
    bindBibliographyInsertButton();
    bindProposalStatusToasts();
    bindDetailVerifiedToggle();
    bindRecipeDeleteGuard();
    bindRecipeDownloadPayload();
    refreshInlineCardEditor();
    ensureRecipeIngredientsEditor();
    ensureRecipeStepsEditor();
    const modal = cardImageCropUi?.modal;
    const modalInDom = modal instanceof HTMLElement && document.body.contains(modal);
    const modalVisible = modalInDom && !modal.hidden;
    const cropKey = [
      cardImageCropState ? "state:1" : "state:0",
      modalInDom ? "dom:1" : "dom:0",
      modalVisible ? "visible:1" : "visible:0",
    ].join("|");
    if (cropKey !== lastCropModalVisibilityKey) {
      lastCropModalVisibilityKey = cropKey;
      cropDebugLog("crop_modal.visibility", { state: cropKey });
    }
  };

  const refreshCompiledPreviewSnapshot = () => {
    const rawTextarea = getRawTextarea();
    if (!(rawTextarea instanceof HTMLTextAreaElement)) return;
    lastCompiledPreviewMarkdown = String(rawTextarea.value || "");
  };

  const observer = new MutationObserver(() => {
    refreshVisualEditor();
  });

  const start = () => {
    ensureCropDebugPanel();
    cropDebugLog("script.start", {
      href: window.location.href,
      userAgent: navigator.userAgent,
    });
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener(
      "pointerdown",
      (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (!target.closest(`#${MODE_CONTAINER_ID}`)) return;
        if (isCompiledMode()) {
          syncRawFromPreview({ emitEvents: true, forceEmit: true });
          refreshCompiledPreviewSnapshot();
          return;
        }
        forceEmitRawTextarea();
      },
      true,
    );
    document.addEventListener(
      "pointerdown",
      (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;
        if (!target.closest(`#${PROPOSAL_ACTIONS_ID} button`)) return;
        if (!isCompiledMode()) return;
        syncRawFromPreview({ emitEvents: true, forceEmit: true });
        refreshCompiledPreviewSnapshot();
      },
      true,
    );
    document.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.closest(`#${MODE_CONTAINER_ID}`)) {
        cropDebugLog("mode.change_event");
        window.setTimeout(() => {
          refreshVisualEditor();
          refreshCompiledPreviewSnapshot();
        }, 0);
      }
    });
    bindRecipeImportFileChipRemovers();
    refreshVisualEditor();
    refreshCompiledPreviewSnapshot();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
