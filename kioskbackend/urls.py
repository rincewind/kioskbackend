"""
URL configuration for kioskbackend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

from display.views import show_presentation, wartungsklappe, banner_edit, index, kalender_dump

urlpatterns = [
    path("accounts/", include("allauth.urls")),
    path("admin/", admin.site.urls),
    path("", index, name="index"),
    path("display/", show_presentation, name="slideshow"),
    path("wartungsklappe/", wartungsklappe, name="wartungsklappe"),
    path("wartungsklappe/banner/<int:pk>", banner_edit, name="banneredit"),
    path("kalenderdump/", kalender_dump, name="kalenderdump"),
    # DANGER Wil Robinson. This is not recommended. Don't do this unless you know why you should'nt.
    # use whitenoise also for media. in this case it's fine
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
] #+ static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
