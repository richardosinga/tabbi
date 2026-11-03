from django.urls import path

from . import views

urlpatterns = [
    path("", views.plan_list, name="plan_list"),
    path("new/", views.plan_new, name="plan_new"),
    path("join/", views.plan_join, name="plan_join"),
    path("<slug:slug>/created/", views.plan_created, name="plan_created"),
    path("<slug:slug>/edit/", views.plan_edit, name="plan_edit"),
    # Before the <slug>/<city_slug>/… routes, which would capture "travelers" as a city.
    path("<slug:slug>/travelers/add/", views.plan_traveler_add, name="plan_traveler_add"),
    path("<slug:slug>/travelers/<str:traveler_id>/remove/", views.plan_traveler_remove, name="plan_traveler_remove"),
    path("<slug:slug>/travelers/<str:traveler_id>/invite/", views.plan_traveler_invite, name="plan_traveler_invite"),
    path("<slug:slug>/travelers/<str:traveler_id>/<slug:city_slug>/", views.plan_traveler_toggle, name="plan_traveler_toggle"),
    path("<slug:slug>/join/<str:token>/", views.plan_traveler_join, name="plan_traveler_join"),
    path("<slug:slug>/sheet/", views.plan_sheet, name="plan_sheet"),
    path("<slug:slug>/sheet/save/", views.plan_sheet_save, name="plan_sheet_save"),
    path("<slug:slug>/add-stop/", views.api_plan_add_stop, name="api_plan_add_stop"),
    path("<slug:slug>/add/", views.plan_poi_add, name="plan_poi_add"),
    path("<slug:slug>/<slug:city_slug>/add/", views.plan_poi_add, name="plan_poi_add_city"),
    path("<slug:slug>/<slug:city_slug>/remove/", views.plan_poi_remove, name="plan_poi_remove"),
    path("<slug:slug>/<slug:city_slug>/note/", views.plan_note_edit, name="plan_note_edit"),
    path("<slug:slug>/<slug:city_slug>/budget/", views.plan_budget_save, name="plan_budget_save"),
    path("<slug:slug>/<slug:city_slug>/poi-images/", views.plan_poi_images, name="plan_poi_images"),
    path("<slug:slug>/<slug:city_slug>/add-daytrip/", views.plan_daytrip_add, name="plan_daytrip_add"),
    path("<slug:slug>/<slug:city_slug>/pin-provider/", views.plan_pin_provider, name="plan_pin_provider"),
    # Must precede the generic <slug>/<city_slug>/ route below, otherwise
    # "set-passport" is captured as a city_slug and routed to plan_stop (404).
    path("<slug:slug>/set-passport/", views.plan_set_passport, name="plan_set_passport"),
    path("<slug:slug>/<slug:city_slug>/", views.plan_stop, name="plan_stop"),
    path("<slug:slug>/", views.plan_detail, name="plan_detail"),
]
