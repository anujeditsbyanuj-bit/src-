import logging
import re
from typing import Optional

from requests import Session

logger = logging.getLogger(__name__)


async def recaptchav3(anchor_url: str) -> Optional[str]:
    """Solve Google reCAPTCHA v3 by extracting and reloading the anchor token."""
    try:
        url_base = "https://www.google.com/recaptcha/"
        post_data = "v={}&reason=q&c={}&k={}&co={}"
        client = Session()
        client.headers.update({"content-type": "application/x-www-form-urlencoded"})
        matches = re.findall(r"([api2|enterprise]+)\/anchor\?(.*)", anchor_url)
        if not matches:
            return None
        url_base += f"{matches[0][0]}/"
        params_str = matches[0][1]
        res = client.get(f"{url_base}anchor", params=params_str, timeout=5)
        token = re.findall(r'"recaptcha-token" value="(.*?)"', res.text)
        if not token:
            return None
        token = token[0]
        params = dict(pair.split("=") for pair in params_str.split("&"))
        post_data = post_data.format(params["v"], token, params["k"], params["co"])
        res = client.post(
            f"{url_base}reload",
            params=f'k={params["k"]}',
            data=post_data,
            timeout=5,
        )
        rresp = re.findall(r'"rresp","(.*?)"', res.text)
        return rresp[0] if rresp else None
    except Exception as e:
        logger.debug(f"recaptchav3 failed: {e}")
        return None


OUO_RECAPTCHA_URL = (
    "https://www.google.com/recaptcha/api2/anchor?"
    "ar=1&k=6Lcr1ncUAAAAAH3cghg6cOTPGARa8adOf-y9zv2x&"
    "co=aHR0cHM6Ly9vdW8uaW86NDQz&hl=en&"
    "v=1B_yv3CBEV10KtI2HJ6eEXhJ&size=invisible&cb=4xnsug1vufyr"
)
