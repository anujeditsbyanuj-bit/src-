const cheerio = require("cheerio");
const { fetchHtml, fetchHtmlWithHeaders } = require("../utils/http");
const { ApiError } = require("../utils/api-error");

const BASE_URL = "https://www.poseidonhd2.co";
const PLAYER_URL = "https://player.poseidonhd2.co";

function extractNextData(html) {
  const match = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (!match) return null;
  try {
    return JSON.parse(match[1]);
  } catch {
    return null;
  }
}

function normalizeUrlSlug(url) {
  if (!url) return "";
  if (url.startsWith("http")) return url;
  return url.startsWith("/") ? url : `/${url}`;
}

async function fetchPoseidonPage(path) {
  const url = `${BASE_URL}${normalizeUrlSlug(path)}`;
  const html = await fetchHtml(url);
  const data = extractNextData(html);
  if (!data) {
    throw new ApiError(404, `No se pudo cargar la pagina: ${url}`);
  }
  return data;
}

async function resolvePlayerUrl(playerPhpUrl) {
  try {
    const html = await fetchHtml(playerPhpUrl);
    const match = html.match(/var url = '([^']+)'/);
    if (match && match[1]) {
      return match[1];
    }
    return null;
  } catch {
    return null;
  }
}

function detectSlugType(slug) {
  if (!slug) return null;
  if (/^series\//.test(slug)) return "series";
  if (/^movies\//.test(slug) || /^movies\./.test(slug)) return "movie";
  return null;
}

function mapSearchResult(item, fallbackType) {
  const titles = item.titles || {};
  const name = titles.name || item.title || "";
  const slug = item.url?.slug || "";
  const images = item.images || {};
  const poster = images.poster || "";
  const backdrop = images.backdrop || "";
  const overview = item.overview || "";
  const rating = item.rate?.average || null;
  const year = item.releaseDate ? new Date(item.releaseDate).getFullYear() : null;
  const tmdbId = item.TMDbId || null;
  const genres = (item.genres || []).map(g => g.name).join(", ");

  const type = detectSlugType(slug) || fallbackType || "movie";

  let detailUrl = "";
  let fullSlug = "";
  if (type === "series" && slug) {
    const cleanSlug = slug.replace(/^series\/[^/]+\//, "");
    detailUrl = `/serie/${tmdbId || ""}/${cleanSlug}`;
    fullSlug = `/serie/${tmdbId || ""}/${cleanSlug}`;
  } else if (slug) {
    const cleanSlug = slug.replace(/^movies\/[^/]+\//, "");
    detailUrl = `/pelicula/${tmdbId || ""}/${cleanSlug}`;
    fullSlug = `/pelicula/${tmdbId || ""}/${cleanSlug}`;
  }

  return {
    id: `poseidon-${tmdbId || slug}`,
    title: name,
    poster,
    backdrop,
    overview,
    rating,
    year,
    genres,
    type,
    url: detailUrl,
    slug: fullSlug || String(tmdbId || slug),
    tmdbId,
    provider: "poseidon",
  };
}

function mapServers(videos, downloads) {
  const servers = [];
  const langMap = {
    latino: "Español Latino",
    spanish: "Español",
    english: "Inglés",
    sub: "Subtitulado",
    subtitulado: "Subtitulado",
  };

  for (const [langKey, serversList] of Object.entries(videos || {})) {
    if (!Array.isArray(serversList) || serversList.length === 0) continue;
    const language = langMap[langKey] || langKey;

    for (const server of serversList) {
      servers.push({
        name: server.cyberlocker || "unknown",
        language,
        quality: server.quality || "HD",
        url: server.result || "",
        embedUrl: server.result || "",
        _playerUrl: server.result || "",
      });
    }
  }

  return servers;
}

async function resolveAllPlayerUrls(servers) {
  const resolvePromises = servers.map(async (server) => {
    if (server._playerUrl && server._playerUrl.includes("player.php")) {
      try {
        const embedUrl = await resolvePlayerUrl(server._playerUrl);
        if (embedUrl) {
          server.embedUrl = embedUrl;
          server.url = embedUrl;
        }
      } catch {
        // Keep original URL if resolution fails
      }
    }
    delete server._playerUrl;
  });
  await Promise.all(resolvePromises);
  return servers;
}

async function searchContent(query) {
  if (!query) throw new ApiError(400, "El parametro 'query' es requerido");

  const data = await fetchPoseidonPage(`/search?q=${encodeURIComponent(query)}`);
  const pageProps = data?.props?.pageProps || {};

  const allItems = [
    ...(pageProps.movies || []).map(item => mapSearchResult(item, "movie")),
    ...(pageProps.series || []).map(item => mapSearchResult(item, "series")),
  ];
  const allResults = allItems;

  if (allResults.length === 0) {
    throw new ApiError(404, `No se encontraron resultados para: ${query}`);
  }

  return allResults;
}

async function getContentInfo(path, type) {
  if (!path) throw new ApiError(400, "Se requiere una ruta o ID de contenido");

  let data;

  // Si el path es solo un número, buscar por ese TMDbId
  const pureNumber = path.replace(/[^0-9]/g, "");
  const isNumericOnly = /^\d+$/.test(path.trim());

  if (isNumericOnly && pureNumber) {
    // Buscar en PoseidonHD por el TMDbId y usar el primer resultado
    try {
      const searchUrl = `${BASE_URL}/search?q=${pureNumber}`;
      const searchHtml = await fetchHtml(searchUrl);
      const searchData = extractNextData(searchHtml);
      const movies = searchData?.props?.pageProps?.movies || [];
      const match = movies.find(m => String(m.TMDbId) === pureNumber);
      if (match?.url?.slug) {
        const slugPath = `/pelicula/${pureNumber}/${match.url.slug.replace(/^movies\/[^/]+\//, "")}`;
        data = await fetchPoseidonPage(slugPath);
      } else {
        data = await fetchPoseidonPage(`/pelicula/${pureNumber}/`);
      }
    } catch {
      throw new ApiError(404, `No se encontro contenido con TMDbId ${pureNumber} en PoseidonHD`);
    }
  } else {
    try {
      data = await fetchPoseidonPage(path);
    } catch {
      if (pureNumber) {
        data = await fetchPoseidonPage(`/pelicula/${pureNumber}/`);
      } else {
        throw new ApiError(404, `No se pudo obtener info para: ${path}`);
      }
    }
  }

  const pageProps = data?.props?.pageProps || {};
  const isMovie = type === "movie" || path.includes("/pelicula/");

  let contentData;
  if (isMovie) {
    contentData = pageProps.thisMovie || {};
  } else {
    contentData = pageProps.thisSerie || {};
  }

  if (!contentData || Object.keys(contentData).length === 0) {
    throw new ApiError(404, `Contenido no encontrado en PoseidonHD`);
  }

  const titles = contentData.titles || {};
  const name = titles.name || contentData.title || "";
  const slug = contentData.url?.slug || path;
  const images = contentData.images || {};
  const poster = images.poster || "";
  const backdrop = images.backdrop || "";
  const overview = contentData.overview || "";
  const rating = contentData.rate?.average || null;
  const year = contentData.releaseDate ? new Date(contentData.releaseDate).getFullYear() : null;
  const tmdbId = contentData.TMDbId || null;
  const genres = (contentData.genres || []).map(g => g.name || g);
  const runtime = contentData.runtime || null;

  const result = {
    id: `poseidon-${tmdbId || slug}`,
    title: name,
    poster,
    backdrop,
    overview,
    rating,
    year,
    genres,
    runtime,
    type: isMovie ? "movie" : "series",
    slug: tmdbId ? String(tmdbId) : slug,
    tmdbId,
    provider: "poseidon",
  };

  if (isMovie) {
    const videos = contentData.videos || {};
    const downloads = contentData.downloads || [];
    result.servers = await resolveAllPlayerUrls(mapServers(videos, downloads));
    result.variants = Object.keys(videos).map(lang => {
      const langMap = {
        latino: "Español Latino",
        spanish: "Español",
        english: "Inglés",
        sub: "Subtitulado",
      };
      return langMap[lang] || lang;
    });
  } else {
    const seasons = (contentData.seasons || [])
      .filter(s => s.number > 0 && s.episodes && s.episodes.length > 0)
      .map(s => ({
        number: s.number,
        episodeCount: s.episodes.length,
        episodes: s.episodes.map(ep => ({
          number: ep.number,
          title: ep.title || `Episodio ${ep.number}`,
          url: ep.url?.slug || "",
          tmdbId: ep.TMDbId || null,
          image: ep.image || "",
          releaseDate: ep.releaseDate || null,
        })),
      }));
    result.seasons = seasons;
  }

  return result;
}

async function getEpisodeServers(slug, season, episode) {
  if (!slug) throw new ApiError(400, "Se requiere el slug/ID de la serie");

  const tmdbId = slug.replace(/[^0-9]/g, "");
  if (!tmdbId) {
    throw new ApiError(400, "Se requiere el TMDbId de la serie para PoseidonHD");
  }

  let pagePath = `/serie/${tmdbId}/_/temporada/${season}/episodio/${episode}`;

  let data;
  try {
    data = await fetchPoseidonPage(pagePath);
  } catch {
    const altPath = `/serie/${tmdbId}/_/seasons/${season}/episodes/${episode}`;
    try {
      data = await fetchPoseidonPage(altPath);
    } catch {
      throw new ApiError(404, `No se encontro el episodio ${season}x${episode} en PoseidonHD`);
    }
  }

  const pageProps = data?.props?.pageProps || {};
  const episodeData = pageProps.episode || {};

  if (!episodeData || Object.keys(episodeData).length === 0) {
    throw new ApiError(404, `No se encontraron servidores para el episodio ${season}x${episode}`);
  }

  const videos = episodeData.videos || {};
  const downloads = episodeData.downloads || [];
  const servers = await resolveAllPlayerUrls(mapServers(videos, downloads));

  return {
    title: episodeData.title || `Episodio ${season}x${episode}`,
    season,
    episode,
    servers,
    variants: Object.keys(videos).map(lang => {
      const langMap = {
        latino: "Español Latino",
        spanish: "Español",
        english: "Inglés",
        sub: "Subtitulado",
      };
      return langMap[lang] || lang;
    }),
  };
}

async function getEpisodeServersByTitle(title, season, episode) {
  if (!title) return null;

  try {
    const results = await searchContent(title);
    if (!results || results.length === 0) return null;

    const seriesResult = results.find(r => r.type === "series") || results[0];
    if (!seriesResult?.slug) return null;

    return await getEpisodeServers(seriesResult.slug, season, episode);
  } catch {
    return null;
  }
}

async function getMovieServers(slug) {
  if (!slug) throw new ApiError(400, "Se requiere el slug/ID de la pelicula");

  const tmdbId = slug.replace(/[^0-9]/g, "");
  if (!tmdbId) {
    throw new ApiError(400, "Se requiere el TMDbId de la pelicula para PoseidonHD");
  }

  let pagePath = `/pelicula/${tmdbId}/_`;

  let data;
  try {
    data = await fetchPoseidonPage(pagePath);
  } catch {
    throw new ApiError(404, `No se encontro la pelicula en PoseidonHD`);
  }

  const pageProps = data?.props?.pageProps || {};
  const movieData = pageProps.thisMovie || {};

  if (!movieData || Object.keys(movieData).length === 0) {
    throw new ApiError(404, `No se encontraron servidores para la pelicula`);
  }

  const videos = movieData.videos || {};
  const downloads = movieData.downloads || [];
  const servers = await resolveAllPlayerUrls(mapServers(videos, downloads));

  return {
    title: movieData.titles?.name || "Pelicula",
    servers,
    variants: Object.keys(videos).map(lang => {
      const langMap = {
        latino: "Español Latino",
        spanish: "Español",
        english: "Inglés",
        sub: "Subtitulado",
      };
      return langMap[lang] || lang;
    }),
  };
}

module.exports = {
  searchContent,
  getContentInfo,
  getEpisodeServers,
  getEpisodeServersByTitle,
  getMovieServers,
  resolvePlayerUrl,
};
