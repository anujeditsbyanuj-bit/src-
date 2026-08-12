const { axiosGet, UA_FIREFOX, cheerio } = require('../resolver-helpers');

async function checkSTLink(url) {
  try {
    const res = await axiosGet(url, {
      method: 'HEAD',
      headers: {
        'User-Agent': UA_FIREFOX,
        'Referer': 'https://streamtape.com/'
      },
      timeout: 6000,
      maxRedirects: 0,
      validateStatus: () => true
    });

    const contentType = res.headers['content-type'] || '';
    if (contentType.includes('application/json')) {
      return null;
    }

    if ([301, 302, 303, 307, 308].includes(res.status)) {
      const location = res.headers['location'];
      if (location) {
        return location.startsWith('//') ? 'https:' + location : location;
      }
    }
  } catch (e) {
    console.error('[ST RESOLVER] Error en HEAD validation:', e.message);
    return null;
  }
  return null;
}

async function extractStreamtape(pageUrl) {
  console.log(`[ST RESOLVER] Resolviendo: ${pageUrl}`);
  try {
    let html;
    try {
      const res = await axiosGet(pageUrl, {
        headers: { 'User-Agent': UA_FIREFOX },
        timeout: 8000
      });
      html = res.data;
    } catch (e) {
      console.error('[ST RESOLVER] Error descargando página:', e.message);
      return null;
    }

    const $ = cheerio.load(html);
    const scriptElems = $('script').toArray();

    for (const el of scriptElems) {
      const scriptContent = $(el).html();
      if (!scriptContent) continue;

      const regex = /document\.getElementById\(['"]([^'"]+)['"]\)\.innerHTML\s*=\s*(.+);/g;
      let match;

      while ((match = regex.exec(scriptContent)) !== null) {
        let expr = match[2]
          .replace(/\+ ?''/g, '')
          .replace(/\.substring\((\d+)\)/g, (_, p) => `.slice(${p})`);

        let link;
        try {
          // Evaluar la concatenación/slicing del enlace de Streamtape
          link = eval(expr);
        } catch (err) {
          continue;
        }

        if (!link) continue;

        let fullLink = link;
        if (fullLink.startsWith('//')) {
          fullLink = 'https:' + fullLink;
        } else if (fullLink.startsWith('/')) {
          fullLink = 'https://streamtape.com' + fullLink;
        }

        // Validar y obtener la redirección real
        const validUrl = await checkSTLink(fullLink);
        if (validUrl) {
          console.log('[ST RESOLVER] URL directa resuelta exitosamente:', validUrl);
          return validUrl;
        }
      }
    }

    console.log('[ST RESOLVER] No se encontró ningún enlace válido');
    return null;
  } catch (e) {
    console.error('[ST RESOLVER] Error general:', e.message);
    return null;
  }
}

module.exports = { extractStreamtape };
