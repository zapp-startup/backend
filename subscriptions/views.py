from django.http import HttpResponse
from django.shortcuts import render
from django.template import loader
from django.views import View
from django.views.generic import ListView

from .models import Subscription

# Create your views here.


def subscription_manual_view(request):
    # Manual rendering: load the template yourself and wrap it in HttpResponse.
    template = loader.get_template("subscriptions/subscription_manual.html")
    context = {"title": "Manual Subscription View"}
    return HttpResponse(template.render(context, request))


def subscription_render_view(request):
    # Django ORM query: Subscription.objects.all() returns a queryset of all rows.
    subscriptions = Subscription.objects.all()
    # Pass the queryset into the template context so it can be iterated over.
    context = {"subscriptions": subscriptions}
    return render(request, "subscriptions/subscription_list.html", context)


class SubscriptionBaseView(View):
    template_name = "subscriptions/subscription_base.html"

    def get(self, request):
        # Same ORM query in a class-based view.
        subscriptions = Subscription.objects.all()
        context = {"subscriptions": subscriptions}
        return render(request, self.template_name, context)


class SubscriptionListView(ListView):
    model = Subscription
    template_name = "subscriptions/subscription_generic_list.html"
    context_object_name = "subscriptions"