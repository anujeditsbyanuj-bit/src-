const cheerio = require("cheerio");
const { fetchHtml } = require("../utils/http");
const { ApiError } = require("../utils/api-error");
const axios = require("axios");

const BASE_URL = process.env.REPELISHD_DOMAIN || "https://repelishd.ceo";

/**
 * Normaliza las URLs para que siempre sean absolutas
 */
function getAbsoluteUrl(path) {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}

/**
 * Busca películas en RePelisHD usando la consulta POST de DLE
 */
async function searchContent(query) {
  if (!query) {
    throw new ApiError(400, "El parametro de busqueda 's' o 'q' es requerido");
  }

  const url = `${BASE_URL}/index.php?do=search`;
  const postData = `do=search&subaction=search&story=${encodeURIComponent(query)}`;

  try {
    const response = await axios.post(url, postData, {
      timeout: 15000,
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        Referer: `${BASE_URL}/`,
      },
    });

    const $ = cheerio.load(response.data);
    const results = [];

    // En DLE, los resultados de búsqueda están contenidos dentro de div#dle-content
    $("#dle-content article.item").each((_, element) => {
      const el = $(element);
      const posterDiv = el.find(".poster");
      const posterLink = posterDiv.find("a");
      const href = posterLink.attr("href") || "";
      const img = posterDiv.find("img");
      const poster = getAbsoluteUrl(img.attr("src") || img.attr("data-src") || "");
      
      const titleLink = el.find(".data h3 a");
      const title = titleLink.text().trim();

      const ratingText = posterDiv.find(".rating").text().trim();
      const rating = ratingText ? ratingText : null;
      
      const yearText = el.find(".data span, .poster span").text().trim();
      const year = yearText ? yearText.replace("HD", "").trim() : null;

      const slug = href.split("/ver-pelicula/").pop().replace(".html", "");

      if (slug) {
        results.push({
          id: slug,
          slug,
          title,
          poster,
          rating,
          year,
          type: "movie",
          url: getAbsoluteUrl(href),
        });
      }
    });

    // De-duplicar resultados por slug
    const uniqueResults = [];
    const seen = new Set();
    for (const r of results) {
      if (!seen.has(r.slug)) {
        seen.add(r.slug);
        uniqueResults.push(r);
      }
    }

    return uniqueResults;
  } catch (error) {
    console.error("Error in RePelisHD searchContent:", error.message);
    return [];
  }
}

/**
 * Obtiene la información detallada y los servidores de una película en RePelisHD
 */
async function getContentInfo(slug, type = "movie") {
  if (!slug) {
    throw new ApiError(400, "El slug del contenido es requerido");
  }

  const path = `/ver-pelicula/${slug}.html`;
  const url = `${BASE_URL}${path}`;
  
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const title = $("h1").not(".text").text().replace(" online HD", "").replace(" online", "").trim() || $(".title").first().text().trim() || "";
  if (!title) {
    throw new ApiError(404, "Contenido no encontrado en RePelisHD");
  }

  const synopsis = $(".description p").text().trim() || $(".description").text().trim() || "";
  const poster = getAbsoluteUrl($(".poster img").first().attr("src") || $(".poster img").first().attr("data-src") || "");

  let year = null;
  const yearMatch = $(".meta, .info, body").text().match(/\d{4}/);
  if (yearMatch) year = yearMatch[0];

  let rating = $(".starstruck-rating, .dt_rating_vgs").first().text().trim() || null;

  const genres = [];
  $('a[href*="/genero/"]').each((_, el) => {
    const text = $(el).text().trim();
    const href = $(el).attr("href") || "";
    const gSlug = href.split("/genero/").pop().replace(/\//g, "");
    if (text && !genres.some(g => g.slug === gSlug)) {
      genres.push({ name: text, slug: gSlug });
    }
  });

  const contentInfo = {
    id: slug,
    slug,
    title,
    originalTitle: title,
    synopsis,
    poster,
    rating,
    year,
    genres,
    cast: [],
    directors: [],
    type: "movie",
    url,
  };

  const servers = [];
  
  // Buscar iframe del resolver en la página de RePelisHD
  const iframeSrc = $("iframe").first().attr("src") || $("iframe").first().attr("data-src") || "";
  if (iframeSrc && iframeSrc.includes("verhdlink.cam")) {
    try {
      // Cargar el player resolver enviando cabecera Referer
      const resolverHtml = await fetchHtml(iframeSrc, { Referer: url });
      const r$ = cheerio.load(resolverHtml);

      // Idiomas: Latino, Castellano, Subtitulado
      const languages = [
        { key: "latino", label: "Latino" },
        { key: "castellano", label: "Castellano" },
        { key: "subtitulado", label: "Subtitulado" }
      ];

      for (const lang of languages) {
        r$(`ul.${lang.key} li`).each((_, el) => {
          const mirror = r$(el);
          const dataLink = mirror.attr("data-link") || "";
          let text = mirror.text().trim().toLowerCase();

          if (dataLink) {
            let embedUrl = dataLink;
            if (embedUrl.startsWith("//")) {
              embedUrl = `https:${embedUrl}`;
            }

            // Identificar clave del servidor y nombre descriptivo
            let serverKey = "unknown";
            let serverName = "Directo";

            if (text.includes("dropload") || embedUrl.includes("dr0pstream") || embedUrl.includes("dropload")) {
              serverKey = "dropload";
              serverName = "Dropload";
            } else if (text.includes("mixdrop") || embedUrl.includes("mixdrop")) {
              serverKey = "mixdrop";
              serverName = "Mixdrop";
            } else if (text.includes("doodstream") || text.includes("dood") || embedUrl.includes("dood")) {
              serverKey = "doodstream";
              serverName = "Doodstream";
            } else if (text.includes("streamwish") || embedUrl.includes("streamwish")) {
              serverKey = "streamwish";
              serverName = "Streamwish";
            } else if (text.includes("fullhd") || text.includes("4k") || text.includes("server 4k")) {
              serverKey = "server4k";
              serverName = "Server 4K";
            }

            servers.push({
              name: serverName,
              server: serverKey,
              language: lang.label,
              embedUrl,
            });
          }
        });
      }
    } catch (err) {
      console.error("Error fetching player mirrors from verhdlink resolver:", err.message);
    }
  }

  contentInfo.servers = servers;
  return contentInfo;
}

/**
 * Placeholder para compatibilidad con series
 */
async function getEpisodeServers(serieSlug, seasonNumber, episodeNumber) {
  return {
    serieSlug,
    season: Number(seasonNumber),
    episode: Number(episodeNumber),
    servers: [],
    url: "",
  };
}

module.exports = {
  searchContent,
  getContentInfo,
  getEpisodeServers,
};
