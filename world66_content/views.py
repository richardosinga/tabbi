from django.http import FileResponse, Http404

from .models import CONTENT_DIR


def content_image(request, path):
    """Serve an image from the world66 content directory."""
    file_path = (CONTENT_DIR / path).resolve()
    if not file_path.is_relative_to(CONTENT_DIR.resolve()):
        raise Http404
    if not file_path.is_file() or file_path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
        raise Http404
    return FileResponse(open(file_path, "rb"))
