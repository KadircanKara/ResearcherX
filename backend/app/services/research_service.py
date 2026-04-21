import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.critic import CriticAgent, CriticInput
from app.agents.planner import PlannerAgent, PlannerInput
from app.agents.searcher import SearcherAgent, SearcherInput
from app.agents.synthesizer import SynthesizerAgent, SynthesizerInput
from app.core.config import settings
from app.core.logging import log
from app.db.models import AgentStep, ResearchRun, RunStatus, StepKind
from app.db.session import SessionLocal
from app.schemas.research import SearchFinding
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
        except Exception as exc:  # noqa: BLE001
            log.exception("research_run_failed", run_id=run_id)
            async with SessionLocal() as db:
                run = await db.get(ResearchRun, run_id)
                if run:
                    run.status = RunStatus.FAILED
                    run.error = str(exc)
                    await db.commit()
            await bus.publish(run_id, {"type": "error", "message": str(exc)})
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
        await self._record_step(run_id, StepKind.PLAN, "planner", {"question": question}, plan.model_dump())
        await bus.publish(run_id, {"type": "plan", "plan": plan.model_dump()})

        # 2. Fan-out search (bounded concurrency)
        sem = asyncio.Semaphore(settings.max_parallel_searchers)

        async def search_one(q: str) -> SearchFinding:
            async with sem:
                await bus.publish(run_id, {"type": "agent_start", "agent": "searcher", "query": q})
                agent = SearcherAgent()
                finding = await agent.run(SearcherInput(query=q))
                await self._record_step(
                    run_id, StepKind.SEARCH, "searcher", {"query": q}, finding.model_dump()
                )
                await bus.publish(
                    run_id, {"type": "finding", "finding": finding.model_dump()}
                )
                return finding

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

    async def _record_step(
        self,
        run_id: str,
        kind: StepKind,
        agent_name: str,
        input_: dict,
        output: dict,
    ) -> None:
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
