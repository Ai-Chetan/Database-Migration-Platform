import bcrypt

def hash_data(data: str) -> str:
    import hashlib
    return hashlib.sha256(data.encode()).hexdigest()
