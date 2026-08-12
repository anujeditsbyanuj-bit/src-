# Stub replacing the upstream pySmartDL-based downloader/ package (removed
# during vendoring — Akbots/animedl_dl.py downloads via Akbots/meow_downloader.py
# instead, same as every other *_dl.py plugin in this repo). Anime.download()/
# AnimeEpisode.download() are unused by our wiring but still import this at
# module load time (sites/anime.py: `from anime_downloader.downloader import
# get_downloader`), so a real NotImplementedError beats a broken import.
def get_downloader(name):
    raise NotImplementedError(
        "anime_downloader's built-in downloader/ was intentionally not "
        "vendored — use Akbots/meow_downloader.download_stream() instead "
        "(see Akbots/animedl_dl.py)."
    )
