require("dotenv").config();

const path = require("node:path");
const express = require("express");
const cors = require("cors");
const helmet = require("helmet");
const morgan = require("morgan");
const compression = require("compression");
const rateLimit = require("express-rate-limit");

const contentRoutes = require("./routes/content.routes");
const tvRoutes = require("./routes/tv.routes");
const downloadService = require("./services/download.service");
const { ApiError } = require("./utils/api-error");

const app = express();
const port = Number(process.env.PORT || 5555);
const rateLimitEnabled = String(process.env.DISABLE_RATE_LIMIT).toLowerCase() !== "true";

// Trust proxy (necessary for rate-limit behind Nginx/PM2)
app.set("trust proxy", 1);

// Global Middlewares
app.use(compression());
app.use(
  helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc: ["'self'"],
        frameSrc: ["'self'", "*"], // Allow iframe embeds from any domain
        scriptSrc: ["'self'", "'unsafe-inline'", "https://unpkg.com"],
        connectSrc: ["'self'", "https://unpkg.com"], // Allow Ionicons fetch SVGs
        styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        fontSrc: ["'self'", "https://fonts.gstatic.com"],
        imgSrc: ["'self'", "data:", "https:"],
      },
    },
    crossOriginResourcePolicy: { policy: "cross-origin" },
  })
);
app.use(cors());
app.use(express.json({ limit: "1mb" }));
app.use(morgan("dev"));

// Rate Limiting
const apiLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { success: false, message: "Demasiadas peticiones. Espera 1 minuto." }
});

const scrapeLimiter = rateLimit({
  windowMs: 60 * 1000,
  max: 5,
  standardHeaders: true,
  legacyHeaders: false,
  message: { success: false, message: "Límite de scrapeo alcanzado. Espera 1 minuto." }
});

// En desarrollo local, DISABLE_RATE_LIMIT=true debe desactivar tanto el
// contador diario como los límites de Express. Antes solo desactivaba el
// primero: búsquedas recientes podían bloquear la resolución de un episodio.
if (rateLimitEnabled) {
  app.use("/api/", apiLimiter);
  app.use("/api/v1/content/resolve", scrapeLimiter);
  app.use("/api/v1/content/search", scrapeLimiter);
  app.use("/api/pelisplus/resolve", scrapeLimiter);
  app.use("/api/pelisplus/search", scrapeLimiter);
}

// Static files
const downloadsDir = downloadService.getDownloadsDir();
const staticDownloadOptions = {
  index: false,
  fallthrough: false,
  setHeaders: (res, filePath) => {
    res.setHeader("Content-Disposition", `attachment; filename="${path.basename(filePath)}"`);
  },
};

app.use(express.static(path.join(__dirname, "../public")));
app.use("/downloads", express.static(downloadsDir, staticDownloadOptions));
app.use("/api/downloads", express.static(downloadsDir, staticDownloadOptions));

// Routes
app.get("/api", (_req, res) => {
  res.status(200).json({
    success: true,
    message: "PeliApi scraper y descargas backend",
    version: "1.0.0",
    endpoints: {
      modern: [
        "/api/v1/content/search",
        "/api/v1/content/catalog",
        "/api/v1/content/genres",
        "/api/v1/content/info/:slug",
        "/api/v1/content/servers",
        "/api/v1/content/resolve",
      ],
      legacy: [
        "/api/pelisplus/search",
        "/api/pelisplus/catalog",
        "/api/pelisplus/genres",
        "/api/pelisplus/info/:slug",
        "/api/pelisplus/servers",
        "/api/pelisplus/resolve",
      ],
    },
  });
});

app.get("/health", async (_req, res) => {
  const { exec } = require("child_process");
  const util = require("util");
  const execAsync = util.promisify(exec);
  
  let chromeCount = 0;
  try {
    if (process.platform === "win32") {
      const { stdout } = await execAsync('tasklist /FI "IMAGENAME eq chrome.exe" /NH || true');
      if (stdout.includes("No tasks are running") || stdout.includes("No se ejecutan tareas")) {
        chromeCount = 0;
      } else {
        chromeCount = stdout.split("\n").filter(line => line.toLowerCase().includes("chrome.exe")).length;
      }
    } else {
      const { stdout } = await execAsync("pgrep -c chrome || true");
      chromeCount = parseInt(stdout.trim()) || 0;
    }
  } catch (e) {
    chromeCount = -1;
  }

  res.status(200).json({
    success: true,
    status: "ok",
    chromeProcesses: chromeCount,
    memoryWarning: chromeCount > 10
  });
});

app.use("/api/v1/content", contentRoutes);
app.use("/api/pelisplus", contentRoutes);
app.use("/api/v1/tv", tvRoutes);

// 404
app.use((_req, _res, next) => {
  next(new ApiError(404, "Endpoint no encontrado"));
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});

// Error handler
app.use((error, _req, res, _next) => {
  const statusCode = error.statusCode || 500;
  const response = {
    success: false,
    message: error.message || "Error interno del servidor",
  };
  if (process.env.NODE_ENV !== "production" && error.details) {
    response.error = error.details;
  }
  res.status(statusCode).json(response);
});

// Check yt-dlp availability at startup (non-blocking)
const ytdlpResolver = require("./utils/resolvers/ytdlp.resolver");
ytdlpResolver.checkYtdlpAvailability().then(() => {
  if (ytdlpResolver.isAvailable) {
    console.log(`[SYSTEM] yt-dlp detectado con éxito. Utilizando como resolvedor primario.`);
  } else {
    console.log(`[SYSTEM] yt-dlp no está instalado o no se encuentra en el PATH. Se usará Puppeteer como fallback.`);
  }
});

const server = app.listen(port, () => {
  console.log(`PeliApi listening on http://localhost:${port}`);
});

server.timeout = 35000;
server.keepAliveTimeout = 35000;
server.headersTimeout = 36000;
