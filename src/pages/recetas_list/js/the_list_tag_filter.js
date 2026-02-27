(function () {
  if (typeof window === "undefined") return;

  const DROPDOWN_CONFIGS = [
    { id: "people-tag-filter", placeholder: "Filtrar por etiquetas", singular: "etiqueta", plural: "etiquetas" },
    { id: "people-tool-filter", placeholder: "Filtrar por herramientas", singular: "herramienta", plural: "herramientas" },
  ];
  const CREATE_TRIGGER_ID = "the-list-create-profile-trigger";
  const CREATE_PAGE_PATH = "/receta/?create=1";
  const VERIFY_PAYLOAD_ID = "recipe-verify-payload";
  const VERIFY_TRIGGER_ID = "recipe-verify-trigger";
  const ICON_LOAD_MORE_TRIGGER_ID = "recipe-icon-load-more-trigger";
  const CARDS_HOST_ID = "people-cards";
  const TOAST_ROOT_ID = "the-list-toast-root";
  const TOAST_HIDE_DELAY_MS = 4200;
  const TOAST_REMOVE_DELAY_MS = 4700;
  const ALL_VALUE = "all";
  const CREATED_RECIPE_QUERY_PARAM = "created_recipe";
  const DELETED_RECIPE_QUERY_PARAM = "deleted_recipe";
  const RECETAS_LIST_TOAST_SESSION_KEY = "recetas_list_success_toast";

  const ensureRoot = () => {
    if (typeof window.gradioApp === "function") {
      try {
        const app = window.gradioApp();
        if (app) return app;
      } catch (error) {
        void error;
      }
    }
    const host = document.querySelector("gradio-app");
    return host ? host.shadowRoot || host : document;
  };

  const normalizeValue = (value) => String(value || "").trim().toLowerCase();
  const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);
  const FALSE_VALUES = new Set(["0", "false", "no", "off"]);
  const isAllValue = (value) => normalizeValue(value) === ALL_VALUE;
  const isEnabledQueryParam = (value) => {
    if (value === null) return false;
    const normalized = normalizeValue(value);
    if (!normalized) return true;
    if (TRUE_VALUES.has(normalized)) return true;
    return !FALSE_VALUES.has(normalized);
  };

  const readSessionToastKind = () => {
    try {
      return normalizeValue(window.sessionStorage.getItem(RECETAS_LIST_TOAST_SESSION_KEY));
    } catch (error) {
      void error;
      return "";
    }
  };

  const clearSessionToastKind = () => {
    try {
      window.sessionStorage.removeItem(RECETAS_LIST_TOAST_SESSION_KEY);
    } catch (error) {
      void error;
    }
  };

  const dedupeValues = (values) => {
    const out = [];
    const seen = new Set();
    (values || []).forEach((value) => {
      const text = String(value || "").trim();
      if (!text) return;
      const key = normalizeValue(text);
      if (seen.has(key)) return;
      seen.add(key);
      out.push(text);
    });
    return out;
  };

  const hiddenInput = (scope) =>
    scope.querySelector(
      "textarea, input[type='hidden'], .choices input[type='hidden'], .multiselect input[type='hidden'], .selectize-control input[type='hidden']",
    );
  const q = (root, selector) => (root ? root.querySelector(selector) : null);
  const setTextboxValue = (el, value) => {
    if (!el) return;
    el.value = String(value ?? "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
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

  let recipeQueryToastHandled = false;

  const maybeShowRecipeQueryToast = () => {
    if (recipeQueryToastHandled) return;
    let url;
    try {
      url = new URL(window.location.href);
    } catch (error) {
      void error;
      return;
    }

    const rawCreatedParam = url.searchParams.get(CREATED_RECIPE_QUERY_PARAM);
    const rawDeletedParam = url.searchParams.get(DELETED_RECIPE_QUERY_PARAM);
    const hasQueryToastFlag = rawCreatedParam !== null || rawDeletedParam !== null;
    const sessionToastKind = readSessionToastKind();
    if (!hasQueryToastFlag && !sessionToastKind) return;

    recipeQueryToastHandled = true;
    const shouldShowCreated = isEnabledQueryParam(rawCreatedParam) || sessionToastKind === "created";
    const shouldShowDeleted = isEnabledQueryParam(rawDeletedParam) || sessionToastKind === "deleted";
    if (shouldShowCreated) {
      showSuccessToast("Receta creada correctamente.");
    }
    if (shouldShowDeleted) {
      showSuccessToast("Receta eliminada correctamente.");
    }

    if (hasQueryToastFlag) {
      url.searchParams.delete(CREATED_RECIPE_QUERY_PARAM);
      url.searchParams.delete(DELETED_RECIPE_QUERY_PARAM);
      const nextSearch = url.searchParams.toString();
      const nextUrl = `${url.pathname}${nextSearch ? `?${nextSearch}` : ""}${url.hash || ""}`;
      window.history.replaceState(window.history.state, "", nextUrl);
    }
    clearSessionToastKind();
  };

  const parseValues = (scope) => {
    const hidden = hiddenInput(scope);
    if (!hidden) return [];
    const raw = hidden.value || "";
    if (!raw.trim()) return [];
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        return dedupeValues(parsed.map((value) => String(value || "").trim()));
      }
    } catch (error) {
      void error;
    }
    return dedupeValues(
      raw
        .split(/[\n,]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    );
  };

  const optionNodes = (scope) =>
    Array.from(
      scope.querySelectorAll(
        ".options .item, .choices__list--dropdown .choices__item--selectable, .multiselect__option, .vs__dropdown-option, .selectize-dropdown .option",
      ),
    );

  const extractOptionValue = (node) => {
    if (!node) return "";
    if (node.dataset && node.dataset.peopleOptionValue) {
      return String(node.dataset.peopleOptionValue || "").trim();
    }
    if (node.dataset && node.dataset.value) return String(node.dataset.value || "").trim();
    const childWithValue = node.querySelector && node.querySelector("[data-value]");
    if (childWithValue && childWithValue.dataset && childWithValue.dataset.value) {
      return String(childWithValue.dataset.value || "").trim();
    }
    const control = node.querySelector && node.querySelector("input[type='checkbox'], input[type='radio']");
    if (control && typeof control.value !== "undefined") {
      return String(control.value || "").trim();
    }
    return String(node.textContent || "").trim();
  };

  const normalizeOptionLabel = (value) =>
    String(value || "")
      .replace(/[✓✔]/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const collectOptionValues = (scope) => {
    const values = [];
    const seen = new Set();
    optionNodes(scope).forEach((node) => {
      const value = String(extractOptionValue(node) || "").trim();
      if (!value || isAllValue(value)) return;
      const key = normalizeValue(value);
      if (seen.has(key)) return;
      seen.add(key);
      values.push(value);
    });
    return values;
  };

  const parseSelectionCount = (scope) => {
    const unique = new Set();
    parseValues(scope).forEach((value) => {
      if (!isAllValue(value)) unique.add(normalizeValue(value));
    });
    return unique.size;
  };

  const summaryText = (count, config) => {
    if (count <= 0) return config.placeholder;
    if (count === 1) return `1 ${config.singular} seleccionada`;
    return `${count} ${config.plural} seleccionadas`;
  };

  const stripNativeTick = (node) => {
    if (!node || node.dataset.peopleTagTickStripped === "1") return;
    const stableValue = normalizeOptionLabel(node.dataset.peopleOptionValue || "") || normalizeOptionLabel(extractOptionValue(node));
    if (stableValue) node.dataset.peopleOptionValue = stableValue;
    const cleanLabel =
      normalizeOptionLabel(node.dataset.peopleCleanLabel || "") ||
      stableValue ||
      normalizeOptionLabel(node.textContent || "");
    if (cleanLabel) {
      node.dataset.peopleCleanLabel = cleanLabel;
    }

    let modified = false;
    const firstEl = node.firstElementChild;
    if (firstEl) {
      const text = (firstEl.textContent || "").trim();
      if (/^[✓✔]+$/.test(text)) {
        firstEl.remove();
        modified = true;
      }
    }
    const firstSvg = node.querySelector && node.querySelector("svg");
    if (firstSvg) {
      firstSvg.remove();
      modified = true;
    }
    const textNodeType = typeof Node !== "undefined" ? Node.TEXT_NODE : 3;
    const textNode = node.firstChild;
    if (textNode && textNode.nodeType === textNodeType) {
      const next = textNode.textContent || "";
      const replaced = next.replace(/^\s*[✓✔]+\s*/, "");
      if (replaced !== next) {
        textNode.textContent = replaced;
        modified = true;
      }
    }
    if (modified) {
      node.dataset.peopleTagTickStripped = "1";
    }
  };

  const decorateOptions = (scope) => {
    optionNodes(scope).forEach((node) => {
      node.classList.add("people-tag-option");
      stripNativeTick(node);
      const value = extractOptionValue(node);
      const label = node.dataset.peopleCleanLabel || normalizeOptionLabel(node.textContent || "");
      if (label) node.dataset.peopleCleanLabel = label;
      node.classList.toggle("people-tag-option--all", isAllValue(value) || isAllValue(label));
    });
  };

  const refreshOptionState = (scope) => {
    const selected = dedupeValues(parseValues(scope));
    const selectedSet = new Set(selected.map((value) => normalizeValue(value)));
    const selectedNonAllSet = new Set([...selectedSet].filter((value) => !isAllValue(value)));

    const allValues = collectOptionValues(scope);
    const allValueSet = new Set(allValues.map((value) => normalizeValue(value)));
    const allSelected =
      allValueSet.size > 0 &&
      allValueSet.size === selectedNonAllSet.size &&
      [...allValueSet].every((value) => selectedNonAllSet.has(value));

    optionNodes(scope).forEach((node) => {
      const rawValue = extractOptionValue(node);
      const label = node.dataset.peopleCleanLabel || "";
      const normalized = normalizeValue(rawValue || label);
      const active = isAllValue(normalized) ? allSelected : selectedNonAllSet.has(normalized);
      node.classList.toggle("is-selected", active);
    });
  };

  const updateSummary = (scope, config) => {
    const summary = summaryText(parseSelectionCount(scope), config);
    const summaryTargets = [
      scope.querySelector(".wrap"),
      scope.querySelector(".wrap-inner"),
      scope.querySelector(".choices__inner"),
      scope.querySelector(".multiselect__tags"),
      scope.querySelector(".vs__selected-options"),
      scope.querySelector(".selectize-input"),
    ].filter(Boolean);
    summaryTargets.forEach((target) => {
      target.dataset.summary = summary;
      target.dataset.placeholder = config.placeholder;
    });

    scope.querySelectorAll("input[type='text']").forEach((input) => {
      input.placeholder = summary;
      if (document.activeElement !== input) {
        input.value = "";
      }
    });
  };

  const hideSelectedPills = (scope) => {
    const selectors = [
      ".multiselect__tag",
      ".multiselect__single",
      ".multiselect__tags > *:not(.multiselect__input)",
      ".selectize-control .item",
      ".selectize-control .selectize-input > div",
      ".vs__selected",
      ".vs__selected-options > *:not(input):not(textarea):not(.vs__search)",
      ".vs__selection",
      ".choices__list--multiple .choices__item",
      ".choices__list--single .choices__item",
      ".wrap .token",
      ".token",
      ".token-remove",
    ];
    selectors.forEach((selector) => {
      scope.querySelectorAll(selector).forEach((node) => {
        node.style.display = "none";
        node.setAttribute("aria-hidden", "true");
      });
    });
  };

  const ensureScopedStyles = (scope) => {
    if (scope.querySelector("style[data-people-tag-style]")) return;
    const style = document.createElement("style");
    style.dataset.peopleTagStyle = "1";
    style.textContent = `
      .wrap .token,
      .token,
      .token-remove,
      .multiselect__tag,
      .multiselect__single,
      .multiselect__tags > *:not(.multiselect__input),
      .selectize-control .item,
      .selectize-control .selectize-input > div,
      .vs__selected,
      .vs__selected-options > *:not(input):not(textarea):not(.vs__search),
      .vs__selection,
      .choices__list--multiple .choices__item,
      .choices__list--single .choices__item {
        display: none !important;
      }
      .wrap,
      .wrap-inner,
      .selectize-input,
      .choices__inner,
      .multiselect__tags,
      .vs__selected-options {
        position: relative;
        min-height: 20px;
      }
      .wrap::after,
      .wrap-inner::after,
      .selectize-input::after,
      .choices__inner::after,
      .multiselect__tags::after,
      .vs__selected-options::after {
        content: attr(data-summary);
        position: absolute;
        left: 12px;
        top: 50%;
        transform: translateY(-50%);
        color: #111827;
        font-size: 0.95rem;
        font-weight: 400;
        pointer-events: none;
      }
      .choices.is-open .choices__inner::after,
      .multiselect--active .multiselect__tags::after,
      .vs--open .vs__selected-options::after,
      .wrap:focus-within::after,
      .wrap-inner:focus-within::after {
        color: #9ca3af;
      }
      .choices__input,
      .multiselect__input,
      .vs__search {
        min-height: 20px;
        color: transparent !important;
        caret-color: #2563eb;
      }
      .choices__input::placeholder,
      .multiselect__input::placeholder,
      .vs__search::placeholder {
        color: transparent !important;
      }
      .choices__list--dropdown,
      .multiselect__content-wrapper ul,
      .vs__dropdown-menu,
      .selectize-dropdown-content,
      .wrap .options,
      .wrap-inner .options,
      .options {
        max-height: 320px;
        overflow-y: auto;
        scroll-behavior: auto;
      }
      .people-tag-option,
      .choices__list--dropdown .choices__item--selectable,
      .multiselect__option,
      .vs__dropdown-option,
      .selectize-dropdown .option {
        position: relative;
        display: flex;
        align-items: center;
        gap: 0.65rem;
        min-height: 38px;
        padding: 10px 12px 10px 43px !important;
        border-radius: 6px;
        color: #0f172a !important;
        font-size: 0.95rem !important;
        line-height: 1.35 !important;
        cursor: pointer;
        user-select: none;
        background-color: transparent !important;
        background-image: none !important;
      }
      .people-tag-option::before,
      .choices__list--dropdown .choices__item--selectable::before,
      .multiselect__option::before,
      .vs__dropdown-option::before,
      .selectize-dropdown .option::before {
        content: "";
        position: absolute;
        left: 16px;
        top: 50%;
        transform: translateY(-50%);
        width: 18px;
        height: 18px;
        border-radius: 6px;
        border: 2px solid #3b82f6 !important;
        background: #ffffff !important;
        box-shadow: inset 0 0 0 2px #ffffff !important;
      }
      .people-tag-option.is-selected::before,
      .people-tag-option[aria-selected="true"]::before,
      .choices__list--dropdown .choices__item--selectable.is-selected::before,
      .choices__list--dropdown .choices__item--selectable[aria-selected="true"]::before,
      .multiselect__option--selected::before,
      .vs__dropdown-option--selected::before,
      .vs__dropdown-option[aria-selected="true"]::before,
      .selectize-dropdown .option.selected::before,
      .selectize-dropdown .option[aria-selected="true"]::before {
        background: #3b82f6 !important;
        border-color: #3b82f6 !important;
        box-shadow: inset 0 0 0 3px #ffffff !important;
      }
      .people-tag-option--all {
        font-weight: 700;
      }
      .people-tag-option input[type="checkbox"],
      .people-tag-option input[type="radio"],
      .people-tag-option svg,
      .choices__list--dropdown .choices__item--selectable::after,
      .multiselect__option::after,
      .vs__dropdown-option::after,
      .selectize-dropdown .option::after {
        display: none !important;
      }
    `;
    scope.appendChild(style);
  };

  const bindDropdown = (config) => {
    const dropdownId = String(config?.id || "").trim();
    if (!dropdownId) return;
    const root = ensureRoot();
    if (!root) return;
    const host = root.querySelector(`#${dropdownId}`);
    if (!host || host.dataset.peopleTagDropdownBound === "1") return;
    const scope = host.shadowRoot || host;
    const listScrollPositions = new WeakMap();
    let suppressScrollCapture = false;
    let suppressScrollToken = 0;

    if (!window._peopleTagDropdownScrolls) {
      window._peopleTagDropdownScrolls = new Map();
    }

    const listSelector =
      ".choices__list--dropdown, .selectize-dropdown-content, .multiselect__content-wrapper ul, .vs__dropdown-menu, .wrap .options, .wrap-inner .options, .options";

    const optionLists = () =>
      Array.from(scope.querySelectorAll(listSelector));

    const listForNode = (node) => node?.closest?.(listSelector) || null;

    const parseNumber = (value) => {
      const parsed = Number(value);
      return Number.isNaN(parsed) ? 0 : parsed;
    };

    const getStoredScroll = () => {
      const fromGlobal = window._peopleTagDropdownScrolls.get(dropdownId);
      if (typeof fromGlobal === "number" && !Number.isNaN(fromGlobal)) return fromGlobal;
      return parseNumber(host.dataset.peopleTagScroll || "0");
    };

    const rememberScrollPosition = (list, value) => {
      const fallback = list ? list.scrollTop || 0 : getStoredScroll();
      const next = typeof value === "number" && !Number.isNaN(value) ? value : fallback;
      if (list) {
        listScrollPositions.set(list, next);
      }
      host.dataset.peopleTagScroll = String(next);
      window._peopleTagDropdownScrolls.set(dropdownId, next);
    };

    const setSuppressScrollCapture = (delayMs = 160) => {
      suppressScrollCapture = true;
      suppressScrollToken += 1;
      const token = suppressScrollToken;
      window.setTimeout(() => {
        if (token !== suppressScrollToken) return;
        suppressScrollCapture = false;
      }, delayMs);
    };

    const captureScrollPositions = (lists) =>
      lists.map((list) => {
        if (!list) return getStoredScroll();
        if (!listScrollPositions.has(list)) {
          rememberScrollPosition(list, getStoredScroll());
        }
        const stored = listScrollPositions.get(list);
        return typeof stored === "number" ? stored : getStoredScroll();
      });

    const restoreScrollPositions = (lists, positions) => {
      lists.forEach((list, index) => {
        if (!list) return;
        const fallback = getStoredScroll();
        const value = typeof positions[index] === "number" ? positions[index] : fallback;
        list.scrollTop = value;
        rememberScrollPosition(list, value);
      });
    };

    const bindListScrollListeners = () => {
      optionLists().forEach((list) => {
        if (!list || list.dataset.peopleTagScrollBound === "1") return;
        list.addEventListener(
          "scroll",
          () => {
            if (suppressScrollCapture) return;
            rememberScrollPosition(list, list.scrollTop || 0);
          },
          { passive: true },
        );
        list.dataset.peopleTagScrollBound = "1";
      });
    };

    const bindOptionScrollSnapshot = () => {
      optionNodes(scope).forEach((node) => {
        if (!node || node.dataset.peopleTagSnapshotBound === "1") return;
        const snapshot = () => {
          const list = listForNode(node);
          if (!list) return;
          rememberScrollPosition(list, list.scrollTop || 0);
          setSuppressScrollCapture();
        };
        node.addEventListener("pointerdown", snapshot, { capture: true, passive: true });
        node.addEventListener("mousedown", snapshot, { capture: true, passive: true });
        node.addEventListener("click", snapshot, { capture: true, passive: true });
        node.dataset.peopleTagSnapshotBound = "1";
      });
    };

    ensureScopedStyles(scope);
    let applyScheduled = false;

    const apply = () => {
      const lists = optionLists();
      bindListScrollListeners();
      const scrollPositions = captureScrollPositions(lists);
      setSuppressScrollCapture();
      hideSelectedPills(scope);
      decorateOptions(scope);
      bindOptionScrollSnapshot();
      refreshOptionState(scope);
      updateSummary(scope, config);
      restoreScrollPositions(lists, scrollPositions);
      window.requestAnimationFrame(() => {
        restoreScrollPositions(lists, scrollPositions);
      });
      window.setTimeout(() => {
        restoreScrollPositions(lists, scrollPositions);
      }, 60);
    };

    const scheduleApply = () => {
      if (applyScheduled) return;
      applyScheduled = true;
      window.requestAnimationFrame(() => {
        applyScheduled = false;
        apply();
      });
    };

    const hidden = hiddenInput(scope);
    if (hidden) {
      hidden.addEventListener("input", () => {
        setSuppressScrollCapture();
        scheduleApply();
      });
      hidden.addEventListener("change", () => {
        setSuppressScrollCapture();
        scheduleApply();
      });
    }

    const observer = new MutationObserver(() => {
      scheduleApply();
    });
    observer.observe(scope, { childList: true, subtree: true });

    apply();
    host.dataset.peopleTagDropdownBound = "1";
  };

  const bindCreateTrigger = () => {
    const root = ensureRoot();
    if (!root) return;
    const host = root.querySelector(`#${CREATE_TRIGGER_ID}`) || document.getElementById(CREATE_TRIGGER_ID);
    if (!(host instanceof HTMLElement) || host.dataset.peopleCreateTriggerBound === "1") return;

    const activate = (event) => {
      event.preventDefault();
      event.stopPropagation();
      window.location.assign(CREATE_PAGE_PATH);
    };

    let swallowNextClick = false;
    host.addEventListener("pointerdown", (event) => {
      if ("button" in event && event.button !== 0) return;
      swallowNextClick = true;
      activate(event);
    });
    host.addEventListener("click", (event) => {
      if (swallowNextClick) {
        swallowNextClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      activate(event);
    });
    host.dataset.peopleCreateTriggerBound = "1";

    const nestedButton = host.querySelector("button");
    if (!(nestedButton instanceof HTMLButtonElement) || nestedButton.dataset.peopleCreateTriggerBound === "1") {
      return;
    }
    let swallowNestedClick = false;
    nestedButton.addEventListener("pointerdown", (event) => {
      if ("button" in event && event.button !== 0) return;
      swallowNestedClick = true;
      activate(event);
    });
    nestedButton.addEventListener("click", (event) => {
      if (swallowNestedClick) {
        swallowNestedClick = false;
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      activate(event);
    });
    nestedButton.dataset.peopleCreateTriggerBound = "1";
  };

  const handleVerifiedBadgeActivation = (badge, event) => {
    const root = ensureRoot();
    if (!root || !(badge instanceof HTMLElement)) return;
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    const slug = String(badge.getAttribute("data-slug") || "").trim().toLowerCase();
    if (!slug) return;
    const currentState = String(badge.getAttribute("data-state") || "").trim().toLowerCase() === "true";
    const payload = { slug, nextState: !currentState };
    const textbox = q(root, `#${VERIFY_PAYLOAD_ID} textarea, #${VERIFY_PAYLOAD_ID} input`);
    const trigger = q(root, `#${VERIFY_TRIGGER_ID}`);
    if (!textbox || !trigger) return;
    setTextboxValue(textbox, JSON.stringify(payload));
    trigger.click();
    const nextLabel = !currentState ? "verificada" : "sin verificar";
    showSuccessToast(`Receta marcada como ${nextLabel}.`);
  };

  const bindVerifiedToggle = () => {
    const root = ensureRoot();
    if (!root) return;
    const host = q(root, `#${CARDS_HOST_ID}`) || document.getElementById(CARDS_HOST_ID);
    if (!(host instanceof HTMLElement) || host.dataset.recipeVerifiedBound === "1") return;

    host.addEventListener("click", (event) => {
      const target = event.target;
      const badge = target instanceof Element ? target.closest(".recipe-card__verified") : null;
      if (!(badge instanceof HTMLElement) || !host.contains(badge)) return;
      handleVerifiedBadgeActivation(badge, event);
    });
    host.addEventListener("keydown", (event) => {
      if (!(event instanceof KeyboardEvent)) return;
      if (event.key !== "Enter" && event.key !== " ") return;
      const target = event.target;
      const badge = target instanceof Element ? target.closest(".recipe-card__verified") : null;
      if (!(badge instanceof HTMLElement) || !host.contains(badge)) return;
      handleVerifiedBadgeActivation(badge, event);
    });

    host.dataset.recipeVerifiedBound = "1";
  };

  const bindInfiniteIconLoad = () => {
    const root = ensureRoot();
    if (!root) return;
    const host = q(root, `#${CARDS_HOST_ID}`) || document.getElementById(CARDS_HOST_ID);
    if (!(host instanceof HTMLElement) || host.dataset.recipeIconLoadBound === "1") return;

    let loading = false;
    let loadingResetTimer = 0;
    let frameScheduled = false;
    let lastRequestedRenderedCount = -1;
    let lastSeenRenderedCount = -1;

    const parseIntSafe = (value, fallback = 0) => {
      const parsed = Number.parseInt(String(value ?? ""), 10);
      return Number.isFinite(parsed) ? parsed : fallback;
    };

    const resetLoading = () => {
      loading = false;
      lastRequestedRenderedCount = -1;
      if (loadingResetTimer) {
        window.clearTimeout(loadingResetTimer);
        loadingResetTimer = 0;
      }
    };

    const getLoadMoreTrigger = () => {
      const nextRoot = ensureRoot();
      if (!nextRoot) return null;
      return q(nextRoot, `#${ICON_LOAD_MORE_TRIGGER_ID}`) || document.getElementById(ICON_LOAD_MORE_TRIGGER_ID);
    };

    const maybeLoadMore = () => {
      const iconGrid = host.querySelector(".people-grid[data-view-mode='icon']");
      const sentinel = host.querySelector("[data-recipes-icon-sentinel='1']");
      if (!(iconGrid instanceof HTMLElement) || !(sentinel instanceof HTMLElement)) {
        resetLoading();
        return;
      }

      const renderedCount = parseIntSafe(sentinel.getAttribute("data-rendered-count"), 0);
      const totalCount = parseIntSafe(sentinel.getAttribute("data-total-count"), 0);
      if (renderedCount > lastSeenRenderedCount) {
        lastSeenRenderedCount = renderedCount;
        if (loading && renderedCount > lastRequestedRenderedCount) {
          loading = false;
          if (loadingResetTimer) {
            window.clearTimeout(loadingResetTimer);
            loadingResetTimer = 0;
          }
        }
      }
      if (totalCount > 0 && renderedCount >= totalCount) {
        resetLoading();
        return;
      }
      if (loading && lastRequestedRenderedCount === renderedCount) return;

      const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
      const rect = sentinel.getBoundingClientRect();
      if (rect.top > viewportHeight + 420) return;

      const trigger = getLoadMoreTrigger();
      if (!(trigger instanceof HTMLElement)) return;
      loading = true;
      lastRequestedRenderedCount = renderedCount;
      trigger.click();
      loadingResetTimer = window.setTimeout(() => {
        // Retry only if the DOM did not advance to a new rendered count.
        if (lastSeenRenderedCount <= lastRequestedRenderedCount) {
          loading = false;
        }
      }, 15000);
    };

    const scheduleMaybeLoadMore = () => {
      if (frameScheduled) return;
      frameScheduled = true;
      window.requestAnimationFrame(() => {
        frameScheduled = false;
        maybeLoadMore();
      });
    };

    window.addEventListener("scroll", scheduleMaybeLoadMore, { passive: true });
    window.addEventListener("resize", scheduleMaybeLoadMore, { passive: true });

    const observer = new MutationObserver(() => {
      scheduleMaybeLoadMore();
    });
    observer.observe(host, { childList: true, subtree: true });

    host.dataset.recipeIconLoadBound = "1";
    scheduleMaybeLoadMore();
  };

  const bootstrap = () => {
    maybeShowRecipeQueryToast();
    DROPDOWN_CONFIGS.forEach((config) => bindDropdown(config));
    bindCreateTrigger();
    bindVerifiedToggle();
    bindInfiniteIconLoad();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }

  const rootObserver = new MutationObserver(() => {
    window.requestAnimationFrame(bootstrap);
  });
  rootObserver.observe(document.body, { childList: true, subtree: true });
})();
