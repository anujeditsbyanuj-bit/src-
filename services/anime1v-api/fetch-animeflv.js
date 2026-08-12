const axios = require("axios");
const fs = require("fs");

async function test() {
  const url = "https://www4.animeflv.net/ver/tensei-shitara-slime-datta-ken-1";
  const { data: html } = await axios.get(url, {
    headers: {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
  });

  fs.writeFileSync("test.html", html);
  console.log("Written to test.html");
}

test().catch(console.error);
