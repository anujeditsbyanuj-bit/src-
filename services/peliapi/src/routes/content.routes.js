const express = require("express");
const axios = require("axios");
const { dailyRateLimit } = require("../middlewares/rate-limit");
const pelisplusService = require("../services/pelisplus.service");
const cuevanaService = require("../services/cuevana.service");
const repelishdService = require("../services/repelishd.service");
const animeytService = require("../services/animeyt.service");
const poseidonService = require("../services/poseidon.service");
const seriesflixService = require("../services/seriesflix.service");
const unlimplayService = require("../services/unlimplay.service");
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

function normalizedTitle(value) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]/g, "");
}

async function findCuevanaEpisodeServers(title, season, episode, originalSlug) {
  if (!title && !originalSlug) return null;

  try {
    const query = title ? title.trim() : originalSlug.replace(/-/g, ' '); 
    const target = normalizedTitle(query);
    const matches = await cuevanaService.searchContent(query);
    let candidate = (matches || []).find((item) => {
      const type = String(item.type || "").toLowerCase();
      const candidateTitle = normalizedTitle(item.title);
      // Solo aceptamos coincidencias exactas del titulo (o si el titulo limpio esta contenido y es serie)
      return (type === "series" || type === "serie") &&
        (candidateTitle === target || candidateTitle === target + ' latino');
    });

    if (!candidate && originalSlug) {
      // Intento secundario con el slug
      const slugQuery = originalSlug.replace(/-/g, ' ').trim();
      const slugTarget = normalizedTitle(slugQuery);
      const slugMatches = await cuevanaService.searchContent(slugQuery);
      candidate = (slugMatches || []).find((item) => {
        const type = String(item.type || "").toLowerCase();
        const candidateTitle = normalizedTitle(item.title);
        return (type === "series" || type === "serie") &&
          (candidateTitle === slugTarget || candidateTitle.includes(slugTarget) || slugTarget.includes(candidateTitle));
      });
    }

    if (!candidate?.slug) return null;
    const data = await cuevanaService.getEpisodeServers(candidate.slug, season, episode);
    return Array.isArray(data?.servers) && data.servers.length > 0 ? data : null;
  } catch (error) {
    console.error("Cuevana fallback failed:", error.message);
    return null;
  }
}

// Aplicar middlewares globales de límites de tráfico (sin requerimiento de API Key)
router.use(dailyRateLimit);

/**
 * Buscar contenido (películas, series, anime)
 * GET /search?s=avatar o GET /search?q=avatar
 */
router.get(
  "/search",
  asyncHandler(async (req, res) => {
    const query = req.query.s || req.query.q || "";

    if (!query) {
      throw new ApiError(400, "El parametro de busqueda 's' o 'q' es requerido");
    }

    let data = [];
    let source = "aggregate";

    // 1. Buscamos en PelisPlus, RePelisHD, PoseidonHD y SeriesFlixHD en paralelo (todos Cheerio, ultraligeros)
    try {
      const [ppData, rpData, poseidonData, sfData] = await Promise.all([
        pelisplusService.searchContent(query).catch(err => {
          console.error("Error buscando en PelisPlus:", err.message);
          return [];
        }),
        repelishdService.searchContent(query).catch(err => {
          console.error("Error buscando en RePelisHD:", err.message);
          return [];
        }),
        poseidonService.searchContent(query).catch(err => {
          console.error("Error buscando en PoseidonHD:", err.message);
          return [];
        }),
        seriesflixService.searchContent(query).catch(err => {
          console.error("Error buscando en SeriesFlixHD:", err.message);
          return [];
        })
      ]);

      const ppMapped = (ppData || []).map(item => ({ ...item, provider: "pelisplus" }));
      const rpMapped = (rpData || []).map(item => ({ ...item, provider: "repelishd" }));
      const poseidonMapped = (poseidonData || []).map(item => ({ ...item, provider: "poseidon" }));
      const sfMapped = (sfData || []).map(item => ({ ...item, provider: "seriesflix" }));
      
      data = [...rpMapped, ...ppMapped, ...poseidonMapped, ...sfMapped];

      // Ordenar inteligentemente los resultados combinados para poner las coincidencias más cercanas al query al principio
      const lowerQuery = query.toLowerCase().trim();
      data.sort((a, b) => {
        const aTitle = a.title.toLowerCase();
        const bTitle = b.title.toLowerCase();

        // Coincidencia exacta de título
        const aExact = aTitle === lowerQuery || aTitle === `[${lowerQuery}]`;
        const bExact = bTitle === lowerQuery || bTitle === `[${lowerQuery}]`;
        if (aExact && !bExact) return -1;
        if (!aExact && bExact) return 1;

        // Comienza con el query
        const aStarts = aTitle.startsWith(lowerQuery) || aTitle.startsWith(`[${lowerQuery}`);
        const bStarts = bTitle.startsWith(lowerQuery) || bTitle.startsWith(`[${lowerQuery}`);
        if (aStarts && !bStarts) return -1;
        if (!aStarts && bStarts) return 1;

        // Contiene el query
        const aIncludes = aTitle.includes(lowerQuery);
        const bIncludes = bTitle.includes(lowerQuery);
        if (aIncludes && !bIncludes) return -1;
        if (!aIncludes && bIncludes) return 1;

        return 0;
      });

      if (data.length > 0) {
        source = rpMapped.length > 0 ? "repelishd" : "pelisplus";
      }
    } catch (error) {
      console.error("Error en búsqueda paralela:", error.message);
    }

    // 2. Si aún no obtuvimos resultados, probamos en Cuevana3 y AnimeYT en paralelo
    if (data.length === 0) {
      try {
        const [cuevanaData, animeytData] = await Promise.all([
          cuevanaService.searchContent(query).catch(() => []),
          animeytService.searchContent(query).catch(() => []),
        ]);
        const cMapped = (cuevanaData || []).map(item => ({ ...item, provider: "cuevana3" }));
        const aMapped = (animeytData || []).map(item => ({ ...item, provider: "animeyt" }));
        data = [...cMapped, ...aMapped];
        source = data.length > 0 ? (cMapped.length > 0 ? "cuevana3" : "animeyt") : "none";
      } catch (error) {
        console.error("Error buscando en Cuevana3/AnimeYT:", error.message);
      }
    }

    res.status(200).json({
      success: true,
      data,
      source,
    });
  })
);

/**
 * Obtener catálogo filtrado por tipo, género y página
 * GET /catalog?type=movie&genre=accion&page=1
 */
router.get(
  "/catalog",
  asyncHandler(async (req, res) => {
    const type = req.query.type || "movie"; // movie, series, anime
    const genre = req.query.genre || "";
    const page = Number(req.query.page || 1);

    // El catálogo se sirve de PelisPlus porque Cuevana3 no tiene endpoints estructurados
    const data = await pelisplusService.getCatalog(type, genre, page);
    if (data && data.items) {
      data.items = data.items.map(item => ({ ...item, provider: "pelisplus" }));
    }
    
    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

/**
 * Obtener géneros disponibles
 * GET /genres
 */
router.get(
  "/genres",
  asyncHandler(async (req, res) => {
    // Los géneros se obtienen de PelisPlus
    const data = await pelisplusService.getGenres();
    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

/**
 * Obtener detalles y servidores de reproducción de una película o serie
 * GET /info/:slug?type=movie
 */
router.get(
  "/info/*",
  asyncHandler(async (req, res) => {
    const slug = req.params[0];
    const type = req.query.type || "movie"; // movie, series, anime
    let provider = req.query.provider;

    // Auto-detectar proveedor basado en el patrón del slug
    if (!provider) {
      const cleanSlug = slug.replace(/^\//, "");
      if (slug.includes("/") && !cleanSlug.startsWith("pelicula/") && !cleanSlug.startsWith("serie/") && !cleanSlug.startsWith("anime/")) {
        provider = "cuevana3";
      } else if (slug.includes("-online-espanol")) {
        provider = "repelishd";
      } else if (slug.startsWith("animeyt-")) {
        provider = "animeyt";
      } else if (cleanSlug.startsWith("pelicula/") || cleanSlug.startsWith("serie/")) {
        // PoseidonHD cuando el slug contiene la ruta completa
        provider = "poseidon";
      } else if (/^\d+$/.test(slug.trim())) {
        // Slug numérico: PelisPlus
        provider = "pelisplus";
      } else {
        provider = "pelisplus";
      }
    }

    let data;
    let source = provider;

    try {
      let service;
      if (provider === "cuevana3") service = cuevanaService;
      else if (provider === "repelishd") service = repelishdService;
      else if (provider === "animeyt") service = animeytService;
      else if (provider === "poseidon") service = poseidonService;
      else if (provider === "seriesflix") service = seriesflixService;
      else service = pelisplusService;

      data = await service.getContentInfo(slug, type);
    } catch (error) {
      console.log(`[info] Primary provider "${provider}" failed for slug "${slug}": ${error.message}`);
      
      // Cascade inteligente por proveedor
      const cascadeProviders = [];
      if (provider === "cuevana3") {
        cascadeProviders.push(
          { name: "seriesflix", fn: () => seriesflixService.getContentInfo(slug.replace("serie/", ""), type) },
          { name: "poseidon", fn: () => poseidonService.getContentInfo(slug, type) },
        );
      } else if (provider === "seriesflix") {
        cascadeProviders.push(
          { name: "cuevana3", fn: () => cuevanaService.getContentInfo(slug, type) },
          { name: "poseidon", fn: () => poseidonService.getContentInfo(slug, type) },
        );
      } else if (provider === "pelisplus") {
        cascadeProviders.push(
          { name: "repelishd", fn: () => repelishdService.getContentInfo(slug, type) },
          { name: "poseidon", fn: () => poseidonService.getContentInfo(slug, type) },
          { name: "cuevana3", fn: () => cuevanaService.getContentInfo(slug, type) },
        );
      } else if (provider === "poseidon") {
        cascadeProviders.push(
          { name: "pelisplus", fn: () => pelisplusService.getContentInfo(slug, type) },
          { name: "cuevana3", fn: () => cuevanaService.getContentInfo(slug, type) },
        );
      } else {
        cascadeProviders.push(
          { name: "seriesflix", fn: () => seriesflixService.getContentInfo(slug, type) },
          { name: "cuevana3", fn: () => cuevanaService.getContentInfo(slug, type) },
        );
      }

      let cascadeSuccess = false;
      for (const fallback of cascadeProviders) {
        try {
          console.log(`[info cascade] Trying ${fallback.name}...`);
          data = await fallback.fn();
          if (data && (data.title || data.seasons)) {
            source = fallback.name;
            cascadeSuccess = true;
            console.log(`[info cascade] ${fallback.name} succeeded`);
            break;
          }
        } catch (fallbackErr) {
          console.warn(`[info cascade] ${fallback.name} failed: ${fallbackErr.message}`);
        }
      }

      if (!cascadeSuccess) {
        throw error;
      }
    }

    // Agregar provider al resultado para consistencia
    if (data) {
      data.provider = source;
    }

    // ── Cascade para series sin temporadas ──────────────────────────────────
    // Si el proveedor primario devolvió una serie pero sin episodios,
    // intentamos en cascada con otros proveedores para recuperar la estructura.
    const isSeriesRequest = String(type).toLowerCase().includes('serie');
    const hasNoSeasons = isSeriesRequest && (!data?.seasons || !Array.isArray(data.seasons) || data.seasons.length === 0);

    if (hasNoSeasons) {
      const fallbackProviders = [
        { name: 'repelishd', service: repelishdService },
        { name: 'seriesflix', service: seriesflixService },
        { name: 'poseidon', service: poseidonService },
      ].filter(p => p.name !== source); // no reintentar el que ya falló

      for (const fallback of fallbackProviders) {
        try {
          console.log(`[info cascade] "${source}" returned 0 seasons, trying ${fallback.name} for slug: ${slug}`);
          const fallbackData = await fallback.service.getContentInfo(slug, type);
          const fallbackSeasons = Array.isArray(fallbackData?.seasons) ? fallbackData.seasons : [];
          if (fallbackSeasons.length > 0) {
            // Mezclar: conservar metadata del proveedor original pero tomar seasons del fallback
            data = {
              ...(data || {}),
              seasons: fallbackSeasons,
              provider: fallback.name,
            };
            console.log(`[info cascade] Found ${fallbackSeasons.length} season(s) via ${fallback.name}`);
            break;
          }
        } catch (fallbackErr) {
          console.warn(`[info cascade] ${fallback.name} also failed:`, fallbackErr.message);
        }
      }
    }

    res.status(200).json({
      success: true,
      data,
      source,
    });
  })
);

/**
 * Obtener servidores de reproducción para un capítulo de una serie o anime
 * GET /servers?slug=breaking-bad&season=1&episode=1
 */
router.get(
  "/servers",
  asyncHandler(async (req, res) => {
    const slug = req.query.slug || req.query.serieSlug;
    const season = Number(req.query.season || 1);
    const episode = Number(req.query.episode || 1);
    const title = typeof req.query.title === "string" ? req.query.title : "";
    const overrideUrl = typeof req.query.url === "string" ? req.query.url : "";
    const tmdbId = req.query.tmdbId; // Extra parameter for unlimplay
    let provider = req.query.provider;

    if (!slug && !tmdbId) {
      throw new ApiError(400, "El parametro 'slug' o 'tmdbId' es requerido");
    }

    // Auto-detectar proveedor basado en el patrón del slug
    if (!provider) {
      if (tmdbId) {
        provider = "unlimplay";
      } else {
        const cleanSlug = slug.replace(/^\//, "");
        if (slug.includes("/") && !cleanSlug.startsWith("pelicula/") && !cleanSlug.startsWith("serie/") && !cleanSlug.startsWith("anime/")) {
          provider = "cuevana3";
        } else if (slug.includes("-online-espanol")) {
          provider = "repelishd";
        } else if (slug.startsWith("animeyt-") || slug.includes("animeyt.cc") || (slug.includes("anime/") && !cleanSlug.startsWith("serie/"))) {
          provider = "animeyt";
        } else if (cleanSlug.startsWith("pelicula/") || cleanSlug.startsWith("serie/")) {
          // PoseidonHD cuando el slug contiene la ruta completa
          provider = "poseidon";
        } else if (/^\d+$/.test(slug.trim())) {
          // Slug numérico: PelisPlus
          provider = "pelisplus";
        } else {
          provider = "pelisplus";
        }
      }
    }

    let data;
    let source = provider;

    try {
      let service;
      if (provider === "unlimplay") service = unlimplayService;
      else if (provider === "cuevana3") service = cuevanaService;
      else if (provider === "repelishd") service = repelishdService;
      else if (provider === "animeyt") service = animeytService;
      else if (provider === "poseidon") service = poseidonService;
      else if (provider === "seriesflix") service = seriesflixService;
      else service = pelisplusService;

      if (provider === "unlimplay") {
        data = await service.getEpisodeServers(tmdbId, season, episode);
      } else {
        data = await service.getEpisodeServers(slug, season, episode, overrideUrl);
      }
    } catch (error) {
      console.log(`[servers] Primary provider "${provider}" failed for slug "${slug}" / tmdbId "${tmdbId}": ${error.message}`);
      // Cascade: try other providers
      const cascadeProviders = [];
      
      if (provider === "unlimplay" && slug) {
        // Fallback para unlimplay si falla pero tenemos slug (vamos a lo clásico)
        cascadeProviders.push(
          { name: "pelisplus", fn: () => pelisplusService.getEpisodeServers(slug, season, episode) },
          { name: "cuevana3", fn: () => findCuevanaEpisodeServers(title, season, episode, slug) },
          { name: "seriesflix", fn: () => seriesflixService.getEpisodeServersByTitle(title, season, episode) }
        );
      } else if (provider === "cuevana3") {
        // Cuevana3 falló → probar SeriesFlix primero (funciona bien con cheerio), luego PoseidonHD
        cascadeProviders.push(
          { name: "seriesflix", fn: () => seriesflixService.getEpisodeServersByTitle(title, season, episode) },
          { name: "poseidon", fn: () => poseidonService.getEpisodeServersByTitle(title, season, episode) },
        );
      } else if (provider === "seriesflix") {
        // SeriesFlix falló → probar Cuevana3, luego PoseidonHD
        cascadeProviders.push(
          { name: "cuevana3", fn: () => cuevanaService.getEpisodeServers(slug, season, episode) },
          { name: "poseidon", fn: () => poseidonService.getEpisodeServersByTitle(title, season, episode) },
        );
      } else if (provider === "pelisplus") {
        cascadeProviders.push(
          { name: "seriesflix", fn: () => seriesflixService.getEpisodeServersByTitle(title, season, episode) },
          { name: "poseidon", fn: () => poseidonService.getEpisodeServersByTitle(title, season, episode) },
          { name: "repelishd", fn: () => repelishdService.getEpisodeServers(slug, season, episode) },
          { name: "cuevana3", fn: () => findCuevanaEpisodeServers(title, season, episode, slug) },
        );
      } else {
        // Cualquier otro proveedor → probar con SeriesFlix y Cuevana3
        cascadeProviders.push(
          { name: "seriesflix", fn: () => seriesflixService.getEpisodeServersByTitle(title, season, episode) },
          { name: "cuevana3", fn: () => cuevanaService.getEpisodeServers(slug, season, episode) },
        );
      }

      let cascadeSuccess = false;
      for (const fallback of cascadeProviders) {
        try {
          console.log(`[servers cascade] Trying ${fallback.name}...`);
          const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error("Timeout en cascade")), 10000));
          const result = await Promise.race([fallback.fn(), timeoutPromise]);
          if (result && Array.isArray(result.servers) && result.servers.length > 0) {
            data = result;
            source = fallback.name;
            cascadeSuccess = true;
            console.log(`[servers cascade] ${fallback.name} returned ${result.servers.length} server(s)`);
            break;
          }
        } catch (fallbackErr) {
          console.warn(`[servers cascade] ${fallback.name} failed: ${fallbackErr.message}`);
        }
      }

      if (!cascadeSuccess) {
        throw error; // lanzamos el error original
      }
    }

    // Algunos episodios siguen presentes en el catálogo pero el proveedor
    // original ya no publica enlaces. Busca en SeriesFlix, PoseidonHD, Cuevana y AnimeYT
    // antes de responder una lista vacía.
    if (!Array.isArray(data?.servers) || data.servers.length === 0) {
      const timeoutPromise = new Promise((resolve) => setTimeout(() => resolve([null, null, null, null]), 12000));
      const [sfData, poseidonData, cuevanaData, animeytData] = await Promise.race([
        Promise.all([
          seriesflixService.getEpisodeServersByTitle(title, season, episode).catch(() => null),
          poseidonService.getEpisodeServersByTitle(title, season, episode).catch(() => null),
          findCuevanaEpisodeServers(title, season, episode, slug).catch(() => null),
          title ? animeytService.getEpisodeServersByTitle(title, season, episode).catch(() => null) : Promise.resolve(null),
        ]),
        timeoutPromise
      ]);
      if (sfData && Array.isArray(sfData.servers) && sfData.servers.length > 0) {
        data = sfData;
        source = "seriesflix";
      } else if (poseidonData && Array.isArray(poseidonData.servers) && poseidonData.servers.length > 0) {
        data = poseidonData;
        source = "poseidon";
      } else if (cuevanaData && Array.isArray(cuevanaData.servers) && cuevanaData.servers.length > 0) {
        data = cuevanaData;
        source = "cuevana3";
      } else if (animeytData && Array.isArray(animeytData.servers) && animeytData.servers.length > 0) {
        data = animeytData;
        source = "animeyt";
      }
    }

    res.status(200).json({
      success: true,
      data,
      source,
    });
  })
);

/**
 * Verificar servidores en paralelo y devolver solo los estables con su URL directa.
 * POST /preflight
 * Body: { slug?, type?, season?, episode?, title?, servers?: [{name, language, quality, embedUrl}] }
 * Si servers no llega, se obtienen del proveedor (prioridad PoseidonHD).
 */
router.post(
  "/preflight",
  asyncHandler(async (req, res) => {
    const { slug, type, season, episode, title, servers: inputServers } = req.body || {};
    console.log(`[PREFLIGHT] Request: slug=${slug} type=${type} season=${season} episode=${episode}`);
    let servers = Array.isArray(inputServers) ? inputServers : null;
    const isMovie = type === "movie";

    if (!servers || servers.length === 0) {
      if (!slug) {
        console.error("[PREFLIGHT] Error: no slug provided");
        throw new ApiError(400, "Se requiere 'slug' o 'servers' para preflight");
      }

      const cleanSlug = (slug || "").replace(/^\//, "");

      try {
        if (isMovie) {
          const info = await poseidonService.getContentInfo(slug, "movie");
          servers = info.servers || [];
          console.log(`[PREFLIGHT] Poseidon movie: ${servers.length} servers`);
        } else {
          const s = Number(season || 1);
          const e = Number(episode || 1);
          const data = await poseidonService.getEpisodeServers(slug, s, e);
          servers = data.servers || [];
          console.log(`[PREFLIGHT] Poseidon episode: ${servers.length} servers`);
        }
      } catch (err) {
        console.log(`[PREFLIGHT] Poseidon failed: ${err.message}, trying PelisPlus...`);
        try {
          if (isMovie) {
            const info = await pelisplusService.getContentInfo(slug, "movie");
            servers = info.servers || [];
            console.log(`[PREFLIGHT] PelisPlus movie: ${servers.length} servers`);
          } else {
            const data = await pelisplusService.getEpisodeServers(slug, Number(season || 1), Number(episode || 1));
            servers = data.servers || [];
            console.log(`[PREFLIGHT] PelisPlus episode: ${servers.length} servers`);
          }
        } catch (err2) {
          console.log(`[PREFLIGHT] PelisPlus failed: ${err2.message}, trying Cuevana by title...`);
          try {
            // Usar búsqueda por título/slug para obtener el slug correcto de Cuevana
            const cuevanaResult = await findCuevanaEpisodeServers(title, Number(season || 1), Number(episode || 1), slug);
            if (cuevanaResult && Array.isArray(cuevanaResult.servers) && cuevanaResult.servers.length > 0) {
              servers = cuevanaResult.servers;
              console.log(`[PREFLIGHT] Cuevana episode (by title): ${servers.length} servers`);
            } else {
              throw new Error('Cuevana returned no servers');
            }
          } catch (err3) {
            if (!isMovie) {
              console.log(`[PREFLIGHT] Cuevana failed: ${err3.message}, trying SeriesFlix...`);
              try {
                const data = await seriesflixService.getEpisodeServers(slug, Number(season || 1), Number(episode || 1));
                servers = data.servers || [];
                console.log(`[PREFLIGHT] SeriesFlix episode: ${servers.length} servers`);
              } catch (err4) {
                console.error(`[PREFLIGHT] All providers failed: ${err4.message}`);
                throw new ApiError(404, "No se encontraron servidores para este contenido");
              }
            } else {
              console.error(`[PREFLIGHT] All providers failed: ${err3.message}`);
              throw new ApiError(404, "No se encontraron servidores para este contenido");
            }
          }
        }
      }
    }

    if (!servers || servers.length === 0) {
      throw new ApiError(404, "No hay servidores disponibles para verificar");
    }

    const TIMEOUT_MS = 16000;
    const AVAIL_TIMEOUT_MS = 10000;

    console.log(`[PREFLIGHT] Verifying ${servers.length} servers in parallel...`);

    async function tryServer(server, index) {
      const embedUrl = server.embedUrl || server.url;
      if (!embedUrl) {
        console.log(`[PREFLIGHT] Server ${index}: skipped (no embedUrl)`);
        return null;
      }

      const start = Date.now();
      try {
        const directUrl = await Promise.race([
          resolveEmbedUrl(embedUrl, embedUrl),
          new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), TIMEOUT_MS)),
        ]);

        if (!directUrl || directUrl === embedUrl) return null;
        if (/\.(html|css|js)(\?.*)?$/i.test(directUrl)) return null;
        if (!/\.(m3u8|mp4|webm|mpd)(\?.*)?$/i.test(directUrl) &&
            !/m3u8|stream|videos?/i.test(directUrl)) return null;

        let latencyMs = Date.now() - start;

        try {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), AVAIL_TIMEOUT_MS);
          const headResp = await fetch(directUrl, {
            method: "HEAD",
            signal: controller.signal,
            headers: { "User-Agent": "Mozilla/5.0 (compatible)" },
          });
          clearTimeout(timer);
          if (!headResp.ok) return null;
          latencyMs = Date.now() - start;
        } catch {
          latencyMs = Date.now() - start;
        }

        console.log(`[PREFLIGHT] Server ${index} OK: ${server.name || server.server} (${server.language}) → ${latencyMs}ms`);
        return {
          name: server.name || server.server || `Servidor ${index + 1}`,
          language: server.language || null,
          quality: server.quality || null,
          embedUrl,
          directUrl,
          latencyMs,
        };
      } catch (err) {
        console.log(`[PREFLIGHT] Server ${index} FAIL: ${server.name || server.server} → ${err.message}`);
        return null;
      }
    }

    let results = await Promise.allSettled(
      servers.map((s, i) => tryServer(s, i))
    );

    let valid = results
      .filter(r => r.status === "fulfilled" && r.value)
      .map(r => r.value);

    let sourceLabel = "preflight";

    if (valid.length === 0) {
      console.log(`[PREFLIGHT] 0 working from initial provider (${servers.length} checked) — cascading to fallbacks...`);

      const fallbacks = [
        { label: "pelisplus", fn: async () => {
          const ppSlug = (slug || "").replace(/^\/?(pelicula|serie|series)\//i, "").replace(/^\d+\//, "").replace(/\/$/, "");
          if (isMovie) {
            const info = await pelisplusService.getContentInfo(ppSlug, "movie");
            return info.servers || [];
          }
          const data = await pelisplusService.getEpisodeServers(ppSlug, Number(season || 1), Number(episode || 1));
          return data.servers || [];
        }},
        { label: "cuevana", fn: async () => {
          const cvName = (slug || "").replace(/^\/?(pelicula|serie|series)\//i, "").replace(/^\d+\//, "").replace(/\/$/, "");
          const data = await cuevanaService.getEpisodeServers(cvName, Number(season || 1), Number(episode || 1));
          return data.servers || [];
        }},
        { label: "seriesflix", fn: async () => {
          if (isMovie) return [];
          const sfSlug = (slug || "").replace(/^\/?(pelicula|serie|series)\//i, "").replace(/^\d+\//, "").replace(/\/$/, "");
          const data = await seriesflixService.getEpisodeServers(sfSlug, Number(season || 1), Number(episode || 1));
          return data.servers || [];
        }},
      ];

      for (const fb of fallbacks) {
        try {
          const fbServers = await fb.fn();
          if (fbServers.length === 0) continue;
          console.log(`[PREFLIGHT] Trying ${fb.label}: ${fbServers.length} servers...`);

          const fbResults = await Promise.allSettled(
            fbServers.map((s, i) => tryServer(s, i))
          );
          valid = fbResults
            .filter(r => r.status === "fulfilled" && r.value)
            .map(r => r.value);

          if (valid.length > 0) {
            sourceLabel = `preflight+${fb.label}`;
            console.log(`[PREFLIGHT] ${fb.label}: ${valid.length}/${fbServers.length} servers working`);
            break;
          }
        } catch (fbErr) {
          console.log(`[PREFLIGHT] ${fb.label} cascade failed: ${fbErr.message}`);
        }
      }
    }

    valid.sort((a, b) => a.latencyMs - b.latencyMs);

    console.log(`[PREFLIGHT] Result: ${valid.length}/${servers.length} servers working`);
    if (valid.length > 0) {
      console.log(`[PREFLIGHT] Best: ${valid[0].name} (${valid[0].language}) → ${valid[0].latencyMs}ms`);
    }

    res.status(200).json({
      success: true,
      data: {
        servers: valid,
        checked: servers.length,
        working: valid.length,
      },
      source: sourceLabel,
    });
  })
);

/**
 * Resolver una URL de embed a enlace directo de video (.mp4, .m3u8)
 * GET /resolve?url=https://streamwish.to/e/xxx
 */
router.get(
  "/resolve",
  asyncHandler(async (req, res) => {
    const embedUrl = req.query.url;
    const parentUrl = req.query.parentUrl || null;
    if (!embedUrl) {
      throw new ApiError(400, "Se requiere el parametro 'url' del embed");
    }

    const directUrl = await resolveEmbedUrl(embedUrl, parentUrl);
    res.status(200).json({
      success: true,
      data: {
        embedUrl,
        directUrl,
      },
      source: "pelisplus",
    });
  })
);

/**
 * Iniciar la descarga de una película o capítulo de serie
 * POST /download
 * Body: { url: "https://www.pelisplushd.la/pelicula/xxx", variant: "Latino", preferredServer: "streamwish" }
 */
router.post(
  "/download",
  asyncHandler(async (req, res) => {
    const baseUrl = `${req.protocol}://${req.get("host")}`;
    const data = downloadService.createDownload(req.body || {}, baseUrl);

    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

/**
 * Obtener estado de una descarga específica
 * GET /download/:id
 */
router.get(
  "/download/:id",
  asyncHandler(async (req, res) => {
    const data = downloadService.getDownload(req.params.id);

    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

/**
 * Iniciar descargas en lote (batch) para múltiples capítulos de una serie
 * POST /batch
 * Body: { mediaUrl: "https://www.pelisplushd.la/serie/xxx", season: 1, episodes: [1, 2, 3], variant: "Latino" }
 */
router.post(
  "/batch",
  asyncHandler(async (req, res) => {
    const baseUrl = `${req.protocol}://${req.get("host")}`;
    const data = downloadService.createBatch(req.body || {}, baseUrl);

    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

/**
 * Obtener estado de una descarga en lote específica
 * GET /batch/:id
 */
router.get(
  "/batch/:id",
  asyncHandler(async (req, res) => {
    const data = downloadService.getBatch(req.params.id);

    res.status(200).json({
      success: true,
      data,
      source: "pelisplus",
    });
  })
);

module.exports = router;
