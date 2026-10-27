/*
  File Name:     migrator.js
  Relative Path: Web/static/migrator.js
  Description:   Front-end behaviour for the Project DB Migrator UI: hamburger menu, configuration form (switches,
                 radios, dependent controls, folder browser), plan, run/progress, ledger.
  Author:        Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
*/
(function () {
  "use strict";
  var TOKEN = (document.querySelector('meta[name="migrator-token"]') || {}).content || "";
  var PAGE = document.body.dataset.page;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function toast(msg, bad) {
    var t = $("#toast"); if (!t) return;
    t.textContent = msg; t.className = "becss-migrator-toast becss-migrator-toast--show" + (bad ? " becss-migrator-toast--bad" : "");
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.className = "becss-migrator-toast"; }, 3200);
  }
  function api(url, opts) {
    opts = opts || {};
    var headers = { "Content-Type": "application/json" };
    if (opts.method && opts.method !== "GET") headers["X-Migrator-Token"] = TOKEN;
    return fetch(url, { method: opts.method || "GET", headers: headers, body: opts.body ? JSON.stringify(opts.body) : undefined })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }); });
  }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function pill(text, kind) { return el("span", { "class": "becss-migrator-pill becss-migrator-pill--" + kind }, text); }
  function canon(cfg) {                                /* key-order independent comparison */
    var out = {}; ["flags", "radios", "texts"].forEach(function (g) { var o = {}; Object.keys(cfg[g]).sort().forEach(function (k) { o[k] = cfg[g][k]; }); out[g] = o; });
    return JSON.stringify(out);
  }
  function bytes(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
  function statusKind(s) { return /failed|invalid|partial/.test(s) ? "bad" : /deleted|quarantined|verified|kept|chunked/.test(s) ? "ok" : "muted"; }

  /* ---- hamburger / side menu ---- */
  (function nav() {
    var toggle = $("#navToggle"), drawer = $("#mobileSidebar"), overlay = $("#sidebarOverlay"), close = $("#navClose");
    var mq = window.matchMedia("(max-width: 768px)");
    function setDrawer(open) {
      drawer.classList.toggle("open", open); overlay.classList.toggle("open", open);
      drawer.setAttribute("aria-hidden", open ? "false" : "true"); toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    toggle.addEventListener("click", function () {
      if (mq.matches) setDrawer(!drawer.classList.contains("open"));
      else { var h = document.body.classList.toggle("becss-migrator-nav-hidden"); toggle.setAttribute("aria-expanded", h ? "false" : "true");
        try { localStorage.setItem("migrator-nav-hidden", h ? "1" : "0"); } catch (e) {} }
    });
    overlay.addEventListener("click", function () { setDrawer(false); });
    close.addEventListener("click", function () { setDrawer(false); });
    close.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") setDrawer(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setDrawer(false); });
    mq.addEventListener("change", function () { setDrawer(false); });
    try { if (localStorage.getItem("migrator-nav-hidden") === "1" && !mq.matches) document.body.classList.add("becss-migrator-nav-hidden"); } catch (e) {}
  })();

  function setMode(dry) {
    var b = $("#modeBadge"); if (!b) return;
    b.dataset.dry = dry ? "true" : "false"; b.textContent = dry ? "DRY RUN" : "LIVE";
    b.className = "becss-migrator-badge " + (dry ? "becss-migrator-badge--dry" : "becss-migrator-badge--live");
    var s = $("#sideStatus"); if (s) s.textContent = "Mode: " + (dry ? "dry run (nothing changes)" : "LIVE (changes data)");
  }

  /* ---- overview ---- */
  function initOverview() { sourceInfo(function (j) {
    var c = $("#sourceCard"); c.textContent = "";
    c.appendChild(el("div", {}, "Mode: " + j.mode.replace(/_/g, " ")));
    var code = el("code", {}, j.path || "(not set)"); var line = el("div", {}); line.appendChild(code); line.appendChild(document.createTextNode(j.exists ? "  ✓ found" : "  ✗ not found" + (j.error ? " - " + j.error : "")));
    c.appendChild(line);
  }); }
  function sourceInfo(cb) { api("/api/source").then(cb); }

  /* ---- configuration ---- */
  function initConfig() {
    var form = $("#configForm"), saved = null;
    function read() {
      var cfg = { flags: {}, radios: {}, texts: {} };
      $$("[data-flag]", form).forEach(function (i) { cfg.flags[i.dataset.flag] = i.checked; });
      $$("[data-radio]:checked", form).forEach(function (i) { cfg.radios[i.dataset.radio] = i.value; });
      $$("[data-text]", form).forEach(function (i) { cfg.texts[i.dataset.text] = i.value; });
      return cfg;
    }
    function write(cfg) {
      $$("[data-flag]", form).forEach(function (i) { i.checked = !!cfg.flags[i.dataset.flag]; });
      $$("[data-radio]", form).forEach(function (i) { i.checked = cfg.radios[i.dataset.radio] === i.value; });
      $$("[data-text]", form).forEach(function (i) { i.value = cfg.texts[i.dataset.text]; });
    }
    function flag(k) { return $('[data-flag="' + k + '"]').checked; }
    function radio(k) { var r = $('[data-radio="' + k + '"]:checked'); return r ? r.value : ""; }
    function hint(k, msg, ok) { var h = $('[data-hint="' + k + '"]'); if (h) { h.textContent = msg || ""; h.classList.toggle("becss-migrator-row__hint--ok", !!ok); } }
    function resolveSource() {
      var cfg = read(), box = $("#sourceResolved");
      if (cfg.radios.Source_Mode === "custom") {
        var p = cfg.texts.Custom_Source_Root.trim();
        box.className = "becss-migrator-callout" + (p ? "" : " becss-migrator-callout--bad");
        box.textContent = p ? "Custom folder (saved value is used on the next run): " + p : "Pick a folder to use as the source.";
      } else sourceInfo(function (j) {
        box.className = "becss-migrator-callout" + (j.exists ? "" : " becss-migrator-callout--bad");
        box.textContent = "PROJECT_INDEX_ROOT resolves to " + (j.path || "(unset)") + (j.exists ? "  ✓ found" : "  ✗ not found on this machine");
      });
    }
    function smart() {                               /* dependent controls */
      var dry = flag("Dry_Run"), custom = radio("Source_Mode") === "custom", del = radio("Original_Disposition") === "delete";
      $("#customRoot").disabled = !custom; $("#browseBtn").disabled = !custom; $("#customField").classList.toggle("becss-migrator-row--dim", !custom);
      $("#liveCallout").hidden = dry; $("#deleteCallout").hidden = dry || !del;
      hint("Dry_Run", dry ? "Safe: nothing will be written or removed." : "LIVE: runs will change data.", dry);
      hint("Binary_Enabled", flag("Binary_Enabled") ? "" : "Zips that contain binary files will be only partly captured and will never be removed.");
      hint("Deep_Verify", flag("Deep_Verify") ? "" : "Only hashes and headers are checked; slower-but-safer deep check is off.");
      $$('[data-radio="Original_Disposition"]').forEach(function (r) { r.closest(".becss-migrator-radio").title = dry ? "Applies when Dry run is off" : ""; });
      var changed = canon(read()) !== canon(saved);
      $("#dirtyNote").textContent = changed ? "Unsaved changes" : "";
      var badgeDry = $("#modeBadge").dataset.dry === "true"; if (changed === false) setMode(badgeDry);
    }
    api("/api/config").then(function (cfg) { saved = cfg; write(cfg); smart(); resolveSource(); });
    form.addEventListener("input", function () { smart(); });
    form.addEventListener("change", function () { smart(); resolveSource(); });
    $("#saveBtn").addEventListener("click", function () {
      var cfg = read();
      api("/api/config", { method: "POST", body: cfg }).then(function (j) {
        if (j.ok) { saved = j.config; setMode(!!j.config.flags.Dry_Run); smart(); resolveSource(); toast("Configuration saved"); }
        else toast((j.problems || [j.error || "Save failed"]).join("; "), true);
      });
    });
    $("#revertBtn").addEventListener("click", function () { write(saved); smart(); resolveSource(); });
    /* folder browser */
    var browser = $("#browser"), cur = "";
    function browse(path) {
      api("/api/browse?path=" + encodeURIComponent(path || "")).then(function (j) {
        if (!j.ok) { toast(j.error || "cannot open", true); return; }
        cur = j.path; $("#browsePath").textContent = j.path; $("#browseUp").disabled = !j.parent; $("#browseUp").dataset.parent = j.parent || "";
        var list = $("#browseList"); list.textContent = "";
        if (!j.dirs.length) list.appendChild(el("li", {}, "(no sub-folders)"));
        j.dirs.forEach(function (d) { var li = el("li", {}, "📁 " + d); li.addEventListener("click", function () { browse(j.path.replace(/[\/\\]$/, "") + "/" + d); }); list.appendChild(li); });
      });
    }
    $("#browseBtn").addEventListener("click", function () { browser.hidden = !browser.hidden; if (!browser.hidden) browse($("#customRoot").value); });
    $("#browseUp").addEventListener("click", function () { browse(this.dataset.parent); });
    $("#browseUse").addEventListener("click", function () { $("#customRoot").value = cur; browser.hidden = true; smart(); resolveSource(); });
  }

  /* ---- plan ---- */
  function initPlan() {
    var rows = [];
    function group(z) { return !z.valid || z.locked ? "bad" : /chunked|verified|deleted|quarantined|kept/.test(z.status) ? "done" : "ready"; }
    function render() {
      var show = {}; $$("[data-show]").forEach(function (c) { show[c.dataset.show] = c.checked; });
      var body = $("#planBody"); body.textContent = "";
      var shown = rows.filter(function (z) { return show[group(z)]; });
      if (!shown.length) { var tr = el("tr"); var td = el("td", { colspan: 5 }, rows.length ? "Nothing matches the filters." : "No zip files found."); tr.appendChild(td); body.appendChild(tr); }
      shown.forEach(function (z) {
        var tr = el("tr"); tr.appendChild(el("td", {}, z.rel)); tr.appendChild(el("td", {}, String(z.files))); tr.appendChild(el("td", {}, bytes(z.size)));
        var st = el("td"); var g = group(z); st.appendChild(pill(z.locked ? "locked" : !z.valid ? "invalid" : z.status || "ready", g === "bad" ? "bad" : g === "done" ? "ok" : "muted")); tr.appendChild(st);
        tr.appendChild(el("td", {}, z.error)); body.appendChild(tr);
      });
      $("#planTitle").textContent = "Zips (" + shown.length + " of " + rows.length + ")";
    }
    function load() {
      $("#planBody").innerHTML = "<tr><td colspan='5'>Scanning…</td></tr>";
      api("/api/plan").then(function (j) {
        if (!j.ok) { $("#planBody").textContent = ""; var tr = el("tr"); tr.appendChild(el("td", { colspan: 5 }, j.error || "scan failed")); $("#planBody").appendChild(tr); return; }
        rows = j.zips; render();
      });
    }
    $$("[data-show]").forEach(function (c) { c.addEventListener("change", render); });
    $("#rescanBtn").addEventListener("click", load); load();
  }

  /* ---- run ---- */
  function initRun() {
    var timer = null, running = false;
    var dryNow = function () { return $("#modeBadge").dataset.dry === "true"; };
    function sync() {
      var dry = dryNow(), box = $("#runMode");
      box.className = "becss-migrator-callout" + (dry ? "" : " becss-migrator-callout--live");
      box.textContent = dry ? "Dry run: phases scan and report only. Switch Dry run off in Configuration to apply changes." : "LIVE: chunks will be written" + ($("#phVerify").checked ? " and original zips will be " + $("#dispositionWord").textContent + "d" : "") + ".";
      $("#confirmRow").hidden = dry; if (dry) $("#confirmLive").checked = false;
      var any = $$("[data-phase]:checked").length > 0;
      $("#runBtn").disabled = running || !any || (!dry && !$("#confirmLive").checked);
      $("#cancelBtn").disabled = !running;
    }
    $$("[data-phase], #confirmLive").forEach(function (c) { c.addEventListener("change", sync); });
    api("/api/config").then(function (cfg) { setMode(!!cfg.flags.Dry_Run); $("#dispositionWord").textContent = cfg.radios.Original_Disposition; sync(); });
    function renderJob(j) {
      var done = j.state !== "running";
      var cur = j.current || {}, pct = cur.total ? Math.round((cur.index / cur.total) * 100) : (done ? 100 : 5);
      $("#bar").style.width = (j.state === "idle" ? 0 : pct) + "%";
      $("#jobLine").textContent = j.state === "running" ? ("Running " + (cur.event || "") + (cur.zip ? ": " + cur.zip : "") + (cur.total ? " (" + cur.index + "/" + cur.total + ")" : "")) :
        j.state === "idle" ? "Idle." : j.state === "failed" ? "Failed: " + j.error : "Job " + j.state + ".";
      $("#footerStatus").textContent = j.state === "running" ? "Running…" : "Ready";
      var body = $("#resultBody"); body.textContent = ""; var n = 0;
      (j.results || []).forEach(function (r) { r.entries.forEach(function (e) { n++; var tr = el("tr"); tr.appendChild(el("td", {}, r.phase)); tr.appendChild(el("td", {}, e.Zip_Rel));
        var o = el("td"); o.appendChild(pill(e.Outcome, statusKind(e.Outcome))); tr.appendChild(o); tr.appendChild(el("td", {}, e.Detail)); body.appendChild(tr); }); });
      if (!n) { var tr0 = el("tr"); tr0.appendChild(el("td", { colspan: 4 }, "No results yet.")); body.appendChild(tr0); }
      $("#reportLine").textContent = (j.results || []).map(function (r) { return r.report; }).filter(Boolean).join("  |  ");
      running = !done; sync();
      if (done && timer) { clearInterval(timer); timer = null; }
    }
    function poll() { api("/api/job").then(renderJob); }
    $("#runBtn").addEventListener("click", function () {
      var phases = $$("[data-phase]:checked").map(function (c) { return c.dataset.phase; });
      api("/api/run", { method: "POST", body: { phases: phases, confirm: $("#confirmLive").checked } }).then(function (j) {
        if (!j.ok) { toast(j.error || "could not start", true); return; }
        running = true; sync(); toast(j.dry_run ? "Dry run started" : "Live run started"); poll(); timer = setInterval(poll, 800);
      });
    });
    $("#cancelBtn").addEventListener("click", function () { api("/api/cancel", { method: "POST", body: {} }).then(function () { toast("Cancelling after the current zip"); }); });
    api("/api/job").then(function (j) { renderJob(j); if (j.state === "running") timer = setInterval(poll, 800); });
  }

  /* ---- ledger ---- */
  function initLedger() {
    var rows = [];
    var views = { all: function () { return true; }, chunked: function (s) { return s === "chunked"; }, verified: function (s) { return s === "verified"; },
      gone: function (s) { return /deleted|quarantined|kept/.test(s); }, problem: function (s) { return /failed|invalid|partial/.test(s); } };
    function render() {
      var v = ($('input[name="ledgerView"]:checked') || {}).value || "all", body = $("#ledgerBody"); body.textContent = "";
      var shown = rows.filter(function (r) { return views[v](r.Status); });
      if (!shown.length) { var tr = el("tr"); tr.appendChild(el("td", { colspan: 6 }, "No entries.")); body.appendChild(tr); }
      shown.forEach(function (r) { var tr = el("tr"); tr.appendChild(el("td", {}, r.Zip_Rel)); tr.appendChild(el("td", {}, r.Project_Name));
        var s = el("td"); s.appendChild(pill(r.Status, statusKind(r.Status))); tr.appendChild(s); tr.appendChild(el("td", {}, r.Versioned_UUID)); tr.appendChild(el("td", {}, r.Updated_At)); tr.appendChild(el("td", {}, r.Message)); body.appendChild(tr); });
      $("#ledgerTitle").textContent = "Entries (" + shown.length + " of " + rows.length + ")";
    }
    $$('input[name="ledgerView"]').forEach(function (r) { r.addEventListener("change", render); });
    api("/api/ledger").then(function (j) { rows = j.rows || []; render(); });
  }

  if (PAGE === "overview") initOverview();
  if (PAGE === "config") initConfig();
  if (PAGE === "plan") initPlan();
  if (PAGE === "run") initRun();
  if (PAGE === "ledger") initLedger();
})();
