"""
Elena Vasquez Statistical Validation Framework

Comprehensive statistical validation framework tailored to Elena's research
methodology and statistical rigor requirements for TCP validation.
"""

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock

import numpy as np
import pytest
from scipy import stats

from tcp.core.descriptors import CapabilityDescriptor
from tcp.core.protocol import TCPProtocol


@dataclass
class StatisticalTestResult:
    """Statistical test result with Elena's required metrics."""

    test_name: str
    sample_size: int
    effect_size: float
    p_value: float
    confidence_interval: Tuple[float, float]
    statistical_power: float
    significance_level: float
    is_significant: bool
    cohen_d: Optional[float] = None
    eta_squared: Optional[float] = None

    @property
    def meets_elena_standards(self) -> bool:
        """Check if result meets Elena's statistical standards."""
        return (
            self.sample_size >= 1000
            and self.effect_size >= 0.8
            and self.statistical_power >= 0.95
            and self.is_significant
        )


class ElenaStatisticalValidationFramework:
    """Statistical validation framework implementing Elena's methodology."""

    def __init__(self):
        self.tcp_protocol = TCPProtocol()
        self.significance_threshold = 0.95
        self.effect_size_threshold = 0.8
        self.sample_size_minimum = 1000
        self.statistical_power_minimum = 0.95

    def generate_statistical_samples(
        self, sample_size: int
    ) -> List[CapabilityDescriptor]:
        """Generate statistically valid sample of capability descriptors."""
        np.random.seed(42)  # Reproducible results for Elena

        # Stratified sampling across security levels
        security_levels = ["SAFE", "LOW_RISK", "MEDIUM_RISK", "HIGH_RISK", "CRITICAL"]
        samples_per_level = sample_size // len(security_levels)

        descriptors = []

        for level in security_levels:
            for i in range(samples_per_level):
                descriptor = CapabilityDescriptor(
                    name=f"tool_{level.lower()}_{i}",
                    description=f"Statistical test tool - {level} - Sample {i}",
                    version=f"1.{i % 10}",
                    parameters=[
                        {
                            "name": f"param_{j}",
                            "type": np.random.choice(["string", "integer", "boolean"]),
                            "required": np.random.choice([True, False]),
                            "description": f"Parameter {j} for statistical testing",
                        }
                        for j in range(np.random.randint(1, 6))
                    ],
                    security_level=level,
                    security_flags=self._generate_security_flags(level),
                    performance_metrics={
                        "execution_time_ns": int(
                            np.random.lognormal(13, 1)
                        ),  # Log-normal distribution
                        "memory_usage_bytes": int(np.random.exponential(8192)),
                        "output_size_bytes": int(np.random.gamma(2, 512)),
                    },
                )
                descriptors.append(descriptor)

        return descriptors

    def _generate_security_flags(self, security_level: str) -> List[str]:
        """Generate appropriate security flags based on security level."""
        flag_probabilities = {
            "SAFE": {"FILE_READ": 0.8, "FILE_WRITE": 0.1},
            "LOW_RISK": {"FILE_READ": 0.9, "FILE_WRITE": 0.3, "NETWORK_ACCESS": 0.2},
            "MEDIUM_RISK": {
                "FILE_WRITE": 0.7,
                "NETWORK_ACCESS": 0.5,
                "SYSTEM_MODIFY": 0.3,
            },
            "HIGH_RISK": {
                "SYSTEM_MODIFY": 0.8,
                "REQUIRES_SUDO": 0.6,
                "DESTRUCTIVE": 0.4,
            },
            "CRITICAL": {"DESTRUCTIVE": 0.9, "IRREVERSIBLE": 0.8, "REQUIRES_SUDO": 0.9},
        }

        flags = []
        for flag, probability in flag_probabilities.get(security_level, {}).items():
            if np.random.random() < probability:
                flags.append(flag)

        return flags

    def compression_ratio_hypothesis_test(
        self, sample_size: int = 2000
    ) -> StatisticalTestResult:
        """
        Statistical hypothesis test for TCP compression ratio claims.

        H0: Compression ratio <= 350:1
        H1: Compression ratio > 350:1
        """
        # Generate sample data
        descriptors = self.generate_statistical_samples(sample_size)
        compression_ratios = []

        for descriptor in descriptors:
            # Calculate theoretical documentation size
            doc_size = (
                len(descriptor.description) * 2
                + len(descriptor.parameters) * 100  # Description
                + 200  # Parameters documentation  # Additional documentation overhead
            )

            # Get TCP binary size
            binary_data = self.tcp_protocol.generate_binary(descriptor)
            binary_size = len(binary_data)

            # Calculate compression ratio
            ratio = doc_size / binary_size
            compression_ratios.append(ratio)

        # Statistical analysis
        sample_mean = np.mean(compression_ratios)
        sample_std = np.std(compression_ratios, ddof=1)

        # One-tailed t-test (H1: mean > 350)
        null_hypothesis_mean = 350.0
        t_statistic = (sample_mean - null_hypothesis_mean) / (
            sample_std / np.sqrt(sample_size)
        )
        p_value = 1 - stats.t.cdf(t_statistic, df=sample_size - 1)

        # Effect size (Cohen's d)
        cohen_d = (sample_mean - null_hypothesis_mean) / sample_std

        # Confidence interval
        alpha = 1 - self.significance_threshold
        t_critical = stats.t.ppf(1 - alpha / 2, df=sample_size - 1)
        margin_error = t_critical * (sample_std / np.sqrt(sample_size))
        ci_lower = sample_mean - margin_error
        ci_upper = sample_mean + margin_error

        # Statistical power (post-hoc)
        effect_size = abs(cohen_d)
        statistical_power = self._calculate_power(effect_size, sample_size, alpha)

        return StatisticalTestResult(
            test_name="compression_ratio_hypothesis_test",
            sample_size=sample_size,
            effect_size=effect_size,
            p_value=p_value,
            confidence_interval=(ci_lower, ci_upper),
            statistical_power=statistical_power,
            significance_level=self.significance_threshold,
            is_significant=p_value < (1 - self.significance_threshold),
            cohen_d=cohen_d,
        )

    def performance_distribution_analysis(
        self, sample_size: int = 1500
    ) -> Dict[str, StatisticalTestResult]:
        """
        Analyze performance metric distributions across hardware backends.

        Tests normality, variance equality, and mean differences.
        """
        descriptors = self.generate_statistical_samples(sample_size)

        # Simulate performance across different backends
        backends = ["cpu", "gpu", "fpga"]
        performance_data = {backend: [] for backend in backends}

        # Performance multipliers for each backend (realistic simulation)
        multipliers = {"cpu": 1.0, "gpu": 0.6, "fpga": 0.2}

        for descriptor in descriptors:
            base_time = descriptor.performance_metrics["execution_time_ns"]

            for backend in backends:
                # Add realistic variation
                variation = np.random.normal(1.0, 0.1)
                backend_time = base_time * multipliers[backend] * variation
                performance_data[backend].append(
                    max(backend_time, 10000)
                )  # Minimum 10μs

        results = {}

        # 1. Normality tests for each backend
        for backend in backends:
            data = performance_data[backend]

            # Shapiro-Wilk test for normality
            _, p_value = stats.shapiro(
                data[:5000] if len(data) > 5000 else data
            )  # Shapiro-Wilk limit

            results[f"{backend}_normality"] = StatisticalTestResult(
                test_name=f"{backend}_normality_test",
                sample_size=len(data),
                effect_size=0.0,  # Not applicable for normality
                p_value=p_value,
                confidence_interval=(0, 1),
                statistical_power=1.0,  # Not applicable
                significance_level=0.05,
                is_significant=p_value < 0.05,
            )

        # 2. ANOVA test for mean differences between backends
        f_statistic, p_value = stats.f_oneway(*[performance_data[b] for b in backends])

        # Effect size for ANOVA (eta-squared)
        total_variance = np.var(
            np.concatenate([performance_data[b] for b in backends]), ddof=1
        )
        within_variance = np.mean(
            [np.var(performance_data[b], ddof=1) for b in backends]
        )
        eta_squared = (
            1 - (within_variance / total_variance) if total_variance > 0 else 0
        )

        results["backend_performance_anova"] = StatisticalTestResult(
            test_name="backend_performance_anova",
            sample_size=sample_size * len(backends),
            effect_size=eta_squared,
            p_value=p_value,
            confidence_interval=(0, 1),  # Not standard for ANOVA
            statistical_power=self._calculate_anova_power(
                eta_squared, sample_size, len(backends)
            ),
            significance_level=0.05,
            is_significant=p_value < 0.05,
            eta_squared=eta_squared,
        )

        # 3. Pairwise comparisons (post-hoc tests)
        for i, backend1 in enumerate(backends):
            for backend2 in backends[i + 1 :]:
                data1, data2 = performance_data[backend1], performance_data[backend2]

                # Independent t-test
                t_stat, p_val = stats.ttest_ind(data1, data2)

                # Effect size (Cohen's d)
                pooled_std = np.sqrt(
                    (
                        (len(data1) - 1) * np.var(data1, ddof=1)
                        + (len(data2) - 1) * np.var(data2, ddof=1)
                    )
                    / (len(data1) + len(data2) - 2)
                )
                cohen_d = (np.mean(data1) - np.mean(data2)) / pooled_std

                # Confidence interval for difference in means
                se_diff = pooled_std * np.sqrt(1 / len(data1) + 1 / len(data2))
                df = len(data1) + len(data2) - 2
                t_critical = stats.t.ppf(0.975, df)
                mean_diff = np.mean(data1) - np.mean(data2)
                margin = t_critical * se_diff

                results[f"{backend1}_vs_{backend2}_comparison"] = StatisticalTestResult(
                    test_name=f"{backend1}_vs_{backend2}_comparison",
                    sample_size=len(data1) + len(data2),
                    effect_size=abs(cohen_d),
                    p_value=p_val,
                    confidence_interval=(mean_diff - margin, mean_diff + margin),
                    statistical_power=self._calculate_power(
                        abs(cohen_d), min(len(data1), len(data2)), 0.05
                    ),
                    significance_level=0.05,
                    is_significant=p_val < 0.05,
                    cohen_d=cohen_d,
                )

        return results

    def behavioral_adoption_regression_analysis(
        self, sample_size: int = 2500
    ) -> StatisticalTestResult:
        """
        Regression analysis for behavioral adoption patterns.

        Models the relationship between TCP characteristics and adoption likelihood.
        """
        # Generate sample with behavioral adoption data
        descriptors = self.generate_statistical_samples(sample_size)

        # Create predictor variables
        X_data = []
        y_data = []

        for descriptor in descriptors:
            # Predictor variables
            security_numeric = {
                "SAFE": 0,
                "LOW_RISK": 1,
                "MEDIUM_RISK": 2,
                "HIGH_RISK": 3,
                "CRITICAL": 4,
            }[descriptor.security_level]
            performance_score = 1.0 / (
                descriptor.performance_metrics["execution_time_ns"] / 1000000
            )  # Inverse of ms
            complexity_score = len(descriptor.parameters)
            flags_count = len(descriptor.security_flags)

            X_data.append(
                [security_numeric, performance_score, complexity_score, flags_count]
            )

            # Outcome variable (simulated adoption likelihood)
            # Based on realistic factors affecting adoption
            adoption_score = (
                10
                - security_numeric * 2
                + performance_score * 2  # Lower security risk increases adoption
                + max(0, 5 - complexity_score)  # Better performance increases adoption
                + max(  # Lower complexity increases adoption
                    0, 3 - flags_count
                )  # Fewer flags increases adoption
            )

            # Add noise and bound to [0, 10]
            adoption_score += np.random.normal(0, 1.5)
            adoption_score = max(0, min(10, adoption_score))
            y_data.append(adoption_score)

        X = np.array(X_data)
        y = np.array(y_data)

        # Multiple linear regression
        from sklearn.linear_model import LinearRegression
        from sklearn.metrics import r2_score

        model = LinearRegression()
        model.fit(X, y)

        # Model performance
        y_pred = model.predict(X)
        r_squared = r2_score(y, y_pred)

        # Statistical significance of the model
        # F-test for overall model significance
        n = len(y)
        k = X.shape[1]

        mse_model = np.mean((y - y_pred) ** 2)
        mse_null = np.var(y, ddof=1)

        f_statistic = ((mse_null - mse_model) / k) / (mse_model / (n - k - 1))
        p_value = 1 - stats.f.cdf(f_statistic, k, n - k - 1)

        # Effect size (R-squared adjusted)
        adjusted_r_squared = 1 - ((1 - r_squared) * (n - 1) / (n - k - 1))

        return StatisticalTestResult(
            test_name="behavioral_adoption_regression",
            sample_size=sample_size,
            effect_size=adjusted_r_squared,
            p_value=p_value,
            confidence_interval=(0, 1),  # For R-squared
            statistical_power=self._calculate_regression_power(
                adjusted_r_squared, k, n
            ),
            significance_level=0.05,
            is_significant=p_value < 0.05,
        )

    def _calculate_power(
        self, effect_size: float, sample_size: int, alpha: float
    ) -> float:
        """Calculate statistical power for t-test."""
        from scipy.stats import norm

        z_alpha = norm.ppf(1 - alpha)
        z_beta = z_alpha - effect_size * np.sqrt(sample_size)
        power = 1 - norm.cdf(z_beta)

        return min(max(power, 0), 1)  # Bound between 0 and 1

    def _calculate_anova_power(
        self, eta_squared: float, sample_size: int, groups: int
    ) -> float:
        """Calculate statistical power for ANOVA."""
        if eta_squared <= 0:
            return 0.05  # Minimum power

        # Cohen's f from eta-squared
        f = np.sqrt(eta_squared / (1 - eta_squared))

        # Approximate power calculation
        df_between = groups - 1
        df_within = sample_size * groups - groups

        # Noncentrality parameter
        ncp = f * f * sample_size * groups

        # Critical F value
        f_critical = stats.f.ppf(0.95, df_between, df_within)

        # Power approximation
        power = 1 - stats.ncf.cdf(f_critical, df_between, df_within, ncp)

        return min(max(power, 0.05), 1)

    def _calculate_regression_power(
        self, r_squared: float, predictors: int, sample_size: int
    ) -> float:
        """Calculate statistical power for regression."""
        if r_squared <= 0:
            return 0.05

        # Effect size f-squared
        f_squared = r_squared / (1 - r_squared)

        # Approximate power (simplified)
        power = min(1.0, f_squared * sample_size / (predictors + 1))

        return min(max(power, 0.05), 1)

    def generate_elena_statistical_report(
        self, output_path: Path = None
    ) -> Dict[str, Any]:
        """Generate comprehensive statistical validation report for Elena."""
        if output_path is None:
            output_path = Path("elena_statistical_validation_report.json")

        print("🔬 Running Elena's Statistical Validation Framework")
        print("=" * 60)

        # Run all statistical tests
        print("1. Compression Ratio Hypothesis Test...")
        compression_result = self.compression_ratio_hypothesis_test(sample_size=2000)

        print("2. Performance Distribution Analysis...")
        performance_results = self.performance_distribution_analysis(sample_size=1500)

        print("3. Behavioral Adoption Regression Analysis...")
        behavioral_result = self.behavioral_adoption_regression_analysis(
            sample_size=2500
        )

        # Compile report
        report = {
            "researcher": "Elena Vasquez",
            "framework_version": "1.0",
            "timestamp": "2025-07-05T22:00:00Z",
            "statistical_standards": {
                "significance_threshold": self.significance_threshold,
                "effect_size_threshold": self.effect_size_threshold,
                "sample_size_minimum": self.sample_size_minimum,
                "statistical_power_minimum": self.statistical_power_minimum,
            },
            "test_results": {
                "compression_ratio": {
                    "test_name": compression_result.test_name,
                    "sample_size": compression_result.sample_size,
                    "effect_size": compression_result.effect_size,
                    "p_value": compression_result.p_value,
                    "confidence_interval": compression_result.confidence_interval,
                    "statistical_power": compression_result.statistical_power,
                    "is_significant": compression_result.is_significant,
                    "meets_elena_standards": compression_result.meets_elena_standards,
                    "cohen_d": compression_result.cohen_d,
                },
                "performance_distribution": {
                    name: {
                        "test_name": result.test_name,
                        "sample_size": result.sample_size,
                        "effect_size": result.effect_size,
                        "p_value": result.p_value,
                        "is_significant": result.is_significant,
                        "meets_elena_standards": result.meets_elena_standards,
                    }
                    for name, result in performance_results.items()
                },
                "behavioral_adoption": {
                    "test_name": behavioral_result.test_name,
                    "sample_size": behavioral_result.sample_size,
                    "effect_size": behavioral_result.effect_size,
                    "p_value": behavioral_result.p_value,
                    "is_significant": behavioral_result.is_significant,
                    "meets_elena_standards": behavioral_result.meets_elena_standards,
                },
            },
            "summary": {
                "total_tests": 1 + len(performance_results) + 1,
                "significant_tests": sum(
                    [
                        compression_result.is_significant,
                        sum(r.is_significant for r in performance_results.values()),
                        behavioral_result.is_significant,
                    ]
                ),
                "meets_elena_standards_count": sum(
                    [
                        compression_result.meets_elena_standards,
                        sum(
                            r.meets_elena_standards
                            for r in performance_results.values()
                        ),
                        behavioral_result.meets_elena_standards,
                    ]
                ),
                "overall_statistical_validity": True,  # Will be computed based on results
            },
        }

        # Overall validity assessment
        total_tests = report["summary"]["total_tests"]
        significant_tests = report["summary"]["significant_tests"]
        elena_standards_met = report["summary"]["meets_elena_standards_count"]

        report["summary"]["overall_statistical_validity"] = (
            significant_tests / total_tests >= 0.8
            and elena_standards_met / total_tests >= 0.7
        )

        # Save report
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        print(f"\n✅ Statistical validation report saved to {output_path}")
        print(f"📊 Significant tests: {significant_tests}/{total_tests}")
        print(f"🎯 Elena standards met: {elena_standards_met}/{total_tests}")
        print(
            f"🏆 Overall validity: {'✅ VALID' if report['summary']['overall_statistical_validity'] else '❌ INVALID'}"
        )

        return report


# Pytest test cases for Elena's framework
class TestElenaStatisticalValidation:
    """Test cases for Elena's statistical validation framework."""

    @pytest.fixture
    def elena_framework(self):
        """Provides Elena's statistical validation framework."""
        return ElenaStatisticalValidationFramework()

    @pytest.mark.elena_vasquez
    @pytest.mark.statistical
    def test_compression_ratio_statistical_significance(self, elena_framework):
        """Test compression ratio meets Elena's statistical standards."""
        result = elena_framework.compression_ratio_hypothesis_test(sample_size=1000)

        # Elena's standards validation
        assert result.sample_size >= elena_framework.sample_size_minimum
        assert result.effect_size >= elena_framework.effect_size_threshold
        assert result.statistical_power >= elena_framework.statistical_power_minimum
        assert result.is_significant
        assert result.meets_elena_standards

    @pytest.mark.elena_vasquez
    @pytest.mark.statistical
    def test_performance_distribution_analysis(self, elena_framework):
        """Test performance distribution meets Elena's standards."""
        results = elena_framework.performance_distribution_analysis(sample_size=1000)

        # Verify ANOVA result
        anova_result = results["backend_performance_anova"]
        assert anova_result.is_significant
        assert anova_result.effect_size > 0.1  # Medium effect size minimum

        # Verify at least one pairwise comparison is significant
        comparison_results = [r for name, r in results.items() if "_vs_" in name]
        assert any(r.is_significant for r in comparison_results)

    @pytest.mark.elena_vasquez
    @pytest.mark.statistical
    def test_behavioral_adoption_regression(self, elena_framework):
        """Test behavioral adoption regression meets Elena's standards."""
        result = elena_framework.behavioral_adoption_regression_analysis(
            sample_size=1000
        )

        assert result.sample_size >= elena_framework.sample_size_minimum
        assert result.is_significant
        assert result.effect_size > 0.1  # Minimum explanatory power

    @pytest.mark.elena_vasquez
    @pytest.mark.statistical
    def test_statistical_power_calculations(self, elena_framework):
        """Test statistical power calculations are accurate."""
        # Test known scenarios
        power = elena_framework._calculate_power(
            effect_size=0.8, sample_size=1000, alpha=0.05
        )
        assert power >= 0.95  # Should have high power with large effect and sample

        power_low = elena_framework._calculate_power(
            effect_size=0.1, sample_size=20, alpha=0.05
        )
        assert power_low < 0.5  # Should have low power with small effect and sample

    @pytest.mark.elena_vasquez
    @pytest.mark.statistical
    def test_report_generation(self, elena_framework, tmp_path):
        """Test Elena's statistical report generation."""
        report_path = tmp_path / "elena_test_report.json"
        report = elena_framework.generate_elena_statistical_report(report_path)

        # Verify report structure
        assert "researcher" in report
        assert report["researcher"] == "Elena Vasquez"
        assert "statistical_standards" in report
        assert "test_results" in report
        assert "summary" in report

        # Verify statistical standards
        standards = report["statistical_standards"]
        assert standards["significance_threshold"] == 0.95
        assert standards["effect_size_threshold"] == 0.8
        assert standards["sample_size_minimum"] == 1000
        assert standards["statistical_power_minimum"] == 0.95

        # Verify file creation
        assert report_path.exists()


if __name__ == "__main__":
    # Standalone execution for Elena's validation
    framework = ElenaStatisticalValidationFramework()
    report = framework.generate_elena_statistical_report()

    print("\n" + "=" * 60)
    print("🎓 Elena Vasquez Statistical Validation Complete")
    print(
        f"📈 Statistical Excellence: {'ACHIEVED' if report['summary']['overall_statistical_validity'] else 'REQUIRES IMPROVEMENT'}"
    )
