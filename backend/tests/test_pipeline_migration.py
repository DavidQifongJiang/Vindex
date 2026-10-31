import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.schema import CreateSchema, DropSchema


@unittest.skipUnless(os.getenv("VINDEX_TEST_DATABASE_URL"), "Set VINDEX_TEST_DATABASE_URL for migration tests")
class PipelineMigrationTest(unittest.TestCase):
    def test_upgrade_preserves_data_and_recovers_inflight_work(self):
        url = os.environ["VINDEX_TEST_DATABASE_URL"]
        schema = "vindex_migration_" + uuid4().hex
        root = create_engine(url)
        with root.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        try:
            with patch("app.db.session.get_engine", return_value=engine):
                config = Config("alembic.ini")
                command.upgrade(config, "20260822_0003")
                with engine.begin() as connection:
                    connection.execute(text("INSERT INTO videos (video_id, status, embedding_status) VALUES ('active', 'processing', 'processing'), ('done', 'processed', 'completed')"))
                    connection.execute(text("INSERT INTO video_search_chunks (video_id, chunk_index, status, processing_attempt_count) VALUES ('active', 0, 'processing', 1)"))
                command.upgrade(config, "head")
                from app.db.models import Base
                inspector = inspect(engine)
                for table in Base.metadata.sorted_tables:
                    self.assertEqual(
                        {column.name for column in table.columns},
                        {column["name"] for column in inspector.get_columns(table.name)},
                        table.name,
                    )
                with engine.connect() as connection:
                    self.assertEqual(set(connection.execute(text("SELECT video_id, stage FROM pipeline_jobs")).all()), {("active", "search"), ("active", "playback")})
                    chunk = connection.execute(text("SELECT status, processing_attempt_count, next_retry_at, processing_token FROM video_search_chunks")).one()
                    self.assertEqual(chunk[:2], ("retrying", 1))
                    self.assertIsNotNone(chunk[2])
                    self.assertIsNone(chunk[3])
                    self.assertEqual(connection.execute(text("SELECT count(*) FROM videos")).scalar(), 2)
                command.downgrade(config, "20260822_0003")
                command.upgrade(config, "head")
        finally:
            engine.dispose()
            with root.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            root.dispose()
