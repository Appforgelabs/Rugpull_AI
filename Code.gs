/*************************************************************************
 * Rugpull_AI + Corridor — unified Apps Script backend (Code.gs)
 *
 * Handles BOTH apps through one web-app URL, in separate namespaces so
 * they never collide:
 *
 *   CORRIDOR CHART
 *     GET   (no params)                    -> { tickers: { SYM: {...}, ... } }
 *     POST  { action:'save',
 *             dataset:{ tickers:{...} } }   -> { tickers:N, eps:N, prices:N }
 *
 *   RUGPULL_AI APP (watchlist + starred)
 *     GET   ?key=rugpull                   -> { app: {...} | null }
 *     POST  { action:'saveApp',
 *             key:'rugpull', dataset:{...} } -> { ok:true, key:'rugpull' }
 *
 * Storage: a flat key/value sheet the script auto-creates and remembers.
 *   - each ticker is one row:  ticker__SYM  | <json>
 *   - app state is one row:    app__rugpull | <json>
 *
 * DEPLOY:
 *   1. Paste this whole file into your Apps Script project (replace Code.gs).
 *   2. Deploy -> Manage deployments -> Edit -> New version.
 *      Execute as: Me.   Who has access: Anyone.   Copy the /exec URL.
 *   3. Use that /exec URL in BOTH the corridor chart and Rugpull_AI.
 *
 * NOTE: this script keeps its data in its OWN auto-created spreadsheet. The
 * first time your corridor does "Save to cloud", it re-seeds the tickers here
 * from your browser's saved state (no data is lost — corridor also keeps a
 * local copy). To instead reuse an existing sheet, set Script Property
 * SHEET_ID to that spreadsheet's id.
 *************************************************************************/

var STORE_TAB = 'store';

// ---- entry points ----------------------------------------------------------
function doGet(e) {
  try {
    if (e && e.parameter && e.parameter.key) {
      if (e.parameter.key === '__all__') {
        // bulk boot route: every app__ blob in ONE call (readAll runs once)
        var all = readAll(), out = {};
        Object.keys(all).forEach(function (k) {
          if (k.indexOf('app__') === 0) {
            try { out[k.slice(5)] = JSON.parse(all[k]); } catch (_) {}
          }
        });
        return json({ app_all: out });
      }
      var raw = readAll()['app__' + e.parameter.key];
      return json({ app: raw ? JSON.parse(raw) : null });
    }
    // default: corridor wants the full tickers object
    var map = readAll(), tickers = {};
    Object.keys(map).forEach(function (k) {
      if (k.indexOf('ticker__') === 0) {
        try { tickers[k.slice(8)] = JSON.parse(map[k]); } catch (_) {}
      }
    });
    return json({ tickers: tickers });
  } catch (err) {
    return json({ error: String(err) });
  }
}

function doPost(e) {
  var lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
    var req = {};
    try { req = JSON.parse(e.postData.contents); } catch (_) {}

    // --- Rugpull_AI namespaced app-state ---
    if (req && req.action === 'saveApp') {
      var map = readAll();
      map['app__' + (req.key || 'rugpull')] = JSON.stringify(req.dataset || {});
      writeAll(map);
      return json({ ok: true, key: req.key || 'rugpull' });
    }

    // --- Corridor tickers save ---
    if (req && req.action === 'save' && req.dataset && req.dataset.tickers) {
      var tk = req.dataset.tickers;
      var m = readAll();
      // drop existing ticker rows, then rewrite the full set (syncs deletes)
      Object.keys(m).forEach(function (k) {
        if (k.indexOf('ticker__') === 0) delete m[k];
      });
      var nT = 0, nE = 0, nP = 0;
      Object.keys(tk).forEach(function (sym) {
        var t = tk[sym] || {};
        m['ticker__' + sym] = JSON.stringify(t);
        nT++;
        if (t.quarters && t.quarters.length) nE += t.quarters.length;
        if (t.prices && t.prices.length) nP += t.prices.length;
      });
      writeAll(m);
      return json({ tickers: nT, eps: nE, prices: nP });
    }

    return json({ error: 'unknown action' });
  } catch (err) {
    return json({ error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (_) {}
  }
}

// ---- storage helpers -------------------------------------------------------
function getSheet() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SHEET_ID');
  var ss;
  if (id) {
    ss = SpreadsheetApp.openById(id);
  } else {
    ss = SpreadsheetApp.create('Rugpull_AI Cloud Store');
    props.setProperty('SHEET_ID', ss.getId());
  }
  var sh = ss.getSheetByName(STORE_TAB);
  if (!sh) {
    sh = ss.insertSheet(STORE_TAB);
    sh.appendRow(['key', 'value']);
  }
  return sh;
}

function readAll() {
  var sh = getSheet();
  var rng = sh.getDataRange().getValues();
  var map = {};
  for (var i = 1; i < rng.length; i++) {      // skip header
    if (rng[i][0]) map[rng[i][0]] = rng[i][1];
  }
  return map;
}

function writeAll(map) {
  var sh = getSheet();
  sh.clearContents();
  var rows = [['key', 'value']];
  Object.keys(map).forEach(function (k) { rows.push([k, map[k]]); });
  sh.getRange(1, 1, rows.length, 2).setValues(rows);
}

// ---- util ------------------------------------------------------------------
function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
