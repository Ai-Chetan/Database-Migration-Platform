from typing import List, Dict, Any

def calculate_chunk_size(total_rows: int, desired_chunks: int = None) -> int:
    if desired_chunks and desired_chunks > 0:
        return max(1, total_rows // desired_chunks)
    return 100000

def generate_pk_chunks(min_pk: int, max_pk: int, chunk_size: int) -> List[tuple]:
    chunks = []
    current = min_pk
    while current <= max_pk:
        end = min(current + chunk_size - 1, max_pk)
        chunks.append((current, end))
        current += chunk_size
    return chunks

def generate_offset_chunks(total_rows: int, chunk_size: int) -> List[tuple]:
    chunks = []
    for offset in range(0, total_rows, chunk_size):
        chunks.append((offset, chunk_size))
    return chunks

def estimate_chunk_count(total_rows: int, chunk_size: int) -> int:
    import math
    if chunk_size <= 0:
        return 0
    return math.ceil(total_rows / chunk_size)
