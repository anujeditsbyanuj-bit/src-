const a = require('axios');
const ch = require('cheerio');
a.get('https://www.pelisplushd.la/serie/malcolm-in-the-middle-la-vida-sigue-siendo-injusta/temporada/1/capitulo/2').then(r => {
  const $ = ch.load(r.data);
  const servers = [];
  $('#link_url span').each((_, spanEl) => {
    servers.push($(spanEl).attr("url"));
  });
  console.log("Servers for ep 2:", servers.slice(0, 3));
}).catch(console.error);

a.get('https://www.pelisplushd.la/serie/malcolm-in-the-middle-la-vida-sigue-siendo-injusta/temporada/1/capitulo/1').then(r => {
  const $ = ch.load(r.data);
  const servers = [];
  $('#link_url span').each((_, spanEl) => {
    servers.push($(spanEl).attr("url"));
  });
  console.log("Servers for ep 1:", servers.slice(0, 3));
}).catch(console.error);
