"""Maps common system names to ScreenScraper's numeric `systemeid`.

Sourced from Skyscraper's (github.com/muldjord/skyscraper) platform mapping,
which is the most reliable public reference for these IDs since ScreenScraper
doesn't publish a clean static table. Only a practical subset is included --
add more as needed, or set `screenscraper_id` directly on a system in
config.yaml to override/extend this without editing code.
"""

SCREENSCRAPER_SYSTEM_IDS: dict[str, int] = {
    "3do": 29,
    "3ds": 17,
    "amiga": 64,
    "amstradcpc": 65,
    "apple2": 86,
    "arcade": 75,
    "atari800": 43,
    "atari2600": 26,
    "atari5200": 40,
    "atari7800": 41,
    "atarijaguar": 27,
    "atarijaguarcd": 171,
    "atarilynx": 28,
    "atarist": 42,
    "c64": 66,
    "coleco": 48,
    "dreamcast": 23,
    "fds": 106,
    "gamegear": 21,
    "gb": 9,
    "gba": 12,
    "gbc": 10,
    "gc": 13,
    "genesis": 1,
    "megadrive": 1,
    "intellivision": 115,
    "mastersystem": 2,
    "megacd": 20,
    "segacd": 20,
    "msx": 113,
    "msx2": 113,
    "n64": 14,
    "nds": 15,
    "neogeo": 142,
    "neogeocd": 70,
    "nes": 3,
    "ngp": 25,
    "ngpc": 82,
    "pc": 135,
    "pc98": 208,
    "pcengine": 31,
    "pcenginecd": 114,
    "pcfx": 72,
    "ps2": 58,
    "psp": 61,
    "psx": 57,
    "saturn": 22,
    "scummvm": 123,
    "sega32x": 19,
    "sg-1000": 109,
    "snes": 4,
    "switch": 225,
    "vectrex": 102,
    "vic20": 73,
    "virtualboy": 11,
    "wii": 16,
    "wiiu": 18,
    "wonderswan": 45,
    "wonderswancolor": 46,
    "x68000": 79,
    "zxspectrum": 76,
}


def lookup_system_id(system_name: str, override: int | None = None) -> int | None:
    if override is not None:
        return override
    return SCREENSCRAPER_SYSTEM_IDS.get(system_name.lower())
