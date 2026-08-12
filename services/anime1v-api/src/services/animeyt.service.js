const axios = require("axios");
const cheerio = require("cheerio");
const vm = require("node:vm");
const { URL } = require("node:url");
const { ApiError } = require("../utils/api-error");

const BASE_URL = "https://animeyt.cc";
const MYTSUMI_BASE = "https://mytsumi.com";

const HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
};

function slugify(text) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

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
    throw new ApiError(500, "No se pudo obtener contenido desde AnimeYT", error.message);
  }
}

function resolveAbsoluteUrl(urlCandidate, domain = "animeyt.cc") {
  if (!urlCandidate || typeof urlCandidate !== "string") {
    return null;
  }
  try {
    const base = `https://${domain}`;
    return new URL(urlCandidate, base).toString();
  } catch (_error) {
    return null;
  }
}

function normalizeInputUrl(urlCandidate, domain = "animeyt.cc") {
  const normalized = resolveAbsoluteUrl(urlCandidate, domain);
  if (!normalized) {
    throw new ApiError(400, "URL invalida");
  }
  return normalized;
}

function parseNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  const converted = Number(value);
  return Number.isFinite(converted) ? converted : null;
}

function parseEpisodeNumberFromUrl(url) {
  try {
    const pathname = new URL(url).pathname;
    const match = pathname.match(/\/anime\/[^/]+-capitulo-(\d+)/);
    if (match) return Number(match[1]);
    const segments = pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    const number = Number(lastSegment);
    return Number.isFinite(number) ? number : null;
  } catch (_error) {
    return null;
  }
}

function normalizeToken(value) {
  return (value || "")
    .toString()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "")
    .trim();
}

function normalizeServerName(serverName, url) {
  if (serverName && typeof serverName === "string") {
    const token = normalizeToken(serverName);
    if (token) {
      return { name: serverName.trim(), token };
    }
  }
  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return {
      name: host,
      token: normalizeToken(host),
    };
  } catch (_error) {
    return { name: "Unknown", token: "unknown" };
  }
}

function pushDeduped(target, link) {
  if (!link || !link.url) {
    return;
  }
  if (target.some((item) => item.url === link.url)) {
    return;
  }
  target.push(link);
}

function buildExcludedTokens(includeMega, excludeServersRaw) {
  const excluded = new Set();
  const raw = typeof excludeServersRaw === "string" ? excludeServersRaw : "";
  for (const part of raw.split(",")) {
    const token = normalizeToken(part);
    if (token) {
      excluded.add(token);
    }
  }
  if (!includeMega) {
    excluded.add("mega");
  }
  return excluded;
}

function filterLinksByServers(links, excludedTokens) {
  return links.filter((link) => {
    const token = normalizeToken(link.token || link.server);
    if (!token) {
      return true;
    }
    if (excludedTokens.has(token)) {
      return false;
    }
    if (token.includes("mega") && excludedTokens.has("mega")) {
      return false;
    }
    return true;
  });
}

function sanitizeLinksForResponse(links) {
  return links.map((link) => {
    const result = {
      server: link.server,
      url: link.url,
    };
    if (link.quality) {
      result.quality = link.quality;
    }
    return result;
  });
}

function extractBalancedSection(text, startIndex, openChar, closeChar) {
  let depth = 0;
  let activeQuote = "";
  let escaped = false;
  for (let index = startIndex; index < text.length; index += 1) {
    const character = text[index];
    if (activeQuote) {
      if (escaped) {
        escaped = false;
        continue;
      }
      if (character === "\\") {
        escaped = true;
        continue;
      }
      if (character === activeQuote) {
        activeQuote = "";
      }
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      activeQuote = character;
      continue;
    }
    if (character === openChar) {
      depth += 1;
    }
    if (character === closeChar) {
      depth -= 1;
      if (depth === 0) {
        return text.slice(startIndex, index + 1);
      }
    }
  }
  return null;
}

function safeEvaluate(expression) {
  try {
    const context = Object.create(null);
    return vm.runInNewContext(expression, context, {
      timeout: 1000,
      displayErrors: false,
    });
  } catch (_error) {
    return null;
  }
}

function extractVarLiteral(html, varName) {
  const marker = `var ${varName}`;
  const startIndex = html.indexOf(marker);
  if (startIndex === -1) {
    return null;
  }
  const equalsIndex = html.indexOf("=", startIndex);
  if (equalsIndex === -1) {
    return null;
  }
  const slice = html.slice(equalsIndex + 1);
  const firstBracketIndex = slice.search(/[\[{]/);
  if (firstBracketIndex === -1) {
    return null;
  }
  const openChar = slice[firstBracketIndex];
  const closeChar = openChar === "{" ? "}" : "]";
  return extractBalancedSection(slice, firstBracketIndex, openChar, closeChar);
}

function tryDecodeBase64(value) {
  if (!value || typeof value !== "string") {
    return null;
  }
  try {
    if (/^[A-Za-z0-9+/=]+$/.test(value) && value.length > 10) {
      const decoded = Buffer.from(value, "base64").toString("utf8");
      if (decoded.startsWith("http://") || decoded.startsWith("https://")) {
        return decoded;
      }
    }
  } catch (_e) {
  }
  return null;
}

function decodeUrlEscapes(value) {
  if (!value || typeof value !== "string") {
    return value;
  }
  return value
    .replace(/\\u0026/g, "&")
    .replace(/\\u003A/g, ":")
    .replace(/\\u002F/g, "/")
    .replace(/&/g, "&");
}

function normalizeVariantKey(value) {
  const normalized = normalizeToken(value);
  if (!normalized) {
    return "SUB";
  }
  if (normalized.includes("sub") || normalized.includes("jap") || normalized.includes("jp")) {
    return "SUB";
  }
  return "DUB";
}

function buildLinkRecord(serverName, url, quality) {
  if (!url) {
    return null;
  }
  const server = normalizeServerName(serverName, url);
  return {
    server: server.name,
    token: server.token,
    url,
    quality: quality || null,
  };
}

function parseSearchResultsFromHtml(html, domain) {
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
      title,
      slug,
      url: resolveAbsoluteUrl(href, domain),
      image: resolveAbsoluteUrl(poster, domain),
      backdrop: null,
      type: "anime",
      score: null,
      status: null,
      year: null,
    });
  });
  return results;
}

async function searchAnime(query, domainCandidate) {
  const cleanQuery = (query || "").toString().trim();
  if (!cleanQuery) {
    throw new ApiError(400, "Se requiere el parametro q");
  }
  const domain = (domainCandidate || "animeyt.cc").toString().trim();
  const url = `${BASE_URL}/?s=${encodeURIComponent(cleanQuery)}`;
  const html = await fetchHtml(url);
  let results = parseSearchResultsFromHtml(html, domain);
  if (results.length === 0) {
    const links = [];
    const $ = cheerio.load(html);
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
      const lowerQuery = cleanQuery.toLowerCase();
      if (lowerTitle.includes(lowerQuery) || lowerQuery.includes(lowerTitle) || slugify(link.title) === slugify(cleanQuery)) {
        results.push({
          id: `animeyt-${link.slug}`,
          title: link.title,
          slug: link.slug,
          url: resolveAbsoluteUrl(link.url, domain),
          image: "",
          backdrop: null,
          type: "anime",
          score: null,
          status: null,
          year: null,
        });
      }
    }
  }
  return {
    success: true,
    data: {
      query: cleanQuery,
      results,
      count: results.length,
    },
    source: "animeyt",
  };
}

async function getAnimeInfo(urlCandidate) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const parsed = new URL(normalizedUrl);
  const segments = parsed.pathname.split("/").filter(Boolean);
  let slug = "";
  if (segments[0] === "category") {
    slug = segments[1] || "";
  }
  if (!slug) {
    throw new ApiError(400, "URL invalida - no se pudo extraer el slug");
  }
  const animeUrl = `${BASE_URL}/category/${slug}/`;
  const html = await fetchHtml(animeUrl);
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
        url: resolveAbsoluteUrl(href, domain),
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
    success: true,
    data: {
      id: slug,
      slug,
      title,
      originalTitle: title,
      description: synopsis,
      image: poster,
      backdrop: null,
      status: null,
      type: "anime",
      year: null,
      startDate: null,
      endDate: null,
      score: rating ? parseNumber(rating) : null,
      votes: null,
      totalEpisodes: episodes.length,
      malId: null,
      trailer: null,
      genres: [],
      episodes: seasons.length > 0 ? [] : episodes,
      seasons,
    },
    source: "animeyt",
  };
}

async function getEpisodeLinks(urlCandidate, includeMegaRaw, excludeServersRaw) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const includeMega = String(includeMegaRaw).toLowerCase() === "true";
  const excludedTokens = buildExcludedTokens(includeMega, excludeServersRaw);
  const parsed = new URL(normalizedUrl);
  const segments = parsed.pathname.split("/").filter(Boolean);
  const episodeNumber = segments.length > 1 ? parseNumber(segments[1]) : null;
  const animeSlug = segments[2] || "";
  const html = await fetchHtml(normalizedUrl);
  const $ = cheerio.load(html);
  const streamLinks = { SUB: [], DUB: [] };
  const downloadLinks = { SUB: [], DUB: [] };
  const embedHolder = $("#embed_holder");
  if (embedHolder.length) {
    const iframes = embedHolder.find("iframe[data-src]");
    iframes.each((_, iframe) => {
      const dataSrc = $(iframe).attr("data-src") || "";
      if (dataSrc) {
        const serverName = "AnimeYT";
        const link = buildLinkRecord(serverName, dataSrc, null);
        if (link) pushDeduped(streamLinks.SUB, link);
      }
    });
    const noscriptIframes = embedHolder.find("noscript iframe");
    noscriptIframes.each((_, iframe) => {
      const src = $(iframe).attr("src") || "";
      if (src) {
        const serverName = "AnimeYT";
        const link = buildLinkRecord(serverName, src, null);
        if (link) pushDeduped(streamLinks.SUB, link);
      }
    });
  }
  const filteredStreamSub = filterLinksByServers(streamLinks.SUB, excludedTokens);
  const filteredStreamDub = filterLinksByServers(streamLinks.DUB, excludedTokens);
  const filteredDownloadSub = filterLinksByServers(downloadLinks.SUB, excludedTokens);
  const filteredDownloadDub = filterLinksByServers(downloadLinks.DUB, excludedTokens);
  const allLinks = [
    ...filteredStreamSub,
    ...filteredStreamDub,
    ...filteredDownloadSub,
    ...filteredDownloadDub
  ];
  return {
    success: true,
    data: {
      id: null,
      episode: episodeNumber,
      title: `Episodio ${episodeNumber || "?"}`,
      season: null,
      variants: {
        SUB: filteredStreamSub.length > 0 || filteredDownloadSub.length > 0 ? 1 : 0,
        DUB: filteredStreamDub.length > 0 || filteredDownloadDub.length > 0 ? 1 : 0,
      },
      publishedAt: null,
      links: allLinks,
      servers: {
        sub: sanitizeLinksForResponse(filteredStreamSub),
        dub: sanitizeLinksForResponse(filteredStreamDub),
      },
      streamLinks: {
        SUB: sanitizeLinksForResponse(filteredStreamSub),
        DUB: sanitizeLinksForResponse(filteredStreamDub),
      },
      downloadLinks: {
        SUB: sanitizeLinksForResponse(filteredDownloadSub),
        DUB: sanitizeLinksForResponse(filteredDownloadDub),
      },
    },
    source: "animeyt",
  };
}

module.exports = {
  searchAnime,
  getAnimeInfo,
  getEpisodeLinks,
};