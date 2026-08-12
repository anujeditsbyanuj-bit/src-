from config import AKBOTS_BRAND_NAME


class page_template(object):
    NAME = AKBOTS_BRAND_NAME or "Akbots"

    DISCLAIMER = (
        "This platform provides high-speed streaming and download services. "
        "Strictly for personal use. Report any copyright infringement or "
        "inappropriate content to our support team."
    )

    REPORT_LINK = "https://t.me/anujedits76"

    COLOURS = {
        "header": {"background": "linear-gradient(90deg, #1a1a1a, #2c3e50)", "color": "#00f0ff"},
        "mx_player": {"background": "#2980b9", "color": "#ffffff"},
        "vlc_player": {"background": "#e67e22", "color": "#ffffff"},
        "playit_player": {"background": "#8e44ad", "color": "#ffffff"},
        "download": {"background": "#27ae60", "color": "#ffffff"},
        "create_link": {"background": "#f1c40f", "color": "#000000"},
        "share": {"background": "#c0392b", "color": "#ffffff"},
        "file_information": {"background": "#34495e", "color": "#ecf0f1"},
        "description": {"background": "#000000", "color": "#bdc3c7"},
    }
