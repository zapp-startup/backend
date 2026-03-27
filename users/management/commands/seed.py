"""
Management command: Agent-based stochastic synthetic finance data generator.

Replaces the original simple seed with a full agent-based pipeline following
the rules document. Each synthetic user gets a latent state vector, and 11
specialized agents generate realistic financial data in the correct order.

Usage:
    python manage.py seed --users 50 --months 18 --prefix seed_
    python manage.py seed --users 10 --months 12 --seed 42
    python manage.py seed --users 5 --use-llm
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from datagen.csv_seed import run_csv_export
from datagen.llm import is_llm_available
from datagen.pipeline import run_pipeline


class Command(BaseCommand):
    help = (
        "Generate agent-based stochastic synthetic finance data. "
        "Creates users, transactions, subscriptions, valuations, "
        "AI conversations, and behavioral annotations."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--users", type=int, default=50,
            help="Number of synthetic users to generate (default: 50).",
        )
        parser.add_argument(
            "--months", type=int, default=18,
            help="Months of transaction history to generate (default: 18).",
        )
        parser.add_argument(
            "--prefix", type=str, default="seed_",
            help="Username prefix for seeded users (default: seed_).",
        )
        parser.add_argument(
            "--password", type=str, default="password123",
            help="Password for all seeded users (default: password123).",
        )
        parser.add_argument(
            "--use-llm", action="store_true", default=False,
            help="Enable LLM-based text generation for richer output.",
        )
        parser.add_argument(
            "--max-llm-reflections", type=int, default=20,
            help="Max LLM calls for reflection text per user when --use-llm (default: 20).",
        )
        parser.add_argument(
            "--seed", type=int, default=None,
            help="Random seed for reproducibility.",
        )
        parser.add_argument(
            "--csv-output-dir", type=str, default=None,
            help="Write generated data directly to CSVs in this directory instead of persisting to the database.",
        )
        parser.add_argument(
            "--include-summary-json", action="store_true", default=False,
            help="When using --csv-output-dir, also write summary_by_user.json.",
        )

    def handle(self, *args, **opts):
        if opts["use_llm"] and not is_llm_available():
            self.stdout.write(self.style.WARNING(
                "LLM_API_KEY not set. "
                "Add it to .env for richer text generation. Falling back to templates."
            ))

        if opts["csv_output_dir"]:
            self.stdout.write(self.style.NOTICE(
                f"Starting agent-based CSV generation: "
                f"{opts['users']} users, {opts['months']} months, prefix='{opts['prefix']}', "
                f"output='{opts['csv_output_dir']}'"
            ))
            stats = run_csv_export(
                num_users=opts["users"],
                months=opts["months"],
                prefix=opts["prefix"],
                password=opts["password"],
                use_llm=opts["use_llm"],
                max_llm_reflections=opts["max_llm_reflections"],
                seed=opts["seed"],
                output_dir=opts["csv_output_dir"],
                include_summary_json=opts["include_summary_json"],
                log=lambda msg: self.stdout.write(msg),
            )
            completion_label = "CSV export complete!"
        else:
            self.stdout.write(self.style.NOTICE(
                f"Starting agent-based data generation: "
                f"{opts['users']} users, {opts['months']} months, prefix='{opts['prefix']}'"
            ))
            stats = run_pipeline(
                num_users=opts["users"],
                months=opts["months"],
                prefix=opts["prefix"],
                password=opts["password"],
                use_llm=opts["use_llm"],
                max_llm_reflections=opts["max_llm_reflections"],
                seed=opts["seed"],
                log=lambda msg: self.stdout.write(msg),
            )
            completion_label = "Seed complete!"

        self.stdout.write(self.style.SUCCESS(
            f"\n{completion_label}\n"
            f"  Users created:           {stats['users_created']}\n"
            f"  Transactions:            {stats['transactions']}\n"
            f"  Subscriptions:           {stats['subscriptions']}\n"
            f"  Subscription valuations: {stats['sub_valuations']}\n"
            f"  Item valuations:         {stats['item_valuations']}\n"
            f"  Conversations:           {stats['conversations']}\n"
            f"  User facts:              {stats['facts']}\n"
            f"  Anomalies injected:      {stats['anomalies']}\n"
            f"  Audit repairs:           {stats['audit_repairs']}"
        ))
