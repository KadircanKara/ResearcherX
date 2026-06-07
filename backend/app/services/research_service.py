import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.critic import CriticAgent, CriticInput
from app.agents.planner import PlannerAgent, PlannerInput, ValidationInput
from app.agents.searcher import SearcherAgent, SearcherInput
from app.agents.synthesizer import SynthesizerAgent, SynthesizerInput
from app.core.config import settings
from app.core.logging import log
from app.db.models import AgentStep, ResearchRun, RunStatus, StepKind
from app.db.session import SessionLocal
from app.schemas.research import FindingValidation, SearchFinding
from app.services.event_bus import bus


class ResearchService:
    def __init__(self) -> None:
        self._planner = PlannerAgent()
        self._synthesizer = SynthesizerAgent()
        self._critic = CriticAgent()

    async def create(self, db: AsyncSession, question: str) -> ResearchRun:
        run = ResearchRun(question=question)
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run

    async def get(self, db: AsyncSession, run_id: str) -> ResearchRun | None:
        stmt = (
            select(ResearchRun)
            .where(ResearchRun.id == run_id)
            .options(selectinload(ResearchRun.steps))
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    async def run_async(self, run_id: str) -> None:
        """Orchestrate the full research pipeline. Publishes events to the bus."""
        try:
            async with SessionLocal() as db:
                run = await db.get(ResearchRun, run_id)
                if run is None:
                    return
                run.status = RunStatus.RUNNING
                await db.commit()

            await bus.publish(run_id, {"type": "status", "status": "running"})
            await self._pipeline(run_id)

            async with SessionLocal() as db:
                run = await db.get(ResearchRun, run_id)
                if run:
                    run.status = RunStatus.COMPLETED
                    await db.commit()
            await bus.publish(run_id, {"type": "status", "status": "completed"})
        except asyncio.CancelledError:
            # Viewer disconnected past the grace window, or server shutdown.
            log.info("research_run_cancelled", run_id=run_id)
            async with SessionLocal() as db:
                run = await db.get(ResearchRun, run_id)
                if run and str(run.status) not in (RunStatus.COMPLETED, RunStatus.FAILED):
                    run.status = RunStatus.FAILED
                    run.error = "run cancelled"
                    await db.commit()
            raise
        except Exception:
            # Full traceback stays in server logs; clients get a generic
            # message — provider/model internals must never leak out.
            log.exception("research_run_failed", run_id=run_id)
            message = f"The research run failed. (ref: {run_id})"
            async with SessionLocal() as db:
                run = await db.get(ResearchRun, run_id)
                if run:
                    run.status = RunStatus.FAILED
                    run.error = message
                    await db.commit()
            await bus.publish(run_id, {"type": "error", "message": message})
        finally:
            await bus.close(run_id)

    async def _pipeline(self, run_id: str) -> None:
        async with SessionLocal() as db:
            run = await db.get(ResearchRun, run_id)
            assert run is not None
            question = run.question

        # 1. Plan
        await bus.publish(run_id, {"type": "agent_start", "agent": "planner"})
        plan = await self._planner.run(PlannerInput(question=question))
        await self._record_step(
            run_id, StepKind.PLAN, "planner", {"question": question}, plan.model_dump()
        )
        await bus.publish(run_id, {"type": "plan", "plan": plan.model_dump()})

        # 2. Fan-out search (bounded concurrency). Each sub-query runs a
        # search → planner-validation → retry-with-revised-query loop: the
        # planner does not pass garbage downstream. After the retry cap, the
        # best attempt is accepted and marked degraded (degrade-don't-fail).
        sem = asyncio.Semaphore(settings.max_parallel_searchers)
        max_attempts = settings.max_search_retries + 1

        async def search_one(q: str) -> SearchFinding:
            async with sem:
                current_query = q
                best: SearchFinding | None = None
                best_step_id: str | None = None
                for attempt in range(1, max_attempts + 1):
                    await bus.publish(
                        run_id,
                        {"type": "agent_start", "agent": "searcher", "query": current_query},
                    )
                    agent = SearcherAgent()
                    finding = await agent.run(SearcherInput(query=current_query))
                    finding.attempts = attempt
                    step_id = await self._record_step(
                        run_id,
                        StepKind.SEARCH,
                        "searcher",
                        {"query": current_query, "attempt": attempt},
                        finding.model_dump(),
                    )
                    best = finding
                    best_step_id = step_id

                    validation = await self._validate_finding(
                        run_id,
                        question=question,
                        query=current_query,
                        finding=finding,
                        attempt=attempt,
                        can_retry=attempt < max_attempts,
                    )

                    if validation.verdict == "valid":
                        finding.validated = True
                        await self._update_step_output(step_id, finding.model_dump())
                        await bus.publish(
                            run_id, {"type": "finding", "finding": finding.model_dump()}
                        )
                        return finding

                    revised = (validation.revised_query or "").strip()
                    if (
                        attempt < max_attempts
                        and revised
                        and revised.lower() != current_query.lower()
                    ):
                        await bus.publish(
                            run_id,
                            {
                                "type": "search_retry",
                                "old_query": current_query,
                                "new_query": revised,
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                            },
                        )
                        current_query = revised
                        continue
                    break  # no usable revision, or retry budget exhausted

                # One hopeless sub-query shouldn't fail the whole run: keep
                # the best attempt, but mark it so downstream/UI can tell.
                assert best is not None and best_step_id is not None
                best.accepted_degraded = True
                await self._update_step_output(best_step_id, best.model_dump())
                await bus.publish(run_id, {"type": "finding", "finding": best.model_dump()})
                return best

        findings = await asyncio.gather(*(search_one(q) for q in plan.sub_queries))

        # 3. Synthesize (streamed)
        await bus.publish(run_id, {"type": "agent_start", "agent": "synthesizer"})
        chunks: list[str] = []
        async for chunk in self._synthesizer.stream(
            SynthesizerInput(question=question, findings=findings)
        ):
            chunks.append(chunk)
            await bus.publish(run_id, {"type": "report_delta", "text": chunk})
        draft = "".join(chunks)
        await self._record_step(
            run_id, StepKind.SYNTHESIZE, "synthesizer", {"question": question}, {"report": draft}
        )

        # 4. Critique
        await bus.publish(run_id, {"type": "agent_start", "agent": "critic"})
        critique = await self._critic.run(
            CriticInput(question=question, draft_report=draft, findings=findings)
        )
        await self._record_step(
            run_id, StepKind.CRITIQUE, "critic", {"draft": draft}, critique.model_dump()
        )
        await bus.publish(run_id, {"type": "critique", "critique": critique.model_dump()})

        # 5. Persist final report
        async with SessionLocal() as db:
            run = await db.get(ResearchRun, run_id)
            if run:
                run.report = draft
                await db.commit()

    async def _validate_finding(
        self,
        run_id: str,
        *,
        question: str,
        query: str,
        finding: SearchFinding,
        attempt: int,
        can_retry: bool,
    ) -> FindingValidation:
        """Planner validation of one searcher finding (judge + revise in one call)."""
        empty = not finding.sources or finding.summary.strip() == "No results found."
        if empty and not can_retry:
            # Cheap pre-check: obviously garbage and no retry budget left —
            # don't burn an LLM call to learn what we already know.
            validation = FindingValidation(verdict="invalid", reasons=["empty result: no sources"])
        else:
            await bus.publish(run_id, {"type": "agent_start", "agent": "planner", "query": query})
            try:
                validation = await self._planner.validate(
                    ValidationInput(question=question, sub_query=query, finding=finding)
                )
            except Exception as exc:  # noqa: BLE001
                # Fail-open: a flaky validator must not make the pipeline less
                # robust than it was without validation. Exception detail goes
                # to the log only — reasons are client-visible (SSE + steps).
                log.warning("validation_degraded", query=query, error=str(exc))
                validation = FindingValidation(verdict="valid", reasons=["validator unavailable"])
            if empty and validation.verdict == "valid":
                # An empty finding is garbage by definition; keep the planner's
                # revised query (if any) but never accept it as valid.
                validation.verdict = "invalid"
                validation.reasons = ["empty result: no sources", *validation.reasons]

        await self._record_step(
            run_id,
            StepKind.VALIDATE,
            "planner",
            {"question": question, "sub_query": query, "attempt": attempt},
            validation.model_dump(),
        )
        await bus.publish(
            run_id,
            {
                "type": "validation",
                "query": query,
                "verdict": validation.verdict,
                "reasons": validation.reasons,
                "attempt": attempt,
            },
        )
        return validation

    async def _record_step(
        self,
        run_id: str,
        kind: StepKind,
        agent_name: str,
        input_: dict,
        output: dict,
    ) -> str:
        async with SessionLocal() as db:
            step = AgentStep(
                run_id=run_id,
                kind=kind,
                agent_name=agent_name,
                input=input_,
                output=output,
            )
            db.add(step)
            await db.commit()
            return step.id

    async def _update_step_output(self, step_id: str, output: dict) -> None:
        """Finalize a recorded step's output after the fact.

        Search steps are recorded before validation, so the accepted step's
        validated/accepted_degraded flags are only known later — and the UI
        seeds its findings from recorded steps, so they must end up accurate.
        """
        async with SessionLocal() as db:
            step = await db.get(AgentStep, step_id)
            if step:
                step.output = output
                await db.commit()
