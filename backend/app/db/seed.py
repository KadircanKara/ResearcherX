from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project, ProjectMember, Role, User

SEED_USERS = [
    {"email": "you@researcherx.dev", "name": "You", "avatar_color": "#2D3FE0"},
    {"email": "amelia@lab.io", "name": "Amelia Chen", "avatar_color": "#E0457F"},
    {"email": "marco@lab.io", "name": "Marco Rossi", "avatar_color": "#1FAE6B"},
]


async def seed_users(db: AsyncSession) -> None:
    """Create the demo users if absent. Idempotent (keyed on email)."""
    existing = {e for (e,) in await db.execute(select(User.email))}
    for spec in SEED_USERS:
        if spec["email"] not in existing:
            db.add(User(**spec))
    await db.flush()


SEED_PROJECTS = [
    {
        "title": "Multi-UAV Coordination",
        "description": (
            "Survey of decentralized control and learning approaches"
            " for cooperative drone swarms."
        ),
        "topic_keywords": [
            "decentralized coordination",
            "multi-agent RL",
            "communication-efficient policies for UAV swarms",
        ],
        "members": [
            {"email": "you@researcherx.dev", "role": Role.OWNER},
            {"email": "amelia@lab.io", "role": Role.EDITOR},
            {"email": "marco@lab.io", "role": Role.VIEWER},
        ],
    },
    {
        "title": "Aerial Computer Vision",
        "description": (
            "Object detection and semantic segmentation for aerial and"
            " satellite imagery."
        ),
        "topic_keywords": [
            "aerial image segmentation",
            "remote sensing deep learning",
            "small object detection UAV",
        ],
        "members": [
            {"email": "you@researcherx.dev", "role": Role.OWNER},
            {"email": "amelia@lab.io", "role": Role.EDITOR},
        ],
    },
    {
        "title": "LLM Alignment Reading Group",
        "description": (
            "Curated papers on RLHF, constitutional AI, and scalable"
            " oversight for large language models."
        ),
        "topic_keywords": [
            "RLHF",
            "constitutional AI",
            "scalable oversight",
        ],
        "members": [
            {"email": "you@researcherx.dev", "role": Role.OWNER},
            {"email": "amelia@lab.io", "role": Role.EDITOR},
            {"email": "marco@lab.io", "role": Role.VIEWER},
        ],
    },
]


async def seed_projects(db: AsyncSession) -> None:
    """Create the three demo projects if absent. Idempotent (keyed on title)."""
    existing_titles = {t for (t,) in await db.execute(select(Project.title))}

    user_map: dict[str, str] = {}
    rows = await db.execute(select(User.email, User.id))
    for email, uid in rows:
        user_map[email] = uid

    for spec in SEED_PROJECTS:
        if spec["title"] in existing_titles:
            continue

        owner_email = next(
            m["email"] for m in spec["members"] if m["role"] == Role.OWNER
        )
        project = Project(
            owner_id=user_map[owner_email],
            title=spec["title"],
            description=spec["description"],
            topic_keywords=spec["topic_keywords"],
        )
        db.add(project)
        await db.flush()

        existing_members: set[str] = set()
        for member_spec in spec["members"]:
            uid = user_map[member_spec["email"]]
            if uid in existing_members:
                continue
            db.add(
                ProjectMember(
                    project_id=project.id,
                    user_id=uid,
                    role=member_spec["role"],
                )
            )
            existing_members.add(uid)
        await db.flush()
