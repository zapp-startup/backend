from django.contrib import admin
from .models import ValuationModelVersion, SubscriptionValuation, ItemValuation

admin.site.register(ValuationModelVersion)
admin.site.register(SubscriptionValuation)
admin.site.register(ItemValuation)