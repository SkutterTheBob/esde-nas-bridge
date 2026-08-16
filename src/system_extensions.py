"""System key -> common ROM file extensions, used to pre-fill `add-system`'s
extensions prompt so you don't have to remember/type them out per system.
Uses the same key vocabulary as system_names.py/scrapers/screenscraper_ids.py
for consistency.

GLOBAL_ARCHIVE_EXTENSIONS are appended to every system's suggestion
regardless of what's in COMMON_EXTENSIONS -- most emulator cores (RetroArch
and otherwise) can read a ROM straight out of a zip/7z/tar archive, so
these are safe defaults everywhere rather than something to list per
system.

Not exhaustive -- an unmapped system key still gets GLOBAL_ARCHIVE_EXTENSIONS
as its suggestion. Override anytime by typing something else at the
prompt, or editing `extensions:` directly in config.yaml.
"""

GLOBAL_ARCHIVE_EXTENSIONS: list[str] = [".zip", ".7z", ".tar"]

COMMON_EXTENSIONS: dict[str, list[str]] = {
    # Nintendo
    "nes": [".nes"],
    "fds": [".fds"],
    "snes": [".sfc", ".smc"],
    "n64": [".z64", ".n64", ".v64"],
    "gb": [".gb"],
    "gbc": [".gbc"],
    "gba": [".gba"],
    "nds": [".nds"],
    "3ds": [".3ds", ".cia"],
    "gc": [".iso", ".gcm", ".rvz"],
    "wii": [".iso", ".wbfs", ".rvz"],
    "wiiu": [".wud", ".wux", ".rpx"],
    "switch": [".nsp", ".xci"],
    "virtualboy": [".vb"],

    # Sega
    "mastersystem": [".sms"],
    "genesis": [".md", ".gen", ".smd"],
    "megadrive": [".md", ".gen", ".smd"],
    "gamegear": [".gg"],
    "sega32x": [".32x"],
    "segacd": [".chd", ".cue"],
    "megacd": [".chd", ".cue"],
    "saturn": [".chd", ".cue"],
    "dreamcast": [".gdi", ".cdi", ".chd"],
    "sg-1000": [".sg"],

    # Sony
    "psx": [".chd", ".cue", ".pbp"],
    "ps2": [".iso", ".chd"],
    "psp": [".iso", ".cso", ".pbp"],

    # Atari
    "atari2600": [".a26", ".bin"],
    "atari5200": [".a52"],
    "atari7800": [".a78"],
    "atari800": [".atr"],
    "atarist": [".st"],
    "atarilynx": [".lnx"],
    "atarijaguar": [".j64", ".jag"],
    "atarijaguarcd": [".cue", ".chd"],

    # NEC
    "pcengine": [".pce"],
    "pcenginecd": [".cue", ".chd"],
    "pcfx": [".cue", ".chd"],

    # SNK
    "neogeocd": [".cue", ".chd"],
    "ngp": [".ngp"],
    "ngpc": [".ngc"],

    # Other
    "3do": [".cue", ".chd", ".iso"],
    "amiga": [".adf"],
    "amstradcpc": [".dsk"],
    "apple2": [".dsk", ".po"],
    "c64": [".d64"],
    "coleco": [".col"],
    "intellivision": [".int"],
    "msx": [".dsk", ".rom"],
    "msx2": [".dsk", ".rom"],
    "pc98": [".d88"],
    "vectrex": [".vec"],
    "vic20": [".prg"],
    "wonderswan": [".ws"],
    "wonderswancolor": [".wsc"],
    "x68000": [".dim"],
    "zxspectrum": [".tzx", ".tap"],
}
