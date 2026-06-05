"""
cloud_sync.py — cross-computer persistence via your existing Google Apps Script.

Reuses the SAME Apps Script web-app URL your corridor chart uses, but writes to
a SEPARATE namespace ('rugpull') so it never touches the corridor's `tickers`
data. This requires a tiny addition to your Apps Script (see APPS_SCRIPT_ADDON
below / the README) that handles two new actions: getApp / saveApp.

What syncs: your watchlist + your starred values. Stored as one JSON blob under
the 'rugpull' key, so it loads identically on any computer.

Protocol (mirrors the corridor's text/plain, no-preflight style):
  GET  ?key=rugpull              -> { app: {...} }   (or {} if unset)
  POST { action:'saveApp', key:'rugpull', dataset:{...} } -> { ok:true }
"""

from __future__ import annotations
import json
import requests

KEY = "rugpull"
TIMEOUT = 20


class CloudError(RuntimeError):
    pass


def _valid(url: str) -> bool:
    return bool(url) and "/exec" in url


def load_app_state(url: str) -> dict | None:
    """Pull the saved {watchlist, starred} blob. Returns None if nothing saved
    or the script doesn't support the namespace yet."""
    if not _valid(url):
        raise CloudError("URL must be the Apps Script web-app deployment ending in /exec")
    try:
        r = requests.get(url, params={"key": KEY}, timeout=TIMEOUT)
        data = r.json()
    except Exception as e:
        raise CloudError(f"load failed: {e}")
    if isinstance(data, dict) and data.get("error"):
        raise CloudError(data["error"])
    # accept either {app:{...}} (new) or a bare blob
    blob = data.get("app") if isinstance(data, dict) else None
    if blob is None and isinstance(data, dict) and "watchlist" in data:
        blob = data
    return blob or None


def save_app_state(url: str, watchlist: list, starred: list) -> dict:
    """Persist watchlist + starred to the cloud under the 'rugpull' key."""
    if not _valid(url):
        raise CloudError("URL must end in /exec (the Web App deployment URL)")
    payload = {
        "action": "saveApp",
        "key": KEY,
        "dataset": {"watchlist": watchlist, "starred": starred,
                    "version": 1},
    }
    try:
        # text/plain => Apps Script treats it as a simple request (no CORS preflight)
        r = requests.post(url, data=json.dumps(payload),
                          headers={"Content-Type": "text/plain;charset=utf-8"},
                          timeout=TIMEOUT)
        data = r.json()
    except Exception as e:
        raise CloudError(f"save failed: {e}")
    if isinstance(data, dict) and data.get("error"):
        raise CloudError(data["error"])
    return data if isinstance(data, dict) else {"ok": True}


# Paste this into your existing Apps Script (Code.gs), ABOVE or alongside your
# current doGet/doPost. It adds the 'rugpull' namespace using Script Properties
# and leaves your corridor `tickers` logic untouched.
APPS_SCRIPT_ADDON = r'''
// ---- Rugpull_AI app-state add-on (namespaced; does NOT touch tickers) ----
// Handles GET ?key=rugpull and POST {action:'saveApp', key, dataset}.
// Stores the blob in Script Properties under "app__<key>".

function _rugpullGet(key) {
  var raw = PropertiesService.getScriptProperties().getProperty('app__' + key);
  return { app: raw ? JSON.parse(raw) : null };
}

function _rugpullSave(key, dataset) {
  PropertiesService.getScriptProperties()
    .setProperty('app__' + key, JSON.stringify(dataset));
  return { ok: true, key: key };
}

// In your doGet(e), add near the top:
//   if (e && e.parameter && e.parameter.key) {
//     return ContentService.createTextOutput(
//       JSON.stringify(_rugpullGet(e.parameter.key)))
//       .setMimeType(ContentService.MimeType.JSON);
//   }
//
// In your doPost(e), add near the top (after you parse the body to `req`):
//   if (req && req.action === 'saveApp') {
//     return ContentService.createTextOutput(
//       JSON.stringify(_rugpullSave(req.key, req.dataset)))
//       .setMimeType(ContentService.MimeType.JSON);
//   }
'''
