# caching.py
from prometheus_client import Counter

CACHE_HITS = Counter('cache_hits_total', 'Cache hit count', ['function'])
CACHE_MISSES = Counter('cache_misses_total', 'Cache miss count', ['function'])

class CacheWithMetrics:
    """Base cache decorator with metrics tracking"""
    def __init__(self, func):
        self.func = func
        self.name = func.__name__
        self.cache = {}
        self.hits = 0
        self.misses = 0

    def __call__(self, *args, **kwargs):
        key = self._make_key(args, kwargs)
        if key in self.cache:
            self.hits += 1
            CACHE_HITS.labels(function=self.name).inc()
            return self.cache[key]

        self.misses += 1
        CACHE_MISSES.labels(function=self.name).inc()
        result = self.func(*args, **kwargs)
        self.cache[key] = result
        return result

    def _make_key(self, args, kwargs):
        return args + tuple(sorted(kwargs.items()))

    def cache_info(self):
        return {
            'hits': self.hits,
            'misses': self.misses,
            'maxsize': None,
            'currsize': len(self.cache)
        }

    def cache_clear(self):
        self.cache.clear()
        self.hits = 0
        self.misses = 0

class HashableCacheWithMetrics(CacheWithMetrics):
    """Cache decorator that handles unhashable types"""
    def _make_key(self, args, kwargs):
        def make_hashable(obj):
            if isinstance(obj, dict):
                return tuple(sorted((k, make_hashable(v)) for k, v in obj.items()))
            elif isinstance(obj, (list, tuple)):
                return tuple(make_hashable(x) for x in obj)
            elif isinstance(obj, set):
                return tuple(sorted(make_hashable(x) for x in obj))
            return obj
        hashable_args = tuple(make_hashable(arg) for arg in args)
        hashable_kwargs = tuple(sorted((k, make_hashable(v)) for k, v in kwargs.items()))
        return (hashable_args, hashable_kwargs)
