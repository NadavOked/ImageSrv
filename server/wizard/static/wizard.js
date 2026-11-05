"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const terminal = new Set([
  "done", "installer-failed", "verify-failed", "hostname-failed",
  "validation-failed", "check-error",
]);
const phaseIndex = {validating: 0, hostname: 1, installing: 2, handoff: 3, verifying: 4, done: 5};
const phaseText = {validating: "בודק את ההגדרות…", hostname: "מגדיר את שם השרת…", installing: "מתקין קבצים ושירותים…", handoff: "מעביר את הקונסולה לפורט 8081…", verifying: "מאמת את מטען האתחול…", done: "ההתקנה הושלמה."};
const retryDelays = [1000, 2000, 5000];
const reconnectLimitMs = 5 * 60 * 1000;

let step = 1;
let initial = null;
let pollTimer = null;
let pollStartedAt = 0;
let pollFailures = 0;
let lastProgress = {};

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data;
  try {
    data = await response.json();
  } catch (_) {
    const error = new Error(`HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  if (!response.ok && !data.errors && !terminal.has(data.state)) {
    const error = new Error(data.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function value(id) {
  return $("#" + id).value;
}

function config() {
  return {
    role: $("input[name=role]:checked")?.value || "",
    primary_url: value("primary_url"),
    interface: $("input[name=interface]:checked")?.value || "",
    mode: $("input[name=mode]:checked")?.value || "",
    address: value("address"),
    netmask: value("netmask"),
    gateway: value("gateway"),
    dns: value("dns"),
    hostname: value("hostname"),
    password: value("password"),
    password_confirm: value("password_confirm"),
    current_password: value("current_password"),
  };
}

function errors(items = {}) {
  $$('[data-error]').forEach((item) => { item.textContent = items[item.dataset.error] || ""; });
}

function show(which) {
  step = which;
  $$(".screen").forEach((item) => item.classList.toggle("active", String(item.dataset.step) === String(which)));
  $$(".steps button").forEach((button, index) => {
    button.classList.toggle("current", index + 1 === which);
    button.classList.toggle("done", typeof which === "number" && index + 1 < which);
    button.disabled = typeof which !== "number" || index + 1 > which;
  });
  const footer = $("#actions");
  footer.hidden = typeof which !== "number" || which === 6;
  if (!footer.hidden) {
    $("#back").disabled = which === 1;
    $("#next").textContent = which === 5 ? "החל התקנה" : "הבא";
    $("#step-label").textContent = `שלב ${which} מתוך 6`;
  }
  if (which === 5) summary();
}

function renderNics(nics, selected) {
  $("#nic-list").innerHTML = nics.map((nic) => `<tr><td><input type="radio" name="interface" value="${esc(nic.name)}" ${nic.name === selected ? "checked" : ""}></td><td class="mono">${esc(nic.name)}</td><td>${esc(nic.link_label || (nic.link === "up" ? "מחובר" : "מנותק"))}</td><td class="mono">${esc(nic.current_ip || "—")}</td><td class="mono">${esc(nic.mac || "—")}</td></tr>`).join("");
}

/* ‏#2422: ‏textContent → innerHTML אינו מקודד גרש כפול או בודד, ו-esc משמש גם בתוך
   value="…" ו-href="…" — שם ‏" סוגר את התכונה. אותה מפה כמו ב-console.js (#1129). */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
function esc(valueToEscape) {
  return String(valueToEscape).replace(/[&<>"'`]/g, (c) => ESC_MAP[c]);
}

function toggle() {
  const secondary = $("input[name=role]:checked")?.value === "secondary";
  $("#primary-fields").classList.toggle("hidden", !secondary);
  const stat = $("input[name=mode]:checked")?.value === "static";
  $("#static-fields").disabled = !stat;
  const password = value("password");
  const tests = {
    length: password.length >= 8,
    letters: /[A-Za-z]/.test(password),
    numbers: /[0-9]/.test(password),
    special: /[!@#$%^&*(),.?":{}|<>]/.test(password),
  };
  Object.entries(tests).forEach(([name, passed]) => $(`[data-rule=${name}]`).classList.toggle("ok", passed));
}

async function validate() {
  const result = await api("/api/wizard/validate", {...config(), step});
  errors(result.errors);
  return result.ok;
}

function summary() {
  const current = config();
  const role = current.role === "secondary" ? `שרת משני · ${esc(current.primary_url)}` : "שרת ראשי";
  const net = current.mode === "dhcp" ? `${esc(current.interface)} · DHCP` : `${esc(current.interface)} · ${esc(current.address)} / ${esc(current.netmask)}`;
  $("#summary").innerHTML = `<table><tbody><tr><th>תפקיד</th><td>${role}</td><td><button type="button" class="summary-change" data-target="1">שינוי</button></td></tr><tr><th>רשת הניהול</th><td dir="ltr">${net}</td><td><button type="button" class="summary-change" data-target="2">שינוי</button></td></tr><tr><th>שם השרת</th><td dir="ltr">${esc(current.hostname)}</td><td><button type="button" class="summary-change" data-target="3">שינוי</button></td></tr><tr><th>משתמש ניהול</th><td dir="ltr">admin · הסיסמה הוגדרה</td><td><button type="button" class="summary-change" data-target="4">שינוי</button></td></tr></tbody></table>`;
  $$(".summary-change").forEach((button) => button.addEventListener("click", () => show(Number(button.dataset.target))));
}

function scheduleProgress(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(progress, delay);
}

function reconnecting(expired = false) {
  const status = $("#reconnect-status");
  status.hidden = false;
  if (expired) {
    status.innerHTML = `החיבור לא חזר בתוך חמש דקות. ההתקנה ממשיכה בשרת; אפשר <a href="${esc(consoleGuess())}">לנסות לפתוח את הקונסולה</a>.`;
  } else {
    status.textContent = "מתחבר מחדש…";
  }
}

function consoleGuess() {
  const current = config();
  const host = current.mode === "static" && current.address ? current.address : current.hostname;
  return lastProgress.console_url || (host ? `https://${host}:8081` : location.origin);
}

function renderProgress(progressData) {
  lastProgress = {...lastProgress, ...progressData};
  $("#reconnect-status").hidden = true;
  $("#output").textContent = progressData.output || "";
  const index = phaseIndex[progressData.state] ?? 2;
  $("#current-step").textContent = phaseText[progressData.state] || "ההתקנה ממשיכה…";
  $("#progress-bar").style.width = `${Math.min(100, (index + 1) * 20)}%`;
  $$("#progress-list li").forEach((item, itemIndex) => {
    item.classList.toggle("done", itemIndex < index);
    item.classList.toggle("current", itemIndex === index);
    item.dataset.mark = itemIndex < index ? "✓" : (itemIndex === index ? "◌" : "×");
  });
}

async function fetchProgress() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch("/api/wizard/progress", {signal: controller.signal});
    if (response.status === 404) return {handoff: true};
    let data;
    try {
      data = await response.json();
    } catch (_) {
      if (response.ok) return {handoff: true};
      throw new Error(`HTTP ${response.status}`);
    }
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    if (typeof data.state !== "string" || typeof data.output !== "string") return {handoff: true};
    return {data};
  } finally {
    clearTimeout(timeout);
  }
}

function handoffDone() {
  finish({
    ...lastProgress,
    state: "done",
    console_url: consoleGuess(),
    user: lastProgress.user || "admin",
    fingerprint: lastProgress.fingerprint || "זמינה בקונסולה",
  });
}

async function progress() {
  if (Date.now() - pollStartedAt >= reconnectLimitMs) {
    reconnecting(true);
    return;
  }
  try {
    const result = await fetchProgress();
    if (result.handoff) {
      handoffDone();
      return;
    }
    pollFailures = 0;
    renderProgress(result.data);
    if (terminal.has(result.data.state)) {
      finish(result.data);
      return;
    }
    scheduleProgress(1000);
  } catch (_) {
    reconnecting();
    const delay = retryDelays[Math.min(pollFailures, retryDelays.length - 1)];
    pollFailures += 1;
    scheduleProgress(delay);
  }
}

async function next() {
  if (step < 5) {
    if (await validate()) show(step + 1);
    return;
  }
  show("progress");
  pollStartedAt = Date.now();
  pollFailures = 0;
  lastProgress = {};
  try {
    const accepted = await api("/api/wizard/apply", config());
    if (accepted.errors) {
      errors(accepted.errors);
      show(5);
      return;
    }
    lastProgress.job = accepted.job;
  } catch (_) {
    reconnecting();
  }
  scheduleProgress(0);
}

function finish(result) {
  clearTimeout(pollTimer);
  if (result.state === "done") {
    $("#console-url").textContent = result.console_url;
    // codeql[js/xss-through-dom] false positive: href is https://<host>:8081 (fixed scheme) or the server's value -- never HTML, never javascript: (#291)
    $("#console-url").href = result.console_url;
    // codeql[js/xss-through-dom] false positive: same as the line above (#291)
    $("#console-button").href = result.console_url;
    $("#console-user").textContent = result.user || "admin";
    $("#fingerprint").textContent = result.fingerprint || "—";
    show(6);
  } else {
    $("#failure").textContent = result.error || "ההתקנה נכשלה.";
    $("#failed-output").textContent = result.output || "";
    show("failed");
  }
}

async function checkPrimary() {
  const box = $("#primary-result");
  box.className = "notice";
  box.textContent = "בודק…";
  try {
    const result = await api("/api/wizard/check-primary", {primary_url: value("primary_url")});
    box.className = `notice ${result.ok ? "good" : "bad"}`;
    box.innerHTML = result.ok ? `<b>השרת הראשי ענה.</b><br>שם: ${esc(result.name)} · גרסה: ${esc(result.version)}<br><span class="mono">${esc(result.fingerprint)}</span>` : `<b>${esc(result.error)}</b><br>${esc(result.reason || result.checked || "")}<br><small>אפשר להמשיך; זהו חיווי אזהרה.</small>`;
  } catch (error) {
    box.className = "notice bad";
    box.textContent = error.message;
  }
}

async function boot() {
  initial = await api("/api/wizard/state");
  const defaults = initial.defaults;
  renderNics(initial.interfaces, defaults.interface);
  $(`input[name=role][value=${defaults.role}]`).checked = true;
  $(`input[name=mode][value=${defaults.mode}]`).checked = true;
  $("#primary_url").value = defaults.primary_url || "";
  $("#hostname").value = defaults.hostname || "imagesrv-server";
  $("#server-name").textContent = defaults.hostname || "";
  $("#current-wrap").classList.toggle("hidden", !initial.rerun);
  toggle();
  const existing = await api("/api/wizard/progress");
  if (terminal.has(existing.state)) {
    finish(existing);
  } else if (existing.job && existing.state !== "ready") {
    show("progress");
    pollStartedAt = Date.now();
    renderProgress(existing);
    scheduleProgress(1000);
  } else {
    show(1);
  }
}

$("#next").addEventListener("click", () => next().catch((error) => errors({_global: error.message})));
$("#back").addEventListener("click", () => show(Math.max(1, step - 1)));
$("#failure-back").addEventListener("click", () => show(5));
$("#failure-retry").addEventListener("click", () => { show(5); next(); });
$("#check-primary").addEventListener("click", checkPrimary);
$("#wizard-form").addEventListener("change", toggle);
$("#password").addEventListener("input", toggle);
$$('.password-eye').forEach((button) => button.addEventListener("click", () => {
  const input = $("#" + button.dataset.password);
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  button.textContent = showing ? "הצג" : "הסתר";
}));
$$('.steps button').forEach((button, index) => button.addEventListener("click", () => {
  if (index + 1 <= step) show(index + 1);
}));
boot().catch((error) => {
  document.body.textContent = `האשף לא הצליח לקרוא את מצב השרת: ${error.message}`;
});
