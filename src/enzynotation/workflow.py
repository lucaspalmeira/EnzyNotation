"""Stage dependency graph definition and deterministic ordering."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from enzynotation.stages.base import Stage


class WorkflowDefinitionError(ValueError):
    """Raised when a workflow graph is ambiguous or invalid."""


class Workflow:
    """A validated directed acyclic graph of pipeline stages."""

    def __init__(self, stages: Iterable[Stage]) -> None:
        ordered_input = tuple(stages)
        self._stages: dict[str, Stage] = {}
        for stage in ordered_input:
            if stage.stage_id in self._stages:
                raise WorkflowDefinitionError(
                    f"Duplicate stage identifier: {stage.stage_id}"
                )
            self._stages[stage.stage_id] = stage
        self._ordered = self._topological_order(ordered_input)

    @classmethod
    def validation_only(cls) -> Workflow:
        """Return the Milestone 2 workflow containing only FASTA validation."""

        from enzynotation.stages.validate import ValidationStage

        return cls([ValidationStage()])

    def _topological_order(self, stages: tuple[Stage, ...]) -> tuple[Stage, ...]:
        for stage in stages:
            missing = [
                dependency
                for dependency in stage.dependencies
                if dependency not in self._stages
            ]
            if missing:
                rendered = ", ".join(missing)
                raise WorkflowDefinitionError(
                    f"Stage {stage.stage_id!r} has unknown dependencies: {rendered}"
                )

        result: list[Stage] = []
        permanent: set[str] = set()
        temporary: set[str] = set()

        def visit(stage: Stage) -> None:
            if stage.stage_id in permanent:
                return
            if stage.stage_id in temporary:
                raise WorkflowDefinitionError(
                    f"Workflow contains a dependency cycle at {stage.stage_id!r}"
                )
            temporary.add(stage.stage_id)
            for dependency in stage.dependencies:
                visit(self._stages[dependency])
            temporary.remove(stage.stage_id)
            permanent.add(stage.stage_id)
            result.append(stage)

        for stage in stages:
            visit(stage)
        return tuple(result)

    @property
    def stages(self) -> tuple[Stage, ...]:
        """Stages in deterministic dependency order."""

        return self._ordered

    def __iter__(self) -> Iterator[Stage]:
        return iter(self._ordered)

    def __len__(self) -> int:
        return len(self._ordered)
