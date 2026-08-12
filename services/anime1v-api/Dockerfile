# Official Puppeteer image — ships Chromium + all its OS-level deps
# preinstalled, which `npm install puppeteer` alone doesn't give you (same
# reasoning as this repo's main Dockerfile installing Playwright's Chromium
# separately from the pip package).
FROM ghcr.io/puppeteer/puppeteer:24.15.0

WORKDIR /app

# The base image already runs as a non-root `pptruser` with npm's global
# cache pre-permissioned for it — keep that user rather than switching to
# root, or npm install can fail on cache permissions.
COPY --chown=pptruser:pptruser package*.json ./
RUN npm install --omit=dev

COPY --chown=pptruser:pptruser . .

ENV PORT=3000
EXPOSE 3000

CMD ["node", "src/server.js"]
