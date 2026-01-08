"""
Metrics collection for Prometheus format output
"""

import time
import threading
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class MetricsCollector:
    """Thread-safe metrics collector for Prometheus format"""
    
    # Histogram buckets for request duration
    DURATION_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float('inf')]
    
    def __init__(self):
        self._lock = threading.Lock()
        # API request counters: (method, endpoint, status) -> count
        self._api_request_counters: Dict[Tuple[str, str, str], int] = defaultdict(int)
        # API request durations: (method, endpoint) -> list of durations
        self._api_request_durations: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        # Memory operation counters: (operation, status) -> count
        self._memory_operation_counters: Dict[Tuple[str, str], int] = defaultdict(int)
        # Error counters: (error_type, endpoint) -> count
        self._error_counters: Dict[Tuple[str, str], int] = defaultdict(int)
        self._start_time = time.time()
        self._max_duration_samples = 10000  # Keep last 10k samples per endpoint
        
    def record_api_request(self, method: str, endpoint: str, status_code: int, duration: float):
        """Record an API request metric"""
        with self._lock:
            status = str(status_code)
            key = (method, endpoint, status)
            self._api_request_counters[key] += 1
            
            # Record duration for histogram
            duration_key = (method, endpoint)
            self._api_request_durations[duration_key].append(duration)
            
            # Keep only recent samples to avoid memory issues
            if len(self._api_request_durations[duration_key]) > self._max_duration_samples:
                self._api_request_durations[duration_key] = \
                    self._api_request_durations[duration_key][-self._max_duration_samples:]
    
    def record_memory_operation(self, operation: str, status: str):
        """Record a memory operation (create, search, etc.)"""
        with self._lock:
            key = (operation, status)
            self._memory_operation_counters[key] += 1
    
    def record_error(self, error_type: str, endpoint: str):
        """Record an error"""
        with self._lock:
            key = (error_type, endpoint)
            self._error_counters[key] += 1
    
    def normalize_endpoint(self, path: str) -> str:
        """Normalize path to endpoint format (remove IDs, etc.)"""
        import re
        # Replace UUIDs
        path = re.sub(
            r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
            '{id}',
            path,
            flags=re.IGNORECASE
        )
        # Replace numeric IDs in path segments
        path = re.sub(r'/\d+/', '/{id}/', path)
        # Remove trailing ID if present
        path = re.sub(r'/\d+$', '/{id}', path)
        # Ensure it starts with /api/v1 if it's an API path
        if not path.startswith('/api/v1'):
            # Try to extract API path
            if '/api/v1' in path:
                path = '/api/v1' + path.split('/api/v1', 1)[1]
        return path
    
    def _calculate_histogram_buckets(self, durations: List[float]) -> Dict[float, int]:
        """Calculate histogram bucket counts"""
        buckets = {bucket: 0 for bucket in self.DURATION_BUCKETS}
        for duration in durations:
            for bucket in self.DURATION_BUCKETS:
                if duration <= bucket:
                    buckets[bucket] += 1
                    break
        return buckets
    
    def get_metrics(self) -> str:
        """Get metrics in Prometheus format"""
        with self._lock:
            lines = []
            
            # powermem_api_requests_total
            lines.append("# HELP powermem_api_requests_total Total number of API requests")
            lines.append("# TYPE powermem_api_requests_total counter")
            for (method, endpoint, status), count in sorted(self._api_request_counters.items()):
                lines.append(
                    f'powermem_api_requests_total{{method="{method}",endpoint="{endpoint}",status="{status}"}} {count}'
                )
            lines.append("")
            
            # powermem_memory_operations_total
            lines.append("# HELP powermem_memory_operations_total Total number of memory operations")
            lines.append("# TYPE powermem_memory_operations_total counter")
            for (operation, status), count in sorted(self._memory_operation_counters.items()):
                lines.append(
                    f'powermem_memory_operations_total{{operation="{operation}",status="{status}"}} {count}'
                )
            lines.append("")
            
            # powermem_api_request_duration_seconds (histogram)
            lines.append("# HELP powermem_api_request_duration_seconds API request duration in seconds")
            lines.append("# TYPE powermem_api_request_duration_seconds histogram")
            for (method, endpoint), durations in sorted(self._api_request_durations.items()):
                if durations:
                    buckets = self._calculate_histogram_buckets(durations)
                    # Output buckets
                    for bucket in self.DURATION_BUCKETS:
                        if bucket == float('inf'):
                            # Use +Inf for the last bucket
                            lines.append(
                                f'powermem_api_request_duration_seconds_bucket{{method="{method}",endpoint="{endpoint}",le="+Inf"}} {buckets[bucket]}'
                            )
                        else:
                            # Format bucket value: use .1f for values >= 0.1, .2f for smaller values
                            if bucket >= 0.1:
                                bucket_str = f"{bucket:.1f}"
                            else:
                                bucket_str = f"{bucket:.2f}"
                            lines.append(
                                f'powermem_api_request_duration_seconds_bucket{{method="{method}",endpoint="{endpoint}",le="{bucket_str}"}} {buckets[bucket]}'
                            )
                    # Output sum and count
                    sum_duration = sum(durations)
                    count = len(durations)
                    lines.append(
                        f'powermem_api_request_duration_seconds_sum{{method="{method}",endpoint="{endpoint}"}} {sum_duration:.6f}'
                    )
                    lines.append(
                        f'powermem_api_request_duration_seconds_count{{method="{method}",endpoint="{endpoint}"}} {count}'
                    )
            lines.append("")
            
            # powermem_errors_total
            lines.append("# HELP powermem_errors_total Total number of errors")
            lines.append("# TYPE powermem_errors_total counter")
            for (error_type, endpoint), count in sorted(self._error_counters.items()):
                lines.append(
                    f'powermem_errors_total{{error_type="{error_type}",endpoint="{endpoint}"}} {count}'
                )
            
            return '\n'.join(lines) + '\n'


# Global metrics collector instance
_metrics_collector = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance"""
    return _metrics_collector
