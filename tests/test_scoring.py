from app.scoring import *


def test_fresh():
    assert dependency_score(DependencySignals()) == 100


def test_penalties():
    assert (
        dependency_score(
            DependencySignals(
                versions_behind=10,
                days_since_last_release=400,
                archived=True,
                cve_severities=("HIGH",),
            )
        )
        == 0
    )


def test_factor_breakdown_matches_total_deduction():
    signals = DependencySignals(versions_behind=2, days_since_last_release=200,
                                archived=True, cve_severities=("HIGH", "LOW"))
    deductions = dependency_deductions(signals)
    assert sum(deductions.values()) == 16 + 15 + 25 + 14
    assert 100 - sum(deductions.values()) == dependency_score(signals)


def test_cves():
    assert cve_penalty(("critical", "low")) == 20


def test_multiple_vulnerabilities_accumulate_and_cap():
    assert cve_penalty(("CRITICAL", "HIGH", "MEDIUM", "LOW")) == 38
    assert cve_penalty(("CRITICAL", "CRITICAL", "CRITICAL")) == 45


def test_aggregate():
    assert freshness_score([80, 90]) == 85 and freshness_score([]) == 0


def test_risk():
    assert risk_score([DependencySignals(archived=True, cve_severities=("HIGH",))]) == 48


def test_risk_keeps_a_dangerous_outlier_visible():
    signals = [DependencySignals() for _ in range(39)]
    signals.append(DependencySignals(archived=True, cve_severities=("CRITICAL",)))
    assert risk_score(signals) > 20
