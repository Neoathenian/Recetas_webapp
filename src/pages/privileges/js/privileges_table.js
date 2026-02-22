(() => {
  if (typeof window === "undefined") return;
  if (window.__privilegesTableBound) return;
  window.__privilegesTableBound = true;

  const ensureRoot = () => {
    if (typeof window.gradioApp === "function") {
      try {
        const app = window.gradioApp();
        if (app) {
          return app.shadowRoot || app;
        }
      } catch {}
    }
    const el = document.querySelector("gradio-app");
    return el ? el.shadowRoot || el : document;
  };

  const q = (root, sel) => (root ? root.querySelector(sel) : null);

  const DEFAULT_HEADER_OFFSET = 16;
  const FLOATING_ID = "priv-floating-header";
  const EXTRA_OFFSET = 8;

  const floatingState = {
    root: null,
    wrapper: null,
    table: null,
    floating: null,
    floatingTable: null,
    mutationObserver: null,
    resizeObserver: null,
    wrapperScrollHandler: null,
    boundWrapper: null,
    offset: DEFAULT_HEADER_OFFSET,
  };

  const setTextboxValue = (el, value) => {
    if (!el) return;
    el.value = String(value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const handlePrivilegeClick = (btn) => {
    const container = btn.closest("#privileges-table");
    if (!container) return;
    if (btn.classList.contains("is-disabled")) return;
    const privilege = btn.getAttribute("data-privilege") || "";
    const email = (btn.getAttribute("data-email") || "").trim();
    if (!privilege || !email) return;
    const current = (btn.getAttribute("data-state") || "").toLowerCase() === "true";
    const payload = {
      email,
      privilege,
      nextState: !current,
    };
    const root = ensureRoot();
    const textbox = q(root, "#priv-toggle-payload textarea, #priv-toggle-payload input");
    const trigger = q(root, "#priv-toggle-trigger");
    if (!textbox || !trigger) return;
    setTextboxValue(textbox, JSON.stringify(payload));
    trigger.click();
  };

  const handleActiveClick = (btn) => {
    const rowId = btn.getAttribute("data-row-id");
    if (!rowId) return;
    const container = btn.closest("#privileges-table");
    if (!container) return;
    const current = (btn.getAttribute("data-state") || "").toLowerCase() === "true";
    const parsedId = Number(rowId);
    const payload = {
      rowId: Number.isNaN(parsedId) ? rowId : parsedId,
      nextState: !current,
    };
    const root = ensureRoot();
    const textbox = q(root, "#priv-active-payload textarea, #priv-active-payload input");
    const trigger = q(root, "#priv-active-trigger");
    if (!textbox || !trigger) return;
    setTextboxValue(textbox, JSON.stringify(payload));
    trigger.click();
  };

  const handleClick = (event) => {
    const flagBtn = event.target.closest(".priv-flag");
    if (flagBtn) {
      event.preventDefault();
      handlePrivilegeClick(flagBtn);
      return;
    }
    const activeBtn = event.target.closest(".priv-active-toggle");
    if (activeBtn) {
      event.preventDefault();
      handleActiveClick(activeBtn);
    }
  };

  const parseOffset = (element) => {
    if (!element) return DEFAULT_HEADER_OFFSET;
    const value = getComputedStyle(element).getPropertyValue("--priv-table-header-offset");
    const parsed = parseFloat(value);
    return Number.isNaN(parsed) ? DEFAULT_HEADER_OFFSET : parsed;
  };

  const isSidebarHeader = (header) => {
    if (!header) return false;
    if (header.dataset && header.dataset.nav === "sidebar") return true;
    return header.classList && header.classList.contains("hdr-wrap--sidebar");
  };

  const parseHeaderHeight = () => {
    if (typeof document === "undefined") return 0;
    const header = document.querySelector(".hdr-wrap");
    if (!header) return 0;
    if (isSidebarHeader(header)) return 0;
    const rect = header.getBoundingClientRect();
    return rect.height || header.offsetHeight || 0;
  };

  const computeFloatingOffset = (tableEl) => {
    const cssValue = parseOffset(tableEl);
    const headerHeight = parseHeaderHeight();
    if (!headerHeight) {
      return cssValue;
    }
    return Math.max(cssValue, headerHeight + EXTRA_OFFSET);
  };

  const ensureFloatingShell = () => {
    const root = floatingState.root;
    if (!root) return null;
    if (floatingState.floating && floatingState.floatingTable) {
      return floatingState.floating;
    }
    let container =
      (typeof root.getElementById === "function" && root.getElementById(FLOATING_ID)) || null;
    if (!container) {
      container = document.createElement("div");
      container.id = FLOATING_ID;
      container.className = "priv-floating-header";
      const parent = root.body || root;
      parent.appendChild(container);
    }
    let floatingTable = container.querySelector("table");
    if (!floatingTable) {
      floatingTable = document.createElement("table");
      container.appendChild(floatingTable);
    }
    floatingState.floating = container;
    floatingState.floatingTable = floatingTable;
    return container;
  };

  const matchColumnWidths = () => {
    if (!floatingState.table || !floatingState.floatingTable) return;
    const sourceCells = floatingState.table.querySelectorAll("thead th");
    const floatingCells = floatingState.floatingTable.querySelectorAll("thead th");
    floatingCells.forEach((cell, idx) => {
      const source = sourceCells[idx];
      if (!source) return;
      const width = source.getBoundingClientRect().width;
      cell.style.width = `${width}px`;
    });
  };

  const updateFloatingVisibility = () => {
    if (!floatingState.table || !floatingState.wrapper || !floatingState.floating) return;
    const nextOffset = computeFloatingOffset(floatingState.table);
    if (Math.abs(nextOffset - floatingState.offset) > 0.5) {
      floatingState.offset = nextOffset;
    }
    const wrapperRect = floatingState.wrapper.getBoundingClientRect();
    const head = floatingState.table.querySelector("thead");
    const headRect = head ? head.getBoundingClientRect() : { height: 0 };
    const shouldShow =
      wrapperRect.top < floatingState.offset &&
      wrapperRect.bottom > floatingState.offset + headRect.height;
    if (!shouldShow) {
      floatingState.floating.classList.remove("is-visible");
      return;
    }
    const width = wrapperRect.width;
    floatingState.floating.classList.add("is-visible");
    floatingState.floating.style.top = `${floatingState.offset}px`;
    floatingState.floating.style.width = `${width}px`;
    floatingState.floating.style.left = `${wrapperRect.left}px`;
    floatingState.floatingTable.style.transform = `translateX(-${floatingState.wrapper.scrollLeft}px)`;
  };

  const ensureWrapperListener = () => {
    if (!floatingState.wrapper) return;
    if (floatingState.boundWrapper === floatingState.wrapper && floatingState.wrapperScrollHandler) {
      return;
    }
    if (
      floatingState.boundWrapper &&
      floatingState.wrapperScrollHandler &&
      floatingState.boundWrapper !== floatingState.wrapper
    ) {
      floatingState.boundWrapper.removeEventListener(
        "scroll",
        floatingState.wrapperScrollHandler,
        { passive: true }
      );
    }
    floatingState.boundWrapper = floatingState.wrapper;
    floatingState.wrapperScrollHandler = () => window.requestAnimationFrame(updateFloatingVisibility);
    floatingState.wrapper.addEventListener("scroll", floatingState.wrapperScrollHandler, { passive: true });
  };

  const rebuildFloatingHeader = () => {
    const root = ensureRoot();
    floatingState.root = root;
    const wrapper = q(root, ".priv-table-wrapper");
    const table = wrapper ? wrapper.querySelector("table") : null;
    if (!wrapper || !table) {
      if (floatingState.floating) {
        floatingState.floating.classList.remove("is-visible");
      }
      return;
    }
    floatingState.wrapper = wrapper;
    floatingState.table = table;
    floatingState.offset = computeFloatingOffset(table);
    ensureFloatingShell();
    const sourceHead = table.querySelector("thead");
    if (!sourceHead || !floatingState.floatingTable) return;
    floatingState.floatingTable.innerHTML = "";
    floatingState.floatingTable.appendChild(sourceHead.cloneNode(true));
    matchColumnWidths();
    updateFloatingVisibility();
    ensureWrapperListener();
    if (typeof ResizeObserver === "function") {
      if (floatingState.resizeObserver) {
        floatingState.resizeObserver.disconnect();
      }
      floatingState.resizeObserver = new ResizeObserver(() => {
        matchColumnWidths();
        updateFloatingVisibility();
      });
      floatingState.resizeObserver.observe(wrapper);
    }
  };

  const attachGlobalListeners = () => {
    window.addEventListener(
      "scroll",
      () => window.requestAnimationFrame(updateFloatingVisibility),
      { passive: true }
    );
    window.addEventListener("resize", () => {
      matchColumnWidths();
      updateFloatingVisibility();
    });
  };

  const startFloatingObserver = () => {
    const root = ensureRoot();
    if (!root) return;
    const target = q(root, "#privileges-table");
    if (!target) {
      setTimeout(startFloatingObserver, 300);
      return;
    }
    if (floatingState.mutationObserver) {
      floatingState.mutationObserver.disconnect();
    }
    floatingState.mutationObserver = new MutationObserver(() => rebuildFloatingHeader());
    floatingState.mutationObserver.observe(target, { childList: true, subtree: true });
    rebuildFloatingHeader();
  };

  const mount = () => {
    const root = ensureRoot();
    if (!root) return;
    root.addEventListener("click", handleClick);
    attachGlobalListeners();
    startFloatingObserver();
  };

  if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(mount, 0);
  } else {
    document.addEventListener("DOMContentLoaded", () => mount(), { once: true });
  }
})();
