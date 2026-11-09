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
