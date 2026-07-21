/* taOSmobile — Electron kiosk shell.
 *
 * Replaces webapp-container (QtWebEngine 5.15 == Chromium 87), which is too
 * old to render the taOS SPA: the bundle calls Object.hasOwn (Chromium 93)
 * and structuredClone (98), and layout/CSS support lags badly. Electron 43
 * ships a current Chromium, so the PWA renders as designed and modern input,
 * gestures and the software keyboard all behave.
 *
 * Runs as a Wayland client of lomiri-system-compositor (/run/wayland-syscomp).
 */
const { app, BrowserWindow, shell } = require("electron");

const TAOS_URL = process.env.TAOS_URL || "http://localhost:6969/";

// Ozone/Wayland on a Mir-backed compositor: prefer Wayland, fall back to
// whatever Ozone can find rather than hard-failing to a black screen.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");
// Touch-first device with no window manager decorations.
app.commandLine.appendSwitch("enable-features", "TouchpadOverscrollHistoryNavigation,OverlayScrollbar");
app.commandLine.appendSwitch("touch-events", "enabled");
// The controller is loopback HTTP; treat it as a secure context so service
// workers and clipboard APIs behave as they would over HTTPS.
app.commandLine.appendSwitch("unsafely-treat-insecure-origin-as-secure", TAOS_URL);

function createWindow() {
  const win = new BrowserWindow({
    fullscreen: true,
    kiosk: true,
    frame: false,
    autoHideMenuBar: true,
    backgroundColor: "#000000",
    webPreferences: {
      // No Node in the page: this renders a remote-ish web app, so keep the
      // renderer sandboxed and without Node primitives.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      spellcheck: false,
    },
  });

  win.setMenuBarVisibility(false);

  // Keep navigation inside taOS; anything else would strand the user in a
  // chrome-less window with no way back.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(TAOS_URL)) {
      shell.openExternal(url).catch(() => {});
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  const load = () => win.loadURL(TAOS_URL);

  // Verification hook: with no way to see the phone's screen, capture what the
  // renderer actually produced. Proves the page rendered rather than merely
  // that the process is alive. Enabled only when TAOS_CAPTURE is set.
  if (process.env.TAOS_CAPTURE) {
    win.webContents.on("did-finish-load", () => {
      setTimeout(async () => {
        try {
          const img = await win.webContents.capturePage();
          require("fs").writeFileSync(process.env.TAOS_CAPTURE, img.toPNG());
          console.log("captured to " + process.env.TAOS_CAPTURE);
        } catch (e) {
          console.error("capture failed: " + e.message);
        }
      }, 8000);
    });
  }

  // The controller is a local service and may still be starting.
  win.webContents.on("did-fail-load", (_e, code, desc) => {
    console.error(`load failed (${code} ${desc}); retrying in 2s`);
    setTimeout(load, 2000);
  });

  load();
}

app.on("ready", createWindow);
app.on("window-all-closed", () => app.quit());
