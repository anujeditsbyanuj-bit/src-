const a = require('axios');
const ch = require('cheerio');
a.get('https://www.pelisplushd.la/serie/malcolm-in-the-middle-la-vida-sigue-siendo-injusta').then(r => {
  const $ = ch.load(r.data);
  const links = [];
  $('a[href*="/serie/"]').each((i, el) => {
    const href = $(el).attr('href');
    if (!href.includes('temporada')) links.push(href);
  });
  console.log("Found series links:", [...new Set(links)]);
});
