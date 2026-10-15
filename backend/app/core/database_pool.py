import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
import logging
from ..config import settings

logger = logging.getLogger(__name__)

class DatabasePool:
    def __init__(self):
        self.engine = None
        self.session_factory = None

    async def initialize(self):
        """Initialize database connection pool"""
        if self.session_factory:
            return  # Already initialized - reuse the same engine across requests

        try:
            # Create async engine with connection pooling.
            # Built from DATABASE_URL (the settings.supabase_db_* fields it used to read don't exist,
            # so the pool never came up and every request silently fell back to mock revenue).
            database_url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1)

            # No explicit poolclass: QueuePool can't be used with an asyncio engine,
            # the default AsyncAdaptedQueuePool is the async equivalent.
            self.engine = create_async_engine(
                database_url,
                pool_size=settings.database_pool_size,  # Number of connections to maintain
                max_overflow=settings.database_max_overflow,  # Additional connections when needed
                pool_pre_ping=True,  # Validate connections
                pool_recycle=settings.database_pool_recycle,  # Recycle connections every hour
                echo=False  # Set to True for SQL debugging
            )
            
            self.session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
            
            logger.info("✅ Database connection pool initialized")
            
        except Exception as e:
            logger.error(f"❌ Database pool initialization failed: {e}")
            self.engine = None
            self.session_factory = None
    
    async def close(self):
        """Close database connections"""
        if self.engine:
            await self.engine.dispose()
    
    def get_session(self) -> AsyncSession:
        """Get database session from pool (use as `async with db_pool.get_session() as session`)"""
        if not self.session_factory:
            raise Exception("Database pool not initialized")
        return self.session_factory()

# Global database pool instance
db_pool = DatabasePool()

async def get_db_session() -> AsyncSession:
    """Dependency to get database session"""
    async with db_pool.get_session() as session:
        yield session
