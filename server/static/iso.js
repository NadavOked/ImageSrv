/* ImageSrv — לשונית "ISO" בדף ספריית האימג'ים (#404; מוקאפ מאושר 07/11:
   docs/design/mockups-1107b/404-iso.html בענף docs/mockups-1107b).

   העלאה, רשימה ומחיקה של קובצי ISO, באותו מבנה כמו האימג'ים: עץ תיקיות מימין, טבלה משמאל, חיפוש.
   ה-API (interfaces.md סעיף 46): ‏GET /isos, ‏POST /isos/upload (גוף = הקובץ; name/folder/filename ב-query),
   ‏POST /isos/{id}/delete, ‏POST /iso-folders, ‏POST /iso-folders/{name}/delete. הכול למנהל, ורק בשרת הראשי.

   מה שאינו כאן, בכוונה: "אתחל מחשב מה-ISO…" ועמודת "אתחול ברשת". איך מגישים ISO לאתחול (memdisk, iPXE או HTTP)
   ואיך זה חי עם Secure Boot ממתין להכרעה (#404) — כפתור שאין מאחוריו דבר היה שקר. לכן גם אין הורדה של ה-ISO.

   מחיקה: כפתור אדום בחלון אישור, בלי הקלדת מילה (#1270). ‏"לא נקרא" (null) אינו "אין ISO" (עיקרון 5).
   משתמש ב-UI/api/post/sheet/toast/esc/ltr מ-console.js. */
"use strict";

let ISOS = null, ISO_FOLDERS = null, isoErr = "", ISO_FOLDER = null, ISO_FILTER = "", ISO_KEY = "";
const ISO_TAB = 3;
const ISO_NAME_RULE = "אותיות אנגליות או עבריות, ספרות, רווח, נקודה, מקף וקו תחתון; עד 48 תווים";

function isoAllowed() { return isAdmin() && !SERVER_CTX; }
function isoTabOpen() { return current === "images" && isoAllowed() && currentTab === ISO_TAB; }
function isoTabs() { return isoAllowed(); }
function isoTabEntry() { return ISOS == null ? "ISO" : ["ISO", ISOS.length]; }

async function loadIsos() {
  let key;
  try {
    const got = await api("/isos");
    ISOS = Array.isArray(got && got.isos) ? got.isos : null;
    ISO_FOLDERS = Array.isArray(got && got.folders) ? got.folders : null;
    isoErr = ISOS == null ? "התשובה מהשרת אינה רשימה" : "";
    key = JSON.stringify([ISOS, ISO_FOLDERS]);
  } catch (e) {
    ISOS = null; ISO_FOLDERS = null; isoErr = e.message;   // "לא נקרא" אינו "אין ISO" (עיקרון 5)
    key = "err:" + e.message;
  }
  if (ISO_FOLDER != null && !(ISO_FOLDERS || []).some((f) => f.name === ISO_FOLDER) && ISO_FOLDER !== "") ISO_FOLDER = null;
  const changed = key !== ISO_KEY;
  ISO_KEY = key;
  if (changed && current === "images") renderCurrent();   // אחרת הבחירה והחיפוש היו נמחקים בכל רענון שלא שינה דבר
}

function isoVisible() {
  let list = ISOS || [];
  if (ISO_FOLDER != null) list = list.filter((m) => (m.folder || "") === ISO_FOLDER);
  const q = ISO_FILTER.trim().toLowerCase();
  return q ? list.filter((m) => [m.name, m.filename, m.folder].some((v) => String(v || "").toLowerCase().includes(q))) : list;
}
function selectIsoFolder(key) {
  ISO_FOLDER = key == null ? null : decodeURIComponent(key);
  if (current === "images") renderCurrent();
}
function isoFilter(value) {
  ISO_FILTER = value || "";
  const box = document.getElementById("iso-table");
  if (box) box.innerHTML = isoTableHtml();
}

/* ‏sha256 מקוצר לתצוגה (ה-64 המלאים ב-tooltip); ‏LTR מבודד. */
function isoShortHash(hash) { return hash && hash.length > 12 ? hash.slice(0, 4) + "…" + hash.slice(-4) : hash || "—"; }

function isoRow(m) {
  const idEnc = encodeId(m.id);
  const verified = m.present
    ? `<span title="${esc(m.sha256)}"><bdi dir="ltr" class="mono">${esc(isoShortHash(m.sha256))}</bdi></span> ${UI.status("ok", "אומת")}`
    : `<span title="${esc(m.sha256)}"><bdi dir="ltr" class="mono">${esc(isoShortHash(m.sha256))}</bdi></span> ${UI.status("err", "הקובץ חסר או השתנה בדיסק")}`;
  return { attrs: `data-id="${esc(m.id)}"`, cells: [
    UI.nameHtml(m.name, m.filename ? `<bdi dir="ltr" class="mono">${esc(m.filename)}</bdi>` : ""),
    ltr(fmtBytes(m.size)), verified,
    ltr(`${fmtDate(m.uploaded_at)} ${fmtClock(m.uploaded_at)}`),
    UI.rowMenu(m.name, [["מחיקה…", `deleteIso('${idEnc}')`, "danger"]]),
  ] };
}

function isoTableHtml() {
  if (ISOS == null) return UI.note("err", `רשימת ה-ISO לא נקראה${isoErr ? ": " + esc(isoErr) : ""}. ״רענון״ מנסה שוב.`);
  if (!ISOS.length) {
    return UI.empty("אין קובצי ISO בספרייה — העלו את הראשון.", `<button class="btn primary" onclick="openIsoUpload()">העלאת ISO</button>`);
  }
  const list = isoVisible();
  if (!list.length) return ISO_FILTER ? UI.empty("אין ISO שתואמים לחיפוש") : UI.empty("גררו אליה ISO, או בחרו אותה בהעלאה הבאה.", "", "התיקייה ריקה");
  return UI.datagrid({ cls: "kebab-col", columns: ["ISO", "גודל", "sha256", "הועלה", { html: `<span class="sr">פעולות</span>` }], opt: [3], rows: list.map(isoRow) });
}

function isoTree() {
  const all = ISOS || [], folders = ISO_FOLDERS || [];
  const node = (label, key, cnt, muted = false) => {
    const on = ISO_FOLDER === key, keyJs = key == null ? "null" : `'${encodeId(key)}'`;
    return `<div class="f${on ? " on" : ""}${muted ? " muted" : ""}" role="treeitem" tabindex="0" aria-selected="${on}" onclick="selectIsoFolder(${keyJs})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectIsoFolder(${keyJs})}">${uiIcon("folder")}<span>${esc(label)}</span><span class="cnt">${cnt}</span></div>`;
  };
  const none = all.filter((m) => !m.folder).length;
  return `<div class="ftree" role="tree" aria-label="תיקיות ISO">${node("כל ה-ISO", null, all.length)}<div class="kids">${folders.map((f) => node(f.name, f.name, f.isos || 0)).join("")}${none && folders.length ? node("ללא תיקייה", "", none, true) : ""}</div></div>`;
}

function isoTabCard() {
  const admin = isAdmin();
  const folder = ISO_FOLDER ? (ISO_FOLDERS || []).find((f) => f.name === ISO_FOLDER) : null;
  const crumb = ISO_FOLDER == null ? `<b>כל ה-ISO</b>`
    : `<a role="link" tabindex="0" onclick="selectIsoFolder(null)">כל ה-ISO</a><span>/</span><b>${esc(ISO_FOLDER === "" ? "ללא תיקייה" : ISO_FOLDER)}</b>`;
  const del = admin && folder && !folder.isos ? `<button class="btn sm danger" onclick="deleteIsoFolder('${encodeId(folder.name)}')">מחק תיקייה</button>` : "";
  const bar = `<div class="dg-bar"><span class="fcrumb">${crumb}</span><span class="sp"></span><input type="search" value="${esc(ISO_FILTER)}" placeholder="חיפוש ISO" aria-label="חיפוש ISO" oninput="isoFilter(this.value)" style="width:180px">${del}</div>`;
  const title = ISO_FOLDER == null ? "ISO" : ISO_FOLDER === "" ? "ללא תיקייה" : ISO_FOLDER;
  return `<div class="c12 card"><div class="card-h"><span>${esc(title)}</span></div><div class="split"><div class="side">${isoTree()}</div><div>${bar}<div id="iso-table">${isoTableHtml()}</div>`
    + `<div class="muted" style="margin:10px 4px">ISO הוא קובץ אחד שמוגש כמדיית אתחול, ולא אימג' דיסק. לכן הוא בלשונית משלו, עם אותה צורת תיקיות, בלי מחיצות ובלי "נכנס לדיסק מ-".</div></div></div></div>`;
}

/* כפתורי הכותרת בלשונית: כמו במוקאפ — העלאת ISO, תיקייה, רענון (בלי קליטה ובלי tar). */
function isoActions() {
  return `<button class="btn primary" onclick="openIsoUpload()">העלאת ISO</button><button class="btn" onclick="addIsoFolderSheet()">+ תיקייה</button>`
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
}

/* ---------- העלאה ---------- */
/* הצעת שם מתוך שם הקובץ: רק תווים שהשרת מקבל. זו הצעה בשדה שהמפעיל רואה ויכול לערוך — השרת עצמו לא מנקה. */
function isoSuggestName(filename) {
  const stem = String(filename || "").replace(/\.iso$/i, "");
  return stem.replace(/[^A-Za-z0-9א-ת ._-]/g, "_").replace(/^[^A-Za-z0-9א-ת]+|[^A-Za-z0-9א-ת]+$/g, "").slice(0, 48);
}

function openIsoUpload() {
  if (!isoAllowed()) { toast("אין הרשאה"); return; }
  const folders = [{ value: "", label: "ללא תיקייה" }].concat((ISO_FOLDERS || []).map((f) => ({ value: f.name, label: f.name })));
  sheet({ title: "העלאת ISO", sub: "הקובץ נשמר רק אחרי שהשרת בדק שהוא ISO תקין ואימת sha256. קובץ אחר נדחה, ולא נשמר.",
    submitLabel: "העלה", size: "mid",
    note: `<div class="form"><div class="field full">${dropzoneHtml({ id: "isoFile", accept: ".iso,application/x-iso9660-image", aria: "בחירת קובץ ISO" })}</div><div id="isoUploadProgress"></div></div>`,
    fields: [
      { id: "name", label: "שם", help: ISO_NAME_RULE },
      { id: "folder", label: "תיקייה", type: "select", value: ISO_FOLDER || "", options: folders },
    ],
    onSubmit: startIsoUpload });
  const input = document.getElementById("isoFile"), name = document.getElementById("sf-name");
  if (input && name) {
    input.addEventListener("change", () => {
      const f = input.files && input.files[0];
      if (f && !name.value) name.value = isoSuggestName(f.name);
    });
  }
}

function startIsoUpload(values) {
  const input = document.getElementById("isoFile");
  const file = input && input.files && input.files[0];
  if (!file) { toast("בחר קובץ לפני העלאה"); return Promise.reject(new Error("לא נבחר קובץ")); }
  return uploadIso(file, values);
}

function isoUploadMessage(request) {
  let detail = "";
  try { detail = JSON.parse(request.responseText).detail; } catch (e) { /* גוף שאינו JSON */ }
  return typeof detail === "string" && detail ? detail : "שגיאה " + request.status;
}

function uploadIso(file, values) {
  if (secondaryReadOnly()) { toast(SECONDARY_READ_ONLY); return Promise.reject(new Error(SECONDARY_READ_ONLY)); }   // ‏XHR עוקף את api()
  const progress = document.getElementById("isoUploadProgress");
  const ok = document.getElementById("sf-submit");
  const draw = (pct, text, stage) => {
    if (progress) progress.innerHTML = `<div class="list-row"><div class="list-main"><strong>${esc(file.name)}</strong><small>${esc(text)}</small></div><div class="list-side">${stage ? "" : pct + "%"}</div></div><div class="progress" style="margin-top:7px"><i style="width:${pct}%"></i></div>`;
  };
  if (ok) ok.disabled = true;
  draw(0, "מתחיל…");
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const query = new URLSearchParams({ name: values.name || "", folder: values.folder || "", filename: file.name });
    request.open("POST", "/api/console/isos/upload?" + query.toString());
    request.setRequestHeader("Content-Type", "application/octet-stream");
    // ‏"ביטול העלאה": סגירת החלון קוטעת גם את הבקשה — אחרת היא ממשיכה ברקע וה-ISO נכנס בלי שהמפעיל רואה.
    for (const id of ["sf-cancel", "sf-x"]) {
      const button = document.getElementById(id);
      if (!button) continue;
      const close = button.onclick;
      button.onclick = (event) => { request.abort(); if (close) close.call(button, event); };
      if (id === "sf-cancel") button.textContent = "ביטול העלאה";
    }
    request.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const pct = Math.round((100 * e.loaded) / e.total);
      draw(pct, `מעלה: ${fmtBytes(e.loaded)} מתוך ${fmtBytes(e.total)} (${pct}%)`);
    };
    request.upload.onload = () => draw(100, "ההעלאה הסתיימה. השרת מאמת sha256, בודק את מבנה ה-ISO ומכניס אותו לספרייה…", true);
    request.onload = () => {
      if (request.status === 200) {
        let name = values.name || file.name;
        try { name = JSON.parse(request.responseText).name || name; } catch (e) { /* ברירת המחדל */ }
        toast(`"${name}" נוסף לספרייה.`, 3200, "ok");
        loadIsos();
        resolve();
      } else {
        if (ok) ok.disabled = false;
        if (progress) progress.innerHTML = "";
        reject(new Error(isoUploadMessage(request)));
      }
    };
    request.onerror = () => {
      if (ok) ok.disabled = false;
      if (progress) progress.innerHTML = "";
      reject(new Error("ההעלאה נכשלה — החיבור נותק."));
    };
    request.send(file);
  });
}

/* ---------- מחיקה ותיקיות ---------- */
function deleteIso(idEnc) {
  if (!isoAllowed()) return;
  const id = decodeURIComponent(idEnc), m = (ISOS || []).find((x) => x.id === id);
  if (!m) return;
  sheet({
    title: "למחוק את ה-ISO?", danger: true, submitLabel: "מחיקה",
    sub: `${m.name} · ${fmtBytes(m.size)}`,
    note: impactList([["b", `${ltr(fmtBytes(m.size))} יימחקו מהשרת`], ["b", "הפעולה אינה הפיכה. כדי להשתמש בו שוב צריך להעלות אותו מחדש."]]),
    onSubmit: async () => {
      await post("/isos/" + encodeId(id) + "/delete");
      toast("ה-ISO נמחק", 2600, "ok");
      await loadIsos();
    },
  });
}

function addIsoFolderSheet() {
  if (!isoAllowed()) return;
  sheet({ title: "תיקיית ISO חדשה", submitLabel: "צור", fields: [{ id: "name", label: "שם התיקייה", help: ISO_NAME_RULE }],
    onSubmit: async (v) => { await post("/iso-folders", { name: v.name }); toast("התיקייה נוצרה", 2600, "ok"); await loadIsos(); } });
}

function deleteIsoFolder(nameEnc) {
  if (!isoAllowed()) return;
  const name = decodeURIComponent(nameEnc);
  sheet({ title: "למחוק את התיקייה?", danger: true, submitLabel: "מחיקה", sub: name,
    note: impactList([["b", "התיקייה ריקה — רק השם שלה יימחק."]]),
    onSubmit: async () => {
      await post("/iso-folders/" + encodeId(name) + "/delete");
      ISO_FOLDER = null;
      toast("התיקייה נמחקה", 2600, "ok");
      await loadIsos();
    } });
}
