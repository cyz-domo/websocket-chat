"""
URL configuration for websocket_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
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
from django.contrib import admin
from django.conf import settings
from django.urls import path, include, re_path
from django.conf.urls.static import static
from django.views.static import serve
from django.views.generic import RedirectView
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from chat import views as chat_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/chat/login/', permanent=False), name='root'),
    path('chat/', include('chat.urls')),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
else:
    # Keep collected static assets reachable when running Daphne directly
    # without an external static file server.
    urlpatterns += [
        re_path(
            r'^static/(?P<path>.*)$',
            serve,
            {'document_root': settings.STATIC_ROOT},
        ),
    ]

# Keep uploaded media reachable even when DEBUG is off during local testing.
# django.conf.urls.static.static() becomes a no-op when DEBUG is False, so
# we wire the media route explicitly here.
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]

urlpatterns += [
    re_path(r'^.*$', chat_views.not_found_page),
]

handler404 = 'chat.views.not_found_page'
