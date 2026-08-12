const prompts = require("prompts");
const cliProgress = require("cli-progress");
const path = require("node:path");
const animeService = require("./src/services/anime.service");
const downloadService = require("./src/services/download.service");

const PROVIDERS = [
  { title: " AnimeAV1", value: "animeav1.com" },
  { title: " AnimeFLV", value: "animeflv.net" },
  { title: " JKAnime", value: "jkanime.net" },
  { title: " TioAnime", value: "tioanime.com" },
  { title: "🍑 HentaiLA", value: "hentaila.com" },
  { title: " MonosChinos", value: "monoschinos2.com" },
];

const PROVIDER_DOMAINS = {
  "animeav1.com": "AnimeAV1",
  "animeflv.net": "AnimeFLV",
  "www4.animeflv.net": "AnimeFLV",
  "jkanime.net": "JKAnime",
  "hentaila.com": "HentaiLA",
  "tioanime.com": "TioAnime",
  "monoschinos2.com": "MonosChinos",
};

function detectProvider(input) {
  const lower = input.toLowerCase();
  for (const [domain, label] of Object.entries(PROVIDER_DOMAINS)) {
    if (lower.includes(domain)) return label;
  }
  return null;
}

async function main() {
  const _0xfxx = "\x1b[36m\x5B\x43\x72\x65\x61\x64\x6F\x20\x79\x20\x4D\x61\x6E\x74\x65\x6E\x69\x64\x6F\x20\x70\x6F\x72\x20\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x20\x2D\x20\x68\x74\x74\x70\x73\x3A\x2F\x2F\x67\x69\x74\x68\x75\x62\x2E\x63\x6F\x6D\x2F\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x2F\x5D\x1b[0m";
  console.log("\n╔══════════════════════════════════╗");
  console.log("║     Descargador Anime1v v2      ║");
  console.log("╚══════════════════════════════════╝");
  console.log(_0xfxx);

  // 1. Provider selection or direct URL
  const { mode } = await prompts({
    type: "select",
    name: "mode",
    message: "¿Cómo quieres buscar?",
    choices: [
      { title: "🔍 Buscar anime por nombre", value: "search" },
      { title: "🔗 Pegar link directo de episodio", value: "direct" },
      { title: "📋 Link del anime (lista de episodios)", value: "anime" },
    ],
  });

  if (!mode) return console.log("Cancelado.");

  let animeUrl = "";
  let providerDomain = "animeav1.com";

  if (mode === "direct") {
    const { url } = await prompts({
      type: "text",
      name: "url",
      message: "Pega el link del episodio (ej. https://hentaila.com/ver/serie-1):",
      validate: (v) => (v && v.length > 10) ? true : "URL muy corta",
    });
    if (!url) return console.log("Cancelado.");

    // Descarga directa de un episodio
    const result = downloadService.createDownload(
      { url: url.trim(), quality: "1080p", variant: "SUB" },
      "http://localhost"
    );
    console.log(`\nDescarga iniciada: ${result.id.slice(0, 8)}`);
    console.log(`Status: ${result.statusUrl}\n`);

    const bar = new cliProgress.SingleBar({
      format: "{bar} {percentage}% | {status}",
      clearOnComplete: true,
    }, cliProgress.Presets.shades_classic);
    bar.start(100, 0, { status: "preparando..." });

    const interval = setInterval(() => {
      try {
        const s = downloadService.getDownload(result.id);
        bar.update(s.progress, { status: s.status });
        if (s.status === "completed" || s.status === "failed") {
          bar.stop();
          clearInterval(interval);
          if (s.fileName) console.log(`Archivo: ${downloadService.getDownloadsDir()}\\${s.fileName}`);
        }
      } catch (_) { clearInterval(interval); }
    }, 1000);
    return;
  }

  // Search or anime URL flow
  let query;

  if (mode === "anime") {
    const { url } = await prompts({
      type: "text",
      name: "url",
      message: "Pega el link del anime (ej. https://animeav1.com/media/dragon-ball):",
    });
    if (!url) return console.log("Cancelado.");
    query = url.trim();
  } else {
    // Select provider first
    const { prov } = await prompts({
      type: "select",
      name: "prov",
      message: "Selecciona el proveedor:",
      choices: PROVIDERS,
      initial: 0,
    });
    if (!prov) return console.log("Cancelado.");
    providerDomain = prov;

    const { q } = await prompts({
      type: "text",
      name: "q",
      message: "Nombre del anime:",
      validate: (v) => v && v.length >= 2 ? true : "Mínimo 2 caracteres",
    });
    if (!q) return console.log("Cancelado.");
    query = q;
  }

  // Detect if query is a URL or search term
  const isLink = query.startsWith("http://") || query.startsWith("https://") || 
                 Object.keys(PROVIDER_DOMAINS).some((d) => query.includes(d));

  console.log();

  if (isLink) {
    animeUrl = query.trim();
    if (!animeUrl.startsWith("http")) animeUrl = `https://${animeUrl}`;
    console.log(`Usando enlace directo: ${animeUrl}`);
  } else {
    // Search
    console.log(`Buscando "${query}" en ${PROVIDERS.find((p) => p.value === providerDomain)?.title || providerDomain}...`);
    const searchResult = await animeService.searchAnime(query, providerDomain);

    if (!searchResult.data.results || searchResult.data.results.length === 0) {
      console.log("No se encontraron resultados. Intenta en otro proveedor.");
      return;
    }

    const choices = searchResult.data.results
      .slice(0, 15)
      .map((anime) => ({
        title: `${anime.title} (${anime.type || "Anime"}) [${searchResult.source}]`,
        value: anime.url,
      }));

    const answer = await prompts({
      type: "select",
      name: "animeUrl",
      message: `Selecciona un anime (${choices.length} resultados):`,
      choices,
      warn: "No encontrado",
    });

    if (!answer.animeUrl) return;
    animeUrl = answer.animeUrl;
  }

  // 2. Get anime info and episodes
  console.log("\nObteniendo información del anime...");
  const info = await animeService.getAnimeInfo(animeUrl);

  if (!info.data.episodes || info.data.episodes.length === 0) {
    console.log("No se encontraron episodios para este anime.");
    return;
  }

  const episodes = info.data.episodes.sort((a, b) => a.number - b.number);
  const source = info.source || "desconocido";
  console.log(`\n${info.data.title || "Anime"} [${source}]`);
  console.log(`Episodios: ${episodes.length} | ${episodes[0]?.number} - ${episodes[episodes.length - 1]?.number}`);

  if (info.data.description) {
    console.log(`Sinopsis: ${info.data.description.slice(0, 150)}${info.data.description.length > 150 ? "..." : ""}`);
  }

  // 3. Select episodes
  const { targetEpisodes } = await prompts({
    type: "text",
    name: "targetEpisodes",
    message: "Episodios a descargar (ej: 1,3,5-8, todos):",
    initial: "todos",
  });
  if (!targetEpisodes) return;

  let episodesToDownload = [];
  const inputCmd = targetEpisodes.trim().toLowerCase();

  if (inputCmd === "todos" || inputCmd === "all") {
    episodesToDownload = episodes;
  } else {
    const nums = new Set();
    const parts = inputCmd.replace(/[{}[\]]/g, "").split(",");

    for (const part of parts) {
      const rangeMatch = part.match(/(\d+)[-:](\d+)/);
      if (rangeMatch) {
        const start = parseInt(rangeMatch[1], 10);
        const end = parseInt(rangeMatch[2], 10);
        const min = Math.min(start, end);
        const max = Math.max(start, end);
        for (let i = min; i <= max; i++) nums.add(i);
      } else {
        const num = Number(part.trim());
        if (!isNaN(num)) nums.add(num);
      }
    }

    episodesToDownload = episodes.filter((ep) => nums.has(ep.number));
  }

  if (episodesToDownload.length === 0) {
    console.log("Ningún episodio seleccionado.");
    return;
  }

  // 4. Select variant and quality
  const { variant } = await prompts({
    type: "select",
    name: "variant",
    message: "Idioma:",
    choices: [
      { title: "SUB (Japonés + subtítulos)", value: "SUB" },
      { title: "DUB (Latino/Español)", value: "DUB" },
    ],
  });
  if (!variant) return;

  // 5. Confirm
  const epNums = episodesToDownload.map((e) => e.number);
  const range = epNums.length > 1
    ? `Episodios ${epNums[0]}-${epNums[epNums.length - 1]} (${epNums.length} eps)`
    : `Episodio ${epNums[0]}`;

  console.log(`\n📋 Resumen:`);
  console.log(`   Proveedor: ${source}`);
  console.log(`   ${range}`);
  console.log(`   Idioma: ${variant}`);
  console.log(`   Carpeta: ${downloadService.getDownloadsDir()}`);
  console.log();

  const { confirm } = await prompts({
    type: "confirm",
    name: "confirm",
    message: "¿Iniciar descargas?",
    initial: true,
  });
  if (!confirm) return console.log("Cancelado.");

  // 6. Start downloads
  console.log(`\nIniciando ${episodesToDownload.length} descargas...\n`);

  const multibar = new cliProgress.MultiBar({
    clearOnComplete: false,
    hideCursor: true,
    format: "{episode} | {bar} | {percentage}% | {status}",
  }, cliProgress.Presets.shades_classic);

  const activeDownloads = new Map();

  for (const ep of episodesToDownload) {
    try {
      const result = downloadService.createDownload(
        { url: ep.url, quality: "1080p", variant },
        "http://localhost"
      );

      const bar = multibar.create(100, 0, {
        episode: `Ep ${ep.number.toString().padStart(4, "0")}`,
        status: "Iniciando...",
      });

      activeDownloads.set(result.downloadId, { bar, completed: false, number: ep.number });
    } catch (err) {
      console.error(`Error ep ${ep.number}: ${err.message}`);
    }
  }

  if (activeDownloads.size === 0) {
    console.log("No se pudo iniciar ninguna descarga.");
    return;
  }

  // Progress monitor
  let completedCount = 0;
  let failedCount = 0;
  const dlDir = downloadService.getDownloadsDir();

  const interval = setInterval(() => {
    let allDone = true;

    for (const [downloadId, dlObj] of activeDownloads.entries()) {
      if (dlObj.completed) continue;

      try {
        const stats = downloadService.getDownload(downloadId);
        dlObj.bar.update(stats.progress || 0, { status: stats.status });

        if (stats.status === "completed" || stats.status === "failed") {
          dlObj.completed = true;
          if (stats.status === "completed") {
            completedCount++;
            dlObj.bar.update(100, { status: "Completado" });
          } else {
            failedCount++;
            dlObj.bar.update(100, { status: `Fallo: ${(stats.error || "").slice(0, 30)}` });
          }
        } else {
          allDone = false;
        }
      } catch (_err) {
        dlObj.completed = true;
        failedCount++;
      }
    }

    if (allDone) {
      clearInterval(interval);
      multibar.stop();
      console.log(`\nTerminado: ${completedCount} completadas, ${failedCount} fallidas`);
      if (completedCount > 0) console.log(`Archivos en: ${dlDir}`);
    }
  }, 1000);
}

main().catch((err) => {
  console.error("\nError inesperado:", err.message);
  process.exit(1);
});
