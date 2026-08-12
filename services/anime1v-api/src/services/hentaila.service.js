const axios = require("axios");
const { URL } = require("node:url");
const { ApiError } = require("../utils/api-error");

const DEFAULT_DOMAIN = "hentaila.com";

const HTTP_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
};

function resolveDataValue(schemaObj, values, key) {
  const schema = schemaObj[key];
  if (schema === undefined || schema === null) {
    return null;
  }

  if (Array.isArray(schema)) {
    return schema.map((idx) => resolveDataValue(idx, values, 0));
  }

  if (typeof schema === "number") {
    return resolveDataValue(values, values, schema);
  }

  if (typeof schema === "object" && schema !== null) {
    const result = {};
    for (const [k, v] of Object.entries(schema)) {
      result[k] = resolveDataValue(v, values, v);
    }
    return result;
  }

  return schema;
}

function devalueObject(schema, values) {
  if (typeof schema !== "object" || schema === null) {
    return schema;
  }

  const result = {};
  for (const [key, valueIdx] of Object.entries(schema)) {
    if (typeof valueIdx === "number") {
      result[key] = values[valueIdx];
    } else if (typeof valueIdx === "object" && valueIdx !== null && !Array.isArray(valueIdx)) {
      result[key] = devalueObject(valueIdx, values);
    } else if (Array.isArray(valueIdx)) {
      result[key] = valueIdx.map((idx) => {
        if (typeof idx === "number") return values[idx];
        if (typeof idx === "object" && idx !== null && !Array.isArray(idx)) {
          return devalueObject(idx, values);
        }
        return idx;
      });
    } else {
      result[key] = valueIdx;
    }
  }

  return result;
}

function parseSvelteKitNodes(nodes) {
  if (!Array.isArray(nodes)) {
    return null;
  }

  const results = [];
  for (const node of nodes) {
    if (!node || node.type !== "data" || !Array.isArray(node.data) || node.data.length === 0) {
      results.push(null);
      continue;
    }

    const schema = node.data[0];
    const values = node.data;

    if (typeof schema === "object" && schema !== null) {
      results.push({ schema, values });
    } else {
      results.push(null);
    }
  }

  return results;
}

function resolveNodeValue(nodeData, schemaKey) {
  if (!nodeData || !nodeData.schema || !Array.isArray(nodeData.values)) {
    return null;
  }

  const idx = nodeData.schema[schemaKey];
  if (typeof idx === "number" && idx >= 0 && idx < nodeData.values.length) {
    return nodeData.values[idx];
  }

  return null;
}

function resolveItemFromData(dataValues, schemaIndex) {
  const itemSchema = dataValues[schemaIndex];
  if (!itemSchema || typeof itemSchema !== "object") {
    return null;
  }

  const item = {};
  for (const [key, valueIdx] of Object.entries(itemSchema)) {
    if (typeof valueIdx === "number" && valueIdx < dataValues.length) {
      item[key] = dataValues[valueIdx];
    }
  }

  return item;
}

function resolveResultsArray(dataValues, refsArray) {
  if (!Array.isArray(refsArray)) {
    return [];
  }

  return refsArray
    .map((idx) => resolveItemFromData(dataValues, idx))
    .filter(Boolean);
}

function resolveNodeObject(nodeData, keys) {
  if (!nodeData || !nodeData.schema || !Array.isArray(nodeData.values)) {
    return null;
  }

  const result = {};
  for (const key of Array.isArray(keys) ? keys : [keys]) {
    const idx = nodeData.schema[key];
    if (typeof idx === "number" && idx >= 0 && idx < nodeData.values.length) {
      result[key] = nodeData.values[idx];
    }
  }

  return result;
}

async function fetchJson(url) {
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
    throw new ApiError(500, "No se pudo obtener contenido desde HentaiLA", error.message);
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
  if (!normalized) {
    return "SUB";
  }

  if (normalized.includes("sub") || normalized.includes("jap") || normalized.includes("jp")) {
    return "SUB";
  }

  if (normalized.includes("dub") || normalized.includes("lat") || normalized.includes("latin") || normalized.includes("esp")) {
    return "DUB";
  }

  return "SUB";
}

function parseEpisodeNumberFromUrl(url) {
  try {
    const pathname = new URL(url).pathname;
    const segments = pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    const numberMatch = lastSegment.match(/(\d+)$/);
    return numberMatch ? Number(numberMatch[1]) : null;
  } catch (_error) {
    return null;
  }
}

function buildLinkRecord(serverName, url, quality) {
  if (!url) {
    return null;
  }

  return {
    server: serverName || "Unknown",
    url,
    quality: quality || null,
  };
}

function parseEmbedsFromNode(nodeData) {
  if (!nodeData || !nodeData.schema) {
    return { SUB: [], DUB: [] };
  }

  const schema = nodeData.schema;
  const data = nodeData.values;

  const embedsIdx = schema["embeds"];
  if (typeof embedsIdx !== "number") {
    return { SUB: [], DUB: [] };
  }

  const embedsRaw = data[embedsIdx];
  if (!embedsRaw || typeof embedsRaw !== "object") {
    return { SUB: [], DUB: [] };
  }

  const result = { SUB: [], DUB: [] };
  for (const [variantKey, variantIdx] of Object.entries(embedsRaw)) {
    const variant = normalizeVariantKey(variantKey);

    // variantIdx points to an array of schema references
    const refsArray = typeof variantIdx === "number" ? data[variantIdx] : variantIdx;
    if (!Array.isArray(refsArray)) {
      continue;
    }

    const entries = resolveResultsArray(data, refsArray);
    for (const entry of entries) {
      if (!entry || !entry.url) {
        continue;
      }

      const link = buildLinkRecord(entry.server || "Unknown", entry.url, null);
      if (link) {
        result[variant].push(link);
      }
    }
  }

  return result;
}

function parseDownloadsFromNode(nodeData) {
  if (!nodeData || !nodeData.schema) {
    return { SUB: [], DUB: [] };
  }

  const schema = nodeData.schema;
  const data = nodeData.values;

  const downloadsIdx = schema["downloads"];
  if (typeof downloadsIdx !== "number") {
    return { SUB: [], DUB: [] };
  }

  const downloadsRaw = data[downloadsIdx];
  if (!downloadsRaw || typeof downloadsRaw !== "object") {
    return { SUB: [], DUB: [] };
  }

  const result = { SUB: [], DUB: [] };
  for (const [variantKey, variantIdx] of Object.entries(downloadsRaw)) {
    const variant = normalizeVariantKey(variantKey);

    const refsArray = typeof variantIdx === "number" ? data[variantIdx] : variantIdx;
    if (!Array.isArray(refsArray)) {
      continue;
    }

    const entries = resolveResultsArray(data, refsArray);
    for (const entry of entries) {
      if (!entry || !entry.url) {
        continue;
      }

      const link = buildLinkRecord(entry.server || "Download", entry.url, entry.quality || entry.server || null);
      if (link) {
        result[variant].push(link);
      }
    }
  }

  return result;
}

function parseMediaFromNode(nodeData) {
  if (!nodeData || !nodeData.schema) {
    return null;
  }

  const data = nodeData.values;

  // Check for nested schema pattern (e.g. {"media": 1} → resolve to inner schema)
  const outerKeys = Object.keys(nodeData.schema);
  let innerSchemaIndex = -1;
  for (const key of outerKeys) {
    const idx = nodeData.schema[key];
    if (typeof idx === "number" && typeof data[idx] === "object" && data[idx] !== null && !Array.isArray(data[idx])) {
      innerSchemaIndex = idx;
      break;
    }
  }

  // Use inner schema if found, otherwise use outer schema
  const schemaIdx = innerSchemaIndex >= 0 ? innerSchemaIndex : 0;
  const schema = innerSchemaIndex >= 0 ? data[innerSchemaIndex] : nodeData.schema;

  const get = (key) => {
    const idx = schema[key];
    return (typeof idx === "number" && idx < data.length) ? data[idx] : null;
  };

  const genresRefs = get("genres");
  const genres = Array.isArray(genresRefs)
    ? resolveResultsArray(data, genresRefs)
    : [];

  const episodesRefs = get("episodes");
  const episodes = Array.isArray(episodesRefs)
    ? resolveResultsArray(data, episodesRefs)
    : [];

  const categoryRaw = get("category");
  const category = categoryRaw && typeof categoryRaw === "object" ? categoryRaw : null;

  return {
    id: get("id"),
    title: get("title"),
    slug: get("slug"),
    synopsis: get("synopsis"),
    status: get("status"),
    episodesCount: get("episodesCount"),
    score: get("score"),
    votes: get("votes"),
    malId: get("malId"),
    startDate: get("startDate"),
    poster: get("poster"),
    category,
    genres,
    episodes,
  };
}

function buildEpisodesList(mediaData) {
  if (!mediaData || !mediaData.episodes || !Array.isArray(mediaData.episodes)) {
    return [];
  }

  const slug = mediaData.slug || "";
  return mediaData.episodes
    .filter((ep) => ep && ep.number && ep.id)
    .map((ep) => ({
      id: ep.id,
      number: ep.number,
      title: `Episodio ${ep.number}`,
      url: `https://${DEFAULT_DOMAIN}/ver/${slug}-${ep.number}`,
    }));
}

function slugFromUrl(url) {
  try {
    const pathname = new URL(url).pathname;
    const segments = pathname.split("/").filter(Boolean);
    const lastSegment = segments[segments.length - 1] || "";
    return lastSegment.replace(/-\d+$/, "");
  } catch (_error) {
    return null;
  }
}

// Public API

async function searchAnime(query, domainCandidate) {
  const cleanQuery = (query || "").toString().trim();
  if (!cleanQuery) {
    throw new ApiError(400, "Se requiere el parametro q");
  }

  const domain = (domainCandidate || DEFAULT_DOMAIN).toString().trim();
  const searchUrl = `https://${domain}/catalogo/__data.json?search=${encodeURIComponent(cleanQuery)}`;
  const data = await fetchJson(searchUrl);

  const parsedNodes = parseSvelteKitNodes(data.nodes);
  const catalogNode = parsedNodes && parsedNodes.length > 2 ? parsedNodes[2] : null;

  let results = [];
  if (catalogNode) {
    const rawResults = resolveNodeValue(catalogNode, "results");
    if (Array.isArray(rawResults)) {
      results = resolveResultsArray(catalogNode.values, rawResults)
        .filter((item) => item && item.title)
        .map((item) => ({
          id: item.id || null,
          title: item.title,
          slug: item.slug || null,
          url: `https://${domain}/media/${item.slug}`,
          image: item.id ? `https://cdn.hentaila.com/covers/${item.id}.jpg` : null,
          backdrop: null,
          type: item.category ? item.category.name : null,
          score: item.score || null,
          status: item.status || null,
          year: null,
        }));
    }
  }

  return {
    success: true,
    data: {
      query: cleanQuery,
      results,
      count: results.length,
    },
    source: "hentaila",
  };
}

async function getAnimeInfo(urlCandidate) {
  const slug = slugFromUrl(urlCandidate);
  if (!slug) {
    throw new ApiError(400, "URL invalida");
  }

  const apiUrl = `https://${DEFAULT_DOMAIN}/media/${slug}/__data.json`;
  const data = await fetchJson(apiUrl);

  const parsedNodes = parseSvelteKitNodes(data.nodes);
  const mediaNode = parsedNodes && parsedNodes.length > 2 ? parsedNodes[2] : null;

  if (!mediaNode) {
    throw new ApiError(404, "Anime no encontrado en HentaiLA");
  }

  const mediaData = parseMediaFromNode(mediaNode);
  const episodes = buildEpisodesList(mediaData);

  return {
    success: true,
    data: {
      id: mediaData.id || null,
      title: mediaData.title,
      titleJapanese: null,
      description: mediaData.synopsis || null,
      image: mediaData.id ? `https://cdn.hentaila.com/covers/${mediaData.id}.jpg` : null,
      backdrop: null,
      status: null,
      type: mediaData.category ? mediaData.category.name : null,
      year: mediaData.startDate ? String(mediaData.startDate).split("-")[0] : null,
      startDate: mediaData.startDate || null,
      endDate: null,
      score: mediaData.score || null,
      votes: mediaData.votes || null,
      totalEpisodes: episodes.length,
      malId: mediaData.malId || null,
      trailer: null,
      genres: Array.isArray(mediaData.genres)
        ? mediaData.genres.map((g) => ({
            id: g.id || null,
            name: g.name,
            slug: g.slug || g.name.toLowerCase().replace(/\s+/g, "-"),
            malId: g.malId || null,
          }))
        : [],
      episodes,
    },
    source: "hentaila",
  };
}

async function getEpisodeLinks(urlCandidate) {
  const slug = slugFromUrl(urlCandidate);
  const episodeNumber = parseEpisodeNumberFromUrl(urlCandidate);

  if (!slug || !episodeNumber) {
    throw new ApiError(400, "URL invalida - no se pudo extraer slug y numero de episodio");
  }

  const apiUrl = `https://${DEFAULT_DOMAIN}/media/${slug}/${episodeNumber}/__data.json`;
  const data = await fetchJson(apiUrl);

  const parsedNodes = parseSvelteKitNodes(data.nodes);

  // Find episode node - resolve nested schemas
  let episodeNode = null;
  for (let i = parsedNodes ? parsedNodes.length - 1 : 0; i >= 0; i--) {
    const node = parsedNodes[i];
    if (!node || !node.schema) continue;

    const data = node.values;

    // Check if this node has embeds or downloads, OR has a nested schema pointing to episode data
    for (const [key, idx] of Object.entries(node.schema)) {
      if (typeof idx !== "number") continue;

      // Direct keys
      if (key === "embeds" || key === "downloads" || key === "episode") {
        episodeNode = node;
        break;
      }

      // Nested schema pattern
      if (typeof data[idx] === "object" && data[idx] !== null && !Array.isArray(data[idx])) {
        const inner = data[idx];
        if (inner.embeds !== undefined || inner.downloads !== undefined || inner.episode !== undefined) {
          episodeNode = { schema: inner, values: data };
          break;
        }
      }
    }

    if (episodeNode) break;
  }

  if (!episodeNode) {
    return {
      success: true,
      data: {
        id: null,
        episode: episodeNumber,
        title: `Episodio ${episodeNumber}`,
        season: null,
        variants: { SUB: 0, DUB: 0 },
        publishedAt: null,
        servers: { sub: [], dub: [] },
        streamLinks: { SUB: [], DUB: [] },
        downloadLinks: { SUB: [], DUB: [] },
      },
      source: "hentaila",
    };
  }

  const embeds = parseEmbedsFromNode(episodeNode);
  const downloads = parseDownloadsFromNode(episodeNode);
  const epData = resolveNodeObject(episodeNode, ["title", "number"]);

  return {
    success: true,
    data: {
      id: null,
      episode: episodeNumber,
      title: (embeds.SUB.length > 0 || downloads.SUB.length > 0)
        ? (epData ? epData.title || `Episodio ${episodeNumber}` : `Episodio ${episodeNumber}`)
        : `Episodio ${episodeNumber}`,
      season: null,
      variants: {
        SUB: embeds.SUB.length > 0 || downloads.SUB.length > 0 ? 1 : 0,
        DUB: embeds.DUB.length > 0 || downloads.DUB.length > 0 ? 1 : 0,
      },
      publishedAt: null,
      servers: {
        sub: embeds.SUB.map((l) => ({ server: l.server, url: l.url })),
        dub: embeds.DUB.map((l) => ({ server: l.server, url: l.url })),
      },
      streamLinks: {
        SUB: embeds.SUB.map((l) => ({ server: l.server, url: l.url })),
        DUB: embeds.DUB.map((l) => ({ server: l.server, url: l.url })),
      },
      downloadLinks: {
        SUB: downloads.SUB.map((l) => ({
          server: l.server,
          url: l.url,
          quality: l.quality || l.server,
        })),
        DUB: downloads.DUB.map((l) => ({
          server: l.server,
          url: l.url,
          quality: l.quality || l.server,
        })),
      },
    },
    source: "hentaila",
  };
}

module.exports = {
  searchAnime,
  getAnimeInfo,
  getEpisodeLinks,
};
