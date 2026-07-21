/* taOSmobile — polyfills for Ubuntu Touch's webview.
 *
 * webapp-container/Morph on UT 24.04 is QtWebEngine 5.15 == Chromium 87.
 * The taOS SPA is built with Vite `target: es2022` and calls runtime APIs
 * that landed after 87, so the bundle throws on load and the screen stays
 * blank. These shims are the minimum needed to boot the SPA there.
 *
 * Loaded before the app bundle. Each shim is feature-detected, so on a
 * modern engine this file is a no-op.
 */
(function () {
  "use strict";

  // Chromium 93.
  if (typeof Object.hasOwn !== "function") {
    Object.defineProperty(Object, "hasOwn", {
      value: function hasOwn(obj, key) {
        if (obj == null) throw new TypeError("Cannot convert undefined or null to object");
        return Object.prototype.hasOwnProperty.call(Object(obj), key);
      },
      configurable: true,
      writable: true,
    });
  }

  // Chromium 98. Structured cloning cannot be fully reproduced in script:
  // this handles the plain data the SPA actually clones (objects, arrays,
  // Date, Map, Set, typed arrays) and preserves cycles. It does NOT handle
  // transferables — nothing in the SPA passes them today.
  if (typeof globalThis.structuredClone !== "function") {
    globalThis.structuredClone = function structuredClone(value) {
      var seen = new Map();

      function clone(v) {
        if (v === null || typeof v !== "object") return v;
        if (seen.has(v)) return seen.get(v);

        if (v instanceof Date) return new Date(v.getTime());
        if (v instanceof RegExp) return new RegExp(v.source, v.flags);
        if (typeof ArrayBuffer !== "undefined" && v instanceof ArrayBuffer) {
          return v.slice(0);
        }
        if (typeof ArrayBuffer !== "undefined" && ArrayBuffer.isView(v)) {
          return new v.constructor(v.buffer.slice(0), v.byteOffset, v.length);
        }

        if (v instanceof Map) {
          var m = new Map();
          seen.set(v, m);
          v.forEach(function (val, key) { m.set(clone(key), clone(val)); });
          return m;
        }
        if (v instanceof Set) {
          var s = new Set();
          seen.set(v, s);
          v.forEach(function (val) { s.add(clone(val)); });
          return s;
        }
        if (Array.isArray(v)) {
          var arr = new Array(v.length);
          seen.set(v, arr);
          for (var i = 0; i < v.length; i++) arr[i] = clone(v[i]);
          return arr;
        }

        var out = {};
        seen.set(v, out);
        Object.keys(v).forEach(function (k) { out[k] = clone(v[k]); });
        return out;
      }

      return clone(value);
    };
  }

  // Chromium 92 — cheap to shim, and trivially missed by a later bundle.
  if (typeof Array.prototype.at !== "function") {
    Object.defineProperty(Array.prototype, "at", {
      value: function at(n) {
        n = Math.trunc(n) || 0;
        if (n < 0) n += this.length;
        return n < 0 || n >= this.length ? undefined : this[n];
      },
      configurable: true,
      writable: true,
    });
  }
  if (typeof String.prototype.at !== "function") {
    Object.defineProperty(String.prototype, "at", {
      value: function at(n) {
        n = Math.trunc(n) || 0;
        if (n < 0) n += this.length;
        return n < 0 || n >= this.length ? undefined : this[n];
      },
      configurable: true,
      writable: true,
    });
  }

  // Surface anything else the old engine chokes on: without this a failed
  // bundle is a silent black screen with no way to tell what broke.
  window.addEventListener("error", function (e) {
    try {
      var el = document.getElementById("taos-boot-error");
      if (!el) {
        el = document.createElement("pre");
        el.id = "taos-boot-error";
        el.style.cssText =
          "position:fixed;inset:0;z-index:99999;margin:0;padding:16px;" +
          "background:#111;color:#f66;font:12px/1.4 monospace;" +
          "white-space:pre-wrap;overflow:auto";
        document.body.appendChild(el);
      }
      el.textContent += (e.message || String(e.error)) + "\n  at " + (e.filename || "?") + ":" + (e.lineno || "?") + "\n\n";
    } catch (_) { /* last resort: nothing to do */ }
  });
})();
