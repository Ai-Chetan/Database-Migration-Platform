from prometheus_client import Counter, Histogram

rows_processed = Counter('rows_processed_total', 'Total number of rows processed by worker')
chunks_completed = Counter('chunks_completed_total', 'Total number of chunks successfully completed')
chunk_duration = Histogram('chunk_duration_seconds', 'Time spent processing a chunk')
retry_count = Counter('retry_count_total', 'Total number of retries triggered')
worker_failures = Counter('worker_failures_total', 'Total number of worker failures')

class MetricsHelper:
    @staticmethod
    def inc_rows_processed(count: int = 1):
        rows_processed.inc(count)

    @staticmethod
    def inc_chunks_completed():
        chunks_completed.inc()

    @staticmethod
    def observe_chunk_duration(duration: float):
        chunk_duration.observe(duration)
    
    @staticmethod
    def inc_retry_count():
        retry_count.inc()
    
    @staticmethod
    def inc_worker_failures():
        worker_failures.inc()

metrics_helper = MetricsHelper()
