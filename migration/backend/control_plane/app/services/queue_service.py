import json
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.shared.config.logging import logger

class QueueService:
    def publish_chunk(self, job_id: str, table_id: str, chunk_id: str, priority: int = 1):
        message = {
            "job_id": job_id,
            "table_id": table_id,
            "chunk_id": chunk_id,
            "priority": priority
        }
        logger.info("Publishing chunk to queue", chunk_id=chunk_id)
        redis_client.lpush(Queues.MIGRATION_QUEUE, json.dumps(message))

    def publish_retry(self, job_id: str, table_id: str, chunk_id: str):
        message = {
            "job_id": job_id,
            "table_id": table_id,
            "chunk_id": chunk_id,
            "priority": 1
        }
        logger.info("Publishing chunk to retry queue", chunk_id=chunk_id)
        redis_client.lpush(Queues.RETRY_QUEUE, json.dumps(message))

    def consume(self, queue_name: str):
        # A simple pop mechanism for testing/validation
        item = redis_client.rpop(queue_name)
        if item:
            return json.loads(item)
        return None

    def ack(self, queue_name: str, message_id: str):
        # Redis lists don't need manual ack if popped properly or if using stream/consumer groups
        pass

    def requeue(self, queue_name: str, message: dict):
        redis_client.lpush(queue_name, json.dumps(message))
