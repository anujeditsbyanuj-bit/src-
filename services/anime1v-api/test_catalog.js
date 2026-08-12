const { getCatalog } = require('./src/services/jkanime.service');
const cheerio = require('cheerio');
const axios = require('axios');

async function test() {
  const html = (await axios.get('https://jkanime.net/directorio/1/')).data;
  const $ = cheerio.load(html);
  const results = [];
  $(".anime__item").each((_, element) => {
    const card = $(element);
    const title = card.find(".anime__item__text h5 a").first().text().trim();
    if (title) results.push(title);
  });
  console.log(results);
}
test();
