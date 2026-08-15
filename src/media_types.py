"""Canonical media type vocabulary, shared across the importer, scraper,
and the interactive `configure-media` CLI command.

Matches ES-DE's own downloaded_media folder names exactly (see
gamelist_writer.py and skraper_import.py for how this was confirmed).
"""

ALL_MEDIA_TYPES = [
    "covers",
    "screenshots",
    "marquees",
    "3dboxes",
    "backcovers",
    "fanart",
    "manuals",
    "miximages",
    "physicalmedia",
    "titlescreens",
    "videos",
]

# Rough guidance only (not measured against your actual library) -- these
# are the types most likely to dominate disk usage for a large collection:
# manuals are often multi-megabyte PDFs, videos are, well, videos.
LARGE_MEDIA_TYPES = {"manuals", "videos"}
