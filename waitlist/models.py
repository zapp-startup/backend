from django.db import models


class WaitlistSignup(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField(unique=True)
    source = models.CharField(max_length=64, default="landing_page", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        self.name = self.name.strip()
        self.source = (self.source or "landing_page").strip() or "landing_page"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.email} ({self.source})"
