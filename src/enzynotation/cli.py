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
from enzynotation.family import load_family_profile
from enzynotation.fasta import (
    FastaValidationOptions,
    validate_fasta,
    write_normalized_fasta,
)
from enzynotation.logging_utils import configure_logging
from enzynotation.runner import PipelineRunner
from enzynotation.stages.blast import BlastStage
from enzynotation.stages.clean import CleanStage
from enzynotation.stages.domains import DomainsStage
from enzynotation.stages.foldseek import FoldseekStage
from enzynotation.stages.motifs import MotifsStage
from enzynotation.stages.structures import StructuresStage
from enzynotation.stages.tmalign import TMAlignStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus
from enzynotation.structure_mapping import load_structures_config
from enzynotation.tools.blast import load_blast_config
from enzynotation.tools.clean import load_clean_config
from enzynotation.tools.foldseek import load_foldseek_config
from enzynotation.tools.hmmer import load_hmmer_config
from enzynotation.tools.interpro import load_interpro_config
from enzynotation.tools.tmalign import load_tmalign_config
from enzynotation.workflow import Workflow

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

    run = subparsers.add_parser(
        "run",
        help="run validation with optional evidence-provider stages",
    )
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
    run.add_argument(
        "--blast-config",
        type=Path,
        metavar="PATH",
        help="enable the BLASTp evidence stage with this YAML configuration",
    )
    run.add_argument(
        "--clean-config",
        type=Path,
        metavar="PATH",
        help="enable CLEAN model-prediction evidence with this YAML configuration",
    )
    run.add_argument(
        "--family-config",
        type=Path,
        metavar="PATH",
        help="family profile used by domain and motif evidence stages",
    )
    run.add_argument(
        "--hmmer-config",
        type=Path,
        metavar="PATH",
        help="enable HMMER domain evidence with this YAML configuration",
    )
    run.add_argument(
        "--interpro-config",
        type=Path,
        metavar="PATH",
        help="configure optional InterProScan domain evidence",
    )
    run.add_argument(
        "--motifs",
        action="store_true",
        help="enable catalytic motif analysis from the family profile",
    )
    run.add_argument(
        "--structures-config",
        type=Path,
        metavar="PATH",
        help="validate and stage supplied structures from this YAML configuration",
    )
    run.add_argument(
        "--foldseek-config",
        type=Path,
        metavar="PATH",
        help="enable Foldseek structural evidence with this YAML configuration",
    )
    run.add_argument(
        "--tmalign-config",
        type=Path,
        metavar="PATH",
        help="enable selective TM-align evidence with this YAML configuration",
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
    stages = [ValidationStage()]
    if args.blast_config is not None:
        blast_config = load_blast_config(args.blast_config)
        stages.append(BlastStage(blast_config))
    if args.clean_config is not None:
        clean_config = load_clean_config(args.clean_config)
        stages.append(CleanStage(clean_config))
    domain_requested = args.hmmer_config is not None or args.interpro_config is not None
    family_requested = domain_requested or args.motifs
    if family_requested and args.family_config is None:
        raise EnzyNotationError(
            "--family-config is required with domain or motif evidence"
        )
    if args.family_config is not None and not family_requested:
        raise EnzyNotationError(
            "--family-config requires --hmmer-config, --interpro-config, or --motifs"
        )
    profile = (
        load_family_profile(args.family_config)
        if args.family_config is not None
        else None
    )
    if domain_requested:
        assert profile is not None
        hmmer = (
            load_hmmer_config(args.hmmer_config)
            if args.hmmer_config is not None
            else None
        )
        interpro = (
            load_interpro_config(args.interpro_config)
            if args.interpro_config is not None
            else None
        )
        stages.append(DomainsStage(profile, hmmer=hmmer, interpro=interpro))
    if args.motifs:
        assert profile is not None
        stages.append(MotifsStage(profile))
    if args.foldseek_config is not None and args.structures_config is None:
        raise EnzyNotationError(
            "--structures-config is required with --foldseek-config"
        )
    if args.tmalign_config is not None and args.foldseek_config is None:
        raise EnzyNotationError("--foldseek-config is required with --tmalign-config")
    foldseek_config = None
    if args.structures_config is not None:
        structures_config = load_structures_config(args.structures_config)
        stages.append(StructuresStage(structures_config))
    if args.foldseek_config is not None:
        foldseek_config = load_foldseek_config(args.foldseek_config)
        stages.append(FoldseekStage(foldseek_config))
    if args.tmalign_config is not None:
        assert foldseek_config is not None
        tmalign_config = load_tmalign_config(args.tmalign_config)
        stages.append(TMAlignStage(tmalign_config, foldseek=foldseek_config))
    workflow = Workflow(stages) if len(stages) > 1 else None
    result = runner.run(
        args.input,
        workflow=workflow,
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
