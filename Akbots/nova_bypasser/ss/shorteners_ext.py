import asyncio
import logging
import re
from typing import Optional
from urllib.parse import unquote
from base64 import b64decode

from bs4 import BeautifulSoup
from requests import Session

logger = logging.getLogger(__name__)


async def _http_get(url: str, headers: dict = None, red: bool = True) -> Optional[str]:
    from httpx import AsyncClient, Timeout
    try:
        async with AsyncClient() as ses:
            resp = await ses.get(url, headers=headers, follow_redirects=red, timeout=Timeout(10.0))
            return resp.text
    except Exception as e:
        logger.debug(f"_http_get failed for {url}: {e}")
        return None


async def decrypt_url(code: str) -> str:
    a, b = "", ""
    for i, item in enumerate(code):
        if i % 2 == 0:
            a += item
        else:
            b = item + b
    key = list(a + b)
    i = 0
    while i < len(key):
        if key[i].isdigit():
            for j in range(i + 1, len(key)):
                if key[j].isdigit():
                    u = int(key[i]) ^ int(key[j])
                    if u < 10:
                        key[i] = str(u)
                    i = j
                    break
        i += 1
    key = "".join(key)
    decrypted = b64decode(key)[16:-16]
    return decrypted.decode("utf-8")


async def shourturl(url: str) -> Optional[str]:
    """Bypass for short.url2go.in and similar shorteners."""
    import cloudscraper
    from urllib.parse import urlparse
    client = cloudscraper.create_scraper(allow_brotli=False)
    url = url[:-1] if url[-1] == "/" else url
    parsed = urlparse(url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    code = url.split("/")[-1]
    final_url = f"{domain}/{code}"
    resp = client.get(final_url, headers={"referer": final_url})
    soup = BeautifulSoup(resp.content, "html.parser")
    inputs = soup.find_all("input")
    data = {inp.get("name"): inp.get("value") for inp in inputs}
    h = {"x-requested-with": "XMLHttpRequest"}
    await asyncio.sleep(8)
    r = client.post(f"{domain}/links/go", data=data, headers=h)
    try:
        return r.json()["url"]
    except Exception:
        return None


async def adfly_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        ysmm = re.findall(r"ysmm\s+=\s+['\"](.*?)['\"]", html)
        if not ysmm:
            return None
        decoded = await decrypt_url(ysmm[0])
        if re.search(r"go\.php\?u=", decoded):
            url = b64decode(re.sub(r"(.*?)u=", "", decoded)).decode()
            return url
        elif "&dest=" in decoded:
            return unquote(re.sub(r"(.*?)dest=", "", decoded))
        return decoded
    except Exception as e:
        logger.debug(f"adfly_bypass failed: {e}")
        return None


async def bitly_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        from httpx import AsyncClient, Timeout
        async with AsyncClient() as ses:
            resp = await ses.get(url, follow_redirects=True, timeout=Timeout(10.0))
            return str(resp.url)
    except Exception as e:
        logger.debug(f"bitly_bypass failed: {e}")
        return None


async def gtlinks_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        if "theforyou.in" not in url:
            from httpx import AsyncClient, Timeout
            async with AsyncClient() as ses:
                resp = await ses.get(url, follow_redirects=True, timeout=Timeout(10.0))
                url = str(resp.url)
        token = url.split("=")[-1]
        domain = "https://go.theforyou.in/"
        client = Session()
        response = client.get(domain + token, headers={"referer": domain + token}, timeout=5)
        soup = BeautifulSoup(response.content, "html.parser")
        go_link = soup.find(id="go-link")
        if not go_link:
            return None
        inputs = go_link.find_all(name="input")
        data = {inp.get("name"): inp.get("value") for inp in inputs}
        await asyncio.sleep(5)
        headers = {"x-requested-with": "XMLHttpRequest"}
        result = client.post(f"{domain}links/go", data=data, headers=headers, timeout=5)
        return result.json().get("url")
    except Exception as e:
        logger.debug(f"gtlinks_bypass failed: {e}")
        return None


async def hypershort_bypass(url: str) -> Optional[str]:
    try:
        client = Session()
        response = client.get(url, timeout=5)
        soup = BeautifulSoup(response.content, "html.parser")
        token_response = client.get(
            "https://blog.miuiflash.com/links/createToken.js", timeout=5
        ).text
        token_regex = re.search(r"itsToken\.value = \S+", token_response)
        if not token_regex:
            return None
        token = token_regex[0].split("=")[1].removesuffix('"').removeprefix(' "')
        re_form = soup.find(id="re-form")
        if not re_form:
            return None
        inputs = re_form.find_all(name="input")
        data = {inp.get("name"): inp.get("value") for inp in inputs}["getData"]
        next_page_link = soup.find("form").get("action")
        resp = client.post(
            next_page_link,
            data={"itsToken": token, "get2Data": data},
            headers={"referer": next_page_link},
            timeout=5,
        )
        soup = BeautifulSoup(resp.content, "html.parser")
        await asyncio.sleep(4)
        iframe = soup.find(name="iframe", id="anonIt")
        if not iframe:
            return None
        tokenize_url = iframe.get("src")
        tokenize_url_resp = client.get(tokenize_url)
        soup = BeautifulSoup(tokenize_url_resp.content, "html.parser")
        await asyncio.sleep(3)
        go_link = soup.find(id="go-link")
        if not go_link:
            return None
        inputs = go_link.find_all(name="input")
        data = {inp.get("name"): inp.get("value") for inp in inputs}
        result = client.post(
            "https://blog.miuiflash.com/blog/links/go",
            data=data,
            cookies=tokenize_url_resp.cookies,
            headers={"x-requested-with": "XMLHttpRequest", "referer": tokenize_url},
            timeout=5,
        )
        return result.json().get("url")
    except Exception as e:
        logger.debug(f"hypershort_bypass failed: {e}")
        return None


async def linkvertise_bypass(url: str) -> Optional[str]:
    try:
        bypass_url = f"https://bypass.pm/bypass2?url={url}"
        html = await _http_get(bypass_url)
        if not html:
            return None
        import json
        data = json.loads(html)
        return data.get("destination")
    except Exception as e:
        logger.debug(f"linkvertise_bypass failed: {e}")
        return None


async def shortest_bypass(url: str) -> Optional[str]:
    try:
        parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(url)
        client = Session()
        resp = client.get(url, headers={"referer": url}, timeout=5)
        session_id = re.findall(
            r"""sessionId(?:\s+)?:(?:\s+)?['|"](.*?)['|"]""", resp.text
        )[0]
        final_url = f"{parsed.scheme}://{parsed.netloc}/shortest-url/end-adsession"
        params = {"adSessionId": session_id, "callback": "_"}
        await asyncio.sleep(5)
        response = client.get(final_url, params=params, headers={"referer": url}, timeout=5)
        urls = re.findall('"(.*?)"', response.text)
        if len(urls) >= 2:
            return urls[1].replace(r"\/", "/")
        return None
    except Exception as e:
        logger.debug(f"shortest_bypass failed: {e}")
        return None


async def shortly_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        from httpx import AsyncClient, Timeout
        async with AsyncClient() as ses:
            resp = await ses.post(
                "https://www.shortly.xyz/getlink.php/",
                data={"id": token},
                headers={"referer": "https://www.shortly.xyz/link"},
                timeout=Timeout(10.0),
            )
            return resp.text
    except Exception as e:
        logger.debug(f"shortly_bypass failed: {e}")
        return None


async def sirigan_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        text = html
        for _ in range(50):
            try:
                text = b64decode(text).decode("utf-8")
            except Exception:
                break
        url_match = re.search(r"url=(.*)", text)
        if url_match:
            return url_match[1]
        return None
    except Exception as e:
        logger.debug(f"sirigan_bypass failed: {e}")
        return None


async def thinfi_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        p = soup.find("p")
        return p.a.get("href") if p and p.a else None
    except Exception as e:
        logger.debug(f"thinfi_bypass failed: {e}")
        return None


async def tinyurl_bypass(url: str) -> Optional[str]:
    try:
        from httpx import AsyncClient, Timeout
        async with AsyncClient() as ses:
            resp = await ses.get(url, follow_redirects=True, timeout=Timeout(10.0))
            return str(resp.url)
    except Exception as e:
        logger.debug(f"tinyurl_bypass failed: {e}")
        return None


async def try2link_bypass(url: str) -> Optional[str]:
    try:
        import cloudscraper
        client = cloudscraper.create_scraper(allow_brotli=False)
        code = url.split("/")[-1]
        domain = "https://try2link.com/"
        response = client.get(domain + code, headers={"referer": domain + code})
        soup = BeautifulSoup(response.content, "html.parser")
        inputs = soup.find(id="go-link").find_all(name="input")
        data = {inp.get("name"): inp.get("value") for inp in inputs}
        timestamp = int(__import__("time", fromlist=["time"]).time() * 1000)
        data["timestamp"] = timestamp
        await asyncio.sleep(7)
        headers = {"x-requested-with": "XMLHttpRequest"}
        result = client.post(f"{domain}links/go", data=data, headers=headers, timeout=5)
        return result.json().get("url")
    except Exception as e:
        logger.debug(f"try2link_bypass failed: {e}")
        return None


async def pkin_bypass(url: str) -> Optional[str]:
    try:
        domain = "https://pkin.me/"
        code = url.split("/")[-1]
        client = Session()
        response = client.get(domain + code, headers={"referer": domain + code}, timeout=5)
        soup = BeautifulSoup(response.content, "html.parser")
        inputs = soup.find(id="go-link").find_all(name="input")
        data = {inp.get("name"): inp.get("value") for inp in inputs}
        await asyncio.sleep(3)
        headers = {"x-requested-with": "XMLHttpRequest"}
        result = client.post(f"{domain}links/go", data=data, headers=headers, timeout=5)
        return result.json().get("url")
    except Exception as e:
        logger.debug(f"pkin_bypass failed: {e}")
        return None


# --- Indian shortener generic bypass ---

SHORTNER_TYPE1 = {
    "https://tekcrypt.in/tek/": ["https?://(tekcrypt\\.in/tek/)\\S+", "https://tekcrypt.in/tek/", 20],
    "https://link.short2url.in/": ["https?://(link\\.short2url\\.in/)\\S+", "https://technemo.xyz/blog/", 10],
    "https://go.rocklinks.net/": ["https?://(go\\.rocklinks\\.net/)\\S+", "https://dwnld.povathemes.com/", 10],
    "https://rocklinks.net/": ["https?://(rocklinks\\.net/)\\S+", "https://dwnld.povathemes.com/", 10],
    "https://earn.moneykamalo.com/": ["https?://(earn\\.moneykamalo\\.com/)\\S+", "https://go.moneykamalo.com//", 5],
    "https://m.easysky.in/": ["https?://(m\\.easysky\\.in/)\\S+", "https://techy.veganab.co/", 5],
    "https://indianshortner.in/": ["https?://(indianshortner\\.in/)\\S+", "https://indianshortner.com/", 5],
    "https://open.crazyblog.in/": ["https?://(open\\.crazyblog\\.in/)\\S+", "https://hr.vikashmewada.com/", 7],
    "https://link.tnvalue.in/": ["https?://(link\\.tnvalue\\.in/)\\S+", "https://internet.webhostingtips.club/", 5],
    "https://shortingly.me/": ["https?://(shortingly\\.me/)\\S+", "https://go.techyjeeshan.xyz/", 5],
    "https://dulink.in/": ["https?://(dulink\\.in/)\\S+", "https://tekcrypt.in/tek/", 20],
    "https://bindaaslinks.com/": ["https?://(bindaaslinks\\.com/)\\S+", "https://www.techishant.in/blog/", 5],
    "https://pdiskshortener.com/": ["https?://(pdiskshortener\\.com/)\\S+", "https://pdiskshortener.com/", 10],
    "https://mdiskshortner.link/": ["https?://(mdiskshortner\\.link/)\\S+", "https://mdiskshortner.link/", 15],
    "http://go.earnl.xyz/": ["https?://(go\\.earnl\\.xyz/)\\S+", "https://v.earnl.xyz/", 5],
    "https://g.rewayatcafe.com/": ["https?://(g\\.rewayatcafe\\.com/)\\S+", "https://course.rewayatcafe.com/", 7],
    "https://ser2.crazyblog.in/": ["https?://(ser2\\.crazyblog\\.in/)\\S+", "https://ser3.crazyblog.in/", 12],
    "http://rocklink.in/": ["https?://(rocklink\\.in/)\\S+", "https://rocklink.in/", 6],
    "https://bitshorten.com/": ["https?://(bitshorten\\.com/)\\S+", "https://bitshorten.com/", 10],
}

SHORTNER_TYPE2 = {
    "https://droplink.co/": ["https?://(droplink\\.co/)\\S+", "https://droplink.co/", "https://yoshare.net", 4],
    "https://tnlink.in/": ["https?://(tnlink\\.in/)\\S+", "https://gadgets.usanewstoday.club/", "https://usanewstoday.club/", 9],
    "https://ez4short.com/nzcU": ["https?://(ez4short\\.com/)\\S+", "https://ez4short.com/", "https://techmody.io/", 5],
    "https://xpshort.com/": ["https?://(xpshort\\.com/)\\S+", "https://push.bdnewsx.com/", "https://veganho.co/", 10],
    "http://vearnl.in/": ["https?://(vearnl\\.in/)\\S+", "https://go.urlearn.xyz/", "https://v.modmakers.xyz/", 5],
    "https://adrinolinks.in/": ["https?://(adrinolinks\\.in/)\\S+", "https://adrinolinks.in/", "https://wikitraveltips.com/", 5],
    "https://techymozo.com/": ["https?://(techymozo\\.com/)\\S+", "https://push.bdnewsx.com/", "https://veganho.co/", 8],
    "https://linkbnao.com/": ["https?://(linkbnao\\.com/)\\S+", "https://go.linkbnao.com/", "https://doibihar.org/", 2],
    "https://linksxyz.in/": ["https?://(linksxyz\\.in/)\\S+", "https://blogshangrila.com/insurance/", "https://cypherroot.com/", 13],
    "https://short-jambo.com/": ["https?://(short\\-jambo\\.com/)\\S+", "https://short-jambo.com/", "https://aghtas.com/", 10],
    "https://ads.droplink.co.in/": ["https?://(ads\\.droplink\\.co\\.in/)\\S+", "https://go.droplink.co.in/", "https://go.droplink.co.in/", 5],
    "https://linkpays.in/": ["https?://(linkpays\\.in/)\\S+", "https://m.techpoints.xyz//", "https://www.filmypoints.in/", 10],
    "https://pi-l.ink/": ["https?://(pi\\-l\\.ink/)\\S+", "https://go.pilinks.net/", "https://poketoonworld.com/", 5],
    "https://link.tnlink.in/": ["https?://(link\\.tnlink\\.in/)\\S+", "https://gadgets.usanewstoday.club/", "https://usanewstoday.club/", 8],
    "https://earn4link.in/": ["https?://(earn4link\\.in/)\\S+", "https://m.open2get.in/", "https://ezeviral.com/", 3],
    "https://open2get.in/": ["https?://(open2get\\.in/)\\S+", "https://m.open2get.in/", "https://ezeviral.com/", 3],
}


async def _shortner_bypass(url: str, domain: str, sleep_time: int, referer: str = "") -> Optional[str]:
    url = url[:-1] if url[-1] == "/" else url
    token = url.split("/")[-1]
    client = Session()
    response = client.get(
        domain + token,
        headers={"referer": referer or domain + token},
        timeout=5,
    )
    soup = BeautifulSoup(response.content, "html.parser").find(id="go-link")
    if not soup:
        return None
    inputs = soup.find_all(name="input")
    data = {inp.get("name"): inp.get("value") for inp in inputs}
    await asyncio.sleep(sleep_time)
    headers = {"x-requested-with": "XMLHttpRequest"}
    result = client.post(f"{domain}links/go", data=data, headers=headers, timeout=5)
    return result.json().get("url")


async def shortner_type_one_bypass(url: str) -> Optional[str]:
    for _, value in SHORTNER_TYPE1.items():
        if re.match(value[0], url):
            return await _shortner_bypass(url, value[1], value[2])
    return None


async def shortner_type_two_bypass(url: str) -> Optional[str]:
    for _, value in SHORTNER_TYPE2.items():
        if re.match(value[0], url):
            return await _shortner_bypass(url, value[1], value[3], value[2])
    return None


async def ouo_bypass(url: str) -> Optional[str]:
    from .captcha_solver import OUO_RECAPTCHA_URL, recaptchav3
    from httpx import AsyncClient, Timeout
    try:
        async with AsyncClient() as sess:
            resp = await sess.get(url, timeout=Timeout(15.0))
            html = resp.text
            match = re.search(r'<input type="hidden" name="xurl" value="(.*?)">', html)
            if not match:
                logger.debug("ouo_bypass: no xurl found")
                return None
            xurl = match.group(1)
            token = await recaptchav3(OUO_RECAPTCHA_URL)
            if not token:
                logger.debug("ouo_bypass: recaptcha token failed")
                return None
            post_url = str(resp.url)
            data = {"xurl": xurl, "recaptcha-token": token}
            headers = {"content-type": "application/x-www-form-urlencoded"}
            result = await sess.post(post_url, data=data, headers=headers, timeout=Timeout(15.0), follow_redirects=True)
            return str(result.url)
    except Exception as e:
        logger.debug(f"ouo_bypass failed: {e}")
        return None
