"""Command-line interface for EnzyNotation."""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Sequence
from pathlib import Path

from enzynotation import __version__
from enzynotation.backends.slurm import SlurmBackend
from enzynotation.config import load_config
from enzynotation.databases import (
    build_database_manifest,
    load_database_registry,
    manifest_has_required_failures,
    write_database_manifest,
)
from enzynotation.exceptions import EnzyNotationError
from enzynotation.family import load_family_profile
from enzynotation.fasta import (
    FastaValidationOptions,
    validate_fasta,
    write_normalized_fasta,
)
from enzynotation.integration import load_integration_config
from enzynotation.logging_utils import configure_logging
from enzynotation.paths import RunPaths
from enzynotation.provenance import create_run_id, utc_now
from enzynotation.reporting import load_report_config
from enzynotation.rules import load_ec_rules
from enzynotation.runner import PipelineRunner
from enzynotation.slurm import build_slurm_plan, load_slurm_config, write_slurm_plan
from enzynotation.stages.blast import BlastStage
from enzynotation.stages.clean import CleanStage
from enzynotation.stages.domains import DomainsStage
from enzynotation.stages.foldseek import FoldseekStage
from enzynotation.stages.integrate import IntegrateStage
from enzynotation.stages.motifs import MotifsStage
from enzynotation.stages.report import ReportStage
from enzynotation.stages.structures import StructuresStage
from enzynotation.stages.tmalign import TMAlignStage
from enzynotation.stages.validate import ValidationStage
from enzynotation.state import RunStatus, StageStatus
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

    databases = subparsers.add_parser(
        "databases",
        help="verify external scientific resources without downloading them",
    )
    database_actions = databases.add_subparsers(
        dest="database_action",
        required=True,
    )

    def add_database_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--config",
            type=Path,
            default=Path("configs/databases.yaml"),
            metavar="PATH",
            help="external resource registry YAML",
        )
        command.add_argument(
            "--database-root",
            type=Path,
            metavar="PATH",
            help="override ENZYNOTATION_DB_ROOT for this verification",
        )
        command.add_argument(
            "--model-root",
            type=Path,
            metavar="PATH",
            help="override ENZYNOTATION_MODEL_ROOT for this verification",
        )

    verify_databases = database_actions.add_parser(
        "verify",
        help="print a deterministic resource verification manifest",
    )
    add_database_options(verify_databases)
    database_manifest = database_actions.add_parser(
        "manifest",
        help="write a deterministic resource verification manifest",
    )
    add_database_options(database_manifest)
    database_manifest.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="PATH",
        help="manifest JSON destination outside read-only resource roots",
    )

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
        "--backend",
        choices=("local", "slurm"),
        default="local",
        help="workflow execution backend (default: local)",
    )
    run.add_argument(
        "--slurm-config",
        type=Path,
        default=Path("configs/slurm/default.yaml"),
        metavar="PATH",
        help="Slurm resources and submission configuration",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="render the Slurm DAG and sbatch argv without submission",
    )
    run.add_argument("--stage-id", help=argparse.SUPPRESS)
    run.add_argument("--slurm-worker", action="store_true", help=argparse.SUPPRESS)
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
        help="family profile used by domain, motif, and integration stages",
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
    run.add_argument(
        "--integration-config",
        type=Path,
        metavar="PATH",
        help="enable final evidence integration with this YAML policy",
    )
    run.add_argument(
        "--confidence-config",
        type=Path,
        metavar="PATH",
        help="override the confidence policy referenced by integration config",
    )
    run.add_argument(
        "--ec-rules",
        type=Path,
        metavar="PATH",
        help="optional EC-specific declarative rules for integration",
    )
    run.add_argument(
        "--report",
        action="store_true",
        help="generate final TSV, JSON, and static HTML reports after integration",
    )
    run.add_argument(
        "--report-config",
        type=Path,
        metavar="PATH",
        help="presentation-only report configuration used with --report",
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


def _build_workflow(args: argparse.Namespace) -> Workflow:
    """Construct the canonical stage DAG shared by local and Slurm execution."""

    stages = [ValidationStage()]
    if args.blast_config is not None:
        blast_config = load_blast_config(args.blast_config)
        stages.append(BlastStage(blast_config))
    if args.clean_config is not None:
        clean_config = load_clean_config(args.clean_config)
        stages.append(CleanStage(clean_config))
    domain_requested = args.hmmer_config is not None or args.interpro_config is not None
    family_required = domain_requested or args.motifs
    if family_required and args.family_config is None:
        raise EnzyNotationError(
            "--family-config is required with domain or motif evidence"
        )
    if args.family_config is not None and not (
        family_required or args.integration_config is not None
    ):
        raise EnzyNotationError(
            "--family-config requires a domain, motif, or integration stage"
        )
    if args.confidence_config is not None and args.integration_config is None:
        raise EnzyNotationError("--confidence-config requires --integration-config")
    if args.ec_rules is not None and args.integration_config is None:
        raise EnzyNotationError("--ec-rules requires --integration-config")
    if args.report and args.integration_config is None:
        raise EnzyNotationError("--report requires --integration-config")
    if args.report_config is not None and not args.report:
        raise EnzyNotationError("--report-config requires --report")
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
    if args.integration_config is not None:
        integration_config = load_integration_config(
            args.integration_config,
            confidence_path=args.confidence_config,
        )
        ec_rules = (
            load_ec_rules(
                args.ec_rules,
                schema_path=Path("configs/schema/ec-rules.schema.json"),
            )
            if args.ec_rules is not None
            else None
        )
        stages.append(
            IntegrateStage(
                integration_config,
                ec_rules=ec_rules,
                family_profile=profile,
            )
        )
    if args.report:
        stages.append(ReportStage(load_report_config(args.report_config)))
    return Workflow(stages)


def _worker_argv(args: argparse.Namespace, run_id: str) -> tuple[str, ...]:
    """Reconstruct a safe local worker invocation from parsed run options."""

    argv = [
        "enzynotation",
        "run",
        str(args.input),
        "--run-id",
        run_id,
        "--results-dir",
        str(args.results_dir),
        "--logs-dir",
        str(args.logs_dir),
        "--backend",
        "local",
    ]
    for config in args.config:
        argv.extend(("--config", str(config)))
    valued = (
        ("--blast-config", args.blast_config),
        ("--clean-config", args.clean_config),
        ("--family-config", args.family_config),
        ("--hmmer-config", args.hmmer_config),
        ("--interpro-config", args.interpro_config),
        ("--structures-config", args.structures_config),
        ("--foldseek-config", args.foldseek_config),
        ("--tmalign-config", args.tmalign_config),
        ("--integration-config", args.integration_config),
        ("--confidence-config", args.confidence_config),
        ("--ec-rules", args.ec_rules),
        ("--report-config", args.report_config),
    )
    for option, value in valued:
        if value is not None:
            argv.extend((option, str(value)))
    if args.motifs:
        argv.append("--motifs")
    if args.report:
        argv.append("--report")
    return tuple(argv)


def _run_slurm(args: argparse.Namespace, workflow: Workflow) -> int:
    """Plan or submit the existing workflow without executing science locally."""

    slurm_config = load_slurm_config(args.slurm_config)
    backend = SlurmBackend(slurm_config)
    run_id = args.run_id or create_run_id()
    paths = RunPaths(args.results_dir, args.logs_dir, run_id)
    plans = build_slurm_plan(workflow, slurm_config, paths)
    worker_argv = _worker_argv(args, run_id)
    synthetic_ids = {
        plan.stage_id: str(100000 + index) for index, plan in enumerate(plans, start=1)
    }
    rendered_jobs = []
    for plan in plans:
        afterok = tuple(synthetic_ids[stage] for stage in plan.afterok_stages)
        afterany = tuple(synthetic_ids[stage] for stage in plan.afterany_stages)
        argv = backend.build_sbatch_argv(
            plan,
            worker_argv,
            afterok_job_ids=afterok,
            afterany_job_ids=afterany,
        )
        rendered_jobs.append(
            {
                "stage_id": plan.stage_id,
                "required": plan.required,
                "scheduler_state": "planned",
                "afterok_stages": list(plan.afterok_stages),
                "afterany_stages": list(plan.afterany_stages),
                "resolved_resources": plan.resources.to_dict(),
                "stdout": str(plan.stdout_pattern),
                "stderr": str(plan.stderr_pattern),
                "sbatch_command": list(argv),
            }
        )
    document: dict[str, object] = {
        "schema_version": 1,
        "run_id": run_id,
        "backend": "slurm",
        "dry_run": args.dry_run,
        "generated_at": utc_now(),
        "configuration": str(slurm_config.source_path),
        "worker_command": list(worker_argv),
        "jobs": rendered_jobs,
        "submissions": [],
    }
    if args.dry_run:
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0

    paths.prepare()
    plan_path = paths.run_root / "slurm" / "plan.json"
    write_slurm_plan(plan_path, document)
    job_ids: dict[str, str] = {}
    submissions = []
    for plan in plans:
        argv = backend.build_sbatch_argv(
            plan,
            worker_argv,
            afterok_job_ids=tuple(job_ids[stage] for stage in plan.afterok_stages),
            afterany_job_ids=tuple(job_ids[stage] for stage in plan.afterany_stages),
        )
        submission = backend.submit(argv, plan)
        job_ids[plan.stage_id] = submission.job_id
        submissions.append(submission.to_dict())
        document["submissions"] = submissions
        write_slurm_plan(plan_path, document)
    print(f"RUN SUBMITTED: {run_id}")
    print(f"Slurm plan: {plan_path}")
    for stage_id, job_id in job_ids.items():
        print(f"{stage_id}: {job_id}")
    return 0


def _run_pipeline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    logging_config = config.section("logging")
    configure_logging(str(logging_config["level"]))
    workflow = _build_workflow(args)
    if args.dry_run and args.backend != "slurm":
        raise EnzyNotationError("--dry-run is available only with --backend slurm")
    if args.stage_id is not None and args.backend != "local":
        raise EnzyNotationError("scheduled stage workers must use --backend local")
    if args.backend == "slurm":
        return _run_slurm(args, workflow)
    runner = PipelineRunner(
        config,
        results_root=args.results_dir,
        logs_root=args.logs_dir,
    )
    if args.stage_id is not None:
        if args.run_id is None:
            raise EnzyNotationError("--stage-id requires --run-id")
        result = runner.run_stage(
            args.input,
            workflow=workflow,
            stage_id=args.stage_id,
            run_id=args.run_id,
            resume=not args.no_resume,
        )
        outcome = result.stage_outcomes.get(args.stage_id)
        print(f"STAGE {args.stage_id} {outcome.value.upper()}: {result.run_id}")
        return 0 if outcome in {StageStatus.COMPLETED, StageStatus.SKIPPED} else 1
    result = runner.run(
        args.input,
        workflow=workflow if len(workflow) > 1 else None,
        run_id=args.run_id,
        resume=not args.no_resume,
    )
    print(f"RUN {result.status.value.upper()}: {result.run_id}")
    print(f"Manifest: {result.manifest_path}")
    return 0 if result.status is not RunStatus.FAILED else 1


def _run_databases(args: argparse.Namespace) -> int:
    registry = load_database_registry(args.config)
    overrides = {
        root_id: value
        for root_id, value in (
            ("databases", args.database_root),
            ("models", args.model_root),
        )
        if value is not None
    }
    manifest = build_database_manifest(registry, root_overrides=overrides)
    if args.database_action == "verify":
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        write_database_manifest(args.output, manifest)
        print(f"Database manifest: {args.output.resolve()}")
    return 1 if manifest_has_required_failures(manifest) else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the EnzyNotation command-line interface."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            return _run_validate(args)
        if args.command == "databases":
            return _run_databases(args)
        if args.command == "run":
            return _run_pipeline(args)
    except EnzyNotationError as exc:
        parser.exit(2, f"enzynotation: error: {exc}\n")
    parser.error(f"unknown command: {args.command}")
    return 2
