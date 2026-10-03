// Visible time on page, for the A/B experiment (docs/experiment_design.md, section 4).
// Counts only while the tab is visible. Each time the page is hidden or closed it
// sends the seconds since the last send, so a visitor's total is the sum of their
// page_time events. Sends nothing unless an experiment is running (#ab-notice is
// only rendered then); the server drops it for opted-out and bot visitors anyway.
(function () {
  var visibleSince = document.visibilityState === "visible" ? Date.now() : null;
  var pending = 0;

  function flush() {
    if (visibleSince !== null) {
      pending += Date.now() - visibleSince;
      visibleSince = null;
    }
    if (pending <= 0 || !document.getElementById("ab-notice") || !navigator.sendBeacon) return;
    var secs = Math.min(Math.round(pending / 1000), 3600);
    pending = 0;
    var body = JSON.stringify({event: "page_time", target: String(secs)});
    navigator.sendBeacon("/api/events", new Blob([body], {type: "application/json"}));
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") flush();
    else visibleSince = Date.now();
  });
  window.addEventListener("pagehide", flush);
})();
