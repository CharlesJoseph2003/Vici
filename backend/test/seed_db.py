"""
Wipe and recreate all tables, then insert a demo org, user, and empty project.

Run from the project root:
  python -m backend.test.seed_db
"""
import asyncio
import uuid

from sqlalchemy import text

from backend.db.models import Base, Organization, Project, User
from backend.db.session import AsyncSessionLocal, engine


async def main():
    async with engine.begin() as conn:
        # Drop any legacy tables that aren't in the current models
        await conn.execute(text("DROP TABLE IF EXISTS agent_messages CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS roi_runs CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS roi_analyses CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS spreadsheets CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS projects CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS organizations CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
    print("Tables recreated")

    async with AsyncSessionLocal() as session:
        org = Organization(name="Demo HVAC Co.")
        session.add(org)
        await session.flush()

        user = User(
            id=uuid.uuid4(),
            org_id=org.id,
            email="demo@example.com",
            role="admin",
        )
        session.add(user)

        project = Project(
            id=uuid.uuid4(),
            org_id=org.id,
            name="Demo Project",
        )
        session.add(project)
        await session.commit()

        print(f"Org:     {org.id}")
        print(f"User:    {user.id}  ({user.email})")
        print(f"Project: {project.id}  ({project.name})")

    async with AsyncSessionLocal() as session:
        for tbl in ["organizations", "users", "projects", "spreadsheets", "roi_runs", "agent_messages"]:
            n = (await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar()
            print(f"  {tbl}: {n}")


if __name__ == "__main__":
    asyncio.run(main())
