"use strict";
// ---- start ------------------------------------------------------------------------------------------------------------------
initSearch();
initUserMenu();
initLogin();
$("login-form").addEventListener("submit", signIn);
$("skip").addEventListener("click", (e) => { e.preventDefault(); $("content").focus(); });
window.addEventListener("hashchange", async () => {
  const p = readAddress();
  if (p === quietAddress) { quietAddress = null; return; }
  if (!me || !p.startsWith("/")) return;
  if (!(await mayLeave())) { writeAddress(shownPath); return; }              // stayed to save: the address shows this screen again
  arrivedAt = p;
  go(() => dispatch(p)).finally(() => { arrivedAt = null; });
});
// closing or reloading the tab with changes not saved: the browser asks first
window.addEventListener("beforeunload", (e) => { if (leaveGuard && leaveGuard()) e.preventDefault(); });
// Single-key shortcuts of the current screen; never while typing, never with a dialog open, never with Ctrl/Alt/Cmd.
document.addEventListener("keydown", (e) => {
  if (!pageKeys || !$("overlay").hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
  pageKeys(e);
});
