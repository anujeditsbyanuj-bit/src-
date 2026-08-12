const prompts = require("prompts");
const cliProgress = require("cli-progress");
const path = require("node:path");
const pelisplusService = require("./src/services/pelisplus.service");
const repelishdService = require("./src/services/repelishd.service");
const cuevanaService = require("./src/services/cuevana.service");
const downloadService = require("./src/services/download.service");

const PROVIDERS = [
  { title: "Busqueda Agregada (Todos)", value: "aggregate" },
  { title: "PelisPlus", value: "pelisplus" },
  { title: "RePelisHD (Solo Peliculas)", value: "repelishd" },
  { title: "Cuevana3", value: "cuevana3" },
];

function detectProvider(url) {
  if (url.includes("repelishd")) return "repelishd";
  if (url.includes("cuevana") || url.includes("cuevana3")) return "cuevana3";
  return "pelisplus";
}

async function searchAggregate(query) {
  const [ppData, rpData] = await Promise.all([
    pelisplusService.searchContent(query).catch(() => []),
    repelishdService.searchContent(query).catch(() => [])
  ]);
  
  const ppMapped = (ppData || []).map(item => ({ ...item, provider: "pelisplus" }));
  const rpMapped = (rpData || []).map(item => ({ ...item, provider: "repelishd" }));
  let data = [...rpMapped, ...ppMapped];

  // Ordenar
  const lowerQuery = query.toLowerCase().trim();
  data.sort((a, b) => {
    const aTitle = a.title.toLowerCase();
    const bTitle = b.title.toLowerCase();
    const aExact = aTitle === lowerQuery;
    const bExact = bTitle === lowerQuery;
    if (aExact && !bExact) return -1;
    if (!aExact && bExact) return 1;
    return 0;
  });

  if (data.length === 0) {
    const cuevanaData = await cuevanaService.searchContent(query).catch(() => []);
    data = (cuevanaData || []).map(item => ({ ...item, provider: "cuevana3" }));
  }

  return data;
}

async function main() {
  const _0xfxx = "\x1b[36m\x5B\x43\x72\x65\x61\x64\x6F\x20\x79\x20\x4D\x61\x6E\x74\x65\x6E\x69\x64\x6F\x20\x70\x6F\x72\x20\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x20\x2D\x20\x68\x74\x74\x70\x73\x3A\x2F\x2F\x67\x69\x74\x68\x75\x62\x2E\x63\x6F\x6D\x2F\x46\x78\x78\x4D\x6F\x72\x67\x61\x6E\x2F\x5D\x1b[0m";
  console.log("\n====================================");
  console.log("       Descargador PeliApi CLI      ");
  console.log("====================================");
  console.log(_0xfxx);

  // 1. Elegir modo
  const { mode } = await prompts({
    type: "select",
    name: "mode",
    message: "Seleccione el metodo de busqueda:",
    choices: [
      { title: "Buscar por nombre", value: "search" },
      { title: "Pegar enlace directo de pelicula o episodio", value: "direct" },
    ],
  });

  if (!mode) return console.log("Cancelado.");

  let selectedUrl = "";
  let provider = "pelisplus";
  let contentTitle = "";
  let contentType = "movie";

  if (mode === "direct") {
    const { url } = await prompts({
      type: "text",
      name: "url",
      message: "Ingrese el enlace directo (PelisPlus, RePelisHD, Cuevana3):",
      validate: (v) => (v && v.length > 10) ? true : "URL muy corta",
    });
    if (!url) return console.log("Cancelado.");

    selectedUrl = url.trim();
    provider = detectProvider(selectedUrl);
    
    // Iniciar descarga directa del enlace
    const result = downloadService.createDownload(
      { url: selectedUrl, quality: "1080p", variant: "Latino" },
      "http://localhost"
    );
    console.log(`\nDescarga iniciada: ${result.id.slice(0, 8)}`);
    
    const bar = new cliProgress.SingleBar({
      format: "{bar} {percentage}% | {status}",
      clearOnComplete: false,
    }, cliProgress.Presets.shades_classic);
    
    bar.start(100, 0, { status: "preparando..." });

    const interval = setInterval(() => {
      try {
        const s = downloadService.getDownload(result.id);
        bar.update(s.progress || 0, { status: s.status || "descargando..." });
        if (s.status === "completed" || s.status === "failed") {
          bar.stop();
          clearInterval(interval);
          if (s.status === "completed") {
            console.log(`\nCompletado. Archivo: ${downloadService.getDownloadsDir()}\\${s.fileName}`);
          } else {
            console.log(`\nError: ${s.error}`);
          }
        }
      } catch (err) {
        clearInterval(interval);
        console.error("Error monitoreando la descarga:", err.message);
      }
    }, 1000);
    return;
  }

  // Flujo de busqueda por nombre
  const { prov } = await prompts({
    type: "select",
    name: "prov",
    message: "Seleccione el proveedor:",
    choices: PROVIDERS,
    initial: 0,
  });
  if (!prov) return console.log("Cancelado.");
  provider = prov;

  const { query } = await prompts({
    type: "text",
    name: "query",
    message: "Nombre de la pelicula o serie:",
    validate: (v) => v && v.length >= 2 ? true : "Ingrese al menos 2 caracteres",
  });
  if (!query) return console.log("Cancelado.");

  console.log("\nBuscando...");
  let searchResults = [];
  if (provider === "aggregate") {
    searchResults = await searchAggregate(query);
  } else {
    let service;
    if (provider === "repelishd") service = repelishdService;
    else if (provider === "cuevana3") service = cuevanaService;
    else service = pelisplusService;

    const data = await service.searchContent(query).catch(() => []);
    searchResults = data.map(item => ({ ...item, provider }));
  }

  if (searchResults.length === 0) {
    console.log("No se encontraron resultados.");
    return;
  }

  const choices = searchResults.slice(0, 20).map((item) => ({
    title: `[${item.provider.toUpperCase()}] ${item.title} (${item.type === "movie" ? "Pelicula" : "Serie"})`,
    value: item,
  }));

  const { selectedItem } = await prompts({
    type: "select",
    name: "selectedItem",
    message: `Resultados encontrados (${choices.length}):`,
    choices,
  });

  if (!selectedItem) return console.log("Cancelado.");

  selectedUrl = selectedItem.url;
  provider = selectedItem.provider;
  contentTitle = selectedItem.title;
  contentType = selectedItem.type;

  console.log(`\nObteniendo detalles de: ${contentTitle}...`);
  
  let service;
  if (provider === "repelishd") service = repelishdService;
  else if (provider === "cuevana3") service = cuevanaService;
  else service = pelisplusService;

  const info = await service.getContentInfo(selectedItem.slug, contentType);

  if (contentType === "movie") {
    // Es pelicula, descargar directamente
    const { variant } = await prompts({
      type: "select",
      name: "variant",
      message: "Seleccione el idioma/variante:",
      choices: [
        { title: "Latino", value: "Latino" },
        { title: "Castellano", value: "Castellano" },
        { title: "Subtitulado", value: "Subtitulado" },
      ],
    });
    if (!variant) return console.log("Cancelado.");

    console.log(`\nIniciando descarga de: ${contentTitle}...`);
    const result = downloadService.createDownload(
      { url: selectedUrl, quality: "1080p", variant },
      "http://localhost"
    );

    const bar = new cliProgress.SingleBar({
      format: "{bar} {percentage}% | {status}",
      clearOnComplete: false,
    }, cliProgress.Presets.shades_classic);
    
    bar.start(100, 0, { status: "preparando..." });

    const interval = setInterval(() => {
      try {
        const s = downloadService.getDownload(result.id);
        bar.update(s.progress || 0, { status: s.status || "descargando..." });
        if (s.status === "completed" || s.status === "failed") {
          bar.stop();
          clearInterval(interval);
          if (s.status === "completed") {
            console.log(`\nCompletado. Archivo: ${downloadService.getDownloadsDir()}\\${s.fileName}`);
          } else {
            console.log(`\nError: ${s.error}`);
          }
        }
      } catch (err) {
        clearInterval(interval);
        console.error("Error monitoreando la descarga:", err.message);
      }
    }, 1000);
  } else {
    // Es serie o anime
    if (!info.seasons || info.seasons.length === 0) {
      console.log("No se encontraron temporadas para esta serie.");
      return;
    }

    const seasonChoices = info.seasons.map((s) => ({
      title: s.name || `Temporada ${s.number}`,
      value: s,
    }));

    const { selectedSeason } = await prompts({
      type: "select",
      name: "selectedSeason",
      message: "Seleccione la temporada:",
      choices: seasonChoices,
    });

    if (!selectedSeason) return console.log("Cancelado.");

    const episodes = selectedSeason.episodes || [];
    if (episodes.length === 0) {
      console.log("No hay episodios disponibles en esta temporada.");
      return;
    }

    console.log(`Episodios disponibles: ${episodes.length} (${episodes[0].number} - ${episodes[episodes.length - 1].number})`);
    
    const { targetEpisodes } = await prompts({
      type: "text",
      name: "targetEpisodes",
      message: "Episodios a descargar (ej: 1,3,5-8, todos):",
      initial: "todos",
    });
    if (!targetEpisodes) return console.log("Cancelado.");

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
      console.log("Ningun episodio seleccionado.");
      return;
    }

    const { variant } = await prompts({
      type: "select",
      name: "variant",
      message: "Seleccione el idioma/variante:",
      choices: [
        { title: "Latino", value: "Latino" },
        { title: "Castellano", value: "Castellano" },
        { title: "Subtitulado", value: "Subtitulado" },
      ],
    });
    if (!variant) return console.log("Cancelado.");

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

    let completedCount = 0;
    let failedCount = 0;
    const dlDir = downloadService.getDownloadsDir();

    const interval = setInterval(() => {
      let allDone = true;

      for (const [downloadId, dlObj] of activeDownloads.entries()) {
        if (dlObj.completed) continue;

        try {
          const stats = downloadService.getDownload(downloadId);
          dlObj.bar.update(stats.progress || 0, { status: stats.status || "descargando..." });

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
}

main().catch((err) => {
  console.error("\nError inesperado:", err.message);
  process.exit(1);
});
