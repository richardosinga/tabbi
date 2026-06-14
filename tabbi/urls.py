from django.urls import include, path
from django.views.generic import RedirectView, TemplateView
from django.http import HttpResponseRedirect

from world66_content.views import content_image
from plans.views import api_plan_create, api_plan_open, api_plan_add_pois, api_plan_remove_poi, api_plan_update_poi, api_research_submit, api_search, api_plans_list, api_add_from_url, api_suggest_destinations
from plans.mcp_view import mcp_endpoint


def logout_view(request):
    request.session.flush()
    return HttpResponseRedirect("/passport/")


urlpatterns = [
    path("", RedirectView.as_view(url="/plans/", permanent=False)),
    path("privacy/", TemplateView.as_view(template_name="privacy.html"), name="privacy"),
    path("connect/", TemplateView.as_view(template_name="connect.html"), name="connect"),
    path("how-it-works/", TemplateView.as_view(template_name="how-it-works.html"), name="how_it_works"),
    path("plans/", include("plans.urls")),
    path("auth/logout/", logout_view, name="logout"),
    path("auth/", include("plans.auth_urls")),
    path("concierge/", include("concierge.urls")),
    path("passport/", include("passport.urls")),
    path("content-image/<path:path>", content_image, name="content_image"),
    path("api/plans/create", api_plan_create, name="api_plan_create"),
    path("api/plans/open", api_plan_open, name="api_plan_open"),
    path("api/plan/add-pois", api_plan_add_pois, name="api_plan_add_pois"),
    path("api/plan/remove-poi", api_plan_remove_poi, name="api_plan_remove_poi"),
    path("api/plan/update-poi", api_plan_update_poi, name="api_plan_update_poi"),
    path("api/research/submit", api_research_submit, name="api_research_submit"),
    path("api/search", api_search, name="api_search"),
    path("api/plans", api_plans_list, name="api_plans_list"),
    path("api/add-from-url", api_add_from_url, name="api_add_from_url"),
    path("api/suggest-destinations", api_suggest_destinations, name="api_suggest_destinations"),
    path("mcp", mcp_endpoint, name="mcp"),
]
