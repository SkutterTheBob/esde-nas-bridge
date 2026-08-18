"""Surgical text-level edits to config.yaml -- deliberately never a full
PyYAML parse+re-dump, since config.yaml is heavily hand-commented and
PyYAML doesn't preserve comments/formatting on round-trip (confirmed: a
round-trip would silently strip every explanatory comment the user relies
on). Every function here finds the exact line(s) to touch via a targeted
regex and leaves everything else in the file untouched, returning False
(no changes made) rather than guessing when the file doesn't look like the
expected shape.

Shared by cli.py's `configure-media`/`add-system` commands and the TUI's
config-editing screens, so both call the same functions instead of one
reaching into the other's internals.
"""
from __future__ import annotations

import re
from pathlib import Path


def _find_section_span(text: str, section_name: str) -> "tuple[int, int] | None":
    """Returns (start, end) character offsets of a top-level section's body
    -- everything after `section_name:` up to the next top-level
    (non-indented, non-blank, non-comment) key or EOF -- or None if the
    section header isn't found. Shared by append_to_yaml_section and
    replace_scalar_in_section so "find this section's boundary" exists in
    exactly one place."""
    header_pattern = re.compile(rf"^{re.escape(section_name)}[ \t]*:[ \t]*$", re.MULTILINE)
    header_match = header_pattern.search(text)
    if not header_match:
        return None

    boundary_pattern = re.compile(r"^\S", re.MULTILINE)
    search_start = header_match.end()
    for m in boundary_pattern.finditer(text, search_start):
        line_start = m.start()
        line_end = text.find("\n", line_start)
        line = text[line_start: line_end if line_end != -1 else len(text)]
        if line.strip().startswith("#"):
            continue
        return (search_start, line_start)
    return (search_start, len(text))


def update_or_insert_scalar(config_path: str, key: str, new_line: str,
                             insert_after_key: str | None = None) -> bool:
    """Replaces an existing `key:` line anywhere in the file with new_line,
    or -- if key isn't present yet -- inserts new_line right after the
    line starting with insert_after_key. Returns False (no changes made)
    if the file can't be read/written, or if key is missing and
    insert_after_key is None or not found either."""
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    existing_pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*:.*$", re.MULTILINE)
    if existing_pattern.search(text):
        text = existing_pattern.sub(f"  {new_line}", text, count=1)
    else:
        if insert_after_key is None:
            return False
        anchor_pattern = re.compile(rf"^([ \t]*{re.escape(insert_after_key)}[ \t]*:.*)$", re.MULTILINE)
        if not anchor_pattern.search(text):
            return False
        text = anchor_pattern.sub(lambda m: f"{m.group(1)}\n  {new_line}", text, count=1)

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def build_system_yaml_block(
    name: str, nas_source: str, subdir: str, extensions: "list[str]",
    retroarch_core: "str | None" = None,
    emulator_binary: "str | None" = None, emulator_args: "str | None" = None,
    emulator_use_shell: bool = False,
    screenscraper_id: "str | None" = None, fullname: "str | None" = None,
) -> str:
    """Builds the indented YAML block for one new `systems:` entry, ready
    to hand to append_to_yaml_section(config_path, "systems", block).
    Exactly one of retroarch_core or emulator_binary+emulator_args should
    be set -- shared by cli.py's `add-system` command and the TUI's Add
    System form so the two never drift apart on the exact block format."""
    lines = [f"  {name}:"]
    lines.append(f"    nas_source: {nas_source}")
    lines.append(f'    subdir: "{subdir}"')
    ext_list = ", ".join(f'"{e}"' for e in extensions)
    lines.append(f"    extensions: [{ext_list}]")
    if emulator_binary:
        lines.append("    emulator:")
        lines.append(f"      binary: '{emulator_binary}'")
        lines.append(f"      args: '{emulator_args}'")
        if emulator_use_shell:
            lines.append("      use_shell: true")
    else:
        lines.append(f'    retroarch_core: "{retroarch_core}"')
    if screenscraper_id:
        lines.append(f"    screenscraper_id: {screenscraper_id}")
    if fullname:
        lines.append(f'    fullname: "{fullname}"')
    return "\n".join(lines)


def append_to_yaml_section(config_path: str, section_name: str, block: str) -> bool:
    """Appends `block` (already-indented YAML lines) as the last entry
    under a top-level `section_name:` mapping. Returns False without
    changing anything if the section header isn't found."""
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    span = _find_section_span(text, section_name)
    if span is None:
        return False
    _, insert_at = span

    # Ensure exactly one blank line's worth of separation before our block.
    prefix = text[:insert_at].rstrip("\n") + "\n"
    suffix = text[insert_at:]
    text = prefix + block.rstrip("\n") + "\n" + suffix

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


def replace_scalar_in_section(config_path: str, section_name: str, key: str,
                               new_value_literal: str) -> bool:
    """Finds section_name's span (same boundary logic as
    append_to_yaml_section), then replaces an EXISTING `key: value` line
    within that span only -- never inserts, since these are keys
    load_config already requires (a missing one means something's already
    wrong with the file; don't guess where to add it). Returns False if
    the section, or the key within it, isn't found."""
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    span = _find_section_span(text, section_name)
    if span is None:
        return False
    start, end = span
    section_text = text[start:end]

    key_pattern = re.compile(rf"^([ \t]*{re.escape(key)}[ \t]*:[ \t]*).*$", re.MULTILINE)
    if not key_pattern.search(section_text):
        return False
    new_section_text = key_pattern.sub(lambda m: f"{m.group(1)}{new_value_literal}", section_text, count=1)

    text = text[:start] + new_section_text + text[end:]
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True


_NAS_ITEM_PATTERN = re.compile(r"^[ \t]*-[ \t]*name:[ \t]*(\S+)[ \t]*$", re.MULTILINE)


def update_nas_source_root(config_path: str, source_name: str, new_root: str) -> bool:
    """`nas:` is a YAML list of mappings, not a flat key: value section, so
    this can't reuse replace_scalar_in_section directly. Finds the nas:
    section span, then within it the list item whose `name:` value equals
    source_name, then the `root:` line bounded by that item's own span
    (up to the next `- name:` line, or the section boundary) -- and
    replaces just that line. Returns False if the section, the named
    source, or its root: line isn't found. Commented-out example entries
    (e.g. `# - name: backup-nas`) never match -- the leading `#` means the
    line's first non-whitespace character isn't the `-` the pattern
    requires."""
    path = Path(config_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False

    span = _find_section_span(text, "nas")
    if span is None:
        return False
    section_start, section_end = span
    section_text = text[section_start:section_end]

    item_matches = list(_NAS_ITEM_PATTERN.finditer(section_text))
    item_start = None
    item_end = len(section_text)
    for i, m in enumerate(item_matches):
        if m.group(1) == source_name:
            item_start = m.start()
            item_end = item_matches[i + 1].start() if i + 1 < len(item_matches) else len(section_text)
            break
    if item_start is None:
        return False

    item_text = section_text[item_start:item_end]
    root_pattern = re.compile(r"^([ \t]*root:[ \t]*).*$", re.MULTILINE)
    if not root_pattern.search(item_text):
        return False
    new_item_text = root_pattern.sub(lambda m: f"{m.group(1)}{new_root}", item_text, count=1)

    new_section_text = section_text[:item_start] + new_item_text + section_text[item_end:]
    text = text[:section_start] + new_section_text + text[section_end:]

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True
