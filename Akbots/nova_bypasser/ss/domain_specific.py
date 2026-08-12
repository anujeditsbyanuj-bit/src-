import logging
import re
from typing import Optional, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class DomainSpecificHandler:
    def __init__(self):
        self._routes: list[tuple[re.Pattern, Callable]] = []
        self._build_routes()

    def _build_routes(self):
        from . import drives
        from . import hosters
        from . import shorteners_ext

        # (regex_pattern, async_bypass_function)
        self._routes = [
            # --- Drives (GD clones) ---
            (re.compile(r"https?://(sharer\.pw/file)\S+"), drives.sharerpw_bypass),
            (re.compile(r"https?://(hubdrive)\S+"), drives.hubdrive_bypass),
            (re.compile(r"https?://(.+)\.gdtot\.(.+)\/\S+\/\S+"), drives.gdtot_bypass),
            (re.compile(r"https?://(anidrive|driveroot|driveflix|indidrive|drivehub|appdrive|driveapp|driveace|gdflix|drivelinks|drivebit|drivesharer|drivepro)\.\S+"), drives.appdrive_bypass),
            (re.compile(r"https?://(jiodrive)\S+"), drives.jiodrive_bypass),
            (re.compile(r"https?://(kolop)\S+"), drives.kolop_bypass),
            (re.compile(r"https?://(katdrive)\S+"), drives.katdrive_bypass),
            (re.compile(r"https?://(gadrive)\S+"), drives.gadrive_bypass),
            (re.compile(r"https?://(drivefire)\S+"), drives.drivefire_bypass),
            (re.compile(r"https?://(drivebuzz)\S+"), drives.drivebuzz_bypass),

            # --- File hosters ---
            (re.compile(r"https?://(anonfiles\.com)\S+"), hosters.anonfiles_bypass),
            (re.compile(r"https?://(antfiles\.com\/\?dl\=)\S+"), hosters.antfiles_bypass),
            (re.compile(r"https?://(pjointe|dl4free|tenvoi|piecejointe|mesfichiers|desfichiers|megadl|dfichiers|alterupload|cjoint|1fichier)\.com/\S+"), hosters.fichier_bypass),
            (re.compile(r"https?://krakenfiles\.com/\S+"), hosters.krakenfiles_bypass),
            (re.compile(r"https?://(mdisk\.me\/convertor)\S+"), hosters.mdisk_bypass),
            (re.compile(r"https?://(www\.mediafire\.com\/download/)\S+"), hosters.mediafire_bypass),
            (re.compile(r"https?://(pixeldrain\.com\/(l|u)\/)\S+"), hosters.pixeldrain_bypass),
            (re.compile(r"https?://(racaty\.(net|io)/)\S+"), hosters.racaty_bypass),
            (re.compile(r"https?://(send\.cm/)\S+"), hosters.sendcm_bypass),
            (re.compile(r"https?://(sfile\.mobi/)\S+"), hosters.sfile_bypass),
            (re.compile(r"https?://(www\.solidfiles\.com/v/)\S+"), hosters.solidfiles_bypass),
            (re.compile(r"https?://(sourceforge\.net/)\S+"), hosters.sourceforge_bypass),
            (re.compile(r"https?://(uploadbaz\.me/)\S+"), hosters.uploadbaz_bypass),
            (re.compile(r"https?://(www\.upload\.ee/)\S+"), hosters.uploadee_bypass),
            (re.compile(r"https?://(uppit\.com/)\S+"), hosters.uppit_bypass),
            (re.compile(r"https?://(userscloud\.com/)\S+"), hosters.userscloud_bypass),
            (re.compile(r"https?://(we\.tl/)\S+"), hosters.wetransfer_bypass),
            (re.compile(r"https?://(yadi\.sk|disk\.yandex\.com)\S+"), hosters.yandex_bypass),
            (re.compile(r"https?://www\d+\.zippyshare\.com/v/[^/]+/file\.html"), hosters.zippyshare_bypass),
            (re.compile(r"https?://(gofile\.io\/d/)\S+"), hosters.gofile_bypass),
            (re.compile(r"https?://(hxfile\.co/)\S+"), hosters.hxfile_bypass),

            # --- Video hosters ---
            (re.compile(r"https?://(fembed|femax20|fcdn|feurl|naniplay|mm9842|layarkacaxxi|naniplay\.nanime|fembed\-hd)\.(com|net|stream|icu|in|biz)\S+"), hosters.fembed_bypass),
            (re.compile(r"https?://(www\.mp4upload\.com/)\S+"), hosters.mp4upload_bypass),
            (re.compile(r"https?://(streamlare|sltube\.(com|org)\/v/)\S+"), hosters.streamlare_bypass),
            (re.compile(r"https?://(watchsb|streamsb)\.(com|net)\/\S+"), hosters.streamsb_bypass),
            (re.compile(r"https?://(streamtape\.(com|to|xyz)/)\S+"), hosters.streamtape_bypass),

            # --- Shorteners ---
            (re.compile(r"https?://(ouo\.(io|press)/)\S+"), shorteners_ext.ouo_bypass),
            (re.compile(r"https?://(short\.url2go\.in/)\S+"), shorteners_ext.shourturl),
            (re.compile(r"https?://(adf\.ly/)\S+"), shorteners_ext.adfly_bypass),
            (re.compile(r"https?://(bit\.ly\/)\S+"), shorteners_ext.bitly_bypass),
            (re.compile(r"https?://(gtlinks\.me\/)\S+"), shorteners_ext.gtlinks_bypass),
            (re.compile(r"https?://(loan\.kinemaster\.cc\/\?token=)\S+"), shorteners_ext.gtlinks_bypass),
            (re.compile(r"https?://(www\.theforyou\.in\/\?token=)\S+"), shorteners_ext.gtlinks_bypass),
            (re.compile(r"https?://(hypershort\.com\/)\S+"), shorteners_ext.hypershort_bypass),
            (re.compile(r"https?://(linkvertise\.com/)\S+"), shorteners_ext.linkvertise_bypass),
            (re.compile(r"https?://(shorte|festyy|gestyy|corneey|destyy|ceesty)\.(st|com)\/\S+"), shorteners_ext.shortest_bypass),
            (re.compile(r"https?://(pkin\.me/)\S+"), shorteners_ext.pkin_bypass),
            (re.compile(r"https?://(www\.shortly\.xyz\/)\S+"), shorteners_ext.shortly_bypass),
            (re.compile(r"https?://(safeurl\.sirigan\.my\.id/)\S+"), shorteners_ext.sirigan_bypass),
            (re.compile(r"https?://(thinfi\.com\/)\S+"), shorteners_ext.thinfi_bypass),
            (re.compile(r"https?://(tinyurl\.com\/)\S+"), shorteners_ext.tinyurl_bypass),
            (re.compile(r"https?://(try2link\.com\/)\S+"), shorteners_ext.try2link_bypass),

            # --- Indian shorteners (type 1) ---
            (re.compile(r"https?://(tekcrypt\.in/tek/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(link\.short2url\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(go\.rocklinks\.net/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(rocklinks\.net/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(earn\.moneykamalo\.com/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(m\.easysky\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(indianshortner\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(open\.crazyblog\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(link\.tnvalue\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(shortingly\.me/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(dulink\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(bindaaslinks\.com/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(pdiskshortener\.com/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(mdiskshortner\.link/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(go\.earnl\.xyz/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(g\.rewayatcafe\.com/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(ser2\.crazyblog\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(bitshorten\.com/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"http?://(rocklink\.in/)\S+"), shorteners_ext.shortner_type_one_bypass),
            (re.compile(r"https?://(urlsopen\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),

            # --- Indian shorteners (type 2) ---
            (re.compile(r"https?://(droplink\.co/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(tnlink\.in\/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(ez4short\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(xpshort\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"http?://(vearnl\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(adrinolinks\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(techymozo\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(linkbnao\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(linksxyz\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(short\-jambo\.com/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(ads\.droplink\.co\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(linkpays\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(pi\-l\.ink/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(link\.tnlink\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(earn4link\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
            (re.compile(r"https?://(open2get\.in/)\S+"), shorteners_ext.shortner_type_two_bypass),
        ]

    def has_route(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        for pattern, _ in self._routes:
            if pattern.search(url):
                return True
        return False

    async def resolve(self, url: str) -> Optional[str]:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        for pattern, func in self._routes:
            if pattern.search(url):
                try:
                    result = await func(url)
                    if result:
                        logger.debug(f"Domain-specific bypass succeeded for {url}: {result[:100]}")
                        return result
                except Exception as e:
                    logger.debug(f"Domain-specific bypass failed for {url}: {e}")
                    continue
        return None
