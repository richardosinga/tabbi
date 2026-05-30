from django.urls import path

from . import views

urlpatterns = [
    path("new/", views.passport_new, name="passport_new"),
    path("join/", views.passport_join, name="passport_join"),
    path("login/<slug:slug>/", views.passport_login, name="passport_login"),
    path("signup/<slug:slug>/", views.passport_signup, name="passport_signup"),
    path("<slug:slug>/created/", views.passport_created, name="passport_created"),
    path("<slug:slug>/edit/", views.passport_edit, name="passport_edit"),
    path("<slug:slug>/", views.passport_detail, name="passport_detail"),
]
