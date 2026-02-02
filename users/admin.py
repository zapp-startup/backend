from django.contrib import admin
from .models import UserProfile, UserPreference

admin.site.register(UserProfile)
admin.site.register(UserPreference)