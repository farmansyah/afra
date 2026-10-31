/* The phone tab bar: marks the current page, and "More" opens the full menu (the sidebar, as a sheet). Does nothing on a desktop. */
(function () {
  var bar = document.getElementById("tabbar"), more = document.getElementById("tabMore");
  if (!bar || !more) return;
  function mark() {
    var v = (location.hash || "#home").slice(1).split("/")[0] || "home", hit = false, open = document.body.classList.contains("nav-open");
    bar.querySelectorAll("a[data-view]").forEach(function (a) { var on = !open && a.dataset.view === v; a.classList.toggle("on", on); hit = hit || a.dataset.view === v; });
    more.classList.toggle("on", open || !hit);  // the menu open, or a page that has no tab of its own
    more.setAttribute("aria-expanded", open ? "true" : "false");
  }
  more.addEventListener("click", function () { document.body.classList.toggle("nav-open"); mark(); });
  document.addEventListener("click", function (e) {  // a tap on the dimmed page closes the menu
    if (document.body.classList.contains("nav-open") && !e.target.closest("#sidebar, #tabbar")) { document.body.classList.remove("nav-open"); mark(); }
  });
  window.addEventListener("hashchange", mark);
  new MutationObserver(mark).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  mark();
})();
