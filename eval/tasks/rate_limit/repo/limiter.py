from collections import defaultdict, deque

REQUESTS_PER_WINDOW = 100
WINDOW_SECONDS = 3600


class UploadLimiter:
    def __init__(self):
        self._events = defaultdict(deque)

    def allow(self, user_id: str, now: float) -> bool:
        events = self._events[user_id]
        cutoff = now - WINDOW_SECONDS
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= REQUESTS_PER_WINDOW:
            return False
        events.append(now)
        return True
