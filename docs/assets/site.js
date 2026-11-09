// Theme switch (auto / light / dark) and the contact dialog.
(function () {
  var root = document.documentElement;
  var KEY = "stokes-theme";

  function stored() {
    try { return localStorage.getItem(KEY) || "auto"; } catch (e) { return "auto"; }
  }
  function apply(mode) {
    if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
    else root.removeAttribute("data-theme");
    document.querySelectorAll("[data-theme-set]").forEach(function (b) {
      b.setAttribute("aria-checked", String(b.getAttribute("data-theme-set") === mode));
    });
  }
  apply(stored());
  document.querySelectorAll("[data-theme-set]").forEach(function (b) {
    b.addEventListener("click", function () {
      var mode = b.getAttribute("data-theme-set");
      try { if (mode === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, mode); } catch (e) {}
      apply(mode);
    });
  });

  // Step gallery: previous and next buttons, disabled at either end. Without script it still scrolls.
  var gallery = document.querySelector("[data-gallery]");
  var gnav = document.querySelector("[data-gallery-nav]");
  if (gallery && gnav) {
    var prev = gnav.querySelector('[data-dir="-1"]');
    var next = gnav.querySelector('[data-dir="1"]');
    var step = function () {
      var s = gallery.querySelector(".slide");
      return s ? s.getBoundingClientRect().width + 20 : gallery.clientWidth * 0.8;
    };
    var sync = function () {
      var max = gallery.scrollWidth - gallery.clientWidth - 2;
      prev.disabled = gallery.scrollLeft <= 2;
      next.disabled = gallery.scrollLeft >= max;
    };
    [prev, next].forEach(function (b) {
      b.addEventListener("click", function () {
        gallery.scrollBy({ left: Number(b.getAttribute("data-dir")) * step(), behavior: "smooth" });
      });
    });
    gallery.addEventListener("scroll", function () { window.requestAnimationFrame(sync); }, { passive: true });
    window.addEventListener("resize", sync);
    gnav.hidden = false;
    sync();
  }

  // Install: a segmented control picks the platform. Without script both snippets show.
  var seg = document.querySelector("[data-seg]");
  if (seg) {
    var tabs = Array.prototype.slice.call(seg.querySelectorAll('[role="tab"]'));
    var select = function (tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(t, false); });
      t.addEventListener("keydown", function (e) {
        if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
        e.preventDefault();
        select(tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length], true);
      });
    });
    // Open on the visitor's own platform when it is obvious.
    var linux = /Linux|X11/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent);
    seg.hidden = false;
    select(tabs[linux ? 1 : 0], false);
  }

  // Video: count a play and a finish once per page view. GoatCounter loads asynchronously and may
  // be missing (blocked or switched off), so every call checks for it first.
  function track(path, title) {
    if (window.goatcounter && typeof window.goatcounter.count === "function") {
      window.goatcounter.count({ path: path, title: title, event: true });
    }
  }
  var film = document.querySelector(".film video");
  if (film) {
    var played = false, ended = false;
    film.addEventListener("play", function () { if (!played) { played = true; track("video-play", "Demo video: play"); } });
    film.addEventListener("ended", function () { if (!ended) { ended = true; track("video-end", "Demo video: watched to the end"); } });
  }

  // The address is assembled only when someone asks for it, so it never sits in the page source.
  var parts = ["dhanninacs", "gmail.com"];
  var dialog = document.getElementById("contact");
  var mail = dialog && dialog.querySelector("[data-mail]");
  var copy = dialog && dialog.querySelector("[data-copy]");
  var copied = dialog && dialog.querySelector("[data-copied]");
  function address() { return parts[0] + "@" + parts[1]; }
  function reveal() {
    var a = address();
    mail.textContent = a;
    mail.href = "mailto:" + a + "?subject=" + encodeURIComponent("Stokes");
    mail.removeAttribute("data-mail");
    copy.hidden = false;
  }
  if (dialog) {
    document.querySelectorAll("[data-contact]").forEach(function (b) {
      b.addEventListener("click", function () {
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
      });
    });
    mail.addEventListener("click", function (e) {
      if (mail.hasAttribute("data-mail")) { e.preventDefault(); reveal(); }
    });
    copy.addEventListener("click", function () {
      if (!navigator.clipboard) return;
      navigator.clipboard.writeText(address()).then(function () {
        copied.hidden = false;
        setTimeout(function () { copied.hidden = true; }, 1600);
      });
    });
    dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });
  }
})();
