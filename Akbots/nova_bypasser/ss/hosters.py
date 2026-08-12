import logging
import re
import json
from typing import Optional
from urllib.parse import urlparse
from math import ceil, floor
from secrets import choice

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


async def anonfiles_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        dl = soup.find(id="download-url")
        return dl["href"] if dl else None
    except Exception as e:
        logger.debug(f"anonfiles_bypass failed: {e}")
        return None


async def antfiles_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        a = soup.find(class_="main-btn", href=True)
        if a:
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}/{a['href']}"
        return None
    except Exception as e:
        logger.debug(f"antfiles_bypass failed: {e}")
        return None


async def fichier_bypass(url: str) -> Optional[str]:
    try:
        client = Session()
        response = client.get(url, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        data = {"adz": soup.find("input").get("value")}
        rate_limit = soup.find("div", {"class": "ct_warn"})
        if rate_limit and "you must wait" in str(rate_limit).lower():
            return None
        r = client.post(url, json=data, timeout=5)
        soup = BeautifulSoup(r.text, "html.parser")
        link = soup.find(class_="ok btn-general btn-orange")
        return link.get("href") if link else None
    except Exception as e:
        logger.debug(f"fichier_bypass failed: {e}")
        return None


async def krakenfiles_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        token = soup.find("input", id="dl-token")
        if not token:
            return None
        token = token["value"]
        hashes = [
            item["data-file-hash"]
            for item in soup.find_all("div", attrs={"data-file-hash": True})
        ]
        if not hashes:
            return None
        dl_hash = hashes[0]
        payload = (
            "------WebKitFormBoundary7MA4YWxkTrZu0gW\r\n"
            'Content-Disposition: form-data; name="token"\r\n\r\n'
            f"{token}\r\n"
            "------WebKitFormBoundary7MA4YWxkTrZu0gW--"
        )
        headers = {
            "content-type": "multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW",
            "cache-control": "no-cache",
            "hash": dl_hash,
        }
        dl_resp = Session().post(
            f"https://krakenfiles.com/download/{dl_hash}",
            data=payload,
            headers=headers,
            timeout=5,
        )
        dl_json = dl_resp.json()
        download_url = dl_json.get("url", "")
        return download_url.replace(" ", "%20") if download_url else None
    except Exception as e:
        logger.debug(f"krakenfiles_bypass failed: {e}")
        return None


async def mdisk_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        api = f"https://diskuploader.entertainvideo.com/v1/file/cdnurl?param={token}"
        html = await _http_get(api)
        if not html:
            return None
        response = json.loads(html)
        dl = response.get("download", "")
        return dl.replace(" ", "%20") if dl else None
    except Exception as e:
        logger.debug(f"mdisk_bypass failed: {e}")
        return None


async def mediafire_bypass(url: str) -> Optional[str]:
    try:
        link = re.search(r"\bhttps?://.*mediafire\.com\S+", url)
        if not link:
            return None
        html = await _http_get(link[0])
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        a = soup.find("a", {"aria-label": "Download file"})
        return a.get("href") if a else None
    except Exception as e:
        logger.debug(f"mediafire_bypass failed: {e}")
        return None


async def pixeldrain_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        file_id = url.split("/")[-1]
        if url.split("/")[-2] == "l":
            return f"https://pixeldrain.com/api/list/{file_id}/zip"
        return f"https://pixeldrain.com/api/file/{file_id}"
    except Exception as e:
        logger.debug(f"pixeldrain_bypass failed: {e}")
        return None


async def racaty_bypass(url: str) -> Optional[str]:
    try:
        client = Session()
        url = client.get(url, timeout=5).url
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        }
        data = {"op": "download2", "id": token, "rand": "", "referer": "", "method_free": "", "method_premium": ""}
        response = client.post(url, headers=headers, data=data, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        btn = soup.find(class_="btn btn-dow")
        if btn:
            return btn["href"]
        unique = soup.find(id="uniqueExpirylink")
        return unique["href"] if unique else None
    except Exception as e:
        logger.debug(f"racaty_bypass failed: {e}")
        return None


async def sendcm_bypass(url: str) -> Optional[str]:
    try:
        base_url = "https://send.cm/"
        client = Session()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.0.0 Safari/537.36",
        }
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        inputs = soup.find_all("input")
        if len(inputs) < 2:
            return None
        file_id = inputs[1]["value"]
        pars = {"op": "download2", "id": file_id, "referer": url}
        resp = client.post(base_url, data=pars, headers=headers, allow_redirects=False, timeout=5)
        return resp.headers.get("Location")
    except Exception as e:
        logger.debug(f"sendcm_bypass failed: {e}")
        return None


async def sfile_bypass(url: str) -> Optional[str]:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 8.0.1; SM-G532G Build/MMB29T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3239.83 Mobile Safari/537.36"
        }
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        html = await _http_get(f"https://sfile.mobi/download/{token}", headers=headers)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        p = soup.find("p")
        return p.a.get("href") if p and p.a else None
    except Exception as e:
        logger.debug(f"sfile_bypass failed: {e}")
        return None


async def solidfiles_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        m = re.search(r"'viewerOptions\',\ (.*?)\);", html)
        if not m:
            return None
        data = json.loads(m[1])
        return data.get("downloadUrl")
    except Exception as e:
        logger.debug(f"solidfiles_bypass failed: {e}")
        return None


async def sourceforge_bypass(url: str) -> Optional[str]:
    try:
        file_path = re.findall(r"files(.*)/download", url)[0]
        project = re.findall(r"projects?/(.*?)/files", url)[0]
        mirrors_url = f"https://sourceforge.net/settings/mirror_choices?projectname={project}&filename={file_path}"
        html = await _http_get(mirrors_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        mirror_list = soup.find("ul", {"id": "mirrorList"})
        if not mirror_list:
            return None
        mirrors = mirror_list.findAll("li")
        for mirror in mirrors[1:]:
            return f'https://{mirror["id"]}.dl.sourceforge.net/project/{project}/{file_path}?viasf=1'
        return None
    except Exception as e:
        logger.debug(f"sourceforge_bypass failed: {e}")
        return None


async def uploadbaz_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        client = Session()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        }
        data = {"op": "download2", "id": token, "rand": "", "referer": "", "method_free": "", "method_premium": ""}
        response = client.post(url, headers=headers, data=data, allow_redirects=False, timeout=5)
        return response.headers.get("Location")
    except Exception as e:
        logger.debug(f"uploadbaz_bypass failed: {e}")
        return None


async def uploadee_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        link = soup.find("a", attrs={"id": "d_l"})
        return link["href"] if link else None
    except Exception as e:
        logger.debug(f"uploadee_bypass failed: {e}")
        return None


async def uppit_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        client = Session()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        }
        data = {"op": "download2", "id": token, "rand": "", "referer": "", "method_free": "", "method_premium": ""}
        response = client.post(url, headers=headers, data=data, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        span = soup.find("span", {"style": "background:#f9f9f9;border:1px dotted #bbb;padding:7px;"})
        return span.a.get("href") if span and span.a else None
    except Exception as e:
        logger.debug(f"uppit_bypass failed: {e}")
        return None


async def userscloud_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        client = Session()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        }
        data = {"op": "download2", "id": token, "rand": "", "referer": "", "method_free": "", "method_premium": ""}
        response = client.post(url, headers=headers, data=data, allow_redirects=False, timeout=5)
        return response.headers.get("Location")
    except Exception as e:
        logger.debug(f"userscloud_bypass failed: {e}")
        return None


async def yandex_bypass(url: str) -> Optional[str]:
    try:
        api = f"https://cloud-api.yandex.net/v1/disk/public/resources/download?public_key={url}"
        html = await _http_get(api)
        if not html:
            return None
        data = json.loads(html)
        return data.get("href")
    except Exception as e:
        logger.debug(f"yandex_bypass failed: {e}")
        return None


async def zippyshare_bypass(url: str) -> Optional[str]:
    try:
        client = Session()
        response = client.get(url, timeout=5)
        dlbutton = re.search(r'href = "([^"]+)" \+ \(([^)]+)\) \+ "([^"]+)', response.text)
        if dlbutton:
            folder, math_chall, filename = dlbutton.groups()
            math_chall = eval(math_chall)
            base = re.search(r"https?://[^/]+", response.url).group(0)
            return f"{base}{folder}{math_chall}{filename}"

        soup = BeautifulSoup(response.content, "html.parser")
        script = soup.find("script", text=re.compile(r"(?si)\s*var a = \d+;"))
        if script:
            sc = str(script)
            var = re.findall(r"var [ab] = (\d+)", sc)
            omg = re.findall(r"\.omg (!?=) [\"']([^\"']+)", sc)
            file = re.findall(r'"(/[^"]+)', sc)
            if var and omg:
                a, b = var
                divider = int(re.findall(r"(\d+)%b", sc)[0])
                a_num = int(a)
                if eval(f"{omg[0][1]!r} {omg[1][0]} {omg[1][1]!r}") or 1:
                    a_num = ceil(a_num // 3)
                else:
                    a_num = floor(a_num // 3)
                base = re.search(r"(^https://www\d+.zippyshare.com)", response.url).group(1)
                return f"{base}{file[0]}{a_num + (divider % int(b))}{file[1]}"
        return None
    except Exception as e:
        logger.debug(f"zippyshare_bypass failed: {e}")
        return None


async def fembed_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        from httpx import AsyncClient, Timeout
        async with AsyncClient() as ses:
            resp = await ses.post(
                f"https://fembed-hd.com/api/source/{token}",
                timeout=Timeout(10.0),
            )
            data = resp.json()
            return data.get("data")
    except Exception as e:
        logger.debug(f"fembed_bypass failed: {e}")
        return None


async def mp4upload_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        headers = {"referer": "https://mp4upload.com"}
        data = {
            "op": "download2", "id": token, "rand": "", "referer": "https://www.mp4upload.com/",
            "method_free": "", "method_premium": "",
        }
        from httpx import AsyncClient, Timeout
        async with AsyncClient() as ses:
            resp = await ses.post(url, headers=headers, data=data, timeout=Timeout(10.0))
            return resp.headers.get("Location")
    except Exception as e:
        logger.debug(f"mp4upload_bypass failed: {e}")
        return None


async def streamlare_bypass(url: str) -> Optional[str]:
    try:
        content_id_match = re.search(r"/[ve]/([^?#&/]+)", url)
        if not content_id_match:
            return None
        content_id = content_id_match.group(1)
        api_link = "https://sltube.org/api/video/download/get"
        client = Session()
        r = client.get(url, timeout=5).text
        soup = BeautifulSoup(r, "html.parser")
        csrf = soup.find("meta", {"name": "csrf-token"})
        if not csrf:
            return None
        csrf_token = csrf.get("content")
        xsrf_token = client.cookies.get_dict().get("XSRF-TOKEN", "")
        headers = {
            "x-requested-with": "XMLHttpRequest",
            "x-csrf-token": csrf_token,
            "x-xsrf-token": xsrf_token,
            "referer": url,
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        result = client.post(api_link, headers=headers, data={"id": content_id}, timeout=5).json()
        return result.get("result")
    except Exception as e:
        logger.debug(f"streamlare_bypass failed: {e}")
        return None


async def _rand_str() -> str:
    chars = "abcdefghijklmnopqrstuvwqyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
    return "".join([choice(chars) for _ in range(12)])


async def _hex_encode(string: str) -> str:
    return string.encode("utf-8").hex()


async def streamsb_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        if ".html" in url:
            url_id = url.split("/")[-1].split(".")[-2]
        else:
            url_id = url.split("/")[-1]
        part_one = f"{await _rand_str()}||{url_id}||{await _rand_str()}||streamsb"
        final_url = f"https://watchsb.com/sources48/{await _hex_encode(part_one)}"
        headers = {
            "watchsb": "sbstream",
            "referer": url,
            "user-agent": "Mozilla/5.0 (Linux; Android 11; 2201116PI) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Mobile Safari/537.36",
        }
        html = await _http_get(final_url, headers=headers)
        if not html:
            return None
        data = json.loads(html)
        stream_data = data.get("stream_data", {})
        return stream_data.get("file")
    except Exception as e:
        logger.debug(f"streamsb_bypass failed: {e}")
        return None


async def streamtape_bypass(url: str) -> Optional[str]:
    try:
        html = await _http_get(url)
        if not html:
            return None
        video = re.findall(r"document.*((?=id\=)[^\"']+)", html)
        if video:
            return f"https://streamtape.com/get_video?{video[-1]}"
        return None
    except Exception as e:
        logger.debug(f"streamtape_bypass failed: {e}")
        return None


async def wetransfer_bypass(url: str) -> Optional[str]:
    try:
        from requests import head
        if url.startswith("https://we.tl/"):
            r = head(url, allow_redirects=True)
            url = r.url
        recipient_id = None
        params = urlparse(url).path.split("/")[2:]
        if len(params) == 2:
            transfer_id, security_hash = params
        elif len(params) == 3:
            transfer_id, recipient_id, security_hash = params
        else:
            return None
        j = {"intent": "entire_transfer", "security_hash": security_hash}
        if recipient_id:
            j["recipient_id"] = recipient_id
        s = Session()
        r = s.get("https://wetransfer.com/", timeout=5)
        m = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
        if not m:
            return None
        s.headers.update({"x-csrf-token": m[1], "x-requested-with": "XMLHttpRequest"})
        r = s.post(
            f"https://wetransfer.com/api/v4/transfers/{transfer_id}/download",
            json=j,
            timeout=5,
        )
        return r.json().get("direct_link")
    except Exception as e:
        logger.debug(f"wetransfer_bypass failed: {e}")
        return None


async def gofile_bypass(url: str) -> Optional[str]:
    try:
        api_uri = "https://api.gofile.io"
        url = url[:-1] if url[-1] == "/" else url
        client = Session()
        response = client.get(f"{api_uri}/createAccount", timeout=5).json()
        data = {
            "contentId": url.split("/")[-1],
            "token": response["data"]["token"],
            "websiteToken": 12345,
            "cache": "true",
        }
        response = client.get(f"{api_uri}/getContent", params=data, timeout=5).json()
        for item in response.get("data", {}).get("contents", {}).values():
            if download_url := item.get("link"):
                return download_url
        return None
    except Exception as e:
        logger.debug(f"gofile_bypass failed: {e}")
        return None


async def hxfile_bypass(url: str) -> Optional[str]:
    try:
        url = url[:-1] if url[-1] == "/" else url
        token = url.split("/")[-1]
        client = Session()
        headers = {
            "content-type": "application/x-www-form-urlencoded",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36",
        }
        data = {"op": "download2", "id": token, "rand": "", "referer": "", "method_free": "", "method_premium": ""}
        response = client.post(url, headers=headers, data=data, timeout=5)
        soup = BeautifulSoup(response.text, "html.parser")
        btn = soup.find(class_="btn btn-dow")
        if btn:
            return btn["href"]
        unique = soup.find(id="uniqueExpirylink")
        return unique["href"] if unique else None
    except Exception as e:
        logger.debug(f"hxfile_bypass failed: {e}")
        return None
