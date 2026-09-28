// Sets the chosen theme (Settings -> Appearance) on <html> before the page is
// drawn, so choosing Dark on a light computer doesn't flash light first. An
// ordinary script, loaded in <head> before the stylesheet: the page's security
// policy allows only DataLab's own files, never inline scripts. The app's own
// code (src/lib/theme.ts) applies the choice again once it starts and owns it
// from then on; keep the key and values the same as there.
(function () {
  try {
    var saved = window.localStorage.getItem("datalab.theme");
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
  } catch (e) {
    // Storage refused: follow the computer.
  }
})();
