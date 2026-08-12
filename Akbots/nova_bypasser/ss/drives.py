import logging
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs
from base64 import b64decode

from requests import Session

from config import config

logger = logging.getLogger(__name__)


async def sharerpw_bypass(url: str) -> Optional[str]:
    if not config.sharerpw_xsrf_token or not config.sharerpw_laravel_session:
        return None
    client = Session()
    client.cookies["XSRF-TOKEN"] = config.sharerpw_xsrf_token
    client.cookies["laravel_session"] = config.sharerpw_laravel_session
    try:
        res = client.get(url)
        token = re.findall(r"_token\s=\s'(.*?)'", res.text, re.DOTALL)[0]
        data = {"_token": token, "nl": 1}
        response = client.post(
            url + "/dl",
            headers={
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            },
            data=data,
        ).json()
        if isinstance(response, str):
            return response
        if response.get("message") == "OK":
            return ""
        return response.get("message", "")
    except Exception as e:
        logger.debug(f"sharerpw_bypass failed for {url}: {e}")
        return None


async def _gd_ajax_bypass(url: str, crypt: str) -> Optional[str]:
    url = url[:-1] if url[-1] == "/" else url
    parsed = urlparse(url)
    client = Session()
    client.cookies.update({"crypt": crypt})
    req_url = f"{parsed.scheme}://{parsed.netloc}/ajax.php?ajax=download"
    try:
        res = client.post(
            req_url,
            headers={"x-requested-with": "XMLHttpRequest"},
            data={"id": url.split("/")[-1]},
        ).json()
        file_val = res.get("file", "")
        gd_id = re.findall(r"gd=(.*)", file_val, re.DOTALL)
        if gd_id:
            return f"https://drive.google.com/open?id={gd_id[0]}"
        return None
    except Exception as e:
        logger.debug(f"_gd_ajax_bypass failed for {url}: {e}")
        return None


async def hubdrive_bypass(url: str) -> Optional[str]:
    if not config.hubdrive_crypt:
        return None
    return await _gd_ajax_bypass(url, config.hubdrive_crypt)


async def gdtot_bypass(url: str) -> Optional[str]:
    if not config.gdtot_crypt:
        return None
    url = url[:-1] if url[-1] == "/" else url
    client = Session()
    try:
        matc = re.findall(r"https?://(.+)\.gdtot\.(.+)\/\S+\/\S+", url)[0]
        client.cookies.update({"crypt": config.gdtot_crypt})
        response = client.get(
            f"https://{matc[0]}.gdtot.{matc[1]}/dld?id={url.split('/')[-1]}"
        )
        url_match = re.findall(r'URL=(.*?)"', response.text)[0]
        params = parse_qs(urlparse(url_match).query)
        if "gd" not in params or not params["gd"] or params["gd"][0] == "false":
            return None
        decoded_id = b64decode(str(params["gd"][0])).decode("utf-8")
        return f"https://drive.google.com/open?id={decoded_id}"
    except Exception as e:
        logger.debug(f"gdtot_bypass failed for {url}: {e}")
        return None


async def _account_login(client, url: str):
    data = {"email": config.appdrive_email, "password": config.appdrive_password}
    client.post(f"https://{urlparse(url).netloc}/login", data=data)


async def _gen_payload(data, boundary=f'{"-" * 6}_'):
    data_string = ""
    for item in data:
        data_string += f"{boundary}\r\n"
        data_string += f'Content-Disposition: form-data; name="{item}"\r\n\r\n{data[item]}\r\n'
    data_string += f"{boundary}--\r\n"
    return data_string


async def _appdrive_lookalike(client, drive_link: str) -> Optional[str]:
    try:
        response = client.get(drive_link).text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response, "html.parser")
        new_link = soup.find(class_="btn").get("href")
        return new_link
    except Exception:
        return drive_link


async def appdrive_bypass(url: str) -> Optional[str]:
    if not config.appdrive_email or not config.appdrive_password:
        return None
    client = Session()
    client.headers.update({
        "user-agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.99 Safari/537.36"
    })
    try:
        url = client.get(url).url
        response = client.get(url)
        key = re.findall(r'"key",\s+"(.*?)"', response.text)[0]
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        ddl_btn = soup.find(id="drc")
    except Exception:
        return None

    headers = {"Content-Type": f"multipart/form-data; boundary={'-' * 4}_"}
    data = {"type": 1, "key": key, "action": "original"}

    if ddl_btn is not None:
        data["action"] = "direct"
    else:
        await _account_login(client, url)

    response = None
    while data["type"] <= 3:
        try:
            response = client.post(
                url, data=await _gen_payload(data), headers=headers
            ).json()
            break
        except Exception:
            data["type"] += 1

    if not response:
        return None

    if "url" in response:
        drive_link = response["url"]
        netloc = urlparse(url).netloc
        if netloc in (
            "driveapp.in", "drivehub.in", "gdflix.pro", "drivesharer.in",
            "drivebit.in", "drivelinks.in", "driveace.in", "drivepro.in", "gdflix.top",
        ):
            return await _appdrive_lookalike(client, drive_link)
        return drive_link

    if "error" in response and response["error"]:
        return response.get("message", "")
    return None


async def jiodrive_bypass(url: str) -> Optional[str]:
    if not config.jiodrive_crypt:
        return None
    return await _gd_ajax_bypass(url, config.jiodrive_crypt)


async def kolop_bypass(url: str) -> Optional[str]:
    if not config.kolop_crypt:
        return None
    return await _gd_ajax_bypass(url, config.kolop_crypt)


async def katdrive_bypass(url: str) -> Optional[str]:
    if not config.katdrive_crypt:
        return None
    return await _gd_ajax_bypass(url, config.katdrive_crypt)


async def gadrive_bypass(url: str) -> Optional[str]:
    if not config.gadrive_crypt:
        return None
    return await _gd_ajax_bypass(url, config.gadrive_crypt)


async def drivefire_bypass(url: str) -> Optional[str]:
    if not config.drivefire_crypt:
        return None
    url = url[:-1] if url[-1] == "/" else url
    parsed = urlparse(url)
    client = Session()
    client.cookies.update({"crypt": config.drivefire_crypt})
    req_url = f"{parsed.scheme}://{parsed.netloc}/ajax.php?ajax=download"
    try:
        res = client.post(
            req_url,
            headers={"x-requested-with": "XMLHttpRequest"},
            data={"id": url.split("/")[-1]},
        ).json()
        gd_id = res.get("file", "").rsplit("/", 1)[-1]
        if gd_id:
            return f"https://drive.google.com/open?id={gd_id}"
        return None
    except Exception as e:
        logger.debug(f"drivefire_bypass failed for {url}: {e}")
        return None


async def drivebuzz_bypass(url: str) -> Optional[str]:
    if not config.drivebuzz_crypt:
        return None
    url = url[:-1] if url[-1] == "/" else url
    parsed = urlparse(url)
    client = Session()
    client.cookies.update({"crypt": config.drivebuzz_crypt})
    req_url = f"{parsed.scheme}://{parsed.netloc}/ajax.php?ajax=download"
    try:
        res = client.post(
            req_url,
            headers={"x-requested-with": "XMLHttpRequest"},
            data={"id": url.split("/")[-1]},
        ).json()
        gd_id = res.get("file", "").rsplit("=", 1)[-1]
        if gd_id:
            return f"https://drive.google.com/open?id={gd_id}"
        return None
    except Exception as e:
        logger.debug(f"drivebuzz_bypass failed for {url}: {e}")
        return None
