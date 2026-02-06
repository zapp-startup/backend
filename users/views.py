from django.shortcuts import render

# Create your views here.

from django.http import HttpResponse
from django.template import loader
from django.views import View
from django.views.generic import ListView, DetailView
from .models import UserProfile, UserPreference

#View 1: Function-Based View (HttpResponse (Manual))

def profile_manual_view(request):
    profiles = UserProfile.objects.all()
    
    template = loader.get_template('users/profile_manual.html')
    
    context = {
        'profiles': profiles,
        'total_count': profiles.count(),
    }

    html = template.render(context, request)
    return HttpResponse(html)

# View 2: Function-Based View (render())
def profile_render_view(request):
    profiles = UserProfile.objects.select_related('user').all()
    
    total_profiles = profiles.count()
    save_more_count = profiles.filter(financial_goal='save_more').count()
    invest_count = profiles.filter(financial_goal='invest').count()
    
    context = {
        'profiles': profiles,
        'total_profiles': total_profiles,
        'save_more_count': save_more_count,
        'invest_count': invest_count,
    }
    
    return render(request, 'users/profile_render.html', context)

# View 3: Class-Based View (Base CBV)
class ProfileBaseView(View):
    
    def get(self, request):
        profiles = UserProfile.objects.select_related('user').all()
        
        strict_profiles = profiles.filter(budget_style='strict')
        flexible_profiles = profiles.filter(budget_style='flexible')
        optimize_profiles = profiles.filter(budget_style='optimize_value')
        
        context = {
            'all_profiles': profiles,
            'strict_profiles': strict_profiles,
            'flexible_profiles': flexible_profiles,
            'optimize_profiles': optimize_profiles,
        }
        
        return render(request, 'users/profile_base_cbv.html', context)

# View 4: Class-Based View (Gen CBV)
class ProfileListView(ListView):
    model = UserProfile
    template_name = 'users/profile_list.html' 
    context_object_name = 'profiles'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['total_profiles'] = UserProfile.objects.count()
        
        context['goal_breakdown'] = {
            'save_more': UserProfile.objects.filter(financial_goal='save_more').count(),
            'invest': UserProfile.objects.filter(financial_goal='invest').count(),
            'reduce_debt': UserProfile.objects.filter(financial_goal='reduce_debt').count(),
            'build_credit': UserProfile.objects.filter(financial_goal='build_credit').count(),
        }
        
        return context