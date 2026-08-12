import time
import tls_client
import uuid
import random
import re
from lxml import etree
import base64
from urllib.parse import urljoin
import os
import subprocess
from tqdm import tqdm
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import *
import shutil

class Miscellaneous:
    def randomize_user_agent(self) -> str:
        android_version = f"{random.randint(13, 15)}"
        okhttp_version = f"4.{random.randint(10, 12)}.{random.randint(0, 9)}"
        return f"Crunchyroll/3.74.2 Android/{android_version} okhttp/{okhttp_version}"

class CrunchyrollBase:
    def __init__(self):
        self.session = tls_client.Session("okhttp4_android_13", random_tls_extension_order=True)

    def set_headers(self, headers):
        self.session.headers.update(headers)

class CrunchyrollLicense(CrunchyrollBase):
    def get_license(self, pssh, token, content_id, vid_token):
        _WVPROXY = "https://www.crunchyroll.com/license/v1/license/widevine"
        from pywidevine.cdm import Cdm
        from pywidevine.device import Device
        from pywidevine.pssh import PSSH
        
        device = Device.load("./l3.wvd")
        cdm = Cdm.from_device(device)
        session_id = cdm.open()
        challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
        etp_anonymous_id_header = str(uuid.uuid4())

        self.set_headers({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept': '*/*',
            'Connection': 'Keep-Alive',
            'Authorization': f"Bearer {vid_token}",
            'content-type': "application/octet-stream",
            'origin': "https://static.crunchyroll.com",
            'referer': "https://static.crunchyroll.com/",
            'x-cr-video-token': token,
            'x-cr-content-id': content_id
        })
        if use_proxy:
            response = self.session.post(_WVPROXY, data=bytes(challenge), proxy=proxy)
        else:
            response = self.session.post(_WVPROXY, data=bytes(challenge))
        if response.status_code != 200:
            print("Error: Failed to get keys")
            print("Response:", response.text)
            return

        cdm.parse_license(session_id, base64.b64decode(response.json()["license"]))
        keys = [{"type": key.type, "kid_hex": key.kid.hex, "key_hex": key.key.hex()} for key in cdm.get_keys(session_id)]
        cdm.close(session_id)

        self.set_headers({"Content-Type": "application/json"})
        if use_proxy:
            self.session.delete(f"https://www.crunchyroll.com/playback/v1/token/{content_id}/{token}", json={}, proxy=proxy)
        else:
            self.session.delete(f"https://www.crunchyroll.com/playback/v1/token/{content_id}/{token}", json={})
        return {"key": keys}
    
def download_segment(segment_links, name, format, max_threads=20):
    base_temp_dir = os.path.join("Temp", name)
    output_filename = name + '.' + format
    os.makedirs(base_temp_dir, exist_ok=True)

    total = len(segment_links)
    buffers = [None] * total
    failed_segments = []

    progress_bar = tqdm(total=total, desc=f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [INFO] : ", unit="file")

    

    def download_single(index, url):
        temp_path = os.path.join(base_temp_dir, f"segment_{index}.ts")
        for retry in range(max_retries):
            try:
                cmd = [
                   "curl", "-s", "--fail", "--connect-timeout", "10",
                   url.strip(), "-o", temp_path
                ]
                if use_proxy:
                    cmd.insert(-2, "--proxy")
                    cmd.insert(-2, proxy)
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if result.returncode == 0:
                    with open(temp_path, 'rb') as f:
                        content = f.read()
                    return index, content
            except Exception:
                pass
            time.sleep(retry_delay)
            print(f"[WARN] Segment {index} failed retrying ({retry + 1}/{max_retries}).")
        return index, None
         
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        future_to_index = {
            executor.submit(download_single, i, url): i
            for i, url in enumerate(segment_links)
        }

        for future in as_completed(future_to_index):
            index, content = future.result()
            if content:
                buffers[index] = content
            else:
                failed_segments.append((index, segment_links[index]))
            progress_bar.update(1)

    progress_bar.close()

    
    if failed_segments:
        print(f"[INFO] Retrying {len(failed_segments)} failed segments one last time...")
        for index, url in failed_segments[:]:
            temp_path = os.path.join(base_temp_dir, f"segment_{index}.ts")
            try:
                cmd = [
                   "curl", "-s", "--fail", "--connect-timeout", "10",
                   url.strip(), "-o", temp_path
                ]
                if use_proxy:
                    cmd.insert(-2, "--proxy")
                    cmd.insert(-2, proxy)
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                if result.returncode == 0:
                   with open(temp_path, 'rb') as f:
                      content = f.read()
                      buffers[index] = content
                      failed_segments.remove((index, url)) 
            except Exception:
                pass
            time.sleep(retry_delay)

   
    if failed_segments:
        with open("error.txt", "w", encoding="utf-8") as error_file:
            for index, url in failed_segments:
                error_file.write(f"{index}: {url.strip()}\n")
                print(f"[WARN] Segment {index} failed permanently after retries.")

   
    os.makedirs("Downloads", exist_ok=True)
    output_path = os.path.join("Downloads", output_filename)
    with open(output_path, 'wb') as out_file:
        for data in buffers:
            if data:
                out_file.write(data)

    shutil.rmtree(base_temp_dir, ignore_errors=True)

    print(f"[INFO] Downloaded and saved as: {output_path}")

def convert_vtt_to_srt_custom(vtt_path, srt_path):
    with open(vtt_path, "r", encoding="utf-8") as vtt_file:
        lines = vtt_file.readlines()

    srt_lines = []
    counter = 1
    buffer = []
    timestamp_line = None
    inside_style_block = False

    for line in lines:
        line = line.strip()

        if line.startswith("STYLE"):
            inside_style_block = True
            continue
        if inside_style_block:
            if line == "}":
                inside_style_block = False
            continue

        if line == "WEBVTT" or re.match(r"^c\d+$", line):
            continue

        if re.match(r"\d{2}:\d{2}:\d{2}\.\d{3} -->", line):
            if timestamp_line and buffer:
                srt_lines.append(f"{counter}")
                srt_lines.append(timestamp_line)
                srt_lines.extend(buffer)
                srt_lines.append("")
                counter += 1
                buffer = []

            
            timestamp_line = line.replace(".", ",")
            timestamp_line = re.sub(r" line:.*", "", timestamp_line)

        elif line == "":
            continue
        else:
            
            clean_line = re.sub(r"</?[^>]+>", "", line)
            buffer.append(clean_line)

    if timestamp_line and buffer:
        srt_lines.append(f"{counter}")
        srt_lines.append(timestamp_line)
        srt_lines.extend(buffer)
        srt_lines.append("")

    with open(srt_path, "w", encoding="utf-8") as srt_file:
        srt_file.write("\n".join(srt_lines))

    print(f"✅ Converted: {vtt_path} -> {srt_path}")

def parse_mpd_content(mpd_content):
    if isinstance(mpd_content, str):
        content = mpd_content.encode('utf-8')
    else:
        content = mpd_content

    root = etree.fromstring(content)
    namespace = {'ns': 'urn:mpeg:dash:schema:mpd:2011'}
    representations = root.findall(".//ns:Representation", namespace)

    video_list = []
    audio_list = []

    for elem in representations:
        rep_id = elem.attrib.get('id', '')
        bandwidth = int(elem.attrib.get('bandwidth', 0))
        codecs = elem.attrib.get('codecs', '')

        width = elem.attrib.get('width')
        height = elem.attrib.get('height')
        width = int(width) if width else None
        height = int(height) if height else None

        base_url_elem = elem.find("ns:BaseURL", namespace)
        base_url = base_url_elem.text.strip() if base_url_elem is not None else None

        segment_template_elem = elem.find("ns:SegmentTemplate", namespace)
        segment_template_url = segment_template_elem.attrib.get('media', '') if segment_template_elem is not None else None

        if not base_url and segment_template_url:
            base_url = segment_template_url

        if width and height:
            video_list.append({
                "name": rep_id,
                "bandwidth": bandwidth,
                "width": width,
                "height": height,
                "codecs": codecs,
                "base_url": base_url
            })
        else:
            audio_list.append({
                "name": rep_id,
                "bandwidth": bandwidth,
                "codecs": codecs,
                "base_url": base_url
            })

    return video_list, audio_list

def parse_mpd_logic(content):
    try:
        if isinstance(content, str):
            content = content.encode('utf-8')

        root = etree.fromstring(content)
        namespaces = {'mpd': 'urn:mpeg:dash:schema:mpd:2011', 'cenc': 'urn:mpeg:cenc:2013'}

        videos = []
        for adaptation_set in root.findall('.//mpd:AdaptationSet[@contentType="video"]', namespaces):
            for representation in adaptation_set.findall('mpd:Representation', namespaces):
                videos.append({
                    'resolution': f"{representation.get('width')}x{representation.get('height')}",
                    'codec': representation.get('codecs'),
                    'mimetype': representation.get('mimeType')
                })

        audios = []
        for adaptation_set in root.findall('.//mpd:AdaptationSet[@contentType="audio"]', namespaces):
            for representation in adaptation_set.findall('mpd:Representation', namespaces):
                audios.append({
                    'audioSamplingRate': representation.get('audioSamplingRate'),
                    'codec': representation.get('codecs'),
                    'mimetype': representation.get('mimeType')
                })

        pssh_list = []
        for content_protection in root.findall('.//mpd:ContentProtection', namespaces):
            pssh_element = content_protection.find('cenc:pssh', namespaces)
            if pssh_element is not None:
                pssh_list.append(pssh_element.text)

        return {"main_content": content.decode('utf-8'), "pssh": pssh_list}

    except etree.XMLSyntaxError as e:
        raise ValueError(f"Invalid MPD content: {e}")
    except Exception as e:
        raise RuntimeError(f"Unexpected error: {e}")
    
def get_segment_link_list(mpd_content, representation_id, url):
        if isinstance(mpd_content, str):
            content = mpd_content.encode('utf-8')
        else:
            content = mpd_content

        try:
            tree = etree.fromstring(content)
            
            ns = {'dash': 'urn:mpeg:dash:schema:mpd:2011'}
    
            
            representation = tree.find(f'.//dash:Representation[@id="{representation_id}"]', ns)
            if representation is None:
              return {}
    
            
            adaptation_set = representation.find('..')
    
            
            segment_template = adaptation_set.find('dash:SegmentTemplate', ns)
            if segment_template is None:
              return {}
    
            segment_timeline = segment_template.find('dash:SegmentTimeline', ns)
            if segment_timeline is None:
              return {}
    
            media_template = segment_template.get('media')
            init_template = segment_template.get('initialization')
            
            
            media_template = media_template.replace('$RepresentationID$', representation_id)
            init_template = init_template.replace('$RepresentationID$', representation_id)
            
            
            segment_list = []
            segment_all = []
            segment_all.append(urljoin(url, init_template))
            current_time = 0
            for segment in segment_timeline.findall('dash:S', ns):
                d_attr = segment.get('d')
                r_attr = segment.get('r')
                if not d_attr:
                    continue
                duration = int(d_attr)
                
                repeat_count = 1
                if r_attr is not None:
                    repeat_count = int(r_attr) + 1
    
                for _ in range(repeat_count):
                    segment_file = media_template.replace('$Time$', str(current_time)).replace('$Number$', str(len(segment_list)+1)) 
                    segment_list.append(urljoin(url, segment_file))
                    segment_all.append(urljoin(url, segment_file))
                    current_time += duration
    
    
            init_url = urljoin(url, init_template)
    
    
            return {"init": init_url, "segments": segment_list, "all": segment_all}
        except Exception as e:
            print(f"An error occurred: {e}")
            return {}

class CrunchyrollAuth(CrunchyrollBase):
    def get_guest_token(self):
        endpoint = "https://www.crunchyroll.com/auth/v1/token"
        authorization_header = "Basic Y3Jfd2ViOg=="
        content_type_header = "application/x-www-form-urlencoded"
        etp_anonymous_id_header = str(uuid.uuid4())

        self.set_headers({
            'Authorization': authorization_header,
            'Connection': 'Keep-Alive',
            'Content-Type': content_type_header,
            'ETP-Anonymous-ID': etp_anonymous_id_header,
            'Host': 'www.crunchyroll.com',
            'User-Agent': Miscellaneous().randomize_user_agent(),
            'X-Datadog-Sampling-Priority': '0',
        })

        data = {"grant_type": "client_id"}
        if use_proxy:
            auth_response = self.session.post(endpoint, data=data, proxy=proxy)
        else:
            auth_response = self.session.post(endpoint, data=data)

        if auth_response.status_code != 200:
            print("Error: Failed to authenticate with Crunchyroll")
            print("Response:", auth_response.text)
            return

        auth_response_payload = auth_response.json()
        access_token = auth_response_payload.get("access_token", "")
        if not access_token:
            print("Error: Access token not received")
            return
        return access_token

    def get_user_token(self, email, password):
        endpoint = "https://www.crunchyroll.com/auth/v1/token"
        authorization_header = "Basic ZG1yeWZlc2NkYm90dWJldW56NXo6NU45aThPV2cyVmtNcm1oekNfNUNXekRLOG55SXo0QU0="
        content_type_header = "application/x-www-form-urlencoded"
        etp_anonymous_id_header = str(uuid.uuid4())

        self.set_headers({
            'Authorization': authorization_header,
            'Connection': 'Keep-Alive',
            'Content-Type': content_type_header,
            'ETP-Anonymous-ID': etp_anonymous_id_header,
            'Host': 'www.crunchyroll.com',
            'User-Agent': Miscellaneous().randomize_user_agent(),
            'X-Datadog-Sampling-Priority': '0',
        })

        data = {
            "username": email,
            "password": password,
            "grant_type": "password",
            "scope": "offline_access",
            "device_id": str(uuid.uuid4()),
            "device_type": "Google sdk_gphone64_x86_64"
        }
        if use_proxy:
            auth_response = self.session.post(endpoint, data=data, proxy=proxy)
        else:
            auth_response = self.session.post(endpoint, data=data)
        if auth_response.status_code != 200:
            print("Error: Failed to authenticate with Crunchyroll")
            print("Response:", auth_response.text)
            return

        auth_response_payload = auth_response.json()
        access_token = auth_response_payload.get("access_token", "")
        if not access_token:
            print("Error: Access token not received")
            return
        return access_token

def get_filter_complex():
    
    return (
        f"[0:v]drawtext=text='{Watermark_Name}':"
        f"fontfile={fontfile}:"
        f"fontcolor={fontcolor}@{opaque}:" 
        f"fontsize={fontsize}:"
        f"x={x_axis}:"
        f"y={y_axis}[v]"
    )

class Crunchyroll(CrunchyrollBase):
    def __init__(self, token):
        super().__init__()
        self.set_headers({
            'authorization': f"Bearer {token}",
            'connection': 'Keep-Alive',
            'content-type': 'application/x-www-form-urlencoded',
            'etp-anonymous-id': str(uuid.uuid4()),
            'host': 'www.crunchyroll.com',
            'user-agent': Miscellaneous().randomize_user_agent(),
            'x-datadog-sampling-priority': '0',
        })

    def get_account_info(self):
        """Retrieve current user account information"""
        if use_proxy:
            return self.session.get("https://www.crunchyroll.com/accounts/v1/me", proxy=proxy).json()
        else:
            return self.session.get("https://www.crunchyroll.com/accounts/v1/me").json()

    def get_content_info(self, url):
        """Retrieve series metadata"""
        series_id = re.search(r'series/([^/]+)', url).group(1)
        if use_proxy:
            series_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}, proxy=proxy
            ).json()
        else:
            series_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}
            ).json()
        if use_proxy:
            seasons_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}/seasons",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}, proxy=proxy
            ).json()
        else:
            seasons_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/series/{series_id}/seasons",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}
            ).json()

        season_id = find_guid_by_locale(seasons_info["data"][0], "en-US")
        if use_proxy:
            episodes_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/seasons/{season_id}/episodes",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}, proxy=proxy
            ).json()
        else:
            episodes_info = self.session.get(
                f"https://www.crunchyroll.com/content/v2/cms/seasons/{season_id}/episodes",
                params={"preferred_audio_language": "en-US", "locale": "en-US"}
            ).json()

        return episodes_info, series_info
    def get_video_info(self, content_id):
        if use_proxy:
            return self.session.get(f"https://cr-play-service.prd.crunchyrollsvc.com/v2/{content_id}/web/chrome/play", proxy=proxy).json()
        else:
            return self.session.get(f"https://cr-play-service.prd.crunchyrollsvc.com/v2/{content_id}/web/chrome/play").json()

    def get_single_info(self, content_id):
        if use_proxy:
            return self.session.get(f"https://www.crunchyroll.com/content/v2/cms/objects/{content_id}?ratings=true&locale=en-US", proxy=proxy).json()
        else:
            return self.session.get(f"https://www.crunchyroll.com/content/v2/cms/objects/{content_id}?ratings=true&locale=en-US").json()
    

    def get_pssh(self, info):
        if use_proxy:
            mpd_content = self.session.get(info["url"], proxy=proxy).text
        else:
            mpd_content = self.session.get(info["url"]).text
        mpd_license = parse_mpd_logic(mpd_content)
        pssh = mpd_license["pssh"][1]
        token = info["token"]
        return pssh, mpd_content, token

def find_guid_by_locale(data, locale):
    """Find the GUID for the specified locale, fallback to en-US if not found"""
    en_us_guid = None
    for version in data["versions"]:
        if version["audio_locale"] == locale:
            return version["guid"]
        if version["audio_locale"] == "en-US":
            en_us_guid = version["guid"]
    return en_us_guid

