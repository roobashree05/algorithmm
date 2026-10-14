def exponential_backoff_seconds(attempt: int, base: float, cap: float) -> float:
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    return min(cap, base * (2 ** (attempt - 1)))
