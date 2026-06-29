// clientlog.js - A1.2/A1.1 capture des erreurs front (diagnostic v15)
// Capte window.onerror, unhandledrejection, console.error/warn -> POST /api/client-log
// Garde-fous : throttle, dedup consecutif, troncature, anti-boucle.
(function () {
  if (window.__ZIMA_CLIENTLOG_INSTALLED) return;
  window.__ZIMA_CLIENTLOG_INSTALLED = true;

  var MAX_PER_SEC = 5;
  var _bucket = [];
  var _lastKey = "";
  var _sending = false;

  function _throttled() {
    var now = Date.now();
    _bucket = _bucket.filter(function (t) { return now - t < 1000; });
    if (_bucket.length >= MAX_PER_SEC) return true;
    _bucket.push(now);
    return false;
  }

  function _trunc(s, n) {
    try { s = (s == null) ? "" : String(s); } catch (e) { return ""; }
    return s.length > n ? s.slice(0, n) : s;
  }

  function send(ev) {
    if (_sending) return;            // anti-boucle : pas de POST imbrique
    if (_throttled()) return;
    var key = (ev.level || "") + "|" + (ev.message || "") + "|" + (ev.source || "") + "|" + (ev.line || "");
    if (key === _lastKey) return;    // dedup consecutif
    _lastKey = key;
    var payload = {
      level: ev.level || "error",
      message: _trunc(ev.message, 2000),
      source: _trunc(ev.source, 500),
      line: (typeof ev.line === "number") ? ev.line : null,
      col: (typeof ev.col === "number") ? ev.col : null,
      stack: _trunc(ev.stack, 4000),
      url: _trunc(location.href, 500),
      tab: _trunc(window.__ZIMA_TAB || "", 120),
      action: _trunc(window.__ZIMA_ACTION || "", 200),
      app_version: _trunc(window.__ZIMA_VERSION || "", 40),
      user_agent: _trunc(navigator.userAgent, 300),
      ts: new Date().toISOString()
    };
    _sending = true;
    try {
      fetch("/api/client-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        keepalive: true
      }).catch(function () {}).finally(function () { _sending = false; });
    } catch (e) {
      _sending = false;             // un echec ne redeclenche pas de POST
    }
  }

  window.addEventListener("error", function (e) {
    send({
      level: "error",
      message: (e && e.message) ? e.message : "window.onerror",
      source: (e && e.filename) ? e.filename : "",
      line: (e && e.lineno) ? e.lineno : null,
      col: (e && e.colno) ? e.colno : null,
      stack: (e && e.error && e.error.stack) ? e.error.stack : ""
    });
  });

  window.addEventListener("unhandledrejection", function (e) {
    var r = e ? e.reason : null;
    var msg = "unhandledrejection";
    var stk = "";
    if (r) {
      if (typeof r === "string") msg = r;
      else if (r.message) { msg = r.message; stk = r.stack || ""; }
      else { try { msg = JSON.stringify(r); } catch (x) { msg = String(r); } }
    }
    send({ level: "error", message: msg, source: "promise", stack: stk });
  });

  ["error", "warn"].forEach(function (lvl) {
    var orig = console[lvl];
    console[lvl] = function () {
      try {
        var parts = [];
        for (var i = 0; i < arguments.length; i++) {
          var a = arguments[i];
          parts.push((a && a.message) ? a.message : (typeof a === "object" ? safeJson(a) : String(a)));
        }
        var stk = "";
        for (var j = 0; j < arguments.length; j++) {
          if (arguments[j] && arguments[j].stack) { stk = arguments[j].stack; break; }
        }
        send({ level: (lvl === "warn") ? "warning" : "error", message: parts.join(" "), source: "console", stack: stk });
      } catch (x) { /* ne jamais casser la console */ }
      return orig.apply(console, arguments);
    };
  });

  function safeJson(o) { try { return JSON.stringify(o); } catch (e) { return String(o); } }
})();
