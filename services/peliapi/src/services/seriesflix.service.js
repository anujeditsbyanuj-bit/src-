const cheerio = require("cheerio");
const { fetchHtml } = require("../utils/http");
const { ApiError } = require("../utils/api-error");

const BASE_URL = "https://seriesflixhd.ws";

function getAbsoluteUrl(path) {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}

function extractSlugFromUrl(url) {
  if (!url) return "";
  const parts = url.split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

async function searchContent(query) {
  if (!query) {
    throw new ApiError(400, "El parametro de busqueda 's' o 'q' es requerido");
  }

  const url = `${BASE_URL}/?s=${encodeURIComponent(query)}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);
  const results = [];

  $("article").each((_, element) => {
    const el = $(element);
    const linkEl = el.find("a").first();
    const href = linkEl.attr("href") || "";
    const title = el.find(".title, h2, h3").first().text().trim() || linkEl.text().trim() || "";
    
    // SeriesFlixHD has posters inside lazy load attributes or src
    const imgEl = el.find("img").first();
    const poster = getAbsoluteUrl(imgEl.attr("src") || imgEl.attr("data-src") || "");

    const slug = extractSlugFromUrl(href);

    if (slug) {
      results.push({
        id: `seriesflix-${slug}`,
        slug,
        title,
        poster,
        rating: null,
        year: null,
        type: href.includes("/serie/") ? "series" : "movie",
        url: getAbsoluteUrl(href),
        provider: "seriesflix",
      });
    }
  });

  return results;
}

async function getContentInfo(slug, type = "series") {
  if (!slug) {
    throw new ApiError(400, "El slug del contenido es requerido");
  }

  // Si el tipo es pelicula, redireccionar a pelisplus o retornar error ya que SeriesFlixHD se enfoca en series
  const path = type === "movie" ? `/pelicula/${slug}/` : `/serie/${slug}/`;
  const url = `${BASE_URL}${path}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const title = $("h1").text().replace("Serie", "").trim() || "";
  if (!title) {
    throw new ApiError(404, "Contenido no encontrado en SeriesFlixHD");
  }

  const synopsis = $(".description p").text().trim() || $(".description").text().trim() || "";
  
  // Buscar poster
  const poster = getAbsoluteUrl($(".poster img").first().attr("src") || $(".poster img").first().attr("data-src") || "");

  const genres = [];
  $('a[href*="/genero/"]').each((_, el) => {
    const text = $(el).text().trim();
    const href = $(el).attr("href") || "";
    const gSlug = href.split("/genero/").pop().replace(/\//g, "");
    if (text) {
      genres.push({ name: text, slug: gSlug });
    }
  });

  const contentInfo = {
    id: `seriesflix-${slug}`,
    slug,
    title,
    originalTitle: title,
    synopsis,
    poster,
    rating: null,
    year: null,
    genres,
    cast: [],
    directors: [],
    type: "series",
    url,
    provider: "seriesflix",
    seasons: [],
  };

  // Buscar temporadas
  const seasonLinks = [];
  $("a[href*='/temporada/']").each((_, el) => {
    const href = $(el).attr("href") || "";
    const text = $(el).text().trim();
    const match = text.match(/\d+/);
    if (href && match) {
      seasonLinks.push({
        number: Number(match[0]),
        url: href,
        name: text,
      });
    }
  });

  // Ordenar temporadas
  seasonLinks.sort((a, b) => a.number - b.number);

  // Obtener episodios para cada temporada
  for (const s of seasonLinks) {
    try {
      const sHtml = await fetchHtml(s.url);
      const s$ = cheerio.load(sHtml);
      const episodes = [];

      s$('a[href*="/episodio/"]').each((_, epEl) => {
        const epHref = s$(epEl).attr("href") || "";
        const epText = s$(epEl).text().trim();
        
        // Formato href: /episodio/slug-seasonxepisode/
        const match = epHref.match(/(\d+)x(\d+)\/?$/);
        if (match) {
          const epNum = Number(match[2]);
          // Evitar duplicados
          if (!episodes.some(e => e.number === epNum)) {
            episodes.push({
              number: epNum,
              title: epText || `Episodio ${epNum}`,
              url: epHref,
              season: s.number,
            });
          }
        }
      });

      episodes.sort((a, b) => a.number - b.number);

      contentInfo.seasons.push({
        number: s.number,
        name: s.name,
        episodes,
      });
    } catch (err) {
      console.error(`Error loading season ${s.number} of ${slug}:`, err.message);
    }
  }

  return contentInfo;
}

async function getEpisodeServers(serieSlug, seasonNumber, episodeNumber) {
  if (!serieSlug || !seasonNumber || !episodeNumber) {
    throw new ApiError(400, "Los parametros serieSlug, seasonNumber y episodeNumber son requeridos");
  }

  const url = `${BASE_URL}/episodio/${serieSlug}-${seasonNumber}x${episodeNumber}/`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const title = $("h1").text().trim() || `Episodio ${episodeNumber}`;
  const servers = [];

  $(".sgty").each((_, el) => {
    const dataUrl = $(el).attr("data-url") || "";
    const text = $(el).text().trim();
    if (dataUrl) {
      try {
        let embedUrl = Buffer.from(dataUrl, "base64").toString("utf-8");
        
        // Si el embed está dentro de un wrapper como nupload.top/iframe/?url=xxx, extraemos la URL real
        if (embedUrl.includes("?url=")) {
          const qs = new URL(embedUrl).searchParams;
          const realUrl = qs.get("url");
          if (realUrl) {
            embedUrl = realUrl;
          }
        }

        let serverKey = "unknown";
        let serverName = text.split("•").pop().trim();

        const lowerUrl = embedUrl.toLowerCase();
        if (lowerUrl.includes("streamwish") || lowerUrl.includes("wish")) serverKey = "streamwish";
        else if (lowerUrl.includes("voe.sx") || lowerUrl.includes("voe")) serverKey = "voesx";
        else if (lowerUrl.includes("streamtape")) serverKey = "streamtape";
        else if (lowerUrl.includes("netu") || lowerUrl.includes("hqq") || lowerUrl.includes("waaw")) serverKey = "netu";
        else if (lowerUrl.includes("vidhide")) serverKey = "vidhide";

        servers.push({
          name: serverName || serverKey,
          server: serverKey,
          language: text.includes("LATINO") ? "Español Latino" : (text.includes("CASTELLANO") ? "Español" : "Subtitulado"),
          embedUrl,
        });
      } catch (e) {
        console.error("Error parsing base64 data-url:", e.message);
      }
    }
  });

  return {
    serieSlug,
    season: Number(seasonNumber),
    episode: Number(episodeNumber),
    title,
    servers,
    url,
  };
}

async function getEpisodeServersByTitle(title, season, episode) {
  if (!title) return null;
  try {
    const results = await searchContent(title);
    if (!results || results.length === 0) return null;
    const series = results.find(r => r.type === "series") || results[0];
    if (!series?.slug) return null;
    return await getEpisodeServers(series.slug, season, episode);
  } catch {
    return null;
  }
}

module.exports = {
  searchContent,
  getContentInfo,
  getEpisodeServers,
  getEpisodeServersByTitle,
};
