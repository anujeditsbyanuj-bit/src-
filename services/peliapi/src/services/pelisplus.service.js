const cheerio = require("cheerio");
const { fetchHtml } = require("../utils/http");
const { ApiError } = require("../utils/api-error");

const rawBaseUrl = process.env.PELISPLUS_DOMAIN || process.env.DEFAULT_PELISPLUS_DOMAIN || "https://www.pelisplushd.la";
const BASE_URL = rawBaseUrl.startsWith("http") ? rawBaseUrl : `https://${rawBaseUrl}`;

/**
 * Normaliza las URLs para que siempre apunten a nuestro dominio o sean absolutas
 */
function getAbsoluteUrl(path) {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}

/**
 * Obtiene el tipo del contenido (movie, series, anime) a partir del path
 */
function detectTypeFromPath(path) {
  if (!path) return "movie";
  if (path.includes("/pelicula/")) return "movie";
  if (path.includes("/serie/")) return "series";
  if (path.includes("/anime/")) return "anime";
  return "movie";
}

/**
 * Extrae el slug a partir del path de PelisPlus
 */
function extractSlugFromPath(path) {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  // Ejemplos:
  // /pelicula/el-padrino -> "el-padrino"
  // /serie/breaking-bad -> "breaking-bad"
  // /anime/naruto -> "naruto"
  return parts[parts.length - 1] || "";
}

/**
 * Busca contenido en PelisPlus
 */
async function searchContent(query) {
  if (!query) {
    throw new ApiError(400, "El parametro de busqueda 's' o 'q' es requerido");
  }

  const url = `${BASE_URL}/search?s=${encodeURIComponent(query)}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);
  const results = [];

  $("a.Posters-link").each((_, element) => {
    const el = $(element);
    const href = el.attr("href") || "";
    const title = el.attr("data-title") || el.find(".listing-content p").text().trim() || "";
    const poster = getAbsoluteUrl(el.find("img.Posters-img").attr("src") || "");
    const ratingText = el.find(".rating span").text().trim();
    const rating = ratingText ? ratingText.split("/")[0] : null;
    const type = detectTypeFromPath(href);
    const slug = extractSlugFromPath(href);

    if (slug) {
      results.push({
        id: slug,
        slug,
        title,
        poster,
        rating,
        type,
        url: getAbsoluteUrl(href),
      });
    }
  });

  return results;
}

/**
 * Obtiene el catalogo por tipo (movies, series, anime), genero, año, etc.
 */
async function getCatalog(type = "movie", genre = "", page = 1) {
  let path = "";
  if (genre) {
    path = `/generos/${genre}`;
  } else {
    switch (type) {
      case "series":
      case "serie":
        path = "/series";
        break;
      case "anime":
        path = "/animes";
        break;
      case "movie":
      case "pelicula":
      default:
        path = "/peliculas";
        break;
    }
  }

  const separator = path.includes("?") ? "&" : "?";
  const url = `${BASE_URL}${path}${separator}page=${page}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);
  const items = [];

  $("a.Posters-link").each((_, element) => {
    const el = $(element);
    const href = el.attr("href") || "";
    const title = el.attr("data-title") || el.find(".listing-content p").text().trim() || "";
    const poster = getAbsoluteUrl(el.find("img.Posters-img").attr("src") || "");
    const ratingText = el.find(".rating span").text().trim();
    const rating = ratingText ? ratingText.split("/")[0] : null;
    const contentType = detectTypeFromPath(href);
    const slug = extractSlugFromPath(href);

    if (slug) {
      items.push({
        id: slug,
        slug,
        title,
        poster,
        rating,
        type: contentType,
        url: getAbsoluteUrl(href),
      });
    }
  });

  // Paginación
  const hasNextPage = $(".pagination a[rel='next']").length > 0 || $(".pagination .page-item:last-child:not(.disabled) a").length > 0;

  return {
    items,
    page: Number(page),
    hasNextPage,
  };
}

/**
 * Obtiene la información detallada de una película o serie
 */
async function getContentInfo(slug, type = "movie") {
  if (!slug) {
    throw new ApiError(400, "El slug del contenido es requerido");
  }

  const path = type === "movie" ? `/pelicula/${slug}` : `/serie/${slug}`;
  const url = `${BASE_URL}${path}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const title = $("h1.m-b-5").text().trim() || $(".font-size-26.font-weight-bold").text().trim() || "";
  if (!title) {
    throw new ApiError(404, "Contenido no encontrado en PelisPlus");
  }

  const originalTitle = $("p.text-opacity").text().trim() || "";
  const synopsis = $(".text-large").text().trim() || $("#synopsis").text().trim() || "";
  const poster = getAbsoluteUrl($("img.img-fluid.m-b-15").attr("src") || $(".img-fluid.rounded").attr("src") || "");
  const ratingVal = $(".font-size-36.font-weight-bold").text().trim();
  const rating = ratingVal ? ratingVal : null;
  const yearMatch = $(".font-size-18.text-info").text().trim().match(/\d{4}/);
  const year = yearMatch ? yearMatch[0] : null;

  const genres = [];
  $("a[href^='/generos/']").each((_, el) => {
    const text = $(el).text().trim();
    const gSlug = $(el).attr("href").split("/").pop();
    if (text) {
      genres.push({ name: text, slug: gSlug });
    }
  });

  // Metadatos adicionales (elenco, director)
  const cast = [];
  const directors = [];
  $(".sectionDetail").each((_, el) => {
    const text = $(el).text();
    if (text.includes("Director:")) {
      $(el).find("a").each((_, a) => directors.push($(a).text().trim()));
    } else if (text.includes("Elenco:")) {
      $(el).find("a").each((_, a) => cast.push($(a).text().trim()));
    }
  });

  const contentInfo = {
    id: slug,
    slug,
    title,
    originalTitle,
    synopsis,
    poster,
    rating,
    year,
    genres,
    cast,
    directors,
    type,
    url,
  };

  if (type === "movie") {
    // Si es película, obtenemos los servidores de reproducción de una vez
    contentInfo.servers = parseServers($);
  } else {
    // Si es serie, obtenemos la estructura de temporadas y capítulos
    contentInfo.seasons = [];
    
    // Las series suelen estructurar temporadas en paneles o listas
    const seasonsTab = $(".divseason");
    if (seasonsTab.length > 0) {
      seasonsTab.each((_, el) => {
        const seasonTitle = $(el).text().trim();
        const seasonNumMatch = seasonTitle.match(/\d+/);
        const seasonNum = seasonNumMatch ? Number(seasonNumMatch[0]) : 1;

        const episodes = [];
        // Buscar links de episodios correspondientes a esta temporada
        // Suelen estar dentro del mismo div o relacionados
        const nextUl = $(el).next("ul");
        nextUl.find("a").each((_, epLink) => {
          const epHref = $(epLink).attr("href") || "";
          const epText = $(epLink).text().trim();
          
          // Formato URL: /serie/{slug}/temporada/{n}/capitulo/{n}
          const epSlugMatch = epHref.match(/temporada\/(\d+)\/capitulo\/(\d+)/i);
          if (epSlugMatch) {
            const epNum = Number(epSlugMatch[2]);
            episodes.push({
              number: epNum,
              title: epText.length > 30 ? `Episodio ${epNum}` : epText || `Episodio ${epNum}`,
              url: getAbsoluteUrl(epHref),
              season: seasonNum,
            });
          }
        });

        // Ordenar episodios por número ascendentemente
        episodes.sort((a, b) => a.number - b.number);

        contentInfo.seasons.push({
          number: seasonNum,
          name: seasonTitle || `Temporada ${seasonNum}`,
          episodes,
        });
      });

      // Ordenar temporadas por número ascendentemente
      contentInfo.seasons.sort((a, b) => a.number - b.number);
    } else {
      // Fallback: buscar cualquier enlace de capítulo en la página
      const episodesMap = new Map();
      $("a[href*='/temporada/']").each((_, epLink) => {
        const epHref = $(epLink).attr("href") || "";
        const epText = $(epLink).text().trim();
        const match = epHref.match(/temporada\/(\d+)\/capitulo\/(\d+)/i);
        if (match) {
          const sNum = Number(match[1]);
          const eNum = Number(match[2]);

          if (!episodesMap.has(sNum)) {
            episodesMap.set(sNum, []);
          }

          episodesMap.get(sNum).push({
            number: eNum,
            title: epText.length > 30 ? `Episodio ${eNum}` : epText || `Episodio ${eNum}`,
            url: getAbsoluteUrl(epHref),
            season: sNum,
          });
        }
      });

      for (const [seasonNum, episodes] of episodesMap.entries()) {
        episodes.sort((a, b) => a.number - b.number);
        contentInfo.seasons.push({
          number: seasonNum,
          name: `Temporada ${seasonNum}`,
          episodes,
        });
      }
      contentInfo.seasons.sort((a, b) => a.number - b.number);
    }
  }

  return contentInfo;
}

/**
 * Obtiene los servidores y URLs de reproducción de un episodio específico
 */
async function getEpisodeServers(serieSlug, seasonNumber, episodeNumber, overrideUrl = "") {
  if (!serieSlug || !seasonNumber || !episodeNumber) {
    throw new ApiError(400, "Los parametros serieSlug, seasonNumber y episodeNumber son requeridos");
  }

  const url = overrideUrl ? getAbsoluteUrl(overrideUrl) : `${BASE_URL}/serie/${serieSlug}/temporada/${seasonNumber}/capitulo/${episodeNumber}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const title = $("h1.m-b-5").text().trim() || "";
  const servers = parseServers($);

  // PelisPlus carga los servidores vía AJAX/JS. Si Cheerio no encontró ninguno
  // (HTML estático vacío), lanzamos error para que el cascade intente otros proveedores.
  if (servers.length === 0) {
    throw new ApiError(404, `PelisPlus: no se encontraron servidores para ${serieSlug} S${seasonNumber}E${episodeNumber} (posible carga dinámica via JS)`);
  }

  return {
    serieSlug,
    season: Number(seasonNumber),
    episode: Number(episodeNumber),
    title,
    servers,
    url,
  };
}

/**
 * Parsea los servidores de la página cargada en Cheerio ($)
 */
function parseServers($) {
  const servers = [];

  // Método 1: Estilo moderno con #link_url span + .TbVideoNv li (usado en series/capítulos)
  if ($("#link_url span").length > 0) {
    const serverNamesMap = new Map();
    $(".TbVideoNv li, .VideoPlayer li").each((_, liEl) => {
      const li = $(liEl);
      const id = li.attr("data-id") || li.attr("lid") || "";
      const name = li.text().trim();
      
      // Intentar deducir el idioma basado en el contenedor del tab (ej. si está dentro de #latino)
      let lang = "Subtitulado";
      const tabPane = li.closest(".tab-pane, .tab_content, [id*='lat'], [id*='sub'], [id*='cast']");
      if (tabPane.length) {
         const paneId = (tabPane.attr("id") || "").toLowerCase();
         if (paneId.includes("lat")) lang = "Latino";
         else if (paneId.includes("cast")) lang = "Castellano";
      } else {
         // Fallback a "Automático" cuando no hay un tab claro de idioma
         const fallbackLang = $(".divseason").text().trim();
         if (fallbackLang && (fallbackLang.toLowerCase() === 'latino' || fallbackLang.toLowerCase() === 'subtitulado' || fallbackLang.toLowerCase() === 'castellano')) {
             lang = fallbackLang;
         } else {
             lang = "Automático";
         }
      }

      if (id && name) {
        serverNamesMap.set(id, { name, language: lang });
      }
    });

    $("#link_url span").each((_, spanEl) => {
      const span = $(spanEl);
      const lid = span.attr("lid") || "";
      const embedUrl = span.attr("url") || "";
      const serverData = serverNamesMap.get(lid) || { name: "Desconocido", language: "Subtitulado" };
      const name = serverData.name;
      const language = serverData.language;

      if (embedUrl) {
        let serverKey = name.toLowerCase().replace(/[^a-z0-9]/g, "");
        if (serverKey.includes("streamwish") || embedUrl.includes("streamwish")) serverKey = "streamwish";
        else if (serverKey.includes("voesx") || serverKey.includes("voe") || embedUrl.includes("voe.sx")) serverKey = "voesx";
        else if (serverKey.includes("streamtape") || embedUrl.includes("streamtape")) serverKey = "streamtape";
        else if (serverKey.includes("netu") || serverKey.includes("hqq") || embedUrl.includes("hqq") || embedUrl.includes("waaw") || embedUrl.includes("netu")) serverKey = "netu";
        else if (serverKey.includes("vidhide") || embedUrl.includes("vidhide")) serverKey = "vidhide";

        servers.push({
          name: name,
          server: serverKey,
          language: language,
          embedUrl: embedUrl,
        });
      }
    });
  }

  // Método 2: Estilo clásico con li.playurl (usado en películas)
  if (servers.length === 0) {
    $("li.playurl").each((_, el) => {
      const element = $(el);
      const embedUrl = element.attr("data-url") || "";
      const language = element.attr("data-name") || "Subtitulado";
      const name = element.find("a").text().trim() || element.text().trim() || "Desconocido";

      if (embedUrl) {
        let serverKey = name.toLowerCase().replace(/[^a-z0-9]/g, "");
        if (serverKey.includes("streamwish") || embedUrl.includes("streamwish")) serverKey = "streamwish";
        else if (serverKey.includes("voesx") || serverKey.includes("voe") || embedUrl.includes("voe.sx")) serverKey = "voesx";
        else if (serverKey.includes("streamtape") || embedUrl.includes("streamtape")) serverKey = "streamtape";
        else if (serverKey.includes("netu") || serverKey.includes("hqq") || embedUrl.includes("hqq") || embedUrl.includes("waaw") || embedUrl.includes("netu")) serverKey = "netu";
        else if (serverKey.includes("vidhide") || embedUrl.includes("vidhide")) serverKey = "vidhide";

        servers.push({
          name: name,
          server: serverKey,
          language: language,
          embedUrl: embedUrl,
        });
      }
    });
  }

  return servers;
}

/**
 * Obtiene la lista completa de géneros disponibles
 */
async function getGenres() {
  const html = await fetchHtml(BASE_URL);
  const $ = cheerio.load(html);
  const genres = [];

  $("a[href^='/generos/']").each((_, el) => {
    const text = $(el).text().trim();
    const slug = $(el).attr("href").split("/").pop();
    if (text && !genres.some((g) => g.slug === slug)) {
      genres.push({ name: text, slug });
    }
  });

  // Fallback si no parseó nada de la home (por ej. si está detrás de un Cloudflare simple)
  if (genres.length === 0) {
    const staticGenres = [
      { name: "Acción", slug: "accion" },
      { name: "Animación", slug: "animacion" },
      { name: "Aventura", slug: "aventura" },
      { name: "Ciencia Ficción", slug: "ciencia-ficcion" },
      { name: "Comedia", slug: "comedia" },
      { name: "Drama", slug: "drama" },
      { name: "Fantasía", slug: "fantasia" },
      { name: "Romance", slug: "romance" },
      { name: "Terror", slug: "terror" },
      { name: "Suspenso", slug: "suspenso" },
      { name: "Anime", slug: "anime" },
    ];
    return staticGenres;
  }

  return genres.sort((a, b) => a.name.localeCompare(b.name));
}

module.exports = {
  searchContent,
  getCatalog,
  getContentInfo,
  getEpisodeServers,
  getGenres,
};
