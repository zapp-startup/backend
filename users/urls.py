from django.urls import path
from . import views

app_name = 'users'

urlpatterns = [
    # view 1
    path('profiles/manual/', 
         views.profile_manual_view, 
         name='profile_manual'),
    
    # view 2
    path('profiles/render/', 
         views.profile_render_view, 
         name='profile_render'),
    
    # view 3
    path('profiles/cbv-base/', 
         views.ProfileBaseView.as_view(), 
         name='profile_cbv_base'),
    
    # view 4
    path('profiles/cbv-generic/', 
         views.ProfileListView.as_view(), 
         name='profile_cbv_generic'),
]