from dataclasses import dataclass


@dataclass(frozen=True)
class DependencySignals:
    versions_behind: int = 0
    days_since_last_release: int = 0
    archived: bool = False
    cve_severities: tuple[str, ...] = ()


def cve_penalty(severities: tuple[str, ...]) -> float:
    return min(
        45.0,
        sum(
            {"CRITICAL": 18, "HIGH": 12, "MEDIUM": 6, "LOW": 2}.get(s.upper(), 4)
            for s in severities
        ),
    )


def dependency_score(s: DependencySignals) -> float:
    deductions = dependency_deductions(s)
    return max(0.0, round(100 - sum(deductions.values()), 2))


def dependency_deductions(s: DependencySignals) -> dict[str, float]:
    lag = min(40.0, max(0, s.versions_behind) * 8.0)
    age = (
        0
        if s.days_since_last_release < 90
        else 8
        if s.days_since_last_release < 180
        else 15
        if s.days_since_last_release <= 365
        else 25
    )
    return {"lag": lag, "age": age, "archived": 25.0 if s.archived else 0.0,
            "cves": cve_penalty(s.cve_severities)}


def freshness_score(scores: list[float]) -> float:
    return round(sum(scores) / len(scores), 2) if scores else 0.0


def risk_score(signals: list[DependencySignals]) -> float:
    if not signals:
        return 0.0
    individual = [min(
        100,
        cve_penalty(s.cve_severities) * 1.5
        + (30 if s.archived else 0)
        + min(40, s.versions_behind * 3)
        + min(20, s.days_since_last_release / 30),
    ) for s in signals]
    average = sum(individual) / len(individual)
    top_outlier = max(individual)
    return round(min(100.0, max(average, top_outlier)), 2)
