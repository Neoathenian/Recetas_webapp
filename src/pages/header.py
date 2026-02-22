from __future__ import annotations
import html
import logging
import time
from typing import Any, Optional
from starlette.requests import Request as StarletteRequest
from src.login_logic import get_user
from src.css.utils import load_css
from src.privileges import page_key_for_route, resolve_nav_links

timing_logger = logging.getLogger("uvicorn.error")

LOGO_URL = "/images/The-list-logo2.png"
FAVICON_URL = "/images/The-list-logo2.png"
_SECTION_ORDER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Recipes", ("the-list", "the-list-review")),
    ("Management", ("privileges",)),
    ("Administration", ("admin",)),
)
_LABEL_OVERRIDES = {
    "the-list": "Recetas",
    "the-list-review": "Review",
    "admin": "Administration",
}
_DEFAULT_SECTION = "Other"


def _log_timing(event_name: str, start: float, **fields: object) -> None:
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if fields:
        field_text = " ".join(f"{key}={value}" for key, value in fields.items())
        timing_logger.info("header.timing event=%s ms=%.2f %s", event_name, elapsed_ms, field_text)
        return
    timing_logger.info("header.timing event=%s ms=%.2f", event_name, elapsed_ms)


def _short_label(label: str) -> str:
    cleaned = (label or "").replace("/", " ").replace("-", " ")
    parts = [part for part in cleaned.split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][0]}{parts[1][0]}".upper()

_ICON_PATHS: dict[str, str] = {
    "the-list": (
        '<path d="M4 6h16"/><path d="M7 6v11a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V6"/>'
        '<path d="M9 10h6"/><path d="M9 13h6"/><path d="M10 3h4"/>'
    ),
    "the-list-review": (
        '<rect x="5" y="4" width="14" height="16" rx="2"/>'
        '<path d="M9 2h6v4H9z"/><path d="M9 12l2 2 4-4"/>'
    ),
    "privileges": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "admin": (
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'
    ),
}


def _nav_icon_markup(key: str, label: str) -> str:
    path = _ICON_PATHS.get(key)
    if path:
        return (
            '<span class="sidebar-link-icon" aria-hidden="true">'
            f'<svg viewBox="0 0 24 24" focusable="false" aria-hidden="true">{path}</svg>'
            "</span>"
        )
    short_label = html.escape(_short_label(label))
    return f'<span class="sidebar-link-icon sidebar-link-icon--text" aria-hidden="true">{short_label}</span>'

FAVICON_SCRIPT = """
<script>
(function() {
  const url = new URL("__LOGO_URL__", window.location.origin).toString();

  const applyIcon = () => {
    const head = document.head || document.getElementsByTagName('head')[0];
    if (!head) return;

    const links = head.querySelectorAll('link[rel~="icon"], link[rel="shortcut icon"]');
    if (links.length) {
      links.forEach((link) => {
        link.setAttribute('href', url);
        link.setAttribute('type', 'image/png');
      });
    } else {
      const link = document.createElement('link');
      link.setAttribute('id', 'the-list-favicon');
      link.setAttribute('rel', 'icon');
      link.setAttribute('type', 'image/png');
      link.setAttribute('href', url);
      head.appendChild(link);
    }
  };

  const applyTitle = () => {
    document.title = 'Recetas Control Center';
  };

  applyIcon();
  applyTitle();

  const observer = new MutationObserver(() => {
    applyIcon();
    applyTitle();
  });
  observer.observe(document.head || document.documentElement, { childList: true, subtree: true });
})();
</script>
""".strip().replace("__LOGO_URL__", FAVICON_URL)

FORCE_LIGHT_MODE_SCRIPT = """
<script>
(function() {
  const BG_COLOR = "#ffffff";
  const ENABLE_BACKGROUND_WATERMARKS = false;

  const applyBackground = () => {
    const rootTargets = [document.documentElement, document.body];
    rootTargets.forEach((el) => {
      if (!el) return;
      el.style.setProperty("background-color", BG_COLOR, "important");
    });

    document.querySelectorAll(
      "gradio-app, .gradio-container, .gradio-container > .main, .gradio-container > .main > .wrap, .gradio-container > .main > .wrap > .contain, .app"
    ).forEach((el) => {
      if (!el) return;
      el.style.setProperty("background", "transparent", "important");
    });
  };

  const ensureWatermarks = () => {
    if (!ENABLE_BACKGROUND_WATERMARKS) {
      document.querySelectorAll(".app-watermark").forEach((el) => el.remove());
      return;
    }
    if (!document.body) return;
    if (document.querySelector(".app-watermark")) return;
    const left = document.createElement("div");
    left.className = "app-watermark app-watermark--left";
    const right = document.createElement("div");
    right.className = "app-watermark app-watermark--right";
    document.body.prepend(right);
    document.body.prepend(left);
  };

  const toPx = (value) => {
    const raw = (value || "").toString().trim();
    if (!raw) return 0;
    if (raw.endsWith("vh")) {
      return (parseFloat(raw) / 100) * window.innerHeight;
    }
    if (raw.endsWith("rem")) {
      const base = parseFloat(getComputedStyle(document.documentElement).fontSize || "16");
      return parseFloat(raw) * base;
    }
    if (raw.endsWith("px")) {
      return parseFloat(raw);
    }
    const asNum = parseFloat(raw);
    return Number.isFinite(asNum) ? asNum : 0;
  };

  const watermarkRoots = () => {
    const roots = [];
    const push = (root) => {
      if (!root || roots.includes(root)) return;
      roots.push(root);
    };
    push(document);
    if (typeof window.gradioApp === "function") {
      try {
        const app = window.gradioApp();
        if (app) push(app.shadowRoot || app);
      } catch (err) {}
    }
    document.querySelectorAll("gradio-app").forEach((el) => {
      push(el.shadowRoot || el);
    });
    return roots;
  };

  const watermarkCandidates = () => {
    const selector = [
      "#task-tracker-shell",
      ".gradio-container > .main > .wrap > .contain",
      ".gradio-container > .main",
      ".gradio-container",
      ".main",
    ].join(", ");
    const nodes = [];
    for (const root of watermarkRoots()) {
      if (!root || !root.querySelectorAll) continue;
      root.querySelectorAll(selector).forEach((node) => {
        if (node && !node.classList?.contains("app-watermark")) {
          nodes.push(node);
        }
      });
    }
    return nodes;
  };

  const resizeObserver = typeof ResizeObserver !== "undefined"
    ? new ResizeObserver(() => {
        syncWatermarkHeight();
      })
    : null;
  const observedNodes = new WeakSet();
  const observeNode = (node) => {
    if (!resizeObserver || !node || observedNodes.has(node)) return;
    observedNodes.add(node);
    try {
      resizeObserver.observe(node);
    } catch (err) {}
  };

  const syncWatermarkHeight = () => {
    if (!ENABLE_BACKGROUND_WATERMARKS) return;
    const candidates = watermarkCandidates();
    let contentHeight = 0;
    for (const node of candidates) {
      observeNode(node);
      const nodeHeight = node.scrollHeight || node.getBoundingClientRect().height || 0;
      if (nodeHeight > contentHeight) contentHeight = nodeHeight;
    }
    const height = Math.max(window.innerHeight, contentHeight);
    const styles = getComputedStyle(document.documentElement);
    const leftOffset = toPx(styles.getPropertyValue("--app-bg-logo-offset"));
    const gap = toPx(styles.getPropertyValue("--app-bg-logo-gap"));
    const rightNudge = toPx(styles.getPropertyValue("--app-bg-logo-right-nudge"));
    const rightOffset = leftOffset + gap + rightNudge;

    document.querySelectorAll(".app-watermark").forEach((el) => {
      if (el.classList.contains("app-watermark--right")) {
        el.style.top = `${rightOffset}px`;
        el.style.height = `${Math.max(0, height - rightOffset)}px`;
      } else if (el.classList.contains("app-watermark--left")) {
        el.style.top = `${leftOffset}px`;
        el.style.height = `${Math.max(0, height - leftOffset)}px`;
      } else {
        el.style.top = "0px";
        el.style.height = `${height}px`;
      }
    });
  };

  const applyLightMode = () => {
    const root = document.documentElement;
    const body = document.body;
    const elements = [root, body];
    const setLight = (el) => {
      if (!el) return;
      if (el.getAttribute("data-theme") !== "light") {
        el.setAttribute("data-theme", "light");
      }
      if (el.classList.contains("dark")) {
        el.classList.remove("dark");
      }
      if (el.style.colorScheme !== "light") {
        el.style.colorScheme = "light";
      }
    };

    elements.forEach(setLight);
    document.querySelectorAll("gradio-app, .gradio-container").forEach(setLight);

    try {
      localStorage.setItem("theme", "light");
    } catch (err) {}
  };

  applyLightMode();
  applyBackground();
  ensureWatermarks();
  syncWatermarkHeight();

  let initialSyncs = 0;
  const scheduleInitialSync = () => {
    syncWatermarkHeight();
    initialSyncs += 1;
    if (initialSyncs < 12) {
      setTimeout(scheduleInitialSync, 200);
    }
  };
  scheduleInitialSync();

  const observer = new MutationObserver(() => {
    applyLightMode();
    applyBackground();
    ensureWatermarks();
    syncWatermarkHeight();
  });
  observer.observe(document.documentElement, {
    attributes: true,
    childList: true,
    subtree: true,
    attributeFilter: ["class", "data-theme"],
  });

  window.addEventListener("resize", syncWatermarkHeight);
})();
</script>
""".strip()

SIDEBAR_COLLAPSE_SCRIPT = """
<script>
(function() {
  const LOG_PREFIX = "[sidebar][collapse]";
  const log = (...args) => {
    try { console.log(LOG_PREFIX, ...args); } catch (err) {}
  };
  const warn = (...args) => {
    try { console.warn(LOG_PREFIX, ...args); } catch (err) {}
  };
  const key = "sidebarCollapsed";
  const findRoots = () => {
    const roots = [];
    const push = (root, label) => {
      if (!root || roots.some((entry) => entry.root === root)) return;
      roots.push({ root, label });
    };
    push(document, "document");
    if (typeof window.gradioApp === "function") {
      try {
        const app = window.gradioApp();
        if (app) push(app.shadowRoot || app, "gradioApp");
      } catch (err) {}
    }
    document.querySelectorAll("gradio-app").forEach((el, idx) => {
      push(el.shadowRoot || el, `gradio-app:${idx}`);
    });
    return roots;
  };
  const findById = (root, id) => {
    if (!root) return null;
    if (typeof root.getElementById === "function") return root.getElementById(id);
    return root.querySelector ? root.querySelector(`#${id}`) : null;
  };
  const locate = () => {
    const roots = findRoots();
    for (const entry of roots) {
      const checkbox = findById(entry.root, "sidebar-collapse");
      const sidebar = findById(entry.root, "sidebar");
      const label =
        (entry.root && entry.root.querySelector && entry.root.querySelector('label[for="sidebar-collapse"]')) ||
        null;
      if (checkbox && sidebar) {
        return { ...entry, checkbox, sidebar, label };
      }
    }
    return null;
  };
  const bind = () => {
    const found = locate();
    log("bind attempt", {
      found: !!found,
      readyState: document.readyState,
    });
    if (!found) return false;
    const { root, label, checkbox, sidebar } = found;
    const rootEl = document.documentElement;
    const body = document.body;
    const host = (root && root.host) || document.querySelector("gradio-app");
    const readVar = (el, name, fallback) => {
      if (!el || !window.getComputedStyle) return fallback;
      const value = getComputedStyle(el).getPropertyValue(name).trim();
      return value || fallback;
    };
    const expandedWidth = readVar(sidebar, "--sidebar-expanded-width", "18rem");
    const collapsedWidth = readVar(sidebar, "--sidebar-collapsed-width", "4.5rem");
    const setVar = (el, name, value) => {
      if (!el || !el.style) return;
      el.style.setProperty(name, value);
    };
    const applyClass = () => {
      const enabled = checkbox.checked;
      log("applyClass", {
        enabled,
        expandedWidth,
        collapsedWidth,
        host: host ? host.tagName : null,
      });
      if (rootEl) rootEl.classList.toggle("sidebar-collapsed", enabled);
      if (body) body.classList.toggle("sidebar-collapsed", enabled);
      if (host) host.classList.toggle("sidebar-collapsed", enabled);
      sidebar.classList.toggle("is-collapsed", enabled);
      const width = enabled ? collapsedWidth : expandedWidth;
      setVar(rootEl, "--sidebar-width", width);
      setVar(body, "--sidebar-width", width);
      setVar(host, "--sidebar-width", width);
    };
    if (checkbox.dataset.sidebarBound !== "1") {
      checkbox.dataset.sidebarBound = "1";
      checkbox.addEventListener("change", () => {
        log("checkbox change", { checked: checkbox.checked });
        applyClass();
        try {
          localStorage.setItem(key, checkbox.checked ? "1" : "0");
        } catch (err) {}
      });
      if (label) {
        label.addEventListener("click", () => {
          log("label click", { checkedBefore: checkbox.checked });
        });
      } else {
        warn("label for sidebar-collapse not found");
      }
      log("listeners bound", {
        root: found.label,
        labelFound: !!label,
      });
    }
    try {
      const stored = localStorage.getItem(key);
      if (stored === "1") {
        checkbox.checked = true;
      }
    } catch (err) {}
    applyClass();
    return true;
  };
  const schedule = () => {
    let attempts = 0;
    log("script loaded", { readyState: document.readyState });
    const tick = () => {
      if (bind()) return;
      attempts += 1;
      if (attempts < 60) {
        requestAnimationFrame(tick);
      } else {
        warn("bind gave up", { attempts });
      }
    };
    tick();
    const observer = new MutationObserver(() => {
      if (bind()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedule);
  } else {
    schedule();
  }
})();
</script>
""".strip()

def with_light_mode_head(head: Optional[str]) -> str:
    if head and head.strip():
        return f"{head}\n{FORCE_LIGHT_MODE_SCRIPT}\n{SIDEBAR_COLLAPSE_SCRIPT}"
    return f"{FORCE_LIGHT_MODE_SCRIPT}\n{SIDEBAR_COLLAPSE_SCRIPT}"

def _header_html(user: Optional[dict], path: str, request: Any) -> str:
    total_start = time.perf_counter()
    step_start = time.perf_counter()
    css = load_css("header.css")
    _log_timing("header_html.load_css", step_start, css_bytes=len(css))
    step_start = time.perf_counter()
    css_block = f"<style>\n{css}\n</style>\n{FAVICON_SCRIPT}"
    active_key = page_key_for_route(path or "")
    _log_timing("header_html.resolve_active_route", step_start, active_key=active_key)

    if user:
        name  = html.escape(user.get("name") or user.get("email") or "Signed in")
        email = html.escape(user.get("email") or "")
        photo = (user.get("picture") or "").strip()
        initial = html.escape((user.get("name") or user.get("email") or "?")[0].upper())

        avatar = (
            f'<img class="avatar-img" src="{html.escape(photo)}" alt="{name}" referrerpolicy="no-referrer" loading="lazy" />'
            if photo else f'<div class="avatar-circle">{initial}</div>'
        )

        account_html = f"""
<details class="account-menu">
  <summary class="account-btn" aria-label="Cuenta de {name}">
    {avatar}
    <div class="account-meta">
      <div class="account-name">{name}</div>
      <div class="account-email">{email}</div>
    </div>
    <span class="account-caret" aria-hidden="true"></span>
  </summary>
  <div class="account-dropdown" role="menu">
    <a href="/logout" role="menuitem" class="menu-link">Sign out</a>
  </div>
</details>""".strip()
    else:
        #This is literally the svg code for the google button
        google_btn = """
            <a href="/auth/google" class="google-btn-pill" aria-label="Sign in with Google">
              <span class="google-icon-wrapper">
                <svg class="google-icon" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" role="img" aria-hidden="true">
                  <path fill="#4285F4" d="M17.64 9.2045c0-.638-.0573-1.2518-.1636-1.836H9v3.4763h4.844c-.208 1.125-.842 2.0777-1.795 2.7156v2.258h2.896c1.696-1.561 2.665-3.86 2.665-6.6139z"/>
                  <path fill="#34A853" d="M9 18c2.43 0 4.467-.806 5.956-2.186l-2.896-2.258c-.805.54-1.836.863-3.06.863-2.351 0-4.341-1.588-5.05-3.72H.945v2.337C2.423 15.98 5.481 18 9 18z"/>
                  <path fill="#FBBC05" d="M3.95 10.699c-.18-.54-.281-1.119-.281-1.699s.101-1.159.281-1.699V4.965H.945a9.002 9.002 0 000 8.07l3.005-2.336z"/>
                  <path fill="#EA4335" d="M9 3.579c1.32 0 2.508.451 3.44 1.337l2.582-2.583C13.462.917 11.425 0 9 0 5.481 0 2.423 2.02.945 4.965l3.005 2.336C4.659 5.167 6.649 3.579 9 3.579z"/>
                </svg>
              </span>
              <span class="btn-text">Sign in with Google</span>
            </a>
            """.strip()
        account_html = google_btn

    # Left-side: site logo and navigation links
    logo_html = (
        '<a href="/" class="site-logo" aria-label="Home">'
        f'<img src="{LOGO_URL}" class="logo-img" alt="Recetas" />'
        '<span class="logo-text">Recetas</span>'
        '</a>'
    )

    step_start = time.perf_counter()
    user_privileges = user.get("privileges") if isinstance(user, dict) else None
    nav_links = resolve_nav_links(user_privileges)
    _log_timing("header_html.resolve_nav_links", step_start, nav_links=len(nav_links))
    link_by_key = {link.key: link for link in nav_links}
    grouped_sections: list[tuple[str, list[str]]] = []
    used_keys: set[str] = set()
    for section_label, keys in _SECTION_ORDER:
        items: list[str] = []
        for key in keys:
            link = link_by_key.get(key)
            if not link:
                continue
            used_keys.add(key)
            label = _LABEL_OVERRIDES.get(link.key, link.label)
            is_active = link.key == active_key
            active_class = " is-active" if is_active else ""
            aria_current = ' aria-current="page"' if is_active else ""
            icon_markup = _nav_icon_markup(link.key, label)
            items.append(
                f'<a href="{html.escape(link.path)}" class="{html.escape(link.css_class)} sidebar-link{active_class}"'
                f'{aria_current} title="{html.escape(label)}">'
                f'{icon_markup}<span class="sidebar-link-text">{html.escape(label)}</span></a>'
            )
        if items:
            grouped_sections.append((section_label, items))

    remaining_links: list[str] = []
    for link in nav_links:
        if link.key in used_keys:
            continue
        label = _LABEL_OVERRIDES.get(link.key, link.label)
        is_active = link.key == active_key
        active_class = " is-active" if is_active else ""
        aria_current = ' aria-current="page"' if is_active else ""
        icon_markup = _nav_icon_markup(link.key, label)
        remaining_links.append(
            f'<a href="{html.escape(link.path)}" class="{html.escape(link.css_class)} sidebar-link{active_class}"'
            f'{aria_current} title="{html.escape(label)}">'
            f'{icon_markup}<span class="sidebar-link-text">{html.escape(label)}</span></a>'
        )
    if remaining_links:
        grouped_sections.append((_DEFAULT_SECTION, remaining_links))

    section_markup = []
    for section_label, items in grouped_sections:
        section_markup.append(
            f"""
<details class="nav-section" open>
  <summary class="nav-section-title">{html.escape(section_label)}</summary>
  <div class="nav-section-links">
    {' '.join(items)}
  </div>
</details>
""".strip()
        )
    nav_markup = "\n".join(section_markup)

    #in the <div><strong></strong> we could put some cool text or maybe a logo
    html_value = f"""{css_block}
<input type="checkbox" id="sidebar-toggle" class="sidebar-toggle-input" />
<input type="checkbox" id="sidebar-collapse" class="sidebar-collapse-input" />
<label for="sidebar-toggle" class="sidebar-toggle-btn" aria-label="Open menu">
  <span class="sidebar-toggle-icon" aria-hidden="true"></span>
</label>
<div class="hdr-wrap hdr-wrap--sidebar" data-nav="sidebar" id="sidebar">
  <div class="hdr">
    <div class="sidebar-top">
      {logo_html}
      <div class="sidebar-top-actions">
        <label for="sidebar-collapse" class="sidebar-collapse-btn" aria-label="Toggle sidebar" title="Toggle sidebar">
          <span class="collapse-icon" aria-hidden="true"></span>
        </label>
        <label for="sidebar-toggle" class="sidebar-close-btn" aria-label="Close menu">
          <span aria-hidden="true"></span>
        </label>
      </div>
    </div>
    <nav class="sidebar-nav" aria-label="Main navigation">
      {nav_markup}
    </nav>
    <div class="sidebar-footer">
      {account_html}
    </div>
  </div>
</div>
<label for="sidebar-toggle" class="sidebar-scrim" aria-hidden="true"></label>
<div class="hdr-spacer"></div>
"""
    _log_timing("header_html.total", total_start, html_bytes=len(html_value), user_present=bool(user))
    return html_value

def render_header(path: str = "/", request: Any = None, *args, **kwargs) -> str:
    total_start = time.perf_counter()
    if "0" in kwargs and isinstance(kwargs["0"], str):
        path = kwargs["0"]
    if hasattr(path, "request") or isinstance(path, StarletteRequest):
        request, path = path, "/"
    step_start = time.perf_counter()
    # Keep sidebar links aligned with current privileges (e.g., recent role changes).
    user = get_user(
        request,
        refresh_privileges=True,
        force_privileges_refresh=True,
    )
    _log_timing("render_header.get_user", step_start, has_user=bool(user))
    step_start = time.perf_counter()
    header_html = _header_html(user, path or "/", request)
    _log_timing("render_header.build_html", step_start, html_bytes=len(header_html))
    _log_timing("render_header.total", total_start, path=path or "/", has_user=bool(user))
    return header_html
