"""LightRAG upload helpers — parser filename hints for multimodal routing."""

from __future__ import annotations

from pathlib import Path

VALID_PARSE_ENGINES = frozenset({"legacy", "native", "mineru", "docling"})
VALID_CHUNKING = frozenset({"F", "R", "V", "P"})


def build_process_options(
    *,
    analyze_images: bool | None = None,
    analyze_tables: bool | None = None,
    analyze_equations: bool | None = None,
    chunking: str | None = None,
    skip_kg: bool | None = None,
    extra: str = "",
) -> str:
    """Build a LightRAG ``process_options`` string (e.g. ``ietP``)."""
    opts: list[str] = []
    if analyze_images:
        opts.append("i")
    if analyze_tables:
        opts.append("t")
    if analyze_equations:
        opts.append("e")
    if chunking:
        c = chunking.strip().upper()
        if c in VALID_CHUNKING:
            opts.append(c)
    if skip_kg:
        opts.append("!")
    extra = (extra or "").strip()
    if extra:
        opts.append(extra)
    return "".join(opts)


def build_upload_filename(
    path: Path,
    *,
    parse_engine: str | None = None,
    process_options: str | None = None,
) -> str:
    """Return multipart upload filename with ``[engine-options]`` hint."""
    stem = path.stem
    suffix = path.suffix
    engine = (parse_engine or "").strip().lower()
    if engine and engine not in VALID_PARSE_ENGINES:
        raise ValueError(
            f"invalid parse_engine {parse_engine!r}; "
            f"expected one of {sorted(VALID_PARSE_ENGINES)}"
        )
    opts = (process_options or "").strip()
    if engine and opts:
        hinted_stem = f"{stem}.[{engine}-{opts}]"
    elif engine:
        hinted_stem = f"{stem}.[{engine}]"
    elif opts:
        hinted_stem = f"{stem}.[-{opts}]"
    else:
        return path.name
    return f"{hinted_stem}{suffix}"
