import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production")
os.environ.setdefault("CODEVECTOR_API_KEY", "codevector-test-not-a-real-key")
os.environ.setdefault("CODEVECTOR_BASE_URL", "https://codevector.example.invalid/v1")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
