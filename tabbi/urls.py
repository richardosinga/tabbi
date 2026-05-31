from django.urls import include, path
from django.views.generic import RedirectView

from world66_content.views import content_image
from plans.views import api_plan_create, api_plan_open, api_plan_add_pois, api_research_submit, api_search, api_plans_list, api_add_from_url

urlpatterns = [
    path("", RedirectView.as_view(url="/plans/", permanent=False)),
    path("plans/", include("plans.urls")),
    path("auth/", include("plans.auth_urls")),
    path("concierge/", include("concierge.urls")),
    path("passport/", include("passport.urls")),
    path("content-image/<path:path>", content_image, name="content_image"),
    path("api/plans/create", api_plan_create, name="api_plan_create"),
    path("api/plans/open", api_plan_open, name="api_plan_open"),
    path("api/plan/add-pois", api_plan_add_pois, name="api_plan_add_pois"),
    path("api/research/submit", api_research_submit, name="api_research_submit"),
    path("api/search", api_search, name="api_search"),
    path("api/plans", api_plans_list, name="api_plans_list"),
    path("api/add-from-url", api_add_from_url, name="api_add_from_url"),
]
