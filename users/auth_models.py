import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    """
    Custom user model extending Django's AbstractUser.
    """
    supabase_uid = models.UUIDField(unique=True, null=True, blank=True, db_index=True)
