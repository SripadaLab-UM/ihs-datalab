"""The catalog: what tables and columns exist in each cohort schema.

Metadata only, never values. It is stored as one YAML file per table under
`<catalog_dir>/<SCHEMA>/<TABLE>.yml`, which is the knowledge base's
`generated/schema/` folder, and can be built from a live database or from the
older CSV metadata export.
"""

from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

import oracledb
import yaml

from datalab import safeyaml

log = logging.getLogger(__name__)

# Unquoted Oracle identifiers: nothing a path could trip over (no dots,
# slashes, spaces or Windows device names' punctuation).
_PLAIN_NAME = re.compile(r"[A-Za-z0-9_$#]+")
# A table's file: what knowledge/check.py allows in generated/schema, and no more.
MAX_TABLE_BYTES = 1024 * 1024
_TABLE_FIELDS = {"schema", "name", "type", "comment", "columns", "primary_key"}
_COLUMN_FIELDS = {"name", "type", "nullable", "comment"}


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    comment: str = ""


@dataclass
class TableInfo:
    schema: str
    name: str
    type: str  # TABLE or VIEW
    comment: str = ""
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class SearchHit:
    table: TableInfo
    score: float
    matching_columns: list[str]


class Catalog:
    def __init__(self, tables: list[TableInfo]) -> None:
        self._tables = {(t.schema, t.name): t for t in tables}
        # Why it has no tables and how to fix that, when whatever loads it
        # knows (catalog_source.py); no paths, since the agent reads it too.
        self.missing: str | None = None

    def __len__(self) -> int:
        return len(self._tables)

    def replace(self, other: Catalog) -> None:
        """Take `other`'s tables, in place: everything holding this catalog
        sees them at once (DataLab building its own, autocatalog.py, or
        reading the knowledge base's again after a sync, catalog_source.py)."""
        self._tables = dict(other._tables)

    def missing_message(self) -> str:
        """What the SQL check and the agent's catalog tools say while it has
        no tables: no query can be checked, so none runs."""
        return (
            "DataLab has no catalog of the cohorts' tables yet, so it can't check queries "
            "and none can run. " + (self.missing or "Settings → About this DataLab says why.")
        )

    @property
    def schemas(self) -> list[str]:
        return sorted({schema for schema, _ in self._tables})

    def get(self, qualified_name: str) -> TableInfo | None:
        schema, _, name = qualified_name.upper().partition(".")
        return self._tables.get((schema, name))

    def column_index(self) -> dict[str, dict[str, dict[str, str]]]:
        """schema -> table -> column -> type, names exactly as Oracle stores them.
        The SQL check resolves every column a query names against it."""
        index: dict[str, dict[str, dict[str, str]]] = {}
        for (schema, name), table in self._tables.items():
            index.setdefault(schema, {})[name] = {c.name: c.type for c in table.columns}
        return index

    def names(self, schema: str) -> list[str]:
        """The tables and views in one cohort schema."""
        return sorted(name for s, name in self._tables if s == schema.upper())

    def cohorts_with(self, name: str) -> list[str]:
        """Which cohort schemas have a table or view with this name."""
        return sorted(schema for schema, table in self._tables if table == name.upper())

    def search(
        self, query: str, *, limit: int = 20, schemas: list[str] | None = None
    ) -> list[SearchHit]:
        terms = _tokens(query)
        if not terms:
            return []
        wanted = {s.upper() for s in schemas} if schemas else None
        hits: list[SearchHit] = []
        for table in self._tables.values():
            if wanted is not None and table.schema not in wanted:
                continue
            hit = _score(table, terms)
            if hit.score > 0:
                hits.append(hit)
        # Ties go to the newest cohort, which is usually what people want.
        hits.sort(key=lambda h: (-h.score, _descending(h.table.schema), h.table.name))
        return hits[:limit]

    # Storage ---------------------------------------------------------------

    @classmethod
    def load(cls, directory: Path) -> Catalog:
        return cls.read(directory)[0]

    @classmethod
    def read(cls, directory: Path) -> tuple[Catalog, list[str]]:
        """The catalog in a folder, and the files left out (named, with why).

        Only `<SCHEMA>/<TABLE>.yml` files that are plain files, not links,
        in plain folders: nothing outside the folder is read.
        """
        files: dict[str, bytes] = {}
        skipped: list[str] = []
        if directory.is_dir() and not directory.is_symlink():
            for folder in sorted(directory.iterdir()):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                for path in sorted(folder.glob("*.yml")):
                    name = f"{folder.name}/{path.name}"
                    if path.is_symlink() or not path.is_file():
                        skipped.append(f"{name}: not a plain file")
                    elif path.stat().st_size > MAX_TABLE_BYTES:
                        skipped.append(f"{name}: larger than 1 MB")
                    else:
                        files[name] = path.read_bytes()
        catalog, unread = cls.from_files(files)
        return catalog, skipped + unread

    @classmethod
    def from_files(cls, files: Mapping[str, bytes]) -> tuple[Catalog, list[str]]:
        """The catalog in `SCHEMA/TABLE.yml -> content` (a folder's, or the
        knowledge base's generated/schema), and the files left out, with why.

        The files may come from the lab's repository: they're data, checked
        as knowledge/check.py checks them (catalog fields only, `schema` and
        `name` matching the folder and file names), read with safeyaml.
        """
        tables: list[TableInfo] = []
        skipped: list[str] = []
        for name, content in sorted(files.items()):
            try:
                tables.append(_table_from_file(name, content))
            except ValueError as error:
                skipped.append(f"{name}: {error}")
        if skipped:
            log.warning("Left %d catalog files out, such as %s", len(skipped), skipped[0])
        return cls(tables), skipped

    def save(self, directory: Path) -> None:
        """One YAML file per table. Refuses (ValueError, before writing
        anything) a schema or table name that isn't a plain Oracle
        identifier, since those become folder and file names. Column names
        only go inside the YAML, and can be anything Oracle allows: the IHS
        databases have quoted ones such as "Black tea"."""
        for table in self._tables.values():
            for name in (table.schema, table.name):
                if not _PLAIN_NAME.fullmatch(name):
                    raise ValueError(
                        f"Not saving the catalog: {name!r} in {table.qualified_name!r} "
                        "isn't a plain Oracle name."
                    )
        for table in self._tables.values():
            folder = directory / table.schema
            folder.mkdir(parents=True, exist_ok=True)
            data = asdict(table)
            (folder / f"{table.name}.yml").write_text(
                yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100),
                encoding="utf-8",
            )

    # Building --------------------------------------------------------------

    @classmethod
    def from_database(cls, connection: oracledb.Connection, schemas: list[str]) -> Catalog:
        """Read catalog views for `schemas`. Reads metadata only."""
        placeholders = ", ".join(f":s{i}" for i in range(len(schemas)))
        binds = {f"s{i}": s.upper() for i, s in enumerate(schemas)}
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT o.owner, o.object_name, o.object_type, c.comments "
                "FROM all_objects o LEFT JOIN all_tab_comments c "
                "ON c.owner = o.owner AND c.table_name = o.object_name "
                f"WHERE o.owner IN ({placeholders}) AND o.object_type IN ('TABLE', 'VIEW')",
                binds,
            )
            objects = cursor.fetchall()
            cursor.execute(
                "SELECT c.owner, c.table_name, c.column_name, c.data_type, c.data_length, "
                "c.data_precision, c.data_scale, c.char_length, c.nullable, m.comments "
                "FROM all_tab_columns c LEFT JOIN all_col_comments m "
                "ON m.owner = c.owner AND m.table_name = c.table_name "
                "AND m.column_name = c.column_name "
                f"WHERE c.owner IN ({placeholders}) ORDER BY c.owner, c.table_name, c.column_id",
                binds,
            )
            columns = cursor.fetchall()
            cursor.execute(
                "SELECT k.owner, k.table_name, k.column_name FROM all_constraints c "
                "JOIN all_cons_columns k ON k.owner = c.owner "
                "AND k.constraint_name = c.constraint_name "
                f"WHERE c.owner IN ({placeholders}) AND c.constraint_type = 'P' "
                "ORDER BY k.owner, k.table_name, k.position",
                binds,
            )
            keys = cursor.fetchall()

        tables = {
            (owner, name): TableInfo(owner, name, kind, comment or "")
            for owner, name, kind, comment in objects
        }
        for (
            owner,
            table,
            name,
            dtype,
            length,
            precision,
            scale,
            chars,
            nullable,
            comment,
        ) in columns:
            if (owner, table) in tables:
                tables[(owner, table)].columns.append(
                    Column(
                        name,
                        _type_label(dtype, length, precision, scale, chars),
                        nullable == "Y",
                        comment or "",
                    )
                )
        for owner, table, name in keys:
            if (owner, table) in tables:
                tables[(owner, table)].primary_key.append(name)
        return cls(list(tables.values()))

    @classmethod
    def from_metadata_export(cls, export_dir: Path) -> Catalog:
        """Build from the CSV export written by the prototype-era export script."""

        def rows(name: str) -> list[dict[str, str]]:
            with (export_dir / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))

        table_comments = {
            (r["owner"], r["object_name"]): r["comments"] for r in rows("table_comments")
        }
        column_comments = {
            (r["owner"], r["object_name"], r["column_name"]): r["comments"]
            for r in rows("column_comments")
        }
        tables = {
            (r["owner"], r["object_name"]): TableInfo(
                r["owner"],
                r["object_name"],
                r["object_type"],
                table_comments.get((r["owner"], r["object_name"]), ""),
            )
            for r in rows("objects")
            if r["object_type"] in ("TABLE", "VIEW")
        }
        for r in sorted(rows("columns"), key=lambda r: int(r["column_id"] or 0)):
            key = (r["owner"], r["object_name"])
            if key in tables:
                tables[key].columns.append(
                    Column(
                        r["column_name"],
                        _type_label(
                            r["data_type"],
                            _int(r["data_length"]),
                            _int(r["data_precision"]),
                            _int(r["data_scale"]),
                            _int(r["char_length"]),
                        ),
                        r["nullable"] == "Y",
                        column_comments.get((*key, r["column_name"]), ""),
                    )
                )
        primary = {
            (r["owner"], r["constraint_name"]): r["table_name"]
            for r in rows("constraints")
            if r["constraint_type"] == "P"
        }
        key_columns = defaultdict(list)
        for r in sorted(rows("constraint_columns"), key=lambda r: int(r["position"] or 0)):
            table = primary.get((r["owner"], r["constraint_name"]))
            if table:
                key_columns[(r["owner"], table)].append(r["column_name"])
        for key, names in key_columns.items():
            if key in tables:
                tables[key].primary_key = names
        return cls(list(tables.values()))


def _table_from_file(name: str, content: bytes) -> TableInfo:
    folder, _, file = name.partition("/")
    table = file.removesuffix(".yml")
    if not (_PLAIN_NAME.fullmatch(folder) and _PLAIN_NAME.fullmatch(table)) or file == table:
        raise ValueError("not a <SCHEMA>/<TABLE>.yml name")
    if len(content) > MAX_TABLE_BYTES:
        raise ValueError("larger than 1 MB")
    try:
        raw = safeyaml.load(content.decode("utf-8"), max_bytes=MAX_TABLE_BYTES)
    except (UnicodeDecodeError, yaml.YAMLError, safeyaml.YamlRefused):
        raise ValueError("not readable YAML") from None
    if not isinstance(raw, dict):
        raise ValueError("not a catalog table")
    if set(raw) - _TABLE_FIELDS:
        raise ValueError("fields other than a catalog table's")
    if raw.get("schema") != folder or raw.get("name") != table:
        raise ValueError("`schema` and `name` don't match its folder and file name")
    if raw.get("type") not in ("TABLE", "VIEW"):
        raise ValueError("`type` isn't TABLE or VIEW")
    columns = []
    for column in _list(raw.get("columns")):
        if not isinstance(column, dict) or set(column) - _COLUMN_FIELDS:
            raise ValueError("a column with fields other than name, type, nullable, comment")
        column_name, column_type = column.get("name"), column.get("type")
        nullable = column.get("nullable", True)
        if not isinstance(column_name, str) or not column_name or not isinstance(column_type, str):
            raise ValueError("a column without a name or type")
        if not isinstance(nullable, bool):
            raise ValueError("a column's `nullable` isn't true or false")
        columns.append(Column(column_name, column_type, nullable, _text(column.get("comment"))))
    key = _list(raw.get("primary_key"))
    if not all(isinstance(k, str) for k in key):
        raise ValueError("`primary_key` isn't a list of column names")
    return TableInfo(folder, table, raw["type"], _text(raw.get("comment")), columns, key)


def _list(value: object) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("a list that isn't one")
    return value


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        raise ValueError("a comment that isn't text")
    return str(value)


def _type_label(
    dtype: str,
    length: int | None,
    precision: int | None,
    scale: int | None,
    chars: int | None,
) -> str:
    if dtype in ("VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR"):
        return f"{dtype}({chars or length})"
    if dtype == "NUMBER" and precision is not None:
        return f"NUMBER({precision},{scale})" if scale else f"NUMBER({precision})"
    return dtype


def _int(value: str) -> int | None:
    return int(value) if value not in ("", None) else None


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) > 1]


def _score(table: TableInfo, terms: list[str]) -> SearchHit:
    """Each search word counts once, by its best match; table names count most."""
    name = table.name.lower()
    name_parts = set(re.split(r"[^a-z0-9]+", name))
    table_comment = table.comment.lower()
    score = 0.0
    matched_terms = 0
    matching: list[str] = []
    for term in terms:
        columns = [c for c in table.columns if term in c.name.lower()]
        commented = [c for c in table.columns if term in c.comment.lower()]
        if term in name_parts or term == name:
            best = 8.0
        elif term in name:
            best = 6.0
        elif term in table_comment:
            best = 3.0
        elif columns:
            best = 2.0
        elif commented:
            best = 1.0
        else:
            continue
        score += best
        matched_terms += 1
        for column in columns or commented:
            if column.name not in matching:
                matching.append(column.name)
    if matched_terms == len(terms) and len(terms) > 1:
        score *= 1.5
    return SearchHit(table, score, matching[:10])


def _descending(text: str) -> tuple[int, ...]:
    return tuple(-ord(ch) for ch in text)
