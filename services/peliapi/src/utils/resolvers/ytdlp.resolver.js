const { execFile } = require("node:child_process");
const { URL } = require("node:url");

const YTDLP_ENABLED = process.env.YTDLP_ENABLED !== "false";
const YTDLP_TIMEOUT = Number(process.env.YTDLP_TIMEOUT_MS) || 8500;

let isAvailable = false;
let checked = false;

function debugLog(message, data) {
  const DEBUG = process.env.DEBUG_RESOLVER === "true" || process.env.DEBUG_DOWNLOAD === "true";
  if (!DEBUG) return;
  const timestamp = new Date().toISOString();
  const header = `[${timestamp}] [YTDLP] ${message}`;
  console.log(header, data ? (typeof data === "string" ? data.slice(0, 500) : data) : "");
}

function execYtdlp(args) {
  return new Promise((resolve, reject) => {
    const child = execFile("yt-dlp", args, {
      timeout: YTDLP_TIMEOUT,
      maxBuffer: 1024 * 1024,
      windowsHide: true,
    }, (err, stdout, stderr) => {
      if (err) {
        reject(err);
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

async function checkYtdlpAvailability() {
  if (!YTDLP_ENABLED) {
    console.log("[YTDLP] yt-dlp desactivado via YTDLP_ENABLED=false");
    isAvailable = false;
    checked = true;
    return false;
  }

  try {
    const { stdout, stderr } = await execYtdlp(["--version"]);
    const version = (stdout || stderr || "").trim();
    if (version) {
      console.log(`[YTDLP] Detectado version: ${version}`);
      isAvailable = true;
    } else {
      console.log("[YTDLP] No se pudo determinar la version");
      isAvailable = false;
    }
  } catch (err) {
    console.log("[YTDLP] No disponible en el sistema:", err.message);
    isAvailable = false;
  }

  checked = true;
  return isAvailable;
}

async function extractWithYtdlp(url, referer) {
  if (!YTDLP_ENABLED) return null;
  if (!checked) await checkYtdlpAvailability();
  if (!isAvailable) return null;

  debugLog("Resolviendo con yt-dlp", url);

  try {
    const args = [
      "-g",
      "--flat-playlist",
      "--no-check-certificates",
      "--socket-timeout", "8",
      "--referer", referer || url,
      url,
    ];

    const { stdout } = await execYtdlp(args);
    const lines = stdout.trim().split("\n").map(l => l.trim()).filter(Boolean);

    for (const line of lines) {
      if (line.startsWith("http") && (line.includes(".m3u8") || line.includes(".mp4"))) {
        debugLog("URL resuelta por yt-dlp", line);
        return line;
      }
    }

    if (lines.length > 0 && lines[0].startsWith("http")) {
      debugLog("URL resuelta por yt-dlp (fallback)", lines[0]);
      return lines[0];
    }

    debugLog("yt-dlp no encontro URL de stream", stdout.slice(0, 300));
    return null;
  } catch (err) {
    debugLog("Error ejecutando yt-dlp", err.message);
    return null;
  }
}

module.exports = {
  get isAvailable() { return isAvailable; },
  checkYtdlpAvailability,
  extractWithYtdlp,
};
