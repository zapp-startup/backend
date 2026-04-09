import hashlib
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .models import AuditEvent


def _request_metadata(request) -> dict:
    meta = {}
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    source_ip = (xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")) or None
    user_agent = (request.META.get("HTTP_USER_AGENT") or "").strip()[:512] or None
    request_id = request.META.get("HTTP_X_REQUEST_ID") or request.META.get("HTTP_X_CORRELATION_ID")
    if source_ip:
        meta["source_ip"] = source_ip
    if user_agent:
        meta["user_agent"] = user_agent
    if request_id:
        meta["request_id"] = str(request_id)[:128]
    return meta


def capture_backend_audit_event(
    *,
    event_name: str,
    outcome: str,
    actor=None,
    source_system: str = "backend-django",
    action: str = "",
    resource_type: str = "",
    resource_id: str = "",
    request=None,
    status_code: int | None = None,
    error_code: str = "",
    error_message: str = "",
    metadata: dict | None = None,
):
    merged_metadata = dict(metadata or {})
    if request is not None:
        merged_metadata.update({k: v for k, v in _request_metadata(request).items() if k not in merged_metadata})
    actor_id = str(getattr(actor, "supabase_uid", "") or getattr(actor, "pk", "") or "").strip()

    normalized_error_code = error_code if error_code is not None else ""
    normalized_error_message = error_message if error_message is not None else ""
    
    return AuditEvent.objects.create(
        user=actor,
        event_name=event_name,
        outcome=outcome,
        actor_id=actor_id,
        actor_type=AuditEvent.ActorType.USER if actor else AuditEvent.ActorType.SYSTEM,
        source_system=source_system,
        request_id=merged_metadata.pop("request_id", ""),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        route=(getattr(request, "path", "") if request is not None else "")[:255],
        method=(getattr(request, "method", "") if request is not None else "")[:16],
        status_code=status_code,
        error_code=normalized_error_code,
        error_message=normalized_error_message,
        metadata=merged_metadata,
    )


def load_security_detection_rules() -> dict:
    rules_path = Path(settings.BASE_DIR) / "ops" / "security_alert_rules.json"
    with rules_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _sev_rank(value: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(value, 0)


def _fingerprint(rule_id: str, group_key: str, started_at: str) -> str:
    return hashlib.sha256(f"{rule_id}:{group_key}:{started_at}".encode("utf-8")).hexdigest()[:16]


def evaluate_security_alerts(now=None) -> list[dict]:
    now = now or timezone.now()
    config = load_security_detection_rules()
    rules = config["rules"]
    max_window = max(rule["window_minutes"] for rule in rules)
    recent_events = list(
        AuditEvent.objects.filter(occurred_at__gte=now - timedelta(minutes=max_window)).order_by("-occurred_at", "-id")
    )
    alerts = []

    for rule in rules:
        if rule["id"] == "auth_failed_login_spike":
            alerts.extend(_detect_failed_logins(rule, recent_events, now))
        elif rule["id"] == "privileged_action_event":
            alerts.extend(_detect_privileged_actions(rule, recent_events, now))
        elif rule["id"] == "banking_policy_denied_spike":
            alerts.extend(_detect_banking_policy_denials(rule, recent_events, now))

    return sorted(alerts, key=lambda item: (_sev_rank(item["severity"]), item["observed_at"]), reverse=True)


def _detect_failed_logins(rule: dict, events: list[AuditEvent], now) -> list[dict]:
    cutoff = now - timedelta(minutes=rule["window_minutes"])
    grouped = defaultdict(list)
    for event in events:
        if event.occurred_at < cutoff or event.event_name != "auth.login" or event.outcome != "failure":
            continue
        group = event.metadata.get("source_ip") or event.metadata.get("email_domain") or "unknown"
        grouped[group].append(event)

    alerts = []
    for group, matches in grouped.items():
        count = len(matches)
        severity = None
        if count >= rule["thresholds"]["high"]:
            severity = "high"
        elif count >= rule["thresholds"]["medium"]:
            severity = "medium"
        if not severity:
            continue
        alerts.append(_build_alert(rule, severity, group, matches, f"{count} failed logins from {group}"))
    return alerts


def _detect_privileged_actions(rule: dict, events: list[AuditEvent], now) -> list[dict]:
    cutoff = now - timedelta(minutes=rule["window_minutes"])
    names = set(rule["event_names"])
    alerts = []
    for event in events:
        if event.occurred_at < cutoff or event.event_name not in names:
            continue
        severity = "high" if event.outcome == "success" else "medium"
        group = event.actor_id or f"event-{event.id}"
        alerts.append(_build_alert(rule, severity, group, [event], f"Privileged action {event.event_name} {event.outcome}"))
    return alerts


def _detect_banking_policy_denials(rule: dict, events: list[AuditEvent], now) -> list[dict]:
    cutoff = now - timedelta(minutes=rule["window_minutes"])
    grouped = defaultdict(list)
    for event in events:
        if event.occurred_at < cutoff or event.event_name != "banking.policy_denied":
            continue
        group = event.actor_id or event.metadata.get("source_ip") or "unknown"
        grouped[group].append(event)

    alerts = []
    for group, matches in grouped.items():
        count = len(matches)
        if count < rule["thresholds"]["medium"]:
            continue
        severity = "high" if count >= rule["thresholds"]["high"] else "medium"
        alerts.append(_build_alert(rule, severity, group, matches, f"{count} banking policy denials for {group}"))
    return alerts


def _build_alert(rule: dict, severity: str, group_key: str, matches: list[AuditEvent], summary: str) -> dict:
    first = matches[-1]
    request_ids = [event.request_id for event in matches if event.request_id][:5]
    source_ips = [event.metadata.get("source_ip") for event in matches if event.metadata.get("source_ip")][:5]
    actor_ids = [event.actor_id for event in matches if event.actor_id][:5]
    routes = [event.route for event in matches if event.route][:5]
    return {
        "rule_id": rule["id"],
        "severity": severity,
        "routing_target": rule["routing"][severity],
        "summary": summary,
        "observed_at": matches[0].occurred_at.isoformat(),
        "dedupe_key": _fingerprint(rule["id"], group_key, first.occurred_at.isoformat()),
        "window_minutes": rule["window_minutes"],
        "triage": {
            "runbook": rule["runbook"],
            "dashboard": rule["dashboard"],
            "group_key": group_key,
            "event_ids": [event.id for event in matches[:5]],
            "event_names": list(dict.fromkeys(event.event_name for event in matches[:5])),
            "request_ids": list(dict.fromkeys(request_ids)),
            "actor_ids": list(dict.fromkeys(actor_ids)),
            "routes": list(dict.fromkeys(routes)),
            "source_ips": list(dict.fromkeys(source_ips)),
        },
    }
