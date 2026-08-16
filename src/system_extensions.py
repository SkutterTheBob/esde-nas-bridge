"""System key -> common ROM file extensions, used to pre-fill `add-system`'s
extensions prompt so you don't have to remember/type them out per system.
Uses the same key vocabulary as system_names.py/scrapers/screenscraper_ids.py
for consistency.

Cartridge/ROM-based systems generally include ".zip" (RetroArch cores read
zipped ROMs directly for these); disc-based systems don't, since disc
images are already compressed (.chd) or too structurally multi-file to zip
usefully.

Not exhaustive -- unknown keys just get no default, no different from
before this existed. Override anytime by typing something else at the
prompt, or editing `extensions:` directly in config.yaml.
"""

COMMON_EXTENSIONS: dict[str, list[str]] = {
    # Nintendo
    "nes": [".nes", ".zip"],
    "fds": [".fds", ".zip"],
    "snes": [".sfc", ".smc", ".zip"],
    "n64": [".z64", ".n64", ".v64", ".zip"],
    "gb": [".gb", ".zip"],
    "gbc": [".gbc", ".zip"],
    "gba": [".gba", ".zip"],
    "nds": [".nds"],
    "3ds": [".3ds", ".cia"],
    "gc": [".iso", ".gcm", ".rvz"],
    "wii": [".iso", ".wbfs", ".rvz"],
    "wiiu": [".wud", ".wux", ".rpx"],
    "switch": [".nsp", ".xci"],
    "virtualboy": [".vb", ".zip"],

    # Sega
    "mastersystem": [".sms", ".zip"],
    "genesis": [".md", ".gen", ".smd", ".zip"],
    "megadrive": [".md", ".gen", ".smd", ".zip"],
    "gamegear": [".gg", ".zip"],
    "sega32x": [".32x", ".zip"],
    "segacd": [".chd", ".cue"],
    "megacd": [".chd", ".cue"],
    "saturn": [".chd", ".cue"],
    "dreamcast": [".gdi", ".cdi", ".chd"],
    "sg-1000": [".sg", ".zip"],

    # Sony
    "psx": [".chd", ".cue", ".pbp"],
    "ps2": [".iso", ".chd"],
    "psp": [".iso", ".cso", ".pbp"],

    # Atari
    "atari2600": [".a26", ".bin", ".zip"],
    "atari5200": [".a52", ".zip"],
    "atari7800": [".a78", ".zip"],
    "atari800": [".atr", ".zip"],
    "atarist": [".st", ".zip"],
    "atarilynx": [".lnx", ".zip"],
    "atarijaguar": [".j64", ".jag", ".zip"],
    "atarijaguarcd": [".cue", ".chd"],

    # NEC
    "pcengine": [".pce", ".zip"],
    "pcenginecd": [".cue", ".chd"],
    "pcfx": [".cue", ".chd"],

    # SNK
    "neogeo": [".zip"],
    "neogeocd": [".cue", ".chd"],
    "ngp": [".ngp", ".zip"],
    "ngpc": [".ngc", ".zip"],

    # Other
    "3do": [".cue", ".chd", ".iso"],
    "amiga": [".adf", ".zip"],
    "amstradcpc": [".dsk", ".zip"],
    "apple2": [".dsk", ".po", ".zip"],
    "arcade": [".zip"],
    "c64": [".d64", ".zip"],
    "coleco": [".col", ".zip"],
    "intellivision": [".int", ".zip"],
    "msx": [".dsk", ".rom", ".zip"],
    "msx2": [".dsk", ".rom", ".zip"],
    "pc98": [".d88", ".zip"],
    "vectrex": [".vec", ".zip"],
    "vic20": [".prg", ".zip"],
    "wonderswan": [".ws", ".zip"],
    "wonderswancolor": [".wsc", ".zip"],
    "x68000": [".dim", ".zip"],
    "zxspectrum": [".tzx", ".tap", ".zip"],
}
