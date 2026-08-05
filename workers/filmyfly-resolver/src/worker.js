// worker.js - FileDL Direct Resolver with Bot Bypass (text-based matching)

const FILMYFLY_BASE = "https://filmyfly.luxe";
const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,/;q=0.8",
  "Accept-Language": "en-US,en;q=0.5",
  "Referer": "https://filmyfly.luxe/",
};

// ─── Router ───────────────────────────────────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname, searchParams } = url;

    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    try {
      let response;

      if (pathname === "/" && request.method === "GET") {
        const pageUrl = searchParams.get("url");
        if (!pageUrl) {
          response = json({ error: "Missing 'url' parameter" }, 400);
        } else {
          response = await handleSingleMovie(pageUrl, env);
        }
      } else if (pathname === "/debug" && request.method === "GET") {
        // Debug: raw HTML dekhne ke liye (testing ke baad hata sakte ho)
        const testUrl = searchParams.get("url");
        if (!testUrl) {
          response = json({ error: "Missing 'url' parameter" }, 400);
        } else {
          const html = await fetchFilesdlHtml(testUrl, env);
          response = new Response(html, { headers: { "Content-Type": "text/plain" } });
        }
      } else {
        response = json({ error: "Not found" }, 404);
      }

      Object.entries(corsHeaders).forEach(([k, v]) => response.headers.set(k, v));
      return response;

    } catch (err) {
      return json({ error: "Internal Server Error", detail: err.message }, 500);
    }
  },
};

// ─── GET /?url=... ────────────────────────────────────────────────────────────

async function handleSingleMovie(pageUrl, env) {
  try {
    const detailHtml = await fetchHtml(pageUrl);
    const detail = parseDetailPage(detailHtml);

    if (!detail.linkmakeUrl) {
      return json({ error: "No download link found" }, 404);
    }

    const linkmakeHtml = await fetchHtml(detail.linkmakeUrl);
    const linkmakeTitle = scrapeLinkmakeTitle(linkmakeHtml);
    const groupedLinks = parseLinkmakeGroupedAuto(linkmakeHtml);

    if (!groupedLinks.length) {
      return json({ error: "No download links found" }, 404);
    }

    const hasValidGroups = groupedLinks.some(g =>
      g.groupTitle &&
      !g.groupTitle.includes("margin") &&
      !g.groupTitle.includes("style") &&
      !g.groupTitle.includes("color") &&
      !g.groupTitle.includes("width") &&
      !g.groupTitle.includes("text-url") &&
      !g.groupTitle.includes("font") &&
      !g.groupTitle.includes("Download Links")
    );

    let downloadLinks;

    if (hasValidGroups) {
      downloadLinks = await Promise.all(
        groupedLinks
          .filter(g =>
            !g.groupTitle.includes("margin") &&
            !g.groupTitle.includes("style") &&
            !g.groupTitle.includes("width") &&
            !g.groupTitle.includes("text-url")
          )
          .map(async group => ({
            groupTitle: group.groupTitle,
            links: await Promise.all(
              group.links.map(async link => ({
                size: link.size,
                url: await resolveFilesdlLink(link.filesdlUrl, env)
              }))
            )
          }))
      );
    } else {
      downloadLinks = await Promise.all(
        groupedLinks.flatMap(group =>
          group.links.map(async link => ({
            size: link.size,
            url: await resolveFilesdlLink(link.filesdlUrl, env)
          }))
        )
      );
    }

    const movieObj = buildMovieObject(detail, downloadLinks, linkmakeTitle, hasValidGroups);
    return json({ movies: [movieObj] });

  } catch (err) {
    console.error("Error processing movie:", err.message);
    return json({ error: "Failed to process movie", detail: err.message }, 500);
  }
}

// ─── FileDL Resolver with Bot Bypass (text-based matching) ──────────────────

function filesdlHeaders(refererUrl) {
  return {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": refererUrl,
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Cache-Control": "max-age=0",
  };
}

const SCRAPERAPI_KEY = "2ce2548f2691683b0d0621a837b445bf";

async function fetchViaScraperApi(targetUrl, env) {
  const apiKey = SCRAPERAPI_KEY;
  if (!apiKey) {
    throw new Error("SCRAPERAPI_KEY not configured");
  }
  const proxyUrl = `https://api.scraperapi.com/?api_key=${apiKey}&url=${encodeURIComponent(targetUrl)}`;
  const res = await fetch(proxyUrl);
  if (!res.ok) {
    throw new Error(`ScraperAPI fetch failed: ${targetUrl} [${res.status}]`);
  }
  return res.text();
}

async function fetchFilesdlHtml(targetUrl, env) {
  try {
    // Step 1: try manual redirect first to discover the real domain
    let pageRes = await fetch(targetUrl, {
      headers: filesdlHeaders("https://google.com/"),
      redirect: "manual",
    });

    // If it's a redirect, follow to the Location header manually
    if (pageRes.status >= 300 && pageRes.status < 400) {
      const location = pageRes.headers.get("location");
      if (location) {
        const resolvedUrl = new URL(location, targetUrl).toString();
        pageRes = await fetch(resolvedUrl, {
          headers: filesdlHeaders(targetUrl),
          redirect: "follow",
        });
        if (pageRes.ok) return pageRes.text();
        throw new Error(`blocked after redirect [${pageRes.status}]`);
      }
    }

    if (pageRes.ok) return pageRes.text();

    throw new Error(`blocked [${pageRes.status}]`);

  } catch (directErr) {
    // Step 2: Fallback to ScraperAPI proxy (bypasses IP/WAF blocks)
    console.error("Direct fetch failed, falling back to ScraperAPI:", directErr.message);
    return fetchViaScraperApi(targetUrl, env);
  }
}

async function resolveFilesdlLink(filesdlUrl, env) {
  try {
    const urlMatch = filesdlUrl.match(/https:\/\/new1\.filesdl\.in\/(drive|cloud)\/([^?]+)/);
    if (!urlMatch) return filesdlUrl;

    const type = urlMatch[1];
    const id = urlMatch[2].split('?')[0];
    const targetUrl = `https://new1.filesdl.in/${type}/${id}`;

    const html = await fetchFilesdlHtml(targetUrl, env);
    let downloadUrl = null;

    // Priority 1: "Direct Download" (Fast/10Gbps) — zdownload.php or fdownload.php
    const fastMatch =
      html.match(/<a[^>]+href=["']([^"']*(?:zdownload|fdownload)\.php[^"']*)["'][^>]*>\s*Direct Download/i) ||
      html.match(/<a[^>]+href=["']([^"']*(?:zdownload|fdownload)\.php[^"']*)["']/i);

    if (fastMatch) downloadUrl = fastMatch[1];

    // Priority 2: "Cloud Direct" — r2.dev link
    if (!downloadUrl) {
      const cloudMatch =
        html.match(/<a[^>]+href=["']([^"']*r2\.dev[^"']*)["'][^>]*>\s*Cloud Direct/i) ||
        html.match(/<a[^>]+href=["']([^"']*r2\.dev[^"']*)["']/i);

      if (cloudMatch) downloadUrl = cloudMatch[1];
    }

    // Priority 3: Any bbbdownload/bbdownload fallback (older page structure)
    if (!downloadUrl) {
      const bbbMatch =
        html.match(/<a[^>]+href=["'](https:\/\/bbbdownload\.filesdl\.in\/[^"']+)["']/i) ||
        html.match(/<a[^>]+href=["'](https:\/\/bbdownload\.filesdl\.in\/[^"']+)["']/i);

      if (bbbMatch) downloadUrl = bbbMatch[1];
    }

    if (!downloadUrl) return filesdlUrl;

    // Decode HTML entities
    downloadUrl = downloadUrl
      .replace(/&amp;/g, '&')
      .replace(/&#038;/g, '&');

    // Token append for r2.dev / expired links
    const token = Math.floor(1000000000 + Math.random() * 9000000000);
    if (downloadUrl.includes("r2.dev") || downloadUrl.includes("expired=")) {
      downloadUrl = downloadUrl + "&token=" + token;
    }

    return downloadUrl;

  } catch (err) {
    console.error("FileDL resolve error:", err.message);
    return filesdlUrl;
  }
}

// ─── Scrapers ─────────────────────────────────────────────────────────────────

function parseDetailPage(html) {
  const result = {
    title: null,
    genre: [],
    duration: null,
    releaseYear: null,
    language: null,
    starcast: [],
    description: null,
    posterImage: null,
    linkmakeUrl: null,
    category: null,
  };

  const ogImage = html.match(/<meta property="og:image" content="([^"]+)"/);
  if (ogImage) result.posterImage = ogImage[1];

  const jsonLdMatch = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/);
  if (jsonLdMatch) {
    try {
      const ld = JSON.parse(jsonLdMatch[1]);
      if (ld.genre) result.genre = ld.genre.split(",").map((s) => s.trim());
      if (ld.duration) result.duration = ld.duration;
      if (ld.datePublished) result.releaseYear = ld.datePublished;
      if (ld.description) result.description = ld.description;
      if (ld.name) result.title = ld.name;
    } catch {}
  }

  const langMatch = html.match(/<strong>Language:<\/strong>\s*<span[^>]*>([^<]+)<\/span>/);
  if (langMatch) result.language = langMatch[1].trim();

  const starMatch = html.match(/<strong>Starcast:<\/strong>\s*<span[^>]*>([^<]+)<\/span>/);
  if (starMatch) {
    result.starcast = starMatch[1].split(",").map((s) => s.trim()).filter(Boolean);
  }

  const dlMatch = html.match(/<div class="dlbtn">\s*<a[^>]+href="(https:\/\/linkmake.in[^"]+)"/);
  if (dlMatch) result.linkmakeUrl = dlMatch[1];

  const breadMatch = html.match(/»\s*<a href="[^"]+">([^<]+)<\/a>\s*»/);
  if (breadMatch) result.category = breadMatch[1].trim();

  return result;
}

function scrapeLinkmakeTitle(html) {
  const titleMatch = html.match(/<title>\s*([^<]*)\s*<\/title>/);
  if (titleMatch) {
    let title = titleMatch[1].trim();
    title = title.replace(/\s*[-|]\s*(LinkMake\.in|JioLink|Link Protect).*$/i, '').trim();
    return title || null;
  }

  const fnameMatch = html.match(/<span style="color:#f44336;">([^<]+)<\/span>/);
  if (fnameMatch) return fnameMatch[1].trim();

  const ogTitleMatch = html.match(/<meta property="og:title" content="([^"]+)"/);
  if (ogTitleMatch) return ogTitleMatch[1].trim();

  return null;
}

// ─── PERMANENT SOLUTION: AUTO PATTERN DETECTION ──────────────────────────────

function parseLinkmakeGroupedAuto(html) {
  const groups = [];

  const groupPatterns = detectGroupPatterns(html);

  const groupPositions = [];
  for (const pattern of groupPatterns) {
    const regex = new RegExp(pattern, 'g');
    let match;
    while ((match = regex.exec(html)) !== null) {
      const title = match[1].trim();
      if (!isInvalidTitle(title)) {
        groupPositions.push({
          title: title,
          startIndex: match.index,
          endIndex: match.index + match[0].length
        });
      }
    }
    if (groupPositions.length) break;
  }

  if (!groupPositions.length) {
    return parseLinkmakeFallback(html);
  }

  for (let i = 0; i < groupPositions.length; i++) {
    const currentGroup = groupPositions[i];
    const startIndex = currentGroup.endIndex;
    const endIndex = (i < groupPositions.length - 1) ? groupPositions[i + 1].startIndex : html.length;

    const sectionHtml = html.substring(startIndex, endIndex);
    const links = extractLinks(sectionHtml);

    if (links.length) {
      groups.push({
        groupTitle: currentGroup.title,
        links: links
      });
    }
  }

  return groups.length ? groups : parseLinkmakeFallback(html);
}

function detectGroupPatterns(html) {
  const patterns = [];

  const groupRegex = /(🔰.*?\{[^}]+\}.*?🔰|🔰.*?\([^)]+\).*?🔰|🔰.*?\[[^\]]+\].*?🔰)/g;
  let match;
  while ((match = groupRegex.exec(html)) !== null) {
    const pattern = match[0];
    let regexPattern = pattern
      .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\\\{[^}]+\\\}/, '\\{([^}]+)\\}')
      .replace(/\\\([^)]+\\\)/, '\\\(([^)]+)\\)')
      .replace(/\\\[[^\]]+\\\]/, '\\\[([^\]]+)\\]');

    if (pattern.includes('{') && pattern.includes('}')) {
      regexPattern = regexPattern.replace(/\\\{[^}]+\\\}/, '\\{([^}]+)\\}');
    } else if (pattern.includes('(') && pattern.includes(')')) {
      regexPattern = regexPattern.replace(/\\\([^)]+\\\)/, '\\\(([^)]+)\\)');
    } else if (pattern.includes('[') && pattern.includes(']')) {
      regexPattern = regexPattern.replace(/\\\[[^\]]+\\\]/, '\\\[([^\]]+)\\]');
    }

    patterns.push(regexPattern);
  }

  if (!patterns.length) {
    patterns.push(
      '🔰~••\\s*\\{([^}]+)\\}\\s*••~🔰',
      '🔰~~×××\\s*\\(([^)]+)\\)\\s*×××~~🔰',
      '🔰~~×××\\s*\\{([^}]+)\\}\\s*×××~~🔰',
      '🔰\\s*\\{([^}]+)\\}\\s*🔰',
      '🔰\\s*\\(([^)]+)\\)\\s*🔰'
    );
  }

  return patterns;
}

function isInvalidTitle(title) {
  const invalidKeywords = [
    'margin', 'style', 'color', 'width', 'text-url',
    'font', 'display', 'position', 'padding', 'border',
    'background', 'font-size', 'line-height', 'important',
    'url', 'css', 'media', 'max-width', 'min-width'
  ];
  return invalidKeywords.some(keyword => title.toLowerCase().includes(keyword));
}

function extractLinks(html) {
  const links = [];

  const dlinkRegex = /<div class="dlink dl"><a href="(https:\/\/[^"]+)"[^>]*><div class="dll">\s*([^<]+)<\/div><\/a><\/div>/g;
  let match;
  while ((match = dlinkRegex.exec(html)) !== null) {
    links.push({
      filesdlUrl: match[1],
      size: extractSize(match[2])
    });
  }

  if (!links.length) {
    const simpleRegex = /<a href="(https:\/\/new1\.filesdl\.in[^"]+)"[^>]*>([^<]*)<\/a>/g;
    while ((match = simpleRegex.exec(html)) !== null) {
      links.push({
        filesdlUrl: match[1],
        size: extractSize(match[2])
      });
    }
  }

  return links;
}

function extractSize(label) {
  const sizeMatch = label.match(/(\d+(?:\.\d+)?(?:Mb|Gb|MB|GB))/i);
  return sizeMatch ? sizeMatch[1] : label.trim();
}

// ─── FALLBACK PARSER ──────────────────────────────────────────────────────────

function parseLinkmakeFallback(html) {
  const groups = [];
  const fallbackGroup = {
    groupTitle: "Download Links",
    links: []
  };

  const dlinkRegex = /<div class="dlink dl"><a href="(https:\/\/[^"]+)"[^>]*><div class="dll">\s*([^<]+)<\/div><\/a><\/div>/gi;
  let match;
  while ((match = dlinkRegex.exec(html)) !== null) {
    const filesdlUrl = match[1];
    const label = match[2].trim();
    const sizeMatch = label.match(/(\d+(?:\.\d+)?(?:Mb|Gb|MB|GB))/i);
    fallbackGroup.links.push({
      filesdlUrl: filesdlUrl,
      size: sizeMatch ? sizeMatch[1] : label
    });
  }

  if (fallbackGroup.links.length) {
    groups.push(fallbackGroup);
  }

  return groups;
}

// ─── HELPERS ──────────────────────────────────────────────────────────────────

function buildMovieObject(detail, downloadLinks, linkmakeTitle, hasGroups) {
  let quality = "480p, 720p, 1080p";
  let allSizes;

  if (hasGroups) {
    allSizes = downloadLinks.flatMap(g => g.links.map(l => l.size || "")).join(" ");
  } else {
    allSizes = downloadLinks.map(l => l.size || "").join(" ");
  }

  if (/UHD|4K/i.test(allSizes)) quality = "4K";
  else if (/1080/i.test(allSizes)) quality = "1080p";
  else if (/720/i.test(allSizes)) quality = "720p";
  else if (/480/i.test(allSizes)) quality = "480p";

  const finalTitle = linkmakeTitle || detail.title || "Unknown Title";

  return {
    title: finalTitle,
    releaseYear: detail.releaseYear ?? null,
    duration: detail.duration ?? null,
    language: detail.language ?? null,
    quality: quality,
    genre: detail.genre ?? [],
    starcast: detail.starcast ?? [],
    posterImage: detail.posterImage ?? null,
    description: detail.description ?? null,
    isPremium: true,
    downloadLinks: downloadLinks
  };
}

async function fetchHtml(url) {
  const res = await fetch(url, {
    headers: HEADERS,
    cf: { cacheTtl: 0, cacheEverything: false },
  });

  if (!res.ok) throw new Error(`Fetch failed: ${url} [${res.status}]`);
  return res.text();
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
