const cheerio = require("cheerio");
const { URL } = require("node:url");
const { ApiError } = require("./api-error");
const {
  fetchHtmlWithHeaders,
  findFirstUrl,
  isLikelyVideoUrl,
  normalizeExtractedUrl,
} = require("./http");

// Shared browser instance
const { getBrowser } = require("./browser");

// yt-dlp high-speed resolver
const ytdlpResolver = require("./resolvers/ytdlp.resolver");

// Modular high-performance resolvers
const { extractVoe } = require("./resolvers/voe.resolver");
const { extractStreamwish } = require("./resolvers/streamwish.resolver");
const { extractStreamtape } = require("./resolvers/streamtape.resolver");

const DEBUG_MODE = process.env.DEBUG_RESOLVER === "true" || process.env.DEBUG_DOWNLOAD === "true";

function debugLog(server, message, data) {
  if (!DEBUG_MODE) {
    return;
  }
  const timestamp = new Date().toISOString();
  const header = `[${timestamp}] [${server}] ${message}`;
  if (data) {
    console.log(header, typeof data === "string" ? data.slice(0, 500) : data);
  } else {
    console.log(header);
  }
}

function getRefererForUrl(url) {
  try {
    const parsed = new URL(url);
    return `${parsed.origin}/`;
  } catch (_error) {
    return "https://www.animeav1.com/";
  }
}

async function resolveEmbedWithPuppeteer(url, referer) {
  debugLog("Puppeteer", "Resolving URL", url);
  try {
    const browser = await getBrowser();
    const page = await browser.newPage();

    await page.setUserAgent(
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    );

    if (referer) {
      await page.setExtraHTTPHeaders({ Referer: referer });
    }

    // 1. Activar interceptación de peticiones de red
    await page.setRequestInterception(true);

    let interceptedUrl = null;
    page.on("request", (req) => {
      const rUrl = req.url();
      const type = req.resourceType();
      
      // Capturar la URL del stream de video objetivo
      const isMedia = /\.(?:m3u8|mp4)(?:\?|#|$)/i.test(rUrl);
      if (
        !interceptedUrl &&
        type !== "document" &&
        isMedia &&
        !rUrl.startsWith("blob:") &&
        !rUrl.includes("blank")
      ) {
        interceptedUrl = rUrl;
      }

      // 2. Bloquear elementos pesados que no aportan a la extracción
      const blockTypes = ["image", "stylesheet", "font"];
      if (blockTypes.includes(type)) {
        req.abort();
        return;
      }

      // 3. Bloquear dominios y scripts de redes de publicidad invasivas
      const isAd = /(google-analytics|doubleclick|adsterra|exoclick|popads|propellerads|clickadu|popcash|adnxs|mgid|outbrain|taboola|histats|amung|jads|ero-advertising|realsrv|admaven|coinhive|monetize|track|adshost|adsrv|a.bestcontent|a.shorte)/i.test(rUrl);
      if (isAd || (type === "script" && !rUrl.includes("player") && !rUrl.includes("video") && !rUrl.includes("jwplatform") && !rUrl.includes("plyr"))) {
        req.abort();
        return;
      }

      req.continue();
    });

    // Wait for page to load
    await page.goto(url, { waitUntil: "networkidle2", timeout: 15000 });

    // Attempt to click play buttons to trigger video load
    try {
      const playBtnSelectors =
        ".play-button, button, .vjs-big-play-button, [role='button'], .jw-icon-display, .plyr__control--overlaid";
      const playBtn = await page.$(playBtnSelectors);
      if (playBtn) await playBtn.click();
    } catch (e) {}

    // Wait for potential JS to execute and requests to fire
    await new Promise((r) => setTimeout(r, 4000));

    // Try clicking inside iframes
    if (!interceptedUrl) {
      for (const frame of page.frames()) {
        try {
          const playBtn = await frame.$(
            ".play-button, button, .vjs-big-play-button, [role='button'], .jw-icon-display, .plyr__control--overlaid"
          );
          if (playBtn) await playBtn.click();
        } catch (e) {}
      }
      await new Promise((r) => setTimeout(r, 3000));
    }

    if (interceptedUrl) {
      debugLog("Puppeteer", "Intercepted media URL", interceptedUrl);
      await page.close();
      return interceptedUrl;
    }

    const html = await page.content();
    await page.close();

    debugLog("Puppeteer", "Fetched HTML length", html.length);

    const $ = cheerio.load(html);

    // Try many patterns
    const patterns = [
      /sources?\s*:\s*\[\s*\{[^}]*(?:file|src)\s*:\s*['"]([^'"]+)['"]/i,
      /"file"\s*:\s*"([^"]+)"/i,
      /"source"\s*:\s*"([^"]+)"/i,
      /"video"\s*:\s*"([^"]+)"/i,
      /(https?:\/\/[^\s"'>]+\.(?:mp4|m3u8)[^\s"'>]*)/i,
      /video\s*src\s*=\s*["']([^"']+)["']/i,
      /source\s+src\s*=\s*["']([^"']+)["']/i,
      /data-src\s*=\s*["']([^"']+)["']/i,
    ];

    for (const pattern of patterns) {
      const match = html.match(pattern);
      if (match && match[1] && isLikelyVideoUrl(match[1])) {
        debugLog("Puppeteer", "Found URL", match[1]);
        return match[1];
      }
    }

    // Try video element
    const videoSrc = $("video").attr("src");
    if (videoSrc && isLikelyVideoUrl(videoSrc)) {
      return videoSrc;
    }

    // Try data attributes
    const dataElements = $("[data-src], [data-source], [data-video], [data-file]");
    for (let i = 0; i < dataElements.length; i++) {
      const dataSrc =
        $(dataElements[i]).attr("data-src") || $(dataElements[i]).attr("data-source");
      if (dataSrc && isLikelyVideoUrl(dataSrc)) {
        return dataSrc;
      }
    }

    return null;
  } catch (err) {
    debugLog("Puppeteer", "Error", err.message);
    return null;
  }
}

async function resolveVidhideUrl(url, referer) {
  debugLog("Vidhide", "Resolving URL", url);
  try {
    const { html } = await fetchHtmlWithHeaders(url, referer);
    debugLog("Vidhide", "Fetched HTML length", html.length);

    const extracted = findFirstUrl(html, [
      /sources?\s*:\s*\[\s*\{[^}]*(?:file|src)\s*:\s*["'](https?:\/\/[^"']+)["']/i,
      /"file"\s*:\s*"([^"]+)"/i,
      /"source"\s*:\s*"([^"]+)"/i,
      /file\s*:\s*'([^']+)'/i,
      /setup\([^)]*file[^)]*\)/i,
    ]);

    if (extracted) {
      debugLog("Vidhide", "Found URL", extracted);
      return extracted;
    }

    debugLog("Vidhide", "No URL found");
    return null;
  } catch (err) {
    debugLog("Vidhide", "Error", err.message);
    return null;
  }
}

async function resolveMp4UploadUrl(url, referer) {
  debugLog("Mp4Upload", "Resolving URL", url);
  try {
    let embedUrl = url;
    const parsed = new URL(url);
    const pathname = parsed.pathname.toLowerCase();
    if (!pathname.includes("embed") && !pathname.endsWith(".html")) {
      const slug = pathname.split("/").filter(Boolean).pop();
      if (slug) embedUrl = `https://www.mp4upload.com/embed-${slug}.html`;
    }

    const { html } = await fetchHtmlWithHeaders(embedUrl, referer);
    debugLog("Mp4Upload", "Fetched HTML length", html.length);

    // El stream va inline en el HTML: player.src({ type: "video/mp4", src: "..." })
    const extracted = findFirstUrl(html, [
      /player\.src\s*\(\s*\{[\s\S]*?src\s*:\s*["']([^"']+)["']/i,
      /player\.src\s*\(\s*["']([^"']+)["']/i,
      /data-file\s*=\s*["']([^"']+)["']/i,
      /data-video\s*=\s*["']([^"']+)["']/i,
      /video(?:\d+)?\s*=\s*["']([^"']+\.mp4[^"']+)["']/i,
    ]);

    if (extracted) {
      debugLog("Mp4Upload", "Found URL", extracted);
      return extracted;
    }

    debugLog("Mp4Upload", "No URL found");
    return null;
  } catch (err) {
    debugLog("Mp4Upload", "Error", err.message);
    return null;
  }
}

async function resolveHqqUrl(url, referer) {
  debugLog("Hqq/Netu", "Resolving URL", url);
  try {
    const { html } = await fetchHtmlWithHeaders(url, referer);
    debugLog("Hqq/Netu", "Fetched HTML length", html.length);

    const extracted = findFirstUrl(html, [
      /sources?\s*:\s*\[\s*\{[^}]*file\s*:\s*["'](https?:\/\/[^"']+)["']/i,
      /file\s*:\s*"([^"]+\.mp4[^"]*)"/i,
      /video(?:\d+)?\s*=\s*["']([^"']+\.mp4[^"']+)["']/i,
    ]);

    if (extracted) {
      debugLog("Hqq/Netu", "Found URL", extracted);
      return extracted;
    }

    debugLog("Hqq/Netu", "No URL found");    return null;
  } catch (err) {
    debugLog("Hqq/Netu", "Error", err.message);
    return null;
  }
}

async function resolveMixdropUrl(url, referer) {
  debugLog("Mixdrop", "Resolving URL", url);
  try {
    const { html } = await fetchHtmlWithHeaders(url, referer);
    debugLog("Mixdrop", "Fetched HTML length", html.length);

    const patterns = [
      /MDCore\.wurl\s*=\s*"([^"]+)"/i,
      /MDCore\.vsrc\s*=\s*"([^"]+)"/i,
      /MDCore\.source\s*=\s*"([^"]+)"/i,
      /\|MDCore\|[^|]*\|wurl\|[^|]*\|(https?[^|]+)/i,
      /(https?:\/\/[a-z0-9]+\.mixdrop\.[a-z]+\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i,
      /(https?:\/\/[a-z0-9]+\.mxdcontent\.[a-z]+\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i,
      /"(https?:\/\/[^"]+\.mp4[^"]*)"/i,
      /\/\/(s-delivery\d+[^\s"']+\.mp4[^\s"']*)/i,
    ];

    for (const pattern of patterns) {
      const match = html.match(pattern);
      if (match && match[1]) {
        let candidate = match[1];
        if (candidate.startsWith("//")) {
          candidate = `https:${candidate}`;
        }
        if (isLikelyVideoUrl(candidate)) {
          debugLog("Mixdrop", "Found URL", candidate);
          return normalizeExtractedUrl(candidate);
        }
      }
    }

    debugLog("Mixdrop", "No URL found in HTML");
    return null;
  } catch (err) {
    debugLog("Mixdrop", "Error", err.message);
    return null;
  }
}

async function resolveDoodstreamUrl(url, referer) {
  debugLog("Doodstream", "Resolving URL", url);
  try {
    let embedUrl = url.replace(/\/d\//, "/e/");
    const { html } = await fetchHtmlWithHeaders(embedUrl, referer);
    debugLog("Doodstream", "Fetched HTML length", html.length);

    const passMatch = html.match(/\/pass_md5\/[^'"\s]+/i);
    if (passMatch) {
      const passUrl = `https://${new URL(embedUrl).hostname}${passMatch[0]}`;
      debugLog("Doodstream", "Fetching pass_md5 token", passUrl);

      const tokenResponse = await fetchHtmlWithHeaders(passUrl, embedUrl);
      const tokenUrl = tokenResponse.html.trim();

      if (tokenUrl && (tokenUrl.startsWith("http") || tokenUrl.startsWith("//"))) {
        const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
        let randomStr = "";
        for (let i = 0; i < 10; i++) {
          randomStr += chars.charAt(Math.floor(Math.random() * chars.length));
        }
        const expiry = Date.now();
        const directUrl = `${tokenUrl}${randomStr}?token=${passMatch[0].split("/").pop()}&expiry=${expiry}`;
        debugLog("Doodstream", "Constructed direct URL", directUrl);
        return directUrl;
      }
    }

    const extracted = findFirstUrl(html, [
      /(https?:\/\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i,
      /(https?:\/\/[^\s"'<>]+\.m3u8[^\s"'<>]*)/i,
      /"file"\s*:\s*"([^"]+)"/i,
      /source\s*src\s*=\s*["']([^"']+)["']/i,
    ]);

    if (extracted) {
      debugLog("Doodstream", "Found fallback URL", extracted);
      return extracted;
    }

    debugLog("Doodstream", "No URL found");
    return null;
  } catch (err) {
    debugLog("Doodstream", "Error", err.message);
    return null;
  }
}

async function resolveDroploadUrl(url, referer) {
  debugLog("Dropload", "Resolving URL", url);
  try {
    const { html } = await fetchHtmlWithHeaders(url, referer);
    debugLog("Dropload", "Fetched HTML length", html.length);

    const extracted = findFirstUrl(html, [
      /(https?:\/\/[^\s"'<>]+\.m3u8[^\s"'<>]*)/i,
      /sources?\s*:\s*\[\s*\{[^}]*(?:file|src)\s*:\s*["'](https?:\/\/[^"']+)["']/i,
      /file\s*:\s*["'](https?:[^\s"']+)["']/i,
      /"file"\s*:\s*"([^"]+)"/i,
      /"source"\s*:\s*"([^"]+)"/i,
      /player\.config\s*=\s*\{[^}]*file\s*:\s*["']([^"']+)["']/i,
      /player\.setup\(\{[^}]*file\s*:\s*["']([^"']+)["']/i,
      /(https?:\/\/[^\s"'<>]+\.mp4[^\s"'<>]*)/i,
    ]);

    if (extracted && !extracted.startsWith("blob:")) {
      debugLog("Dropload", "Found URL", extracted);
      return extracted;
    }

    const dataMatch = html.match(/data-src=["']([^"']+\.(?:m3u8|mp4)[^"']*)["']/i);
    if (dataMatch && dataMatch[1] && !dataMatch[1].startsWith("blob:")) {
      debugLog("Dropload", "Found data-src URL", dataMatch[1]);
      return normalizeExtractedUrl(dataMatch[1]);
    }

    debugLog("Dropload", "No URL found in HTML");
    return null;
  } catch (err) {
    debugLog("Dropload", "Error", err.message);
    return null;
  }
}

async function resolveJKPlayerUrl(url, referer) {
  try {
    const { html } = await fetchHtmlWithHeaders(url, referer || "https://jkanime.net/");
    const match = html.match(/video:\s*\{\s*url:\s*['"]([^'"]+)['"]/i);
    if (match && match[1]) {
      debugLog("resolveEmbed", "JKPlayer direct mp4", match[1]);
      return match[1];
    }
    return null;
  } catch (err) {
    debugLog("JKPlayer", "Error", err.message);
    return null;
  }
}

async function resolveEmbedUrl(url, parentUrl = null) {
  if (!url) {
    return null;
  }

  let parsed;
  try {
    parsed = new URL(url);
  } catch (_error) {
    return url;
  }

  const host = parsed.hostname.replace(/^www\./, "").toLowerCase();
  const pathname = parsed.pathname.toLowerCase();
  const referer = getRefererForUrl(parentUrl || url);

  debugLog("resolveEmbed", `Host: ${host}, Path: ${pathname}`, url);

  // 1. High Performance Modular Resolvers
  if (/wish|awish|playnix|medix|niramirus|kravaxxa|davioad|haxlopp|tryzendm|dumbalag|dhcplay|hglink/i.test(host)) {
    const resolved = await extractStreamwish(url);
    if (resolved) return resolved;
  }

  if (/streamtape/i.test(host)) {
    const resolved = await extractStreamtape(url);
    if (resolved) return resolved;
  }

  if (/voe/i.test(host) || /\/e\//.test(pathname)) {
    const resolved = await extractVoe(url);
    if (resolved) return resolved;
  }

  // Zilla Networks: el m3u8 se sirve directamente en /m3u8/<id> (CORS *)
  if (/zilla-networks/i.test(host) && /^\/play\//.test(pathname)) {
    const zillaId = pathname.split("/").filter(Boolean).pop();
    if (zillaId) {
      const hlsUrl = `https://player.zilla-networks.com/m3u8/${zillaId}`;
      debugLog("resolveEmbed", "Zilla direct m3u8", hlsUrl);
      return hlsUrl;
    }
  }

  // JKPlayer
  if (/jkplayer/i.test(url) || /jkanime\.net\/jkplayer/i.test(url)) {
    const resolved = await resolveJKPlayerUrl(url, referer);
    if (resolved) return resolved;
  }

  // 2. Specific Inline Resolvers
  if (/hqq\.tv|netu|waaw/i.test(host)) {
    const resolved = await resolveHqqUrl(url, referer);
    if (resolved) return resolved;
  }

  if (/vidhide/i.test(host)) {
    const resolved = await resolveVidhideUrl(url, referer);
    if (resolved) return resolved;
  }

  if (/mixdrop|mxdcontent/i.test(host)) {
    const resolved = await resolveMixdropUrl(url, referer);
    if (resolved) return resolved;
  }

  if (/dood|dstream|ds2play|doods/i.test(host)) {
    const resolved = await resolveDoodstreamUrl(url, referer);
    if (resolved) return resolved;
  }

  if (/dropload|dr0pstream|drop\.download/i.test(host)) {
    const resolved = await resolveDroploadUrl(url, referer);
    if (resolved) return resolved;
  }

  if (/mp4upload/i.test(host)) {
    debugLog("resolveEmbed", "Using MP4Upload resolver", null);
    if (!pathname.includes("embed") && !pathname.endsWith(".html")) {
      const slug = pathname.split("/").filter(Boolean).pop();
      if (slug) {
        url = `https://www.mp4upload.com/embed-${slug}.html`;
        debugLog("resolveEmbed", `Converted MP4Upload download URL to embed: ${url}`, null);
      }
    }
    const resolved = await resolveMp4UploadUrl(url, referer);
    if (resolved) return resolved;
    const puppeteerResolved = await resolveEmbedWithPuppeteer(url, referer);
    if (puppeteerResolved) return puppeteerResolved;
    throw new Error("No se pudo resolver enlace directo en MP4Upload");
  }

  // 2.5. yt-dlp High-Speed Resolver
  if (ytdlpResolver.isAvailable) {
    const ytdlpResolved = await ytdlpResolver.extractWithYtdlp(url, referer);
    if (ytdlpResolved) return ytdlpResolved;
  }

  // 3. Puppeteer Protected Fallback
  const isProtectedSite = /wish|playnix|medix|niramirus|kravaxxa|davioad|haxlopp|tryzendm|dumbalag|dhcplay|hglink|vidhide|voe|mixdrop|dood|dropload/i.test(host);
  if (isProtectedSite) {
    debugLog("resolveEmbed", "Using Puppeteer for protected site", null);
    const resolved = await resolveEmbedWithPuppeteer(url, referer);
    if (resolved) return resolved;
  }

  // Fallback: try Puppeteer for any embed
  debugLog("resolveEmbed", "Trying Puppeteer fallback", null);
  const puppeteerResolved = await resolveEmbedWithPuppeteer(url, referer);
  if (puppeteerResolved) {
    return puppeteerResolved;
  }

  return url;
}

module.exports = {
  resolveStreamwishUrl: extractStreamwish,
  resolveStreamtapeUrl: extractStreamtape,
  resolveVoeUrl: extractVoe,
  resolveVidhideUrl,
  resolveHqqUrl,
  resolveMp4UploadUrl,
  resolveMixdropUrl,
  resolveDoodstreamUrl,
  resolveDroploadUrl,
  resolveEmbedWithPuppeteer,
  resolveEmbedUrl,
};
