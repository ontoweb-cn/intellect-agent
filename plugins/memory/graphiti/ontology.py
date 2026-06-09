"""Ontology — entity/edge type definitions loaded from YAML.

Phase 5.1: lets operators constrain Graphiti's learned extraction with
a curated schema instead of pure learned mode.  Loads
``$INTELLECT_HOME/graphiti/ontology.yaml`` (when present), parses
entity/edge type declarations, and dynamically constructs Pydantic
classes suitable for ``Graphiti.add_episode(entity_types=...,
edge_types=..., edge_type_map=...)``.

YAML shape:

    entities:
      Person:
        description: "A human individual."
        properties:
          name: {type: str, required: true, description: "Full name"}
          email: {type: str, required: false}
          birthdate: {type: date, required: false}
      Project:
        description: "A unit of work or initiative."
        properties:
          name: {type: str, required: true}
          status: {type: str, required: false, description: "active|paused|completed"}
    edges:
      WORKS_ON:
        description: "Person actively contributes to a Project."
        properties:
          role: {type: str, required: false}
          since: {type: date, required: false}
      MANAGES:
        description: "Person has managerial responsibility for a Project."
    edge_map:
      # (source_entity, target_entity) -> [allowed edge types]
      [Person, Project]: [WORKS_ON, MANAGES]

The file is OPTIONAL.  When absent or unparseable, the plugin falls
back to graphiti-core's learned mode — same behavior as Phases 0-4.

Supported property types: ``str``, ``int``, ``float``, ``bool``,
``date``, ``datetime``, ``list[str]``.  Anything else is rejected
loudly at load time (better than silently coercing to str).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)

ONTOLOGY_FILE_NAME = "ontology.yaml"

# Whitelisted property type names → Python types.  Stays narrow on
# purpose: anything outside this set should go through a deliberate
# schema review (and a code change here) rather than land via free-form
# YAML.  Coercing unknown strings to ``Any`` would defeat the point of
# a curated ontology.
_TYPE_MAP: Dict[str, Type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "bool": bool,
    "boolean": bool,
    "date": date,
    "datetime": datetime,
    "list[str]": List[str],
    "list[int]": List[int],
}


@dataclass
class _Property:
    name: str
    py_type: Type
    required: bool = False
    description: str = ""


@dataclass
class _TypeDecl:
    """One entity-type or edge-type declaration."""
    name: str
    description: str = ""
    properties: List[_Property] = field(default_factory=list)


@dataclass
class Ontology:
    """Parsed ontology ready to feed Graphiti.add_episode kwargs."""

    entities: Dict[str, Type] = field(default_factory=dict)        # name -> Pydantic class
    edges: Dict[str, Type] = field(default_factory=dict)
    edge_type_map: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    source: str = ""                                                # path it came from

    def is_empty(self) -> bool:
        return not self.entities and not self.edges

    def as_add_episode_kwargs(self) -> Dict[str, Any]:
        """Return kwargs ready to splat into ``Graphiti.add_episode``."""
        out: Dict[str, Any] = {}
        if self.entities:
            out["entity_types"] = dict(self.entities)
        if self.edges:
            out["edge_types"] = dict(self.edges)
        if self.edge_type_map:
            out["edge_type_map"] = dict(self.edge_type_map)
        return out


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def ontology_path(intellect_home: str = "") -> Path:
    home = intellect_home or os.environ.get(
        "INTELLECT_HOME", str(Path.home() / ".intellect")
    )
    return Path(home) / "graphiti" / ONTOLOGY_FILE_NAME


def load_ontology(intellect_home: str = "") -> Ontology:
    """Read + parse the ontology file.  Returns an empty Ontology when
    the file is absent or the parse fails — the caller falls back to
    graphiti-core's learned mode either way.
    """
    path = ontology_path(intellect_home)
    if not path.exists():
        return Ontology()

    try:
        import yaml  # PyYAML is a core dep
    except ImportError:
        logger.warning("graphiti: PyYAML not available; ontology ignored")
        return Ontology()

    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning(
            "graphiti: failed to parse %s: %s — falling back to learned mode",
            path,
            exc,
        )
        return Ontology()

    if not isinstance(raw, dict):
        logger.warning(
            "graphiti: ontology at %s is not a mapping; ignoring", path
        )
        return Ontology()

    try:
        return _build_ontology(raw, source=str(path))
    except OntologyError as exc:
        logger.warning(
            "graphiti: ontology rejected (%s); falling back to learned mode",
            exc,
        )
        return Ontology()


class OntologyError(ValueError):
    """Raised when an ontology YAML is structurally invalid."""


def _build_ontology(raw: Dict[str, Any], *, source: str) -> Ontology:
    entity_decls = _parse_type_block(
        raw.get("entities") or {}, label="entity"
    )
    edge_decls = _parse_type_block(raw.get("edges") or {}, label="edge")

    entity_classes = {
        decl.name: _build_pydantic_model(decl) for decl in entity_decls
    }
    edge_classes = {
        decl.name: _build_pydantic_model(decl) for decl in edge_decls
    }
    edge_type_map = _parse_edge_map(
        raw.get("edge_map") or {},
        entity_names=set(entity_classes.keys()),
        edge_names=set(edge_classes.keys()),
    )

    return Ontology(
        entities=entity_classes,
        edges=edge_classes,
        edge_type_map=edge_type_map,
        source=source,
    )


def _parse_type_block(
    block: Any, *, label: str
) -> List[_TypeDecl]:
    if not isinstance(block, dict):
        raise OntologyError(f"`{label}s` block must be a mapping")
    out: List[_TypeDecl] = []
    for name, decl in block.items():
        if not isinstance(name, str) or not name:
            raise OntologyError(f"{label} name must be a non-empty string")
        if not _is_valid_type_name(name):
            raise OntologyError(
                f"{label} name {name!r} must be CamelCase or "
                f"UPPER_SNAKE (got {name!r})"
            )
        if decl is None:
            decl = {}
        if not isinstance(decl, dict):
            raise OntologyError(
                f"{label} {name!r}: declaration must be a mapping"
            )
        props = _parse_properties(decl.get("properties") or {}, label=label, owner=name)
        out.append(
            _TypeDecl(
                name=name,
                description=str(decl.get("description") or ""),
                properties=props,
            )
        )
    return out


def _parse_properties(
    props: Any, *, label: str, owner: str
) -> List[_Property]:
    if not isinstance(props, dict):
        raise OntologyError(
            f"{label} {owner!r}: `properties` must be a mapping"
        )
    out: List[_Property] = []
    for pname, pdecl in props.items():
        if not isinstance(pname, str) or not pname.isidentifier():
            raise OntologyError(
                f"{label} {owner!r}: property name {pname!r} is not a "
                "valid Python identifier"
            )
        if pdecl is None:
            pdecl = {}
        if not isinstance(pdecl, dict):
            raise OntologyError(
                f"{label} {owner!r}: property {pname!r}: declaration "
                "must be a mapping"
            )
        ptype_name = str(pdecl.get("type") or "str").strip()
        py_type = _TYPE_MAP.get(ptype_name)
        if py_type is None:
            raise OntologyError(
                f"{label} {owner!r}: property {pname!r}: unknown type "
                f"{ptype_name!r}. Allowed: {sorted(_TYPE_MAP)!r}"
            )
        out.append(
            _Property(
                name=pname,
                py_type=py_type,
                required=bool(pdecl.get("required", False)),
                description=str(pdecl.get("description") or ""),
            )
        )
    return out


def _parse_edge_map(
    raw: Any,
    *,
    entity_names: set,
    edge_names: set,
) -> Dict[Tuple[str, str], List[str]]:
    """edge_map YAML accepts a few shapes; normalise to ``{(src, dst): [edges]}``."""
    if not raw:
        return {}
    if not isinstance(raw, (dict, list)):
        raise OntologyError("`edge_map` must be a mapping or list of pairs")

    out: Dict[Tuple[str, str], List[str]] = {}

    def _record(src: Any, dst: Any, edges: Any) -> None:
        if not isinstance(src, str) or not isinstance(dst, str):
            raise OntologyError("edge_map key must be a [src, dst] pair of strings")
        if src not in entity_names:
            raise OntologyError(f"edge_map references unknown entity {src!r}")
        if dst not in entity_names:
            raise OntologyError(f"edge_map references unknown entity {dst!r}")
        if isinstance(edges, str):
            edges = [edges]
        if not isinstance(edges, list):
            raise OntologyError(
                f"edge_map entry ({src},{dst}): value must be a list of edge type names"
            )
        for e in edges:
            if e not in edge_names:
                raise OntologyError(
                    f"edge_map ({src},{dst}) references unknown edge {e!r}"
                )
        out[(src, dst)] = list(edges)

    if isinstance(raw, dict):
        # YAML maps can't have list keys, so users often write the pair
        # as the value of an arbitrary key, or as a 2-element list/tuple.
        # We accept both:
        #   "[Person, Project]": [WORKS_ON]            (string key)
        #   ? - Person                                (flow mapping with list key)
        #     - Project
        #     : [WORKS_ON]
        for k, v in raw.items():
            pair = _normalize_pair_key(k)
            _record(pair[0], pair[1], v)
    else:
        # list-of-pairs form: each item is {pair: [src, dst], edges: [...]}
        for item in raw:
            if not isinstance(item, dict):
                raise OntologyError(
                    "edge_map list items must be mappings"
                )
            pair = item.get("pair") or item.get("entities")
            if not isinstance(pair, list) or len(pair) != 2:
                raise OntologyError(
                    "edge_map list item: `pair` must be [src, dst]"
                )
            _record(pair[0], pair[1], item.get("edges"))

    return out


def _normalize_pair_key(k: Any) -> Tuple[str, str]:
    if isinstance(k, tuple) and len(k) == 2:
        return (str(k[0]), str(k[1]))
    if isinstance(k, list) and len(k) == 2:
        return (str(k[0]), str(k[1]))
    if isinstance(k, str):
        s = k.strip().lstrip("[").rstrip("]")
        parts = [p.strip().strip("'\"") for p in s.split(",")]
        if len(parts) == 2:
            return (parts[0], parts[1])
    raise OntologyError(f"edge_map key {k!r} is not a [src, dst] pair")


def _is_valid_type_name(name: str) -> bool:
    if not name or not name[0].isalpha():
        return False
    if name.replace("_", "").isalnum() and (
        name[0].isupper() or name.isupper()
    ):
        return True
    return False


def _build_pydantic_model(decl: _TypeDecl) -> Type:
    """Construct a Pydantic BaseModel subclass from a _TypeDecl."""
    from pydantic import Field, create_model

    fields: Dict[str, Any] = {}
    for p in decl.properties:
        default = ... if p.required else None
        py_t = p.py_type if p.required else Optional[p.py_type]
        fields[p.name] = (py_t, Field(default, description=p.description))

    model = create_model(decl.name, **fields)
    if decl.description:
        model.__doc__ = decl.description
    return model
