const puppeteer = require("puppeteer");

let browserInstance = null;

// Simple Semaphore / Concurrency Limiter
let activePages = 0;
// Set to 2 as default
const MAX_CONCURRENT_PAGES = Number(process.env.MAX_CONCURRENT_PAGES || 2);
const queue = [];

function acquireSlot() {
  if (activePages < MAX_CONCURRENT_PAGES) {
    activePages++;
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    queue.push(resolve);
  });
}

function releaseSlot() {
  activePages--;
  if (queue.length > 0) {
    activePages++;
    const next = queue.shift();
    next();
  }
}

async function getBrowser() {
  if (browserInstance) {
    try {
      await browserInstance.version();
      return browserInstance;
    } catch (err) {
      console.log("Browser instance dead, relaunching...");
      browserInstance = null;
    }
  }

  browserInstance = await puppeteer.launch({
    headless: true,
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--disable-accelerated-2d-canvas",
      "--disable-gpu",
      "--no-zygote",
      "--single-process",
      "--disable-background-networking",
      "--disable-background-timer-throttling",
      "--disable-backgrounding-occluded-windows",
      "--disable-breakpad",
      "--disable-component-extensions-with-background-pages",
      "--disable-extensions",
      "--disable-features=TranslateUI,BlinkGenPropertyTrees",
      "--disable-ipc-flooding-protection",
      "--disable-renderer-backgrounding",
      "--metrics-recording-only"
    ]
  });
  return browserInstance;
}

async function fetchPageContent(url) {
  await acquireSlot();
  try {
    const browser = await getBrowser();
    const page = await browser.newPage();
    try {
      await page.setRequestInterception(true);
      page.on("request", (req) => {
        const type = req.resourceType();
        if (type === "image" || type === "stylesheet" || type === "font" || type === "media") {
          req.abort();
        } else {
          req.continue();
        }
      });

      await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 25000 });
      const content = await page.content();
      return content;
    } finally {
      try {
        await page.evaluate(() => window.stop()).catch(() => {});
        await page.close({ runBeforeUnload: false });
      } catch (e) {
        console.error("Error closing page in fetchPageContent:", e.message);
      }
    }
  } finally {
    releaseSlot();
  }
}

async function scrapeWithPage(url, scraperFn) {
  await acquireSlot();
  try {
    const browser = await getBrowser();
    const page = await browser.newPage();
    try {
      await page.setRequestInterception(true);
      page.on("request", (req) => {
        const type = req.resourceType();
        if (type === "image" || type === "stylesheet" || type === "font" || type === "media") {
          req.abort();
        } else {
          req.continue();
        }
      });

      await page.setUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36");
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 25000 });
      const result = await page.evaluate(scraperFn);
      return result;
    } finally {
      try {
        await page.evaluate(() => window.stop()).catch(() => {});
        await page.close({ runBeforeUnload: false });
      } catch (e) {
        console.error("Error closing page in scrapeWithPage:", e.message);
      }
    }
  } finally {
    releaseSlot();
  }
}

async function cleanup() {
  if (browserInstance) {
    console.log("Shutting down global browser instance...");
    try {
      const pages = await browserInstance.pages();
      await Promise.all(pages.map(p => p.close({ runBeforeUnload: false }).catch(() => {})));
      await browserInstance.close();
    } catch (err) {
      console.error("Error closing browser instance on exit:", err);
      if (browserInstance.process()) {
        try {
          browserInstance.process().kill("SIGKILL");
        } catch (killErr) {
          // ignore
        }
      }
    }
    browserInstance = null;
  }
}

// Trap system signals for graceful exit
process.on("SIGINT", async () => {
  console.log("SIGINT received.");
  await cleanup();
  process.exit(0);
});

process.on("SIGTERM", async () => {
  console.log("SIGTERM received.");
  await cleanup();
  process.exit(0);
});

process.on("exit", () => {
  if (browserInstance && browserInstance.process()) {
    try {
      browserInstance.process().kill("SIGKILL");
    } catch (e) {}
  }
});

module.exports = {
  getBrowser,
  fetchPageContent,
  scrapeWithPage,
  cleanup
};
