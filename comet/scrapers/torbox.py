import json
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

PANTHER_SUPABASE_API_KEY = os.getenv(
    "PANTHER_SUPABASE_API_KEY",
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
        media_id = request.media_only_id

        try:
            print(
                f"[TorBox Proxy] starting search for {media_id}",
                flush=True,
            )

            if not PANTHER_TORBOX_PROXY_URL:
                raise RuntimeError(
                    "PANTHER_TORBOX_PROXY_URL is not configured"
                )

            if not PANTHER_TORBOX_PROXY_SECRET:
                raise RuntimeError(
                    "PANTHER_TORBOX_PROXY_SECRET is not configured"
                )

            if not PANTHER_SUPABASE_API_KEY:
                raise RuntimeError(
                    "PANTHER_SUPABASE_API_KEY is not configured"
                )

            print(
                "[TorBox Proxy] proxy secret and Supabase API key are present",
                flush=True,
            )

            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Panther-Comet-Secret": PANTHER_TORBOX_PROXY_SECRET,

                # Supabase Edge Gateway authentication.
                # Use the project's publishable/anon API key here,
                # never the service-role/secret key.
                "apikey": PANTHER_SUPABASE_API_KEY,
            }

            # Legacy anon keys are JWTs. Supplying them as Authorization as
            # well keeps compatibility with projects where verify_jwt expects
            # an Authorization header specifically.
            if PANTHER_SUPABASE_API_KEY.startswith("eyJ"):
                headers["Authorization"] = (
                    f"Bearer {PANTHER_SUPABASE_API_KEY}"
                )

            async with self.session.post(
                PANTHER_TORBOX_PROXY_URL,
                json={
                    "action": "torbox_search",
                    "media_id": media_id,
                    "proxy_secret": PANTHER_TORBOX_PROXY_SECRET,
                },
                headers=headers,
            ) as response:
                response_text = await response.text()

                print(
                    f"[TorBox Proxy] HTTP {response.status} for {media_id}",
                    flush=True,
                )

                if response.status < 200 or response.status >= 300:
                    safe_body = response_text[:500]

                    print(
                        f"[TorBox Proxy] ERROR BODY: {safe_body}",
                        flush=True,
                    )

                    raise RuntimeError(
                        "Panther TorBox proxy returned "
                        f"HTTP {response.status}: "
                        f"{safe_body}"
                    )

                try:
                    payload = json.loads(response_text)
                except Exception as exc:
                    raise RuntimeError(
                        "Panther TorBox proxy returned invalid JSON"
                    ) from exc

            if not isinstance(payload, dict):
                print(
                    "[TorBox Proxy] ERROR: top-level payload is not an object",
                    flush=True,
                )
                return []

            if payload.get("success") is not True:
                error = payload.get("error")

                print(
                    f"[TorBox Proxy] ERROR: success=false; error={error}",
                    flush=True,
                )

                raise RuntimeError(
                    str(error or "Panther TorBox proxy search failed")
                )

            torbox_response = payload.get("data")

            if not isinstance(torbox_response, dict):
                print(
                    "[TorBox Proxy] No TorBox response object in payload.data",
                    flush=True,
                )
                return []

            torbox_data = torbox_response.get("data")

            if not isinstance(torbox_data, dict):
                print(
                    "[TorBox Proxy] No TorBox data object in payload.data.data",
                    flush=True,
                )
                return []

            torrent_items = torbox_data.get("torrents")

            if not isinstance(torrent_items, list):
                print(
                    "[TorBox Proxy] No torrents array in TorBox response",
                    flush=True,
                )
                return []

            print(
                f"[TorBox Proxy] TorBox returned {len(torrent_items)} raw torrents for {media_id}",
                flush=True,
            )

            rejected = 0

            for torrent in torrent_items:
                parsed = self._parse_torrent(torrent)

                if parsed is not None:
                    torrents.append(parsed)
                else:
                    rejected += 1

            print(
                f"[TorBox Proxy] parsed={len(torrents)} rejected={rejected} for {media_id}",
                flush=True,
            )

        except Exception as e:
            print(
                f"[TorBox Proxy] EXCEPTION for {media_id}: {type(e).__name__}: {e}",
                flush=True,
            )

            # Never log either secret/API key.
            log_scraper_error(
                "TorBox",
                "panther-supabase-proxy",
                media_id,
                e,
            )

        return torrents
