/* taOSmobile — on-screen keyboard for the exclusive session.
 *
 * WHY THIS EXISTS
 * Ubuntu Touch's keyboard is maliit-server, a separate process that draws
 * through the compositor's input-method protocol. The exclusive session runs
 * the kiosk on Qt's eglfs platform: one process owns the framebuffer and there
 * is no compositor, so maliit cannot appear. Without this file there is no way
 * to type anything on the device.
 *
 * Living in the web layer also means one keyboard for phone, tablet and
 * desktop rather than per-platform input-method integration.
 *
 * Suppressed automatically when a real input method is available (i.e. running
 * under Lomiri), so the same bundle works in both sessions.
 */
(function () {
  "use strict";

  // Under Lomiri, maliit handles input; only take over when we are the
  // display server. The exclusive launcher sets this marker on the URL.
  var FORCE = /[?&]taosOsk=1/.test(location.search);
  if (!FORCE && !/eglfs/i.test(navigator.userAgent) && !window.__taosExclusive) {
    // Heuristic fallback: assume a platform keyboard exists on touch devices
    // that report visualViewport resizing (Lomiri/maliit does).
    if ("virtualKeyboard" in navigator || window.visualViewport) return;
  }

  var LAYOUTS = {
    letters: [
      ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
      ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
      ["⇧", "z", "x", "c", "v", "b", "n", "m", "⌫"],
      ["?123", ",", " ", ".", "⏎"],
    ],
    symbols: [
      ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
      ["@", "#", "$", "%", "&", "-", "+", "(", ")"],
      ["=*\\", "*", '"', "'", ":", ";", "!", "?", "⌫"],
      ["ABC", ",", " ", ".", "⏎"],
    ],
    extra: [
      ["~", "`", "|", "•", "√", "π", "÷", "×", "{", "}"],
      ["£", "¢", "€", "¥", "^", "_", "=", "[", "]"],
      ["?123", "<", ">", "©", "®", "™", "/", "\\", "⌫"],
      ["ABC", ",", " ", ".", "⏎"],
    ],
  };

  var state = { layout: "letters", shift: false, target: null };
  var root = null;

  function css() {
    if (document.getElementById("taos-osk-style")) return;
    var s = document.createElement("style");
    s.id = "taos-osk-style";
    s.textContent = [
      "#taos-osk{position:fixed;left:0;right:0;bottom:0;z-index:2147483000;",
      "  background:#14161a;border-top:1px solid #2a2f36;padding:6px 4px",
      "  calc(6px + env(safe-area-inset-bottom,0px));display:none;",
      "  font-family:system-ui,-apple-system,sans-serif;user-select:none;",
      "  -webkit-user-select:none;touch-action:manipulation}",
      "#taos-osk.taos-osk-open{display:block}",
      "#taos-osk .taos-osk-row{display:flex;gap:5px;margin:5px 3px;justify-content:center}",
      "#taos-osk button{flex:1 1 0;min-width:0;height:46px;border:0;border-radius:7px;",
      "  background:#272b31;color:#f2f4f7;font-size:17px;line-height:1;",
      "  display:flex;align-items:center;justify-content:center;padding:0}",
      "#taos-osk button:active{background:#3b424b}",
      "#taos-osk button.taos-osk-wide{flex:2 1 0}",
      "#taos-osk button.taos-osk-space{flex:5 1 0}",
      "#taos-osk button.taos-osk-mod{background:#1c2026;font-size:14px}",
      "#taos-osk button.taos-osk-on{background:#3d6ae0}",
      "#taos-osk .taos-osk-bar{display:flex;justify-content:flex-end;padding:0 6px}",
      "#taos-osk .taos-osk-bar button{flex:0 0 auto;width:52px;height:26px;",
      "  font-size:12px;background:transparent;color:#8b939e}",
    ].join("");
    document.head.appendChild(s);
  }

  /* React (and other frameworks) subscribe to input events and ignore direct
   * .value writes, so set through the native setter and dispatch the events a
   * real keypress would produce. */
  function setValue(el, value, caret) {
    var proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(el, value);
    else el.value = value;
    if (typeof caret === "number" && el.setSelectionRange) {
      try { el.setSelectionRange(caret, caret); } catch (_) {}
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function insert(text) {
    var el = state.target;
    if (!el) return;

    if (el.isContentEditable) {
      document.execCommand("insertText", false, text);
      return;
    }
    var start = el.selectionStart, end = el.selectionEnd;
    if (typeof start !== "number") {
      setValue(el, (el.value || "") + text);
      return;
    }
    var v = el.value || "";
    setValue(el, v.slice(0, start) + text + v.slice(end), start + text.length);
  }

  function backspace() {
    var el = state.target;
    if (!el) return;

    if (el.isContentEditable) {
      document.execCommand("delete", false);
      return;
    }
    var start = el.selectionStart, end = el.selectionEnd;
    var v = el.value || "";
    if (typeof start !== "number") { setValue(el, v.slice(0, -1)); return; }
    if (start !== end) {
      setValue(el, v.slice(0, start) + v.slice(end), start);
    } else if (start > 0) {
      setValue(el, v.slice(0, start - 1) + v.slice(start), start - 1);
    }
  }

  function enter() {
    var el = state.target;
    if (!el) return;
    var multiline = el.tagName === "TEXTAREA" || el.isContentEditable;
    // Send a real Enter so key handlers (send message, submit) fire.
    var opts = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true };
    var prevented = !el.dispatchEvent(new KeyboardEvent("keydown", opts));
    el.dispatchEvent(new KeyboardEvent("keyup", opts));
    if (prevented) return;
    if (multiline) { insert("\n"); return; }
    if (el.form && typeof el.form.requestSubmit === "function") el.form.requestSubmit();
    else if (el.form) el.form.submit();
  }

  function press(key) {
    switch (key) {
      case "⇧": state.shift = !state.shift; render(); return;
      case "⌫": backspace(); return;
      case "⏎": enter(); return;
      case "?123": state.layout = "symbols"; state.shift = false; render(); return;
      case "ABC": state.layout = "letters"; state.shift = false; render(); return;
      case "=*\\": state.layout = "extra"; render(); return;
      default:
        insert(state.shift && state.layout === "letters" ? key.toUpperCase() : key);
        if (state.shift) { state.shift = false; render(); }
    }
  }

  function render() {
    css();
    if (!root) {
      root = document.createElement("div");
      root.id = "taos-osk";
      // Never steal focus from the field being typed into.
      root.addEventListener("mousedown", function (e) { e.preventDefault(); });
      root.addEventListener("touchstart", function (e) { e.preventDefault(); }, { passive: false });
      document.body.appendChild(root);
    }
    root.textContent = "";

    var bar = document.createElement("div");
    bar.className = "taos-osk-bar";
    var hide = document.createElement("button");
    hide.textContent = "Hide";
    hide.addEventListener("click", close);
    bar.appendChild(hide);
    root.appendChild(bar);

    LAYOUTS[state.layout].forEach(function (row) {
      var r = document.createElement("div");
      r.className = "taos-osk-row";
      row.forEach(function (key) {
        var b = document.createElement("button");
        var isMod = ["⇧", "⌫", "?123", "ABC", "=*\\", "⏎"].indexOf(key) !== -1;
        b.textContent = key === " " ? "" : (state.shift && state.layout === "letters" && !isMod ? key.toUpperCase() : key);
        if (key === " ") b.className = "taos-osk-space";
        else if (isMod) b.className = "taos-osk-wide taos-osk-mod" + (key === "⇧" && state.shift ? " taos-osk-on" : "");
        b.addEventListener("click", function (e) { e.preventDefault(); press(key); });
        r.appendChild(b);
      });
      root.appendChild(r);
    });
  }

  function isTypable(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    if (el.tagName === "TEXTAREA") return !el.readOnly && !el.disabled;
    if (el.tagName !== "INPUT") return false;
    return !el.readOnly && !el.disabled &&
      /^(text|search|url|email|password|tel|number|)$/i.test(el.type || "");
  }

  function open(el) {
    state.target = el;
    render();
    root.classList.add("taos-osk-open");
    // Keep the focused field above the keyboard.
    document.body.style.paddingBottom = root.offsetHeight + "px";
    setTimeout(function () {
      try { el.scrollIntoView({ block: "center", behavior: "smooth" }); } catch (_) {}
    }, 50);
  }

  function close() {
    if (root) root.classList.remove("taos-osk-open");
    document.body.style.paddingBottom = "";
    state.target = null;
  }

  document.addEventListener("focusin", function (e) {
    if (isTypable(e.target)) open(e.target);
  });
  document.addEventListener("focusout", function (e) {
    // Ignore blur caused by tapping the keyboard itself.
    setTimeout(function () {
      if (!isTypable(document.activeElement)) close();
    }, 120);
  });

  window.taosOsk = { open: open, close: close, isOpen: function () { return !!state.target; } };
})();
