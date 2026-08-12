const axios = require('axios');
const cheerio = require('cheerio');
const { URL } = require('url');
const http = require('http');
const https = require('https');

const UA_FIREFOX =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0';

const axiosInstance = axios.create({
  timeout: 10000,
  headers: { 'User-Agent': UA_FIREFOX },
  httpAgent: new http.Agent({ keepAlive: true }),
  httpsAgent: new https.Agent({ keepAlive: true })
});

async function axiosGet(url, opts = {}) {
    return await axiosInstance.get(url, {
      responseType: 'text',
      ...opts
    });
}

function transformObeywish(url) {
  try {
    const u = new URL(url);
    if (u.hostname.includes('obeywish.com')) {
      u.hostname = 'asnwish.com';
      return u.toString();
    }
    return url;
  } catch {
    return url;
  }
}

module.exports = {
  axiosGet,
  cheerio,
  transformObeywish,
  UA_FIREFOX
};
