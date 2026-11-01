from django.conf import settings


def world66_url(request):
    return {
        "WORLD66_SITE_URL": settings.WORLD66_SITE_URL,
        "DEPLOY_VERSION": settings.DEPLOY_VERSION,
        "CARTO_BASEMAP_KEY": settings.CARTO_BASEMAP_KEY,
    }
