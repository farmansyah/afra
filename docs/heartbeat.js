/* A.F.R.A heartbeat. Every so often the ♥ taps out "RAINA DANA" in Morse code.
   (c) 2026 danafarmansyah. Crafted with love. */
(function () {
  var MORSE = { R: ".-.", A: ".-", I: "..", N: "-.", D: "-.." };
  var UNIT = 150; // ms: dot = 1 unit on, dash = 3 units on, gaps 1 / 3 / 7 units (standard Morse timing)

  function frames(text) {
    var t = 0, beats = [];
    text.split(" ").forEach(function (word, wi) {
      if (wi) t += 4 * UNIT;                     // word gap = 7 units (3 already added after the last letter)
      word.split("").forEach(function (ch) {
        MORSE[ch].split("").forEach(function (sym) {
          var on = (sym === "." ? 1 : 3) * UNIT;
          beats.push([t, t + on]);
          t += on + UNIT;                        // gap between symbols = 1 unit
        });
        t += 2 * UNIT;                           // gap between letters = 3 units
      });
    });
    var total = t, ramp = 35, kf = [{ offset: 0, transform: "scale(1)", filter: "none" }];
    beats.forEach(function (b) {
      kf.push({ offset: b[0] / total, transform: "scale(1)", filter: "none" });
      kf.push({ offset: (b[0] + ramp) / total, transform: "scale(1.42)", filter: "drop-shadow(0 0 6px rgba(229,72,77,.85))" });
      kf.push({ offset: (b[1] - ramp) / total, transform: "scale(1.42)", filter: "drop-shadow(0 0 6px rgba(229,72,77,.85))" });
      kf.push({ offset: b[1] / total, transform: "scale(1)", filter: "none" });
    });
    kf.push({ offset: 1, transform: "scale(1)", filter: "none" });
    return { kf: kf, total: total };
  }

  var MSG = frames("RAINA DANA");
  window.afraHeartbeat = function () {
    document.querySelectorAll(".dedi-heart").forEach(function (h) {
      if (h.animate) h.animate(MSG.kf, { duration: MSG.total, easing: "linear" });
    });
  };

  var seen = function () { try { return localStorage.getItem("rt_dedi_seen") === "1"; } catch (e) { return false; } };
  document.addEventListener("click", function (e) {
    if (e.target.closest && e.target.closest(".dedi-heart")) { try { localStorage.setItem("rt_dedi_seen", "1"); } catch (err) { } }
  });

  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  function schedule(first) {
    var min = first ? 15 : (seen() ? 240 : 60), max = first ? 45 : (seen() ? 480 : 180);
    setTimeout(function () {
      if (document.visibilityState === "visible") window.afraHeartbeat();
      schedule(false);
    }, (min + Math.random() * (max - min)) * 1000);
  }
  schedule(true);
})();
