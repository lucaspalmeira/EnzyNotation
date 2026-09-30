"""Command-line interface for EnzyNotation."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from enzynotation import __version__
from enzynotation.config import load_config
from enzynotation.exceptions import EnzyNotationError
from enzynotation.fasta import (
    FastaValidationOptions,
    validate_fasta,
    write_normalized_fasta,
)
from enzynotation.logging_utils import configure_logging

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""

    parser = argparse.ArgumentParser(
        prog="enzynotation",
        description="Evidence-based protein enzyme annotation pipeline",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="validate and normalize a protein FASTA file"
    )
    validate.add_argument("input", type=Path, help="input protein FASTA")
    validate.add_argument(
        "--config",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="YAML overlay; may be supplied more than once",
    )
    validate.add_argument(
        "--output", type=Path, help="write normalized FASTA when validation succeeds"
    )
    validate.add_argument("--report", type=Path, help="write a JSON validation report")
    return parser


def _write_json_report(report: dict[str, object], destination: Path) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise EnzyNotationError(
            f"Cannot write validation report {destination}: {exc}"
        ) from exc


def _run_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logging_config = config.section("logging")
    configure_logging(str(logging_config["level"]))

    options = FastaValidationOptions.from_mapping(config.section("input"))
    result = validate_fasta(args.input, options)
    report = result.to_dict()

    if args.report is not None:
        _write_json_report(report, args.report)

    for issue in result.issues:
        location = f" line {issue.line}" if issue.line is not None else ""
        record = f" [{issue.record_id}]" if issue.record_id is not None else ""
        LOGGER.log(
            logging.ERROR if issue.severity.value == "error" else logging.WARNING,
            "%s%s%s: %s",
            issue.code,
            location,
            record,
            issue.message,
        )

    if not result.is_valid:
        print(
            f"INVALID: {len(result.records)} record(s), "
            f"{len(result.errors)} error(s), {len(result.warnings)} warning(s)"
        )
        return 1

    output_config = config.section("output")
    if args.output is not None:
        write_normalized_fasta(
            result.records, args.output, line_width=int(output_config["line_width"])
        )
    print(f"VALID: {len(result.records)} record(s), {len(result.warnings)} warning(s)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the EnzyNotation command-line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _run_validate(args)
    except EnzyNotationError as exc:
        parser.exit(2, f"enzynotation: error: {exc}\n")
    parser.error(f"unknown command: {args.command}")
    return 2
