const axios = require("axios");
const cheerio = require("cheerio");
const vm = require("node:vm");
const { URL } = require("node:url");
const { ApiError } = require("../utils/api-error");

const DEFAULT_DOMAIN = process.env.DEFAULT_ANIME_DOMAIN || "animeav1.com";

const HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
};

const SERVER_PATTERNS = [
  { token: "pdrain", name: "PDrain", test: /(pixeldrain|pdrain)/i },
  { token: "hls", name: "HLS", test: /(hls|m3u8|zilla|player\.)/i },
  { token: "upnshare", name: "UPNShare", test: /(upnshare|uns\.bio)/i },
  { token: "mega", name: "Mega", test: /(mega\.nz|mega)/i },
  { token: "mp4upload", name: "MP4Upload", test: /(mp4upload)/i },
  { token: "1fichier", name: "1Fichier", test: /(1fichier)/i },
  { token: "fembed", name: "Fembed", test: /(fembed|femax20)/i },
];

const VIDEO_URL_REGEX =
  /https?:\/\/(?:www\.)?(?:pixeldrain\.com|mega\.nz|mp4upload\.com|1fichier\.com|player\.[^\s"'<>]+|[^\s"'<>]*zilla[^\s"'<>]*|[^\s"'<>]*uns\.bio[^\s"'<>]*)[^\s"'<>]*/gi;

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
    throw new ApiError(500, "No se pudo obtener contenido desde AnimeAV1", error.message);
  }
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function walk(value, visitor, seen = new Set()) {
  if (!value || typeof value !== "object") {
    return;
  }

  if (seen.has(value)) {
    return;
  }

  seen.add(value);
  visitor(value);

  if (Array.isArray(value)) {
    for (const item of value) {
      walk(item, visitor, seen);
    }
    return;
  }

  for (const child of Object.values(value)) {
    walk(child, visitor, seen);
  }
}

function collectValuesByKey(root, keyName) {
  const values = [];
  walk(root, (node) => {
    if (!isObject(node)) {
      return;
    }

    if (Object.prototype.hasOwnProperty.call(node, keyName)) {
      values.push(node[keyName]);
    }
  });
  return values;
}

function collectArrays(root) {
  const arrays = [];
  walk(root, (node) => {
    if (Array.isArray(node)) {
      arrays.push(node);
    }
  });
  return arrays;
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

function extractSvelteData(html) {
  const $ = cheerio.load(html);
  const scripts = $("script")
    .map((_, element) => $(element).html() || "")
    .get();

  for (const scriptContent of scripts) {
    if (!scriptContent.includes("__sveltekit_") || !scriptContent.includes("data:")) {
      continue;
    }

    let pointer = scriptContent.indexOf("__sveltekit_");
    while (pointer !== -1) {
      const equalsPosition = scriptContent.indexOf("=", pointer);
      if (equalsPosition === -1) {
        break;
      }

      const objectStart = scriptContent.indexOf("{", equalsPosition);
      if (objectStart === -1) {
        break;
      }

      const objectLiteral = extractBalancedSection(scriptContent, objectStart, "{", "}");
      if (objectLiteral) {
        const payload = safeEvaluate(`(${objectLiteral})`);
        if (payload && Array.isArray(payload.data)) {
          return payload.data;
        }
      }

      pointer = scriptContent.indexOf("__sveltekit_", pointer + "__sveltekit_".length);
    }

    const dataMarker = scriptContent.indexOf("data:");
    if (dataMarker !== -1) {
      const listStart = scriptContent.indexOf("[", dataMarker);
      if (listStart !== -1) {
        const listLiteral = extractBalancedSection(scriptContent, listStart, "[", "]");
        if (listLiteral) {
          const payloadData = safeEvaluate(`(${listLiteral})`);
          if (Array.isArray(payloadData)) {
            return payloadData;
          }
        }
      }
    }
  }

  return null;
}

function resolveAbsoluteUrl(urlCandidate, domain = DEFAULT_DOMAIN) {
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

function normalizeInputUrl(urlCandidate, domain = DEFAULT_DOMAIN) {
  const normalized = resolveAbsoluteUrl(urlCandidate, domain);
  if (!normalized) {
    throw new ApiError(400, "URL invalida");
  }
  return normalized;
}

function detectDomain(urlCandidate) {
  try {
    return new URL(urlCandidate).hostname || DEFAULT_DOMAIN;
  } catch (_error) {
    return DEFAULT_DOMAIN;
  }
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
    const segments = pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    const number = Number(lastSegment);
    return Number.isFinite(number) ? number : null;
  } catch (_error) {
    return null;
  }
}

function normalizeServerName(serverName, url) {
  const source = `${serverName || ""} ${url || ""}`.trim();
  for (const knownServer of SERVER_PATTERNS) {
    if (knownServer.test.test(source)) {
      return knownServer;
    }
  }

  if (serverName && typeof serverName === "string") {
    return {
      name: serverName.trim(),
      token: serverName
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "")
        .trim(),
    };
  }

  try {
    const host = new URL(url).hostname.replace(/^www\./, "");
    return {
      name: host,
      token: host.toLowerCase().replace(/[^a-z0-9]+/g, ""),
    };
  } catch (_error) {
    return { name: "Unknown", token: "unknown" };
  }
}

function normalizeLinkObject(entry, domain) {
  if (!entry) {
    return null;
  }

  if (typeof entry === "string") {
    const url = resolveAbsoluteUrl(entry, domain);
    if (!url) {
      return null;
    }
    const server = normalizeServerName("", url);
    return {
      server: server.name,
      token: server.token,
      url,
      quality: null,
    };
  }

  if (!isObject(entry)) {
    return null;
  }

  const urlCandidate =
    entry.url ||
    entry.href ||
    entry.link ||
    entry.embed ||
    entry.streamUrl ||
    entry.downloadUrl ||
    entry.file ||
    entry.source ||
    null;

  const url = resolveAbsoluteUrl(urlCandidate, domain);
  if (!url) {
    return null;
  }

  const server = normalizeServerName(entry.server || entry.name || entry.provider || entry.host, url);
  const quality =
    entry.quality ||
    entry.resolution ||
    entry.label ||
    (typeof entry.size === "string" ? entry.size : null) ||
    null;

  return {
    server: server.name,
    token: server.token,
    url,
    quality,
  };
}

function inferLinkKind(url, explicitKind) {
  if (explicitKind) {
    return explicitKind;
  }

  if (typeof url !== "string") {
    return "stream";
  }

  if (/(embed|play\/?|m3u8|hls|player\.|uns\.bio|upnshare)/i.test(url)) {
    return "stream";
  }

  return "download";
}

function pushDeduped(target, link) {
  if (!link) {
    return;
  }

  const exists = target.some((item) => item.url === link.url);
  if (!exists) {
    target.push(link);
  }
}

function parseVariantContainer(container, kindHint, domain, collector) {
  if (!isObject(container)) {
    return;
  }

  const variantPairs = [
    ["SUB", container.SUB ?? container.sub],
    ["DUB", container.DUB ?? container.dub],
  ];

  for (const [variant, value] of variantPairs) {
    if (!value) {
      continue;
    }

    if (Array.isArray(value)) {
      for (const entry of value) {
        const normalized = normalizeLinkObject(entry, domain);
        if (!normalized) {
          continue;
        }
        const kind = inferLinkKind(normalized.url, kindHint);
        pushDeduped(collector[kind][variant], normalized);
      }
      continue;
    }

    if (isObject(value)) {
      for (const [childKey, childValue] of Object.entries(value)) {
        if (!Array.isArray(childValue)) {
          const normalized = normalizeLinkObject(childValue, domain);
          if (!normalized) {
            continue;
          }
          const childKind =
            /download/i.test(childKey) ? "download" : /stream|embed|server/i.test(childKey) ? "stream" : inferLinkKind(normalized.url, kindHint);
          pushDeduped(collector[childKind][variant], normalized);
          continue;
        }

        const childKind =
          /download/i.test(childKey) ? "download" : /stream|embed|server/i.test(childKey) ? "stream" : kindHint || "stream";

        for (const entry of childValue) {
          const normalized = normalizeLinkObject(entry, domain);
          if (!normalized) {
            continue;
          }
          const inferredKind = inferLinkKind(normalized.url, childKind);
          pushDeduped(collector[inferredKind][variant], normalized);
        }
      }
    }
  }
}

function extractLinksFromData(dataRoot, html, domain) {
  const collector = {
    stream: { SUB: [], DUB: [] },
    download: { SUB: [], DUB: [] },
  };

  walk(dataRoot, (node) => {
    if (!isObject(node)) {
      return;
    }

    if (node.streamLinks) {
      parseVariantContainer(node.streamLinks, "stream", domain, collector);
    }

    if (node.downloadLinks) {
      parseVariantContainer(node.downloadLinks, "download", domain, collector);
    }

    if (node.servers) {
      parseVariantContainer(node.servers, "stream", domain, collector);
    }

    const hasVariantShape =
      Object.prototype.hasOwnProperty.call(node, "SUB") ||
      Object.prototype.hasOwnProperty.call(node, "sub") ||
      Object.prototype.hasOwnProperty.call(node, "DUB") ||
      Object.prototype.hasOwnProperty.call(node, "dub");

    if (hasVariantShape) {
      parseVariantContainer(node, null, domain, collector);
    }
  });

  if (collector.stream.SUB.length === 0 && collector.download.SUB.length === 0 && typeof html === "string") {
    const foundUrls = html.match(VIDEO_URL_REGEX) || [];
    for (const rawUrl of foundUrls) {
      const url = resolveAbsoluteUrl(rawUrl, domain);
      if (!url) {
        continue;
      }
      const server = normalizeServerName("", url);
      const link = { server: server.name, token: server.token, url, quality: null };
      const kind = inferLinkKind(url);
      pushDeduped(collector[kind].SUB, link);
    }
  }

  return collector;
}

function buildExcludedTokens(includeMega, excludeServersRaw) {
  const excluded = new Set();

  const raw = typeof excludeServersRaw === "string" ? excludeServersRaw : "";
  for (const part of raw.split(",")) {
    const token = part.toLowerCase().replace(/[^a-z0-9]+/g, "").trim();
    if (token) {
      excluded.add(token);
    }
  }

  if (!includeMega) {
    excluded.add("mega");
  }

  excluded.add("terabox");
  excluded.add("mp4upload");

  return excluded;
}

function filterLinksByServers(links, excludedTokens) {
  return links.filter((link) => {
    const token = (link.token || "").toLowerCase();
    if (excludedTokens.has(token)) {
      return false;
    }

    if (token.includes("mega") && excludedTokens.has("mega")) {
      return false;
    }

    if (link.url && /#[a-zA-Z0-9]/.test(link.url)) {
      return false;
    }

    if (link.url && /terabox/i.test(link.url)) {
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

function chooseBestMediaCandidate(dataRoot) {
  const candidates = collectValuesByKey(dataRoot, "media").filter(isObject);

  walk(dataRoot, (node) => {
    if (!isObject(node)) {
      return;
    }

    if (typeof node.title === "string" && (Array.isArray(node.episodes) || node.description)) {
      candidates.push(node);
    }
  });

  let best = null;
  let bestScore = -1;

  for (const candidate of candidates) {
    let score = 0;
    if (typeof candidate.title === "string") score += 3;
    if (Array.isArray(candidate.episodes)) score += 3;
    if (Array.isArray(candidate.genres)) score += 1;
    if (candidate.description) score += 1;
    if (candidate.poster || candidate.image) score += 1;
    if (candidate.id) score += 1;

    if (score > bestScore) {
      best = candidate;
      bestScore = score;
    }
  }

  return best;
}

function normalizeGenres(genres) {
  if (!Array.isArray(genres)) {
    return [];
  }

  return genres
    .map((genre) => {
      if (typeof genre === "string") {
        return {
          id: null,
          name: genre,
          slug: genre.toLowerCase().replace(/\s+/g, "-"),
          malId: null,
        };
      }

      if (!isObject(genre)) {
        return null;
      }

      return {
        id: genre.id ?? null,
        name: genre.name || genre.title || null,
        slug: genre.slug || null,
        malId: genre.malId ?? genre.mal_id ?? null,
      };
    })
    .filter((genre) => genre && genre.name);
}

function normalizeEpisodes(episodes, domain, slug) {
  if (!Array.isArray(episodes)) {
    return [];
  }

  return episodes
    .map((episode, index) => {
      if (!isObject(episode)) {
        return null;
      }

      const inferredNumber =
        parseNumber(episode.number) ??
        parseNumber(episode.episode) ??
        parseNumber(episode.ep) ??
        parseNumber(episode.order) ??
        index + 1;

      let episodeUrl = resolveAbsoluteUrl(episode.url || episode.href || episode.link, domain);
      if (!episodeUrl && slug && Number.isFinite(inferredNumber)) {
        episodeUrl = resolveAbsoluteUrl(`/media/${slug}/${inferredNumber}`, domain);
      }

      return {
        id: episode.id ?? null,
        number: inferredNumber,
        title: episode.title || `Episodio ${inferredNumber}`,
        url: episodeUrl,
      };
    })
    .filter((episode) => episode && episode.url);
}

function normalizeRelations(relations, domain) {
  if (!Array.isArray(relations)) {
    return [];
  }

  return relations
    .map((relation) => {
      if (!isObject(relation) || !isObject(relation.destination)) {
        return null;
      }

      const destination = relation.destination;
      const slug = destination.slug || null;
      const url = resolveAbsoluteUrl(destination.url || (slug ? `/media/${slug}` : null), domain);
      if (!url) {
        return null;
      }

      return {
        type: parseNumber(relation.type),
        relationType: relation.type || null,
        title: destination.title || null,
        slug,
        url,
        image: resolveAbsoluteUrl(destination.poster || destination.image || (destination.id ? `https://cdn.animeav1.com/covers/${destination.id}.jpg` : null), domain),
        startDate: destination.startDate || destination.start_date || null,
      };
    })
    .filter((relation) => relation && relation.title);
}

function normalizeAnimeInfo(media, domain) {
  const episodes = normalizeEpisodes(media.episodes || media.episodeList || [], domain, media.slug);

  return {
    id: media.id ?? null,
    title: media.title || null,
    titleJapanese:
      (isObject(media.aka) && (media.aka["ja-jp"] || media.aka["ja"] || media.aka.jp)) || media.titleJapanese || null,
    description: media.description || media.synopsis || null,
    image: resolveAbsoluteUrl(media.poster || media.image || media.cover || (media.id ? `https://cdn.animeav1.com/covers/${media.id}.jpg` : null), domain),
    backdrop: resolveAbsoluteUrl(media.backdrop || media.banner || media.thumbnail, domain),
    status: (isObject(media.status) ? media.status.name : media.status) || null,
    type: (isObject(media.category) ? media.category.name : media.type) || null,
    year: media.year ? String(media.year) : null,
    startDate: media.startDate || media.start_date || null,
    endDate: media.endDate || media.end_date || null,
    score: parseNumber(media.score),
    votes: parseNumber(media.votes || media.scoreVotes || media.voters),
    totalEpisodes: parseNumber(media.totalEpisodes) || episodes.length,
    malId: media.malId ?? media.mal_id ?? null,
    trailer: resolveAbsoluteUrl(media.trailer, domain),
    genres: normalizeGenres(media.genres),
    episodes,
    relations: normalizeRelations(media.relations, domain),
  };
}

function chooseLikelySearchArray(dataRoot) {
  const candidateArrays = collectArrays(dataRoot).filter((array) => array.length > 0 && array.length <= 300);

  let bestArray = null;
  let bestScore = -1;

  for (const array of candidateArrays) {
    let totalScore = 0;
    let objectItems = 0;

    for (const item of array) {
      if (!isObject(item)) {
        continue;
      }

      objectItems += 1;
      let score = 0;

      if (typeof item.title === "string" || typeof item.name === "string") score += 2;
      if (typeof item.slug === "string" || typeof item.url === "string") score += 2;
      if (item.poster || item.image || item.backdrop) score += 1;
      if (item.category || item.type) score += 1;
      if (item.status || item.year) score += 0.5;
      if (item.description || item.synopsis) score += 0.5;

      totalScore += score;
    }

    if (objectItems === 0) {
      continue;
    }

    const averageScore = totalScore / objectItems;
    if (averageScore > bestScore) {
      bestScore = averageScore;
      bestArray = array;
    }
  }

  return bestScore >= 2 ? bestArray : null;
}

function mapSearchResults(array, domain) {
  return array
    .map((item) => {
      if (!isObject(item)) {
        return null;
      }

      const title = item.title || item.name || null;
      if (!title) {
        return null;
      }

      const slug = item.slug || null;
      const url = resolveAbsoluteUrl(item.url || item.href || (slug ? `/media/${slug}` : null), domain);
      if (!url) {
        return null;
      }

      let img = item.poster || item.image || item.cover || null;
      if (!img && item.id) {
        img = `https://cdn.animeav1.com/covers/${item.id}.jpg`;
      }

      return {
        id: item.id ?? null,
        title,
        slug,
        url,
        image: resolveAbsoluteUrl(img, domain),
        backdrop: resolveAbsoluteUrl(item.backdrop || item.banner, domain),
        type: (isObject(item.category) ? item.category.name : item.type) || null,
        score: parseNumber(item.score),
        status: (isObject(item.status) ? item.status.name : item.status) || null,
        year: item.year ? String(item.year) : null,
      };
    })
    .filter(Boolean);
}

function normalizeTextForSearch(value) {
  return (value || "")
    .toString()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function filterSearchResultsByQuery(results, query) {
  const normalizedQuery = normalizeTextForSearch(query);
  if (!normalizedQuery) {
    return results.slice(0, 20);
  }

  const queryTerms = normalizedQuery.split(/\s+/).filter(Boolean);
  const scored = [];

  for (const result of results) {
    const title = normalizeTextForSearch(result.title);
    const slug = normalizeTextForSearch(result.slug);
    const combined = `${title} ${slug}`.trim();

    let score = 0;
    if (title === normalizedQuery || slug === normalizedQuery) {
      score += 5;
    }

    if (title.includes(normalizedQuery) || slug.includes(normalizedQuery)) {
      score += 3;
    }

    for (const term of queryTerms) {
      if (term.length < 2) {
        continue;
      }

      if (combined.includes(term)) {
        score += 1;
      }
    }

    if (score > 0) {
      scored.push({ result, score });
    }
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.map((item) => item.result).slice(0, 20);
}

function parseSearchResultsFromHtml(html, domain) {
  const $ = cheerio.load(html);
  const results = [];

  $("a[href^='/media/']").each((_, element) => {
    const href = $(element).attr("href");
    if (!href || !/^\/media\/[^/]+$/i.test(href)) {
      return;
    }

    const card = $(element).closest("article").length ? $(element).closest("article") : $(element);
    const title =
      $(card).find("h3, h2, [title]").first().text().trim() ||
      $(card).find("img").first().attr("alt") ||
      $(element).attr("title") ||
      null;

    if (!title) {
      return;
    }

    const slug = href.replace(/^\/media\//, "").trim();
    const image = resolveAbsoluteUrl($(card).find("img").first().attr("src"), domain);

    results.push({
      id: null,
      title,
      slug,
      url: resolveAbsoluteUrl(href, domain),
      image,
      backdrop: null,
      type: null,
      score: null,
      status: null,
      year: null,
    });
  });

  const unique = [];
  const seenUrls = new Set();

  for (const item of results) {
    if (seenUrls.has(item.url)) {
      continue;
    }
    seenUrls.add(item.url);
    unique.push(item);
  }

  return unique;
}

function firstObjectByKey(dataRoot, keyName) {
  const values = collectValuesByKey(dataRoot, keyName);
  for (const value of values) {
    if (isObject(value)) {
      return value;
    }
  }
  return null;
}

function parseBoolean(value) {
  if (typeof value === "boolean") {
    return value;
  }
  return String(value).toLowerCase() === "true";
}

async function getAnimeInfo(urlCandidate) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const domain = detectDomain(normalizedUrl);
  const html = await fetchHtml(normalizedUrl);

  const svelteData = extractSvelteData(html);
  if (!svelteData) {
    throw new ApiError(500, "No se pudo extraer informacion del anime");
  }

  const media = chooseBestMediaCandidate(svelteData);
  if (!media) {
    throw new ApiError(404, "No se encontro informacion del anime");
  }

  return {
    success: true,
    data: normalizeAnimeInfo(media, domain),
    source: "json",
  };
}

async function searchAnime(query, domainCandidate) {
  const cleanQuery = (query || "").toString().trim();
  if (!cleanQuery) {
    throw new ApiError(400, "Se requiere el parametro q");
  }

  const domain = (domainCandidate || DEFAULT_DOMAIN || "animeav1.com").toString().trim();

  let bestResults = [];
  let bestSource = "html";

  const candidates = [
    { key: "search", value: cleanQuery },
    { key: "q", value: cleanQuery },
  ];

  for (const candidate of candidates) {
    const searchUrl = `https://${domain}/catalogo?${candidate.key}=${encodeURIComponent(candidate.value)}`;
    const html = await fetchHtml(searchUrl);

    let results = [];
    const svelteData = extractSvelteData(html);
    if (svelteData) {
      const bestArray = chooseLikelySearchArray(svelteData);
      if (bestArray) {
        results = mapSearchResults(bestArray, domain);
      }
    }

    if (results.length === 0) {
      results = parseSearchResultsFromHtml(html, domain);
    }

    results = filterSearchResultsByQuery(results, cleanQuery);

    if (results.length > bestResults.length) {
      bestResults = results;
      bestSource = svelteData ? "json" : "html";
    }

    if (bestResults.length >= 5) {
      break;
    }
  }

  return {
    success: true,
    data: {
      query: cleanQuery,
      results: bestResults,
      count: bestResults.length,
    },
    source: bestSource,
  };
}

async function getEpisodeLinks(urlCandidate, includeMegaRaw, excludeServersRaw) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const domain = detectDomain(normalizedUrl);
  const includeMega = parseBoolean(includeMegaRaw);
  const excludedTokens = buildExcludedTokens(includeMega, excludeServersRaw);

  const html = await fetchHtml(urlCandidate);
  const svelteData = extractSvelteData(html);
  const dataRoot = svelteData || {};

  const episodeObject = firstObjectByKey(dataRoot, "episode") || {};
  const links = extractLinksFromData(dataRoot, html, domain);

  const filteredStreamSub = filterLinksByServers(links.stream.SUB, excludedTokens);
  const filteredStreamDub = filterLinksByServers(links.stream.DUB, excludedTokens);
  const filteredDownloadSub = filterLinksByServers(links.download.SUB, excludedTokens);
  const filteredDownloadDub = filterLinksByServers(links.download.DUB, excludedTokens);

  // Combine all links for frontend compatibility
  const allLinks = [
    ...filteredStreamSub,
    ...filteredStreamDub,
    ...filteredDownloadSub,
    ...filteredDownloadDub
  ];

  return {
    success: true,
    data: {
      id: episodeObject.id ?? null,
      episode:
        parseNumber(episodeObject.number) ||
        parseNumber(episodeObject.episode) ||
        parseEpisodeNumberFromUrl(normalizedUrl),
      title: episodeObject.title || `Episodio ${parseEpisodeNumberFromUrl(normalizedUrl) || "?"}`,
      season: episodeObject.season ?? null,
      variants: {
        SUB: filteredStreamSub.length > 0 || filteredDownloadSub.length > 0 ? 1 : 0,
        DUB: filteredStreamDub.length > 0 || filteredDownloadDub.length > 0 ? 1 : 0,
      },
      publishedAt: episodeObject.publishedAt || episodeObject.published_at || null,
      // Combined links for frontend compatibility
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
    source: svelteData ? "json" : "html",
  };
}

async function getCatalog(page, genre) {
  const pageNum = Math.max(1, parseInt(page) || 1);
  const domain = DEFAULT_DOMAIN;

  let catalogUrl = `https://${domain}/catalogo?page=${pageNum}`;
  if (genre && typeof genre === "string" && genre.trim()) {
    catalogUrl += `&genre=${encodeURIComponent(genre.trim())}`;
  }

  const html = await fetchHtml(catalogUrl);

  let results = [];
  const svelteData = extractSvelteData(html);
  if (svelteData) {
    const bestArray = chooseLikelySearchArray(svelteData);
    if (bestArray) {
      results = mapSearchResults(bestArray, domain);
    }
  }

  if (results.length === 0) {
    results = parseSearchResultsFromHtml(html, domain);
  }

  return {
    success: true,
    data: {
      page: pageNum,
      genre: genre || null,
      results,
      count: results.length,
      hasMore: results.length >= 10,
    },
    source: svelteData ? "json" : "html",
  };
}

module.exports = {
  searchAnime,
  getAnimeInfo,
  getEpisodeLinks,
  getCatalog,
};
