const cheerio = require("cheerio");
const { fetchHtml } = require("../utils/http");
const { ApiError } = require("../utils/api-error");

const PRIMARY_DOMAIN =
  process.env.CINECALIDAD_DOMAIN ||
  process.env.DEFAULT_CINECALIDAD_DOMAIN ||
  "www.cinecalidad.am";

const MIRRORS = ["www.cinecalidad.am", "www.cinecalidad.rs", "www.cinecalidad.fm", "cinecalidad.onl"]
  .filter((d) => d !== PRIMARY_DOMAIN)
  .filter((d, i, arr) => arr.indexOf(d) === i);

function buildBaseUrl(domain) {
  return domain.startsWith("http") ? domain : `https://${domain}`;
}

function getAbsoluteUrl(path) {
  if (!path) return "";
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  if (path.startsWith("//")) return `https:${path}`;
  return `${buildBaseUrl(PRIMARY_DOMAIN)}${path.startsWith("/") ? "" : "/"}${path}`;
}

/**
 * Intenta cargar el HTML probando cada espejo en orden hasta que uno responda.
 */
async function fetchCinecalidad(path) {
  const domains = [PRIMARY_DOMAIN, ...MIRRORS];
  let lastError = null;

  for (const domain of domains) {
    const url = `${buildBaseUrl(domain)}${path.startsWith("/") ? path : `/${path}`}`;
    try {
      return await fetchHtml(url);
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError || new ApiError(500, "No se pudo contactar ningún espejo de CineCalidad");
}

function detectTypeFromPath(path) {
  if (!path) return "movie";
  if (path.includes("/ver-serie/")) return "series";
  if (path.includes("/ver-pelicula/")) return "movie";
  return "movie";
}

function extractSlugFromPath(path) {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  return parts[parts.length - 1] || "";
}

/**
 * Convierte una URL de CineCalidad a un slug estable para la API.
 * Movie  -> ver-pelicula/<slug>
 * Series -> ver-serie/<slug>
 */
function urlToSlug(path) {
  const clean = (path || "").replace(/^https?:\/\/[^/]+/i, "").replace(/\/+$/, "");
  if (clean.includes("ver-pelicula/")) return `ver-pelicula/${clean.split("ver-pelicula/").pop()}`;
  if (clean.includes("ver-serie/")) return `ver-serie/${clean.split("ver-serie/").pop()}`;
  return clean;
}

/**
 * Parsea una tarjeta de catálogo/búsqueda (article.item)
 */
function mapItem($, element, fallbackType) {
  const el = $(element);
  const link = el.find("a[href*='/ver-pelicula/'], a[href*='/ver-serie/']").first();
  const href = link.attr("href") || "";
  const img = el.find("img.lazy, img[data-src]").first();
  const title = el.find(".in_title").text().trim() || el.find("img").attr("alt") || "";
  const poster = getAbsoluteUrl(img.attr("data-src") || img.attr("src") || "");
  const synopsis = el.find(".custom_synop").text().trim() || "";
  const rating = el.find(".rating").text().trim() || null;
  const type = detectTypeFromPath(href) || fallbackType || "movie";
  const slug = extractSlugFromPath(href);

  let year = null;
  el.find(".home_post_content p").each((_, p) => {
    const text = $(p).text().trim();
    const m = text.match(/\b(19\d\d|20\d\d)\b/);
    if (m && !year) year = m[0];
  });

  const genres = [];
  el.find(".home_post_cat a").each((_, a) => {
    const text = $(a).text().trim();
    if (text && !genres.includes(text)) genres.push(text);
  });

  return {
    id: slug,
    slug: urlToSlug(href),
    title,
    poster,
    synopsis,
    rating: rating ? Number(rating) : null,
    year,
    genres,
    type,
    url: getAbsoluteUrl(href),
    provider: "cinecalidad",
  };
}

function parseServers($) {
  const servers = [];

  $("li.dooplay_player_option[data-option]").each((_, el) => {
    const $el = $(el);
    const embedUrl = ($el.attr("data-option") || "").trim();
    if (!embedUrl) return;

    // Saltar trailers de YouTube
    if (embedUrl.includes("youtube.com") || embedUrl.includes("youtu.be")) return;

    const name = $el.clone().find("span").remove().end().text().trim() || $el.text().trim() || "Desconocido";
    const lower = `${name} ${embedUrl}`.toLowerCase();

    let server = name.toLowerCase().replace(/[^a-z0-9]/g, "") || "unknown";
    if (lower.includes("vimeos") || embedUrl.includes("vimeos")) server = "vimeos";
    else if (lower.includes("voe") || embedUrl.includes("voe.sx")) server = "voesx";
    else if (lower.includes("dood") || embedUrl.includes("dood")) server = "doodstream";
    else if (lower.includes("goodstream") || embedUrl.includes("goodstream")) server = "goodstream";

    servers.push({
      name,
      server,
      language: "Latino",
      embedUrl,
      url: embedUrl,
    });
  });

  return servers;
}

/**
 * Busca contenido en CineCalidad
 * GET ?s=query
 */
async function searchContent(query) {
  if (!query) throw new ApiError(400, "El parametro 'query' es requerido");

  const html = await fetchCinecalidad(`?s=${encodeURIComponent(query)}`);
  const $ = cheerio.load(html);
  const results = [];

  $("article.item, .items article").each((_, el) => {
    const item = mapItem($, el);
    if (item.slug && item.title) results.push(item);
  });

  return results;
}

/**
 * Obtiene el catálogo por tipo, género y página.
 * Movie  -> /ver-pelicula/ | /ver-pelicula/page/N/
 * Series -> /ver-serie/    | /ver-serie/page/N/
 * Género -> /genero-de-la-pelicula/<genre>/
 */
async function getCatalog(type = "movie", genre = "", page = 1) {
  let path = "";
  if (genre) {
    path = `/genero-de-la-pelicula/${encodeURIComponent(genre)}`;
  } else if (type === "series" || type === "serie") {
    path = "/ver-serie";
  } else {
    path = "/ver-pelicula";
  }

  const pageNum = Number(page || 1);
  if (pageNum > 1) path = `${path}/page/${pageNum}`;
  path = `${path}/`;

  const html = await fetchCinecalidad(path);
  const $ = cheerio.load(html);
  const items = [];

  $("article.item, .items article").each((_, el) => {
    const item = mapItem($, el);
    if (item.slug && item.title) items.push(item);
  });

  const hasNextPage = $(".pagination, .pagination-holder, nav.pagination").text().length > 0;

  return {
    items,
    page: pageNum,
    hasNextPage,
  };
}

/**
 * Obtiene la información detallada de una película o serie.
 */
async function getContentInfo(slug, type = "movie") {
  if (!slug) throw new ApiError(400, "El slug del contenido es requerido");

  const normalized = String(slug).replace(/^https?:\/\/[^/]+/i, "").replace(/^\/+/, "").replace(/\/+$/, "");
  const isSeries = type === "series" || type === "serie" || normalized.includes("ver-serie/");
  const isMovie = !isSeries && (type === "movie" || normalized.includes("ver-pelicula/"));

  const path = isSeries
    ? `/ver-serie/${normalized.replace(/^ver-serie\//, "")}/`
    : `/ver-pelicula/${normalized.replace(/^ver-pelicula\//, "")}/`;

  const html = await fetchCinecalidad(path);
  const $ = cheerio.load(html);

  const title = $("#single h1, #contenedor h1, .dtsingle h1").first().text().trim() || "";
  if (!title) throw new ApiError(404, "Contenido no encontrado en CineCalidad");

  const posterEl = $("#single img.alignnone, #single img.size-full, .dtsingle img.alignnone").first();
  const poster = getAbsoluteUrl(posterEl.attr("data-src") || posterEl.attr("src") || "");
  const synopsis = $(".dtsingle td p, #single td p").first().text().trim() || "";

  let rating = null;
  const starWidth = $(".star-ratings-css-top").attr("style") || "";
  const widthMatch = starWidth.match(/width:\s*(\d+(?:\.\d+)?)%/);
  if (widthMatch) rating = Number((Number(widthMatch[1]) / 10).toFixed(1));

  let year = null;
  const pageText = $(".dtsingle, #single").text();
  const yearMatch = pageText.match(/(?:Estreno|T\S*tulos?)[^)]{0,40}?(\b(?:19\d\d|20\d\d)\b)/);
  if (yearMatch) year = yearMatch[1];
  if (!year) {
    const y = pageText.match(/\b(20\d\d|19\d\d)\b/);
    if (y) year = y[0];
  }

  const genres = [];
  $("a[href*='/genero-de-la-pelicula/']").each((_, a) => {
    const text = $(a).text().trim();
    if (text && !genres.includes(text)) genres.push(text);
  });

  const directors = [];
  const cast = [];
  $(".dtsingle td, #single td").each((_, td) => {
    const $td = $(td);
    const text = $td.text();
    if (text.includes("Director:")) {
      $td.find("a[aria-label], a.por").each((_, a) => directors.push($(a).attr("aria-label") || $(a).text().trim()));
    }
    if (text.includes("Elenco:")) {
      $td.find("a[aria-label], a.por").each((_, a) => cast.push($(a).attr("aria-label") || $(a).text().trim()));
    }
  });

  const info = {
    id: normalized,
    slug: urlToSlug(path),
    title,
    originalTitle: title,
    synopsis,
    poster,
    rating,
    year,
    genres,
    directors,
    cast,
    type: isSeries ? "series" : "movie",
    url: getAbsoluteUrl(path),
    provider: "cinecalidad",
  };

  if (isSeries) {
    info.seasons = [];

    $("#jstab, .se-c").each((_, tabEl) => {
      const $tab = $(tabEl);
      const seasonNum = Number($tab.attr("data-tab"));
      if (!seasonNum || seasonNum < 1) return;

      const episodes = [];
      $tab.find("ul.episodios li").each((_, li) => {
        const $li = $(li);
        const a = $li.find("a[href*='/ver-el-episodio/']");
        const href = a.attr("href") || "";
        if (!href) return;

        const epTitle = a.text().trim();
        const match = href.match(/ver-el-episodio\/[^/]*-(\d+)x(\d+)\//);
        let epNum = match ? Number(match[2]) : null;
        if (!epNum) {
          const numerando = $li.find(".numerando").text().trim().match(/E(\d+)/i);
          epNum = numerando ? Number(numerando[1]) : null;
        }
        if (!epNum) return;

        episodes.push({
          number: epNum,
          title: epTitle || `Episodio ${epNum}`,
          url: getAbsoluteUrl(href),
          season: seasonNum,
        });
      });

      episodes.sort((a, b) => a.number - b.number);
      if (episodes.length > 0) {
        info.seasons.push({
          number: seasonNum,
          name: `Temporada ${seasonNum}`,
          episodes,
        });
      }
    });

    info.seasons.sort((a, b) => a.number - b.number);
  } else {
    info.servers = parseServers($);
  }

  return info;
}

/**
 * Obtiene los servidores de un episodio específico.
 * Slug de serie: "ver-serie/animal" | "animal"
 */
async function getEpisodeServers(slug, season, episode) {
  if (!slug) throw new ApiError(400, "Se requiere el slug de la serie");

  const normalized = String(slug).replace(/^https?:\/\/[^/]+/i, "").replace(/^\/+/, "").replace(/\/+$/, "")
    .replace(/^ver-serie\//, "");
  const sNum = Number(season || 1);
  const eNum = Number(episode || 1);
  if (!sNum || !eNum) throw new ApiError(400, "season y episode son requeridos");

  const path = `/ver-el-episodio/${normalized}-${sNum}x${eNum}/`;
  const html = await fetchCinecalidad(path);
  const $ = cheerio.load(html);

  const title = $("h1").first().text().trim() || `Episodio ${sNum}x${eNum}`;
  const servers = parseServers($);

  return {
    title,
    season: sNum,
    episode: eNum,
    servers,
    url: getAbsoluteUrl(path),
    provider: "cinecalidad",
  };
}

/**
 * Obtiene los géneros disponibles desde la home.
 */
async function getGenres() {
  const html = await fetchCinecalidad("/");
  const $ = cheerio.load(html);
  const genres = [];

  $("a[href*='/genero-de-la-pelicula/']").each((_, a) => {
    const text = $(a).text().trim();
    const href = $(a).attr("href") || "";
    const slug = href.split("/genero-de-la-pelicula/").pop().replace(/\/+$/, "");
    if (text && !genres.some((g) => g.slug === slug)) {
      genres.push({ name: text, slug });
    }
  });

  if (genres.length === 0) {
    return [
      { name: "Acción", slug: "accion" },
      { name: "Animación", slug: "animacion" },
      { name: "Anime", slug: "anime" },
      { name: "Aventura", slug: "aventura" },
      { name: "Ciencia Ficción", slug: "ciencia-ficcion" },
      { name: "Comedia", slug: "comedia" },
      { name: "Drama", slug: "drama" },
      { name: "Familia", slug: "familia" },
      { name: "Fantasía", slug: "fantasia" },
      { name: "Misterio", slug: "misterio" },
      { name: "Romance", slug: "romance" },
      { name: "Terror", slug: "terror" },
      { name: "Suspenso", slug: "suspense" },
    ];
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
