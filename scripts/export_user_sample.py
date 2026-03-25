#!/usr/bin/env python
"""
One-off export utility for synthetic data quality review.

Exports a sample of users plus related rows to CSV files and a nested JSON
summary for per-user analysis.

Run from project root:
    python scripts/export_user_sample.py
    python scripts/export_user_sample.py --size 40 --mode first --username-prefix seed
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "zapp.settings.development")

import django

django.setup()

from django.contrib.auth import get_user_model

from ai.models import Conversation, Message, UserFact
from datagen.export_utils import compute_subscription_utilization
from subscriptions.models import Merchant, Subscription
from transactions.models import Transaction
from users.models import UserComputed, UserPreference, UserRawExplicit, UserRawInferred
from valuations.models import ItemValuation, SubscriptionValuation, ValuationModelVersion

User = get_user_model()

DEFAULT_SAMPLE_SIZE = 50
DEFAULT_OUTPUT_DIR = "exports/user_sample"
SAMPLE_MODES = ("random", "first")
SENSITIVE_FIELDS = {"password", "plaid_access_token"}


def json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "__float__") and type(obj).__name__ == "Decimal":
        return float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def model_to_dict_safe(instance, exclude_fields=None):
    if instance is None:
        return None

    exclude = set(exclude_fields or []) | SENSITIVE_FIELDS
    data = {}
    for f in instance._meta.get_fields():
        if f.many_to_many or f.one_to_many:
            continue
        if f.name in exclude:
            continue
        try:
            val = getattr(instance, f.name)
        except Exception:
            continue

        if hasattr(f, "remote_field") and f.remote_field:
            data[f"{f.name}_id"] = val.pk if val else None
        elif val is None:
            data[f.name] = None
        elif hasattr(val, "isoformat"):
            data[f.name] = val.isoformat()
        elif hasattr(val, "__float__") and type(val).__name__ == "Decimal":
            data[f.name] = float(val)
        else:
            data[f.name] = val
    return data


def get_csv_fields(model_class):
    names = []
    for f in model_class._meta.get_fields():
        if f.many_to_many or f.one_to_many:
            continue
        if f.name in SENSITIVE_FIELDS:
            continue
        if not getattr(f, "concrete", False):
            continue
        if hasattr(f, "attname") and hasattr(f, "remote_field") and f.remote_field:
            names.append(f.attname)
        else:
            names.append(f.name)
    return names


def subscription_export_fields():
    return get_csv_fields(Subscription) + ["subscription_utilization"]


def subscription_export_row(subscription):
    row = []
    fields = get_csv_fields(Subscription)
    for field_name in fields:
        val = getattr(subscription, field_name, None)
        if val is not None and hasattr(val, "isoformat"):
            val = val.isoformat()
        elif val is not None and hasattr(val, "__float__") and type(val).__name__ == "Decimal":
            val = float(val)
        elif hasattr(val, "pk"):
            val = val.pk
        row.append(val)

    row.append(
        compute_subscription_utilization(
            subscription,
            getattr(getattr(subscription, "merchant", None), "category", None),
        )
    )
    return row


def write_csv(output_path: Path, model_class, rows, filename: str):
    path = output_path / filename
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    if model_class is Subscription and filename == "subscriptions_subscription.csv":
        fields = subscription_export_fields()
    else:
        fields = get_csv_fields(model_class)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for obj in rows:
            if model_class is Subscription and filename == "subscriptions_subscription.csv":
                row = subscription_export_row(obj)
            else:
                row = []
                for field_name in fields:
                    val = getattr(obj, field_name, None)
                    if val is not None and hasattr(val, "isoformat"):
                        val = val.isoformat()
                    elif val is not None and hasattr(val, "__float__") and type(val).__name__ == "Decimal":
                        val = float(val)
                    elif hasattr(val, "pk"):
                        val = val.pk
                    row.append(val)
            writer.writerow(row)
    print(f"  Wrote {filename} ({len(rows)} rows)")


def build_user_queryset(username_prefix: str | None):
    qs = User.objects.all().order_by("id")
    if username_prefix:
        qs = qs.filter(username__startswith=username_prefix)
    return qs


def run_export(
    sample_size: int,
    mode: str,
    output_dir: str | Path,
    username_prefix: str | None,
    skip_summary_json: bool = False,
):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    all_users = build_user_queryset(username_prefix)
    total = all_users.count()
    if total == 0:
        print("No matching users in database. Aborting.")
        return

    if mode == "random":
        user_ids = list(all_users.order_by("?")[:sample_size].values_list("id", flat=True))
    else:
        user_ids = list(all_users[:sample_size].values_list("id", flat=True))

    user_ids = sorted(user_ids)
    users = list(User.objects.filter(id__in=user_ids).order_by("id"))

    print(f"Selected {len(users)} users (mode={mode}, total matching={total})")
    if username_prefix:
        print(f"Username prefix filter: {username_prefix}")
    print(f"Output directory: {output_path.absolute()}")

    raw_explicit = list(UserRawExplicit.objects.filter(user_id__in=user_ids))
    raw_inferred = list(UserRawInferred.objects.filter(user_id__in=user_ids))
    computed = list(UserComputed.objects.filter(user_id__in=user_ids))
    preferences = list(UserPreference.objects.filter(user_id__in=user_ids))
    transactions = list(Transaction.objects.filter(user_id__in=user_ids))
    subscriptions = list(Subscription.objects.filter(user_id__in=user_ids))
    subscription_valuations = list(SubscriptionValuation.objects.filter(user_id__in=user_ids))
    item_valuations = list(ItemValuation.objects.filter(user_id__in=user_ids))
    conversations = list(Conversation.objects.filter(user_id__in=user_ids))
    facts = list(UserFact.objects.filter(user_id__in=user_ids))

    conversation_ids = [c.id for c in conversations]

    messages = list(Message.objects.filter(conversation_id__in=conversation_ids))

    merchant_ids_from_subs = {s.merchant_id for s in subscriptions}
    merchant_ids_from_txns = set(
        Transaction.objects.filter(user_id__in=user_ids)
        .exclude(merchant_id__isnull=True)
        .values_list("merchant_id", flat=True)
    )
    merchant_ids = merchant_ids_from_subs | merchant_ids_from_txns
    merchants = list(Merchant.objects.filter(id__in=merchant_ids)) if merchant_ids else []

    model_version_ids = {sv.model_version_id for sv in subscription_valuations}
    model_version_ids.update(iv.model_version_id for iv in item_valuations)
    model_versions = (
        list(ValuationModelVersion.objects.filter(id__in=model_version_ids))
        if model_version_ids
        else []
    )

    user_fields = [f.name for f in User._meta.get_fields() if getattr(f, "concrete", False) and f.name not in SENSITIVE_FIELDS]
    with (output_path / "users_user.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(user_fields)
        for user in users:
            writer.writerow([getattr(user, field_name, None) for field_name in user_fields])
    print(f"  Wrote users_user.csv ({len(users)} rows)")

    write_csv(output_path, UserRawExplicit, raw_explicit, "users_userrawexplicit.csv")
    write_csv(output_path, UserRawInferred, raw_inferred, "users_userrawinferred.csv")
    write_csv(output_path, UserComputed, computed, "users_usercomputed.csv")
    write_csv(output_path, UserPreference, preferences, "users_userpreference.csv")
    write_csv(output_path, Transaction, transactions, "transactions_transaction.csv")
    write_csv(output_path, Subscription, subscriptions, "subscriptions_subscription.csv")
    write_csv(output_path, Merchant, merchants, "subscriptions_merchant.csv")
    write_csv(output_path, SubscriptionValuation, subscription_valuations, "valuations_subscriptionvaluation.csv")
    write_csv(output_path, ItemValuation, item_valuations, "valuations_itemvaluation.csv")
    write_csv(output_path, ValuationModelVersion, model_versions, "valuations_valuationmodelversion.csv")
    write_csv(output_path, Conversation, conversations, "ai_conversation.csv")
    write_csv(output_path, Message, messages, "ai_message.csv")
    write_csv(output_path, UserFact, facts, "ai_userfact.csv")

    if not skip_summary_json:
        merchants_by_id = {m.id: m for m in merchants}
        messages_by_conversation_id = {}
        for message in messages:
            messages_by_conversation_id.setdefault(message.conversation_id, []).append(message)

        subscriptions_by_user_id = {}
        for subscription in subscriptions:
            subscriptions_by_user_id.setdefault(subscription.user_id, []).append(subscription)

        result = []
        for user in users:
            uid = user.id
            user_conversations = [c for c in conversations if c.user_id == uid]
            user_subscriptions = subscriptions_by_user_id.get(uid, [])
            user_transactions = [t for t in transactions if t.user_id == uid]

            result.append(
                {
                    "user": model_to_dict_safe(user, exclude_fields={"password"}),
                    "raw_explicit": next((model_to_dict_safe(r) for r in raw_explicit if r.user_id == uid), None),
                    "raw_inferred": next((model_to_dict_safe(r) for r in raw_inferred if r.user_id == uid), None),
                    "computed": next((model_to_dict_safe(c) for c in computed if c.user_id == uid), None),
                    "preferences": [model_to_dict_safe(p) for p in preferences if p.user_id == uid],
                    "transactions": [
                        {
                            **model_to_dict_safe(t),
                            "merchant_name": merchants_by_id.get(t.merchant_id).name if t.merchant_id in merchants_by_id else None,
                        }
                        for t in user_transactions
                    ],
                    "subscriptions": [
                        {
                            **model_to_dict_safe(s),
                            "merchant_name": merchants_by_id.get(s.merchant_id).name if s.merchant_id in merchants_by_id else None,
                            "subscription_utilization": compute_subscription_utilization(
                                s,
                                merchants_by_id.get(s.merchant_id).category if s.merchant_id in merchants_by_id else None,
                            ),
                        }
                        for s in user_subscriptions
                    ],
                    "subscription_valuations": [
                        model_to_dict_safe(sv) for sv in subscription_valuations if sv.user_id == uid
                    ],
                    "item_valuations": [
                        model_to_dict_safe(iv) for iv in item_valuations if iv.user_id == uid
                    ],
                    "conversations": [
                        {
                            **model_to_dict_safe(conversation),
                            "messages": [
                                model_to_dict_safe(message)
                                for message in messages_by_conversation_id.get(conversation.id, [])
                            ],
                        }
                        for conversation in user_conversations
                    ],
                    "facts": [model_to_dict_safe(fact) for fact in facts if fact.user_id == uid],
                }
            )

        summary_path = output_path / "summary_by_user.json"
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=json_serializer)
        print(f"  Wrote summary_by_user.json ({len(result)} users)")

    metadata = {
        "exported_user_ids": user_ids,
        "sample_size": len(users),
        "requested_sample_size": sample_size,
        "mode": mode,
        "username_prefix": username_prefix,
        "summary_json_written": not skip_summary_json,
        "counts": {
            "users": len(users),
            "raw_explicit": len(raw_explicit),
            "raw_inferred": len(raw_inferred),
            "computed": len(computed),
            "preferences": len(preferences),
            "transactions": len(transactions),
            "subscriptions": len(subscriptions),
            "subscription_valuations": len(subscription_valuations),
            "item_valuations": len(item_valuations),
            "conversations": len(conversations),
            "messages": len(messages),
            "facts": len(facts),
            "merchants": len(merchants),
            "model_versions": len(model_versions),
        },
    }
    with (output_path / "export_metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print("  Wrote export_metadata.json")

    print("\nExport complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Export a sample of users and related data for manual or LLM-based quality review."
    )
    parser.add_argument(
        "--size",
        "-n",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Number of users to export (default: {DEFAULT_SAMPLE_SIZE})",
    )
    parser.add_argument(
        "--mode",
        "-m",
        choices=SAMPLE_MODES,
        default="random",
        help="Sample strategy: random or first N by id",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--username-prefix",
        default=None,
        help="Optional username prefix filter, e.g. 'seed'",
    )
    parser.add_argument(
        "--skip-summary-json",
        action="store_true",
        help="Skip writing summary_by_user.json and only export CSVs + metadata",
    )
    args = parser.parse_args()

    run_export(
        sample_size=args.size,
        mode=args.mode,
        output_dir=args.output,
        username_prefix=args.username_prefix,
        skip_summary_json=args.skip_summary_json,
    )


if __name__ == "__main__":
    main()
