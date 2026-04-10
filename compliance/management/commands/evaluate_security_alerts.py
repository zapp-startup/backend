import json

from django.core.management.base import BaseCommand

from compliance.monitoring import evaluate_security_alerts


class Command(BaseCommand):
    help = "Evaluate recent security alerts from AuditEvent telemetry."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json")
        parser.add_argument(
            "--min-severity",
            default="medium",
            choices=["low", "medium", "high", "critical"],
        )

    def handle(self, *args, **options):
        order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        alerts = [
            alert for alert in evaluate_security_alerts() if order[alert["severity"]] >= order[options["min_severity"]]
        ]
        if options["as_json"]:
            self.stdout.write(json.dumps({"alerts": alerts}, indent=2))
        else:
            for alert in alerts:
                self.stdout.write(
                    f"[{alert['severity']}] {alert['rule_id']} route={alert['routing_target']} summary={alert['summary']}"
                )
        if any(alert["severity"] in {"high", "critical"} for alert in alerts):
            raise SystemExit(2)
