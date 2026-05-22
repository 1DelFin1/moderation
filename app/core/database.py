from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import settings


engine = create_async_engine(
    str(settings.POSTGRES_URL_ASYNC),
    echo=settings.ECHO,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass
