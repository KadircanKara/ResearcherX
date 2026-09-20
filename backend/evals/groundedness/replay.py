"""Generation cache: answer once, judge many times.

WHY THIS EXISTS. Answering all 42 cases costs ~$0.52 and judging them on
gpt-4.1 costs ~$2.30, so the two questions this harness gets asked have very
different prices:

  "did the system get better?"   -> needs new answers. Generate.
  "is a cheaper judge as good?"  -> needs the SAME answers, judged twice.

Replaying a saved generation makes the second question cost only judge tokens,
and -- more importantly than the money -- makes it a CONTROLLED comparison.
Two judges run against freshly generated answers are grading different text,
because generation is sampled: a disagreement between them would be
inseparable from the models having answered differently. Byte-identical input
is the whole point.

The cache is a plain JSON file, not a database table: it is a measurement
artifact belonging to one run, and every harness here writes nothing to the
live database.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.chat_agent import ChunkContext
from evals.groundedness.generate import Generated

# Bumped when `Generated` changes shape. A silently-mismatched replay would
# judge answers against a catalog that never produced them.
CACHE_VERSION = 1


def save_generations(
    path: Path, generations: dict[str, Generated], *, answering_model: str, project_id: str
) -> None:
    payload = {
        "version": CACHE_VERSION,
        "answering_model": answering_model,
        "project_id": project_id,
        "generations": {
            case_id: {
                "question": g.question,
                "answer": g.answer,
                "chunks": [
                    {
                        "n": c.n,
                        "paper_id": c.paper_id,
                        "title": c.title,
                        "chunk_index": c.chunk_index,
                        "text": c.text,
                    }
                    for c in g.chunks
                ],
                "citations": list(g.citations),
                "scope_source": g.scope_source,
                "scoped_titles": list(g.scoped_titles),
                "widened": g.widened,
                "stripped_markers": g.stripped_markers,
            }
            for case_id, g in generations.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2))


def load_generations(path: Path) -> tuple[dict[str, Generated], str]:
    """Returns (generations by case id, the model that produced them).

    Raises on a version mismatch rather than best-effort parsing: a replay that
    silently dropped a field would judge answers against a catalog that is not
    the one the model saw, and every number would be wrong in a way nothing
    reports.
    """
    payload = json.loads(Path(path).read_text())
    version = payload.get("version")
    if version != CACHE_VERSION:
        raise ValueError(
            f"{path}: generation cache version {version!r}, this build expects {CACHE_VERSION}. "
            "Re-generate rather than replaying a cache of a different shape."
        )
    generations = {
        case_id: Generated(
            question=entry["question"],
            answer=entry["answer"],
            chunks=tuple(ChunkContext(**chunk) for chunk in entry["chunks"]),
            citations=tuple(entry["citations"]),
            scope_source=entry["scope_source"],
            scoped_titles=tuple(entry["scoped_titles"]),
            widened=entry["widened"],
            stripped_markers=entry["stripped_markers"],
        )
        for case_id, entry in payload["generations"].items()
    }
    return generations, payload.get("answering_model", "unknown")
