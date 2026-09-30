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
from enzynotation.runner import PipelineRunner
from enzynotation.state import RunStatus

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

    run = subparsers.add_parser("run", help="run the local validation-only workflow")
    run.add_argument("input", type=Path, help="input protein FASTA")
    run.add_argument(
        "--config",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="YAML overlay; may be supplied more than once",
    )
    run.add_argument("--run-id", help="stable run identifier used for resume")
    run.add_argument(
        "--results-dir",
        type=Path,
        default=Path("results"),
        help="root directory for scientific run artifacts",
    )
    run.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs"),
        help="root directory for stage stdout/stderr logs",
    )
    run.add_argument(
        "--no-resume",
        action="store_true",
        help="fail if the selected run already exists",
    )
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


def _run_pipeline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logging_config = config.section("logging")
    configure_logging(str(logging_config["level"]))
    runner = PipelineRunner(
        config,
        results_root=args.results_dir,
        logs_root=args.logs_dir,
    )
    result = runner.run(
        args.input,
        run_id=args.run_id,
        resume=not args.no_resume,
    )
    print(f"RUN {result.status.value.upper()}: {result.run_id}")
    print(f"Manifest: {result.manifest_path}")
    return 0 if result.status is not RunStatus.FAILED else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the EnzyNotation command-line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "run":
            return _run_pipeline(args)
    except EnzyNotationError as exc:
        parser.exit(2, f"enzynotation: error: {exc}\n")
    parser.error(f"unknown command: {args.command}")
    return 2
