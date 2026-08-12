const { axiosGet, cheerio } = require('../resolver-helpers');

function rot13(str) {
  return str.replace(/[A-Za-z]/g, (c) =>
    String.fromCharCode(c.charCodeAt(0) + (c.toLowerCase() < 'n' ? 13 : -13))
  );
}

function sanitizeSpecialChars(str) {
  const patterns = ['@$', '^^', '~@', '%?', '*~', '!!', '#&'];
  patterns.forEach((p) => {
    const regex = new RegExp(p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
    str = str.replace(regex, '_');
  });
  return str;
}

function removeUnderscores(str) {
  return str.split('_').join('');
}

function decodeBase64(str) {
  return Buffer.from(str, 'base64').toString('utf-8');
}

function shiftChars(str, shift) {
  return str
    .split('')
    .map((c) => String.fromCharCode(c.charCodeAt(0) - shift))
    .join('');
}

function reverseString(str) {
  return str.split('').reverse().join('');
}

function decodeObfuscatedData(obfuscated) {
  try {
    let step = rot13(obfuscated);
    step = sanitizeSpecialChars(step);
    step = removeUnderscores(step);
    step = decodeBase64(step);
    step = shiftChars(step, 3);
    step = reverseString(step);
    step = decodeBase64(step);
    return JSON.parse(step);
  } catch (err) {
    console.error('[VOE Resolver] Error decoding data:', err);
    return null;
  }
}

async function extractVoe(pageUrl) {
  console.log(`[VOE RESOLVER] Resolviendo: ${pageUrl}`);
  try {
    let html;
    try {
      const res = await axiosGet(pageUrl, {
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
          'Accept-Encoding': 'gzip, deflate, br'
        },
        timeout: 8000
      });
      html = res.data;
    } catch (e) {
      console.error('[VOE RESOLVER] Error descargando página:', e.message);
      return null;
    }

    // Detectar redirección JS
    if (html.includes('window.location.href')) {
      const match = html.match(/window\.location\.href\s*=\s*['"]([^'"]+)['"]/);
      if (match && match[1]) {
        const redirectUrl = match[1];
        console.log('[VOE RESOLVER] Redirección detectada a:', redirectUrl);
        try {
          const redirectRes = await axiosGet(redirectUrl, {
            headers: {
              'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0',
              'Accept-Encoding': 'gzip, deflate, br'
            },
            timeout: 8000
          });
          html = redirectRes.data;
        } catch (err) {
          console.error('[VOE RESOLVER] Error descargando URL redirigida:', err.message);
          return null;
        }
      }
    }

    const $ = cheerio.load(html);

    // Buscar JSON ofuscado
    const script = $('script[type="application/json"]').get().find(s => {
      try {
        const parsed = JSON.parse($(s).html());
        return Array.isArray(parsed) && typeof parsed[0] === 'string';
      } catch {
        return false;
      }
    });

    if (!script) {
      console.log('[VOE RESOLVER] No se encontró el script JSON ofuscado');
      return null;
    }

    const obfuscated = $(script).html();
    const data = decodeObfuscatedData(obfuscated);

    if (!data) {
      console.log('[VOE RESOLVER] Falló decodificación de datos ofuscados');
      return null;
    }

    // Retorna direct_access_url (MP4 directo) con preferencia, o el source (HLS m3u8 directo)
    const finalUrl = data.direct_access_url || data.source || null;
    if (finalUrl) {
      console.log('[VOE RESOLVER] URL resuelta exitosamente:', finalUrl);
    }
    return finalUrl;
  } catch (err) {
    console.error('[VOE RESOLVER] Error inesperado:', err.message);
    return null;
  }
}

module.exports = { extractVoe };
