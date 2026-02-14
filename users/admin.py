from django.contrib import admin
from .models import (
    UserRawExplicit,
    UserRawInferred,
    UserComputed,
    UserPreference,
)

admin.site.register(UserRawExplicit)
admin.site.register(UserRawInferred)
admin.site.register(UserComputed)
admin.site.register(UserPreference)
