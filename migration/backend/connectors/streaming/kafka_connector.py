"""
Kafka Streaming Connector
File: migration/backend/connectors/streaming/kafka_connector.py

Implements DatabaseConnector for Apache Kafka.

Two migration directions:
    Kafka  → Database  (consume messages, write to target DB)
    Database → Kafka   (read from source DB, produce to Kafka topic)

Design decisions vs SQL connectors:
    - No PK-range chunking (Kafka has no PKs, only offsets)
    - Uses time-window or offset-range chunking instead
    - stream_rows() implements a consumer with configurable timeout
    - bulk_insert() is a producer — sends rows as JSON messages
    - discover_schema() samples N messages and infers schema

config:
    bootstrap_servers: "localhost:9092" or "broker1:9092,broker2:9092"
    topic:             "customers"     (used as "table_name")
    group_id:          "migration-consumer-{job_id}"
    auto_offset_reset: "earliest" | "latest"
    consumer_timeout_ms: 10000    (stop after N ms of no messages)
    max_messages:      None       (None = consume all, int = stop after N)
    schema_registry_url: None     (optional for Avro/Schema Registry)
    message_format:    "json" | "avro" | "string"
    key_deserializer:  "string" | "json"

    # Producer settings (for DB → Kafka direction):
    compression:       "none" | "gzip" | "snappy" | "lz4"
    batch_size:        16384   (bytes)
    linger_ms:         5       (producer batching delay)
    acks:              "all"   (durability: "0" | "1" | "all")

Requirements:
    pip install confluent-kafka
"""

import json
import time
import uuid
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult
)
from backend.shared.config.logging import logger


class KafkaConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "kafka"

    @property
    def display_name(self) -> str:
        return f"Apache Kafka ({self.config.get('bootstrap_servers', '')})"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=False, checksum=False, constraints=False,
            indexes=False, jsonb=True, partitioning=True,
        )

    def connect(self) -> None:
        # Validate confluent_kafka is available
        try:
            from confluent_kafka import Consumer, Producer
        except ImportError:
            raise ImportError(
                "confluent-kafka package required for Kafka connector. "
                "Install with: pip install confluent-kafka"
            )
        self._running = False
        logger.info("KafkaConnector ready",
                    servers=self.config.get("bootstrap_servers"))

    def disconnect(self) -> None:
        self._running = False

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            from confluent_kafka.admin import AdminClient
            admin = AdminClient({
                "bootstrap.servers": self.config.get("bootstrap_servers", "localhost:9092")
            })
            metadata = admin.list_topics(timeout=10)
            return {
                "success":    True,
                "db_version": f"Kafka ({len(metadata.topics)} topics)",
                "latency_ms": int((time.time() - start) * 1000),
                "error":      None,
                "topic_count": len(metadata.topics),
            }
        except Exception as e:
            return {
                "success": False, "db_version": None,
                "latency_ms": int((time.time() - start) * 1000), "error": str(e),
            }

    def discover_schema(self) -> SchemaInfo:
        """
        List topics and sample N messages from each to infer schema.
        Topics are treated as tables.
        """
        try:
            from confluent_kafka.admin import AdminClient
            admin    = AdminClient({
                "bootstrap.servers": self.config.get("bootstrap_servers", "localhost:9092")
            })
            metadata = admin.list_topics(timeout=10)
            topics   = [t for t in metadata.topics.keys()
                        if not t.startswith("__")]  # skip internal topics
        except Exception as e:
            logger.warning("Kafka topic listing failed", error=str(e))
            topic = self.config.get("topic", "")
            topics = [topic] if topic else []

        tables = {}
        for topic in topics[:20]:   # Limit to 20 topics for discovery
            try:
                # Sample 10 messages to infer schema
                sample_rows = list(self._consume(topic, max_messages=10, timeout_ms=5000))
                if not sample_rows:
                    continue

                # Infer columns from first non-empty record
                sample = sample_rows[0]
                columns = {}
                for key, val in sample.items():
                    if isinstance(val, bool):    sql_type = "boolean"
                    elif isinstance(val, int):   sql_type = "bigint"
                    elif isinstance(val, float): sql_type = "double"
                    elif isinstance(val, (dict, list)): sql_type = "jsonb"
                    else:                        sql_type = "text"
                    columns[key] = {
                        "type": sql_type, "nullable": True,
                        "pk": False, "unique": False, "default": None, "extra": "",
                    }

                # Add Kafka metadata columns
                columns["_kafka_offset"]    = {"type": "bigint", "nullable": True, "pk": False, "unique": False, "default": None, "extra": ""}
                columns["_kafka_partition"] = {"type": "int",    "nullable": True, "pk": False, "unique": False, "default": None, "extra": ""}
                columns["_kafka_timestamp"] = {"type": "bigint", "nullable": True, "pk": False, "unique": False, "default": None, "extra": ""}

                tables[topic] = {
                    "columns":      columns,
                    "primary_keys": [],
                    "foreign_keys": [],
                    "indexes":      [],
                    "row_count":    self._get_topic_message_count(topic),
                    "topic":        topic,
                    "partitions":   metadata.topics[topic].partitions if topic in metadata.topics else {},
                }
            except Exception as e:
                logger.debug("Kafka topic schema failed", topic=topic, error=str(e))

        return SchemaInfo(
            database=self.config.get("bootstrap_servers", "localhost:9092"),
            engine="kafka",
            tables=tables,
        )

    def get_row_count(self, table_name: str) -> int:
        return self._get_topic_message_count(table_name)

    def get_avg_row_size(self, table_name: str) -> int:
        return 1024   # typical JSON message size

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=1000
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Consume messages from a Kafka topic.
        pk_start/pk_end are interpreted as offset range when pk_column="_kafka_offset",
        otherwise all messages are consumed up to max_messages or timeout.
        """
        use_offset_range = (pk_column == "_kafka_offset")
        max_messages     = self.config.get("max_messages")
        timeout_ms       = self.config.get("consumer_timeout_ms", 30000)

        if use_offset_range:
            max_messages = int(pk_end) - int(pk_start) + 1

        for msg in self._consume(table_name, max_messages=max_messages,
                                  timeout_ms=timeout_ms,
                                  start_offset=int(pk_start) if use_offset_range else None):
            if columns:
                msg = {k: v for k, v in msg.items() if k in columns}
            yield msg

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        """
        Produce rows to a Kafka topic as JSON messages.
        table_name becomes the topic name.
        """
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)

        from confluent_kafka import Producer, KafkaException

        start = time.time()
        conf  = {
            "bootstrap.servers": self.config.get("bootstrap_servers", "localhost:9092"),
            "compression.type":  self.config.get("compression", "none"),
            "batch.size":        self.config.get("batch_size", 16384),
            "linger.ms":         self.config.get("linger_ms", 5),
            "acks":              self.config.get("acks", "all"),
        }
        producer = Producer(conf)
        topic    = table_name
        inserted = 0
        failed   = 0
        delivery_errors = []

        def _on_delivery(err, msg):
            nonlocal inserted, failed
            if err:
                failed   += 1
                delivery_errors.append(str(err))
            else:
                inserted += 1

        for row in rows:
            # Strip Kafka metadata columns before producing
            clean = {k: v for k, v in row.items()
                     if not k.startswith("_kafka_")}
            try:
                message_key = str(row.get("id") or row.get("_id") or uuid.uuid4())
                producer.produce(
                    topic=topic,
                    key=message_key.encode(),
                    value=json.dumps(clean, default=str).encode(),
                    on_delivery=_on_delivery,
                )
                # Poll periodically to trigger delivery callbacks
                if inserted + failed > 0 and (inserted + failed) % 1000 == 0:
                    producer.poll(0)
            except KafkaException as e:
                failed += 1

        producer.flush(timeout=30)
        elapsed = int((time.time() - start) * 1000)
        return BulkWriteResult(
            inserted, 0, failed, elapsed,
            "; ".join(delivery_errors[:3]) if delivery_errors else None
        )

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        if pk_column == "_kafka_offset":
            return max(0, int(pk_end) - int(pk_start) + 1)
        return sum(1 for _ in self.stream_rows(table_name, pk_column, pk_start, pk_end))

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        import hashlib
        h = hashlib.md5()
        for row in self.stream_rows(table_name, pk_column, pk_start, pk_end,
                                     batch_size=500):
            h.update(json.dumps(row, sort_keys=True, default=str).encode())
        return h.hexdigest()[:16]

    # ── Private helpers ────────────────────────────────────────────────────────

    def _consume(
        self,
        topic:        str,
        max_messages: Optional[int] = None,
        timeout_ms:   int = 30000,
        start_offset: Optional[int] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """Core consumer loop. Yields parsed message dicts."""
        from confluent_kafka import Consumer, TopicPartition, OFFSET_BEGINNING

        group_id = self.config.get("group_id",
                                   f"migration-{uuid.uuid4().hex[:8]}")
        conf = {
            "bootstrap.servers":  self.config.get("bootstrap_servers", "localhost:9092"),
            "group.id":           group_id,
            "auto.offset.reset":  self.config.get("auto_offset_reset", "earliest"),
            "enable.auto.commit": False,
            "session.timeout.ms": 30000,
        }
        consumer  = Consumer(conf)
        fmt       = self.config.get("message_format", "json")
        count     = 0
        timeout_s = timeout_ms / 1000

        try:
            consumer.subscribe([topic])

            if start_offset is not None:
                partitions = consumer.assignment()
                for tp in partitions:
                    consumer.seek(TopicPartition(tp.topic, tp.partition, start_offset))

            while True:
                if max_messages and count >= max_messages:
                    break

                msg = consumer.poll(timeout=timeout_s)
                if msg is None:
                    break   # Timeout — no more messages

                if msg.error():
                    logger.warning("Kafka consumer error", error=str(msg.error()))
                    break

                try:
                    value = msg.value()
                    if fmt == "json":
                        row = json.loads(value.decode("utf-8"))
                    elif fmt == "string":
                        row = {"value": value.decode("utf-8")}
                    else:
                        row = {"value": value}

                    if isinstance(row, dict):
                        row["_kafka_offset"]    = msg.offset()
                        row["_kafka_partition"] = msg.partition()
                        row["_kafka_timestamp"] = msg.timestamp()[1]
                        yield row
                        count += 1

                except Exception as e:
                    logger.debug("Message parse failed", error=str(e))

        finally:
            consumer.close()

    def _get_topic_message_count(self, topic: str) -> int:
        """Estimate total messages in a topic from partition offsets."""
        try:
            from confluent_kafka import Consumer, TopicPartition
            conf = {
                "bootstrap.servers": self.config.get("bootstrap_servers", "localhost:9092"),
                "group.id":          f"migration-size-{uuid.uuid4().hex[:8]}",
            }
            consumer   = Consumer(conf)
            metadata   = consumer.list_topics(topic, timeout=10)
            topic_meta = metadata.topics.get(topic)
            if not topic_meta:
                consumer.close()
                return 0

            total = 0
            for partition_id in topic_meta.partitions:
                tp_low  = TopicPartition(topic, partition_id, -2)  # OFFSET_BEGINNING
                tp_high = TopicPartition(topic, partition_id, -1)  # OFFSET_END
                low, _  = consumer.get_watermark_offsets(tp_low,  timeout=5)
                _, high = consumer.get_watermark_offsets(tp_high, timeout=5)
                total  += max(0, high - low)

            consumer.close()
            return total
        except Exception:
            return 0
