"""Lightweight protein-structure parsing and structural identifiers."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from pathlib import Path


class StructureError(ValueError):
    """Raised when a supplied structure cannot satisfy its manifest mapping."""


_AMINO_ACIDS = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "MSE": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "ASX": "B",
    "GLX": "Z",
    "SEC": "U",
    "PYL": "O",
    "UNK": "X",
}


@dataclass(frozen=True, slots=True)
class StructureSelection:
    """Validated chain/model selection and unmodified selected file content."""

    structure_format: str
    chain_id: str
    model_index: int
    model_identifier: str
    sequence: str
    residue_count: int
    atom_count: int
    selected_content: str


def normalize_structure_format(value: str, path: Path) -> str:
    """Normalize an explicit PDB/mmCIF format and verify the file suffix."""

    normalized = value.strip().lower()
    aliases = {"pdb": "pdb", "cif": "mmcif", "mmcif": "mmcif"}
    try:
        result = aliases[normalized]
    except KeyError as exc:
        raise StructureError(f"unsupported structure format {value!r}") from exc
    suffix = path.suffix.lower()
    valid_suffixes = {"pdb": {".pdb", ".ent"}, "mmcif": {".cif", ".mmcif"}}
    if suffix not in valid_suffixes[result]:
        raise StructureError(
            f"structure format {result} does not match file suffix {suffix!r}"
        )
    return result


def _sequence_status(query: str, structure: str) -> tuple[str, str]:
    if not structure:
        return "unavailable", "no protein CA residues were available for comparison"
    if query == structure:
        return "exact", "structure-associated sequence exactly matches the query"
    if structure in query:
        return (
            "compatible_partial",
            "structure sequence is a contiguous subsequence of the query",
        )
    if query in structure:
        return (
            "compatible_extension",
            "query sequence is a contiguous subsequence of the structure",
        )
    return "mismatch", "structure-associated sequence differs from the query"


def compare_structure_sequence(query: str, structure: str) -> tuple[str, str]:
    """Report a conservative, threshold-free sequence compatibility state."""

    return _sequence_status(query, structure)


def structural_correlation_group(
    query_id: str, reference_id: str, chain_id: str | None
) -> str:
    """Return the shared Foldseek/TM-align group for one structural pair."""

    rendered = f"structural:{query_id}:{reference_id}:{chain_id or '-'}"
    if len(rendered) <= 512:
        return rendered
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return f"structural:sha256:{digest}"


def _select_chain(chains: list[str], requested: str | None) -> str:
    if not chains:
        raise StructureError("structure contains no readable protein atoms")
    if requested is None or not requested.strip():
        return chains[0]
    if requested not in chains:
        raise StructureError(f"requested chain {requested!r} is not present")
    return requested


def _select_model(models: list[str], model_index: int | None) -> tuple[int, str]:
    if not models:
        raise StructureError("structure contains no readable models")
    selected_index = model_index or 1
    if selected_index < 1 or selected_index > len(models):
        raise StructureError(
            f"requested model index {selected_index} is not present; "
            f"structure has {len(models)} model(s)"
        )
    return selected_index, models[selected_index - 1]


def _parse_pdb(
    text: str, *, chain_id: str | None, model_index: int | None
) -> StructureSelection:
    rows: list[tuple[str, str, str, str, str, str]] = []
    model = "1"
    models: list[str] = []
    for line in text.splitlines():
        if line.startswith("MODEL"):
            model = line[10:14].strip() or str(len(models) + 1)
            continue
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if len(line) < 54:
            raise StructureError("malformed PDB atom row shorter than 54 columns")
        atom = line[12:16].strip()
        residue = line[17:20].strip().upper()
        chain = line[21:22].strip() or "."
        residue_id = f"{line[22:26].strip()}:{line[26:27].strip()}"
        if model not in models:
            models.append(model)
        rows.append((model, chain, atom, residue, residue_id, line))
    selected_index, selected_model = _select_model(models, model_index)
    chains = list(dict.fromkeys(row[1] for row in rows if row[0] == selected_model))
    selected_chain = _select_chain(chains, chain_id)
    selected = [
        row for row in rows if row[0] == selected_model and row[1] == selected_chain
    ]
    residues: list[tuple[str, str]] = []
    for _, _, atom, residue, residue_id, _ in selected:
        if atom == "CA" and (not residues or residues[-1][0] != residue_id):
            residues.append((residue_id, _AMINO_ACIDS.get(residue, "X")))
    content = "\n".join(row[-1] for row in selected) + "\nEND\n"
    return StructureSelection(
        structure_format="pdb",
        chain_id=selected_chain,
        model_index=selected_index,
        model_identifier=selected_model,
        sequence="".join(value for _, value in residues),
        residue_count=len(residues),
        atom_count=len(selected),
        selected_content=content,
    )


def _cif_atom_loop(text: str) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index].strip() != "loop_":
            index += 1
            continue
        index += 1
        headers: list[str] = []
        while index < len(lines) and lines[index].strip().startswith("_"):
            headers.append(lines[index].strip())
            index += 1
        if not headers or not any(item.startswith("_atom_site.") for item in headers):
            continue
        tokens: list[str] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith("#"):
                if tokens:
                    break
                index += 1
                continue
            if (
                stripped == "loop_"
                or stripped.startswith("_")
                or stripped.startswith("data_")
            ):
                break
            try:
                tokens.extend(shlex.split(stripped, posix=True))
            except ValueError as exc:
                raise StructureError(f"malformed mmCIF atom row: {exc}") from exc
            index += 1
        if len(tokens) % len(headers) != 0:
            raise StructureError("mmCIF atom loop has an incomplete row")
        return headers, [
            tokens[offset : offset + len(headers)]
            for offset in range(0, len(tokens), len(headers))
        ]
    raise StructureError("mmCIF file has no _atom_site loop")


def _cif_index(headers: list[str], *names: str, required: bool = True) -> int | None:
    for name in names:
        if name in headers:
            return headers.index(name)
    if required:
        raise StructureError(f"mmCIF atom loop is missing {names[0]}")
    return None


def _parse_mmcif(
    text: str, *, chain_id: str | None, model_index: int | None
) -> StructureSelection:
    headers, rows = _cif_atom_loop(text)
    group = _cif_index(headers, "_atom_site.group_PDB")
    atom = _cif_index(headers, "_atom_site.auth_atom_id", "_atom_site.label_atom_id")
    residue = _cif_index(headers, "_atom_site.auth_comp_id", "_atom_site.label_comp_id")
    chain = _cif_index(headers, "_atom_site.auth_asym_id", "_atom_site.label_asym_id")
    residue_id = _cif_index(
        headers, "_atom_site.auth_seq_id", "_atom_site.label_seq_id"
    )
    insertion = _cif_index(headers, "_atom_site.pdbx_PDB_ins_code", required=False)
    model = _cif_index(headers, "_atom_site.pdbx_PDB_model_num", required=False)
    assert group is not None and atom is not None and residue is not None
    assert chain is not None and residue_id is not None
    protein_rows = [row for row in rows if row[group] in {"ATOM", "HETATM"}]
    models = list(
        dict.fromkeys(row[model] if model is not None else "1" for row in protein_rows)
    )
    selected_index, selected_model = _select_model(models, model_index)
    chains = list(
        dict.fromkeys(
            row[chain]
            for row in protein_rows
            if (row[model] if model is not None else "1") == selected_model
        )
    )
    selected_chain = _select_chain(chains, chain_id)
    selected = [
        row
        for row in protein_rows
        if (row[model] if model is not None else "1") == selected_model
        and row[chain] == selected_chain
    ]
    residues: list[tuple[str, str]] = []
    for row in selected:
        atom_name = row[atom].strip("\"'")
        key = f"{row[residue_id]}:{row[insertion] if insertion is not None else ''}"
        if atom_name == "CA" and (not residues or residues[-1][0] != key):
            residues.append((key, _AMINO_ACIDS.get(row[residue].upper(), "X")))
    rendered = ["data_enzynotation_selected", "#", "loop_", *headers]
    rendered.extend(" ".join(shlex.quote(value) for value in row) for row in selected)
    rendered.append("#")
    return StructureSelection(
        structure_format="mmcif",
        chain_id=selected_chain,
        model_index=selected_index,
        model_identifier=selected_model,
        sequence="".join(value for _, value in residues),
        residue_count=len(residues),
        atom_count=len(selected),
        selected_content="\n".join(rendered) + "\n",
    )


def validate_structure(
    path: Path,
    *,
    structure_format: str,
    chain_id: str | None,
    model_index: int | None,
) -> StructureSelection:
    """Read and select one chain/model without altering coordinate values."""

    source = Path(path)
    if not source.is_file():
        raise StructureError(f"structure file does not exist: {source}")
    normalized_format = normalize_structure_format(structure_format, source)
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise StructureError(f"structure is not readable: {source}: {exc}") from exc
    if not text.strip():
        raise StructureError(f"structure file is empty: {source}")
    if normalized_format == "pdb":
        return _parse_pdb(text, chain_id=chain_id, model_index=model_index)
    return _parse_mmcif(text, chain_id=chain_id, model_index=model_index)
