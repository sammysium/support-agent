
from typing import Annotated

from fastapi import Depends

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from src.utils.get_env import get_env_value


# e.g. postgresql+psycopg://user:password@localhost:5432/sherrif
database_url = get_env_value("DATABASE_URL")

# pre_ping: a connection Postgres dropped while idle is replaced instead of failing the request
engine = create_engine(database_url, pool_pre_ping=True)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)
    add_missing_columns()


def add_missing_columns():
    """create_all doesn't change tables that already exist: add any column a model gained since (nullable,
    with its default), so an existing database keeps working."""
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                default_sql = "" if default is None else f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {int(default) if isinstance(default, bool) else default}"
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" '
                                        f'{column.type.compile(engine.dialect)}{default_sql}'))


def get_db_session():
    with Session(engine) as session:
        yield session

SessionDep = Annotated[Session, Depends(get_db_session)]
