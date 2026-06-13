from django.urls import include, path
from django.views.generic import RedirectView

from world66_content.views import content_image

urlpatterns = [
    path("", RedirectView.as_view(url="/passport/", permanent=False)),
    path("passport/", include("passport.urls")),
    path("content-image/<path:path>", content_image, name="content_image"),
]
