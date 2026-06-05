from django.conf import settings
from django.db import models

from core.security.encrypted_fields import EncryptedCharField, EncryptedJSONField, EncryptedTextField


class ConversationContext(models.TextChoices):
    GENERAL = "general", "General"
    SUBSCRIPTION = "subscription", "Subscription"
    PRODUCT = "product", "Product"
    BUDGETING = "budgeting", "Budgeting"


class Conversation(models.Model):
    """
    Represents a single chat thread between a user and the ai.
    May be linked to a specific subscription or valuation for context.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversations",
    )

    title = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Optional human-readable title for the conversation.",
    )

    context_type = models.CharField(
        max_length=32,
        choices=ConversationContext.choices,
        default=ConversationContext.GENERAL,
    )

    linked_subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="conversations",
        help_text="Set if this conversation is about a specific subscription.",
    )

    linked_item_valuation = models.ForeignKey(
        "valuations.ItemValuation",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="conversations",
        help_text="Set if this conversation is about a purchase decision.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    summary_text = EncryptedTextField(
        blank=True,
        default="",
        help_text="Rolling summary of older turns kept in the short-term memory store.",
    )
    session_state_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="Structured short-term memory for active goals, entities, and open loops.",
    )
    last_summarized_message_id = models.BigIntegerField(
        blank=True,
        null=True,
        help_text="Highest message id included in the rolling summary.",
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["user", "updated_at"]),
        ]

    def __str__(self) -> str:
        return f"Conversation({self.user} • {self.context_type})"


class MessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"


class Message(models.Model):
    """
    Represents a single message inside a conversation.
    """

    id = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )

    role = models.CharField(
        max_length=16,
        choices=MessageRole.choices,
    )

    content = EncryptedTextField()

    metadata_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="Optional metadata (tool calls, citations, extracted facts).",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"Message({self.role} @ {self.created_at})"


class UserFact(models.Model):
    """
    Stores stable, structured facts about a user extracted from chat or onboarding.
    Used to maintain AI consistency over time.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="facts",
    )

    fact_key = models.CharField(
        max_length=128,
        help_text="Canonical key, e.g. 'values_convenience'.",
    )

    fact_value_json = EncryptedJSONField(
        default=dict,
        help_text="Structured representation of the fact.",
    )

    source = models.CharField(
        max_length=32,
        default="chat",
        help_text="chat / onboarding / inferred",
    )

    confidence = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.80,
        help_text="Confidence score between 0 and 1.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "fact_key")
        indexes = [
            models.Index(fields=["user", "fact_key"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.fact_key}"


class ConversationMemoryKind(models.TextChoices):
    TOPIC = "topic", "Topic"
    DECISION = "decision", "Decision"
    FACT = "fact", "Fact"
    GOAL = "goal", "Goal"
    PREFERENCE = "preference", "Preference"


class ConversationMemoryItem(models.Model):
    """
    Durable memory items extracted from important turns.
    These are concise, retrieval-friendly notes used to maintain continuity.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_memory_items",
    )
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="memory_items",
        blank=True,
        null=True,
    )
    source_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="memory_items",
        blank=True,
        null=True,
    )
    memory_kind = models.CharField(
        max_length=32,
        choices=ConversationMemoryKind.choices,
        default=ConversationMemoryKind.TOPIC,
    )
    dedupe_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Optional stable key used to upsert durable memories.",
    )
    summary_text = EncryptedTextField(
        help_text="Short retrieval-friendly summary of the memory.",
    )
    detail_json = EncryptedJSONField(
        default=dict,
        blank=True,
        help_text="Optional structured details extracted from the turn.",
    )
    tags_json = EncryptedJSONField(
        default=list,
        blank=True,
        help_text="Normalized tags used for lightweight retrieval.",
    )
    importance = models.PositiveSmallIntegerField(
        default=1,
        help_text="Relative salience from 1 (low) to 5 (high).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "updated_at"]),
            models.Index(fields=["user", "memory_kind"]),
            models.Index(fields=["conversation", "updated_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                condition=models.Q(dedupe_key__isnull=False),
                name="uniq_user_conversation_memory_dedupe_key",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} • {self.memory_kind} • {self.summary_text[:48]}"
