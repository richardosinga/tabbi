from django.urls import path

from . import views

urlpatterns = [
    path("signup/<slug:slug>/", views.plan_signup, name="plan_signup"),
    path("login/<slug:slug>/", views.plan_login, name="plan_login"),
    path("logout/", views.plan_logout, name="plan_logout"),
]
