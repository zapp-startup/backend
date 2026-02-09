from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    path('details/', 
         views.ProfileListView.as_view(), 
         name='profile_details'),
]