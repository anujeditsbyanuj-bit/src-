const express = require("express");
const { requireApiKey } = require("../middlewares/auth");
const { dailyRateLimit } = require("../middlewares/rate-limit");
const animeService = require("../services/anime.service");
const downloadService = require("../services/download.service");
const { resolveEmbedUrl } = require("../utils/resolvers");
const { ApiError } = require("../utils/api-error");

const router = express.Router();

function asyncHandler(handler) {
  return async (req, res, next) => {
    try {
      await handler(req, res, next);
    } catch (error) {
      next(error);
    }
  };
}

router.get(
  "/image-proxy",
  asyncHandler(async (req, res) => {
    const { url } = req.query;
    if (!url) {
      throw new ApiError(400, "Se requiere el parametro url");
    }

    try {
      const axios = require("axios");
      const response = await axios.get(url, {
        responseType: "stream",
        headers: {
          "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          Referer: new URL(url).origin
        },
        timeout: 10000
      });

      res.setHeader("Content-Type", response.headers["content-type"] || "image/jpeg");
      response.data.pipe(res);
    } catch (err) {
      throw new ApiError(500, "Error al descargar la imagen de portada", err.message);
    }
  })
);

router.use(requireApiKey, dailyRateLimit);

router.get(
  "/search",
  asyncHandler(async (req, res) => {
    const response = await animeService.searchAnime(req.query.q, req.query.domain);
    res.status(200).json(response);
  })
);

router.get(
  "/info",
  asyncHandler(async (req, res) => {
    if (!req.query.url) {
      throw new ApiError(400, "Se requiere el parametro url");
    }

    const response = await animeService.getAnimeInfo(req.query.url);
    res.status(200).json(response);
  })
);

router.get(
  "/episode",
  asyncHandler(async (req, res) => {
    if (!req.query.url) {
      throw new ApiError(400, "Se requiere el parametro url");
    }

    const response = await animeService.getEpisodeLinks(req.query.url, req.query.includeMega, req.query.excludeServers);
    res.status(200).json(response);
  })
);

router.get(
  "/catalog",
  asyncHandler(async (req, res) => {
    const provider = req.query.provider || req.query.domain;
    let service;
    if (provider === "animeflv") {
      service = require("../services/animeflv.service");
    } else if (provider === "jkanime") {
      service = require("../services/jkanime.service");
    } else if (provider === "tioanime") {
      service = require("../services/tioanime.service");
    } else if (provider === "monoschinos") {
      service = require("../services/monoschinos.service");
    } else if (provider === "hentaila") {
      service = require("../services/hentaila.service");
    } else if (provider === "animeyt") {
      service = require("../services/animeyt.service");
    } else if (provider === "animeav1") {
      service = require("../services/animeav1.service");
    } else {
      service = require("../services/jkanime.service");
    }

    if (typeof service.getCatalog !== "function") {
      console.log(`[ANIME CATALOG] Selected provider ${provider} does not support getCatalog. Falling back to AnimeAV1.`);
      service = require("../services/animeav1.service");
    }

    const isRecent = req.query.recent === 'true';

    const response = service.getCatalog
      ? await service.getCatalog(req.query.page, req.query.type || req.query.genre, isRecent)
      : await service.searchAnime("");
    
    if (response && response.data && Array.isArray(response.data.results)) {
      response.data.results.forEach(item => {
        if (item.url) item.slug = item.url;
        item.provider = provider || "jkanime";
      });
    }
    
    res.status(200).json(response);
  })
);

router.get(
  "/resolve",
  asyncHandler(async (req, res) => {
    let urls = [];
    if (req.query.urls) {
      try {
        urls = JSON.parse(req.query.urls);
        if (!Array.isArray(urls)) urls = [urls];
      } catch (_e) {
        urls = [req.query.urls];
      }
    } else if (req.query.url) {
      urls = [req.query.url];
    }

    if (urls.length === 0) {
      throw new ApiError(400, "Se requiere el parametro url o urls");
    }

    const resolvePromises = urls.map(async (url) => {
      try {
        const directUrl = await resolveEmbedUrl(url);
        if (directUrl && directUrl !== url) {
          let server = "unknown";
          if (url.includes("voe")) server = "voe";
          else if (url.includes("tape")) server = "streamtape";
          else if (url.includes("wish") || url.includes("playnix") || url.includes("medix") || url.includes("awish")) server = "streamwish";
          else if (url.includes("vidhide")) server = "vidhide";
          else if (url.includes("dood")) server = "doodstream";

          return {
            success: true,
            server,
            mediaType: directUrl.includes(".m3u8") ? "hls" : "mp4",
            streamUrl: directUrl,
            resolvedFrom: url
          };
        }
      } catch (err) {
        console.warn(`[RESOLVE CASCADE] Fallo en ${url}: ${err.message}`);
      }
      throw new Error("No se pudo resolver");
    });

    try {
      // Carrera en paralelo: el primer servidor que resuelva con éxito entrega el OK e interrumpe la espera de los demás
      const fastestResult = await Promise.any(resolvePromises);
      return res.status(200).json(fastestResult);
    } catch (err) {
      throw new ApiError(404, "No se pudo obtener el enlace de streaming directo en ningún servidor");
    }
  })
);

router.post(
  "/download",
  asyncHandler(async (req, res) => {
    const baseUrl = `${req.protocol}://${req.get("host")}`;
    const data = downloadService.createDownload(req.body || {}, baseUrl);

    res.status(200).json({
      success: true,
      data,
    });
  })
);

router.get(
  "/download/:id",
  asyncHandler(async (req, res) => {
    const data = downloadService.getDownload(req.params.id);

    res.status(200).json({
      success: true,
      data,
    });
  })
);

router.post(
  "/batch-download",
  asyncHandler(async (req, res) => {
    const baseUrl = `${req.protocol}://${req.get("host")}`;
    const data = downloadService.createBatch(req.body || {}, baseUrl);

    res.status(200).json({
      success: true,
      data,
    });
  })
);

router.get(
  "/batch/:id",
  asyncHandler(async (req, res) => {
    const data = downloadService.getBatch(req.params.id);

    res.status(200).json({
      success: true,
      data,
    });
  })
);

module.exports = router;
