import redis
from backend.shared.config.settings import settings
from backend.shared.exceptions.base import PlatformException

class RedisManager:
    def __init__(self):
        self.pool = redis.ConnectionPool(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
            max_connections=50,
            health_check_interval=30
        )
        self.client = redis.Redis(connection_pool=self.pool)

    def get_client(self) -> redis.Redis:
        return self.client

    def ping(self) -> bool:
        try:
            return self.client.ping()
        except redis.RedisError as e:
            raise PlatformException(
                error_code="REDIS_CONNECTION_FAILED",
                message="Failed to connect to Redis",
                http_status=500,
                details={"error": str(e)}
            )

redis_manager = RedisManager()
redis_client = redis_manager.get_client()
