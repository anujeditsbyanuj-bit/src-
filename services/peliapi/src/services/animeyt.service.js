const cheerio = require("cheerio");
const { fetchHtml, fetchHtmlWithHeaders } = require("../utils/http");
const { scrapeWithPage } = require("../utils/browser");
const { ApiError } = require("../utils/api-error");

const BASE_URL = "https://animeyt.cc";
const MYTSUMI_BASE = "https://mytsumi.com";

function slugify(text) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

async function searchContent(query) {
  if (!query) throw new ApiError(400, "El parametro 'query' es requerido");

  const url = `${BASE_URL}/?s=${encodeURIComponent(query)}`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);
  const results = [];

  $("article").each((_, el) => {
    const titleEl = $(el).find("h2 a, h3 a, .entry-title a").first();
    const imgEl = $(el).find("img").first();
    const href = titleEl.attr("href") || "";
    const title = titleEl.text().trim();

    if (!href || !title) return;

    const catMatch = href.match(/\/category\/([^/]+)/);
    if (!catMatch) return;

    const slug = catMatch[1];
    const poster = imgEl.attr("src") || imgEl.attr("data-src") || "";

    results.push({
      id: `animeyt-${slug}`,
      slug,
      title,
      poster,
      rating: null,
      year: null,
      type: "anime",
      url: href,
      provider: "animeyt",
    });
  });

  if (results.length === 0) {
    const links = [];
    $("a[href*='/category/']").each((_, el) => {
      const href = $(el).attr("href") || "";
      const text = $(el).text().trim();
      const catMatch = href.match(/\/category\/([^/]+)/);
      if (catMatch && text) {
        links.push({ slug: catMatch[1], title: text, url: href });
      }
    });

    const seen = new Set();
    for (const link of links) {
      if (seen.has(link.slug)) continue;
      seen.add(link.slug);

      const lowerTitle = link.title.toLowerCase();
      const lowerQuery = query.toLowerCase();
      if (lowerTitle.includes(lowerQuery) || lowerQuery.includes(lowerTitle) || slugify(link.title) === slugify(query)) {
        results.push({
          id: `animeyt-${link.slug}`,
          slug: link.slug,
          title: link.title,
          poster: "",
          rating: null,
          year: null,
          type: "anime",
          url: link.url,
          provider: "animeyt",
        });
      }
    }
  }

  return results;
}

async function getContentInfo(slug, type = "anime") {
  if (!slug) throw new ApiError(400, "El slug del contenido es requerido");

  const url = `${BASE_URL}/category/${slug}/`;
  const html = await fetchHtml(url);
  const $ = cheerio.load(html);

  const titleEl = $("h1 span").first();
  let title = "";
  if (titleEl.length && !titleEl.find("img").length) {
    title = titleEl.text().trim();
  }
  if (!title) {
    title = $("h1").clone().children().remove().end().text().trim();
  }
  if (!title) {
    title = $("h1").text().replace(/AnimeYT.*$/i, "").trim();
  }
  if (!title) throw new ApiError(404, "Anime no encontrado en AnimeYT");

  const synopsis = $(".entry-content p, .description, .synopsis").first().text().trim() || "";
  const poster = $(".post-thumbnail img, .featured-image img, article img").first().attr("src") || "";
  const rating = $(".rating, .score, .rating-score").first().text().trim() || null;

  const episodes = [];
  $("a[href*='/anime/']").each((_, el) => {
    const href = $(el).attr("href") || "";
    const epMatch = href.match(/\/(\d+)\/anime\/(.+?)(?:-capitulo-(\d+))?\/?$/);
    if (epMatch) {
      const epNum = epMatch[3] ? Number(epMatch[3]) : null;
      episodes.push({
        number: epNum,
        title: `Episodio ${epNum || ""}`.trim(),
        url: href,
        id: epMatch[1],
        slug: epMatch[2],
      });
    }
  });

  episodes.sort((a, b) => (a.number || 0) - (b.number || 0));

  const seasonsMap = new Map();
  for (const ep of episodes) {
    if (ep.number === null) continue;
    const seasonNum = 1;
    if (!seasonsMap.has(seasonNum)) seasonsMap.set(seasonNum, []);
    seasonsMap.get(seasonNum).push({ ...ep, season: seasonNum });
  }

  const seasons = [];
  for (const [num, eps] of seasonsMap.entries()) {
    seasons.push({
      number: num,
      name: `Temporada ${num}`,
      episodes: eps,
    });
  }
  seasons.sort((a, b) => a.number - b.number);

  return {
    id: slug,
    slug,
    title,
    originalTitle: title,
    synopsis,
    poster,
    rating,
    year: null,
    genres: [],
    cast: [],
    directors: [],
    type: "anime",
    url,
    seasons: seasons.length > 0 ? seasons : undefined,
    episodes: seasons.length === 0 && episodes.length > 0 ? episodes : undefined,
  };
}

async function getEpisodeServers(episodeId, seasonNumber, episodeNumber) {
  if (!episodeId) throw new ApiError(400, "El episodeId (URL del episodio) es requerido");

  let episodeUrl = episodeId;
  if (!episodeUrl.startsWith("http")) {
    episodeUrl = `${BASE_URL}/${episodeId}/`;
  }

  const pageData = await scrapeWithPage(episodeUrl, () => {
    const title = document.querySelector("h1")?.textContent?.trim() || "";
    const embedHolder = document.querySelector("#embed_holder");
    if (!embedHolder) return { title, servers: [] };

    const servers = [];
    const iframes = embedHolder.querySelectorAll("iframe[data-src]");
    for (const iframe of iframes) {
      const dataSrc = iframe.getAttribute("data-src") || "";
      servers.push({ dataSrc: dataSrc.trim(), src: "" });
    }

    const noscriptIframes = embedHolder.querySelectorAll("noscript iframe");
    for (const iframe of noscriptIframes) {
      const src = iframe.getAttribute("src") || "";
      if (src && !servers.some((s) => s.dataSrc === src || s.src === src)) {
        servers.push({ dataSrc: "", src: src.trim() });
      }
    }

    return { title, servers };
  }, { waitUntil: "networkidle2", extraWait: 3000 });

  const title = pageData.title || `Episodio ${episodeNumber}`;
  const servers = [];

  for (const srv of pageData.servers || []) {
    const url = srv.dataSrc || srv.src;
    if (!url) continue;

    if (url.includes("mytsumi.com")) {
      const valueMatch = url.match(/value=([^&\s]+)/);
      if (valueMatch) {
        try {
          const mytsumiServers = await fetchMytsumiServers(valueMatch[1]);
          servers.push(...mytsumiServers);
        } catch (e) {
          console.error("Error fetching mytsumi servers:", e.message);
        }
      }
    } else if (url.includes("terabox.com")) {
      servers.push({
        name: "TeraBox",
        server: "terabox",
        language: "Latino",
        embedUrl: url,
        is_mp4: false,
      });
    } else if (url.includes("/new/play/")) {
      const serverMatch = url.match(/server=([^&]+)/);
      const valueMatch = url.match(/value=([^&\s]+)/);
      const serverName = serverMatch ? serverMatch[1] : "unknown";

      if (serverName === "tera" && valueMatch) {
        const teraboxUrl = `https://terabox.com/sharing/embed?surl=${valueMatch[1]}&resolution=1080&autoplay=true&mute=false`;
        servers.push({
          name: "TeraBox",
          server: "terabox",
          language: "Latino",
          embedUrl: teraboxUrl,
          is_mp4: false,
        });
      } else {
        servers.push({
          name: serverName,
          server: serverName,
          language: "Latino",
          embedUrl: url,
          is_mp4: false,
        });
      }
    } else {
      let serverKey = "unknown";
      if (url.includes("streamtape")) serverKey = "streamtape";
      else if (url.includes("voe.sx") || url.includes("voe")) serverKey = "voesx";
      else if (url.includes("streamwish")) serverKey = "streamwish";
      else if (url.includes("ok.ru")) serverKey = "okru";
      else if (url.includes("mega.nz")) serverKey = "mega";
      else if (url.includes("archive.org")) serverKey = "direct";

      servers.push({
        name: serverKey,
        server: serverKey,
        language: "Latino",
        embedUrl: url,
        is_mp4: false,
      });
    }
  }

  if (servers.length === 0) {
    throw new ApiError(404, "No se encontraron servidores para este episodio");
  }

  return {
    episodeId,
    season: Number(seasonNumber || 1),
    episode: Number(episodeNumber || 1),
    title,
    servers,
    url: episodeUrl,
  };
}

async function fetchMytsumiServers(value) {
  const optionsUrl = `${MYTSUMI_BASE}/multiplayer/options.php?server=multi&value=${encodeURIComponent(value)}`;
  const optionsHtml = await fetchHtml(optionsUrl);

  const encodedMatch = optionsHtml.match(/azakuEncodedURL\s*=\s*["']([A-Za-z0-9+/=]+)["']/);
  if (!encodedMatch) return [];

  let contenedorUrl;
  try {
    contenedorUrl = Buffer.from(encodedMatch[1], "base64").toString("utf-8");
  } catch {
    return [];
  }

  const contenedorHtml = await fetchHtmlWithHeaders(contenedorUrl, optionsUrl);
  const rawHtml = contenedorHtml.html || "";

  const videoTabsMatch = rawHtml.match(/const videoTabs\s*=\s*(\[[\s\S]*?\]);/);
  if (!videoTabsMatch) return [];

  let videoTabs;
  try {
    videoTabs = JSON.parse(videoTabsMatch[1]);
  } catch {
    return [];
  }

  return videoTabs
    .filter((tab) => tab.url && tab.status === "active")
    .map((tab) => {
      const embedUrl = tab.url;
      let serverKey = tab.tab_name.toLowerCase().replace(/[^a-z0-9]/g, "");

      if (embedUrl.includes("streamtape")) serverKey = "streamtape";
      else if (embedUrl.includes("voe.sx") || embedUrl.includes("voe")) serverKey = "voesx";
      else if (embedUrl.includes("streamwish")) serverKey = "streamwish";
      else if (embedUrl.includes("ok.ru")) serverKey = "okru";
      else if (embedUrl.includes("mega.nz")) serverKey = "mega";
      else if (embedUrl.includes("archive.org") || tab.is_mp4) serverKey = "direct";

      return {
        name: tab.tab_name,
        server: serverKey,
        language: "Latino",
        embedUrl,
        is_mp4: tab.is_mp4 || false,
      };
    });
}

async function getEpisodeServersByTitle(title, seasonNumber, episodeNumber) {
  const searchResults = await searchContent(title);
  if (!searchResults || searchResults.length === 0) {
    throw new ApiError(404, `No se encontro anime "${title}" en AnimeYT`);
  }

  const anime = searchResults[0];
  const info = await getContentInfo(anime.slug);

  if (info.seasons && info.seasons.length > 0) {
    const season = info.seasons.find((s) => s.number === Number(seasonNumber));
    if (season) {
      const ep = season.episodes.find((e) => e.number === Number(episodeNumber));
      if (ep && ep.url) {
        return getEpisodeServers(ep.url, seasonNumber, episodeNumber);
      }
    }
  } else if (info.episodes && info.episodes.length > 0) {
    const ep = info.episodes.find((e) => e.number === Number(episodeNumber));
    if (ep && ep.url) {
      return getEpisodeServers(ep.url, seasonNumber, episodeNumber);
    }
  }

  throw new ApiError(404, `Episodio ${seasonNumber}x${episodeNumber} no encontrado`);
}

module.exports = {
  searchContent,
  getContentInfo,
  getEpisodeServers,
  getEpisodeServersByTitle,
};
