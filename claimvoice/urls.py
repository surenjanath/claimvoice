from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("claims.urls")),
]

# Recordings are not served from MEDIA_URL: django.conf.urls.static only works
# under DEBUG, and whitenoise handles static files but not media. They go
# through a view instead, which works in production and leaves somewhere
# obvious to put access control.
