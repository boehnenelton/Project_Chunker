/*
  File Name:     chunker.js
  Relative Path: Web/static/chunker/chunker.js
  Description:   Front-end for the unified Project Chunker web app: shell (menu, picker, toast, job badge),
                 shared widgets, and one init function per sidebar section. All dynamic text is set with
                 textContent (no innerHTML); state-changing calls carry the per-process token.
  VERSION:       1.8.0
  Author:        Elton Boehnen · boehnenelton2024@gmail.com · boehnenelton2024.pages.dev · github.com/boehnenelton
  RELATIONAL_ID: 6f351352-50fe-44a6-b7a4-7bcc73182201
*/
(function () {
  "use strict";
  var TOKEN = (document.querySelector('meta[name="chunker-token"]') || {}).content || "";
  var PAGE = document.body.dataset.page;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var P = "becss-chunker-";

  /* ───────── helpers ───────── */
  function h(tag, attrs) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] !== false && attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k] === true ? "" : attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c === null || c === undefined || c === false) continue;
      if (Array.isArray(c)) c.forEach(function (x) { if (x) e.appendChild(x); });
      else e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); return e; }
  function toast(msg, bad) {
    var t = $("#toast"); if (!t) return;
    t.textContent = msg; t.className = P + "toast " + P + "toast--show" + (bad ? " " + P + "toast--bad" : "");
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.className = P + "toast"; }, 3600);
  }
  function api(url, method, body) {
    var headers = { "Content-Type": "application/json" };
    if (method && method !== "GET") headers["X-Chunker-Token"] = TOKEN;
    return fetch(url, { method: method || "GET", headers: headers, body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { return r.json().then(function (j) { j._status = r.status; return j; }, function () { return { ok: false, error: "bad response", _status: r.status }; }); })
      .then(function (j) { if (j.ok === false) { var e = new Error(j.error || (j.problems || []).join("; ") || "request failed"); e.data = j; throw e; } return j; });
  }
  function bytes(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
  function pill(text, kind) { return h("span", { "class": P + "pill " + P + "pill--" + (kind || "muted"), text: text }); }
  function kindOf(s) { return /fail|invalid|partial|error|blocked/.test(s) ? "bad" : /deleted|quarantined|verified|kept|chunked|done|archived|ok/.test(s) ? "ok" : "muted"; }
  function section(title, nodes) { return h("section", { "class": P + "section" }, title ? h("h2", { "class": P + "section__title", text: title }) : null, nodes); }
  function muted(text) { return h("p", { "class": P + "muted", text: text }); }
  function banner(text, ok) { return h("div", { "class": P + "banner" + (ok ? " " + P + "banner--ok" : ""), role: "alert", text: text }); }
  function btn(text, onclick, kind) { return h("button", { type: "button", "class": P + "btn" + (kind ? " " + P + "btn--" + kind : ""), text: text, onclick: onclick }); }
  function stats(pairs) {
    return h("div", { "class": P + "stats" }, pairs.map(function (p) {
      return h("div", { "class": P + "stat" }, h("span", { "class": P + "stat__value", text: String(p[1]) }), h("span", { "class": P + "stat__label", text: p[0] }));
    }));
  }
  function listBox(items, cap) {
    cap = cap || 200;
    var ul = h("ul", { "class": P + "list" });
    items.slice(0, cap).forEach(function (t) { ul.appendChild(h("li", { text: t })); });
    if (items.length > cap) ul.appendChild(h("li", { text: "… " + (items.length - cap) + " more" }));
    return ul;
  }
  function table(cols, rows, onRow) {
    var thead = h("tr", null, cols.map(function (c) { return h("th", { text: c }); }));
    var body = h("tbody");
    rows.forEach(function (r) {
      var tr = h("tr", onRow ? { "class": P + "row--clip", onclick: function () { onRow(r); } } : null,
        r.cells.map(function (c) { return h("td", null, c instanceof Node ? c : document.createTextNode(String(c))); }));
      body.appendChild(tr);
    });
    return h("div", { "class": P + "tablewrap" }, h("table", { "class": P + "table" }, h("thead", null, thead), body));
  }
  function sw(label, desc, checked, onchange) {
    var input = h("input", { type: "checkbox", role: "switch", "aria-label": label });
    input.checked = !!checked;
    input.addEventListener("change", function () { onchange && onchange(input.checked); });
    return { input: input, el: h("div", { "class": P + "row" },
      h("div", { "class": P + "row__info" }, h("span", { "class": P + "row__label", text: label }), desc ? h("span", { "class": P + "row__desc", text: desc }) : null),
      h("label", { "class": P + "switch" }, input, h("span", { "class": P + "switch__track" }))) };
  }
  function check(label, checked, onchange) {
    var input = h("input", { type: "checkbox" });
    input.checked = !!checked;
    input.addEventListener("change", function () { onchange && onchange(input.checked); });
    return { input: input, el: h("label", { "class": P + "check" }, input, h("span", { "class": P + "check__box" }), h("span", { text: label })) };
  }
  function field(label, input, extra) {
    return h("div", { "class": P + "field" }, h("label", { "class": P + "field__label", text: label }), h("div", { "class": P + "field__line" }, input, extra));
  }
  function select(options, value) {
    var s = h("select", { "class": P + "select" }, options.map(function (o) {
      var v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o;
      return h("option", { value: v, text: t });
    }));
    s.value = value; return s;
  }

  /* ───────── shell ───────── */
  (function nav() {
    var toggle = $("#navToggle"), drawer = $("#mobileSidebar"), overlay = $("#sidebarOverlay"), close = $("#navClose");
    var mq = window.matchMedia("(max-width: 768px)");
    function setDrawer(open) {
      drawer.classList.toggle("open", open); overlay.classList.toggle("open", open);
      drawer.setAttribute("aria-hidden", open ? "false" : "true"); toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    toggle.addEventListener("click", function () {
      if (mq.matches) setDrawer(!drawer.classList.contains("open"));
      else { var hid = document.body.classList.toggle(P + "nav-hidden"); toggle.setAttribute("aria-expanded", hid ? "false" : "true"); }
    });
    overlay.addEventListener("click", function () { setDrawer(false); });
    close.addEventListener("click", function () { setDrawer(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") setDrawer(false); });
    mq.addEventListener("change", function () { setDrawer(false); });
  })();
  api("/api/meta").then(function (m) { var s = $("#sideStorage"); if (s) { s.textContent = m.storage_root; s.title = m.storage_root; } }).catch(function () {});
  function pollBadge() {
    api("/api/jobs").then(function (j) { var b = $("#jobBadge"); if (b) b.hidden = !j.current; }).catch(function () {});
  }
  pollBadge(); setInterval(pollBadge, 4000);

  /* ───────── active project: chosen on the Projects page, kept on the server (so it survives reloads and restarts),
     and targeted by every other page until it is changed or cleared ───────── */
  var ACTIVE = { info: null, listeners: [], ready: null };
  function loadActive() {
    return api("/api/active").then(function (r) {
      ACTIVE.info = r.active;
      if (r.was) toast("The active project \u201c" + r.was + "\u201d no longer exists, so it was cleared.", true);
      activeChanged(); return r.active;
    }).catch(function () { return null; });
  }
  function setActive(name) { return api("/api/active", "POST", { name: name }).then(function (r) { ACTIVE.info = r.active; activeChanged(); return r.active; }); }
  function clearActive() { return api("/api/active", "DELETE").then(function () { ACTIVE.info = null; activeChanged(); }); }
  function activeChanged() { renderActiveBar(); ACTIVE.listeners.forEach(function (fn) { try { fn(ACTIVE.info); } catch (e) { /* a page handler must not break the others */ } }); }
  function renderActiveBar() {
    var bar = $("#activeBar"); if (!bar) return;
    clear(bar); var a = ACTIVE.info;
    bar.className = P + "active" + (a ? "" : " " + P + "active--none");
    if (!a) {
      bar.appendChild(h("span", { "class": P + "active__label", text: "No active project" }));
      bar.appendChild(h("a", { "class": P + "active__link", href: "/projects", text: "Choose one on the Projects page" }));
      return;
    }
    bar.appendChild(h("span", { "class": P + "active__label", text: "Active project" }));
    bar.appendChild(h("strong", { "class": P + "active__name", text: a.name }));
    bar.appendChild(pill(a.latest.Version_Label, "muted")); bar.appendChild(pill(a.latest.Status, kindOf(a.latest.Status)));
    bar.appendChild(h("a", { "class": P + "active__link", href: "/projects", text: "Change" }));
    bar.appendChild(btn("Clear", function () { clearActive().catch(function (e) { toast(e.message, true); }); }));
  }
  ACTIVE.ready = loadActive();

  /* picker: resolves to a path string or null */
  function pickPath(opts) {
    opts = opts || {};
    var modal = $("#pickerModal"), list = $("#pickList"), pathEl = $("#pickPath"), cur = "", parent = "";
    return new Promise(function (resolve) {
      function done(v) { modal.hidden = true; $("#pickChoose").onclick = $("#pickCancel").onclick = $("#pickUp").onclick = null; resolve(v); }
      function load(path) {
        var q = "/api/browse?path=" + encodeURIComponent(path) + (opts.files ? "&files=1&suffix=" + encodeURIComponent(opts.suffix || "") : "");
        api(q).then(function (r) {
          cur = r.path; parent = r.parent; pathEl.textContent = cur || "(roots)";
          clear(list);
          (r.dirs || []).forEach(function (d) {
            var full = cur ? cur + (cur.slice(-1) === "/" ? "" : "/") + d : d;
            list.appendChild(h("li", { text: (cur ? "▸ " : "") + (cur ? d : d), onclick: function () { load(full); } }));
          });
          (r.files || []).forEach(function (f) {
            list.appendChild(h("li", { text: "• " + f, onclick: function () { done(cur + (cur.slice(-1) === "/" ? "" : "/") + f); } }));
          });
          if (!list.firstChild) list.appendChild(h("li", { text: "(empty)" }));
          $("#pickChoose").hidden = !!opts.files || !cur;
        }).catch(function (e) { toast(e.message, true); });
      }
      $("#pickUp").onclick = function () { load(parent || ""); };
      $("#pickCancel").onclick = function () { done(null); };
      $("#pickChoose").onclick = function () { done(cur || null); };
      modal.hidden = false; load(opts.start || "");
    });
  }
  function pathField(label, opts) {
    var input = h("input", { type: "text", "class": P + "input", readonly: true, placeholder: "use Browse", "aria-label": label });
    var b = btn("Browse", function () { pickPath(opts).then(function (p) { if (p) { input.value = p; input.dispatchEvent(new Event("change")); } }); });
    return { input: input, el: field(label, input, b), get: function () { return input.value; }, set: function (v) { input.value = v; } };
  }

  /* job watcher: renders progress into box, resolves with the finished job view */
  function watchJob(jobId, box) {
    return new Promise(function (resolve) {
      var bar = h("div", { "class": P + "progress" }, h("div", { "class": P + "progress__bar", style: "width:15%" }));
      var line = h("p", { "class": P + "muted", text: "starting…" });
      clear(box).appendChild(h("div", null, bar, line)); box.hidden = false;
      var cancelBtn = null;
      function tick() {
        api("/api/jobs/" + jobId).then(function (r) {
          var j = r.job, c = j.current || {};
          var label = c.label || c.stage || c.event || "working";
          if (c.index && c.total) bar.firstChild.style.width = Math.max(5, Math.round(100 * c.index / c.total)) + "%";
          else bar.firstChild.style.width = "55%";
          line.textContent = j.state === "running" ? (c.stage ? c.stage + ": " : "") + label : j.state;
          if (j.cancellable && j.state === "running" && !cancelBtn) {
            cancelBtn = btn("Cancel", function () { api("/api/jobs/" + jobId + "/cancel", "POST", {}).then(function () { toast("cancel requested"); }).catch(function (e) { toast(e.message, true); }); });
            box.appendChild(cancelBtn);
          }
          if (j.state === "running") setTimeout(tick, 600);
          else { bar.firstChild.style.width = "100%"; if (cancelBtn) cancelBtn.remove(); resolve(j); }
        }).catch(function (e) { toast(e.message, true); resolve({ state: "failed", error: e.message }); });
      }
      tick();
    });
  }

  /* ───────── result renderers ───────── */
  function renderRun(r, root) {
    var s = r.stats || {};
    var title = (r.dry_run ? "[preview] " : "") + r.project_name + " V" + r.project_version + "-PKG" + r.package_version;
    root.appendChild(h("p", null, h("strong", { text: title }), " ", pill(r.mode), " ", pill(r.tracker_source + " tracker")));
    root.appendChild(muted("uuid " + r.versioned_uuid));
    if (r.stats) root.appendChild(stats([["files", s.files], ["empty dirs", s.empty_dirs], ["binary", s.binary_files], ["filtered dirs", s.excluded_dirs],
      ["filtered files", s.excluded_files], ["size", bytes(s.total_bytes)]]));
    (r.warnings || []).forEach(function (w) { root.appendChild(banner(w)); });
    if (r.stats && r.stats.skipped.count) { root.appendChild(h("p", { text: "Skipped (" + r.stats.skipped.count + ")" })); root.appendChild(listBox(r.stats.skipped.items)); }
    if (r.stats && r.stats.ignored_binary.count) { root.appendChild(h("p", { text: "Binary ignored (" + r.stats.ignored_binary.count + ")" })); root.appendChild(listBox(r.stats.ignored_binary.items)); }
    if (r.scoop) {
      var sc = r.scoop;
      root.appendChild(h("p", null, h("strong", { text: "Scoop: " }), (sc.dry_run ? "would remove " : "removed ") + sc.removed.count + " file(s), " + sc.dirs_removed + " empty dir(s)" + (sc.root_removed ? ", source folder removed" : "") + "; " + sc.residual.count + " left in place"));
      if (sc.removed.count) root.appendChild(listBox(sc.removed.items));
      if (sc.residual.count) { root.appendChild(h("p", { text: "Left in place" })); root.appendChild(listBox(sc.residual.items)); }
      if (sc.changed_since_snapshot.count) root.appendChild(banner("Changed since snapshot, NOT removed: " + sc.changed_since_snapshot.items.join(", ")));
    }
  }
  function renderZip(z, root) {
    root.appendChild(h("p", null, h("strong", { text: z.zip }), " ", pill(z.files + " files"), " ", pill(z.zip_deleted ? "zip deleted" : z.dry_run ? "preview" : "zip kept", z.zip_deleted ? "ok" : "muted")));
    root.appendChild(muted("sha256 " + z.sha256));
    if (z.zip_kept_reason) root.appendChild(banner("Zip kept: " + z.zip_kept_reason));
    (z.warnings || []).forEach(function (w) { root.appendChild(banner(w)); });
    if (z.run) renderRun(z.run, root);
  }

  /* ───────── chunk: ONE page for track / chunk / track+chunk / scoop / zip intake ───────── */
  function radioGroup(name, options, value, onchange) {
    var wrap = h("div", { "class": P + "radios " + P + "radios--inline", role: "radiogroup", "aria-label": name }), inputs = {};
    options.forEach(function (o) {
      var input = h("input", { type: "radio", name: name, value: o[0] });
      input.checked = o[0] === value;
      input.addEventListener("change", function () { if (input.checked && onchange) onchange(o[0]); });
      inputs[o[0]] = input;
      wrap.appendChild(h("label", { "class": P + "radio" }, input, h("span", { "class": P + "radio__box" },
        h("span", { "class": P + "radio__title", text: o[1] }), o[2] ? h("span", { "class": P + "radio__desc", text: o[2] }) : null)));
    });
    return { el: wrap, get: function () { var k; for (k in inputs) { if (inputs[k].checked) return k; } return value; },
             set: function (v) { if (inputs[v]) inputs[v].checked = true; } };
  }
  function group(label, node) { return h("div", { "class": P + "field" }, h("span", { "class": P + "field__label", text: label }), node); }
  function shq(s) { return /[^\w@%+=:,.\/-]/.test(s) ? "'" + s.replace(/'/g, "'\\''") + "'" : s; }
  function csv(s) { return String(s || "").split(",").map(function (x) { return x.trim(); }).filter(Boolean); }

  function initChunk() {
    var root = $("#opRoot"), PHRASE = "DELETE FILTERED";
    var state = { planId: "", keep: {}, filters: [], projectName: "", defaultMb: "25", zipConfirm: null, nameInput: null, backup: null, phrase: null, note: null };
    var seq = 0, busy = false;            // seq drops stale preview replies; busy locks the buttons during a run
    var INTRO = {
      track: "Detect or create the tracker for the folder and register it. Writes only chunker storage.",
      chunk: "Snapshot the folder into a BEJSON snapshot. Writes only chunker storage; the folder is not touched. The snapshot is not registered.",
      both: "Track and snapshot in one go (the usual choice). Writes only chunker storage; the folder is not touched.",
      scoop: "Track + chunk, verify the snapshot against the folder byte-for-byte, then REMOVE the archived files from the folder.",
      zip: "Validate the zip, extract it to staging, track + chunk + verified capture. The zip is deleted only when everything was captured."
    };
    var RUNLABEL = { track: "Run track", chunk: "Run chunk", both: "Run track + chunk", scoop: "Scoop (remove archived files)", zip: "Import zip" };
    var qm = new URLSearchParams(location.search).get("mode");
    var startZip = qm === "zip", startAction = /^(track|chunk|both|scoop)$/.test(qm || "") ? qm : "both";

    var source = radioGroup("source", [["folder", "Folder", "A project directory."], ["zip", "Zip file", "A .zip archive of a project."]], startZip ? "zip" : "folder", onSource);
    var action = radioGroup("action", [["track", "Track", "Register the project only."], ["chunk", "Chunk", "Snapshot only."],
      ["both", "Track + chunk", "Register and snapshot."], ["scoop", "Scoop", "Snapshot, verify, then remove the files."]], startAction, onMode);
    var folder = pathField("Folder", {}), zipf = pathField("Zip file", { files: true, suffix: ".zip" });
    var intro = muted(""), preview = h("div"), result = h("div"), jobBox = h("div", { hidden: true }), confirmBox = h("div");
    var binary = radioGroup("binary", [["inherit", "Inherit", "manifest / config"], ["true", "On", "store as base64"], ["false", "Off", "ignore binaries"]], "inherit", invalidate);
    var maxmb = h("input", { type: "number", min: "0", step: "1", "class": P + "input " + P + "input--short", value: "25", "aria-label": "Max file size MB" });
    var ext = h("input", { type: "text", "class": P + "input", placeholder: "py,js (empty = all)" });
    var extra = h("input", { type: "text", "class": P + "input", placeholder: "extra dir names to exclude, comma-separated" });
    var useFilters = check("Use filters (untick to capture everything)", !startZip, function () { syncFilters(); invalidate(); });
    var filterBox = h("div", { "class": P + "filters" });
    var deleteFiltered = check("Also delete filtered content: permanent, never archived, needs a second confirmation", false, function () { state.keep = {}; invalidate(); });
    deleteFiltered.el.className += " " + P + "check--danger";
    var keepZip = check("Keep the zip even after a verified capture", false, invalidate);
    var applyZip = radioGroup("applyzip", [["inherit", "Filter config", "use apply_to_zip"], ["true", "Apply filters", "filtered content is lost"], ["false", "Capture everything", "nothing left out"]], "inherit", invalidate);
    var activeNote = h("div", { "class": P + "callout", hidden: true });
    var idName = h("input", { type: "text", "class": P + "input", placeholder: "keep what the tracker / folder says", autocomplete: "off", "aria-label": "Project name override" });
    var idVer = h("input", { type: "text", "class": P + "input", placeholder: "keep what the tracker says", autocomplete: "off", "aria-label": "Version override" });
    var idPkg = h("input", { type: "text", "class": P + "input", placeholder: "keep what the tracker says", autocomplete: "off", "aria-label": "Package override" });
    var idFromName = check("Take V#-PKG# from the zip's file name when the tracker inside is missing or only has scaffold defaults (1.0.0 / 100)", false, invalidate);
    var onConflict = radioGroup("conflict", [["backup", "Replace, keep the previous", "the old snapshot is copied to superseded/"], ["refuse", "Refuse", "write nothing"]], "backup", invalidate);
    var useActiveName = btn("Use active project", function () { if (ACTIVE.info) { idName.value = ACTIVE.info.name; invalidate(); } });
    var identityPanel = h("div", { "class": P + "panel" }, h("div", { "class": P + "subtitle", text: "Identity and conflicts" }),
      muted("Leave blank to use the tracker. Anything entered here wins over the tracker, the folder name and a zip file name."),
      field("Project name", idName, useActiveName), field("Version", idVer), field("Package", idPkg), idFromName.el,
      group("A version already stored with different content", onConflict.el));
    var scoopPanel = h("div", { "class": P + "panel" }, h("div", { "class": P + "subtitle", text: "Scoop" }), deleteFiltered.el);
    var zipPanel = h("div", { "class": P + "panel" }, h("div", { "class": P + "subtitle", text: "Zip" }), keepZip.el, group("Filters for this zip", applyZip.el));
    var cliLine = h("pre", { "class": P + "pre", "aria-label": "Equivalent CLI command" });
    var previewBtn = btn("Preview (dry run)", doPreview), runBtn = btn("Run", run, "primary");
    var actionSection = section("Action", action.el);

    function maxVal() { var v = parseFloat(maxmb.value); return isFinite(v) && v >= 0 ? v : parseFloat(state.defaultMb); }
    function isZip() { return source.get() === "zip"; }
    function mode() { return isZip() ? "zip" : action.get(); }
    function opName() { var m = mode(); return m === "both" ? "chunk" : m; }
    function target() { return isZip() ? zipf : folder; }
    function filterDiff() {
      var on = [], off = [];
      if (useFilters.input.checked) state.filters.forEach(function (f) {
        if (f.group === "switch" || !f._input) return;
        if (f._input.checked && f.enabled !== true) on.push(f.setting_id);
        if (!f._input.checked && f.enabled === true) off.push(f.setting_id);
      });
      return { on: on, off: off };
    }
    function syncFilters() { Array.prototype.forEach.call(filterBox.querySelectorAll("input"), function (i) { i.disabled = !useFilters.input.checked; }); }
    function layout() {
      var zip = isZip(), m = mode();
      folder.el.hidden = zip; zipf.el.hidden = !zip; actionSection.hidden = zip;
      scoopPanel.hidden = m !== "scoop"; zipPanel.hidden = !zip; idFromName.el.hidden = !zip;
      intro.textContent = INTRO[m]; runBtn.textContent = RUNLABEL[m];
    }
    function body() {
      var d = filterDiff(), m = mode();
      var b = { target: target().get(), binary: binary.get(), max_file_mb: maxVal(), ext: ext.value, extra_excludes: extra.value,
        use_filters: useFilters.input.checked, filter_on: d.on, filter_off: d.off };
      if (opName() === "chunk") b.track = m === "both";
      if (idName.value.trim()) b.project_name = idName.value.trim();
      if (idVer.value.trim()) b.project_version = idVer.value.trim();
      if (idPkg.value.trim()) b.package_version = idPkg.value.trim();
      if (isZip() && idFromName.input.checked) b.identity_from_filename = true;
      if (onConflict.get() !== "backup") b.on_conflict = onConflict.get();
      if (m === "scoop") {
        b.delete_filtered = deleteFiltered.input.checked;
        b.keep_filtered = Object.keys(state.keep).filter(function (k) { return state.keep[k]; });
      }
      if (m === "zip") { b.keep_zip = keepZip.input.checked; if (applyZip.get() !== "inherit") b.apply_filters_to_zip = applyZip.get() === "true"; }
      return b;
    }
    function cli() {
      var m = mode(), t = target().get() || (isZip() ? "<zip>" : "<folder>"), a = ["python3 CLI/project_chunker.py", shq(t)], d = filterDiff();
      a.push({ track: "--track", chunk: "--chunk", both: "--track --chunk", scoop: "--scoop", zip: "--scoop" }[m]);
      if (binary.get() === "true") a.push("--binary"); else if (binary.get() === "false") a.push("--no-binary");
      csv(ext.value).forEach(function (e) { a.push("--ext " + shq(e.charAt(0) === "." ? e : "." + e)); });
      if (maxVal() !== parseFloat(state.defaultMb)) a.push("--max-file-mb " + maxVal());
      csv(extra.value).forEach(function (x) { a.push("--exclude " + shq(x)); });
      if (!useFilters.input.checked) a.push("--no-filters");
      d.on.forEach(function (i) { a.push("--filter " + shq(i)); });
      d.off.forEach(function (i) { a.push("--no-filter " + shq(i)); });
      if (m === "scoop" && deleteFiltered.input.checked) a.push("--delete-filtered");
      if (m === "zip" && keepZip.input.checked) a.push("--keep-zip");
      if (idName.value.trim()) a.push("--project-name " + shq(idName.value.trim()));
      if (idVer.value.trim()) a.push("--project-version " + shq(idVer.value.trim()));
      if (idPkg.value.trim()) a.push("--package-version " + shq(idPkg.value.trim()));
      if (m === "zip" && idFromName.input.checked) a.push("--identity-from-filename");
      if (onConflict.get() !== "backup") a.push("--on-conflict " + onConflict.get());
      var text = a.join(" ");
      if (m === "zip" && applyZip.get() !== "inherit") text += "\n# zip filter policy has no CLI flag: set apply_to_zip in config/chunker_filters.json";
      cliLine.textContent = text;
    }
    function gate() {
      var m = mode(), ok = !!target().get();
      if (ok && m === "scoop") {
        ok = !!(state.planId && state.nameInput && state.nameInput.value === state.projectName && state.backup && state.backup.input.checked &&
          (!deleteFiltered.input.checked || (state.phrase && state.phrase.value === PHRASE)));
      } else if (ok && m === "zip") {
        ok = !!(state.planId && (keepZip.input.checked || (state.zipConfirm && state.zipConfirm.input.checked)));
      }
      runBtn.disabled = busy || !ok; previewBtn.disabled = busy;
    }
    function resetConfirm() { clear(confirmBox); state.zipConfirm = state.nameInput = state.backup = state.phrase = state.note = null; }
    function invalidate() { seq++; state.planId = ""; clear(preview); resetConfirm(); layout(); cli(); gate(); }
    function onSource() { useFilters.input.checked = !isZip(); syncFilters(); applyActive(); invalidate(); }
    /* the active project is this page's default target: its source folder fills the Folder field until the user picks another */
    function applyActive() {
      var a = ACTIVE.info; clear(activeNote); activeNote.hidden = true;
      if (isZip()) return;
      var cur = folder.get();
      if (!a) { if (cur && cur === state.activeFill) { folder.set(""); state.activeFill = ""; invalidate(); } return; }
      if (a.source_path) {
        if (!cur || cur === state.activeFill) { folder.set(a.source_path); state.activeFill = a.source_path; cur = a.source_path; invalidate(); }
        if (cur === a.source_path) activeNote.appendChild(h("span", null, "Target: the active project ", h("strong", { text: a.name }), "\u2019s source folder."));
        else activeNote.appendChild(h("span", null, "This folder is not the active project \u201c" + a.name + "\u201d\u2019s source folder; the run targets the folder you chose. ",
          btn("Use the active project\u2019s folder", function () { folder.set(a.source_path); state.activeFill = a.source_path; applyActive(); invalidate(); })));
      } else {
        activeNote.appendChild(h("span", null, "The active project ", h("strong", { text: a.name }), " has no source folder on disk (it was never tracked from one, or the folder is gone or outside the allowed roots). Choose a folder."));
      }
      activeNote.hidden = false;
    }
    function onMode() { invalidate(); }
    function keepChanged() {
      state.planId = "";
      if (!state.note) { state.note = muted("Selection changed: run Preview again, then confirm."); confirmBox.appendChild(state.note); }
      gate();
    }

    function renderZipConfirm() {
      if (keepZip.input.checked) return;
      var c = check("I understand the zip will be deleted after a verified, complete capture.", false, gate);
      state.zipConfirm = c; confirmBox.appendChild(c.el);
    }
    function renderScoopConfirm(res) {
      var fm = res.filtered_matches || { dirs: [], files: [] }, n = fm.dirs.length + fm.files.length;
      if (n) {
        confirmBox.appendChild(banner("FILTERS ARE ON: " + fm.dirs.length + " dir(s) and " + fm.files.length + " file(s) match a filter. They are NOT archived and will be LEFT IN PLACE, so the source folder stays. Deleting them is optional and permanent (checkbox above)."));
        if (deleteFiltered.input.checked) {
          confirmBox.appendChild(h("p", null, h("strong", { text: "Filtered content to delete (untick to keep):" })));
          var wrap = h("div");
          fm.dirs.map(function (d) { return d + "/"; }).concat(fm.files).forEach(function (item) {
            var key = item.replace(/\/$/, "");
            if (state.keep[key] === undefined) state.keep[key] = false;
            wrap.appendChild(check(item, !state.keep[key], function (v) { state.keep[key] = !v; keepChanged(); }).el);
          });
          confirmBox.appendChild(wrap);
        }
      }
      if (deleteFiltered.input.checked) {
        state.phrase = h("input", { type: "text", "class": P + "input", placeholder: "type " + PHRASE, autocomplete: "off" });
        state.phrase.addEventListener("input", gate);
        confirmBox.appendChild(field("Second confirmation (deleting filtered content)", state.phrase));
      }
      state.nameInput = h("input", { type: "text", "class": P + "input", placeholder: "type: " + res.project_name, autocomplete: "off" });
      state.nameInput.addEventListener("input", gate);
      state.backup = check("The project is backed up (or restorable from the snapshot).", false, gate);
      confirmBox.appendChild(field("Type the project name to confirm", state.nameInput));
      confirmBox.appendChild(state.backup.el);
    }

    function doPreview() {
      if (!target().get()) { toast("pick a " + (isZip() ? "zip" : "folder") + " first", true); return; }
      var m = mode(), zip = isZip();
      var my = ++seq; busy = true; clear(preview); resetConfirm(); state.planId = ""; gate();
      api("/api/preview/" + opName(), "POST", body()).then(function (r) {
        if (my !== seq) return;                                   // options changed while the preview ran
        state.planId = r.plan_id; clear(preview);
        if (zip) renderZip(r.result, preview); else renderRun(r.result, preview);
        if (m === "scoop") { state.projectName = r.result.project_name; renderScoopConfirm(r.result); }
        else if (zip) renderZipConfirm();
        gate(); toast("preview ready");
      }).catch(function (e) { if (my === seq) preview.appendChild(banner(e.message)); toast(e.message, true); })
        .then(function () { busy = false; gate(); });
    }
    function run() {
      var m = mode(), zip = isZip(), op = opName(), b = body(); b.plan_id = state.planId;
      if (m === "scoop") {
        b.confirm_name = state.nameInput.value; b.confirm_backup = state.backup.input.checked;
        if (state.phrase) b.confirm_filtered_text = state.phrase.value;
      }
      if (zip) b.confirm = keepZip.input.checked || !!(state.zipConfirm && state.zipConfirm.input.checked);
      busy = true; gate(); clear(result);
      api("/api/run/" + op, "POST", b).then(function (r) { return watchJob(r.job_id, jobBox); }).then(function (j) {
        clear(result);
        if (j.state === "done") {
          result.appendChild(banner(m + " finished", true));
          if (zip) renderZip(j.result, result); else renderRun(j.result, result);
        } else result.appendChild(banner(m + " " + j.state + (j.error ? ": " + j.error : "")));
        state.planId = ""; clear(preview); resetConfirm();
      }).catch(function (e) { result.appendChild(banner(e.message)); toast(e.message, true); })
        .then(function () { busy = false; gate(); });
    }

    [folder.input, zipf.input].forEach(function (i) { i.addEventListener("change", invalidate); });
    folder.input.addEventListener("change", applyActive);
    [maxmb, ext, extra, idName, idVer, idPkg].forEach(function (i) { i.addEventListener("input", invalidate); });
    maxmb.addEventListener("change", function () { if (!(parseFloat(maxmb.value) >= 0)) { maxmb.value = state.defaultMb; invalidate(); } });
    api("/api/meta").then(function (m) { state.defaultMb = String(m.max_file_mb); maxmb.value = state.defaultMb; cli(); }).catch(function () {});
    api("/api/filters").then(function (r) {
      state.filters = r.rows;
      state.filters.forEach(function (f) {
        if (f.group === "switch") return;
        var c = check(f.setting_id, f.enabled === true, invalidate); f._input = c.input; filterBox.appendChild(c.el);
      });
      syncFilters(); cli();
    }).catch(function (e) { toast(e.message, true); });

    root.appendChild(intro);
    root.appendChild(section("Source", [source.el, activeNote, folder.el, zipf.el]));
    root.appendChild(actionSection);
    root.appendChild(section("Options", [group("Binary files", binary.el), field("Max file size (MB, 0 = no limit)", maxmb),
      field("Only these extensions", ext), field("Extra excluded dir names", extra),
      h("div", { "class": P + "subtitle", text: "Filters (untick one to leave it off for this run only)" }), useFilters.el, filterBox, scoopPanel, zipPanel, identityPanel]));
    root.appendChild(section("Equivalent CLI command", [cliLine, muted("Preview runs the same options as a dry run: it computes everything and writes nothing.")]));
    root.appendChild(h("div", { "class": P + "actions" }, previewBtn, runBtn));
    [preview, confirmBox, jobBox, result].forEach(function (e) { root.appendChild(e); });
    layout(); syncFilters(); cli(); gate();
    ACTIVE.ready.then(applyActive); ACTIVE.listeners.push(applyActive);
  }

  /* ───────── restore ───────── */
  function initRestore() {
    var root = $("#restoreRoot"), state = { planId: "", projects: [] };
    var sel = h("select", { "class": P + "select", "aria-label": "Version" });
    var mode = select([["original", "recorded original location"], ["pick", "choose a folder"]], "original");
    var dest = pathField("Destination folder", {}); dest.el.hidden = true;
    var over = sw("Overwrite existing files", "Off: any collision blocks the restore.", false, inv);
    var tracker = sw("Write bejson_project.json", "Write the version's tracker into the restored folder.", false, inv);
    var preview = h("div"), result = h("div"), jobBox = h("div", { hidden: true });
    var ok = check("Restore these files now.", false, function () { runBtn.disabled = !(state.planId && ok.input.checked); });
    var runBtn = btn("Restore", run, "primary"); runBtn.disabled = true;
    function inv() { state.planId = ""; runBtn.disabled = true; }
    mode.addEventListener("change", function () { dest.el.hidden = mode.value === "original"; inv(); });
    sel.addEventListener("change", inv); dest.input.addEventListener("change", inv);
    function body() { return { selector: sel.value, dest: mode.value === "pick" ? dest.get() : "", overwrite: over.input.checked, write_tracker: tracker.input.checked }; }
    function doPreview() {
      if (!sel.value) { toast("select a version", true); return; }
      if (mode.value === "pick" && !dest.get()) { toast("pick a destination", true); return; }
      clear(preview);
      api("/api/preview/restore", "POST", body()).then(function (r) {
        var p = r.result; state.planId = r.plan_id;
        preview.appendChild(h("p", null, h("strong", { text: p.project_name }), " → " + p.destination + (p.to_original ? " (original location)" : "")));
        preview.appendChild(stats([["files", p.files], ["empty dirs", p.dirs], ["mtimes", p.mtimes], ["collisions", p.collisions.length]]));
        if (p.errors && p.errors.length) { preview.appendChild(banner("Cannot restore: " + p.errors.join("; "))); }
        if (p.type_clashes && p.type_clashes.length) { preview.appendChild(banner("Type clashes: " + p.type_clashes.join(", "))); }
        if (p.collisions.length) { preview.appendChild(h("p", { text: "Existing files that would be overwritten:" })); preview.appendChild(listBox(p.collisions)); }
        if (p.blocked) preview.appendChild(banner("Blocked: " + (over.input.checked ? "resolve the errors above" : "turn on Overwrite or choose an empty destination")));
        else { preview.appendChild(ok.el); }
        runBtn.disabled = !(!p.blocked && ok.input.checked);
        ok.input.onchange = function () { runBtn.disabled = !(!p.blocked && ok.input.checked); };
      }).catch(function (e) { preview.appendChild(banner(e.message)); toast(e.message, true); });
    }
    function run() {
      var b = body(); b.plan_id = state.planId; b.confirm = true; runBtn.disabled = true; clear(result);
      api("/api/run/restore", "POST", b).then(function (r) { return watchJob(r.job_id, jobBox); }).then(function (j) {
        if (j.state === "done") {
          var r = j.result;
          result.appendChild(banner("Restored " + r.files + " file(s), " + r.dirs + " empty dir(s) to " + r.destination, true));
          (r.warnings || []).forEach(function (w) { result.appendChild(banner(w)); });
        } else result.appendChild(banner("restore " + j.state + (j.error ? ": " + j.error : "")));
      }).catch(function (e) { result.appendChild(banner(e.message)); });
    }
    /* the version list follows the active project (its newest version is pre-selected); "all projects" widens it */
    var allProjects = check("Show every project\u2019s versions", false, function () { fillVersions(); inv(); }), loaded = [];
    function fillVersions() {
      var a = ACTIVE.info, keep = sel.value, only = a && !allProjects.input.checked ? a.name : "";
      clear(sel); sel.appendChild(h("option", { value: "", text: "— select —" }));
      loaded.forEach(function (p) { if (only && p.name !== only) return; p.versions.forEach(function (v) {
        sel.appendChild(h("option", { value: v.Versioned_UUID, text: p.name + " " + v.Version_Label + " [" + v.Status + "] " + v.Versioned_UUID.slice(0, 8) }));
      }); });
      if (keep && Array.prototype.some.call(sel.options, function (o) { return o.value === keep; })) sel.value = keep;
      else if (only && sel.options.length > 1) sel.selectedIndex = 1;               // newest version of the active project
    }
    api("/api/projects").then(function (r) {
      loaded = r.projects;
      var want = new URLSearchParams(location.search).get("v");
      ACTIVE.ready.then(function () {
        // a version asked for by link that belongs to another project switches the list to every project
        if (want && ACTIVE.info && !ACTIVE.info.versions.some(function (v) { return v.Versioned_UUID === want; })) allProjects.input.checked = true;
        fillVersions(); if (want) sel.value = want; inv();
      });
    }).catch(function (e) { toast(e.message, true); });
    ACTIVE.listeners.push(function () { if (loaded.length) { fillVersions(); inv(); } });
    root.appendChild(muted("Rebuild a registered version. Files, empty folders and modified times are restored; nothing is overwritten unless you allow it."));
    root.appendChild(section("Version", [field("Version", sel), allProjects.el, field("Destination", mode), dest.el]));
    root.appendChild(section("Options", [over.el, tracker.el]));
    root.appendChild(h("div", { "class": P + "actions" }, btn("Preview", doPreview), runBtn));
    [preview, jobBox, result].forEach(function (e) { root.appendChild(e); });
  }

    /* ───────── projects: collapsible project headers, version list, one editor for every version ───────── */
  function initProjects() {
    var root = $("#projectsRoot"), cards = {}, open = {}, order = [];
    var KEY = "chunker.projects.open";
    try { open = JSON.parse(sessionStorage.getItem(KEY) || "{}") || {}; } catch (e) { open = {}; }
    var wanted = new URLSearchParams(location.search).get("open");
    if (wanted) open[wanted] = true;
    function saveOpen() { try { sessionStorage.setItem(KEY, JSON.stringify(open)); } catch (e) { /* private mode */ } }
    var search = h("input", { type: "search", "class": P + "input", placeholder: "filter by project name", "aria-label": "Filter projects" });
    var list = h("div"), detail = h("div"), counter = muted(""), lastProjects = [];
    var expandBtn = btn("Expand all", function () { setAll(true); }), collapseBtn = btn("Collapse all", function () { setAll(false); });

    function short(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
    function setOpen(name, on) {
      var c = cards[name]; if (!c) return;
      open[name] = on; if (!on) delete open[name];
      c.head.setAttribute("aria-expanded", String(on)); c.body.hidden = !on; c.chev.textContent = on ? "−" : "+";
      if (on && !c.filled) fill(c);
      saveOpen();
    }
    function setAll(on) { order.forEach(function (n) { if (!cards[n].el.hidden) setOpen(n, on); }); }
    function applyFilter() {
      var q = search.value.toLowerCase().trim(), shown = 0;
      order.forEach(function (n) { var m = !q || n.toLowerCase().indexOf(q) >= 0; cards[n].el.hidden = !m; if (m) shown++; });
      counter.textContent = order.length ? shown + " of " + order.length + " project(s)" : "";
    }

    function buildCard(p, idx) {
      var id = "acc-" + idx + "-" + Date.now().toString(36);
      var chev = h("span", { "class": P + "acc__chev", "aria-hidden": "true", text: "+" });
      var latest = p.versions[0] || {};
      var head = h("button", { type: "button", id: id + "-h", "class": P + "acc__head", "aria-expanded": "false", "aria-controls": id },
        h("span", { "class": P + "acc__title", text: p.name }),
        h("span", { "class": P + "acc__meta" }, pill(p.version_count + " version(s)"), pill(latest.Version_Label || "", ""), pill(p.latest_status, kindOf(p.latest_status))), chev);
      var body = h("div", { id: id, "class": P + "acc__body", role: "region", "aria-labelledby": id + "-h", hidden: true });
      // the active-project control sits outside the (button) header so it can be used with the card collapsed
      var activeBtn = btn("Make active", function () {
        var isActive = ACTIVE.info && ACTIVE.info.name === p.name;
        (isActive ? clearActive() : setActive(p.name)).then(function () { toast(isActive ? "No active project" : p.name + " is now the active project"); })
          .catch(function (e) { toast(e.message, true); });
      });
      var activePill = pill("ACTIVE", "ok"); activePill.hidden = true;
      head.querySelector("." + P + "acc__meta").appendChild(activePill);
      var activeRow = h("div", { "class": P + "acc__active" }, activeBtn);
      var c = { p: p, el: h("div", { "class": P + "acc" }, head, activeRow, body), head: head, body: body, chev: chev, filled: false,
                activeBtn: activeBtn, activePill: activePill };
      head.addEventListener("click", function () { setOpen(p.name, body.hidden); });
      return c;
    }

    function fill(c) {
      c.filled = true; clear(c.body);
      var p = c.p, vs = p.versions, tracked = 0, chunked = 0, archived = 0, bytesTotal = 0;
      vs.forEach(function (v) {
        if (v.Status === "tracked") tracked++; else if (v.Status === "chunked") chunked++; else if (v.Status === "archived") archived++;
        if (v.snapshot_state === "ok") bytesTotal += v.snapshot_bytes;
      });
      c.body.appendChild(stats([["versions", vs.length], ["tracked", tracked], ["chunked", chunked], ["archived", archived], ["snapshots", bytes(bytesTotal)]]));
      c.body.appendChild(h("div", { "class": P + "subtitle", text: "Versions" }));
      vs.forEach(function (v) {
        var facts = v.snapshot_state === "ok" ? v.files + " files, " + v.empty_dirs + " empty dirs, " + bytes(v.snapshot_bytes) + " · " + (v.snapshot_date || "") : "snapshot " + v.snapshot_state;
        c.body.appendChild(h("div", { "class": P + "row" }, h("div", { "class": P + "row__info" },
          h("span", { "class": P + "row__label" }, v.Version_Label + "  ", pill(v.Status, kindOf(v.Status))),
          h("span", { "class": P + "row__desc", text: facts }),
          h("span", { "class": P + "row__desc", text: "id " + v.Versioned_UUID.slice(0, 8) + (v.Original_Path ? " · source " + v.Original_Path : "") + (v.Updated_At ? " · updated " + v.Updated_At : "") })),
          h("div", { "class": P + "actions" },
            btn("Files", function () { showFiles(p.name, v); }), btn("Export zip", function () { exportZip(v); }),
            h("a", { "class": P + "btn", href: "/restore?v=" + encodeURIComponent(v.Versioned_UUID), text: "Restore" }))));
      });
      var editorBox = h("div", null, muted("loading details…"));
      c.body.appendChild(h("div", { "class": P + "subtitle", text: "Tracker details (applies to all " + vs.length + " version(s))" }));
      c.body.appendChild(editorBox);
      loadEditor(c, editorBox);
    }

    function loadEditor(c, box) {
      api("/api/projects/" + encodeURIComponent(c.p.name) + "/tracker").then(function (d) { clear(box); buildEditor(c, d, box); })
        .catch(function (e) { clear(box).appendChild(banner(e.message)); });
    }

    function buildEditor(c, d, box) {
      var nVer = c.p.versions.length, items = [], reviewBox = h("div"), jobBox = h("div", { hidden: true }), busy = false, plan = null;
      var summary = muted(""), reviewBtn = btn("Review changes", review, "primary"), applyBtn = btn("", apply, "primary");
      var snaps = check("Also update the tracker copy stored inside each snapshot (Restore writes it back out; rewrites snapshot files)", true, invalidate);
      var nameWarn = banner("Renaming moves this project's tracker database and snapshot files and rewrites every snapshot header. It cannot be undone from here (a backup of the registry and tracker database is kept).");
      nameWarn.hidden = true;
      var customName = h("input", { type: "text", "class": P + "input", placeholder: "new field name, e.g. License", autocomplete: "off", "aria-label": "New field name", maxlength: "64" });
      var customVal = h("input", { type: "text", "class": P + "input", placeholder: "value", autocomplete: "off", "aria-label": "New field value", maxlength: "500" });

      function mkField(f, extra) {
        var multi = f.multiline, initial = f.mixed ? "" : (f.distinct[0] || "");
        var input = multi ? h("textarea", { "class": P + "input " + P + "input--area", rows: "3", maxlength: "4000" })
          : h("input", { type: f.kind === "url" ? "url" : "text", "class": P + "input", autocomplete: "off", spellcheck: "false", maxlength: "500" });
        input.value = initial; input.placeholder = f.mixed ? "differs between versions" : (f.hint || "");
        input.setAttribute("aria-label", f.label);
        var it = { f: f, input: input, initial: initial, forced: false };
        var note = f.mixed ? h("span", { "class": P + "field__note", text: "Versions differ: " + f.distinct.map(function (x) { return x === "" ? "(empty)" : short(x, 60); }).join("  |  ") }) : null;
        var clearBtn = null;
        if (f.kind !== "name" && f.kind !== "path" && (f.mixed || initial)) {
          clearBtn = btn("Clear", function () { input.value = ""; it.forced = true; input.placeholder = "will be cleared on all versions"; changed(); });
        }
        input.addEventListener("input", function () { it.forced = false; changed(); });
        it.el = h("div", { "class": P + "field" }, h("span", { "class": P + "field__label", text: f.label + (extra ? "  (extra)" : "") }),
          h("div", { "class": P + "field__line" }, input, clearBtn), note, f.hint && !f.mixed && f.kind === "name" ? h("span", { "class": P + "field__note", text: f.hint }) : null);
        it.dirty = function () { return it.forced || input.value !== it.initial; };
        items.push(it); return it.el;
      }
      d.editable.forEach(function (f) { box.appendChild(mkField(f, false)); });
      if (d.extras.length) { box.appendChild(h("div", { "class": P + "subtitle", text: "Other tracker fields" })); d.extras.forEach(function (f) { box.appendChild(mkField(f, true)); }); }
      box.appendChild(h("div", { "class": P + "subtitle", text: "Add a field to every version" }));
      box.appendChild(h("div", { "class": P + "field" }, customName, customVal));
      box.appendChild(snaps.el); box.appendChild(nameWarn);
      box.appendChild(muted("Version numbers, version IDs and the global project GUID cannot be changed here."));
      box.appendChild(summary); box.appendChild(h("div", { "class": P + "actions" }, reviewBtn)); box.appendChild(reviewBox); box.appendChild(jobBox);
      changed();

      function collect() {
        var out = {}, n = 0;
        items.forEach(function (it) { if (it.dirty()) { out[it.f.key] = it.input.value; n++; } });
        var cn = customName.value.trim(); if (cn) { out[cn] = customVal.value; n++; }
        return { changes: out, count: n };
      }
      function changed() { invalidate(); }
      function invalidate() {
        plan = null; clear(reviewBox); var s = collect();
        nameWarn.hidden = !(items.some(function (it) { return it.f.key === "Project_Name" && it.dirty(); }));
        summary.textContent = s.count ? s.count + " field(s) changed" : "No changes yet: edit a field above.";
        reviewBtn.disabled = busy || !s.count;
      }
      customName.addEventListener("input", invalidate); customVal.addEventListener("input", invalidate);

      function body() { return { name: c.p.name, changes: collect().changes, update_snapshots: snaps.input.checked }; }
      function review() {
        var b = body(); reviewBtn.disabled = true; clear(reviewBox);
        api("/api/preview/tracker", "POST", b).then(function (r) {
          plan = { id: r.plan_id, body: b, res: r.result }; renderPlan(r.result);
        }).catch(function (e) { reviewBox.appendChild(banner(e.message)); toast(e.message, true); }).then(function () { reviewBtn.disabled = busy || !collect().count; });
      }
      function renderPlan(res) {
        clear(reviewBox);
        if (res.unchanged) { reviewBox.appendChild(banner("Nothing to change: every selected value already matches.", true)); return; }
        var rows = [];
        res.versions.forEach(function (v) {
          if (!v.changes.length) rows.push({ cells: [v.label, "(no change)", "", ""] });
          v.changes.forEach(function (ch) { rows.push({ cells: [v.label, ch.key, ch.old === "" ? "(empty)" : short(ch.old, 80), ch.new === "" ? "(empty)" : short(ch.new, 80)] }); });
        });
        reviewBox.appendChild(h("div", { "class": P + "subtitle", text: "Review: " + res.project + (res.renamed ? " → " + res.new_name : "") }));
        reviewBox.appendChild(table(["Version", "Field", "Before", "After"], rows));
        reviewBox.appendChild(muted("Snapshots to rewrite: " + res.snapshots_to_rewrite + " (" + bytes(res.snapshot_bytes) + ")" + (res.renamed ? " · files move to the new project folder" : "")));
        res.warnings.forEach(function (w) { reviewBox.appendChild(banner(w)); });
        var ok = check("I reviewed these changes and want them applied to all " + res.versions.length + " version(s).", false, function () { applyBtn.disabled = busy || !ok.input.checked; });
        applyBtn.textContent = "Apply to " + res.versions.length + " version(s)"; applyBtn.disabled = true;
        reviewBox.appendChild(ok.el); reviewBox.appendChild(h("div", { "class": P + "actions" }, applyBtn));
        plan.ok = ok;
      }
      function apply() {
        if (!plan || !plan.ok.input.checked) return;
        var b = plan.body; b.plan_id = plan.id; b.confirm = true; busy = true; applyBtn.disabled = true; reviewBtn.disabled = true;
        api("/api/run/tracker", "POST", b).then(function (r) { return watchJob(r.job_id, jobBox); }).then(function (j) {
          busy = false;
          if (j.state !== "done") { clear(reviewBox).appendChild(banner("edit " + j.state + (j.error ? ": " + j.error : "") + ". Nothing was changed."));  invalidate(); return; }
          var r = j.result; toast("saved");
          reloadAfter(r.project, r.new_name, banner("Updated " + r.versions_updated + " of " + r.versions + " version(s): " + r.fields.join(", ") + (r.renamed ? " (renamed to " + r.new_name + ")" : ""), true));
        }).catch(function (e) { busy = false; clear(reviewBox).appendChild(banner(e.message)); toast(e.message, true); invalidate(); });
      }
    }

    function reloadAfter(oldName, newName, msg) {
      Promise.all([api("/api/projects"), loadActive()]).then(function (rs) { return rs[0]; }).then(function (r) {   // a rename keeps the project active
        delete cards[oldName]; delete cards[newName];            // rebuilt from fresh data; other projects keep their open editors
        if (oldName !== newName) { delete open[oldName]; }
        open[newName] = true; saveOpen(); sync(r.projects);
        clear(detail).appendChild(msg);
        if (cards[newName]) cards[newName].el.scrollIntoView({ behavior: "smooth", block: "start" });
      }).catch(function (e) { toast(e.message, true); });
    }

    function exportZip(v) {
      toast("building zip…");
      api("/api/projects/" + v.Versioned_UUID + "/export", "POST", {}).then(function (r) {
        clear(detail).appendChild(banner("Export ready: " + r.name + " (" + bytes(r.size) + ")", true));
        detail.appendChild(h("a", { "class": P + "btn " + P + "btn--primary", href: "/api/download/" + encodeURIComponent(r.name), text: "Download" }));
      }).catch(function (e) { toast(e.message, true); });
    }
    function showFiles(name, v) {
      var q = h("input", { type: "search", "class": P + "input", placeholder: "filter files", "aria-label": "Filter files" });
      var tbl = h("div"), pre = h("div");
      function load() {
        api("/api/projects/" + v.Versioned_UUID + "/files?q=" + encodeURIComponent(q.value)).then(function (r) {
          clear(tbl).appendChild(table(["Path", "Kind", "Size", "Modified"], r.rows.map(function (f) { return { f: f, cells: [f.path, f.kind, f.kind === "dir" ? "" : bytes(f.size), f.modified] }; }), function (row) {
            if (row.f.kind === "dir") return;
            api("/api/projects/" + v.Versioned_UUID + "/file?path=" + encodeURIComponent(row.f.path)).then(function (x) {
              clear(pre).appendChild(h("p", null, h("strong", { text: x.path }), " " + x.kind + " " + bytes(x.size)));
              pre.appendChild(x.kind === "binary" ? muted("binary file: not previewed") : h("pre", { "class": P + "pre", text: x.text + (x.truncated ? "\n… truncated" : "") }));
            }).catch(function (e) { toast(e.message, true); });
          }));
          if (r.truncated) tbl.appendChild(muted("Showing " + r.shown + " of " + r.total + "; filter to narrow."));
        }).catch(function (e) { toast(e.message, true); });
      }
      q.addEventListener("input", function () { clearTimeout(q._t); q._t = setTimeout(load, 250); });
      clear(detail).appendChild(section(name + " " + v.Version_Label, [q, tbl, pre])); load();
      detail.scrollIntoView({ behavior: "smooth" });
    }

    // Rebuilds only cards whose data are new; existing cards (and any unsaved edits in them) are reused.
    function sync(projects) {
      lastProjects = projects;
      var next = {}; order = [];
      var act = ACTIVE.info && ACTIVE.info.name;
      projects = projects.slice().sort(function (a, b) { return (b.name === act) - (a.name === act); });       // the active project is pinned first
      projects.forEach(function (p, i) {
        order.push(p.name);
        if (cards[p.name]) { cards[p.name].p = p; next[p.name] = cards[p.name]; }
        else { next[p.name] = buildCard(p, i); }
      });
      Object.keys(cards).forEach(function (n) { if (!next[n]) cards[n].el.remove(); });
      cards = next; clear(list);
      order.forEach(function (n) { list.appendChild(cards[n].el); });
      if (!order.length) list.appendChild(muted("No projects yet. Use Chunk to add one."));
      order.forEach(function (n) { if (open[n]) setOpen(n, true); });
      markActive(); applyFilter();
    }
    function markActive() {
      var act = ACTIVE.info && ACTIVE.info.name;
      order.forEach(function (n) {
        var c = cards[n], on = n === act;
        c.activePill.hidden = !on; c.el.classList.toggle(P + "acc--active", on);
        c.activeBtn.textContent = on ? "Active project \u2713 \u00b7 click to clear" : "Make active";
        c.activeBtn.className = P + "btn" + (on ? " " + P + "btn--primary" : "");
      });
    }

    search.addEventListener("input", applyFilter);
    root.appendChild(field("Search", search));
    root.appendChild(h("div", { "class": P + "actions" }, expandBtn, collapseBtn));
    root.appendChild(counter); root.appendChild(list); root.appendChild(detail);
    Promise.all([api("/api/projects"), ACTIVE.ready]).then(function (rs) {
      sync(rs[0].projects);
      if (ACTIVE.info && !open[ACTIVE.info.name]) setOpen(ACTIVE.info.name, true);        // show the active project's details
    }).catch(function (e) { toast(e.message, true); });
    ACTIVE.listeners.push(function () { if (lastProjects.length) sync(lastProjects); });
  }

  /* ───────── dashboard ───────── */
  function initDashboard() {
    var root = $("#dashRoot");
    api("/api/dashboard").then(function (d) {
      var act = d.active;
      root.appendChild(section("Active project", act ? [
        h("p", null, h("strong", { text: act.name }), "  ", pill(act.latest.Version_Label, "muted"), " ", pill(act.latest.Status, kindOf(act.latest.Status)), "  " + act.version_count + " version(s)"),
        muted(act.source_exists ? "Source folder: " + act.source_path : "No source folder on disk for this project."),
        h("div", { "class": P + "actions" },
          h("a", { "class": P + "btn", href: "/chunk", text: "Chunk" }), h("a", { "class": P + "btn", href: "/restore", text: "Restore" }),
          h("a", { "class": P + "btn", href: "/verify", text: "Verify" }), h("a", { "class": P + "btn", href: "/projects", text: "Details" }))
      ] : [muted("None chosen. Open the Projects page and press Make active on a project; every other page then targets it."),
           h("div", { "class": P + "actions" }, h("a", { "class": P + "btn " + P + "btn--primary", href: "/projects", text: "Choose a project" }))]));
      root.appendChild(stats([["projects", d.projects], ["versions", d.versions], ["filters on", d.filters_on + "/" + d.filters_total], ["migrator ledger", d.ledger]]));
      var st = Object.keys(d.status).map(function (k) { return pill(k + ": " + d.status[k], kindOf(k)); });
      root.appendChild(section("Status", h("p", null, st.length ? st : muted("nothing registered yet"))));
      root.appendChild(section("Storage", muted(d.storage_root)));
      root.appendChild(section("Recent jobs", d.recent.length ? table(["When", "Op", "State", "Summary"], d.recent.map(function (j) { return { cells: [j.finished || j.started, j.op, pill(j.state, kindOf(j.state)), j.summary || j.error] }; })) : muted("no jobs yet")));
      root.appendChild(section("Start", h("div", { "class": P + "actions" }, [["Chunk", "/chunk"], ["Scoop", "/chunk?mode=scoop"], ["Zip intake", "/chunk?mode=zip"], ["Restore", "/restore"]].map(function (l) { return h("a", { "class": P + "btn", href: l[1], text: l[0] }); }))));
    }).catch(function (e) { root.appendChild(banner(e.message)); });
  }

  /* ───────── verify ───────── */
  function initVerify() {
    var root = $("#verifyRoot"), out = h("div"), box = h("div", { hidden: true });
    var scope = radioGroup("scope", [["active", "Active project", "only findings that concern it"], ["all", "Whole storage", "every tracker, registry row and snapshot"]], "all", function () {});
    var scopeBox = section("Scope", scope.el);
    function syncScope() {                                      // default: the active project when there is one
      var a = ACTIVE.info; scopeBox.hidden = !a;
      if (!a) scope.set("all"); else if (!syncScope.touched) scope.set("active");
    }
    var b = btn("Verify storage", function () {
      b.disabled = true; clear(out);
      api("/api/run/verify", "POST", { scope: ACTIVE.info ? scope.get() : "all" }).then(function (r) { return watchJob(r.job_id, box); }).then(function (j) {
        if (j.state !== "done") { out.appendChild(banner("verify " + j.state + ": " + (j.error || ""))); return; }
        var r = j.result;
        out.appendChild(banner(r.errors + " error(s), " + r.warnings + " warning(s)", r.errors === 0));
        if (r.issues.length) out.appendChild(table(["Severity", "Path", "Message"], r.issues.map(function (i) { return { cells: [pill(i.severity, i.severity === "error" ? "bad" : "muted"), i.path, i.message] }; })));
      }).catch(function (e) { out.appendChild(banner(e.message)); }).then(function () { b.disabled = false; });
    }, "primary");
    root.appendChild(muted("Reads every tracker, registry row and snapshot, re-hashes content and reports problems. Read-only."));
    root.appendChild(scopeBox);
    root.appendChild(h("div", { "class": P + "actions" }, b)); root.appendChild(box); root.appendChild(out);
    scope.el.addEventListener("change", function () { syncScope.touched = true; });
    ACTIVE.ready.then(syncScope); ACTIVE.listeners.push(syncScope);
  }

  /* ───────── filters ───────── */
  function initFilters() {
    var root = $("#filtersRoot"), rows = [], holder = h("div"), msg = h("div");
    function load() { return api("/api/filters").then(function (r) { rows = r.rows; render(r.file); }).catch(function (e) { toast(e.message, true); }); }
    function render(file) {
      clear(holder);
      holder.appendChild(muted("Config file: " + file));
      var switches = rows.filter(function (r) { return r.group === "switch"; });
      holder.appendChild(section("Switches", switches.map(function (r) { return sw(r.setting_id, r.description, r.enabled === true, function (v) { r.enabled = v; }).el; })));
      ["dirs", "files"].forEach(function (g) {
        var nodes = rows.filter(function (r) { return r.group === g; }).map(function (r) {
          var pat = h("input", { type: "text", "class": P + "input", value: r.patterns, "aria-label": r.setting_id + " patterns" });
          pat.addEventListener("input", function () { r.patterns = pat.value; });
          var s = sw(r.setting_id + (r.builtin ? "" : " (custom)"), r.description, r.enabled === true, function (v) { r.enabled = v; });
          var del = r.builtin ? null : btn("Remove", function () { rows = rows.filter(function (x) { return x !== r; }); render(file); });
          return h("div", null, s.el, h("div", { "class": P + "field__line" }, pat, del));
        });
        holder.appendChild(section(g === "dirs" ? "Directory filters" : "File filters", nodes));
      });
      var nid = h("input", { type: "text", "class": P + "input", placeholder: "custom id (letters, digits, _ -)" });
      var ng = select([["dirs", "directory"], ["files", "file"]], "dirs");
      var np = h("input", { type: "text", "class": P + "input", placeholder: "patterns, comma-separated" });
      holder.appendChild(section("Add custom filter", [field("Id", nid), field("Type", ng), field("Patterns", np), btn("Add", function () {
        if (!nid.value.trim()) { toast("enter an id", true); return; }
        rows.push({ setting_id: nid.value.trim(), group: ng.value, enabled: true, patterns: np.value, description: "Custom filter.", builtin: false }); render(file);
      })]));
    }
    var testPat = h("input", { type: "text", "class": P + "input", placeholder: "*.log,node_modules" });
    var testPaths = h("textarea", { "class": P + "input", rows: "4", placeholder: "one relative path per line" });
    var testOut = h("div");
    root.appendChild(muted("Filters decide what Chunk/Scoop leave out. Filtered files are not archived. Patterns are case-sensitive globs; a pattern containing / matches the relative path."));
    root.appendChild(holder);
    root.appendChild(h("div", { "class": P + "actions" },
      btn("Save", function () {
        api("/api/filters", "POST", { rows: rows.map(function (r) { return { setting_id: r.setting_id, group: r.group, enabled: r.enabled === true, patterns: r.patterns, description: r.description }; }) })
          .then(function (r) { rows = r.rows; toast("filters saved"); load(); }).catch(function (e) { toast(e.message, true); clear(msg).appendChild(banner(e.message)); });
      }, "primary"),
      btn("Reload", load),
      btn("Reset to defaults", function () {
        if (!confirm("Replace the filter file with the built-in defaults?")) return;
        api("/api/filters/reset", "POST", { confirm: true }).then(function () { toast("reset"); load(); }).catch(function (e) { toast(e.message, true); });
      })));
    root.appendChild(msg);
    root.appendChild(section("Test patterns", [field("Patterns", testPat), field("Paths", testPaths), btn("Test", function () {
      api("/api/filters/test", "POST", { patterns: testPat.value, paths: testPaths.value.split("\n") }).then(function (r) {
        clear(testOut).appendChild(table(["Path", "Result"], r.results.map(function (x) { return { cells: [x.path, pill(x.match ? "filtered" : "kept", x.match ? "bad" : "ok")] }; })));
      }).catch(function (e) { toast(e.message, true); });
    }), testOut]));
    load();
  }

  /* ───────── jobs ───────── */
  function initJobs() {
    var root = $("#jobsRoot"), cur = h("div"), hist = h("div"), detail = h("div");
    function load() {
      api("/api/jobs").then(function (r) {
        clear(cur);
        if (r.current) cur.appendChild(banner("Running: " + r.current.label + " — " + ((r.current.current || {}).label || (r.current.current || {}).stage || "working"), true));
        clear(hist).appendChild(r.history.length ? table(["Started", "Op", "State", "Summary"], r.history.map(function (j) { return { j: j, cells: [j.started, j.op, pill(j.state, kindOf(j.state)), j.summary || j.error] }; }), function (row) {
          api("/api/jobs/" + row.j.job_id).then(function (x) { clear(detail).appendChild(h("pre", { "class": P + "pre", text: JSON.stringify(x.job, null, 2) })); })
            .catch(function () { clear(detail).appendChild(h("pre", { "class": P + "pre", text: JSON.stringify(row.j, null, 2) })); });
        }) : muted("no jobs recorded"));
      }).catch(function (e) { toast(e.message, true); });
    }
    root.appendChild(cur); root.appendChild(section("History", hist)); root.appendChild(detail);
    load(); setInterval(load, 4000);
  }

  /* ───────── settings ───────── */
  function initSettings() {
    var root = $("#settingsRoot"), inputs = {};
    api("/api/settings").then(function (r) {
      root.appendChild(muted("File: " + r.file + ". Changes marked ↻ apply after restarting the app."));
      Object.keys(r.spec).forEach(function (k) {
        var s = r.spec[k], v = r.values[k], needs = r.restart_needed.indexOf(k) >= 0 ? " ↻" : "", node;
        if (s.kind === "bool") { var x = sw(k + needs, s.description, v === "true"); inputs[k] = function () { return x.input.checked ? "true" : "false"; }; node = x.el; }
        else if (s.kind.indexOf("choice:") === 0) { var sl = select(s.kind.slice(7).split("|"), v); inputs[k] = function () { return sl.value; }; node = field(k + needs, sl, h("span", { "class": P + "muted", text: s.description })); }
        else { var i = h("input", { type: s.kind === "int" ? "number" : "text", "class": P + "input", value: v }); inputs[k] = function () { return i.value; }; node = field(k + needs, i, null); node.appendChild(muted(s.description)); }
        root.appendChild(node);
      });
      root.appendChild(h("div", { "class": P + "actions" }, btn("Save", function () {
        var vals = {}; Object.keys(inputs).forEach(function (k) { vals[k] = inputs[k](); });
        api("/api/settings", "POST", { values: vals }).then(function () { toast("settings saved"); }).catch(function (e) { toast(e.message, true); });
      }, "primary")));
    }).catch(function (e) { root.appendChild(banner(e.message)); });
  }

  /* ───────── migrator ───────── */
  function initMigrator() {
    var root = $("#migRoot"), tabs = {}, panels = {}, cfgState = null, meta = null;
    var names = [["overview", "Overview"], ["config", "Config"], ["plan", "Plan"], ["run", "Run"], ["ledger", "Ledger"]];
    var tabBar = h("div", { "class": P + "tabs", role: "tablist" });
    names.forEach(function (n) {
      panels[n[0]] = h("div", { "class": P + "panel", hidden: true });
      tabs[n[0]] = h("button", { type: "button", role: "tab", "class": P + "tab", text: n[1], onclick: function () { show(n[0]); } });
      tabBar.appendChild(tabs[n[0]]);
    });
    root.appendChild(tabBar); names.forEach(function (n) { root.appendChild(panels[n[0]]); });
    function show(k) {
      names.forEach(function (n) { panels[n[0]].hidden = n[0] !== k; tabs[n[0]].className = P + "tab" + (n[0] === k ? " " + P + "tab--active" : ""); });
      if (k === "ledger") loadLedger(); if (k === "plan") loadPlan(); if (k === "overview") loadOverview();
    }
    function loadOverview() {
      api("/api/migrator/source").then(function (s) {
        clear(panels.overview).appendChild(muted("Bulk-migrates a folder of project zips into the chunker: chunk phase captures each zip (zips untouched), verify phase compares every snapshot to its zip and then applies the original-zip disposition."));
        panels.overview.appendChild(section("Source", h("p", null, s.path || s.error || "(unresolved)", " ", pill(s.exists ? "exists" : "missing", s.exists ? "ok" : "bad"), " ", pill(s.mode))));
        panels.overview.appendChild(section("Steps", h("ol", { "class": P + "steps" }, ["Config: choose source and switches (Dry_Run is on by default).", "Plan: review the zips found.", "Run: chunk, then verify.", "Ledger: per-zip outcome."].map(function (t) { return h("li", { text: t }); }))));
      }).catch(function (e) { clear(panels.overview).appendChild(banner(e.message)); });
    }
    function loadConfig() {
      api("/api/migrator/config").then(function (r) {
        cfgState = r.config; meta = r; var p = clear(panels.config);
        r.bool_keys.forEach(function (k) { p.appendChild(sw(k, r.bool_help[k], cfgState.flags[k], function (v) { cfgState.flags[k] = v; }).el); });
        Object.keys(r.radio_options).forEach(function (k) {
          var s = select(r.radio_options[k], cfgState.radios[k]); s.addEventListener("change", function () { cfgState.radios[k] = s.value; });
          p.appendChild(field(k, s, h("span", { "class": P + "muted", text: r.radio_help[k] })));
        });
        Object.keys(cfgState.texts).forEach(function (k) {
          var i = h("input", { type: "text", "class": P + "input", value: cfgState.texts[k] }); i.addEventListener("input", function () { cfgState.texts[k] = i.value; });
          var extra = k === "Custom_Source_Root" ? btn("Browse", function () { pickPath({}).then(function (v) { if (v) { i.value = v; cfgState.texts[k] = v; } }); }) : null;
          p.appendChild(field(k, i, extra)); p.appendChild(muted(r.text_help[k] || ""));
        });
        p.appendChild(h("div", { "class": P + "actions" }, btn("Save config", function () {
          api("/api/migrator/config", "POST", cfgState).then(function () { toast("saved"); }).catch(function (e) { toast(e.message, true); });
        }, "primary")));
      }).catch(function (e) { clear(panels.config).appendChild(banner(e.message)); });
    }
    function loadPlan() {
      var p = clear(panels.plan);
      api("/api/migrator/plan").then(function (r) {
        p.appendChild(muted("Source: " + r.source + " — " + r.zips.length + " zip(s)"));
        p.appendChild(table(["Zip", "Size", "Files", "Valid", "Status"], r.zips.map(function (z) { return { cells: [z.rel, bytes(z.size), z.files, pill(z.valid ? "valid" : "invalid: " + z.error, z.valid ? "ok" : "bad"), z.locked ? "locked" : (z.status || "new")] }; })));
      }).catch(function (e) { p.appendChild(banner(e.message)); });
    }
    function loadLedger() {
      var p = clear(panels.ledger);
      api("/api/migrator/ledger").then(function (r) {
        p.appendChild(muted(r.file));
        p.appendChild(table(["Zip", "Status", "Project", "Message", "Updated"], r.rows.map(function (x) { return { cells: [x.Zip_Rel, pill(x.Status, kindOf(x.Status)), x.Project_Name, x.Message, x.Updated_At] }; })));
      }).catch(function (e) { p.appendChild(banner(e.message)); });
    }
    (function buildRun() {
      var p = panels.run, ph = { chunk: check("Chunk phase", true), verify: check("Verify phase", false) };
      var live = check("I confirm a LIVE run (Dry_Run is off in Config).", false);
      var out = h("div"), box = h("div", { hidden: true });
      var go = btn("Run selected phases", function () {
        var phases = Object.keys(ph).filter(function (k) { return ph[k].input.checked; });
        go.disabled = true; clear(out);
        api("/api/run/migrator", "POST", { phases: phases, confirm: live.input.checked }).then(function (r) { return watchJob(r.job_id, box); }).then(function (j) {
          if (j.state === "failed") { out.appendChild(banner(j.error)); return; }
          (j.result ? j.result.phases : []).forEach(function (x) {
            out.appendChild(section(x.phase + (x.dry_run ? " (dry run)" : ""), [stats(Object.keys(x.counts).map(function (k) { return [k, x.counts[k]]; })), x.entries.length ? table(["Zip", "Action", "Outcome", "Detail"], x.entries.map(function (e) { return { cells: [e.Zip_Rel, e.Action, pill(e.Outcome, kindOf(e.Outcome)), e.Detail] }; })) : null, muted("Report: " + x.report)]));
          });
          if (j.state === "cancelled") out.appendChild(banner("Cancelled."));
        }).catch(function (e) { out.appendChild(banner(e.message)); }).then(function () { go.disabled = false; });
      }, "primary");
      p.appendChild(section("Phases", [ph.chunk.el, ph.verify.el, live.el]));
      p.appendChild(h("div", { "class": P + "actions" }, go)); p.appendChild(box); p.appendChild(out);
    })();
    loadConfig(); show("overview");
  }

  var pages = { chunk: initChunk, restore: initRestore, projects: initProjects, dashboard: initDashboard, verify: initVerify,
    filters: initFilters, jobs: initJobs, settings: initSettings, migrator: initMigrator, about: function () {} };
  if (pages[PAGE]) pages[PAGE]();
})();
