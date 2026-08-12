const axios = require("axios");
const cheerio = require("cheerio");
const vm = require("node:vm");
const { URL } = require("node:url");
const { ApiError } = require("../utils/api-error");

const DEFAULT_DOMAIN = "tioanime.com";

const HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
};

async function fetchHtml(url) {
  try {
    const timeout = Number(process.env.REQUEST_TIMEOUT_MS || 15000);
    const response = await axios.get(url, {
      timeout,
      headers: HTTP_HEADERS,
      maxRedirects: 5,
      validateStatus: (status) => status >= 200 && status < 400,
    });
    return response.data;
  } catch (error) {
    throw new ApiError(500, "No se pudo obtener contenido desde TioAnime", error.message);
  }
}

function normalizeToken(value) {
  return (value || "")
    .toString()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .trim();
}

function normalizeVariantKey(value) {
  const normalized = normalizeToken(value);
  if (!normalized) return "SUB";
  if (normalized.includes("sub") || normalized.includes("jap") || normalized.includes("jp")) return "SUB";
  return "DUB";
}

function parseEpisodeNumberFromUrl(url) {
  try {
    const segments = new URL(url).pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    const match = lastSegment.match(/(\d+)$/);
    return match ? Number(match[1]) : null;
  } catch (_) {
    return null;
  }
}

function slugFromUrl(url) {
  try {
    const segments = new URL(url).pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    return lastSegment.replace(/-\d+$/, "");
  } catch (_) {
    return null;
  }
}

function parseVideoSources(html) {
  const match = html.match(/videos\s*=\s*(\[[\s\S]*?\]);/);
  if (!match) return null;

  try {
    const jsonStr = match[1]
      .replace(/\\\//g, "/")
      .replace(/\\"/g, '"');
    const raw = JSON.parse(jsonStr);

    if (!Array.isArray(raw)) return null;

    const result = { SUB: [], DUB: [] };

    for (const entry of raw) {
      if (!Array.isArray(entry) || entry.length < 2) continue;
      const serverName = entry[0] || "Unknown";
      const url = entry[1] || null;
      if (!url) continue;

      result.SUB.push({ server: serverName, url });
    }

    return result;
  } catch (_) {
    return null;
  }
}

function parseEpisodeListFromHtml(html) {
  const match = html.match(/episodes\s*=\s*(\[[^\]]*\])/);
  if (!match) return null;

  try {
    const raw = JSON.parse(match[1]);
    if (!Array.isArray(raw)) return null;

    return raw
      .filter((n) => typeof n === "number" && Number.isFinite(n))
      .map((n) => ({
        id: null,
        number: n,
      }))
      .sort((a, b) => a.number - b.number);
  } catch (_) {
    return null;
  }
}

function parseAnimeInfoFromHtml(html) {
  const $ = cheerio.load(html);

  const title = $("h1.title").first().text().trim() || $("h1").first().text().trim() || null;
  const description =
    $("p.sinopsis").first().text().trim() ||
    $(".description").first().text().trim() ||
    $("meta[name='description']").attr("content") ||
    null;

  if (description && description.startsWith("ver online")) {
    return { title, description: null, genres: [], type: null };
  }

  const genres = [];
  $(".genres span a, a[href*='genero']").each((_, el) => {
    const name = $(el).text().trim();
    if (name) {
      genres.push({
        id: null,
        name,
        slug: name.toLowerCase().replace(/\s+/g, "-"),
        malId: null,
      });
    }
  });

  const typeEl = $(".anime-type-peli, .anime-type-serie, .anime-type-ova, .type").first();
  const type = typeEl.text().trim() || null;

  return { title, description, genres, type };
}

function buildLinkRecord(serverName, url, quality) {
  if (!url) return null;
  return { server: serverName || "Unknown", url, quality: quality || null };
}

// Public API

async function searchAnime(query, domainCandidate) {
  const cleanQuery = (query || "").toString().trim();
  if (!cleanQuery) {
    throw new ApiError(400, "Se requiere el parametro q");
  }

  const domain = (domainCandidate || DEFAULT_DOMAIN).toString().trim();
  const searchUrl = `https://${domain}/directorio?search=${encodeURIComponent(cleanQuery)}`;
  const html = await fetchHtml(searchUrl);

  const $ = cheerio.load(html);
  const results = [];

  $("article.anime").each((_, element) => {
    const card = $(element);
    const link = card.find("a[href^='/anime/']").first().attr("href");
    const title = card.find("h3.title").first().text().trim();
    const image = card.find("img").first().attr("src");

    if (!link || !title) return;

    const slug = link.replace("/anime/", "");
    const typeEl = card.find(".anime-type-peli, .anime-type-serie, .anime-type-ova").first();
    const type = typeEl.text().trim() || null;

    results.push({
      id: null,
      title,
      slug,
      url: `https://${domain}${link}`,
      image: image ? `https://${domain}${image}` : null,
      backdrop: null,
      type: type || "Anime",
      score: null,
      status: null,
      year: null,
    });
  });

  return {
    success: true,
    data: { query: cleanQuery, results, count: results.length },
    source: "tioanime",
  };
}

async function getAnimeInfo(urlCandidate) {
  const slug = slugFromUrl(urlCandidate);
  if (!slug) throw new ApiError(400, "URL invalida");

  const animeUrl = `https://${DEFAULT_DOMAIN}/anime/${slug}`;
  const html = await fetchHtml(animeUrl);

  const info = parseAnimeInfoFromHtml(html);

  // Try to get episodes from JS variable
  let episodesList = parseEpisodeListFromHtml(html);

  // If not in JS, try scraping from links
  if (!episodesList || episodesList.length === 0) {
    const $ = cheerio.load(html);
    episodesList = [];
    $("a[href*='/ver/']").each((_, el) => {
      const href = $(el).attr("href");
      if (!href) return;
      const number = parseEpisodeNumberFromUrl(`https://${DEFAULT_DOMAIN}${href}`);
      if (number) {
        episodesList.push({ id: null, number });
      }
    });
    // Dedup
    const seen = new Set();
    episodesList = episodesList.filter((e) => {
      const k = e.number;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }

  const episodes = episodesList
    .filter((ep) => ep && ep.number)
    .map((ep) => ({
      id: ep.id || null,
      number: ep.number,
      title: `Episodio ${ep.number}`,
      url: `https://${DEFAULT_DOMAIN}/ver/${slug}-${ep.number}`,
    }));

  return {
    success: true,
    data: {
      id: null,
      title: info.title,
      titleJapanese: null,
      description: info.description,
      image: null,
      backdrop: null,
      status: null,
      type: info.type,
      year: null,
      startDate: null,
      endDate: null,
      score: null,
      votes: null,
      totalEpisodes: episodes.length,
      malId: null,
      trailer: null,
      genres: info.genres || [],
      episodes,
    },
    source: "tioanime",
  };
}

async function getEpisodeLinks(urlCandidate) {
  const slug = slugFromUrl(urlCandidate);
  const episodeNumber = parseEpisodeNumberFromUrl(urlCandidate);

  if (!slug || !episodeNumber) {
    throw new ApiError(400, "URL invalida - no se pudo extraer slug y numero");
  }

  const episodeUrl = `https://${DEFAULT_DOMAIN}/ver/${slug}-${episodeNumber}`;
  const html = await fetchHtml(episodeUrl);

  const videoSources = parseVideoSources(html);

  const streamLinks = { SUB: [], DUB: [] };
  const downloadLinks = { SUB: [], DUB: [] };

  if (videoSources) {
    for (const [variantKey, entries] of Object.entries(videoSources)) {
      for (const entry of entries) {
        const link = buildLinkRecord(entry.server, entry.url, null);
        if (link) {
          const key = normalizeVariantKey(variantKey);
          streamLinks[key].push(link);
        }
      }
    }
  }

  const episodeTitle =
    cheerio.load(html)("h1.title, h1").first().text().trim() || `Episodio ${episodeNumber}`;

  // Combine all links for frontend compatibility
  const allLinks = [
    ...streamLinks.SUB,
    ...streamLinks.DUB,
    ...downloadLinks.SUB,
    ...downloadLinks.DUB
  ];

  return {
    success: true,
    data: {
      id: null,
      episode: episodeNumber,
      title: episodeTitle,
      season: null,
      variants: {
        SUB: streamLinks.SUB.length > 0 || downloadLinks.SUB.length > 0 ? 1 : 0,
        DUB: streamLinks.DUB.length > 0 || downloadLinks.DUB.length > 0 ? 1 : 0,
      },
      publishedAt: null,
      links: allLinks,
      servers: {
        sub: streamLinks.SUB.map((l) => ({ server: l.server, url: l.url })),
        dub: streamLinks.DUB.map((l) => ({ server: l.server, url: l.url })),
      },
      streamLinks: {
        SUB: streamLinks.SUB.map((l) => ({ server: l.server, url: l.url })),
        DUB: streamLinks.DUB.map((l) => ({ server: l.server, url: l.url })),
      },
      downloadLinks: {
        SUB: downloadLinks.SUB.map((l) => ({ server: l.server, url: l.url })),
        DUB: downloadLinks.DUB.map((l) => ({ server: l.server, url: l.url })),
      },
    },
    source: "tioanime",
  };
}

module.exports = {
  searchAnime,
  getAnimeInfo,
  getEpisodeLinks,
};
