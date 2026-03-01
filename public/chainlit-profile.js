(() => {
  try {
    document.documentElement.setAttribute("data-cl-profile-js", "v10");
  } catch (_) {
    // no-op
  }
  const ROOT_ID = "cl-profile-root";
  const FLYOUT_ID = "cl-profile-flyout";
  const BTN_ID = "cl-profile-button";
  const NAME_ID = "cl-profile-name";
  const HANDLE_ID = "cl-profile-handle";
  const INITIALS_ID = "cl-profile-initials";
  const FLY_NAME_ID = "cl-flyout-name";
  const FLY_HANDLE_ID = "cl-flyout-handle";
  const FLY_INITIALS_ID = "cl-flyout-initials";

  let profile = {
    display_name: "User",
    subtitle: "@account",
    initials: "U",
    is_authenticated: false,
  };
  let sidebarCollapsedState = false;

  function destroyRoot() {
    const root = document.getElementById(ROOT_ID);
    if (root) root.remove();
  }

  function isLoginScreen() {
    const passwordInput = document.querySelector("input[type='password']");
    const signInButton = Array.from(document.querySelectorAll("button")).find((b) =>
      /sign in/i.test((b.textContent || "").trim())
    );
    return Boolean(passwordInput && signInButton);
  }

  function closeFlyout() {
    const flyout = document.getElementById(FLYOUT_ID);
    if (flyout) flyout.classList.remove("open");
  }

  async function logout() {
    destroyRoot();
    try {
      await fetch("/logout", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
      });
    } catch (_) {
      // no-op
    } finally {
      window.location.href = "/";
    }
  }

  function toggleFlyout() {
    const flyout = document.getElementById(FLYOUT_ID);
    if (!flyout) return;
    flyout.classList.toggle("open");
  }

  function getSidebarHost() {
    const selectors = [
      "[data-sidebar='sidebar']",
      "[data-testid='chat-sidebar']",
      "[data-testid='sidebar']",
      "nav[aria-label*='history' i]",
      "aside",
      "[class*='chat-sidebar']",
      "[class*='ChatSidebar']",
      "[class*='sidebar']",
      "[class*='Sidebar']",
    ];

    const candidates = [];
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!(el instanceof HTMLElement)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < 140 || rect.height < 260) continue;
        // Must be on the left side (chat-history sidebar), not right settings panel.
        if (rect.left > window.innerWidth * 0.35) continue;

        const text = (el.textContent || "").toLowerCase();
        const hasHistoryHints =
          text.includes("new chat") ||
          text.includes("search") ||
          text.includes("history") ||
          el.getAttribute("data-sidebar") === "sidebar" ||
          el.querySelector("[data-testid='new-chat-button']") ||
          el.querySelector("button[aria-label*='new chat' i]");
        if (!hasHistoryHints) continue;

        candidates.push({ el, rect });
      }
    }

    if (!candidates.length) return null;
    candidates.sort((a, b) => a.rect.left - b.rect.left || b.rect.height - a.rect.height);
    return candidates[0].el;
  }

  function ensurePlacement() {
    const root = document.getElementById(ROOT_ID);
    if (!(root instanceof HTMLElement)) return;

    const sidebar = getSidebarHost();
    if (sidebar instanceof HTMLElement) {
      if (root.parentElement !== sidebar) {
        sidebar.appendChild(root);
      }
      root.classList.add("cl-profile-in-sidebar");
      root.style.width = "";
      root.style.maxWidth = "";
      return;
    }

    if (root.parentElement !== document.body) {
      document.body.appendChild(root);
    }
    root.classList.remove("cl-profile-in-sidebar");
    root.style.width = "";
    root.style.maxWidth = "";
  }

  function isSidebarCollapsed() {
    return Boolean(sidebarCollapsedState);
  }

  function updateCollapsedMode() {
    const root = document.getElementById(ROOT_ID);
    if (!(root instanceof HTMLElement)) return;
    const collapsed = isSidebarCollapsed();
    root.classList.toggle("cl-collapsed", collapsed);
    if (collapsed) closeFlyout();
  }

  function getSidebarToggleModeFromElement(el) {
    if (!(el instanceof HTMLElement)) return null;
    const label = (
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.textContent ||
      ""
    )
      .trim()
      .toLowerCase();
    if (!label) return null;
    if (label.includes("close sidebar")) return "close";
    if (label.includes("open sidebar")) return "open";
    return null;
  }

  function findClosestSidebarToggle(target) {
    if (!(target instanceof Element)) return null;
    const chain = [target, ...(target.closest("button, [role='button']") ? [target.closest("button, [role='button']")] : [])];
    for (const node of chain) {
      if (!(node instanceof HTMLElement)) continue;
      const mode = getSidebarToggleModeFromElement(node);
      if (mode) return mode;
    }

    // Fallback: detect the left-top sidebar toggle even when label text isn't exposed.
    const btn = target.closest("button, [role='button']");
    if (btn instanceof HTMLElement) {
      const rect = btn.getBoundingClientRect();
      const nearLeftTop = rect.left < 90 && rect.top < 90 && rect.width <= 56 && rect.height <= 56;
      if (nearLeftTop) {
        return isSidebarCollapsed() ? "open" : "close";
      }
    }
    return null;
  }

  function syncCollapsedStateFromDom() {
    const closeBtn = document.querySelector("button[aria-label*='close sidebar' i]");
    if (closeBtn instanceof HTMLElement) {
      sidebarCollapsedState = false;
      return;
    }
    const openBtn = document.querySelector("button[aria-label*='open sidebar' i]");
    if (openBtn instanceof HTMLElement) {
      sidebarCollapsedState = true;
      return;
    }

    const sidebar = getSidebarHost();
    if (sidebar instanceof HTMLElement) {
      const rect = sidebar.getBoundingClientRect();
      sidebarCollapsedState = rect.width < 120 || rect.height < 220;
      return;
    }
    // Keep last known state if DOM markers are temporarily unavailable.
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value || "";
  }

  function applyProfile(nextProfile) {
    if (nextProfile && typeof nextProfile === "object") {
      profile = {
        display_name: nextProfile.display_name || profile.display_name,
        subtitle: nextProfile.subtitle || profile.subtitle,
        initials: nextProfile.initials || profile.initials,
        is_authenticated:
          typeof nextProfile.is_authenticated === "boolean"
            ? nextProfile.is_authenticated
            : profile.is_authenticated,
      };
    }

    if (!profile.is_authenticated || isLoginScreen()) {
      destroyRoot();
      return;
    }

    createRoot();
    setText(NAME_ID, profile.display_name);
    setText(HANDLE_ID, profile.subtitle);
    setText(INITIALS_ID, profile.initials);
    setText(FLY_NAME_ID, profile.display_name);
    setText(FLY_HANDLE_ID, profile.subtitle);
    setText(FLY_INITIALS_ID, profile.initials);
  }

  function createRoot() {
    if (document.getElementById(ROOT_ID)) return;
    if (isLoginScreen()) return;

    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.innerHTML = `
      <div id="${FLYOUT_ID}" class="cl-profile-flyout">
        <div class="cl-flyout-head">
          <div id="${FLY_INITIALS_ID}" class="cl-flyout-avatar">U</div>
          <div class="cl-flyout-meta">
            <div id="${FLY_NAME_ID}" class="cl-flyout-name">User</div>
            <div id="${FLY_HANDLE_ID}" class="cl-flyout-handle">@account</div>
          </div>
        </div>
        <button class="cl-flyout-item" data-action="personalization">Personalization</button>
        <button class="cl-flyout-item" data-action="settings">Settings</button>
        <button class="cl-flyout-item" data-action="help">Help</button>
        <button class="cl-flyout-item danger" data-action="logout">Log out</button>
      </div>
      <button id="${BTN_ID}" class="cl-profile-button" aria-label="Open profile menu">
        <div id="${INITIALS_ID}" class="cl-profile-avatar">U</div>
        <div class="cl-profile-meta">
          <div id="${NAME_ID}" class="cl-profile-name">User</div>
          <div id="${HANDLE_ID}" class="cl-profile-handle">@account</div>
        </div>
      </button>
    `;

    const sidebar = getSidebarHost();
    if (sidebar instanceof HTMLElement) {
      sidebar.appendChild(root);
      root.classList.add("cl-profile-in-sidebar");
    } else {
      document.body.appendChild(root);
    }

    const btn = document.getElementById(BTN_ID);
    if (btn) btn.addEventListener("click", (e) => { e.stopPropagation(); toggleFlyout(); });

    root.querySelectorAll(".cl-flyout-item").forEach((item) => {
      item.addEventListener("click", (e) => {
        e.stopPropagation();
        const action = item.getAttribute("data-action");
        closeFlyout();
        if (action === "logout") {
          logout();
          return;
        }
        if (action === "help") {
          window.open("https://docs.chainlit.io", "_blank");
          return;
        }
        if (action === "settings" || action === "personalization") {
          const settingsButton =
            document.querySelector("[data-testid='chat-settings-button']") ||
            document.querySelector("button[aria-label*='settings' i]");
          if (settingsButton) settingsButton.click();
        }
      });
    });

    document.addEventListener("click", () => closeFlyout());
    ensurePlacement();
    updateCollapsedMode();
    setText(NAME_ID, profile.display_name);
    setText(HANDLE_ID, profile.subtitle);
    setText(INITIALS_ID, profile.initials);
    setText(FLY_NAME_ID, profile.display_name);
    setText(FLY_HANDLE_ID, profile.subtitle);
    setText(FLY_INITIALS_ID, profile.initials);
  }

  function parseProfileEvent(event) {
    const data = event && event.data;
    if (!data) return null;
    if (data.event === "user_profile" && data.payload) return data.payload;
    if (data.type === "window_message" && data.data && data.data.event === "user_profile") return data.data.payload;
    if (data.payload && data.payload.event === "user_profile") return data.payload.payload;
    return null;
  }

  function hideNativeTopRightProfile() {
    if (isLoginScreen()) return;
    const previouslyHidden = document.querySelectorAll("[data-cl-hide-native-profile='1']");
    previouslyHidden.forEach((el) => {
      if (el instanceof HTMLElement) {
        el.style.display = "";
      }
      el.removeAttribute("data-cl-hide-native-profile");
    });

    const candidates = Array.from(document.querySelectorAll("button,[role='button']"));
    for (const el of candidates) {
      if (!(el instanceof HTMLElement)) continue;
      if (el.closest(`#${ROOT_ID}`)) continue;

      const rect = el.getBoundingClientRect();
      const nearTopRight = rect.top < 120 && rect.left > window.innerWidth * 0.65;
      if (!nearTopRight) continue;

      const txt = (el.textContent || "").trim();
      const tinyAvatarLike =
        rect.width >= 24 &&
        rect.width <= 48 &&
        rect.height >= 24 &&
        rect.height <= 48 &&
        /^[A-Za-z0-9]{1,2}$/.test(txt);
      const menuTrigger = (el.getAttribute("aria-haspopup") || "").toLowerCase() === "menu";
      const profileLikeLabel = /profile|account|user/i.test(el.getAttribute("aria-label") || "");

      if (!(tinyAvatarLike || menuTrigger || profileLikeLabel)) continue;
      if (
        /readme|dark|light|theme|search|new chat/i.test(
          `${el.getAttribute("aria-label") || ""} ${txt}`
        )
      ) {
        continue;
      }

      el.style.display = "none";
      el.setAttribute("data-cl-hide-native-profile", "1");
    }
  }

  window.addEventListener("message", (event) => {
    const incoming = parseProfileEvent(event);
    if (incoming) applyProfile(incoming);
    hideNativeTopRightProfile();
  });

  document.addEventListener("click", () => {
    setTimeout(hideNativeTopRightProfile, 0);
  });

  document.addEventListener(
    "click",
    (event) => {
      const mode = findClosestSidebarToggle(event.target);
      if (!mode) return;
      // Let Chainlit handle the actual toggle first, then read DOM state once.
      setTimeout(() => {
        syncCollapsedStateFromDom();
        updateCollapsedMode();
      }, 80);
    },
    true
  );

  const observer = new MutationObserver(() => {
    if (isLoginScreen()) {
      destroyRoot();
      return;
    }
    if (profile.is_authenticated) createRoot();
    ensurePlacement();
    syncCollapsedStateFromDom();
    updateCollapsedMode();
    hideNativeTopRightProfile();
  });
  observer.observe(document.body, { childList: true, subtree: true });

  window.addEventListener("resize", ensurePlacement);
  window.addEventListener("resize", updateCollapsedMode);
  window.setInterval(updateCollapsedMode, 500);

  if (isLoginScreen()) {
    destroyRoot();
  }
})();
