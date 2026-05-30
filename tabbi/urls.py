from django.urls import include, path
from django.views.generic import RedirectView

from world66_content.views import content_image

urlpatterns = [
    path("", RedirectView.as_view(url="/plans/"), permanent=False),
    path("plans/", include("plans.urls")),
    path("auth/", include("plans.auth_urls")),
    path("concierge/", include("concierge.urls")),
    path("content-image/<path:path>", content_image, name="content_image"),
]
