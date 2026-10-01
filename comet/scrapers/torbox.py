import os

from comet.core.logger import log_scraper_error
from comet.scrapers.base import BaseScraper
from comet.scrapers.models import ScrapeRequest
from comet.services.torrent_manager import extract_trackers_from_magnet


PANTHER_TORBOX_PROXY_URL = os.getenv(
    "PANTHER_TORBOX_PROXY_URL",
    "https://yjjrokhbapsehorfgnfz.supabase.co/functions/v1/panther-torbox",
).strip()

PANTHER_TORBOX_PROXY_SECRET = os.getenv(
    "PANTHER_TORBOX_PROXY_SECRET",
    "",
).strip()


class TorboxScraper(BaseScraper):
    def __init__(self, manager, session):
        super().__init__(manager, session)

    @staticmethod
    def _parse_torrent(torrent):
        if not isinstance(torrent, dict):
            return None

        title = torrent.get("raw_title")
        info_hash = torrent.get("hash")
        tracker = torrent.get("tracker")
        magnet = torrent.get("magnet")

        if (
            not isinstance(title, str)
            or not title
            or not isinstance(info_hash, str)
            or not info_hash
            or "size" not in torrent
            or not isinstance(tracker, str)
            or not tracker
            or not isinstance(magnet, str)
        ):
            return None

        return {
            "title": title,
            "infoHash": info_hash,
            "fileIndex": None,
            "seeders": torrent.get("last_known_seeders"),
            "size": torrent["size"],
            "tracker": f"TorBox|{tracker}",
            "sources": extract_trackers_from_magnet(magnet),
        }

    async def scrape(self, request: ScrapeRequest):
        torrents = []

        try:
            if not PANTHER_TORBOX_PROXY_URL:
                raise RuntimeError(
                    "PANTHER_TORBOX_PROXY_URL is not configured"
                )

            if not PANTHER_TORBOX_PROXY_SECRET:
                raise RuntimeError(
                    "PANTHER_TORBOX_PROXY_SECRET is not configured"
                )

            async with self.session.post(
                PANTHER_TORBOX_PROXY_URL,
                json={
                    "action": "torbox_search",
                    "media_id": request.media_only_id,
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Panther-Comet-Secret": PANTHER_TORBOX_PROXY_SECRET,
                },
            ) as response:
                response_text = await response.text()

                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(
                        "Panther TorBox proxy returned "
                        f"HTTP {response.status}: "
                        f"{response_text[:500]}"
                    )

                try:
                    payload = await response.json()
                except Exception as exc:
                    raise RuntimeError(
                        "Panther TorBox proxy returned invalid JSON"
                    ) from exc

            if not isinstance(payload, dict):
                return []

            if payload.get("success") is not True:
                error = payload.get("error")
                raise RuntimeError(
                    str(error or "Panther TorBox proxy search failed")
                )

            # Supabase wraps TorBox's original response in `data`.
            torbox_response = payload.get("data")

            if not isinstance(torbox_response, dict):
                return []

            torbox_data = torbox_response.get("data")

            if not isinstance(torbox_data, dict):
                return []

            torrent_items = torbox_data.get("torrents")

            if not isinstance(torrent_items, list):
                return []

            for torrent in torrent_items:
                parsed = self._parse_torrent(torrent)

                if parsed is not None:
                    torrents.append(parsed)

        except Exception as e:
            # Do not log the proxy secret or the TorBox API token.
            log_scraper_error(
                "TorBox",
                "panther-supabase-proxy",
                request.media_only_id,
                e,
            )

        return torrents
