#!/usr/bin/env python3
"""
Statistical Performance Engine for TCP Core Optimization
Dr. Elena Vasquez & Dr. Yuki Tanaka Collaboration

Mathematical foundation for real-time behavioral analysis with hardware acceleration.
Maintains statistical rigor while enabling microsecond-scale performance decisions.
"""

import logging
import math
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

# Hardware acceleration imports (optional)
try:
    import numba

    NUMBA_AVAILABLE = True
except ImportError:
    NUMBA_AVAILABLE = False

try:
    import cupy as cp

    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

logger = logging.getLogger(__name__)


class OptimizationBackend(Enum):
    """Available optimization backends for statistical operations."""

    CPU_BASIC = "cpu_basic"
    CPU_SIMD = "cpu_simd"
    CPU_NUMBA = "cpu_numba"
    GPU_CUPY = "gpu_cupy"
    AUTO = "auto"


@dataclass
class StatisticalMeasurement:
    """Statistical measurement with metadata for performance analysis."""

    value: float
    timestamp: float
    operation_type: str
    confidence_level: float = 0.95
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceStatistics:
    """Comprehensive performance statistics with mathematical validation."""

    mean: float
    std_dev: float
    variance: float
    count: int
    min_value: float
    max_value: float

    # Statistical inference
    confidence_interval: Tuple[float, float]
    standard_error: float

    # Performance metrics
    throughput_ops_per_sec: float
    latency_percentiles: Dict[int, float]  # 50th, 95th, 99th percentiles

    # Quality metrics
    coefficient_of_variation: float
    skewness: float
    kurtosis: float


class RingBuffer:
    """Lock-free ring buffer for high-performance measurement storage."""

    def __init__(self, capacity: int):
        self.capacity = capacity
        self.buffer = np.zeros(capacity, dtype=np.float64)
        self.head = 0
        self.size = 0
        self._lock = threading.RLock()

    def append(self, value: float) -> None:
        """Append value to ring buffer (thread-safe)."""
        with self._lock:
            self.buffer[self.head] = value
            self.head = (self.head + 1) % self.capacity
            if self.size < self.capacity:
                self.size += 1

    def get_data(self) -> np.ndarray:
        """Get current buffer data (thread-safe copy)."""
        with self._lock:
            if self.size == 0:
                return np.array([])
            elif self.size < self.capacity:
                return self.buffer[: self.size].copy()
            else:
                # Buffer is full, need to reconstruct order
                data = np.zeros(self.capacity, dtype=np.float64)
                tail = (self.head - self.size) % self.capacity

                if tail < self.head:
                    data[:] = self.buffer[tail : self.head]
                else:
                    # Wrapped around
                    first_part = self.buffer[tail:]
                    second_part = self.buffer[: self.head]
                    data[: len(first_part)] = first_part
                    data[len(first_part) :] = second_part

                return data


class RunningStatistics:
    """Numerically stable running statistics using Welford's algorithm."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Reset all statistics."""
        self.count = 0
        self.mean = 0.0
        self.m2 = 0.0  # Sum of squared differences from mean
        self.min_value = float("inf")
        self.max_value = float("-inf")

        # Additional moments for skewness and kurtosis
        self.m3 = 0.0
        self.m4 = 0.0

    def update(self, value: float) -> None:
        """Update statistics with new value (Welford's algorithm)."""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2

        # Update higher moments for skewness/kurtosis
        if self.count > 1:
            delta_n = delta / self.count
            delta_n2 = delta_n * delta_n
            term1 = delta * delta2 * (self.count - 1)
            self.m3 += term1 * delta_n * (self.count - 2) - 3 * delta_n * self.m2
            self.m4 += (
                term1 * delta_n2 * (self.count**2 - 3 * self.count + 3)
                + 6 * delta_n2 * self.m2
                - 4 * delta_n * self.m3
            )

        # Update min/max
        self.min_value = min(self.min_value, value)
        self.max_value = max(self.max_value, value)

    @property
    def variance(self) -> float:
        """Sample variance."""
        return self.m2 / (self.count - 1) if self.count > 1 else 0.0

    @property
    def std_dev(self) -> float:
        """Sample standard deviation."""
        return math.sqrt(self.variance)

    @property
    def standard_error(self) -> float:
        """Standard error of the mean."""
        return self.std_dev / math.sqrt(self.count) if self.count > 0 else 0.0

    @property
    def coefficient_of_variation(self) -> float:
        """Coefficient of variation (relative standard deviation)."""
        return self.std_dev / abs(self.mean) if self.mean != 0 else float("inf")

    @property
    def skewness(self) -> float:
        """Sample skewness (third standardized moment)."""
        if self.count < 3 or self.variance == 0:
            return 0.0
        return (self.m3 / self.count) / (self.variance**1.5)

    @property
    def kurtosis(self) -> float:
        """Sample kurtosis (fourth standardized moment)."""
        if self.count < 4 or self.variance == 0:
            return 0.0
        return (self.m4 / self.count) / (self.variance**2) - 3

    def confidence_interval(
        self, confidence_level: float = 0.95
    ) -> Tuple[float, float]:
        """Calculate confidence interval for the mean."""
        if self.count < 2:
            return (self.mean, self.mean)

        # Use t-distribution for small samples, normal for large
        alpha = 1 - confidence_level
        df = self.count - 1

        if self.count >= 30:
            # Normal approximation
            z_score = self._normal_ppf(1 - alpha / 2)
            margin = z_score * self.standard_error
        else:
            # t-distribution
            t_score = self._t_ppf(1 - alpha / 2, df)
            margin = t_score * self.standard_error

        return (self.mean - margin, self.mean + margin)

    @staticmethod
    def _normal_ppf(p: float) -> float:
        """Percent point function (inverse CDF) for standard normal distribution."""
        # Rational approximation for inverse normal CDF
        # Accurate to about 1e-9 for p in (0.001, 0.999)
        if p <= 0 or p >= 1:
            return float("inf") if p >= 1 else float("-inf")

        if p == 0.5:
            return 0.0

        # Transform to standard normal
        if p > 0.5:
            sign = 1
            p = 1 - p
        else:
            sign = -1

        # Rational approximation coefficients
        a = [
            0,
            -3.969683028665376e01,
            2.209460984245205e02,
            -2.759285104469687e02,
            1.383577518672690e02,
            -3.066479806614716e01,
            2.506628277459239e00,
        ]

        b = [
            0,
            -5.447609879822406e01,
            1.615858368580409e02,
            -1.556989798598866e02,
            6.680131188771972e01,
            -1.328068155288572e01,
        ]

        q = math.sqrt(-2 * math.log(p))
        x = (
            ((((a[6] * q + a[5]) * q + a[4]) * q + a[3]) * q + a[2]) * q + a[1]
        ) * q + a[0]
        x /= ((((b[5] * q + b[4]) * q + b[3]) * q + b[2]) * q + b[1]) * q + 1

        return sign * (q - x)

    @staticmethod
    def _t_ppf(p: float, df: int) -> float:
        """Percent point function for t-distribution (approximation)."""
        if df >= 30:
            # Normal approximation for large df
            return RunningStatistics._normal_ppf(p)

        # Simple approximation for t-distribution
        z = RunningStatistics._normal_ppf(p)

        # Cornish-Fisher expansion approximation
        c1 = z**3 + z
        c2 = 5 * z**5 + 16 * z**3 + 3 * z
        c3 = 3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z

        correction = c1 / (4 * df) + c2 / (96 * df**2) + c3 / (384 * df**3)

        return z + correction


class StatisticalPerformanceEngine:
    """
    High-performance statistical engine for TCP behavioral analysis.

    Provides real-time statistical validation with hardware acceleration support.
    Maintains mathematical rigor while enabling microsecond-scale decisions.
    """

    def __init__(
        self,
        window_size: int = 10000,
        backend: OptimizationBackend = OptimizationBackend.AUTO,
        confidence_level: float = 0.95,
    ):
        """
        Initialize statistical performance engine.

        Args:
            window_size: Size of rolling window for measurements
            backend: Optimization backend to use
            confidence_level: Default confidence level for intervals
        """
        self.window_size = window_size
        self.confidence_level = confidence_level

        # Statistical storage
        self.measurements = defaultdict(lambda: RingBuffer(window_size))
        self.running_stats = defaultdict(RunningStatistics)
        self.baselines = defaultdict(RunningStatistics)

        # Performance monitoring
        self.operation_counts = defaultdict(int)
        self.operation_times = defaultdict(RunningStatistics)

        # Hardware acceleration
        self.backend = self._select_optimal_backend(backend)
        logger.info(f"Statistical engine initialized with backend: {self.backend}")

        # Thread safety
        self._lock = threading.RLock()

    def _select_optimal_backend(
        self, requested: OptimizationBackend
    ) -> OptimizationBackend:
        """Select optimal backend based on availability and request."""
        if requested == OptimizationBackend.AUTO:
            # Auto-select best available backend
            if GPU_AVAILABLE:
                return OptimizationBackend.GPU_CUPY
            elif NUMBA_AVAILABLE:
                return OptimizationBackend.CPU_NUMBA
            else:
                return OptimizationBackend.CPU_BASIC

        # Validate requested backend
        if requested == OptimizationBackend.GPU_CUPY and not GPU_AVAILABLE:
            logger.warning(
                "GPU backend requested but CuPy not available, falling back to CPU"
            )
            return (
                OptimizationBackend.CPU_NUMBA
                if NUMBA_AVAILABLE
                else OptimizationBackend.CPU_BASIC
            )

        if requested == OptimizationBackend.CPU_NUMBA and not NUMBA_AVAILABLE:
            logger.warning(
                "Numba backend requested but not available, falling back to basic CPU"
            )
            return OptimizationBackend.CPU_BASIC

        return requested

    def record_measurement(
        self,
        operation_id: str,
        value: float,
        timestamp: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Record performance measurement with real-time statistical analysis.

        Args:
            operation_id: Unique identifier for the operation type
            value: Measured value (e.g., execution time, throughput)
            timestamp: Measurement timestamp (current time if None)
            metadata: Additional metadata for the measurement

        Returns:
            Real-time statistical analysis results
        """
        if timestamp is None:
            timestamp = time.perf_counter()

        measurement_start = time.perf_counter()

        with self._lock:
            # Store measurement
            self.measurements[operation_id].append(value)
            self.running_stats[operation_id].update(value)
            self.operation_counts[operation_id] += 1

            # Real-time statistical analysis
            stats = self.running_stats[operation_id]

            # Anomaly detection (3-sigma rule)
            z_score = (value - stats.mean) / stats.std_dev if stats.std_dev > 0 else 0.0
            is_anomaly = abs(z_score) > 3.0

            # Statistical significance vs baseline
            baseline_stats = self.baselines.get(operation_id)
            p_value = None
            effect_size = None

            if baseline_stats and baseline_stats.count > 0:
                # Welch's t-test for unequal variances
                if stats.count > 1 and baseline_stats.count > 1:
                    p_value = self._welch_t_test(stats, baseline_stats)
                    effect_size = self._cohens_d(stats, baseline_stats)

            # Performance metrics
            measurement_time = time.perf_counter() - measurement_start
            self.operation_times["statistical_analysis"].update(
                measurement_time * 1000
            )  # Convert to ms

            return {
                "operation_id": operation_id,
                "measurement": value,
                "timestamp": timestamp,
                "statistics": {
                    "mean": stats.mean,
                    "std_dev": stats.std_dev,
                    "variance": stats.variance,
                    "count": stats.count,
                    "confidence_interval": stats.confidence_interval(
                        self.confidence_level
                    ),
                    "coefficient_of_variation": stats.coefficient_of_variation,
                    "skewness": stats.skewness,
                    "kurtosis": stats.kurtosis,
                },
                "anomaly_detection": {
                    "z_score": z_score,
                    "is_anomaly": is_anomaly,
                    "threshold": 3.0,
                },
                "baseline_comparison": {
                    "p_value": p_value,
                    "effect_size": effect_size,
                    "is_significant": p_value < 0.05 if p_value is not None else False,
                },
                "performance": {
                    "analysis_time_ms": measurement_time * 1000,
                    "backend": self.backend.value,
                },
                "metadata": metadata or {},
            }

    def establish_baseline(
        self, operation_id: str, values: List[float]
    ) -> PerformanceStatistics:
        """
        Establish statistical baseline for an operation.

        Args:
            operation_id: Operation identifier
            values: Historical performance values

        Returns:
            Comprehensive baseline statistics
        """
        baseline = RunningStatistics()
        for value in values:
            baseline.update(value)

        self.baselines[operation_id] = baseline

        # Calculate percentiles
        sorted_values = sorted(values)
        n = len(sorted_values)
        percentiles = {}
        for p in [50, 90, 95, 99]:
            idx = int(n * p / 100)
            percentiles[p] = sorted_values[min(idx, n - 1)]

        # Estimate throughput (inverse of mean if value represents time)
        throughput = 1.0 / baseline.mean if baseline.mean > 0 else 0.0

        return PerformanceStatistics(
            mean=baseline.mean,
            std_dev=baseline.std_dev,
            variance=baseline.variance,
            count=baseline.count,
            min_value=baseline.min_value,
            max_value=baseline.max_value,
            confidence_interval=baseline.confidence_interval(self.confidence_level),
            standard_error=baseline.standard_error,
            throughput_ops_per_sec=throughput,
            latency_percentiles=percentiles,
            coefficient_of_variation=baseline.coefficient_of_variation,
            skewness=baseline.skewness,
            kurtosis=baseline.kurtosis,
        )

    def batch_statistical_analysis(
        self, operation_data: Dict[str, List[float]], use_gpu: bool = False
    ) -> Dict[str, PerformanceStatistics]:
        """
        Perform batch statistical analysis with optional GPU acceleration.

        Args:
            operation_data: Dictionary mapping operation IDs to measurement lists
            use_gpu: Whether to use GPU acceleration if available

        Returns:
            Statistical analysis results for each operation
        """
        if use_gpu and self.backend == OptimizationBackend.GPU_CUPY:
            return self._gpu_batch_analysis(operation_data)
        else:
            return self._cpu_batch_analysis(operation_data)

    def _cpu_batch_analysis(
        self, operation_data: Dict[str, List[float]]
    ) -> Dict[str, PerformanceStatistics]:
        """CPU-based batch statistical analysis."""
        results = {}

        for operation_id, values in operation_data.items():
            if not values:
                continue

            # Use NumPy for vectorized operations if available
            if len(values) > 100 and self.backend == OptimizationBackend.CPU_NUMBA:
                results[operation_id] = self._numba_statistics(values)
            else:
                results[operation_id] = self.establish_baseline(operation_id, values)

        return results

    def _gpu_batch_analysis(
        self, operation_data: Dict[str, List[float]]
    ) -> Dict[str, PerformanceStatistics]:
        """GPU-accelerated batch statistical analysis."""
        if not GPU_AVAILABLE:
            return self._cpu_batch_analysis(operation_data)

        results = {}

        for operation_id, values in operation_data.items():
            if (
                not values or len(values) < 100
            ):  # GPU overhead not worth it for small datasets
                results[operation_id] = self.establish_baseline(operation_id, values)
                continue

            # Transfer to GPU
            gpu_values = cp.array(values, dtype=cp.float64)

            # GPU-accelerated statistics
            mean = cp.mean(gpu_values)
            std_dev = cp.std(gpu_values, ddof=1)
            variance = cp.var(gpu_values, ddof=1)
            min_val = cp.min(gpu_values)
            max_val = cp.max(gpu_values)

            # Transfer back to CPU
            mean_cpu = float(mean.get())
            std_dev_cpu = float(std_dev.get())
            variance_cpu = float(variance.get())
            min_cpu = float(min_val.get())
            max_cpu = float(max_val.get())

            # Calculate additional statistics on CPU (more complex operations)
            baseline = RunningStatistics()
            for value in values:
                baseline.update(value)

            # Calculate percentiles on GPU
            gpu_sorted = cp.sort(gpu_values)
            n = len(values)
            percentiles = {}
            for p in [50, 90, 95, 99]:
                idx = int(n * p / 100)
                percentiles[p] = float(gpu_sorted[min(idx, n - 1)].get())

            results[operation_id] = PerformanceStatistics(
                mean=mean_cpu,
                std_dev=std_dev_cpu,
                variance=variance_cpu,
                count=n,
                min_value=min_cpu,
                max_value=max_cpu,
                confidence_interval=baseline.confidence_interval(self.confidence_level),
                standard_error=std_dev_cpu / math.sqrt(n),
                throughput_ops_per_sec=1.0 / mean_cpu if mean_cpu > 0 else 0.0,
                latency_percentiles=percentiles,
                coefficient_of_variation=(
                    std_dev_cpu / abs(mean_cpu) if mean_cpu != 0 else float("inf")
                ),
                skewness=baseline.skewness,
                kurtosis=baseline.kurtosis,
            )

        return results

    def _numba_statistics(self, values: List[float]) -> PerformanceStatistics:
        """Numba-accelerated statistical calculations."""
        if not NUMBA_AVAILABLE:
            return self.establish_baseline("", values)

        # Convert to numpy array for Numba
        arr = np.array(values, dtype=np.float64)

        # Use Numba JIT-compiled functions for better performance
        mean = float(self._numba_mean(arr))
        variance = float(self._numba_variance(arr, mean))
        std_dev = math.sqrt(variance)

        # Use regular NumPy for percentiles (Numba doesn't optimize these well)
        percentiles = {}
        for p in [50, 90, 95, 99]:
            percentiles[p] = float(np.percentile(arr, p))

        # Create running statistics for additional metrics
        baseline = RunningStatistics()
        for value in values:
            baseline.update(value)

        return PerformanceStatistics(
            mean=mean,
            std_dev=std_dev,
            variance=variance,
            count=len(values),
            min_value=float(np.min(arr)),
            max_value=float(np.max(arr)),
            confidence_interval=baseline.confidence_interval(self.confidence_level),
            standard_error=std_dev / math.sqrt(len(values)),
            throughput_ops_per_sec=1.0 / mean if mean > 0 else 0.0,
            latency_percentiles=percentiles,
            coefficient_of_variation=std_dev / abs(mean) if mean != 0 else float("inf"),
            skewness=baseline.skewness,
            kurtosis=baseline.kurtosis,
        )

    @staticmethod
    @numba.jit(nopython=True) if NUMBA_AVAILABLE else lambda f: f
    def _numba_mean(arr: np.ndarray) -> float:
        """Numba-optimized mean calculation."""
        return np.mean(arr)

    @staticmethod
    @numba.jit(nopython=True) if NUMBA_AVAILABLE else lambda f: f
    def _numba_variance(arr: np.ndarray, mean: float) -> float:
        """Numba-optimized variance calculation."""
        return np.var(arr - mean)

    def _welch_t_test(
        self, stats1: RunningStatistics, stats2: RunningStatistics
    ) -> float:
        """
        Welch's t-test for comparing two groups with unequal variances.

        Returns p-value for two-tailed test.
        """
        if stats1.count < 2 or stats2.count < 2:
            return 1.0

        # Calculate t-statistic
        mean_diff = stats1.mean - stats2.mean
        pooled_se = math.sqrt(
            stats1.variance / stats1.count + stats2.variance / stats2.count
        )

        if pooled_se == 0:
            return 1.0 if mean_diff == 0 else 0.0

        t_stat = abs(mean_diff / pooled_se)

        # Calculate degrees of freedom (Welch-Satterthwaite equation)
        s1_sq_n1 = stats1.variance / stats1.count
        s2_sq_n2 = stats2.variance / stats2.count

        df_num = (s1_sq_n1 + s2_sq_n2) ** 2
        df_denom = s1_sq_n1**2 / (stats1.count - 1) + s2_sq_n2**2 / (stats2.count - 1)

        if df_denom == 0:
            return 1.0

        df = df_num / df_denom

        # Approximate p-value using t-distribution
        # For simplicity, use normal approximation for large df
        if df >= 30:
            # Normal approximation
            p_value = 2 * (1 - self._normal_cdf(t_stat))
        else:
            # Simple t-distribution approximation
            p_value = 2 * (1 - self._t_cdf(t_stat, df))

        return min(p_value, 1.0)

    def _cohens_d(self, stats1: RunningStatistics, stats2: RunningStatistics) -> float:
        """Calculate Cohen's d effect size."""
        if stats1.count < 2 or stats2.count < 2:
            return 0.0

        # Pooled standard deviation
        pooled_var = (
            (stats1.count - 1) * stats1.variance + (stats2.count - 1) * stats2.variance
        ) / (stats1.count + stats2.count - 2)

        if pooled_var <= 0:
            return 0.0

        pooled_std = math.sqrt(pooled_var)

        # Cohen's d
        d = abs(stats1.mean - stats2.mean) / pooled_std

        # Hedge's correction for small sample bias
        correction_factor = 1 - (3 / (4 * (stats1.count + stats2.count - 2) - 1))

        return d * correction_factor

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Cumulative distribution function for standard normal distribution."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    @staticmethod
    def _t_cdf(x: float, df: int) -> float:
        """Approximate cumulative distribution function for t-distribution."""
        # Simple approximation for t-distribution CDF
        # For better accuracy, would need more complex implementation
        if df >= 30:
            return StatisticalPerformanceEngine._normal_cdf(x)

        # Very rough approximation - in production, use scipy.stats.t.cdf
        correction = 1 + x * x / (4 * df) - x * x * x * x / (96 * df * df)
        return StatisticalPerformanceEngine._normal_cdf(x * correction)

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary of the statistical engine."""
        with self._lock:
            total_operations = sum(self.operation_counts.values())

            analysis_stats = self.operation_times.get("statistical_analysis")
            analysis_summary = {
                "mean_time_ms": analysis_stats.mean if analysis_stats else 0,
                "std_dev_ms": analysis_stats.std_dev if analysis_stats else 0,
                "count": analysis_stats.count if analysis_stats else 0,
            }

            return {
                "backend": self.backend.value,
                "total_operations": total_operations,
                "operation_counts": dict(self.operation_counts),
                "window_size": self.window_size,
                "confidence_level": self.confidence_level,
                "analysis_performance": analysis_summary,
                "hardware_acceleration": {
                    "numba_available": NUMBA_AVAILABLE,
                    "gpu_available": GPU_AVAILABLE,
                    "backend_active": self.backend.value,
                },
            }


# Global instance for easy access
statistical_engine = StatisticalPerformanceEngine()


def benchmark_statistical_engine():
    """Benchmark the statistical engine performance."""
    import random

    print("🔬 Statistical Performance Engine Benchmark")
    print("=" * 60)

    # Test data
    test_data = [random.gauss(100, 15) for _ in range(10000)]

    # Single measurement benchmark
    start_time = time.perf_counter()
    for i, value in enumerate(test_data[:1000]):
        result = statistical_engine.record_measurement(f"test_op", value)
        if i == 0:
            print(
                f"First measurement analysis time: {result['performance']['analysis_time_ms']:.3f}ms"
            )

    single_time = time.perf_counter() - start_time
    print(
        f"1000 single measurements: {single_time*1000:.2f}ms ({single_time}ms per measurement)"
    )

    # Batch analysis benchmark
    batch_data = {
        "tcp_validation": test_data[:2500],
        "binary_serialization": test_data[2500:5000],
        "capability_matching": test_data[5000:7500],
        "descriptor_creation": test_data[7500:10000],
    }

    start_time = time.perf_counter()
    batch_results = statistical_engine.batch_statistical_analysis(batch_data)
    batch_time = time.perf_counter() - start_time

    print(f"Batch analysis (4 operations × 2500 samples): {batch_time*1000:.2f}ms")

    # GPU benchmark if available
    if GPU_AVAILABLE:
        start_time = time.perf_counter()
        gpu_results = statistical_engine.batch_statistical_analysis(
            batch_data, use_gpu=True
        )
        gpu_time = time.perf_counter() - start_time
        print(
            f"GPU batch analysis: {gpu_time*1000:.2f}ms ({batch_time/gpu_time:.1f}x speedup)"
        )

    # Summary
    summary = statistical_engine.get_performance_summary()
    print(f"\nEngine Summary:")
    print(f"  Backend: {summary['backend']}")
    print(f"  Total operations: {summary['total_operations']}")
    print(
        f"  Average analysis time: {summary['analysis_performance']['mean_time_ms']:.3f}ms"
    )

    return batch_results


if __name__ == "__main__":
    benchmark_statistical_engine()
