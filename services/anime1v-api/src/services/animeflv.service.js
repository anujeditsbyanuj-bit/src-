const axios = require("axios");
const cheerio = require("cheerio");
const vm = require("node:vm");
const { URL } = require("node:url");
const { ApiError } = require("../utils/api-error");

// Shared global browser instead of launching multiple duplicate chrome processes
const { getBrowser } = require("../utils/browser");

async function fetchHtmlWithPuppeteer(url) {
  const browser = await getBrowser();
  const page = await browser.newPage();
  
  await page.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
  );
  
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  
  // Wait for protection to resolve
  let retries = 0;
  while (retries < 10) {
    const content = await page.content();
    const $ = cheerio.load(content);
    const title = $("title").text();
    const bodyText = $("body").text().trim();
    
    // If we have actual content (not just protection), break
    if (title && !title.includes("animeflv") && !title.includes("Checking")) break;
    if (bodyText.length > 500) break;
    
    await new Promise(r => setTimeout(r, 2000));
    retries++;
  }
  
  const content = await page.content();
  await page.close();
  
  return content;
}

const DEFAULT_DOMAIN = "animeflv.net";

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
      timeout,
    });
    return response.data;
  } catch (error) {
    try {
      console.log("fetchHtml: trying with shared puppeteer for", url);
      return await fetchHtmlWithPuppeteer(url);
    } catch (puppeteerError) {
      throw new ApiError(500, "No se pudo obtener contenido desde AnimeFLV", error.message);
    }
  }
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
    const number = Number(lastSegment.match(/(\d+)(?:\D*)$/)?.[1]);
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
    // Ignore decode errors
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
    .replace(/&amp;/g, "&");
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

  $("article.Anime, .ListAnimes li article").each((_, element) => {
    const card = $(element);
    const link = card.find("a[href^='/anime/']").first().attr("href") || card.find("a").first().attr("href");
    const title = card.find("h3.Title").first().text().trim() || card.find("img").attr("alt") || null;
    const image = card.find("img").first().attr("src") || card.find("img").first().attr("data-src") || null;

    if (!link || !title) {
      return;
    }

    const slug = link.split("/").filter(Boolean).pop() || null;

    results.push({
      id: null,
      title,
      slug,
      url: resolveAbsoluteUrl(link, domain),
      image: resolveAbsoluteUrl(image, domain),
      backdrop: null,
      type: card.find(".Type").first().text().trim() || null,
      score: null,
      status: null,
      year: null,
    });
  });

  return results;
}

function parseAnimeInfoFromHtml(html, domain) {
  const $ = cheerio.load(html);

  const title = $("h1").first().text().trim() || null;
  const description = $(".Description").first().text().trim() || $(".Anime-Description").first().text().trim() || null;
  const image =
    $(".AnimeCover img").attr("src") || $(".cover img").attr("src") || $(".Anime img").attr("src") || null;

  const genres = [];
  $(".Nvgnrs a, .ListAnmRel a").each((_, link) => {
    const name = $(link).text().trim();
    if (!name) {
      return;
    }
    genres.push({
      id: null,
      name,
      slug: name.toLowerCase().replace(/\s+/g, "-"),
      malId: null,
    });
  });

  return {
    title,
    description,
    image,
    genres,
    type: $(".Type").first().text().trim() || null,
  };
}

function parseEpisodesFromHtml(html, domain, slug) {
  const $ = cheerio.load(html);
  const episodes = [];

  $("a[href^='/ver/'], a[href*='/ver/']").each((_, element) => {
    const link = $(element).attr("href");
    if (!link) {
      return;
    }

    const url = resolveAbsoluteUrl(link, domain);
    const number = parseEpisodeNumberFromUrl(url);
    if (!number) {
      return;
    }

    if (episodes.some((ep) => ep.url === url)) {
      return;
    }

    episodes.push({
      id: null,
      number,
      title: `Episodio ${number}`,
      url,
    });
  });

  return episodes;
}

function parseEpisodeListFromScript(html, domain, slug) {
  const episodesLiteral = extractVarLiteral(html, "episodes");
  if (!episodesLiteral) {
    return [];
  }

  const list = safeEvaluate(`(${episodesLiteral})`);
  if (!Array.isArray(list)) {
    return [];
  }

  const resolvedSlug = slug || null;
  return list
    .map((entry) => {
      if (!Array.isArray(entry) || entry.length === 0) {
        return null;
      }

      const number = parseNumber(entry[0]);
      if (!number && number !== 0) {
        return null;
      }

      const episodeSlug = resolvedSlug ? `${resolvedSlug}-${number}` : null;
      const url = episodeSlug ? `https://${domain}/ver/${episodeSlug}` : null;

      return {
        id: entry[1] ?? null,
        number,
        title: `Episodio ${number}`,
        url,
      };
    })
    .filter((episode) => episode && episode.url);
}

function parseVideoSources(html) {
  const videosLiteral = extractVarLiteral(html, "videos");
  if (!videosLiteral) {
    return null;
  }

  let parsed = safeEvaluate(`(${videosLiteral})`);
  if (!parsed || typeof parsed !== "object") {
    return null;
  }

  for (const [key, entries] of Object.entries(parsed)) {
    if (!Array.isArray(entries)) {
      continue;
    }

    for (const entry of entries) {
      if (!entry || typeof entry !== "object") {
        continue;
      }

      for (const urlField of ["code", "url", "embed", "file"]) {
        if (entry[urlField] && typeof entry[urlField] === "string") {
          const decoded = tryDecodeBase64(entry[urlField]);
          if (decoded) {
            entry[urlField] = decoded;
          } else {
            entry[urlField] = decodeUrlEscapes(entry[urlField]);
          }
        }
      }
    }
  }

  return parsed;
}

function parseDownloadRows(html, domain) {
  const $ = cheerio.load(html);
  const rows = [];

  $("table tbody tr").each((_, element) => {
    const cells = $(element).find("td");
    const server = $(cells[0]).text().trim() || null;
    const format = $(cells[1]).text().trim() || null;
    const variant = $(cells[2]).text().trim() || null;
    const url = $(element).find("a").attr("href") || null;

    if (!url) {
      return;
    }

    rows.push({
      server,
      format,
      variant,
      url: resolveAbsoluteUrl(url, domain),
    });
  });

  return rows;
}

async function searchAnime(query, domainCandidate) {
  const cleanQuery = (query || "").toString().trim();
  if (!cleanQuery) {
    throw new ApiError(400, "Se requiere el parametro q");
  }

  const domain = (domainCandidate || DEFAULT_DOMAIN || "www4.animeflv.net").toString().trim();
  const searchUrl = `https://${domain}/browse?q=${encodeURIComponent(cleanQuery)}`;
  const html = await fetchHtml(searchUrl);

  const results = parseSearchResultsFromHtml(html, domain);

  return {
    success: true,
    data: {
      query: cleanQuery,
      results,
      count: results.length,
    },
    source: "animeflv",
  };
}

async function getAnimeInfo(urlCandidate) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const parsed = new URL(normalizedUrl);
  // Use the domain from the input URL if available, fallback to DEFAULT_DOMAIN
  const domain = parsed.hostname || DEFAULT_DOMAIN;
  const segments = parsed.pathname.split("/").filter(Boolean);

  let slug = segments[1] || "";
  if (segments[0] === "ver") {
    const rawSlug = segments[1] || "";
    slug = rawSlug.replace(/-\d+$/, "");
  }

  if (segments[0] === "anime") {
    slug = segments[1] || "";
  }

  if (!slug) {
    throw new ApiError(400, "URL invalida");
  }

  const baseUrl = `https://${domain}/anime/${slug}`;
  const html = await fetchHtml(baseUrl);

  const info = parseAnimeInfoFromHtml(html, domain);
  let episodes = parseEpisodesFromHtml(html, domain, slug);

  if (episodes.length === 0) {
    episodes = parseEpisodeListFromScript(html, domain, slug);
  }

  return {
    success: true,
    data: {
      id: null,
      title: info.title,
      titleJapanese: null,
      description: info.description,
      image: resolveAbsoluteUrl(info.image, domain),
      backdrop: null,
      status: null,
      type: info.type || null,
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
    source: "animeflv",
  };
}

async function getEpisodeLinks(urlCandidate, includeMegaRaw, excludeServersRaw) {
  const normalizedUrl = normalizeInputUrl(urlCandidate);
  const parsedEpUrl = new URL(normalizedUrl);
  const epDomain = parsedEpUrl.hostname || DEFAULT_DOMAIN;
  const includeMega = String(includeMegaRaw).toLowerCase() === "true";
  const excludedTokens = buildExcludedTokens(includeMega, excludeServersRaw);

  const html = await fetchHtml(normalizedUrl);

  const streamLinks = { SUB: [], DUB: [] };
  const downloadLinks = { SUB: [], DUB: [] };

  const videoSources = parseVideoSources(html);
  if (videoSources && typeof videoSources === "object") {
    for (const [key, entries] of Object.entries(videoSources)) {
      const variant = normalizeVariantKey(key);
      if (!Array.isArray(entries)) {
        continue;
      }

      for (const entry of entries) {
        if (!entry) {
          continue;
        }

        const url = entry.code || entry.url || entry.embed || entry.file || null;
        const serverName = entry.title || entry.server || "Unknown";
        const link = buildLinkRecord(serverName, url, null);
        if (link) {
          pushDeduped(streamLinks[variant], link);
        }
      }
    }
  }

  const downloadRows = parseDownloadRows(html, epDomain);
  for (const row of downloadRows) {
    const variant = normalizeVariantKey(row.variant);
    const link = buildLinkRecord(row.server || "Download", row.url, row.format || null);
    if (link) {
      pushDeduped(downloadLinks[variant], link);
    }
  }

  const filteredStreamSub = filterLinksByServers(streamLinks.SUB, excludedTokens);
  const filteredStreamDub = filterLinksByServers(streamLinks.DUB, excludedTokens);
  const filteredDownloadSub = filterLinksByServers(downloadLinks.SUB, excludedTokens);
  const filteredDownloadDub = filterLinksByServers(downloadLinks.DUB, excludedTokens);

  // Combine all links for frontend compatibility
  const allLinks = [
    ...filteredStreamSub,
    ...filteredStreamDub,
    ...filteredDownloadSub,
    ...filteredDownloadDub
  ];

  const episodeNumber = parseEpisodeNumberFromUrl(normalizedUrl);
  const title = cheerio.load(html)("h1").first().text().trim() || null;

  return {
    success: true,
    data: {
      id: null,
      episode: episodeNumber,
      title: title || `Episodio ${episodeNumber || "?"}`,
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
    source: "animeflv",
  };
}

async function getCatalog(page, genre) {
  const pageNum = Math.max(1, parseInt(page) || 1);
  const domain = DEFAULT_DOMAIN || "animeflv.net";

  let catalogUrl = `https://${domain}/browse?page=${pageNum}`;
  if (genre && typeof genre === "string" && genre.trim()) {
    catalogUrl += `&genre[]=${encodeURIComponent(genre.trim().toLowerCase())}`;
  }

  const html = await fetchHtml(catalogUrl);
  const results = parseSearchResultsFromHtml(html, domain);

  return {
    success: true,
    data: {
      page: pageNum,
      genre: genre || null,
      results,
      count: results.length,
      hasMore: results.length >= 20,
    },
    source: "animeflv",
  };
}

module.exports = {
  searchAnime,
  getAnimeInfo,
  getEpisodeLinks,
  getCatalog,
};
