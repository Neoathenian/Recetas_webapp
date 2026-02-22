(function () {
  const ACTION_INPUT_ID = "the-list-review-change-action";
  const APPLY_BUTTON_ID = "the-list-review-apply-change-btn";
  const MENU_ID = "the-list-review-change-menu";
  const TRACKED_PANELS = new Set([
    "the-list-admin-compiled-base",
    "the-list-admin-compiled-current",
    "the-list-admin-compiled-proposed",
  ]);
  let activeChangeId = 0;

  const setNativeValue = (element, value) => {
    if (!element) return false;
    const proto = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
    if (!descriptor || typeof descriptor.set !== "function") return false;
    descriptor.set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  const getActionInput = () => {
    const root = document.getElementById(ACTION_INPUT_ID);
    if (!root) return null;
    return root.querySelector("textarea, input");
  };

  const getApplyButton = () =>
    document.querySelector(`#${APPLY_BUTTON_ID} button, button#${APPLY_BUTTON_ID}, #${APPLY_BUTTON_ID}`);

  const hideMenu = () => {
    const menu = document.getElementById(MENU_ID);
    if (!menu) return;
    menu.classList.remove("is-open");
  };

  const ensureMenu = () => {
    let menu = document.getElementById(MENU_ID);
    if (menu) return menu;

    menu = document.createElement("div");
    menu.id = MENU_ID;
    menu.className = "the-list-review-change-menu";
    menu.innerHTML = [
      "<div class='the-list-review-change-menu__title'>Select source for this change</div>",
      "<button type='button' class='the-list-review-change-menu__btn' data-review-source='base'>Select base</button>",
      "<button type='button' class='the-list-review-change-menu__btn' data-review-source='current'>Select current (compiled)</button>",
      "<button type='button' class='the-list-review-change-menu__btn' data-review-source='proposed'>Select proposed</button>",
    ].join("");
    document.body.appendChild(menu);
    return menu;
  };

  const openMenu = (x, y) => {
    const menu = ensureMenu();
    menu.style.left = `${Math.max(8, x)}px`;
    menu.style.top = `${Math.max(8, y)}px`;
    menu.classList.add("is-open");
  };

  const resolveTrackedPanel = (node) => {
    if (!node) return null;
    const panel = node.closest(
      "#the-list-admin-compiled-base, #the-list-admin-compiled-current, #the-list-admin-compiled-proposed",
    );
    if (!panel) return null;
    if (!TRACKED_PANELS.has(panel.id)) return null;
    return panel;
  };

  const applySourceSelection = (source) => {
    if (!activeChangeId || !source) return;
    const input = getActionInput();
    const button = getApplyButton();
    if (!input || !button) return;
    const payload = JSON.stringify({
      change_id: activeChangeId,
      source: String(source),
    });
    if (!setNativeValue(input, payload)) return;
    button.click();
  };

  document.addEventListener("click", (event) => {
    const menuButton = event.target.closest(`#${MENU_ID} [data-review-source]`);
    if (menuButton) {
      event.preventDefault();
      const source = String(menuButton.getAttribute("data-review-source") || "").trim().toLowerCase();
      hideMenu();
      applySourceSelection(source);
      return;
    }

    if (event.target.closest(`#${MENU_ID}`)) return;

    const changeNode = event.target.closest("[data-review-change-id]");
    const panel = resolveTrackedPanel(changeNode);
    if (!changeNode || !panel) {
      hideMenu();
      return;
    }

    const changeId = Number.parseInt(changeNode.getAttribute("data-review-change-id") || "", 10);
    if (!Number.isInteger(changeId) || changeId <= 0) {
      hideMenu();
      return;
    }

    activeChangeId = changeId;
    const mouseEvent = event;
    openMenu(mouseEvent.clientX + 8, mouseEvent.clientY + 8);
    event.preventDefault();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideMenu();
  });
  window.addEventListener("scroll", hideMenu, true);
  window.addEventListener("resize", hideMenu);
})();
