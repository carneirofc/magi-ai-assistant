// Theme-flash prevention. Resolve + apply the theme before first paint so there's
// no light→dark flash. Mirrors @carneirofc/ui's ThemeToggleButton: it reads/writes
// the "ui-theme" localStorage key and toggles data-theme on <html>. The consuming
// root layout injects this as a blocking <script> in <head>.

export const themeInitScript = `
try {
  var t = localStorage.getItem("ui-theme");
  if (t !== "light" && t !== "dark") {
    t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.setAttribute("data-theme", t);
} catch (e) {
  document.documentElement.setAttribute("data-theme", "light");
}
`;
